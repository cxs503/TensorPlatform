"""Retrospective backtest of the readiness gate.

The backtester rebuilds, for every historical campaign node, *the artefact set
that was visible at a given moment* and asks the gate what it would have said.

Two evaluation moments are reconstructed because they answer different questions:

``T_dispatch``  the node's mission was created (the gate's real use: it runs
                right before a task is handed to a worker)
``T_stop``      work on the node stopped: mission ``finished_at`` when the node
                was executed, else the node's last ``actual_end``/``created_at``
                (for nodes that were never handed out at all, e.g. release gates
                that wait for a human)

Derived per node, at each moment:

* upstream ``dispatched`` = upstream mission created at or before the moment
* upstream ``delivered``  = upstream mission finished at or before the moment
* upstream ``succeeded``  = ... and that mission ended with status ``succeeded``
* artefact record         = the upstream mission's result field names (what the
                            delivery actually carried) plus its finish timestamp

Labels come from the same snapshot through configuration supplied cause rules
(``config/dispatch_readiness_labels.json``), so the label logic carries no
domain vocabulary either:

``delivery_gap``   (primary, objective) blocked while at least one declared
                   upstream artefact was still absent when work stopped
``upstream_cause`` (audit)              blocked with a block reason that names
                   the upstream / release-gate class explicitly
``infra_cause``    (out of scope)       blocked for proven infrastructure causes
                   (worker lost, model error, hollow success)

Everything is a pure function of the snapshot, so the backtest is reproducible
bit for bit given the same snapshot and seed.
"""

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import build_contract_table, contract_from_metadata
from .models import (
    CONFLICT,
    MODE_DECLARED,
    MODE_DELIVERED,
    MODE_TERMINAL_SUCCESS,
    NOT_READY,
    READY,
    ArtifactRecord,
    InputContract,
    ReadinessResult,
    UpstreamState,
)
from .resolver import _MODE_RANK, evaluate_readiness

LABEL_CONFIG_ENV = "TP_DISPATCH_READINESS_LABELS"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_LABEL_CONFIG = os.path.join(_REPO_ROOT, "config", "dispatch_readiness_labels.json")

MODE_LABELS = {
    MODE_DECLARED: "declared",
    MODE_DELIVERED: "delivered",
    MODE_TERMINAL_SUCCESS: "terminal_success",
}


