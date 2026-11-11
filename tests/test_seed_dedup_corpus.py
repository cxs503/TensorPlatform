"""Corpus-level redundancy baseline for the seeding dedup gate.

Two things need to stay true as this matures:

* the **fast scorer** used for a 1.9k-node census must agree with the pairwise
  oracle ``dual_similarity`` (otherwise the census and the gate would quote
  different similarity for the same pair);
* **labels** must come from provenance (ids), never from the metric under test,
  so precision/recall are not circular.

All fixtures use invented, domain-free vocabulary.
"""
from __future__ import annotations

import json
import os

import pytest

from tools.seed_dedup import corpus as C
from tools.seed_dedup.core import SeedConfig, dual_similarity

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO_ROOT, "data/seed_dedup/corpus/nodes.json")
PROVENANCE = os.path.join(REPO_ROOT, "data/seed_dedup/corpus/nodes.provenance.json")


# --------------------------------------------------------------- fixtures
WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]


def _node(i: int, title: str, goal: str, *, ext: str = "", status: str = "done") -> dict:
    return {
        "node_id": f"n{i:03d}",
        "title": title,
        "goal": goal,
        "status": status,
        "ext_id": ext or f"x{i:03d}",
        "plan_id": 1,
        "repo": "synthetic",
        "proposal_src": [],
    }


def _synthetic_corpus() -> list:
    """A small corpus with invented words and little shared boilerplate.

    Deliberately *not* built from one template: the blocker drops grams that
    occur in more than ``max_df_frac`` of the corpus, so a fully boilerplate
    corpus would have no discriminative grams left to block on.
    """
    nodes = []
    for i in range(12):
        w = WORDS[i % len(WORDS)] + WORDS[(i * 3) % len(WORDS)] + WORDS[(i * 5) % len(WORDS)]
        nodes.append(
            _node(
                i,
                f"audit the {w} gate",
                f"run the {w} procedure {w} and record digest {w} number {i}",
            )
        )
    # two planted near-duplicates (same text, different id / ext id)
    dup_title = "audit the epsilonzetaeta gate"
    dup_goal = "run the epsilonzetaeta procedure and record the digest twice"
    nodes.append(_node(20, dup_title, dup_goal))
    nodes.append(_node(21, dup_title, dup_goal))
    # an exact reseed: same ext id as node 3
    nodes.append(_node(22, "audit the alphabeta delta gate", "run the alpha beta delta probe and record digest 3", ext="x003"))
    # a second text-identical pair with unrelated identity (a re-submitted seed)
    nodes.append(_node(23, "calibrate the zetaeta prisms", "calibrate the zetaeta prisms twice and keep the seal"))
    nodes.append(_node(24, "calibrate the zetaeta prisms", "calibrate the zetaeta prisms twice and keep the seal"))
    return nodes


# --------------------------------------------------------------- scorer
def test_fast_census_scorer_matches_the_pairwise_oracle():
    nodes = _synthetic_corpus()
    index = C.build_index(nodes)
    cfg = SeedConfig()
    for probe in (0, 5, 12):
        cache = C.candidate_sim_cache(index, C.node_text(nodes[probe]), nodes, cfg=cfg)
        for node in nodes:
            oracle = dual_similarity(index, C.node_text(nodes[probe]), node["node_id"], C.node_text(node), cfg)
            fast = cache["sims"][node["node_id"]]
            assert fast["score"] == pytest.approx(oracle.score, abs=1e-6)
            assert fast["jaccard_3gram"] == pytest.approx(oracle.jaccard_3gram, abs=1e-6)


def test_sim_cache_refuses_to_serve_a_different_candidate():
    nodes = _synthetic_corpus()
    index = C.build_index(nodes)
    cache = C.candidate_sim_cache(index, C.node_text(nodes[0]), nodes)
    ledger = C.to_ledger_nodes(nodes[1:])
    with pytest.raises(ValueError):
        C.adjudicate({"title": "unrelated title text", "goal": "a goal that is long enough to pass the floor"},
                     ledger, index, sim_cache=cache)


# --------------------------------------------------------------- blocking
def test_blocking_finds_planted_duplicates_and_stays_a_subset():
    nodes = _synthetic_corpus()
    pairs = C.blocked_pairs(nodes, min_shared=6, max_df_frac=0.5)
    assert all(0 <= a < b < len(nodes) for a, b in pairs)
    got = {(nodes[a]["node_id"], nodes[b]["node_id"]) for a, b in pairs}
    assert ("n020", "n021") in got, "identical texts must be blocked together"
    assert len(pairs) < len(nodes) * (len(nodes) - 1) // 2


def test_brute_force_oracle_agrees_with_blocked_census_on_hits():
    nodes = _synthetic_corpus()
    truth = {(r["a"], r["b"]): r for r in C.brute_force_scores(nodes)}
    pairs = C.blocked_pairs(nodes, min_shared=6, max_df_frac=0.5)
    scored = C.pairwise_scores(nodes, pairs)
    blocked = {(r["a"], r["b"]) for r in scored}
    planted = {("n020", "n021"), ("n023", "n024")}
    assert planted <= blocked, "blocking must not lose the planted duplicates"
    for row in scored:
        assert row["score"] == pytest.approx(truth[(row["a"], row["b"])]["score"], abs=1e-6)


