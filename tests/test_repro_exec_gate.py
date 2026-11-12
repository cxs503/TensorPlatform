#!/usr/bin/env python3
"""Acceptance tests for ``tools/repro_exec_gate`` (delivery-time reproducibility gate).

The gate under test answers one question: *does the delivery contract's
``repro_cmd`` actually run and reproduce its declared artifacts?* The fixtures
below are synthetic and domain-free by construction; every case asserts the
exact ``reason`` enum value, not just pass/fail, because a gate that reports the
right verdict for the wrong reason is not auditable.

Coverage map (the six fixture classes required by the acceptance criteria):

======  ==========================================  ==========================
class   fixture                                      asserted reason
======  ==========================================  ==========================
PASS-1  command writes a file, exits 0               OK
PASS-2  command generates an artifact, sha256 match  OK
PASS-3  command with no artifact declaration         OK
BAD     unusable worktree / cwd                      SPAWN_ERROR
NONZERO command exits 3, prints to stderr            EXIT_NONZERO
ARTIF   declared artifact absent / digest mismatch   ARTIFACT_MISSING /
                                                     ARTIFACT_SHA_MISMATCH
TIME    command outlives the hard timeout            TIMEOUT + no survivors
======  ==========================================  ==========================

Plus: CLI exit-code contract (0/1/2), sealed ``--json-out`` artifact, the
zero-domain-vocabulary assertion on this package's own source, and a
core-vs-CLI agreement test that guards against judge-logic drift.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = REPO_ROOT / "tools" / "repro_exec_gate"

from tools.repro_exec_gate import cli, core, report  # noqa: E402

REQUIRED_REASONS = {"OK", "NO_REPRO_CMD", "EXIT_NONZERO", "ARTIFACT_MISSING",
                    "ARTIFACT_SHA_MISMATCH", "TIMEOUT", "SPAWN_ERROR"}


# ── helpers ─────────────────────────────────────────────────────────────────
def write_contract(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_cli(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Invoke the frozen CLI in a child interpreter (real exit code, real stdout)."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run([sys.executable, "-m", "tools.repro_exec_gate", *args],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=timeout, env=env)


def stdout_payload(proc: subprocess.CompletedProcess) -> dict:
    """The CLI must print exactly one JSON line on stdout."""
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected a single stdout line, got {len(lines)}: {lines!r}"
    payload = json.loads(lines[0])
    for key in ("status", "reason", "exit_code", "duration_s",
                "missing_artifacts", "log_tail"):
        assert key in payload, f"missing frozen key {key!r}"
    assert payload["status"] in ("PASS", "FAIL")
    return payload


def pids_matching(token: str) -> list[int]:
    """Pids whose command line contains ``token`` (``/proc`` scan, no third-party dep)."""
    hits: list[int] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():  # pragma: no cover - non-Linux hosts
        return hits
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if token.encode() in raw:
            hits.append(int(entry.name))
    return hits


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


# ── PASS fixtures ───────────────────────────────────────────────────────────
def test_pass_plain_command(tmp_path: Path, worktree: Path):
    """PASS-1: a normal command produces a file and exits 0."""
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "printf 'a\\nb\\n' > out.txt"})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["status"] == "PASS"
    assert verdict["reason"] == "OK"
    assert verdict["exit_code"] == 0
    assert verdict["missing_artifacts"] == []
    assert (worktree / "out.txt").read_text() == "a\nb\n"
    assert verdict["duration_s"] >= 0.0


def test_pass_generated_artifact_sha256_matches(tmp_path: Path, worktree: Path):
    """PASS-2: the command regenerates an artifact whose sha256 matches the contract."""
    generated = "payload-42\n"
    contract = write_contract(tmp_path / "c.json", {
        "repro_cmd": "printf 'payload-42\\n' > result.txt",
        "artifacts": [{"path": "result.txt", "sha256": sha256_text(generated)}],
    })
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "OK", verdict
    assert core.sha256_file(worktree / "result.txt") == sha256_text(generated)


def test_pass_no_artifact_declaration(tmp_path: Path, worktree: Path):
    """PASS-3: artifacts are optional; exit code alone decides."""
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "true"})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert (verdict["status"], verdict["reason"], verdict["exit_code"]) == ("PASS", "OK", 0)


def test_pass_nested_output_and_artifact_without_digest(tmp_path: Path, worktree: Path):
    """A declared artifact with no sha256 is an existence check only."""
    contract = write_contract(tmp_path / "c.json", {
        "repro_cmd": "mkdir -p sub && echo hi > sub/out.txt",
        "artifacts": [{"path": "sub/out.txt"}],
    })
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "OK", verdict


# ── failing fixtures ────────────────────────────────────────────────────────
def test_spawn_error_on_unusable_worktree(tmp_path: Path):
    """BAD path: cwd does not exist -> the process cannot be created at all."""
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "true"})
    verdict = core.evaluate(core.load_contract(contract), tmp_path / "no_such_dir")
    assert verdict["reason"] == "SPAWN_ERROR", verdict
    assert verdict["status"] == "FAIL"
    assert "spawn error" in verdict["log_tail"]


def test_exit_nonzero_carries_status_and_stderr(tmp_path: Path, worktree: Path):
    """NONZERO: exit code is reported verbatim and merged stderr survives in the tail."""
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "echo boom >&2; exit 3"})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "EXIT_NONZERO"
    assert verdict["exit_code"] == 3
    assert "boom" in verdict["log_tail"]


def test_exit_nonzero_takes_precedence_over_missing_artifact(tmp_path: Path, worktree: Path):
    """A broken run must not be re-labelled as an artifact problem."""
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "exit 9", "artifacts": [{"path": "never.txt"}]})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "EXIT_NONZERO"
    assert verdict["exit_code"] == 9


def test_artifact_missing(tmp_path: Path, worktree: Path):
    """ARTIF: command succeeds but never produces the declared artifact."""
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "true", "artifacts": [{"path": "absent.txt"}]})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "ARTIFACT_MISSING"
    assert verdict["missing_artifacts"] == ["absent.txt"]
    assert verdict["exit_code"] == 0


def test_artifact_sha_mismatch(tmp_path: Path, worktree: Path):
    """ARTIF: artifact exists but its bytes differ from the declared digest."""
    contract = write_contract(tmp_path / "c.json", {
        "repro_cmd": "printf 'actual\\n' > result.txt",
        "artifacts": [{"path": "result.txt", "sha256": "0" * 64}],
    })
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "ARTIFACT_SHA_MISMATCH"
    assert verdict["missing_artifacts"] == ["result.txt"]
    assert verdict["exit_code"] == 0
    assert "declared" in verdict["log_tail"] and "actual" in verdict["log_tail"]


def test_no_repro_cmd_is_fail_not_usage_error(tmp_path: Path, worktree: Path):
    """A contract without a command is a legitimate FAIL verdict (exit 1), not a crash."""
    contract = write_contract(tmp_path / "c.json", {"artifacts": [{"path": "x"}]})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert (verdict["status"], verdict["reason"], verdict["exit_code"]) == \
        ("FAIL", "NO_REPRO_CMD", None)

    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
    assert proc.returncode == 1, proc.stderr
    assert stdout_payload(proc)["reason"] == "NO_REPRO_CMD"


def test_blank_repro_cmd_is_treated_as_absent(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "   "})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    assert verdict["reason"] == "NO_REPRO_CMD"


# ── timeout: verdict + no orphan processes ──────────────────────────────────
def test_timeout_kills_whole_process_group(tmp_path: Path, worktree: Path):
    """TIME: hard timeout -> TIMEOUT, and neither the child nor its grandchild survives.

    The duration token doubles as a unique process marker, so the assertion cannot
    be satisfied by an unrelated ``sleep`` from another tenant on the same host.
    The marker is also sampled *during* the run: an empty survivor set is only
    meaningful if the processes actually existed (non-vacuous instrumentation).
    """
    token = f"98765.{uuid.uuid4().int % 1_000_000:06d}"
    contract = write_contract(tmp_path / "c.json", {
        "repro_cmd": f"sleep {token} & sleep {token}",
        "timeout_s": 1.0,
    })
    observed: list[list[int]] = []
    stop = threading.Event()

    def sampler() -> None:  # proves the group really was running before the kill
        while not stop.is_set():
            hits = pids_matching(token)
            if hits:
                observed.append(hits)
            time.sleep(0.02)

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()
    try:
        verdict = core.evaluate(core.load_contract(contract), worktree)
    finally:
        stop.set()
        sampler_thread.join(timeout=5)

    try:
        assert verdict["reason"] == "TIMEOUT", verdict
        assert verdict["status"] == "FAIL"
        assert verdict["exit_code"] is None
        assert 0.8 <= verdict["duration_s"] <= 1.0 + core.KILL_GRACE_S + 3.0
        assert observed, "instrumentation was vacuous: marker never seen while running"
        assert max(len(hits) for hits in observed) >= 2, "grandchild never spawned"
        assert "process group killed" in verdict["log_tail"]
        assert "survivors" not in verdict["log_tail"], verdict["log_tail"]

        survivors: list[int] = []
        deadline = time.time() + 5.0
        while time.time() < deadline:  # allow the kernel a moment to reap
            survivors = pids_matching(token)
            if not survivors:
                break
            time.sleep(0.05)
        assert survivors == [], f"orphaned processes after timeout: {survivors}"
    finally:  # never leak a stray process into the suite even on failure
        for pid in pids_matching(token):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_cli_timeout_override_beats_contract(tmp_path: Path, worktree: Path):
    """--timeout wins over the contract's own timeout_s."""
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "sleep 5", "timeout_s": 30})
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree),
                    "--timeout", "0.5"])
    assert proc.returncode == 1
    payload = stdout_payload(proc)
    assert payload["reason"] == "TIMEOUT"
    assert payload["duration_s"] < 5.0


