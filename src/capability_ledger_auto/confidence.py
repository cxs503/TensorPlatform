"""证据驱动的置信度（confidence）模型。

修复前的事实：``GradeDeterminer`` 里置信度是**规则常量**（NOT_SUPPORTED 0.95 /
MOCK 0.85 / STUB 0.85 / REAL 0.90 / WITHHELD 0.70）。也就是说同一等级的两条
能力，无论证据是「3 个文件真实执行通过的测试 + 五元齐全 + 报告 PASS」还是
「一处静态文本命中」，置信度完全相同——这个数字不携带任何证据信息，却会被
下游当成排序/告警阈值使用（不可用的度量比没有度量更危险）。

本模块把置信度改为从证据特征确定性计算：

============  =========================================================
特征          含义
============  =========================================================
``complete``  五元完整度 = present_count / 5
``quality``   证据链质量 chain_quality ∈ [0,1]
``source``    测试证据强度：executed=1.0 / static=0.4 / 无=0.0
``margin``    判定裕度（REAL：通过率高出阈值的归一化距离；MOCK：低于阈值的距离）
``reasons``   MOCK 独立反证条数（报告裁决/标记/执行失败，取 min(1, k/2)）
``stub``      仅接口实现条数（取 min(1, n/2)）
``ready``     0.5*complete + 0.3*quality + 0.2*source（"接近 REAL 的程度"）
============  =========================================================

各等级公式（权重可经 ``policy['confidence_weights']`` 外置覆盖）::

    REAL          = b + wq*quality + ws*source + wm*margin
    MOCK          = b + wr*reasons + ws*source + wc*complete
    STUB          = b + wt*stub + wn*(无测试) + wa*(无 PASS 裁决)
    WITHHELD      = b + wi*(1 - ready)         # 越接近 REAL，越不"确信不足"
    NOT_SUPPORTED = b                            # 规则保证无任何痕迹

单调性（有测试保证）：证据越强 → REAL/MOCK 置信度越高；越接近 REAL →
WITHHELD 置信度越低。所有值裁剪到 ``[floor, ceiling]``，不出现 1.0 的伪确定性。

零领域语义：只读扫描计数与治理裁决词表，不含任何域词条。
"""
from __future__ import annotations

from typing import Any

# 每级置信度的截断带（防止出现 0.0/1.0 的伪确定性）
CONFIDENCE_BANDS: dict[str, tuple[float, float]] = {
    "REAL": (0.50, 0.98),
    "MOCK": (0.50, 0.97),
    "STUB": (0.45, 0.95),
    "WITHHELD": (0.30, 0.88),
    "NOT_SUPPORTED": (0.80, 0.97),
}

DEFAULT_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "test_pass_threshold": 0.80,
    "real_base": 0.35, "real_quality": 0.30, "real_source": 0.20, "real_margin": 0.15,
    "mock_base": 0.42, "mock_reasons": 0.32, "mock_source": 0.14, "mock_complete": 0.12,
    "stub_base": 0.45, "stub_nodes": 0.26, "stub_no_tests": 0.14, "stub_no_pass": 0.15,
    "withheld_base": 0.34, "withheld_gap": 0.36,
    "not_supported_base": 0.92, "not_supported_gap": 0.05,
}


def _clip(value: float, band: tuple[float, float]) -> float:
    lo, hi = band
    return round(min(hi, max(lo, value)), 4)


