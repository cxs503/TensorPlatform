"""判据注册表 v2 与派生引擎 v2：入册 / 可裁 / 观测阶复算 / 重推导闭环 / 覆盖率账本。

与 v1（``registry.py`` + ``derivation.py``）的分工：

* v1 管"N 条判据是否合规、探针是否通过"；
* v2 管"**阈值自身是否是 h 的函数**"——观测阶与被声明指数的偏差、``threshold(h)``
  显式表达式的重推导闭环、以及"阈值被拟合到实测"这一负控的自动降级。

引擎不信任判据自报的任何实测数字：指数由 ``hs``/``errors`` 序列自己重算，
闭环比值由表达式自己求值，领域语义只经 ``source_ref`` 字符串注入。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from . import kernels, schema_v2
from .probes import PROBES
from .probes_linear import SYNTHETIC_PROBES

ALL_PROBES: Dict[str, object] = {**PROBES, **SYNTHETIC_PROBES}

#: 重推导闭环允许的"实测/预测"比值区间（C5）
CLOSURE_RATIO_BOUNDS = (0.5, 2.0)

#: 观测阶与声明指数允许偏差（C2）
EXPONENT_TOLERANCE_DEFAULT = 0.3

#: 判"阈值被拟合到实测"的相对容差（C7 负控）
FIT_TO_MEASUREMENT_TOL = 1e-6

#: 回读类探针：测量值**就是**注册阈值（来源绑定），不参与"拟合实测"判定
BINDING_PROBES = ("source_constant",)


# --------------------------------------------------------------------------- #
# 工件加载
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_bundle(path: Path, *, review_date: Optional[str] = None) -> List[Dict]:
    """读入判据工件（``{..., "criteria": [...]}``）并规范化成 v2 条目。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_items = payload.get("criteria") if isinstance(payload, dict) else payload
    digest = file_sha256(path)
    entries: List[Dict] = []
    for item in raw_items or []:
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict) and item.get("source_ref_stub") is not None:
            source_ref = None
        normalized = schema_v2.normalize_entry(
            item, file_sha256=digest, locator=str(Path(path).as_posix()),
            review_date=review_date)
        if source_ref is not None:
            normalized["source_ref"] = source_ref
        normalized["_artifact"] = str(Path(path).as_posix())
        entries.append(normalized)
    return entries


def load_dir(root: Path, *, review_dates: Optional[Dict[str, str]] = None) -> List[Dict]:
    review_dates = review_dates or {}
    entries: List[Dict] = []
    for path in sorted(Path(root).glob("*.criteria.json")):
        entries.extend(load_bundle(path, review_date=review_dates.get(Path(path).as_posix())))
    return entries


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
@dataclass
class RegistryV2Findings:
    registered: List[str] = field(default_factory=list)
    quarantined: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    adjudicable: List[str] = field(default_factory=list)
    non_adjudicable: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "registered_total": len(self.registered),
            "registered": sorted(self.registered),
            "quarantined_total": len(self.quarantined),
            "quarantined": self.quarantined,
            "warnings": self.warnings,
            "adjudicable_total": len(self.adjudicable),
            "adjudicable": sorted(self.adjudicable),
            "non_adjudicable_total": len(self.non_adjudicable),
            "non_adjudicable": self.non_adjudicable,
        }


