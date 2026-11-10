"""Wave-2 maturity: reproducibility gate (C7) and the probe-provenance path defect.

Node p83w1-n07 delivered the six criteria of the frame; this file covers what wave 2
added on top, and each case below is written so that it fails on the pre-wave-2 code:

* ``to_case_record`` used to take the frame checkout from ``artifact_path.parents[3]``,
  i.e. from *where the artifact was written*.  An acceptance run with ``--out`` outside
  the checkout therefore produced a probe case with ``git_commit: null`` -- a measured
  residual that could not be attributed to any code -- and C5 dropped to 113/114 with
  no error anywhere.  Provenance must follow the code, not the output path.
* ``corpus_digest`` is the sha256 of the corpus document, which embeds ``harvested_utc``;
  it changes on every run, so it cannot answer "was this the same harvest?".  C7 compares
  ``content_digest`` instead (source bytes + commit + parsing config, wall clock and host
  paths excluded) and reports the byte digest beside it to show it is not the gate.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# helpers: a measurement shaped like probe.measure() output, no solver needed
# ---------------------------------------------------------------------------
def _measurement(commit: str = "abc123def456") -> dict:
    return {
        "schema": "xsolver/probe/v1",
        "probe_id": "unit_probe",
        "backend": {"module": "fake_backend", "version": f"1.2.3+git.{commit}",
                    "repo_commit": commit},
        "provenance": {"repo_dir": "/nonexistent/fake", "repo_commit": commit,
                       "module_file": "/nonexistent/fake/__init__.py", "verified": True},
        "setup": {"grid": [8, 8], "tau": 0.8, "velocity": 0.05, "dtype": "float64",
                  "horizons": [10, 100], "certification_horizon": 100},
        "certification": {"horizon": 100, "mass": 1e-15, "momentum": 2e-15},
    }


def _case(case_id: str, *, sha: str = "a" * 64, commit: str = "c0ffee123456",
          solver_version: str = "solver@1.0", input_digest: str = "b" * 64,
          harvested_utc: str = "2026-11-10T00:00:00+00:00",
          source_path: str = "/root/tensor-ecosystem/A/benchmarks/verified/x.json") -> dict:
    return {
        "schema": "xsolver/case/v1",
        "case_id": case_id,
        "problem_id": case_id.split("::")[0],
        "condition": {"label": "Fn", "value": 0.3, "case_name": case_id},
        "provenance": {"source_path": source_path, "source_sha256": sha,
                       "solver_version": solver_version, "git_commit": commit,
                       "harvested_utc": harvested_utc},
        "input": {"provenance": {"input_digest": input_digest}},
        "output": {"primary_quantities": {"C_T": 0.004}},
    }


def _corpus(cases=None, harvested_utc: str = "2026-11-10T00:00:00+00:00") -> dict:
    cases = cases if cases is not None else [_case("probe_repo::case_a"),
                                            _case("probe_repo::case_b", sha="c" * 64)]
    return {"schema": "xsolver/corpus/v1", "harvested_utc": harvested_utc,
            "stats": {"case_count": len(cases)}, "cases": cases}


# ---------------------------------------------------------------------------
# defect: probe provenance must not depend on where the artifact is written
# ---------------------------------------------------------------------------
def test_probe_case_provenance_survives_output_dir_outside_checkout(xsolver, tmp_path):
    """Baseline failure: this returned ``git_commit: None`` for any out-of-tree --out."""
    outside = tmp_path / "not-a-checkout" / "probe" / "lb_conservation_probe.json"
    record = xsolver.probe.to_case_record(_measurement(), outside)
    commit = record["provenance"]["git_commit"]
    assert commit, ("probe case written outside the checkout lost git_commit: provenance "
                    "must follow the code that measured the residual")
    assert record["input"]["provenance"]["solver_version"], "solver_version must stay recorded"


def test_probe_case_provenance_is_path_invariant(xsolver, tmp_path):
    inside = xsolver.repo / "artifacts" / "xsolver" / "probe" / "p.json"
    outside = tmp_path / "elsewhere" / "deeper" / "p.json"
    a = xsolver.probe.to_case_record(_measurement(), inside)
    b = xsolver.probe.to_case_record(_measurement(), outside)
    assert a["provenance"]["git_commit"] == b["provenance"]["git_commit"]
    assert a["input"]["provenance"]["input_digest"] == b["input"]["provenance"]["input_digest"]
    assert a["case_id"] == b["case_id"]


def test_probe_case_commit_is_the_frame_head(xsolver):
    head = subprocess.run(["git", "-C", str(xsolver.repo), "rev-parse", "--short=12", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0:
        pytest.skip("not a git checkout")
    record = xsolver.probe.to_case_record(_measurement(), Path("/tmp/xsolver-w2/probe.json"))
    assert record["provenance"]["git_commit"] == head.stdout.strip()


def test_probe_frame_repo_is_this_checkout(xsolver):
    assert xsolver.probe.FRAME_REPO == xsolver.repo


# ---------------------------------------------------------------------------
# reproducibility fingerprint: wall clock and host path are not content
# ---------------------------------------------------------------------------
def test_content_digest_ignores_wall_clock_and_source_path(xsolver):
    first = _corpus()
    second = _corpus(harvested_utc="2027-01-01T09:00:00+00:00")
    for case in second["cases"]:
        case["provenance"]["harvested_utc"] = "2027-01-01T09:00:00+00:00"
        case["provenance"]["source_path"] = "/moved/to/another/host/x.json"
    fp1 = xsolver.corpus.content_fingerprint(first)
    fp2 = xsolver.corpus.content_fingerprint(second)
    assert fp1["content_digest"] == fp2["content_digest"]
    # ... while the byte digest of the document does move: that is why it is not the gate
    assert xsolver.normalize.digest_of(first) != xsolver.normalize.digest_of(second)


def test_content_digest_detects_changed_source_bytes(xsolver):
    base = _corpus()
    moved = copy.deepcopy(base)
    moved["cases"][1]["provenance"]["source_sha256"] = "d" * 64
    assert (xsolver.corpus.content_fingerprint(base)["content_digest"]
            != xsolver.corpus.content_fingerprint(moved)["content_digest"])


def test_content_digest_detects_changed_commit_and_config(xsolver, monkeypatch):
    base = _corpus()
    moved = copy.deepcopy(base)
    moved["cases"][0]["provenance"]["git_commit"] = "deadbeef0000"
    assert (xsolver.corpus.content_fingerprint(base)["content_digest"]
            != xsolver.corpus.content_fingerprint(moved)["content_digest"])
    # a parsing-config change must move the fingerprint even with identical artifacts
    before = xsolver.corpus.content_fingerprint(base)
    monkeypatch.setattr(xsolver.corpus, "_config_digests",
                        lambda: {"normalization_rules": "0" * 64})
    after = xsolver.corpus.content_fingerprint(base)
    assert after["frame_config"] == {"normalization_rules": "0" * 64}
    assert after["content_digest"] != before["content_digest"]


def test_index_carries_both_digests(xsolver, tmp_path):
    corpus_path, index_path = xsolver.corpus.save_corpus(_corpus(), tmp_path)
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    assert index["content_digest"]
    assert index["corpus_digest"]
    assert index["frame_config_digest"]
    assert index["content_digest"] != index["corpus_digest"]


# ---------------------------------------------------------------------------
# C7 criterion: withheld / pass / fail, and the negative control
# ---------------------------------------------------------------------------
def test_c7_withheld_without_second_harvest(xsolver):
    criterion = xsolver.acceptance.criterion_reproducibility(
        xsolver.corpus.content_fingerprint(_corpus()), None, {"first": "x", "second": None})
    assert criterion["status"] == xsolver.conservation.WITHHELD


def test_c7_pass_when_only_wall_clock_differs(xsolver):
    fp1 = xsolver.corpus.content_fingerprint(_corpus())
    second = _corpus(harvested_utc="2027-02-02T00:00:00+00:00")
    fp2 = xsolver.corpus.content_fingerprint(second)
    criterion = xsolver.acceptance.criterion_reproducibility(
        fp1, fp2, {"first": "byte1", "second": "byte2"})
    assert criterion["status"] == xsolver.conservation.PASS
    assert criterion["value"]["content_digest_stable"] is True
    assert criterion["value"]["byte_digest_stable"] is False


def test_c7_fails_on_changed_case_and_names_it(xsolver):
    fp1 = xsolver.corpus.content_fingerprint(_corpus())
    changed = _corpus()
    changed["cases"][0]["provenance"]["source_sha256"] = "e" * 64
    fp2 = xsolver.corpus.content_fingerprint(changed)
    criterion = xsolver.acceptance.criterion_reproducibility(
        fp1, fp2, {"first": "b", "second": "c"})
    assert criterion["status"] == xsolver.conservation.FAIL
    assert criterion["value"]["cases_changed"] == 1
    assert criterion["value"]["cases_changed_ids"] == ["probe_repo::case_a"]


def test_c7_fails_on_vanished_case(xsolver):
    fp1 = xsolver.corpus.content_fingerprint(_corpus())
    fp2 = xsolver.corpus.content_fingerprint(_corpus(cases=[_case("probe_repo::case_a")]))
    criterion = xsolver.acceptance.criterion_reproducibility(fp1, fp2, None)
    assert criterion["status"] == xsolver.conservation.FAIL
    assert criterion["value"]["cases_vanished"] == ["probe_repo::case_b"]


# ---------------------------------------------------------------------------
# iron law: the adjudicated problem is governance data, not source code
# ---------------------------------------------------------------------------
def test_primary_problem_comes_from_config(xsolver):
    from services.xsolver.jsonschema_lite import load_config
    cfg = load_config("acceptance")
    assert xsolver.acceptance.PRIMARY_PROBLEM == cfg["primary_problem_id"]
    assert xsolver.acceptance.NODE_ID == cfg["node"]["id"]
    assert xsolver.acceptance.MISSION_ID == cfg["node"]["mission_id"]


def test_adjudication_criterion_reports_absent_problem(xsolver):
    """A mis-pointed config must read as FAIL, never as a silent empty comparison."""
    criterion = xsolver.acceptance.criterion_adjudication(
        {"groups": []}, problem_id="problem_not_in_report")
    assert criterion["status"] == xsolver.conservation.FAIL
    assert "absent" in criterion["detail"]