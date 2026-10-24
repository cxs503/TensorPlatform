"""token 计量：优先 tiktoken 真实编码，缺失时显式降级为字符估算。

口径透明是硬要求（AIL_DATA_SPEC §11 / 基准协议 §2.2）：调用方拿到 TokenCounter
后必须把 counter.info() 记进产物，读者才能判断数字能否跨口径比较。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

# tiktoken 的 cl100k_base 需要 BPE 词表；离线环境若无缓存会尝试联网下载。
# 这里显式要求「要么本地可用、要么降级」，不允许挂死在网络请求上。
_REQUIRED_ENCODING = "cl100k_base"


class TokenCounter:
    """确定性 token 计数器。

    kind == 'tiktoken'    : 真实 BPE 编码（可与其他 tiktoken 口径比较）
    kind == 'char_estimate': 无 tiktoken 时的保守估算（1 token ≈ 0.75 中文字 / 4 英文字符），
                             只能做本报告内部相对比较，不可跨口径比较。
    """

    def __init__(self, encoding_name: str = _REQUIRED_ENCODING) -> None:
        self._enc = None
        self._kind = "char_estimate"
        self._encoding_name = encoding_name
        try:  # pragma: no cover - 环境相关
            import tiktoken

            self._enc = tiktoken.get_encoding(encoding_name)
            self._kind = "tiktoken"
        except Exception:  # 缺包 / 词表不可用 -> 显式降级
            self._enc = None
            self._kind = "char_estimate"

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text, disallowed_special=()))
        # CJK 按 ~0.75 token/字，ASCII 按 ~1 token/4 字符；两者取上界求和，宁高不低。
        cjk = sum(1 for ch in text if "\u2e80" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff")
        other = len(text) - cjk
        return int(cjk / 0.75 + other / 4.0 + 0.999)

    def info(self) -> dict:
        return {
            "kind": self._kind,
            "encoding": self._encoding_name if self._kind == "tiktoken" else None,
            "comparable_across_tools": self._kind == "tiktoken",
            "note": ("真实 BPE 编码，可与其他 cl100k_base 口径比较"
                     if self._kind == "tiktoken" else
                     "无 tiktoken，按字符估算；只可本报告内部比较，禁止跨口径引用"),
        }


@lru_cache(maxsize=4)
def default_counter() -> TokenCounter:
    return TokenCounter()


def text_fingerprint(text: str) -> str:
    """文本内容指纹（用于把 token 计数与具体上下文绑定，便于审计复算）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()