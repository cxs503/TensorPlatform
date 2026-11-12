"""Verification tests for geometry.hydro_curves capability.

Tests the ``POST /calculations/hydro_curves`` endpoint against closed-form
textbook formulas for a rectangular barge (box hull, Cb = 1).

Acceptance criteria (per mission m16985):
  C1  displacement error < 1%  (all drafts in scan)
  C2  KB error < 1%
  C3  BM_T error < 2%
  C4  >= 10 pytest items all green
  C5  error table with explicit <3% verdict
  C6  zero hand-copy: all numbers programmatically injected
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

_CALC_PATH = BACKEND_DIR / "api" / "calculations.py"
_spec = importlib.util.spec_from_file_location("calculations_hc", _CALC_PATH)
calcs = importlib.util.module_from_spec(_spec)
sys.modules["calculations_hc"] = calcs
_spec.loader.exec_module(calcs)


def box_ref(length, beam, draft, rho=1025.0):
    nabla = length * beam * draft
    kb = draft / 2.0
    it = length * beam ** 3 / 12.0
    il = length ** 3 * beam / 12.0
    bm_t = it / nabla
    bm_l = il / nabla
    return {
        "volume_m3": nabla, "displacement_t": nabla * rho / 1000.0,
        "cb": 1.0, "waterplane_area_m2": length * beam,
        "kb_m": kb, "bm_t_m": bm_t, "bm_l_m": bm_l, "km_t_m": kb + bm_t,
    }


def rel_err(computed, exact):
    scale = abs(exact) if abs(exact) > 1e-15 else 1.0
    return abs(computed - exact) / scale


L, B, D = 80.0, 20.0, 12.0
T_MIN, T_MAX, T_STEP = 2.0, 10.0, 0.5
RHO = 1025.0


@pytest.fixture(scope="module")
def hc_response():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(calcs.router, prefix="/api/v1")
    client = TestClient(app)
    r = client.post("/api/v1/calculations/hydro_curves", json={
        "hull": "box", "length": L, "beam": B, "depth": D,
        "draft_min": T_MIN, "draft_max": T_MAX, "draft_step": T_STEP, "rho": RHO,
    })
    assert r.status_code == 200, r.text
    return r.json()


class TestC1_Displacement:
    @pytest.mark.parametrize("draft", [
        pytest.param(T_MIN + i * T_STEP, id=f"T={T_MIN + i * T_STEP:.1f}")
        for i in range(int((T_MAX - T_MIN) / T_STEP) + 1)
    ])
    def test_displacement_error_lt_1pct(self, hc_response, draft):
        draft = round(draft, 6)
        point = next(p for p in hc_response["curves"] if abs(p["draft_m"] - draft) < 1e-6)
        ref = box_ref(L, B, draft, RHO)
        err = rel_err(point["volume_m3"], ref["volume_m3"])
        assert err < 0.01, f"T={draft}: vol err={err:.4e}"

    def test_displacement_t_error_lt_1pct(self, hc_response):
        for p in hc_response["curves"]:
            ref = box_ref(L, B, p["draft_m"], RHO)
            err = rel_err(p["displacement_t"], ref["displacement_t"])
            assert err < 0.01


class TestC2_KB:
    @pytest.mark.parametrize("draft", [
        pytest.param(T_MIN + i * T_STEP, id=f"T={T_MIN + i * T_STEP:.1f}")
        for i in range(int((T_MAX - T_MIN) / T_STEP) + 1)
    ])
    def test_kb_error_lt_1pct(self, hc_response, draft):
        draft = round(draft, 6)
        point = next(p for p in hc_response["curves"] if abs(p["draft_m"] - draft) < 1e-6)
        ref = box_ref(L, B, draft, RHO)
        err = rel_err(point["kb_m"], ref["kb_m"])
        assert err < 0.01


class TestC3_BM:
    @pytest.mark.parametrize("draft", [
        pytest.param(T_MIN + i * T_STEP, id=f"T={T_MIN + i * T_STEP:.1f}")
        for i in range(int((T_MAX - T_MIN) / T_STEP) + 1)
    ])
    def test_bm_t_error_lt_2pct(self, hc_response, draft):
        draft = round(draft, 6)
        point = next(p for p in hc_response["curves"] if abs(p["draft_m"] - draft) < 1e-6)
        ref = box_ref(L, B, draft, RHO)
        err = rel_err(point["bm_t_m"], ref["bm_t_m"])
        assert err < 0.02

    def test_bm_l_error_lt_2pct(self, hc_response):
        for p in hc_response["curves"]:
            ref = box_ref(L, B, p["draft_m"], RHO)
            err = rel_err(p["bm_l_m"], ref["bm_l_m"])
            assert err < 0.02


class TestC4_TestCount:
    def test_n_points_matches_expected(self, hc_response):
        expected = int((T_MAX - T_MIN) / T_STEP) + 1
        assert hc_response["n_points"] == expected

    def test_cb_is_exactly_one(self, hc_response):
        for p in hc_response["curves"]:
            assert p["cb"] == pytest.approx(1.0, rel=1e-9)

    def test_waterplane_area_constant(self, hc_response):
        expected_aw = L * B
        for p in hc_response["curves"]:
            err = rel_err(p["waterplane_area_m2"], expected_aw)
            assert err < 0.01

    def test_km_consistency(self, hc_response):
        for p in hc_response["curves"]:
            expected_km = p["kb_m"] + p["bm_t_m"]
            err = rel_err(p["km_t_m"], expected_km)
            assert err < 1e-9

    def test_wetted_surface_box(self, hc_response):
        for p in hc_response["curves"]:
            T = p["draft_m"]
            expected_ws = L * (B + 2.0 * T)
            err = rel_err(p["wetted_surface_m2"], expected_ws)
            assert err < 0.01


class TestC5_ErrorTable:
    def test_error_table_all_under_3pct(self, hc_response):
        for p in hc_response["curves"]:
            T = p["draft_m"]
            ref = box_ref(L, B, T, RHO)
            for key, computed in [("volume_m3", p["volume_m3"]), ("kb_m", p["kb_m"]),
                                   ("bm_t_m", p["bm_t_m"]), ("bm_l_m", p["bm_l_m"]),
                                   ("waterplane_area_m2", p["waterplane_area_m2"])]:
                err = rel_err(computed, ref[key]) * 100
                assert err < 3.0, f"T={T}: {key} err={err:.4f}% >= 3%"

    def test_error_table_printable(self, hc_response):
        print("\n=== Hydro Curves Error Table (box barge L=80 B=20 D=12) ===")
        print(f"{'Draft':>6} {'Vol':>10} {'Vol_ref':>10} {'Vol_err%':>10} "
              f"{'KB':>8} {'KB_ref':>8} {'KB_err%':>10} "
              f"{'BM_T':>10} {'BM_T_ref':>10} {'BM_T_err%':>12} {'<3%?':>6}")
        all_pass = True
        for p in hc_response["curves"]:
            T = p["draft_m"]
            ref = box_ref(L, B, T, RHO)
            ve = rel_err(p["volume_m3"], ref["volume_m3"]) * 100
            ke = rel_err(p["kb_m"], ref["kb_m"]) * 100
            be = rel_err(p["bm_t_m"], ref["bm_t_m"]) * 100
            verdict = "PASS" if (ve < 3 and ke < 3 and be < 3) else "FAIL"
            if verdict == "FAIL":
                all_pass = False
            print(f"{T:>6.1f} {p['volume_m3']:>10.2f} {ref['volume_m3']:>10.2f} {ve:>10.6f} "
                  f"{p['kb_m']:>8.4f} {ref['kb_m']:>8.4f} {ke:>10.6f} "
                  f"{p['bm_t_m']:>10.4f} {ref['bm_t_m']:>10.4f} {be:>12.6f} {verdict:>6}")
        print(f"\nAll points pass <3%: {'YES' if all_pass else 'NO'}")
        assert all_pass


class TestC6_InputValidation:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(calcs.router, prefix="/api/v1")
        return TestClient(app)

    def test_draft_max_exceeds_depth(self, client):
        r = client.post("/api/v1/calculations/hydro_curves", json={
            "hull": "box", "length": L, "beam": B, "depth": D,
            "draft_min": 2.0, "draft_max": D + 1.0, "draft_step": 0.5,
        })
        assert r.status_code == 400

    def test_draft_max_lt_min(self, client):
        r = client.post("/api/v1/calculations/hydro_curves", json={
            "hull": "box", "length": L, "beam": B, "depth": D,
            "draft_min": 10.0, "draft_max": 2.0, "draft_step": 0.5,
        })
        assert r.status_code == 400

    def test_unknown_hull_type(self, client):
        r = client.post("/api/v1/calculations/hydro_curves", json={
            "hull": "wigley", "length": L, "beam": B, "depth": D,
            "draft_min": 2.0, "draft_max": 10.0, "draft_step": 0.5,
        })
        assert r.status_code == 400

    def test_with_kg_produces_gm(self, client):
        r = client.post("/api/v1/calculations/hydro_curves", json={
            "hull": "box", "length": L, "beam": B, "depth": D,
            "draft_min": 2.0, "draft_max": 4.0, "draft_step": 1.0, "kg": 5.0,
        })
        assert r.status_code == 200
        data = r.json()
        for p in data["curves"]:
            assert p["gm_m"] is not None
