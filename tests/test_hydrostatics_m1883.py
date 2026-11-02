"""Regression tests for ship hydrostatics verification (m1883).

Run:
  cd /root/tp-agent/work/m1883 && PYTHONPATH=backend python3 -m pytest tests/test_hydrostatics_m1883.py -v
"""
from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from services.ship_hydrostatics import (
    compute_hydrostatics,
    wigley_offsets,
    barge_offsets,
)


def box_barge_exact(L, B, T, KG=None):
    vol = B * L * T
    return {
        "volume_m3": vol, "cb": 1.0, "cm": 1.0, "cp": 1.0, "cw": 1.0,
        "waterplane_area_m2": B * L,
        "kb_m": T / 2.0,
        "bm_t_m": B * B / (12.0 * T),
        "bm_l_m": L * L / (12.0 * T),
        "km_t_m": T / 2.0 + B * B / (12.0 * T),
        "gm_m": T / 2.0 + B * B / (12.0 * T) - KG if KG else None,
        "wetted_surface_m2": L * (B + 2.0 * T),
        "lcb_m": 0.0, "lcf_m": 0.0,
    }


def wigley_exact(L, B, T, KG=None):
    vol = 4.0 * B * L * T / 9.0
    kb = 5.0 * T / 8.0
    bm_t = 3.0 * B * B / (35.0 * T)
    bm_l = 3.0 * L * L / (40.0 * T)
    return {
        "volume_m3": vol, "cb": 4.0 / 9.0, "cm": 2.0 / 3.0, "cp": 2.0 / 3.0, "cw": 2.0 / 3.0,
        "waterplane_area_m2": 2.0 * B * L / 3.0,
        "kb_m": kb, "bm_t_m": bm_t, "bm_l_m": bm_l,
        "km_t_m": kb + bm_t,
        "gm_m": kb + bm_t - KG if KG else None,
        "lcb_m": 0.0, "lcf_m": 0.0,
    }


class TestBoxBarge:
    L, B, T, KG = 100.0, 20.0, 8.0, 6.0

    @pytest.fixture(autouse=True)
    def setup(self):
        hull = barge_offsets(length=self.L, beam=self.B, draft=self.T,
                             n_stations=21, n_waterlines=11)
        self.res = compute_hydrostatics(hull, kg=self.KG)
        self.exact = box_barge_exact(self.L, self.B, self.T, KG=self.KG)

    @pytest.mark.parametrize("field", [
        "volume_m3", "cb", "cm", "cp", "cw", "waterplane_area_m2",
        "kb_m", "bm_t_m", "bm_l_m", "km_t_m", "gm_m", "lcb_m", "lcf_m",
    ])
    def test_field(self, field):
        computed = getattr(self.res, field)
        ref = self.exact[field]
        if ref is None:
            return
        if abs(ref) > 1e-12:
            assert abs(computed - ref) / abs(ref) < 1e-3, f"{field}: {computed} vs {ref}"
        else:
            assert abs(computed - ref) < 1e-9

    def test_wetted_surface(self):
        ref = self.exact["wetted_surface_m2"]
        err = abs(self.res.wetted_surface_m2 - ref) / ref
        assert err < 0.01, f"wetted surface error {err:.4f} > 1%"


class TestWigleyIII:
    L, B, T, KG = 100.0, 15.0, 6.0, 4.0

    @pytest.fixture(autouse=True)
    def setup(self):
        hull = wigley_offsets(length=self.L, beam=self.B, draft=self.T,
                              n_stations=41, n_waterlines=21)
        self.res = compute_hydrostatics(hull, kg=self.KG)
        self.exact = wigley_exact(self.L, self.B, self.T, KG=self.KG)

    @pytest.mark.parametrize("field", [
        "volume_m3", "cb", "cm", "cp", "cw", "waterplane_area_m2",
        "kb_m", "bm_t_m", "bm_l_m", "km_t_m", "gm_m", "lcb_m", "lcf_m",
    ])
    def test_field(self, field):
        computed = getattr(self.res, field)
        ref = self.exact[field]
        if ref is None:
            return
        if abs(ref) > 1e-12:
            assert abs(computed - ref) / abs(ref) < 1e-3, f"{field}: {computed} vs {ref}"
        else:
            assert abs(computed - ref) < 1e-9


class TestGridConvergence:
    L, B, T, KG = 100.0, 15.0, 6.0, 4.0

    def test_volume_converges(self):
        vol_ref = 4.0 * self.B * self.L * self.T / 9.0
        errors = []
        for ns in [11, 21, 41, 81, 161]:
            nw = ns // 2 + 1
            hull = wigley_offsets(length=self.L, beam=self.B, draft=self.T,
                                  n_stations=ns, n_waterlines=nw)
            res = compute_hydrostatics(hull, kg=self.KG)
            err = abs(res.volume_m3 - vol_ref) / vol_ref
            errors.append(err)
        for i in range(len(errors) - 1):
            assert errors[i] >= errors[i + 1], \
                f"Volume error not monotonic: {errors[i]:.2e} -> {errors[i+1]:.2e}"

    def test_kb_converges(self):
        kb_ref = 5.0 * self.T / 8.0
        errors = []
        for ns in [11, 21, 41, 81, 161]:
            nw = ns // 2 + 1
            hull = wigley_offsets(length=self.L, beam=self.B, draft=self.T,
                                  n_stations=ns, n_waterlines=nw)
            res = compute_hydrostatics(hull, kg=self.KG)
            err = abs(res.kb_m - kb_ref) / kb_ref
            errors.append(err)
        for i in range(len(errors) - 1):
            assert errors[i] >= errors[i + 1], \
                f"KB error not monotonic: {errors[i]:.2e} -> {errors[i+1]:.2e}"

    def test_fine_grid_accuracy(self):
        hull = wigley_offsets(length=self.L, beam=self.B, draft=self.T,
                              n_stations=161, n_waterlines=81)
        res = compute_hydrostatics(hull, kg=self.KG)
        exact = wigley_exact(self.L, self.B, self.T, KG=self.KG)
        for field in ["volume_m3", "cb", "kb_m", "bm_t_m", "bm_l_m"]:
            computed = getattr(res, field)
            ref = exact[field]
            err = abs(computed - ref) / abs(ref) if abs(ref) > 1e-12 else abs(computed - ref)
            assert err < 1e-4, f"{field} at 161 stations: error {err:.2e} > 0.01%"
