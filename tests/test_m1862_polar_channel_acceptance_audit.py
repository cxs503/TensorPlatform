"""m1862 — /polar/acceptance/channel（W3-N11 判据 b）独立复核的机器判据。

背景（见 docs/verification_debt/polar_channel/REPORT.md）：
``services.polar.channel.verify_channel_concentrations`` 自称在锚定浓度 c=0.40 上
标定唯一常数 K、在 c=0.20/0.70 两个 hold-out 浓度上复核生产式与 Riska/VTT 参考式，
``max|rel_err| < 10%`` 即判据 (b) 通过，并被 ``/polar/acceptance`` 用来支撑
"冰区通道阻力已标定" 的能力声明。

本模块把该声明拆成可机器裁决的 5 条（A1-A5），判据与实现在
``tools/verifier/polar_channel_acceptance_audit.py``：

* A1 hold-out 残差 == 混合密度比恒等式（精确）           -> 必须是 PASS（锚）
* A2 判据 (b) 的判别力矩阵（逐项扰动 brash 物理）          -> 记录，缺陷项 xfail
* A3 厚度 hold-out（在 h_c 标定、在 0.8 m 评测）          -> 缺陷项 xfail
* A4 物理极限：c->0 / h->0 退化为开阔水；对 c,h,V 单调      -> 必须是 PASS（锚）
* A5 量纲一致的修法让厚度 hold-out 变成恒真               -> 修法有效性记录

xfail(strict=True) 的用法沿用 tests/test_ice_resistance_crosscheck.py：缺陷被修好后
测试会立刻转红，强制同步更新台账与报告，而不是静默腐烂。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRIPT = os.path.join(REPO, "tools", "verifier", "polar_channel_acceptance_audit.py")


def _load():
    name = "polar_channel_acceptance_audit"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit_mod():
    return _load()


@pytest.fixture(scope="module")
def audit(audit_mod):
    return audit_mod.run_audit(write=False)


# ---------------------------------------------------------------------------
# A1（锚）hold-out 误差 = 混合密度比恒等式
# ---------------------------------------------------------------------------

def test_a1_holdout_error_is_exactly_the_density_ratio(audit):
    """A1: 每个浓度的 hold-out 误差被 rho_b(c)/rho_b(c0)-1 精确解释（<1e-9 %）。"""
    a1 = audit["A1_density_identity"]
    assert a1["pass"] is True, a1["rows"]
    assert a1["max_abs_identity_residual_pct"] < 1e-9
    anchor = [r for r in a1["rows"] if abs(r["concentration"] - 0.40) < 1e-12]
    assert anchor and abs(anchor[0]["rel_err_pct"]) < 1e-9  # 锚点处恒为 0
    # 数值必须是"密度比"量级（2-4%），不能是零也不能是 10% 以上
    holdout = [r for r in a1["rows"] if abs(r["concentration"] - 0.40) > 1e-12]
    assert all(1.0 < abs(r["rel_err_pct"]) < 5.0 for r in holdout), holdout


def test_a1_holdout_error_is_linear_in_concentration(audit):
    """A1b: 因为 rho_b(c) 对 c 线性，hold-out 误差必为 (c-c0)*(rho_i-rho_w)/rho_b(c0)。"""
    a1 = audit["A1_density_identity"]
    rows = a1["rows"]
    c0 = 0.40
    weak, ice = 1025.0, 917.0          # 与通道模型同一物性（rho_w, rho_i）
    rho_b0 = 0.4 * ice + 0.6 * weak
    for row in rows:
        expected = 100.0 * (row["concentration"] - c0) * (ice - weak) / rho_b0
        assert row["rel_err_pct"] == pytest.approx(expected, abs=1e-6)


def test_a1_reported_brash_error_is_reproduced(audit):
    """A1c: 被审计对象自报的 brash 相对误差必须被独立复算逐例复现（<1e-6 %）。"""
    mine = {r["concentration"]: r["rel_err_pct"] for r in audit["A1_density_identity"]["rows"]}
    rows = audit["reported_by_subject"]["rows"]
    assert len(rows) == 3
    for row in rows:
        # 被审计对象把百分比四舍五入到 4 位小数
        assert row["rel_error_brash_pct"] == pytest.approx(mine[row["concentration"]], abs=5e-5)
    # 自报的 headline 是"总阻力"口径，被开阔水项稀释，故小于 brash 口径的 3.30%
    assert audit["verdict"]["reported_max_abs_rel_err_pct"] < max(abs(v) for v in mine.values())


# ---------------------------------------------------------------------------
# A2 判别力矩阵
# ---------------------------------------------------------------------------

def test_a2_discriminating_power_is_partial_not_full(audit):
    """A2: 判据 (b) 的判别力只有 2/8 —— 不是 0（浓度指数能抓到），也远不是 8。"""
    a2 = audit["A2_vacuity"]
    assert a2["n_variants"] == 8
    caught = set(a2["caught"])
    assert caught == {"conc_exp_1.0", "conc_exp_2.5"}, caught
    assert a2["discriminating_power"] == "2/8"


@pytest.mark.xfail(strict=True, reason="欠账 m1862: 判据 (b) 对宽度指数零判别力（B 被标定常数吸收）")
def test_a2_debt_breadth_exponent_untested(audit):
    variants = {v["variant"]: v for v in audit["A2_vacuity"]["variants"]}
    assert not variants["breadth_exp_2"]["criterion_b_pass"]


@pytest.mark.xfail(strict=True, reason="欠账 m1862: 判据 (b) 对 T*Cb 项零判别力（结构性均匀因子被 K 吸收）")
def test_a2_debt_t_cb_untested(audit):
    variants = {v["variant"]: v for v in audit["A2_vacuity"]["variants"]}
    assert not variants["drop_T_Cb"]["criterion_b_pass"]


@pytest.mark.xfail(strict=True, reason="欠账 m1862: 判据 (b) 对速度因子 f_v 零判别力（分子分母同除）")
def test_a2_debt_velocity_factor_untested(audit):
    variants = {v["variant"]: v for v in audit["A2_vacuity"]["variants"]}
    assert not variants["drop_fv"]["criterion_b_pass"]


@pytest.mark.xfail(strict=True, reason="欠账 m1862: 换成更不物理的常数密度反而 0% 误差 —— 判据奖励错模型")
def test_a2_debt_constant_density_passes_better(audit):
    variants = {v["variant"]: v for v in audit["A2_vacuity"]["variants"]}
    row = variants["density_constant_rho_ice"]
    assert not row["criterion_b_pass"], row


# ---------------------------------------------------------------------------
# A3 厚度 hold-out
# ---------------------------------------------------------------------------

def test_a3_calibration_constant_scales_inversely_with_thickness(audit):
    """A3 锚: K * h 在整个厚度网格上恒定（1e-9 相对），即生产/参考差一个长度量纲。"""
    a3 = audit["A3_thickness_holdout"]
    assert a3["k_times_h_is_constant"] is True
    kh = a3["k_times_h"]
    assert max(kh) - min(kh) < 1e-9 * max(abs(v) for v in kh)


def test_a3_error_tracks_hc_over_he(audit):
    """A3 锚: brash 项厚度 hold-out 误差 == h_cal/h_eval - 1（精确到 1e-6 %）。"""
    for row in audit["A3_thickness_holdout"]["rows"]:
        assert row["rel_err_brash_pct"] == pytest.approx(row["predicted_hc_over_he_pct"], abs=1e-6)


@pytest.mark.xfail(strict=True, reason="欠账 m1862: 判据 (b) 仅在 h_b=0.8 m 单一厚度成立，厚度一换即 70% 级失效")
def test_a3_debt_criterion_b_fails_under_thickness_holdout(audit):
    a3 = audit["A3_thickness_holdout"]
    assert a3["criterion_b_pass_under_thickness_holdout"] is True


# ---------------------------------------------------------------------------
# A4 物理极限（锚）
# ---------------------------------------------------------------------------

def test_a4_limits_degenerate_to_open_water(audit):
    """A4: c->0 与 h_b->0 时通道阻力精确退化为开阔水阻力。"""
    a4 = audit["A4_physical_limits"]
    assert a4["pass"] is True
    assert a4["limit_c_to_zero_rel_err"] < 1e-9
    assert a4["limit_h_to_zero_rel_err"] < 1e-9


def test_a4_density_limits(audit):
    d = audit["A4_physical_limits"]["density_limits"]
    assert d["rho_b(0)"] == pytest.approx(d["rho_w"], rel=1e-12)
    assert d["rho_b(1)"] == pytest.approx(d["rho_i"], rel=1e-12)


def test_a4_monotonicity(audit):
    a4 = audit["A4_physical_limits"]
    assert a4["monotone_in_concentration"]
    assert a4["monotone_in_thickness"]
    assert a4["monotone_in_speed"]


# ---------------------------------------------------------------------------
# A5 修法
# ---------------------------------------------------------------------------

def test_a5_consistent_form_makes_thickness_holdout_invariant(audit):
    """A5: 生产式改成 rho_b g h_b^2 B ... 后，厚度 hold-out 误差归零（<5%）。"""
    a5 = audit["A5_consistent_fix"]
    assert a5["thickness_holdout_pass"] is True
    assert a5["max_abs_rel_err_brash_pct"] < 5.0
    # 修法对量级的影响必须显式记录（h_eval/(T*Cb)），不能静默改物理
    assert a5["magnitude_change_at_h_eval"] > 0


# ---------------------------------------------------------------------------
# 证据等级裁决
# ---------------------------------------------------------------------------

def test_verdict_grade_is_withheld(audit):
    """证据等级裁决：判据 (b) 不能支撑 ANCHORED，保持 WITHHELD。"""
    v = audit["verdict"]
    assert v["evidence_grade_for_/polar/acceptance/channel"] == "WITHHELD"
    assert v["reported_criterion_b_pass"] is True          # 被审计对象自报"通过"
    assert v["a3_criterion_b_is_thickness_pinned"] is True  # 但只在单一厚度成立


def test_audit_artifact_is_machine_readable(tmp_path):
    """工件可落盘且字段齐全（防手工数字，报告里所有数字从工件读）。"""
    mod = _load()
    out = mod.run_audit(write=True)
    path = os.path.join(REPO, "artifacts", "polar_channel_acceptance", "audit.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        disk = json.load(fh)
    for key in ("A1_density_identity", "A2_vacuity", "A3_thickness_holdout",
                "A4_physical_limits", "A5_consistent_fix", "verdict"):
        assert key in disk
    assert disk["verdict"] == out["verdict"]

def test_withheld_override_is_honoured_by_census():
    """覆盖度闸门的诚实性：显式 overrides 必须把端点钉回 WITHHELD。

    背景：census 按"路径名 + 容差标记"机械判 ANCHORED，本单的复核文档本身就会
    把 /polar/acceptance/channel 与 /route "升级"。故在
    docs/audit/ship_api_mock_allowlist.json::withheld_override 里显式申报降级，
    本条测试保证该申报真的生效（只降不升）。
    """
    import importlib.util as _ilu
    import json as _json

    spec = _ilu.spec_from_file_location(
        "ship_api_evidence_census",
        os.path.join(REPO, "tools", "audit", "ship_api_evidence_census.py"))
    mod = _ilu.module_from_spec(spec)
    sys.modules["ship_api_evidence_census"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    from pathlib import Path

    over = mod.load_withheld_override(Path(REPO))
    assert "GET /polar/acceptance/channel" in over
    assert "GET /polar/acceptance/route" in over
    for reason in over.values():
        assert len(reason) > 20  # 必须给出理由，不接受空申报

    led = mod.census(Path(REPO))
    by_ep = {r["endpoint"]: r for r in led["endpoints"]}
    for key in over:
        assert by_ep[key]["grade"] == "WITHHELD", by_ep[key]
        assert by_ep[key]["override_reason"]
