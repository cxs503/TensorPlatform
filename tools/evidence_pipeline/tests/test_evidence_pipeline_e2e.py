"""End-to-end tests of the evidence-governance pipeline.

Every test drives the *real* chain — a generator subprocess, the gate dispatch,
the artifact, the ingest gate (including a replay) and the regression detector.
Nothing is monkeypatched: the numbers asserted below are the numbers the
generators actually produce.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import helpers
from tools.evidence_pipeline.artifact import verify_artifact
from tools.evidence_pipeline.config import ROOT, load_config

BENCH_IDS = list(helpers.PASSING_BENCHMARKS)


# --------------------------------------------------------------------------- pass
@pytest.mark.parametrize("bench", BENCH_IDS)
def test_full_chain_from_scratch(bench: str, tmp_path: Path) -> None:
    """config → execute → gates → artifact → ingest gate → baseline → stable."""
    cfg_path = helpers.localise(bench, tmp_path)
    out_dir = tmp_path / "runs"

    first = helpers.run(cfg_path, out_dir, update_baseline=True)
    assert first.status == "pass", first.to_dict()
    assert first.exit_code == 0
    assert first.verdict["passed"] is True
    assert first.ingest["passed"] is True
    assert first.ingest["n_failed"] == 0
    assert first.regression["decision"]["classification"] == "no_baseline"
    assert first.regression["baseline_updated"] is True
    assert first.ledger.verify_chain() is True

    artifact = helpers.load_json(helpers.artifact_of(out_dir, bench))
    assert artifact["schema"] == "evidence-pipeline/artifact/1"
    assert artifact["status"] == "pass"
    assert artifact["payload_digest"] == first.ledger.entries[1]["payload_digest"]
    # every gate in the configuration produced an auditable verdict
    cfg = load_config(cfg_path)
    assert [g["id"] for g in artifact["gates"]] == [g.id for g in cfg.gates]
    assert all(g["passed"] for g in artifact["gates"])

    second = helpers.run(cfg_path, out_dir)
    assert second.status == "pass"
    assert second.regression["decision"]["classification"] == "stable"
    assert second.regression["comparison"]["within_tolerance"] is True


@pytest.mark.parametrize("bench", BENCH_IDS)
def test_artifact_is_byte_identical_across_runs(bench: str, tmp_path: Path) -> None:
    """Determinism: same config → same artifact bytes (no timestamps inside)."""
    cfg_path = helpers.localise(bench, tmp_path)
    out_dir = tmp_path / "runs"
    helpers.run(cfg_path, out_dir, update_baseline=True)
    artifact_path = helpers.artifact_of(out_dir, bench)
    first_bytes = artifact_path.read_bytes()

    helpers.run(cfg_path, out_dir)
    assert artifact_path.read_bytes() == first_bytes

    # no wall clock inside the artifact (that is what makes the bytes stable) …
    artifact = json.loads(first_bytes)
    assert "created_utc" not in artifact and "started_utc" not in artifact
    # … the chain to the previous artifact *file* of the same node lives in the run record
    import hashlib

    record = helpers.load_json(helpers.record_of(out_dir, bench))
    artifact_entries = [e for e in record["provenance"]["entries"] if e["stage"] == "artifact"]
    assert artifact_entries[-1]["prev_artifact_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert artifact["self_sha256"] != artifact_entries[-1]["prev_artifact_sha256"]


def test_committed_baselines_reproduce_locally(tmp_path: Path) -> None:
    """The committed baselines must reproduce: drift == 0 against a fresh run."""
    for bench in BENCH_IDS:
        cfg = load_config(helpers.BENCHMARKS / f"{bench}.json")
        assert cfg.baseline_path.is_file(), f"missing committed baseline for {bench}"
        result = helpers.run(helpers.BENCHMARKS / f"{bench}.json", tmp_path / bench)
        assert result.status == "pass", result.to_dict()
        decision = result.regression["decision"]
        assert decision["classification"] == "stable"
        assert decision["drifted"] == []
        assert result.regression["comparison"]["max_rel_drift"] <= cfg.regression.tol


# -------------------------------------------------------------------- gate fail
def test_numeric_gate_failure_is_fail_closed(tmp_path: Path) -> None:
    cfg_path = helpers.mutate_config(
        helpers.BENCHMARKS / "syn-alpha-quadrature.json",
        tmp_path / "cfg" / "alpha_tight.json",
        {"gates.0.thresholds.p_order.min": 5.0,
         "regression.baseline": str(tmp_path / "b.json")},
    )
    result = helpers.run(cfg_path, tmp_path / "runs")
    assert result.status == "gate_fail"
    assert result.exit_code == 2
    assert result.verdict["passed"] is False
    assert "p_order" in " ".join(result.verdict["failures"]["convergence_order"])

    artifact = helpers.load_json(helpers.artifact_of(tmp_path / "runs", "syn-alpha-quadrature"))
    assert artifact["status"] == "fail"
    # the failed artifact must be refused by the ingest gate
    assert result.ingest["passed"] is False
    failed_ids = {c["id"] for c in result.ingest["checks"] if not c["passed"]}
    assert "A4_status_pass" in failed_ids
    assert verify_artifact(helpers.artifact_of(tmp_path / "runs", "syn-alpha-quadrature"),
                           config_path=str(cfg_path))["passed"] is False


def test_conservation_gate_discriminates_conservative_from_nonconservative(
    tmp_path: Path,
) -> None:
    """The conservation gate is not vacuous: negative control fails by orders of magnitude."""
    good = helpers.run(
        helpers.mutate_config(
            helpers.BENCHMARKS / "syn-beta-exchange.json",
            tmp_path / "cfg" / "beta.json",
            {"regression.baseline": str(tmp_path / "b.json")},
        ),
        tmp_path / "good",
    )
    assert good.status == "pass"
    good_artifact = helpers.load_json(helpers.artifact_of(tmp_path / "good", "syn-beta-exchange"))
    good_gate = {g["id"]: g for g in good_artifact["gates"]}["exchange_conservation"]
    good_sum = good_gate["metrics"]["sum_rel_residual"]

    bad = helpers.run(
        helpers.CONTROLS / "syn-delta-nonconservative.json", tmp_path / "bad"
    )
    assert bad.status == "gate_fail"
    assert bad.exit_code == 2
    bad_artifact = helpers.load_json(
        helpers.artifact_of(tmp_path / "bad", "syn-delta-nonconservative")
    )
    bad_gate = {g["id"]: g for g in bad_artifact["gates"]}["exchange_conservation"]
    bad_sum = bad_gate["metrics"]["sum_rel_residual"]

    assert bad_gate["passed"] is False
    assert bad_sum > 1e-2
    assert good_sum < 1e-14
    # ten orders of magnitude between "conserved" and "the correction was skipped"
    assert bad_sum / good_sum > 1e10
    assert bad_sum / bad_gate["thresholds"]["sum_rel_residual"] > 1e10


# ------------------------------------------------------------------ ingest gate
def test_replay_mismatch_rejects_non_deterministic_generator(tmp_path: Path) -> None:
    result = helpers.run(helpers.CONTROLS / "syn-eps-nondeterministic.json", tmp_path / "runs")
    assert result.verdict["passed"] is True          # the numeric gate itself passes
    assert result.status == "ingest_rejected"
    assert result.exit_code == 3
    failed = {c["id"] for c in result.ingest["checks"] if not c["passed"]}
    assert "A9_replay" in failed


def test_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    bench = "syn-beta-exchange"
    cfg_path = helpers.localise(bench, tmp_path)
    helpers.run(cfg_path, tmp_path / "runs", update_baseline=True)
    artifact_path = helpers.artifact_of(tmp_path / "runs", bench)
    pristine = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert verify_artifact(artifact_path, config_path=str(cfg_path))["passed"] is True

    # 1. a single altered number in the payload
    tampered = json.loads(json.dumps(pristine))
    tampered["payload"]["metrics"]["sum_rel_residual"] = 0.5
    helpers.write_json(artifact_path, tampered)
    report = verify_artifact(artifact_path, config_path=str(cfg_path))
    assert report["passed"] is False
    assert {"A3_self_digest", "A6_payload_digest"} & {
        c["id"] for c in report["checks"] if not c["passed"]
    }

    # 2. a re-labelled status
    tampered = json.loads(json.dumps(pristine))
    tampered["status"] = "pass"
    tampered["verdict"]["passed"] = False
    helpers.write_json(artifact_path, tampered)
    report = verify_artifact(artifact_path, config_path=str(cfg_path))
    assert report["passed"] is False
    assert "A5_verdict_consistency" in {c["id"] for c in report["checks"] if not c["passed"]}

    # 3. the pristine artifact is still accepted (the gate is not "always red")
    helpers.write_json(artifact_path, pristine)
    assert verify_artifact(artifact_path, config_path=str(cfg_path))["passed"] is True


def test_generator_identity_tamper_is_rejected(tmp_path: Path) -> None:
    from tools.evidence_pipeline.artifact import build_artifact
    from tools.evidence_pipeline.execution import execute_generator
    from tools.evidence_pipeline.gate import run_gates

    original = ROOT / "tools/evidence_pipeline/generators/syn_alpha_quadrature.py"
    copy = tmp_path / "tools/evidence_pipeline/generators/syn_alpha_quadrature.py"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, copy)

    cfg_path = helpers.mutate_config(
        helpers.BENCHMARKS / "syn-alpha-quadrature.json",
        tmp_path / "cfg" / "alpha_copy.json",
        {"generator": str(copy), "regression.baseline": str(tmp_path / "b.json")},
    )
    cfg = load_config(cfg_path)
    result = execute_generator(cfg)
    assert result.ok, result.stderr
    outcomes = run_gates(cfg, result.payload)
    artifact = build_artifact(cfg, result.payload, outcomes,
                              provenance={"note": "generator-identity probe"},
                              config_path=str(cfg_path))
    artifact_path = helpers.write_json(tmp_path / "runs" / "copy.artifact.json", artifact)
    assert verify_artifact(artifact_path, config_path=str(cfg_path))["passed"] is True

    # edit the generator after the artifact was produced
    copy.write_text(copy.read_text() + "\n# edited after the run\n", encoding="utf-8")
    report = verify_artifact(artifact_path, config_path=str(cfg_path))
    assert report["passed"] is False
    failed = {c["id"] for c in report["checks"] if not c["passed"]}
    assert "A7_generator_identity" in failed
    assert "A3_self_digest" not in failed  # the artifact itself was not altered


def test_config_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    bench = "syn-alpha-quadrature"
    cfg_path = helpers.localise(bench, tmp_path)
    helpers.run(cfg_path, tmp_path / "runs", update_baseline=True)
    artifact_path = helpers.artifact_of(tmp_path / "runs", bench)

    weakened = helpers.mutate_config(
        cfg_path,
        tmp_path / "cfg" / "alpha_weakened.json",
        {"gates.0.thresholds.p_order.min": 1.0},
    )
    report = verify_artifact(artifact_path, config_path=str(weakened))
    assert report["passed"] is False
    assert "A8_config_identity" in {c["id"] for c in report["checks"] if not c["passed"]}


# ------------------------------------------------------------------- regression
def test_regression_drift_triggers_rerun_and_requires_explicit_rebless(tmp_path: Path) -> None:
    bench = "syn-alpha-quadrature"
    cfg_path = helpers.localise(bench, tmp_path)
    out_dir = tmp_path / "runs"
    helpers.run(cfg_path, out_dir, update_baseline=True)

    baseline_path = tmp_path / "baselines" / f"{bench}.json"
    baseline = helpers.load_json(baseline_path)
    baseline["metrics"]["err_fine"] = baseline["metrics"]["err_fine"] * (1.0 + 1e-3)
    helpers.write_json(baseline_path, baseline)

    drifted = helpers.run(cfg_path, out_dir)
    assert drifted.status == "regression_fail"
    assert drifted.exit_code == 4
    decision = drifted.regression["decision"]
    assert decision["classification"] == "drift_reproducible"
    assert decision["passed"] is False
    assert decision["drifted"] == ["err_fine"]
    # 1 judged run + regression.reruns re-runs, all reproducing the same payload
    assert len(drifted.attempts) == 3
    assert len({a["payload_digest"] for a in drifted.attempts}) == 1
    assert drifted.regression["comparison"]["per_metric"]["err_fine"]["rel_drift"] == pytest.approx(
        1e-3 / (1.0 + 1e-3), rel=1e-6
    )

    refused = helpers.run(cfg_path, out_dir, update_baseline=True)
    assert refused.status == "baseline_refused"
    assert refused.exit_code == 6
    assert helpers.load_json(baseline_path)["metrics"]["err_fine"] == baseline["metrics"]["err_fine"]

    reblessed = helpers.run(cfg_path, out_dir, update_baseline=True, accept_drift=True)
    assert reblessed.status == "pass"
    assert reblessed.regression["baseline_updated"] is True
    assert helpers.load_json(baseline_path)["metrics"]["err_fine"] != baseline["metrics"]["err_fine"]

    settled = helpers.run(cfg_path, out_dir)
    assert settled.status == "pass"
    assert settled.regression["decision"]["classification"] == "stable"


def test_runner_and_artifact_cli_exit_codes(tmp_path: Path) -> None:
    """The CLI contract (exit codes) is what CI consumes — test it, not just the API."""
    cfg_path = helpers.localise("syn-alpha-quadrature", tmp_path)
    out_dir = tmp_path / "runs"
    env = {"PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0"}
    import os

    base_env = dict(os.environ)
    base_env.update(env)

    good = subprocess.run(
        [sys.executable, "-m", "tools.evidence_pipeline.runner",
         "--config", str(cfg_path), "--out-dir", str(out_dir), "--update-baseline"],
        cwd=str(ROOT), env=base_env, capture_output=True, text=True,
    )
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["status"] == "pass"

    bad = subprocess.run(
        [sys.executable, "-m", "tools.evidence_pipeline.runner",
         "--config", str(helpers.CONTROLS / "syn-delta-nonconservative.json"),
         "--out-dir", str(tmp_path / "bad")],
        cwd=str(ROOT), env=base_env, capture_output=True, text=True,
    )
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["status"] == "gate_fail"

    artifact_path = helpers.artifact_of(out_dir, "syn-alpha-quadrature")
    ingest_ok = subprocess.run(
        [sys.executable, "-m", "tools.evidence_pipeline.artifact", "--gate", str(artifact_path),
         "--config", str(cfg_path)],
        cwd=str(ROOT), env=base_env, capture_output=True, text=True,
    )
    assert ingest_ok.returncode == 0, ingest_ok.stdout

    tampered = json.loads(artifact_path.read_text(encoding="utf-8"))
    tampered["payload"]["metrics"]["p_order"] = 9.99
    helpers.write_json(artifact_path, tampered)
    ingest_bad = subprocess.run(
        [sys.executable, "-m", "tools.evidence_pipeline.artifact", "--gate", str(artifact_path),
         "--config", str(cfg_path)],
        cwd=str(ROOT), env=base_env, capture_output=True, text=True,
    )
    assert ingest_bad.returncode == 3
    assert json.loads(ingest_bad.stdout)["passed"] is False


def test_run_record_captures_provenance_chain(tmp_path: Path) -> None:
    bench = "syn-gamma-partition"
    cfg_path = helpers.localise(bench, tmp_path)
    out_dir = tmp_path / "runs"
    helpers.run(cfg_path, out_dir, update_baseline=True)
    record = helpers.load_json(helpers.record_of(out_dir, bench))
    provenance = record["provenance"]
    assert provenance["chain_valid"] is True
    assert provenance["n_entries"] >= 6
    stages = [e["stage"] for e in provenance["entries"]]
    assert stages[:3] == ["config", "execute", "validate"]
    assert "gates" in stages and "artifact" in stages and "ingest_gate" in stages
    assert "regression" in stages
    # every entry is hash-linked to the previous one
    prev = "genesis"
    for i, entry in enumerate(provenance["entries"]):
        assert entry["seq"] == i
        assert entry["prev_digest"] == prev
        prev = entry["digest"]
    # the artifact digest recorded in the ledger matches the artifact on disk
    artifact = helpers.load_json(helpers.artifact_of(out_dir, bench))
    assert [e for e in provenance["entries"] if e["stage"] == "artifact"][0][
        "self_sha256"
    ] == artifact["self_sha256"]