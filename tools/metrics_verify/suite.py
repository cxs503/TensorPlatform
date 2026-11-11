#!/usr/bin/env python3
"""Acceptance suite for the platform metrics tools (pulse / chronicle / portrait).

Produces, in one deterministic run:

* ``report.json``  -- every criterion with its number, threshold, verdict, the
  seeds used, the git commit and the sha256 of the four modules under test;
* ``report.md``    -- the same tables rendered programmatically (no hand-copied
  digits: the markdown is generated from the JSON payload);
* ``fig*.png``     -- four figures built from the same payload.

Usage::

    python3 -m tools.metrics_verify.suite --out artifacts/metrics_verify/latest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tools.pulse import saturation_pulse as sp
from tools.worker_portrait import portrait as wp

from . import chronicle_integrity, figures, portrait_consistency, pulse_latency, report_doc
from .api import SPEC, app
from .sim import FleetConfig, simulate_drift_stream, simulate_fleet

MODULES = (
    "tools/metrics_verify/stats.py",
    "tools/metrics_verify/sim.py",
    "tools/metrics_verify/pulse_latency.py",
    "tools/metrics_verify/chronicle_integrity.py",
    "tools/metrics_verify/portrait_consistency.py",
    "tools/metrics_verify/api.py",
    "tools/metrics_verify/suite.py",
)
UNDER_TEST = (
    "tools/pulse/saturation_pulse.py",
    "tools/worker_portrait/portrait.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # pragma: no cover - environment without git
        return "UNKNOWN"


def _pytest_test_count(path: str = "tests/metrics_verify") -> int:
    """Count collected tests without executing them (criterion g)."""
    try:
        out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", path],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:  # pragma: no cover
        return 0
    for line in reversed(out.splitlines()):
        line = line.strip()
        if " test" in line and ("collected" in line or "test" in line):
            parts = line.split()
            for token in parts:
                if token.isdigit():
                    return int(token)
    return sum(1 for line in out.splitlines() if "::" in line)


def _api_probe() -> Dict[str, Any]:
    """Route inventory + 422 behaviour of all three endpoints (criterion g)."""
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        return {"available": False, "error": str(exc)}
    client = TestClient(app)
    verify_routes = sorted(
        r.path for r in app.routes
        if getattr(r, "path", "").startswith("/v1/metrics_verify")
        and getattr(r, "methods", set()) and "POST" in getattr(r, "methods", set())
    )
    probes = {
        "pulse": ("/v1/metrics_verify/pulse", {"n_agents": 0}),
        "chronicle": ("/v1/metrics_verify/chronicle", {"n_events": -5}),
        "portrait": ("/v1/metrics_verify/portrait", {"missions_per_agent": "not-a-number"}),
    }
    status: Dict[str, int] = {}
    for name, (path, bad_body) in probes.items():
        status[name] = client.post(path, json=bad_body).status_code
    return {
        "available": True,
        "verify_routes": verify_routes,
        "n_verify_routes": len(verify_routes),
        "invalid_input_status": status,
        "all_422": all(code == 422 for code in status.values()),
    }


def run_all(*, out_dir: Path, pulse_cfg: Optional[FleetConfig] = None,
            chronicle_events: int = 10000, portrait_agents: int = 8,
            portrait_missions: int = 200, make_figures: bool = True,
            pytest_probe: bool = True) -> Dict[str, Any]:
    pulse_cfg = pulse_cfg or FleetConfig(n_agents=100, n_heartbeats=1000, seed=20261111)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- pulse (criteria a, b) ---------------- #
    pulse = pulse_latency.run_pulse_verify(pulse_cfg)
    fleet = simulate_fleet(pulse_cfg)

    # ---------------- chronicle (criteria c, d) ------------ #
    chronicle = chronicle_integrity.run_chronicle_verify(n_events=chronicle_events, seed=20261111)

    # ---------------- portrait (criteria e, f) ------------- #
    portrait = portrait_consistency.run_portrait_verify(
        n_agents=portrait_agents, missions_per_agent=portrait_missions, seed=20261111)

    # ---------------- criterion g -------------------------- #
    api_probe = _api_probe()
    n_tests = _pytest_test_count() if pytest_probe else 0
    g_pass = bool(api_probe.get("n_verify_routes") == 3 and api_probe.get("all_422")
                  and n_tests >= 15)

    criteria: Dict[str, Dict[str, Any]] = {
        "a_pulse_latency": {
            "description": "heartbeat latency mean < 1 s and P99 < 5 s",
            "n_agents": pulse_cfg.n_agents,
            "n_heartbeats_per_agent": pulse_cfg.n_heartbeats,
            "n_samples": pulse["latency"]["n_latency_samples"],
            "mean_s": pulse["ceiling"]["mean_s"],
            "mean_limit_s": pulse["ceiling"]["mean_limit_s"],
            "p99_s": pulse["ceiling"]["p99_s"],
            "p99_limit_s": pulse["ceiling"]["p99_limit_s"],
            "pass": pulse["ceiling"]["pass"],
        },
        "b_pulse_loss_detection": {
            "description": "5 % injected heartbeat loss detected at > 95 %",
            "injected_rate": 0.05,
            "n_injected": pulse["loss"]["n_injected"],
            "n_detected": pulse["loss"]["n_detected"],
            "detection_rate_strict": pulse["loss"]["detection_rate_strict"],
            "detection_rate_detectable": pulse["loss"]["detection_rate_detectable"],
            "n_undetectable_trailing": pulse["loss"]["n_undetectable_trailing"],
            "n_false_positive_gaps": pulse["loss"]["n_false_positive_gaps"],
            "threshold": pulse["loss"]["threshold_detection"],
            "pass": pulse["loss"]["pass"],
        },
        "c_chronicle_integrity": {
            "description": "10000 events: no loss, order correct 100 %",
            "n_events": chronicle["honest"]["n_events"],
            "n_missing_vs_truth": chronicle["honest"]["n_missing_vs_truth"],
            "completeness": chronicle["honest"]["completeness"],
            "order_correct_rate": chronicle["honest"]["order_correct_rate"],
            "n_seq_regressions": chronicle["honest"]["n_seq_regressions"],
            "n_ts_regressions": chronicle["honest"]["n_ts_regressions"],
            "pass": chronicle["honest"]["pass"],
        },
        "d_chronicle_disorder": {
            "description": "1 % injected out-of-order detected at > 99 %",
            "injected_rate": 0.01,
            "n_injected_transpositions": chronicle["disorder"]["n_injected_transpositions"],
            "n_injected_events": chronicle["disorder"]["n_injected_events"],
            "n_injected_detected": chronicle["disorder"]["n_injected_detected"],
            "detection_rate": chronicle["disorder"]["detection_rate"],
            "n_false_alarm_positions": chronicle["disorder"]["n_false_alarm_positions"],
            "threshold": chronicle["disorder"]["threshold"],
            "pass": chronicle["disorder"]["pass"],
        },
        "e_portrait_consistency": {
            "description": "success rate / mean wall time / domain mix vs truth < 5 %",
            "n_workers": portrait["consistency"]["n_workers"],
            "n_missions": portrait["consistency"]["n_missions"],
            "max_success_rate_abs_error": portrait["consistency"]["max_success_rate_abs_error"],
            "max_mean_wall_rel_error": portrait["consistency"]["max_mean_wall_rel_error"],
            "max_domain_share_abs_error": portrait["consistency"]["max_domain_share_abs_error"],
            "tolerance": portrait["consistency"]["tolerance"],
            "traceability_ok": portrait["consistency"]["traceability"]["ok"],
            "pass": portrait["consistency"]["pass"],
        },
        "f_portrait_drift": {
            "description": "behavioural regime change flagged within 10 events",
            "n_trials": portrait.get("drift", {}).get("n_trials"),
            "latency_mean": portrait.get("drift", {}).get("latency_mean"),
            "latency_max": portrait.get("drift", {}).get("latency_max"),
            "latency_threshold": portrait.get("drift", {}).get("latency_threshold"),
            "n_missed": portrait.get("drift", {}).get("n_missed"),
            "n_false_alarms_stable": portrait.get("drift", {}).get("n_false_alarms_stable"),
            "pass": portrait.get("drift", {}).get("pass", False),
        },
        "g_harness_hygiene": {
            "description": "pytest >= 15 cases, 3 API endpoints, invalid input -> 422",
            "n_pytest_cases": n_tests,
            "n_verify_routes": api_probe.get("n_verify_routes"),
            "invalid_input_status": api_probe.get("invalid_input_status"),
            "pass": g_pass,
        },
    }

    # ---------------- negative controls (rig must be able to FAIL) ---- #
    controls = {
        "pulse_stall_sensitivity": pulse["sensitivity_control"],
        "pulse_loss_false_positive": pulse["loss_false_positive_control"],
        "chronicle_drop": chronicle["drop_control"],
        "chronicle_duplicate": chronicle["duplicate_control"],
        "portrait_corruption": portrait["corruption_control"],
    }
    all_pass = all(c["pass"] for c in criteria.values()) and all(c["pass"] for c in controls.values())

    figure_paths: Dict[str, str] = {}
    if make_figures:
        drift_example = portrait_consistency.detect_success_drift(
            simulate_drift_stream(seed=11).outcomes, change_index=150)
        figure_paths = {
            "fig1_pulse_latency": figures.fig_pulse_latency(
                fleet.delivered_latencies, pulse["latency"]["measured"],
                pulse["latency"]["injected"], out_dir / "fig1_pulse_latency.png"),
            "fig2_chronicle_integrity": figures.fig_chronicle(
                chronicle, out_dir / "fig2_chronicle_integrity.png"),
            "fig3_portrait_consistency": figures.fig_portrait(
                portrait["consistency"], portrait["consistency_workers"],
                out_dir / "fig3_portrait_consistency.png"),
            "fig4_portrait_drift": figures.fig_drift(
                portrait["drift"], drift_example, out_dir / "fig4_portrait_drift.png"),
        }

    report: Dict[str, Any] = {
        "run_id": datetime.now(timezone.utc).strftime("mv-%Y%m%dT%H%M%SZ"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "spec": SPEC,
        "parameters": {
            "pulse": pulse_cfg.__dict__,
            "chronicle": {"n_events": chronicle_events, "sessions": 5, "seed": 20261111},
            "portrait": {"n_agents": portrait_agents, "missions_per_agent": portrait_missions,
                         "seed": 20261111},
        },
        "criteria": criteria,
        "all_criteria_pass": all(c["pass"] for c in criteria.values()),
        "negative_controls": controls,
        "all_controls_pass": all(c["pass"] for c in controls.values()),
        "overall_pass": all_pass,
        "evidence": {
            "modules_sha256": {p: _sha256(Path(p)) for p in MODULES},
            "modules_under_test_sha256": {p: _sha256(Path(p)) for p in UNDER_TEST},
            "under_test_versions": {
                "saturation_pulse": getattr(sp, "COLLECTOR_ID", "tools/pulse/saturation_pulse.py"),
                "worker_portrait_schema": getattr(wp, "SCHEMA", None),
                "worker_portrait_definition_version": getattr(wp, "DEFINITION_VERSION", None),
            },
            "figures": {k: {"path": v, "sha256": _sha256(Path(v))} for k, v in figure_paths.items()},
            "api_probe": api_probe,
        },
        "pulse_detail": {
            "latency": pulse["latency"],
            "loss": pulse["loss"],
            "core": pulse["core"],
        },
        "chronicle_detail": chronicle,
        "portrait_detail": {
            "consistency": portrait["consistency"],
            "consistency_workers": portrait["consistency_workers"],
            "drift": portrait.get("drift"),
            "domain_drift": portrait.get("domain_drift"),
        },
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    (out_dir / "report.md").write_text(render_markdown(report) + "\n")
    (out_dir / "report_paper.md").write_text(report_doc.render_paper(report) + "\n")
    return report


def _fmt(value: Any, nd: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "PASS" if value else "**FAIL**"
    if isinstance(value, float):
        return f"{value:.{nd}g}"
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the acceptance tables straight from the JSON payload (no hand-copy)."""
    lines: List[str] = []
    lines.append("# Metrics-tool verification run")
    lines.append("")
    lines.append(f"- run_id: `{report['run_id']}`")
    lines.append(f"- generated_at: `{report['generated_at']}`")
    lines.append(f"- git_commit: `{report['git_commit']}`")
    lines.append(f"- overall_pass: **{report['overall_pass']}**")
    lines.append("")
    lines.append("## Acceptance criteria")
    lines.append("")
    lines.append("| id | criterion | key number | threshold | verdict |")
    lines.append("|---|---|---|---|---|")
    key_map = {
        "a_pulse_latency": "mean / P99",
        "b_pulse_loss_detection": "detection_rate_strict",
        "c_chronicle_integrity": "completeness / order_correct_rate",
        "d_chronicle_disorder": "detection_rate",
        "e_portrait_consistency": "max error",
        "f_portrait_drift": "latency_max",
        "g_harness_hygiene": "n_pytest_cases / n_verify_routes",
    }
    for cid, c in report["criteria"].items():
        if cid == "a_pulse_latency":
            number = f"mean={_fmt(c['mean_s'])} s, P99={_fmt(c['p99_s'])} s"
            thr = f"< {c['mean_limit_s']} s / < {c['p99_limit_s']} s"
        elif cid in ("b_pulse_loss_detection", "d_chronicle_disorder"):
            number = _fmt(c["detection_rate"] if cid == "d_chronicle_disorder" else c["detection_rate_strict"])
            thr = f"> {c['threshold']}"
        elif cid == "c_chronicle_integrity":
            number = f"completeness={_fmt(c['completeness'])}, order={_fmt(c['order_correct_rate'])}"
            thr = "= 1.0 / = 1.0"
        elif cid == "e_portrait_consistency":
            number = (f"rate={_fmt(c['max_success_rate_abs_error'])}, "
                      f"wall={_fmt(c['max_mean_wall_rel_error'])}, "
                      f"domain={_fmt(c['max_domain_share_abs_error'])}")
            thr = f"< {c['tolerance']}"
        elif cid == "f_portrait_drift":
            number = f"latency_max={_fmt(c['latency_max'])} events"
            thr = f"< {c['latency_threshold']} events"
        else:
            number = f"{c['n_pytest_cases']} tests, {c['n_verify_routes']} endpoints"
            thr = ">= 15 / = 3 / 422"
        lines.append(f"| {cid} | {key_map.get(cid, '')} | {number} | {thr} | {_fmt(c['pass'])} |")
    lines.append("")
    lines.append("## Negative controls (the rig must be able to fail)")
    lines.append("")
    lines.append("| control | observed | verdict |")
    lines.append("|---|---|---|")
    for name, c in report["negative_controls"].items():
        observed = {k: v for k, v in c.items() if k not in ("pass", "report")}
        snippet = ", ".join(f"{k}={_fmt(v)}" for k, v in list(observed.items())[:4])
        lines.append(f"| {name} | {snippet} | {_fmt(c.get('pass'))} |")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append("| artifact | sha256 |")
    lines.append("|---|---|")
    for path, digest in report["evidence"]["modules_under_test_sha256"].items():
        lines.append(f"| `{path}` (under test) | `{digest[:16]}…` |")
    for name, meta in report["evidence"]["figures"].items():
        lines.append(f"| `{meta['path']}` | `{meta['sha256'][:16]}…` |")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/metrics_verify/latest")
    parser.add_argument("--pulse-agents", type=int, default=100)
    parser.add_argument("--pulse-heartbeats", type=int, default=1000)
    parser.add_argument("--chronicle-events", type=int, default=10000)
    parser.add_argument("--portrait-agents", type=int, default=8)
    parser.add_argument("--portrait-missions", type=int, default=200)
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args(argv)
    report = run_all(
        out_dir=Path(args.out),
        pulse_cfg=FleetConfig(n_agents=args.pulse_agents, n_heartbeats=args.pulse_heartbeats),
        chronicle_events=args.chronicle_events,
        portrait_agents=args.portrait_agents,
        portrait_missions=args.portrait_missions,
        make_figures=not args.no_figures,
    )
    print(json.dumps({"overall_pass": report["overall_pass"],
                      "criteria": {k: v["pass"] for k, v in report["criteria"].items()},
                      "out": args.out}, indent=2))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())