# --------------------------------------------------------------------------- #
# label configuration (injected, never hard coded)
# --------------------------------------------------------------------------- #
def load_label_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load label rules; missing file -> empty rules (no labels, identity)."""
    candidates = [path, os.getenv(LABEL_CONFIG_ENV), DEFAULT_LABEL_CONFIG]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _matches_any(text: str, needles: Sequence[str]) -> bool:
    return any(needle and needle in text for needle in needles)


# --------------------------------------------------------------------------- #
# snapshot -> dispatch-time view
# --------------------------------------------------------------------------- #
def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _upstream_state(
    up_node: Optional[Mapping[str, Any]],
    up_mission: Optional[Mapping[str, Any]],
    moment: Optional[datetime],
) -> UpstreamState:
    if up_node is None:
        return UpstreamState(node="?", dispatched=False, delivered=False, succeeded=False, detail="unknown node")

    key = str(up_node.get("key", up_node.get("id")))
    if up_mission is None:
        return UpstreamState(
            node=key,
            dispatched=False,
            delivered=False,
            succeeded=False,
            detail="no mission was ever created for this upstream node",
        )
    created = parse_ts(up_mission.get("created_at"))
    finished = parse_ts(up_mission.get("finished_at"))
    dispatched = moment is not None and created is not None and created <= moment
    delivered = dispatched and finished is not None and finished <= moment
    succeeded = delivered and str(up_mission.get("status", "")) == "succeeded"
    record = None
    if delivered:
        record = ArtifactRecord(
            node=key,
            artifact="delivery",
            fields=tuple(up_mission.get("result_keys") or ()),
            units=None,
            version=None,
            produced_at=finished.isoformat() if finished else None,
            terminal_success=succeeded,
        )
    detail = "" if dispatched else "upstream mission created after the evaluated moment"
    return UpstreamState(
        node=key,
        dispatched=dispatched,
        delivered=delivered,
        succeeded=succeeded,
        record=record,
        detail=detail,
    )


def _satisfied(view_contract: InputContract, states: Mapping[str, UpstreamState]) -> Tuple[int, int]:
    ok = 0
    total = len(view_contract.requirements)
    for req in view_contract.requirements:
        state = states.get(req.upstream)
        if state is None:
            continue
        need = _MODE_RANK.get(req.mode, 2)
        if need >= _MODE_RANK[MODE_TERMINAL_SUCCESS]:
            ok += 1 if state.succeeded else 0
        elif need >= _MODE_RANK[MODE_DELIVERED]:
            ok += 1 if state.delivered else 0
        else:
            ok += 1 if (state.dispatched or state.delivered) else 0
    return ok, total


@dataclass
class NodeView:
    key: str
    ext_id: str
    plan_id: Any
    node_type: str
    status: str
    block_reason: str
    consecutive_fails: int
    dependencies: List[str]
    t_dispatch: Optional[datetime]
    t_stop: Optional[datetime]
    contract: InputContract
    states_dispatch: Dict[str, UpstreamState] = field(default_factory=dict)
    states_stop: Dict[str, UpstreamState] = field(default_factory=dict)
    mission_status: str = ""
    mission_finished_at: Optional[datetime] = None
    dispatch_observed: bool = False
    gap_at_dispatch: bool = False
    gap_at_stop: bool = False
    ratio_at_dispatch: float = 1.0
    ratio_at_stop: float = 1.0

    @property
    def terminal(self) -> bool:
        return self.status in {"done", "blocked"}

    def moment_states(self, moment: str) -> Dict[str, UpstreamState]:
        if moment == "dispatch":
            return self.states_dispatch
        if moment == "consult":
            return self.states_dispatch if self.dispatch_observed else self.states_stop
        return self.states_stop

    def moment_time(self, moment: str) -> Optional[datetime]:
        if moment == "dispatch":
            return self.t_dispatch
        if moment == "consult":
            return self.t_dispatch if self.dispatch_observed else self.t_stop
        return self.t_stop


def _mission_index(snapshot: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(m.get("id")): m for m in snapshot.get("missions") or []}


def build_views(
    snapshot: Mapping[str, Any],
    declared_contracts: Optional[Mapping[str, InputContract]] = None,
    mode_override: Optional[str] = None,
    stop_anchor: str = "birth",
) -> List[NodeView]:
    """Reconstruct one :class:`NodeView` per historical node.

    ``mode_override`` replaces the requirement mode chosen by inference (used by
    the policy-sensitivity study: declared / delivered / terminal_success).

    ``stop_anchor`` picks what counts as "the moment work on the node stopped" and
    therefore what "the upstream gap at the end" means:

    ``birth``  the node row was created (plan seeding). For nodes the platform
               never handed out (release gates that start in a waiting state) this
               is the only well defined moment.
    ``update`` the node row was last modified (the status flip). Strictest.
    ``auto``   mission ``finished_at`` when the node ran, else its creation time.
    """
    nodes = {str(n.get("key", n.get("id"))): n for n in snapshot.get("nodes") or []}
    missions = _mission_index(snapshot)
    declared = dict(declared_contracts or {})
    for key, node in nodes.items():
        explicit = contract_from_metadata(node)
        if explicit is not None:
            declared.setdefault(key, explicit)
    table = build_contract_table(nodes.values(), declared)

    views: List[NodeView] = []
    for key, node in sorted(nodes.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        mission = missions.get(str(node.get("mission_id"))) if node.get("mission_id") else None
        t_dispatch = parse_ts(mission.get("created_at")) if mission else None
        mission_finished = parse_ts(mission.get("finished_at")) if mission else None
        if stop_anchor == "update":
            t_stop = parse_ts(node.get("updated_at")) or parse_ts(node.get("created_at"))
        elif stop_anchor == "auto":
            t_stop = mission_finished or parse_ts(node.get("actual_end")) or parse_ts(node.get("created_at"))
        else:  # "birth"
            t_stop = parse_ts(node.get("created_at"))
        deps = [str(d) for d in (node.get("dependencies") or [])]

        contract = table[key]
        if mode_override:
            contract = InputContract(
                node=contract.node,
                requirements=tuple(
                    type(req)(
                        upstream=req.upstream,
                        artifact=req.artifact,
                        mode=mode_override,
                        fields=req.fields,
                        units=req.units,
                        version_min=req.version_min,
                        required=req.required,
                    )
                    for req in contract.requirements
                ),
                version=contract.version,
                source=f"{contract.source}+override:{mode_override}",
            )

        def states_at(moment: Optional[datetime]) -> Dict[str, UpstreamState]:
            out: Dict[str, UpstreamState] = {}
            for dep in deps:
                dep_node = nodes.get(dep)
                dep_mission = missions.get(str((dep_node or {}).get("mission_id"))) if dep_node else None
                out[dep] = _upstream_state(dep_node, dep_mission, moment)
            return out

        s_dispatch = states_at(t_dispatch)
        s_stop = states_at(t_stop)
        ok_d, total_d = _satisfied(contract, s_dispatch) if deps else (0, 0)
        ok_s, total_s = _satisfied(contract, s_stop) if deps else (0, 0)

        views.append(
            NodeView(
                key=key,
                ext_id=str(node.get("ext_id", key)),
                plan_id=node.get("plan_id"),
                node_type=str(node.get("node_type", "task")),
                status=str(node.get("status", "")),
                block_reason=str(node.get("block_reason") or ""),
                consecutive_fails=int(node.get("consecutive_fails") or 0),
                dependencies=deps,
                t_dispatch=t_dispatch,
                t_stop=t_stop,
                contract=contract,
                states_dispatch=s_dispatch,
                states_stop=s_stop,
                mission_status=str(mission.get("status", "")) if mission else "",
                mission_finished_at=mission_finished,
                dispatch_observed=t_dispatch is not None,
                gap_at_dispatch=bool(deps) and total_d > 0 and ok_d < total_d,
                gap_at_stop=bool(deps) and total_s > 0 and ok_s < total_s,
                ratio_at_dispatch=(ok_d / total_d) if total_d else 1.0,
                ratio_at_stop=(ok_s / total_s) if total_s else 1.0,
            )
        )
    return views


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #
def label_views(views: Sequence[NodeView], rules: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Attach labels. ``label=None`` means the row is out of scope (reported apart)."""
    upstream_needles = list(rules.get("upstream_cause_needles") or [])
    infra_needles = list(rules.get("infra_cause_needles") or [])
    include_infra = bool(rules.get("include_infra_in_primary", False))
    configured = bool(rules)

    rows: List[Dict[str, Any]] = []
    for view in views:
        if not view.terminal:
            continue
        text = view.block_reason
        reason_upstream = _matches_any(text, upstream_needles)
        reason_infra = _matches_any(text, infra_needles)
        gap = bool(view.gap_at_stop)

        stratum = ""
        label: Optional[int] = None
        if not configured:
            stratum = "unlabeled_no_configuration"
        elif view.status == "done":
            label, stratum = 0, "negative_done"
        elif reason_infra and not include_infra:
            label, stratum = None, "excluded_infra"
        elif gap:
            label, stratum = 1, "positive_delivery_gap"
        elif reason_upstream:
            label, stratum = None, "excluded_release_gate"   # upstream delivered, human release pending
        else:
            label, stratum = 0, "blocked_no_gap"

        rows.append(
            {
                "node": view.key,
                "ext_id": view.ext_id,
                "plan_id": view.plan_id,
                "node_type": view.node_type,
                "status": view.status,
                "block_reason": text,
                "t_dispatch": view.t_dispatch.isoformat() if view.t_dispatch else None,
                "t_stop": view.t_stop.isoformat() if view.t_stop else None,
                "dispatch_observed": view.dispatch_observed,
                "dependencies": list(view.dependencies),
                "gap_at_dispatch": bool(view.gap_at_dispatch),
                "gap_at_stop": gap,
                "ratio_at_dispatch": round(view.ratio_at_dispatch, 6),
                "ratio_at_stop": round(view.ratio_at_stop, 6),
                "reason_upstream": reason_upstream,
                "reason_infra": reason_infra,
                "label": label,
                "stratum": stratum,
            }
        )
    return rows


