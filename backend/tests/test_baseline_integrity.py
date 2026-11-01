"""Benchmark baseline integrity gate (verification-debt regression tests).

These tests lock in three properties that were previously unenforced:

1. The reference dataset is *internally consistent* (Ct <-> Rt, Fr, physicality).
   Before this suite: 12/12 cases had a corrupt ``experimental_rt_n`` column
   (Ct-implied Rt off by 3.4x-9.9x) and 4/4 DTMB5415 cases carried a Froude
   number computed with an implied hull length of 181.2 m instead of 5.72 m.
2. The gate can FAIL — the audit is exercised against a deliberately corrupted
   copy of the dataset (PB-005: a gate that cannot fail is not a gate).
3. ``ship_proxy.BENCHMARK_REFERENCE`` derives R_T / GM instead of hard-coding
   them, and a case whose tolerance exceeds the 15% platform standard is not
   reported as a passing gate.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE / "tools" / "audit"))

import baseline_integrity as audit  # noqa: E402
from services.ship_proxy import (  # noqa: E402
    BENCHMARK_REFERENCE,
    POLICY_MAX_TOLERANCE_PCT,
    benchmark_validate,
    derive_rt_from_ct,
    wall_sided_gm,
)

DATASET = WORKTREE / "backend" / "data" / "benchmark_reference.json"


# --------------------------------------------------------------------------- #
# 1. baseline self-consistency (the audit gate itself)
# --------------------------------------------------------------------------- #
def test_repository_dataset_is_consistent():
    report = audit.run(DATASET)
    assert report["summary"]["verdict"] == "PASS", report["summary"]
    for case in report["case_results"]:
        c1 = case["checks"]["C1_ct_rt"]
        assert c1["status"] == "PASS", (case["case_id"], c1)
        assert c1["deviation_pct"] < 1.0  # derived columns reproduce exactly
        assert case["checks"]["C2_froude"]["status"] == "PASS", case["case_id"]
        assert case["checks"]["C4_physicality"]["status"] == "PASS", case["case_id"]


def test_audit_gate_can_fail_on_corrupt_rt(tmp_path):
    """Injected Rt corruption must turn the audit red."""
    data = json.loads(DATASET.read_text())
    data["cases"][0]["experimental_rt_n"] /= 6.0
    bad = tmp_path / "benchmark_reference.json"
    bad.write_text(json.dumps(data))
    report = audit.run(bad)
    assert report["summary"]["verdict"] == "FAIL"
    assert report["case_results"][0]["checks"]["C1_ct_rt"]["status"] == "FAIL"


def test_audit_gate_can_fail_on_wrong_froude(tmp_path):
    """The 181.2 m implied-length defect must be detectable."""
    data = json.loads(DATASET.read_text())
    dtmb = next(c for c in data["cases"] if c["case_id"] == "DTMB5415-01")
    dtmb["froude_number"] = 0.061  # as-listed (wrong L) value
    bad = tmp_path / "benchmark_reference.json"
    bad.write_text(json.dumps(data))
    report = audit.run(bad)
    c2 = next(c for c in report["case_results"]
              if c["case_id"] == "DTMB5415-01")["checks"]["C2_froude"]
    assert c2["status"] == "FAIL"
    assert abs(c2["implied_length_m"] - 181.2) < 1.0


def test_as_listed_values_preserved_for_traceability():
    """Corrections must not erase the historical values."""
    data = json.loads(DATASET.read_text())
    case = next(c for c in data["cases"] if c["case_id"] == "SUBOFF-01")
    assert case["experimental_rt_n_as_listed_20261031"] == pytest.approx(12.5)
    assert case["rt_provenance"].startswith("derived")
    dtmb = next(c for c in data["cases"] if c["case_id"] == "DTMB5415-01")
    assert dtmb["froude_number_as_listed_20261031"] == pytest.approx(0.061)


# --------------------------------------------------------------------------- #
# 2. gate store derives its references (no hand-copied numbers)
# --------------------------------------------------------------------------- #
def test_resistance_reference_is_ct_identity_derived():
    for name in ("kvlcc2_full", "suboff_model"):
        case = BENCHMARK_REFERENCE["resistance"][name]
        expected = derive_rt_from_ct(case["reference_ct"], case["fluid_density_kg_m3"],
                                     case["wetted_surface_m2"], case["speed_ms"])
        assert case["reference_rt"] == pytest.approx(expected, rel=1e-4)
        assert case["provenance"]


def test_kvlcc2_reference_matches_dataset_within_policy():
    """Gate store vs dataset must agree inside the 15% policy band."""
    report = audit.run(DATASET)
    for row in report["cross_store"]:
        assert row["status"] == "PASS", row
        assert row["deviation_pct"] <= POLICY_MAX_TOLERANCE_PCT


def test_stability_reference_is_analytic_wall_sided():
    """GM = KB + BM - KG; the old hand-copied 3.5 m was ~94% low."""
    barge = BENCHMARK_REFERENCE["stability"]["generic_barge"]
    p = barge["params"]
    gm = wall_sided_gm(p["length"], p["beam"], p["draft"], p["cb"], p["kg"])
    assert gm["kb"] == pytest.approx(2.0)
    assert gm["bm"] == pytest.approx(20.0 ** 2 / (12 * 4.0 * 0.85), rel=1e-9)
    assert gm["gm"] == pytest.approx(6.804, abs=1e-3)
    assert barge["reference_gm"] == pytest.approx(gm["gm"], abs=1e-3)


def test_no_gate_exceeds_policy_tolerance():
    """300% tolerance = gate that can never fail; it must not survive."""
    for calc_type, cases in BENCHMARK_REFERENCE.items():
        for name, spec in cases.items():
            assert spec["tolerance_pct"] <= POLICY_MAX_TOLERANCE_PCT, (calc_type, name)
    suboff = BENCHMARK_REFERENCE["resistance"]["suboff_model"]
    assert suboff["gate_enabled"] is False
    assert suboff["status"] == "honest_fail_not_a_gate"
    assert "honest-FAIL" in suboff["note"]


# --------------------------------------------------------------------------- #
# 3. benchmark_validate honesty (pass / fail / withheld)
# --------------------------------------------------------------------------- #
def _proxy_returning(payload):
    proxy = MagicMock()
    proxy.resistance = AsyncMock(return_value=payload)
    proxy.stability = AsyncMock(return_value=payload)
    return proxy


def test_validate_passes_on_reference_value():
    ref = BENCHMARK_REFERENCE["resistance"]["kvlcc2_full"]["reference_rt"]
    result = asyncio.run(benchmark_validate("resistance", "kvlcc2_full",
                                      proxy=_proxy_returning({"rt": ref})))
    assert result["status"] == "PASS" and result["pass"] is True
    assert result["error_pct"] == pytest.approx(0.0, abs=1e-6)


def test_validate_fails_on_20pct_garbage():
    """Gate must be able to fail: +20% error is outside the 15% policy."""
    ref = BENCHMARK_REFERENCE["resistance"]["kvlcc2_full"]["reference_rt"]
    result = asyncio.run(benchmark_validate("resistance", "kvlcc2_full",
                                      proxy=_proxy_returning({"rt": ref * 1.20})))
    assert result["status"] == "FAIL" and result["pass"] is False
    assert result["error_pct"] == pytest.approx(20.0, abs=0.1)


def test_validate_withholds_when_upstream_key_missing():
    """A silent 0 default would fake a 100% error; require WITHHELD instead."""
    result = asyncio.run(benchmark_validate("resistance", "kvlcc2_full",
                                      proxy=_proxy_returning({"method": "x"})))
    assert result["status"] == "WITHHELD"
    assert result["pass"] is None
    assert "missing" in result["error"]


def test_validate_withholds_for_non_gate_case():
    """SUBOFF is honest-FAIL, not a gate: no green light may be printed."""
    result = asyncio.run(benchmark_validate("resistance", "suboff_model",
                                      proxy=_proxy_returning({"rt": 15.0})))
    assert result["status"] == "WITHHELD"
    assert result["pass"] is None
    assert result["gate_enabled"] is False


def test_validate_reports_upstream_failure_honestly():
    from services.ship_proxy import ShipProxyError

    proxy = MagicMock()
    proxy.resistance = AsyncMock(side_effect=ShipProxyError("boom", status_code=502))
    result = asyncio.run(benchmark_validate("resistance", "kvlcc2_full", proxy=proxy))
    assert result["pass"] is False
    assert "boom" in result["error"]