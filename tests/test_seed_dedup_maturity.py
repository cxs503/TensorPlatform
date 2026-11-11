"""Maturity runner: end-to-end gate metrics, determinism, and artifact audit.

The runner is what turns "the gate exists" into "the gate carries numbers":
corpus redundancy baseline + provenance-labelled replay + synthetic graded
replay, all sealed, plus a repeat run that must be byte-identical.
"""
from __future__ import annotations

import json
import os

import pytest

from tools.seed_dedup import corpus as C
from tools.seed_dedup.core import SeedConfig
from tools.seed_dedup.maturity import determinism_check, run
from tools.whitespace_radar_core.evidence import verify

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(REPO_ROOT, "artifacts/seed_dedup/corpus_maturity.json")


def _corpus(n: int = 24) -> dict:
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
             "iota", "kappa", "lambda", "mu"]
    nodes = []
    for i in range(n):
        w = words[i % len(words)]
        nodes.append(
            {
                "node_id": f"n{i:03d}",
                "title": f"audit the {w} gate",
                "goal": f"run the {w} gate against the frozen corpus number {i} and record the digest",
                "status": "done",
                "ext_id": f"x{i:03d}",
                "plan_id": 1 + i % 3,
                "repo": "synthetic",
                "proposal_src": [],
            }
        )
    # planted duplicates: byte-identical text, and one reseed sharing an ext id
    nodes.append(dict(nodes[3], node_id="n900", ext_id="x003"))
    nodes.append(dict(nodes[4], node_id="n901", ext_id="xfoo"))
    nodes.append(dict(nodes[5], node_id="n902", ext_id="xbar"))
    return {"schema": "seed-corpus/v1", "n_nodes": len(nodes), "nodes": nodes, "digest": {"sha256": "0" * 64}}


def test_run_emits_every_acceptance_number():
    rep = run(_corpus(), thetas=(0.2, 0.3, 0.6), n_positive=10, n_negative=10)
    assert rep["schema"] == "seed-dedup-maturity/v1"
    assert rep["corpus"]["n_unique_node_ids"] == rep["corpus"]["n_nodes"]
    assert rep["baseline"]["corpus"]["n_pairs_blocked"] > 0
    assert len(rep["census_sweep"]) == 3
    assert rep["labelled_pairs"]["n_pairs"] == (
        rep["labelled_pairs"]["n_duplicate"] + rep["labelled_pairs"]["n_novel"]
    )
    chosen = rep["provenance_replay"]
    assert 0.0 <= chosen["precision"] <= 1.0 and 0.0 <= chosen["recall"] <= 1.0
    assert rep["synthetic_replay"]["n_pairs"] == 100
    acc = rep["acceptance"]
    for key in (
        "provenance_pair_precision_ok",
        "provenance_pair_recall_ok",
        "synthetic_precision_ok",
        "synthetic_recall_ok",
        "determinism_identical",
        "passed",
    ):
        assert key in acc


def test_planted_duplicates_are_blocked_so_recall_is_full():
    rep = run(_corpus(), thetas=(0.30,), n_positive=10, n_negative=10)
    assert rep["provenance_replay"]["recall"] == 1.0, rep["provenance_replay"]["missed_duplicates"]
    assert rep["provenance_replay"]["false_blocks"] == []


def test_determinism_check_reports_identical_seals():
    corpus = _corpus()
    first = C.redundancy_baseline(corpus["nodes"], cfg=SeedConfig(), min_shared=6, max_df_frac=0.5)
    det = determinism_check(
        corpus, first["digest"]["sha256"], cfg=SeedConfig(), min_shared=6, max_df_frac=0.5
    )
    assert det["identical"] is True
    assert det["seal_a"] == det["seal_b"]


def test_census_sweep_is_monotone_non_increasing_in_theta():
    rep = run(_corpus(), thetas=(0.2, 0.4, 0.6), n_positive=10, n_negative=10)
    counts = [row["n_pairs_ge_theta"] for row in rep["census_sweep"]]
    assert counts == sorted(counts, reverse=True)


# --------------------------------------------------------------- artifact
def test_committed_maturity_artifact_verifies_and_carries_numbers():
    if not os.path.isfile(REPORT):
        pytest.skip("maturity artifact not produced in this checkout")
    payload = json.load(open(REPORT, encoding="utf-8"))
    assert verify(payload), "maturity artifact seal does not verify"
    report = payload
    assert report["corpus"]["n_nodes"] >= 1000
    assert report["policy"]["calibrated_theta"] > report["policy"]["shipped_default_theta"]
    assert report["determinism"]["identical"] is True
    base = report["baseline"]["baseline"]
    blocked = report["baseline"]["corpus"]["n_pairs_blocked"]
    assert blocked > 0
    assert base["n_pairs_ge_theta"] <= blocked
    assert report["provenance_replay"]["n_pairs"] >= 100
    pair_level = report["provenance_replay"]["pair_level"]["at_theta"]
    assert pair_level["precision"] >= 0.85
    assert pair_level["recall"] >= 0.85
    assert report["synthetic_replay"]["at_theta"]["precision"] >= 0.85
    assert report["synthetic_replay"]["at_theta"]["recall"] >= 0.85
    assert report["determinism"]["identical"] is True
    assert report["acceptance"]["passed"] is True
    assert report["acceptance"]["determinism_identical"] is True
    assert report["labelled_pairs"]["digest"]
    for row in report["provenance_replay"]["rows"]:
        assert row["decision"] in ("allow", "block")
        assert row["a"] != row["b"]