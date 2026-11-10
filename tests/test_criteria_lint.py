#!/usr/bin/env python3
"""criteria_lint（p372w2-n06）单元与判据测试。

零领域语义纪律：本文件的夹具用**合成指标名**（指标 A / metric_x），
不出现任何领域词条；域词条只能经 ``extra_reference_terms`` 注入。

覆盖：三形态抽取 / 三要素拦截召回 / 标签 / 原因码登记与覆盖 /
纯函数确定性（sha256） / 派发前门四模式 / 注入的「不自证满足」性质。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import criteria_lint as CL  # noqa: E402


# ───────────────────────── 夹具（合成、零领域语义）─────────────────────────

#: 每项标注「缺哪个要素」——gold 标签由作者在看输出前写定（本文件即冻结夹具）
GOLD_MISSING = [
    ("指标 Alpha 应当稳定可靠", {"threshold", "reference_anchor", "verdict_mark"}),
    ("指标 Beta 与若干次运行结果差别不大",
     {"threshold", "reference_anchor", "verdict_mark"}),
    ("指标 Gamma 覆盖率 达到 <NUM>%", {"threshold", "reference_anchor"}),
    ("指标 Delta <= 0.05", {"reference_anchor"}),
    ("指标 Epsilon 与基线一致", {"threshold"}),
    ("指标 Zeta 相对文献 处于 0.3 至 0.7", {"threshold", "verdict_mark"}),
    ("指标 Eta 区间外自动标记", {"threshold", "reference_anchor", "verdict_mark"}),
    ("指标 Theta 在合理范围内尽量高", {"threshold", "reference_anchor", "verdict_mark"}),
    ("指标 Iota 输出结构可配置", {"threshold", "reference_anchor", "verdict_mark"}),
]

GOLD_COMPLETE = [
    "指标 Kappa 相对实验误差 <= 5%",
    "指标 Lambda 与基线相对误差 小于等于 0.02",
    "指标 Mu 质量守恒残差 <= 1e-9",
    "同输入同种子 sha256 一致率 100%",
    "指标 Nu 覆盖区间 0.1–2.0 rad/s（vs 解析解）",
    "指标 Xi 堆积角差异 小于 2 度（vs 实验）",
]


# ───────────────────────── 1. 登记表与原因码 ─────────────────────────

def test_reason_code_registry_is_machine_readable_and_at_least_5():
    assert len(CL.REASON_CODES) >= 5
    for code, msg in CL.REASON_CODES.items():
        # 机器可读 = 稳定大写下划线标识 + 人可读说明
        assert code == code.upper()
        assert code.startswith("CRIT_")
        assert msg.strip()
    # 三要素缺失各有一个专属码
    for elem, code in CL.MISSING_CODE.items():
        assert code in CL.REASON_CODES
        assert elem in CL.ELEMENT_KEYS


def test_emitted_codes_are_all_registered():
    goal = "【机器验收判据】(a)指标 A 稳定；(b)指标 B <= 0.05；(c)指标 C 与基线一致。"
    rep = CL.lint(goal)
    emitted = set(rep.reason_counts)
    assert emitted <= set(CL.REASON_CODES)
    assert emitted, "至少应触发一种原因码"


# ───────────────────────── 2. 三形态抽取 ─────────────────────────

def test_extract_numbered_form():
    goal = ("【机器验收判据】全部满足：\n(1) 指标 A 相对实验误差 <= 5%；\n"
            "(2) 指标 B 与基线一致，残差 <= 1e-9。")
    items, form, span = CL.extract_items(goal)
    assert form == "numbered"
    assert len(items) == 2
    assert items[0].startswith("指标 A")
    # span 定位到判据块（不是整篇）
    assert 0 < span[0] < span[1] <= len(goal)


def test_extract_semicolon_form():
    goal = "机器验收判据：指标 A 相对实验误差 <= 5%；指标 B 与基线一致；指标 C 稳定。"
    items, form, _ = CL.extract_items(goal)
    assert form == "semicolon"
    assert len(items) == 3


def test_extract_threshold_phrase_form():
    goal = ("判据：指标 A 相对实验误差 <= 5%。指标 B 与基线相对误差 小于等于 0.02。"
            "其余为定性描述。")
    items, form, _ = CL.extract_items(goal)
    assert form in ("threshold_phrase", "semicolon", "sentence")
    assert any("<=" in it for it in items)


def test_pointer_only_block_reports_pointer_code():
    goal = "【机器验收判据】见战役表 metadata。"
    rep = CL.lint(goal)
    assert rep.verdict == "fail"
    assert "CRIT_POINTER_ONLY" in rep.doc_codes


def test_no_criteria_block_reports_code():
    rep = CL.lint("这里只有散文，没有任何可判定条款，也没有数字。")
    assert "CRIT_NO_CRITERIA_BLOCK" in rep.doc_codes


# ───────────────────────── 3. 拦截召回 100% + 精度 ─────────────────────────

@pytest.mark.parametrize("text,missing", GOLD_MISSING)
def test_gold_missing_items_are_blocked_with_matching_reason_codes(text, missing):
    it = CL.lint_item(text, 1)
    assert it.blocked is True, f"漏拦：{text}"
    assert set(it.missing) == missing, f"缺失要素误判：{text} → {it.missing}"
    for elem in missing:
        assert CL.MISSING_CODE[elem] in it.reason_codes


def test_block_recall_is_100_percent_on_gold_set():
    """判据三要素缺失拦截率 = 100%（gold 集逐条计）。"""
    blocked = [CL.lint_item(t, i).blocked for i, (t, _) in enumerate(GOLD_MISSING, 1)]
    assert all(blocked), [t for (t, _), b in zip(GOLD_MISSING, blocked) if not b]
    assert sum(blocked) / len(blocked) == 1.0


def test_complete_items_are_not_blocked():
    bad = {t: CL.lint_item(t, i).missing for i, t in enumerate(GOLD_COMPLETE, 1)
           if CL.lint_item(t, i).blocked}
    assert not bad, f"误拦（精度损失）：{bad}"


def test_duplicate_item_is_flagged():
    rep = CL.lint("机器验收判据：(a) 指标 A 相对实验误差 <= 5%；(b) 指标 A 相对实验误差 <= 5%。")
    codes = [c for it in rep.items for c in it.reason_codes]
    assert "CRIT_DUPLICATE_ITEM" in codes


def test_unparsable_item_is_flagged():
    it = CL.lint_item("  ", 1)
    assert "CRIT_UNPARSABLE_ITEM" in it.reason_codes and it.blocked


# ───────────────────────── 4. 标签语义 ─────────────────────────

def test_labels_semantics():
    numeric = CL.lint_item("指标 A 相对实验误差 <= 5%", 1)
    assert CL.LABEL_NUMERIC in numeric.labels
    assert CL.LABEL_NO_REF not in numeric.labels

    boolean = CL.lint_item("指标 B 通过一致性校验（与基线相同）", 1)
    assert CL.LABEL_BOOL in boolean.labels
    assert CL.LABEL_NUMERIC not in boolean.labels

    no_ref = CL.lint_item("指标 C <= 0.5", 1)
    assert CL.LABEL_NO_REF in no_ref.labels


def test_threshold_implies_verdict_mark_structural_invariant():
    """结构性质：阈值命中 ⇒ 判定记号命中（比较符本身即判定面）。

    这条性质解释了为何 ``CRIT_NO_VERDICT_MARK`` 只在同时缺阈值时出现。
    """
    for text in [t for t, _ in GOLD_MISSING] + GOLD_COMPLETE:
        it = CL.lint_item(text, 1)
        if it.hits["threshold"]:
            assert it.hits["verdict_mark"], text


def test_extra_reference_terms_are_injectable():
    text = "指标 A 相对 Zorbium 误差 <= 5%"
    assert CL.LABEL_NO_REF in CL.lint_item(text, 1).labels
    injected = CL.lint_item(text, 1, extra_reference_terms=("Zorbium",))
    assert CL.LABEL_NO_REF not in injected.labels
    assert not injected.blocked


# ───────────────────────── 5. 确定性（sha256 可复算）─────────────────────────

def test_same_input_same_sha256():
    goal = "机器验收判据：(a) 指标 A 相对实验误差 <= 5%；(b) 指标 B 稳定。"
    a, b = CL.lint(goal), CL.lint(goal)
    assert a.canonical_json() == b.canonical_json()
    assert a.report_sha256 == b.report_sha256
    assert a.source["sha256"] == hashlib.sha256(goal.encode("utf-8")).hexdigest()


def test_batch_is_deterministic_and_order_stable():
    nodes = [
        {"node_id": 1, "ext_id": "n01", "goal": "机器验收判据：(a) 指标 A <= 5% 相对实验。"},
        {"node_id": 2, "ext_id": "n02", "goal": "机器验收判据：(a) 指标 B 稳定。"},
    ]
    a = CL.lint_many(nodes)
    b = CL.lint_many(list(reversed(nodes)))
    sha = lambda d: {r["ext_id"]: r["report_sha256"] for r in d["nodes"]}  # noqa: E731
    assert sha(a) == sha(b)
    assert a["aggregate"] == b["aggregate"]


# ───────────────────────── 6. 派发前门四模式 ─────────────────────────

BAD_GOAL = "机器验收判据：(a) 指标 A 稳定；(b) 指标 B 与基线一致。"
GOOD_GOAL = "机器验收判据：(a) 指标 A 相对实验误差 <= 5%；(b) 指标 B 与基线一致（残差 <= 1e-9）。"


def test_mode_off_is_bypass():
    d = CL.pre_dispatch(BAD_GOAL, mode="off")
    assert d["action"] == "pass" and d.get("skipped") is True


def test_mode_advisory_only_logs():
    d = CL.pre_dispatch(BAD_GOAL, mode="advisory")
    assert d["action"] == "pass_logged"
    assert d["verdict"] == "fail"
    assert "CRIT_NO_NUMERIC_THRESHOLD" in d["reason_codes"]


def test_mode_block_refuses():
    d = CL.pre_dispatch(BAD_GOAL, mode="block")
    assert d["action"] == "refuse"


def test_mode_enforce_injects_append_only_and_cannot_self_satisfy():
    d = CL.pre_dispatch(BAD_GOAL, mode="enforce")
    assert d["action"] == "inject" and d["injected"] is True
    new_goal = d["goal"]
    # 纯追加：剥离区块后与原文逐字节相等
    assert new_goal.startswith(BAD_GOAL)
    assert CL.strip_slot_blocks(new_goal) == BAD_GOAL
    # 注入不改变判决（不自证满足）
    again = CL.pre_dispatch(new_goal, mode="block")
    assert again["action"] == "refuse"
    assert again["reason_codes"] == d["reason_codes"]


def test_good_goal_passes_all_modes():
    for mode in ("advisory", "enforce", "block"):
        d = CL.pre_dispatch(GOOD_GOAL, mode=mode)
        assert d["action"] == "pass", (mode, d["reason_codes"])


def test_structured_criteria_take_precedence_over_goal_text():
    meta = {"acceptance_criteria": [
        {"metric": "metric_x vs experiment", "threshold": 0.05, "unit": "relative"},
    ]}
    d = CL.pre_dispatch(BAD_GOAL, mode="block", meta=meta)
    assert d["action"] == "pass"          # 结构化判据三要素齐备 → 放行
    rep = CL.lint_structured(meta["acceptance_criteria"])
    assert rep.block_form == "structured"
    assert rep.items[0].text == "metric_x vs experiment <= 0.05 relative"


def test_pre_dispatch_env_mode(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "block")
    assert CL.pre_dispatch(BAD_GOAL)["action"] == "refuse"
    monkeypatch.setenv(CL.ENV_MODE, "advisory")
    assert CL.pre_dispatch(BAD_GOAL)["action"] == "pass_logged"
    monkeypatch.setenv(CL.ENV_MODE, "bogus-mode")
    assert CL.pre_dispatch(BAD_GOAL)["mode"] == "advisory"   # 非法值回默认（fail-open）


# ───────────────────────── 7. 批次汇总（免 DB）─────────────────────────

def test_lint_many_aggregate_shape():
    out = CL.lint_many([
        {"node_id": 1, "ext_id": "n01", "goal": BAD_GOAL},
        {"node_id": 2, "ext_id": "n02", "goal": GOOD_GOAL},
    ], mode="advisory")
    agg = out["aggregate"]
    assert out["n_nodes"] == 2
    assert agg["items"] == 4
    assert 0 < agg["blocked_items"] < 4
    assert agg["actions"] == {"pass": 1, "pass_logged": 1}
    assert agg["n_reason_codes_emitted"] >= 1


def test_canonical_json_is_stable_under_dict_order():
    rep = CL.lint(GOOD_GOAL)
    payload = rep.as_dict()
    a = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()
    assert a == rep.report_sha256

# ───────────────────────── 8. 原因码可达性（防装饰品）─────────────────────────

def test_every_registered_reason_code_is_reachable():
    """登记表里 9 个原因码必须都有合成输入能触发（不可达 = 装饰品）。"""
    probe = CL.probe_reason_codes()
    assert probe["n_registered"] == len(CL.REASON_CODES) >= 5
    assert probe["unreachable"] == [], probe["unreachable"]
    assert probe["n_reachable"] == probe["n_registered"]
    for code, witnesses in probe["witness"].items():
        assert witnesses, f"{code} 无可触发输入"
