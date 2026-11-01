"""SUBOFF 阻力验证的「参考侧供给」回归门 —— m1709 验证欠账。

背景：m1444 的 `tools/validate_suboff_resistance.py` 把 SUBOFF 实验值硬编码，
其中 Rt 用的是正典 v3.0 已明确作废的 legacy 值（12.50 N，见
`backend/data/benchmark_reference.json` 的 `legacy_values.v1_experimental_rt_n`），
而同一行的 Ct 用的是有效值 —— 参考侧自相矛盾，导致「Rt 误差 ~99%、HONEST-FAIL」
这一结论是数据缺陷的产物，而非模型真实精度。

本文件把这条教训固化成机器可裁的门：
  G1 参考侧供给闭合（Rt = Ct·½ρV²S，正典恒等式 R1）
  G2 Froude 派生量闭合（Fr = V/√(gL)，正典 R3）
  G3 禁止使用作废值（legacy Rt 不得出现在任何验证脚本的参考侧）
  G4 台账数字必须由程序复算（零手抄：ledger == 独立重算）
  G5 判决如实（对正典 v3.0 的误差是否 <3% / <15%，逐项回答）

运行：`python3 -m pytest tests/test_suboff_canon_audit.py -q`
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for p in (str(ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.validate_suboff_resistance_canon import (  # noqa: E402
    build_ledger,
    engine_resistance,
    load_canon,
    reference_closure,
    suboff_cases,
)

G = 9.81
CANON = load_canon()
CASES = suboff_cases(CANON)
LEDGER = build_ledger()
ROWS = {r["case_id"]: r for r in LEDGER["rows"]}


# --------------------------------------------------------------------------- #
# G1 参考侧供给闭合
# --------------------------------------------------------------------------- #
def test_g1_reference_supply_closes_on_canon_identity():
    assert len(CASES) == 4, f"正典 v3.0 应含 4 个 SUBOFF 算例，实际 {len(CASES)}"
    for case in CASES:
        rel = reference_closure(case)
        assert rel["closure_rel_err"] < 1e-3, (
            f"{case['case_id']}: Rt={case['rt_ref_n']} 与 Ct·½ρV²S={rel['rt_from_identity_n']:.3f} "
            f"不闭合（相对偏差 {rel['closure_rel_err']:.2e}）"
        )


# --------------------------------------------------------------------------- #
# G2 Froude 派生量闭合
# --------------------------------------------------------------------------- #
def test_g2_froude_column_is_derived_not_transcribed():
    for case in CASES:
        fr = case["speed_ms"] / math.sqrt(G * case["length_m"])
        assert abs(fr - case["fr_canon"]) / case["fr_canon"] < 1e-3
    # 旧工具的错误数列（真值的一半量级）必须被排除
    for case in CASES:
        assert case["fr_canon"] > 0.4, f"{case['case_id']}: Fr={case['fr_canon']} 与 L/V 不自洽"


# --------------------------------------------------------------------------- #
# G3 禁止作废值进入参考侧
# --------------------------------------------------------------------------- #
def test_g3_superseded_reference_values_are_not_used():
    for case in CASES:
        legacy = case["legacy_rt_n"]
        assert legacy is not None, "正典应保留 legacy 值以支持本门禁"
        ratio = case["rt_ref_n"] / legacy
        assert ratio > 5.0, (
            f"{case['case_id']}: 正典 Rt={case['rt_ref_n']} 与作废值 {legacy} 的比值 {ratio:.2f} "
            "应显著大于 1（正典裁决书对 SUBOFF-01 记载 ~7.6 倍）"
        )
        # 台账里绝不允许 legacy 值充当参考
        assert ROWS[case["case_id"]]["rt_ref_n"] == case["rt_ref_n"]


@pytest.mark.parametrize("script", [
    "tools/validate_suboff_resistance.py",
    "tools/validate_suboff_resistance_canon.py",
])
def test_g3b_scripts_carry_no_retired_literals(script):
    text = (ROOT / script).read_text(encoding="utf-8")
    # 去掉文档字符串与注释里的引述后再查字面量：作废值只能出现在说明文字里
    text = re.sub(r'""".*?"""', "", text, flags=re.S)
    code = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    for literal in ("12.50", "21.00", "31.50", "45.00"):
        # 允许出现在文档字符串/报告字符串中，禁止出现在数据表里
        assert not re.search(rf'"_?rt_exp\w*"\s*:\s*{re.escape(literal)}', code), (
            f"{script} 仍把作废参考值 {literal} 当作实验值"
        )


# --------------------------------------------------------------------------- #
# G4 零手抄：台账数字由程序复算
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_canon_surface", [True, False])
def test_g4_ledger_matches_independent_recompute(use_canon_surface):
    key = "engine_s_canon" if use_canon_surface else "engine_s_empirical"
    for case in CASES:
        re_ = engine_resistance(case, use_canon_surface)
        row = ROWS[case["case_id"]][key]
        assert row["rt_n"] == pytest.approx(re_["rt_n"], rel=1e-12, abs=1e-12)
        assert row["ct"] == pytest.approx(re_["ct"], rel=1e-12, abs=1e-15)
        err = 100.0 * (re_["rt_n"] - case["rt_ref_n"]) / case["rt_ref_n"]
        assert row["rt_err_pct"] == pytest.approx(err, rel=1e-12, abs=1e-12)


# --------------------------------------------------------------------------- #
# G5 判决如实
# --------------------------------------------------------------------------- #
def test_g5_verdict_is_reported_honestly():
    s = LEDGER["summary_s_canon"]
    # 修正参考侧后，误差量级从「~99%（作废旧值造成）」回落到 10^1% 量级
    assert 5.0 < s["rt_abs_err_pct_mean"] < 40.0
    assert s["rt_abs_err_pct_max"] < 50.0
    # 逐项回答两档精度线：本模型对 SUBOFF 回转体不满足 15% 验收线
    assert s["all_rt_within_3pct"] is False
    assert s["all_rt_within_15pct"] is False
    over15 = [r["case_id"] for r in LEDGER["rows"]
              if abs(r["engine_s_canon"]["rt_err_pct"]) >= 15.0]
    assert len(over15) == 1, f"超出 15% 验收线的算例应为 1 个（SUBOFF-04），实际 {over15}"


def test_g5b_high_froude_degrades_monotonically():
    """误差随 Fn 单调恶化 → 是适用范围问题（阻力模型的 Cr 律高 Fn 失真），不是随机噪声。"""
    errs = [ROWS[c["case_id"]]["engine_s_canon"]["rt_err_pct"] for c in CASES]
    assert errs == sorted(errs), f"Rt 误差未随 Fn 单调恶化：{errs}"


def test_g6_ledger_artifact_written():
    path = ROOT / "outputs" / "suboff_canon_validation" / "ledger.json"
    assert path.exists(), "先运行 python3 tools/validate_suboff_resistance_canon.py"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["canon_version"] == CANON["version"]
    assert [r["case_id"] for r in on_disk["rows"]] == [c["case_id"] for c in CASES]
    # 落盘台账与重算台账逐值一致（防止工件与代码脱钩）
    for a, b in zip(on_disk["rows"], LEDGER["rows"]):
        assert a["engine_s_canon"]["rt_n"] == pytest.approx(b["engine_s_canon"]["rt_n"], rel=1e-9)