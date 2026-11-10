"""派发前可行性先验校准器 —— pytest（判据验的是管线正确性，不是数据运气）。

覆盖
----
1. **PAV/保序**：单调性 + 与独立二次规划解（``scipy.optimize.minimize`` SLSQP）同解。
2. **Platt**：可逆的仿射变形必须被学回来（斜率/截距恢复），且概率校准严格变好。
3. **度量**：Brier/AUC/ECE 在可手算的例子上取值正确；并列秩处理；ECE 空箱不留假 0。
4. **逻辑回归**：凸问题最优性（梯度≈0）、系数恢复方向。
5. **特征因果性**：前缀算特征 == 全集算特征再切前缀（防泄漏）；新颖度批量实现与在线
   单点实现同值；判据结构解析对三种形态（字典/字符串/嵌套）都成立。
6. **时间切分**：三窗计划不相交且时间有序；每窗两类别都有。
7. **端到端**：合成数据（真实生成过程为 logit 线性）上 BSS>0、ECE 校准后下降、
   时间留出无泄漏；**shuffle 负控**把 AUC 打回 0.5 附近、BSS≤0。
8. **CLI 幂等**：同输入跑两次产物逐字节相同。
9. **派发前路径**：``predict`` 只吃历史 + 规格，且概率随判据变硬而下降（方向性检查）。
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tools.pre_dispatch_prior import calibrate as cal
from tools.pre_dispatch_prior import backtest as bt
from tools.pre_dispatch_prior import dataset as ds
from tools.pre_dispatch_prior import features as ft
from tools.pre_dispatch_prior import report as rp


# ─────────────────────────── 合成数据工具 ───────────────────────────

def _ts(day: int, minute: int = 0) -> str:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (base + timedelta(days=day, minutes=minute)).isoformat()


def _synthetic_nodes(n_plans: int = 40, per_plan: int = 8, seed: int = 7,
                     shift_at: int = 26) -> list[ds.RawNode]:
    """合成战役：判据越硬（数值阈值多）、依赖越深、repo 越生 → 越容易失败。

    后期计划（plan >= shift_at）成功率整体下降 → 制造先验漂移，考校准的漂移修正。
    """
    rng = np.random.default_rng(seed)
    nodes: list[ds.RawNode] = []
    nid = 1
    for p in range(n_plans):
        plan_start = _ts(p)
        for k in range(per_plan):
            n_crit = int(rng.integers(1, 5))
            hard = float(rng.uniform(0, 1))
            first_in_plan = nid - k
            deps = (tuple(range(max(first_in_plan, nid - 2), nid))
                    if k >= 2 else ())          # 只在本计划内成链（深度 ≤ 2）
            # 生成过程：判据越硬（数值阈值占比高）、依赖越深 → 越可能验收失败
            base_logit = 0.6 - 1.8 * hard - 0.4 * (1.0 if deps else 0.0)
            if p >= shift_at:
                base_logit -= 1.5          # 后期波次更难
            y = int(rng.uniform() < 1.0 / (1.0 + math.exp(-base_logit)))
            crit = []
            for c in range(n_crit):
                if c < int(round(hard * n_crit)):
                    crit.append({"metric": f"m{c}", "operator": "<", "threshold": 0.05,
                                 "unit": "ratio"})
                else:
                    crit.append({"metric": f"gate{c}", "operator": "==",
                                 "threshold": "PASS", "unit": "bool"})
            nodes.append(ds.RawNode(
                node_id=nid, plan_id=p + 1,
                created_at=_ts(p, k * 10), plan_created_at=plan_start,
                status="done" if y else "blocked",
                node_type="gate" if k == 0 else "task",
                text=f"node {nid} plan {p} crit{n_crit} hard{int(hard * 10)}",
                dependencies=deps,
                metadata={"acceptance_criteria": crit, "risk": "high" if hard > 0.6 else "low",
                          "repo": f"Repo{p % 4}", "goal": f"goal {p}-{k}",
                          "_block_reason": "" if y else "mission failed x2"},
            ))
            nid += 1
    return nodes


@pytest.fixture(scope="module")
def synthetic():
    nodes = _synthetic_nodes()
    fs = ft.extract_features(nodes)
    return nodes, fs


# ─────────────────────────── 1. 保序回归 ───────────────────────────

def _isotonic_reference(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """独立参考解：在保序约束下最小化平方误差（scipy SLSQP）。"""
    from scipy.optimize import minimize
    n = len(y)

    def obj(g):
        return float(np.sum((y - g) ** 2))

    cons = [{"type": "ineq", "fun": (lambda g, i=i: g[i + 1] - g[i])} for i in range(n - 1)]
    res = minimize(obj, y.copy(), constraints=cons, method="SLSQP",
                   options={"ftol": 1e-12, "maxiter": 500})
    return res.x


def test_pav_is_monotone_and_matches_qp():
    rng = np.random.default_rng(0)
    y = rng.uniform(size=12)
    fitted = cal._pav(y, np.ones(len(y)))
    assert np.all(np.diff(fitted) >= -1e-12), "PAV 输出必须单调非降"
    ref = _isotonic_reference(np.arange(len(y), dtype=float), y)
    assert np.allclose(fitted, ref, atol=2e-4), "PAV 与独立 QP 解必须同解"


def test_isotonic_calibrator_shape_and_monotone():
    rng = np.random.default_rng(1)
    s = rng.normal(size=400)
    y = (rng.uniform(size=400) < cal.sigmoid(1.5 * s)).astype(float)
    iso = cal.IsotonicCalibrator().fit(s, y)
    assert iso.is_monotone()
    grid = np.linspace(-3, 3, 61)
    pred = iso.predict(grid)
    assert np.all(np.diff(pred) >= -1e-12)
    assert pred.min() > 0 and pred.max() < 1
    # 两端 clip：极值外推不越界
    assert iso.predict(np.array([-50.0, 50.0])).tolist() == [pred[0], pred[-1]]


# ─────────────────────────── 2. Platt ───────────────────────────

def test_platt_recovers_affine_distortion():
    rng = np.random.default_rng(2)
    z = rng.normal(size=4000)
    p_true = cal.sigmoid(0.0 + 1.2 * z)
    y = (rng.uniform(size=4000) < p_true).astype(float)
    # 原始分数被压扁：s = 0.25 z → 需要 a≈4.8 才能还原
    s = 0.25 * z
    platt = cal.PlattCalibrator(target_smoothing=False).fit(s, y)
    assert 4.0 < platt.a < 5.6, f"斜率恢复失败 a={platt.a}"
    assert abs(platt.b) < 0.35, f"截距恢复失败 b={platt.b}"
    assert cal.brier(platt.predict(s), y) < cal.brier(cal.sigmoid(s), y)


def test_platt_keeps_ranking_for_positive_slope():
    rng = np.random.default_rng(3)
    s = rng.normal(size=200)
    y = (rng.uniform(size=200) < cal.sigmoid(s)).astype(float)
    pl = cal.PlattCalibrator().fit(s, y)
    assert pl.a > 0
    assert cal.auc(pl.predict(s), y) == pytest.approx(cal.auc(s, y), abs=1e-12)


# ─────────────────────────── 3. 度量 ───────────────────────────

def test_metrics_hand_computable():
    p = np.array([0.0, 0.5, 1.0])
    y = np.array([0.0, 1.0, 1.0])
    assert cal.brier(p, y) == pytest.approx((0 + 0.25 + 0) / 3)
    # AUC：正例 {0.5,1.0} 负例 {0.0} → 两个正例都排在负例之上
    assert cal.auc(p, y) == pytest.approx(1.0)
    # 完全反序 → 0
    assert cal.auc(1 - p, y) == pytest.approx(0.0)
    # 并列秩：全部同分 → 0.5
    assert cal.auc(np.full(4, 0.3), np.array([0, 1, 1, 0])) == pytest.approx(0.5)


def test_brier_skill_reference_semantics():
    y = np.array([1.0, 1.0, 1.0, 0.0])
    prior = float(y.mean())
    assert cal.brier(np.full(4, prior), y) == pytest.approx(
        cal.brier(np.full(4, prior), y))
    assert cal.brier_skill(np.full(4, prior), y) == pytest.approx(0.0, abs=1e-12)
    assert cal.brier_skill(np.full(4, 1.0), y) < 0


def test_ece_drops_empty_bins():
    p = np.array([0.91, 0.92, 0.93, 0.94])
    y = np.array([1.0, 1.0, 0.0, 1.0])
    out = cal.ece(p, y, n_bins=10)
    assert out["n_bins_used"] == 1, "空箱必须剔除，不能写成 0 制造假 ECE"
    assert out["ece"] == pytest.approx(abs(0.75 - p.mean()))


def test_auc_nan_for_single_class():
    assert math.isnan(cal.auc(np.array([0.1, 0.2]), np.array([1.0, 1.0])))


# ─────────────────────────── 4. 逻辑回归 ───────────────────────────

def test_logistic_satisfies_first_order_optimality():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(500, 3))
    y = (rng.uniform(size=500) < cal.sigmoid(X @ np.array([1.0, -0.5, 0.2]))).astype(float)
    m = cal.LogisticModel(l2=0.5).fit(X, y)
    p = cal.sigmoid(X @ m.coef + m.intercept)
    grad = X.T @ (p - y) + 0.5 * m.coef
    assert np.max(np.abs(grad)) < 1e-4, "凸问题最优时正则化梯度应≈0"
    assert m.coef[0] > 0 > m.coef[1], "系数符号应与真值一致"


# ─────────────────────────── 5. 特征 ───────────────────────────

def test_features_are_causal():
    nodes = _synthetic_nodes(n_plans=16, per_plan=6)
    ordered = sorted(nodes, key=lambda n: n.sort_key)
    full = ft.extract_features(ordered)
    for k in (7, 30, len(ordered) - 5):
        prefix = ft.extract_features(ordered[:k])
        assert prefix.rows == full.rows[:k], f"前 {k} 个节点的特征与全集不一致（泄漏）"


def test_novelty_batch_matches_online():
    nodes = _synthetic_nodes(n_plans=10, per_plan=5)
    ordered = sorted(nodes, key=lambda n: n.sort_key)
    batch = ft.novelty_batch([n.text for n in ordered])
    state = ft.HistoryState()
    online = []
    for n in ordered:
        online.append(state.features_for(n)[ft.FEATURE_NAMES.index("novelty")])
        state.observe(n)
    assert np.allclose(batch, online, atol=1e-12)


def test_criteria_parsing_three_shapes():
    f1 = ft.criteria_features({"acceptance_criteria": [
        {"metric": "a", "operator": "<", "threshold": 0.1},
        {"metric": "b", "operator": "==", "threshold": "PASS", "unit": "bool"}]})
    assert f1["criteria_n_log"] == pytest.approx(math.log1p(2))
    assert f1["criteria_numeric_share"] == pytest.approx(0.5)
    assert f1["criteria_bool_share"] == pytest.approx(0.5)
    f2 = ft.criteria_features({"acceptance_criteria": ["误差 < 5%", "曲线单调"]})
    assert f2["criteria_numeric_share"] == pytest.approx(0.5)
    assert f2["criteria_structured_share"] == 0.0
    f3 = ft.criteria_features({"acceptance_criteria": {"metric": "c", "operator": "<=",
                                                       "threshold": 1.0}})
    assert f3["has_criteria"] == 1.0 and f3["criteria_numeric_share"] == 1.0
    assert ft.criteria_features({})["has_criteria"] == 0.0


def test_labels_exclude_censored_and_infra_blocks():
    def mk(status, reason=""):
        return ds.RawNode(node_id=1, plan_id=1, created_at=_ts(0), plan_created_at=_ts(0),
                          status=status, node_type="task", text="x", dependencies=(),
                          metadata={"_block_reason": reason})
    assert ds.label_primary(mk("done")) == 1
    assert ds.label_primary(mk("blocked")) == 0
    assert ds.label_primary(mk("in-flight")) is None
    assert ds.label_primary(mk("pending")) is None
    assert ds.label_strict(mk("blocked", "infra x5 (agent_lost)")) is None
    assert ds.label_strict(mk("blocked", "mission failed x2")) == 0


# ─────────────────────────── 6. 时间切分 ───────────────────────────

def test_time_split_is_plan_disjoint_and_ordered(synthetic):
    _, fs = synthetic
    sp = bt.time_split(fs)
    sets = [set(sp.train), set(sp.calib), set(sp.test)]
    assert not (sets[0] & sets[1]) and not (sets[1] & sets[2]) and not (sets[0] & sets[2])
    plans = [set(fs.plan_ids[i] for i in s) for s in sets]
    assert not (plans[0] & plans[1]) and not (plans[1] & plans[2]), "计划必须互不相交"
    last_train = max(fs.plan_created_at[i] for i in sp.train)
    first_test = min(fs.plan_created_at[i] for i in sp.test)
    assert last_train <= first_test, "训练窗必须早于测试窗（时间留出）"
    for s in sets:
        ys = {fs.labels[i] for i in s}
        assert ys == {0, 1}


# ─────────────────────────── 7. 端到端 ───────────────────────────

def test_end_to_end_calibration_improves(synthetic):
    nodes, fs = synthetic
    sp = bt.time_split(fs)
    pipe = bt.Pipeline.fit(fs, sp)
    ev = bt.evaluate(pipe, fs, sp)
    v = ev["variants"]
    # 有真实信号：AUC 明显高于 0.5
    assert v["platt_prior"]["auc"] > 0.6, v["platt_prior"]
    # 校准把原始分数的 ECE 打下来
    assert v["platt_prior"]["ece_uniform10"]["ece"] < v["raw"]["ece_uniform10"]["ece"]
    # 先验漂移修正后 Brier 优于气候基准
    assert v["platt_prior"]["brier_skill_vs_train_prior"] > 0
    # 单调保序变体不得逆转排名
    assert v["isotonic_prior"]["auc"] > 0.55
    # OOF 斜率为正
    assert pipe.oof["platt_slope_oof"] > 0
    # 监控窗（中段）可评估
    mon = bt.evaluate_indices(pipe, fs, sp.calib)
    assert mon["n"] == len(sp.calib)


def test_shuffle_control_destroys_signal(synthetic):
    _, fs = synthetic
    sp = bt.time_split(fs)
    pipe = bt.Pipeline.fit(fs, sp)
    ev = bt.evaluate(pipe, fs, sp)
    sh = bt.shuffle_control(fs, ev, n_perm=25)
    assert abs(sh["null_auc"]["mean"] - 0.5) <= 0.05, sh["null_auc"]
    # 小样本（n_test≈64）下单个置换的 BSS 噪声 ~0.05 量级，故这里卡均值 + 「观测超过零分布上界」
    # （生产数据 n_test≈400 时报告里的 null_bss_max ≤ 0.05 判据才收得紧）
    assert sh["null_bss"]["mean"] <= 0.05, sh["null_bss"]
    assert sh["observed"]["brier_skill"] > sh["null_bss"]["max"], "观测技能须超过置换零分布上界"
    # 统计功效说明：n_test=64 时 null AUC 的标准差 ~0.11，AUC 的显著性检验几乎没功效
    # （生产数据 n_test≈390，null std ~0.04，才有 p≈0.01）；这里只卡「不落在左尾」
    assert sh["p_value_auc"] <= 0.35, sh["p_value_auc"]
    assert sh["p_value_bss"] <= 0.1, sh["p_value_bss"]


def test_pipeline_roundtrip_and_determinism(synthetic, tmp_path):
    _, fs = synthetic
    sp = bt.time_split(fs)
    pipe = bt.Pipeline.fit(fs, sp)
    blob = json.dumps(pipe.to_dict(), sort_keys=True)
    pipe2 = bt.Pipeline.from_dict(json.loads(blob))
    X = fs.matrix(sp.test)
    for k, v in pipe.predict(X).items():
        assert np.allclose(v, pipe2.predict(X)[k], atol=1e-12), k
    # 重跑同一输入 → 逐字节相同
    pipe3 = bt.Pipeline.fit(fs, sp)
    assert json.dumps(pipe3.to_dict(), sort_keys=True) == blob


def test_criteria_verdict_reports_numbers(synthetic):
    _, fs = synthetic
    sp = bt.time_split(fs)
    pipe = bt.Pipeline.fit(fs, sp)
    ev = bt.evaluate(pipe, fs, sp)
    sh = bt.shuffle_control(fs, ev, n_perm=15)
    checks = rp.criteria_verdict({"split": sp.to_dict(), "test": ev, "oof": pipe.oof}, sh)
    assert set(checks) >= {"auc_test_min", "brier_skill_platt_min", "ece_reduction_min",
                           "null_auc_within_tolerance", "null_bss_max", "p_value_bss",
                           "platt_slope_positive", "ALL"}
    for k, v in checks.items():
        if k == "ALL":
            continue
        assert isinstance(v["pass"], bool)
        assert v["threshold"] is not None


# ─────────────────────────── 8. CLI ───────────────────────────

def _write_fixture(nodes, path):
    ds.dump_fixture(nodes, str(path))


def test_cli_run_is_idempotent(tmp_path):
    nodes = _synthetic_nodes(n_plans=34, per_plan=7)
    fixture = tmp_path / "fixture.json"
    _write_fixture(nodes, fixture)
    outs = []
    for tag in ("a", "b"):
        out_dir = tmp_path / tag
        rc = subprocess.run(
            [sys.executable, "-m", "tools.pre_dispatch_prior", "run",
             "--source", "fixture", "--fixture", str(fixture),
             "--out-dir", str(out_dir), "--perm", "5"],
            capture_output=True, text=True, cwd="/root/tp-agent/work/m9049")
        assert rc.returncode in (0, 1), rc.stderr[-2000:]
        outs.append(out_dir)
    a = (outs[0] / "backtest.json").read_text(encoding="utf-8")
    b = (outs[1] / "backtest.json").read_text(encoding="utf-8")
    assert a == b, "同输入两次运行产物必须逐字节相同（幂等）"
    payload = json.loads(a)
    for key in ("campaign", "backtest", "shuffle", "criteria", "model"):
        assert key in payload
    assert (outs[0] / "REPORT.md").exists()
    assert (outs[0] / "fig_reliability.png").exists()
    assert (outs[0] / "fig_null_control.png").exists()


def test_predict_path_uses_history_only(tmp_path):
    nodes = _synthetic_nodes(n_plans=34, per_plan=7)
    fixture = tmp_path / "fixture.json"
    _write_fixture(nodes, fixture)
    out_dir = tmp_path / "run"
    rc = subprocess.run(
        [sys.executable, "-m", "tools.pre_dispatch_prior", "run",
         "--source", "fixture", "--fixture", str(fixture),
         "--out-dir", str(out_dir), "--perm", "3"],
        capture_output=True, text=True, cwd="/root/tp-agent/work/m9049")
    assert rc.returncode in (0, 1), rc.stderr[-2000:]

    specs = {"nodes": [
        {"name": "easy", "acceptance_criteria": [
            {"metric": "a", "operator": "==", "threshold": "PASS", "unit": "bool"}],
         "risk": "low", "repo": "Repo0", "goal": "easy gate"},
        {"name": "hard", "acceptance_criteria": [
            {"metric": f"m{i}", "operator": "<", "threshold": 0.01} for i in range(6)],
         "risk": "high", "repo": "Repo9", "goal": "hard numeric criteria set"},
    ]}
    spec_path = tmp_path / "specs.json"
    spec_path.write_text(json.dumps(specs), encoding="utf-8")
    pred_out = tmp_path / "pred.json"
    rc = subprocess.run(
        [sys.executable, "-m", "tools.pre_dispatch_prior", "predict",
         "--model", str(out_dir / "model.json"), "--nodes", str(spec_path),
         "--source", "fixture", "--fixture", str(fixture), "--out", str(pred_out)],
        capture_output=True, text=True, cwd="/root/tp-agent/work/m9049")
    assert rc.returncode == 0, rc.stderr[-2000:]
    payload = json.loads(pred_out.read_text(encoding="utf-8"))
    preds = payload["predictions"]
    assert len(preds) == 2
    for p in preds:
        assert 0.0 < p["p_accept"]["platt_prior"] < 1.0
        assert p["band"] in ("rescale", "watch", "normal")
    easy, hard = preds[0]["p_accept"]["platt_prior"], preds[1]["p_accept"]["platt_prior"]
    assert hard < easy, "判据更硬/更生的仓应给出更低验收先验（方向性）"
    # 派发前路径不得读取任何标签：历史里没有这两个节点
    assert payload["history_nodes"] == len(nodes)