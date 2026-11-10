"""Integration cost accounting: standalone vs integrated, per stage and total.

The question this module answers is not "is it fast?" but "how much does
*composing* the stages cost?".  That is measured by replaying every stage on the
exact inputs the integrated run gave it (:class:`~.dag.PipelineRun.inputs_used`):

* ``sum_standalone`` -- median of the summed per-stage medians, i.e. what the
  work would cost if nothing were wired together;
* ``integrated``    -- median wall time of whole-pipeline runs (binding, port
  typing already done statically, digest hashing, chaining, error handling);
* ``overhead_ratio`` = ``(integrated - sum_standalone) / sum_standalone``.

Three details keep the number honest:

1. **Warm-up run first** (import cost of SciPy / the stage libraries is excluded
   from both sides, otherwise "overhead" would just measure import order).
2. **Per-stage replay uses the captured inputs**, so the comparison is the same
   computation, not a cheaper surrogate of it.
3. **Determinism is verified** by comparing the run hash across repeats; if the
   hashes differ the timings describe different work and the report says so.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .dag import Plan, PipelineRun, build_plan, run_pipeline
from .registry import ExecutionContext


@dataclass
class StageTiming:
    node_id: str
    adapter: str
    integrated_median_s: float
    standalone_median_s: float

    @property
    def delta_s(self) -> float:
        return self.integrated_median_s - self.standalone_median_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node_id,
            "adapter": self.adapter,
            "integrated_median_s": self.integrated_median_s,
            "standalone_median_s": self.standalone_median_s,
            "delta_s": self.delta_s,
        }


@dataclass
class OverheadReport:
    spec_name: str
    repeats: int
    integrated_median_s: float
    integrated_samples: List[float]
    standalone_sum_s: float
    stages: List[StageTiming]
    chain_digest: str
    chain_digests: List[str]
    status: str
    evidence: str
    determinism: bool
    #: per-run ``total_wall - sum(node walls)``: the harness's own cost
    orchestration_samples_s: List[float] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def orchestration_s(self) -> float:
        return statistics.median(self.orchestration_samples_s) if self.orchestration_samples_s else 0.0

    @property
    def node_sum_median_s(self) -> float:
        return sum(s.integrated_median_s for s in self.stages)

    @property
    def orchestration_ratio(self) -> float:
        """Harness cost relative to the stage work in the *same* run (primary)."""
        base = self.node_sum_median_s
        if base <= 0.0:
            return 0.0
        return self.orchestration_s / base

    @property
    def overhead_ratio(self) -> float:
        """Cross-run comparison against standalone replay (secondary, noisier)."""
        if self.standalone_sum_s <= 0.0:
            return 0.0
        return (self.integrated_median_s - self.standalone_sum_s) / self.standalone_sum_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec_name,
            "repeats": self.repeats,
            "status": self.status,
            "evidence": self.evidence,
            "chain_digest": self.chain_digest,
            "determinism": self.determinism,
            "integrated_median_s": self.integrated_median_s,
            "integrated_samples_s": list(self.integrated_samples),
            "standalone_sum_s": self.standalone_sum_s,
            "overhead_ratio": self.overhead_ratio,
            "orchestration_s": self.orchestration_s,
            "orchestration_ratio": self.orchestration_ratio,
            "orchestration_samples_s": list(self.orchestration_samples_s),
            "stages": [s.to_dict() for s in self.stages],
            "notes": list(self.notes),
        }


def _replay_stage(plan: Plan, node_id: str, inputs: Mapping[str, Any],
                  ctx: ExecutionContext) -> float:
    adapter = plan.adapters[node_id]
    t0 = time.perf_counter()
    adapter.run(ctx, plan.spec.node(node_id).params, inputs)
    return time.perf_counter() - t0


def measure(
    spec,
    repeats: int = 3,
    ctx: Optional[ExecutionContext] = None,
    plan: Optional[Plan] = None,
) -> OverheadReport:
    """Run the pipeline ``repeats`` times plus one warm-up, then replay stages."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    ctx = ctx or ExecutionContext()
    plan = plan or build_plan(spec)

    warm = run_pipeline(spec, ctx, plan=plan)      # import / cache warm-up, untimed
    first = warm

    integrated: List[float] = []
    orchestration: List[float] = []
    digests: List[str] = []
    runs: List[PipelineRun] = []
    for _ in range(repeats):
        run = run_pipeline(spec, ctx, plan=plan)
        integrated.append(run.wall_s)
        orchestration.append(run.wall_s - sum(r.wall_s for r in run.records))
        digests.append(run.chain_digest)
        runs.append(run)

    # per-stage integrated medians come from the timed runs
    stage_int: Dict[str, List[float]] = {n.node_id: [] for n in spec.nodes}
    for run in runs:
        for rec in run.records:
            stage_int[rec.node_id].append(rec.wall_s)

    # standalone replay on the captured inputs of the first timed run
    base = runs[0] if runs else first
    stage_standalone: Dict[str, List[float]] = {}
    notes: List[str] = []
    for node in spec.nodes:
        rec = base.record(node.node_id)
        if rec.status != "OK":
            stage_standalone[node.node_id] = [0.0]
            notes.append(f"{node.node_id}: not OK ({rec.status}); excluded from cost sum")
            continue
        inputs = base.inputs_used.get(node.node_id, {})
        samples = [_replay_stage(plan, node.node_id, inputs, ctx) for _ in range(repeats)]
        stage_standalone[node.node_id] = samples

    stages: List[StageTiming] = []
    for node in spec.nodes:
        adapter = plan.adapters[node.node_id]
        int_med = statistics.median(stage_int[node.node_id]) if stage_int[node.node_id] else 0.0
        st_med = (statistics.median(stage_standalone[node.node_id])
                  if stage_standalone.get(node.node_id) else 0.0)
        stages.append(StageTiming(
            node_id=node.node_id,
            adapter=adapter.kind,
            integrated_median_s=float(int_med),
            standalone_median_s=float(st_med),
        ))

    standalone_sum = float(sum(s.standalone_median_s for s in stages))
    determinism = len(set(digests)) == 1
    if not determinism:
        notes.append(
            "run digests differ across repeats: outputs are not reproducible, so the "
            "timing comparison mixes different work"
        )
    return OverheadReport(
        spec_name=spec.name,
        repeats=repeats,
        integrated_median_s=float(statistics.median(integrated)) if integrated else 0.0,
        integrated_samples=[float(v) for v in integrated],
        standalone_sum_s=standalone_sum,
        stages=stages,
        chain_digest=digests[0] if digests else "",
        chain_digests=digests,
        status=base.status,
        evidence=base.evidence,
        determinism=determinism,
        orchestration_samples_s=[float(v) for v in orchestration],
        notes=notes,
    )