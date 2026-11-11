"""Historical PASS census: extraction, classification, recomputability."""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_evidence_chain import reclassify as R


def _spec(**kw) -> R.ReclassSpec:
    base = dict(sources=["docs/**/*.json"], margin_default=0.05, min_computable_n=3)
    base.update(kw)
    return R.ReclassSpec(**base)


def test_parse_threshold_handles_numbers_and_strings():
    assert R.parse_threshold(0.05) == 0.05
    assert R.parse_threshold("<= 1e-12") == 1e-12
    assert R.parse_threshold("≤15%") == 15.0
    assert R.parse_threshold(True) is None
    assert R.parse_threshold("no digits") is None


def test_extract_claims_finds_nested_status_records():
    payload = {
        "gates": {
            "g1": {"status": "PASS", "value": 0.01, "threshold": 0.05},
            "g2": {"verdict": "FAIL", "value": 0.3, "threshold": 0.1},
        },
        "list": [{"result": "OK", "observed": 0.02, "tolerance": "<=0.05"}],
    }
    claims = R.extract_claims(payload, "doc.json", _spec())
    assert len(claims) == 3
    assert {c.status for c in claims} == {"PASS", "FAIL"}
    assert claims[0].threshold == 0.05


def test_classification_of_each_bucket():
    spec = _spec()
    nodes = {
        "adequate": {"status": "PASS", "value": 0.01, "threshold": 0.05,
                     "n": 40, "sd": 0.01},
        "underpowered": {"status": "PASS", "value": 0.04, "threshold": 0.05,
                         "n": 3, "sd": 0.05},
        "threshold_only": {"status": "PASS", "value": 0.02, "threshold": 0.05},
        "inconsistent": {"status": "PASS", "value": 0.20, "threshold": 0.05},
        "unverifiable": {"status": "PASS", "threshold": 0.05},
        "failed": {"status": "FAIL", "value": 0.3, "threshold": 0.05},
    }
    got = {}
    for name, node in nodes.items():
        claim = R.extract_claims({"g": node}, "doc.json", spec)[0]
        R.classify(claim, spec)
        got[name] = claim
    assert got["adequate"].classification == "ADEQUATE"
    assert not got["adequate"].needs_reverification
    assert got["underpowered"].classification == "UNDERPOWERED"
    assert got["underpowered"].needs_reverification
    assert got["threshold_only"].classification == "THRESHOLD_ONLY"
    assert got["inconsistent"].classification == "INCONSISTENT"
    assert got["unverifiable"].classification == "UNVERIFIABLE"
    assert got["failed"].classification == "FAIL_RECORDED"
    assert not got["failed"].needs_reverification     # honest FAIL is preserved


def test_adequate_requires_mde_coverage_of_the_margin():
    spec = _spec()
    # n = 4 with a large spread cannot certify 80 % of the margin
    claim = R.extract_claims(
        {"g": {"status": "PASS", "value": 0.005, "threshold": 0.05, "n": 4, "sd": 0.05}},
        "doc.json", spec,
    )[0]
    R.classify(claim, spec)
    assert claim.classification == "UNDERPOWERED"
    assert claim.mde is not None and claim.mde < 0.8 * 0.05


def test_summary_and_recompute_are_consistent(synth_config, synth_root):
    spec = _spec(margin_default=0.05)
    claims, inventory = R.scan_claims(synth_root, spec)
    summary = R.summarize(claims)
    assert summary["n_pass"] == 6      # 5 gates + 1 scalar verdict
    assert summary["n_fail"] == 1
    assert 0.0 < summary["low_power_flag_rate"] < 1.0
    check = R.recompute(summary, claims)
    assert check["rate_matches"] and check["digest_matches"]
    assert check["n_claims"] == summary["n_claims"]
    assert inventory[0]["status"] == "ok"


def test_recompute_detects_tampering(synth_config, synth_root):
    spec = _spec()
    claims, _ = R.scan_claims(synth_root, spec)
    summary = R.summarize(claims)
    claims[0].classification = "FABRICATED"    # tamper after the fact
    check = R.recompute(summary, claims)
    assert not check["digest_matches"]


