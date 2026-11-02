"""m1860 验证欠账台账的「可判性」回归测试（verifier 席）。

背景：``tools/validation_debt_ledger.py`` 已按 100+ 篇平台报告抽出台账，并被当作
「验证欠账优先级」的数据源。但抽取器本身没被计量过——m1860 抽检 45 条（人工标注，
``artifacts/validation_debt_m1860/gold_eval.json``）发现 5 条假欠账（表格数据行 1、
✅ 正向自证句 1、已修好的过程坑 2、英文自证句 1），精度 88.89%。

本文件的断言逐条钉死不变量，**每条都对应一个可被推翻的命题**：
- ✅ 是"已达标"标记，不是"欠账"标记；❌ 反而常常标记真实缺口（"❌ 未覆盖"），
  所以**不能用字形做判据**，只能用"有没有待办活口"做判据（守卫 LIVE_DEBT_GUARD）。
- 数据表行（≥3 列且含数字格）与表头行不该变成条目；
  但风险登记表那种"带长句/指标词的表格行"是真实欠账，必须保留且不能被续行粘坏。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import validation_debt_ledger as L  # noqa: E402


# ── 正向自证句 vs 真实缺口：字形不是判据，语义才是 ───────────────────────────────
def test_check_mark_positive_statement_is_dropped():
    items = L.extract_items("- ✅ 质量守恒验证（漂移 <0.1%）")
    assert items == []


def test_cross_mark_is_not_a_drop_signal():
    """反例：❌ 常标记真实缺口（m1860 抽检 S05/S24），丢弃它就是把欠账藏起来。"""
    items = L.extract_items("- ❌ 高 Re 湍流：未覆盖")
    assert len(items) == 1
    assert items == ["❌ 高 Re 湍流：未覆盖"]


def test_positive_mark_with_live_gap_survives_guard():
    """复合句"已可用，但 X 尚未验证"必须整条保留（守卫生效）。"""
    items = L.extract_items("- ✅ 端点已可用，但冰区附加阻力尚未验证")
    assert len(items) == 1 and "尚未验证" in items[0]


# ── 已闭合的过程坑 vs 仍在的欠账 ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "- **坑（本单）**：解析尾部积分首轮写反了，已改为 1 − e^{−Bω_c^{−4}} 并写进注释。",
    "- **探测自身也有失效模式**：首版只扫单点，导致假阳性。已修正为双点扫描。",
])
def test_fixed_pitfalls_are_dropped(text):
    assert L.extract_items(text) == []


def test_fixed_pitfall_with_remaining_gap_survives():
    items = L.extract_items("- 首版已修正为双点扫描，但三船型仍未验证")
    assert len(items) == 1 and "仍未验证" in items[0]


def test_english_self_attestation_is_dropped():
    items = L.extract_items("- No test was rewritten or xfailed to make this record green; no threshold was touched.")
    assert items == []


# ── 表格行三分类 ───────────────────────────────────────────────────────────────
def test_numeric_table_row_kind_and_drop():
    row = "ship-scale accounting (CA added) -- WRONG for model-test Ct | +15.5, +22.6 | 56.39 | 0"
    assert L.table_row_kind(row) == "numeric"
    assert L.extract_items("| " + row) == []


def test_header_row_is_dropped():
    row = "部署端点 | Ct | 实际来流 (m/s) | 反解 Rt (N) | Ct 误差 (%)"
    assert L.table_row_kind(row) == "header"
    assert L.extract_items("| " + row) == []


def test_risk_register_row_is_text_and_kept_standalone():
    """风险登记行是真实欠账：保留，且不得与上一行续接成"表头+正文"的怪条目。"""
    body = "\n".join([
        "| R11 | 根目录级 figures/ 写入：工具 cwd 依赖导致的结构污染 | LOW | LIKELY | "
        "历史提交中根级 figures/ 落盘 7 个，当前 0 个 | 再发生一次就与 papers/figures/ 双份 |",
    ])
    assert L.table_row_kind(body) == "text"
    items = L.extract_items(body)
    assert len(items) == 1
    assert items[0].startswith("R11 根目录级 figures/ 写入")


def test_text_table_row_does_not_absorb_previous_line():
    body = "\n".join([
        "| R7 | 数据入库体积集中：data 目录占仓内字节大头 | LOW | CERTAIN | 占 21.4% |",
        "| R8 | 论文图散落仓根：出图脚本 cwd 依赖 | MEDIUM | LIKELY | 历史 7 个文件 |",
    ])
    items = L.extract_items(body)
    assert len(items) == 2
    assert items[1].startswith("R8 ")


# ── 丢弃必须记账，不能静默 ─────────────────────────────────────────────────────
def test_diagnostics_account_for_every_drop():
    body = "\n".join([
        "- ✅ 质量守恒验证（漂移 <0.1%）",
        "| 端点 | Ct | Rt (N) | 误差 (%) |",
        "| /calculations/resistance | 0.017240 | 350.03 | +353.68 |",
        "- **局限**：KVLCC2 缺湿面积，J5 判 WITHHELD。",
    ])
    items, diag = L.extract_items_diag(body)
    assert [i for i in items] == ["**局限**：KVLCC2 缺湿面积，J5 判 WITHHELD。"]
    assert diag["positive"] == 1
    assert diag["table_headers"] == 1
    assert diag["table_rows"] == 1
    assert sum(diag.values()) == 3


def test_build_ledger_stats_carry_drop_counters():
    md = "## ⑤ 注意点\n- ✅ 质量守恒验证（漂移 <0.1%）\n- **局限**：未覆盖双桨工况。\n"
    led = L.build_ledger([{"id": 1, "title": "t", "markdown": md}], repo_root="/nonexistent")
    s = led["stats"]
    assert s["items_total"] == 1
    assert s["dropped_positive_statements"] == 1
    for key in ("dropped_table_rows", "dropped_table_headers", "dropped_closed_statements"):
        assert key in s and s[key] >= 0


def test_extract_items_signature_is_unchanged():
    """向后兼容：老调用方拿到的仍是 List[str]。"""
    out = L.extract_items("- **局限**：仅适用 Fr<0.3。")
    assert isinstance(out, list) and all(isinstance(x, str) for x in out)