def predict_views(views: Sequence[NodeView], moment: str = "dispatch") -> Dict[str, ReadinessResult]:
    """Run the gate at the requested moment for every node."""
    out: Dict[str, ReadinessResult] = {}
    for view in views:
        if not view.dependencies:
            out[view.key] = ReadinessResult(
                node=view.key,
                status=READY,
                attributions=[],
                contract_source=view.contract.source,
                details={"requirements": 0, "checked": 0, "no_upstream": True},
            )
            continue
        moment_time = view.moment_time(moment)
        if moment == "dispatch" and moment_time is None:
            out[view.key] = ReadinessResult(
                node=view.key,
                status=READY,
                attributions=[],
                contract_source=view.contract.source,
                details={"requirements": len(view.contract.requirements), "checked": 0, "never_dispatched": True},
            )
            continue
        out[view.key] = evaluate_readiness(view.contract, view.moment_states(moment))
    return out


def risk_score(result: ReadinessResult) -> float:
    """Continuous *risk* score in [0, 1] used for the ROC curve.

    risk = 1 - (share of declared requirements satisfied), with an extra penalty
    per caliber conflict. Higher = the node looks less ready, which is the
    direction of the positive (blocked) class.
    """
    details = result.details or {}
    requirements = int(details.get("requirements") or 0)
    if requirements == 0:
        return 0.0
    missing = len([a for a in result.attributions if a.outcome == "missing"])
    conflicts = len([a for a in result.attributions if a.outcome == "caliber"])
    satisfied = max(0, requirements - missing - conflicts)
    risk = 1.0 - satisfied / float(requirements)
    risk += 0.5 * conflicts / float(requirements)
    return max(0.0, min(1.0, risk))


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    """Rank based AUC with tie handling (Mann-Whitney U / (n1*n0))."""
    pairs = list(zip(scores, labels))
    n1 = sum(labels)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    pairs.sort(key=lambda p: p[0])
    rank_sum = 0.0
    index = 0
    while index < len(pairs):
        end = index
        while end + 1 < len(pairs) and pairs[end + 1][0] == pairs[index][0]:
            end += 1
        avg_rank = (index + end) / 2.0 + 1.0
        for pos in range(index, end + 1):
            if pairs[pos][1] == 1:
                rank_sum += avg_rank
        index = end + 1
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def roc_points(labels: Sequence[int], scores: Sequence[float]) -> List[Dict[str, float]]:
    positives = sum(labels) or 1
    negatives = (len(labels) - sum(labels)) or 1
    points = [{"threshold": 1.0000001, "tpr": 0.0, "fpr": 0.0}]
    for thr in sorted(set(scores), reverse=True):
        tp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 1)
        fp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 0)
        points.append({"threshold": round(thr, 6), "tpr": tp / positives, "fpr": fp / negatives})
    points.append({"threshold": -0.0000001, "tpr": 1.0, "fpr": 1.0})
    return points


