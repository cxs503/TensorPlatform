"""Campaign-instance tests for the p106w2 cross-solver interoperation gate.

These run against the **committed snapshots** of the sibling node artifacts
(``data/campaigns/p106w2/raw``), so the verdict of the integration gate is
reproducible from the repository alone -- no ecosystem checkout, no live
solver, no hand-typed number.  What is asserted here is the *verdict shape and
its fracture points*, not a green gate: the honest answer for this campaign is
FAIL and the test would be lying if it demanded otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.xsolver.interop import FAIL, PASS, WITHHELD, evaluate, load_manifest  # noqa: E402
from services.xsolver.interop.gate import digest_of, sha256_file  # noqa: E402

DATA = REPO / "data" / "campaigns" / "p106w2"
MANIFEST = DATA / "interop_manifest.json"


@pytest.fixture(scope="module")
def verdict():
    return evaluate(load_manifest(MANIFEST), root=DATA)


def _strip(verdict):
    """Drop the run timestamp and the self-referential digest before comparing."""
    return {k: v for k, v in verdict.items() if k not in ("generated_at", "verdict_digest")}


def _checks(verdict):
    return {c["id"]: c for item in verdict["items"] for c in item["checks"]}


def test_all_pinned_snapshots_are_intact(verdict):
    artifacts = verdict["artifacts"]
    for name, art in artifacts.items():
        if art["expected_sha256"]:
            on_disk = sha256_file(DATA / art["path"])
            assert on_disk == art["expected_sha256"], f"{name} snapshot drifted from its pin"
            assert art["present"], f"{name} pinned artifact must load"
    # the two in-flight nodes have no artifact and say so
    assert artifacts["n01_bubble"]["present"] is False
    assert artifacts["n10_fatigue"]["present"] is False
    assert "in-flight" in artifacts["n01_bubble"]["reason"]


def test_gate_is_honest_fail_not_green(verdict):
    assert verdict["gate_status"] == FAIL
    assert verdict["honest_fail"] is True
    assert verdict["upgrade_forbidden"] is True
    assert all(item["status"] == FAIL for item in verdict["items"])
    assert len(verdict["fractures"]) >= 10


def test_absent_artifacts_never_vote_pass(verdict):
    checks = _checks(verdict)
    for check_id in ("a.bubble_artifact_present", "c.fatigue_artifact_present"):
        assert checks[check_id]["status"] == WITHHELD
        assert checks[check_id]["fractures"][0]["type"] == "artifact_missing"
    # the RAO inter-comparison cannot run either: no RAO series on either arm
    assert checks["b.rao_series_intercompare"]["status"] == WITHHELD


def test_item_a_mechanism_crosscheck(verdict):
    checks = _checks(verdict)
    band = checks["a.slip_drag_reduction_vs_dns_band"]
    assert band["status"] == FAIL
    values = {p["label"]: p["value"] for p in band["detail"]["points"]}
    assert values["b+=10"] == pytest.approx(0.236)
    assert values["b+=20"] == pytest.approx(0.472)
    # extracted slip drag reduction sits 21x below the lower edge of the DNS band
    assert 20.0 < band["detail"]["points"][0]["factor_below_lo"] < 22.0
    assert checks["a.dcf_monotone_in_bplus"]["status"] == PASS


def test_item_b_rao_intercompare_preconditions(verdict):
    checks = _checks(verdict)
    upstream = checks["b.n06_rao_criterion"]
    assert upstream["status"] == FAIL
    assert upstream["fractures"][0]["type"] == "upstream_not_delivered"
    cb = checks["b.geometry_block_coefficient"]["detail"]
    assert cb["entries"][0]["value"] == pytest.approx(0.707477, abs=1e-5)
    assert cb["entries"][1]["value"] == pytest.approx(0.444416, abs=1e-5)
    assert cb["max_rel_diff"] == pytest.approx(0.3718, abs=5e-4)
    length = checks["b.geometry_length"]["detail"]
    assert length["max_rel_diff"] == pytest.approx(0.1667, abs=5e-4)
    assert checks["b.geometry_block_coefficient"]["status"] == FAIL
    assert checks["b.geometry_length"]["status"] == FAIL


def test_item_c_interface_contract(verdict):
    checks = _checks(verdict)
    units = checks["c.torsion_field_units"]
    assert units["status"] == PASS, "producer units/dimensions must be self-consistent"
    dims = {row["field"]: row["dimension"] for row in units["detail"]["fields"] if row.get("dimension")}
    assert dims["segment_stresses"] == dims["allowable_stress"]  # both MPa -> same dimension vector
    decl = checks["c.torsion_declarations"]
    assert decl["status"] == FAIL
    missing = {v["declaration"] for v in decl["detail"]["violations"] if v["type"] == "missing_declaration"}
    assert missing == {"amplitude_convention", "stress_type", "load_case_id", "s_n_reference"}
    assert checks["c.damage_additivity"]["status"] == WITHHELD  # consumer side absent


def test_item_d_emergence_to_torsion_coupling(verdict):
    checks = _checks(verdict)
    assert checks["d.blade_count"]["status"] == PASS
    rated = checks["d.rated_speed"]
    assert rated["status"] == FAIL and rated["detail"]["max_rel_diff"] == pytest.approx(0.0833, abs=5e-4)
    inertia = checks["d.shaft_inertia"]["detail"]
    assert inertia["entries"][0]["value"] == pytest.approx(5000.0)
    assert inertia["entries"][1]["value"] == pytest.approx(12865.0)  # sum of the lumped inertias
    band = checks["d.rpm_excursion_vs_barred_band"]
    assert band["status"] == PASS
    assert band["detail"]["band_a"][0] == pytest.approx(48.51, abs=1e-2)
    assert band["detail"]["band_a"][1] == pytest.approx(171.49, abs=1e-2)
    assert band["detail"]["overlap_fraction"] == pytest.approx(1.0)
    order = checks["d.excitation_order_coverage"]
    assert order["status"] == FAIL
    assert order["detail"]["required_orders"] == [5]
    assert order["detail"]["provided_orders"] == [3]
    assert order["detail"]["missing_orders"] == [5]


def test_verdict_is_reproducible(verdict):
    again = evaluate(load_manifest(MANIFEST), root=DATA)
    assert digest_of(_strip(verdict)) == digest_of(_strip(again))
    assert verdict["manifest_digest"] == again["manifest_digest"]
    # every fracture cites an artifact or an upstream record (audit chain)
    for fracture in verdict["fractures"]:
        assert fracture["check"] and fracture["type"] and fracture["message"]


def test_committed_verdict_matches_a_fresh_run():
    """artifacts/p106w2_interop/verdict.json must be the verdict of this code+data."""
    path = REPO / "artifacts" / "p106w2_interop" / "verdict.json"
    if not path.exists():
        pytest.skip("gate artifacts not generated in this checkout")
    committed = json.loads(path.read_text(encoding="utf-8"))
    fresh = evaluate(load_manifest(MANIFEST), root=DATA)
    assert digest_of(_strip(committed)) == digest_of(_strip(fresh))
    # verdict_digest excludes itself, so a fresh run recomputes it over identical inputs
    assert committed["verdict_digest"] is not None