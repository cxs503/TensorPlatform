"""引擎数据模型 + 通用数值工具（无 IO、无领域知识）。

裁决记录（CaseVerdict / SuiteVerdict）是**证据**而非日志：每个 check 都带
``passed`` / ``detail`` / ``actual``，失败必须可解释（禁止静默通过）。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


class ConfigError(ValueError):
    """配置本身不合法（fail-closed：配置错 ≠ 算例失败，退出码区分）。"""


def is_finite(x: Any) -> bool:
    """严格的有限数判定：bool / None / str / NaN / inf 全部为 False。"""
    if isinstance(x, bool) or x is None:
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(float(x))


def rel_error(actual: Any, reference: Any, eps: float = 1e-300) -> Optional[float]:
    """相对误差 |a-r| / max(|r|, eps)。任一输入非有限/不可转数 → None（不返回 0）。"""
    try:
        a, r = float(actual), float(reference)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(r)):
        return None
    denom = max(abs(r), eps)
    return abs(a - r) / denom


def rel_drift(current: Any, baseline: Any, eps: float = 1e-300) -> Optional[float]:
    """相对漂移 |cur-base| / max(|base|, eps)；分母贴 0 时用 eps 避免爆炸。"""
    return rel_error(current, baseline, eps=eps)


@dataclass
class Check:
    """单条判据的裁决记录。"""

    metric: str
    op: str
    passed: bool
    detail: str
    actual: Any = None
    threshold: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaseVerdict:
    """单算例裁决：判据门 + 数值 + 出处。"""

    case_id: str
    title: str = ""
    passed: bool = False
    status: str = "fail"            # pass | fail | error
    metrics: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    checks: List[Check] = field(default_factory=list)
    error: Optional[str] = None
    failed_checks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class SuiteVerdict:
    """整 Suite 裁决 + 汇总计数（供 KPI / 回归 / 报告消费）。"""

    suite: str
    run_id: str
    passed: bool = False
    cases: List[CaseVerdict] = field(default_factory=list)
    checks: List[Check] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite": self.suite,
            "run_id": self.run_id,
            "passed": self.passed,
            "counts": dict(self.counts),
            "checks": [c.to_dict() for c in self.checks],
            "cases": [c.to_dict() for c in self.cases],
        }