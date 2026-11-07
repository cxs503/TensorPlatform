"""三道门共用的结果类型。

门的判据必须**机器可裁**：每个 GateResult 都带 evidence（可 JSON 落盘的原始证据），
协调者/继承者/审计者只能读证词，不能读"我觉得改了没事"。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class GateResult:
    gate: str
    status: GateStatus
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is GateStatus.PASS

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "evidence": self.evidence,
        }


@contextmanager
def timed(result: GateResult):
    """在 with 块内计时并回填 duration_ms。"""
    t0 = time.monotonic()
    try:
        yield result
    finally:
        result.duration_ms = int((time.monotonic() - t0) * 1000)


def pass_(gate: str, detail: str = "", evidence: dict | None = None) -> GateResult:
    return GateResult(gate, GateStatus.PASS, detail, evidence or {})


def fail(gate: str, detail: str, evidence: dict | None = None) -> GateResult:
    return GateResult(gate, GateStatus.FAIL, detail, evidence or {})