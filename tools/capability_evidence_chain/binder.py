"""Evidence-chain binding: artefacts -> capability samples -> TOST decisions.

Three responsibilities, all configuration driven:

``collect_samples``
    Pull the numeric observations of each capability group (repo-scoped, value
    range filtered, deterministically truncated to ``max_samples``).
``associate``
    Compute the *association rate* of the evidence chain: of the artefacts a
    scanner recognized as carrying verification numbers, how many can be bound
    to a declared capability entry.  Unbound artefacts are listed with a
    reason instead of being dropped.
``evaluate``
    Run the equivalence machinery for every capability and build the MDE
    table (one row per capability entry).

Nothing here encodes a physical rule: margins, alpha, theta and the metric
patterns are all injected by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import equivalence as eq
from .config import CapabilitySpec, EvidenceChainConfig, GroupSpec
from .scanner import Observation, ScanResult

__all__ = [
    "GroupSample",
    "ledger_proposals",
    "collect_samples",
    "associate",
    "evaluate",
    "mde_table",
]


@dataclass
class GroupSample:
    capability: str
    group: str
    repo: str
    values: List[float] = field(default_factory=list)
    files: List[str] = field(default_factory=list)          # files behind ``values``
    accepted_files: set = field(default_factory=set)        # every file that passed the range filter
    n_available: int = 0
    truncated: bool = False
    out_of_range: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "group": self.group,
            "repo": self.repo,
            "n": len(self.values),
            "n_available": self.n_available,
            "truncated": self.truncated,
            "out_of_range": self.out_of_range,
            "files": len(set(self.files)),
        }


def _in_range(value: float, group: GroupSpec) -> bool:
    bounds = group.value_bounds
    if bounds is None:
        return True
    lo, hi = bounds
    return lo <= value <= hi


def collect_samples(
    config: EvidenceChainConfig, scan_result: ScanResult
) -> Dict[Tuple[str, str], GroupSample]:
    """Collect per-(capability, group) samples with deterministic truncation."""
    samples: Dict[Tuple[str, str], GroupSample] = {}
    for cap in config.capabilities:
        for gkey, group in cap.groups.items():
            label = f"{cap.id}:{gkey}"
            obs: List[Observation] = [
                o for o in scan_result.observations
                if o.repo == group.repo and label in o.labels
            ]
            obs.sort(key=lambda o: (o.path, o.key_path))
            s = GroupSample(capability=cap.id, group=gkey, repo=group.repo)
            for o in obs:
                if not _in_range(o.value, group):
                    s.out_of_range += 1
                    continue
                s.n_available += 1
                s.accepted_files.add(o.path)
                if len(s.values) < group.max_samples:
                    s.values.append(o.value)
                    s.files.append(o.path)
            s.truncated = s.n_available > len(s.values)
            samples[(cap.id, gkey)] = s
    return samples


def associate(
    config: EvidenceChainConfig,
    scan_result: ScanResult,
    samples: Optional[Dict[Tuple[str, str], GroupSample]] = None,
) -> Dict[str, Any]:
    """Association statistics of the evidence chain (machine criterion (a)).

    ``recognized``: artefacts whose JSON carries at least one numeric leaf
    matching a declared metric pattern.

    ``bound``: recognized artefacts with at least one observation that an
    in-scope capability group accepted (repo match + value range).  The
    association rate is ``bound / recognized``; ``strict_rate`` additionally
    divides by all scanned artefacts so unrecognized files stay visible.
    """
    if samples is None:
        samples = collect_samples(config, scan_result)

    # repo-scoped recognition: an artefact counts as evidence only if at least
    # one of its matched leaves belongs to a capability group declared for the
    # *same* repository (otherwise a FEM key would "recognize" a BEM artefact).
    label_repo = {
        f"{cap.id}:{g}": grp.repo
        for cap in config.capabilities
        for g, grp in cap.groups.items()
    }
    scoped = {
        (o.repo, o.path)
        for o in scan_result.observations
        if any(label_repo.get(lab) == o.repo for lab in o.labels)
    }
    recognized = [a for a in scan_result.artifacts if (a.repo, a.path) in scoped]
    bound_paths = set()
    for s_ in samples.values():
        for p_ in s_.accepted_files:
            bound_paths.add((s_.repo, p_))
    bound = [a for a in recognized if (a.repo, a.path) in bound_paths]
    # reason for the unbound ones, so the gap is auditable
    scoped_obs = {(o.repo, o.path) for o in scan_result.observations if (o.repo, o.path) in scoped}
    unbound = [
        {
            "repo": a.repo,
            "path": a.path,
            "reason": (
                "all in-scope observations fell outside the declared value range"
                if (a.repo, a.path) in scoped_obs
                else "no capability group accepted any observation"
            ),
            "n_matched_leaves": a.n_matched,
        }
        for a in recognized
        if (a.repo, a.path) not in bound_paths
    ]
    n_rec = len(recognized)
    return {
        "repos_scanned": len({a.repo for a in scan_result.artifacts}),
        "files_scanned": len(scan_result.artifacts),
        "recognized_files": n_rec,
        "bound_files": len(bound),
        "association_rate": (len(bound) / n_rec) if n_rec else 0.0,
        "strict_rate": (len(bound) / len(scan_result.artifacts))
        if scan_result.artifacts
        else 0.0,
        "bound_by_repo": _count_by_repo(bound),
        "recognized_by_repo": _count_by_repo(recognized),
        "unbound": unbound[:50],
        "n_unbound": len(unbound),
        "caps_without_data": [
            cap.id for cap in config.capabilities
            if not any(samples[(cap.id, g)].values for g in cap.groups)
        ],
    }


def _count_by_repo(records: Sequence[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in records:
        out[r.repo] = out.get(r.repo, 0) + 1
    return out


def evaluate(
    config: EvidenceChainConfig,
    samples: Dict[Tuple[str, str], GroupSample],
    ks_boot: int = 2000,
    seed: int = 20261111,
    sensitivity: bool = True,
) -> List[Dict[str, Any]]:
    """Run the equivalence decision for every capability entry."""
    results: List[Dict[str, Any]] = []
    for cap in config.capabilities:
        row: Dict[str, Any] = {
            "capability": cap.id,
            "scenario": cap.scenario,
            "unit": cap.unit,
            "note": cap.note,
            "margin": cap.margin,
            "alpha": cap.alpha,
            "power_target": cap.power_target,
            "groups": {g: samples[(cap.id, g)].to_dict() for g in cap.groups},
        }
        a = samples[(cap.id, "a")]
        if cap.scenario == "mean":
            if len(a.values) < 2:
                row.update({
                    "decision": "WITHHELD_NO_DATA" if not a.values else "WITHHELD_SMALL_SAMPLE",
                    "equivalent": False,
                    "n": len(a.values),
                    "power": None,
                    "mde": None,
                })
                results.append(row)
                continue
            res = eq.tost_mean_one_sample(
                a.values, cap.margin, cap.alpha, cap.power_target
            )
            row.update(res.to_dict())
            row["test"] = row.pop("scenario")
            row["scenario"] = cap.scenario
            row["n_required_80"] = eq.n_required_mean(
                row.get("notes", {}).get("sd", 0.0) or 0.0,
                cap.margin,
                cap.alpha,
                cap.power_target,
            )
            row["mde_vs_margin"] = (
                "mde_covers_margin" if (res.mde or 0.0) >= 0.5 * cap.margin
                else "mde_below_half_margin"
            )
        elif cap.scenario in ("variance", "distribution"):
            b = samples[(cap.id, "b")]
            if len(a.values) < 3 or len(b.values) < 3:
                row.update({
                    "decision": "WITHHELD_NO_DATA"
                    if not a.values or not b.values
                    else "WITHHELD_SMALL_SAMPLE",
                    "equivalent": False,
                    "n": len(a.values) + len(b.values),
                    "power": None,
                    "mde": None,
                })
                results.append(row)
                continue
            if cap.scenario == "variance":
                res = eq.tost_variance_ratio(
                    a.values, b.values, cap.theta, cap.alpha, sensitivity,
                    cap.power_target,
                )
            else:
                res = eq.tost_distribution_ks(
                    a.values, b.values, cap.margin, cap.alpha,
                    n_boot=ks_boot, seed=seed, compute_sensitivity=sensitivity,
                    power_target=cap.power_target,
                )
            row.update(res.to_dict())
            row["test"] = row.pop("scenario")
            row["scenario"] = cap.scenario
        else:  # pragma: no cover - config validation prevents this
            raise ValueError(f"unknown scenario {cap.scenario}")
        row["decision"] = row.get("decision") or (
            "EQUIVALENT" if row.get("equivalent") else "NOT_EQUIVALENT"
        )
        results.append(row)
    return results


def ledger_proposals(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn each equivalence decision into a mechanical ledger action.

    This is the last link of the chain: evidence -> certificate -> ledger
    action, with no human in the loop.  A capability is only promoted when
    the certificate is both *significant* (interval inside the margin) and
    *sensitive* (MDE covers the margin at the target power); a significant
    but insensitive certificate stays withheld, and a failed equivalence
    downgrades the claim.
    """
    out: List[Dict[str, Any]] = []
    for r in results:
        decision = r.get("decision")
        power = r.get("power")
        mde = r.get("mde")
        margin = r.get("margin") or 0.0
        sensitive = (
            power is not None and power >= 0.8
            and mde is not None and margin > 0 and mde >= 0.8 * margin
        )
        if decision == "EQUIVALENT" and sensitive:
            action, level = "UPGRADE_TO_REAL", "REAL"
        elif decision == "EQUIVALENT":
            action, level = "RETAIN_WITHHELD_LOW_POWER", "WITHHELD"
        elif decision == "NOT_EQUIVALENT":
            action, level = "DOWNGRADE_OR_FIX", "MOCK"
        else:
            action, level = "RETAIN_WITHHELD_NO_DATA", "WITHHELD"
        out.append({
            "capability": r["capability"],
            "scenario": r["scenario"],
            "action": action,
            "proposed_level": level,
            "decision": decision,
            "power": power,
            "mde": mde,
            "margin": margin,
            "sensitive": bool(sensitive),
            "evidence_files": sum(g.get("files", 0) for g in r.get("groups", {}).values()),
        })
    return out


def mde_table(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact MDE / power table (machine criterion (c))."""
    rows: List[Dict[str, Any]] = []
    for r in results:
        rows.append(
            {
                "capability": r["capability"],
                "scenario": r["scenario"],
                "unit": r.get("unit", ""),
                "n": r.get("n"),
                "estimate": r.get("estimate"),
                "margin": r.get("margin"),
                "ci_low": r.get("ci_low"),
                "ci_high": r.get("ci_high"),
                "power": r.get("power"),
                "mde": r.get("mde"),
                "mde_ratio": (
                    (r.get("mde") / r["margin"])
                    if r.get("mde") is not None and r.get("margin") else None
                ),
                "decision": r.get("decision"),
                "mde_coverage": (
                    (r.get("mde") / r["margin"]) if r.get("mde") is not None and r.get("margin") else None
                ),
                "underpowered": (
                    (r.get("power") is not None and r["power"] < 0.8)
                    or (r.get("mde") is not None
                        and r["margin"] > 0
                        and r["mde"] < 0.8 * r["margin"])
                ),
            }
        )
    return rows