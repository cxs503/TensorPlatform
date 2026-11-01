"""Geometry-exact wetted surface: closed-form anchors + endpoint wiring (m1683).

Two kinds of assertions, mirroring the platform's evidence discipline:

* **agreement** — the revolved-polyline integration reproduces the closed-form
  wetted surface of a cylinder, sphere, prolate/oblate spheroid and the box-hull
  convention to quadrature accuracy, and the error decays at the expected
  second order under meridian refinement;
* **the gate can fail** — the legacy fit ``pi*B*L*(0.5+0.25*Cb)`` is shown to
  miss the same anchors by far more than the 15 % acceptability line, and the
  new geometry gate (Cb integrated from the meridian vs Cb declared) rejects an
  inconsistent geometry instead of silently returning a number.

The endpoint tests drive the real coroutine of ``/calculations/resistance`` with
the same import stub used by ``tests/test_m1386_stability_gz_validation.py``.
"""
from __future__ import annotations

import asyncio
import math
import os
import sys

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, _BACKEND)

# ``api/__init__`` pulls api.auth -> database.config (psycopg2/cryptography are
# absent in the verification sandbox).  A bare package stub keeps this suite
# importable everywhere; nothing of the unit under test is stubbed.
if "api" not in sys.modules:
    import types

    _pkg = types.ModuleType("api")
    _pkg.__path__ = [os.path.join(_BACKEND, "api")]
    sys.modules["api"] = _pkg

from fastapi import HTTPException  # noqa: E402

from services.wetted_surface import (  # noqa: E402
    GeometryError,
    box_wetted_surface,
    cylinder_wetted_surface,
    ellipsoid_wetted_surface,
    empirical_wetted_surface,
    legacy_fit_bias_report,
    prolate_spheroid_profile,
    revolution_wetted_surface,
    sphere_wetted_surface,
)

from api.calculations import ResistanceRequest, calc_resistance  # noqa: E402

L_SUBOFF, D_SUBOFF = 4.356, 0.508  # DARPA SUBOFF parent geometry (canon)


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


# ---------------------------------------------------------------------------
# 1. Closed-form anchors
# ---------------------------------------------------------------------------

class TestClosedFormAnchors:
    def test_cylinder_is_exact(self):
        length, diameter = 10.0, 2.0
        profile = [(length * i / 200.0, 0.5 * diameter) for i in range(201)]
        res = revolution_wetted_surface(profile)
        assert rel(res.wetted_surface_m2, cylinder_wetted_surface(diameter, length)) < 1e-12
        assert res.method == "revolution_frustum_exact"
        # volume of that cylinder is exact too, and Cb -> pi/4? no: Cb uses B=T=D
        assert rel(res.volume_m3, math.pi * 0.25 * diameter**2 * length) < 1e-12

    def test_sphere(self):
        radius = 1.0
        n = 2001
        profile = []
        for i in range(n):
            x = 2.0 * radius * i / (n - 1)
            profile.append((x, math.sqrt(max(0.0, radius**2 - (x - radius) ** 2))))
        res = revolution_wetted_surface(profile)
        assert rel(res.wetted_surface_m2, sphere_wetted_surface(radius)) < 1e-5
        assert rel(res.volume_m3, 4.0 / 3.0 * math.pi * radius**3) < 1e-4

    def test_prolate_spheroid_suboff_equivalent(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 2001)
        res = revolution_wetted_surface(profile)
        exact = ellipsoid_wetted_surface(0.5 * L_SUBOFF, 0.5 * D_SUBOFF)
        assert rel(res.wetted_surface_m2, exact) < 1e-5
        # a spheroid returns pi/6 under the platform's B=T=D convention
        assert res.cb_geometry == pytest.approx(math.pi / 6.0, rel=1e-5)

    def test_oblate_spheroid_branch(self):
        # a < b: oblate closed form must still bracket the numerical integral
        a, b = 1.0, 2.0
        n = 2001
        profile = []
        for i in range(n):
            x = 2.0 * a * i / (n - 1)
            u = max(-1.0, min(1.0, (x - a) / a))
            profile.append((x, b * math.sqrt(max(0.0, 1.0 - u * u))))
        res = revolution_wetted_surface(profile)
        assert rel(res.wetted_surface_m2, ellipsoid_wetted_surface(a, b)) < 1e-5

    def test_box_convention_matches_offset_path(self):
        # ship_hydrostatics integrates to L*(B+2T) on barge_offsets; same here
        assert box_wetted_surface(100.0, 20.0, 8.0) == pytest.approx(100.0 * (20.0 + 16.0))

    def test_convergence_is_second_order(self):
        exact = ellipsoid_wetted_surface(0.5 * L_SUBOFF, 0.5 * D_SUBOFF)
        errs = []
        for n in (101, 201, 401, 801):
            res = revolution_wetted_surface(prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, n))
            errs.append(rel(res.wetted_surface_m2, exact))
        assert errs[0] > errs[1] > errs[2] > errs[3], errs
        # O(h^2): each doubling cuts the error by ~4x; require >= 3x (loose but real)
        for coarse, fine in zip(errs, errs[1:]):
            assert coarse / fine > 3.0, errs


