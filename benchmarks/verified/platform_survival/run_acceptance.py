#!/usr/bin/env python3
"""Acceptance harness for the long-run survival & progress-maturity layer (P148W2-N01).

Every number in ``results.json`` is produced by running the machinery below; nothing
is transcribed by hand.  The ten criteria are the node's A1..A10, and each one is
scored against a *pre-declared* threshold that is written into the same JSON, so a
reader can see both sides of every comparison.

    python3 benchmarks/verified/platform_survival/run_acceptance.py

Outputs (all inside this directory):
    results.json                  criteria verdicts + measured numbers + artifact paths
    resume_consistency_report.json  bit-level resume table (A2 / A7 / A9)
    figures/*.png                 progress timeline, stall ladder, criteria dashboard
    artifacts/                    crash bundle(s), task run directories
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HERE = Path(__file__).resolve().parent

from tools.longrun import supervise as supervise_mod  # noqa: E402
from tools.longrun.job import SyntheticIterativeJob  # noqa: E402
from tools.progress_meter.stall import LEVELS  # noqa: E402
from tools.progress_meter.strategies import Strategy, StrategyPlan  # noqa: E402
from tools.survival import degrade, forensics_bundle  # noqa: E402
from tools.survival.workload import TaskConfig, run_task  # noqa: E402

#: Resume geometry shared by A2/A7/A9: 40 steps, checkpoint every 5 -> a checkpoint
#: at step 20 is exactly 50 % of the plan.
STEPS = 40
CKPT_EVERY = 5
MID_STEP = 20
JOB_CFG = {"job": {"grid": 32, "precision": "float64", "dt": 0.25, "seed": 7},
           "steps": STEPS, "checkpoint_every": CKPT_EVERY,
           "forensics": {"stack_interval_s": 0.5}}


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj


def _fresh(root: Path, name: str) -> Path:
    d = root / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# A1: crash forensics
# --------------------------------------------------------------------------- #
def check_a1(root: Path) -> Dict[str, Any]:
    art = _fresh(root, "a1_crash")
    bundle = forensics_bundle.dump_crash(
        workdir=art / "run", outroot=art,
        cfg={"job": {"grid": 24}, "steps": 30, "checkpoint_every": 5,
             "forensics": {"stack_interval_s": 0.5}},
        crash_mode="segv", at_step=12, stamp="a1segv",
    )
    bdir = Path(bundle["bundle_dir"])
    regs = json.loads((bdir / "registers.json").read_text())
    stack_lines = [ln for ln in (bdir / "stack.txt").read_text().splitlines() if ln.startswith("#")]
    mappings = (bdir / "mappings.txt").read_text()
    manifest = json.loads((bdir / "manifest.json").read_text())
    # reproducibility: copy elsewhere, re-verify -> identical digest
    copy_dir = art / "copied"
    if copy_dir.exists():
        shutil.rmtree(copy_dir)
    shutil.copytree(bdir, copy_dir)
    recheck = forensics_bundle.verify_bundle(copy_dir)
    measured = {
        "register_fields": regs.get("n_fields", 0),
        "register_names": sorted(regs.get("registers", {}))[:12],
        "stack_frames": len(stack_lines),
        "stack_has_symbol": any("in " in ln for ln in stack_lines),
        "mappings_lines": len([ln for ln in mappings.splitlines() if ln.strip()]),
        "manifest_keys": len(manifest),
        "manifest_has_checkpoint_chain": bool(manifest.get("checkpoint_chain")),
        "bundle_files": bundle["n_files"],
        "bundle_digest": bundle["bundle_digest"],
        "copy_digest": recheck["bundle_digest"],
        "copy_verifies": recheck["ok"],
        "signal": bundle.get("signal"),
        "failure_mode": bundle.get("failure_mode"),
    }
    checks = {
        "registers_ge_5": measured["register_fields"] >= 5,
        "frames_ge_3": measured["stack_frames"] >= 3,
        "mappings_present": measured["mappings_lines"] >= 5,
        "manifest_snapshot": measured["manifest_keys"] >= 8,
        "sha256_manifest_verifies": recheck["ok"],
        "digest_reproducible_on_copy": recheck["bundle_digest"] == bundle["bundle_digest"],
        "signal_is_segv": measured["signal"] in (11, None) or measured["failure_mode"] == "native_segfault",
    }
    return {
        "id": "A1",
        "name": "crash forensics completeness",
        "threshold": "registers>=5, frames>=3, mappings>=5 lines, sha256 verify ok",
        "measured": measured,
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {"bundle": str(bdir), "copy": str(copy_dir)},
    }


# --------------------------------------------------------------------------- #
# A2 / A7 / A9: resume consistency, negative control, idempotency
# --------------------------------------------------------------------------- #
def _direct_digest() -> str:
    job = SyntheticIterativeJob(grid=32, precision="float64", dt=0.25, seed=7)
    for i in range(1, STEPS + 1):
        job.step(i)
    return job.result_digest()


def check_resume_family(root: Path) -> Dict[str, Any]:
    base_dir = _fresh(root, "a2_resume")
    full = supervise_mod.supervise(dict(JOB_CFG), workdir=base_dir, job_id="uninterrupted")
    full_digest = full["child"]["result_digest"]
    direct = _direct_digest()

    # --- A2: interrupt at step 21 (real SIGSEGV), resume from the step-20 checkpoint
    interrupted = supervise_mod.supervise(
        {**JOB_CFG, "crash": {"at_step": MID_STEP + 1, "mode": "segv"}},
        workdir=base_dir / "interrupted", job_id="interrupted",
    )
    resumed = supervise_mod.supervise(
        {**JOB_CFG, "job_id": "resumed"}, workdir=base_dir / "interrupted", job_id="resumed"
    )
    ckpt_step = resumed["child"]["resumed_from_step"]
    a2_ok = (
        interrupted["failure_mode"] == "native_segfault"
        and resumed["child"]["finished"] is True
        and ckpt_step == MID_STEP
        and resumed["child"]["result_digest"] == full_digest
    )

    # --- A7: negative control -- a full run without interruption reproduces the
    #         bare-job digest computed outside the survival layer
    a7_ok = full["child"]["finished"] is True and full_digest == direct

    # --- A9: three resumes from the same checkpoint -> identical digest & key
    thrice = []
    for rep in range(3):
        rep_report = supervise_mod.supervise(
            {**JOB_CFG, "job_id": f"idem_{rep}"},
            workdir=base_dir / "interrupted", job_id=f"idem_{rep}",
        )
        thrice.append({"result_digest": rep_report["child"]["result_digest"],
                       "idempotency_key": rep_report["child"]["idempotency_key"],
                       "resumed_from_step": rep_report["child"]["resumed_from_step"]})
    digests = {r["result_digest"] for r in thrice}
    keys = {r["idempotency_key"] for r in thrice}
    a9_ok = len(digests) == 1 and len(keys) == 1 and digests.pop() == full_digest

    report = {
        "schema": "survival.resume_consistency/1",
        "geometry": {"steps": STEPS, "checkpoint_every": CKPT_EVERY,
                     "mid_checkpoint_step": MID_STEP, "grid": 32},
        "uninterrupted_digest": full_digest,
        "bare_job_digest": direct,
        "interrupted": {
            "failure_mode": interrupted["failure_mode"],
            "term_signal": interrupted["term_signal"],
            "core_status": interrupted["core"]["status"],
            "n_native_frames": interrupted["core"]["n_native_frames"],
            "uncaught_error": interrupted.get("uncaught_error"),
            "checkpoint_at_interrupt": interrupted["child"].get("final_checkpoint_digest"),
        },
        "resumed": {"from_step": ckpt_step,
                    "result_digest": resumed["child"]["result_digest"],
                    "bit_identical_to_uninterrupted": resumed["child"]["result_digest"] == full_digest},
        "idempotency_repeats": thrice,
        "digest_bytes": 32,
        "a2": a2_ok,
        "a7": a7_ok,
        "a9": a9_ok,
        "workdir": str(base_dir),
    }
    (HERE / "resume_consistency_report.json").write_text(
        json.dumps(_jsonable(report), indent=2, sort_keys=True), encoding="utf-8")
    return {
        "A2": {
            "id": "A2",
            "name": "resume bit-consistency from the 50 % checkpoint",
            "threshold": "resumed digest == uninterrupted digest (sha256), from_step == 20",
            "measured": {
                "resumed_from_step": ckpt_step,
                "resumed_digest": resumed["child"]["result_digest"],
                "uninterrupted_digest": full_digest,
                "bit_identical": resumed["child"]["result_digest"] == full_digest,
                "interrupted_signal": interrupted["term_signal"],
                "interrupted_mode": interrupted["failure_mode"],
                "core_status": interrupted["core"]["status"],
            },
            "checks": {"resumed_midpoint": ckpt_step == MID_STEP,
                       "bit_identical": resumed["child"]["result_digest"] == full_digest,
                       "interrupt_was_a_real_crash": interrupted["failure_mode"] == "native_segfault"},
            "passed": bool(a2_ok),
            "artifacts": {"report": str(HERE / "resume_consistency_report.json")},
        },
        "A7": {
            "id": "A7",
            "name": "negative control: uninterrupted run == bare job result",
            "threshold": "checkpointed full run digest == digest of the job stepped without the survival layer",
            "measured": {"full_run_digest": full_digest, "bare_job_digest": direct,
                         "finished": full["child"]["finished"],
                         "wall_s": full["wall_s"]},
            "checks": {"digests_equal": full_digest == direct, "run_finished": full["child"]["finished"] is True},
            "passed": bool(a7_ok),
            "artifacts": {"report": str(HERE / "resume_consistency_report.json")},
        },
        "A9": {
            "id": "A9",
            "name": "idempotency: three resumes from one checkpoint agree",
            "threshold": "1 distinct digest and 1 distinct idempotency key across 3 resumes",
            "measured": {"digests": [r["result_digest"] for r in thrice],
                         "distinct_digests": len(digests),
                         "distinct_keys": len(keys),
                         "equals_uninterrupted": bool(digests and list(digests)[0] == full_digest)},
            "checks": {"single_digest": len(digests) == 1, "single_key": len(keys) == 1,
                       "matches_uninterrupted": a9_ok},
            "passed": bool(a9_ok),
            "artifacts": {"report": str(HERE / "resume_consistency_report.json")},
        },
    }


# --------------------------------------------------------------------------- #
# A3 / A4 / A5 / A6 / A8: progress metering + stall ladder
# --------------------------------------------------------------------------- #
def check_progress_family(root: Path) -> Dict[str, Any]:
    normal = run_task(_fresh(root, "a3_normal"),
                      TaskConfig(budget_total=100, grid=12, planned_artifacts=20,
                                 criteria=(("first_artifact", 1), ("half_plan", 10))),
                      plan=StrategyPlan([Strategy("baseline", 0.2)]))
    a3_err = abs(normal["progress_rate"] - normal["manual"]["progress_rate"]) / normal["manual"]["progress_rate"]
    a3_ok = a3_err <= 0.05

    # A4/A5: zero-yield strategy burns the whole budget -> detect inside 30 %, reroute,
    #        and the switched strategy must then produce at least one artifact.
    stalled = run_task(_fresh(root, "a4_stalled"),
                       TaskConfig(budget_total=100, grid=12, planned_artifacts=20),
                       plan=StrategyPlan([Strategy("barren", 0.0), Strategy("fruitful", 0.05)]))
    first_frac = stalled["stall"]["first_detection_fraction"]
    levels = [e["level"] for e in stalled["stall"]["events"]]
    a4_ok = (stalled["stall"]["detected"] and first_frac <= 0.30
             and "reroute" in levels and stalled["reroute_at_budget"] is not None)
    a5_ok = a4_ok and stalled["post_reroute_artifacts"] >= 1

    # A6: strategy dies at 40 of a 200 budget -> full ladder -> honest FAIL that keeps
    #     every artifact produced before the death (plus the flushed final state).
    failing = run_task(_fresh(root, "a6_honest_fail"),
                       TaskConfig(budget_total=200, grid=12, planned_artifacts=40),
                       plan=StrategyPlan([Strategy("fruitful_early", 0.1, active_until_budget=40),
                                          Strategy("barren", 0.0)]),
                       stall_frac=0.10)
    fail = failing.get("fail") or {}
    ratios = fail.get("ratios", {})
    a6_ok = (
        failing["status"] == "FAIL"
        and bool(fail.get("reason"))
        and ratios.get("retention_vs_produced", 0.0) >= 0.5
        and (Path(failing["artifact_dir"]).parent / "FAIL.json").exists()
        and all((Path(loc := failing["artifact_dir"]) / rec["name"]).exists()
                for rec in fail.get("partial_artifacts", []))
    )

    # A8: negative controls -- steady and bursty producers must never trip the detector.
    bursty = run_task(_fresh(root, "a8_bursty"),
                      TaskConfig(budget_total=100, grid=12, planned_artifacts=10),
                      plan=StrategyPlan([Strategy("bursty", 0.05)]))  # one artifact per 20 % budget
    steady = run_task(_fresh(root, "a8_steady"),
                      TaskConfig(budget_total=100, grid=12, planned_artifacts=20),
                      plan=StrategyPlan([Strategy("steady", 0.2)]))
    fp = int(bursty["stall"]["detected"]) + int(steady["stall"]["detected"])
    a8_ok = fp == 0

    return {
        "A3": {
            "id": "A3",
            "name": "progress_rate accuracy vs manual recount",
            "threshold": "relative error <= 5 %",
            "measured": {
                "meter_progress_rate": normal["progress_rate"],
                "manual_progress_rate": normal["manual"]["progress_rate"],
                "relative_error": a3_err,
                "artifacts_on_disk": normal["manual"]["artifacts"],
                "criteria_passed": normal["manual"]["criteria_passed"],
                "info_gain_bits": normal["manual"]["info_gain_bits"],
                "budget_used": normal["manual"]["budget_used"],
            },
            "checks": {"error_le_5pct": a3_err <= 0.05},
            "passed": bool(a3_ok),
            "artifacts": {"run": normal["artifact_dir"]},
        },
        "A4": {
            "id": "A4",
            "name": "stall detection sensitivity (zero-artifact task)",
            "threshold": "detected and first detection within 30 % of budget, reroute issued",
            "measured": {
                "first_detection_budget_fraction": first_frac,
                "events": [(e["level"], e["budget_used"], e["detail"]) for e in stalled["stall"]["events"]],
                "levels_seen": levels,
                "stall_window_budget": stalled["stall"]["stall_window_budget"],
            },
            "checks": {"detected": stalled["stall"]["detected"],
                       "within_30pct": (first_frac is not None and first_frac <= 0.30),
                       "reroute_issued": "reroute" in levels},
            "passed": bool(a4_ok),
            "artifacts": {"run": stalled["artifact_dir"], "stall": str(Path(stalled["artifact_dir"]).parent / "stall.json")},
        },
        "A5": {
            "id": "A5",
            "name": "reroute effectiveness (artifacts after the switch)",
            "threshold": ">= 1 valid artifact produced after the reroute, within the remaining budget",
            "measured": {"reroute_at_budget": stalled["reroute_at_budget"],
                         "post_reroute_artifacts": stalled["post_reroute_artifacts"],
                         "artifacts": stalled["artifacts"],
                         "credits": stalled["credits"]},
            "checks": {"post_reroute_ge_1": stalled["post_reroute_artifacts"] >= 1},
            "passed": bool(a5_ok),
            "artifacts": {"run": stalled["artifact_dir"]},
        },
        "A6": {
            "id": "A6",
            "name": "honest FAIL with retained intermediate artifacts",
            "threshold": "status FAIL, retention_vs_produced >= 0.5, non-empty reason, artifacts legible",
            "measured": {"status": failing["status"], "reason": fail.get("reason"),
                         "ratios": ratios, "budget_used": failing["budget_used"],
                         "artifacts": failing["artifacts"],
                         "stall_levels": [e["level"] for e in failing["stall"]["events"]]},
            "checks": {"status_fail": failing["status"] == "FAIL",
                       "retention_ge_50pct": ratios.get("retention_vs_produced", 0.0) >= 0.5,
                       "reason_present": bool(fail.get("reason")),
                       "artifacts_on_disk": all(
                           (Path(failing["artifact_dir"]) / rec["name"]).exists()
                           for rec in fail.get("partial_artifacts", []))},
            "passed": bool(a6_ok),
            "artifacts": {"run": failing["artifact_dir"],
                          "fail_json": str(Path(failing["artifact_dir"]).parent / "FAIL.json"),
                          "fail_md": str(Path(failing["artifact_dir"]).parent / "FAIL.md")},
        },
        "A8": {
            "id": "A8",
            "name": "negative control: no false stall on legitimately uneven producers",
            "threshold": "0 stall events across steady (5 % gaps) and bursty (20 % gaps) producers",
            "measured": {"bursty_detected": bursty["stall"]["detected"],
                         "bursty_artifacts": bursty["artifact_count"],
                         "bursty_gap_fraction": 0.20,
                         "steady_detected": steady["stall"]["detected"],
                         "steady_artifacts": steady["artifact_count"],
                         "false_positives": fp},
            "checks": {"zero_false_positives": fp == 0},
            "passed": bool(a8_ok),
            "artifacts": {"bursty": bursty["artifact_dir"], "steady": steady["artifact_dir"]},
        },
    }


# --------------------------------------------------------------------------- #
# A10: memory-constrained degradation
# --------------------------------------------------------------------------- #
def check_a10(root: Path) -> Dict[str, Any]:
    art = _fresh(root, "a10_degrade")
    # geometry chosen so the *projected* 4-array footprint (4 n^2 8 B) cannot fit under
    # the cap while a streamed run can: 4*1536^2*8 B = 75.5 MB vs a 96 MB cap at ~33 MB RSS.
    n, iters = 1536, 6
    cap = 96 << 20
    degraded = degrade.run_degraded(n=n, iters=iters, mem_cap_bytes=cap,
                                    outdir=art, threads=1)
    reference_full = degrade.run_kernel(n=n, iters=iters, chunk=None,
                                        threads=max(1, os.cpu_count() or 1))
    reference_single = degrade.run_kernel(n=n, iters=iters, chunk=None, threads=1)
    np = __import__("numpy")
    field_path = Path(degraded["field_path"])
    field = np.load(field_path)
    field_sha256 = hashlib.sha256(field_path.read_bytes()).hexdigest()
    err_vs_parallel = degrade.rel_error(field, reference_full)
    err_vs_single = degrade.rel_error(field, reference_single)
    decision = degraded["decision"]
    checks = {
        "auto_single_thread": decision["threads"] == 1,
        "chunk_streaming": bool(decision.get("chunk_rows")),
        "full_grid_admissible_under_cap": degraded["full_grid_probe"]["full_grid_admissible"],
        "error_le_5pct": err_vs_parallel <= 0.05,
        "error_le_5pct_vs_single": err_vs_single <= 0.05,
    }
    measured = {
        "mem_cap_bytes": cap,
        "n": n,
        "iters": iters,
        "decision": decision,
        "full_grid_probe": degraded["full_grid_probe"],
        "rel_error_vs_parallel_reference": err_vs_parallel,
        "rel_error_vs_single_reference": err_vs_single,
        "peak_rss_kb": degraded["peak_rss_kb"],
        "wall_s": degraded["wall_s"],
        "thread_env": degraded.get("thread_env"),
        "field_l2": degraded["field_l2"],
        "field_npy_sha256": field_sha256,
        "field_npy_bytes": field_path.stat().st_size,
    }
    # the point of the cap probe: the full-grid allocation is refused, the streamed one is not
    passed = (checks["auto_single_thread"] and checks["chunk_streaming"]
              and not checks["full_grid_admissible_under_cap"] and checks["error_le_5pct"])
    return {"id": "A10", "name": "memory-constrained degradation to single thread",
            "threshold": "auto single-thread + chunked; full-grid refused under cap; error <= 5 %",
            "measured": measured,
            "checks": checks,
            "passed": bool(passed),
            "artifacts": {"run": str(art), "result": str(art / f"degraded_{n}_{cap}.json")}}


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(runs: Dict[str, Dict[str, Any]], outdir: Path) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # ---- fig 1: credit accrual vs budget, with stall markers
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    styles = {"steady": ("#1b6ca8", "-"), "bursty": ("#8a6d3b", "--"),
              "stalled": ("#b03a2e", "-"), "failing": ("#6c3483", "-.")}
    for label, run in runs.items():
        if label not in styles:
            continue
        color, ls = styles[label]
        ts = run.get("_series") or []
        xs = [p["budget"] for p in ts]
        ys = [p["credits"] for p in ts]
        ax.step(xs, ys, where="post", color=color, ls=ls, label=f"{label} (credits)")
        for ev in run["stall"]["events"]:
            ax.axvline(ev["budget_used"], color=color, alpha=0.25, lw=1)
            ax.annotate(ev["level"], (ev["budget_used"], 0.4),
                        rotation=90, fontsize=6.5, color=color, va="bottom")
    ax.set_xlabel("budget consumed (iteration units)")
    ax.set_ylabel("accumulated credit")
    ax.set_title("Progress credit vs budget, with stall-ladder actions (vertical lines)")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")
    p1 = outdir / "fig1_progress_timeline.png"
    fig.tight_layout()
    fig.savefig(p1, dpi=160)
    plt.close(fig)
    figs.append({"name": p1.name, "path": str(p1),
                 "caption": "Credit accrual for steady, bursty, stalled (reroute recovered) and "
                            "failing runs; vertical lines mark stall-ladder actions."})

    # ---- fig 2: ladder escalation (budget fraction vs rung)
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    rung_index = {name: i + 1 for i, name in enumerate(LEVELS)}
    for label, run in runs.items():
        if label not in styles or not run["stall"]["events"]:
            continue
        color, ls = styles[label]
        xs = [0.0] + [ev["budget_fraction"] for ev in run["stall"]["events"]]
        ys = [0] + [rung_index[ev["level"]] for ev in run["stall"]["events"]]
        ax.step(xs, ys, where="post", marker="o", ms=3, color=color, ls=ls, label=label)
    ax.axvline(0.30, color="k", ls=":", lw=1)
    ax.annotate("A4 limit: 30 % of budget", (0.302, 0.15), fontsize=7, rotation=90)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.set_yticklabels(["ok"] + list(LEVELS))
    ax.set_xlabel("budget fraction at detection")
    ax.set_ylabel("recovery rung")
    ax.set_title("Stall ladder escalation in budget space")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    p2 = outdir / "fig2_stall_ladder.png"
    fig.tight_layout()
    fig.savefig(p2, dpi=160)
    plt.close(fig)
    figs.append({"name": p2.name, "path": str(p2),
                 "caption": "Escalation rung vs the budget fraction at which it fired; the dotted "
                            "line is the A4 detection limit (30 % of budget)."})

    # ---- fig 3: criteria dashboard (measured / threshold, log scale)
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    labels, ratios = [], []
    for cid, run in sorted(runs.items()):
        if not cid.startswith("A"):
            continue
        m = run.get("_dash")
        if not m:
            continue
        labels.append(f"{cid}\n{run['name'].split(':')[0][:22]}")
        ratios.append(m)
    colors = ["#1e8449" if r >= 1.0 else "#c0392b" for r in ratios]
    ax.bar(range(len(ratios)), ratios, color=colors)
    ax.set_xticks(range(len(ratios)))
    ax.set_xticklabels(labels, fontsize=6.5)
    ax.axhline(1.0, color="k", ls="--", lw=1)
    ax.set_yscale("log")
    ax.set_ylabel("measured / threshold (>=1 passes, log scale)")
    ax.set_title("Acceptance dashboard: every criterion against its pre-declared threshold")
    ax.grid(alpha=0.25, axis="y")
    p3 = outdir / "fig3_criteria_dashboard.png"
    fig.tight_layout()
    fig.savefig(p3, dpi=160)
    plt.close(fig)
    figs.append({"name": p3.name, "path": str(p3),
                 "caption": "Normalised margin per criterion; bars above 1.0 pass."})
    return figs


def _series(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-budget credit accrual, reconstructed from the event ledger."""
    ledger = Path(run["artifact_dir"]).parent / "progress.jsonl"
    pts: List[Dict[str, Any]] = []
    total = 0.0
    if not ledger.exists():
        return pts
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        total += float(rec["credit"])
        pts.append({"budget": float(rec["budget_used"]), "credits": total})
    budget_total = float(run["cfg"]["budget_total"])
    pts.append({"budget": budget_total, "credits": total})
    return pts


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _dashboard_ratio(cid: str, crit: Dict[str, Any]) -> Optional[float]:
    m = crit.get("measured", {})
    try:
        if cid == "A1":
            return min(m["register_fields"] / 5.0, m["stack_frames"] / 3.0,
                       m["mappings_lines"] / 5.0, 1.0 if m["copy_verifies"] else 0.0)
        if cid in {"A2", "A9"}:
            return 1.0 if crit["passed"] else 0.0
        if cid == "A3":
            return 0.05 / m["relative_error"] if m["relative_error"] > 0 else 1.0
        if cid == "A4":
            return 0.30 / m["first_detection_budget_fraction"]
        if cid == "A5":
            return m["post_reroute_artifacts"] / 1.0
        if cid == "A6":
            return m["ratios"]["retention_vs_produced"] / 0.5
        if cid == "A7":
            return 1.0 if crit["passed"] else 0.0
        if cid == "A8":
            return 1.0 if m["false_positives"] == 0 else 0.0
        if cid == "A10":
            base = 0.05 / m["rel_error_vs_parallel_reference"] if m["rel_error_vs_parallel_reference"] > 0 else 1.0
            return min(base, 1.0 if m["decision"]["threads"] == 1 else 0.0, 1.0 if crit["passed"] else 0.0)
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P148W2-N01 acceptance harness")
    ap.add_argument("--outdir", default=str(HERE))
    ap.add_argument("--skip-figures", action="store_true")
    args = ap.parse_args(argv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    workroot = Path(tempfile.mkdtemp(prefix="p148w2_survival_"))
    started = time.time()
    criteria: Dict[str, Dict[str, Any]] = {}

    a1 = check_a1(workroot)
    criteria[a1["id"]] = a1
    criteria.update(check_resume_family(workroot))
    criteria.update(check_progress_family(workroot))
    a10 = check_a10(workroot)
    criteria[a10["id"]] = a10

    # timeline runs for the figure (cheap re-runs, same parameters as above)
    runs: Dict[str, Dict[str, Any]] = {}
    spec = [
        ("steady", StrategyPlan([Strategy("steady", 0.2)]), 100, 0.25),
        ("bursty", StrategyPlan([Strategy("bursty", 0.05)]), 100, 0.25),
        ("stalled", StrategyPlan([Strategy("barren", 0.0), Strategy("fruitful", 0.05)]), 100, 0.25),
        ("failing", StrategyPlan([Strategy("fruitful_early", 0.1, active_until_budget=40),
                                  Strategy("barren", 0.0)]), 200, 0.10),
    ]
    for label, plan, budget, frac in spec:
        run = run_task(_fresh(workroot, f"fig_{label}"),
                       TaskConfig(budget_total=budget, grid=12, planned_artifacts=budget // 5),
                       plan=plan, stall_frac=frac)
        run["_series"] = _series(run)
        runs[label] = run

    for cid, crit in criteria.items():
        crit["_dash"] = _dashboard_ratio(cid, crit)
    figs: List[Dict[str, str]] = []
    if not args.skip_figures:
        figs = make_figures({**criteria, **runs}, outdir / "figures")

    # copy the evidence directories we want to keep into the repo (bundle + FAIL records)
    keep = outdir / "artifacts"
    if keep.exists():
        shutil.rmtree(keep)
    keep.mkdir(parents=True, exist_ok=True)
    for src in (criteria["A1"]["artifacts"]["bundle"],
                str(Path(criteria["A6"]["artifacts"]["fail_json"]).parent),
                criteria["A10"]["artifacts"]["run"]):
        s = Path(src)
        if s.exists():
            dst = keep / s.name
            if dst.exists():
                shutil.rmtree(dst)
            # the degraded field array is ~19 MB and fully reproducible from the recorded
            # seed/geometry; keep its sha256 in results.json and skip the bytes in git
            shutil.copytree(s, dst, ignore=shutil.ignore_patterns("*.npy"))
    shutil.copy2(HERE / "resume_consistency_report.json", keep / "resume_consistency_report.json")

    ordered = [criteria[k] for k in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10")]
    results = {
        "schema": "survival.acceptance/1",
        "node": "P148W2-N01",
        "mission_id": 10578,
        "generated_unix": time.time(),
        "runtime_s": round(time.time() - started, 2),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "gdb": shutil.which("gdb"),
            "repo_head": _git_head(),
        },
        "criteria": {c["id"]: {k: v for k, v in c.items() if not k.startswith("_")} for c in ordered},
        "summary": {
            "total": len(ordered),
            "passed": sum(1 for c in ordered if c["passed"]),
            "failed": [c["id"] for c in ordered if not c["passed"]],
            "all_passed": all(c["passed"] for c in ordered),
        },
        "figures": figs,
        "artifacts_dir": str(keep),
    }
    (outdir / "results.json").write_text(json.dumps(_jsonable(results), indent=2, sort_keys=True),
                                         encoding="utf-8")
    print(json.dumps(_jsonable(results["summary"]), indent=2))
    for c in ordered:
        print(f"{c['id']:>3} {'PASS' if c['passed'] else 'FAIL'}  {c['name']}")
        if not c["passed"]:
            print("     checks:", json.dumps(_jsonable(c["checks"]), sort_keys=True))
    print("results ->", outdir / "results.json")
    return 0 if results["summary"]["all_passed"] else 1


def _git_head() -> Optional[str]:
    try:
        import subprocess

        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=20).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())