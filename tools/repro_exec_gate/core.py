#!/usr/bin/env python3
"""Delivery-time reproducibility **execution** gate (domain-neutral).

Why this module exists
----------------------
``tools/evidence_grade.py`` rule R02 only asserts that the contract *carries* a
``repro_cmd`` field (``if not contract.get("repro_cmd"): fail("R02_REPRO_CMD")``).
Field presence is a declaration, not a capability: a contract can name a command
that does not exist, exits non-zero, needs network, or never regenerates the
artifacts it claims. That gap is measured, not hypothetical -- deliveries were
graded L1/L2 because their declared command could not actually run.

This module closes the gap by **executing** the declared command inside the
delivery worktree and judging the outcome:

    verdict = execute(contract, worktree)     # side effects confined to worktree

Division of labour (deliberate, do not merge):

* ``tools/evidence_grade.py``  -> existence/structure of the declarations.
* ``tools/repro_exec_gate``    -> whether the declarations survive execution.

Contract (JSON, everything domain-specific arrives through this file)
---------------------------------------------------------------------
=====  ==================  ==========================================
field  required            meaning
=====  ==================  ==========================================
repro_cmd  yes             shell command line, executed with cwd=worktree
artifacts  no              ``[{"path": REL, "sha256": HEX}]`` to check after
timeout_s  no              per-contract hard timeout (CLI --timeout wins)
=====  ==================  ==========================================

Verdict (first failing check decides; ``reason`` is the machine-readable enum)
-----------------------------------------------------------------------------
===========================  ===============================================
reason                        meaning
===========================  ===============================================
OK                            command exited 0 and every artifact verified
NO_REPRO_CMD                  contract declares no command -> nothing to run
SPAWN_ERROR                   the process could not be created (bad cwd/binary)
TIMEOUT                       hard timeout hit; process group was killed
EXIT_NONZERO                  command exited non-zero
ARTIFACT_MISSING              a declared artifact was not produced
ARTIFACT_SHA_MISMATCH         artifact bytes differ from the declared digest
===========================  ===============================================

Design rules
------------
* Zero domain vocabulary: this file mentions no formula, unit, substance or
  field name. ``tests/test_repro_exec_gate.py`` greps this package for a
  forbidden vocabulary list to keep it that way.
* Hard timeout always kills the **whole process group** (``start_new_session``
  + ``killpg``), so a gate expiry cannot leave detached children behind.
* Output is spooled to an unlinked temporary file, never to a pipe: a surviving
  grandchild cannot block the gate through an inherited pipe descriptor.
* Only files and processes are touched; no network, no import of the graded
  subject, no writes outside ``worktree`` (except the caller's ``--json-out``).
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "repro-exec-gate/1.0"
DEFAULT_TIMEOUT_S = 120.0
LOG_TAIL_LINES = 40
LOG_HASH_CHUNK = 1 << 20
KILL_GRACE_S = 5.0
POLL_S = 0.02

#: machine-readable reasons, in precedence order (first failure wins)
REASONS = (
    "OK",
    "NO_REPRO_CMD",
    "SPAWN_ERROR",
    "TIMEOUT",
    "EXIT_NONZERO",
    "ARTIFACT_MISSING",
    "ARTIFACT_SHA_MISMATCH",
)
#: not a verdict: emitted only on exit code 2 (the gate could not even start)
USAGE_REASON = "USAGE_ERROR"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"


class ContractInvalid(ValueError):
    """Contract is not usable at all (bad JSON shape) -> CLI usage error."""


# ── contract ────────────────────────────────────────────────────────────────
def load_contract(path: str | os.PathLike) -> dict:
    """Read a contract file. Raises :class:`ContractInvalid` on unusable input."""
    p = Path(path)
    if not p.is_file():
        raise ContractInvalid(f"contract not found: {p}")
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractInvalid(f"contract unreadable: {p}: {exc}") from exc
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractInvalid(f"contract is not valid JSON: {exc}") from exc
    if not isinstance(contract, dict):
        raise ContractInvalid("contract must be a JSON object")
    return contract


def normalize_contract(contract: Mapping[str, Any]) -> dict:
    """Validate shape; return ``{"repro_cmd": str|None, "artifacts": [...], "timeout_s": float|None}``.

    A *missing* command is not an error here: it is a legitimate FAIL verdict
    (``NO_REPRO_CMD``). Structurally broken artifacts are a usage error.
    """
    if not isinstance(contract, Mapping):
        raise ContractInvalid("contract must be an object")

    cmd = contract.get("repro_cmd")
    if cmd is not None and not isinstance(cmd, str):
        raise ContractInvalid("repro_cmd must be a string")
    cmd = (cmd or "").strip() or None

    artifacts_raw = contract.get("artifacts") or []
    if not isinstance(artifacts_raw, list):
        raise ContractInvalid("artifacts must be an array")
    artifacts: list[dict] = []
    for i, entry in enumerate(artifacts_raw):
        if not isinstance(entry, Mapping):
            raise ContractInvalid(f"artifacts[{i}] must be an object")
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel.strip():
            raise ContractInvalid(f"artifacts[{i}].path must be a non-empty string")
        digest = entry.get("sha256")
        if digest is not None:
            digest = str(digest).strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ContractInvalid(f"artifacts[{i}].sha256 must be 64 hex chars")
        artifacts.append({"path": rel.strip(), "sha256": digest or None})

    timeout_s = contract.get("timeout_s")
    if timeout_s is not None:
        try:
            timeout_s = float(timeout_s)
        except (TypeError, ValueError) as exc:
            raise ContractInvalid("timeout_s must be a number") from exc
        if not (timeout_s > 0):
            raise ContractInvalid("timeout_s must be > 0")

    return {"repro_cmd": cmd, "artifacts": artifacts, "timeout_s": timeout_s}


# ── process execution ───────────────────────────────────────────────────────
@dataclass
class ExecResult:
    """Outcome of one command execution."""

    exit_code: int | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    spawn_error: str | None = None
    log_tail: str = ""
    log_bytes: int = 0
    log_sha256: str = ""
    pgid: int | None = None
    leftover_pids: list[int] = field(default_factory=list)


def _kill_group(pgid: int | None) -> None:
    """SIGKILL a whole process group (shell, command and any grandchild in it)."""
    if not pgid:
        return
    for _ in range(2):  # second pass catches children forked during the first
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(POLL_S)


def _proc_state(pid: int) -> str | None:
    """Process state letter from /proc (``None`` if already gone)."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:  # comm may contain spaces/parentheses: split after the last ')'
        return raw.rsplit(")", 1)[1].split()[0]
    except IndexError:  # pragma: no cover
        return None


