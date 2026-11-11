"""能力台账语义三元分层：capability（能力声明）→ delivery（交付物）→ evidence（证据链）。

为什么是三元而不是一元
----------------------
历史台账把"能力"记成一维等级（REAL/MOCK/STUB/WITHHELD）。这个一维标签同时承担了
三种不同性质的信息：

* **capability**：对外承诺了什么（一句话，可判真假）
* **delivery**：拿到什么实物（模块/API/文档/测试/工件，路径可校验）
* **evidence**：凭什么说它能用（测试通过？解析解对标？外部基准？工件指纹？）

一维标签的后果：把"有代码"当成"有证据"（→ 过度声明 REAL），或把"没证据"错记成
"没实现"（→ 把 NOT_SUPPORTED 的缺失抹掉，报告里看不出缺什么）。

本模块立三条硬约束（机器可判）
------------------------------
1. ``REAL`` 必须有 ≥1 个 delivery（路径存在）且 ≥1 条 evidence 且 evidence 类型至少含
   一种"可执行/可对标"证据（test / reference / artifact-digest）。
2. ``NOT_SUPPORTED`` 必须带非空 ``reason``，且**不得**携带 delivery/evidence——
   缺失必须显式登记，不许用"暂未评估"掩盖。
3. 任何 delivery 的 ``path`` 若声明了 ``sha256``，校验时必须相符，否则该 delivery 失效
   （防止"路径还在、内容已换"的僵尸交付物）。

等级语义（与平台证据分级对齐）
------------------------------
``REAL`` 能力有实物且有证据；``MOCK`` 有实物但证据是占位/随机；``STUB`` 只有壳；
``WITHHELD`` 有实物但证据不足（外部数据缺失）；``NOT_SUPPORTED`` 明确不支持。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

GRADES = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
_STRONG_EVIDENCE = ("test", "reference", "artifact-digest", "external-benchmark")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Delivery:
    """交付物：能力对外承诺的实物载体。"""

    delivery_id: str
    kind: str                      # module | api | doc | test | artifact
    path: str
    sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"delivery_id": self.delivery_id, "kind": self.kind, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class Evidence:
    """证据：凭什么说交付物可用。"""

    evidence_id: str
    kind: str                      # test | reference | artifact-digest | benchmark | manual-review | placeholder
    ref: str
    digest: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "kind": self.kind, "ref": self.ref, "digest": self.digest, "note": self.note}


@dataclass(frozen=True)
class CapabilityEntry:
    """一条能力台账条目：capability + deliveries + evidence。"""

    capability_id: str
    statement: str                 # 能力声明（一句话，可判真假）
    grade: str
    deliveries: tuple[Delivery, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    reason: str | None = None      # NOT_SUPPORTED / WITHHELD 必填
    scope_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "statement": self.statement,
            "grade": self.grade,
            "deliveries": [d.as_dict() for d in self.deliveries],
            "evidence": [e.as_dict() for e in self.evidence],
            "reason": self.reason,
            "scope_note": self.scope_note,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CapabilityEntry":
        return CapabilityEntry(
            capability_id=d["capability_id"],
            statement=d.get("statement", ""),
            grade=d.get("grade", "WITHHELD"),
            deliveries=tuple(Delivery(**x) for x in d.get("deliveries") or []),
            evidence=tuple(Evidence(**x) for x in d.get("evidence") or []),
            reason=d.get("reason"),
            scope_note=d.get("scope_note"),
        )


def triad_validate(
    entries: Iterable[CapabilityEntry],
    *,
    root: Path | str = ".",
    check_paths: bool = True,
    check_digests: bool = True,
) -> dict[str, Any]:
    """校验三元分层的自洽性，返回可入报告的台账 + 违规清单。"""
    root = Path(root)
    rows: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    grade_counts = {g: 0 for g in GRADES}
    for e in entries:
        problems: list[str] = []
        if e.grade not in GRADES:
            problems.append(f"unknown_grade:{e.grade}")
        if not e.statement.strip():
            problems.append("empty_statement")
        deliveries = []
        for d in e.deliveries:
            exists = None
            digest_ok = None
            if check_paths:
                p = root / d.path
                exists = p.exists()
                if not exists:
                    problems.append(f"delivery_path_missing:{d.path}")
                elif check_digests and d.sha256:
                    digest_ok = _sha256_file(p) == d.sha256
                    if not digest_ok:
                        problems.append(f"delivery_digest_mismatch:{d.path}")
            deliveries.append({**d.as_dict(), "exists": exists, "digest_ok": digest_ok})

        strong = [ev for ev in e.evidence if ev.kind in _STRONG_EVIDENCE]
        if e.grade == "REAL":
            if not e.deliveries:
                problems.append("REAL_without_delivery")
            if not e.evidence:
                problems.append("REAL_without_evidence")
            elif not strong:
                problems.append("REAL_without_strong_evidence")
        if e.grade == "NOT_SUPPORTED":
            if not (e.reason or "").strip():
                problems.append("NOT_SUPPORTED_without_reason")
            if e.deliveries:
                problems.append("NOT_SUPPORTED_with_deliveries")
            if e.evidence:
                problems.append("NOT_SUPPORTED_with_evidence")
        if e.grade in ("WITHHELD", "MOCK", "STUB") and not (e.reason or "").strip():
            problems.append(f"{e.grade}_without_reason")
        if len([ev for ev in e.evidence if ev.kind == "placeholder"]) and e.grade == "REAL":
            problems.append("REAL_with_placeholder_evidence")

        grade_counts[e.grade] = grade_counts.get(e.grade, 0) + 1
        rows.append({**e.as_dict(), "deliveries_checked": deliveries, "violations": problems})
        for p in problems:
            violations.append({"capability_id": e.capability_id, "problem": p})

    n = len(rows)
    return {
        "n_entries": n,
        "grade_counts": grade_counts,
        "n_violations": len(violations),
        "violations": violations,
        "entries": rows,
        "not_supported_registered": grade_counts.get("NOT_SUPPORTED", 0),
        "layering_completeness": (
            sum(1 for r in rows if r["deliveries"] and r["evidence"]) / n if n else 0.0
        ),
    }


def ledger_from_json(path: Path | str) -> list[CapabilityEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("capabilities") if isinstance(data, dict) else data
    return [CapabilityEntry.from_dict(x) for x in items or []]