"""Run loop: resume -> iterate -> checkpoint -> degrade -> report.

This is the code that lives *inside* the guarded child process.  It never handles
fatal signals (see :mod:`tools.longrun.guardian`); it only keeps state recoverable
and leaves an auditable trail.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from . import checkpoint as ckpt_mod
from . import forensics
from . import ladder as ladder_mod
from . import resume as resume_mod
from .job import SyntheticIterativeJob

EXIT_OK = 0
EXIT_PY_EXCEPTION = 70
EXIT_RESOURCE = 71


def build_job(cfg: Dict[str, Any]) -> SyntheticIterativeJob:
    job_cfg = dict(cfg.get("job") or {})
    crash = cfg.get("crash") or {}
    return SyntheticIterativeJob(
        grid=int(job_cfg.get("grid", 64)),
        precision=str(job_cfg.get("precision", "float64")),
        dt=float(job_cfg.get("dt", 0.25)),
        seed=int(job_cfg.get("seed", 7)),
        crash_at=crash.get("at_step"),
        crash_mode=str(crash.get("mode", "segv")),
    )


def run_job(cfg: Dict[str, Any], outdir: os.PathLike | str) -> Dict[str, Any]:
    """Execute the configured job with the full survival layer; return a report dict."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    forensic_dir = Path(cfg.get("forensic_dir") or (outdir / "forensics"))
    workdir = Path(cfg.get("workdir") or (outdir / "work"))
    workdir.mkdir(parents=True, exist_ok=True)
    steps = int(cfg.get("steps", 64))
    checkpoint_every = int(cfg.get("checkpoint_every", 16))
    job = build_job(cfg)
    input_digest = job.input_digest()
    config_digest = ckpt_mod.sha256_bytes(
        ckpt_mod.canonical_json(
            {
                "job": job.params(),
                "checkpoint_every": checkpoint_every,
                "ladder": (cfg.get("ladder") or {}).get("rungs"),
            }
        ).encode()
    )

    report: Dict[str, Any] = {
        "schema": "longrun.run/1",
        "job_id": cfg.get("job_id", "job"),
        "steps_requested": steps,
        "checkpoint_every": checkpoint_every,
        "input_digest": input_digest,
        "config_digest": config_digest,
        "started_unix": time.time(),
        "finished": False,
        "stop_reason": None,
    }

    recorder = forensics.FlightRecorder(
        forensic_dir,
        job_id=report["job_id"],
        stack_interval=float((cfg.get("forensics") or {}).get("stack_interval_s", 1.0)),
        memory_interval=float((cfg.get("forensics") or {}).get("memory_interval_s", 2.0)),
        enabled=bool((cfg.get("forensics") or {}).get("enabled", True)),
    )
    exit_code = EXIT_OK
    try:
        store = ckpt_mod.CheckpointStore(workdir / "checkpoints")
        audit = resume_mod.ResumeAudit(workdir / "resume_audit.jsonl", run_id=str(os.getpid()))
        controller = resume_mod.ResumeController(
            store,
            audit,
            input_digest=input_digest,
            config_digest=config_digest,
            require_match=bool((cfg.get("resume") or {}).get("require_match", True)),
        )
        plan = controller.plan(force_from_step=(cfg.get("resume") or {}).get("from_step"))
        report.update(
            {
                "resume_plan": plan.as_dict(),
                "resumed_from_step": plan.from_step,
                "resume_verified": plan.verified,
            }
        )
        ck = resume_mod.restore(store, plan) if plan.checkpoint_digest else None
        if ck is not None:
            job.load_state(ck.arrays, ck.scalars)
            audit.log(
                "resume_applied",
                from_step=ck.step,
                checkpoint_digest=ck.digest,
                state_digest=ck.manifest.get("state_digest"),
                idempotency_key=plan.idempotency_key,
            )
        recorder.log("run_start", input_digest=input_digest, config_digest=config_digest,
                     resumed_from=plan.from_step)

        lad: Optional[ladder_mod.ResourceLadder] = None
        ladder_cfg = cfg.get("ladder") or {}
        if ladder_cfg.get("enabled", True):
            rungs = ladder_cfg.get("rungs") or list(ladder_mod.DEFAULT_RUNGS)
            lad = ladder_mod.ResourceLadder(
                probe=ladder_mod.ScriptedProbe(ladder_cfg["scripted_utilization"])
                if ladder_cfg.get("scripted_utilization")
                else None,
                rungs=[(r[0], r[1]) if isinstance(r, (list, tuple)) else (r["rung"], r["threshold"])
                       for r in rungs],
                min_interval_s=float(ladder_cfg.get("min_interval_s", 0.5)),
            )

        t0 = time.time()
        stop_reason = "steps_done"
        ladder_events = []
        for i in range(plan.from_step + 1, steps + 1):
            job.maybe_crash(i)
            job.step(i)
            recorder.note_step(i, time.time() - t0)
            if lad is not None:
                event = lad.poll()
                if event is not None:
                    applied = []
                    for rung_name in event["rungs_applied"]:
                        applied.append(job.apply_degradation(rung_name))
                    ladder_events.append({**event, "applied": applied})
                    recorder.log("ladder", **{k: v for k, v in event.items() if k != "applied"},
                                 applied=[a["rung"] for a in applied])
                    if event["kind"] == "graceful_exit":
                        stop_reason = "graceful_degraded_exit"
                        break
            if checkpoint_every and (i % checkpoint_every == 0):
                manifest = store.save(
                    i,
                    job.state_arrays(),
                    job.state_scalars(),
                    config_digest=config_digest,
                    input_digest=input_digest,
                    code_version=forensics.code_version(),
                )
                recorder.note_checkpoint(i, manifest["digest"], str(store.root))
                audit.log("checkpoint", step=i, digest=manifest["digest"],
                          state_digest=manifest["state_digest"])
                store.prune(keep=int(cfg.get("keep_checkpoints", 4)))

        final_step = job.step_index
        final_manifest = store.save(
            final_step,
            job.state_arrays(),
            job.state_scalars(),
            config_digest=config_digest,
            input_digest=input_digest,
            code_version=forensics.code_version(),
            label="final",
        )
        audit.log("final_checkpoint", step=final_step, digest=final_manifest["digest"],
                  state_digest=final_manifest["state_digest"])
        report.update(
            {
                "finished": stop_reason == "steps_done",
                "stop_reason": stop_reason,
                "final_step": final_step,
                "result_digest": job.result_digest(),
                "final_checkpoint_digest": final_manifest["digest"],
                "final_checkpoint_path": str(store.root),
                "degradations": job.degradations,
                "ladder": lad.report() if lad is not None else None,
                "ladder_events": ladder_events,
                "idempotency_key": plan.idempotency_key,
                "wall_s": round(time.time() - t0, 6),
                "in_thread_overhead_ns": recorder.overhead_ns,
            }
        )
        if stop_reason == "graceful_degraded_exit":
            exit_code = ladder_mod.GRACEFUL_EXIT_CODE
            audit.log("graceful_exit", code=exit_code, final_step=final_step)
    except ckpt_mod.CheckpointError as exc:
        exit_code = EXIT_PY_EXCEPTION
        report.update(forensics.exception_evidence(exc))
        report["stop_reason"] = "state_corruption"
        recorder.log("error", **{"error_type": type(exc).__name__, "error": str(exc)[:600]})
    except MemoryError as exc:
        exit_code = EXIT_RESOURCE
        report.update(forensics.exception_evidence(exc))
        report["stop_reason"] = "memory_error"
        recorder.log("error", error_type="MemoryError", error=str(exc)[:600])
    except BaseException as exc:  # noqa: BLE001 - the autopsy needs every failure
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            raise
        exit_code = EXIT_PY_EXCEPTION
        report.update(forensics.exception_evidence(exc))
        report["stop_reason"] = "python_exception"
        report["traceback_tail"] = "".join(traceback.format_exc())[-4000:]
        recorder.log("error", error_type=type(exc).__name__, error=str(exc)[:600])
    finally:
        report["ended_unix"] = time.time()
        report["exit_code"] = exit_code
        recorder.close(exit_code=exit_code, reason=report.get("stop_reason") or "finished")
        report["forensic_dir"] = str(forensic_dir)
        report["last_checkpoint_digest"] = recorder.last_checkpoint_digest
        report["memory_probe"] = forensics.memory_probe()
        report["runtime_env"] = forensics.runtime_env()
        report["code_version"] = forensics.code_version()
        result_path = outdir / "result.json"
        tmp = result_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, result_path)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="run a long job with the survival layer")
    ap.add_argument("cfg", help="path to the JSON job configuration")
    ap.add_argument("--outdir", default=None)
    ns = ap.parse_args(argv)
    cfg = json.loads(Path(ns.cfg).read_text(encoding="utf-8"))
    outdir = ns.outdir or cfg.get("outdir") or str(Path(ns.cfg).parent)
    report = run_job(cfg, outdir)
    print(json.dumps({k: report.get(k) for k in ("result_digest", "stop_reason", "finished",
                                                 "resumed_from_step", "exit_code")}, sort_keys=True))
    return int(report.get("exit_code") or 0)