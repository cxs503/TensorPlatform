"""黄金集：加载 + 跑推断 + 一致率/混淆矩阵/逐类 P-R-F1。

黄金集是**冻结的人工标签文件**（``fixtures/golden_set.json``），标签在跑推断
之前写定，文件 sha256 记入冻结清单（``fixtures/golden_set.sha256``）与报告。
本模块只做"读标签 → 跑引擎 → 比对"，不改标签。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .engine import infer
from .ladder import LADDER, rank_of

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_set.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_golden(path: Optional[Path | str] = None) -> Dict[str, Any]:
    """读取黄金集，附上文件 sha256 与行数校验（行数不是 40 直接报错）。"""
    p = Path(path) if path else DEFAULT_GOLDEN
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    if len(rows) != 40:
        raise ValueError(f"黄金集行数应为 40，实为 {len(rows)}")
    keys = [r["capability_key"] for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("黄金集存在重复 capability_key")
    data["_sha256"] = _sha256_file(p)
    data["_path"] = str(p)
    return data


def frozen_sha(golden: Mapping[str, Any]) -> str:
    return str(golden.get("_sha256", ""))


@dataclass
class GoldenReport:
    total: int
    correct: int
    accuracy: float
    macro_f1: float
    per_class: Dict[str, Dict[str, float]] = field(default_factory=dict)
    confusion: Dict[str, Dict[str, int]] = field(default_factory=dict)
    mismatches: List[Dict[str, Any]] = field(default_factory=list)
    sha256: str = ""
    labels: Sequence[str] = LADDER
    naive_baseline: Dict[str, Any] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        """判据 (a)：40 行黄金集 F1 ≥ 0.9。"""
        return self.macro_f1 >= 0.9

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 6),
            "macro_f1": round(self.macro_f1, 6),
            "per_class": {k: {m: round(v, 6) for m, v in d.items()} for k, d in self.per_class.items()},
            "confusion": self.confusion,
            "mismatches": self.mismatches,
            "golden_set_sha256": self.sha256,
            "labels": list(self.labels),
            "threshold": 0.9,
            "naive_baseline": dict(self.naive_baseline),
            "passes": self.passes,
        }


def _macro_f1(expected: Sequence[str], predicted: Sequence[str]) -> float:
    f1s: List[float] = []
    for label in LADDER:
        tp = sum(1 for e, p in zip(expected, predicted) if e == label and p == label)
        fp = sum(1 for e, p in zip(expected, predicted) if e != label and p == label)
        fn = sum(1 for e, p in zip(expected, predicted) if e == label and p != label)
        if tp:
            f1s.append(_prf(tp, fp, fn)["f1"])
    return sum(f1s) / len(f1s) if f1s else 0.0


def naive_level(row: Mapping[str, Any]) -> str:
    """naive 基线读法：证据里出现的**最高环**直接当标签（不看有效性/作用域/矛盾）。

    这是"自述式"读法的形式化下限，用来证明 fail-closed 引擎不是恒等映射。
    """
    ranks = []
    for node in row.get("nodes") or []:
        rung = str((node or {}).get("rung", ""))
        if rung in LADDER:
            ranks.append(rank_of(rung))
    return LADDER[max(ranks)] if ranks else LADDER[0]


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": f1}


def evaluate_golden(golden: Mapping[str, Any]) -> GoldenReport:
    """对黄金集逐行推断并统计一致率 / 混淆矩阵 / 宏平均 F1。"""
    rows = golden.get("rows") or []
    confusion: Dict[str, Dict[str, int]] = {a: {b: 0 for b in LADDER} for a in LADDER}
    predicted: List[str] = []
    expected: List[str] = []
    mismatches: List[Dict[str, Any]] = []
    for row in rows:
        exp = str(row["expected_level"])
        result = infer(
            row["capability_key"],
            row.get("nodes") or [],
            claimed_level=row.get("claim"),
            contradictions=row.get("contradictions") or [],
        )
        pred = result.level
        confusion.setdefault(exp, {b: 0 for b in LADDER})
        confusion[exp][pred] = confusion[exp].get(pred, 0) + 1
        expected.append(exp)
        predicted.append(pred)
        if pred != exp:
            mismatches.append({
                "capability_key": row["capability_key"],
                "archetype": row.get("archetype"),
                "expected": exp,
                "predicted": pred,
                "reasons": result.reasons,
                "break_rung": result.break_rung,
            })
    naive_pred = [naive_level(r) for r in rows]
    naive_acc = (sum(1 for e, p in zip(expected, naive_pred) if e == p) / len(rows)) if rows else 0.0
    naive_baseline = {
        "rule": "max rung present (validity/scope/contradiction ignored)",
        "accuracy": naive_acc,
        "macro_f1": _macro_f1(expected, naive_pred),
        "differing_rows": sum(1 for a, b in zip(naive_pred, predicted) if a != b),
    }
    total = len(rows)
    correct = sum(1 for e, p in zip(expected, predicted) if e == p)
    per_class: Dict[str, Dict[str, float]] = {}
    f1s: List[float] = []
    for label in LADDER:
        tp = sum(1 for e, p in zip(expected, predicted) if e == label and p == label)
        fp = sum(1 for e, p in zip(expected, predicted) if e != label and p == label)
        fn = sum(1 for e, p in zip(expected, predicted) if e == label and p != label)
        scores = _prf(tp, fp, fn)
        per_class[label] = scores
        if tp:  # 宏平均只对出现过的类取（未见类 F1 无定义，不计 0 以免虚降）
            f1s.append(scores["f1"])
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return GoldenReport(
        total=total,
        correct=correct,
        accuracy=correct / total if total else 0.0,
        macro_f1=macro_f1,
        per_class=per_class,
        confusion=confusion,
        mismatches=mismatches,
        sha256=frozen_sha(golden),
        naive_baseline=naive_baseline,
    )