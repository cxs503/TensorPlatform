"""Generator for the labelled seeding near-duplicate pair set (100 pairs).

Design (controlled comparison, predeclared)
-------------------------------------------
*Unit of analysis*: one ``(candidate, neighbour)`` pair.  The positive class is
``duplicate`` — a re-worded restatement of an existing/in-flight node with **no
residual Δ** — which the gate must ``block``.  Two negative classes:

``novel``   same wording family **plus** a fresh Δ identifier in one channel
            (the only difference from ``duplicate`` is the Δ declaration, which
            is what makes the Δ channel identifiable);
``unique``  a different topic written with a different skeleton and different
            content words, **no** Δ — it must be allowed by the similarity
            threshold alone (this is what measures specificity).

``duplicate`` is further split by paraphrase strength, because a lexical gate has
a *known* blind spot: ``terse`` re-seedings (title + one clause) and ``heavy``
rewrites drift away from the neighbour's wording.  Those are kept in the positive
class on purpose — a benchmark that only contained easy paraphrases would report
a recall number that means nothing.

Vocabulary is invented (CJK syllables + generic project connectors), so the
fixture carries no subject-matter semantics, and the ledger is generated at a
realistic size (``ledger_size`` nodes) because the similarity features are
IDF-weighted and a two-document corpus has degenerate statistics.

Threshold policy: ``theta`` is *not* fitted here.  The generator only records the
score sweep (``theta_sweep`` in the emitted report); the shipped value lives in
:class:`ops.taskplane.governance.seeding_dedup.SeedingConfig` and was chosen on
the ``dev`` split (pairs 0..39) — see the node report for the plateau.
"""
from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Sequence, Tuple

SCHEMA = "seeding-pairs/v1"
SEED = 20261110
N_PAIRS = 100
N_LEDGER = 400

#: invented 2-char content words built from disjoint character pools
_HEADS = "珈琮珉玳珩珞璟珂"
_TAILS = "码尺轴环理纹铸钤"
WORDS: Tuple[str, ...] = tuple(f"{h}{t}" for h in _HEADS for t in _TAILS)

#: generic project connectors (paraphrase map; no subject-matter vocabulary)
CONNECTOR_PARA = {
    "复核": "校验",
    "台账": "登记簿",
    "摘要": "概述",
    "写进": "登记到",
    "评审": "复评",
}

STRENGTHS = ("light", "medium", "heavy", "terse")
LABELS = ("duplicate", "novel", "unique")


def _words(rng: random.Random) -> List[str]:
    return rng.sample(WORDS, 4)


def _template_one(ws: Sequence[str], value: int, strength: str) -> str:
    """The "same idea" template; ``strength`` controls how far the wording moves."""
    core = (
        f"{ws[0]} {ws[1]} 的 {ws[2]} 用 {ws[3]} 阈值 {value} 复核 并 把 摘要 写进 共享 台账 "
        "供 后续 评审"
    )
    if strength == "light":
        return core
    out = core.replace("供后续", "")
    for src, dst in CONNECTOR_PARA.items():
        out = out.replace(src, dst)
    if strength == "medium":
        return out
    if strength == "heavy":
        return f"本项 {out.replace(' 并 ', '，随后 ')}，供复评留档"
    # terse: the same idea, stated minimally (a real re-seeding failure mode)
    return f"{ws[0]} {ws[1]} {ws[2]} {ws[3]} 阈值 {value} 校验 登记"


def _template_two(ws: Sequence[str], value: int) -> str:
    """A different skeleton: the ``unique`` class."""
    return (
        f"关注 {ws[2]} 在 {ws[0]} 情形下 {ws[1]} 的漂移，按 {ws[3]} 记录差额 "
        f"{value} 并交专人复评"
    )


def _title(ws: Sequence[str]) -> str:
    return f"{ws[0]} {ws[1]} 复核"


