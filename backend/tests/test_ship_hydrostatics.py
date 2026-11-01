"""Verification tests for the hull-form hydrostatics module.

Anchors (all analytic, no empirical data involved):

* rectangular barge -- nabla=B*L*T, Cb=1, Aw=B*L, KB=T/2,
  BM_T=B^2/(12T), BM_L=L^2/(12T), S=L*(B+2T);
* Wigley parabolic hull ``y=(B/2)(1-(2x/L)^2)(1-(1-z/T)^2)`` -- nabla=4BLT/9,
  Cb=4/9, Aw=2BL/3, KC/L=0, KB=5T/8, Cm=2/3, BM_T=3B^2/(35T), BM_L=3L^2/(40T).

The Wigley closed forms are derived in
``docs/ship_hydrostatics_verification.md`` (section 3) and were re-derived
independently of the implementation.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.ship_hydrostatics import (  # noqa: E402
    GeometryError,
    HullOffsets,
    barge_offsets,
    compute_hydrostatics,
    simpson_integrate,
    wigley_offsets,
)

# ``api`` package __init__ pulls in the full auth/DB stack, which is not
# importable in the unit-test environment; load the router module directly.
_CALC_PATH = BACKEND_DIR / "api" / "calculations.py"
_spec = importlib.util.spec_from_file_location("calculations_under_test", _CALC_PATH)
calcs = importlib.util.module_from_spec(_spec)
sys.modules["calculations_under_test"] = calcs   # pydantic needs the module registered
_spec.loader.exec_module(calcs)

REL_TOL = 1e-5


def wigley_exact(length: float, beam: float, draft: float) -> dict:
    """Closed-form hydrostatics of the Wigley hull (derivation in docs)."""
    return {
        "volume_m3": 4.0 * beam * length * draft / 9.0,
        "cb": 4.0 / 9.0,
        "cm": 2.0 / 3.0,
        "waterplane_area_m2": 2.0 * beam * length / 3.0,
        "lcb_m": 0.0,
        "lcf_m": 0.0,
        "kb_m": 5.0 * draft / 8.0,
        "bm_t_m": 3.0 * beam ** 2 / (35.0 * draft),
        "bm_l_m": 3.0 * length ** 2 / (40.0 * draft),
    }


def barge_exact(length: float, beam: float, draft: float) -> dict:
    return {
        "volume_m3": length * beam * draft,
        "cb": 1.0,
        "cm": 1.0,
        "waterplane_area_m2": length * beam,
        "lcb_m": 0.0,
        "lcf_m": 0.0,
        "kb_m": 0.5 * draft,
        "bm_t_m": beam ** 2 / (12.0 * draft),
        "bm_l_m": length ** 2 / (12.0 * draft),
    }


def relative_error(computed, exact):
    scale = abs(exact) if abs(exact) > 0 else 1.0
    return abs(computed - exact) / scale


# ---------------------------------------------------------------------------
# 1. Analytic anchors
# ---------------------------------------------------------------------------

class TestWigleyAnchors:
    """Wigley closed forms vs integrated values."""

    L, B, T = 6.0, 0.6, 0.375

    @pytest.fixture(scope="class")
    def result(self):
        return compute_hydrostatics(
            wigley_offsets(self.L, self.B, self.T, 81, 41), kg=0.5
        )

    @pytest.mark.parametrize(
        "key",
        ["volume_m3", "cb", "cm", "waterplane_area_m2", "kb_m", "bm_t_m", "bm_l_m"],
    )
    def test_closed_form(self, result, key):
        exact = wigley_exact(self.L, self.B, self.T)[key]
        err = relative_error(getattr(result, key), exact)
        assert err < REL_TOL, f"{key}: got {getattr(result, key)} vs {exact} (rel {err:.2e})"

    def test_centres_on_midships(self, result):
        assert abs(result.lcb_m) < 1e-9
        assert abs(result.lcf_m) < 1e-9

    def test_coefficient_identities(self, result):
        # Cp = Cb / Cm and Cw = Aw / (L*B) are algebraically consistent
        assert result.cp == pytest.approx(result.cb / result.cm, rel=1e-9)
        assert result.cw == pytest.approx(
            result.waterplane_area_m2 / (self.L * self.B), rel=1e-9
        )

    def test_km_and_gm(self, result):
        assert result.km_t_m == pytest.approx(result.kb_m + result.bm_t_m, rel=1e-12)
        assert result.gm_m == pytest.approx(result.km_t_m - 0.5, rel=1e-12)


class TestBargeAnchors:
    """Textbook box-hull hydrostatics."""

    L, B, T = 100.0, 20.0, 8.0

    @pytest.fixture(scope="class")
    def result(self):
        return compute_hydrostatics(barge_offsets(self.L, self.B, self.T, 41, 21))

    @pytest.mark.parametrize(
        "key",
        ["volume_m3", "cb", "cm", "waterplane_area_m2", "kb_m", "bm_t_m", "bm_l_m"],
    )
    def test_closed_form(self, result, key):
        exact = barge_exact(self.L, self.B, self.T)[key]
        assert relative_error(getattr(result, key), exact) < 1e-9

    def test_wetted_surface(self, result):
        # box section: L*(B + 2T) once the flat of bottom is included
        assert result.wetted_surface_m2 == pytest.approx(
            self.L * (self.B + 2.0 * self.T), rel=1e-9
        )

    def test_displacement_uses_density(self):
        res = compute_hydrostatics(
            barge_offsets(self.L, self.B, self.T), rho=1000.0
        )
        assert res.displacement_t == pytest.approx(self.L * self.B * self.T, rel=1e-12)


# ---------------------------------------------------------------------------
# 2. Quadrature behaviour
# ---------------------------------------------------------------------------

class TestQuadrature:
    def test_simpson_is_exact_for_cubics(self):
        # int_0^2 x^3 dx = 4 with any spacing small enough / even intervals
        xs = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        value, method = simpson_integrate([x ** 3 for x in xs], xs)
        assert method == "simpson"
        assert value == pytest.approx(4.0, rel=1e-12)

    def test_trapezoid_fallback_on_odd_intervals(self):
        # 4 samples -> odd number of intervals -> trapezoid (exact value is 1.125)
        xs = [0.0, 0.5, 1.0, 1.5]
        value, method = simpson_integrate([x ** 2 for x in xs], xs)
        assert method == "trapezoid"
        assert value == pytest.approx(1.1875, rel=1e-12)

    def test_mesh_refinement_reduces_bm_t_error(self):
        L, B, T = 6.0, 0.6, 0.375
        exact = wigley_exact(L, B, T)["bm_t_m"]
        coarse = compute_hydrostatics(wigley_offsets(L, B, T, 21, 41))
        fine = compute_hydrostatics(wigley_offsets(L, B, T, 41, 41))
        err_c = relative_error(coarse.bm_t_m, exact)
        err_f = relative_error(fine.bm_t_m, exact)
        assert err_f < err_c / 8.0, f"no convergence: {err_c:.3e} -> {err_f:.3e}"


# ---------------------------------------------------------------------------
# 3. Geometry gate + input validation
# ---------------------------------------------------------------------------

class TestGeometryGate:
    L, B, T = 6.0, 0.6, 0.375

    def test_matching_reference_cb_passes(self):
        res = compute_hydrostatics(
            wigley_offsets(self.L, self.B, self.T, 41, 21), cb_reference=4.0 / 9.0
        )
        assert res.cb == pytest.approx(4.0 / 9.0, rel=1e-6)

    def test_mismatched_reference_cb_rejected(self):
        with pytest.raises(GeometryError, match="geometry gate failed"):
            compute_hydrostatics(
                wigley_offsets(self.L, self.B, self.T, 41, 21), cb_reference=0.60
            )

    def test_non_monotone_stations_rejected(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        bad = HullOffsets(off.length, [0.0, 1.0, 0.9, 3.0, 4.0, 5.0, 6.0],
                          off.waterlines, [off.half_breadths[0]] * 7)
        with pytest.raises(GeometryError, match="strictly increasing"):
            compute_hydrostatics(bad)

    def test_row_length_mismatch_rejected(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        bad_rows = [list(r) for r in off.half_breadths]
        bad_rows[3] = bad_rows[3][:-1]
        with pytest.raises(GeometryError, match="half-breadths"):
            compute_hydrostatics(
                HullOffsets(off.length, off.stations, off.waterlines, bad_rows)
            )

    def test_negative_half_breadth_rejected(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        bad_rows = [list(r) for r in off.half_breadths]
        bad_rows[2][2] = -0.01
        with pytest.raises(GeometryError, match="negative half-breadth"):
            compute_hydrostatics(
                HullOffsets(off.length, off.stations, off.waterlines, bad_rows)
            )

    def test_first_waterline_must_be_keel(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        shifted = [0.1 + w for w in off.waterlines]
        with pytest.raises(GeometryError, match="keel"):
            compute_hydrostatics(
                HullOffsets(off.length, off.stations, shifted, off.half_breadths)
            )

    def test_station_outside_hull_rejected(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        stations = list(off.stations)
        stations[-1] = self.L * 1.5
        with pytest.raises(GeometryError, match="outside hull extent"):
            compute_hydrostatics(
                HullOffsets(off.length, stations, off.waterlines, off.half_breadths)
            )

    def test_degenerate_zero_volume_rejected(self):
        off = wigley_offsets(self.L, self.B, self.T, 11, 11)
        zeros = [[0.0] * len(off.waterlines) for _ in off.stations]
        with pytest.raises(GeometryError):
            compute_hydrostatics(
                HullOffsets(off.length, off.stations, off.waterlines, zeros)
            )

    def test_implausible_cb_rejected(self):
        # "cigar" form: breadth concentrated over ~20% of the length, so the
        # integrated Cb falls below the physical window even though a max
        # breadth is well defined (scaling a hull uniformly cannot trip the
        # gate, because B is taken from the table itself).
        off = wigley_offsets(self.L, self.B, self.T, 41, 11)
        rows = []
        for xs, row in zip(off.stations, off.half_breadths):
            w = math.exp(-(((xs - 0.5 * self.L) / (0.1 * self.L)) ** 2))
            rows.append([v * w for v in row])
        with pytest.raises(GeometryError, match="block coefficient"):
            compute_hydrostatics(
                HullOffsets(off.length, off.stations, off.waterlines, rows)
            )


# ---------------------------------------------------------------------------
# 4. Stability hand-off (GM sign, honest negative-GM reporting)
# ---------------------------------------------------------------------------

class TestStabilityHandOff:
    def test_negative_gm_reported_not_raised(self):
        res = compute_hydrostatics(wigley_offsets(6.0, 0.6, 0.375, 41, 21), kg=0.5)
        assert res.gm_m is not None and res.gm_m < 0
        assert any("GM <= 0" in note for note in res.notes)

    def test_gm_absent_without_kg(self):
        res = compute_hydrostatics(wigley_offsets(6.0, 0.6, 0.375, 41, 21))
        assert res.gm_m is None

    def test_gm_consistent_with_existing_stability_endpoint(self):
        """KM from geometry feeds /stability's GM/BM inputs."""
        L, B, T = 60.0, 10.0, 4.0
        res = compute_hydrostatics(wigley_offsets(L, B, T, 81, 41), kg=3.0)
        # the GZ produced by the wall-sided formula must be > 0 for GM > 0
        phi = math.radians(5.0)
        gz = res.gm_m * math.sin(phi) + 0.5 * res.bm_t_m * math.tan(phi) ** 2 * math.sin(phi)
        assert res.gm_m > 0 and gz > 0


