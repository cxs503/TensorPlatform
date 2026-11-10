"""Orchestration: change manifest in, structured regression report out.

Pipeline (criterion (d) in the campaign node)::

    change manifest
      -> impact analysis (ledger + import graph)         [impact.py]
      -> test selection with reason trail
      -> cache probe                                     [cache.py]
      -> execution of the *uncached* subset only         [runner.py]
      -> comparison against the stored baseline
      -> structured JSON report (+ exit code)

Verdict / exit-code contract::

    no_dependents -> 0   nothing tracked by the ledger observes the change
    pass          -> 0   re-run tests show no pass->fail transition
    regression    -> 1   at least one pass->fail transition
    fail          -> 1   at least one failure (pre-existing or new)
    error         -> 2   guard could not complete

The comparison is baseline-relative on purpose: a test that was already red
before the change is *not* a regression (honest FAIL is not laundered into a
new alarm), it is reported under ``pre_existing_failures``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import ResultCache, CacheEntry, dependency_digests
from .impact import ImpactAnalyzer, Impact
from .model import (
    EXIT_CODES,
    GUARD_VERSION,
    REPORT_SCHEMA,
    VERDICT_ERROR,
    VERDICT_FAIL,
    VERDICT_NO_DEPENDENTS,
    VERDICT_PASS,
    VERDICT_PASS_WITH_KNOWN,
    VERDICT_REGRESSION,
    ChangeManifest,
    TaskLedger,
    sha256_obj,
)
from .runner import TestOutcome
from .scanner import RepoGraph

FAILING = {"failed", "error"}


@dataclass
class GuardConfig:
    root: Path | str
    ledger: TaskLedger
    graph: Optional[RepoGraph] = None
    test_roots: Sequence[str] = ()
    include_import_closure: bool = True
    cache_dir: Optional[Path | str] = None
    baseline_path: Optional[Path | str] = None
    pytest_args: Sequence[str] = ("-q", "--tb=short", "-p", "no:cacheprovider")
    timeout: int = 1800
    runners: Any = None  # injected runner (tests / alternate executor)


class RegressionGuard:
    def __init__(self, config: GuardConfig, runner=None) -> None:
        self.config = config
        self.root = Path(config.root)
        self.analyzer = ImpactAnalyzer(
            ledger=config.ledger,
            graph=config.graph,
            root=self.root,
            test_roots=config.test_roots,
            include_import_closure=config.include_import_closure,
        )
        from .cache import ResultCache as _RC
        cache_path = Path(config.cache_dir) / "results.json" if config.cache_dir else None
        self.cache = _RC(cache_path, enabled=config.cache_dir is not None)
        if runner is not None:
            self.runner = runner
        elif config.runners is not None:
            self.runner = config.runners
        else:
            from .runner import PytestRunner
            self.runner = PytestRunner(self.root, args=config.pytest_args, timeout=config.timeout)

    # -- baseline ----------------------------------------------------------
    def load_baseline(self) -> Dict[str, str]:
        if not self.config.baseline_path:
            return {}
        p = Path(self.config.baseline_path)
        if not p.exists():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in raw.get("outcomes", {}).items()}

    def write_baseline(self, outcomes: Iterable[TestOutcome]) -> Path:
        assert self.config.baseline_path
        p = Path(self.config.baseline_path)
        merged = dict(self.load_baseline())
        for o in outcomes:
            merged[o.test_id] = o.outcome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"schema_version": REPORT_SCHEMA, "outcomes": dict(sorted(merged.items()))},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return p

    def _dedupe_selectors(self, selectors: Sequence[str]) -> List[str]:
        """Drop selectors already covered by a broader directory selector.

        Without this, a ledger task declaring ``tests/pkg`` and an import edge
        selecting ``tests/pkg/test_a.py`` would execute that file twice in the
        same run (observed: 48 executions for a 24-case suite).
        """
        dirs = [s for s in selectors if (self.root / s.split("::", 1)[0]).is_dir()]
        keep: List[str] = []
        for sel in selectors:
            rel = sel.split("::", 1)[0]
            if any(rel != d and rel.startswith(d.rstrip("/") + "/") for d in dirs):
                continue
            keep.append(sel)
        return keep

    # -- main entry --------------------------------------------------------
    def run(self, manifest: ChangeManifest, dry_run: bool = False, use_cache: bool = True,
            update_baseline: bool = False) -> Dict[str, Any]:
        impact = self.analyzer.analyze(manifest)
        selected = self._dedupe_selectors(impact.test_ids)
        cache_keys: Dict[str, str] = {}
        outcomes: List[TestOutcome] = []
        to_execute: List[str] = []
        cached_selectors = 0

        if not dry_run:
            for test_id in selected:
                key = self._cache_key(test_id)
                cache_keys[test_id] = key
                hit = self.cache.get(key) if use_cache else None
                if hit is not None:
                    cached_selectors += 1
                    for rec in hit.outcomes:
                        outcomes.append(
                            TestOutcome(
                                test_id=str(rec.get("test_id", test_id)),
                                outcome=str(rec.get("outcome", "not_run")),
                                duration_s=float(rec.get("duration_s", 0.0)),
                                cache_hit=True,
                                executed=False,
                            )
                        )
                    continue
                to_execute.append(test_id)

            if to_execute:
                produced = self.runner.run(to_execute)
                outcomes.extend(produced)
                file_to_selector = {t.split("::", 1)[0]: t for t in to_execute}
                grouped: Dict[str, List[TestOutcome]] = {}
                for outcome in produced:
                    sel = file_to_selector.get(outcome.test_id.split("::", 1)[0], outcome.test_id)
                    grouped.setdefault(sel, []).append(outcome)
                for sel, group in grouped.items():
                    key = cache_keys.get(sel)
                    usable = [o for o in group if o.outcome != "not_run"]
                    if key is None or not usable:
                        continue
                    self.cache.put(
                        CacheEntry(
                            key=key,
                            selector=sel,
                            outcomes=[
                                {"test_id": o.test_id, "outcome": o.outcome,
                                 "duration_s": round(o.duration_s, 6)}
                                for o in usable
                            ],
                            code_digest=self._code_digest(sel),
                        )
                    )

            if self.cache.enabled:
                if self.config.graph is not None:
                    self.cache.gc(self.config.graph.files.keys())
                self.cache.save()
            if update_baseline:
                self.write_baseline(o for o in outcomes if o.outcome != "not_run")

        baseline = self.load_baseline()
        report = self._report(
            manifest, impact, outcomes, baseline, cache_keys, dry_run,
            {"selected_selectors": len(selected), "executed_selectors": len(to_execute),
             "cached_selectors": cached_selectors},
            selected,
        )
        return report

    # -- helpers -----------------------------------------------------------
    def _cache_key(self, test_id: str) -> str:
        digests = dependency_digests(self.root, test_id, self.config.graph)
        test_src = ""
        rel = test_id.split("::", 1)[0]
        facts = self.config.graph.files.get(rel) if self.config.graph else None
        if facts is not None:
            test_src = facts.sha256
        return ResultCache.make_key(test_id, self.config.pytest_args, digests, test_src)

    def _code_digest(self, test_id: str) -> str:
        return sha256_obj(dependency_digests(self.root, test_id, self.config.graph))

    def _report(self, manifest, impact: Impact, outcomes, baseline, cache_keys, dry_run,
                counters: Optional[Dict[str, int]] = None, selected: Sequence[str] = ()) -> Dict[str, Any]:
        selected = list(selected) or impact.test_ids
        by_test = {o.test_id: o for o in outcomes}

        regressions, fixed, pre_existing, newly_failing = [], [], [], []
        for test_id in sorted(by_test):
            now = by_test[test_id].outcome
            before = baseline.get(test_id)
            if before is None:
                if now in FAILING:
                    newly_failing.append(test_id)
                continue
            if before == "passed" and now in FAILING:
                regressions.append(test_id)
            elif before in FAILING and now == "passed":
                fixed.append(test_id)
            elif before in FAILING and now in FAILING:
                pre_existing.append(test_id)

        executed = [o for o in outcomes if o.executed]
        cached = [o for o in outcomes if o.cache_hit]
        not_run = sorted(t for t in by_test if by_test[t].outcome == "not_run")
        if not selected:
            verdict = VERDICT_NO_DEPENDENTS
        elif regressions:
            verdict = VERDICT_REGRESSION
        elif not_run:
            # Fail-closed: a selector that never ran is *no evidence*, so it
            # must never be laundered into a green verdict.
            verdict = VERDICT_ERROR
        elif newly_failing:
            verdict = VERDICT_FAIL
        elif pre_existing:
            # Only tests that were already red re-ran red: do not block, but say so.
            verdict = VERDICT_PASS_WITH_KNOWN
        else:
            verdict = VERDICT_PASS
        if dry_run:
            verdict = VERDICT_NO_DEPENDENTS if not selected else VERDICT_PASS

        unjustified = [s.test_id for s in impact.selections if not s.reasons]
        false_trigger_rate = (len(unjustified) / len(impact.selections)) if impact.selections else 0.0
        counters = counters or {"selected_selectors": len(selected), "executed_selectors": 0,
                                "cached_selectors": 0}
        confirmed = sum(
            1 for s in impact.selections
            for r in s.reasons if r.kind == "import" and "confirmed=true" in r.detail
        )
        import_reasons = sum(1 for s in impact.selections for r in s.reasons if r.kind == "import")

        report: Dict[str, Any] = {
            "schema_version": REPORT_SCHEMA,
            "guard": {"name": "cross_mission_regression_guard", "version": GUARD_VERSION},
            "inputs": {
                "change_manifest": manifest.to_dict(),
                "manifest_digest": manifest.digest,
                "ledger_digest": self.config.ledger.digest,
                "graph_digest": self.config.graph.digest if self.config.graph else "",
                "baseline_digest": sha256_obj(dict(sorted(baseline.items()))),
                "test_roots": list(self.config.test_roots),
                "import_closure": bool(self.config.include_import_closure),
            },
            "impact": impact.to_dict(),
            "selection": {
                "test_ids": sorted(selected),
                "count": len(selected),
                "unjustified": unjustified,
                "analysed_before_dedupe": len(impact.selections),
            },
            "execution": {
                "executed": len(executed),
                "cached": len(cached),
                "invocations": getattr(self.runner, "invocations", 0),
                "outcomes": [by_test[t].to_dict() for t in sorted(by_test)],
            },
            "deltas": {
                "regressions": sorted(regressions),
                "newly_failing": sorted(newly_failing),
                "fixed": sorted(fixed),
                "pre_existing_failures": sorted(pre_existing),
                "unchanged_passed": sorted(
                    t for t in by_test if by_test[t].outcome == "passed" and baseline.get(t) == "passed"
                ),
                    "not_run": not_run,
            },
            "verdict": {"status": verdict, "exit_code": EXIT_CODES[verdict] if not dry_run else 0},
            "metrics": {
                "false_trigger_rate": round(false_trigger_rate, 6),
                # selector-level hit rate (a file with 40 cases counts once) and
                # the outcome-level replay ratio are both reported, explicitly
                "selected_selectors": counters["selected_selectors"],
                "executed_selectors": counters["executed_selectors"],
                "cached_selectors": counters["cached_selectors"],
                "cache_hit_rate": round(
                    counters["cached_selectors"] / counters["selected_selectors"], 6
                ) if counters["selected_selectors"] else 0.0,
                "replay_ratio": round(len(cached) / (len(cached) + len(executed)), 6)
                if (cached or executed) else 0.0,
                "import_reasons": import_reasons,
                "import_reasons_confirmed": confirmed,
                "affected_tasks": len(impact.tasks),
                "affected_features": len(impact.features),
            },
        }
        # Digest = fingerprint of the guard's *conclusion* (what changed, what
        # it affects, what it found, what it decided).  Execution mechanics and
        # metrics are excluded: cache hit-rate and durations legitimately differ
        # between a cold and a warm run of the same change.
        stable = {
            k: report[k]
            for k in ("schema_version", "guard", "inputs", "impact", "selection", "deltas", "verdict")
        }
        report["report_digest"] = sha256_obj(stable)
        return report


def write_report(report: Dict[str, Any], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p