# ── log tail ────────────────────────────────────────────────────────────────
def test_log_tail_is_last_40_lines(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "for i in $(seq 1 60); do echo line-$i; done"})
    verdict = core.evaluate(core.load_contract(contract), worktree)
    lines = verdict["log_tail"].splitlines()
    assert len(lines) == core.LOG_TAIL_LINES == 40
    assert lines[-1] == "line-60"
    assert lines[0] == "line-21"


# ── CLI exit-code contract (0 / 1 / 2) ──────────────────────────────────────
def test_cli_exit_0_on_pass(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "echo fine"})
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
    assert proc.returncode == 0, proc.stderr
    payload = stdout_payload(proc)
    assert (payload["status"], payload["reason"], payload["exit_code"]) == ("PASS", "OK", 0)


def test_cli_exit_1_on_fail(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "exit 4"})
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
    assert proc.returncode == 1, proc.stderr
    assert stdout_payload(proc)["reason"] == "EXIT_NONZERO"


def test_cli_exit_2_when_contract_absent(tmp_path: Path, worktree: Path):
    proc = run_cli(["--contract", str(tmp_path / "missing.json"),
                    "--worktree", str(worktree)])
    assert proc.returncode == 2
    payload = stdout_payload(proc)
    assert payload["reason"] == "USAGE_ERROR"
    assert "not found" in payload["log_tail"]