def confusion(labels: Sequence[int], predictions: Sequence[int]) -> Dict[str, int]:
    tp = sum(1 for y, p in zip(labels, predictions) if y == 1 and p == 1)
    fn = sum(1 for y, p in zip(labels, predictions) if y == 1 and p == 0)
    fp = sum(1 for y, p in zip(labels, predictions) if y == 0 and p == 1)
    tn = sum(1 for y, p in zip(labels, predictions) if y == 0 and p == 0)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn}


def _wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def permutation_null(
    labels: Sequence[int],
    scores: Sequence[float],
    iterations: int = 200,
    seed: int = 20261110,
) -> Dict[str, Any]:
    """Shuffle labels, recompute AUC -> 95% interval (AC3 negative control)."""
    observed = roc_auc(labels, scores)
    rng = random.Random(seed)
    shuffled = list(labels)
    draws: List[float] = []
    for _ in range(iterations):
        rng.shuffle(shuffled)
        value = roc_auc(shuffled, scores)
        if not math.isnan(value):
            draws.append(value)
    draws.sort()
    if not draws:
        return {"observed": observed, "iterations": 0, "interval_95": [float("nan")] * 2,
                "covers_0_5": False, "within_band_0_4_0_6": False, "draws": []}
    low = draws[max(0, int(0.025 * len(draws)) - 1)]
    high = draws[min(len(draws) - 1, int(math.ceil(0.975 * len(draws))) - 1)]
    return {
        "observed": observed,
        "iterations": iterations,
        "seed": seed,
        "null_mean": sum(draws) / len(draws),
        "interval_95": [low, high],
        "covers_0_5": bool(low <= 0.5 <= high),
        "within_band_0_4_0_6": bool(low >= 0.4 and high <= 0.6),
        "draws": [round(v, 6) for v in draws],
    }


