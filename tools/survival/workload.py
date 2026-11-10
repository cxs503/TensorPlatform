"""A generic budgeted task used to exercise the survival stack end to end.

This is the *test rig*, not a solver: a small periodic relaxation sweep whose only
purpose is to consume budget and emit artifacts under a switchable strategy.  It is
deliberately domain-free (TensorPlatform iron rule) and deliberately deterministic
(seeded, single-threaded) so that every number in the acceptance report is
recomputable.

Two knobs create the failure modes the platform must survive:

* a strategy with ``yield_per_budget == 0`` burns budget while emitting nothing
  (the "busy but barren" long run) -- used for stall detection (A4);
* a ``stop_after`` budget cap lets a run end mid-plan, so honest-FAIL retention
  (A6) can be checked on partial work.

The loop order is fixed and matters: spend budget -> attempt work -> record credit
-> let the meter/stall policy see the result.  Nothing may be credited before it
exists on disk.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from tools.progress_meter.meter import ProgressMeter, Weights
from tools.progress_meter.stall import DEFAULT_STALL_FRAC, StallDetector
from tools.progress_meter.strategies import DEFAULT_PLAN, Strategy, StrategyPlan

from .honest import HonestFail

SCHEMA = "survival.workload/1"


@dataclass
class TaskConfig:
    budget_total: int = 100
    grid: int = 16
    seed: int = 11
    planned_artifacts: int = 50
    criteria: Sequence[Tuple[str, int]] = (("first_artifact", 1), ("half_plan", 25))
    stop_after: Optional[int] = None
    tolerance: float = 0.05

    def as_dict(self) -> Dict[str, Any]:
        return {
            "budget_total": self.budget_total,
            "grid": self.grid,
            "seed": self.seed,
            "planned_artifacts": self.planned_artifacts,
            "criteria": [list(c) for c in self.criteria],
            "stop_after": self.stop_after,
            "tolerance": self.tolerance,
        }


def _entropy(counts: Sequence[int]) -> float:
    total = float(sum(counts))
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BudgetedTask:
    """Budgets in iterations; artifacts are JSON summaries of the relaxation field."""

    def __init__(self, cfg: TaskConfig) -> None:
        self.cfg = cfg
        rng = np.random.default_rng(cfg.seed)
        self.u = rng.standard_normal((cfg.grid, cfg.grid))
        self.step = 0
        #: Beta(1,1) belief over the binary outcome "field mean inside tolerance".
        self.alpha = 1.0
        self.beta = 1.0
        self.tolerance = float(cfg.tolerance)

    def sweep(self) -> None:
        u = self.u
        lap = np.roll(u, 1, 0) + np.roll(u, -1, 0) + np.roll(u, 1, 1) + np.roll(u, -1, 1) - 4.0 * u
        self.u = u + 0.1 * lap
        self.step += 1

    def emit(self, artifact_dir: Path, tag: str = "") -> Dict[str, Any]:
        mean = float(self.u.mean())
        std = float(self.u.std())
        payload = {
            "step": self.step,
            "mean": mean,
            "std": std,
            "sha256": _sha256_bytes(self.u.tobytes()),
            "strategy_tag": tag,
        }
        name = f"art_{self.step:04d}{('_' + tag) if tag else ''}.json"
        p = artifact_dir / name
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return {"name": name, "mean": mean, "std": std, "path": str(p),
                "sha256": payload["sha256"]}

    # -- measured information gain ----------------------------------------- #
    @staticmethod
    def _binary_entropy(p: float) -> float:
        p = min(1.0, max(0.0, p))
        if p in (0.0, 1.0):
            return 0.0
        return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))

    def observe_info(self, value: float) -> float:
        """Bits of posterior entropy removed about "is the run on target?".

        The belief is Beta(alpha, beta) over the binary event ``|mean| <= tol`` and
        every artifact is one Bernoulli observation.  Information gain is therefore
        ``H(p_before) - H(p_after)`` in bits -- a measured quantity, not a claim.
        """
        p_before = self.alpha / (self.alpha + self.beta)
        h_before = self._binary_entropy(p_before)
        if abs(float(value)) <= self.tolerance:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        p_after = self.alpha / (self.alpha + self.beta)
        return max(0.0, h_before - self._binary_entropy(p_after))


def run_task(
    workdir: Path | str,
    cfg: Optional[TaskConfig] = None,
    plan: Optional[StrategyPlan] = None,
    stall_frac: float = DEFAULT_STALL_FRAC,
    weights: Weights = Weights(),
    stop_after: Optional[int] = None,
    fail_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the budgeted task under meter + stall policy; return an auditable report."""
    cfg = cfg or TaskConfig()
    workdir = Path(workdir)
    artifact_dir = workdir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    plan = plan or StrategyPlan(list(DEFAULT_PLAN))
    meter = ProgressMeter(
        budget_total=cfg.budget_total,
        weights=weights,
        ledger_path=workdir / "progress.jsonl",
        artifact_dir=artifact_dir,
    )
    detector = StallDetector(meter, plan, stall_frac=stall_frac)
    task = BudgetedTask(cfg)
    honest = HonestFail(artifact_dir=artifact_dir, planned_total=cfg.planned_artifacts)
    actions: List[Dict[str, Any]] = []
    artifacts: List[Dict[str, Any]] = []
    criteria_done: set[str] = set()
    stop_at = int(stop_after if stop_after is not None else (cfg.stop_after or cfg.budget_total))
    forced_minimal = False
    status = "COMPLETE"
    fail_payload: Optional[Dict[str, Any]] = None
    actions_path = workdir / "actions.jsonl"

    def log_action(rec: Dict[str, Any]) -> None:
        actions.append(rec)
        with actions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    for step in range(1, stop_at + 1):
        meter.advance(budget_used=step)
        task.sweep()
        strat: Strategy = plan.current
        if forced_minimal:
            minimal = next((s for s in plan.strategies if s.minimal), strat)
            strat = minimal
        dying = strat.active_until_budget is not None and meter.budget_used > strat.active_until_budget
        if strat.yield_per_budget > 0 and not dying:
            interval = max(1, int(round(1.0 / strat.yield_per_budget)))
            if step % interval == 0:
                rec = task.emit(artifact_dir, tag=strat.name if strat.name != "baseline" else "")
                meter.record_artifact(rec["name"], digest=rec["sha256"])
                artifacts.append({**rec, "budget": step, "strategy": strat.name})
                honest.register(rec["name"])
                bits = task.observe_info(rec["mean"])
                if bits > 0:
                    meter.record_info_gain(f"obs_{step:04d}", bits)
        for cname, threshold in cfg.criteria:
            if cname in criteria_done:
                continue
            if len(artifacts) >= int(threshold):
                meter.record_criterion(cname, True)
                criteria_done.add(cname)
                log_action({"kind": "criterion_pass", "at_budget": step, "criterion": cname})

        event = detector.observe()
        if event is None:
            continue
        log_action(
            {
                "kind": "stall",
                "at_budget": step,
                "level": event.level,
                "detail": event.detail,
            }
        )
        if event.level == "restate":
            restate = workdir / "restatements"
            restate.mkdir(exist_ok=True)
            (restate / f"restate_{event.index:02d}.json").write_text(
                json.dumps(
                    {
                        "criteria": [list(c) for c in cfg.criteria],
                        "budget_total": cfg.budget_total,
                        "budget_used": meter.budget_used,
                        "current_strategy": plan.current.name,
                        "instruction": "resume the declared pipeline; emit one artifact per interval",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        elif event.level == "reroute":
            log_action({"kind": "reroute", "at_budget": step, **event.detail})
        elif event.level == "shrink":
            forced_minimal = True
            log_action({"kind": "shrink", "at_budget": step, **event.detail})
        elif event.level == "honest_fail":
            status = "FAIL"
            # Flush the live state as a final artifact *before* declaring FAIL: partial
            # work must survive the failure (A6 retention floor is enforced in honest.py).
            rec = task.emit(artifact_dir, tag="final_state")
            artifacts.append({**rec, "budget": step, "strategy": "final_state"})
            honest.register(rec["name"])
            honest.note("stall_ladder_exhausted", {"at_budget": step})
            fail_payload = honest.write(
                reason=fail_reason or f"no credit for {detector.stall_window:.0f} budget units at rung 4",
                extra={"stall_events": [e.as_dict() for e in detector.events],
                       "budget_used": meter.budget_used, "criteria_passed": sorted(criteria_done)},
                resume_point={"last_artifact": artifacts[-1]["name"] if artifacts else None,
                              "budget_used": meter.budget_used},
            )
            break

    # ---- report ---------------------------------------------------------- #
    reroute_at = next((a["at_budget"] for a in actions if a["kind"] == "reroute"), None)
    post_reroute = (
        [a for a in artifacts if a["budget"] > reroute_at] if reroute_at is not None else []
    )
    # budget for the manual recount comes from the loop counter, not from the meter
    manual = meter.manual_recount(budget_used=float(task.step), artifact_dir=artifact_dir)
    snapshot = meter.snapshot()
    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "cfg": cfg.as_dict(),
        "budget_used": meter.budget_used,
        "credits": snapshot["credits"],
        "progress_rate": snapshot["progress_rate"],
        "artifacts": [a["name"] for a in artifacts],
        "artifact_count": len(artifacts),
        "criteria_passed": sorted(criteria_done),
        "info_gain_bits": snapshot["info_gain_bits"],
        "meter": snapshot,
        "manual": manual,
        "progress_rate_error": ProgressMeter.relative_error(snapshot["progress_rate"], manual["progress_rate"]),
        "stall": detector.as_dict(),
        "actions": actions,
        "reroute_at_budget": reroute_at,
        "post_reroute_artifacts": len(post_reroute),
        "plan": plan.as_dict(),
        "fail": fail_payload,
        "artifact_dir": str(artifact_dir),
        "finished_unix": time.time(),
    }
    (workdir / "task_report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    detector.write(workdir / "stall.json")
    return report