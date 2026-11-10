"""增量扫描缓存：以文件内容 sha256 为键，未变更文件跳过解析（C5 <60s）。

缓存只存**派生信号**（AST 统计量），不存判定结论；判定永远由当次 spec+信号重算，
因此缓存不会掩盖规则变化，也不会把旧结论当成新证据。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

CACHE_SCHEMA = "capability_audit_gate.cache.v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class ScanCache:
    """路径 → (sha256, signal) 缓存；命中率与耗时写进报告（可审计）。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.enabled = True
        if self.path and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if raw.get("schema") == CACHE_SCHEMA:
                    self._data = dict(raw.get("entries") or {})
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get_or_compute(self, path: Path, rel: str, compute: Callable[[Path], dict],
                       namespace: str = "impl") -> dict:
        """``namespace`` 区分同一文件的解析口径（impl / tests）；混用会让信号串味。"""
        key = f"{namespace}:{rel}"
        if not self.enabled:
            self.misses += 1
            return compute(path)
        digest = sha256_file(path)
        entry = self._data.get(key)
        if entry and entry.get("sha256") == digest:
            self.hits += 1
            return dict(entry["signal"])
        self.misses += 1
        signal = compute(path)
        self._data[key] = {"sha256": digest, "signal": signal}
        return signal

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": CACHE_SCHEMA, "entries": self._data}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8")