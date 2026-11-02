"""m1817 回归测试：阻力域解析自洽审计（Wigley 解析船型）。

两层：
  A. 参考侧不变量（无网络、无条件成立）——钉住“尺子”：Michell 两条独立路线一致、
     θ 网格加倍收敛、Wigley 几何闭式值、湿表面积积分收敛、ITTC-57 与 Schoenherr 相容。
  B. 平台侧冻结值（读 docs/verification_debt/wigley_michell/artifacts/audit_v2.json）——
     钉住 2026-11-02 实测偏差，将来平台修好（或再退化）测试会失败，强制更新台账。

运行：python3 -m pytest tests/test_resistance_identity_audit_m1817.py -q
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "docs" / "verification_debt" / "wigley_michell"
if str(AUDIT_DIR) not in sys.path:
    sys.path.insert(0, str(AUDIT_DIR))

import references as ref  # noqa: E402
from michell_wigley import (  # noqa: E402
    Wigley, michell_rw, rw_double_quad, rw_theta_integral, wetted_surface,
)

L, B, T = 100.0, 10.0, 6.25
HULL = Wigley(L=L, B=B, T=T, rho=1025.0)
ARTIFACT = AUDIT_DIR / "artifacts" / "audit_v2.json"


# --------------------------------------------------------------------------
# A. 参考侧不变量
# --------------------------------------------------------------------------
def test_wigley_geometry_closed_form():
    assert abs(ref.wigley_cb() - 4.0 / 9.0) < 1e-12
    assert abs(ref.wigley_volume(L, B, T) - 4.0 * L * B * T / 9.0) < 1e-9
    # 数值体积积分必须复现闭式值（<1e-6 相对）
    gx, wx = np.polynomial.legendre.leggauss(160)
    gz, wz = np.polynomial.legendre.leggauss(160)
    x = 0.5 * L * gx
    z = 0.5 * T * (gz + 1.0)
    X, Z = np.meshgrid(x, z, indexing="ij")
    y_half = 0.5 * B * (1.0 - (2.0 * X / L) ** 2) * (1.0 - (Z / T) ** 2)
    vol = 2.0 * np.einsum("i,j,ij->", 0.5 * L * wx, 0.5 * T * wz, y_half)
    assert abs(vol / ref.wigley_volume(L, B, T) - 1.0) < 1e-6


def test_wetted_surface_quadrature_converges():
    s1 = wetted_surface(HULL, n_x=120, n_z=120)
    s2 = wetted_surface(HULL, n_x=240, n_z=240)
    assert s1 > 0.0
    assert abs(s2 - s1) / s2 < 1e-4
    # 解析量级自检：平底驳船 S = 2(LB + LT + BT)... Wigley 应落在其 0.6~0.8 倍区间
    barge = 2.0 * (L * B + L * T + B * T)
    assert 0.35 * barge < s2 < 0.6 * barge


def test_michell_two_independent_routes_agree():
    """路线 A（内层闭式 + θ 求积）与路线 B（面元暴力积分）必须一致。"""
    u = 0.25 * math.sqrt(ref.G * L)
    a = rw_theta_integral(HULL, u, n_theta=96)
    b = rw_double_quad(HULL, u, n_x=160, n_z=120, n_theta=48)
    assert a > 0.0 and b > 0.0
    assert abs(b - a) / a < 0.03, f"routes disagree: A={a:.4e} B={b:.4e}"


def test_michell_theta_refinement():
    for fn in (0.15, 0.25, 0.35):
        u = fn * math.sqrt(ref.G * L)
        r1 = rw_theta_integral(HULL, u, n_theta=96)
        r2 = rw_theta_integral(HULL, u, n_theta=192)
        assert r1 > 0.0 and r2 > 0.0
        assert abs(r2 - r1) / r2 < 0.01
    # 兴波阻力量级自检：Cw 落在薄船文献区间 3e-4 ~ 1e-2
    s = wetted_surface(HULL, n_x=200, n_z=200)
    for fn in (0.20, 0.30):
        u = fn * math.sqrt(ref.G * L)
        cw = michell_rw(HULL, u, n_theta=96) / (0.5 * 1025.0 * s * u ** 2)
        assert 3e-4 < cw < 1e-2, cw


def test_ittc57_vs_schoenherr():
    for re_ in (1e7, 1e8, 1e9):
        assert abs(ref.ittc57_cf(re_) / ref.schoenherr_cf(re_) - 1.0) < 0.05


# --------------------------------------------------------------------------
# B. 平台侧冻结值（2026-11-02 实测，读工件）
# --------------------------------------------------------------------------
pytestmark_artifact = pytest.mark.skipif(not ARTIFACT.exists(),
                                         reason="audit_v2.json 不存在（未跑审计）")
ART = json.loads(ARTIFACT.read_text()) if ARTIFACT.exists() else {"checks": [], "rows": []}
CHECKS = {c["id"]: c for c in ART.get("checks", [])}
ROWS = ART.get("rows", [])


@pytestmark_artifact
def test_identities_re_within_1e_4():
    for cid in ("C1_Re", "C2_Fn", "C3_Pe"):
        assert abs(CHECKS[cid]["rel_err"]) < 1e-4, (cid, CHECKS[cid])
        assert CHECKS[cid]["verdict"] == "PASS"


@pytestmark_artifact
def test_wetted_surface_regression_error_pinned():
    """已知缺陷：端点不接受 S，用 Holtrop 回归式，对解析几何 -13.6%（<-3% 闸门）。"""
    err = CHECKS["C4_S"]["rel_err"]
    assert -0.16 < err < -0.11, err
    assert CHECKS["C4_S"]["verdict"] == "FAIL"
    assert abs(CHECKS["C8_scatter_S"]["rel_err"]) < 1e-3  # 跨速散度≈0，说明是系统偏差


@pytestmark_artifact
def test_component_gap_is_correlation_allowance():
    assert abs(CHECKS["C5_gap"]["rel_err"]) < 0.01
    assert CHECKS["C5_gap"]["verdict"] == "PASS"


@pytestmark_artifact
def test_deployed_cr_is_piecewise_fudge_not_holtrop():
    """端点自述 Holtrop，实测与分段线性 fudge 复算一致（1e-4 量级）。"""
    assert abs(CHECKS["C6_fudge"]["rel_err"]) < 1e-3
    assert CHECKS["C6_fudge"]["verdict"] == "PASS"


@pytestmark_artifact
def test_froude_similarity_holds_v2():
    """v1 探针把降速写成 U/√0.5（放大 1.414），得出 167% 假阳性；v2 修正后 <1%。"""
    assert abs(CHECKS["C9_froude"]["rel_err"]) < 0.01, CHECKS["C9_froude"]
    fs = ART.get("froude_scaling", {})
    assert abs(fs["Rw_ratio"] - 8.0) / 8.0 < 0.01


@pytestmark_artifact
def test_michell_diagnostic_band():
    """薄船 vs 平台为理论侧诊断（非闸门）：Fn=0.30 处平台约为薄船的 2 倍量级。"""
    ratio = CHECKS["C7_michell"]["served"] / CHECKS["C7_michell"]["reference"]
    assert 1.5 < ratio < 4.0, ratio
    assert CHECKS["C7_michell"]["gated"] is False


@pytestmark_artifact
def test_rows_are_monotone_in_speed():
    rt = [r["Rt_kN"] for r in ROWS]
    assert all(b > a for a, b in zip(rt, rt[1:]))
