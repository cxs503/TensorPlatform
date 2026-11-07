"""评价体系外部效度校准（IN55-N07）单测。

判据的正确性靠**合成数据**证明（可控真值），真实数据的数字由 CLI 跑出；
两类证据互补，不用真实数据的不确定性去掩盖管线 bug。
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval_validity import calibrate, dataset, metrics  # noqa: E402
from tools.eval_validity.dataset import EvalRecord  # noqa: E402


# ─────────────────────────── 夹具：合成记录 ───────────────────────────

def make_records(n=400, signal=2.2, seed=7, start=0.0, span=8 * 86400.0):
    """合成「判级 → 下游 merge」数据流。

    ``signal`` 控制判级与实际质量的耦合强度：0 = 判级纯噪声（负控），
    越大越可分。时间越晚越难 merge（模拟流水线波次漂移）。
    """
    rng = random.Random(seed)
    # 判级：观测到的质量 + 噪声 → 五档 S1..S5（S1 最好）
    cuts = (0.84, 0.25, -0.25, -0.84)
    recs = []
    for i in range(n):
        t = start + span * i / n
        quality = rng.gauss(0, 1)                       # 真实工程质量
        noise = rng.gauss(0, 1.0 / max(signal, 1e-6))   # 判级噪声
        qhat = quality + noise
        rank = 1 + sum(1 for c in cuts if qhat > c)      # 1=S1 ... 5=S5
        grade = f"S{min(rank, 5)}"
        e_level = "E2" if rng.random() < 0.6 else "E1"
        p_merge = 1 / (1 + math.exp(-(2.0 * quality - 0.2 * (t / (span or 1)))))
        y = 1 if rng.random() < p_merge else 0
        recs.append(EvalRecord(
            mission_id=i, repo="TensorLBM", grade=grade, e_level=e_level,
            eval_status="confirmed", decided_at=t,
            outcome="merge" if y else "rework",
            evidence="merge_ledger", finished_at=t,
        ))
    return recs


# ─────────────────────────── 指标核 ───────────────────────────

def test_auc_perfect_and_reversed():
    assert metrics.auc_roc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert metrics.auc_roc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0


def test_auc_ties_and_single_class():
    # 全平局 → 0.5；单类 → nan（不许把缺失伪装成 0.5）
    assert metrics.auc_roc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)
    assert math.isnan(metrics.auc_roc([1, 1], [0.2, 0.9]))


def test_auc_matches_pairwise_definition():
    y = [0, 1, 0, 1, 1, 0]
    p = [0.2, 0.7, 0.4, 0.6, 0.9, 0.1]
    pos = [v for v, t in zip(p, y) if t == 1]
    neg = [v for v, t in zip(p, y) if t == 0]
    manual = sum(1.0 if a > b else 0.5 if a == b else 0.0
                 for a in pos for b in neg) / (len(pos) * len(neg))
    assert metrics.auc_roc(y, p) == pytest.approx(manual)


def test_brier_hand_computed():
    assert metrics.brier_score([1, 0], [0.9, 0.2]) == pytest.approx((0.01 + 0.04) / 2)
    assert math.isnan(metrics.brier_score([], []))


def test_calibration_bins_partition_and_rates():
    y = [1, 1, 0, 0]
    p = [0.05, 0.15, 0.75, 0.95]
    bins = metrics.calibration_bins(y, p)
    assert len(bins) == 10
    assert sum(b["n"] for b in bins) == 4                 # 分箱不重不漏
    assert bins[0]["n"] == 1 and bins[0]["emp_rate"] == 1.0
    assert bins[9]["n"] == 1 and bins[9]["emp_rate"] == 0.0
    # 空箱必须留空而不是被填成 0
    assert bins[4]["n"] == 0 and bins[4]["emp_rate"] is None


def test_ece_zero_for_constant_calibrated_predictor():
    y = [1] * 30 + [0] * 70
    bins = metrics.calibration_bins(y, [0.3] * 100)
    # 预测 0.3、实际 0.3 → ECE = 0
    assert metrics.expected_calibration_error(bins) == pytest.approx(0.0, abs=1e-12)


def test_wilson_interval_covers_rate_and_shrinks():
    lo, hi = metrics.wilson_interval(30, 100)
    assert lo < 0.30 < hi
    lo2, hi2 = metrics.wilson_interval(300, 1000)
    assert (hi2 - lo2) < (hi - lo)          # 样本越多区间越窄
    assert math.isnan(metrics.wilson_interval(0, 0)[0])


# ─────────────────────────── 时间窗 ───────────────────────────

def test_walk_forward_has_no_leakage():
    times = [float(i) for i in range(100)]
    splits = metrics.walk_forward_splits(times, n_windows=4, min_train=20, min_test=10)
    used = [s for s in splits if not s.get("skipped")]
    assert used, "应至少产出 1 个可用切分"
    for sp in used:
        assert max(times[i] for i in sp["train_idx"]) < min(times[i] for i in sp["test_idx"])
        assert not set(sp["train_idx"]) & set(sp["test_idx"])


def test_walk_forward_reports_insufficient_sample_instead_of_lying():
    splits = metrics.walk_forward_splits([float(i) for i in range(10)],
                                         n_windows=4, min_train=40, min_test=15)
    assert splits[0]["skipped"] and splits[0]["test_idx"] == []


# ─────────────────────────── 管线端到端 ───────────────────────────

def test_walk_forward_recovers_known_signal():
    recs = make_records(n=500, signal=3.0, seed=11)
    out = calibrate.walk_forward(recs, n_windows=4, min_train=100, min_test=50)
    assert out["n_pooled"] > 0
    assert out["auc"] >= 0.70, f"强信号下 AUC 应达标，实测 {out['auc']}"
    assert out["brier"] < 0.25


def test_negative_control_stays_at_chance():
    recs = make_records(n=400, signal=1e-6, seed=3)   # 判级纯噪声
    neg = calibrate.negative_control(recs, n_perm=60, seed=5,
                                     n_windows=3, min_train=100, min_test=50)
    assert abs(neg["null_mean"] - 0.5) <= 0.05
    assert neg["perm_pvalue"] > 0.05            # 观测值淹没在零分布里


def test_negative_control_detects_real_signal():
    recs = make_records(n=400, signal=3.0, seed=9)
    neg = calibrate.negative_control(recs, n_perm=60, seed=5,
                                     n_windows=3, min_train=100, min_test=50)
    assert neg["observed_auc"] >= 0.7
    assert neg["perm_pvalue"] <= 0.05           # 明显高于置换零分布


# ─────────────────────────── 通胀检测 ───────────────────────────

def _infl_records(top_share_good=0.9, merge_rate=0.2, n=100):
    n_top = int(n * top_share_good)
    recs = []
    n_merge = int(n * merge_rate)
    for i in range(n):
        recs.append(EvalRecord(
            mission_id=i, repo="TensorLBM",
            grade="S1" if i < n_top else "S2", e_level="E2",
            eval_status="confirmed", decided_at=float(i),
            outcome="merge" if i < n_merge else "rework",
            evidence="merge_ledger", finished_at=float(i),
        ))
    return recs


def test_inflation_alerts_when_high_grades_and_low_merge():
    out = calibrate.inflation_check(_infl_records(0.9, 0.2), top_grades=("S1",))
    assert out["alert"] is True
    assert out["verdict"] == "SCORE_INFLATION"
    assert out["top_grade_share"] > 0.30 and out["merge_rate"] < 0.50


def test_inflation_silent_when_merge_rate_healthy():
    out = calibrate.inflation_check(_infl_records(0.9, 0.8), top_grades=("S1",))
    assert out["alert"] is False
    assert out["verdict"] == "no_inflation_detected"


def test_inflation_silent_when_top_grade_share_low():
    out = calibrate.inflation_check(_infl_records(0.10, 0.2), top_grades=("S1",))
    assert out["alert"] is False


# ─────────────────────────── 跨仓 ───────────────────────────

def test_cross_repo_spread_and_incomplete_flag():
    recs = []
    for repo, signal in (("TensorLBM", 3.0), ("TensorDEM", 0.8)):
        for r in make_records(n=200, signal=signal, seed=hash(repo) % 1000):
            r.repo = repo
            recs.append(r)
    out = calibrate.by_repo(recs, repos=("TensorLBM", "TensorDEM"), n_windows=3,
                            min_train=60, min_test=30)
    assert out["spread"] == pytest.approx(
        abs(out["repos"]["TensorLBM"]["auc"] - out["repos"]["TensorDEM"]["auc"]))
    assert out["complete"] is True

    # 常数判级仓（全 S1）→ AUC 无定义，必须报 NOT_COMPUTABLE 且不能算一致
    const = make_records(n=120, signal=2.0, seed=21)
    for r in const:
        r.grade = "S1"
        r.repo = "ship-performance-platform"
    out2 = calibrate.by_repo(const, repos=("ship-performance-platform",))
    assert out2["repos"]["ship-performance-platform"]["status"] == \
        "NOT_COMPUTABLE_NO_GRADE_VARIANCE"
    assert out2["complete"] is False and out2["consistent"] is False


# ─────────────────────────── 数据层 ───────────────────────────

def test_read_only_guard_rejects_writes_and_multi_statement():
    dataset.assert_read_only("SELECT 1")
    dataset.assert_read_only("WITH x AS (SELECT 1) SELECT * FROM x")
    with pytest.raises(ValueError):
        dataset.assert_read_only("INSERT INTO t VALUES (1)")
    with pytest.raises(ValueError):
        dataset.assert_read_only("SELECT 1; DROP TABLE t")
    with pytest.raises(ValueError):
        dataset.assert_read_only("UPDATE t SET a = 1")


def test_pr_history_outcome_semantics():
    assert dataset.outcome_from_pr_history([{"state": "open"}, {"state": "merged"}]) == "merge"
    assert dataset.outcome_from_pr_history(
        [{"state": "merged"}, {"state": "closed"}]) == "rollback"
    assert dataset.outcome_from_pr_history([{"state": "closed"}]) == "discard"
    # 观测期：超 24h 仍未合并 → discard；观测期未到 → censored（不许当负例）
    old = [{"state": "open", "at": "2026-11-06T00:00:00+00:00"}]  # epoch 1793923200
    assert dataset.outcome_from_pr_history(old, as_of=1793923200.0 + 25 * 3600,
                                           horizon_hours=24) == "discard"
    assert dataset.outcome_from_pr_history(old, as_of=1793923200.0 + 3600,
                                           horizon_hours=24) == "censored"


def test_landing_ledger_distinguishes_post_merge_regression():
    entry_merge = {"merge_ts": 100.0, "adverse_ts": 50.0}
    entry_regress = {"merge_ts": 100.0, "adverse_ts": 200.0}
    entry_red = {"merge_ts": None, "adverse_ts": 200.0}
    entry_pending = {"merge_ts": None, "adverse_ts": None}
    assert dataset.outcome_from_landing(entry_merge)[0] == "merge"
    assert dataset.outcome_from_landing(entry_regress)[0] == "rework"   # 合并后回归
    assert dataset.outcome_from_landing(entry_red)[0] == "rework"       # 红门
    assert dataset.outcome_from_landing(entry_pending)[0] == "censored"


def test_extract_records_uses_ledger_and_labels(tmp_path):
    ledger = tmp_path / "merge_master.json"
    ledger.write_text(json.dumps({"events": [
        {"repo": "TensorLBM", "mid": "1", "action": "merged",
         "ts": "2026-11-06T01:00:00+00:00"},
        {"repo": "TensorLBM", "mid": "2", "action": "checks_hold",
         "ts": "2026-11-06T02:00:00+00:00"},
        {"repo": "TensorLBM", "mid": 3, "action": "order_hold",
         "ts": "2026-11-06T03:00:00+00:00"},
    ]}), encoding="utf-8")
    rows = [
        ["1", "TensorLBM", "S1", "E2", "confirmed", "2026-11-06 02:00:00+00",
         "succeeded", "[]", "f", "f", "f", "2026-11-06 01:00:00+00"],
        ["2", "TensorLBM", "S2", "E1", "confirmed", "2026-11-06 03:00:00+00",
         "succeeded", "[]", "f", "f", "f", "2026-11-06 02:00:00+00"],
        ["3", "TensorLBM", "S2", "E1", "confirmed", "2026-11-06 04:00:00+00",
         "succeeded", "[]", "f", "f", "f", "2026-11-06 03:00:00+00"],
    ]
    recs = dataset.extract_records(rows=rows, landing_ledgers=[ledger])
    by_id = {r.mission_id: r for r in recs}
    assert by_id[1].outcome == "merge" and by_id[1].y == 1
    assert by_id[2].outcome == "rework" and by_id[2].y == 0
    assert by_id[3].outcome == "censored" and by_id[3].y is None
    assert by_id[1].evidence == "merge_ledger"
    assert by_id[1].finished_at is not None and by_id[1].ref_time == by_id[1].finished_at


def test_ship_ledger_mapping(tmp_path):
    p = tmp_path / "ship.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [
        {"branch": "agent/m10", "result": "merged"},
        {"branch": "agent/m11", "result": "held"},
        {"branch": "agent/m12", "result": "conflict"},
        {"branch": "agent/m13"},                       # worktree_add_failed，无 result
    ]), encoding="utf-8")
    m = dataset.load_ship_ledger(p)
    assert m[10] == "merge" and m[11] == "rework" and m[12] == "rework"
    assert 13 not in m


# ─────────────────────────── 判据总装 ───────────────────────────

def test_run_calibration_returns_all_five_criteria():
    recs = make_records(n=400, signal=3.0, seed=13)
    out = calibrate.run_calibration(recs, n_windows=3, min_train=100,
                                    min_test=40, n_perm=30,
                                    repos=("TensorLBM",))
    assert set(out["criteria"]) == {
        "C1_walk_forward_auc>=0.70", "C2_brier<=0.15", "C3_inflation_alert",
        "C4_negative_control_auc~0.5", "C5_cross_repo_spread<=0.10"}
    assert out["criteria"]["C1_walk_forward_auc>=0.70"]["pass"] is True
    assert out["criteria"]["C4_negative_control_auc~0.5"]["pass"] is True
    assert out["n_labeled"] == len(recs)


def test_primary_evidence_excludes_selection_biased_source():
    """pr_gate 按成功挑样 → 必须只进敏感性，不进主判据。"""
    recs = make_records(n=200, signal=3.0, seed=17)
    for r in recs:                       # 强行把一半样本标成 pr_gate
        if r.mission_id % 2 == 0:
            r.evidence = "pr_gate"
    out = calibrate.run_calibration(recs, n_windows=3, min_train=60, min_test=30,
                                    n_perm=20, repos=("TensorLBM",))
    assert out["n_labeled"] == 100
    assert out["n_labeled_all_sources"] == 200
    assert out["sensitivity"]["by_source"]["pr_gate"]["n"] == 100
    assert out["sensitivity"]["primary_evidence"] == list(calibrate.PRIMARY_EVIDENCE)


def test_cli_writes_json_from_jsonl(tmp_path):
    recs = make_records(n=300, signal=3.0, seed=23)
    src = tmp_path / "records.jsonl"
    src.write_text("\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in recs),
                   encoding="utf-8")
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "eval_validity" / "run.py"),
         "--from-jsonl", str(src), "--out", str(out), "--no-figures",
         "--n-perm", "20", "--n-windows", "3", "--min-train", "60",
         "--min-test", "30", "--repos", "TensorLBM"],
        capture_output=True, text=True)
    assert out.exists(), proc.stderr[:500]
    payload = json.loads(out.read_text())
    assert payload["n_labeled"] == 300
    assert payload["status"] == "OK"
    assert payload["criteria"]["C1_walk_forward_auc>=0.70"]["pass"] is True
    assert payload["commands"]["rerun"].startswith("python3 tools/eval_validity/run.py")