"""QA 产线机器门单测 —— 判据可失败（红队）与确定性。

红队要求（FEAT-QA-FACTORY 铁则）：机器门必须**能被造假语料打穿**。
每个红队用例都断言 gate 对**合成**缺陷给出非 pass 档，且证据带行号/计数。
测试夹具一律用合成域名与合成内容，不含任何领域算例（TensorPlatform 零领域语义铁律）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.qa_factory import consistency as cons
from tools.qa_factory.gate import (EvidenceRoots, check_numbers, check_pointers, check_structure,
                                   decide_tier, extract_key_numbers, extract_pointers,
                                   gate_report, summarize)

# ---------------------------------------------------------------- 合成夹具

GOOD = """# 合成算例 A：目标量 12.345 与基线对比

## ① 案例介绍
两平板间一维输运，几何 L=1.250 m，网格 32 x 64，参数见下表。

| 参数 | 值 | 出处 |
|---|---|---|
| 黏度 | 0.0125 | `artifacts/params.json` |

## ② 方法
用 `tools/synthetic/run.py` 求解，收敛判据 1.0e-10，结果写 `artifacts/run_out.json`。

## ③ 计算结果
| 量 | 计算值 | 参考值 | 相对误差 |
|---|---|---|---|
| 目标量 | 12.345 | 12.400 | 0.44% |

上表数值由 `artifacts/run_out.json` 程序化注入（零手抄）。

## ④ 结果图
场量云图与比较曲线已内嵌 figures。

## ⑤ 注意点
适用范围限于层流；外推需复核。

## ⑥ 误差判定
关键量相对误差 0.44% < 3%，判据成立。

## 附录：判据与局限

