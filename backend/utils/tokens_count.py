from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from config import configer

if TYPE_CHECKING:
    from tokenizers import Tokenizer


_TOKENIZER_CACHE: Tokenizer | None = None


def _get_tokenizer() -> Tokenizer:
    """模块级缓存 tokenizer，避免每次 count_tokens 都重复加载。"""
    global _TOKENIZER_CACHE

    if _TOKENIZER_CACHE is None:
        from tokenizers import Tokenizer

        _TOKENIZER_CACHE = Tokenizer.from_file(str(Path(configer.tokenizer_path) / 'tokenizer.json'))
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
    return len(tokenizer.encode(text, add_special_tokens=False).ids)