# --------------------------------------------------------------------------- #
# AC5 replay: intercept -> fix upstream -> dispatch again
# --------------------------------------------------------------------------- #
def replay_interception(views: Sequence[NodeView], predictions: Mapping[str, ReadinessResult]) -> Dict[str, Any]:
    """Replay what happened after the gate fires.

    ``released_after_upstream_fix``: deferred node that later reached ``done``
    although the upstream gap was open when it was dispatched — i.e. the platform
    did close the gap and the node then delivered.
    """
    intercepted = 0
    released_after_fix = 0
    released_first_pass = 0
    stuck = 0
    examples: List[Dict[str, Any]] = []
    for view in views:
        result = predictions.get(view.key)
        if result is None or not view.dependencies or result.status == READY:
            continue
        intercepted += 1
        if view.status == "done":
            if view.gap_at_dispatch and not view.gap_at_stop:
                released_after_fix += 1
                if view.consecutive_fails == 0:
                    released_first_pass += 1
        elif view.status == "blocked":
            stuck += 1
        if len(examples) < 10 and result.blocking_reasons:
            examples.append(
                {
                    "node": view.key,
                    "ext_id": view.ext_id,
                    "plan_id": view.plan_id,
                    "final_status": view.status,
                    "verdict": result.status,
                    "reasons": result.blocking_reasons[:3],
                }
            )
    return {
        "intercepted": intercepted,
        "released_after_upstream_fix": released_after_fix,
        "released_first_pass": released_first_pass,
        "first_pass_rate_after_fix": (released_first_pass / released_after_fix) if released_after_fix else None,
        "still_blocked_after_interception": stuck,
        "attribution_examples": examples,
    }


# --------------------------------------------------------------------------- #
# target evaluation
# --------------------------------------------------------------------------- #
def _strata(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        key = f"{row['stratum']}|label={row['label']}"
        out[key] = out.get(key, 0) + 1
    return out


def evaluate_target(
    rows: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, ReadinessResult],
    target: str,
    only_dispatched: bool = False,
) -> Dict[str, Any]:
    used = [
        r
        for r in rows
        if r["label"] is not None and (r["dispatch_observed"] or not only_dispatched)
    ]
    labels = [int(r["label"]) for r in used]
    preds = [0 if predictions[r["node"]].status == READY else 1 for r in used]
    scores = [risk_score(predictions[r["node"]]) for r in used]
    cm = confusion(labels, preds)
    n_pos = cm["tp"] + cm["fn"]
    n_neg = cm["fp"] + cm["tn"]
    recall = cm["tp"] / n_pos if n_pos else float("nan")
    fpr = cm["fp"] / n_neg if n_neg else float("nan")
    precision = cm["tp"] / (cm["tp"] + cm["fp"]) if (cm["tp"] + cm["fp"]) else float("nan")
    return {
        "target": target,
        "only_dispatched": only_dispatched,
        "n_labeled": len(used),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "confusion_matrix": cm,
        "recall": recall,
        "recall_ci95": list(_wilson(cm["tp"], n_pos)),
        "fpr": fpr,
        "fpr_ci95": list(_wilson(cm["fp"], n_neg)),
        "precision": precision,
        "accuracy": (cm["tp"] + cm["tn"]) / len(used) if used else float("nan"),
        "auc": roc_auc(labels, scores),
        "roc": roc_points(labels, scores),
        "positive_nodes": [r["node"] for r in used if r["label"] == 1],
        "false_positive_nodes": [r["node"] for r in used if r["label"] == 0 and predictions[r["node"]].status != READY][:50],
        "strata": _strata(rows),
    }


