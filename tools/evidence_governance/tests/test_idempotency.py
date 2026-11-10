"""Criterion #4: two runs on the same input produce identical artifact sha256.

Also checks the stronger property the criterion implies: the artifacts must not
depend on *input ordering* either, and the on-disk manifest must equal the
in-memory digest (so the published ledger is the thing that was verified).
"""
from __future__ import annotations

import copy
import json

from tools.evidence_governance.artifacts import combined_digest, write_artifact_set
from tools.evidence_governance.runner import run_pipeline, verify_idempotent

from .conftest import ROOT


def test_two_runs_produce_identical_artifacts(real_config, tmp_path):
    cfg = copy.deepcopy(real_config)
    cfg["output"] = {"dir": str(tmp_path / "run")}
    payloads_a, summary_a = run_pipeline(cfg, ROOT)
    payloads_b, summary_b = run_pipeline(cfg, ROOT)
    assert combined_digest(payloads_a) == combined_digest(payloads_b)
    assert summary_a["combined_sha256"] == summary_b["combined_sha256"]
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["combined_sha256"] == summary_a["combined_sha256"]


def test_verify_idempotent_helper_reports_identity(demo_config, tmp_path):
    cfg = copy.deepcopy(demo_config)
    cfg["output"] = {"dir": str(tmp_path / "demo")}
    result = verify_idempotent(cfg, ROOT)
    assert result["identical"] is True
    assert result["run_a_sha256"] == result["run_b_sha256"]
    assert result["n_artifacts"] == 5


def test_artifact_payloads_carry_no_wall_clock(real_config, tmp_path):
    cfg = copy.deepcopy(real_config)
    cfg["output"] = {"dir": str(tmp_path / "run")}
    payloads, _ = run_pipeline(cfg, ROOT)
    blob = json.dumps(payloads, sort_keys=True)
    for needle in ("generated_at", "timestamp", "started_at", "finished_at"):
        assert needle not in blob, needle


def test_manifest_lists_every_artifact_with_a_hash(tmp_path):
    manifest = write_artifact_set(tmp_path / "set", {"b.json": {"x": 1}, "a.json": {"y": 2}})
    names = [f["name"] for f in manifest["files"]]
    assert names == ["a.json", "b.json"]
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])
    assert manifest["n_files"] == 2
    assert len(manifest["combined_sha256"]) == 64


def test_proposal_order_does_not_change_the_digest(pipeline):
    from tools.evidence_governance.proposal import summarise_proposals

    _s, _c, compiler, _e = pipeline
    items = _s.scan().items
    forward = summarise_proposals(compiler.compile(items))
    backward = summarise_proposals(compiler.compile(list(reversed(items))))
    assert forward["digest"] == backward["digest"]