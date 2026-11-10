"""五级证据等级分类法（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）。

**零领域语义**：本模块只定义等级偏序与降级判定，不含任何域词条、域判据、域算例。
能力清单、路径绑定、基准定义全部由外部 spec（配置/工件）注入。

等级偏序（``RANK``）与台账 ``level_rank`` 对齐（artifacts/capability_ledger）：

    NOT_SUPPORTED(0) < STUB(1) < MOCK(2) < WITHHELD(3) < REAL(4)

注意偏序语义：WITHHELD 高于 MOCK/STUB —— "证据不足"不是比"造假"更强的负面结论，
而是**更强的保留**：MOCK/STUB 是有正面反证的判决，WITHHELD 是拒绝下判决。
降级判定因此不能只看 rank 差值，还要看是否落在"有无正面反证"的分界上（``classify_change``）。
"""
from __future__ import annotations

from typing import Iterable

LEVELS: tuple[str, ...] = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
RANK: dict[str, int] = {lv: i for i, lv in enumerate(LEVELS)}

#: 有正面反证的弱等级（违规事实已被证明，而非仅证据不足）
POSITIVELY_REFUTED: frozenset[str] = frozenset({"NOT_SUPPORTED", "STUB", "MOCK"})

#: 证据链种类（每类都要落到具体路径，禁止空引用）
EVIDENCE_KINDS: tuple[str, ...] = (
    "impl_files",      # 实现源码（含 sha256 / 行数 / 函数数）
    "test_files",      # 测试源码（含测试函数名 / 数值断言数）
    "test_names",      # 具体测试函数名（证据链要求"测试名"）
    "benchmark",       # 基准工件（含实测值 / 参考值 / 误差 / 容差）
    "reason_codes",    # 判定理由码（可复算）
)


def normalize(level: str) -> str:
    lv = str(level or "").strip().upper().replace("-", "_").replace(" ", "_")
    if lv in RANK:
        return lv
    raise ValueError(f"unknown evidence level: {level!r} (expected one of {LEVELS})")


def rank(level: str) -> int:
    return RANK[normalize(level)]


def is_downgrade(old: str, new: str) -> bool:
    """新等级是否弱于旧等级（rank 严格下降）。"""
    return rank(new) < rank(old)


def classify_change(old: str, new: str) -> str:
    """返回 STABLE / UPGRADE / DOWNGRADE / REFUTATION。

    * ``UPGRADE``：rank 上升；
    * ``DOWNGRADE``：rank 下降但不跨越"正面反证"分界（如 REAL→WITHHELD）；
    * ``REFUTATION``：从非正面反证状态落入 ``POSITIVELY_REFUTED``（如 REAL→MOCK、
      WITHHELD→STUB）——最严重的一类，回归防护必须无条件红；
    * ``STABLE``：等级不变。
    """
    o, n = normalize(old), normalize(new)
    if o == n:
        return "STABLE"
    if RANK[n] > RANK[o]:
        return "UPGRADE"
    if n in POSITIVELY_REFUTED and o not in POSITIVELY_REFUTED:
        return "REFUTATION"
    return "DOWNGRADE"


def tally(levels: Iterable[str]) -> dict[str, int]:
    out = {lv: 0 for lv in LEVELS}
    for lv in levels:
        out[normalize(lv)] += 1
    return out


def majority_baseline(levels: Iterable[str]) -> tuple[str, float]:
    """多数类基线（对照用）：总是预测最常见的等级时的期望一致率。

    自动判定的价值必须 relative to 这个基线来读——高一致率本身可能只是类别不平衡。
    """
    levels = [normalize(lv) for lv in levels]
    if not levels:
        return "WITHHELD", 0.0
    t = tally(levels)
    lv, n = max(t.items(), key=lambda kv: (kv[1], -RANK[kv[0]]))
    return lv, n / len(levels)