def mode_sensitivity(snapshot: Mapping[str, Any], rules: Mapping[str, Any]) -> Dict[str, Any]:
    """Policy study: FPR on healthy nodes vs coverage of real blocks per mode."""
    out: Dict[str, Any] = {}
    for mode in (MODE_DECLARED, MODE_DELIVERED, MODE_TERMINAL_SUCCESS):
        views = build_views(snapshot, mode_override=mode)
        rows = {r["node"]: r for r in label_views(views, rules)}
        preds = predict_views(views, moment="consult")
        fpr_num = fpr_den = 0
        cov_num = cov_den = 0
        for view in views:
            row = rows.get(view.key)
            if row is None:
                continue
            fired = preds[view.key].status != READY
            if view.status == "done":
                fpr_den += 1
                fpr_num += 1 if fired else 0
            elif row["label"] is not None and row["label"] == 1:
                cov_den += 1
                cov_num += 1 if fired else 0
        out[MODE_LABELS.get(mode, mode)] = {
            "mode": mode,
            "done_nodes": fpr_den,
            "done_deferred": fpr_num,
            "fpr": (fpr_num / fpr_den) if fpr_den else float("nan"),
            "positive_nodes": cov_den,
            "positives_deferred": cov_num,
            "coverage": (cov_num / cov_den) if cov_den else float("nan"),
        }
    return out


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def run_backtest(
    snapshot: Mapping[str, Any],
    label_rules: Optional[Mapping[str, Any]] = None,
    permutation_iterations: int = 200,
    seed: int = 20261110,
    frozen_thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Run the full backtest and return a JSON-serialisable report body."""
    rules = dict(label_rules if label_rules is not None else load_label_config())
    views = build_views(snapshot, stop_anchor="birth")
    rows = label_views(views, rules)
    preds_stop = predict_views(views, moment="stop")
    preds_dispatch = predict_views(views, moment="dispatch")
    preds_consult = predict_views(views, moment="consult")

    targets = {
        "delivery_gap_consult": evaluate_target(rows, preds_consult, "delivery_gap@consult"),
        "delivery_gap_dispatch": evaluate_target(rows, preds_dispatch, "delivery_gap@dispatch", only_dispatched=True),
        "delivery_gap_stop": evaluate_target(rows, preds_stop, "delivery_gap@stop"),
    }

    audit_rules = dict(rules)
    audit_rules["primary_target"] = "upstream_cause"
    audit_rows = label_views(views, audit_rules)
    targets["upstream_cause_stop"] = evaluate_target(audit_rows, preds_stop, "upstream_cause@stop")

    primary = targets["delivery_gap_consult"]
    used = [r for r in rows if r["label"] is not None]
    labels = [int(r["label"]) for r in used]
    scores = [risk_score(preds_consult[r["node"]]) for r in used]
    null_forward = permutation_null(labels, scores, iterations=permutation_iterations, seed=seed)

    used_stop = [r for r in rows if r["label"] is not None]
    null_stop = permutation_null(
        [int(r["label"]) for r in used_stop],
        [risk_score(preds_stop[r["node"]]) for r in used_stop],
        iterations=permutation_iterations,
        seed=seed,
    )

    label_sensitivity: Dict[str, Any] = {}
    for anchor in ("birth", "update", "auto"):
        anchor_views = build_views(snapshot, stop_anchor=anchor)
        anchor_rows = label_views(anchor_views, rules)
        anchor_preds = predict_views(anchor_views, moment="consult")
        anchor_target = evaluate_target(anchor_rows, anchor_preds, f"delivery_gap@{anchor}")
        label_sensitivity[anchor] = {
            "labeled": anchor_target["n_labeled"],
            "positives": anchor_target["n_positive"],
            "negatives": anchor_target["n_negative"],
            "recall": anchor_target["recall"],
            "fpr": anchor_target["fpr"],
            "auc": anchor_target["auc"],
            "strata": anchor_target["strata"],
        }

    out_of_scope = [r for r in rows if r["label"] is None]
    thresholds = dict(frozen_thresholds or {"recall_min": 0.60, "fpr_max": 0.05})

    body = {
        "schema": "dispatch-readiness-backtest/1.0",
        "snapshot_provenance": snapshot.get("provenance", {}),
        "snapshot_counts": snapshot.get("counts", {}),
        "label_rules": rules,
        "label_definitions": {
            "delivery_gap": "blocked while >=1 declared upstream artefact was still absent when work stopped",
            "upstream_cause": "blocked with a block reason naming the upstream/release-gate class",
            "infra_cause": "blocked for proven infrastructure causes (excluded from primary metrics)",
        },
        "frozen_thresholds": thresholds,
        "gate_decisions_primary": {
            "READY": len([p for p in preds_consult.values() if p.status == READY]),
            "NOT_READY": len([p for p in preds_consult.values() if p.status == NOT_READY]),
            "CONFLICT": len([p for p in preds_consult.values() if p.status == CONFLICT]),
        },
        "gate_decisions_dispatch": {
            "READY": len([p for p in preds_dispatch.values() if p.status == READY]),
            "NOT_READY": len([p for p in preds_dispatch.values() if p.status == NOT_READY]),
            "CONFLICT": len([p for p in preds_dispatch.values() if p.status == CONFLICT]),
        },
        "targets": targets,
        "permutation_null_primary": {k: v for k, v in null_forward.items() if k != "draws"},
        "permutation_null_stop": {k: v for k, v in null_stop.items() if k != "draws"},
        "permutation_null_primary_draws": null_forward.get("draws", []),
        "out_of_scope": {"count": len(out_of_scope), "by_stratum": _strata(out_of_scope)},
        "mode_sensitivity": mode_sensitivity(snapshot, rules),
        "label_sensitivity": label_sensitivity,
        "interception_replay": replay_interception(views, preds_consult),
        "ac": {
            "AC1_labeled_nodes": {
                "labeled": primary["n_labeled"],
                "positives": primary["n_positive"],
                "labeled_ok": primary["n_labeled"] >= 40,
                "positives_ok": primary["n_positive"] >= 10,
                "passed": bool(primary["n_labeled"] >= 40 and primary["n_positive"] >= 10),
            },
            "AC2_power": {
                "recall": primary["recall"],
                "fpr": primary["fpr"],
                "recall_ok": bool(primary["recall"] >= thresholds["recall_min"]),
                "fpr_ok": bool(primary["fpr"] <= thresholds["fpr_max"]),
                "passed": bool(
                    primary["recall"] >= thresholds["recall_min"] and primary["fpr"] <= thresholds["fpr_max"]
                ),
            },
            "AC3_permutation": {
                "interval_95": null_forward.get("interval_95"),
                "covers_0_5": null_forward.get("covers_0_5"),
                "within_band": null_forward.get("within_band_0_4_0_6"),
                "passed": bool(null_forward.get("covers_0_5")),
            },
        },
        "reproducibility": {
            "seed": seed,
            "permutation_iterations": permutation_iterations,
            "views": len(views),
        },
    }
    body["report_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return body


def write_report(body: Mapping[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False, indent=2, default=str)
    return path


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import sys

    snap_path = sys.argv[1] if len(sys.argv) > 1 else "artifacts/dispatch_readiness/history_snapshot.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "artifacts/dispatch_readiness/backtest_report.json"
    with open(snap_path, "r", encoding="utf-8") as handle:
        snapshot = json.load(handle)
    body = run_backtest(snapshot)
    write_report(body, out_path)
    print(json.dumps(body["ac"], ensure_ascii=False, indent=2, default=str))