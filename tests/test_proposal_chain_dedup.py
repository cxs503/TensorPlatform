"""跨战役语义去重器（合成数据，零领域语义）。"""

from __future__ import annotations

import pytest

from tools.proposal_chain import dedup
from tools.proposal_chain.config import load_config
from tools.proposal_chain.dedup import Doc, analyze, merge_suggestions, scan_pairs
from tools.proposal_chain.semantic import SemanticSpace


@pytest.fixture(scope="module")
def cfg():
    return load_config(
        overrides={
            "repo_whitelist": ["RepoA", "RepoB"],
            "dedup": {"dup_threshold": 0.85, "review_threshold": 0.55, "max_merge_group_size": 3},
        }
    )


def docs_fixture() -> list[Doc]:
    return [
        Doc(id="d1", text="zeta widget lattice solver anchor alpha-7 tolerance 3 percent", campaign="c1", repo="RepoA"),
        Doc(id="d2", text="zeta widget lattice solver anchor alpha-7 tolerance 3 percent", campaign="c2", repo="RepoA"),
        Doc(id="d3", text="kilo pump curve regression reference beta-9 error 5 percent", campaign="c1", repo="RepoB"),
        Doc(id="d4", text="orthogonal filler about quarterly planning vocabulary unrelated", campaign="c3", repo="RepoB"),
    ]


def test_duplicate_pair_detected_across_campaigns(cfg):
    res = analyze(docs_fixture(), cfg.raw, use_lsa=False)
    pairs = {(p.a, p.b): p for p in res.duplicate_pairs}
    assert ("d1", "d2") in pairs
    assert pairs[("d1", "d2")].cross_campaign is True
    assert pairs[("d1", "d2")].score >= 0.85


def test_non_duplicate_pair_not_flagged(cfg):
    res = analyze(docs_fixture(), cfg.raw, use_lsa=False)
    flagged = {frozenset((p.a, p.b)) for p in res.duplicate_pairs}
    assert frozenset(("d3", "d4")) not in flagged


def test_merge_suggestion_has_representative_and_members(cfg):
    res = analyze(docs_fixture(), cfg.raw, use_lsa=False)
    groups = res.duplicate_groups
    assert len(groups) == 1
    g = groups[0]
    assert set(g.members) == {"d1", "d2"}
    assert g.representative in g.members
    assert g.action == "merge"
    assert 0.85 <= g.max_similarity <= 1.0
    assert g.campaigns == ["c1", "c2"]


def test_empty_pool_returns_empty_result(cfg):
    res = analyze([], cfg.raw)
    assert res.stats["doc_count"] == 0
    assert res.pairs == [] and res.suggestions == []


def test_single_doc_and_blank_texts_are_safe(cfg):
    one = analyze([Doc(id="a", text="solo", campaign="c1")], cfg.raw, use_lsa=False)
    assert one.pairs == []
    blanks = analyze(
        [Doc(id="a", text="", campaign="c1"), Doc(id="b", text="   ", campaign="c2")],
        cfg.raw,
        use_lsa=False,
    )
    assert blanks.pairs == []
    assert blanks.stats["empty_text_docs"] == 2


def test_scan_pairs_records_shared_terms(cfg):
    docs = docs_fixture()
    sem = SemanticSpace.build([d.text for d in docs], cfg.raw)
    pairs, stats = scan_pairs(sem, docs, dup_threshold=0.85, review_threshold=0.3)
    assert stats["candidate_pairs"] == len(pairs)
    assert pairs
    shared_terms = [t for t, _ in pairs[0].shared_terms]
    assert shared_terms  # 可解释性：至少一个共享词项


def test_pair_order_is_deterministic(cfg):
    docs = docs_fixture()
    a = analyze(docs, cfg.raw, use_lsa=False)
    b = analyze(list(reversed(docs)), cfg.raw, use_lsa=False)
    key = lambda res: sorted((p.a, p.b, round(p.score, 6)) for p in res.pairs)
    assert key(a) == key(b)


def test_oversized_group_goes_to_cluster_review(cfg):
    docs = [
        Doc(id=f"d{i}", text="zeta widget lattice solver anchor alpha-7 tolerance 3 percent",
            campaign=f"c{i}", repo="RepoA")
        for i in range(5)
    ]
    sem = SemanticSpace.build([d.text for d in docs], cfg.raw)
    pairs, _ = scan_pairs(sem, docs, dup_threshold=0.85, review_threshold=0.55)
    groups = merge_suggestions(docs, pairs, sem, dup_threshold=0.85, max_merge_group_size=3)
    assert groups and groups[0].action == "cluster_review"
    assert len(groups[0].members) == 5


def test_emit_cap_limits_pair_output(cfg):
    docs = [
        Doc(id=f"d{i}", text="zeta widget lattice solver anchor alpha-7",
            campaign=f"c{i}", repo="RepoA")
        for i in range(6)
    ]
    res = analyze(docs, cfg.raw, use_lsa=False, emit_pair_cap=2)
    assert len(res.pairs) == 2
    assert res.stats["cap_hit"] is True


def test_review_band_pairs_are_separated_from_duplicates(cfg):
    docs = [
        Doc(id="a", text="zeta widget lattice solver anchor alpha-7 tolerance", campaign="c1"),
        Doc(id="b", text="zeta widget lattice solver anchor alpha-7 accuracy", campaign="c2"),
    ]
    res = analyze(docs, cfg.raw, use_lsa=False)
    for p in res.duplicate_pairs:
        assert p.verdict == "duplicate" and p.score >= 0.85
    for p in res.review_pairs:
        assert p.verdict == "near_duplicate" and p.score < 0.85