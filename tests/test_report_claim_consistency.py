"""Acceptance tests for tools.audit.report_claim_consistency.

The gate must be shown to have discriminating power *before* its verdict is
trusted: a corpus with known labels (which (case, quantity, speed) groups are
really inconsistent) is run through it and every label must be recovered.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.audit.report_claim_consistency import (  # noqa: E402
    Claim,
    SPREAD_FLAG_PCT,
    audit_texts,
    extract_claims,
    group_claims,
    map_quantity,
    parse_markdown_tables,
)

# ---------------------------------------------------------------------------
# labelled synthetic corpus: {report_id: markdown}; ground truth below
# ---------------------------------------------------------------------------
CONSISTENT = """
| 船 | V [kn] | R_T [kN] | P_E [kW] |
|---|---|---|---|
| KCS | 15.0 | 1276.4 | 9848.6 |
"""

CONSISTENT_AGAIN = """
| 船 | V [kn] | R_T [kN] | P_E [kW] |
|---|---|---|---|
| KCS | 15.0 | 1280.1 | 9860.0 |
"""

INCONSISTENT_RT = """
| 船 | V [kn] | R_T [kN] |
|---|---|---|
| KCS | 15.0 | 1630.0 |
"""

INCONSISTENT_GM = """
| L | B | T | GM [m] | KM [m] |
|---|---|---|---|---|
| 100 | 20 | 8 | 9.167 | 12.167 |
"""

INCONSISTENT_GM_AGAIN = """
| L | B | T | GM [m] | KM [m] |
|---|---|---|---|---|
| 100 | 20 | 8 | 5.167 | 8.167 |
"""

DIFFERENT_CASES = """
| 船 | V [kn] | R_T [kN] |
|---|---|---|
| KVLCC2 | 15.5 | 3850.8 |
| JBC | 14.0 | 1276.4 |
"""

OTHER_SPEED = """
| 船 | V [kn] | R_T [kN] |
|---|---|---|
| KCS | 13.5 | 980.0 |
"""

NO_SPEED_COLUMN = """
| 船 | R_T [kN] |
|---|---|
| KCS | 1276.4 |
"""

SIDE_SPECIFIC_ONLY = """
| 船 | GM_ref [m] | GM_plat [m] | 基准 R_T [kN] | R_T 内核 [kN] |
|---|---|---|---|---|
| KCS | 5.167 | 9.167 | 1276.4 | 1630.0 |
"""

NO_TABLE = "正文里提到 KCS R_T = 1276.4 kN，但没有表格，不应被抽取。"

KEY_VALUE = """
| 关键量 | 实测 | 基准 |
|---|---|---|
| GM [m] | 5.167 | 5.166 |
"""

KEY_VALUE_NO_CASE = """
| 关键量 | 实测 |
|---|---|
| R_T [kN] | 1276.4 |
"""

CORPUS = {
    1: CONSISTENT,
    2: CONSISTENT_AGAIN,
    3: INCONSISTENT_RT,
    4: INCONSISTENT_GM,
    5: INCONSISTENT_GM_AGAIN,
    6: DIFFERENT_CASES,
    7: SIDE_SPECIFIC_ONLY,
    8: NO_TABLE,
    9: OTHER_SPEED,
    10: NO_SPEED_COLUMN,
}

#: groups that must be flagged as inconsistent
TRUTH_FLAGGED = {("KCS", "R_T", 15.0), ("GEOM:100x20x8", "GM", None),
                 ("GEOM:100x20x8", "KM", None)}
#: comparable groups that must stay unflagged (incl. the same case at another V)
TRUTH_CLEAN = {("KCS", "P_E", 15.0), ("KVLCC2", "R_T", 15.5),
               ("JBC", "R_T", 14.0), ("KCS", "R_T", 13.5)}


# ---------------------------------------------------------------------------
# header mapping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("header,expected", [
    ("R_T [kN]", "R_T"),
    ("GM [m]", "GM"),
    ("T33 [s]", "T33"),
    ("P_D [kW]", "P_D"),
    ("K_T", "K_T"),
])
def test_quantity_headers_map(header, expected):
    assert map_quantity(header) == expected


@pytest.mark.parametrize("header", [
    "GM_ref [m]", "GM_plat [m]", "R_T 基准 [kN]", "解析 T33 [s]",
    "R_T 端点 [kN]", "备注", "端点数",
])
def test_side_specific_headers_are_skipped(header):
    assert map_quantity(header) is None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_tables_are_found():
    tables = parse_markdown_tables(CONSISTENT)
    assert len(tables) == 1
    header, rows = tables[0]
    assert header[2] == "R_T [kN]"
    assert len(rows) == 1


def test_no_table_means_no_claims():
    assert extract_claims(NO_TABLE, 8) == []


def test_side_specific_table_yields_no_claims():
    assert extract_claims(SIDE_SPECIFIC_ONLY, 7) == []


def test_wide_table_claims_carry_case_value_unit_speed():
    got = {(c.case, c.quantity, c.value, c.unit, c.speed) for c in extract_claims(CONSISTENT, 1)}
    assert ("KCS", "R_T", 1276.4, "kN", 15.0) in got
    assert ("KCS", "P_E", 9848.6, "kW", 15.0) in got


def test_key_value_table_uses_the_title_for_the_case():
    claims = extract_claims(KEY_VALUE, 1, "KCS 稳性复核")
    assert [(c.case, c.quantity, c.value) for c in claims] == [("KCS", "GM", 5.167)]


def test_key_value_rows_without_case_are_dropped():
    assert extract_claims(KEY_VALUE_NO_CASE, 1) == []


def test_misaligned_rows_are_dropped():
    md = """
