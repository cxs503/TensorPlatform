"""Behavioural contract for an external regression module (loaded by path).

The module is pinned by sha256 in the mutation ledger.  Assertions are
behavioural pins: numeric outputs at declared inputs, branch selection, alias
equivalence, non-negativity and derived-property algebra.  The suite is
domain-blind (no production quantity, unit or case name is asserted); inputs
are synthetic samples.

One public entry point is deliberately *not* executed: it imports a sibling
module relatively, which a by-path loader cannot resolve.  Mutants inside it
therefore survive and are reported honestly in the mutation ledger.
"""

from __future__ import annotations

import importlib.util
import math
import os
import pathlib

import numpy as np
import pytest

_MODULE_ENV = os.environ.get("GM_TARGET_MODULE")
if not _MODULE_ENV:  # contract suites only run inside a mutation sandbox
    pytest.skip("GM_TARGET_MODULE not set", allow_module_level=True)
MODULE_PATH = pathlib.Path(_MODULE_ENV).resolve()
_SPEC = importlib.util.spec_from_file_location("gm_target_under_test", MODULE_PATH)
MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
import sys as _sys
_sys.modules["gm_target_under_test"] = MOD  # dataclasses need the module registered
_SPEC.loader.exec_module(MOD)

FR_GRID = (0.1, 0.2, 0.25, 0.3, 0.35)
PINNED = {
    0.6: (1.24e-05, 4.84e-05, 6.015625e-05, 6.94e-05, 7.718125e-05),
    0.7: (4.788e-05, 9.208e-05, 0.0001059375, 0.00011628, 0.0001243675),
    0.8: (9.336e-05, 0.00014576, 0.00016171875, 0.00017316, 0.00018155375),
}
PINNED_EXP = {
    0.6: (0.001171336455, 0.00245436025, 0.002952290955, 0.003340310608, 0.003622954375),
    0.7: (0.002106180488, 0.004200830167, 0.004991233814, 0.0056009337, 0.006042055495),
    0.8: (0.00351292509, 0.006736499205, 0.007945669004, 0.008887493153, 0.009583481204),
}


@pytest.mark.parametrize("bad_fr", [0.0, -0.1, -1.0])
def test_non_positive_input_returns_zero(bad_fr):
    for cb in (0.6, 0.7, 0.8):
        assert MOD.series60_Cw_todd(bad_fr, cb) == 0.0
        assert MOD.series60_Cw_todd_1963(bad_fr, cb) == 0.0


@pytest.mark.parametrize("cb", [0.6, 0.7, 0.8])
def test_pinned_polynomial_branch(cb):
    got = [MOD.series60_Cw_todd(fr, cb) for fr in FR_GRID]
    assert got == pytest.approx(list(PINNED[cb]), rel=1e-9)


@pytest.mark.parametrize("cb", [0.6, 0.7, 0.8])
def test_pinned_exponential_branch(cb):
    got = [MOD.series60_Cw_todd_1963(fr, cb) for fr in FR_GRID]
    assert got == pytest.approx(list(PINNED_EXP[cb]), rel=1e-9)


def test_alias_matches_primary():
    for cb in (0.6, 0.7, 0.8, 0.65):
        for fr in FR_GRID:
            assert MOD.series60_Cw(fr, cb) == MOD.series60_Cw_todd(fr, cb)


def test_branch_band_selection():
    # |Cb - 0.6| < 0.05 falls into the first branch
    assert MOD.series60_Cw_todd(0.2, 0.64) == pytest.approx(
        MOD.series60_Cw_todd(0.2, 0.6), rel=1e-12
    )
    # |Cb - 0.8| < 0.05 falls into the third branch
    assert MOD.series60_Cw_todd(0.2, 0.76) == pytest.approx(
        MOD.series60_Cw_todd(0.2, 0.8), rel=1e-12
    )
    # a value in no band exercises the interpolation path
    interp = MOD.series60_Cw_todd(0.2, 0.65)
    assert 0.0 < interp < 1.0


def test_output_is_non_negative_and_finite():
    for cb in np.linspace(0.6, 0.8, 5):
        for fr in np.linspace(0.02, 0.5, 7):
            for fn in (MOD.series60_Cw_todd, MOD.series60_Cw_todd_1963):
                value = fn(float(fr), float(cb))
                assert math.isfinite(value)
                assert value >= 0.0


def test_derived_properties_algebra():
    L, B, T, Cb = 6.0, 0.8, 0.35, 0.7
    props = MOD.series60_hull_properties(L, B, T, Cb)
    assert props["volume"] == pytest.approx(Cb * L * B * T, rel=1e-12)
    factor = 0.453 + 0.442 * Cb - 0.295 * 0.56
    assert props["S_wet"] == pytest.approx(L * (2.0 * T + B) * factor, rel=1e-12)
    assert props["L"] == L and props["B"] == B and props["T"] == T and props["Cb"] == Cb


def test_reference_data_grid_and_values():
    rows = MOD.series60_reference_data(0.7)
    assert len(rows) == 26
    assert rows[0]["Fr"] == pytest.approx(0.1, rel=1e-12)
    assert rows[-1]["Fr"] == pytest.approx(0.35, rel=1e-12)
    assert rows[0]["Cw_todd"] == pytest.approx(PINNED[0.7][0], rel=1e-9)
    assert rows[-1]["Cw_todd"] == pytest.approx(PINNED[0.7][-1], rel=1e-9)
    custom = MOD.series60_reference_data(0.6, np.array([0.15, 0.3]))
    assert [row["Fr"] for row in custom] == pytest.approx([0.15, 0.3], rel=1e-12)
    assert custom[0]["Cb"] == 0.6


def test_validation_entry_point_is_exposed():
    assert callable(getattr(MOD, "series60_validation"))

# --------------------------------------------------------------------------- #
# band boundaries (added after the mutation ledger exposed boundary-blindness)
# --------------------------------------------------------------------------- #
def _interpolated_cw(fr: float, cb: float) -> float:
    """Independent re-implementation of the declared interpolation branch."""
    c1 = (-0.5, 8.0, -20.0, 25.0, -10.0)
    c2 = (0.0, 12.0, -30.0, 35.0, -14.0)
    t = (cb - 0.6) / 0.2
    coeffs = [a + t * (b - a) for a, b in zip(c1, c2)]
    value = sum(c * fr ** i for i, c in enumerate(coeffs)) * 1e-4
    return max(value, 0.0)


def test_interpolation_branch_below_the_first_band():
    # Cb = 0.5 is outside every band: the interpolation branch must be used
    value = MOD.series60_Cw_todd(0.2, 0.5)
    assert value == pytest.approx(_interpolated_cw(0.2, 0.5), rel=1e-12)
    assert value != pytest.approx(MOD.series60_Cw_todd(0.2, 0.6), rel=1e-12)


def test_branch_band_inside_boundary_uses_band():
    # |Cb - 0.7| = 0.049... is still inside the band
    assert MOD.series60_Cw_todd(0.2, 0.65) == pytest.approx(
        MOD.series60_Cw_todd(0.2, 0.7), rel=1e-12)


def test_zero_floor_boundary():
    # far outside the fitted range the polynomial goes negative; the floor must clamp at 0.0
    assert MOD.series60_Cw_todd(10.0, 0.6) == 0.0
