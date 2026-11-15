"""m21818 验收测试：EFD 库 ITTC-78 换算三要素入库 + 跨尺度换算。

覆盖：
  1. 换算单调性（速度↑ → R_T↑）
  2. 恒等式残差 <1e-9
  3. 缺参 fail-closed
  4. scale_basis 计数
  5. KCS 全尺度阻力锚 ≥1
  6. 可扩展配对数 ≥8

运行：python3 -m pytest tests/test_efd_xscale.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ship_perf.efd_db import EFD_RECORDS, get_scale_basis_counts, get_scalable_pairs, EFDRecord
from ship_perf.ittc78_scaling import (
    ittc57_cf,
    ittc78_extrapolate,
    roughness_allowance_delta_cf,
    extrapolate_from_record,
    safe_extrapolate,
    MissingScalingParamsError,
)


# ---------------------------------------------------------------------------
# C2: scale_basis 计数
# ---------------------------------------------------------------------------

def test_scale_basis_counts():
    counts = get_scale_basis_counts()
    assert counts["full"] >= 4, f"full 记录不足: {counts}"
    assert counts["model"] >= 16, f"model 记录不足: {counts}"
    assert counts["undeclared"] >= 1, f"undeclared 记录不足: {counts}"


# ---------------------------------------------------------------------------
# C3: 恒等式残差 <1e-9
# ---------------------------------------------------------------------------

def test_identity_residual_zero():
    """ITTC-78 代数恒等式自检残差必须 <1e-9。"""
    result = ittc78_extrapolate(
        C_tm=0.004096,
        Re_m=9.28e6,
        Re_s=2.89e9,
        form_factor_1pk=1.342,
        ks_m=150e-6,
        L_s=230.0,
    )
    assert abs(result.identity_residual) < 1e-9, f"残差={result.identity_residual:.2e}"


def test_identity_residual_various_params():
    """多组参数下恒等式残差均 <1e-9。"""
    test_cases = [
        (0.004321, 2.23e6, 4.96e9, 1.450, 150e-6, 320.0),
        (0.003892, 4.66e6, 1.48e9, 1.280, 150e-6, 141.8),
        (0.003733, 1.50e6, 5.00e8, 1.200, 150e-6, 100.0),
        (0.003512, 4.34e6, 5.08e8, 1.200, 150e-6, 117.6),
    ]
    for C_tm, Re_m, Re_s, ff, ks, L in test_cases:
        result = ittc78_extrapolate(C_tm, Re_m, Re_s, ff, ks, L)
        assert abs(result.identity_residual) < 1e-9, (
            f"C_tm={C_tm}: 残差={result.identity_residual:.2e}"
        )


# ---------------------------------------------------------------------------
# C6a: 换算单调性
# ---------------------------------------------------------------------------

def test_scaling_monotonicity():
    """同一船型，速度↑ → 全尺度 R_T↑（单调性）。"""
    kcs_models = [r for r in EFD_RECORDS if r.ship_name == "KCS" and r.scale_basis == "model"]
    kcs_full = [r for r in EFD_RECORDS if r.ship_name == "KCS" and r.scale_basis == "full"][0]

    results = []
    for m in sorted(kcs_models, key=lambda r: r.speed_ms):
        result, R_Ts, V_s = extrapolate_from_record(m, kcs_full)
        results.append((m.speed_ms, R_Ts))

    for i in range(1, len(results)):
        assert results[i][1] > results[i-1][1], (
            f"单调性破坏: V={results[i-1][0]:.3f}→{results[i][0]:.3f}, "
            f"R_T={results[i-1][1]:.1f}→{results[i][1]:.1f}"
        )


# ---------------------------------------------------------------------------
# C6b: 缺参 fail-closed
# ---------------------------------------------------------------------------

def test_missing_params_fail_closed():
    """缺 (1+k)、k_s、ν 任一参数时必须 fail-closed。"""
    undeclared = [r for r in EFD_RECORDS if r.scale_basis == "undeclared"][0]
    kcs_full = [r for r in EFD_RECORDS if r.ship_name == "KCS" and r.scale_basis == "full"][0]

    with pytest.raises(MissingScalingParamsError):
        safe_extrapolate(undeclared, kcs_full)


def test_missing_params_individual():
    """逐项测试缺参 fail-closed。"""
    kcs_model = [r for r in EFD_RECORDS if r.id == "KCS-model-001"][0]
    kcs_full = [r for r in EFD_RECORDS if r.id == "KCS-full-001"][0]

    # 正常情况应通过
    result, R_Ts, V_s = safe_extrapolate(kcs_model, kcs_full)
    assert result.C_ts > 0

    # 构造缺参记录
    rec_no_ff = EFDRecord(
        id="test-no-ff", ship_name="KCS", ship_type="container",
        scale_basis="model", scale_ratio=27.778,
        L_pp_m=7.2786, L_wl_m=7.602, B_m=1.019, T_m=0.250,
        displacement_m3=1.497, Cb=0.6505, wetted_surface_m2=6.019,
        speed_ms=1.281, R_T_N=38.74, C_T=0.004096,
        form_factor_1pk=None, form_factor_provenance="无",
        roughness_ks_m=0.0, roughness_provenance="无",
        nu_m2s=1.004e-6, nu_provenance="无",
        rho_kgm3=998.2, rho_provenance="无",
        test_facility=None, reference="test",
    )
    with pytest.raises(MissingScalingParamsError):
        safe_extrapolate(rec_no_ff, kcs_full)

    rec_no_ks = EFDRecord(
        id="test-no-ks", ship_name="KCS", ship_type="container",
        scale_basis="model", scale_ratio=27.778,
        L_pp_m=7.2786, L_wl_m=7.602, B_m=1.019, T_m=0.250,
        displacement_m3=1.497, Cb=0.6505, wetted_surface_m2=6.019,
        speed_ms=1.281, R_T_N=38.74, C_T=0.004096,
        form_factor_1pk=1.342, form_factor_provenance="无",
        roughness_ks_m=None, roughness_provenance="无",
        nu_m2s=1.004e-6, nu_provenance="无",
        rho_kgm3=998.2, rho_provenance="无",
        test_facility=None, reference="test",
    )
    with pytest.raises(MissingScalingParamsError):
        safe_extrapolate(rec_no_ks, kcs_full)

    rec_no_nu = EFDRecord(
        id="test-no-nu", ship_name="KCS", ship_type="container",
        scale_basis="model", scale_ratio=27.778,
        L_pp_m=7.2786, L_wl_m=7.602, B_m=1.019, T_m=0.250,
        displacement_m3=1.497, Cb=0.6505, wetted_surface_m2=6.019,
        speed_ms=1.281, R_T_N=38.74, C_T=0.004096,
        form_factor_1pk=1.342, form_factor_provenance="无",
        roughness_ks_m=0.0, roughness_provenance="无",
        nu_m2s=None, nu_provenance="无",
        rho_kgm3=998.2, rho_provenance="无",
        test_facility=None, reference="test",
    )
    with pytest.raises(MissingScalingParamsError):
        safe_extrapolate(rec_no_nu, kcs_full)


# ---------------------------------------------------------------------------
# C4: KCS 全尺度阻力锚 ≥1
# ---------------------------------------------------------------------------

def test_kcs_full_scale_anchor():
    """KCS 全尺度可判 pass 的阻力锚 ≥1 条。"""
    kcs_models = [r for r in EFD_RECORDS if r.ship_name == "KCS" and r.scale_basis == "model"]
    kcs_full = [r for r in EFD_RECORDS if r.ship_name == "KCS" and r.scale_basis == "full"][0]

    pass_count = 0
    for m in kcs_models:
        result, R_Ts, V_s = extrapolate_from_record(m, kcs_full)
        assert result.pass_check, f"KCS {m.id} 恒等式自检失败"
        assert R_Ts > 0, f"KCS {m.id} 全尺度阻力非正"
        pass_count += 1

    assert pass_count >= 1, f"KCS 全尺度 pass 记录数={pass_count}"


# ---------------------------------------------------------------------------
# C2: 跨尺度换算可执行记录数 ≥8
# ---------------------------------------------------------------------------

def test_scalable_pairs_count():
    """可跨尺度换算的配对数 ≥8。"""
    pairs = get_scalable_pairs()
    assert len(pairs) >= 8, f"可换算配对数={len(pairs)} < 8"


# ---------------------------------------------------------------------------
# 附加：ITTC-57 Cf 正确性
# ---------------------------------------------------------------------------

def test_ittc57_cf_values():
    """ITTC-57 Cf 在典型 Re 下的值。"""
    # Re=1e7 → Cf≈0.0030
    cf = ittc57_cf(1e7)
    assert 0.0028 < cf < 0.0032, f"Cf(1e7)={cf}"

    # Re=1e9 → Cf≈0.0015
    cf = ittc57_cf(1e9)
    assert 0.0014 < cf < 0.0016, f"Cf(1e9)={cf}"


# ---------------------------------------------------------------------------
# 附加：粗糙度补贴单调性
# ---------------------------------------------------------------------------

def test_roughness_allowance_monotone():
    """k_s↑ → ΔC_f↑（单调性）。"""
    L = 230.0
    d1 = roughness_allowance_delta_cf(0.0, L)
    d2 = roughness_allowance_delta_cf(150e-6, L)
    d3 = roughness_allowance_delta_cf(300e-6, L)
    assert d1 < d2 < d3, f"ΔC_f 非单调: {d1}, {d2}, {d3}"


# ---------------------------------------------------------------------------
# 附加：所有记录都有 provenance
# ---------------------------------------------------------------------------

def test_all_records_have_provenance():
    """每条记录的三要素都有 provenance 字段。"""
    for r in EFD_RECORDS:
        assert r.form_factor_provenance, f"{r.id}: form_factor_provenance 为空"
        assert r.roughness_provenance, f"{r.id}: roughness_provenance 为空"
        assert r.nu_provenance, f"{r.id}: nu_provenance 为空"