| 船 | R_T [kN] | 备注 |
|---|---|---|
| KCS | 1276.4 |
"""
    assert extract_claims(md, 1) == []


def test_geometry_signature_is_the_fallback_case_key():
    assert {c.case for c in extract_claims(INCONSISTENT_GM, 4)} == {"GEOM:100x20x8"}


def test_unknown_operating_point_is_tier_b():
    # the claim is kept, but only a coarse magnitude disagreement (>50%) flags it
    claims = extract_claims(NO_SPEED_COLUMN, 10)
    assert claims and claims[0].speed is None
    assert not extract_claims(NO_SPEED_COLUMN, 10) == []


def test_tier_b_needs_a_large_magnitude_gap_to_flag():
    small = [Claim(1, "KCS", "R_T", 1276.4, "kN"), Claim(2, "KCS", "R_T", 1400.0, "kN")]
    assert not group_claims(small)[0].flagged       # 9.2% < 50% at unknown speed
    big = [Claim(1, "KCS", "R_T", 1276.4, "kN"), Claim(2, "KCS", "R_T", 3880.0, "kN")]
    g = group_claims(big)[0]
    assert g.flagged and g.tier == "B"


def test_geometry_independent_claims_do_not_need_speed():
    # GM is not speed dependent, so the geometry-keyed GM/KM claims survive
    qs = {c.quantity for c in extract_claims(INCONSISTENT_GM, 4)}
    assert qs == {"GM", "KM"}


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------
def test_single_report_values_are_never_flagged():
    claims = [Claim(1, "KCS", "GM", 1.0, "m"), Claim(1, "KCS", "GM", 1.4, "m")]
    (g,) = group_claims(claims)
    assert g.spread_pct == pytest.approx(33.33, abs=0.01)
    assert not g.flagged  # one report quoting a sweep is not a cross-report defect


def test_flagged_requires_both_spread_and_two_reports():
    claims = [Claim(1, "KCS", "GM", 1.000, "m"), Claim(2, "KCS", "GM", 1.002, "m")]
    (g,) = group_claims(claims)
    assert g.spread_pct == pytest.approx(0.2, abs=0.01)
    assert not g.flagged


def test_speed_is_part_of_the_group_key():
    claims = [Claim(1, "KCS", "R_T", 1276.4, "kN", 15.0),
              Claim(2, "KCS", "R_T", 980.0, "kN", 13.5)]
    groups = group_claims(claims)
    assert len(groups) == 2
    assert not any(g.flagged for g in groups)


# ---------------------------------------------------------------------------
# end-to-end discrimination on the labelled corpus
# ---------------------------------------------------------------------------
def test_every_true_inconsistency_is_caught():
    _claims, groups = audit_texts(CORPUS)
    flagged = {(g.case, g.quantity, g.speed) for g in groups if g.flagged}
    assert TRUTH_FLAGGED <= flagged, "missed: %s" % (TRUTH_FLAGGED - flagged)


def test_clean_groups_are_not_flagged():
    _claims, groups = audit_texts(CORPUS)
    flagged = {(g.case, g.quantity, g.speed) for g in groups if g.flagged}
    assert not (TRUTH_CLEAN & flagged), "false alarm: %s" % (TRUTH_CLEAN & flagged)


def test_case_separation_prevents_false_merges():
    _claims, groups = audit_texts(CORPUS)
    for g in groups:
        if g.quantity == "R_T" and g.speed in (14.0, 15.5):
            assert len(g.values) == 1


def test_threshold_is_explicit_and_configurable():
    claims = [Claim(1, "KCS", "R_T", 1000.0, "kN", 15.0), Claim(2, "KCS", "R_T", 1060.0, "kN", 15.0)]
    assert group_claims(claims, 5.0)[0].flagged
    assert not group_claims(claims, 10.0)[0].flagged
    assert SPREAD_FLAG_PCT == 5.0


def test_flagged_groups_are_sorted_by_spread():
    _claims, groups = audit_texts(CORPUS)
    spreads = [g.spread_pct for g in groups]
    assert spreads == sorted(spreads, reverse=True)


def test_every_claim_keeps_a_provenance_snippet():
    claims = extract_claims(CONSISTENT, 1)
    assert claims and all(c.row for c in claims)