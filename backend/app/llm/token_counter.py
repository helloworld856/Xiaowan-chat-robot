from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import UTILS_PATH

if TYPE_CHECKING:
    from tokenizers import Tokenizer


_TOKENIZER_CACHE: Tokenizer | None = None


def _get_tokenizer() -> Tokenizer:
    """缓存分词器，避免每次调用 count_tokens 时重复加载。"""
    global _TOKENIZER_CACHE

    if _TOKENIZER_CACHE is None:
        from tokenizers import Tokenizer

        _TOKENIZER_CACHE = Tokenizer.from_file(str(UTILS_PATH / 'deepseek_tokenizer' / 'tokenizer.json'))
    return _TOKENIZER_CACHE


def count_tokens(text: str) -> int:
    """
    计算文本的token数量
    
    Args:
        text: 要计算的文本
        
    Returns:
        token数量
    """
    # 只统计文本本身，用于估算历史压缩阈值。
    tokenizer = _get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=False).ids)
