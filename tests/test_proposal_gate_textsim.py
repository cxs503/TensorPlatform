"""文本归一化 / 字符 3-gram / Jaccard 与复核带阈值的行为测试。

判据全部是「可复算的数字」：每条断言都打印/可比的具体分值。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proposal_gate_support import (  # noqa: E402
    DISTINCT_TOPICS,
    PARAPHRASE_VARIANTS,
    synth_config,
    v2_payload,
)
from tools.proposal_gate.contract import validate_proposal  # noqa: E402
from tools.proposal_gate.textsim import (  # noqa: E402
    normalize,
    shingles,
    sim_from_sets,
    similarity,
    similarity_texts,
)


@pytest.fixture(scope="module")
def cfg():
    return synth_config()


def test_nfkc_folds_fullwidth_and_case(cfg):
    assert normalize("ＦＯＯ　ＢＡＲ１２３") == "foo bar123"
    assert normalize("Foo   Bar\n\tBaz") == "foo bar baz"


def test_markup_and_punctuation_are_neutral(cfg):
    a = normalize("**foo** —— bar：契约、去重器")
    b = normalize("foo bar 契约 去重器")
    assert a == b, (a, b)


def test_shingles_are_char_ngrams_without_spaces(cfg):
    grams = shingles("foo bar", 3)
    assert "foo" in grams and "oba" in grams
    assert all(" " not in g for g in grams)
    assert shingles("fo", 3) == frozenset({"fo"})
    assert shingles("", 3) == frozenset()


def test_identity_is_one(cfg):
    text = "foo 通道三道机器门：产物契约、去重器、覆盖率账本"
    assert similarity(text, text, cfg) == pytest.approx(1.0)


def test_distinct_topics_stay_far_below_threshold(cfg):
    """precision 门：主题不同的合法提案，最高分必须远低于复核带下界。"""
    base = validate_proposal(v2_payload(), cfg).doc.blob()
    scores = []
    for spec in DISTINCT_TOPICS:
        other = validate_proposal(
            v2_payload(title=spec["title"], objective=spec["objective"], criteria=spec["criteria"]),
            cfg,
        ).doc.blob()
        scores.append(similarity(base, other, cfg))
    review_threshold = float(cfg.need("dedup.review_threshold"))
    assert max(scores) < review_threshold, scores
    # 与复核带下界至少留 0.30 的余量（可复算的精度声明）
    assert max(scores) < review_threshold - 0.30, scores


def test_heavy_paraphrase_lands_in_review_band(cfg):
    """同义改写（换词/换语序/换从句）必须被捕获：至少落进复核带，绝不静默入池。"""
    base = validate_proposal(v2_payload(), cfg).doc.blob()
    review_threshold = float(cfg.need("dedup.review_threshold"))
    dup_threshold = float(cfg.need("dedup.dup_threshold"))
    scores = []
    for variant in PARAPHRASE_VARIANTS:
        other = validate_proposal(v2_payload(title=variant, objective=variant), cfg).doc.blob()
        scores.append(similarity(base, other, cfg))
    assert min(scores) >= review_threshold, scores
    assert max(scores) < dup_threshold, scores  # 说明它们靠复核带接住，不是靠自动拒


def test_light_rewording_crosses_auto_reject_threshold(cfg):
    """轻改写（个别词替换）必须直接命中自动拒阈值。"""
    base = validate_proposal(v2_payload(), cfg).doc.blob()
    dup_threshold = float(cfg.need("dedup.dup_threshold"))
    rewrites = [
        base.replace("建立", "构建"),
        base.replace("账本", "台账"),
        base.replace("去重器", "查重组件"),
    ]
    scores = [similarity(base, r, cfg) for r in rewrites]
    assert min(scores) >= dup_threshold, scores


def test_containment_is_length_ratio_guarded(cfg):
    """短文本整体被长文本包含时，长度比闸门必须阻止包含度接管（防误判）。"""
    short = "widget 账本"
    long = ("widget 账本 " + " ".join(f"bar 段落{i} 覆盖 判据" for i in range(30))).strip()
    res = similarity_texts(short, long, cfg)
    assert res.length_ratio < float(cfg.get("dedup.min_length_ratio"))
    assert res.score == pytest.approx(res.jaccard), res.as_dict()


def test_sim_from_sets_matches_text_path(cfg):
    a, b = "foo bar 契约", "foo bar 契约与账本"
    size = int(cfg.need("dedup.shingle_size"))
    assert sim_from_sets(shingles(a, size), shingles(b, size), cfg).score == pytest.approx(
        similarity(a, b, cfg)
    )


def test_empty_text_is_zero_not_exception(cfg):
    assert similarity("", "foo bar", cfg) == 0.0
    assert similarity("", "", cfg) == 0.0