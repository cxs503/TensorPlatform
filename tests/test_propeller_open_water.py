"""Verification suite for the open-water propeller model (mission 1393).

Evidence grade rationale
------------------------
The capability ``propeller.open_water`` is advertised as REAL by the platform.
Before this suite the implementation (``tools/batch_scan``) was a hard-coded
surrogate ``KT = max(0.05, 0.5-0.3J)``, ``KQ = max(0.01, 0.05+0.02J)``,
``eta0 = clip(..., 0.3, 0.85)``, which fails every elementary propeller
invariant.  The tests below do two things:

1. ``TestLegacySurrogateFalsification`` reproduces that surrogate locally and
   proves it violates the invariants - i.e. the test set is *sensitive* (it can
   still see the defect) and the old code was indeed MOCK-grade.
2. ``TestBladeElementMomentumModel`` locks the physics of the replacement
   (``backend/services/propeller_open_water.py``) to analytic benchmarks:

   * ideal actuator-disc efficiency bound ``eta_ideal = 2/(1+sqrt(1+C_T))``,
   * monotonic ``KT(J)``, ``KQ(J)`` with a unique zero-thrust advance ratio,
   * single interior efficiency maximum,
   * exact Froude-scaling invariance and density invariance of the
     non-dimensional coefficients,
   * declared blade-area ratio honoured by the generated chord distribution,
   * zero-pitch limit,
   * momentum-coupled vs pure blade-element cross-check.

No literature *point* data (Wageningen chart values) was reachable offline for
this mission; absolute-level accuracy against published charts therefore stays
WITHHELD and is documented as such in
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services.propeller_open_water import (  # noqa: E402
    CL_MAX,
    PropellerGeometry,
    best_efficiency_point,
    blade_element_section,
    ideal_efficiency_bound,
    open_water_curve,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)

FAST = dict(sections=16)
GEOM = PropellerGeometry(**FAST)


def legacy_surrogate(J: float, D: float = 5.0, rpm: float = 100.0, Va_knots: float = 12.0) -> dict:
    """The shipped-before surrogate, kept here only as a falsification target."""
    n = rpm / 60.0
    KT = max(0.05, 0.5 - 0.3 * J)
    KQ = max(0.01, 0.05 + 0.02 * J)
    eta0 = (J * KT) / (2 * math.pi * KQ) if KQ > 0 else 0.0
    eta0 = min(0.85, max(0.3, eta0))
    return {"J": J, "KT": KT, "KQ": KQ, "eta0": eta0}


# ---------------------------------------------------------------------------
# 1. Sensitivity: the legacy surrogate really was unphysical
# ---------------------------------------------------------------------------
class TestLegacySurrogateFalsification:
    def test_legacy_torque_increases_with_advance_ratio(self):
        ks = [legacy_surrogate(J)["KQ"] for J in (0.1, 0.3, 0.5, 0.7)]
        assert ks[-1] > ks[0], "legacy surrogate must reproduce the unphysical KQ growth"
        assert all(b >= a for a, b in zip(ks, ks[1:]))

    def test_legacy_efficiency_is_artificially_plateaued_at_zero_advance(self):
        # a real propeller has eta0 -> 0 as J -> 0; the surrogate is clipped to 0.3
        etas = [legacy_surrogate(J)["eta0"] for J in (0.02, 0.04, 0.06, 0.08)]
        assert len(set(round(e, 12) for e in etas)) == 1
        assert etas[0] == pytest.approx(0.3)

    def test_legacy_has_no_zero_thrust_advance_ratio(self):
        assert legacy_surrogate(5.0)["KT"] == pytest.approx(0.05)

    def test_legacy_ignores_pitch_and_blade_area(self):
        # identical output regardless of propeller geometry: the surrogate has no
        # P/D, Z or Ae/A0 argument at all
        assert set(legacy_surrogate(0.6)) == {"J", "KT", "KQ", "eta0"}


# ---------------------------------------------------------------------------
# 2. Geometry bookkeeping
# ---------------------------------------------------------------------------
class TestGeometry:
    def test_declared_blade_area_ratio_is_honoured(self):
        g = PropellerGeometry(diameter_m=5.0, blade_area_ratio=0.55, sections=40)
        n = 400
        h = (1.0 - g.hub_ratio) / n
        area = 0.0
        for k in range(n + 1):
            x = g.hub_ratio + k * h
            w = 1.0 if k in (0, n) else (4.0 if k % 2 else 2.0)
            area += w * g.chord_m(x)
        area *= h / 3.0 * g.radius_m * g.blade_count
        assert area == pytest.approx(g.blade_area_m2, rel=2e-3)

    def test_pitch_angle_formula(self):
        g = PropellerGeometry(pitch_ratio=1.0)
        assert g.pitch_angle_rad(0.7) == pytest.approx(math.atan(1.0 / (math.pi * 0.7)), rel=1e-12)
        assert g.pitch_angle_rad(0.7) > g.pitch_angle_rad(0.9)  # pitch angle falls to the tip

    def test_chord_positive_over_span(self):
        g = PropellerGeometry(**FAST)
        for k in range(1, 40):
            x = g.hub_ratio + (1.0 - g.hub_ratio) * k / 40.0
            assert g.chord_m(x) > 0.0

    def test_invalid_geometry_rejected(self):
        with pytest.raises(ValueError):
            PropellerGeometry(diameter_m=-1)
        with pytest.raises(ValueError):
            PropellerGeometry(blade_count=12)
        with pytest.raises(ValueError):
            PropellerGeometry(sections=4)


# ---------------------------------------------------------------------------
# 3. Analytic bound: ideal (actuator-disc) efficiency
# ---------------------------------------------------------------------------
class TestMomentumBound:
    @pytest.mark.parametrize("J", [0.2, 0.4, 0.6, 0.8])
    def test_efficiency_below_ideal_disc_bound(self, J):
        pt = open_water_point(GEOM, J)
        assert 0.0 < pt["eta0"] < pt["eta_ideal"] < 1.0

    def test_ideal_bound_formula_is_dimensionless(self):
        g = PropellerGeometry(**FAST)
        # C_T = 8 KT / (pi J^2) -> stated bounds at reference values
        assert ideal_efficiency_bound(0.2, 0.6, g) == pytest.approx(
            2.0 / (1.0 + math.sqrt(1.0 + 8 * 0.2 / (math.pi * 0.36))), rel=1e-12)

    def test_efficiency_ratio_realistic(self):
        # real propulsors reach 0.75-0.90 of the ideal-disc efficiency
        pt = open_water_point(GEOM, 0.8)
        assert 0.70 <= pt["eta0"] / pt["eta_ideal"] <= 0.92


# ---------------------------------------------------------------------------
# 4. Shape of the open-water characteristics
# ---------------------------------------------------------------------------
class TestCurveStructure:
    def test_torque_strictly_decreasing(self):
        curve = open_water_curve(GEOM, [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9])
        kq = [p["KQ"] for p in curve]
        assert all(b < a for a, b in zip(kq, kq[1:])), kq

    def test_thrust_strictly_decreasing(self):
        curve = open_water_curve(GEOM, [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9])
        kt = [p["KT"] for p in curve]
        assert all(b < a for a, b in zip(kt, kt[1:])), kt

    def test_zero_thrust_advance_ratio_exists_and_is_sharp(self):
        J0 = solve_zero_thrust_advance_ratio(GEOM)
        assert 0.5 < J0 < 1.6
        assert abs(open_water_point(GEOM, J0)["KT"]) < 2e-3
        assert open_water_point(GEOM, 0.9 * J0)["KT"] > 0.0
        assert open_water_point(GEOM, 1.1 * J0)["KT"] < 0.0

    def test_efficiency_zero_at_both_ends_with_single_maximum(self):
        J0 = solve_zero_thrust_advance_ratio(GEOM)
        assert open_water_point(GEOM, 0.0)["eta0"] == pytest.approx(0.0, abs=1e-12)
        etas = [open_water_point(GEOM, J0 * k / 20.0)["eta0"] for k in range(0, 20)]
        peak = etas.index(max(etas))
        assert 0 < peak < len(etas) - 1
        assert etas[peak] > etas[peak - 1] and etas[peak] > etas[peak + 1]

    def test_pitch_ratio_shifts_zero_thrust_point(self):
        j0 = [solve_zero_thrust_advance_ratio(PropellerGeometry(pitch_ratio=pd, **FAST))
              for pd in (0.6, 0.8, 1.0, 1.2)]
        assert all(b > a for a, b in zip(j0, j0[1:])), j0

    def test_more_blade_area_more_torque(self):
        kq = [open_water_point(PropellerGeometry(blade_area_ratio=ae, **FAST), 0.4)["KQ"]
              for ae in (0.35, 0.55, 0.75)]
        assert all(b > a for a, b in zip(kq, kq[1:])), kq

    def test_zero_pitch_is_a_brake_not_a_propulsor(self):
        # near-zero pitch: no thrust at J=0 and braking (turbine-mode) thrust for J>0
        g = PropellerGeometry(pitch_ratio=0.02, **FAST)
        assert abs(open_water_point(g, 0.0)["KT"]) < 0.01
        assert open_water_point(g, 0.3)["KT"] < 0.0


# ---------------------------------------------------------------------------
# 5. Invariances (exact, machine-checkable)
# ---------------------------------------------------------------------------
class TestInvariances:
    def test_froude_scaling_invariance(self):
        small = PropellerGeometry(diameter_m=0.25, **FAST)
        big = PropellerGeometry(diameter_m=5.0, **FAST)
        a = open_water_point(small, 0.6, n_rps=20.0)
        b = open_water_point(big, 0.6, n_rps=1.0)
        assert a["KT"] == pytest.approx(b["KT"], rel=1e-9)
        assert a["KQ"] == pytest.approx(b["KQ"], rel=1e-9)
        assert a["eta0"] == pytest.approx(b["eta0"], rel=1e-9)

    def test_density_invariance_of_coefficients(self):
        a = open_water_point(GEOM, 0.5, rho=998.0)
        b = open_water_point(GEOM, 0.5, rho=1025.0)
        assert a["KT"] == pytest.approx(b["KT"], rel=1e-12)
        assert a["KQ"] == pytest.approx(b["KQ"], rel=1e-12)

    def test_thrust_round_trip(self):
        pt = open_water_point(PropellerGeometry(diameter_m=5.0, **FAST), 0.6, rho=1025.0, n_rps=2.0)
        expected = pt["KT"] * 1025.0 * 2.0 ** 2 * 5.0 ** 4
        assert pt["thrust_n"] == pytest.approx(expected, rel=1e-10)


# ---------------------------------------------------------------------------
# 6. Cross-check: momentum coupling must relieve loading
# ---------------------------------------------------------------------------
class TestinducedVelocityCrossCheck:
    def test_induction_reduces_bollard_thrust(self):
        coupled = open_water_point(GEOM, 0.0, axial_induction=True)["KT"]
        pure = open_water_point(GEOM, 0.0, axial_induction=False)["KT"]
        assert coupled < pure, (coupled, pure)

    def test_induction_converges_at_design_points(self):
        for J in (0.2, 0.4, 0.6):
            assert open_water_point(GEOM, J)["induction_converged"] is True

    def test_section_lift_is_capped(self):
        cl, cd = blade_element_section(math.radians(40.0))
        assert cl == pytest.approx(CL_MAX)
        assert cd > 0.0


# ---------------------------------------------------------------------------
# 7. Batch wrapper (tools/batch_scan) must use the physics model
# ---------------------------------------------------------------------------
class TestBatchWrapperWiring:
    def test_batch_executor_uses_bem_model(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "batch_scan")
        )
        from batch_executor import ShipCalculationEngine  # noqa: E402

        params = {
            "diameter_m": 5.0,
            "pitch_ratio": 1.0,
            "blade_area_ratio": 0.55,
            "blade_count": 4,
            "rpm": 100,
            "advance_speed_kn": 12 * 0.5144 / 0.5144 * 0.6 * 100 / 60.0 * 5.0 / 0.5144,
        }
        out = ShipCalculationEngine.calc_propeller(params)
        assert out["model"].startswith("bem")
        # wrapper must expose a physically decreasing KQ with advance ratio
        lo = ShipCalculationEngine.calc_propeller(dict(params, advance_speed_kn=2.0))
        hi = ShipCalculationEngine.calc_propeller(dict(params, advance_speed_kn=8.0))
        assert hi["torque_coefficient"] < lo["torque_coefficient"]

    def test_batch_wrapper_rejects_bad_geometry(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "batch_scan")
        )
        from batch_executor import ShipCalculationEngine  # noqa: E402

        with pytest.raises(ValueError):
            ShipCalculationEngine.calc_propeller({"diameter_m": 0, "rpm": 100})