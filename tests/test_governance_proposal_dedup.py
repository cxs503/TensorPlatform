"""IN99-N10(a) 提案去重门：五特征裁决 + 关键词扫描 + 兄弟聚类 + C1 召回/误报。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.taskplane.governance import proposal_dedup as dd

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "governance" / "proposal_pairs.json"

LONG_A = ("提案去重门：对提案摘要做归一化后计算骨架相似度与词频加权余弦，超过阈值判为近重复并拒收，"
          "同批提案再做兄弟聚类保留代表，参考语料关键词命中率过高也拒绝。")
LONG_B = ("为防同族提案重复点火，先把摘要规范化，再用字符级 IDF 余弦与骨架相似度打分，越界即拦下，"
          "同一批里相似的两条只留信息量最大的一条。")


def _fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {p["id"]: p["summary"] for p in data["proposals"]}, data["pairs"]


# ─────────────────────────── C1：召回 / 误报 ───────────────────────────

def test_c1_holdout_recall_and_fpr():
    """dev 选阈值 → 冻结 → holdout 上召回 ≥0.90、误报 ≤0.10（判据 C1）。"""
    texts, pairs = _fixture()
    dev, hold = bench_split(pairs)
    cfg = dd.DedupConfig()
    idf = dd.build_idf(list(texts.values()))
    dev_scored = [(p["label"], dd.similarity(texts[p["a"]], texts[p["b"]], idf, cfg).score) for p in dev]
    dup = [s for lab, s in dev_scored if lab == "dup"]
    ind = [s for lab, s in dev_scored if lab == "indep"]
    theta = round((min(dup) + max(ind)) / 2, 3)          # 最大间隔中点（只读 dev 标签）
    assert abs(theta - cfg.theta_clone) <= 0.05, f"模块默认阈值 {cfg.theta_clone} 与 dev 选择 {theta} 偏离过大"
    scored = [(p["label"], dd.similarity(texts[p["a"]], texts[p["b"]], idf, cfg).score) for p in hold]
    recall = sum(1 for lab, s in scored if lab == "dup" and s >= cfg.theta_clone) / \
        sum(1 for lab, _ in scored if lab == "dup")
    fpr = sum(1 for lab, s in scored if lab == "indep" and s >= cfg.theta_clone) / \
        sum(1 for lab, _ in scored if lab == "indep")
    assert recall >= 0.90, f"holdout 召回 {recall:.3f} < 0.90"
    assert fpr <= 0.10, f"holdout 误报 {fpr:.3f} > 0.10"


def bench_split(pairs):
    ordered = sorted(pairs, key=lambda p: (p["a"], p["b"]))
    return ordered[0::2], ordered[1::2]


# ─────────────────────────── 个体裁决 ───────────────────────────

def test_tuning_clone_rejected_as_near_duplicate():
    clone = LONG_A.replace("超过阈值判为近重复", "阈值取 0.62 判为近重复")
    pool = [{"id": "P1", "summary": LONG_A}]
    v = dd.evaluate_proposal({"id": "P2", "summary": clone, "deliverables": ["code"]}, pool)
    assert not v.accepted
    assert v.code == "NEAR_DUPLICATE"
    assert v.best_match_id == "P1"
    assert v.score >= dd.DedupConfig().theta_clone


def test_independent_proposal_accepted():
    pool = [{"id": "P1", "summary": "缓存失效策略：按分区版本号做键前缀，版本递增即自然失效旧键，"
                                   "后台按存活时间清理，命中率与过期时间做成度量上报。"}]
    v = dd.evaluate_proposal({"id": "P2", "summary": LONG_B, "deliverables": ["code"]}, pool)
    assert v.accepted and v.code == "OK"


@pytest.mark.parametrize("proposal,code", [
    ({"id": "X", "summary": "", "deliverables": ["c"]}, "MISSING_SUMMARY"),
    ({"id": "X", "summary": "太短", "deliverables": ["c"]}, "SUMMARY_TOO_SHORT"),
    ({"id": "X", "summary": LONG_A}, "MISSING_DELIVERABLES"),
    ({"id": "X", "summary": LONG_A, "deliverables": []}, "MISSING_DELIVERABLES"),
])
def test_fail_closed_cases(proposal, code):
    v = dd.evaluate_proposal(proposal, [])
    assert not v.accepted and v.code == code


def test_keyword_collision_against_verified_reference():
    """与已验证语料关键词高度重合 → 拒（KEYWORD_COLLISION），且给出命中词。"""
    ref = {"id": "verified-42", "text": LONG_A}
    v = dd.evaluate_proposal({"id": "P9", "summary": LONG_A.replace("拒收", "拦下"),
                              "deliverables": ["code"]},
                             pool=[], references=[ref])
    assert not v.accepted and v.code == "KEYWORD_COLLISION"
    assert v.keyword_hits and all(h.source_id == "verified-42" for h in v.keyword_hits)


def test_threshold_is_tunable_via_config():
    pool = [{"id": "P1", "summary": LONG_A}]
    p = {"id": "P2", "summary": LONG_B, "deliverables": ["code"]}
    loose = dd.DedupConfig(theta_clone=0.99)      # 阈值拔高 → 放行
    strict = dd.DedupConfig(theta_clone=0.01)     # 阈值压低 → 拒
    assert dd.evaluate_proposal(p, pool, cfg=loose).accepted
    assert not dd.evaluate_proposal(p, pool, cfg=strict).accepted


def test_config_rejects_unknown_key_and_roundtrips():
    with pytest.raises(ValueError):
        dd.DedupConfig.from_dict({"theta_clone": 0.3, "nope": 1})
    cfg = dd.DedupConfig(theta_clone=0.33, keyword_top_k=8)
    assert dd.DedupConfig.from_dict(cfg.to_dict()) == cfg


# ─────────────────────────── 兄弟聚类 ───────────────────────────

def test_siblings_keep_one_representative():
    batch = [
        {"id": "A", "summary": LONG_A},
        {"id": "B", "summary": LONG_A.replace("拒收", "拒绝")},          # 近重复
        {"id": "C", "summary": "缓存失效按版本号前缀，过期时间 900 秒，后台回收，命中率上报度量。"},
    ]
    verdicts = dd.resolve_siblings(batch)
    by_id = {v.proposal_id: v for v in verdicts}
    assert by_id["A"].accepted
    assert not by_id["B"].accepted and by_id["B"].code == "NEAR_DUPLICATE_SIBLING"
    assert by_id["B"].best_match_id == "A"
    assert by_id["C"].accepted
    assert len(dd.detect_siblings(batch)) == 2


def test_sibling_threshold_tunable():
    batch = [{"id": "A", "summary": LONG_A}, {"id": "B", "summary": LONG_B}]
    assert len(dd.detect_siblings(batch, dd.DedupConfig(theta_sibling=0.99))) == 2
    assert len(dd.detect_siblings(batch, dd.DedupConfig(theta_sibling=0.01))) == 1


def test_gate_batch_report_is_serializable_and_deterministic():
    proposals = [{"id": "A", "summary": LONG_A, "deliverables": ["c"]},
                 {"id": "B", "summary": LONG_B, "deliverables": ["c"]},
                 {"id": "C", "summary": LONG_A, "deliverables": ["c"]}]
    r1 = dd.gate(proposals)
    r2 = dd.gate(proposals)
    assert r1 == r2
    assert r1["n_in"] == 3 and r1["n_accepted"] == 2 and r1["n_rejected"] == 1
    json.dumps(r1, ensure_ascii=False)                       # 可落盘
    assert [v["proposal_id"] for v in r1["verdicts"]] == ["A", "B", "C"]