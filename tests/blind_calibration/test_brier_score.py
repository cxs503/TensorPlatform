"""判据 B：Brier 分数（< 0.25 = 优于随机预测基线），含 skill score / log loss。"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.blind_calibration import metrics as M


def test_random_forecast_baseline_is_quarter():
    """p≡0.5 时 Brier 恒为 0.25：这就是「随机预测基线」的由来。"""
    for ys in ([0, 1], [0, 1, 0, 1, 1, 0], [1] * 7 + [0] * 3):
        ps = [0.5] * len(ys)
        assert M.brier_score(ps, ys) == pytest.approx(0.25, abs=1e-12)


def test_analytic_values():
    assert M.brier_score([1.0, 1.0], [1, 1]) == pytest.approx(0.0)      # 完美
    assert M.brier_score([0.0, 0.0], [1, 1]) == pytest.approx(1.0)      # 全错向
    assert M.brier_score([1.0, 0.0], [0, 1]) == pytest.approx(1.0)      # 反着说
    assert M.brier_score([0.9, 0.1], [1, 0]) == pytest.approx(0.01)
    assert M.brier_score([0.9, 0.1], [0, 1]) == pytest.approx(0.81)


def test_brier_is_mean_of_squared_errors():
    ps = [0.2, 0.4, 0.6, 0.8]
    ys = [0, 1, 1, 0]
    manual = sum((p - y) ** 2 for p, y in zip(ps, ys)) / 4
    assert M.brier_score(ps, ys) == pytest.approx(manual)


def test_brier_bounds():
    ps = [0.0, 1.0, 0.3, 0.7]
    ys = [0, 1, 1, 0]
    bs = M.brier_score(ps, ys)
    assert 0.0 <= bs <= 1.0


def test_climatology_is_optimal_constant_forecast():
    """在常数预测里，取结局基率的 Brier 最小（Brier 的严格凸性）。"""
    ys = [1] * 7 + [0] * 3
    base = M.climatology_brier(ys)
    assert base == pytest.approx(0.7 * 0.3)
    for c in (0.0, 0.25, 0.5, 0.7 + 1e-3, 0.9, 1.0):
        assert M.brier_score([c] * len(ys), ys) >= base - 1e-15


def test_skill_score_sign_and_degenerate_case():
    ys = [1, 0, 1, 0, 1, 0]
    good = M.brier_skill_score([0.9, 0.1, 0.9, 0.1, 0.9, 0.1], ys)
    bad = M.brier_skill_score([0.1, 0.9, 0.1, 0.9, 0.1, 0.9], ys)
    assert good["skill"] > 0 > bad["skill"]
    assert good["skill"] == pytest.approx(1 - good["brier"] / good["brier_climatology"])
    # 结局无变异 → 气候基准 BS=0，skill 无定义（必须如实返回 None，不返回 1.0 假装完美）
    null = M.brier_skill_score([0.9, 0.8], [1, 1])
    assert null["skill"] is None and "note" in null


def test_binned_brier_le_raw_when_forecasts_differ_within_bin():
    """分箱替代会抹掉箱内离散：分箱版 Brier 与逐样本版必须分开报，不互相冒充。"""
    ps = [0.11, 0.19, 0.11, 0.19]
    ys = [1, 0, 0, 1]
    raw = M.brier_score(ps, ys)
    binned = M.binned_brier(ps, ys)
    assert raw != pytest.approx(binned)
    dec = M.murphy_decomposition(ps, ys)
    assert dec["within_bin_term"] == pytest.approx(raw - binned)


def test_score_bundle_flags_random_forecast_as_failing():
    ys = [0, 1, 0, 1, 1, 0, 1, 0]
    res = M.score([0.5] * len(ys), ys)
    assert res["brier"]["raw"] == pytest.approx(0.25)
    # 判据是严格小于：随机预测不通过
    assert res["checks"]["brier_lt_random_baseline"]["pass"] is False
    assert res["all_pass"] is False


def test_score_bundle_passes_on_sharp_calibrated_forecasts():
    # 每箱 100 个样本、正例数按箱中心取整 → 箱内频率 ≈ 声明概率（真·校准良好）
    ps, ys = [], []
    for k in range(10):
        p = (k + 0.5) / 10.0
        n_pos = round(100 * p)
        ps += [p] * 100
        ys += [1] * n_pos + [0] * (100 - n_pos)
    res = M.score(ps, ys)
    assert res["brier"]["raw"] < 0.25
    assert res["checks"]["reliability_max_deviation_lt"]["pass"] is True
    assert res["checks"]["murphy_identity"]["pass"] is True
    assert res["all_pass"] is True


def test_thresholds_are_injectable():
    """判据阈值来自参数（可注入），不写死某个域的习惯。"""
    ps, ys = [0.95, 0.05, 0.9, 0.1], [1, 0, 1, 0]
    strict = M.score(ps, ys, brier_max=0.001, max_deviation_max=0.001)
    loose = M.score(ps, ys, brier_max=0.5, max_deviation_max=0.5)
    assert strict["all_pass"] is False
    assert loose["all_pass"] is True


def test_log_loss_finite_at_extremes():
    assert math.isfinite(M.log_loss([0.0, 1.0], [0, 1]))
    assert M.log_loss([0.999999, 0.000001], [1, 0]) < 1e-3