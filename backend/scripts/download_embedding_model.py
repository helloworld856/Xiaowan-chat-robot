"""下载本地轻量多语 ONNX 向量模型（默认 intfloat/multilingual-e5-small）。"""

import argparse
import os
import shutil
from pathlib import Path

DEFAULT_REPO = 'Xenova/multilingual-e5-small'
FALLBACK_REPO = 'intfloat/multilingual-e5-small'


def download_model(repo_id: str, output_dir: Path, *, quantized: bool = True, endpoint: str | None = None) -> None:
    from huggingface_hub import hf_hub_download

    if endpoint:
        os.environ['HF_ENDPOINT'] = endpoint
    output_dir.mkdir(parents=True, exist_ok=True)

    tokenizer_src = hf_hub_download(repo_id=repo_id, filename='tokenizer.json')
    shutil.copyfile(tokenizer_src, output_dir / 'tokenizer.json')

    candidates = ['onnx/model_quantized.onnx', 'onnx/model.onnx', 'model.onnx'] if quantized else [
        'onnx/model.onnx', 'model.onnx', 'onnx/model_quantized.onnx',
    ]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            model_src = hf_hub_download(repo_id=repo_id, filename=candidate)
            shutil.copyfile(model_src, output_dir / 'model.onnx')
            size_mb = (output_dir / 'model.onnx').stat().st_size / (1024 * 1024)
            print(f'已下载 {repo_id}:{candidate} -> {output_dir / "model.onnx"} ({size_mb:.1f} MB)')
            print(f'已下载 {repo_id}:tokenizer.json -> {output_dir / "tokenizer.json"}')
            return
        except Exception as error:
            last_error = error
    raise RuntimeError(f'无法在仓库 {repo_id} 中找到可用的 ONNX 模型文件') from last_error


def main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='下载小晚长期记忆使用的本地多语 ONNX 向量模型')
    parser.add_argument('--repo', default=DEFAULT_REPO,
                        help=f'HuggingFace 仓库名（默认 {DEFAULT_REPO}，备选 {FALLBACK_REPO}）')
    parser.add_argument('--output', default=str(backend_root / 'models' / 'multilingual-e5-small'),
                        help='模型保存目录（需与 system_config.json 的 memory.embedding.model_dir 对应）')
    parser.add_argument('--fp32', action='store_true', help='下载未量化的 FP32 ONNX 模型（默认优先下载 INT8 量化版）')
    parser.add_argument('--endpoint', default=os.getenv('HF_ENDPOINT'),
                        help='可选镜像地址，例如 https://hf-mirror.com')
    args = parser.parse_args()
    download_model(args.repo, Path(args.output), quantized=not args.fp32, endpoint=args.endpoint)


if __name__ == '__main__':
    main()