def test_unreadable_source_is_inventoried_not_fatal(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "broken.json").write_text("{oops", encoding="utf-8")
    (tmp_path / "docs" / "ok.json").write_text(json.dumps(
        {"g": {"status": "PASS", "value": 0.01, "threshold": 0.05}}), encoding="utf-8")
    claims, inventory = R.scan_claims(tmp_path, _spec())
    status = {i["source"]: i["status"] for i in inventory}
    assert status["docs/broken.json"] == "unreadable"
    assert status["docs/ok.json"] == "ok"
    assert len(claims) == 1


def test_claim_dict_is_json_safe(synth_config, synth_root):
    claims, _ = R.scan_claims(synth_root, _spec())
    for c in claims:
        json.dumps(c.to_dict())

def test_reclassification_can_exclude_its_own_outputs(tmp_path: Path):
    """Regression guard: the census must not read the artefact it just wrote.

    The first live run did exactly that and inflated the claim list on every
    re-run (the written JSON contains the claim records themselves).
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "artifacts" / "run_out").mkdir(parents=True)
    (tmp_path / "docs" / "acceptance.json").write_text(json.dumps(
        {"g": {"status": "PASS", "value": 0.01, "threshold": 0.05}}), encoding="utf-8")
    previous_output = {"claims": [{"status": "PASS", "value": 0.2, "threshold": 0.05}],
                       "summary": {"n_pass": 1}}
    (tmp_path / "artifacts" / "run_out" / "evidence_chain.json").write_text(
        json.dumps(previous_output), encoding="utf-8")

    spec_all = R.ReclassSpec(
        sources=["docs/**/*.json", "artifacts/**/*.json"], margin_default=0.05
    )
    claims_all, _ = R.scan_claims(tmp_path, spec_all)
    assert len(claims_all) == 2                       # feedback loop reproduced

    spec_guarded = R.ReclassSpec(
        sources=["docs/**/*.json", "artifacts/**/*.json"],
        exclude=["artifacts/run_out/**"],
        margin_default=0.05,
    )
    claims_guarded, _ = R.scan_claims(tmp_path, spec_guarded)
    assert len(claims_guarded) == 1                   # own output ignored


def test_shipped_config_excludes_its_output_directory():
    config_path = Path(__file__).resolve().parents[1] / "configs" / "ecosystem_solvers.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    excluded = raw["reclassification"].get("exclude", [])
    # both the machine artefact and the report directory carry PASS records and
    # must never feed the census (self-feedback, see the regression test above)
    assert any("artifacts/p474w1_n01" in pat for pat in excluded)
    assert any("docs/p474w1_n01" in pat for pat in excluded)


def test_reclassification_base_can_point_at_another_repository(synth_root):
    """The census may live in the platform repo while the config root is elsewhere."""
    from tools.capability_evidence_chain import pipeline

    cfg = {
        "schema_version": "1.0",
        "repos": [{"name": "synth_solver_a", "path": "synth_solver_a",
                   "include": ["artifacts/**/*.json"]}],
        "capabilities": [],
        "reclassification": {"sources": ["docs/**/*.json"], "base": str(synth_root),
                             "margin_default": 0.05},
    }
    cfg_path = synth_root.parent / "cfg_base.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    art = pipeline.run(cfg_path, root=synth_root, ks_boot=50)
    assert art["reclassification"]["summary"]["n_pass"] == 6
    assert art["reclassification"]["base_dir"] == str(synth_root)


def test_value_and_threshold_are_unit_consistent():
    """A physical quantity under ``value`` must not be compared to a percent tolerance."""
    spec = _spec()
    node = {"status": "PASS", "value": 6.06e8, "deviation_pct": 0.0,
            "tolerance_pct": 2.0}
    claim = R.extract_claims({"c": node}, "doc.json", spec)[0]
    assert claim.value == 0.0 and claim.threshold == 2.0
    R.classify(claim, spec)
    assert claim.classification == "THRESHOLD_ONLY"   # not INCONSISTENT


def test_pairs_fall_back_to_independent_keys():
    spec = _spec()
    claim = R.extract_claims(
        {"c": {"status": "PASS", "value": 0.01, "tolerance": 0.05}}, "doc.json", spec
    )[0]
    assert claim.value == 0.01 and claim.threshold == 0.05
