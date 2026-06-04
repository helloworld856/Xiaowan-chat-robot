from transformers import AutoTokenizer
from typing import Optional
from config import configer

_TOKENIZER_CACHE: Optional[AutoTokenizer] = None
_LOCAL_TOKENIZER_PATH: Optional[str] = None


def _get_tokenizer() -> AutoTokenizer:
    """模块级缓存 tokenizer，避免每次 count_tokens 都重复加载。"""
    global _TOKENIZER_CACHE, _LOCAL_TOKENIZER_PATH

    if _LOCAL_TOKENIZER_PATH is None:
        _LOCAL_TOKENIZER_PATH = configer.tokenizer_path

    if _TOKENIZER_CACHE is None:
        _TOKENIZER_CACHE = AutoTokenizer.from_pretrained(
            _LOCAL_TOKENIZER_PATH,
            trust_remote_code=True,
            use_fast=True,
        )
    return _TOKENIZER_CACHE


def count_tokens(text: str) -> int:
    """
    计算文本的token数量
    
    Args:
        text: 要计算的文本
        
    Returns:
        token数量
    """
    tokenizer = _get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=False))