class RegistryV2:
    """v2 注册表：条目集合 + 校验 + 可裁集合 + 覆盖率账本。"""

    def __init__(self, entries: Iterable[Dict]) -> None:
        self.entries: List[Dict] = [dict(e) for e in entries]
        self._by_id: Dict[str, Dict] = {}
        for entry in self.entries:
            cid = entry.get("criterion_id")
            if cid is not None:
                self._by_id.setdefault(str(cid), entry)

    # ------------------------------------------------------------- 构造 --
    @classmethod
    def from_bundles(cls, paths: Sequence[Path], *, review_dates: Optional[Dict[str, str]] = None) -> "RegistryV2":
        review_dates = review_dates or {}
        entries: List[Dict] = []
        for path in paths:
            entries.extend(load_bundle(Path(path), review_date=review_dates.get(Path(path).as_posix())))
        return cls(entries)

    def get(self, cid: str) -> Dict:
        return self._by_id[cid]

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------- 校验 --
    def validate(self) -> RegistryV2Findings:
        findings = RegistryV2Findings()
        seen: set = set()
        for entry in self.entries:
            cid = str(entry.get("criterion_id") or "<unnamed>")
            reasons = schema_v2.validate_entry(entry, known_ids=seen)
            if reasons:
                findings.quarantined.append({"id": cid, "reasons": reasons,
                                             "artifact": entry.get("_artifact")})
                continue
            seen.add(cid)
            findings.registered.append(cid)

            verdict = schema_v2.adjudicability(entry)
            if verdict["adjudicable"]:
                findings.adjudicable.append(cid)
            else:
                findings.non_adjudicable.append({"id": cid, "reason": str(verdict["reason"]),
                                                 "source_type": str(entry.get("source_type"))})
                if entry.get("source_type") == "heuristic":
                    findings.warnings.append(
                        f"[C1 显式标注] {cid} source_type=heuristic → 不可裁，已剔除可裁集合")
        return findings

    # --------------------------------------------------------- 覆盖率账本 --
    def coverage_ledger(self, findings: Optional[RegistryV2Findings] = None) -> Dict[str, object]:
        findings = findings or self.validate()
        registered = [self._by_id[cid] for cid in findings.registered if cid in self._by_id]
        total = len(registered)
        by_type: Dict[str, int] = {}
        for entry in registered:
            key = str(entry.get("source_type"))
            by_type[key] = by_type.get(key, 0) + 1

        sourced = [e for e in registered
                   if not schema_v2.validate_source_ref(e.get("source_ref"), e.get("source_type"))]
        derivable = [e for e in registered if self.is_derivable(e)]
        adjudicable = [e for e in registered
                       if schema_v2.adjudicability(e)["adjudicable"]]

        def frac(items: Sequence[Dict]) -> float:
            return (len(items) / total) if total else 0.0

        return {
            "registered_total": total,
            "quarantined_total": len(findings.quarantined),
            "by_source_type": dict(sorted(by_type.items())),
            "sourced_total": len(sourced),
            "sourced_fraction": frac(sourced),
            "derivable_total": len(derivable),
            "derivable_fraction": frac(derivable),
            "adjudicable_total": len(adjudicable),
            "adjudicable_fraction": frac(adjudicable),
            "source_type_missing": sum(1 for e in registered if not e.get("source_type")),
            "source_type_missing_fraction": (
                sum(1 for e in registered if not e.get("source_type")) / total) if total else 0.0,
            "heuristic_total": by_type.get("heuristic", 0),
            "heuristic_fraction": (by_type.get("heuristic", 0) / total) if total else 0.0,
            "depends_on_h_total": sum(
                1 for e in registered
                if any((d or {}).get("param") == "h" for d in (e.get("depends_on") or []))),
            "expression_total": sum(
                1 for e in registered if (e.get("derivation") or {}).get("expression")),
        }

    @staticmethod
    def is_derivable(entry: Dict) -> bool:
        """可派生 = 有探针 + (显式表达式 or 声明依赖指数 or 解析恒等派生)。"""
        probe = entry.get("probe") or {}
        if not probe.get("name") or probe["name"] not in ALL_PROBES:
            return False
        derivation = entry.get("derivation") or {}
        if derivation.get("expression"):
            return True
        if entry.get("depends_on"):
            return True
        return derivation.get("method") == "analytic_identity"


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
def _compare(value: float, op: str, target: float, tol: float = 1e-12) -> bool:
    if op == "<=":
        return value <= target
    if op == ">=":
        return value >= target
    if op == "<":
        return value < target
    if op == ">":
        return value > target
    if op == "==":
        return abs(value - target) <= max(tol, 0.0)
    if op == "!=":
        return abs(value - target) > max(tol, 0.0)
    raise ValueError(f"unsupported comparator {op!r}")


