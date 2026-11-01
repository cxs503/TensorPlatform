"""Regression gate: BEM open-water model vs the Wageningen B-series reference.

These tests guard the *verified envelope* established by
``tools/verify/propeller_open_water/audit_bem_vs_bseries.py`` (mission m1644).
They are not a re-derivation of the physics: they pin the envelope so that a
silent change of the BEM model (section data, induction solver, loss model)
cannot pass unnoticed, and they assert the physical invariants that the
absolute-accuracy audit does **not** excuse.

Verdict recorded by the audit: the model is structurally sound
(eta_0 <= actuator-disc bound, energy consistent, induction converged,
zero-thrust advance ratio present) but its absolute KT/KQ are outside the
platform 15% band over 59% of the audited cells, therefore the *absolute*
open-water prediction level of this module is WITHHELD, not REAL. The envelope
below is what an honest regression test can pin.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "verify", "propeller_open_water"))

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    ideal_efficiency_bound,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
import reference_bseries as ref  # noqa: E402

AUDIT_JSON = os.path.join(
    REPO_ROOT, "artifacts", "m1644", "propeller_bem_ow", "audit.json")

GEOMS = [
    ("B4-70", 1.00, 0.70, 4),
    ("B4-40", 1.00, 0.40, 4),
    ("B5-80", 0.95, 0.80, 5),
]


def _geom(pd: float, ae: float, z: int) -> PropellerGeometry:
    return PropellerGeometry(pitch_ratio=pd, blade_area_ratio=ae, blade_count=z)


def test_reference_table_is_the_cross_checked_one():
    """The reference must still be the 39/47-term table, not a local surrogate."""
    assert len(ref.KT_COEFFICIENTS) == 39
    assert len(ref.KQ_COEFFICIENTS) == 47


@pytest.mark.parametrize("name,pd,ae,z", GEOMS)
def test_bem_zero_thrust_ratio_within_30pct_of_reference(name, pd, ae, z):
    """J0 is the geometry-integrated signature of the model; the audit measured
    <= 27% deviation for every audited geometry. 30% is the guard band."""
    g = _geom(pd, ae, z)
    j0_bem = solve_zero_thrust_advance_ratio(g)
    j0_ref = ref.zero_thrust_J(pd, ae, z)
    assert abs(j0_bem - j0_ref) / j0_ref < 0.30, (name, j0_bem, j0_ref)


@pytest.mark.parametrize("name,pd,ae,z", GEOMS)
def test_bem_efficiency_never_exceeds_actuator_disc_bound(name, pd, ae, z):
    """Hard energy gate: no open-water point may beat the ideal bound."""
    g = _geom(pd, ae, z)
    j0 = ref.zero_thrust_J(pd, ae, z)
    for k in range(1, 16):
        J = (0.10 + 0.05 * k) * j0
        pt = open_water_point(g, J)
        bound = ideal_efficiency_bound(pt["KT"], J, g)
        assert pt["eta0"] <= bound + 1e-9, (name, J, pt["eta0"], bound)


@pytest.mark.parametrize("name,pd,ae,z", GEOMS)
def test_bem_kt_error_envelope_is_stable(name, pd, ae, z):
    """Envelope guard: mean |dKT| inside 5..40% on the loaded branch.

    A drop below 5% would mean the model was silently calibrated to this
    polynomial (then the audit must be re-run and the report updated); a rise
    above 40% means a real regression in the BEM implementation.
    """
    g = _geom(pd, ae, z)
    j0 = ref.zero_thrust_J(pd, ae, z)
    errs = []
    for k in range(1, 16):
        J = (0.10 + 0.05 * k) * j0
        bem = open_water_point(g, J)
        r = ref.open_water_coefficients(J, pd, ae, z)
        errs.append(abs(100.0 * (bem["KT"] - r["KT"]) / r["KT"]))
    mean_err = sum(errs) / len(errs)
    assert 5.0 < mean_err < 40.0, (name, mean_err)


def test_bem_torque_falls_with_advance_ratio():
    """Qualitative physics: KQ must decrease with J (the legacy surrogate failed
    exactly here, see the module docstring)."""
    g = _geom(1.0, 0.55, 4)
    kq = [open_water_point(g, J)["KQ"] for J in (0.1, 0.3, 0.5, 0.7)]
    assert all(kq[i] > kq[i + 1] for i in range(len(kq) - 1)), kq


def test_audit_artifact_is_reproducible():
    """The published audit must be regenerable from the repository itself."""
    proc = subprocess.run(
        [sys.executable, os.path.join(
            REPO_ROOT, "tools", "verify", "propeller_open_water",
            "audit_bem_vs_bseries.py")],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    with open(AUDIT_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    s = data["summary"]
    # the two headline findings of the m1644 audit
    assert 10.0 < s["mean_abs_e_KT_pct_all"] < 30.0
    assert s["all_eta0_below_ideal_bound"] is True
    assert s["cells_within_15pct_KT_and_KQ"] < s["cells_total"]