def test_cli_exit_2_when_contract_json_invalid(tmp_path: Path, worktree: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = run_cli(["--contract", str(bad), "--worktree", str(worktree)])
    assert proc.returncode == 2
    assert stdout_payload(proc)["reason"] == "USAGE_ERROR"


def test_cli_exit_2_when_argument_missing(worktree: Path):
    proc = run_cli(["--worktree", str(worktree)])  # --contract missing
    assert proc.returncode == 2
    assert stdout_payload(proc)["reason"] == "USAGE_ERROR"


def test_cli_exit_2_on_structurally_invalid_contract(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "true", "artifacts": [{"sha256": "0" * 64}]})
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
    assert proc.returncode == 2
    assert "artifacts[0].path" in stdout_payload(proc)["log_tail"]


def test_cli_exit_2_on_bad_artifact_digest(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "true", "artifacts": [{"path": "a", "sha256": "zz"}]})
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
    assert proc.returncode == 2


# ── sealed --json-out artifact ──────────────────────────────────────────────
def test_json_out_is_sealed_and_self_verifiable(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json",
                              {"repro_cmd": "echo tail-thing", "artifacts": []})
    out = tmp_path / "artifacts" / "gate.json"
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree),
                    "--json-out", str(out)])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert report.verify_seal(payload), "payload_sha256 does not match its own body"
    assert payload["verdict"]["reason"] == "OK"
    assert payload["log_sha256"] == sha256_text("tail-thing\n")
    assert payload["log_bytes"] == len("tail-thing\n")
    assert payload["schema_version"] == report.REPORT_VERSION
    # tampering with the body must break the seal
    payload["verdict"]["reason"] = "EXIT_NONZERO"
    assert not report.verify_seal(payload)


