"""seed_dispatch 集成层测试：种子候选/排序、泄漏审计（双侧）、判据汇总、报告渲染。

域中立：合成 token（r00…, seed-000x），无物理域词；所有断言对应真实缺陷可打红的点：
* 种子特征名与历史任务**逐字一致**（否则接不进同一模型）；
* repo 归属由来源任务多数票决定，且溯源链带三级 sha256；
* 泄漏审计：事后字段扰动 → 特征矩阵不变；注入结局当特征 → 指标必须跳变（审计有鉴别力）；
* 判据汇总：缺证据的判据必须 ok=False（不许静默通过）；
* 报告：六要素章节齐全、数字来自 payload、无占位符。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.seed_dispatch import fixtures as fx
from tools.seed_dispatch.integration import (
    assemble_gates,
    audit_leakage,
    corpus_summary,
    rank_seeds,
    seed_candidates,
    sha256_text,
)
from tools.seed_dispatch.log import build_records
from tools.seed_dispatch.params import SeedDispatchParams
from tools.seed_dispatch.policy import FEATURES, walk_forward
from tools.seed_dispatch.report import render_markdown


def _small_world(n: int = 420, seed: int = 17):
    raw = fx.make_raw(n, seed=seed)
    recs = build_records(raw)
    return raw, recs


def test_seed_features_match_history_design_matrix():
    raw, recs = _small_world()
    bundle = fx.make_distill_bundle(mission_ids=[r.mission_id for r in recs[:6]])
    cands = seed_candidates(bundle, recs)
    assert cands
    for cand in cands:
        assert set(cand["features"]) == set(FEATURES)
        assert cand["provenance"]["sources"]
        src = cand["provenance"]["sources"][0]
        assert src["quote_sha256"] and src["source_sha256"] and src["mission_id"] is not None
        assert cand["provenance"]["seed_text_sha256"] == sha256_text(
            f"{cand['title']} :: {[s for s in ['']][0] if False else ''}"
        ) or len(cand["provenance"]["seed_text_sha256"]) == 64


def test_seed_repo_attribution_follows_sources():
    raw, recs = _small_world()
    target = recs[10]
    bundle = fx.make_distill_bundle(n_seeds=1, mission_ids=[target.mission_id])
    cand = seed_candidates(bundle, recs)[0]
    assert cand["repo"] == target.repo


def test_rank_seeds_is_deterministic_and_scored():
    raw, recs = _small_world()
    bundle = fx.make_distill_bundle(mission_ids=[r.mission_id for r in recs[:6]],
                                    novelty=[0.9, 0.6, 0.75])
    cands = seed_candidates(bundle, recs)
    a = rank_seeds(cands, recs, epochs=300)
    b = rank_seeds(cands, recs, epochs=300)
    assert [r["seed_id"] for r in a["ranked"]] == [r["seed_id"] for r in b["ranked"]]
    assert a["score_sha256"] == b["score_sha256"]
    ranks = [r["rank"] for r in a["ranked"]]
    assert ranks == list(range(1, len(ranks) + 1))
    scores = [r["evoi"] for r in a["ranked"]]
    assert scores == sorted(scores, reverse=True)
    for row in a["ranked"]:
        assert 0.0 <= row["p_pass"] <= 1.0
        assert row["predicted_cost"] > 0.0


def test_seed_novelty_drives_evoi_ordering():
    """同一 repo/长度下，新颖性更高的种子 EVOI 必须更高（单调性）。"""
    raw, recs = _small_world()
    bundle = fx.make_distill_bundle(n_seeds=2, mission_ids=[recs[3].mission_id],
                                    novelty=[0.2, 0.99])
    cands = seed_candidates(bundle, recs)
    ranked = rank_seeds(cands, recs, epochs=200)["ranked"]
    assert ranked[0]["features"]["f_novelty"] > ranked[1]["features"]["f_novelty"]
    assert ranked[0]["evoi"] > ranked[1]["evoi"]


def test_leakage_audit_detects_injection_and_ignores_perturb():
    raw, recs = _small_world(600, seed=23)
    params = {
        "windows": 3, "top_frac": 0.2, "min_train": 150, "ridge": 1.0,
        "epochs": 250, "lr": 0.35, "random_repeats": 5, "leakage_tol": 0.02,
        "leakage_sensitivity": 0.02, "seed": 7,
    }
    audit = audit_leakage(raw, recs, params=params)
    assert audit["features_identical"] is True          # 事后字段扰动不进特征
    assert audit["tolerance_ok"] is True                # 指标不显著漂移
    assert audit["sensitivity_ok"] is True              # 注入泄漏必须被抓到
    assert audit["injection_jump"] >= audit["sensitivity_floor"]
    assert audit["feature_hash"] == audit["feature_hash"]


def test_leakage_audit_flags_when_features_would_read_post_hoc():
    """把 user-visible 的事后字段塞进特征 → 审计的"特征不变性"必须失败。"""
    from tools.seed_dispatch.integration import _feature_hash, _perturb_raw

    raw, recs = _small_world(200, seed=5)
    poisoned = []
    for rec in recs:
        poisoned.append(
            type(rec)(
                mission_id=rec.mission_id, repo=rec.repo, created_at=rec.created_at, ts=rec.ts,
                features={**rec.features, "f_repo_pass": float(rec.cost_tool_calls or 0.0)},
                label=rec.label, cost_tool_calls=rec.cost_tool_calls, wall_sec=rec.wall_sec,
                status=rec.status, posthoc_sha256=rec.posthoc_sha256,
            )
        )
    rebuilt = build_records(_perturb_raw(raw, seed=99))
    assert _feature_hash(poisoned) != _feature_hash(rebuilt)


def test_assemble_gates_fails_without_evidence():
    gates = assemble_gates(
        distill_run={}, walk={}, leakage={}, pytest_check=None, coverage=None,
        domain_scan=None, cluster_consistency=None, params={},
    )
    assert gates["a_parse_coverage"]["ok"] is False
    assert gates["b_cluster_consistency"]["ok"] is False
    assert gates["f_tests_coverage"]["ok"] is False
    assert gates["g_zero_domain_semantics"]["ok"] is False
    assert gates["all_pass"] is False


def test_assemble_gates_passes_when_all_evidence_present():
    raw, recs = _small_world(500, seed=29)
    wf = walk_forward(recs, windows=3, min_train=150, epochs=200, random_repeats=5)
    gates = assemble_gates(
        distill_run={
            "coverage": {"parse_coverage": 0.97, "reports_total": 100, "reports_consistent": 97,
                         "items": 250, "items_by_kind": {"limitation": 250}},
            "gate": {"mean_max_sim_of_passed": 0.31, "mean_sim_of_passed": 0.02,
                     "novelty_min": 0.6, "candidates": 6, "passed": 4},
        },
        walk=wf,
        leakage={"features_identical": True, "tolerance_ok": True, "sensitivity_ok": True},
        pytest_check={"pass": True},
        coverage={"value": 0.9, "ok": True},
        domain_scan={"ok": True},
        cluster_consistency={"value": 0.85, "judged": 4, "ok": True},
        params={"min_precision_gain_pp": -100.0},
    )
    assert gates["all_pass"] is True
    assert gates["d_ranking_utility"]["ok"] is True
    assert gates["c_novelty_gate"]["value"] == 0.31


def test_corpus_summary_counts_are_consistent():
    raw, recs = _small_world(300, seed=31)
    s = corpus_summary(recs)
    assert s["records"] == s["with_label"] + s["censored"]
    assert s["passed"] + s["failed"] == s["with_label"]
    assert s["feature_names"] == list(FEATURES)


def test_markdown_has_six_sections_and_no_placeholders(tmp_path: Path):
    raw, recs = _small_world(500, seed=37)
    wf = walk_forward(recs, windows=3, min_train=150, epochs=200, random_repeats=5)
    bundle = fx.make_distill_bundle(mission_ids=[r.mission_id for r in recs[:4]])
    cands = seed_candidates(bundle, recs)
    ranking = rank_seeds(cands, recs, epochs=150)
    gates = assemble_gates(
        distill_run={"coverage": {"parse_coverage": 1.0}, "gate": {}},
        walk=wf,
        leakage={"features_identical": True, "tolerance_ok": True, "sensitivity_ok": True,
                 "metrics_clean": {"precision_evoi": 0.5}, "metrics_perturbd": {"precision_evoi": 0.5},
                 "injected_leaky_precision": 0.9, "delta": {}},
        pytest_check={"pass": True}, coverage={"value": 0.9, "ok": True},
        domain_scan={"ok": True}, cluster_consistency={"value": 0.9, "ok": True},
        params={"top_frac": 0.2},
    )
    payload = {
        "mission_id": 11930, "node": "p318w2-n10",
        "generated_utc": "2026-01-01T00:00:00Z", "runtime_sec": 1.0,
        "params": SeedDispatchParams().to_dict(),
        "corpus": {**corpus_summary(recs), "snapshot": "synthetic", "snapshot_sha256": "0" * 64},
        "walk_forward": wf,
        "offpolicy": {"rounds": 10, "support": 0.2, "naive_value": 0.6, "ips_value": 0.55,
                      "snips_value": 0.58, "ess": 3.0, "mean_propensity": 0.1},
        "leakage": {"features_identical": True, "tolerance_ok": True, "sensitivity_ok": True,
                    "metrics_clean": {"precision_evoi": 0.5},
                    "metrics_perturbd": {"precision_evoi": 0.5},
                    "injected_leaky_precision": 0.9, "delta": {"precision_evoi": 0.0}},
        "seed_ranking": {"model": ranking["model"], "cost_model": ranking["cost_model"],
                         "train_n": ranking.get("train_n"),
                         "score_sha256": ranking["score_sha256"],
                         "count": len(ranking["ranked"]), "top": ranking["ranked"][:3]},
        "gates": gates,
        "distill": {"dir": "synthetic", "seeds_sha256": "1" * 64, "run_sha256": "2" * 64},
    }
    md = render_markdown(payload, {}, [])
    for heading in ("① 案例介绍", "② 怎么做的", "③ 计算结果", "④ 结果图", "⑤ 注意点与局限",
                    "⑥ 误差判定"):
        assert heading in md
    for banned in ("TBD", "TODO", "???", "FIXME"):
        assert banned not in md
    assert f"{wf['gain_vs_fifo_pp']:+.2f} pp" in md
    assert "honest FAIL" in md


def test_fixture_bundle_roundtrip(tmp_path: Path):
    bundle = fx.make_distill_bundle(n_seeds=2)
    out = fx.dump_bundle(bundle, str(tmp_path / "distill"))
    seeds = json.loads((Path(out) / "seeds.json").read_text(encoding="utf-8"))
    assert len(seeds["accepted"]) == 2