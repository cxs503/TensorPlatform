"""MOCK 指纹探测器的自检 + 变异体判负 + 离线回放一致性。

原则：先证「探测器的判决逻辑」本身可靠（正向对照必判真、变异体必判负），
再谈它对平台的判决。全部用例离线，不依赖在线服务。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.audit.mock_fingerprint import (  # noqa: E402
    SCORE_PATH, classify_ratio, classify_series, classify_vectors, endpoint_verdict,
    lattice_step, load_raw, plateau_tail, relative_spread, score,
)

SCORE = json.load(open(SCORE_PATH, encoding="utf-8")) if os.path.exists(SCORE_PATH) else None


# ── ① 数值内核 ────────────────────────────────────────────────────────────────
def test_relative_spread_basic():
    assert relative_spread([2.0, 2.0, 2.0]) == 0.0
    assert relative_spread([1.0, 3.0]) == pytest.approx(2.0 / 3.0)


def test_lattice_step_detects_coarse_grid():
    # 等步长栅格（m1523 的回转要素即落在 0.5L = 115 m 的整数倍上）
    assert lattice_step([460.0, 690.0, 920.0, 1150.0]) == pytest.approx(230.0)
    assert lattice_step([690.0, 800.0, 1035.0]) is None   # 非等步长 ⇒ 不判栅格
    assert lattice_step([1.0, 1.15, 1.37, 1.71]) is None   # 非等步长 ⇒ 不判栅格


# ── ② 标量判据：正向对照 + 变异体 ────────────────────────────────────────────
def test_constant_series_is_invariant():
    r = classify_series([1.26, 1.26, 1.26, 1.26])
    assert r["verdict"] == "INVARIANT"
    r2 = classify_series([5.0, 5.0, 5.0, 5.0])
    assert r2["verdict"] == "INVARIANT"


def test_responsive_series_not_flagged():
    r = classify_series([100.0, 160.0, 240.0, 380.0, 520.0], expect="inc")
    assert r["verdict"] == "RESPONSIVE"
    assert r["direction_violation"] is False


def test_direction_violation_flagged():
    r = classify_series([520.0, 380.0, 240.0], expect="inc")
    assert r["verdict"] == "DIRECTION_VIOLATION"


def test_coarse_lattice_flagged():
    # 只允许 4 档取整：相对步长 > 1%
    r = classify_series([10.0, 10.5, 11.0, 11.5, 12.0])
    assert r["verdict"] == "COARSE_LATTICE"


def test_fine_grid_is_not_lattice():
    vals = [1.000, 1.0021, 1.0043, 1.0066, 1.0090]
    assert classify_series(vals, expect="inc")["verdict"] == "RESPONSIVE"


# ── ③ 向量判据：跨扫参不变 / 响应内常量 ──────────────────────────────────────
def test_vectors_identical_curves_are_invariant():
    curve = [0.5, 1.4, 2.1, 1.3, 0.6]
    r = classify_vectors([curve, list(curve), list(curve)])
    assert r["verdict"] == "INVARIANT"
    assert r["across_sweep_spread"] <= 1e-12


def test_flat_curve_flagged():
    # 一次响应内 20 个频率点全等 ⇒ 常量填充（strip 端点 pitch_rao ≡ 0.02 即此形态）
    r = classify_vectors([[0.02] * 20, [0.02] * 20])
    assert r["verdict"] == "FLAT"


def test_varying_curve_responsive():
    r = classify_vectors([[0.5, 1.2, 2.0, 1.1, 0.4], [0.6, 1.5, 2.4, 1.3, 0.5]])
    assert r["verdict"] == "RESPONSIVE"


# ── ④ 比例刚性 / 尾部夹紧 ────────────────────────────────────────────────────
def test_rigid_ratio_detected():
    kt = [0.294, 0.252, 0.210, 0.168, 0.126]
    kq = [0.1 * v for v in kt]           # KQ ≡ KT/10 精确成立
    assert classify_ratio(kq, kt)["verdict"] == "RIGID_RATIO"


def test_independent_polynomials_not_rigid():
    kt = [0.30, 0.25, 0.20, 0.14, 0.07]
    kq = [0.032, 0.030, 0.026, 0.020, 0.012]
    assert classify_ratio(kq, kt)["verdict"] == "RESPONSIVE"


def test_plateau_tail_detected():
    assert plateau_tail([0.16, 0.48, 0.80, 0.85, 0.85])["verdict"] == "PLATEAU"
    assert plateau_tail([0.16, 0.48, 0.80, 0.86, 0.84])["verdict"] == "RESPONSIVE"


# ── ⑤ 端点级汇总 ─────────────────────────────────────────────────────────────
def test_endpoint_verdict_aggregation():
    inv = {"verdict": "INVARIANT"}
    resp = {"verdict": "RESPONSIVE"}
    lat = {"verdict": "COARSE_LATTICE"}
    assert endpoint_verdict({"a": inv, "b": resp}, 200) == "MOCK"
    assert endpoint_verdict({"a": lat, "b": resp}, 200) == "PARTIAL"
    assert endpoint_verdict({"a": resp, "b": resp}, 200) == "RESPONSIVE"
    assert endpoint_verdict({"a": resp}, 500) == "UNAVAILABLE"
    assert endpoint_verdict({}, 200) == "NO_KEY"


# ── ⑥ 离线回放：判决可复算、与入库 scoreboard 逐项一致 ──────────────────────
@pytest.mark.skipif(SCORE is None, reason="scoreboard 未生成")
def test_offline_replay_matches_stored_scoreboard():
    recomputed = score(load_raw())
    stored = json.load(open(SCORE_PATH, encoding="utf-8"))
    assert set(recomputed["rows"]) == set(stored["rows"])
    for sid in stored["rows"]:
        assert recomputed["rows"][sid]["verdict"] == stored["rows"][sid]["verdict"], sid
        assert set(recomputed["rows"][sid]["keys"]) == set(stored["rows"][sid]["keys"]), sid


@pytest.mark.skipif(SCORE is None, reason="scoreboard 未生成")
def test_stored_fingerprints_are_present():
    """历史人工发现的三类指纹必须仍被机器检出（防止探测器退化）。"""
    rows = SCORE["rows"]
    assert rows["maneuvering-rudder"]["verdict"] == "MOCK"        # m1523 的 0.5L 栅格常量表
    assert rows["seakeeping-period"]["verdict"] == "MOCK"         # m1547 的常数 RAO
    assert rows["propeller-wageningen-j"]["keys"]["KQ/KT"]["verdict"] == "RIGID_RATIO"
    assert rows["strip-heading"]["keys"]["roll_rao"]["verdict"] == "INVARIANT"


@pytest.mark.skipif(SCORE is None, reason="scoreboard 未生成")
def test_positive_controls_stay_responsive():
    """正向对照：真物理端点不得被误判为 MOCK（防假阳性）。"""
    rows = SCORE["rows"]
    for sid in ("resistance-speed", "resistance-cb", "stability-kg",
                "stability-beam", "seakeeping-heading"):
        assert rows[sid]["verdict"] == "RESPONSIVE", sid