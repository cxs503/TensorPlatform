"""Regression tests for the level-ice resistance cross-implementation audit (m1682).

These tests *pin the audit findings*.  They are derived from
``tools/ice_resistance_crosscheck.py`` and the analytical anchors documented in
``docs/reports/m1682_level_ice_resistance_crosscheck.md``:

* three level-ice resistance implementations coexist in the tree
  (``polar`` TP-POLAR-1, ``lindqvist`` Lindqvist-1989 raw, ``twin`` digital-twin
  B/L form) and they disagree by up to 1.99x at the same physical condition;
* the polar path is 39-54 % below the published full-scale trial means, the twin
  form 37-54 % below, the raw Lindqvist form -6.4 % / -8.9 % / -29.1 %;
* the twin breaking term scales as h^2 instead of the elastic-foundation h^1.25
  because it replaces the ice characteristic length l_c by the ship length L;
* the polar submersion term uses B*T*Cb instead of a rubble-transport contact
  length, which makes its equivalent ice-hull contact area 20-28x smaller than
  Lindqvist's.

Values are pinned to 0.1 pp so that any physics or constant change in the three
implementations fails loudly and forces the audit table to be re-derived.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tools"))

import ice_resistance_crosscheck as hx  # noqa: E402

IMPLS = hx.IMPLS
CASES = hx.trial_cases()


# ---------------------------------------------------------------------------
# adapters: the harness must measure the production code, not a re-implementation
# ---------------------------------------------------------------------------
def test_lindqvist_adapter_is_exact():
    """The `lindqvist` adapter calls the production function (1e-9 agreement)."""
    from services.ice_resistance_uq import SHIP_TRIAL_DATA, _lindqvist_raw

    for key, entry in SHIP_TRIAL_DATA.items():
        p = entry["params"]
        prod = _lindqvist_raw(
            p, entry["nominal_ice_thickness_m"], entry["nominal_ice_strength_MPa"],
            entry["nominal_friction_coeff"], entry["nominal_speed_ms"],
        ) * 1000.0
        case = next(c for c in CASES if c["key"] == key)
        adapter = hx.lindqvist_raw(case["ship"], case["v"], case["h"], case["sigma_mpa"])
        assert abs(adapter - prod) <= 1e-9 * prod


def test_lindqvist_component_split_sums_to_total():
    """The component split reproduces the production total to 0.1 % (g = 9.81 vs 9.80665)."""
    for c in CASES:
        comp = hx.lindqvist_raw_components(c["ship"], c["v"], c["h"], c["sigma_mpa"])
        total = hx.lindqvist_raw(c["ship"], c["v"], c["h"], c["sigma_mpa"])
        assert abs(comp["total"] - total) <= 1e-3 * total


# ---------------------------------------------------------------------------
# analytical anchors: thickness exponents
# ---------------------------------------------------------------------------
def test_analytic_breaking_exponent_is_1p25():
    """Beam on elastic foundation: R_b ~ h^2 / l_c with l_c ~ h^0.75 -> h^1.25."""
    assert hx.breaking_exponent() == pytest.approx(1.25, abs=1e-9)


def test_polar_breaking_exponent_matches_analytic():
    for c in CASES:
        r1 = hx.polar_components(c["ship"], c["v"], 0.4, c["sigma_mpa"])["breaking"]
        r2 = hx.polar_components(c["ship"], c["v"], 0.8, c["sigma_mpa"])["breaking"]
        n = math.log(r2 / r1) / math.log(2.0)
        assert n == pytest.approx(1.25, abs=1e-6)


def test_polar_submersion_exponent_carries_speed_correction():
    """polar submersion is h * (1 + C v/sqrt(gh)) -> local exponent 1 - 0.5*delta."""
    for c in CASES:
        n = hx.local_exponent(
            lambda s, v, h, sg: {"total": hx.polar_components(s, v, h, sg)["submersion"]},
            c["ship"], c["v"], 1.0, c["sigma_mpa"],
        )
        assert 0.75 <= n <= 0.95


def test_twin_breaking_exponent_is_two_defect_pin():
    """DEFECT PIN: the digital-twin breaking term scales as h^2, not h^1.25.

    ``services/digital_twin/models/resistance.py`` writes
    ``R_b = (27/64) sigma_b h^2 B / L`` -- L stands in for the ice characteristic
    length l_c, which removes the h^0.75 of l_c.  Downstream this makes the term
    18x too small at the MV_Arctic trial condition (P2 in the audit table).
    """
    for c in CASES:
        n = hx.local_exponent(
            lambda s, v, h, sg: {"total": hx.twin_components(s, v, h, sg)["breaking"]},
            c["ship"], c["v"], 0.6, c["sigma_mpa"],
        )
        assert n == pytest.approx(2.0, abs=1e-3)


# ---------------------------------------------------------------------------
# absolute level vs the published full-scale means (15 % platform gate)
# ---------------------------------------------------------------------------
PINNED_ERR = {
    # ship: (polar, lindqvist, twin) relative errors [%]
    "MV_Arctic": (-54.21, -8.88, -54.27),
    "Polarstern": (-38.97, -6.36, -36.62),
    "Terry_Fox": (-48.33, -29.08, -44.65),
}


def test_pinned_absolute_errors():
    rows = {r["ship"]: r for r in hx.probe_p1_absolute(CASES)}
    for ship, (ep, el, et) in PINNED_ERR.items():
        row = rows[ship]
        assert row["polar_err_pct"] == pytest.approx(ep, abs=0.1)
        assert row["lindqvist_err_pct"] == pytest.approx(el, abs=0.1)
        assert row["twin_err_pct"] == pytest.approx(et, abs=0.1)


@pytest.mark.parametrize("ship", ["MV_Arctic", "Polarstern", "Terry_Fox"])
def test_polar_and_twin_fail_the_15pct_gate(ship):
    """Honest audit result: the two production level-ice paths miss the 15 % gate."""
    row = next(r for r in hx.probe_p1_absolute(CASES) if r["ship"] == ship)
    assert abs(row["polar_err_pct"]) > 15.0
    assert abs(row["twin_err_pct"]) > 15.0


def test_lindqvist_passes_gate_on_two_ships():
    rows = {r["ship"]: r for r in hx.probe_p1_absolute(CASES)}
    assert abs(rows["MV_Arctic"]["lindqvist_err_pct"]) < 15.0
    assert abs(rows["Polarstern"]["lindqvist_err_pct"]) < 15.0


@pytest.mark.xfail(strict=True, reason=(
    "honest FAIL (m1682): raw Lindqvist is -29.1 % low on Terry_Fox (75 m, h=0.6 m). "
    "Two of three ships pass but the small-ship end of the envelope does not; the "
    "residual is a model-form error, not a constant offset, so no global factor fixes it."))
def test_lindqvist_terry_fox_inside_gate():
    row = next(r for r in hx.probe_p1_absolute(CASES) if r["ship"] == "Terry_Fox")
    assert abs(row["lindqvist_err_pct"]) < 15.0


# ---------------------------------------------------------------------------
# structural defects diagnosed by the audit
# ---------------------------------------------------------------------------
def test_inter_impl_spread_is_two_x():
    """Two production paths differ by a factor ~2 at the MV_Arctic trial point."""
    spread = {r["ship"]: r["spread_ratio"] for r in hx.probe_p7_spread(CASES)}
    assert spread["MV_Arctic"] == pytest.approx(1.99, abs=0.02)
    assert spread["Polarstern"] == pytest.approx(1.53, abs=0.02)
    assert spread["Terry_Fox"] == pytest.approx(1.37, abs=0.02)


def test_polar_submersion_contact_area_is_an_order_too_small():
    """DEFECT PIN: polar uses B*T*Cb instead of a rubble-transport contact length."""
    rows = {r["ship"]: r for r in hx.probe_p8_submersion_form(CASES)}
    for ship, row in rows.items():
        assert row["area_ratio_lindqvist_over_polar"] > 15.0, ship
        # force ratio is smaller than the area ratio because the two models apply
        # different velocity corrections to the submersion term (10-19x measured)
        assert row["submersion_ratio_lindqvist_over_polar"] > 10.0, ship
    # MV_Arctic: 28.1x in contact area -> 19.0x in the submersion force
    assert rows["MV_Arctic"]["area_ratio_lindqvist_over_polar"] == pytest.approx(28.1, abs=0.2)


def test_submersion_share_separates_the_two_model_families():
    rows = hx.probe_p2_components(CASES)
    polar = [r["submersion_share"] for r in rows if r["impl"] == "polar"]
    lind = [r["submersion_share"] for r in rows if r["impl"] == "lindqvist"]
    twin = [r["submersion_share"] for r in rows if r["impl"] == "twin"]
    assert max(polar) < 0.10
    assert min(lind) > 0.45
    assert min(twin) > 0.65


def test_length_sensitivity_artefact():
    """The twin ice term moves with the ship length through B/L; polar does not."""
    rows = hx.probe_p4_length_sensitivity(CASES)
    for row in rows:
        assert row["twin_spread_pct"] > 1.0
        assert row["polar_spread_pct"] < 1.0


def test_invariants_hold_for_all_implementations():
    rows = hx.probe_p5_invariants(CASES)
    for row in rows:
        assert row["non_negative"]
        assert row["monotone_in_h"]
        assert row["monotone_in_sigma"]
        assert row["small_h_finite"]


# ---------------------------------------------------------------------------
# committed artifact stays in sync with the harness
# ---------------------------------------------------------------------------
def test_committed_artifact_matches_harness_verdict():
    import json

    art = ROOT / "docs/reports/artifacts/m1682_ice_resistance_crosscheck.json"
    if not art.exists():
        pytest.skip("audit artifact not present")
    saved = json.loads(art.read_text())
    cases = hx.trial_cases()
    recomputed = hx.verdict({
        "P1_absolute": hx.probe_p1_absolute(cases),
        "P3_exponents": hx.probe_p3_exponents(cases),
        "P4_length_sensitivity": hx.probe_p4_length_sensitivity(cases),
        "P5_invariants": hx.probe_p5_invariants(cases),
    })
    assert recomputed["n_checks"] == saved["verdict"]["n_checks"]
    assert recomputed["n_pass"] == saved["verdict"]["n_pass"]
    assert recomputed["n_pass"] == 12  # documented in the report