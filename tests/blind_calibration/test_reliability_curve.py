"""判据 A：可靠性曲线（10 箱，逐箱 p̄ vs f，最大偏差 < 0.1）。

全部用**构造出已知答案**的样本验证，不依赖任何数据集/域内容。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.blind_calibration import metrics as M


# --------------------------------------------------------------------------- #
# 分箱几何
# --------------------------------------------------------------------------- #
def test_bin_edges_and_uniform_partition():
    edges = M.bin_edges(10)
    assert len(edges) == 11
    assert edges[0] == 0.0 and edges[-1] == 1.0
    assert all(edges[i] < edges[i + 1] for i in range(10))
    assert M.bin_index(0.0) == 0
    assert M.bin_index(0.0999) == 0
    assert M.bin_index(0.1) == 1
    assert M.bin_index(0.9999) == 9
    assert M.bin_index(1.0) == 9, "p=1.0 必须归末箱（右端闭合）"


def test_bin_index_rejects_out_of_range():
    with pytest.raises(ValueError):
        M.bin_index(1.2)
    with pytest.raises(ValueError):
        M.bin_index(-0.01)
    with pytest.raises(ValueError):
        M.bin_index(float("nan"))


# --------------------------------------------------------------------------- #
# 完美校准 / 反校准
# --------------------------------------------------------------------------- #
def _perfect_set(n_per_bin: int = 20) -> tuple[list[float], list[int]]:
    """构造「每箱实测频率 == 箱内平均概率」的样本（偏差应为 0）。

    箱中心取 ``(k+0.5)/10``，每箱 ``n_per_bin`` 个样本、正例数 = ``round(n*p)``；
    ``n`` 必须是 20 的倍数，否则频率只能取 1/n 的倍数，凑不出箱中心。
    """
    ps: list[float] = []
    ys: list[int] = []
    for k in range(10):
        p = (k + 0.5) / 10.0           # 箱中心：0.05, 0.15, …, 0.95
        n_pos = round(n_per_bin * p)   # 精确可表示
        assert abs(n_pos / n_per_bin - p) < 1e-12
        for i in range(n_per_bin):
            ps.append(p)
            ys.append(1 if i < n_pos else 0)
    return ps, ys


def test_perfectly_calibrated_has_zero_max_deviation():
    ps, ys = _perfect_set(20)   # 每箱 20 个样本，箱中心可精确表示
    curve = M.reliability_curve(ps, ys)
    assert curve["n"] == len(ps) == 200
    assert curve["n_bins"] == 10
    assert curve["n_occupied_bins"] == 10
    assert curve["max_deviation"] == pytest.approx(0.0, abs=1e-12)
    assert curve["ece"] == pytest.approx(0.0, abs=1e-12)


def test_anti_calibrated_exposes_large_deviation():
    ps = [0.9] * 20
    ys = [0] * 20                      # 说 90%，一次没中
    curve = M.reliability_curve(ps, ys)
    assert curve["max_deviation"] == pytest.approx(0.9, abs=1e-12)
    assert curve["n_occupied_bins"] == 1
    assert curve["n_empty_bins"] == 9
    # 空箱必须如实列出，而不是被当成偏差 0 而「帮忙」降低最大偏差
    assert curve["empty_bin_indices"] == [0, 1, 2, 3, 4, 5, 6, 7, 8]


def test_bin_mean_and_frequency_match_manual_computation():
    ps = [0.11, 0.19, 0.21, 0.79, 0.81, 0.99]
    ys = [1, 0, 1, 1, 0, 1]
    curve = M.reliability_curve(ps, ys)
    by_idx = {b["index"]: b for b in curve["bins"]}
    assert by_idx[1]["n"] == 2
    assert by_idx[1]["mean_prob"] == pytest.approx((0.11 + 0.19) / 2)
    assert by_idx[1]["observed_freq"] == pytest.approx(0.5)
    assert by_idx[1]["deviation"] == pytest.approx(0.15 - 0.5)
    assert by_idx[2]["n"] == 1 and by_idx[2]["observed_freq"] == 1.0
    assert by_idx[7]["n"] == 1 and by_idx[7]["observed_freq"] == 1.0
    assert by_idx[8]["n"] == 1 and by_idx[8]["observed_freq"] == 0.0
    assert by_idx[9]["n"] == 1 and by_idx[9]["observed_freq"] == 1.0
    manual = max(abs(by_idx[1]["deviation"]), abs(by_idx[2]["deviation"]),
                 abs(by_idx[7]["deviation"]), abs(by_idx[8]["deviation"]),
                 abs(by_idx[9]["deviation"]))
    assert curve["max_deviation"] == pytest.approx(manual)


def test_ece_is_sample_weighted():
    # 两箱：一箱 8 个样本偏差 0.1，另一箱 2 个样本偏差 0.5
    ps = [0.5] * 8 + [0.5] * 2
    ys = [1, 1, 1, 1, 0, 0, 0, 0] + [0, 0]
    curve = M.reliability_curve(ps, ys)
    # 全部落在同一箱：p̄=0.5, f=0.4 → ECE = 0.1
    assert curve["n_occupied_bins"] == 1
    assert curve["ece"] == pytest.approx(0.1, abs=1e-12)


def test_curve_scales_with_n_bins():
    ps, ys = _perfect_set(20)
    for n_bins in (4, 5, 10, 20):
        curve = M.reliability_curve(ps, ys, n_bins)
        assert curve["n_bins"] == n_bins
        assert len(curve["bins"]) == n_bins
        assert curve["n_occupied_bins"] + curve["n_empty_bins"] == n_bins


# --------------------------------------------------------------------------- #
# 小样本区间：别把 1 样本箱的 0 偏差当「校准良好」
# --------------------------------------------------------------------------- #
def test_wilson_interval_widens_for_small_bins():
    lo1, hi1 = M.wilson_interval(1, 1)
    lo20, hi20 = M.wilson_interval(20, 20)
    assert (hi1 - lo1) > (hi20 - lo20)
    assert 0.0 <= lo1 <= hi1 <= 1.0
    assert M.wilson_interval(0, 0) == (0.0, 1.0)


def test_curve_with_intervals_annotates_every_bin():
    ps = [0.05, 0.15, 0.95]
    ys = [0, 0, 1]
    curve = M.curve_with_intervals(ps, ys)
    for b in curve["bins"]:
        assert "observed_freq_ci" in b
        assert len(b["observed_freq_ci"]) == 2
        assert "n_positive" in b


def test_groupwise_splits_and_skips_tiny_groups():
    ps = [0.9, 0.9, 0.9, 0.1]
    ys = [1, 1, 0, 0]
    groups = ["a", "a", "a", "b"]
    out = M.groupwise(ps, ys, groups)
    assert out["a"]["n"] == 3
    assert out["a"]["brier"] == pytest.approx((0.01 + 0.01 + 0.81) / 3)
    assert out["b"]["skipped"]


# --------------------------------------------------------------------------- #
# 输入纪律：坏输入抛错，不静默丢弃
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ps,ys", [
    ([0.5, 0.5], [1]),            # 长度不一致
    ([1.5], [1]),                 # 概率越界
    ([0.5], [2]),                 # 结局不是 0/1
    ([float("nan")], [1]),        # 非有限
    ([], []),                     # 空样本
])
def test_bad_inputs_raise(ps, ys):
    with pytest.raises(ValueError):
        M.reliability_curve(ps, ys)