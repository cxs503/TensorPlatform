"""Evidence aggregator: criteria x artifact metrics -> PASS/FAIL/UNKNOWN.

Resolution ladder (strongest anchor first, each step records its own score so
the report can show *why* a number was used):

1. ``metric_name`` - the criterion text itself names a metric identifier.
2. ``hint``        - a lexicon hint (error / duration / test_count / ...)
                     resolved scope-first: same record, then same artifact,
                     then the whole directory.
3. ``declared_tolerance`` - the criterion carries a number and the *same
                     artifact record* declares a tolerance equal to it; the
                     record's own error field is then taken as the observation
                     (score 0.75, always flagged in the caveats).
4. nothing         - the criterion stays UNKNOWN.  It is never guessed.

A node is only PASS when every criterion is PASS.  FAIL is sticky and UNKNOWN
downgrades to INCOMPLETE, so the engine can never launder missing evidence into
a green verdict.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Sequence

from .criteria import error_leaf_rank, load_lexicon
from .model import (
    Comparator,
    Criterion,
    CriterionOutcome,
    MetricRecord,
    NodeEvidence,
    NodeVerdict,
    Observation,
    Verdict,
    node_verdict_from_outcomes,
)

_EPS = 1e-12


# ---------------------------------------------------------------------------
# Scoped selection helpers
# ---------------------------------------------------------------------------

def _in_scope(metric: MetricRecord, scope: str) -> bool:
    if not scope:
        return False
    return metric.scope == scope or metric.scope.startswith(scope + ".") or metric.scope.startswith(scope + "[")


def _scope_metrics(evidence: NodeEvidence, scope: str) -> list[MetricRecord]:
    if not scope:
        return []
    return [m for m in evidence.metrics if _in_scope(m, scope)]


def _is_flag(metric: MetricRecord) -> bool:
    if metric.kind not in ("bool", "status"):
        return False
    leaf = metric.leaf.lower()
    return (
        leaf in ("passed", "pass", "ok", "green", "all_passed", "all_pass", "all_green")
        or leaf.endswith("_pass") or leaf.endswith("_passed")
        or leaf.startswith("pass_") or leaf == "status" or leaf.endswith("_status")
    )


def _numeric(metrics: Iterable[MetricRecord]) -> list[MetricRecord]:
    return [m for m in metrics if m.kind == "number" and m.value is not None]


def _error_metrics(evidence: NodeEvidence, criterion: Criterion, lex: dict) -> tuple[list[MetricRecord], float]:
    words = tuple(w.lower() for w in lex["metric_hints"]["error"])
    tol = tuple(k.lower() for k in lex.get("tolerance_keys", []))
    picks = [m for m in _numeric(evidence.metrics)
             if m.leaf.lower() not in tol and any(w in m.leaf.lower() for w in words)]
    # keep only the most specific error-name class so that a method name such as
    # "finite_difference_k" cannot shadow the recorded "relative_error"
    if picks:
        best = min(error_leaf_rank(m.leaf, lex) for m in picks)
        picks = [m for m in picks if error_leaf_rank(m.leaf, lex) == best]
    scoped = [m for m in picks if _in_scope(m, criterion.scope)]
    if scoped:
        return scoped, 0.85
    in_src = [m for m in picks if m.source == criterion.source]
    if in_src:
        return in_src, 0.75
    return picks, 0.7


def _duration_metrics(evidence: NodeEvidence, criterion: Criterion, lex: dict) -> tuple[list[MetricRecord], float]:
    words = tuple(w.lower() for w in lex["metric_hints"]["duration"])
    picks = [m for m in _numeric(evidence.metrics) if any(w in m.leaf.lower() for w in words)]
    scoped = [m for m in picks if _in_scope(m, criterion.scope)]
    if scoped:
        return scoped, 0.85
    in_src = [m for m in picks if m.source == criterion.source]
    if in_src:
        return in_src, 0.8
    return picks, 0.75


def _flags(evidence: NodeEvidence, criterion: Criterion) -> tuple[list[MetricRecord], float]:
    flags = [m for m in evidence.metrics if _is_flag(m)]
    scoped = [m for m in flags if _in_scope(m, criterion.scope)]
    if scoped:
        return scoped, 0.85
    in_src = [m for m in flags if m.source == criterion.source]
    if in_src:
        return in_src, 0.75
    return flags, 0.6


def _scoped_count(evidence: NodeEvidence, criterion: Criterion, lex: dict) -> Observation | None:
    """Count-like numeric inside the criterion's own record (strongest anchor)."""
    scoped = _numeric(_scope_metrics(evidence, criterion.scope))
    if not scoped:
        return None
    words = tuple(w.lower() for w in lex["metric_hints"].get("count", [])) + (
        "test", "total", "n_", "num_")
    cand = [m for m in scoped if any(w in m.leaf.lower() for w in words)]
    if not cand:
        return None
    best = max(cand, key=lambda m: float(m.value))
    return Observation(value=float(best.value), matched_keys=(best.key,), source=best.source,
                       scope=best.scope, match_score=0.9)


