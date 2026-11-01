"""m1643 — CII reference lines (MEPC.353(78) G1) verified against the published table.

Why this file exists
--------------------
``backend/tests/polar/test_polar_physics.py::test_criterion_d_cii_matches_imo_guideline_paths``
re-checks the reduction-factor schedule Z and the rating boundaries d1..d4 against
hand-copied expected values and compares the mass path with the energy path.  All of
that is **self-consistency**; the G1 reference-line coefficients ``(a, c)`` -- the
single most consequential table of the regulation -- had no external anchor at all.

That gap hid a real defect: ``constants.CII_REFERENCE_LINES["general_cargo"]`` shipped
``c = -1.430`` instead of ``0.792``, which under-stated CII_ref by a factor ~555 at
20 000 DWT and therefore rated *every* general cargo ship E (attained/required ~408).
Fixed in m1643; the assertions below make the table externally anchored so the same
class of defect cannot recur silently.

Reference: IMO Res. MEPC.353(78) (2022 Guidelines on the CII reference lines, G1),
Annex Table 1, ``CII_ref = a * capacity^(-c)``.

Run (no DB needed):

    PYTHONPATH=backend python3 -m pytest backend/tests/polar/test_cii_reference_lines_m1643.py --noconftest -q
"""
from __future__ import annotations

import pytest

from services.polar import cii as C
from services.polar import constants as K

# ---------------------------------------------------------------------------
# Independent copy of the published G1 table.  ``c`` is stored as printed by the
# guideline (positive), i.e. CII_ref = a * capacity**(-c).
# ---------------------------------------------------------------------------
PUBLISHED_G1 = {
    "bulk_carrier": (4745.0, 0.622),
    "gas_carrier_large": (14405.0e7, 2.071),
    "gas_carrier_small": (8104.0, 0.639),
    "tanker": (5247.0, 0.610),
    "container_ship": (1984.0, 0.489),
    "general_cargo": (31948.0, 0.792),
    "refrigerated_cargo": (4600.0, 0.557),
    "combination_carrier": (5119.0, 0.622),
}

#: Capacity anchors per category (the ranges over which G1 applies that line).
ANCHOR_CAPACITIES = {
    "bulk_carrier": (20000.0, 50000.0, 200000.0),
    "tanker": (20000.0, 100000.0, 300000.0),
    "container_ship": (20000.0, 50000.0, 200000.0),
    "general_cargo": (5000.0, 20000.0, 50000.0),
    "refrigerated_cargo": (5000.0, 10000.0, 20000.0),
    "combination_carrier": (20000.0, 50000.0, 150000.0),
    "gas_carrier_small": (10000.0, 30000.0, 60000.0),
    "gas_carrier_large": (70000.0, 100000.0, 200000.0),
}


def test_platform_table_equals_published_g1_table():
    """Every supported (a, c) row must be bit-for-bit the guideline's."""
    assert set(K.CII_REFERENCE_LINES) == set(PUBLISHED_G1)
    for cat, (a_ref, c_ref) in PUBLISHED_G1.items():
        line = K.CII_REFERENCE_LINES[cat]
        assert line["a"] == pytest.approx(a_ref, rel=1e-12), cat
        # the platform stores the exponent's sign in ``c``
        assert -line["c"] == pytest.approx(c_ref, rel=1e-12), cat


def test_cii_reference_values_match_independent_recomputation():
    """CII_ref at the anchor capacities, vs the published table recomputed here."""
    n = 0
    for cat, dwts in ANCHOR_CAPACITIES.items():
        a_ref, c_ref = PUBLISHED_G1[cat]
        for dwt in dwts:
            ref = a_ref * dwt ** (-c_ref)
            got = C.cii_reference_value(cat, dwt)
            assert abs(got - ref) / ref * 100.0 < 1e-9, (cat, dwt, got, ref)
            n += 1
    assert n == 24


def test_general_cargo_exponent_defect_is_gone():
    """Regression: the pre-fix ``c = -1.430`` is rejected by the published value.

    With -1.430 the 20 000 DWT reference line was 0.0225 g/(dwt nm), i.e. a factor
    ~555 below the guideline; the correct value is 12.53.
    """
    bad = K.CII_REFERENCE_LINES["general_cargo"]["a"] * 20000.0 ** -1.430
    good = C.cii_reference_value("general_cargo", 20000.0)
    assert bad / good == pytest.approx(1.0 / 555.0, rel=0.02)
    assert 5.0 < good < 60.0
    assert K.CII_REFERENCE_LINES["general_cargo"]["c"] == pytest.approx(-0.792)


def test_coefficient_free_invariants_pass():
    """Magnitude band + monotone decrease + cross-type ordering, no document needed."""
    inv = C.cii_reference_line_invariants()
    assert inv["pass"] is True, inv["failed"]
    assert inv["n_failed"] == 0
    # the invariant set genuinely bites: it must reject the pre-fix exponent
    bad = K.CII_REFERENCE_LINES["general_cargo"]["a"] * 20000.0 ** -1.430
    assert not (C.CII_REF_BAND[0] <= bad <= C.CII_REF_BAND[1])
    assert bad < C.cii_reference_value("bulk_carrier", 20000.0)  # order violated


def test_unimplemented_g1_categories_are_not_silently_guessed():
    """G1 ship types without verified coefficients must raise NOT_SUPPORTED."""
    for cat in K.CII_REFERENCE_LINES_NOT_SUPPORTED:
        with pytest.raises(NotImplementedError):
            C.cii_reference_value(cat, 100000.0)


def _gc_voyage(attained_reference: float) -> dict:
    """Representative 20 000 DWT general cargo voyage (3000 nm @ 14 kn, 85 % MCR)."""
    dwt, pb, sfoc, v, dist = 20000.0, 4200.0, 195.0, 14.0, 3000.0
    hours = dist / v
    p_me, p_ae = 0.85 * pb, 0.05 * pb
    fuel_t = (p_me + p_ae) * sfoc * hours / 1.0e6
    voy = C.VoyageEfficiency(
        fuel_t=fuel_t,
        fuel_type="heavy_fuel_oil",
        distance_nm=dist,
        capacity=dwt,
        category="general_cargo",
        brake_energy_mj=(p_me + p_ae) * hours * C.MJ_PER_KWH,
        sfoc_g_kwh=sfoc,
    )
    return voy.as_dict(year=2026)


def test_general_cargo_rating_is_discriminating():
    """The rating must respond to the ship, not saturate at E for every vessel.

    Pre-fix this voyage sat at attained/required = 407.7 (grade E) purely because
    the reference line was three orders of magnitude too small.  Post-fix the same
    hull lands inside the A..C band, i.e. the rating discriminates again.
    """
    row = _gc_voyage(0.0)
    attained = row["attained_cii"]
    req = C.required_cii("general_cargo", 20000.0, 2026)
    assert 5.0 < req["required_cii"] < 60.0
    rating = C.cii_rating(attained, req["required_cii"])
    assert 0.3 < rating["attained_over_required"] < 2.0
    assert rating["rating"] in {"A", "B", "C"}
    # and the pre-fix reference line would indeed have produced the E saturation
    bad_ref = K.CII_REFERENCE_LINES["general_cargo"]["a"] * 20000.0 ** -1.430
    bad_req = bad_ref * (1.0 - K.CII_REDUCTION_FACTOR[2026] / 100.0)
    assert C.cii_rating(attained, bad_req)["rating"] == "E"
    assert C.cii_rating(attained, bad_req)["attained_over_required"] > 100.0