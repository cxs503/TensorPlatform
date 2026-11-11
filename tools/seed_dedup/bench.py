"""Benchmark generation and P/R evaluation for seed dedup.

Generates 100 labelled pairs (duplicate / novel / unique) with controlled
strength levels, then evaluates precision and recall of the adjudication gate.

The benchmark is deterministic: same seed -> same pairs.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DEFAULT_PAIRS_PATH = os.path.join(REPO_ROOT, "data", "seed_dedup", "pairs_100.json")

# ── Synthetic vocabulary (domain-neutral, invented words) ──────────────

_TEMPLATES_TITLE = [
    "audit the {noun} gate",
    "verify the {noun} pipeline",
    "calibrate the {noun} model",
    "benchmark the {noun} solver",
    "optimise the {noun} routine",
    "validate the {noun} output",
    "profile the {noun} kernel",
    "tune the {noun} threshold",
    "review the {noun} contract",
    "extend the {noun} harness",
]

_TEMPLATES_GOAL = [
    "run the {noun} against the {adj} corpus with {param} and record the digest",
    "compare the {noun} output to the {adj} baseline using {param} as criterion",
    "measure the {noun} performance on {adj} inputs with {param} tolerance",
    "check the {noun} convergence against {adj} reference with {param} steps",
    "test the {noun} robustness under {adj} conditions with {param} samples",
]

_NOUNS = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi",
    "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
    "vortex", "prism", "nexus", "flux", "core", "shell", "mesh", "grid",
]

_ADJS = [
    "synthetic", "canonical", "reference", "baseline", "golden",
    "adversarial", "perturbed", "noisy", "clean", "frozen",
]

_PARAMS = [
    "threshold 4", "tolerance 0.01", "quota 8", "window 16",
    "stride 2", "depth 3", "limit 100", "seed 42",
]

_DELTA_QUANTITIES = [
    "quantity:residual-norm", "quantity:energy-balance", "quantity:mass-flux",
    "quantity:drag-coeff", "quantity:lift-ratio", "quantity:phase-lag",
    "quantity:wave-height", "quantity:layer-depth", "quantity:temp-gradient",
    "quantity:stress-max",
]

_DELTA_CRITERIA = [
    "criterion:convergence-1e6", "criterion:bounded-growth",
    "criterion:mass-conservation", "criterion:energy-bounded",
    "criterion:phase-accuracy", "criterion:grid-independence",
]

_DELTA_TRUTHS = [
    "truth:analytic-solution", "truth:experimental-data",
    "truth:benchmark-value", "truth:reference-table",
    "truth:calibration-curve",
]


def _seeded_random(seed: int) -> random.Random:
    return random.Random(seed)


def _fill(template: str, rng: random.Random) -> str:
    """Fill a template with random vocabulary."""
    result = template
    result = result.replace("{noun}", rng.choice(_NOUNS))
    result = result.replace("{adj}", rng.choice(_ADJS))
    result = result.replace("{param}", rng.choice(_PARAMS))
    return result


def _make_node(idx: int, rng: random.Random, deltas: bool = False) -> Dict[str, Any]:
    """Generate a synthetic ledger node."""
    title = _fill(rng.choice(_TEMPLATES_TITLE), rng)
    goal = _fill(rng.choice(_TEMPLATES_GOAL), rng)
    node = {
        "node_id": f"hist-{idx:04d}",
        "title": title,
        "goal": goal,
        "status": rng.choice(["done", "done", "done", "inflight"]),
    }
    if deltas:
        d = {}
        if rng.random() < 0.5:
            d["new_quantity"] = [rng.choice(_DELTA_QUANTITIES)]
        if rng.random() < 0.4:
            d["new_criterion"] = [rng.choice(_DELTA_CRITERIA)]
        if rng.random() < 0.3:
            d["new_truth"] = [rng.choice(_DELTA_TRUTHS)]
        if d:
            node["deltas"] = d
    return node


def _make_duplicate(base: Dict[str, Any], strength: str, rng: random.Random) -> Dict[str, Any]:
    """Create a near-duplicate candidate from a base node."""
    title = base["title"]
    goal = base["goal"]

    if strength == "heavy":
        # Swap a few words, keep structure
        words = title.split()
        if len(words) > 2:
            i, j = rng.sample(range(len(words)), 2)
            words[i], words[j] = words[j], words[i]
        title = " ".join(words)
        goal_words = goal.split()
        if len(goal_words) > 4:
            i, j = rng.sample(range(len(goal_words)), 2)
            goal_words[i], goal_words[j] = goal_words[j], goal_words[i]
        goal = " ".join(goal_words)
    elif strength == "medium":
        # Replace one noun
        title = title.replace(rng.choice(_NOUNS), rng.choice(_NOUNS), 1)
    elif strength == "light":
        # Add a modifier
        title = f"extended {title}"
    elif strength == "terse":
        # Shortened version
        title = title[:max(10, len(title) // 2)]
        goal = goal[:max(30, len(goal) // 2)]

    return {"title": title, "goal": goal, "deltas": {}}


def _make_novel(base: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """Create a novel candidate with fresh Δ."""
    title = _fill(rng.choice(_TEMPLATES_TITLE), rng)
    goal = _fill(rng.choice(_TEMPLATES_GOAL), rng)
    channel = rng.choice(list(_DELTA_QUANTITIES + _DELTA_CRITERIA + _DELTA_TRUTHS))
    prefix = channel.split(":")[0]
    if prefix == "quantity":
        deltas = {"new_quantity": [channel]}
    elif prefix == "criterion":
        deltas = {"new_criterion": [channel]}
    else:
        deltas = {"new_truth": [channel]}
    return {"title": title, "goal": goal, "deltas": deltas}


def _make_unique(rng: random.Random) -> Dict[str, Any]:
    """Create a completely unique candidate."""
    title = _fill(rng.choice(_TEMPLATES_TITLE), rng)
    goal = _fill(rng.choice(_TEMPLATES_GOAL), rng)
    return {"title": title, "goal": goal, "deltas": {}}


def build_benchmark(
    n_pairs: int = 100,
    n_ledger: int = 120,
    seed: int = 42,
    label_mix: Optional[Dict[str, int]] = None,
    strength_mix: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Build a deterministic benchmark with labelled pairs.

    Returns a dict with:
    - ``ledger``: list of synthetic ledger nodes
    - ``pairs``: list of (candidate, label, strength) tuples
    - ``generator``: metadata about generation
    """
    rng = _seeded_random(seed)
    label_mix = label_mix or {"duplicate": 40, "novel": 30, "unique": 30}
    strength_mix = strength_mix or {"light": 14, "medium": 13, "heavy": 13}

    # Build ledger
    ledger = [_make_node(i, rng, deltas=True) for i in range(n_ledger)]

    # Build pairs
    pairs = []
    labels = (
        ["duplicate"] * label_mix["duplicate"]
        + ["novel"] * label_mix["novel"]
        + ["unique"] * label_mix["unique"]
    )
    rng.shuffle(labels)

    strengths = []
    for s, count in strength_mix.items():
        strengths.extend([s] * count)
    rng.shuffle(strengths)

    for i, label in enumerate(labels):
        if label == "duplicate":
            base = ledger[i % len(ledger)]
            strength = strengths[i % len(strengths)] if strengths else "medium"
            cand = _make_duplicate(base, strength, rng)
            pairs.append({
                "candidate": cand,
                "label": "duplicate",
                "strength": strength,
                "base_node_id": base["node_id"],
            })
        elif label == "novel":
            base = ledger[i % len(ledger)]
            cand = _make_novel(base, rng)
            pairs.append({
                "candidate": cand,
                "label": "novel",
                "strength": None,
                "base_node_id": base["node_id"],
            })
        else:
            cand = _make_unique(rng)
            pairs.append({
                "candidate": cand,
                "label": "unique",
                "strength": None,
                "base_node_id": None,
            })

    # Deterministic seal
    payload = json.dumps({"ledger": ledger, "pairs": pairs}, sort_keys=True)
    seal = hashlib.sha256(payload.encode()).hexdigest()[:16]

    return {
        "schema": "seed-dedup-bench/v1",
        "ledger": ledger,
        "pairs": pairs,
        "generator": {
            "seed": seed,
            "n_ledger": n_ledger,
            "n_pairs": n_pairs,
            "label_mix": label_mix,
            "strength_mix": strength_mix,
            "seal": seal,
            "note": "synthetic vocabulary, not fitted to any domain",
        },
    }


