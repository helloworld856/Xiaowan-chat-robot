"""Local multilingual ONNX embedding engine with startup auto-download and graceful fallback."""

from logging import WARNING
import os
from pathlib import Path
import shutil
from threading import Event, RLock, Thread

import numpy as np

from app.account_logging import discard_logs_after, log_event, logger
from app.config import BACKEND_ROOT
from app.system_config import EmbeddingMemoryConfig

DEFAULT_EMBEDDING_REPO = 'Xenova/multilingual-e5-small'
DEFAULT_MIRROR_ENDPOINT = 'https://hf-mirror.com'


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


def _candidate_endpoints(configured: str | None) -> list[str | None]:
    explicit = (configured or os.getenv('HF_ENDPOINT') or '').strip().rstrip('/')
    if explicit:
        endpoints: list[str | None] = [explicit]
        if explicit != DEFAULT_MIRROR_ENDPOINT:
            endpoints.append(DEFAULT_MIRROR_ENDPOINT)
        return endpoints
    return [None, DEFAULT_MIRROR_ENDPOINT]


def download_embedding_model_files(
    repo_id: str,
    output_dir: Path,
    *,
    quantized: bool = True,
    endpoint: str | None = None,
    stop_event: Event | None = None,
) -> tuple[Path, Path]:
    """Download tokenizer.json and model.onnx atomically, falling back to hf-mirror when needed."""
    from huggingface_hub import hf_hub_download

    output_dir.mkdir(parents=True, exist_ok=True)
    tok_part = output_dir / 'tokenizer.json.part'
    onnx_part = output_dir / 'model.onnx.part'
    tok_dest = output_dir / 'tokenizer.json'
    onnx_dest = output_dir / 'model.onnx'

    onnx_candidates = ['onnx/model_quantized.onnx', 'onnx/model.onnx', 'model.onnx'] if quantized else [
        'onnx/model.onnx', 'model.onnx', 'onnx/model_quantized.onnx',
    ]

    last_error: Exception | None = None
    try:
        for ep in _candidate_endpoints(endpoint):
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError('embedding download cancelled on shutdown')
            try:
                tok_src = hf_hub_download(repo_id=repo_id, filename='tokenizer.json', endpoint=ep, etag_timeout=8)
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError('embedding download cancelled on shutdown')
                shutil.copyfile(tok_src, tok_part)
                if tok_part.stat().st_size <= 0:
                    raise RuntimeError('downloaded tokenizer.json is empty')

                downloaded_onnx = False
                for candidate in onnx_candidates:
                    if stop_event is not None and stop_event.is_set():
                        raise InterruptedError('embedding download cancelled on shutdown')
                    try:
                        model_src = hf_hub_download(repo_id=repo_id, filename=candidate, endpoint=ep, etag_timeout=8)
                        shutil.copyfile(model_src, onnx_part)
                        if onnx_part.stat().st_size <= 0:
                            raise RuntimeError('downloaded model.onnx is empty')
                        downloaded_onnx = True
                        break
                    except InterruptedError:
                        raise
                    except Exception as inner_error:
                        last_error = inner_error
                if not downloaded_onnx:
                    continue

                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError('embedding download cancelled on shutdown')
                os.replace(tok_part, tok_dest)
                os.replace(onnx_part, onnx_dest)
                return onnx_dest, tok_dest
            except InterruptedError:
                raise
            except Exception as error:
                last_error = error
        raise RuntimeError(f'无法从 {repo_id} 下载可用的本地向量模型') from last_error
    finally:
        for part_file in (tok_part, onnx_part):
            try:
                part_file.unlink(missing_ok=True)
            except OSError:
                pass


class LocalEmbeddingModel:
    """Thread-safe wrapper around an ONNX sentence embedding model (e.g. multilingual-e5-small)."""

    def __init__(self, config: EmbeddingMemoryConfig | None = None, *, root: Path = BACKEND_ROOT):
        self.config = config or EmbeddingMemoryConfig()
        model_path = Path(self.config.model_dir)
        self.model_dir = model_path if model_path.is_absolute() else (root / model_path)
        self._lock = RLock()
        self._stop_event = Event()
        self._load_failed = False
        self._logged_missing = False
        self._download_thread: Thread | None = None
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()

    def has_model_files(self) -> bool:
        onnx_file = self.model_dir / 'model.onnx'
        tokenizer_file = self.model_dir / 'tokenizer.json'
        try:
            return (
                onnx_file.is_file() and onnx_file.stat().st_size > 0
                and tokenizer_file.is_file() and tokenizer_file.stat().st_size > 0
            )
        except OSError:
            return False

    def prepare_on_startup(self, *, blocking: bool = False) -> None:
        """Check local model files on backend startup; load if present, or download if missing."""
        if not self.config.enabled or self._stop_event.is_set():
            return
        if self.has_model_files():
            self._ensure_loaded()
            return
        if not self.config.auto_download:
            log_event('embedding_model_unavailable', system=True, reason='auto_download_disabled')
            logger.info('未检测到本地向量模型（%s）且已关闭自动下载，长期记忆使用关键词与时间混合召回', self.model_dir.name)
            return

        with self._lock:
            if self._download_thread is not None and self._download_thread.is_alive():
                return

            def _download_worker():
                with discard_logs_after(self._stop_event):
                    try:
                        download_embedding_model_files(
                            self.config.repo_id,
                            self.model_dir,
                            endpoint=self.config.hf_endpoint or None,
                            stop_event=self._stop_event,
                        )
                        if self._stop_event.is_set():
                            return
                        log_event('embedding_model_downloaded', system=True,
                                  repo=self.config.repo_id, model=self.model_dir.name)
                        self._ensure_loaded()
                    except InterruptedError:
                        return
                    except Exception as error:
                        log_event('embedding_model_download_failed', level=WARNING, system=True,
                                  repo=self.config.repo_id, error=type(error).__name__)
                        logger.warning('本地向量模型自动下载失败（%s），本次运行继续使用关键词与时间混合召回', type(error).__name__)

            log_event('embedding_model_download_started', system=True,
                      repo=self.config.repo_id, model=self.model_dir.name)
            logger.warning('未检测到本地向量模型（%s），开始后台自动下载，下载完成前暂用关键词混合召回', self.model_dir.name)
            if blocking:
                _download_worker()
            else:
                thread = Thread(target=_download_worker, name='xiaowan-embedding-dl', daemon=True)
                self._download_thread = thread
                thread.start()

    def close(self) -> None:
        self._stop_event.set()

    def _ensure_loaded(self) -> bool:
        if not self.config.enabled or self._stop_event.is_set():
            return False
        if self._session is not None:
            return True
        if self._load_failed:
            return False
        with self._lock:
            if self._session is not None:
                return True
            if self._load_failed:
                return False
            if not self.has_model_files():
                if not self._logged_missing:
                    self._logged_missing = True
                    log_event('embedding_model_unavailable', system=True, reason='files_missing')
                    logger.info('本地向量模型文件尚未就绪（%s），长期记忆暂用关键词与时间混合召回', self.model_dir.name)
                return False
            onnx_file = self.model_dir / 'model.onnx'
            tokenizer_file = self.model_dir / 'tokenizer.json'
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
                self._load_failed = True
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