合成算例只用于门禁单测：几何与参数均为合成量，不代表任何真实物理算例；
网格分辨率未做收敛性研究，误差结论仅在这一合成设置下成立。方法链路与生产链路同构
（程序化注入 → 工件 JSON → 表格），因此形态判定可迁移；物理判定不可迁移，须由领域仓自证。
"""


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def roots(tmp_path: Path) -> EvidenceRoots:
    work = tmp_path / "m9001"
    _write(work, "artifacts/params.json", '{"nu": 0.0125}')
    _write(work, "artifacts/run_out.json", '{"q": 12.345}')
    _write(work, "tools/synthetic/run.py", "print('ok')")
    return EvidenceRoots(work_dir=work, report_exists=lambda rid: rid in {1, 2, 3},
                         mission_exists=lambda mid: mid in {9001})


# ---------------------------------------------------------------- 结构查

def test_structure_good_report_ok(roots: EvidenceRoots) -> None:
    v = gate_report(report_id=1, markdown=GOOD, title="合成算例 A", figures=2, roots=roots)
    assert v.checks["structure"]["status"] == "ok", v.checks["structure"]


def test_structure_missing_sections_fails() -> None:
    md = "# 一句话\n\n我做了点事，效果不错。\n"
    c = check_structure(md, title="x", figures=0)
    assert c.status == "fail"
    assert c.reason.startswith("corpus_audit:")


def test_meta_report_uses_verdict_table() -> None:
    meta = ("# 【QA】批次质检 1,2,3\n\n## 判定表\n\n| 报告 | 判定 | 理由 |\n|---|---|---|\n"
            "| 1 | PASS | 结构齐备 |\n| 2 | RESUBMIT | 数字对不上 |\n\n"
            + "补充说明。" * 120)
    assert check_structure(meta, title="【QA】批次质检 1,2,3",
                           scenario_key="report-qa").status == "ok"
    bare = "# 【QA】批次质检 9\n\n没有判定表，只有一句话。\n"
    assert check_structure(bare, title="【QA】批次质检 9", scenario_key="report-qa").status == "fail"


# ---------------------------------------------------------------- 指针查（红队）

def test_pointers_all_resolved_ok(roots: EvidenceRoots) -> None:
    c, ptrs = check_pointers(GOOD, roots)
    assert c.status == "ok", (c.reason, c.metrics)
    assert c.metrics["resolved"] >= 3
    assert all(p.status in ("resolved",) for p in ptrs)


def test_redteam_fabricated_artifacts_are_caught(roots: EvidenceRoots) -> None:
    """造假：宣称一批不存在的工件路径 —— 必须被打成 suspect。"""
    md = GOOD.replace("`artifacts/params.json`", "`artifacts/nope_1.json`") \
             .replace("`artifacts/run_out.json`", "`artifacts/nope_2.json`") \
             .replace("`tools/synthetic/run.py`", "`tools/synthetic/nope_3.py`")
    g = gate_report(report_id=2, markdown=md, title="合成算例 A", figures=2, roots=roots)
    assert g.checks["pointers"]["status"] == "fail"
    assert g.checks["pointers"]["reason"] == "pointer_mostly_contradicted"
    assert g.tier == "suspect"
    assert g.detail["contradicted_pointers"], "必须留下反例指针证据（行号+路径）"


def test_pointer_unverifiable_root_is_not_failure() -> None:
    """根不在（老 worktree 回收）→ unverifiable，不冤杀报告。"""
    c, _ = check_pointers(GOOD, EvidenceRoots(work_dir=None))
    assert c.status == "flag"
    assert c.reason == "no_checkable_pointer_root_unavailable"
    assert c.metrics["contradicted"] == 0


def test_pointer_declared_no_artifacts_is_ok() -> None:
    md = "# 纯文档任务\n\n本单为后端文档任务，无工件（不可复算），仅改 docs。\n" + "说明。" * 200
    c, _ = check_pointers(md, EvidenceRoots(work_dir=None))
    assert c.status == "ok"
    assert c.metrics["declared_no_artifacts"] is True


def test_report_and_mission_refs_resolved(roots: EvidenceRoots) -> None:
    md = "# 引用链\n\n见报告 #2 与 mission #9001 的结论，另见报告 #999（不存在）。\n" + "细节。" * 150
    ptrs = extract_pointers(md)
    assert [p.raw for p in ptrs if p.kind == "report"] == ["2", "999"]
    c, ptrs2 = check_pointers(md, roots)
    statuses = {(p.kind, p.raw): p.status for p in ptrs2}
    assert statuses[("report", "2")] == "resolved"
    assert statuses[("mission", "9001")] == "resolved"
    assert statuses[("report", "999")] == "contradicted"
    assert c.status == "flag"


# ---------------------------------------------------------------- 数字查（红队）

def test_numbers_bound_ok(roots: EvidenceRoots) -> None:
    c = check_numbers(GOOD)
    assert c.status == "ok", (c.reason, c.metrics)
    assert c.metrics["bound_ratio"] >= 0.6


def test_redteam_handcopied_numbers_are_caught() -> None:
    """造假：满篇精度数字但无任何溯源线索、也无「不可复算」声明 —— 必须 fail。"""
    rows = "\n".join(f"- 情形 {i}：目标量 {1.2345 + i:0.4f}，效率 {0.9871 - i / 1000:0.4f}，"
                     f"偏差 {3.1415 + i:0.4f}" for i in range(30))
    md = ("# 结果汇报\n\n## ① 案例\n合成情形族。\n\n## ② 方法\n随便算的。\n\n## ③ 计算结果\n\n"
          + rows + "\n\n## ⑤ 注意点\n无。\n\n## ⑥ 误差判定\n很好，误差很小。\n")
    c = check_numbers(md)
    assert c.status == "fail"
    assert c.reason == "numbers_unbound"
    assert c.metrics["key_numbers"] >= 30


def test_numbers_declared_nonrecomputable_downgrades_to_flag() -> None:
    rows = "\n".join(f"- 情形 {i}：目标量 {1.2345 + i:0.4f}" for i in range(20))
    md = "# 结果\n\n" + rows + "\n\n注：以上为人工输入的历史数据，不可复算。\n"
    c = check_numbers(md)
    assert c.status in ("flag", "ok")   # 声明后不再 fail
    assert c.metrics["declared_nonrecomputable"] is True


def test_numbers_meta_report_uses_verdict_table() -> None:
    meta = "| 报告 | 判定 |\n|---|---|\n| 1 | PASS |\n"
    assert check_numbers(meta, kind="meta").status == "ok"


def test_no_numbers_fails() -> None:
    c = check_numbers("# 空报告\n\n完成了。\n")
    assert c.status == "fail" and c.reason == "no_numeric_claim"


def test_extract_key_numbers_ignores_paths_and_years() -> None:
    md = "见 m1234 与 artifacts/a1.json，2026 年，L12 行，值 0.0123。\n"
    toks = [t for t, _ in extract_key_numbers(md)]
    assert "0.0123" in toks
    assert not any(t in ("1234", "2026", "12") for t in toks)


# ---------------------------------------------------------------- 分档与确定性

def test_tier_monotone() -> None:
    from tools.qa_factory.gate import CheckResult
    ok, flag, fail = CheckResult("ok"), CheckResult("flag"), CheckResult("fail")
    assert decide_tier(ok, ok, ok) == "pass"
    assert decide_tier(ok, flag, ok) == "machine-flag"
    assert decide_tier(fail, ok, ok) == "suspect"
    assert decide_tier(fail, flag, fail) == "suspect"


def test_deterministic_same_input_same_verdict(roots: EvidenceRoots) -> None:
    a = gate_report(report_id=1, markdown=GOOD, title="t", figures=2, roots=roots).to_dict()
    b = gate_report(report_id=1, markdown=GOOD, title="t", figures=2, roots=roots).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_summarize_counts(roots: EvidenceRoots) -> None:
    v1 = gate_report(report_id=1, markdown=GOOD, title="t", figures=2, roots=roots)
    v2 = gate_report(report_id=2, markdown="# 空\n\n无。\n", title="t", figures=0, roots=roots)
    s = summarize([v1, v2])
    assert s["total"] == 2
    assert s["tiers"]["pass"] + s["tiers"]["suspect"] + s["tiers"]["machine-flag"] == 2
    assert any(k.startswith("structure:") for k in s["reasons"])


# ---------------------------------------------------------------- 抽样与一致性门

def test_stratified_sample_deterministic() -> None:
    tiers = {i: ("pass" if i % 3 == 0 else "machine-flag" if i % 3 == 1 else "suspect")
             for i in range(300)}
    a = cons.stratified_sample(tiers, per_stratum=5, seed="s1")
    b = cons.stratified_sample(tiers, per_stratum=5, seed="s1")
    assert a == b
    assert len(a) == 15
    assert {p["tier"] for p in a} == {"pass", "machine-flag", "suspect"}
    c = cons.stratified_sample(tiers, per_stratum=5, seed="s2")
    assert [p["report_id"] for p in a] != [p["report_id"] for p in c]


def test_consistency_gate_fails_on_disagreement() -> None:
    machine = {i: ("pass" if i < 50 else "suspect") for i in range(100)}
    deep = {i: ("pass" if i < 40 else "machine-flag") for i in range(100)}   # 一致 40/100
    res = cons.evaluate_consistency(machine, deep)
    assert res["gate_passed"] is False
    assert res["gate_reason"] == "g1_fail"
    assert res["unsafe_machine_pass"]


def test_consistency_gate_passes_on_agreement() -> None:
    machine = {i: ("pass" if i < 50 else "machine-flag" if i < 80 else "suspect") for i in range(100)}
    deep = dict(machine)
    res = cons.evaluate_consistency(machine, deep)
    assert res["gate_passed"] is True and res["agreement_3class"] == 1.0
    assert res["kappa_3class"] == 1.0


def test_consistency_gate_blocks_small_sample() -> None:
    machine = {i: "pass" for i in range(5)}
    res = cons.evaluate_consistency(machine, dict(machine))
    assert res["gate_passed"] is False and res["gate_reason"] == "not_enough_sample"


def test_consistency_renders_markdown() -> None:
    machine = {i: "pass" for i in range(40)}
    md = cons.render_markdown(cons.evaluate_consistency(machine, dict(machine)))
    assert "G1" in md and "gate_passed" in md