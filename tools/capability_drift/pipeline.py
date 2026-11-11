"""检测流水线：变更 → 触发 → 重跑 → 判据 → 定位 → 告警，一步到位的编排入口。

CI 用法::

    python3 -m tools.capability_drift.ci --registry-dir capdrift --base origin/main

库用法::

    from tools.capability_drift import model, pipeline
    bundle = model.load_bundle("capdrift")
    run = pipeline.monitor(bundle, changed_files=["solver/kernel.py"])
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import attribution, model
from .detector import DriftReport, evaluate
from .runner import CaseRun, run_cases


@dataclass
class MonitorRun:
    report: DriftReport
    runs: Dict[str, CaseRun]
    changed_files: Tuple[str, ...]
    scope: Tuple[str, ...]
    elapsed_s: float
    skipped: bool = False  # 无受影响能力 → 不重跑
    repo_root: Optional[Path] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
            "scope": list(self.scope),
            "skipped": self.skipped,
            "elapsed_s": round(self.elapsed_s, 3),
            "health": self.report.health(),
            "cases": {k: v.as_dict() for k, v in self.runs.items()},
        }


def monitor(
    bundle: model.Bundle,
    *,
    changed_files: Optional[Sequence[str]] = None,
    scope: Optional[Sequence[str]] = None,
    workers: int = 4,
    timeout: float = 300.0,
    threshold_scale: float = 1.0,
    approval_key: Optional[str] = None,
    repo_root: Optional[str | Path] = None,
) -> MonitorRun:
    """跑一轮检测。``changed_files`` 给了就只查受影响能力；都与受影响能力为空则不跑。"""
    started = time.perf_counter()
    changed = tuple(changed_files or ())
    if scope is not None:
        scope_ids = tuple(s for s in scope if bundle.capability(s) is not None)
    elif changed:
        scope_ids = model.affected_capabilities(bundle.capabilities, changed)
    else:
        scope_ids = bundle.ids

    repo = Path(repo_root) if repo_root is not None else bundle.repo_root
    cases = [
        c
        for cap in bundle.capabilities
        if cap.id in scope_ids
        for c in cap.cases
    ]
    runs = run_cases(cases, repo, workers=workers, timeout=timeout) if cases else {}
    runs_by_cap: Dict[str, Dict[str, CaseRun]] = {}
    for cap in bundle.capabilities:
        if cap.id not in scope_ids:
            continue
        runs_by_cap[cap.id] = {c.id: runs[c.id] for c in cap.cases if c.id in runs}
    report = evaluate(
        bundle,
        runs_by_cap,
        scope=scope_ids,
        threshold_scale=threshold_scale,
        approval_key=approval_key,
    )
    return MonitorRun(
        report=report,
        runs=runs,
        changed_files=changed,
        scope=scope_ids,
        elapsed_s=time.perf_counter() - started,
        skipped=not scope_ids,
        repo_root=repo,
    )


def attribute_run(run: MonitorRun, bundle: model.Bundle, *, limit: int = 5) -> Dict[str, List[dict]]:
    """对漂移能力做 git blame 定位（只需在检出内跑，纯读操作）。"""
    if run.repo_root is None:
        return {}
    return attribution.attribute_report(
        run.repo_root, bundle.capabilities, run.report.drifted_capabilities(), limit=limit
    )


def observed_values(
    bundle: model.Bundle, runs: Mapping[str, CaseRun]
) -> Dict[str, Dict[str, float]]:
    """从重跑结果汇总出每能力每指标的实测数字（标定/更新基线用）。"""
    out: Dict[str, Dict[str, float]] = {}
    for cap in bundle.capabilities:
        values: Dict[str, float] = {}
        for case in cap.cases:
            run = runs.get(case.id)
            if run is None or not run.ok:
                continue
            for metric in cap.metrics:
                if metric in run.metrics:
                    values[metric] = run.metrics[metric]
        if values:
            out[cap.id] = values
    return out