# ---------------------------------------------------------------------------
# 2. The gates can fail
# ---------------------------------------------------------------------------

class TestGatesCanFail:
    def test_legacy_fit_misses_anchors_beyond_15pct(self):
        report = legacy_fit_bias_report(L_SUBOFF, D_SUBOFF)
        assert report["cylinder"]["legacy_bias_pct"] < -15.0
        assert report["prolate_spheroid"]["legacy_bias_pct"] < -3.0

    def test_cb_gate_rejects_inconsistent_geometry(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 401)
        with pytest.raises(GeometryError):
            revolution_wetted_surface(profile, cb_reference=0.90)

    def test_cb_gate_accepts_consistent_geometry(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 401)
        res = revolution_wetted_surface(profile, cb_reference=math.pi / 6.0)
        assert res.cb_geometry == pytest.approx(math.pi / 6.0, rel=1e-4)

    @pytest.mark.parametrize(
        "profile",
        [
            [(0.0, 1.0)],                       # one point
            [(1.0, 0.5), (0.0, 0.5)],           # decreasing x
            [(0.0, -0.1), (1.0, 0.2)],          # negative radius
            [(0.0, 0.0), (1.0, 0.0)],           # degenerate body
            [(0.0, 0.2), (0.0, 0.3)],           # duplicate x
        ],
    )
    def test_inadmissible_profiles_rejected(self, profile):
        with pytest.raises(GeometryError):
            revolution_wetted_surface(profile)


# ---------------------------------------------------------------------------
# 3. Endpoint wiring (/calculations/resistance)
# ---------------------------------------------------------------------------

def _resistance(**kwargs):
    req = ResistanceRequest(**kwargs)
    return asyncio.run(calc_resistance(req))


class TestEndpointWiring:
    def test_geometry_path_beats_legacy_fit(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 2001)
        res = _resistance(
            speed_ms=2.0,
            length=L_SUBOFF,
            beam=D_SUBOFF,
            draft=D_SUBOFF,
            cb=math.pi / 6.0,
            meridian_profile=[list(p) for p in profile],
        )
        exact = ellipsoid_wetted_surface(0.5 * L_SUBOFF, 0.5 * D_SUBOFF)
        assert res.wetted_surface_source == "geometry_integrated"
        assert res.wetted_surface_method == "revolution_frustum_exact"
        assert rel(res.wetted_surface_m2, exact) < 1e-4
        # the legacy fit on the same geometry is far outside the 15 % line
        assert rel(empirical_wetted_surface(L_SUBOFF, D_SUBOFF, math.pi / 6.0), exact) > 0.15

    def test_default_path_is_unchanged(self):
        res = _resistance(speed_ms=2.0)
        assert res.wetted_surface_source == "empirical_formula"
        assert res.wetted_surface_method == ""
        # the endpoint rounds S to 6 decimals; unchanged formula must still be used
        assert res.wetted_surface_m2 == pytest.approx(
            empirical_wetted_surface(4.356, 0.508, 0.542), abs=1e-6
        )

    def test_explicit_surface_still_wins_when_no_geometry(self):
        res = _resistance(speed_ms=2.0, wetted_surface_m2=5.42)
        assert res.wetted_surface_source == "input_validated"
        assert res.wetted_surface_m2 == pytest.approx(5.42, rel=1e-12)

    def test_endpoint_cb_gate_returns_400(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 201)
        with pytest.raises(HTTPException) as exc:
            _resistance(speed_ms=2.0, cb=0.85, meridian_profile=[list(p) for p in profile])
        assert exc.value.status_code == 400

    def test_endpoint_resistance_is_consistent_with_returned_surface(self):
        profile = prolate_spheroid_profile(L_SUBOFF, D_SUBOFF, 801)
        res = _resistance(speed_ms=2.0, cb=math.pi / 6.0,
                          meridian_profile=[list(p) for p in profile])
        # Rf must equal 0.5*rho*Cf*S*V^2 with the *returned* S (no hidden surface)
        rho, nu, V = 998.0, 1.004e-6, 2.0
        Re = V * L_SUBOFF / nu
        cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert res.frictional_resistance_n == pytest.approx(
            0.5 * rho * res.wetted_surface_m2 * cf * V**2, rel=1e-4
        )