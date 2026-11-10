"""回测面测试：指标数学 / 阈值选择 / 负控与平衡设计 / 端到端小语料 / 契约体检。

指标用手算得出的合成数据校验（不依赖任何领域数据）。
"""

from __future__ import annotations

from tools.dispatch_contract.backtest import (
    balanced_sample,
    best_threshold,
    confusion,
    evaluate,
    pr_curve,
    roc_auc,
    score_records,
)
from tools.dispatch_contract.contract_schema import load_config
from tools.dispatch_contract.lint import lint_corpus, lint_record

CFG = load_config()


def _rec(mid, label, contract, upstream=None):
    return {
        "schema": "dispatch-contract/corpus/v1",
        "node_id": str(mid), "mission_id": str(mid), "label_blocked": label,
        "label_non_delivery": label, "fail_stage": None, "gate": None,
        "declared": {"refs": [], "input_paths": [], "output_paths": ["docs/a.md"], "n_criteria": 1},
        "dispatch": {"node_id": str(mid), "contract": contract,
                     "upstream": upstream or {}, "dispatched_at": "2026-01-02T00:00:00Z"},
        "label_input_unavailable": 0 if contract else None,
        "oracle_unavailable": [],
    }


def _contract(ref="1001"):
    return {"node_id": "n", "contract_version": "dispatch-contract/v1",
            "input_contract": [{"kind": "upstream_delivery", "ref": ref, "required": True}],
            "output_contract": []}


def test_confusion_math():
    cm = confusion([1, 1, 0, 0], [1, 0, 1, 0])
    assert cm == {**cm, "tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert cm["precision"] == 0.5 and cm["recall"] == 0.5 and cm["f1"] == 0.5


def test_confusion_edge_no_prediction():
    cm = confusion([1, 0], [0, 0])
    assert cm["precision"] == 0.0 and cm["recall"] == 0.0


def test_roc_auc_perfect_and_random():
    assert roc_auc([0.9, 0.8, 0.1, 0.0], [1, 1, 0, 0]) == 1.0
    assert roc_auc([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == 0.5
    assert roc_auc([0.0, 0.0], [0, 0]) != roc_auc([0.0, 0.0], [0, 0])  # nan


def test_pr_curve_monotone_recall():
    pts = pr_curve([0.9, 0.5, 0.1], [1, 0, 1])
    recs = [p["recall"] for p in pts]
    assert recs == sorted(recs)
    assert pts[-1]["recall"] == 1.0


def test_best_threshold_picks_separating_value():
    thr = best_threshold([0.9, 0.8, 0.0, 0.0], [1, 1, 0, 0])
    assert 0.0 <= thr <= 0.8
    pred = [1 if s >= thr - 1e-12 else 0 for s in [0.9, 0.8, 0.0, 0.0]]
    assert confusion([1, 1, 0, 0], pred)["f1"] == 1.0


def test_balanced_sample_is_fifty_fifty():
    rows = [{"mission_id": str(i), "label": 1, "score": 0.5} for i in range(4)]
    rows += [{"mission_id": str(i), "label": 0, "score": 0.0} for i in range(10)]
    bal = balanced_sample(rows, seed=1)
    assert sum(r["label"] for r in bal) == 4 and len(bal) == 8
    assert balanced_sample(rows, seed=1) == bal          # 确定性


def test_score_records_marks_degraded_without_contract():
    out = score_records([_rec(1, 0, None), _rec(2, 1, _contract(),
                                                {"1001": {"found": False}})], CFG)
    assert out[0]["degraded"] is True and out[0]["score"] == 0.0
    assert out[1]["degraded"] is False and out[1]["score"] == 1.0


def test_evaluate_end_to_end_structure():
    recs = []
    for i in range(20):
        blocked = i % 2 == 0
        upstream = {"1001": {"found": False}} if blocked else \
                   {"1001": {"found": True, "status": "succeeded", "delivered_before_dispatch": True}}
        r = _rec(i, 1 if blocked else 0, _contract(), upstream)
        r["label_input_unavailable"] = 1 if blocked else 0
        recs.append(r)
    rep = evaluate(recs, CFG, shuffles=20)
    assert rep["schema"] == "dispatch-contract/backtest/v1"
    for key in ("coverage", "population", "decidable", "split", "pr_curve",
                "negative_control", "balanced_design", "tier_a_satisfiability",
                "fail_stage_profile"):
        assert key in rep
    assert rep["decidable"]["verdict_block"]["precision"] == 1.0
    assert rep["tier_a_satisfiability"]["confusion"]["recall"] == 1.0
    assert 0.0 <= rep["negative_control"]["precision_mean"] <= 1.0
    assert rep["balanced_design"]["prevalence"] == 0.5


def test_lint_flags_missing_contract_and_criteria():
    r = _rec(1, 0, None)
    r["declared"] = {"refs": [], "input_paths": [], "output_paths": [], "n_criteria": 0}
    codes = lint_record(r, CFG)
    assert {"NO_INPUT_CONTRACT", "NO_OUTPUT_CONTRACT", "NO_CRITERIA"} <= set(codes)


def test_lint_flags_orphan_input_artifact():
    c = {"node_id": "n", "contract_version": "dispatch-contract/v1",
         "input_contract": [{"kind": "artifact", "ref": "artifacts/x.json", "required": True}],
         "output_contract": []}
    r = _rec(1, 0, c, {"artifacts/x.json": {"found": False}})
    r["declared"]["input_paths"] = ["artifacts/x.json"]
    assert "ORPHAN_INPUT_ARTIFACT" in lint_record(r, CFG)


def test_lint_corpus_summary():
    res = lint_corpus([_rec(1, 0, None), _rec(2, 0, _contract(), {"1001": {"found": True}})])
    assert res["n_records"] == 2
    assert res["defect_counts"]["NO_INPUT_CONTRACT"] == 1
    assert 0.0 <= res["defect_rates"]["NO_INPUT_CONTRACT"] <= 1.0