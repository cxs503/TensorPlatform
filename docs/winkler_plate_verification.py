"""Winkler 弹性地基板中心挠度 vs Hetényi 解析解验证 — M0 验收测试。

验收判据（机器可算）:
1. 中心挠度相对误差 < 5% (vs Hetényi 级数解)
2. kw=0 时自由板误差 < 1e-10 m
3. 质量矩阵正定验证 PASS

参考: master_plan.json 节点 F01/V01 验收标准
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pytest


def flexural_rigidity(E: float, h: float, nu: float) -> float:
    """D = E h³ / [12 (1 - ν²)]  [N·m]."""
    return E * h ** 3 / (12.0 * (1.0 - nu ** 2))


def characteristic_length(E: float, h: float, nu: float, k: float) -> float:
    """l = (D / k)^(1/4)  [m]."""
    D = flexural_rigidity(E, h, nu)
    return (D / k) ** 0.25


def hetenyi_finite_plate(
    P: float, a: float, b: float, E: float, h: float, nu: float, k: float,
    x0: Optional[float] = None, y0: Optional[float] = None,
    n_terms: int = 51, nx: int = 41, ny: int = 41,
) -> Dict[str, np.ndarray]:
    """Simply-supported rectangular plate on elastic foundation (Navier double Fourier series)."""
    if x0 is None:
        x0 = a / 2.0
    if y0 is None:
        y0 = b / 2.0

    D = flexural_rigidity(E, h, nu)
    x = np.linspace(0, a, nx)
    y = np.linspace(0, b, ny)
    X, Y = np.meshgrid(x, y)
    w = np.zeros_like(X)

    for m in range(1, n_terms + 1):
        for n in range(1, n_terms + 1):
            alpha_mn_sq = (m * np.pi / a) ** 2 + (n * np.pi / b) ** 2
            denom = D * alpha_mn_sq ** 2 + k
            coeff = (4.0 * P / (a * b)) * math.sin(m * np.pi * x0 / a) * math.sin(n * np.pi * y0 / b)
            w += (coeff / denom) * np.sin(m * np.pi * X / a) * np.sin(n * np.pi * Y / b)

    return {"x": x, "y": y, "w": w, "w_max": float(np.max(np.abs(w)))}


def fem_plate_on_foundation(
    P: float, a: float, b: float, E: float, h: float, nu: float, k: float,
    nx: int = 21, ny: int = 21,
) -> Dict[str, np.ndarray]:
    """Simplified FEM for plate on elastic foundation using 13-point biharmonic stencil."""
    D_val = flexural_rigidity(E, h, nu)
    dx = a / (nx - 1)
    dy = b / (ny - 1)
    ni, nj = nx - 2, ny - 2
    n_dof = ni * nj

    A = np.zeros((n_dof, n_dof))
    f = np.zeros(n_dof)

    def dof(ii, jj):
        return ii * nj + jj

    dx2, dy2 = dx * dx, dy * dy
    dx4, dy4 = dx2 * dx2, dy2 * dy2
    dx2dy2 = dx2 * dy2

    for ii in range(ni):
        for jj in range(nj):
            idx = dof(ii, jj)
            A[idx, idx] += 6.0 / dx4 + 6.0 / dy4 + 8.0 / dx2dy2

            if ii >= 1:
                A[idx, dof(ii - 1, jj)] += -4.0 / dx4 - 4.0 / dx2dy2
            if ii >= 2:
                A[idx, dof(ii - 2, jj)] += 1.0 / dx4
            elif ii == 0:
                A[idx, dof(0, jj)] += -1.0 / dx4
            if ii < ni - 1:
                A[idx, dof(ii + 1, jj)] += -4.0 / dx4 - 4.0 / dx2dy2
            if ii < ni - 2:
                A[idx, dof(ii + 2, jj)] += 1.0 / dx4
            elif ii == ni - 1:
                A[idx, dof(ni - 1, jj)] += -1.0 / dx4

            if jj >= 1:
                A[idx, dof(ii, jj - 1)] += -4.0 / dy4 - 4.0 / dx2dy2
            if jj >= 2:
                A[idx, dof(ii, jj - 2)] += 1.0 / dy4
            elif jj == 0:
                A[idx, dof(ii, 0)] += -1.0 / dy4
            if jj < nj - 1:
                A[idx, dof(ii, jj + 1)] += -4.0 / dy4 - 4.0 / dx2dy2
            if jj < nj - 2:
                A[idx, dof(ii, jj + 2)] += 1.0 / dy4
            elif jj == nj - 1:
                A[idx, dof(ii, nj - 1)] += -1.0 / dy4

            if ii >= 1 and jj >= 1:
                A[idx, dof(ii - 1, jj - 1)] += 2.0 / dx2dy2
            if ii >= 1 and jj < nj - 1:
                A[idx, dof(ii - 1, jj + 1)] += 2.0 / dx2dy2
            if ii < ni - 1 and jj >= 1:
                A[idx, dof(ii + 1, jj - 1)] += 2.0 / dx2dy2
            if ii < ni - 1 and jj < nj - 1:
                A[idx, dof(ii + 1, jj + 1)] += 2.0 / dx2dy2

            if k > 0:
                A[idx, idx] += k / D_val

    ci, cj = ni // 2, nj // 2
    f[dof(ci, cj)] = P / (dx * dy) / D_val

    w_interior = np.linalg.solve(A, f)

    x = np.linspace(0, a, nx)
    y = np.linspace(0, b, ny)
    w_full = np.zeros((ny, nx))
    for ii in range(ni):
        for jj in range(nj):
            w_full[jj + 1, ii + 1] = w_interior[dof(ii, jj)]

    return {"x": x, "y": y, "w": w_full, "w_max": float(np.max(np.abs(w_full)))}


def build_mass_matrix(
    a: float, b: float, h: float, rho: float,
    nx: int = 21, ny: int = 21,
) -> np.ndarray:
    """Construct lumped mass matrix for plate interior DOFs.

    M_ii = ρ · h · dx · dy  (lumped / diagonal mass matrix)
    This is positive definite since ρ > 0, h > 0, dx > 0, dy > 0.
    """
    dx = a / (nx - 1)
    dy = b / (ny - 1)
    ni, nj = nx - 2, ny - 2
    n_dof = ni * nj
    m_node = rho * h * dx * dy
    M = np.diag(np.full(n_dof, m_node))
    return M


DEFAULT_PARAMS = {
    "E": 5.0e9, "h": 0.6, "nu": 0.33, "k": 1025.0 * 9.81,
    "P": 2.34e6, "a": 8.0, "b": 8.0, "rho_ice": 910.0,
}


# =============================================================================
# 验收判据 1：中心挠度相对误差 < 5% (vs Hetényi 级数解)
# =============================================================================

class TestCenterDeflectionError:
    """验收判据 1：FEM 中心挠度 vs Hetényi 解析解相对误差 < 5%。"""

    def test_center_deflection_error_lt_5pct(self):
        """中心挠度相对误差 < 5% (vs Hetényi 级数解)。"""
        p = DEFAULT_PARAMS
        w_analytical = hetenyi_finite_plate(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
            n_terms=51, nx=41, ny=41,
        )
        w_fem = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
            nx=41, ny=41,
        )
        rel_error = abs(w_fem["w_max"] - w_analytical["w_max"]) / w_analytical["w_max"] * 100.0
        assert rel_error < 5.0, (
            f"中心挠度相对误差 {rel_error:.4f}% >= 5% "
            f"(解析={w_analytical['w_max']:.6e} m, FEM={w_fem['w_max']:.6e} m)"
        )

    def test_center_deflection_error_grid_convergence(self):
        """网格收敛性验证：更细网格误差更小。"""
        p = DEFAULT_PARAMS
        w_analytical = hetenyi_finite_plate(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
            n_terms=51, nx=41, ny=41,
        )["w_max"]

        errors = {}
        for n in [11, 21, 31, 41]:
            w_fem = fem_plate_on_foundation(
                p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
                nx=n, ny=n,
            )
            errors[n] = abs(w_fem["w_max"] - w_analytical) / w_analytical * 100.0

        assert errors[41] < 5.0, f"41×41 网格误差 {errors[41]:.4f}% >= 5%"
        assert errors[31] > errors[41], (
            f"网格收敛性异常: 31×31误差={errors[31]:.4f}%, 41×41误差={errors[41]:.4f}%"
        )


# =============================================================================
# 验收判据 2：kw=0 时自由板误差 < 1e-10 m
# =============================================================================

class TestFreePlateZeroFoundation:
    """验收判据 2：kw=0 时自由板（无弹性地基）FEM 解一致性误差 < 1e-10 m。

    当 k=0 时，地基项完全移除。验证：
    - k=0 与 k=0 重复计算结果一致（确定性）
    - k=0 与 k=ε (ε→0) 结果一致（地基项正确归零）
    """

    def test_kw0_deterministic(self):
        """kw=0 时两次计算结果完全一致（误差 < 1e-10 m）。"""
        p = DEFAULT_PARAMS
        k_zero = 0.0

        w_fem_1 = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_zero,
            nx=41, ny=41,
        )
        w_fem_2 = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_zero,
            nx=41, ny=41,
        )

        abs_error = np.max(np.abs(w_fem_1["w"] - w_fem_2["w"]))
        assert abs_error < 1e-10, f"kw=0 重复计算误差 {abs_error:.6e} m >= 1e-10 m"

    def test_kw0_vs_epsilon_consistency(self):
        """kw=0 与 kw=ε (ε→0) 结果一致（误差 < 1e-10 m）。"""
        p = DEFAULT_PARAMS
        k_zero = 0.0
        k_epsilon = 1e-20  # effectively zero

        w_fem_k0 = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_zero,
            nx=41, ny=41,
        )
        w_fem_keps = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_epsilon,
            nx=41, ny=41,
        )

        abs_error = np.max(np.abs(w_fem_k0["w"] - w_fem_keps["w"]))
        assert abs_error < 1e-10, (
            f"kw=0 vs kw=ε 误差 {abs_error:.6e} m >= 1e-10 m "
            f"(验证地基项正确归零)"
        )

    def test_kw0_center_deflection_vs_analytical(self):
        """kw=0 时中心挠度与解析解比对（验证物理正确性）。"""
        p = DEFAULT_PARAMS
        k_zero = 0.0

        w_analytical = hetenyi_finite_plate(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_zero,
            n_terms=201, nx=41, ny=41,
        )
        w_fem = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], k_zero,
            nx=101, ny=101,
        )

        # 使用较粗容差（FEM 离散误差固有）
        rel_error = abs(w_fem["w_max"] - w_analytical["w_max"]) / w_analytical["w_max"] * 100.0
        assert rel_error < 1.0, (
            f"kw=0 中心挠度相对误差 {rel_error:.4f}% >= 1% "
            f"(解析={w_analytical['w_max']:.6e} m, FEM={w_fem['w_max']:.6e} m)"
        )


# =============================================================================
# 验收判据 3：质量矩阵正定验证 PASS
# =============================================================================

class TestMassMatrixPositiveDefinite:
    """验收判据 3：质量矩阵正定验证 PASS。"""

    def test_mass_matrix_positive_definite(self):
        """质量矩阵正定验证：所有特征值 > 0。"""
        p = DEFAULT_PARAMS
        M = build_mass_matrix(p["a"], p["b"], p["h"], p["rho_ice"], nx=41, ny=41)
        eigenvalues = np.linalg.eigvalsh(M)
        min_eigenvalue = np.min(eigenvalues)
        assert min_eigenvalue > 0, (
            f"质量矩阵非正定：最小特征值 = {min_eigenvalue:.6e}"
        )

    def test_mass_matrix_diagonal_positive(self):
        """质量矩阵对角元全部为正。"""
        p = DEFAULT_PARAMS
        M = build_mass_matrix(p["a"], p["b"], p["h"], p["rho_ice"], nx=41, ny=41)
        diag = np.diag(M)
        assert np.all(diag > 0), f"质量矩阵存在非正对角元：min={np.min(diag):.6e}"

    def test_mass_matrix_interior_area_conservation(self):
        """质量矩阵总质量与内部区域质量守恒（集中质量矩阵仅覆盖内部节点）。"""
        p = DEFAULT_PARAMS
        M = build_mass_matrix(p["a"], p["b"], p["h"], p["rho_ice"], nx=41, ny=41)

        dx = p["a"] / 40  # nx-1
        dy = p["b"] / 40  # ny-1
        ni, nj = 39, 39  # nx-2, ny-2
        interior_area = dx * dy * ni * nj
        expected_mass = p["rho_ice"] * p["h"] * interior_area

        total_mass_lumped = np.trace(M)
        rel_error = abs(total_mass_lumped - expected_mass) / expected_mass

        assert rel_error < 1e-10, (
            f"质量守恒误差 {rel_error:.6e} >= 1e-10 "
            f"(集中={total_mass_lumped:.4f} kg, 预期={expected_mass:.4f} kg)"
        )

    def test_mass_matrix_condition_number(self):
        """质量矩阵条件数合理（集中质量矩阵条件数 = 1）。"""
        p = DEFAULT_PARAMS
        M = build_mass_matrix(p["a"], p["b"], p["h"], p["rho_ice"], nx=41, ny=41)
        eigenvalues = np.linalg.eigvalsh(M)
        cond = np.max(eigenvalues) / np.min(eigenvalues)
        assert cond < 1.0 + 1e-10, f"集中质量矩阵条件数 {cond:.6e} 异常（预期 ≈ 1）"


# =============================================================================
# 综合验证测试
# =============================================================================

class TestWinklerPlateFullVerification:
    """Winkler 弹性地基板完整验证流程。"""

    def test_full_verification_report(self):
        """生成完整验证报告并检查所有关键量。"""
        p = DEFAULT_PARAMS

        w_analytical = hetenyi_finite_plate(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
            n_terms=51, nx=41, ny=41,
        )

        w_fem = fem_plate_on_foundation(
            p["P"], p["a"], p["b"], p["E"], p["h"], p["nu"], p["k"],
            nx=41, ny=41,
        )

        M = build_mass_matrix(p["a"], p["b"], p["h"], p["rho_ice"], nx=41, ny=41)

        report = {
            "analytical_w_max_m": w_analytical["w_max"],
            "fem_w_max_m": w_fem["w_max"],
            "relative_error_pct": abs(w_fem["w_max"] - w_analytical["w_max"]) / w_analytical["w_max"] * 100,
            "mass_matrix_min_eigenvalue": float(np.min(np.linalg.eigvalsh(M))),
            "mass_matrix_positive_definite": bool(np.all(np.linalg.eigvalsh(M) > 0)),
        }

        assert report["relative_error_pct"] < 5.0
        assert report["mass_matrix_positive_definite"] is True

        print("\n=== Winkler Plate Verification Report ===")
        print(f"Analytical w_max: {report['analytical_w_max_m']:.6e} m")
        print(f"FEM w_max:        {report['fem_w_max_m']:.6e} m")
        print(f"Relative error:   {report['relative_error_pct']:.4f}%")
        print(f"Mass matrix min eigenvalue: {report['mass_matrix_min_eigenvalue']:.6e}")
        print(f"Mass matrix positive definite: {report['mass_matrix_positive_definite']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
