"""Local multilingual ONNX embedding engine with automatic graceful fallback."""

from pathlib import Path
from threading import RLock

import numpy as np

from app.account_logging import log_event, logger
from app.config import BACKEND_ROOT
from app.system_config import EmbeddingMemoryConfig


def pack_vector(vector: np.ndarray) -> tuple[bytes, int]:
    arr = np.asarray(vector, dtype='<f4').reshape(-1)
    return arr.tobytes(), int(arr.shape[0])


def unpack_vector(blob: bytes | memoryview | None, dim: int | None) -> np.ndarray | None:
    if blob is None or not isinstance(dim, int) or dim <= 0:
        return None
    raw = bytes(blob)
    if len(raw) != dim * 4:
        return None
    vec = np.frombuffer(raw, dtype='<f4')
    if not np.all(np.isfinite(vec)):
        return None
    return vec


def cosine_similarity(query: np.ndarray, candidate: np.ndarray) -> float:
    if query.shape != candidate.shape or query.size == 0:
        return 0.0
    score = float(np.dot(query, candidate))
    if not np.isfinite(score):
        return 0.0
    return max(0.0, min(1.0, score))


class LocalEmbeddingModel:
    """Thread-safe wrapper around an ONNX sentence embedding model (e.g. multilingual-e5-small)."""

    def __init__(self, config: EmbeddingMemoryConfig | None = None, *, root: Path = BACKEND_ROOT):
        self.config = config or EmbeddingMemoryConfig()
        model_path = Path(self.config.model_dir)
        self.model_dir = model_path if model_path.is_absolute() else (root / model_path)
        self._lock = RLock()
        self._initialized = False
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()

    def _ensure_loaded(self) -> bool:
        if not self.config.enabled:
            return False
        if self._initialized:
            return self._session is not None
        with self._lock:
            if self._initialized:
                return self._session is not None
            self._initialized = True
            onnx_file = self.model_dir / 'model.onnx'
            tokenizer_file = self.model_dir / 'tokenizer.json'
            if not (onnx_file.is_file() and tokenizer_file.is_file()):
                log_event('embedding_model_unavailable', system=True, reason='files_missing')
                logger.info('本地向量模型文件尚未就绪（%s），长期记忆自动使用关键词与时间混合召回', self.model_dir.name)
                return False
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(tokenizer_file))
                tokenizer.enable_truncation(max_length=self.config.max_length)
                tokenizer.enable_padding(pad_id=0, pad_token='<pad>')

                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(
                    str(onnx_file), sess_options=options, providers=['CPUExecutionProvider'],
                )
                self._input_names = {inp.name for inp in session.get_inputs()}
                self._tokenizer = tokenizer
                self._session = session
                log_event('embedding_model_loaded', system=True, model=self.model_dir.name)
                return True
            except Exception as error:
                log_event('embedding_model_unavailable', system=True, reason=type(error).__name__)
                logger.warning('无法加载本地向量模型（%s），自动降级为关键词召回', type(error).__name__)
                self._session = None
                self._tokenizer = None
                return False

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    def _encode_raw(self, texts: list[str]) -> list[np.ndarray] | None:
        if not texts:
            return []
        if not self._ensure_loaded():
            return None
        try:
            with self._lock:
                encodings = self._tokenizer.encode_batch(texts)
            input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)
            feeds = {}
            if 'input_ids' in self._input_names:
                feeds['input_ids'] = input_ids
            if 'attention_mask' in self._input_names:
                feeds['attention_mask'] = attention_mask
            if 'token_type_ids' in self._input_names:
                feeds['token_type_ids'] = np.zeros_like(input_ids)

            outputs = self._session.run(None, feeds)
            primary = np.asarray(outputs[0], dtype=np.float32)
            if primary.ndim == 3:
                mask = attention_mask[:, :, None].astype(np.float32)
                summed = np.sum(primary * mask, axis=1)
                counts = np.clip(np.sum(mask, axis=1), 1e-9, None)
                pooled = summed / counts
            elif primary.ndim == 2:
                pooled = primary
            else:
                return None

            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            normalized = pooled / np.clip(norms, 1e-12, None)
            return [row.astype('<f4', copy=False) for row in normalized]
        except Exception as error:
            logger.warning('向量编码失败（%s），本轮降级为关键词召回', type(error).__name__)
            return None

    def encode_query(self, text: str) -> np.ndarray | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        vectors = self._encode_raw([f'{self.config.query_prefix}{cleaned}'])
        return vectors[0] if vectors else None

    def encode_passages(self, texts: list[str]) -> list[np.ndarray] | None:
        if not texts:
            return []
        prefixed = [f'{self.config.passage_prefix}{item.strip()}' for item in texts]
        return self._encode_raw(prefixed)
