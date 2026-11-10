"""验证锚模型与单锚判决（verification anchor & per-anchor verdict）。

本模块把「一次对标」抽象成 **验证锚（verification anchor）**：一个可机器解析的
(相对误差 rel_error, 参考值相对不确定度 uncertainty, 证据根 independence_key)。
判决三态 **PASS / FAIL / UNDECIDABLE**，其中 UNDECIDABLE 是引入的不确定度带
（uncertainty band）导致的不可判态：

    判决线 tol 为不可接受误差上限；band = z * uncertainty（z 为覆盖因子，默认 2σ）
    - rel_error + band <  tol            → PASS（决定性通过）
    - rel_error - band >  tol            → FAIL（决定性否证）
    - 否则（区间跨过 tol）               → UNDECIDABLE（证据不足以裁决）
    - 无参考值 rel_error is None         → UNDECIDABLE（验证锚缺失）

零领域语义：只认数字与证据根，不认任何域词条。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

PASS = "PASS"
FAIL = "FAIL"
UNDECIDABLE = "UNDECIDABLE"
ANCHOR_VERDICTS = (PASS, FAIL, UNDECIDABLE)

# 锚类型（证据来源类别）；REAL 判据要求至少两类独立来源，防止同源重复计数
ANCHOR_KINDS = ("analytic", "reference", "experiment", "benchmark", "field")


@dataclass(frozen=True)
class VerificationAnchor:
    """单条验证锚。

    Parameters
    ----------
    anchor_id : str
        锚唯一标识（同一能力下唯一）。
    kind : str
        证据来源类别，取自 :data:`ANCHOR_KINDS`。
    rel_error : float | None
        与被验证参考值的相对误差（|计算-参考|/参考）。None 表示无参考值可算。
    uncertainty : float
        参考值自身相对不确定度（1σ）。默认 0.0（参考值视为精确）。
    independence_key : str
        证据根标识（数据集哈希/文献 DOI/算例 ID）。同键的锚视为**非独立**，
        在独立性计数中只计一次；缺省时退化为 anchor_id（即每个锚各自独立）。
    has_reference_value : bool
        是否真的取到了参考值（缺参考值即 fail-closed 不可判）。
    tolerance : float | None
        该锚自带的判决线；None 表示使用策略默认 tol。
    """

    anchor_id: str
    kind: str
    rel_error: float | None = None
    uncertainty: float = 0.0
    independence_key: str = ""
    has_reference_value: bool = True
    tolerance: float | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.anchor_id:
            raise ValueError("anchor_id 不能为空")
        if self.kind not in ANCHOR_KINDS:
            raise ValueError(f"未知锚类型: {self.kind!r}（应取 {ANCHOR_KINDS}）")
        if self.rel_error is not None and self.rel_error < 0.0:
            raise ValueError(f"rel_error 必须非负: {self.rel_error}")
        if self.uncertainty < 0.0:
            raise ValueError(f"uncertainty 必须非负: {self.uncertainty}")
        if self.tolerance is not None and self.tolerance <= 0.0:
            raise ValueError(f"tolerance 必须为正: {self.tolerance}")

    # ── 机器可算的标识 ────────────────────────────────────────────
    @property
    def effective_independence_key(self) -> str:
        return self.independence_key or self.anchor_id

    def payload(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "kind": self.kind,
            "rel_error": self.rel_error,
            "uncertainty": self.uncertainty,
            "independence_key": self.effective_independence_key,
            "has_reference_value": self.has_reference_value,
            "tolerance": self.tolerance,
            "notes": list(self.notes),
        }

    def digest(self) -> str:
        """锚的内容哈希（进 provenance 链，防手抄/篡改）。"""
        blob = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnchorVerdict:
    """单锚判决结果（完全由数字决定，可复算）。"""

    anchor_id: str
    kind: str
    verdict: str
    rel_error: float | None
    tolerance: float
    band: float
    margin: float | None  # rel_error - tolerance（正=超线）
    reason: str
    independence_key: str = ""
    adverse: bool = False  # UNDECIDABLE 且中心估计偏坏侧（rel_error > tol）
    digest: str = ""

    @property
    def decisive(self) -> bool:
        return self.verdict in (PASS, FAIL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "kind": self.kind,
            "verdict": self.verdict,
            "rel_error": None if self.rel_error is None else round(self.rel_error, 6),
            "tolerance": round(self.tolerance, 6),
            "band": round(self.band, 6),
            "margin": None if self.margin is None else round(self.margin, 6),
            "reason": self.reason,
            "independence_key": self.independence_key,
            "adverse": self.adverse,
            "decisive": self.decisive,
            "digest": self.digest,
        }


def judge_anchor(
    anchor: VerificationAnchor,
    tol: float,
    coverage_z: float = 2.0,
) -> AnchorVerdict:
    """对单锚做机器判决（PASS/FAIL/UNDECIDABLE）。

    fail-closed 规则：任何无法把不确定度区间与判决线分离的情形都判 UNDECIDABLE，
    绝不默认判 PASS。
    """
    tol_eff = anchor.tolerance if anchor.tolerance is not None else tol
    band = coverage_z * anchor.uncertainty
    key = anchor.effective_independence_key

    def _mk(verdict: str, reason: str, adverse: bool = False) -> AnchorVerdict:
        return AnchorVerdict(
            anchor_id=anchor.anchor_id,
            kind=anchor.kind,
            verdict=verdict,
            rel_error=anchor.rel_error,
            tolerance=tol_eff,
            band=band,
            margin=(None if anchor.rel_error is None else anchor.rel_error - tol_eff),
            reason=reason,
            independence_key=key,
            adverse=adverse,
            digest=anchor.digest(),
        )

    # 1) 缺参考值 → 不可判（fail-closed）
    if not anchor.has_reference_value or anchor.rel_error is None:
        return _mk(
            UNDECIDABLE,
            "验证锚无参考值（has_reference_value=False 或 rel_error=None），无法裁决",
        )

    err = anchor.rel_error
    # 2) 区间与判决线可分离 → 决定性判决
    if err + band < tol_eff:
        return _mk(PASS, f"rel_error({err:.4f})+band({band:.4f}) < tol({tol_eff:.4f})")
    if err - band > tol_eff:
        return _mk(FAIL, f"rel_error({err:.4f})-band({band:.4f}) > tol({tol_eff:.4f})")

    # 3) 区间跨过判决线 → 不可判
    adverse = err > tol_eff
    return _mk(
        UNDECIDABLE,
        (
            f"不确定度带跨判决线：|rel_error-tol|={abs(err - tol_eff):.4f} "
            f"≤ band={band:.4f}（中心估计{'偏坏' if adverse else '偏好'}侧）"
        ),
        adverse=adverse,
    )


def judge_anchors(
    anchors: Iterable[VerificationAnchor],
    tol: float,
    coverage_z: float = 2.0,
) -> list[AnchorVerdict]:
    """批量判决，保持输入顺序（可复现）。"""
    return [judge_anchor(a, tol=tol, coverage_z=coverage_z) for a in anchors]


def independent_decisive_passes(
    verdicts: Iterable[AnchorVerdict],
) -> tuple[list[AnchorVerdict], dict[str, int]]:
    """按证据根去重后的决定性 PASS 锚。

    同 independence_key 的多条 PASS 只算一条（同源重复不计入独立性），
    返回 (去重后代表锚列表, 每个证据根被折叠的次数)。
    """
    by_key: dict[str, AnchorVerdict] = {}
    collapsed: dict[str, int] = {}
    for v in verdicts:
        if v.verdict != PASS:
            continue
        key = v.independence_key or v.anchor_id
        if key in by_key:
            collapsed[key] = collapsed.get(key, 1) + 1
            continue
        by_key[key] = v
        collapsed.setdefault(key, 1)
    return list(by_key.values()), collapsed


def decisive_failures(verdicts: Iterable[AnchorVerdict]) -> list[AnchorVerdict]:
    return [v for v in verdicts if v.verdict == FAIL]


def adverse_undecidables(verdicts: Iterable[AnchorVerdict]) -> list[AnchorVerdict]:
    return [v for v in verdicts if v.verdict == UNDECIDABLE and v.adverse]


def inconclusive_undecidables(verdicts: Iterable[AnchorVerdict]) -> list[AnchorVerdict]:
    return [v for v in verdicts if v.verdict == UNDECIDABLE]


def distinct_kinds(verdicts: Iterable[AnchorVerdict]) -> set[str]:
    return {v.kind for v in verdicts}


__all__ = [
    "PASS",
    "FAIL",
    "UNDECIDABLE",
    "ANCHOR_VERDICTS",
    "ANCHOR_KINDS",
    "VerificationAnchor",
    "AnchorVerdict",
    "judge_anchor",
    "judge_anchors",
    "independent_decisive_passes",
    "decisive_failures",
    "adverse_undecidables",
    "inconclusive_undecidables",
    "distinct_kinds",
]