def evidence_features(
    grade: str,
    scan: Any,
    chain: Any,
    mock_reason_count: int = 0,
    policy: dict[str, Any] | None = None,
) -> dict[str, float]:
    """从扫描结果与证据链抽取置信度特征（纯函数，确定性）。"""
    p = dict(DEFAULT_CONFIDENCE_WEIGHTS)
    if policy:
        for key, val in policy.get("confidence_weights", {}).items():
            p[key] = float(val)
        if "test_pass_threshold" in policy:
            p["test_pass_threshold"] = float(policy["test_pass_threshold"])

    five_present = int(getattr(chain.five_tuple, "present_count", 0) or 0)
    complete = min(1.0, five_present / 5.0)
    quality = float(getattr(chain, "chain_quality", 0.0) or 0.0)
    source_key = getattr(scan, "test_evidence_source", "") or ""
    source = {"executed": 1.0, "static": 0.4}.get(source_key, 0.0)
    pass_rate = float(getattr(scan, "test_pass_rate", 0.0) or 0.0)
    thr = p["test_pass_threshold"]
    margin_up = max(0.0, min(1.0, (pass_rate - thr) / max(1e-9, 1.0 - thr)))
    margin_down = max(0.0, min(1.0, (thr - pass_rate) / max(1e-9, thr)))
    margin = margin_up if grade != "MOCK" else margin_down

    adj = str(getattr(scan, "adjudicated_level", "") or "").upper()
    has_pass = adj in {"PASS", "REAL"}
    stub_only = int(getattr(scan, "stub_only_code_count", 0) or 0)
    ready = 0.5 * complete + 0.3 * quality + 0.2 * source

    return {
        "complete": round(complete, 4),
        "quality": round(quality, 4),
        "source": round(source, 4),
        "pass_rate": round(pass_rate, 4),
        "margin": round(margin, 4),
        "reasons": round(min(1.0, mock_reason_count / 2.0), 4),
        "stub": round(min(1.0, stub_only / 2.0), 4),
        "has_pass_adjudication": 1.0 if has_pass else 0.0,
        "has_tests": 1.0 if getattr(scan, "has_tests", False) else 0.0,
        "ready": round(ready, 4),
        "raw_reason_count": float(mock_reason_count),
        "raw_stub_nodes": float(stub_only),
        "five_tuple_present": float(five_present),
    }


def confidence_from_features(
    grade: str, features: dict[str, float], policy: dict[str, Any] | None = None,
) -> float:
    """按等级公式把特征映射为置信度（确定性、可解释）。"""
    p = dict(DEFAULT_CONFIDENCE_WEIGHTS)
    if policy:
        for key, val in policy.get("confidence_weights", {}).items():
            p[key] = float(val)
    band = CONFIDENCE_BANDS.get(grade, (0.0, 1.0))

    if grade == "REAL":
        raw = (p["real_base"] + p["real_quality"] * features["quality"]
               + p["real_source"] * features["source"] + p["real_margin"] * features["margin"])
    elif grade == "MOCK":
        raw = (p["mock_base"] + p["mock_reasons"] * features["reasons"]
               + p["mock_source"] * features["source"] + p["mock_complete"] * features["complete"])
    elif grade == "STUB":
        raw = (p["stub_base"] + p["stub_nodes"] * features["stub"]
               + p["stub_no_tests"] * (1.0 - features["has_tests"])
               + p["stub_no_pass"] * (1.0 - features["has_pass_adjudication"]))
    elif grade == "WITHHELD":
        raw = p["withheld_base"] + p["withheld_gap"] * (1.0 - features["ready"])
    elif grade == "NOT_SUPPORTED":
        raw = p["not_supported_base"] + p["not_supported_gap"] * (1.0 - features["complete"])
    else:
        raw = 0.5
    return _clip(raw, band)


def evidence_confidence(
    grade: str,
    scan: Any,
    chain: Any,
    mock_reason_count: int = 0,
    policy: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float]]:
    """一步得到 ``(置信度, 特征)``；特征随产物落盘，便于独立复算。"""
    feats = evidence_features(grade, scan, chain, mock_reason_count, policy)
    return confidence_from_features(grade, feats, policy), feats


__all__ = [
    "CONFIDENCE_BANDS",
    "DEFAULT_CONFIDENCE_WEIGHTS",
    "evidence_features",
    "confidence_from_features",
    "evidence_confidence",
]