def evaluate_benchmark(
    benchmark: Dict[str, Any],
    index,  # Bm25Index
    *,
    theta: Optional[float] = None,
    k: Optional[int] = None,
    thetas: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Evaluate precision and recall on the benchmark pairs.

    ``thetas`` evaluates several thresholds from a single similarity pass per
    candidate (the channels do not depend on theta, only the decision does), and
    returns ``sweeps`` alongside the metrics of the first threshold.

    Ground truth:

    - ``duplicate`` -> should be BLOCK
    - ``novel`` -> should be ALLOW (has fresh residual delta)
    - ``unique`` -> should be ALLOW (no near neighbour)

    Returns metrics dict with precision, recall, and per-label breakdown.
    """
    from .corpus import candidate_sim_cache
    from .ledger import adjudicate, ALLOW, BLOCK

    ledger = benchmark["ledger"]
    pairs = benchmark["pairs"]
    from .core import SeedConfig

    default_theta = SeedConfig().theta
    ths = [round(float(t), 6) for t in (thetas or [theta if theta is not None else default_theta])]
    tallies: Dict[str, Dict[str, int]] = {
        str(t): {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for t in ths
    }
    per_theta_label: Dict[str, Dict[str, Dict[str, int]]] = {str(t): {} for t in ths}

    for pair in pairs:
        cand = pair["candidate"]
        label = pair["label"]
        # one similarity pass per candidate; the decision is the only part that
        # depends on theta, so a sweep costs almost nothing extra
        cache = candidate_sim_cache(
            index, f"{cand.get('title') or ''}\n{cand.get('goal') or ''}", ledger
        ) if ths else None
        # Positive = should block (duplicate), Negative = should allow (novel/unique)
        should_block = label == "duplicate"
        for t in ths:
            kwargs = {"theta": t, "k": k}
            if cache is not None:
                kwargs["sim_cache"] = cache
            env = adjudicate(cand, ledger, index, **kwargs)
            blocked = env["decision"] == BLOCK
            tally = tallies[str(t)]
            if should_block and blocked:
                tally["tp"] += 1
            elif should_block:
                tally["fn"] += 1
            elif blocked:
                tally["fp"] += 1
            else:
                tally["tn"] += 1
            bucket = per_theta_label[str(t)].setdefault(label, {"allowed": 0, "blocked": 0})
            bucket["allowed" if not blocked else "blocked"] += 1

    first = str(ths[0]) if ths else ""
    tally = tallies[first] if first else {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    tp, fp, fn, tn = tally["tp"], tally["fp"], tally["fn"], tally["tn"]
    per_label = per_theta_label[first] if first else {}

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "n_pairs": len(pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_label": per_label,
        "policy": {
            "theta": theta,
            "k": k,
        },
        "sweeps": [
            {
                "theta": round(float(t), 6),
                **tallies[str(t)],
                "precision": round(
                    tallies[str(t)]["tp"] / (tallies[str(t)]["tp"] + tallies[str(t)]["fp"]), 6
                ) if (tallies[str(t)]["tp"] + tallies[str(t)]["fp"]) else 0.0,
                "recall": round(
                    tallies[str(t)]["tp"] / (tallies[str(t)]["tp"] + tallies[str(t)]["fn"]), 6
                ) if (tallies[str(t)]["tp"] + tallies[str(t)]["fn"]) else 0.0,
            }
            for t in ths
        ],
    }