# --------------------------------------------------------------- census
def test_families_are_connected_components():
    fams = C.families([("a", "b"), ("b", "c"), ("x", "y"), ("z", "z")])
    assert ["a", "b", "c"] in fams
    assert ["x", "y"] in fams
    assert all(len(f) > 1 for f in fams)


def test_redundancy_baseline_is_deterministic_and_sealed():
    nodes = _synthetic_corpus()
    a = C.redundancy_baseline(nodes, min_shared=6, max_df_frac=0.5)
    b = C.redundancy_baseline(nodes, min_shared=6, max_df_frac=0.5)
    assert a["digest"]["sha256"] == b["digest"]["sha256"]
    assert C.verify(a)
    base = a["baseline"]
    assert base["n_pairs_ge_theta"] >= 1
    assert base["n_families"] >= 1
    assert base["largest_family_size"] >= 2
    assert 0.0 <= base["redundant_node_frac"] <= 1.0


# --------------------------------------------------------------- labels
def test_provenance_labels_come_from_ids_not_similarity():
    nodes = _synthetic_corpus()
    labelled = C.provenance_pairs(nodes, n_positive=10, n_negative=10, seed=7)
    assert labelled["n_duplicate"] >= 2
    dups = [p for p in labelled["pairs"] if p["label"] == "duplicate"]
    assert {p["why"] for p in dups} <= {"same_ext_id", "same_seed_head"}
    for pair in labelled["pairs"]:
        a, b = nodes[pair["a_index"]], nodes[pair["b_index"]]
        if pair["label"] == "novel":
            assert a["ext_id"] != b["ext_id"]
    assert labelled["label_sources"]["duplicate"] == ["same_seed_head"]
    again = C.provenance_pairs(nodes, n_positive=10, n_negative=10, seed=7)
    assert again["digest"] == labelled["digest"]


def test_provenance_replay_recall_and_precision_on_planted_pairs():
    nodes = _synthetic_corpus()
    labelled = C.provenance_pairs(nodes, n_positive=10, n_negative=10, seed=7)
    rep = C.evaluate_provenance_pairs(nodes, labelled, thetas=(0.30,))
    assert rep["n_pairs"] == labelled["n_pairs"]
    assert rep["n_duplicate"] >= 2
    assert rep["recall"] >= 0.85, rep["missed_duplicates"]
    assert rep["precision"] >= 0.85, rep["false_blocks"]
    assert rep["metrics"]["tp"] + rep["metrics"]["fn"] == labelled["n_duplicate"]


def test_theta_sweep_reports_every_threshold_it_was_given():
    nodes = _synthetic_corpus()
    labelled = C.provenance_pairs(nodes, n_positive=10, n_negative=10, seed=7)
    rep = C.evaluate_provenance_pairs(nodes, labelled, thetas=(0.2, 0.3, 0.6))
    assert [r["theta"] for r in rep["sweep"]] == [0.2, 0.3, 0.6]
    assert rep["sweep"][0]["tp"] + rep["sweep"][0]["fn"] == labelled["n_duplicate"]


# --------------------------------------------------------------- artifacts
def test_committed_snapshot_matches_its_provenance():
    if not os.path.isfile(CORPUS):
        pytest.skip("corpus snapshot not built in this checkout")
    payload = C.load_corpus(CORPUS)
    prov = json.load(open(PROVENANCE, encoding="utf-8"))
    from tools.seed_dedup.corpus_export import corpus_digest

    assert corpus_digest(payload["nodes"]) == prov["snapshot"]["sha256"]
    assert prov["n_nodes"] == payload["n_nodes"] == len(payload["nodes"])
    assert len({n["node_id"] for n in payload["nodes"]}) == payload["n_nodes"], "node ids must be unique"
    assert payload["n_nodes"] >= 1000, "the ledger must cover the reachable campaign corpus"
    assert prov["sources"]["node"] + prov["sources"]["mission"] == payload["n_nodes"]


def test_export_projection_is_lossless_for_the_gate_fields():
    from tools.seed_dedup.corpus_export import shape_node, _as_list

    node = shape_node(
        {"id": 7, "name": "audit the beta gate", "status": "done",
         "metadata": {"goal": "g" * 900, "ext_id": "X-1", "repo": "synthetic", "proposal_src": 42}},
        9001,
        goal_chars=100,
    )
    assert node["node_id"] == "p9001:7"
    assert node["status"] == "done"
    assert len(node["goal"]) == 100
    assert node["goal_truncated_at"] == 100
    assert node["proposal_src"] == [42]
    assert _as_list(None) == [] and _as_list([1, 2]) == [1, 2] and _as_list(3) == [3]