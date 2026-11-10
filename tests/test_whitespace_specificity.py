"""Acceptance tests for the whitespace-adjudication *specificity discipline*.

The node's machine criteria map onto this file as follows:

``C1``  radar replay bench on the **pinned** pool -> 18/18, ``require_all``
``C2``  the 6 adversarial *false covered* probes -> 6/6 downgraded to ``generic_only``
``C4``  same input -> byte identical envelope (sha256 equal, twice)
``C5``  zero-domain-semantics scan stays green, and a *mutation* of one scanned
        file turns it red (the check is proven to have teeth)

Plus the unit-level semantics of the new verdict: a witness document is required,
the refused evidence is kept in the ledger, and the thresholds did not move.

Run::

    python3 -m pytest tests/test_whitespace_specificity.py -q
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core import (  # noqa: E402
    BLANK,
    COVERED,
    GENERIC_ONLY,
    PARTIAL,
    Bm25Index,
    Doc,
    Thresholds,
    analyze,
    apply_pin,
    dumps,
    load_bench,
    load_false_covered,
    load_pin,
    load_pool,
    passes_acceptance,
    pin_report,
    pool_digest,
    replay,
    replay_false_covered,
    verify,
)
from tools.whitespace_radar_core.bridge import dedup_gate  # noqa: E402

POOL_MANIFEST = os.path.join(REPO_ROOT, "data/whitespace_radar/pool/manifest.json")
POOL_PIN = os.path.join(REPO_ROOT, "data/whitespace_radar/pool/pin.json")
BENCH = os.path.join(REPO_ROOT, "data/whitespace_radar/replay_bench.json")
FALSE_COVERED = os.path.join(REPO_ROOT, "data/whitespace_radar/false_covered_probes.json")
NEUTRALITY = os.path.join(REPO_ROOT, "data/whitespace_radar/domain_terms.json")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pin():
    return load_pin(POOL_PIN)


@pytest.fixture(scope="module")
def live_docs():
    return load_pool(REPO_ROOT, POOL_MANIFEST)


@pytest.fixture(scope="module")
def pool(pin, live_docs):
    docs = apply_pin(live_docs, pin)
    return docs, Bm25Index.build(docs), pool_digest(docs)


@pytest.fixture(scope="module")
def bench():
    return load_bench(BENCH)


# --------------------------------------------------------------------------
# C1 — the replay bench is scored against the pool its labels were frozen on
# --------------------------------------------------------------------------
def test_c1_pinned_pool_reproduces_every_frozen_label(pool, bench, pin, live_docs):
    docs, idx, digest = pool
    report = replay(idx, bench, pool_digest=digest, pool_pin=pin_report(live_docs, pin))
    assert report["n_topics"] == 18
    assert report["n_correct"] == 18, report["mismatches"]
    assert report["accuracy"] == 1.0
    assert report["blank_false_positive"] == 0
    assert passes_acceptance(report, require_all=True)


def test_c1_live_pool_drift_is_reported_not_absorbed(live_docs, pin, bench, pool):
    """Repo growth must show up as *drift*, not as a silent label mismatch."""
    drift = pin_report(live_docs, pin)
    assert drift["drift"] is True
    assert drift["n_extra"] > 0, "the working tree has grown since the labels were frozen"
    assert drift["n_pinned"] > 0

    _docs, pinned_idx, pinned_digest = pool
    live_idx = Bm25Index.build(live_docs)
    live_report = replay(live_idx, bench, pool_digest=pool_digest(live_docs))
    # The live pool no longer reproduces every frozen label — that is exactly why
    # the bench runs on the pinned snapshot, and why the drift travels with the
    # report instead of being averaged away.
    assert live_report["n_correct"] < 18
    assert live_report["pool_pin"] == {}
    pinned_report = replay(pinned_idx, bench, pool_digest=pinned_digest)
    assert pinned_report["accuracy"] == 1.0


# --------------------------------------------------------------------------
# C2 — the adversarial false-covered probes are downgraded
# --------------------------------------------------------------------------
def test_c2_false_covered_probes_are_all_downgraded(pool):
    docs, idx, digest = pool
    probe_set = load_false_covered(FALSE_COVERED)
    report = replay_false_covered(idx, probe_set, pool_digest=digest)
    assert report["n_probes"] == 6
    assert report["n_detected"] == 6, report["missed"]
    assert report["detection_rate"] == 1.0
    for row in report["rows"]:
        assert row["verdict"] == GENERIC_ONLY
        assert row["expected"] == GENERIC_ONLY
        # the legacy gate would have said covered: score + coverage still clear
        assert row["n_existing_specific"] >= 2
        # ... but the witnesses never co-locate
        assert row["n_witness_probes"] < 2
        assert row["generic_terms"], "the generic ledger must keep the refused wording"


def test_c2_probe_set_declares_its_construction_rule():
    probe_set = load_false_covered(FALSE_COVERED)
    assert "disjoint document support" in probe_set["construction_rule"]
    for probe in probe_set["probes"]:
        assert probe["co_occur_docs"] == 0
        assert probe["legacy_gate"]["n_existing_specific"] >= 2
        assert probe["legacy_gate"]["specific_coverage"] >= 0.5
        assert set(probe["probe_a"]["docs"]) & set(probe["probe_b"]["docs"]) == set()


# --------------------------------------------------------------------------
# unit semantics on a synthetic pool (invented vocabulary, no subject matter)
# --------------------------------------------------------------------------
def _synthetic(witness_together: bool):
    """Two rare probes that either live in one document or in two documents."""
    # ``复核`` is deliberately ubiquitous (df/N well above rare_frac) so that the
    # only discriminative probes are ``珈码`` / ``琮尺``: whether they share a
    # document is then the *only* difference between the two fixtures.
    docs = [
        Doc("doc:a", "doc", "alpha record", "珈码 复核 记录 编号 一", "syn/a.md"),
        Doc("doc:b", "doc", "beta record", "琮尺 复核 记录 编号 二", "syn/b.md"),
    ]
    for i in range(60):
        docs.append(
            Doc(f"doc:f{i:02d}", "doc", f"filler {i}", f"填充 复核 记录 编号{i} 位{i}", f"syn/f{i:02d}.md")
        )
    if witness_together:
        docs.append(
            Doc("doc:w", "doc", "witness", "珈码 琮尺 复核 记录 合计 见下", "syn/w.md")
        )
    return docs


def test_covered_needs_a_witness_document():
    th = Thresholds(s_covered=1.0, rare_frac=0.05)
    scattered = _synthetic(witness_together=False)
    idx = Bm25Index.build(scattered)
    env = analyze(idx, "珈码 琮尺 复核", thresholds=th)
    assert env["verdict"] == GENERIC_ONLY
    corrob = env["specificity"]["corroboration"]
    assert corrob["n_probes"] == 1 and corrob["corroborated"] is False
    assert env["generic_ledger"]["generic_probes"], "refused probes must be listed"
    assert any(p["why"] == "lone_mention" for p in env["generic_ledger"]["generic_probes"])

    together = _synthetic(witness_together=True)
    idx2 = Bm25Index.build(together)
    env2 = analyze(idx2, "珈码 琮尺 复核", thresholds=th)
    assert env2["verdict"] == COVERED
    assert env2["specificity"]["corroboration"]["doc_id"] == "doc:w"
    assert env2["specificity"]["corroboration"]["n_probes"] >= 2
    assert "generic_ledger" not in env2


def test_generic_only_keeps_the_generic_word_list():
    th = Thresholds(s_covered=1.0, rare_frac=0.05)
    idx = Bm25Index.build(_synthetic(witness_together=False))
    env = analyze(idx, "珈码 琮尺 复核", thresholds=th)
    ledger = env["generic_ledger"]
    terms = {r["term"]: r for r in ledger["all_terms"]}
    assert "复核" in terms and terms["复核"]["generic"] is True
    assert "珈码" in terms and terms["珈码"]["generic"] is False
    assert {r["term"] for r in ledger["generic_terms"]} <= {t for t, r in terms.items() if r["generic"]}
    assert all("df" in p and "best_doc" in p for p in ledger["generic_probes"])


def test_blank_is_unaffected_by_the_discipline():
    th = Thresholds(s_covered=1.0, rare_frac=0.05)
    idx = Bm25Index.build(_synthetic(witness_together=False))
    env = analyze(idx, "鹦鹉 螺线 虚词", thresholds=th)
    assert env["verdict"] == BLANK
    assert env["zero_hit_proof"]["existence_hits"] == 0


def test_partial_still_means_adjacent_work():
    th = Thresholds(s_covered=10.0, rare_frac=0.05)
    idx = Bm25Index.build(_synthetic(witness_together=False))
    env = analyze(idx, "珈码 琮尺 复核", thresholds=th)
    assert env["verdict"] == PARTIAL


# --------------------------------------------------------------------------
# bridge: the new verdict is actionable and fail-safe
# --------------------------------------------------------------------------
def test_bridge_routes_generic_only_to_narrow_claim(pool):
    docs, idx, digest = pool
    probe_set = load_false_covered(FALSE_COVERED)
    env = analyze(idx, probe_set["probes"][0]["topic"], pool_digest=digest)
    assert env["verdict"] == GENERIC_ONLY
    gate = dedup_gate(env)
    assert gate["verdict"] == GENERIC_ONLY and gate["decision"] == "narrow_claim"
    assert gate["citations"], "a generic_only gate still cites the documents it read"
    assert gate["generic_ledger"]["refused_probes"]
    assert gate["evidence_sha256"] == env["digest"]["sha256"]
    assert verify(gate)


def test_bridge_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        dedup_gate({"verdict": "probably_covered"})


# --------------------------------------------------------------------------
# C4 — determinism
# --------------------------------------------------------------------------
def test_c4_rerun_is_byte_identical(pool, bench):
    docs, idx, digest = pool
    a = analyze(idx, "珈码 琮尺 复核", pool_digest=digest, as_of="pool:x")
    b = analyze(idx, "珈码 琮尺 复核", pool_digest=digest, as_of="pool:x")
    assert dumps(a) == dumps(b)
    assert a["digest"]["sha256"] == b["digest"]["sha256"]
    assert verify(a)
    ra = replay(idx, bench, pool_digest=digest)
    rb = replay(idx, bench, pool_digest=digest)
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)


def test_c4_false_covered_replay_is_byte_identical(pool):
    docs, idx, digest = pool
    probe_set = load_false_covered(FALSE_COVERED)
    a = replay_false_covered(idx, probe_set, pool_digest=digest)
    b = replay_false_covered(idx, probe_set, pool_digest=digest)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------
# thresholds are frozen: the discipline changed the *unit* of evidence only
# --------------------------------------------------------------------------
FROZEN_THRESHOLDS = {
    "s_floor": 1.0,
    "s_covered": 10.0,
    "c_covered": 0.5,
    "min_existing_probes": 2,
    "rare_frac": 0.02,
    "window": 4,
    "stride": 2,
    "min_probe_len": 2,
    "min_latin_len": 3,
}


def test_threshold_vector_is_unchanged(pool, bench):
    assert Thresholds().as_dict() == FROZEN_THRESHOLDS
    docs, idx, digest = pool
    report = replay(idx, bench, pool_digest=digest, thresholds=Thresholds())
    assert report["thresholds"] == FROZEN_THRESHOLDS
    assert report["accuracy"] == 1.0


# --------------------------------------------------------------------------
# C5 — zero domain semantics, with a mutation control (red/green contrast)
# --------------------------------------------------------------------------
def _scan(path: str, terms) -> list:
    hits = []
    text = open(path, encoding="utf-8").read().lower()
    for term in terms:
        t = term.lower()
        found = (t in text) if re.search(r"[^\x00-\x7f]", t) else re.search(rf"\b{re.escape(t)}\b", text)
        if found:
            hits.append(term)
    return hits


def test_c5_scanned_sources_are_domain_neutral():
    spec = json.load(open(NEUTRALITY, encoding="utf-8"))
    offenders = {}
    for rel in spec["scope"]:
        path = os.path.join(REPO_ROOT, rel)
        assert os.path.isfile(path), rel
        hits = _scan(path, spec["terms"])
        if hits:
            offenders[rel] = hits
    assert offenders == {}, f"domain vocabulary leaked into code: {offenders}"


def test_c5_mutation_control_turns_the_scan_red(tmp_path):
    """Splice a listed term into a scanned file -> the scanner must fire.

    The injected term is read from the lexicon data (never written literally in
    this file), so the control cannot itself trip the scan.
    """
    spec = json.load(open(NEUTRALITY, encoding="utf-8"))
    rel = spec["scope"][0]
    src = os.path.join(REPO_ROOT, rel)
    leak = spec["terms"][0]
    mutated = tmp_path / "mutated.py"
    mutated.write_text(
        open(src, encoding="utf-8").read() + f"\nLEAK = {leak!r}\n", encoding="utf-8"
    )
    assert _scan(str(mutated), spec["terms"]) == [leak]


def test_c5_new_modules_are_inside_the_scan_scope():
    spec = json.load(open(NEUTRALITY, encoding="utf-8"))
    scope = set(spec["scope"])
    for rel in (
        "tools/whitespace_radar_core/adjudicate.py",
        "tools/whitespace_radar_core/sources.py",
        "tools/whitespace_radar_core/bench.py",
        "tools/whitespace_radar_core/bridge.py",
        "tools/whitespace_radar_false_covered.py",
        "ops/taskplane/governance/seeding_dedup.py",
        "ops/taskplane/governance/seeding_dedup_bench.py",
        "ops/taskplane/governance/seeding_dedup_pairs.py",
        "tests/test_whitespace_specificity.py",
        "tests/test_seeding_dedup_ledger.py",
    ):
        assert rel in scope, f"{rel} must be covered by the neutrality scan"