"""耦合验证报告生成 (machine-readable acceptance report).

报告 = 判据清单 + 每条判据的数字 + 通过/失败, 不写"看起来差不多"这类散文。
所有数字都从被测对象直接取 (``InterfaceBalance.to_dict()`` /
``ClockAdjudication.to_dict()`` / ``TransitivityResult.to_dict()``), 不做手抄。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Union

SCHEMA_ID = "coupling-conservation-acceptance/v1"


@dataclass
class Criterion:
    """一条机器判据的结果 (id/name/passed/numbers 四件套)."""

    criterion_id: str
    name: str
    passed: bool
    numbers: Dict[str, object] = field(default_factory=dict)
    notes: str = ""
    artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.criterion_id,
            "name": self.name,
            "passed": bool(self.passed),
            "numbers": _jsonable(self.numbers),
            "notes": self.notes,
            "artifacts": list(self.artifacts),
        }


def _jsonable(obj):
    """递归把 Fraction / tuple 等转成 JSON 可序列化对象 (Fraction -> float)."""
    from fractions import Fraction

    if isinstance(obj, Fraction):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


@dataclass
class AcceptanceReport:
    """一次耦合守恒契约验收的完整记录."""

    mission_id: str
    criteria: List[Criterion] = field(default_factory=list)
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.criteria) and all(c.passed for c in self.criteria)

    @property
    def failed_ids(self) -> List[str]:
        return [c.criterion_id for c in self.criteria if not c.passed]

    def add(self, criterion: Criterion) -> Criterion:
        self.criteria.append(criterion)
        return criterion

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_ID,
            "mission_id": self.mission_id,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "passed": self.passed,
            "n_criteria": len(self.criteria),
            "n_passed": sum(1 for c in self.criteria if c.passed),
            "failed": self.failed_ids,
            "meta": _jsonable(self.meta),
            "criteria": [c.to_dict() for c in self.criteria],
        }

    def write(self, path: Union[str, Path]) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        return p

    def summary_table(self) -> str:
        lines = ["| 判据 | 名称 | 结果 | 关键数字 |", "|---|---|---|---|"]
        for c in self.criteria:
            key = "; ".join(
                f"{k}={_fmt(v)}" for k, v in list(c.numbers.items())[:4]
            )
            lines.append(
                f"| {c.criterion_id} | {c.name} | {'PASS' if c.passed else 'FAIL'} | {key} |"
            )
        return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.3e}"
    return str(v)


def load_report(path: Union[str, Path]) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def acceptance_gate(report: Mapping[str, object], required_ids: Sequence[str]) -> dict:
    """闸门: 所有 required 判据必须存在且 PASS (缺判据也算失败)."""
    by_id = {c["id"]: c for c in report.get("criteria", [])}
    missing = [i for i in required_ids if i not in by_id]
    failed = [i for i in required_ids if i in by_id and not by_id[i]["passed"]]
    return {
        "passed": not missing and not failed,
        "missing": missing,
        "failed": failed,
        "checked": list(required_ids),
    }