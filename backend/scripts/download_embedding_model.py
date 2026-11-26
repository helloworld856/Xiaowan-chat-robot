"""下载本地轻量多语 ONNX 向量模型（默认 Xenova/multilingual-e5-small）。"""

import argparse
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm.embeddings import DEFAULT_EMBEDDING_REPO, download_embedding_model_files

FALLBACK_REPO = 'intfloat/multilingual-e5-small'


def main() -> None:
    parser = argparse.ArgumentParser(description='下载小晚长期记忆使用的本地多语 ONNX 向量模型')
    parser.add_argument('--repo', default=DEFAULT_EMBEDDING_REPO,
                        help=f'HuggingFace 仓库名（默认 {DEFAULT_EMBEDDING_REPO}，备选 {FALLBACK_REPO}）')
    parser.add_argument('--output', default=str(BACKEND_ROOT / 'models' / 'multilingual-e5-small'),
                        help='模型保存目录（需与 system_config.json 的 memory.embedding.model_dir 对应）')
    parser.add_argument('--fp32', action='store_true', help='下载未量化的 FP32 ONNX 模型（默认优先下载 INT8 量化版）')
    parser.add_argument('--endpoint', default=os.getenv('HF_ENDPOINT'),
                        help='可选镜像地址，例如 https://hf-mirror.com')
    args = parser.parse_args()
    onnx_dest, tok_dest = download_embedding_model_files(
        args.repo, Path(args.output), quantized=not args.fp32, endpoint=args.endpoint,
    )
    size_mb = onnx_dest.stat().st_size / (1024 * 1024)
    print(f'已下载模型文件 -> {onnx_dest} ({size_mb:.1f} MB)')
    print(f'已下载分词配置 -> {tok_dest}')


if __name__ == '__main__':
    main()