def build_ledger(n: int = N_LEDGER, seed: int = SEED) -> List[Dict[str, Any]]:
    rng = random.Random(seed + 1)
    ledger: List[Dict[str, Any]] = []
    for i in range(n):
        ws = _words(rng)
        value = rng.randint(2, 13)
        ledger.append(
            {
                "node_id": f"node-{i:04d}",
                "title": _title(ws),
                "goal": _template_one(ws, value, "light"),
                "status": "inflight" if i % 5 == 0 else "done",
                "deltas": {},
            }
        )
    return ledger


def build_pairs(n: int = N_PAIRS, seed: int = SEED, ledger: Sequence[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    rng = random.Random(seed)
    ledger = list(ledger or build_ledger(seed=seed))
    pairs: List[Dict[str, Any]] = []
    # label mix: 40 duplicate / 30 novel / 30 unique, interleaved deterministically
    plan: List[str] = ["duplicate"] * 40 + ["novel"] * 30 + ["unique"] * 30
    rng.shuffle(plan)
    for i, label in enumerate(plan[:n]):
        node = ledger[rng.randrange(len(ledger))]
        ws = node["title"].split()[0:1]
        # recover the neighbour's four content words from its goal text
        words = [w for w in node["goal"].split() if len(w) == 2 and w in WORDS][:4]
        while len(words) < 4:
            words = _words(rng)
        if label == "unique":
            other = _words(rng)
            while set(other) & set(words):
                other = _words(rng)
            candidate = {
                "title": "漂移 复评",
                "goal": _template_two(other, 3),
                "deltas": {},
            }
            strength = "unique"
        else:
            strength = STRENGTHS[i % len(STRENGTHS)]
            value = rng.randint(2, 13)
            goal = _template_one(words, value, strength)
            deltas: Dict[str, List[str]] = {}
            if label == "novel":
                channel = ("new_quantity", "new_criterion", "new_truth")[i % 3]
                deltas = {channel: [f"{channel.split('_')[1]}:{words[0]}-{i}"]}
            candidate = {"title": _title(words), "goal": goal, "deltas": deltas}
        pairs.append(
            {
                "pair_id": f"P{i:03d}",
                "label": label,
                "paraphrase": strength,
                "candidate": candidate,
                "neighbour": {k: node[k] for k in ("node_id", "title", "goal", "status", "deltas")},
                "split": "dev" if i < 40 else "holdout",
            }
        )
    return {
        "schema": SCHEMA,
        "purpose": (
            "Labelled seeding pairs: positive = duplicate (re-worded restatement without "
            "residual delta, must block); negatives = novel (same wording family but a fresh "
            "delta identifier) and unique (different topic, different skeleton)."
        ),
        "generator": {
            "module": "ops/taskplane/governance/seeding_dedup_pairs.py",
            "seed": seed,
            "n_pairs": len(pairs),
            "n_ledger": len(ledger),
            "strength_mix": {s: sum(1 for p in pairs if p["paraphrase"] == s) for s in STRENGTHS + ("unique",)},
            "label_mix": {lab: sum(1 for p in pairs if p["label"] == lab) for lab in LABELS},
            "split": {"dev": sum(1 for p in pairs if p["split"] == "dev"),
                      "holdout": sum(1 for p in pairs if p["split"] == "holdout")},
            "note": (
                "theta is not fitted by this generator; the shipped theta comes from "
                "SeedingConfig and was selected on the dev split only"
            ),
        },
        "ledger": ledger,
        "pairs": pairs,
    }


def write_pairs(path: str, n: int = N_PAIRS, seed: int = SEED) -> Dict[str, Any]:
    payload = build_pairs(n=n, seed=seed)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return payload


if __name__ == "__main__":  # pragma: no cover - CLI
    import argparse

    ap = argparse.ArgumentParser(description="generate the seeding dedup pair set")
    ap.add_argument("--out", default="data/seeding_dedup/pairs_100.json")
    ap.add_argument("--n", type=int, default=N_PAIRS)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    out = a.out if os.path.isabs(a.out) else os.path.join(root, a.out)
    payload = write_pairs(out, n=a.n, seed=a.seed)
    print(f"[seeding-pairs] {payload['generator']['n_pairs']} pairs -> {a.out}")