def _suites(evidence: NodeEvidence, criterion: Criterion) -> tuple[list[dict], float]:
    suites = evidence.derived.get("test_suites") or []
    in_src = [s for s in suites if not criterion.source or s.get("source") == criterion.source]
    if in_src:
        return in_src, 0.85 if criterion.source else 0.75
    return suites, 0.7


def _worst(metrics: Sequence[MetricRecord], score: float) -> Observation | None:
    if not metrics:
        return None
    worst = max(metrics, key=lambda m: abs(float(m.value)))
    return Observation(value=float(worst.value), matched_keys=tuple(m.key for m in metrics[:5]),
                       source=worst.source, scope=worst.scope, match_score=score)


# ---------------------------------------------------------------------------
# Metric lookup by explicit identifier
# ---------------------------------------------------------------------------

def _find_by_name(metrics: Sequence[MetricRecord], name: str) -> Observation | None:
    target = name.strip()
    base = target.split("[")[0]
    best: tuple[float, MetricRecord] | None = None
    for m in metrics:
        if m.kind != "number" or m.value is None:
            continue
        leaf = m.leaf
        if leaf == target or m.key == target:
            score = 1.0
        elif leaf == base:
            score = 0.95
        elif m.key.endswith("." + target) or m.key.endswith("." + base):
            score = 0.9
        elif target.lower() in m.key.lower():
            score = 0.8
        else:
            continue
        if best is None or score > best[0]:
            best = (score, m)
    if best is None:
        return None
    score, m = best
    return Observation(value=float(m.value), matched_keys=(m.key,), source=m.source, scope=m.scope, match_score=score)


# ---------------------------------------------------------------------------
# Hint resolution
# ---------------------------------------------------------------------------

def _from_derived(hint: str, evidence: NodeEvidence, criterion: Criterion, lex: dict) -> Observation | None:
    d = evidence.derived
    if hint == "error":
        return _worst(*_error_metrics(evidence, criterion, lex))
    if hint == "duration":
        return _worst(*_duration_metrics(evidence, criterion, lex))
    if hint in ("test_count", "case_count"):
        scoped = _scoped_count(evidence, criterion, lex)
        if scoped is not None:
            return scoped
    if hint in ("test_count", "test_pass_rate"):
        suites, score = _suites(evidence, criterion)
        if not suites:
            return None
        total = max(float(s["total"]) for s in suites)
        passed = max(float(s["passed"]) for s in suites)
        if hint == "test_count":
            return Observation(value=total, matched_keys=tuple(k for s in suites for k in s.get("keys", []))[:5],
                               source=suites[0]["source"], match_score=score)
        if total <= 0:
            return None
        return Observation(value=passed / total, source=suites[0]["source"], match_score=score)
    if hint == "case_count":
        n = float(d.get("verified_case_count") or 0)
        # "no verified/ directory" is absence of that artifact, not evidence that
        # the node ran zero cases -> refuse rather than report a hard zero
        if n <= 0:
            return None
        return Observation(value=n, source="derived.verified_case_count", match_score=0.8)
    if hint == "ratio":
        flags, score = _flags(evidence, criterion)
        if not flags:
            return None
        good = sum(1 for m in flags if bool(m.value))
        return Observation(value=good / len(flags), matched_keys=tuple(m.key for m in flags[:5]),
                           source=flags[0].source, match_score=score)
    if hint == "all_true":
        flags, score = _flags(evidence, criterion)
        if not flags:
            return None
        return Observation(value=all(bool(m.value) for m in flags),
                           matched_keys=tuple(m.key for m in flags[:5]),
                           source=flags[0].source, match_score=score)
    if hint == "count":
        counted = d.get("count_metrics") or []
        if counted:
            top = max(counted, key=lambda x: float(x["value"]))
            return Observation(value=float(top["value"]), matched_keys=(top["key"],),
                               source="derived.count_metrics", match_score=0.5)
    return None


