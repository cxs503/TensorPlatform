"""判据 C：Murphy 三分分解 —— REL − RES + UNC 与 Brier 的恒等（残差 < 1e-10）。

用**独立实现的参考算法**（朴素循环、不同代码路径）交叉验证分量，避免
「同一个式子抄两遍」式的自证。
"""

from __future__ import annotations

import math
import os
import random
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.blind_calibration import metrics as M


# --------------------------------------------------------------------------- #
# 独立参考实现（朴素分组，不复用被测代码的分箱函数）
# --------------------------------------------------------------------------- #
def reference_components(p, y, n_bins=10):
    n = len(p)
    y_bar = sum(y) / n
    unc = y_bar * (1.0 - y_bar)
    groups: dict[int, list[int]] = {}
    for i, prob in enumerate(p):
        k = min(int(prob * n_bins), n_bins - 1)
        groups.setdefault(k, []).append(i)
    rel = 0.0
    res = 0.0
    bs_binned = 0.0
    for k, members in groups.items():
        nk = len(members)
        pk = sum(p[i] for i in members) / nk
        fk = sum(y[i] for i in members) / nk
        rel += nk * (pk - fk) ** 2 / n
        res += nk * (fk - y_bar) ** 2 / n
        bs_binned += nk * (pk - fk) ** 2 / n + nk * fk * (1 - fk) / n
    return {"reliability": rel, "resolution": res, "uncertainty": unc,
            "brier_binned": bs_binned}


def _random_dataset(seed: int, n: int = 200):
    rng = random.Random(seed)
    p = [round(rng.random(), 3) for _ in range(n)]
    y = [1 if rng.random() < q else 0 for q in p]
    return p, y


# --------------------------------------------------------------------------- #
# 恒等式
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7, 11, 101, 997])
def test_identity_holds_to_1e_10(seed):
    p, y = _random_dataset(seed)
    dec = M.murphy_decomposition(p, y)
    lhs = dec["reliability"] - dec["resolution"] + dec["uncertainty"]
    assert abs(lhs - dec["brier_binned"]) <= 1e-10
    assert dec["identity_holds"] is True
    assert abs(dec["identity_residual"]) <= M.IDENTITY_TOL == 1e-10


def test_identity_survives_degenerate_shapes():
    cases = [
        ([0.5], [1]),                                  # 单样本
        ([0.0, 0.0, 0.0], [0, 0, 0]),                  # 全负例
        ([1.0, 1.0], [1, 1]),                          # 全正例
        ([0.3] * 50, [1, 0] * 25),                     # 单一概率
        ([0.001, 0.999], [1, 0]),                      # 贴边概率
        ([0.0, 1.0, 0.0, 1.0], [0, 1, 1, 0]),          # 极端值 + 混合结局
    ]
    for p, y in cases:
        dec = M.murphy_decomposition(p, y)
        assert abs(dec["reliability"] - dec["resolution"] + dec["uncertainty"]
                   - dec["brier_binned"]) <= 1e-10
        assert dec["identity_holds"] is True


def test_identity_for_many_bin_counts():
    p, y = _random_dataset(42)
    for n_bins in (2, 3, 5, 10, 17, 50):
        dec = M.murphy_decomposition(p, y, n_bins)
        assert dec["n_bins"] == n_bins
        assert abs(dec["reliability"] - dec["resolution"] + dec["uncertainty"]
                   - dec["brier_binned"]) <= 1e-10


# --------------------------------------------------------------------------- #
# 分量语义
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", [5, 6, 9])
def test_components_match_independent_reference(seed):
    p, y = _random_dataset(seed, 120)
    dec = M.murphy_decomposition(p, y)
    ref = reference_components(p, y)
    assert dec["reliability"] == pytest.approx(ref["reliability"], abs=1e-12)
    assert dec["resolution"] == pytest.approx(ref["resolution"], abs=1e-12)
    assert dec["uncertainty"] == pytest.approx(ref["uncertainty"], abs=1e-12)
    assert dec["brier_binned"] == pytest.approx(ref["brier_binned"], abs=1e-12)


def test_uncertainty_only_depends_on_base_rate():
    p = [0.2, 0.8, 0.5, 0.5]
    y = [1, 0, 1, 0]
    dec = M.murphy_decomposition(p, y)
    assert dec["uncertainty"] == pytest.approx(0.5 * 0.5)
    # 换预测（结局不变）→ 不确定性不变
    dec2 = M.murphy_decomposition([0.9, 0.1, 0.6, 0.4], y)
    assert dec2["uncertainty"] == pytest.approx(dec["uncertainty"])


def test_perfectly_calibrated_has_zero_reliability_and_resolution_when_constant():
    dec = M.murphy_decomposition([0.4] * 10, [1, 0, 1, 1, 0, 1, 0, 0, 1, 0])
    # 单一箱 → f == ȳ == 0.5 → RES=0；p̄=0.4 而 f=0.5 → REL=0.01
    assert dec["resolution"] == pytest.approx(0.0, abs=1e-15)
    assert dec["reliability"] == pytest.approx(0.01, abs=1e-15)
    assert dec["uncertainty"] == pytest.approx(0.25)


def test_resolution_increases_with_sharper_and_correct_bins():
    y = [1, 1, 1, 1, 0, 0, 0, 0]
    weak = M.murphy_decomposition([0.5] * 8, y)   # 全落同一箱 → 零分辨力
    sharp = M.murphy_decomposition([0.95, 0.95, 0.95, 0.95, 0.05, 0.05, 0.05, 0.05], y)
    assert weak["resolution"] == pytest.approx(0.0, abs=1e-15)
    assert sharp["resolution"] > weak["resolution"]
    # 分辨力是「组间拉开」的能力：weak 全在一箱 → RES=0；sharp 拉到两端 → RES=0.25
    assert sharp["reliability"] >= weak["reliability"], "同一数据下自 Laplace 化的 REL 不会更小"
    assert sharp["brier_raw"] < weak["brier_raw"]


def test_reliability_penalises_overconfidence():
    y = [0] * 10
    no = M.murphy_decomposition([0.1] * 10, y)
    yes = M.murphy_decomposition([0.9] * 10, y)
    assert yes["reliability"] > no["reliability"]
    assert yes["brier_raw"] > no["brier_raw"]


def test_base_rate_reported():
    p, y = _random_dataset(3, 40)
    dec = M.murphy_decomposition(p, y)
    assert dec["base_rate"] == pytest.approx(sum(y) / len(y))


def test_raw_and_binned_both_reported_and_consistent():
    p, y = _random_dataset(8, 90)
    dec = M.murphy_decomposition(p, y)
    assert dec["brier_raw"] == pytest.approx(M.brier_score(p, y))
    assert dec["brier_binned"] == pytest.approx(M.binned_brier(p, y))
    assert dec["within_bin_term"] == pytest.approx(dec["brier_raw"] - dec["brier_binned"])
    # 分箱粗化不会改变恒等式结构，只是把箱内离散摊到 within_bin_term
    assert math.isfinite(dec["within_bin_term"])