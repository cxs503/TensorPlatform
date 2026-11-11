"""The three exits: commit, report POST, artifact write.

One engine, three places where content leaves the work tree — and each one is
fail-closed. The design rule is that the *gate*, not the caller, decides:

* ``gate_pre_commit``  — reads ``git diff --cached`` and scans only **added**
  lines (diff-level), with source line numbers preserved through the hunk map;
* ``gate_report``      — scans a payload (recursively, plus one decode level) and
  refuses to call the transport at all when dirty, so a leak cannot be "already
  sent" by the time it is noticed;
* ``gate_artifact``    — scans the bytes *before* they reach the writer; raw
  content is never written, and the optional repair path writes only a redacted
  variant that is re-scanned and must come back clean.

Every verdict is deliberately content-free (masked preview + length + sha256),
so it can be archived, POSTed or committed without becoming a leak of its own.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .engine import Scanner
from .model import GateError, ScanResult, Verdict
from .redact import redact_text
from .policy import Policy, PolicyError, load_policy

LEDGER_PATH = os.environ.get("SECRET_SCAN_LEDGER", "runs/secret_scan/events.jsonl")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def build_scanner(policy_path: Optional[str] = None, *, profile: str = "full") -> Scanner:
    return Scanner(load_policy(policy_path, profile=profile))


def _safe_scanner(policy_path: Optional[str], profile: str = "full") -> Tuple[Optional[Scanner], Optional[str]]:
    try:
        return build_scanner(policy_path, profile=profile), None
    except PolicyError as exc:
        return None, "policy_error:{}".format(exc)


def _unknown(exit_name: str, error: str, policy_sha: str = "") -> Verdict:
    verdict = Verdict(exit_name=exit_name, decision="unknown", scan=ScanResult(target=exit_name),
                      policy_sha256=policy_sha, extra={"error": error})
    return verdict


def append_ledger(entry: Dict[str, Any], path: Optional[str] = None) -> None:
    """Append a content-free event to the gate ledger (best effort)."""
    target = path or LEDGER_PATH
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        record = dict(entry)
        record.setdefault("ts", _dt.datetime.now(_dt.timezone.utc).isoformat())
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# exit 1: pre-commit (diff level)
# --------------------------------------------------------------------------
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_unified_diff(diff_text: str) -> Dict[str, Tuple[List[str], List[int]]]:
    """Return ``{path: (added_lines, source_line_numbers)}`` from a unified diff.

    Only ``+`` lines are collected: a commit is judged by what it *adds*, and
    re-scanning existing (already reviewed) lines would bury the signal.
    """
    files: Dict[str, Tuple[List[str], List[int]]] = {}
    current: Optional[str] = None
    new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            name = raw[4:].strip()
            if name.startswith("b/"):
                name = name[2:]
            current = None if name == "/dev/null" else name
            if current is not None:
                files.setdefault(current, ([], []))
            continue
        if raw.startswith("--- "):
            continue
        match = _HUNK_RE.match(raw)
        if match:
            new_line = int(match.group(1))
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            lines, numbers = files[current]
            lines.append(raw[1:])
            numbers.append(new_line)
            new_line += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            new_line += 1
    return files


def _git(args: Sequence[str], cwd: str) -> str:
    try:
        completed = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("git_failed:{}".format(exc)) from exc
    if completed.returncode != 0:
        raise GateError("git_error:{}".format(completed.stderr.strip()[:200]))
    return completed.stdout


def gate_pre_commit(
    repo: str = ".",
    *,
    policy_path: Optional[str] = None,
    staged_only: bool = True,
    diff_text: Optional[str] = None,
    ledger_path: Optional[str] = None,
) -> Verdict:
    """Exit 1. Fail-closed: no policy or no git ⇒ ``unknown`` ⇒ BLOCK."""
    scanner, error = _safe_scanner(policy_path)
    if scanner is None:
        return _unknown("pre_commit", error or "policy_error")
    try:
        if diff_text is None:
            args = ["diff", "--unified=0", "--no-color"]
            if staged_only:
                args.insert(1, "--cached")
            diff_text = _git(args, repo)
    except GateError as exc:
        return _unknown("pre_commit", str(exc), scanner.policy.sha256)

    files = parse_unified_diff(diff_text)
    result = ScanResult(target="git-diff")
    for path, (lines, numbers) in files.items():
        if not lines:
            continue
        text = "\n".join(lines)
        sub = scanner.scan_text(text, path=path, apply_location_whitelist=True)
        result.scanned_chars += sub.scanned_chars
        result.decoded_blobs += sub.decoded_blobs
        for hit in sub.hits:
            source_line = numbers[hit.line - 1] if 0 < hit.line <= len(numbers) else hit.line
            result.hits.append(_relabel(hit, line=source_line, path=path))
        result.files.append({"path": path, "added_lines": len(lines), "hits": len(sub.hits)})

    decision = "allow" if result.clean else "block"
    verdict = Verdict(
        exit_name="pre_commit",
        decision=decision,
        scan=result,
        policy_sha256=scanner.policy.sha256,
        extra={"staged_only": staged_only, "files_considered": len(files)},
    )
    append_ledger(
        {"exit": "pre_commit", "decision": decision, "hits": len(result.hits),
         "by_type": result.by_type(), "digest": verdict.to_dict()["digest"]},
        ledger_path,
    )
    return verdict


# --------------------------------------------------------------------------
# exit 2: report gate (payload level, decode-aware)
# --------------------------------------------------------------------------
def gate_report(
    payload: Any,
    *,
    policy_path: Optional[str] = None,
    send: Optional[Callable[[Any], Any]] = None,
    ledger_path: Optional[str] = None,
    max_hits: int = 200,
) -> Verdict:
    """Exit 2. The transport callable is only invoked when the payload is clean."""
    scanner, error = _safe_scanner(policy_path)
    if scanner is None:
        return _unknown("report_gate", error or "policy_error")
    apply_location = bool(scanner.policy.exits.get("report_gate", {}).get("apply_location_whitelist", False))
    decode = bool(scanner.policy.exits.get("report_gate", {}).get("scan_decoded", True))
    result = scanner.scan_value(payload, path="payload", decode=decode,
                                apply_location_whitelist=apply_location)
    decision = "allow" if result.clean else "block"
    sent = False
    if decision == "allow" and send is not None:
        send(payload)
        sent = True
    verdict = Verdict(
        exit_name="report_gate",
        decision=decision,
        scan=result,
        policy_sha256=scanner.policy.sha256,
        extra={"sent": sent, "transport_invoked": sent, "decode_passes": 1 if decode else 0},
    )
    append_ledger(
        {"exit": "report_gate", "decision": decision, "hits": len(result.hits),
         "sent": sent, "digest": verdict.to_dict(max_hits=max_hits)["digest"]},
        ledger_path,
    )
    return verdict


def ensure_clean_report(payload: Any, *, policy_path: Optional[str] = None) -> None:
    """Raise :class:`GateError` (with a content-free message) when dirty."""
    verdict = gate_report(payload, policy_path=policy_path)
    if verdict.blocked:
        raise GateError("report_blocked:{}:{}".format(
            verdict.scan.by_type(), verdict.to_dict()["digest"]))


# --------------------------------------------------------------------------
# exit 3: artifact writer (pre-write)
# --------------------------------------------------------------------------
def gate_artifact(
    path: str,
    content: Any,
    *,
    policy_path: Optional[str] = None,
    write: bool = True,
    ledger_path: Optional[str] = None,
) -> Verdict:
    """Exit 3. Raw bytes never reach disk; the repair path is verified clean."""
    scanner, error = _safe_scanner(policy_path)
    if scanner is None:
        return _unknown("artifact_writer", error or "policy_error")
    if isinstance(content, (bytes, bytearray)):
        text = bytes(content).decode("utf-8", "surrogateescape")
    elif isinstance(content, str):
        text = content
    else:
        return _unknown("artifact_writer", "unsupported_content_type:{}".format(type(content).__name__),
                        scanner.policy.sha256)

    result = scanner.scan_text(text, path=path, apply_location_whitelist=True)
    cfg = scanner.policy.exits.get("artifact_writer", {})
    extra: Dict[str, Any] = {"written": False, "raw_write_blocked": not result.clean,
                             "path": path, "bytes": len(text.encode("utf-8", "surrogateescape"))}
    redacted_text: Optional[str] = None

    if result.clean:
        if write:
            _write_text(path, text)
            extra["written"] = True
        decision = "allow"
    else:
        decision = "block"
        if cfg.get("redact_on_hit", True):
            redacted_text, applied = redact_text(
                text,
                result.hits,
                template=str(scanner.policy.redaction.get("placeholder", "<REDACTED:{type}:{len}>")),
                max_type_chars=int(scanner.policy.redaction.get("max_type_chars", 32)),
            )
            extra["redactions"] = len(applied)
            extra["placeholder_examples"] = [p for _, _, p in applied[:3]]
            if cfg.get("verify_after_redact", True):
                verify = scanner.scan_text(redacted_text, path=path)
                extra["verified_clean"] = verify.clean
                if not verify.clean:
                    extra["error"] = "redaction_verification_failed"
                    decision = "unknown"
                    append_ledger({"exit": "artifact_writer", "decision": decision,
                                   "hits": len(result.hits), "error": extra["error"]}, ledger_path)
                    return Verdict("artifact_writer", decision, result, scanner.policy.sha256, None, extra)
            if write and decision == "block":
                _write_text(path, redacted_text)
                extra["written"] = True
                extra["written_redacted"] = True
    verdict = Verdict(
        exit_name="artifact_writer",
        decision=decision,
        scan=result,
        policy_sha256=scanner.policy.sha256,
        redacted=redacted_text,
        extra=extra,
    )
    append_ledger(
        {"exit": "artifact_writer", "decision": decision, "hits": len(result.hits),
         "written": extra["written"], "digest": verdict.to_dict()["digest"]},
        ledger_path,
    )
    return verdict


def _write_text(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(text)


def _relabel(hit, *, line: int, path: str):
    from dataclasses import replace

    return replace(hit, line=line, path=path)