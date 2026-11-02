"""m1965 · PCE 代理不确定性通道验证（models 席自找活）。

覆盖四个修复点，全部对**解析解**或**蒙特卡洛**机器裁决：

T1 传播器解析基准：线性/二次参考函数上 delta 法 std 对解析导数（容差 1e-9）。
T2 量纲不变性：输入/输出同时放大 1e6 倍，相对 std 不变。
T3 十进输出单位换算：``log10_outputs`` 的 std 必须回到物理单位（对解析导数，
   且与 legacy 的"十进数值直接报出"相差 y·ln10 量级）。
T4 键集合完整：``predict_with_uncertainty()["std"]`` 必须覆盖 ``mean`` 全部键
   （``life_years`` 由后处理生成）。
T5 非十进输出零回归：物理输出 std 与 ``pce.predict_std`` 逐位一致（1e-12）。
T6 真实疲劳代理：修复后的 damage std 与参考模型 MC 同量级，legacy 至少高 5 倍。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.digital_twin.models.geometry import HullForm  # noqa: E402
from backend.services.digital_twin.pce import PolynomialChaos  # noqa: E402
from backend.services.digital_twin.surrogate_registry import (  # noqa: E402
    PCESurrogate,
    get_registry,
)
from backend.services.digital_twin.types import Field  # noqa: E402

C_X2 = 3.0
SIGMA_FRAC = 0.01


def _analytic_surrogate() -> PCESurrogate:
    """y1 = 3x^2 + 1 拟合在 log10 空间；y2 = 2x 直接拟合。"""
    fields = [Field("x", "m", 1.0, 3.0, "probe input")]

    def ref(X: np.ndarray) -> np.ndarray:
        x = X[:, 0]
        return np.column_stack([C_X2 * x ** 2 + 1.0, 2.0 * x])

    def scalar(v):
        x = float(v["x"])
        return {"damage_per_day": C_X2 * x ** 2 + 1.0, "y2": 2.0 * x}

    def post(out):
        dmg = max(out["damage_per_day"], 0.0)
        return {**out, "life_years": 1.0 / (365.25 * dmg) if dmg > 0 else 1e12}

    sur = PCESurrogate(
        name="analytic_probe", description="analytic PCE probe",
        reference_model="analytic", hull=HullForm(180.0, 32.0, 11.5, 0.80),
        input_fields=fields, output_names=["damage_per_day", "y2"],
        reference_fn=ref, reference_scalar=scalar, degree=3, n_samples=200,
        log10_outputs=("damage_per_day",), postprocess=post,
    )
    return sur


@pytest.fixture(scope="module")
def analytic_surrogate() -> PCESurrogate:
    return _analytic_surrogate()


# ---------------------------------------------------------------- T1
@pytest.mark.parametrize("scale,offset,slope", [
    (1.0, 0.0, 2.5), (1.0e3, 0.0, 2.5), (1.0e6, 0.0, 2.5), (2.0e4, 5.0e4, 2.5),
])
def test_predict_std_matches_analytic_derivative(scale, offset, slope):
    lo, hi = offset, offset + scale
    pce = PolynomialChaos(["x"], [lo], [hi], degree=2)
    pce.fit(lambda X: (slope * X).reshape(-1, 1), n_samples=200, seed=7,
            output_names=["y"])
    x0, sigma = offset + 0.5 * scale, SIGMA_FRAC * scale
    got = pce.predict_std({"x": x0}, sigma_input=[sigma])["y"]
    want = abs(slope) * sigma
    assert abs(got - want) / want < 1e-9


# ---------------------------------------------------------------- T2
def test_predict_std_is_unit_scale_invariant():
    def _rel(scale: float) -> float:
        lo, hi = 0.0, scale
        pce = PolynomialChaos(["x"], [lo], [hi], degree=2)
        pce.fit(lambda X: (2.5 * X).reshape(-1, 1), n_samples=160, seed=11,
                output_names=["y"])
        sigma = SIGMA_FRAC * scale
        got = pce.predict_std({"x": 0.5 * scale}, sigma_input=[sigma])["y"]
        return got / (2.5 * sigma)

    assert abs(_rel(1.0) - 1.0) < 1e-11
    assert abs(_rel(1.0e6) - 1.0) < 1e-9


# ---------------------------------------------------------------- T3/T4/T5
def test_log10_output_std_is_converted_to_physical_units(analytic_surrogate):
    sur = analytic_surrogate
    x0 = 2.0
    sigma = SIGMA_FRAC * (3.0 - 1.0)
    u = sur.predict_with_uncertainty({"x": x0}, sigma_input=[sigma])
    y1 = C_X2 * x0 ** 2 + 1.0
    want = abs(2.0 * C_X2 * x0) * sigma  # d/dx (C_X2*x^2 + 1) = 2*C_X2*x
    got = u["std"]["damage_per_day"]
    assert abs(got - want) / want < 2e-3, (got, want)

    legacy = sur.pce.predict_std({"x": x0}, sigma_input=[sigma])["damage_per_day"]
    # legacy 是十进空间数值，物理等价量应放大 y*ln10 倍
    assert legacy < 0.5 * got
    assert abs(legacy * y1 * math.log(10.0) - got) / got < 2e-3


def test_std_keys_cover_mean_keys(analytic_surrogate):
    sur = analytic_surrogate
    u = sur.predict_with_uncertainty({"x": 2.0})
    assert sorted(u["std"]) == sorted(u["mean"])
    assert "life_years" in u["std"]
    assert u["std_units"] == "physical"
    # life_years = 1/(365.25*dmg) -> 一阶传播 sigma_life = life*sigma_dmg/dmg
    dmg = u["mean"]["damage_per_day"]
    expect = u["mean"]["life_years"] * u["std"]["damage_per_day"] / dmg
    # 中心差分对 1/x 有一阶以上的 (sigma/x)^2 偏差（约 3e-4），故容差 1e-3
    assert abs(u["std"]["life_years"] - expect) / expect < 1e-3


def test_non_log_output_std_is_unchanged(analytic_surrogate):
    sur = analytic_surrogate
    sigma = [SIGMA_FRAC * 2.0]
    u = sur.predict_with_uncertainty({"x": 2.5}, sigma_input=sigma)
    direct = sur.pce.predict_std({"x": 2.5}, sigma_input=sigma)["y2"]
    assert abs(u["std"]["y2"] - direct) <= 1e-12 * max(1.0, abs(direct))


def test_predict_std_never_extrapolates_at_bound(analytic_surrogate):
    sur = analytic_surrogate
    u = sur.predict_with_uncertainty({"x": 3.0})
    assert math.isfinite(u["std"]["y2"]) and u["std"]["y2"] > 0.0


# ---------------------------------------------------------------- T6
def test_real_fatigue_surrogate_units_fixed():
    reg = get_registry(n_samples=160)
    fat = reg._surrogates["fatigue"]
    point = {"Hs": 4.0, "Tp": 8.0, "ice_concentration": 0.2, "ice_thickness": 0.3}
    u = fat.predict_with_uncertainty(point)
    legacy = fat.pce.predict_std(point)["damage_per_day"]

    fields = fat.inputs.fields
    rng = np.random.default_rng(20261101)
    n = 4000
    X = np.column_stack([
        np.clip(point[f.name] + rng.normal(0.0, SIGMA_FRAC * (f.upper - f.lower), n),
                f.lower, f.upper) for f in fields
    ])
    Y = np.atleast_2d(np.asarray(fat._ref_fn(X), dtype=float)).reshape(n, -1)
    mc_std = float(np.std(Y[:, 0], ddof=1))

    fixed = u["std"]["damage_per_day"]
    assert legacy / mc_std > 5.0, "legacy 十进空间数值应显著高报"
    assert abs(fixed - mc_std) / mc_std < 0.60, (fixed, mc_std)
    assert sorted(u["std"]) == sorted(u["mean"])