"""seed_dispatch 核心层测试：日志适配（时序严格/无泄漏）+ 策略面（walk-forward/IPS/EWMA）。

域中立：全部使用 ``fixtures.make_raw`` 的合成 token（r00…, t0001…），无任何物理域词。
关键断言（每条都能被真实缺陷打红）：
* 特征只用派单时刻字段 → 事后字段扰动不影响特征矩阵；
* 时序严格 → 第 i 条记录的 repo 通过率只由 <i 条决定（改后段标签不动前段特征）；
* 标签口径 → accepted=False 的 succeeded 记 0；非终态删失；
* precision@20% / AUC 的边界与已知真值一致；
* propensity 归一 + IPS/SNIPS/ESS 与手算一致。
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from tools.seed_dispatch import fixtures as fx
from tools.seed_dispatch.log import (
    NoveltyStream,
    build_records,
    snapshot_sha256,
    summarise,
)
from tools.seed_dispatch.params import ConfigError, load_params
from tools.seed_dispatch.policy import (
    FEATURES,
    auc,
    build_slates,
    evoi,
    fit_cost_model,
    fit_logistic,
    matrix,
    offpolicy_summary,
    precision_at_frac,
    predict,
    walk_forward,
)


# --- 日志适配 ---------------------------------------------------------------

def test_records_are_time_ordered_and_features_complete():
    raw = fx.make_raw(60, seed=5)
    recs = build_records(raw)
    assert len(recs) == 60
    ts = [r.ts for r in recs]
    assert ts == sorted(ts)
    for r in recs:
        assert set(r.features) == set(FEATURES)
    assert summarise(recs)["with_label"] == 60


def test_labels_follow_acceptance_over_status():
    raw = [
        {"id": 1, "repo": "r00", "goal": "alpha beta", "created_at": "2026-01-01T00:00:00+00:00",
         "status": "succeeded", "result": {"accepted": True}},
        {"id": 2, "repo": "r00", "goal": "gamma delta", "created_at": "2026-01-01T01:00:00+00:00",
         "status": "succeeded", "result": {"accepted": False}},
        {"id": 3, "repo": "r01", "goal": "epsilon zeta", "created_at": "2026-01-01T02:00:00+00:00",
         "status": "revoked", "result": {}},
        {"id": 4, "repo": "r01", "goal": "eta theta", "created_at": "2026-01-01T03:00:00+00:00",
         "status": "failed", "result": {}},
    ]
    recs = build_records(raw)
    assert [r.label for r in recs] == [1, 0, None, 0]


def test_features_strictly_causal():
    """改后段标签不得改变前段特征（无回看 → 无未来信息）。"""
    raw = fx.make_raw(80, seed=9)
    base = build_records(raw)
    mutated = copy.deepcopy(raw)
    for row in mutated[-10:]:
        row["status"] = "failed" if row["status"] == "succeeded" else "succeeded"
        row["result"]["accepted"] = row["status"] == "succeeded"
    after = build_records(mutated)
    for a, b in zip(base[:-10], after[:-10]):
        assert a.features == b.features


def test_post_hoc_fields_do_not_touch_features():
    raw = fx.make_raw(40, seed=11)
    base = build_records(raw)
    perturbed = copy.deepcopy(raw)
    for row in perturbed:
        row["result"] = {"headline": None, "tool_calls": 0, "fail_stage": "perturb"}
        row["finished_at"] = None
    after = build_records(perturbed)
    assert [r.features for r in base] == [r.features for r in after]


def test_novelty_stream_is_bounded_and_reacts_to_repetition():
    s = NoveltyStream(pool=100, alpha=0.3)
    first, vec = s.score("alpha beta gamma")
    assert first == 1.0  # 空池 → 全新颖
    s.update(vec)
    repeat, vec2 = s.score("alpha beta gamma")
    assert 0.0 <= repeat < first
    s.update(vec2)
    other, _ = s.score("omicron lambda xi")
    assert other > repeat


def test_snapshot_hash_order_independent():
    raw = fx.make_raw(12, seed=2)
    assert snapshot_sha256(raw) == snapshot_sha256(list(reversed(raw)))


# --- 策略面 -----------------------------------------------------------------

def test_precision_at_frac_and_auc():
    labels = [1, 0, 1, 0, 1, 0]
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
    assert precision_at_frac(scores, labels, 0.5) == pytest.approx(2 / 3)
    assert precision_at_frac([-s for s in scores], labels, 0.5) == pytest.approx(1 / 3)
    assert auc([1, 0], [1.0, 0.0]) == 1.0
    assert auc([0, 1], [1.0, 0.0]) == 0.0
    assert auc([1, 0], [0.5, 0.5]) == pytest.approx(0.5)


def test_logistic_recovers_known_signal():
    raw = fx.make_raw(700, seed=21)
    recs = build_records(raw)
    X = matrix(recs)
    y = np.asarray([int(r.label) for r in recs], dtype=float)
    model = fit_logistic(X, y, ridge=0.5, epochs=600, lr=0.4)
    p = predict(model, X)
    assert auc(y.tolist(), p.tolist()) > 0.6
    assert 0.0 < float(p.min()) < float(p.max()) < 1.0


def test_cost_model_positive_and_monotone_in_budget():
    recs = build_records(fx.make_raw(300, seed=4))
    cm = fit_cost_model(recs)
    rows = np.asarray([[3.0, 6.0], [7.0, 6.0]])
    costs = cm.predict(rows)
    assert costs[0] > 0 and costs[1] > costs[0]


def test_evoi_definition_and_clip():
    p = np.asarray([0.5, 0.5])
    gain = np.asarray([0.4, 0.4])
    cost = np.asarray([0.0, 2.0])
    out = evoi(p, gain, cost)
    assert out[0] == pytest.approx(0.2)   # cost clip 到 1.0
    assert out[1] == pytest.approx(0.1)


def test_walk_forward_beats_random_on_synthetic_signal():
    recs = build_records(fx.make_raw(900, seed=31))
    wf = walk_forward(recs, windows=4, min_train=200, epochs=500, top_frac=0.2,
                      random_repeats=15, ridge=0.5)
    assert wf["evaluated_records"] > 0
    assert wf["precision_weighted"]["evoi"] > wf["precision_weighted"]["random"]
    assert wf["pooled_auc"] > 0.5
    assert len(wf["windows"]) >= 2


def test_walk_forward_rejects_insufficient_data():
    recs = build_records(fx.make_raw(50, seed=6))
    with pytest.raises(ValueError):
        walk_forward(recs, windows=4, min_train=300)


def test_slates_propensity_normalised_and_offpolicy_consistent():
    raw = fx.make_raw(200, seed=8)
    recs = build_records(raw)
    scores = [float(r.label or 0) for r in recs]  # 完美打分：目标策略 = 必然通过项
    slates = build_slates(recs, scores, hours=6, min_slate=2, explore_eps=0.05)
    assert slates
    for sl in slates[:5]:
        total = sum(sl["pi0"].values())
        assert total == pytest.approx(1.0, abs=1e-6)
    ops = offpolicy_summary(slates)
    assert ops.rounds == len(slates)
    assert 0.0 <= ops.support <= 1.0
    assert ops.ess <= ops.rounds + 1e-9
    # 完美打分：目标策略实际命中率 = 1，SNIPS（奖励全 1）必然 = 1；IPS 是无偏但高方差的估计
    assert ops.naive_value == pytest.approx(1.0)
    assert ops.snips_value == pytest.approx(1.0, abs=1e-9)
    assert ops.ips_value > 0.0
    assert ops.support <= 1.0


def test_offpolicy_empty_is_zero():
    ops = offpolicy_summary([])
    assert ops.rounds == 0 and ops.ips_value == 0.0


def test_params_reject_unknown_and_out_of_range():
    with pytest.raises(ConfigError):
        load_params({"nope": 1})
    with pytest.raises(ConfigError):
        load_params({"top_frac": 2.0})
    with pytest.raises(ConfigError):
        load_params({"windows": 1})
    p = load_params({"windows": 4, "top_frac": 0.25})
    assert p.windows == 4 and p.top_frac == 0.25