def _declared_tolerance_observation(criterion: Criterion, evidence: NodeEvidence, lex: dict) -> Observation | None:
    """Resolve a number-only criterion through a sibling declared tolerance."""
    if criterion.threshold is None:
        return None
    tol_keys = tuple(k.lower() for k in lex.get("tolerance_keys", []))
    err_words = tuple(w.lower() for w in lex["metric_hints"]["error"])
    scoped = _scope_metrics(evidence, criterion.scope)
    tolerance_hit = None
    for m in scoped:
        if m.kind != "number" or m.value is None:
            continue
        if m.leaf.lower() not in tol_keys:
            continue
        if _close(abs(float(m.value)), abs(criterion.threshold)):
            tolerance_hit = m
            break
    if tolerance_hit is None:
        return None
    errs = [m for m in scoped if m.kind == "number" and m.value is not None
            and any(w in m.leaf.lower() for w in err_words) and m.leaf.lower() not in tol_keys]
    if not errs:
        return None
    worst = max(errs, key=lambda m: abs(float(m.value)))
    return Observation(
        value=float(worst.value),
        matched_keys=(tolerance_hit.key, worst.key),
        source=worst.source,
        scope=criterion.scope,
        match_score=0.75,
        scaling="declared_tolerance",
    )


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    denom = max(abs(a), abs(b), _EPS)
    return abs(a - b) / denom < 1e-9


# ---------------------------------------------------------------------------
# Observation + judgement
# ---------------------------------------------------------------------------

def observe(criterion: Criterion, evidence: NodeEvidence, lex: dict | None = None) -> Observation:
    lex = lex or load_lexicon()
    if criterion.metric_name:
        obs = _find_by_name(evidence.metrics, criterion.metric_name)
        if obs is not None:
            return obs
    hints: list[str] = []
    # a numeric criterion that only matched a boolean hint ("... >= 8 cases, all
    # passing") needs a number first: numeric hints are tried before the
    # boolean one.  With no hint at all nothing is inferred.
    if criterion.hint:
        if criterion.comparator in (Comparator.ALL_TRUE, Comparator.PRESENT):
            hints.append(criterion.hint)
        else:
            for alt in ("test_count", "test_pass_rate", "case_count", "ratio", "count"):
                hints.append(alt)
            hints.append(criterion.hint)
    for hint in hints:
        obs = _from_derived(hint, evidence, criterion, lex)
        if obs is not None:
            return obs
    obs = _declared_tolerance_observation(criterion, evidence, lex)
    if obs is not None:
        return obs
    return Observation(value=None)


def _scale(observed: float, unit: str | None) -> tuple[float, str | None]:
    """Percent criteria are satisfied either in percent or as a fraction."""
    if unit == "%" and observed != 0 and abs(observed) <= 1.0:
        return observed * 100.0, "fraction->percent"
    return observed, None


def judge(
    criterion: Criterion,
    evidence: NodeEvidence,
    lex: dict | None = None,
    provided_elements: Sequence[str] | None = None,
) -> CriterionOutcome:
    lex = lex or load_lexicon()
    if criterion.comparator in (Comparator.NONE, Comparator.APPROX):
        obs = Observation(value=None)
    else:
        obs = observe(criterion, evidence, lex)

    # --- non-numeric comparators -------------------------------------
    if criterion.comparator is Comparator.PRESENT:
        names = list(lex.get("element_names", {}).keys())
        present = set(provided_elements or evidence.derived.get("provided_elements") or [])
        missing = [n for n in names if n not in present]
        ok = not missing
        return CriterionOutcome(
            criterion=criterion,
            verdict=Verdict.PASS if ok else Verdict.FAIL,
            observation=Observation(value=ok, matched_keys=tuple(sorted(present)), source="report.six_elements",
                                    match_score=1.0 if present else 0.0),
            note=("all declared elements present" if ok else f"missing elements: {missing}"),
            confidence=0.9 if present else 0.0,
        )

    if criterion.comparator is Comparator.NONE:
        return CriterionOutcome(criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
                                note="criterion has no comparator/number; not machine-judgeable", confidence=0.0)

    if criterion.comparator is Comparator.APPROX:
        return CriterionOutcome(criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
                                note="approximate target without tolerance; refused to invent one", confidence=0.0)

    if obs.value is None:
        return CriterionOutcome(criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
                                note="no matching artifact metric", confidence=0.0)

    if criterion.comparator is Comparator.ALL_TRUE:
        ok = bool(obs.value)
        return CriterionOutcome(criterion=criterion, verdict=Verdict.PASS if ok else Verdict.FAIL,
                                observation=obs, note="all-true flag over artifact pass flags",
                                confidence=0.9 * obs.match_score)

    if criterion.comparator is Comparator.RATIO_GE:
        a, b = criterion.ratio or (0, 1)
        target = a / b if b else 1.0
        observed = float(obs.value)
        ok = observed + _EPS >= target
        note = f"observed ratio {observed:.4f} vs required {a}/{b}={target:.4f}"
        return CriterionOutcome(criterion=criterion, verdict=Verdict.PASS if ok else Verdict.FAIL,
                                observation=obs, note=note, confidence=0.85 * obs.match_score)

    # --- numeric comparators -----------------------------------------
    if criterion.threshold is None:
        return CriterionOutcome(criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
                                note="comparator without threshold", confidence=0.0)

    raw = float(obs.value)
    observed, scaling = _scale(raw, criterion.unit)
    if scaling:
        obs = replace(obs, value=observed, scaling=scaling)
    thr = float(criterion.threshold)
    op = criterion.comparator
    if op is Comparator.GE:
        ok = observed >= thr - _EPS
    elif op is Comparator.GT:
        ok = observed > thr + _EPS
    elif op is Comparator.LE:
        ok = observed <= thr + _EPS
    elif op is Comparator.LT:
        ok = observed < thr - _EPS
    elif op is Comparator.EQ:
        ok = _close(observed, thr)
    else:  # pragma: no cover - guarded above
        return CriterionOutcome(criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
                                note="unsupported comparator", confidence=0.0)

    note = f"observed {observed:g}{(' ' + criterion.unit) if criterion.unit else ''} {op.value} {thr:g}"
    if scaling:
        note += " (scaled from fraction)"
    if obs.scaling == "declared_tolerance":
        note += " (observed taken from the record's own error field because the criterion declares a matching tolerance)"
    if not ok and "deviation_documented" in criterion.notes:
        return CriterionOutcome(
            criterion=criterion, verdict=Verdict.MECHANICAL_CONFLICT, observation=obs,
            note=note + " | the record flags success AND explains the deviation: curation required",
            confidence=criterion.parse_confidence * obs.match_score)
    if not ok and ("association_inferred" in criterion.notes or "direction:undeclared" in criterion.notes):
        # two independent reasons why an out-of-band number is NOT a failure:
        #  - the tolerance/value association was inferred by the engine, and
        #  - the record never declares which side of the band is "good"
        # in both cases the honest output is "needs curation", never FAIL
        return CriterionOutcome(
            criterion=criterion, verdict=Verdict.UNKNOWN, observation=obs,
            note=note + " | out of band but the declaration is not explicit "
                        "(assumed association / undeclared direction): curation required, not FAIL",
            confidence=0.4 * obs.match_score)
    return CriterionOutcome(criterion=criterion, verdict=Verdict.PASS if ok else Verdict.FAIL,
                            observation=obs, note=note,
                            confidence=criterion.parse_confidence * obs.match_score)


