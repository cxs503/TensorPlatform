"""Unit tests for the open-water audit kernel and invariant battery.

Runs without a database: the kernel is loaded from its file path so that
``backend/api/__init__.py`` (which opens a DB engine) is never imported.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_KERNEL = ROOT / "backend" / "api" / "domain" / "propeller_open_water.py"


def _load():
    spec = importlib.util.spec_from_file_location("pow_under_test", _KERNEL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pow_under_test"] = mod        # dataclass needs the registry entry
    spec.loader.exec_module(mod)
    return mod


K = _load()


# ---------------------------------------------------------------- kernel ----

def test_open_water_identity_holds_exactly():
    r = K.open_water(0.5, 1.0, 4, 0.55)
    assert r.eta0 == pytest.approx(0.5 * r.KT / (2 * math.pi * r.KQ), rel=1e-12)


def test_kt_decreases_with_advance_ratio():
    kts = [K.ll_kernel(j, 1.0, 4, 0.55)[0] for j in (0.0, 0.2, 0.4, 0.6, 0.8)]
    assert all(b <= a + 1e-12 for a, b in zip(kts, kts[1:]))


def test_kt_zero_thrust_is_finite_and_physical():
    j0 = K._zeros_of(lambda j: K.ll_kernel(j, 1.0, 4, 0.55)[0])
    assert not math.isnan(j0)
    assert 0.6 < j0 < 1.3                       # for P/D = 1.0


def test_no_negative_thrust_past_zero_point():
    for j in (1.0, 1.2, 1.5, 2.0):
        kt, kq, _ = K.ll_kernel(j, 1.0, 4, 0.55)
        assert kt >= 0.0
        assert kq > 0.0                          # windmilling still needs torque


def test_kt_increases_with_pitch():
    kts = [K.ll_kernel(0.4, p, 4, 0.55)[0] for p in (0.6, 0.8, 1.0, 1.2, 1.4)]
    assert all(b > a for a, b in zip(kts, kts[1:]))


def test_efficiency_peak_is_interior_and_in_envelope():
    j_opt, eta_max = K.design_advance_ratio(1.0, 4, 0.55)
    assert 0.25 < j_opt < 0.95                   # not pinned to a search bound
    assert 0.30 < eta_max < 0.78


def test_negative_advance_ratio_rejected():
    with pytest.raises(ValueError):
        K.kp_curves(-0.1, 1.0, 4, 0.55)


# ------------------------------------------------- serving-fit regressions ---

def test_serving_kernel_torque_is_a_fixed_fraction_of_thrust():
    """Documents the defect: 10*KQ/KT == 1 to machine precision."""
    for (j, p_d, z, ae) in [(0.2, 1.0, 4, 0.55), (0.4, 1.2, 5, 0.70), (0.6, 0.8, 4, 0.40)]:
        kt, kq, _ = K.serve_kernel_replay(j, p_d, z, ae)
        assert 10.0 * kq / kt == pytest.approx(1.0, abs=1e-12)


def test_serving_kernel_efficiency_is_geometry_independent():
    """eta0 = 10J/(2*pi) until the 0.85 clip -> independent of P/D, Z, Ae/Ao.
    
    This test documents a known defect in the serving kernel (MOCK hand-fit):
    the efficiency is independent of geometry, which is unphysical. The test
    uses a tolerance to account for floating-point precision.
    """
    eta = {K.serve_kernel_replay(0.3, p, z, ae)[2]
           for p in (0.6, 1.0, 1.4) for z in (4, 5) for ae in (0.40, 0.85)}
    # All values should be approximately equal (within floating-point tolerance)
    eta_list = list(eta)
    for e in eta_list[1:]:
        assert e == pytest.approx(eta_list[0], rel=1e-9)


def test_serving_kernel_efficiency_plateaus_at_clip():
    etas = [K.serve_kernel_replay(j / 100, 1.0, 4, 0.55)[2] for j in range(45, 101)]
    assert max(etas) == pytest.approx(0.85, abs=1e-9)
    assert sum(1 for e in etas if e == pytest.approx(0.85, abs=1e-9)) > 20


# ---------------------------------------------------------- invariant gates --

def test_invariant_battery_flags_serving_kernel():
    inv = {i.ident: i for i in K.check_invariants(K.serve_kernel_replay)}
    for ident in ("I1", "I2", "I4", "I5", "I6", "I7"):
        assert inv[ident].verdict == "FAIL", f"{ident} should fail on the serving kernel"
    for ident in ("I3", "I8", "I9"):
        assert inv[ident].verdict == "PASS", f"{ident} should pass on the serving kernel"


def test_invariant_battery_passes_structural_checks_on_ll_kernel():
    inv = {i.ident: i for i in K.check_invariants(K.ll_kernel)}
    for ident in ("I1", "I2", "I3", "I4", "I5", "I8"):
        assert inv[ident].verdict == "PASS", f"{ident} failed: {inv[ident].detail}"


def test_invariant_ids_are_unique_and_complete():
    ids = [i.ident for i in K.check_invariants(K.ll_kernel)]
    assert ids == [f"I{n}" for n in range(1, 10)]
