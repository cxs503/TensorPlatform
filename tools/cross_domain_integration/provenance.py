"""确定性哈希与 JSON 落盘 (纯 stdlib)，供审计链复核。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: str | Path, obj: Any) -> str:
    """写确定性 JSON 并返回内容 sha256 (报告自证用)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    p.write_text(text + "\n", encoding="utf-8")
    return sha256_text(text)