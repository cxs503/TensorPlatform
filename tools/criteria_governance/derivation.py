"""派生一致性引擎：把每条判据的"声明"变成"可复算的推导"。

对 ``derivation.method`` 的两条主路：

* ``analytic_identity``  —— 探针直接给出与解析恒等式的残差，阈值算子裁判；
* ``refinement_scan``    —— 探针给 h→h/2 细化扫描序列，**引擎自己**用 log-log 最小二乘
  重算实测衰减指数（不采信探针自报值），再看 |p_实测 − p_声明| ≤ tolerance。

引擎同时产生每个判据的"可派生"标记：只有 ``source_type=analytic`` 且推导链可解析、
探针可执行者才算可派生（经验/启发型不计入可派生率分子）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import kernels
from .probes import PROBES
from .registry import ThresholdRegistry, ValidationFindings, compare_op


@dataclass
class CriterionResult:
    id: str
    source_type: str
    cohort: str
    metric: str
    method: str
    kind: str
    value: float
    op: str
    target: float
    threshold_pass: bool
    verdict: str  # PASS / FAIL / ERROR
    derivable: bool = False
    measured_exponent: Optional[float] = None
    declared_exponent: Optional[float] = None
    exponent_deviation: Optional[float] = None
    exponent_tolerance: Optional[float] = None
    exponent_consistent: Optional[bool] = None
    chain_depth: int = 0
    detail: Dict[str, object] = field(default_factory=dict)
    error: Optional[str] = None
    series: Optional[Dict[str, Sequence[float]]] = None

    def to_dict(self) -> Dict[str, object]:
        payload = {
            "id": self.id,
            "source_type": self.source_type,
            "cohort": self.cohort,
            "metric": self.metric,
            "derivation_method": self.method,
            "probe_kind": self.kind,
            "measured_value": self.value,
            "threshold": {"op": self.op, "value": self.target},
            "threshold_pass": self.threshold_pass,
            "verdict": self.verdict,
            "derivable": self.derivable,
            "chain_depth": self.chain_depth,
            "detail": self.detail,
        }
        if self.measured_exponent is not None:
            payload["refinement"] = {
                "measured_exponent": self.measured_exponent,
                "declared_exponent": self.declared_exponent,
                "deviation": self.exponent_deviation,
                "tolerance": self.exponent_tolerance,
                "consistent": self.exponent_consistent,
            }
        if self.series:
            payload["series"] = {k: list(v) for k, v in self.series.items()}
        if self.error:
            payload["error"] = self.error
        return payload


class DerivationEngine:
    """判据派生引擎（仪器来自 :mod:`.probes`，参数来自判据工件）。"""

    def __init__(self, registry: ThresholdRegistry, probes: Optional[Dict] = None) -> None:
        self.registry = registry
        self.probes = dict(PROBES if probes is None else probes)

    # ------------------------------------------------------------------ --
    def evaluate(self, cid: str, chain_depth: int = 0) -> CriterionResult:
        entry = self.registry.get(cid)
        threshold = entry["threshold"]
        derivation = entry["derivation"]
        probe_spec = entry["probe"]
        op, target = threshold["op"], float(threshold["value"])
        method = derivation.get("method", "")
        result = CriterionResult(
            id=cid,
            source_type=entry["source_type"],
            cohort=entry.get("cohort", ""),
            metric=entry["metric"],
            method=method,
            kind="unknown",
            value=float("nan"),
            op=op,
            target=target,
            threshold_pass=False,
            verdict="ERROR",
            chain_depth=chain_depth,
        )

        probe = self.probes.get(probe_spec["name"])
        if probe is None:
            result.error = f"probe_not_found:{probe_spec['name']}"
            return result

        try:
            observation = probe(dict(probe_spec.get("params") or {}))
        except Exception as exc:  # 探针失败 = honest FAIL，不粉饰
            result.error = f"probe_raised:{type(exc).__name__}:{exc}"
            return result

        result.kind = observation.get("kind", "identity")
        result.value = float(observation["value"])
        result.detail = observation.get("detail") or {}
        result.threshold_pass = compare_op(result.value, op, target,
                                          tol=float(derivation.get("tolerance") or 0.0))
        result.verdict = "PASS" if result.threshold_pass else "FAIL"

        if result.kind == "refinement":
            hs = list(observation["hs"])
            errors = list(observation["errors"])
            measured = kernels.fit_decay_exponent(hs, errors)
            declared = float(derivation["declared_exponent"])
            tol = float(derivation["tolerance"])
            deviation = abs(measured - declared)
            result.measured_exponent = measured
            result.declared_exponent = declared
            result.exponent_deviation = deviation
            result.exponent_tolerance = tol
            result.exponent_consistent = deviation <= tol
            result.series = {"hs": hs, "errors": errors}
            if not result.exponent_consistent:
                result.verdict = "FAIL"
            result.derivable = True
        elif method == "analytic_identity":
            # 恒等式判据：派生链在解析闭式解处终止，推导可复算
            result.derivable = True

        if result.source_type != "analytic":
            result.derivable = False  # 分母只认分析型声明

        if result.verdict == "PASS" and not result.threshold_pass:
            result.verdict = "FAIL"
        return result

    # ------------------------------------------------------------------ --
    def evaluate_all(self, findings: Optional[ValidationFindings] = None) -> List[CriterionResult]:
        findings = findings or self.registry.validate()
        results = []
        for cid in findings.registered:
            results.append(self.evaluate(cid, chain_depth=findings.chain_depth.get(cid, 0)))
        return results

    def summary(self, results: Sequence[CriterionResult]) -> Dict[str, object]:
        analytic = [r for r in results if r.source_type == "analytic"]
        derivable = [r for r in analytic if r.derivable]
        refinement = [r for r in results if r.kind == "refinement"]
        deviations = [r.exponent_deviation for r in refinement if r.exponent_deviation is not None]
        failing = [r.id for r in results if r.verdict != "PASS"]
        return {
            "criteria_total": len(results),
            "criteria_pass": len(results) - len(failing),
            "failing": failing,
            "analytic_total": len(analytic),
            "analytic_derivable": len(derivable),
            "derivability_rate": (len(derivable) / len(analytic)) if analytic else 0.0,
            "refinement_total": len(refinement),
            "refinement_consistent": sum(1 for r in refinement if r.exponent_consistent),
            "max_exponent_deviation": max(deviations) if deviations else None,
            "exponent_tolerance_min": min(
                (r.exponent_tolerance for r in refinement
                 if r.exponent_tolerance is not None), default=None),
        }