"""Unit tests for task salvage — synthetic fixtures only (no domain vocabulary).

Every fixture below is invented: repositories named ``alpha``, files named
``widget.py``, mission ids like ``m1001``.  The zero-domain guard at the bottom
runs the platform denylist scanner over this package and fails on a hit, so a
future edit cannot smuggle a field term into the mechanism.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.task_salvage import backtest, continuation, salvage, sift, taxonomy


# ───────────────────────────────── taxonomy ─────────────────────────────────

def _record(stage: str | None, headline: str = "", mission_id: int = 1001, goal: str = "") -> dict:
    record = {
        "id": mission_id,
        "status": "failed",
        "repo": "AlphaRepo",
        "goal": goal,
        "headline": headline,
        "result": {"evidence": {"checks": {}}},
    }
    if stage is not None:
        record["result"]["evidence"]["checks"]["fail_stage"] = stage
    return record


@pytest.mark.parametrize(
    "stage,expected_bucket,in_scope",
    [
        ("llm_error", "llm_error", True),
        ("timeout", "timeout", True),
        ("wall_exhausted", "timeout", True),
        ("budget_exhausted", "budget_exhausted", True),
        ("token_budget_exhausted", "budget_exhausted", True),
        ("no_progress", "no_progress", False),
        ("no_progress+forced_finish", "no_progress", False),
        (None, "other", False),
        ("", "other", False),
    ],
)
def test_taxonomy_matrix(stage, expected_bucket, in_scope) -> None:
    record = _record(stage)
    assert taxonomy.bucket_of(record) == expected_bucket
    assert taxonomy.in_scope(record) is in_scope


def test_taxonomy_falls_back_to_headline_text() -> None:
    record = _record("unmapped_reason", headline="runner timed out after 3600s")
    assert taxonomy.bucket_of(record) == "timeout"


def test_taxonomy_reason_codes_are_stable_and_distinct() -> None:
    assert taxonomy.reason_code(_record("timeout")) == "TERMINAL_TIMEOUT"
    assert taxonomy.reason_code(_record("wall_exhausted")) == taxonomy.reason_code(_record("timeout"))
    assert taxonomy.reason_code(_record(None)) == "TERMINAL_OTHER:no_evidence_row"
    assert taxonomy.reason_code(_record("weird")) == "TERMINAL_OTHER:weird"


def test_taxonomy_distill_counts() -> None:
    rows = [_record("llm_error"), _record("timeout"), _record("no_progress"), _record(None)]
    summary = taxonomy.distill(rows)
    assert summary["total"] == 4
    assert summary["in_scope"] == 2
    assert summary["bucket_counts"]["other"] == 1


# ─────────────────────────────────── sift ───────────────────────────────────

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_gate_parse_python_and_json(tmp_path: Path) -> None:
    good = _write(tmp_path / "alpha" / "widget.py", "VALUE = 1\n")
    bad = _write(tmp_path / "alpha" / "broken.py", "def f(:\n")
    good_json = _write(tmp_path / "alpha" / "meta.json", '{"a": 1}')
    bad_json = _write(tmp_path / "alpha" / "meta_bad.json", "{a: 1")
    assert sift.gate_parse(good)["status"] == "pass"
    assert sift.gate_parse(bad)["status"] == "fail"
    assert sift.gate_parse(bad)["detail"].startswith("syntax")
    assert sift.gate_parse(good_json)["status"] == "pass"
    assert sift.gate_parse(bad_json)["status"] == "fail"


def test_gate_parse_text_and_binary(tmp_path: Path) -> None:
    text = _write(tmp_path / "docs" / "note.md", "# note\n")
    binary = tmp_path / "runs" / "blob.unknownbi"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x00\x01\x02")
    assert sift.gate_parse(text)["status"] == "pass"
    assert sift.gate_parse(binary)["status"] == "fail"
    assert sift.gate_parse(tmp_path / "missing.py")["detail"] == "file_missing"


def test_gate_smoke_pass_and_fail(tmp_path: Path) -> None:
    ok = _write(tmp_path / "alpha" / "widget.py", "VALUE = 1\n")
    boom = _write(tmp_path / "alpha" / "explodes.py", "raise RuntimeError('boom')\n")
    assert sift.gate_smoke(ok, tmp_path)["status"] == "pass"
    failed = sift.gate_smoke(boom, tmp_path)
    assert failed["status"] == "fail" and "boom" in failed["detail"]
    note = _write(tmp_path / "docs" / "note.md", "# note\n")
    assert sift.gate_smoke(note, tmp_path)["status"] == "not_applicable"


def test_smoke_skipped_never_passes_silently(tmp_path: Path) -> None:
    module = _write(tmp_path / "alpha" / "widget.py", "VALUE = 1\n")
    record = sift.sift_file(module, "alpha/widget.py", tmp_path, ("alpha",), smoke=False)
    assert record["verdict"] == sift.BLOCKED
    assert "smoke" in record["failed_gates"]


def test_root_level_file_maps_to_checkout_root(tmp_path: Path) -> None:
    assert sift.gate_structural("notes.py", ("alpha",))["status"] == "pass"
    assert sift.gate_structural(".gitignore", ("alpha",))["status"] == "pass"


@pytest.mark.parametrize("rel", ["__pycache__/x.pyc", "node_modules/pkg/index.js", ".scratch/dbg.py", "tmp/cache.json"])
def test_residue_is_never_a_candidate(rel: str, tmp_path: Path) -> None:
    assert salvage._is_residue(rel) is True


def test_gate_structural_uses_layout(tmp_path: Path) -> None:
    assert sift.gate_structural("alpha/widget.py", ("alpha", "beta"))["status"] == "pass"
    assert sift.gate_structural("gamma/widget.py", ("alpha",))["status"] == "fail"
    assert sift.gate_structural(None, ("alpha",))["detail"] == "outside_worktree"
    assert sift.gate_structural("README.md", ("alpha",))["status"] == "pass"


def test_normalize_rel_rejects_escapes(tmp_path: Path) -> None:
    wt = tmp_path / "m1001"
    wt.mkdir()
    assert sift.normalize_rel("alpha/widget.py", wt) == "alpha/widget.py"
    assert sift.normalize_rel(str(wt / "alpha" / "widget.py"), wt) == "alpha/widget.py"
    assert sift.normalize_rel("../etc/passwd", wt) is None
    assert sift.normalize_rel("/etc/passwd", wt) is None
    assert sift.normalize_rel("", wt) is None


def test_dominant_reason_picks_most_common_failure(tmp_path: Path) -> None:
    rows = [
        {"failed_gates": ["parse"]},
        {"failed_gates": ["parse", "structural"]},
        {"failed_gates": ["parse"]},
        {"failed_gates": []},
    ]
    assert sift.dominant_reason(rows) == "parse_failed"
    assert sift.dominant_reason([{"failed_gates": []}]) == "no_blocking_gate"


# ──────────────────────────────── salvage build ─────────────────────────────

def _git_init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-c", "user.name=salvage-test", "-c", "user.email=t@example.invalid", "init", "-q", str(repo)],
        check=True,
        capture_output=True,
    )
    return repo


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = _git_init(tmp_path / "m1001")
    _write(wt / "alpha" / "widget.py", "VALUE = 1\n")
    _write(wt / "docs" / "note.md", "# note\n")
    return wt


def _repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in ("alpha", "docs", "artifacts", "tools"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_build_inheritable_state(worktree: Path, tmp_path: Path) -> None:
    record = _record("budget_exhausted", goal="必须产出 artifacts/ 与 tests/ 证据。")
    payload = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    assert payload["state"] == sift.INHERITABLE
    assert payload["blocked"] is None
    assert [r["path"] for r in payload["inheritable"]] == ["alpha/widget.py", "docs/note.md"]
    assert payload["counts"]["inheritable"] == 2
    assert payload["terminal"]["in_scope"] is True
    assert payload["residual"], "obligations must survive into the artefact"
    assert payload["continuation"]["seedable"] is True


def test_build_blocked_codes(worktree: Path, tmp_path: Path) -> None:
    record = _record("llm_error")
    missing = salvage.build(record, tmp_path / "nope", repo_root=_repo_root(tmp_path))
    assert missing["state"] == sift.BLOCKED
    assert missing["blocked"]["code"] == "no_worktree"

    empty = _git_init(tmp_path / "m2002")
    no_candidates = salvage.build(_record("llm_error", mission_id=2002), empty, repo_root=_repo_root(tmp_path))
    assert no_candidates["blocked"]["code"] == "no_candidates"

    broken = _git_init(tmp_path / "m3003")
    _write(broken / "alpha" / "widget.py", "def broken(:\n")
    parse_blocked = salvage.build(_record("timeout", mission_id=3003), broken, repo_root=_repo_root(tmp_path))
    assert parse_blocked["blocked"]["code"] == "parse_failed"

    exploding = _git_init(tmp_path / "m4004")
    _write(exploding / "alpha" / "widget.py", "raise RuntimeError('boom')\n")
    smoke_blocked = salvage.build(_record("timeout", mission_id=4004), exploding, repo_root=_repo_root(tmp_path))
    assert smoke_blocked["blocked"]["code"] == "smoke_failed"

    # structural gate: with a destination layout that does not contain the path's
    # root, the file is unmappable and the task is blocked for that reason.
    outside = _git_init(tmp_path / "m5005")
    _write(outside / "gamma" / "widget.py", "VALUE = 1\n")
    structural_blocked = salvage.build(
        _record("timeout", mission_id=5005), outside, repo_root=_repo_root(tmp_path), roots=("alpha", "docs")
    )
    assert structural_blocked["blocked"]["code"] == "structural_failed"


def test_build_is_byte_idempotent(worktree: Path, tmp_path: Path) -> None:
    record = _record("llm_error")
    first = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    second = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    assert salvage.canonical_json(first) == salvage.canonical_json(second)
    assert salvage.fingerprint(first) == salvage.fingerprint(second)
    assert first["continuation"]["subtask_id"] == second["continuation"]["subtask_id"]


def test_fingerprint_tracks_content_change(worktree: Path, tmp_path: Path) -> None:
    record = _record("llm_error")
    before = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    _write(worktree / "alpha" / "widget.py", "VALUE = 2\n")
    after = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    assert salvage.fingerprint(before) != salvage.fingerprint(after)


def test_write_and_residual_matching(worktree: Path, tmp_path: Path) -> None:
    record = _record("budget_exhausted", goal="必须交付 artifacts/alpha_report.json。另需人工复核。")
    payload = salvage.build(record, worktree, repo_root=_repo_root(tmp_path))
    path = salvage.write(payload, tmp_path / "out")
    assert path.name == "salvage.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["state"] == payload["state"]
    states = {row["item"][:12]: row["state"] for row in loaded["residual"]}
    assert any(state == "OPEN" for state in states.values()), "an unmatched obligation must stay OPEN"


def test_candidates_come_from_evidence_and_git(worktree: Path, tmp_path: Path) -> None:
    record = _record("llm_error")
    record["result"]["evidence"]["summary"] = {"changed_files": [str(worktree / "alpha" / "widget.py"), "docs/note.md"]}
    rels = dict(salvage._candidate_rels(record, worktree))
    assert rels["alpha/widget.py"] == "evidence.changed_files"
    assert "docs/note.md" in rels


# ─────────────────────────────── continuation ───────────────────────────────

def test_continuation_seed_shape(worktree: Path, tmp_path: Path) -> None:
    payload = salvage.build(_record("llm_error", goal="必须交付 tests/ 用例。"), worktree, repo_root=_repo_root(tmp_path))
    seed = payload["continuation"]
    assert seed["parent_mission_id"] == 1001
    assert seed["subtask_id"].startswith("cont-1001-")
    assert seed["seedable"] is True
    assert {row["path"] for row in seed["inputs"]} == {"alpha/widget.py", "docs/note.md"}
    assert set(seed["do_not_redo"]) == {"alpha/widget.py", "docs/note.md"}
    assert seed["budget_hint"]["inherited_files"] == 2
    assert 0 < seed["budget_hint"]["retry_scale"] <= 1


def test_continuation_blocked_seed_keeps_residual(worktree: Path, tmp_path: Path) -> None:
    empty = _git_init(tmp_path / "m6006")
    payload = salvage.build(_record("timeout", mission_id=6006, goal="必须交付 docs/ 说明。"), empty, repo_root=_repo_root(tmp_path))
    seed = payload["continuation"]
    assert seed["seedable"] is False
    assert "无文件通过三筛" in seed["goal"]
    assert seed["residual_open_count"] >= 1


def test_continuation_builder_is_pure(worktree: Path, tmp_path: Path) -> None:
    payload = salvage.build(_record("llm_error"), worktree, repo_root=_repo_root(tmp_path))
    again = continuation.build({k: v for k, v in payload.items() if k != "continuation"}, payload["fingerprint"]["provisional_sha256"])
    assert again == payload["continuation"]


# ─────────────────────────────── backtest gates ─────────────────────────────

def test_fisher_matches_reference_values() -> None:
    assert backtest.fisher_exact_2x2(3, 1, 1, 3)["p_value"] == pytest.approx(0.485714, abs=1e-6)
    assert backtest.fisher_exact_2x2(10, 0, 0, 10)["p_value"] == pytest.approx(1.1e-05, abs=1e-6)
    assert backtest.fisher_exact_2x2(9, 1, 2, 7)["p_value"] == pytest.approx(0.005477, abs=1e-6)
    assert backtest.fisher_exact_2x2(0, 0, 0, 0)["p_value"] == 1.0


def test_mcnemar_exact() -> None:
    assert backtest.mcnemar_exact(4, 0)["p_value"] == pytest.approx(0.125)
    assert backtest.mcnemar_exact(0, 0)["p_value"] == 1.0


def _mission(mid: int, files: list[dict], state: str, blocked: str | None, fp: str) -> dict:
    return {
        "mission_id": mid,
        "files": files,
        "oracle_files": [
            {"path": row["path"], "verdict": row.get("oracle", row["verdict"]), "failed_gates": []} for row in files
        ],
        "salvage": {"written": True, "state": state, "blocked_code": blocked, "fingerprint": fp, "bytes_sha256": fp},
        "salvage_second": {"fingerprint": fp, "bytes_sha256": fp, "equal_bytes": True},
    }


def test_consistency_and_false_positive_math() -> None:
    mission = _mission(
        1,
        [
            {"path": "alpha/a.py", "verdict": "INHERITABLE"},
            {"path": "alpha/b.py", "verdict": "INHERITABLE", "oracle": "BLOCKED"},
            {"path": "alpha/c.py", "verdict": "BLOCKED"},
            {"path": "alpha/d.py", "verdict": "BLOCKED"},
        ],
        "INHERITABLE",
        None,
        "fp1",
    )
    corpus = {"missions": [mission]}
    gate = backtest.gate_consistency(corpus)
    assert gate["judged_files"] == 4
    assert gate["agree"] == 3 and gate["disagree"] == 1
    assert gate["consistency"] == pytest.approx(0.75)
    assert gate["false_positives"] == 1
    assert gate["false_positive_rate"] == pytest.approx(0.5)
    assert gate["consistency_pass"] is False


def test_idempotency_gate_detects_divergence() -> None:
    corpus = {"missions": [_mission(1, [], "INHERITABLE", None, "abc")]}
    assert backtest.gate_idempotency(corpus)["idempotency_pass"] is True
    corpus["missions"][0]["salvage_second"]["fingerprint"] = "def"
    assert backtest.gate_idempotency(corpus)["idempotency_pass"] is False


def test_salvage_rate_gate() -> None:
    corpus = {"missions": [_mission(1, [], "INHERITABLE", None, "a"), _mission(2, [], "BLOCKED", "no_worktree", "b")]}
    gate = backtest.gate_salvage_rate(corpus)
    assert gate["terminal_tasks"] == 2 and gate["salvage_written"] == 2
    assert gate["rate"] == 1.0 and gate["state_blocked"] == 1
    assert gate["blocked_codes"] == {"no_worktree": 1}
    assert gate["salvage_rate_pass"] is True


def _family(idx: int, required: int, inherited: int, capacity: int) -> dict:
    names = [f"alpha/f{i}.py" for i in range(required)]
    return {
        "node_key": f"node-{idx}",
        "failed_mission_id": idx,
        "ok_mission_id": 1000 + idx,
        "required": names,
        "required_code": names,
        "inherited": names[:inherited],
        "capacity": capacity,
        "capacity_code": capacity,
    }


def test_replay_gate_detects_gain() -> None:
    families = [_family(i, required=6, inherited=4, capacity=3) for i in range(1, 11)]
    gate = backtest.gate_replay({"families": families})
    assert gate["pairs"] == 10
    assert gate["gain_pairs"] == 10
    assert gate["control_pass_rate"] == 0.0
    assert gate["enabled_pass_rate"] == 1.0
    assert gate["fisher"]["p_value"] < backtest.THRESHOLDS["replay_fisher_alpha"]
    assert gate["replay_gain_pass"] is True


def test_replay_gate_honest_fail_without_gain() -> None:
    families = [_family(i, required=6, inherited=0, capacity=9) for i in range(1, 11)]
    gate = backtest.gate_replay({"families": families})
    assert gate["gain_pairs"] == 0
    assert gate["enabled_pass_rate"] == gate["control_pass_rate"] == 1.0
    assert gate["replay_gain_pass"] is False


def test_replay_gate_requires_min_pairs() -> None:
    families = [_family(i, required=6, inherited=4, capacity=3) for i in range(1, 4)]
    assert backtest.gate_replay({"families": families})["replay_gain_pass"] is False


def test_aggregate_reports_every_gate() -> None:
    missions = [_mission(1, [{"path": "alpha/a.py", "verdict": "INHERITABLE"}], "INHERITABLE", None, "x")]
    families = [_family(i, 6, 4, 3) for i in range(1, 11)]
    report = backtest.aggregate({"missions": missions, "families": families})
    assert set(report["verdict"]) == {"salvage_rate", "consistency", "false_positive", "idempotency", "replay_gain"}
    assert report["all_pass"] is True


# ─────────────────────────────── zero-domain guard ──────────────────────────

def test_package_and_tests_carry_no_domain_vocabulary() -> None:
    """Run the platform denylist scanner over this package: zero hits required."""
    from tools.audit import domain_leak_scan

    package_dir = Path(__file__).resolve().parents[1]
    root = package_dir.parents[1]
    result = domain_leak_scan.scan(paths=[str(package_dir)], root=root)
    hits = result.get("hits") or result.get("matches") or []
    assert hits == [], f"domain vocabulary leaked into the mechanism: {hits[:3]}"