def evaluate_entry(entry: Dict, *, probes: Optional[Dict] = None) -> Dict[str, object]:
    """执行探针、重算观测阶、跑重推导闭环，并做"阈值=实测"降级检测。"""
    probes = ALL_PROBES if probes is None else probes
    probe = entry.get("probe") or {}
    cid = str(entry.get("criterion_id"))
    outcome: Dict[str, object] = {
        "criterion_id": cid,
        "source_type": entry.get("source_type"),
        "comparator": entry.get("comparator"),
        "threshold": entry.get("threshold"),
        "measured": None,
        "threshold_pass": None,
        "verdict": "ERROR",
        "exponent": None,
        "closure": None,
        "fit_to_measurement": None,
    }

    fn = probes.get(probe.get("name"))
    if fn is None:
        outcome["error"] = f"probe_not_found:{probe.get('name')}"
        return outcome
    try:
        observation = fn(dict(probe.get("params") or {}))
    except Exception as exc:  # honest FAIL：探针炸了就是炸了
        outcome["error"] = f"probe_raised:{type(exc).__name__}:{exc}"
        return outcome

    measured = float(observation["value"])
    outcome["measured"] = measured
    outcome["kind"] = observation.get("kind")
    outcome["detail"] = observation.get("detail") or {}
    outcome["series"] = {k: list(observation[k]) for k in ("hs", "errors") if k in observation}

    threshold = entry.get("threshold")
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        passed = _compare(measured, str(entry.get("comparator")), float(threshold),
                          tol=float((entry.get("derivation") or {}).get("tolerance") or 0.0))
        outcome["threshold_pass"] = passed
        outcome["verdict"] = "PASS" if passed else "FAIL"

    # ---- C2：观测阶 vs 声明指数（引擎自己重算，不采信自报值）
    declared = (entry.get("derivation") or {}).get("declared_exponent")
    if declared is not None and observation.get("kind") == "refinement":
        hs = [float(h) for h in observation["hs"]]
        errors = [float(e) for e in observation["errors"]]
        measured_exponent = kernels.fit_decay_exponent(hs, errors)
        deviation = abs(measured_exponent - float(declared))
        tolerance = float((entry.get("derivation") or {}).get("tolerance") or EXPONENT_TOLERANCE_DEFAULT)
        outcome["exponent"] = {
            "declared": float(declared),
            "measured": measured_exponent,
            "deviation": deviation,
            "tolerance": tolerance,
            "consistent": deviation <= tolerance,
        }
        if not outcome["exponent"]["consistent"]:
            outcome["verdict"] = "FAIL"

    # ---- C5：threshold(h) 显式表达式的重推导闭环
    expression = (entry.get("derivation") or {}).get("expression")
    if expression and observation.get("kind") == "refinement":
        outcome["closure"] = closure_check(
            expression, list(observation["hs"]), list(observation["errors"]),
            dict(observation.get("detail") or {}), entry.get("depends_on") or [],
            safety_factor=(entry.get("derivation") or {}).get("safety_factor"))

    # ---- C7：阈值被拟合到实测 → 降级（只对"误差量上界"型判据生效，见 fit_suspicion）
    if outcome["measured"] is not None and isinstance(threshold, (int, float)) \
            and not isinstance(threshold, bool):
        outcome["fit_to_measurement"] = fit_ratio(float(threshold), measured)
        if probe.get("name") in BINDING_PROBES:
            outcome["fit_rule"] = "binding_probe_exempt"
        elif fit_suspicion(str(entry.get("comparator")), float(threshold), measured,
                           outcome["fit_to_measurement"]):
            outcome["verdict"] = "FAIL"
            outcome["demoted_to_empirical"] = True
        else:
            outcome["fit_rule"] = "not_applicable_or_clean"
    return outcome


