#!/usr/bin/env python3
"""Rendering / persistence helpers for the reproducibility execution gate.

Everything here is pure bookkeeping: turn a verdict into the one-line JSON that
the gate contract freezes, and (optionally) persist a self-describing artifact
whose own body digest can be recomputed by a third party.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import core

REPORT_VERSION = "repro-exec-gate-report/1.0"
#: frozen key order of the stdout line (a consumer may rely on the single line)
STDOUT_KEYS = ("status", "reason", "exit_code", "duration_s",
               "missing_artifacts", "log_tail")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used for every digest in this package."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_stdout(verdict: Mapping[str, Any]) -> str:
    """Single-line JSON payload (heartbeat: no line breaks, even inside log_tail)."""
    payload = {key: verdict.get(key) for key in STDOUT_KEYS}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_artifact(verdict: Mapping[str, Any], *, contract_path: str | os.PathLike,
                   worktree: str | os.PathLike, repro_cmd: str | None,
                   artifact_details: Sequence[Mapping[str, Any]] = (),
                   log_sha256: str = "", log_bytes: int = 0,
                   timeout_s: float | None = None,
                   command: Sequence[str] = ()) -> dict:
    """Assemble the persisted report body (``payload_sha256`` excluded)."""
    body = {
        "schema_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contract": str(contract_path),
        "worktree": str(Path(worktree).resolve()) if Path(worktree).exists() else str(worktree),
        "repro_cmd": repro_cmd,
        "timeout_s": timeout_s,
        "invocation": list(command),
        "verdict": {key: verdict.get(key) for key in STDOUT_KEYS},
        "artifact_details": [dict(d) for d in artifact_details],
        "log_sha256": log_sha256,
        "log_bytes": log_bytes,
        "gate": {"version": core.SCHEMA_VERSION, "reasons": list(core.REASONS)},
    }
    return body


def sealed(body: Mapping[str, Any]) -> dict:
    """Attach ``payload_sha256`` = digest of the body with that field removed.

    Verifiable offline: drop the field, re-encode canonically, re-hash.
    """
    payload = dict(body)
    payload.pop("payload_sha256", None)
    payload["payload_sha256"] = sha256_text(canonical_json(payload))
    return payload


def verify_seal(payload: Mapping[str, Any]) -> bool:
    declared = payload.get("payload_sha256")
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    return bool(declared) and declared == sha256_text(canonical_json(body))


def write_json_out(path: str | os.PathLike, payload: Mapping[str, Any]) -> Path:
    """Write the sealed payload; return the path actually written."""
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False,
                                 sort_keys=True) + "\n", encoding="utf-8")
    return target


def render_markdown(verdict: Mapping[str, Any], *, contract_path: str,
                    worktree: str, repro_cmd: str | None,
                    artifact_details: Sequence[Mapping[str, Any]] = ()) -> str:
    """Human-readable summary of one gate run (for reports / logs)."""
    rows = [
        ("status", verdict.get("status")),
        ("reason", verdict.get("reason")),
        ("exit_code", verdict.get("exit_code")),
        ("duration_s", verdict.get("duration_s")),
        ("missing_artifacts", ", ".join(verdict.get("missing_artifacts") or []) or "-"),
        ("contract", contract_path),
        ("worktree", worktree),
        ("repro_cmd", repro_cmd or "-"),
    ]
    lines = ["# Reproducibility execution gate", "", "| key | value |", "| --- | --- |"]
    def _cell(value) -> str:
        return str(value).replace("|", "\\|")

    lines += ["| {} | {} |".format(k, _cell(v)) for k, v in rows]
    if artifact_details:
        lines += ["", "| artifact | declared sha256 | actual sha256 | status |",
                  "| --- | --- | --- | --- |"]
        for d in artifact_details:
            lines.append("| {path} | {declared_sha256} | {actual_sha256} | {status} |".format(
                path=d.get("path"),
                declared_sha256=(d.get("declared_sha256") or "-")[:16],
                actual_sha256=(d.get("actual_sha256") or "-")[:16],
                status=d.get("status")))
    tail = (verdict.get("log_tail") or "").strip()
    if tail:
        lines += ["", "## log tail (last %d lines)" % core.LOG_TAIL_LINES, "", "```", tail, "```"]
    return "\n".join(lines) + "\n"