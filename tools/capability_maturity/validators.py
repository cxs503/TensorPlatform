"""基准提供方注册表：实现与参考值必须**不同提供方**（独立性可机器裁决）。"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


def _source_fingerprint(fn: Callable[..., Any]) -> str:
    """实现源码文件 sha256 前缀（可复算的提供方指纹；取不到则退回 qualified name）。"""
    try:
        path = inspect.getsourcefile(fn) or inspect.getfile(fn)
        data = Path(path).read_bytes()
        return hashlib.sha256(data).hexdigest()[:16]
    except Exception:
        return ""


@dataclass(frozen=True)
class Probe:
    """一个可调用探针：实现（candidate）或参考（reference）。"""

    name: str
    provider: str
    kind: str  # "implementation" | "reference" | "baseline"
    fn: Callable[..., Any]
    source_sha256: str = ""
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "provider": self.provider, "kind": self.kind,
                "source_sha256": self.source_sha256, "note": self.note}


class ProbeRegistry:
    """探针注册表；拒绝同名覆盖与未知 kind。"""

    KINDS = ("implementation", "reference", "baseline")

    def __init__(self) -> None:
        self._probes: Dict[str, Probe] = {}

    def register(self, name: str, provider: str, kind: str,
                 fn: Callable[..., Any], note: str = "") -> Probe:
        if kind not in self.KINDS:
            raise ValueError(f"未知探针种类 {kind!r}（允许 {self.KINDS}）")
        if name in self._probes:
            raise ValueError(f"探针重名 {name!r}（禁止静默覆盖）")
        probe = Probe(name=name, provider=provider, kind=kind, fn=fn,
                      source_sha256=_source_fingerprint(fn), note=note)
        self._probes[name] = probe
        return probe

    def get(self, name: str) -> Probe:
        if name not in self._probes:
            raise KeyError(f"未注册的探针 {name!r}")
        return self._probes[name]

    def names(self) -> List[str]:
        return sorted(self._probes)

    def independence(self, impl: str, ref: str) -> Dict[str, Any]:
        """独立性判定：提供方不同才算独立（同源=自己证自己，禁止）。"""
        a, b = self.get(impl), self.get(ref)
        same_provider = a.provider.strip().lower() == b.provider.strip().lower()
        return {
            "implementation": a.as_dict(),
            "reference": b.as_dict(),
            "same_provider": same_provider,
            "independent": not same_provider,
        }