def fit_suspicion(comparator: str, threshold: float, measured: float, ratio: float) -> bool:
    """C7 判据：这条阈值是不是"照着实测值抄下来的"。

    只对**误差量上界**型判据生效（``<=`` 且阈值/实测同时为正）：恒等式判据（``==``）
    的阈值是精确目标（秩=1、残差=0），实测恰好等于它是**正确**而非拟合，不能误伤。
    """
    if comparator not in ("<=", "<"):
        return False
    if threshold <= 0.0 or measured <= 0.0:
        return False
    return ratio <= FIT_TO_MEASUREMENT_TOL


def fit_ratio(threshold: float, measured: float) -> float:
    """阈值与实测的相对距离（0 = 阈值就是实测值）。"""
    return abs(threshold - measured) / max(abs(measured), 1e-12)


def demote_fitted(registry: RegistryV2, outcomes: Sequence[Dict]) -> List[Dict]:
    """把"阈值=实测"的判据就地降级为 empirical（C7 负控的机器执行部分）。"""
    demotions: List[Dict] = []
    by_id = {o["criterion_id"]: o for o in outcomes}
    for entry in registry.entries:
        cid = str(entry.get("criterion_id"))
        outcome = by_id.get(cid)
        if not outcome or not outcome.get("demoted_to_empirical"):
            continue
        before = entry.get("source_type")
        entry["source_type"] = "empirical"
        entry["fit_to_measurement"] = True
        demotions.append({
            "criterion_id": cid,
            "source_type_before": before,
            "source_type_after": "empirical",
            "fit_ratio": outcome.get("fit_to_measurement"),
            "reason": "threshold_equals_measured_value",
        })
    return demotions


def closure_check(expression: str, hs: Sequence[float], errors: Sequence[float],
                  detail: Dict, depends_on: Sequence[Dict],
                  safety_factor: Optional[float] = None) -> Dict[str, object]:
    """在 h/2、h/4 两档上验证 threshold(h) 表达式与实测误差的比值 ∈ [0.5, 2]。"""
    base_index = max(len(hs) - 3, 0)
    levels = []
    constant = detail.get("analytic_error_constant")
    derivation = {}
    for item in depends_on or []:
        derivation = derivation  # 依赖量本身不提供常数；常数来源见下方 detail/entry
    for offset, label in enumerate(("h", "h/2", "h/4")):
        idx = base_index + offset
        if idx >= len(hs):
            continue
        h = float(hs[idx])
        values = {"h": h}
        if constant is not None:
            values["C"] = float(constant)
        if safety_factor is not None:
            values["SF"] = float(safety_factor)
        try:
            predicted = schema_v2.evaluate_threshold_law(expression, values)
        except schema_v2.ThresholdLawError as exc:
            return {"expression": expression, "error": str(exc), "levels": levels,
                    "closure_ok": False}
        measured = float(errors[idx])
        ratio = measured / predicted if predicted else float("inf")
        levels.append({"level": label, "h": h, "predicted": predicted,
                       "measured": measured, "ratio": ratio,
                       "in_bounds": CLOSURE_RATIO_BOUNDS[0] <= ratio <= CLOSURE_RATIO_BOUNDS[1]})
    checked = [lvl for lvl in levels if lvl["level"] in ("h/2", "h/4")]
    return {
        "expression": expression,
        "levels": levels,
        "bounds": list(CLOSURE_RATIO_BOUNDS),
        "closure_ok": bool(checked) and all(lvl["in_bounds"] for lvl in checked),
        "checked_levels": [lvl["level"] for lvl in checked],
    }


__all__ = [
    "ALL_PROBES", "CLOSURE_RATIO_BOUNDS", "EXPONENT_TOLERANCE_DEFAULT",
    "FIT_TO_MEASUREMENT_TOL", "BINDING_PROBES", "RegistryV2", "RegistryV2Findings", "load_bundle",
    "load_dir", "evaluate_entry", "demote_fitted", "closure_check", "fit_ratio",
    "file_sha256",
]