def _live_group_members(pgid: int | None, exclude: Sequence[int] = ()) -> list[int]:
    """Pids still *running* inside ``pgid`` (reaped/zombie slots are not survivors)."""
    if not pgid:
        return []
    skip = set(exclude)
    members: list[int] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():  # pragma: no cover - non-Linux hosts
        return members
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        pid = int(entry.name)
        try:
            if os.getpgid(pid) != pgid:
                continue
        except (ProcessLookupError, PermissionError, OSError):
            continue
        if _proc_state(pid) in (None, "Z"):
            continue
        members.append(pid)
    return members


def _read_log(path: str) -> tuple[str, int, str]:
    """Return ``(tail_text, total_bytes, sha256_of_full_log)``."""
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(LOG_HASH_CHUNK), b""):
            digest.update(chunk)
        keep = min(size, 64 * 1024 * LOG_TAIL_LINES * 4)
        if keep:
            handle.seek(size - keep)
            tail = handle.read(keep).decode("utf-8", "replace")
        else:
            tail = ""
    lines = tail.splitlines()
    return "\n".join(lines[-LOG_TAIL_LINES:]), size, digest.hexdigest()


def run_command(cmd: str, cwd: str | os.PathLike, timeout_s: float = DEFAULT_TIMEOUT_S,
                env: Mapping[str, str] | None = None) -> ExecResult:
    """Execute ``cmd`` through the shell with ``cwd``, enforcing a hard timeout.

    On timeout the whole process group is SIGKILLed (the shell, the command and
    any grandchild that stayed in the group); survivors are reported in
    :attr:`ExecResult.leftover_pids` and never left silently running.
    """
    started = time.monotonic()
    spool = tempfile.NamedTemporaryFile(prefix="repro_exec_gate_", suffix=".log", delete=False)
    spool_path = spool.name
    spool.close()
    result = ExecResult()
    try:
        with open(spool_path, "wb") as sink:
            try:
                proc = subprocess.Popen(  # noqa: S602 - shell is the declared contract
                    cmd,
                    shell=True,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=dict(env) if env is not None else None,
                )
            except OSError as exc:  # missing cwd, unexecutable shell, ENOMEM ...
                result.spawn_error = f"{type(exc).__name__}: {exc}"
                result.duration_s = round(time.monotonic() - started, 3)
                result.log_tail = f"spawn error: {result.spawn_error}"
                return result

            result.pgid = proc.pid  # start_new_session => pid is the group leader
            try:
                result.exit_code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                result.timed_out = True
                _kill_group(result.pgid)
                try:  # reap the leader before judging survivors
                    proc.wait(timeout=KILL_GRACE_S)
                except subprocess.TimeoutExpired:  # pragma: no cover - kernel defers
                    pass
                result.leftover_pids = _live_group_members(result.pgid, exclude=(proc.pid,))
                result.exit_code = None
            finally:
                result.duration_s = round(time.monotonic() - started, 3)

        tail, size, digest = _read_log(spool_path)
        result.log_tail, result.log_bytes, result.log_sha256 = tail, size, digest
        return result
    finally:
        try:
            os.unlink(spool_path)
        except OSError:  # pragma: no cover
            pass


