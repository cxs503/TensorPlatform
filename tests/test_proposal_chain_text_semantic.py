"""文本层与语义空间（合成数据，零领域语义）。"""

from __future__ import annotations

import math

import pytest

from tools.proposal_chain.config import load_config
from tools.proposal_chain.semantic import SemanticSpace, idf_top_terms
from tools.proposal_chain.text import TfidfSpace, build_space, normalize, tokens

SYNTH_DOCS = [
    "zeta widget lattice solver: benchmark anchor alpha-7 with tolerance < 3%",
    "zeta widget lattice solver benchmark: anchor alpha-7 tolerance < 3% (reworded)",
    "kilo pump curve regression: reference beta-9 with error < 5%",
    "orthogonal filler text about quarterly planning and unrelated vocabulary",
]


@pytest.fixture(scope="module")
def cfg():
    return load_config(overrides={"repo_whitelist": ["RepoA", "RepoB"]})


def test_normalize_collapses_markup_and_case():
    raw = "**AAA**   `bbb` ##  CCC!!!"
    out = normalize(raw, ("**", "`", "##"))
    assert out == "aaa bbb ccc"
    assert "  " not in out


def test_tokens_latin_min_length_and_cjk_bigram():
    toks = tokens("ab abc 网格模型", min_latin=2)
    assert "ab" in toks and "abc" in toks
    assert "网格" in toks and "格模" in toks
    assert "网" not in toks  # 单字默认丢弃


def test_tokens_cjk_unigram_flag_keeps_single_chars():
    toks = tokens("网格模型", cjk_unigram=True)
    assert "网" in toks and "网格" in toks


def test_tfidf_vectors_are_l2_normalized():
    space = build_space(SYNTH_DOCS, {"text": {}, "tfidf": {}})
    for vec in space.vectors:
        norm = math.sqrt(sum(v * v for v in vec.values()))
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_tfidf_cosine_bounds_and_self_similarity():
    space = build_space(SYNTH_DOCS, {"text": {}, "tfidf": {}})
    assert space.sim(0, 1) > space.sim(0, 2)
    for i in range(len(SYNTH_DOCS)):
        assert space.sim(i, i) == pytest.approx(1.0, abs=1e-9)
        assert 0.0 <= space.sim(i, 2) <= 1.0


def test_empty_corpus_builds_empty_space_without_raising():
    space = build_space([], {"text": {}, "tfidf": {}})
    assert space.n_docs == 0 and space.vocab == {}
    assert TfidfSpace.cosine({}, {}) == 0.0
    assert space.vectorize("anything") == {}


def test_high_df_terms_filtered_as_generic():
    docs = ["alpha beta", "alpha gamma", "alpha delta", "alpha epsilon"]
    space = build_space(docs, {"text": {}, "tfidf": {"max_df_ratio": 0.5}})
    assert "alpha" not in space.vocab  # df=4/4 > 0.5 → 通用词剔除
    assert "beta" in space.vocab


def test_idf_top_terms_is_deterministic():
    space = build_space(SYNTH_DOCS, {"text": {}, "tfidf": {}})
    first = idf_top_terms(space, 3)
    second = idf_top_terms(space, 3)
    assert first == second
    assert all(isinstance(t, str) for t, _ in first)


def test_semantic_space_uses_lsa_backend_when_enabled(cfg):
    sem = SemanticSpace.build(SYNTH_DOCS, cfg.raw)
    assert sem.backend == "lsa"
    assert sem.dim > 0
    assert sem.stats()["docs"] == len(SYNTH_DOCS)


def test_semantic_space_is_deterministic_for_fixed_seed(cfg):
    a = SemanticSpace.build(SYNTH_DOCS, cfg.raw, dim=5, seed=7)
    b = SemanticSpace.build(SYNTH_DOCS, cfg.raw, dim=5, seed=7)
    assert (a.embedding == b.embedding).all()


def test_semantic_space_falls_back_to_tfidf_when_disabled(cfg):
    raw = {**cfg.raw, "lsa": {**cfg.raw.get("lsa", {}), "enabled": False}}
    sem = SemanticSpace.build(SYNTH_DOCS, raw)
    assert sem.backend == "tfidf"
    assert "lsa_disabled_by_config" in sem.notes


def test_semantic_space_empty_corpus_is_safe(cfg):
    sem = SemanticSpace.build([], cfg.raw)
    assert sem.n_docs == 0
    assert sem.sim_matrix_pairs(0.5) == []
    assert any("empty_corpus" in n for n in sem.notes)