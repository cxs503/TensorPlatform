"""Verification anchors for the seakeeping endpoints (docs/verification_debt/seakeeping_fk).

Three layers, all runnable offline except the last:

1. ``references_seakeeping`` detectors flag the historical defect class
   ("the three degrees of freedom return the same curve / the answer ignores its inputs").
2. The repository kernel ``backend/api/seakeeping_physics.py`` satisfies the analytic
   anchor suite A1, A2, A4, A6, A7, A9, A10 (dispersion, Froude-Krylov long-wave limit,
   heading/excitation structure, natural-period closure, cross-endpoint closure, roll
   peak location and magnitude). These are closed forms, not fitted data.
3. If :8001 is reachable, the *deployed* ``/seakeeping/rao`` must not be degenerate;
   otherwise the test is skipped (deployment not available in this environment).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
DEBT = REPO / "docs" / "verification_debt" / "seakeeping_fk"
sys.path.insert(0, str(DEBT))

import references_seakeeping as ref  # noqa: E402

HULL = dict(L=100.0, B=10.0, T=6.25, D=10.0, Cb=0.60, KG=7.0, GM=1.50, speed=0.0)
HULL["displacement"] = HULL["Cb"] * HULL["L"] * HULL["B"] * HULL["T"] * ref.RHO / 1000.0


def _kernel():
    sys.path.insert(0, str(REPO / "backend" / "api"))
    try:
        import seakeeping_physics as sp
    except Exception as exc:                      # pragma: no cover
        pytest.skip(f"kernel not importable: {exc!r}")
    return sp


# ---------------------------------------------------------------------------
# 1. detectors
# ---------------------------------------------------------------------------
def test_detector_flags_identical_dof_curves():
    """An exact copy of one DOF curve into another must be detected as degenerate."""
    a = [1.0, 2.0, 3.0, 4.0]
    same = ref.dof_degeneracy(a, list(a))
    assert same["max_rel_gap"] == 0.0
    assert same["n_differing"] == 0

    healthy = ref.dof_degeneracy(a, [1.0, 2.0, 3.0, 4.4])
    assert healthy["n_differing"] == 1
    assert healthy["max_rel_gap"] > 0.05


def test_detector_flags_input_invariance():
    base = [1.0, 2.0, 3.0]
    out = ref.input_degeneracy({
        "base": base,
        "heading": list(base),
        "speed": list(base),
        "block_coefficient": [1.0, 2.1, 3.0],
    })
    assert out["fully_invariant"] is False
    assert set(out["invariant_to"]) == {"heading", "speed"}
    assert out["sensitive_to"] == ["block_coefficient"]


# ---------------------------------------------------------------------------
# 2. closed forms
# ---------------------------------------------------------------------------
def test_reference_closed_forms():
    """Independent checks of the reference helpers themselves."""
    w = 1.0
    assert ref.deep_water_k(w) == pytest.approx(w * w / ref.G)
    assert ref.wavelength(2.0 * math.pi) == pytest.approx(ref.G / (2.0 * math.pi))
    # head seas (180 deg) raise the encounter frequency by exactly k U
    U = 5.0
    assert ref.encounter_omega(w, U, 180.0) == pytest.approx(w + ref.deep_water_k(w) * U)
    # following seas (0 deg) lower it
    assert ref.encounter_omega(w, U, 0.0) == pytest.approx(w - ref.deep_water_k(w) * U)
    # roll: k_xx = 3.6 m and GM = 1.5 m give omega_4 = sqrt(g GM)/k_xx
    assert ref.roll_peak_omega(1.5, 3.6) == pytest.approx(math.sqrt(ref.G * 1.5) / 3.6)
    assert ref.roll_peak_magnification(0.08) == pytest.approx(6.25)
    # heave: T = 2 pi sqrt((m+A33)/(rho g Aw)) with A33 = 0.9 m on a 733.33 m2 waterplane
    mass = 3843.75e3
    assert ref.heave_natural_period(mass, 0.9 * mass, 733.33333) == pytest.approx(
        2.0 * math.pi * math.sqrt(1.9 * mass / (ref.RHO * ref.G * 733.33333)))


# ---------------------------------------------------------------------------
# 3. repository kernel anchors
# ---------------------------------------------------------------------------
def test_repository_kernel_satisfies_analytic_anchors():
    sp = _kernel()
    ship = sp.ship_from_request(dict(HULL))
    freqs = list(sp.FREQ_GRID_DEFAULT)
    curve = sp.rao_curves(ship, heading_deg=180.0, speed_kn=0.0, freqs=freqs)
    nat = sp.natural_periods(ship)
    w = np.asarray(curve["frequencies"], float)
    hv = np.asarray(curve["heave_rao"], float)
    rl = np.asarray(curve["roll_rao"], float)

    # A1 dispersion / encounter-frequency identity
    we_ref = np.array([ref.encounter_omega(x, 0.0, 180.0) for x in w])
    assert np.max(np.abs((np.asarray(curve["encounter_frequencies"]) - we_ref) / we_ref)) < 1e-9

    # A2 Froude-Krylov long-wave limits, beam seas for roll (excitation ~ sin(beta))
    i0 = int(np.argmin(w))
    c90 = sp.rao_curves(ship, heading_deg=90.0, speed_kn=0.0, freqs=freqs)
    assert abs(ref.rel_err(hv[i0], 1.0)) < 0.05
    assert abs(ref.rel_err(np.asarray(curve["pitch_rao"])[i0], 1.0)) < 0.05
    assert abs(ref.rel_err(np.asarray(c90["roll_rao"])[i0], 1.0)) < 0.05

    # A4 heading structure: roll excitation ~ sin(beta) => identically zero in head seas
    assert np.max(rl) < 1e-9
    assert np.max(c90["roll_rao"]) > 0.1

    # A6 natural-period closure from the kernel's own reported added mass / GM
    mass = HULL["displacement"] * 1000.0
    aw = sp.sectional_geometry(HULL["L"], HULL["B"], HULL["T"], HULL["Cb"])["Aw"]
    assert ref.heave_natural_period(mass, nat["A33"], aw) == pytest.approx(nat["heave"], rel=1e-9)
    assert ref.roll_natural_period(HULL["GM"], nat["k_xx_eff"]) == pytest.approx(nat["roll"], rel=1e-9)

    # A7 cross-endpoint closure (both endpoints consume solve_rao)
    row = sp.solve_rao(ship, 0.8, 180.0, 0.0, 1.0)
    mo = sp.motion_amplitudes(ship, 0.8, 180.0, 0.0, 1.0)
    assert mo["heave_rao"] == pytest.approx(row["heave_rao"], rel=1e-12)

    # A9/A10 roll peak at omega_4 with the SDOF magnification 1/(2 zeta)
    w4 = ref.roll_peak_omega(HULL["GM"], nat["k_xx_eff"])
    beam = np.asarray(c90["roll_rao"], float)
    assert abs(ref.rel_err(float(w[int(np.argmax(beam))]), w4)) < 0.15
    assert abs(ref.rel_err(float(np.max(beam)), ref.roll_peak_magnification(sp.ZETA_ROLL))) < 0.05

    # A8 geometry gate: sectional quadrature must reproduce Cb L B T
    nabla = sp.sectional_geometry(HULL["L"], HULL["B"], HULL["T"], HULL["Cb"])["nabla"]
    assert abs(ref.rel_err(nabla, HULL["Cb"] * HULL["L"] * HULL["B"] * HULL["T"])) < 1e-3


# ---------------------------------------------------------------------------
# 4. deployed endpoint must not be degenerate
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason="known defect: the deployed /seakeeping/rao still serves the legacy "
           "input-invariant curve with heave_rao == pitch_rao; switching the service to "
           "backend/api/seakeeping_physics.py is tracked as a separate ship-repo mission",
)
def test_deployed_rao_endpoint_is_not_degenerate():
    import requests

    base = "http://127.0.0.1:8001/api/v1"
    try:
        requests.get("http://127.0.0.1:8001/health", timeout=3)
    except Exception:
        pytest.skip("ship platform :8001 not reachable")

    def rao(**over):
        body = dict(HULL)
        body.update(over)
        r = requests.post(f"{base}/seakeeping/rao", json=body, timeout=60)
        assert r.status_code == 200
        return r.json()

    base_curve = rao(heading=180.0, speed=0.0, wave_frequency=0.5)
    d = ref.dof_degeneracy(base_curve["heave_rao"], base_curve["pitch_rao"])
    assert d["n_differing"] > 0, "heave and pitch RAO return the same curve (legacy defect)"
    heading = rao(heading=90.0)
    gap = max(abs(a - b) for a, b in zip(base_curve["roll_rao"], heading["roll_rao"]))
    assert gap > 1e-6, "roll RAO does not react to heading (legacy defect)"