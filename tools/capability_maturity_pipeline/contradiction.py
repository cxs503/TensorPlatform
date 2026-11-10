"""矛盾检测：后续证据推翻先前判定基座（p156w2-n07）。

语义（机器可判，无人工裁量）：

* **supersession（推翻）**：同一 ``(capability_id, metric_name)`` 上，后来的证据
  数值与台账当前基座证据偏离超过 ``deviation_threshold_pct``，且时间上不早于
  基座证据 → 基座证据被判为 *refuted*，由新证据取代。
* **conflict（冲突）**：同一批扫描内部同键证据互相偏离超阈值 → 数据源之间
  不自洽，记告警（不静默取用）。

阈值必须**在扫描前声明**（``ContradictionPolicy``），不可事后调整；策略本身带
sha256 指纹进入审计链 payload。

零领域语义：只看 (能力 ID, 指标名, 数值, 时间)，不理解指标含义。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..capability_ledger_maturity.evidence_collector import EvidenceItem


def _parse_ts(ts: str) -> str:
    """时间戳比较用规范化（ISO8601 字符串可直接字典序比较，此处做防御性截断）。"""
    return (ts or "").replace("Z", "+00:00")


@dataclass(frozen=True)
class ContradictionPolicy:
    """预声明的矛盾判据。

    Parameters
    ----------
    deviation_threshold_pct : float
        同一指标两次测量相对偏离超过该百分比即视为矛盾（默认 1.0%）。
    min_abs_deviation : float
        下限绝对量，避免 0 附近数值放大（默认 1e-9）。
    """
    deviation_threshold_pct: float = 1.0
    min_abs_deviation: float = 1e-9

    def sha256(self) -> str:
        blob = json.dumps({"deviation_threshold_pct": self.deviation_threshold_pct,
                           "min_abs_deviation": self.min_abs_deviation},
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


DEFAULT_POLICY = ContradictionPolicy()


def relative_deviation_pct(old: float, new: float,
                           min_abs: float = 1e-9) -> float:
    """相对偏离百分比（分母取 |old|，old≈0 时退化为绝对量）。"""
    denom = max(abs(old), min_abs)
    return abs(new - old) / denom * 100.0


@dataclass(frozen=True)
class Supersession:
    """一条推翻记录。"""
    capability_id: str
    metric_name: str
    refuted_sha256: str
    refuted_value: float
    refuted_timestamp: str
    by_sha256: str
    by_value: float
    by_timestamp: str
    deviation_pct: float
    rule: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "metric_name": self.metric_name,
            "refuted_sha256": self.refuted_sha256,
            "refuted_value": self.refuted_value,
            "refuted_timestamp": self.refuted_timestamp,
            "by_sha256": self.by_sha256,
            "by_value": self.by_value,
            "by_timestamp": self.by_timestamp,
            "deviation_pct": round(self.deviation_pct, 6),
            "rule": self.rule,
        }


@dataclass(frozen=True)
class Conflict:
    """同批证据内部不自洽告警。"""
    capability_id: str
    metric_name: str
    sha_a: str
    sha_b: str
    value_a: float
    value_b: float
    deviation_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {"capability_id": self.capability_id, "metric_name": self.metric_name,
                "sha_a": self.sha_a, "sha_b": self.sha_b,
                "value_a": self.value_a, "value_b": self.value_b,
                "deviation_pct": round(self.deviation_pct, 6)}


def quantity_key(item: EvidenceItem) -> Tuple[str, str, Any]:
    """「同一个量」的身份键。

    规则（机器可判、无人工裁量）：

    * 有参照值 → 身份 = (能力, 指标名, 参照值)：参照值不同 = 不同算例，不可比。
    * 无参照值 → 身份 = (能力, 指标名, 来源文件)：只有**同一工件被先后重写**
      才视为同一个量；不同文件里的同名无参照指标不可比（否则误报爆炸）。

    只按 metric_name 比较会把「不同算例的同名误差」当成互相矛盾。
    """
    ref = item.reference_value
    third: Any = round(float(ref), 9) if ref is not None else f"path:{item.source_path}"
    return (item.capability_id, item.metric_name, third)


def conflict_key(item: EvidenceItem) -> Optional[Tuple[str, str, float]]:
    """同批冲突比较使用的身份键：**必须有参照值**才可跨来源比较。

    无参照值的同名指标无法判断是否在测同一个量（同一文件里多个条目通常就是不
    同算例），参与成对比较只会产生误报，故返回 ``None`` 表示不可比。
    """
    if item.reference_value is None:
        return None
    return (item.capability_id, item.metric_name,
            round(float(item.reference_value), 9))


def _metric_value(item: EvidenceItem) -> Optional[float]:
    """可用于矛盾比较的数值（优先误差百分比，其次测量值）。"""
    if item.error_pct is not None:
        return float(item.error_pct)
    if item.metric_value is not None:
        return float(item.metric_value)
    return None


def detect_supersessions(prior: Sequence[EvidenceItem],
                         incoming: Sequence[EvidenceItem],
                         policy: ContradictionPolicy = DEFAULT_POLICY
                         ) -> List[Supersession]:
    """检测 incoming 对 prior（台账基座）的推翻。

    Parameters
    ----------
    prior : sequence of EvidenceItem
        台账当前等级所依据的基座证据。
    incoming : sequence of EvidenceItem
        本轮扫描得到的新证据。

    Notes
    -----
    * 仅当新证据时间戳 **不早于** 被推翻证据时才允许推翻（时间倒流不算推翻）；
    * 同一基座证据被多条新证据推翻时，只保留偏离最大者（确定性）。

    Returns
    -------
    list of Supersession
        按 (capability_id, metric_name, refuted_sha256) 排序。
    """
    best: Dict[Tuple[str, str, Any, str], Supersession] = {}
    for old in prior:
        old_v = _metric_value(old)
        if old_v is None:
            continue
        for new in incoming:
            if new.capability_id != old.capability_id:
                continue
            if new.metric_name != old.metric_name:
                continue
            if quantity_key(new) != quantity_key(old):
                continue            # 不同算例（参照值不同）→ 不构成矛盾
            if new.sha256 == old.sha256:
                continue
            new_v = _metric_value(new)
            if new_v is None:
                continue
            if _parse_ts(new.timestamp) < _parse_ts(old.timestamp):
                continue
            dev = relative_deviation_pct(old_v, new_v, policy.min_abs_deviation)
            if dev <= max(policy.deviation_threshold_pct,
                          policy.min_abs_deviation):
                continue
            key = (old.capability_id, old.metric_name, quantity_key(old)[2],
                   old.sha256)
            cand = Supersession(
                capability_id=old.capability_id,
                metric_name=old.metric_name,
                refuted_sha256=old.sha256,
                refuted_value=old_v,
                refuted_timestamp=old.timestamp,
                by_sha256=new.sha256,
                by_value=new_v,
                by_timestamp=new.timestamp,
                deviation_pct=dev,
                rule="SUPERSEDE_DEVIATION_EXCEEDS_THRESHOLD",
            )
            if key not in best or dev > best[key].deviation_pct:
                best[key] = cand
    return [best[k] for k in sorted(best, key=lambda k: (k[0], k[1],
                                                         str(k[2]), k[3]))]


def detect_conflicts(items: Sequence[EvidenceItem],
                     policy: ContradictionPolicy = DEFAULT_POLICY,
                     max_group_size: int = 64
                     ) -> List[Conflict]:
    """同一批证据内部同键数值互相偏离超阈值 → 冲突告警。

    Parameters
    ----------
    max_group_size : int
        单个 ``(capability_id, metric_name)`` 分组超过该规模时跳过成对比较
        （成对枚举是 O(n²)，且超大分组通常意味着指标名未细分、比较无意义）。
        跳过的分组数量由 :func:`oversized_groups` 单独统计。
    """
    buckets: Dict[Tuple[str, str, float], List[EvidenceItem]] = {}
    for it in items:
        if _metric_value(it) is None:
            continue
        k = conflict_key(it)
        if k is None:
            continue
        buckets.setdefault(k, []).append(it)
    out: List[Conflict] = []
    for (cap, metric, _ref), group in sorted(buckets.items()):
        uniq = {it.sha256: it for it in group}
        if len(uniq) > max_group_size:
            continue
        vals = sorted(uniq.values(), key=lambda x: x.sha256)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                a, b = vals[i], vals[j]
                av, bv = _metric_value(a), _metric_value(b)
                dev = relative_deviation_pct(av, bv, policy.min_abs_deviation)
                if dev > max(policy.deviation_threshold_pct,
                             policy.min_abs_deviation):
                    out.append(Conflict(cap, metric, a.sha256, b.sha256,
                                        av, bv, dev))
    return out


def oversized_groups(items: Sequence[EvidenceItem],
                     max_group_size: int = 64) -> List[Tuple[str, str, int]]:
    """返回超过成对比较上限的分组 ``(capability_id, metric_name, size)``。"""
    buckets: Dict[Tuple[str, str, float], set] = {}
    for it in items:
        if _metric_value(it) is None:
            continue
        k = conflict_key(it)
        if k is None:
            continue
        buckets.setdefault(k, set()).add(it.sha256)
    return [(key[0], key[1], len(v)) for key, v in sorted(buckets.items())
            if len(v) > max_group_size]


def supersession_payload(supersessions: Sequence[Supersession]) -> Dict[str, Any]:
    """把推翻记录压成审计链 payload 片段。"""
    return {
        "superseded_refs": [s.refuted_sha256 for s in supersessions],
        "superseded_by": [s.by_sha256 for s in supersessions],
        "supersession_count": len(supersessions),
        "max_deviation_pct": round(max((s.deviation_pct for s in supersessions),
                                       default=0.0), 6),
    }