# ── artifacts ───────────────────────────────────────────────────────────────
def sha256_file(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(LOG_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_artifacts(artifacts: Sequence[Mapping[str, Any]], worktree: str | os.PathLike) -> list[dict]:
    """Verify each declared artifact; return one detail record per artifact."""
    root = Path(worktree)
    details: list[dict] = []
    for entry in artifacts:
        rel = str(entry["path"])
        target = Path(rel)
        if not target.is_absolute():
            target = root / target
        declared = entry.get("sha256")
        record = {"path": rel, "declared_sha256": declared, "status": "OK",
                  "actual_sha256": None}
        if not target.is_file():
            record["status"] = "MISSING"
        else:
            actual = sha256_file(target)
            record["actual_sha256"] = actual
            if declared and actual != declared:
                record["status"] = "SHA_MISMATCH"
        details.append(record)
    return details


# ── verdict ─────────────────────────────────────────────────────────────────
def make_verdict(reason: str, exit_code: int | None = None, duration_s: float = 0.0,
                 missing_artifacts: Sequence[str] = (), log_tail: str = "",
                 status: str | None = None) -> dict:
    if reason not in REASONS + (USAGE_REASON,):  # pragma: no cover - programming error
        raise ValueError(f"unknown reason {reason!r}")
    if status is None:
        status = STATUS_PASS if reason == "OK" else STATUS_FAIL
    return {
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
        "duration_s": round(float(duration_s), 3),
        "missing_artifacts": list(missing_artifacts),
        "log_tail": log_tail,
    }


def judge(run: ExecResult | None, artifacts: Sequence[Mapping[str, Any]],
          worktree: str | os.PathLike) -> tuple[dict, list[dict]]:
    """Map one execution + artifact check onto the reason enum (precedence order).

    Returns ``(verdict, artifact_details)``. Precedence: an unrunnable, timed-out
    or failing command is reported before artifacts are inspected, because a
    digest check on a broken run would hide the real cause.
    """
    if run is None:
        return make_verdict("NO_REPRO_CMD",
                            log_tail="contract declares no repro_cmd: nothing to execute"), []
    if run.spawn_error:
        return make_verdict("SPAWN_ERROR", duration_s=run.duration_s,
                            log_tail=run.log_tail), []
    if run.timed_out:
        note = "hard timeout hit; process group killed"
        if run.leftover_pids:
            note += f"; survivors: {sorted(run.leftover_pids)}"
        return make_verdict("TIMEOUT", exit_code=None, duration_s=run.duration_s,
                            log_tail=note), []
    if run.exit_code != 0:
        return make_verdict("EXIT_NONZERO", exit_code=run.exit_code,
                            duration_s=run.duration_s, log_tail=run.log_tail), []

    details = check_artifacts(artifacts, worktree)
    bad = [d["path"] for d in details if d["status"] != "OK"]
    missing = [d for d in details if d["status"] == "MISSING"]
    mismatched = [d for d in details if d["status"] == "SHA_MISMATCH"]
    if missing:
        return make_verdict("ARTIFACT_MISSING", exit_code=run.exit_code,
                            duration_s=run.duration_s, missing_artifacts=bad,
                            log_tail=run.log_tail), details
    if mismatched:
        lines = "\n".join(f"{d['path']}: declared {d['declared_sha256']} "
                           f"actual {d['actual_sha256']}" for d in mismatched)
        return make_verdict("ARTIFACT_SHA_MISMATCH", exit_code=run.exit_code,
                            duration_s=run.duration_s, missing_artifacts=bad,
                            log_tail=(run.log_tail + "\n" + lines).strip()), details
    return make_verdict("OK", exit_code=run.exit_code, duration_s=run.duration_s,
                        log_tail=run.log_tail), details


def resolve_timeout(spec: Mapping[str, Any], timeout_s: float | None = None) -> float:
    """CLI override wins, then the contract value, then the package default."""
    chosen = timeout_s if timeout_s is not None else spec.get("timeout_s")
    limit = float(chosen if chosen is not None else DEFAULT_TIMEOUT_S)
    if not (limit > 0):
        raise ContractInvalid("timeout must be > 0")
    return limit


def run_and_judge(spec: Mapping[str, Any], worktree: str | os.PathLike,
                  timeout_s: float | None = None) -> tuple[dict, ExecResult | None, list[dict]]:
    """Execute the normalized contract and judge it. Returns ``(verdict, run, details)``."""
    limit = resolve_timeout(spec, timeout_s)
    cmd = spec.get("repro_cmd")
    if not cmd:
        verdict, details = judge(None, (), worktree)
        return verdict, None, details
    run = run_command(cmd, worktree, timeout_s=limit)
    verdict, details = judge(run, spec.get("artifacts") or (), worktree)
    return verdict, run, details


def evaluate(contract: Mapping[str, Any], worktree: str | os.PathLike,
             timeout_s: float | None = None) -> dict:
    """Execute the contract's command in ``worktree`` and return the verdict dict."""
    spec = normalize_contract(contract)
    verdict, _run, _details = run_and_judge(spec, worktree, timeout_s=timeout_s)
    return verdict


def evaluate_path(contract_path: str | os.PathLike, worktree: str | os.PathLike,
                  timeout_s: float | None = None) -> tuple[dict, dict, dict]:
    """Load + execute. Returns ``(verdict, contract, spec)``; raises ``ContractInvalid``."""
    contract = load_contract(contract_path)
    spec = normalize_contract(contract)
    return evaluate(contract, worktree, timeout_s=timeout_s), contract, spec
