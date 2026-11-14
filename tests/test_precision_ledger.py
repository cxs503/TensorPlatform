"""Tests for the KPI-GATE-100 precision ledger.

Coverage:
  * scan/normalisation (alias mapping, computed-only rule, mirror de-duplication)
  * baseline adjudication against the canonical dataset (legacy Rt superseded,
    canonical WITHHELD honoured)
  * three gate bases for a banded quantity + 15% / 3% boundary behaviour
  * WITHHELD / STUB / NOT_SUPPORTED never PASS
  * rel_err recompute consistency for every ledger entry
  * the machine gate accepts a clean ledger and rejects tampered ones
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precision_ledger import (ADOPTED_GATE_BASIS, TOL_HARD, TOL_TARGET, Baseline,
                              Entry, build_ledger, check_ledger, gate_adjudication,
                              scan_all)
from precision_ledger.model import REL_ERR_TOL


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ledger():
    return build_ledger(ROOT)


@pytest.fixture(scope="module")
def entries(ledger):
    return ledger["entries"]


def _entry(computed, value, low=None, high=None, grade="REAL"):
    e = Entry(
        entry_id="t|CASE-1|Ct",
        case_id="CASE-1",
        quantity="Ct",
        computed=computed,
        baseline=Baseline(value=value, low=low, high=high, source="test",
                          evidence_grade=grade),
        evidence_grade=grade,
        source_file="test.json",
        adapter="unit",
        family="test",
    )
    return e


# --------------------------------------------------------------------------
# 1. scan & normalisation
# --------------------------------------------------------------------------
def test_scan_produces_at_least_15_entries(entries):
    assert len(entries) >= 15


def test_scan_requires_a_numeric_computed_value(ledger):
    """Baseline-only declarations (reference datasets) are not comparisons.

    An entry without a numeric result may only exist as an explicitly aggregated
    STUB/WITHHELD row (a whole batch declared no result / no baseline), never as a
    first-class comparison.
    """
    for e in ledger["entries"]:
        if e["computed"] is None:
            assert e["evidence_grade"] in ("STUB", "WITHHELD")
            assert e["pass_15pct"] is False and e["pass_3pct"] is False
    sources = {e["source_file"] for e in ledger["entries"]}
    # the canonical reference datasets only declare baselines -> must not appear
    assert "backend/data/benchmark_reference.json" not in sources
    assert "backend/data/benchmark_reference_sources_v2.json" not in sources


def test_aliases_are_normalised(ledger):
    """`calc_ct`/`Ct_exp` and `Ct_calc`/`Ct_exp` land in the same quantity bucket."""
    qty = {e["quantity"] for e in ledger["entries"]}
    assert {"Ct", "Rt"} <= qty
    sub = [e for e in ledger["entries"] if e["case_id"] == "SUBOFF-01" and e["quantity"] == "Ct"]
    assert sub, "SUBOFF-01 Ct comparison must be found"
    assert any(abs(e["computed"] - 0.003462) < 1e-9 for e in sub)


def test_mirror_copies_are_collapsed(ledger):
    """Byte-identical evidence copies are one entry, the rest goes to `mirrors`."""
    assert ledger["totals"]["mirror_copies_collapsed"] > 0
    mirrored = [e for e in ledger["entries"] if e["mirrors"]]
    assert mirrored
    for e in mirrored:
        for m in e["mirrors"]:
            assert m != e["source_file"]
    # backend/artifacts/ is a copy of artifacts/ -> must not be a primary source
    primary = {e["source_file"] for e in ledger["entries"]}
    assert not any(p.startswith("backend/artifacts/") for p in primary)


def test_entry_ids_unique_and_sorted(ledger):
    ids = [e["entry_id"] for e in ledger["entries"]]
    assert len(ids) == len(set(ids))


def test_scan_is_deterministic(ledger):
    again = build_ledger(ROOT)
    assert [(e["entry_id"], e["computed"], e["evidence_grade"]) for e in again["entries"]] == \
           [(e["entry_id"], e["computed"], e["evidence_grade"]) for e in ledger["entries"]]


def test_capability_audit_adapter_takes_outcome_quantities_only(entries):
    """The capability-audit adapter records Ct/RT (outcome), not components or identities."""
    audit = [e for e in entries if e["adapter"] == "capability_audit"]
    assert audit, "docs/m1536 capability audit must be normalised"
    assert {e["quantity"] for e in audit} == {"Ct", "RT"}
    for e in audit:
        assert "@" in e["case_id"], "case id must come from the enclosing case dict"
        assert e["baseline"]["kind"] == "reference_implementation"
        assert e["evidence_grade"] == "REAL"
        assert "rel_err_pct" in e["note"]  # recomputation cross-check recorded


def test_capability_audit_recomputes_the_source_error(entries):
    """Stored rel_err must agree with the error the source itself reported."""
    for e in entries:
        if e["adapter"] != "capability_audit":
            continue
        assert abs(e["rel_err"]["mid"] - (e["computed"] - e["baseline"]["value"])
                   / e["baseline"]["value"]) <= REL_ERR_TOL


# --------------------------------------------------------------------------
# 2. baseline adjudication
# --------------------------------------------------------------------------
def test_superoff_rt_legacy_baseline_is_readjudicated(ledger):
    """SUBOFF Rt comparisons use the canonical R1-derived force, not legacy 12.5 N."""
    rt = [e for e in ledger["entries"] if e["case_id"].startswith("SUBOFF") and e["quantity"] == "Rt"]
    assert rt
    for e in rt:
        assert e["baseline"]["value"] is not None
        assert e["baseline"]["value"] > 50.0, "legacy 12.5 N must not survive as a baseline"
    assert any(e["ref_as_recorded"] is not None and e["ref_as_recorded"] < 50.0 for e in rt), \
        "the superseded legacy value must still be recorded for provenance"


def test_canonical_withheld_rt_never_judged(ledger):
    """DTMB-5415 / KVLCC2 Rt baselines are WITHHELD in the canonical dataset."""
    rt = [e for e in ledger["entries"]
          if e["quantity"] == "Rt" and e["case_id"].split("-")[0] in ("DTMB5415", "KVLCC2")]
    assert rt
    for e in rt:
        assert e["evidence_grade"] == "WITHHELD"
        assert e["pass_15pct"] is False and e["pass_3pct"] is False
        assert "WITHHELD" in e["withheld_reason"] or "rt_status" in e["withheld_reason"]


def test_declared_band_is_applied_to_suboff_ct(ledger):
    """SUBOFF-01 Ct uses the declared 0.0035-0.0040 band, judged at the midpoint."""
    e = next(x for x in ledger["entries"]
             if x["case_id"] == "SUBOFF-01" and x["quantity"] == "Ct"
             and x["evidence_grade"] == "REAL")
    b = e["baseline"]
    assert (b["low"], b["high"]) == (0.0035, 0.0040)
    assert b["value"] == pytest.approx(0.00375)
    assert e["rel_err"]["low"] == pytest.approx((e["computed"] - 0.0035) / 0.0035)
    assert e["rel_err"]["high"] == pytest.approx((e["computed"] - 0.0040) / 0.0040)


# --------------------------------------------------------------------------
# 3. gate semantics: three bases, boundaries, withheld invariants
# --------------------------------------------------------------------------
def test_three_gate_bases_disagree_on_a_banded_quantity():
    """Inside-band but 6.4% from the midpoint: the three bases give different verdicts.

    midpoint   : 15% PASS, 3% FAIL (6.4% > 3%)
    containment: 15% PASS, 3% PASS  <- the band absorbs the 6.4% deviation
    endpoints  : 15% PASS, 3% FAIL (needs closeness to both endpoints)
    """
    e = _entry(computed=0.00351, value=None, low=0.0035, high=0.0040)
    e.baseline.value = 0.00375
    rel = e.rel_err()
    assert rel["mid"] == pytest.approx((0.00351 - 0.00375) / 0.00375, abs=1e-12)
    assert e.gate(TOL_HARD, "mid") is True
    assert e.gate(TOL_TARGET, "mid") is False
    assert e.band_contained() is True
    assert e.gate(TOL_HARD, "containment") is True
    assert e.gate(TOL_TARGET, "containment") is True   # <- the flaw of containment
    assert e.gate(TOL_HARD, "endpoints") is True
    assert e.gate(TOL_TARGET, "endpoints") is False


def test_band_midpoint_anchor_numbers():
    """The adjudication anchor: 0.003393 vs [0.0035, 0.0040] -> 9.52% at the midpoint."""
    adj = gate_adjudication()
    assert adj["adopted"] == ADOPTED_GATE_BASIS == "mid"
    assert set(adj["options"]) == {"endpoints", "mid", "containment"}
    mid = adj["options"]["mid"]
    assert mid["rel_err_mid"] == pytest.approx((0.003393 - 0.00375) / 0.00375, abs=1e-12)
    assert mid["statistic"] == pytest.approx(0.0952, abs=5e-4)
    assert mid["pass_15pct"] is True and mid["pass_3pct"] is False
    # the point sits below the band -> containment cannot pass
    assert adj["options"]["containment"]["contained"] is False


@pytest.mark.parametrize("tol,ratio,expected", [
    (TOL_HARD, 1.0, True),      # exactly 15% passes (<=) -- float-rounding guarded
    (TOL_HARD, 1.000001, False),
    (TOL_TARGET, 1.0, True),    # exactly 3% passes
    (TOL_TARGET, 1.000001, False),
    (TOL_HARD, 0.0, True),
])
def test_gate_boundaries(tol, ratio, expected):
    e = _entry(computed=1.0 + tol * ratio, value=1.0)
    assert e.gate(tol) is expected


def test_withheld_entry_never_passes():
    e = Entry(
        entry_id="w", case_id="CASE-2", quantity="Rt", computed=10.0,
        baseline=Baseline(value=None, low=None, high=None, source="none",
                          evidence_grade="WITHHELD"),
        evidence_grade="WITHHELD", source_file="test.json", adapter="unit",
        withheld_reason="no baseline",
    )
    assert e.judged is False
    assert e.gate(TOL_HARD) is False
    assert e.gate(TOL_TARGET) is False
    d = e.as_dict()
    assert d["pass_15pct"] is False and d["pass_3pct"] is False


def test_missing_baseline_becomes_withheld_with_reason(tmp_path):
    """A raw record with no resolvable baseline must be WITHHELD and explained."""
    from precision_ledger.sources import RawRecord
    from precision_ledger.ledger import build_entries

    rec = RawRecord(source_file="synthetic.json", adapter="unit", family="synthetic",
                    case_id="UNKNOWN-99", quantity="Ct", computed=0.123,
                    ref_recorded=None)
    built = build_entries(ROOT, records=[rec])
    assert len(built) == 1
    e = built[0].as_dict()
    assert e["evidence_grade"] == "WITHHELD"
    assert e["pass_15pct"] is False
    assert e["withheld_reason"]


def test_self_referential_comparison_is_mock(entries):
    mock = [e for e in entries if e["evidence_grade"] == "MOCK"]
    assert mock, "the verification-oracle report is expected to be MOCK"
    for e in mock:
        assert e["self_referential"] is True
        assert e["computed"] == e["baseline"]["value"]
        assert e["rel_err"]["mid"] == 0.0


def test_non_judgeable_grades_never_pass(entries):
    for e in entries:
        if e["evidence_grade"] in ("WITHHELD", "STUB", "NOT_SUPPORTED"):
            assert e["pass_15pct"] is False and e["pass_3pct"] is False
            if e["evidence_grade"] == "WITHHELD":
                assert e["withheld_reason"].strip()


# --------------------------------------------------------------------------
# 4. recompute consistency & machine gate
# --------------------------------------------------------------------------
def test_rel_err_recompute_consistency(entries):
    for e in entries:
        c, b = e["computed"], e["baseline"]
        for key, ref in (("low", b["low"]), ("mid", b["value"]), ("high", b["high"])):
            stored = e["rel_err"][key]
            if c is None or ref is None or ref == 0:
                assert stored is None
                continue
            assert stored == pytest.approx((c - ref) / ref, abs=REL_ERR_TOL)


def test_summary_counts_match_entries(ledger):
    t, g = ledger["totals"], ledger["gate_results"]
    assert t["entries"] == len(ledger["entries"])
    assert sum(t["by_grade"].values()) == t["entries"]
    assert g["pass_15pct"] == sum(1 for e in ledger["entries"] if e["pass_15pct"])
    assert g["pass_3pct"] == sum(1 for e in ledger["entries"] if e["pass_3pct"])
    assert g["denominator"] == sum(1 for e in ledger["entries"] if e["judged"])
    assert g["violations"] == sum(1 for e in ledger["entries"]
                                 if e["judged"] and not e["pass_15pct"])


def test_check_accepts_clean_ledger(ledger):
    ok, errs = check_ledger(ledger)
    assert ok, errs


def test_check_rejects_tampered_rel_err(ledger):
    bad = copy.deepcopy(ledger)
    e = next(x for x in bad["entries"] if x["computed"] is not None and x["rel_err"]["mid"])
    e["rel_err"]["mid"] *= 0.5
    ok, errs = check_ledger(bad)
    assert not ok
    assert any("rel_err.mid" in m for m in errs)


def test_check_rejects_withheld_that_passes(ledger):
    bad = copy.deepcopy(ledger)
    e = next(x for x in bad["entries"] if x["evidence_grade"] == "WITHHELD")
    e["pass_15pct"] = True
    ok, errs = check_ledger(bad)
    assert not ok
    assert any("PASS" in m or "pass_15pct" in m for m in errs)


def test_check_rejects_missing_field(ledger):
    bad = copy.deepcopy(ledger)
    del bad["entries"][0]["baseline"]["source"]
    ok, errs = check_ledger(bad)
    assert not ok
    assert any("baseline missing field" in m for m in errs)


# --------------------------------------------------------------------------
# 5. CLI end to end
# --------------------------------------------------------------------------
def test_cli_scan_and_check(tmp_path):
    out = tmp_path / "ledger.json"
    docs = tmp_path / "docs"
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_precision_ledger.py"),
         "--scan", str(ROOT), "--out", str(out), "--docs", str(docs), "--no-figures", "--quiet"],
        capture_output=True, text=True, timeout=900,
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["entries"]) >= 15
    assert (docs / "REPORT.md").exists()
    assert (out.parent / "summary.md").exists()
    q = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_precision_ledger.py"), "--check", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert q.returncode == 0, q.stderr
    # tamper -> non-zero exit
    data["entries"][0]["pass_15pct"] = not data["entries"][0]["pass_15pct"]
    out.write_text(json.dumps(data), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_precision_ledger.py"), "--check", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 1