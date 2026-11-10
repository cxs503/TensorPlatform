"""证据链：每件工件按内容 sha256 入账，缺件即不允许升级。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .criteria import AcceptanceCriteria


def _canonical_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Artifact:
    kind: str
    name: str
    sha256: str
    n_bytes: int
    payload: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "sha256": self.sha256,
                "n_bytes": self.n_bytes}


@dataclass
class EvidenceChain:
    """一项能力的证据链（append-only：同一 kind 允许多次登记，摘要取全部）。"""

    capability_id: str
    artifacts: List[Artifact] = field(default_factory=list)

    def add(self, kind: str, name: str, payload: Any) -> Artifact:
        raw = _canonical_bytes(payload)
        art = Artifact(kind=kind, name=name,
                       sha256=hashlib.sha256(raw).hexdigest(), n_bytes=len(raw),
                       payload=payload if isinstance(payload, dict) else None)
        self.artifacts.append(art)
        return art

    def kinds(self) -> List[str]:
        return [a.kind for a in self.artifacts]

    def complete(self, criteria: AcceptanceCriteria = None) -> Dict[str, Any]:
        criteria = criteria or AcceptanceCriteria()
        missing = [k for k in criteria.required_chain if k not in self.kinds()]
        return {
            "capability_id": self.capability_id,
            "required": list(criteria.required_chain),
            "present": self.kinds(),
            "missing": missing,
            "complete": not missing,
        }

    def digest(self) -> str:
        """链条指纹：工件 (kind,name,sha256) 的有序摘要。"""
        blob = json.dumps([[a.kind, a.name, a.sha256] for a in self.artifacts],
                          ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "digest": self.digest(),
            "artifacts": [a.as_dict() for a in self.artifacts],
        }
