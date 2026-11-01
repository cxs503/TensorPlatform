"""阻力引擎外部基准闸门测试（E1–E7，m1666 验证欠账）。

判据全部是**可手算复核**的解析量或正典记录值，不连服务、不连 DB：

  A1 球体极限：L=D 时 S = 4πr²
  A2 长椭球闭式解 vs 正典记录实测 S=5.42 m²（Roddy 1990）——基准自校 <3%
  A3 引擎 S 公式对同一行偏差 −18.5% → 必须判 FAIL（不得静默通过）
  A4 闸门可 PASS：把记录 S 换成引擎自洽值时应判 PASS（证明不是永久 FAIL 闸）
  A5 Cr 形状律 4 项解析极限（含 Fn<=0.05 归零保护）
  A6 参考行 reference_status=WITHHELD 时必须 WITHHELD，不得产生精度判决
  A7 误差分解恒等式（面积项 × 系数项 × 记录闭合因子 = 总比）
  A8 引擎默认数值指纹回归（本单只加闸门，不改引擎输出）
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services import resistance_external_baseline as ext  # noqa: E402
from services import ship_resistance as sr  # noqa: E402

CANON = BACKEND_DIR / "data" / "benchmark_reference.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(CANON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def suboff01(dataset):
    return next(c for c in dataset["cases"] if c["case_id"] == "SUBOFF-01")


@pytest.fixture(scope="module")
def dtmb01(dataset):
    return next(c for c in dataset["cases"] if c["case_id"] == "DTMB5415-01")


# ---------------------------------------------------------------------------
# A1/A2 — geometric baseline is analytically sound and self-checks on data
# ---------------------------------------------------------------------------

def test_spheroid_sphere_limit():
    """L = D ⇒ 球体，侧面积必须是 4πr²（闭式解自检）。"""
    s = ext.prolate_spheroid_wetted_area(1.0, 1.0)
    assert s == pytest.approx(math.pi, rel=1e-12)          # r=0.5 → 4πr² = π


def test_spheroid_slender_asymptote_and_monotone():
    """细长极限：S → π²LD/4（= 柱面 πDL 的 π/4 倍），且随 D 单调增。

    这条渐近式是本模块几何基准的解析锚：它说明长椭球**不是**柱面，
    用它当回转体基准不会系统性高估面积。
    """
    s_thin = ext.prolate_spheroid_wetted_area(100.0, 0.02)
    ref = math.pi ** 2 * 100.0 * 0.02 / 4.0
    assert abs(s_thin - ref) / ref < 1e-4
    assert s_thin == pytest.approx(math.pi * 0.02 * 100.0 * math.pi / 4.0, rel=1e-4)
    vals = [ext.prolate_spheroid_wetted_area(4.356, d) for d in (0.1, 0.3, 0.508, 0.8)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_baseline_selfcheck_against_recorded_suboff(suboff01):
    """几何基准 vs 记录实测 S：必须 <3%，否则基准不可用、判决须降级。"""
    ws = ext.check_wetted_surface_external(suboff01)
    assert ws["baseline_name"] == "prolate_spheroid_same_L_D"
    assert ws["baseline_selfcheck"]["verdict"] == ext.PASS
    assert abs(ws["baseline_selfcheck"]["error_pct"]) < 3.0
    # 记录值 5.42 m²，几何基准 5.49 m² —— 两条独立来源互证
    assert ws["wetted_surface_recorded_m2"] == pytest.approx(5.42)
    assert ws["wetted_surface_baseline_m2"] == pytest.approx(5.4938, rel=1e-3)


# ---------------------------------------------------------------------------
# A3 — the audited defect: engine S formula vs external baselines
# ---------------------------------------------------------------------------

def test_engine_wetted_surface_fails_15pct_bar(suboff01):
    ws = ext.check_wetted_surface_external(suboff01)
    assert ws["wetted_surface_engine_m2"] == pytest.approx(4.41791, rel=1e-5)
    assert ws["engine_vs_recorded"]["error_pct"] == pytest.approx(-18.489, abs=1e-2)
    assert ws["engine_vs_recorded"]["verdict"] == ext.FAIL
    assert ws["engine_vs_recorded"]["within_accept_bar"] is False
    assert ws["engine_vs_baseline"]["verdict"] == ext.FAIL
    assert ws["verdict"] == ext.FAIL


def test_wetted_surface_ship_fit_is_beam_sensitive_not_axisymmetric():
    """该拟合式的病根：S 随 B（船宽）线性、经 Cb 只有 0.25 权，回转体上必然偏小。

    Cb 从 π/6 扫到 0.8，engine S 全区间都低于记录 5.42 m² —— 说明偏差来自
    「式子的形状」而不是「Cb 取值」，换 Cb 无法洗白。
    """
    for cb in (ext.CB_PROALTE_SPHEROID, 0.542, 0.6, 0.7, 0.8):
        s = sr.wetted_surface_axisym(0.508, 4.356, cb)
        assert ext.rel_err_pct(s, 5.42) < 0.0        # 恒偏小
        assert sr.wetted_surface_axisym(0.508, 4.356, cb) == pytest.approx(
            math.pi * 0.508 * 4.356 * (0.5 + 0.25 * cb), rel=1e-12)


# ---------------------------------------------------------------------------
# A4 — the gate is not a permanent-FAIL gate
# ---------------------------------------------------------------------------

def test_gate_passes_a_consistent_case(suboff01):
    """把记录 S 直接设成引擎自洽值（构造 CASE），闸门必须判 PASS。"""
    synthetic = json.loads(json.dumps(suboff01))
    synthetic["case_id"] = "SYNTH-CONSISTENT"
    s_engine = sr.wetted_surface_axisym(0.508, 4.356, 0.542)
    synthetic["geometry"]["wetted_surface_m2"] = s_engine
    # 同时把记录的 Ct/Rt 也设为引擎自洽值，消除 A7 的系数项偏差
    bd = sr.resistance_breakdown(4.356, 0.508, 0.508, 0.542, 3.0, 998.2, 1.004e-06)
    synthetic["experimental_ct"] = bd["ct"]
    synthetic["experimental_rt_n"] = bd["rt_n"]
    row = ext.audit_case(synthetic)
    ws = row["wetted_surface_external"]
    assert ws["engine_vs_recorded"]["verdict"] == ext.PASS
    # 该构造下椭球基准偏离记录值 24% → 判为不适用，降级 WITHHELD 且不参与判决
    assert ws["baseline_inadmissible"] is True
    assert ws["engine_vs_baseline"]["verdict"] == ext.WITHHELD
    assert row["residual_magnitude"]["verdict"] == ext.PASS
    assert row["end_to_end"]["ct_verdict"] == ext.PASS
    assert row["end_to_end"]["rt_verdict"] == ext.PASS
    # 精度判决 PASS；但 Cb 无溯源 → provenance_caveat 仍为 True（证据等级问题单列）
    assert row["verdict"] == ext.PASS
    assert row["provenance_caveat"] is True
    assert row["cb_provenance"]["verdict"] == ext.WITHHELD


# ---------------------------------------------------------------------------
# A5 — Cr shape law limits
# ---------------------------------------------------------------------------

def test_residual_law_limits_and_floor():
    law = ext.check_residual_law_form()
    assert law["verdict"] == ext.PASS
    for key in ("L1_zero_at_fn0", "L2_monotone", "L3_magnitude_band", "L4_exponent_2p5"):
        assert law["checks"][key]["pass"] is True, key
    # fn_floor=0.05 以下**有意**归零（阻塞流区无残阻），不得算作单调性失败
    assert sr.residual_coefficient(0.05, 0.542) == 0.0
    assert sr.residual_coefficient(0.02, 0.542) == 0.0
    assert law["checks"]["L4_exponent_2p5"]["ratio"] == pytest.approx(2.0 ** 2.5)


# ---------------------------------------------------------------------------
# A6 — non-adjudicable reference rows must not produce a verdict
# ---------------------------------------------------------------------------

def test_withheld_reference_rows_are_not_judged(dtmb01):
    assert dtmb01["reference_status"] == "WITHHELD"
    assert ext.is_adjudicable(dtmb01) is False
    mag = ext.check_residual_magnitude(dtmb01, dtmb01["speed_ms"], 998.2, 1.004e-06)
    assert mag["verdict"] == ext.WITHHELD
    assert "WITHHELD" in mag["reason"]
    end = ext.check_end_to_end(dtmb01, dtmb01["speed_ms"], 998.2, 1.004e-06)
    assert end["verdict"] == ext.WITHHELD


def test_missing_recorded_s_is_withheld_not_fail():
    """没有实测湿面积时只能 WITHHELD —— 不许用几何基准单方面判引擎死刑。"""
    case = {
        "case_id": "NO-S",
        "form_type": "body_of_revolution",
        "reference_status": "DERIVED",
        "geometry": {"length_m": 4.356, "diameter_m": 0.508},
    }
    ws = ext.check_wetted_surface_external(case)
    assert ws["verdict"] == ext.WITHHELD
    assert ws["baseline_selfcheck"]["verdict"] == ext.WITHHELD


# ---------------------------------------------------------------------------
# A7 — multiplicative error decomposition is an identity
# ---------------------------------------------------------------------------

def test_end_to_end_decomposition_identity(suboff01):
    end = ext.check_end_to_end(suboff01, 3.0, 998.2, 1.004e-06)
    dec = end["decomposition"]
    assert dec["identity_holds"] is True
    assert dec["product_check"] == pytest.approx(dec["total_ratio"], rel=1e-12)
    # 记录的 Ct↔Rt 闭合因子 ≈1（正典自洽）
    assert dec["closure_recorded"] == pytest.approx(1.0, abs=1e-4)
    # 面积项 −18.49%，系数项 −8.90%，总 −25.75%
    assert dec["area_term_pct"] == pytest.approx(-18.489, abs=1e-2)
    assert dec["ct_term_pct"] == pytest.approx(-8.898, abs=1e-2)
    assert dec["total_error_pct"] == pytest.approx(-25.745, abs=1e-2)
    # 反事实（用记录 S）= 只剩系数项 → 面积误差是总误差的主导项
    assert dec["counterfactual_rt_error_pct"] == pytest.approx(-8.898, abs=1e-2)
    assert end["rt_verdict"] == ext.FAIL and end["ct_verdict"] == ext.WARN


def test_residual_magnitude_grows_with_froude(dataset):
    """Cr 律的 Fn^2.5 指数与记录不符：低 Fn 偏小、高 Fn 偏大（过陡）。"""
    rows = [ext.check_residual_magnitude(c, c["speed_ms"], 998.2, 1.004e-06)
            for c in dataset["cases"] if c["case_id"].startswith("SUBOFF")]
    errs = [r["error_pct"] for r in rows]
    assert errs[0] == pytest.approx(-36.25, abs=0.1)
    assert errs[-1] == pytest.approx(82.56, abs=0.1)
    assert all(b > a for a, b in zip(errs, errs[1:]))          # 单调变差


# ---------------------------------------------------------------------------
# A8 — dataset roll-up + engine fingerprint regression
# ---------------------------------------------------------------------------

def test_dataset_rollup_binding(dataset):
    audit = ext.audit_dataset(dataset)
    assert audit["n_rows"] == 12
    assert audit["per_form_verdict"]["displacement_hull"] == ext.WITHHELD
    assert audit["per_form_verdict"]["body_of_revolution"] == ext.FAIL
    assert audit["binding"] == ext.FAIL


def test_engine_default_numbers_unchanged():
    """本单只加闸门：引擎默认输出必须与审计前一字不差（防止顺手改数）。"""
    bd = sr.resistance_breakdown(4.356, 0.508, 0.508, 0.542, 3.0, 998.2, 1.004e-06)
    assert bd["wetted_surface_m2"] == pytest.approx(4.417911491407555, rel=1e-15)
    assert bd["cf"] == pytest.approx(0.0028672071638755144, rel=1e-15)
    assert bd["cr"] == pytest.approx(0.0005946819392833916, rel=1e-12)
    assert bd["ct"] == pytest.approx(0.003461889103158906, rel=1e-12)
    assert bd["rt_n"] == pytest.approx(68.7005544395378, rel=1e-12)


def test_verdict_bar_boundaries():
    assert ext.verdict_for_error(0.0) == ext.PASS
    assert ext.verdict_for_error(3.0) == ext.PASS
    assert ext.verdict_for_error(3.0001) == ext.WARN
    assert ext.verdict_for_error(15.0) == ext.WARN
    assert ext.verdict_for_error(15.0001) == ext.FAIL
    assert ext.verdict_for_error(-15.0001) == ext.FAIL
    assert ext.verdict_for_error(float("nan")) == ext.WITHHELD


def test_worst_severity_ordering():
    assert ext.worst([ext.PASS, ext.WARN]) == ext.WARN
    assert ext.worst([ext.WARN, ext.WITHHELD, ext.PASS]) == ext.WITHHELD
    assert ext.worst([ext.WITHHELD, ext.FAIL]) == ext.FAIL
    assert ext.worst([]) == ext.PASS