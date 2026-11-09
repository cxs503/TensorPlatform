#!/usr/bin/env python3
"""派发前门：fail-closed 分级 + 「证据槽位」回填（P151W1-N10 / ③④）。

分级（``schema.MODE_ACTIONS``）::

    off       完全旁路（回滚开关）
    advisory  只记录（**默认**；不碰任务书文本 → 零流程风险）
    enforce   缺 P1/P2 → 追加「证据槽位」建议块，再照常派发（不改任务语义）
    block     缺 P1/P2 → 拒绝派发（节点留在 pending，下一周期重试）

注入纪律（防自证满足）
----------------------
* 注入块由 ``SLOT_BLOCK_BEGIN/END`` 定界，分类器**先剥离**再判定 → 注入不会把
  weak 判成 strong；``leak``（注入造成假合规）在结构上为 0，并由
  ``leak_rate`` 实测复核（含「朴素注入」负控，证明该指标有鉴别力）。
* 注入文本不含任何数字：占位符为 ``<NUM>``/``<UNIT>``/``<METRIC>`` 这类无数字记号，
  即使剥离失败也不会凭形状满足 P1（双保险）。
* 只**追加**结构槽位，不重写/不删除作者原文：``assert_append_only`` 可机检。

零领域语义：本文件只有形状/方法论词，无域词条。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import classifier as C
from . import schema as S

ENV_MODE = "TP_JUDGEABILITY_GATE"

_SLOT_TEXT: Dict[str, str] = {
    "P1": (
        "· P1 数值阈值槽位：未检出可机器裁决的数值阈值。"
        "请回填形如「<METRIC> <比较符> <NUM><UNIT>」的一条（比较符取 <= / >= / == / ±，"
        "<NUM> 为阿拉伯数字，「回填建议」不是判据本体）。"
    ),
    "P2": (
        "· P2 参考种类槽位：未检出对标来源。请从封闭词表 "
        + "、".join(S.REFERENCE_KINDS)
        + " 中挑一个填入，并在判据里显式写出该词。"
    ),
    "P3": (
        "· P3 工件路径槽位（advisory，不阻塞派发）：建议给出 artifacts/ 或 runs/ 下"
        "可复算的 .json / .png 路径。"
    ),
}


@dataclass(frozen=True)
class Decision:
    action: str                      # "pass" | "pass_logged" | "inject" | "refuse"
    mode: str
    verdict: str
    goal: str                        # 可能已被注入的文本（advisory 模式等价原文）
    injected: bool
    missing: Tuple[str, ...]
    hard_missing: Tuple[str, ...]
    advisory: Tuple[str, ...]        # 只记录不阻塞的项（如缺 P3）
    judgment: Dict[str, Any]
    reasons: Tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.action == "refuse"

    def as_dict(self, with_goal: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "action": self.action,
            "mode": self.mode,
            "verdict": self.verdict,
            "injected": self.injected,
            "missing": list(self.missing),
            "hard_missing": list(self.hard_missing),
            "advisory": list(self.advisory),
            "reasons": list(self.reasons),
            "judgment": self.judgment,
        }
        if with_goal:
            out["goal"] = self.goal
        return out


def resolve_mode(mode: Optional[str] = None, *, default: str = "advisory") -> str:
    """模式解析：显式参数 > 环境变量 > 默认。非法值 → 回默认（fail-open to advisory）。"""
    val = (mode or os.getenv(ENV_MODE) or default or "advisory").strip().lower()
    return val if val in S.GATE_MODES else default


def suggest_slots(missing: Optional[Tuple[str, ...]] = None) -> str:
    """生成「证据槽位」建议块（定界、无数字、只含结构槽位）。"""
    keys = [k for k in ("P1", "P2", "P3") if missing is None or k in missing]
    if not keys:
        return ""
    body = "\n".join(_SLOT_TEXT[k] for k in keys)
    return (
        f"\n{S.SLOT_BLOCK_BEGIN}\n"
        "派发前门自动追加的结构槽位建议（只追加、不改任务语义；回填判据时请删本区块）：\n"
        f"{body}\n"
        f"{S.SLOT_BLOCK_END}\n"
    )


def inject_slots(goal: str, missing: Optional[Tuple[str, ...]] = None) -> str:
    """把建议块**追加**到任务书末尾（幂等：已有区块则不再重复追加）。"""
    text = goal or ""
    if S.SLOT_BLOCK_BEGIN in text:
        return text
    return text + suggest_slots(missing)


def assert_append_only(before: str, after: str) -> bool:
    """机检注入是纯追加：after 的前缀必须等于 before，且剥离区块后逐字节相等。"""
    return after.startswith(before) and S.strip_slot_blocks(after) == before


def decide(goal: str, *, mode: Optional[str] = None,
           meta: Optional[Dict[str, Any]] = None) -> Decision:
    """派发前门主判定（纯函数，可脱库单测）。"""
    m = resolve_mode(mode)
    j = C.classify_goal(goal)
    hard = j.hard_missing
    advisory = tuple(d for d in j.missing if d not in ("P1", "P2"))
    reasons: List[str] = []
    if j.scope == "whole":
        reasons.append("no_criteria_section: 未找到判据段标记 → 任务书级 fail-closed 判 weak"
                       "（维度命中原样保留，供审计观察）")

    if m == "off":
        return Decision("pass", m, j.verdict, goal, False, j.missing, hard, advisory,
                        j.as_dict(), tuple(reasons))
    if m == "advisory" or not hard:
        reasons.append("advisory_only") if hard else reasons.append("criteria_ok")
        return Decision("pass_logged", m, j.verdict, goal, False, j.missing, hard,
                        advisory, j.as_dict(), tuple(reasons))
    if m == "enforce":
        new_goal = inject_slots(goal, j.missing)
        valid = assert_append_only(goal, new_goal)
        reasons.append("slots_injected" if valid else "inject_not_append_only")
        return Decision("inject", m, j.verdict, new_goal, True, j.missing, hard,
                        advisory, j.as_dict(), tuple(reasons))
    # block
    reasons.append("blocked_missing:" + ",".join(hard))
    return Decision("refuse", m, j.verdict, goal, False, j.missing, hard, advisory,
                    j.as_dict(), tuple(reasons))


def pre_dispatch(goal: str, *, mode: Optional[str] = None,
                 meta: Optional[Dict[str, Any]] = None) -> Decision:
    """dispatcher/wave_cycle 的挂钩入口（异常一律降级为 pass，绝不打断派发）。"""
    try:
        return decide(goal, mode=mode, meta=meta)
    except Exception as exc:  # pragma: no cover - 防御：门坏了不许误伤流程
        return Decision("pass", "advisory", "unknown", goal, False, (), (),
                        (f"gate_error:{type(exc).__name__}",),
                        {"error": f"{type(exc).__name__}: {exc}"},
                        ("fail_open_on_gate_error",))


def leak_rate(goals: List[str], *, marker: bool = True) -> Dict[str, Any]:
    """enforce 模式「假晋升」漏放率：注入后由 weak 变 strong 的单数 / 总单数。

    ``marker=True``：正规注入（带定界标记，分类器剥离）→ 期望 0（结构上不可能）；
    ``marker=False``：「朴素注入」负控（去掉定界标记，建议词直接可达分类器）
                    → 期望 > 0，用于证明该指标有鉴别力，而非恒 0 的空指标。
    """
    n = len(goals)
    leaked: List[int] = []
    for i, g in enumerate(goals):
        before = C.classify_goal(g).verdict
        raw = inject_slots(g, None)
        if not marker:
            raw = raw.replace(S.SLOT_BLOCK_BEGIN, "").replace(S.SLOT_BLOCK_END, "")
        after = C.classify_goal(raw).verdict
        if before != "strong" and after == "strong":
            leaked.append(i)
    return {"metric": "false_promotion", "n": n, "leaked": len(leaked),
            "leak_rate": (len(leaked) / n if n else 0.0),
            "leaked_idx_sample": leaked[:20], "marker": marker}


def replay_enforce(goals: List[str]) -> Dict[str, Any]:
    """把 enforce 门在历史单上重放，量出「漏放」（该注入却没注入）与「假晋升」。

    * ``un_flagged``：判据缺 P1/P2（hard_missing）但派发文本里没有槽位区块 = 漏放；
    * ``false_promotion``：注入后 weak→strong（自证满足）；
    * 两者都应为 0，且 ``n_flagged > 0`` 证明读数不是「全都没触发」的空绿。
    """
    n = len(goals)
    un_flagged: List[int] = []
    promoted: List[int] = []
    flagged = 0
    for i, g in enumerate(goals):
        d = decide(g, mode="enforce")
        if d.hard_missing:
            flagged += 1
            if S.SLOT_BLOCK_BEGIN not in d.goal:
                un_flagged.append(i)
            if C.classify_goal(d.goal).verdict == "strong":
                promoted.append(i)
    return {"n": n, "n_flagged": flagged,
            "n_clean": n - flagged,
            "un_flagged": len(un_flagged), "un_flagged_idx_sample": un_flagged[:20],
            "false_promotion": len(promoted),
            "false_promotion_idx_sample": promoted[:20],
            "miss_rate": (len(un_flagged) / n if n else 0.0),
            "false_promotion_rate": (len(promoted) / n if n else 0.0)}
