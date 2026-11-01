"""m1659 — resistance-kernel routing: legacy axisymmetric fit vs Holtrop-Mennen.

Background
----------
``docs/resistance_benchmark_verification.md`` (m1414) measured the legacy wetted
surface ``S = pi*B*L*(0.5+0.25*Cb)`` against three benchmark hulls and found
errors of 43 %+ on merchant hulls (it is an axisymmetric-body fit).  The
published Holtrop-Mennen (1982) regression already lives in
``backend/services/holtrop_mennen.py`` and is validated in
``backend/tests/test_benchmark_reference_integrity.py`` (KVLCC2 public value
27,194 m^2, +/-10 %), but the HTTP endpoint never used it.

These tests pin the wire-up:

* the default kernel is unchanged (bit-for-bit backward compatibility);
* ``method="holtrop_mennen"`` reproduces the service *exactly* (no re-fitting in
  the API layer) and keeps the canon rule R1 identity ``Ct = Rt/(0.5 rho S V^2)``;
* the resistance decomposition sums to ``Rt`` (audit-friendly);
* a caller-supplied ``wetted_surface_m2`` still wins in both kernels;
* the geometric sanity gate (Cb range, positive dimensions) is applied before
  either kernel runs.
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.calculations import ResistanceRequest, calc_resistance  # noqa: E402
from services.holtrop_mennen import holtrop_resistance  # noqa: E402
from fastapi import HTTPException  # noqa: E402

KVLCC2 = dict(
    ship_type="KVLCC2",
    length=320.0,
    beam=58.0,
    draft=20.8,
    cb=0.8098,
    speed_ms=7.97,            # ~15.5 kn service speed
    water_density=1025.0,     # seawater (canon rule R2: full scale -> seawater)
    kinematic_viscosity=1.188e-6,
)
KVLCC2_PUBLIC_S = 27194.0     # public wetted-surface value used by the canon gate


def run(**kwargs):
    return asyncio.run(calc_resistance(ResistanceRequest(**kwargs)))


def legacy_s(L: float, B: float, cb: float) -> float:
    return math.pi * B * L * (0.5 + 0.25 * cb)


# ---------------------------------------------------------------------------
# 1. Backward compatibility of the default kernel
# ---------------------------------------------------------------------------

def test_default_kernel_is_legacy_and_unchanged():
    """No method/default-field change: the shipped SUBOFF defaults must not move."""
    r = run(speed_ms=2.5)                      # pure defaults = DARPA SUBOFF
    assert r.resistance_method == "ittc_axisymmetric"
    assert r.wetted_surface_source == "empirical_formula"
    assert r.wetted_surface_m2 == pytest.approx(legacy_s(4.356, 0.508, 0.542), abs=1e-5)
    assert r.wave_resistance_n == 0.0
    assert r.correlation_allowance_n == 0.0


# ---------------------------------------------------------------------------
# 2. The merchant-hull kernel fixes the m1414 wetted-surface finding
# ---------------------------------------------------------------------------

def test_holtrop_wetted_surface_beats_legacy_on_kvlcc2():
    r = run(method="holtrop_mennen", **KVLCC2)
    assert r.wetted_surface_source == "holtrop_mennen_1982"
    err_holtrop = abs(r.wetted_surface_m2 - KVLCC2_PUBLIC_S) / KVLCC2_PUBLIC_S
    err_legacy = abs(legacy_s(320.0, 58.0, 0.8098) - KVLCC2_PUBLIC_S) / KVLCC2_PUBLIC_S
    # published fit reproduces the public value inside its own band ...
    assert err_holtrop < 0.10, (r.wetted_surface_m2, err_holtrop)
    # ... while the axisymmetric fit is demonstrably outside it (the m1414 defect)
    assert err_legacy > 0.30, err_legacy
    assert err_holtrop < err_legacy / 3.0


def test_holtrop_physical_band_and_monotonicity():
    """S/(L*B) must stay in the canon R1 band [1.0, 3.2] and grow with Cb."""
    prev = 0.0
    for cb in (0.60, 0.70, 0.8098, 0.85):
        r = run(method="holtrop_mennen", **{**KVLCC2, "cb": cb})
        band = r.wetted_surface_m2 / (320.0 * 58.0)
        assert 1.0 <= band <= 3.2, (cb, band)
        assert r.wetted_surface_m2 > prev
        prev = r.wetted_surface_m2


# ---------------------------------------------------------------------------
# 3. Wire-up fidelity: the API must not re-fit the service
# ---------------------------------------------------------------------------

def test_endpoint_reproduces_service_exactly():
    r = run(method="holtrop_mennen", **KVLCC2)
    s = holtrop_resistance(320.0, 58.0, 20.8, 0.8098, 7.97,
                           rho=1025.0, nu=1.188e-6)
    assert r.wetted_surface_m2 == pytest.approx(s.wetted_surface_m2, abs=1e-4)
    assert r.total_resistance_n == pytest.approx(s.rt_n, rel=1e-6)
    assert r.total_resistance_coefficient == pytest.approx(s.ct, rel=1e-6)
    assert r.friction_coefficient_cf == pytest.approx(s.cf, rel=1e-6)


def test_canon_r1_identity_and_decomposition_close():
    r = run(method="holtrop_mennen", **KVLCC2)
    ct_identity = r.total_resistance_n / (0.5 * 1025.0 * r.wetted_surface_m2 * 7.97 ** 2)
    assert r.total_resistance_coefficient == pytest.approx(ct_identity, rel=1e-6)
    parts = (r.frictional_resistance_n + r.form_resistance_n + r.residual_resistance_n
             + r.correlation_allowance_n + r.appendage_resistance_n)
    assert parts == pytest.approx(r.total_resistance_n, rel=1e-6)
    # redundancy floor: Ct must not fall below the ITTC-1957 friction line (R5)
    assert r.total_resistance_coefficient > r.friction_coefficient_cf
    assert r.model_notes, "Holtrop branch must disclose derived inputs / domain warnings"


# ---------------------------------------------------------------------------
# 4. Precedence and gates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["ittc_axisymmetric", "holtrop_mennen"])
def test_supplied_wetted_surface_wins(method):
    r = run(method=method, wetted_surface_m2=12345.0, **KVLCC2)
    assert r.wetted_surface_m2 == pytest.approx(12345.0)
    assert r.wetted_surface_source == "input_validated"


@pytest.mark.parametrize("method", ["ittc_axisymmetric", "holtrop_mennen"])
def test_geometry_gate_precedes_kernel(method):
    # schema-level rejection (cb > 1, negative draft) never reaches a kernel
    with pytest.raises(ValueError):
        run(method=method, **{**KVLCC2, "cb": 1.2})
    with pytest.raises(ValueError):
        run(method=method, **{**KVLCC2, "draft": -1.0})
    # endpoint-level gate: Cb below the physical band [0.3, 1.0] passes the schema
    # and must be rejected with HTTP 400 *before* the kernel runs
    with pytest.raises(HTTPException) as exc:
        run(method=method, **{**KVLCC2, "cb": 0.20})
    assert exc.value.status_code == 400


def test_holtrop_caller_particulars_are_used():
    """Explicit Cm/Cwp must reach the service (no silent regression substitution)."""
    r_derived = run(method="holtrop_mennen", **KVLCC2)
    r_given = run(method="holtrop_mennen", **KVLCC2, cm=0.995, cwp=0.90)
    assert any("derived cm" in n for n in r_derived.model_notes)
    assert not any("derived cm" in n for n in r_given.model_notes)
    assert r_given.wetted_surface_m2 != pytest.approx(r_derived.wetted_surface_m2)