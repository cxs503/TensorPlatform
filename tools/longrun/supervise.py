"""End-to-end driver: guarded child + survival layer + machine-readable report.

``supervise()`` is the only entry point most callers need:

.. code-block:: python

    report = supervise({"job": {"grid": 256}, "steps": 400,
                        "crash": {"at_step": 250, "mode": "segv"}},
                       workdir="/tmp/run1")
    report["failure_mode"]          # -> 'native_segfault'
    report["core"]["core_path"]     # -> real ELF core written by gdb
    report["bundle_completeness"]["completeness"]   # -> 1.0

Everything is written under ``workdir``: ``cfg.json``, ``result.json`` (child),
``forensics/bundle_<ts>.json`` (autopsy), ``forensics/flight.log``, ``journal.jsonl``,
``work/checkpoints/`` and ``work/resume_audit.jsonl``.  The report carries the paths,
so the numbers in a downstream report are always traceable to an artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import forensics, guardian, ladder as ladder_mod

REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    root = str(REPO_ROOT)
    if root not in pp.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join([root] + ([pp] if pp else []))
    env.setdefault("PYTHONUNBUFFERED", "1")
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def supervise(
    cfg: Dict[str, Any],
    workdir: os.PathLike | str,
    *,
    job_id: str = "longrun",
    timeout: Optional[float] = None,
    capture_core: bool = True,
    gdb: Optional[str] = None,
    poll_s: float = 0.01,
    full_core: bool = False,
    max_core_bytes: int = 2 << 30,
    keep_oversize_core: bool = False,
) -> Dict[str, Any]:
    """Run ``cfg`` under the guardian and return the full autopsy/report dict."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    forensic_dir = workdir / "forensics"
    forensic_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(cfg)
    cfg.setdefault("job_id", job_id)
    cfg.setdefault("workdir", str(workdir / "work"))
    cfg.setdefault("forensic_dir", str(forensic_dir))
    cfg.setdefault("outdir", str(workdir))
    cfg_path = workdir / "cfg.json"
    cfg_path.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")

    core_path = workdir / "forensics" / f"core.{job_id}.{int(time.time())}"
    started = time.time()
    gres = guardian.run_guarded(
        sys.executable,
        ["-m", "tools.longrun.jobrunner", str(cfg_path)],
        cwd=str(REPO_ROOT),
        env=_python_env(),
        core_path=core_path if capture_core else None,
        timeout=timeout,
        gdb=gdb,
        capture=capture_core,
        poll_s=poll_s,
        full_core=full_core,
        max_core_bytes=max_core_bytes,
        keep_oversize_core=keep_oversize_core,
    )
    ended = time.time()
    child_report: Dict[str, Any] = {}
    result_path = workdir / "result.json"
    if result_path.exists():
        try:
            child_report = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            child_report = {}

    evidence = gres.as_dict()
    evidence["child_report"] = child_report
    classification = forensics.classify_failure(evidence)

    flight = forensic_dir / "flight.log"
    journal = forensic_dir / "journal.jsonl"
    stack_snapshot = forensics.dedent_stack_snapshot(flight)
    if not stack_snapshot and child_report.get("traceback"):
        stack_snapshot = child_report["traceback"]
    bundle = {
        "schema": forensics.SCHEMA,
        "job_id": cfg.get("job_id"),
        "pid": gres.pid,
        "started_unix": child_report.get("started_unix", started),
        "ended_unix": child_report.get("ended_unix", ended),
        "wall_s": round(ended - started, 6),
        "exit_code": gres.exit_code,
        "term_signal": gres.term_signal,
        "signal_name": forensics.FATAL_SIGNALS.get(int(gres.term_signal or -1)),
        "siginfo": gres.siginfo,
        "failure_mode": classification["failure_mode"],
        "classification_reasons": classification["classification_reasons"],
        "python_stack_snapshot": stack_snapshot,
        "state_journal_tail": forensics.journal_tail(journal),
        "memory_probe": forensics.memory_probe(),
        "runtime_env": forensics.runtime_env(),
        "code_version": forensics.code_version(),
        "last_checkpoint_digest": (
            child_report.get("last_checkpoint_digest")
            or child_report.get("final_checkpoint_digest")
            or (forensics.last_checkpoint(journal) or {}).get("digest")
        ),
        "last_checkpoint_step": (forensics.last_checkpoint(journal) or {}).get("step"),
        "stop_reason": child_report.get("stop_reason"),
        "result_digest": child_report.get("result_digest"),
        "resumed_from_step": child_report.get("resumed_from_step"),
        "core_path": gres.core.get("core_path"),
        "core_size": gres.core.get("core_size"),
        "core_sha256": gres.core.get("core_sha256"),
        "core_status": gres.core.get("core_status"),
        "elf_core_path": gres.core.get("elf_core_path"),
        "elf_core_size": gres.core.get("elf_core_size"),
        "elf_core_sha256": gres.core.get("elf_core_sha256"),
        "elf_core_retained": gres.core.get("elf_core_retained"),
        "core_reopen_ok": gres.core.get("core_reopen_ok"),
        "native_bt": gres.core.get("native_bt") or [],
        "core_reopen_bt": gres.core.get("core_reopen_bt") or [],
        "mappings_digest": hashlib.sha256((gres.core.get("mappings") or "").encode()).hexdigest(),
        "registers_digest": hashlib.sha256((gres.core.get("registers") or "").encode()).hexdigest(),
        "stops": gres.stops,
        "oom_kill_delta": gres.oom_kill_delta,
        "timed_out": gres.timed_out,
        "artifacts": {
            "cfg": str(cfg_path),
            "result": str(result_path),
            "flight_log": str(flight),
            "journal": str(journal),
            "checkpoints": str(workdir / "work" / "checkpoints"),
            "resume_audit": str(workdir / "work" / "resume_audit.jsonl"),
        },
    }
    require_core = bool(gres.crashed and gres.term_signal in forensics.FATAL_SIGNALS)
    bundle["bundle_completeness"] = forensics.completeness(bundle, require_core=require_core)
    bundle["uncaught_error"] = {
        "error_type": child_report.get("error_type"),
        "error": child_report.get("error"),
    }
    bundle_path = forensic_dir / f"bundle_{cfg.get('job_id')}.json"
    forensics.write_bundle(bundle, bundle_path)
    bundle["bundle_path"] = str(bundle_path)

    report = {
        "schema": "longrun.report/1",
        "job_id": cfg.get("job_id"),
        "workdir": str(workdir),
        "failure_mode": classification["failure_mode"],
        "classification_reasons": classification["classification_reasons"],
        "crashed": gres.crashed,
        "exit_code": gres.exit_code,
        "term_signal": gres.term_signal,
        "core": {
            "status": gres.core.get("core_status"),
            "path": gres.core.get("core_path"),
            "size": gres.core.get("core_size"),
            "sha256": gres.core.get("core_sha256"),
            "elf_core_path": gres.core.get("elf_core_path"),
            "elf_core_size": gres.core.get("elf_core_size"),
            "elf_core_sha256": gres.core.get("elf_core_sha256"),
            "elf_core_retained": gres.core.get("elf_core_retained"),
            "elf_core_note": gres.core.get("elf_core_note"),
            "reopen_ok": gres.core.get("core_reopen_ok"),
            "capture_s": gres.core.get("capture_s"),
            "n_native_frames": len(gres.core.get("native_bt") or []),
            "native_bt": (gres.core.get("native_bt") or [])[:8],
        },
        "bundle_path": str(bundle_path),
        "bundle_completeness": bundle["bundle_completeness"],
        "child": {
            "finished": child_report.get("finished"),
            "stop_reason": child_report.get("stop_reason"),
            "result_digest": child_report.get("result_digest"),
            "resumed_from_step": child_report.get("resumed_from_step"),
            "final_checkpoint_digest": child_report.get("final_checkpoint_digest"),
            "final_checkpoint_path": child_report.get("final_checkpoint_path"),
            "final_step": child_report.get("final_step"),
            "degradations": child_report.get("degradations"),
            "idempotency_key": child_report.get("idempotency_key"),
            "wall_s": child_report.get("wall_s"),
            "in_thread_overhead_ns": child_report.get("in_thread_overhead_ns"),
            "ladder": child_report.get("ladder"),
        },
        "wall_s": round(ended - started, 6),
        "artifacts": bundle["artifacts"],
    }
    report_path = workdir / "supervise_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report