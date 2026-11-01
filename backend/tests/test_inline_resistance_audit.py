"""闸门测试：TensorPlatform inline 阻力端点验证欠账（tools/verify/inline_resistance_audit.py）。

本文件把"验证判决"变成可回归的机器判据，防止后续有人悄悄改回伪造系数、
或把数据集缺陷重新当成端点缺陷（反之亦然）。三条纪律：
  1. 判据必须能从工件（端源码 / 数据集 / 闭式解析）复算，不读手抄数字；
  2. FAIL 断言即"已证明的缺陷"——修好了它会变红，逼后代重新审计而不是删测试；
  3. 解析参考自身也要被独立核对（闭合面积对数值积分、ITTC-57 对闭式）。
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AUDIT_PY = REPO / "tools" / "verify" / "inline_resistance_audit.py"
INLINE_PY = REPO / "backend" / "api" / "calculations.py"
DATASET = REPO / "backend" / "data" / "benchmark_reference.json"


def _load(name: str, path: Path):
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses 需要模块已注册
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load("inline_resistance_audit", AUDIT_PY)


@pytest.fixture(scope="module")
def result(audit):
    return audit.run_audit()


@pytest.fixture(scope="module")
def checks(result):
    return {c["id"]: c for c in result.checks}


# ---------------------------------------------------------------------------
# 0. 参考实现自校（解析参考先自证，才能用来判别人）
# ---------------------------------------------------------------------------
def test_ittc57_friction_line_is_standard_closed_form(audit):
    """ITTC-1957: Cf = 0.075/(log10 Re - 2)^2 —— 逐点对闭式复算。"""
    for re in (1e6, 1e7, 1.302e7, 1e8, 1e9):
        expected = 0.075 / (math.log10(re) - 2.0) ** 2
        assert audit.ittc57_cf(re) == pytest.approx(expected, rel=1e-12)
    # 单调递减（物理必需）
    assert audit.ittc57_cf(1e6) > audit.ittc57_cf(1e7) > audit.ittc57_cf(1e8)


def test_spheroid_area_matches_numerical_surface_of_revolution(audit):
    """长球体湿表面积闭式解 vs 数值积分 2π∫r√(1+r'²)dx（独立复核解析参考）。"""
    L, D = 4.356, 0.508
    a, b = L / 2.0, D / 2.0
    n = 400_000
    dx = L / n
    s = 0.0
    for i in range(n):
        x = -a + (i + 0.5) * dx
        r = b * math.sqrt(max(1.0 - (x / a) ** 2, 0.0))
        drdx = -(b * b / (a * a)) * x / max(r, 1e-12)
        s += 2.0 * math.pi * r * math.sqrt(1.0 + drdx * drdx) * dx
    closed = audit.spheroid_wetted_area(L, D)
    assert closed == pytest.approx(s, rel=2e-3)


def test_hoerner_form_factor_monotone_and_limits(audit):
    """形状因子随细长比单调下降，且 L/D→∞ 时 →1（光滑细长体）。"""
    vals = [audit.hoerner_form_factor(ld) for ld in (4.0, 6.0, 8.575, 12.0, 20.0)]
    assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    assert vals[0] > 1.05 and audit.hoerner_form_factor(1e6) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 1. 端点摩擦阻力通道是 REAL（这条是"绿灯"：证明判据不是一味否定）
# ---------------------------------------------------------------------------
def test_inline_friction_component_equals_ittc57(audit):
    """端点 Cf 通道逐字等于 ITTC-1957：Ct - Cr 应与闭式 Cf 一致到 1e-9 相对。"""
    mod = audit.load_inline_module()
    out = audit.call_resistance(
        mod, ship_type="SUBOFF", speed_ms=3.0, length=4.356, beam=0.508,
        draft=0.476, cb=0.542, water_density=audit.RHO, kinematic_viscosity=audit.NU,
    )
    s = math.pi * 0.508 * 4.356 * (0.5 + 0.25 * 0.542)
    q = 0.5 * audit.RHO * s * 3.0 ** 2
    cf_impl = (out["frictional_resistance_n"]) / q
    assert cf_impl == pytest.approx(audit.ittc57_cf(out["reynolds_number"]), rel=1e-9)
    assert (out["frictional_resistance_n"] + out["residual_resistance_n"]) == pytest.approx(
        out["total_resistance_n"], rel=1e-9
    )


# ---------------------------------------------------------------------------
# 2. 已证明的缺陷（回归锁）
# ---------------------------------------------------------------------------
def test_wetted_surface_formula_deviates_from_analytic_by_20pct(audit, result, checks):
    """P1a：inline 湿表面积比同 L/D 解析回转体面积低估约 20.9% > 15% 门。"""
    assert checks["P1a"]["verdict"] == "FAIL"
    assert checks["P1a"]["value"] == pytest.approx(-20.91, abs=0.2)
    rev = next(r for r in result.rows if r.s_spheroid)
    assert rev.s_spheroid < rev.s_cylinder  # 解析估计落在圆柱上界之内
    assert (rev.s_inline - rev.s_spheroid) / rev.s_spheroid < -0.15


def test_dataset_wetted_surface_is_consistent_with_analytic(audit, checks):
    """P1b 反证：数据集 S=5.42 m² 与解析 5.4938 m² 差 -1.34% ⇒ 缺陷在 endpoints 侧。"""
    assert checks["P1b"]["verdict"] == "PASS"
    assert abs(checks["P1b"]["value"]) < 5.0


def test_residual_term_has_no_provenance_in_source():
    """P5：Cr 项在源码里没有任何引文/拟合点位；只有自述 'curve-fitting'。"""
    src = INLINE_PY.read_text()
    start = src.index("Empirical residual resistance coefficient")
    block = src[start: src.index("Rr = 0.5 * rho * S * Cr * V ** 2")]
    assert "curve-fitting to experimental data" in block
    for token in ("http", "doi", "ITTC", "Holtrop", "1984", "R2", "R²", "table", "Table"):
        assert token not in block, f"Cr 项出现了出处线索 {token!r}，请重新审计其引文"


def test_dataset_violates_resistance_identity(audit, result, checks):
    """P2a：数据集 Rt 列与 0.5ρSCtV² 差到 852%，Rt 列不可用于误差判定。"""
    assert checks["P2a"]["verdict"] == "FAIL"
    id_rows = [r for r in result.rows if r.identity_resid_pct is not None]
    assert len(id_rows) == 4
    assert all(abs(r.identity_resid_pct) > 15.0 for r in id_rows)
    assert 0.0 < min(abs(r.identity_resid_pct) for r in id_rows)


def test_dataset_violates_froude_identity(audit, result, checks):
    """P2b：DTMB-5415 四行 Fn 列与 V/√(gL) 差 -462.9%（尺度/速度混装）。"""
    assert checks["P2b"]["verdict"] == "FAIL"
    bad = [r for r in result.rows if r.fn_resid_pct is not None and abs(r.fn_resid_pct) > 5.0]
    assert {r.ship_type for r in bad} == {"DTMB-5415"}
    for r in bad:
        assert r.admissible is False and r.reject_reason


def test_inline_underestimates_full_hull_by_over_15pct(audit, result, checks):
    """P3-KVLCC2：丰满船 Ct 全部低估 49.5–56.1%（缺 1+k 与兴波项）。"""
    assert checks["P3-KVLCC2"]["verdict"] == "FAIL"
    kv = [r for r in result.rows if r.ship_type == "KVLCC2"]
    assert len(kv) == 4 and all(r.err_inline_pct < -15.0 for r in kv)


def test_implied_form_factor_below_physical_lower_bound(audit, result, checks):
    """P6：水面船隐含 (1+k)+Cw/Cf 仅 1.02–1.03，低于任何已发表丰满船下限。"""
    assert checks["P6"]["verdict"] == "FAIL"
    surf = [r for r in result.rows if r.s_spheroid is None and r.admissible]
    assert surf and all(r.implied_1k is not None and r.implied_1k < 1.10 for r in surf)


def test_submerged_body_analytic_reference_disagrees_with_dataset(audit, result, checks):
    """P4：(1+k)Cf 比数据集低 19.2–37.8% ⇒ 数据集 Ct 在高 Fn 段混入非全浸没关系。"""
    assert checks["P4"]["verdict"] == "FAIL"
    rev = [r for r in result.rows if r.ct_analytic is not None]
    assert all(r.err_analytic_pct is not None and r.err_analytic_pct < -15.0 for r in rev)


# ---------------------------------------------------------------------------
# 3. 审计器本身的健全性
# ---------------------------------------------------------------------------
def test_audit_covers_all_dataset_cases(audit, result):
    data = json.loads(DATASET.read_text())
    assert len(result.rows) == len(data["cases"])
    assert {r.case_id for r in result.rows} == {c["case_id"] for c in data["cases"]}


def test_audit_exit_code_blocks_ci_while_fail_exists(audit, tmp_path):
    """有 FAIL 判据时退出码必须为 1（可作为 CI 门）。"""
    rc = audit.main(["--out", str(tmp_path), "--no-figures"])
    assert rc == 1
    payload = json.loads((tmp_path / "results.json").read_text())
    assert payload["summary"]["FAIL"] >= 6
    assert payload["evidence_level"]["friction_line(ITTC-57)"].startswith("REAL")


def test_audit_figures_renderable(audit, result, tmp_path):
    """figures 可程序化生成（报告要求云图/曲线，不许手绘）。"""
    figs = audit.make_figures(result, tmp_path)
    assert len(figs) >= 4
    for f in figs:
        p = Path(f["path"])
        assert p.exists() and p.stat().st_size > 5_000