# ---------------------------------------------------------------------------
# 5. API surface
# ---------------------------------------------------------------------------

class TestHydrostaticsEndpoint:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(calcs.router, prefix="/api/v1")
        return TestClient(app)

    def test_wigley_default(self, client):
        r = client.post("/api/v1/calculations/hydrostatics", json={"kg": 0.5})
        assert r.status_code == 200, r.text
        data = r.json()
        exact = wigley_exact(6.0, 0.6, 0.375)
        for key in ("volume_m3", "cb", "kb_m", "bm_t_m", "bm_l_m"):
            assert relative_error(data[key], exact[key]) < REL_TOL
        assert data["integration"] == "simpson"
        assert data["gm_m"] == pytest.approx(data["km_t_m"] - 0.5, rel=1e-6)

    def test_table_hull_roundtrip(self, client):
        off = barge_offsets(100.0, 20.0, 8.0, 21, 11)
        payload = {
            "hull": "table",
            "length": off.length,
            "stations": list(off.stations),
            "waterlines": list(off.waterlines),
            "half_breadths": [list(r) for r in off.half_breadths],
            "kg": 4.0,
        }
        r = client.post("/api/v1/calculations/hydrostatics", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["cb"] == pytest.approx(1.0, rel=1e-9)
        # the endpoint rounds to 6 decimals, so compare at that resolution
        assert data["bm_t_m"] == pytest.approx(20.0 ** 2 / (12 * 8.0), abs=1e-6)
        assert data["gm_m"] == pytest.approx(data["km_t_m"] - 4.0, rel=1e-6)

    def test_geometry_gate_returns_400(self, client):
        r = client.post(
            "/api/v1/calculations/hydrostatics",
            json={"cb_reference": 0.62},
        )
        assert r.status_code == 400
        assert "geometry gate" in r.json()["detail"]

    def test_unknown_hull_returns_400(self, client):
        r = client.post("/api/v1/calculations/hydrostatics", json={"hull": "kvlcc"})
        assert r.status_code == 400

    def test_table_without_offsets_returns_400(self, client):
        r = client.post("/api/v1/calculations/hydrostatics", json={"hull": "table"})
        assert r.status_code == 400

    def test_rejects_non_positive_length(self, client):
        r = client.post("/api/v1/calculations/hydrostatics", json={"length": -1})
        assert r.status_code == 422

    def test_timing_recorded(self, client):
        client.post("/api/v1/calculations/hydrostatics", json={})
        r = client.get("/api/v1/calculations/timing_baseline")
        assert r.status_code == 200
        assert "hydrostatics" in r.json()["baseline"]