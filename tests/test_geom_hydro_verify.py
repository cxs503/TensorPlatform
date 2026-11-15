"""
geometry.hydro_curves 能力验证测试套件
======================================
验收判据：
1. 箱型驳船（L=100, B=20, T=10, Cb=1.0）Cb/L/B/T 误差 <1%
2. Wigley III 船型静水力曲线积分误差 <3%
3. ≥10 例 pytest 测试全部通过
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.ship_hydrostatics import (
    barge_offsets, wigley_offsets, compute_hydrostatics, simpson_integrate
)

RHO = 1025.0


def rel_err(computed, exact):
    scale = abs(exact) if abs(exact) > 1e-15 else 1.0
    return abs(computed - exact) / scale


def box_ref(L, B, T, rho=RHO):
    nabla = L * B * T
    return {
        "volume_m3": nabla, "displacement_t": nabla * rho / 1000.0,
        "cb": 1.0, "waterplane_area_m2": L * B,
        "kb_m": T / 2.0, "bm_t_m": B**2 / (12.0 * T),
        "bm_l_m": L**2 / (12.0 * T),
        "km_t_m": T / 2.0 + B**2 / (12.0 * T),
        "wetted_surface_m2": L * (B + 2.0 * T),
    }


def wigley_ref(L, B, T, rho=RHO):
    nabla = 4.0 * B * L * T / 9.0
    return {
        "volume_m3": nabla, "displacement_t": nabla * rho / 1000.0,
        "cb": 4.0 / 9.0, "cm": 2.0 / 3.0,
        "waterplane_area_m2": 2.0 * B * L / 3.0,
        "kb_m": 5.0 * T / 8.0,
        "bm_t_m": 3.0 * B**2 / (35.0 * T),
        "bm_l_m": 3.0 * L**2 / (40.0 * T),
        "km_t_m": 5.0 * T / 8.0 + 3.0 * B**2 / (35.0 * T),
    }


# ── 案例1: 箱型驳船 L=100, B=20, D=10 ─────────────────────────────────

L1, B1, D1 = 100.0, 20.0, 10.0


class TestBoxBargeCbLBT:
    """验收判据1: 箱型驳船 Cb/L/B/T 误差 <1%"""

    @pytest.fixture
    def result(self):
        offsets = barge_offsets(length=L1, beam=B1, draft=D1, n_stations=41, n_waterlines=21)
        return compute_hydrostatics(offsets, rho=RHO)

    def test_cb_error_lt_1pct(self, result):
        ref = box_ref(L1, B1, D1, RHO)
        err = rel_err(result.cb, ref["cb"])
        assert err < 0.01, f"Cb error {err*100:.6f}% >= 1%"

    def test_length_error_lt_1pct(self, result):
        err = rel_err(result.length_m, L1)
        assert err < 0.01, f"Length error {err*100:.6f}% >= 1%"

    def test_beam_error_lt_1pct(self, result):
        err = rel_err(result.beam_m, B1)
        assert err < 0.01, f"Beam error {err*100:.6f}% >= 1%"

    def test_draft_error_lt_1pct(self, result):
        err = rel_err(result.draft_m, D1)
        assert err < 0.01, f"Draft error {err*100:.6f}% >= 1%"


class TestBoxBargeHydrostatics:
    """箱型驳船静水力参数验证"""

    @pytest.fixture
    def result(self):
        offsets = barge_offsets(length=L1, beam=B1, draft=D1, n_stations=41, n_waterlines=21)
        return compute_hydrostatics(offsets, rho=RHO)

    def test_volume_exact(self, result):
        ref = box_ref(L1, B1, D1, RHO)
        err = rel_err(result.volume_m3, ref["volume_m3"])
        assert err < 1e-9, f"Volume error {err*100:.6e}%"

    def test_kb_exact(self, result):
        ref = box_ref(L1, B1, D1, RHO)
        err = rel_err(result.kb_m, ref["kb_m"])
        assert err < 1e-9, f"KB error {err*100:.6e}%"

    def test_bm_t_exact(self, result):
        ref = box_ref(L1, B1, D1, RHO)
        err = rel_err(result.bm_t_m, ref["bm_t_m"])
        assert err < 1e-6, f"BM_T error {err*100:.6e}%"

    def test_waterplane_area_exact(self, result):
        ref = box_ref(L1, B1, D1, RHO)
        err = rel_err(result.waterplane_area_m2, ref["waterplane_area_m2"])
        assert err < 1e-9, f"Aw error {err*100:.6e}%"


# ── 案例2: Wigley III ──────────────────────────────────────────────────

L2, B2, T2 = 100.0, 20.0, 10.0


class TestWigleyIII:
    """验收判据2: Wigley III 静水力曲线积分误差 <3%"""

    @pytest.fixture
    def result(self):
        offsets = wigley_offsets(length=L2, beam=B2, draft=T2, n_stations=81, n_waterlines=41)
        return compute_hydrostatics(offsets, rho=RHO)

    def test_cb_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.cb, ref["cb"])
        assert err < 0.03, f"Cb error {err*100:.6f}% >= 3%"

    def test_volume_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.volume_m3, ref["volume_m3"])
        assert err < 0.03, f"Volume error {err*100:.6f}% >= 3%"

    def test_kb_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.kb_m, ref["kb_m"])
        assert err < 0.03, f"KB error {err*100:.6f}% >= 3%"

    def test_bm_t_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.bm_t_m, ref["bm_t_m"])
        assert err < 0.03, f"BM_T error {err*100:.6f}% >= 3%"

    def test_bm_l_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.bm_l_m, ref["bm_l_m"])
        assert err < 0.03, f"BM_L error {err*100:.6f}% >= 3%"

    def test_waterplane_area_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.waterplane_area_m2, ref["waterplane_area_m2"])
        assert err < 0.03, f"Aw error {err*100:.6f}% >= 3%"

    def test_cm_error_lt_3pct(self, result):
        ref = wigley_ref(L2, B2, T2, RHO)
        err = rel_err(result.cm, ref["cm"])
        assert err < 0.03, f"Cm error {err*100:.6f}% >= 3%"


# ── 案例3: 多吃水扫描验证 ──────────────────────────────────────────────

class TestMultiDraftScan:
    """多吃水扫描验证：静水力曲线单调性"""

    def test_box_volume_increases_with_draft(self):
        """箱型驳船排水体积随吃水单调递增"""
        volumes = []
        for T in [1.0, 3.0, 5.0, 7.0, 10.0]:
            offsets = barge_offsets(length=L1, beam=B1, draft=T, n_stations=41, n_waterlines=21)
            r = compute_hydrostatics(offsets, rho=RHO)
            volumes.append(r.volume_m3)
        for i in range(len(volumes) - 1):
            assert volumes[i] < volumes[i + 1], f"Volume not monotonic at T={i}"

    def test_box_kb_increases_with_draft(self):
        """箱型驳船 KB 随吃水单调递增"""
        kbs = []
        for T in [1.0, 3.0, 5.0, 7.0, 10.0]:
            offsets = barge_offsets(length=L1, beam=B1, draft=T, n_stations=41, n_waterlines=21)
            r = compute_hydrostatics(offsets, rho=RHO)
            kbs.append(r.kb_m)
        for i in range(len(kbs) - 1):
            assert kbs[i] < kbs[i + 1], f"KB not monotonic at T={i}"

    def test_wigley_cb_constant(self):
        """Wigley 船型 Cb 不随吃水变化（几何相似）"""
        cbs = []
        for T in [2.0, 4.0, 6.0, 8.0, 10.0]:
            offsets = wigley_offsets(length=L2, beam=B2, draft=T, n_stations=81, n_waterlines=41)
            r = compute_hydrostatics(offsets, rho=RHO)
            cbs.append(r.cb)
        for cb in cbs:
            assert abs(cb - 4.0 / 9.0) < 0.001, f"Cb={cb} deviates from 4/9"


# ── 案例4: Simpson 积分器验证 ──────────────────────────────────────────

class TestSimpsonIntegrator:
    """Simpson 积分器单元测试"""

    def test_simpson_constant(self):
        """常数函数积分"""
        y = [5.0] * 21
        x = [i * 0.5 for i in range(21)]
        val, method = simpson_integrate(y, x)
        expected = 5.0 * (x[-1] - x[0])
        assert abs(val - expected) < 1e-12, f"Constant integral error: {val} vs {expected}"
        assert method == "simpson"

    def test_simpson_linear(self):
        """线性函数积分"""
        x = [i * 0.1 for i in range(21)]
        y = [2.0 * xi + 1.0 for xi in x]
        val, method = simpson_integrate(y, x)
        expected = (y[0] + y[-1]) / 2.0 * (x[-1] - x[0])
        assert abs(val - expected) < 1e-12, f"Linear integral error: {val} vs {expected}"

    def test_simpson_quadratic(self):
        """二次函数积分（Simpson 精确）"""
        x = [i * 0.1 for i in range(21)]
        y = [xi ** 2 for xi in x]
        val, method = simpson_integrate(y, x)
        expected = (x[-1] ** 3 - x[0] ** 3) / 3.0
        assert abs(val - expected) < 1e-12, f"Quadratic integral error: {val} vs {expected}"

    def test_trapezoid_fallback(self):
        """非均匀网格回退到梯形积分"""
        x = [0.0, 0.5, 1.2, 2.0]
        y = [0.0, 0.5, 1.2, 2.0]
        val, method = simpson_integrate(y, x)
        assert method == "trapezoid"