# ---------------------------------------------------------------------------
# Node aggregation
# ---------------------------------------------------------------------------

def aggregate(
    evidence: NodeEvidence,
    criteria: Sequence[Criterion],
    lex: dict | None = None,
    provided_elements: Sequence[str] | None = None,
) -> NodeVerdict:
    lex = lex or load_lexicon()
    outcomes = [judge(c, evidence, lex, provided_elements) for c in criteria]
    verdict = node_verdict_from_outcomes(outcomes)
    matched = [o for o in outcomes if o.verdict is not Verdict.UNKNOWN]
    confidence = (sum(o.confidence for o in matched) / len(outcomes)) if outcomes else 0.0

    caveats: list[str] = []
    unknown = [o for o in outcomes if o.verdict is Verdict.UNKNOWN]
    if unknown:
        caveats.append(f"{len(unknown)} criterion/criteria not machine-judgeable: " +
                       "; ".join(sorted({o.note for o in unknown}))[:400])
    inferred = [o for o in outcomes if o.observation.scaling == "declared_tolerance"]
    if inferred:
        caveats.append(f"{len(inferred)} criterion/criteria judged via declared-tolerance inference (score 0.75)")
    scaled = [o for o in outcomes if o.observation.scaling == "fraction->percent"]
    if scaled:
        caveats.append(f"{len(scaled)} observation(s) rescaled from fraction to percent")
    conflicts = [o for o in outcomes if o.verdict is Verdict.MECHANICAL_CONFLICT]
    if conflicts:
        caveats.append(f"{len(conflicts)} declared number(s) violated with a documented deviation: "
                       + "; ".join(o.criterion.raw for o in conflicts[:5]))
    caveats.extend(evidence.warnings[:10])

    return NodeVerdict(
        node_id=evidence.node_id,
        verdict=verdict,
        outcomes=outcomes,
        confidence=confidence,
        evidence=evidence,
        elapsed_s=evidence.elapsed_s,
        caveats=caveats,
    )


def judge_node(
    root: str,
    criteria_items: Iterable | None = None,
    node_id: str | None = None,
    lex: dict | None = None,
    provided_elements: Sequence[str] | None = None,
) -> NodeVerdict:
    """Convenience: scan a node directory, parse criteria, aggregate."""
    from .criteria import parse_criteria
    from .discovery import discover_criteria
    from .scanner import scan_node_dir

    lex = lex or load_lexicon()
    evidence = scan_node_dir(root, node_id=node_id, lex=lex)
    crits: list[Criterion] = (discover_criteria(evidence, lex) if criteria_items is None
                              else list(parse_criteria(criteria_items, source="cli", lexicon=lex)))
    return aggregate(evidence, crits, lex, provided_elements=provided_elements)