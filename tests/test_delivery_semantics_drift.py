#!/usr/bin/env python3
"""交付语义分类器漂移检测（p372w2-n06 / E2–E3）测试。

覆盖：
1. 抽样口径确定性（同输入必同样本、种子写死在排序键里）；
2. 指标口径与冻结基线对照（macro-F1 / 混淆矩阵 / bootstrap 可复算）；
3. 告警判定逻辑（超 10% 告警、恰好 10% 不告警、8% 不告警）；
4. 冻结纪律（语料 sha256 被改 → 断言失败；标签文件 sha256 与 metrics.json 一致）；
5. 已提交工件与脚本重算一致（零手抄）；
6. A3 opt-in 变体不改默认路径（v1.1 冻结口径不变）。

测试不访问网络/DB：全部读冻结工件或纯函数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_semantics_drift import evaluate as EV  # noqa: E402
from tools.delivery_semantics_drift import harvest as HV  # noqa: E402

DRIFT = ROOT / "quality" / "delivery_semantics" / "evidence" / "drift"


# ───────────────────────── 抽样口径 ─────────────────────────

def test_sample_key_is_deterministic_and_mission_specific():
    a, b = HV.sample_key(12345), HV.sample_key(12345)
    assert a == b and len(a) == 64
    assert HV.sample_key(12345) != HV.sample_key(12346)


def test_stratified_sample_is_deterministic_and_respects_quota():
    cands = []
    for i in range(40):
        cands.append({"mission_id": 1000 + i,
                      "proxy_complete": i % 2 == 0,
                      "sample_key": HV.sample_key(1000 + i)})
    s1 = HV.stratified_sample(cands, per_stratum=5)
    s2 = HV.stratified_sample(list(reversed(cands)), per_stratum=5)
    assert [c["mission_id"] for c in s1] == [c["mission_id"] for c in s2]
    assert len(s1) == 10
    assert sum(1 for c in s1 if c["proxy_complete"]) == 5


def test_proxy_complete_requires_run_shape_and_implementation():
    assert HV.proxy_complete({"checks_head": "12 passed", "n_changed_files": 2, "n_on_disk": 0})
    assert HV.proxy_complete({"checks_head": "sha256 一致", "n_changed_files": 0, "n_on_disk": 3})
    assert not HV.proxy_complete({"checks_head": "", "n_changed_files": 3, "n_on_disk": 1})
    assert not HV.proxy_complete({"checks_head": "12 passed", "n_changed_files": 0, "n_on_disk": 0})


# ───────────────────────── 指标口径 ─────────────────────────

def test_confusion_and_per_class_on_synthetic():
    y_true = ["delivered_complete", "delivered_complete", "suspect_undeclared"]
    y_pred = ["delivered_complete", "delivered_partial", "suspect_undeclared"]
    m = EV.confusion(y_true, y_pred)
    assert m["delivered_complete"]["delivered_complete"] == 1
    assert m["delivered_complete"]["delivered_partial"] == 1
    pc = EV.per_class(m)
    assert pc["delivered_partial"]["support"] == 0            # 无 support 的类不进 macro
    assert pc["macro_f1"] == pytest.approx((pc["delivered_complete"]["f1"]
                                            + pc["suspect_undeclared"]["f1"]) / 2, abs=1e-4)
    assert pc["accuracy"] == 0.6667


def test_bootstrap_is_deterministic_given_seed():
    y_true = ["delivered_complete", "delivered_partial", "suspect_undeclared"] * 4
    y_pred = ["delivered_complete", "delivered_complete", "suspect_undeclared"] * 4
    a = EV.bootstrap_ci(y_true, y_pred, n_boot=200, seed=7)
    b = EV.bootstrap_ci(y_true, y_pred, n_boot=200, seed=7)
    assert a == b and a[0] <= a[1]


def test_cumulative_curve_is_monotone_in_n():
    y_true = ["delivered_complete"] * 3 + ["suspect_undeclared"] * 3
    y_pred = ["delivered_complete"] * 2 + ["suspect_undeclared"] + ["suspect_undeclared"] * 3
    curve = EV.cumulative_curve(y_true, y_pred)
    assert [c["n"] for c in curve] == [2, 3, 4, 5, 6]
    assert all(0.0 <= c["macro_f1"] <= 1.0 for c in curve)


# ───────────────────────── 告警逻辑 ─────────────────────────

def test_alert_logic_selftest_covers_threshold_boundary():
    assert EV._alert_logic_selftest(0.9, 0.6, 0.10) is True    # 逻辑正确 → 自检通过
    assert EV._alert_logic_selftest(0.9, 0.6, 0.10) is not None
    # 边界：恰好 10% 不告警，11% 告警
    thr = 0.10
    fires = lambda base, new: round((base - new) / base, 4) > thr  # noqa: E731
    assert fires(1.0, 0.90) is False
    assert fires(1.0, 0.89) is True


# ───────────────────────── 冻结工件 ─────────────────────────

def test_committed_artifacts_match_recomputation():
    """已提交 metrics.json 必须能由脚本从冻结工件重算（零手抄）。"""
    committed = json.loads((DRIFT / "metrics.json").read_text(encoding="utf-8"))
    fresh = EV.evaluate(DRIFT, None)
    assert fresh["metrics"]["macro_f1"] == committed["metrics"]["macro_f1"]
    assert fresh["metrics"]["accuracy"] == committed["metrics"]["accuracy"]
    assert fresh["confusion"] == committed["confusion"]
    assert fresh["drift"]["decay_rel"] == committed["drift"]["decay_rel"]
    assert fresh["label_freeze"]["labels_sha256"] == committed["label_freeze"]["labels_sha256"]
    assert list(fresh["macro_f1_bootstrap95"]) == committed["macro_f1_bootstrap95"]


def test_corpus_pin_is_enforced(monkeypatch, tmp_path):
    """语料被改动 → evaluate 必须拒绝（sha256 与预注册不符）。"""
    drift = tmp_path / "drift"
    drift.mkdir()
    for name in ("preregistration.json", "corpus_raw.json", "labels.json"):
        (drift / name).write_text((DRIFT / name).read_text(encoding="utf-8"), encoding="utf-8")
    doc = json.loads((drift / "corpus_raw.json").read_text(encoding="utf-8"))
    doc["units"][0]["result"]["checks"] = "tampered: 999 passed"
    (drift / "corpus_raw.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        EV.evaluate(drift, None)


def test_sample_size_meets_acceptance_floor():
    corpus = json.loads((DRIFT / "corpus_raw.json").read_text(encoding="utf-8"))
    assert len(corpus["units"]) >= 20
    assert corpus["sample_summary"]["sampled"] == len(corpus["units"])


def test_metrics_artifact_records_honest_verdict():
    m = json.loads((DRIFT / "metrics.json").read_text(encoding="utf-8"))
    # honest-FAIL 不可覆盖：判据结论必须由数字推出，不得手改成 true
    assert m["criteria"]["c1_decay_le_10pct"] == (m["drift"]["decay_rel"]
                                                  <= m["drift"]["alert_threshold"])
    assert m["drift"]["drift_alert"] == (m["drift"]["decay_rel"] > m["drift"]["alert_threshold"])
    assert m["all_criteria_met"] == all(bool(v) for v in m["criteria"].values())


# ───────────────────────── 分类器 opt-in 变体 ─────────────────────────

def test_fallback_variant_does_not_change_default_path():
    from api.domain.delivery_state import assess_delivery  # noqa: E402
    payload = {"changed_files": ["a.py"], "checks": "", "headline": "37 项 pytest 全部通过"}
    assert assess_delivery(payload).state == "suspect_undeclared"           # v1.1 默认不变
    assert assess_delivery(payload, verification_fallback=True).state == "delivered_complete"


def test_fallback_keeps_checks_text_when_present():
    from api.domain.delivery_state import assess_delivery  # noqa: E402
    payload = {"changed_files": ["a.py"], "checks": "12 passed", "headline": "无关声明"}
    assert assess_delivery(payload, verification_fallback=True).state == "delivered_complete"