def test_json_out_written_on_failure_too(tmp_path: Path, worktree: Path):
    contract = write_contract(tmp_path / "c.json", {"repro_cmd": "exit 2"})
    out = tmp_path / "gate.json"
    proc = run_cli(["--contract", str(contract), "--worktree", str(worktree),
                    "--json-out", str(out)])
    assert proc.returncode == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"]["reason"] == "EXIT_NONZERO"
    assert report.verify_seal(payload)


# ── invariants of the package itself ────────────────────────────────────────
def test_reason_enum_covers_frozen_contract():
    assert REQUIRED_REASONS.issubset(set(core.REASONS))
    assert core.REASONS[0] == "OK"  # OK first: precedence order is documented top-down
    assert report.STDOUT_KEYS == ("status", "reason", "exit_code", "duration_s",
                                 "missing_artifacts", "log_tail")


def test_core_and_cli_agree_on_every_fixture(tmp_path: Path, worktree: Path):
    """The judge logic must have exactly one implementation (no drift)."""
    cases = [
        {"repro_cmd": "true"},
        {"repro_cmd": "exit 5"},
        {"artifacts": []},
        {"repro_cmd": "true", "artifacts": [{"path": "gone.txt"}]},
        {"repro_cmd": "echo x > a.txt", "artifacts": [{"path": "a.txt", "sha256": "1" * 64}]},
    ]
    for i, payload in enumerate(cases):
        contract = write_contract(tmp_path / f"c{i}.json", payload)
        proc = run_cli(["--contract", str(contract), "--worktree", str(worktree)])
        from_cli = stdout_payload(proc)
        from_core = core.evaluate(core.load_contract(contract), worktree)
        assert (from_cli["status"], from_cli["reason"], from_cli["exit_code"]) == \
            (from_core["status"], from_core["reason"], from_core["exit_code"]), payload


FORBIDDEN_TERMS = (
    # Chinese domain vocabulary
    "船", "冰", "阻力", "雷诺", "推进", "耐波", "操纵", "稳性", "螺旋桨", "冰区", "结构强度",
    # Latin domain vocabulary (matched as whole tokens: "device" must not trip "ice")
    "ship", "hull", "ice", "lbm", "fvm", "fem", "bem", "sph", "dem", "dg", "cad",
    "reynolds", "resistance", "propeller", "seakeeping", "cavitation", "hydrodynamics",
)


def test_zero_domain_vocabulary_in_package_source():
    """Platform rule: common machinery carries no domain semantics (contract injects data)."""
    offenders: list[str] = []
    files = sorted(PKG_DIR.rglob("*.py"))
    assert files, f"no sources found under {PKG_DIR}"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN_TERMS:
            pattern = term if not term.isascii() else rf"(?<![a-z0-9]){term}(?![a-z0-9])"
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.name}:{line}: {term}")
    assert offenders == [], "domain vocabulary leaked into platform tooling: " + \
        "; ".join(offenders[:10])


def test_package_import_has_no_side_effects(tmp_path: Path):
    """Importing the package must not execute commands or create files."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run([sys.executable, "-c",
                           "import tools.repro_exec_gate as m; print(m.__version__)"],
                          cwd=str(tmp_path), capture_output=True, text=True,
                          env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == core.SCHEMA_VERSION

# ── report instruments stay tied to the suite ───────────────────────────────
def test_fixture_matrix_reproduces_every_verdict_class():
    """The report's measuring instrument must agree with the tests (no drift)."""
    from tools.repro_exec_gate import matrix

    result = matrix.run_matrix()
    summary = result["summary"]
    assert summary["all_match"], [r for r in result["rows"] if not r["match"]]
    assert summary["cases"] == len(matrix.CASES) == 9
    assert {r["observed_reason"] for r in result["rows"]} == REQUIRED_REASONS


def test_selftest_contract_points_at_this_suite():
    """The self-check must execute the acceptance suite, not something weaker."""
    from tools.repro_exec_gate import selftest

    contract = selftest.build_contract(REPO_ROOT)
    assert selftest.SELFTEST_SUITE in contract["repro_cmd"]
    declared = contract["artifacts"][0]
    assert declared["path"] == selftest.SELFTEST_SUITE
    assert declared["sha256"] == core.sha256_file(REPO_ROOT / selftest.SELFTEST_SUITE)
