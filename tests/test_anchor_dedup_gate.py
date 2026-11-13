"""锚点查重门（M-R17-01）纯函数面：放行/拒绝/豁免/归一化/窗口语义。

零领域语义：所有夹具锚点都是合成锚（``SYN-*``），不含任何真实领域名词。
规则对应 ``docs/ANCHOR_DEDUP_GATE.md`` §2 与工具书 §2：

1. 同锚点在飞 → reject
2. 同锚点 24h 内 succeeded → reject
3. 24h 内 failed → allow（重试合法）
4. 派生单（parent_mission_id 非空 / goal 带 返修|交差审计|重开|管到底）→ exempt
5. 锚点比较归一化（大小写/空白/连字符/全半角）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.mission_governance import anchor_dedup_gate as g  # noqa: E402

NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
SYN = "SYN-ANCHOR-ALPHA"


def _mission(mid, status, *, anchor=None, goal=None, parent=None, hours_ago=None, now=NOW):
    goal = goal if goal is not None else f"【锚点={anchor or SYN}】synthetic fixture mission"
    when = None
    if hours_ago is not None:
        when = (now - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": mid,
        "status": status,
        "goal": goal,
        "parent_mission_id": parent,
        "finished_at": when,
        "created_at": when,
    }


# ── 1. 无冲突放行 ────────────────────────────────────────────────────────
def test_allow_when_no_conflict():
    out = g.gate(SYN, [], [], now=NOW)
    assert out["decision"] == "allow"
    assert out["conflicts"] == []
    assert out["readonly"] is True
    assert out["window_hours"] == 24


# ── 2. 同锚点在飞 → reject ───────────────────────────────────────────────
def test_reject_when_anchor_in_flight():
    out = g.gate(SYN, [_mission(101, "running")], [], now=NOW)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [101]
    assert "在飞" in out["reason"]


def test_in_flight_rejects_regardless_of_age():
    """卡住的在飞单（3 天前创建、仍 running）也要挡住再派一份。"""
    out = g.gate(SYN, [_mission(102, "running", hours_ago=72)], [], now=NOW)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [102]


def test_multiple_in_flight_conflicts_all_listed():
    out = g.gate(SYN, [_mission(103, "running"), _mission(104, "queued")], [], now=NOW)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [103, 104]
    assert len(out["conflicts_detail"]) == 2


# ── 3. 24h 内已交付 → reject ─────────────────────────────────────────────
def test_reject_when_delivered_within_window():
    out = g.gate(SYN, [], [_mission(105, "succeeded", hours_ago=2)], now=NOW)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [105]
    assert out["conflicts_detail"][0]["conflict_kind"] == "delivered_in_window"


def test_allow_when_delivery_outside_window():
    """超窗放行：窗口滚出去之后同锚点可以再派（否则锚点会被永久封死）。"""
    out = g.gate(SYN, [], [_mission(106, "succeeded", hours_ago=30)], now=NOW)
    assert out["decision"] == "allow"
    assert out["out_of_window"][0]["mission_id"] == 106


def test_window_boundary_respected():
    """窗口可调：同一单在 window_hours=1 下超窗放行、在 24h 下拒绝。"""
    done = _mission(107, "succeeded", hours_ago=3)
    assert g.gate(SYN, [], [done], now=NOW, window_hours=1)["decision"] == "allow"
    assert g.gate(SYN, [], [done], now=NOW, window_hours=24)["decision"] == "reject"


# ── 4. 窗口内 failed → allow（重试合法）───────────────────────────────────
def test_allow_when_only_failed_in_window():
    out = g.gate(SYN, [], [_mission(108, "failed", hours_ago=1)], now=NOW)
    assert out["decision"] == "allow"
    assert out["conflicts"] == []
    assert "failed" in out["reason"]


def test_failed_then_succeeded_in_window_still_rejects():
    """同锚点既有 failed 又有 succeeded：交付过就是重复，不能拿 failed 抵账。"""
    out = g.gate(SYN, [], [_mission(109, "failed", hours_ago=5),
                           _mission(110, "succeeded", hours_ago=1)], now=NOW)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [110]


# ── 5. 派生单豁免 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("marker", ["返修", "交差审计", "重开", "管到底"])
def test_exempt_derived_by_goal_marker(marker):
    candidate = {"goal": f"【{marker}】补做 SYN 的收尾", "parent_mission_id": None}
    out = g.gate(SYN, [_mission(111, "running")], [], now=NOW, candidate=candidate)
    assert out["decision"] == "exempt"
    assert out["conflicts"] == []


def test_exempt_derived_by_parent_id():
    out = g.gate(SYN, [], [], now=NOW, candidate={"parent_mission_id": 42, "goal": "x"})
    assert out["decision"] == "exempt"
    assert "parent_mission_id=42" in out["reason"]


def test_derived_in_flight_mission_is_not_a_conflict():
    """在飞的派生单不构成冲突：否则返修单会把同锚点新单永久卡住。"""
    derived = _mission(112, "running", parent=99)
    out = g.gate(SYN, [derived], [], now=NOW)
    assert out["decision"] == "allow"


def test_non_derived_candidate_still_rejects():
    out = g.gate(SYN, [_mission(113, "running")], [], now=NOW,
                 candidate={"goal": "普通派单", "parent_mission_id": None})
    assert out["decision"] == "reject"


# ── 6. 归一化 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("variant", [
    "syn-anchor-alpha",            # 大小写
    "  SYN-ANCHOR-ALPHA  ",        # 空白
    "SYN—ANCHOR—ALPHA",            # em dash
    "ＳＹＮ－ＡＮＣＨＯＲ－ＡＬＰＨＡ",  # 全角
    "SYN\u2011ANCHOR\u2011ALPHA",
])
def test_normalization_matches_variants(variant):
    assert g.normalize_anchor(variant) == g.normalize_anchor(SYN)
    stored = _mission(114, "running", anchor=SYN)
    out = g.gate(variant, [stored], [], now=NOW)
    assert out["decision"] == "reject"


def test_normalization_does_not_merge_distinct_anchors():
    """归一化不得激进到把 A-B 与 AB 合并（误合并比重复更贵）。"""
    assert g.normalize_anchor("SYN-AB") != g.normalize_anchor("SYNAB")
    out = g.gate("SYN-AB", [_mission(115, "running", anchor="SYNAB")], [], now=NOW)
    assert out["decision"] == "allow"


def test_normalize_lowercases():
    """归一化必须折叠大小写：goal 里的锚点常是大写，派单侧常传小写。"""
    assert g.normalize_anchor("SyN-AnChOr") == "syn-anchor"
    assert g.normalize_anchor("SYN-ANCHOR") == g.normalize_anchor("syn-anchor")


def test_normalize_collapses_inner_whitespace():
    """归一化必须吃掉锚点内部空白（"SYN - A" 与 "SYN-A" 同锚点）。"""
    assert g.normalize_anchor("SYN - ANCHOR") == "syn-anchor"
    assert g.normalize_anchor("SYN\t-  ANCHOR\n") == "syn-anchor"


def test_normalize_unifies_dash_variants():
    """各种 Unicode 破折号 → 同一个 '-'。"""
    for dash in ("—", "–", "‑", "－", "\u2212"):
        assert g.normalize_anchor(f"SYN{dash}A") == "syn-a"


def test_extract_anchor_from_goal_text():
    assert g.extract_anchor("EDIT_WHITELIST: docs/ ... 【锚点=SYN-X-1】 其它说明") == "SYN-X-1"
    assert g.extract_anchor("【锚点： SYN-COLON 】") == "SYN-COLON"
    assert g.extract_anchor("无标签的普通任务") is None


def test_empty_anchor_rejects_with_reason():
    out = g.gate("   ", [], [], now=NOW)
    assert out["decision"] == "reject"
    assert "锚点为空" in out["reason"]


# ── 7. 契约面 ────────────────────────────────────────────────────────────
def test_decision_vocabulary_is_closed():
    assert set(g.DECISIONS) == {"allow", "exempt", "reject"}
    for out in (g.gate(SYN, [], [], now=NOW),
                g.gate(SYN, [_mission(116, "running")], [], now=NOW),
                g.gate(SYN, [], [], now=NOW, candidate={"parent_mission_id": 7})):
        assert out["decision"] in g.DECISIONS
        assert isinstance(out["conflicts"], list)
        assert out["reason"]
        assert out["gate"] == g.GATE_ID


def test_window_hours_is_clamped():
    out = g.gate(SYN, [], [], now=NOW, window_hours=10_000)
    assert out["window_hours"] == g.MAX_WINDOW_HOURS
    assert g.gate(SYN, [], [], now=NOW, window_hours=0)["window_hours"] == g.DEFAULT_WINDOW_HOURS


def test_gate_does_not_mutate_inputs():
    in_flight = [_mission(117, "running")]
    recent = [_mission(118, "succeeded", hours_ago=1)]
    snapshot = ([dict(m) for m in in_flight], [dict(m) for m in recent])
    g.gate(SYN, in_flight, recent, now=NOW)
    assert (in_flight, recent) == snapshot