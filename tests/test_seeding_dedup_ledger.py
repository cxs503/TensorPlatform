"""Acceptance tests for the seeding near-duplicate ledger with residual-Δ gate.

Machine criteria covered here:

``C3``  >= 100 labelled pairs -> precision >= 0.85 and recall >= 0.85
``C4``  same input -> byte identical sealed envelope (twice)
``C5``  the module is inside the zero-domain-semantics scan scope (enforced in
        ``tests/test_whitespace_specificity.py``)

plus the fail-closed semantics: no Δ -> block, Δ repeating the neighbour -> block,
fresh Δ in any of the three channels -> allow, in-flight neighbours count.

Run::

    python3 -m pytest tests/test_seeding_dedup_ledger.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from ops.taskplane.governance.proposal_dedup import DedupConfig  # noqa: E402
from ops.taskplane.governance.seeding_dedup import (  # noqa: E402
    ALLOW,
    BLOCK,
    CODES,
    DELTA_CHANNELS,
    SCHEMA,
    SeedingConfig,
    Candidate,
    Node,
    adjudicate,
    ledger_seal,
    load_ledger,
    near_duplicate_report,
)
from ops.taskplane.governance.seeding_dedup_bench import (  # noqa: E402
    DEFAULT_PAIRS,
    evaluate,
    load_pairs,
)
from ops.taskplane.governance.seeding_dedup_pairs import build_pairs  # noqa: E402
from tools.whitespace_radar_core.evidence import dumps, verify  # noqa: E402

PAIRS = os.path.join(REPO_ROOT, DEFAULT_PAIRS)


@pytest.fixture(scope="module")
def pairs_payload():
    return load_pairs(PAIRS)


NEIGHBOUR = {
    "node_id": "hist-001",
    "title": "audit the alpha gate",
    "goal": (
        "audit the alpha gate that belongs to zeta with threshold 4 and record the "
        "digest in the shared roster for later review"
    ),
    "status": "done",
    "deltas": {"new_quantity": ["quantity:alpha-threshold-1"]},
}
CAND = {
    "title": "audit the alpha gate",
    "goal": (
        "audit the alpha gate that belongs to zeta with threshold 4 and record the "
        "digest in the shared roster for later review"
    ),
    "deltas": {},
}


# --------------------------------------------------------------------------
# C3 — precision / recall on the labelled pair set
# --------------------------------------------------------------------------
def test_c3_gate_precision_and_recall(pairs_payload):
    report = evaluate(pairs_payload)
    gate = report["gate"]
    assert report["n_pairs"] == 100
    # predeclared acceptance: both >= 0.85
    assert gate["precision"] >= 0.85, gate
    assert gate["recall"] >= 0.85, gate
    assert report["gate_holdout"]["precision"] >= 0.85
    assert report["gate_holdout"]["recall"] >= 0.85


def test_c3_ablation_shows_the_delta_channel_is_load_bearing(pairs_payload):
    report = evaluate(pairs_payload)
    # Similarity alone blocks every fresh-Δ pair too, so precision collapses;
    # declaring a Δ without looking at the score blocks every genuinely new topic.
    assert report["ablation_similarity_only"]["precision"] < report["gate"]["precision"]
    assert report["ablation_delta_only"]["precision"] < report["gate"]["precision"]
    assert report["gate"]["precision"] == 1.0 and report["gate"]["recall"] == 1.0


def test_c3_pair_set_is_deterministic_and_documented(pairs_payload):
    regenerated = build_pairs()
    assert regenerated["pairs"] == pairs_payload["pairs"]
    assert regenerated["ledger"] == pairs_payload["ledger"]
    gen = pairs_payload["generator"]
    assert gen["label_mix"] == {"duplicate": 40, "novel": 30, "unique": 30}
    assert set(gen["strength_mix"]) == {"light", "medium", "heavy", "terse", "unique"}
    assert gen["n_ledger"] >= 100
    assert "not fitted" in gen["note"]


# --------------------------------------------------------------------------
# fail-closed semantics
# --------------------------------------------------------------------------
def test_block_when_no_residual_delta():
    env = adjudicate(CAND, [NEIGHBOUR])
    assert env["schema"] == SCHEMA
    assert env["decision"] == BLOCK and env["code"] == "NO_RESIDUAL_DELTA"
    required = env["required_residual"]
    assert required["nearest"] == "hist-001"
    assert required["nearest_score"] >= required["theta"]
    assert set(required["missing_channels"]) == set(DELTA_CHANNELS)
    assert required["channels"]["new_quantity"]["neighbour_occupied"] == ["quantity:alpha-threshold-1"]
    assert env["reasons"] and "fail-closed" in " ".join(env["reasons"])
    assert verify(env)


def test_block_when_delta_repeats_the_neighbour():
    cand = dict(CAND, deltas={"new_quantity": ["QUANTITY:ALPHA-THRESHOLD-1"]})
    env = adjudicate(cand, [NEIGHBOUR])
    assert env["decision"] == BLOCK and env["code"] == "NO_RESIDUAL_DELTA"
    ch = env["required_residual"]["channels"]["new_quantity"]
    assert ch["residual"] == [] and ch["repeated"]


def test_block_when_delta_is_only_restated_in_the_neighbour_text():
    neighbour = dict(NEIGHBOUR, deltas={}, goal=NEIGHBOUR["goal"] + " criterion: zeta-bound-2 applies")
    cand = dict(CAND, deltas={"new_criterion": ["criterion: zeta-bound-2"]})
    env = adjudicate(cand, [neighbour])
    assert env["decision"] == BLOCK and env["code"] == "NO_RESIDUAL_DELTA"


@pytest.mark.parametrize("channel", DELTA_CHANNELS)
def test_allow_when_any_channel_carries_fresh_delta(channel):
    cand = dict(CAND, deltas={channel: [f"{channel}: fresh-value-9"]})
    env = adjudicate(cand, [NEIGHBOUR])
    assert env["decision"] == ALLOW and env["code"] == "RESIDUAL_DELTA"
    assert env["required_residual"]["channels"][channel]["satisfied"] is True


def test_inflight_neighbour_blocks_like_history():
    inflight = dict(NEIGHBOUR, node_id="air-777", status="inflight")
    env = adjudicate(CAND, [inflight])
    assert env["decision"] == BLOCK
    assert env["ledger"]["n_inflight"] == 1 and env["ledger"]["n_done"] == 0
    assert env["required_residual"]["nearest_status"] == "inflight"


def test_allow_when_ledger_has_no_near_duplicate():
    """The threshold is injectable; with it above the neighbour's score the gate allows.

    (On a two-document corpus the IDF statistics are degenerate, so the unit test
    pins the threshold explicitly instead of pretending the score is meaningful.)
    """
    far = dict(
        NEIGHBOUR,
        node_id="hist-999",
        title="rank the buffer for kappa",
        goal="rank the buffer belonging to kappa with quota 8 then publish the fingerprint",
        deltas={},
    )
    env = adjudicate(CAND, [far], theta=0.99)
    assert env["decision"] == ALLOW and env["code"] == "NEW_TOPIC"
    assert env["required_residual"] is None
    assert env["policy"]["theta"] == 0.99
    assert env["neighbours"][0]["node_id"] == "hist-999"


@pytest.mark.parametrize(
    "cand",
    [
        {"title": "", "goal": CAND["goal"]},
        {"title": "audit", "goal": "too short"},
        {"title": "ab", "goal": "x y z"},
    ],
)
def test_fail_closed_on_insufficient_input(cand):
    env = adjudicate(cand, [NEIGHBOUR])
    assert env["decision"] == BLOCK and env["code"] == "INSUFFICIENT_INPUT"
    assert env["neighbours"] == []


def test_fail_closed_on_invalid_ledger():
    env = adjudicate(CAND, [{"title": "no id", "goal": "x"}])
    assert env["decision"] == BLOCK and env["code"] == "LEDGER_INVALID"


def test_block_with_empty_ledger_is_impossible_but_documented():
    """An empty ledger must not crash: no neighbours -> NEW_TOPIC (allow)."""
    env = adjudicate(CAND, [])
    assert env["decision"] == ALLOW and env["code"] == "NEW_TOPIC"


def test_codes_are_a_closed_set():
    for code in ("NEW_TOPIC", "RESIDUAL_DELTA", "NO_RESIDUAL_DELTA", "INSUFFICIENT_INPUT", "LEDGER_INVALID"):
        assert code in CODES


# --------------------------------------------------------------------------
# ledger / neighbours
# --------------------------------------------------------------------------
def test_neighbours_are_ranked_and_capped():
    ledger = [NEIGHBOUR] + [
        dict(NEIGHBOUR, node_id=f"hist-10{i}", title=f"audit the beta gate {i}",
             goal=f"audit the beta gate {i} with threshold {i} and record the digest")
        for i in range(8)
    ]
    env = adjudicate(CAND, ledger, k=3)
    scores = [n["score"] for n in env["neighbours"]]
    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)
    assert env["neighbours"][0]["node_id"] == "hist-001"


def test_ledger_seal_tracks_content():
    a = ledger_seal([Node("n1", "t", "g")])
    b = ledger_seal([Node("n1", "t", "g ",)])
    assert a != b
    assert a == ledger_seal([Node("n1", "t", "g")])


def test_load_ledger_accepts_export_shape(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"schema": "x", "nodes": [NEIGHBOUR]}), encoding="utf-8")
    nodes = load_ledger(str(path))
    assert nodes[0].node_id == "hist-001" and nodes[0].status == "done"


# --------------------------------------------------------------------------
# C4 — determinism
# --------------------------------------------------------------------------
def test_c4_same_input_same_bytes():
    for _ in range(2):
        pass
    a = adjudicate(CAND, [NEIGHBOUR], cfg=DedupConfig())
    b = adjudicate(CAND, [NEIGHBOUR], cfg=DedupConfig())
    assert dumps(a) == dumps(b)
    assert a["digest"]["sha256"] == b["digest"]["sha256"]


def test_c4_batch_report_is_deterministic(pairs_payload):
    cands = [p["candidate"] for p in pairs_payload["pairs"][:12]]
    ledger = pairs_payload["ledger"]
    a = near_duplicate_report(cands, ledger)
    b = near_duplicate_report(cands, ledger)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["n_candidates"] == 12 and a["n_blocked"] + a["n_allowed"] == 12


def test_candidate_dataclass_matches_mapping_api():
    node = Node(NEIGHBOUR["node_id"], NEIGHBOUR["title"], NEIGHBOUR["goal"], NEIGHBOUR["status"], NEIGHBOUR["deltas"])
    a = adjudicate(CAND, [node])
    b = adjudicate(CAND, [NEIGHBOUR])
    assert a["digest"]["sha256"] == b["digest"]["sha256"]
    cand = Candidate(CAND["title"], CAND["goal"], CAND["deltas"])
    assert adjudicate(cand, [NEIGHBOUR])["digest"]["sha256"] == b["digest"]["sha256"]