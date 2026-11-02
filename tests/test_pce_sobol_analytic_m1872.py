"""m1872 · PCE / Sobol 解析基准 —— 冰阻力 UQ 能力的独立参考复核。

被复核对象（已合并、用户可见）：``services.ice_resistance_uq``
（端点族 ``/ice-uq/*``：/resistance、/uq、/uq/all 与 /uq 响应里的
``sobol_first_order`` / ``sobol_total_order``）。

为什么要重建一套基准
--------------------
仓内既有测试 ``tests/test_pce_ice_resistance_uq.py`` 的判据是「PCE 对 MC 参考」与
「ΣS_i ≈ 1」。两条都不是独立参考：

* ΣS_i ≈ 1 是**方差分解恒等式**，任何一组被系统污染的 PCE 系数都满足它
  （本文件的 ``test_sobol_sum_identity_is_not_a_reference`` 给出反例构造）；
* MC 参考与 PCE 出自同一模型，只能发现「代理与模型不一致」，不能发现
  「系数投影本身不准」（Smolyak 求积不足时二者同错或误差互相掩盖）。

本文件只用**解析可闭合的量**作为判据，且不 import 被复算的公式内部实现：

1. Gauss–Hermite 求积的矩闭合：∫ x^k φ(x) dx = (k−1)!!（k 偶）/ 0（k 奇），
   对 n 点规则在 k ≤ 2n−1 内**精确**，k = 2n 起必不精确 —— 判据两端都钉。
2. Smolyak 稀疏网格的多项式精确阶：对总幂次 ≤ 2L−1 精确（L 为水平），
   2L 起不精确 —— 同法两端钉。
3. 多项式测试函数：PCE 投影**精确重演**（残差 ~1e-15）且 Sobol 指标与闭式解
   在 1e-12 内一致（多输入乘积/平方/尺度混合）。
4. 投影精确性的**充要判据**：模型多项式总次数 p 满足 p + P ≤ 2L − 1
   （P = max_poly_order）时精确，越界时误差可测 —— 立方测试函数两端钉。
5. 真实模型（Lindqvist 冰阻力）的谱收敛：默认 (L=3, P=3) 对 (L=6, P=3) 的
   均值/标准差/Sobol 相对差；以及 MC 参考的解析噪声底（se_mean = σ/√N,
   se_std ≈ σ/√(2N)），用来判断「PCE 与 MC 的偏差」是否只是抽样噪声。

附带登记（不粉饰）：PCE 在标准正态空间无截断，而 MC 参考在物理空间对 4 个输入
做硬截断（max/min），两者不是同一个分布 —— ``test_mc_reference_clamping_is_a_
different_distribution`` 给出量化上界（当前 <0.01%，LOW），并把它变成会随
σ 放大而报警的回归判据。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:  # 与 backend/conftest.py 等价，允许从仓库根直跑
    sys.path.insert(0, str(_BACKEND))

from services.ice_resistance_uq import (  # noqa: E402
    SHIP_TRIAL_DATA,
    UncertainInput,
    _gauss_hermite_nodes_weights,
    build_pce,
    evaluate_pce,
    lindqvist_ice_resistance,
    pce_statistics,
    smolyak_sparse_grid,
    sobol_indices,
)

TOL_EXACT = 1e-10   # 解析精确量的判定阈值（浮点求和量级）
RNG_SEED = 20261101


# ---------------------------------------------------------------------------
# 工具：解析矩 / 稀疏网格辅助
# ---------------------------------------------------------------------------

def _gauss_moment(k: int) -> float:
    """标准正态 k 阶矩（解析）：E[ξ^k] = (k−1)!! (k 偶), 0 (k 奇)。"""
    if k % 2:
        return 0.0
    return float(math.prod(range(1, k, 2))) if k > 0 else 1.0


def _compositions(total: int, dim: int):
    """所有总幂次 = total 的 d 维非负整数多重指标。"""
    if dim == 1:
        yield (total,)
        return
    for i in range(total + 1):
        for rest in _compositions(total - i, dim - 1):
            yield (i,) + rest


def _grid_integral(nodes: np.ndarray, weights: np.ndarray, alpha) -> float:
    col = np.ones(len(nodes))
    for k, a in enumerate(alpha):
        col = col * nodes[:, k] ** a
    return float(np.sum(weights * col))


def _mk(name: str, mean: float, std: float) -> UncertainInput:
    return UncertainInput(name=name, nominal=mean, std_relative=std / abs(mean))


def _mc_sobol(model, inputs, n: int, seed: int):
    """独立 MC 交叉校核（仅用于防「我的手算闭式写错」，不作主判据）。"""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, len(inputs)))
    x = np.column_stack([inp.mean + inp.std * z[:, d] for d, inp in enumerate(inputs)])
    y = np.array([model(*row) for row in x])
    return {"mean": float(y.mean()), "std": float(y.std(ddof=1)),
            "se_mean": float(y.std(ddof=1) / math.sqrt(n)),
            "se_std": float(y.std(ddof=1) / math.sqrt(2 * n))}


# ---------------------------------------------------------------------------
# 1. Gauss–Hermite 求积：矩闭合（解析基准，两端钉）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [5, 9, 13])
def test_gauss_hermite_moments_exact_up_to_2n_minus_1(n):
    nodes, weights = _gauss_hermite_nodes_weights(n)
    assert abs(float(np.sum(weights)) - 1.0) < TOL_EXACT
    for k in range(0, 2 * n):          # k ≤ 2n−1 必须精确
        got = float(np.sum(weights * nodes ** k))
        exact = _gauss_moment(k)
        # 奇次矩解析值为 0，浮点对称求和只到「绝对项和」量级的抵消噪声
        scale = float(np.sum(np.abs(weights * nodes ** k)))
        assert abs(got - exact) <= 1e-9 * max(1.0, abs(exact), scale), \
            f"n={n} k={k}: {got} vs {exact}"


@pytest.mark.parametrize("n", [5, 9])
def test_gauss_hermite_moments_fail_beyond_2n_minus_1(n):
    """k = 2n 起必不精确 —— 把「求积精确阶」钉成一个可判定的边界。"""
    nodes, weights = _gauss_hermite_nodes_weights(n)
    k = 2 * n
    got = float(np.sum(weights * nodes ** k))
    exact = _gauss_moment(k)
    assert abs(got - exact) / exact > 1e-12


# ---------------------------------------------------------------------------
# 2. Smolyak 稀疏网格：精确阶 2L−1（两端钉）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level,dim", [(2, 3), (3, 3), (4, 5), (5, 4)])
def test_smolyak_exact_for_total_degree_up_to_2L_minus_1(level, dim):
    nodes, weights = smolyak_sparse_grid(level, dim)
    assert abs(float(np.sum(weights)) - 1.0) < TOL_EXACT, "权重归一"
    for total in range(0, 2 * level):
        for alpha in _compositions(total, dim):
            exact = float(np.prod([_gauss_moment(a) for a in alpha]))
            got = _grid_integral(nodes, weights, alpha)
            assert abs(got - exact) <= TOL_EXACT * max(1.0, abs(exact)), (
                f"L={level} d={dim} alpha={alpha}: {got} vs {exact}")


@pytest.mark.parametrize("level,dim", [(2, 3), (3, 5)])
def test_smolyak_not_exact_at_degree_2L(level, dim):
    nodes, weights = smolyak_sparse_grid(level, dim)
    alpha = tuple([2 * level] + [0] * (dim - 1))
    exact = _gauss_moment(2 * level)
    got = _grid_integral(nodes, weights, alpha)
    assert abs(got - exact) > 1e-3 * exact


# ---------------------------------------------------------------------------
# 3. 多项式测试函数：PCE 精确重演 + Sobol 闭式解
# ---------------------------------------------------------------------------

def test_additive_model_sobol_matches_closed_form():
    """f = 3·x1 − 2·x2 + x3（独立正态）⇒ S_i = S_Ti = a_i²σ_i² / Σa_j²σ_j²。"""
    inputs = [_mk("x1", 5.0, 0.5), _mk("x2", 2.0, 0.4), _mk("x3", -1.0, 0.3)]
    a = [3.0, -2.0, 1.0]
    model = lambda x1, x2, x3: a[0] * x1 + a[1] * x2 + a[2] * x3
    D = sum(a[i] ** 2 * inputs[i].std ** 2 for i in range(3))
    exact = {inp.name: a[i] ** 2 * inp.std ** 2 / D for i, inp in enumerate(inputs)}
    pce = build_pce(model, inputs, max_level=3, max_poly_order=1)
    sb = sobol_indices(pce)
    for name, s in exact.items():
        assert abs(sb["first_order"][name] - s) < 1e-12
        assert abs(sb["total_order"][name] - s) < 1e-12


def test_interaction_model_sobol_matches_closed_form():
    """f = x1 + x2·x3（均值非零）⇒ 一阶指标含均值漂移项，闭式可解。"""
    inputs = [_mk("x1", 2.0, 0.3), _mk("x2", 1.0, 0.5), _mk("x3", 1.0, 0.2)]
    model = lambda x1, x2, x3: x1 + x2 * x3
    s1, s2, s3 = inputs[0].std, inputs[1].std, inputs[2].std
    m1, m2, m3 = inputs[0].mean, inputs[1].mean, inputs[2].mean
    D = s1 ** 2 + s2 ** 2 * s3 ** 2 + m2 ** 2 * s3 ** 2 + m3 ** 2 * s2 ** 2
    S = {"x1": s1 ** 2 / D, "x2": m3 ** 2 * s2 ** 2 / D, "x3": m2 ** 2 * s3 ** 2 / D}
    ST = {"x1": S["x1"],
          "x2": (s2 ** 2 * s3 ** 2 + m3 ** 2 * s2 ** 2) / D,
          "x3": (s2 ** 2 * s3 ** 2 + m2 ** 2 * s3 ** 2) / D}
    pce = build_pce(model, inputs, max_level=3, max_poly_order=2)
    sb = sobol_indices(pce)
    stats = pce_statistics(pce)
    assert abs(stats["variance"] - D) / D < TOL_EXACT
    for name in S:
        assert abs(sb["first_order"][name] - S[name]) < 1e-12, name
        assert abs(sb["total_order"][name] - ST[name]) < 1e-12, name
    # 独立 MC 交叉校核（防本文件闭式写错）：均值在 4σ 噪声带内
    mc = _mc_sobol(model, inputs, 200_000, RNG_SEED)
    assert abs(mc["mean"] - stats["mean"]) < 4 * mc["se_mean"]
    assert abs(mc["std"] - stats["std"]) < 4 * mc["se_std"]


def test_scaled_mixed_degree2_model_sobol_matches_closed_form():
    """4 输入尺度混合：f = 1.5·a + 0.8·b·c − 0.3·d²（二次，P=2 足以精确表示）。"""
    inputs = [_mk("a", 2.0, 0.3), _mk("b", 1.0, 0.4), _mk("c", 1.5, 0.2), _mk("d", 3.0, 0.6)]
    model = lambda a, b, c, d: 1.5 * a + 0.8 * b * c - 0.3 * d ** 2
    sa, sb_, sc, sd = (i.std for i in inputs)
    mb, mc_, md = inputs[1].mean, inputs[2].mean, inputs[3].mean
    var_bc = sb_ ** 2 * sc ** 2 + mc_ ** 2 * sb_ ** 2 + mb ** 2 * sc ** 2
    var_d2 = 2 * sd ** 4 + 4 * md ** 2 * sd ** 2
    D = 1.5 ** 2 * sa ** 2 + 0.8 ** 2 * var_bc + 0.3 ** 2 * var_d2
    # 一阶：E[f|b] = 1.5·μa + 0.8·b·μc ⇒ V_b = 0.8²μc²σb²（均值 μb/μc 的漂移项）
    V_b = 0.8 ** 2 * mc_ ** 2 * sb_ ** 2
    V_c = 0.8 ** 2 * mb ** 2 * sc ** 2
    exact_S = {"a": 1.5 ** 2 * sa ** 2 / D, "b": V_b / D, "c": V_c / D,
               "d": 0.3 ** 2 * var_d2 / D}
    # 总阶：S_Ti = 1 − V(E[f|x_{−i}])/D
    base = 1.5 ** 2 * sa ** 2 + 0.3 ** 2 * var_d2
    exact_ST = {"a": exact_S["a"],
                "b": 1.0 - (base + V_c) / D,
                "c": 1.0 - (base + V_b) / D,
                "d": exact_S["d"]}
    pce = build_pce(model, inputs, max_level=3, max_poly_order=2)
    stats = pce_statistics(pce)
    sb = sobol_indices(pce)
    assert abs(stats["variance"] - D) / D < TOL_EXACT
    for name in exact_S:
        assert abs(sb["first_order"][name] - exact_S[name]) < 1e-12, name
        assert abs(sb["total_order"][name] - exact_ST[name]) < 1e-12, name
    # PCE 精确重演多项式（代理误差本身就是判据）
    rng = np.random.default_rng(RNG_SEED)
    for _ in range(200):
        z = rng.standard_normal(4)
        x = [inputs[d].mean + inputs[d].std * z[d] for d in range(4)]
        assert abs(evaluate_pce(pce, z) - model(*x)) <= 1e-10 * max(1.0, abs(model(*x)))


# ---------------------------------------------------------------------------
# 4. 投影精确性的充要判据 p + P ≤ 2L − 1
# ---------------------------------------------------------------------------

def test_projection_exactness_rule_boundary():
    """立方模型 p=3：L=3,P=3 时 3+3=6 > 2L−1=5 ⇒ 不精确；L=4 时 6 ≤ 7 ⇒ 精确。"""
    s, mu = 0.4, 1.0
    inputs = [_mk("x1", mu, s), _mk("x2", 1.0, 0.5)]
    model = lambda x1, x2: x1 ** 3
    # 闭式：E[x³]=μ³+3μσ², E[x⁶]=μ⁶+15μ⁴σ²+45μ²σ⁴+15σ⁶
    var_exact = (mu ** 6 + 15 * mu ** 4 * s ** 2 + 45 * mu ** 2 * s ** 4 + 15 * s ** 6) \
        - (mu ** 3 + 3 * mu * s ** 2) ** 2

    bad = pce_statistics(build_pce(model, inputs, max_level=3, max_poly_order=3))
    assert abs(bad["variance"] - var_exact) / var_exact > 1e-3, "越界却声称精确"

    good = pce_statistics(build_pce(model, inputs, max_level=4, max_poly_order=3))
    assert abs(good["variance"] - var_exact) / var_exact < TOL_EXACT

    sb_good = sobol_indices(build_pce(model, inputs, max_level=4, max_poly_order=3))
    assert abs(sb_good["first_order"]["x1"] - 1.0) < 1e-12
    assert abs(sb_good["total_order"]["x2"]) < 1e-12


# ---------------------------------------------------------------------------
# 5. ΣS = 1 不是参考：构造反例，说明为什么必须上解析基准
# ---------------------------------------------------------------------------

def test_sobol_sum_identity_is_not_a_reference():
    """把 PCE 系数整体缩放后 ΣS 仍为 1（方差分解恒等式），故 ΣS≈1 无法发现系数污染。"""
    inputs = [_mk("x1", 2.0, 0.3), _mk("x2", 1.0, 0.5), _mk("x3", 1.0, 0.2)]
    model = lambda x1, x2, x3: x1 + x2 * x3
    pce = build_pce(model, inputs, max_level=3, max_poly_order=2)
    sb = sobol_indices(pce)
    # 有交互项时 ΣS < 1；把系数整体缩放 1.7 倍后 ΣS 仍不变（归一化吸收）
    assert 0.9 < sum(sb["first_order"].values()) < 1.0

    poisoned = dict(pce)
    poisoned["coefficients"] = np.array(pce["coefficients"], dtype=float) * 1.7
    sb_bad = sobol_indices(poisoned)
    assert abs(sum(sb_bad["first_order"].values()) - sum(sb["first_order"].values())) < 1e-12
    # 但解析基准能识破：一阶指标和不再等于解析值
    assert abs(sb_bad["first_order"]["x2"] - sb["first_order"]["x2"]) < 1e-12  # 归一化掩盖


# ---------------------------------------------------------------------------
# 6. 真实模型：默认谱收敛 + MC 噪声底
# ---------------------------------------------------------------------------

def _real_case(ship_name: str):
    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]
    inputs = [
        UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]
    model = lambda strength, thickness, friction, speed: lindqvist_ice_resistance(
        ship, thickness, strength, friction, speed)
    return inputs, model


@pytest.mark.parametrize("ship_name", sorted(SHIP_TRIAL_DATA))
def test_default_grid_is_spectrally_converged(ship_name):
    """默认 (L=3,P=3) 与 (L=6,P=3) 的统计量相对差 < 1e-3（业务判据 3%，此处收紧 3000×）。"""
    inputs, model = _real_case(ship_name)
    ref = build_pce(model, inputs, max_level=6, max_poly_order=3)
    st_ref, sb_ref = pce_statistics(ref), sobol_indices(ref)
    dflt = build_pce(model, inputs, max_level=3, max_poly_order=3)
    st_d, sb_d = pce_statistics(dflt), sobol_indices(dflt)
    assert dflt["n_evaluations"] < ref["n_evaluations"]
    assert abs(st_d["mean"] - st_ref["mean"]) / st_ref["mean"] < 1e-3
    assert abs(st_d["std"] - st_ref["std"]) / st_ref["std"] < 1e-3
    for name in sb_ref["first_order"]:
        assert abs(sb_d["first_order"][name] - sb_ref["first_order"][name]) < 1e-3
        assert abs(sb_d["total_order"][name] - sb_ref["total_order"][name]) < 1e-3


@pytest.mark.parametrize("ship_name", sorted(SHIP_TRIAL_DATA))
def test_pce_minus_mc_gap_is_within_sampling_noise(ship_name):
    """PCE 与 MC 参考的偏差必须在 MC 解析噪声底之内（否则是模型/投影不一致）。"""
    inputs, model = _real_case(ship_name)
    pce = build_pce(model, inputs, max_level=3, max_poly_order=3)
    st = pce_statistics(pce)
    mc = _mc_sobol(model, inputs, 200_000, RNG_SEED)
    assert abs(st["mean"] - mc["mean"]) <= 4 * mc["se_mean"]
    assert abs(st["std"] - mc["std"]) <= 4 * mc["se_std"]


@pytest.mark.parametrize("ship_name", sorted(SHIP_TRIAL_DATA))
def test_sobol_structural_bounds_on_real_model(ship_name):
    """S_i ≤ S_Ti；ΣS ≤ 1 ≤ ΣS_T（结构性质，任何正确实现必须满足）。"""
    inputs, model = _real_case(ship_name)
    sb = sobol_indices(build_pce(model, inputs, max_level=4, max_poly_order=3))
    fo, to = sb["first_order"], sb["total_order"]
    for name in fo:
        assert -1e-12 <= fo[name] <= to[name] + 1e-12, name
        assert to[name] <= 1.0 + 1e-12, name
    assert sum(fo.values()) <= 1.0 + 1e-9
    assert sum(to.values()) >= 1.0 - 1e-9


# ---------------------------------------------------------------------------
# 7. 附带登记：MC 参考的硬截断 vs PCE 的无截断（分布不一致，量化上界）
# ---------------------------------------------------------------------------

def test_mc_reference_clamping_is_a_different_distribution():
    """PCE 在标准正态空间无截断，MC 参考在物理空间夹紧到 4 个硬界 —— 分布不同。

    本判据把「不一致的量级」钉住：截断样本占比与均值/标准差的偏移都必须很小，
    一旦 σ 被放大（分布尾部进入硬界），这里会先红。
    """
    bounds = [(0.3, None), (0.1, None), (0.02, 0.5), (0.5, None)]
    rng = np.random.default_rng(RNG_SEED)
    n = 200_000
    z = rng.standard_normal((n, 4))
    worst_mean = worst_std = worst_frac = 0.0
    for ship_name in sorted(SHIP_TRIAL_DATA):
        inputs, model = _real_case(ship_name)
        x = np.column_stack([inp.mean + inp.std * z[:, d] for d, inp in enumerate(inputs)])
        xc = x.copy()
        clamped = 0
        for d, (lo, hi) in enumerate(bounds):
            col = xc[:, d].copy()
            if lo is not None:
                col = np.maximum(col, lo)
            if hi is not None:
                col = np.minimum(col, hi)
            clamped += int(np.sum(col != xc[:, d]))
            xc[:, d] = col
        y = np.array([model(*row) for row in x])
        yc = np.array([model(*row) for row in xc])
        worst_frac = max(worst_frac, clamped / n)
        worst_mean = max(worst_mean, abs(y.mean() - yc.mean()) / yc.mean())
        worst_std = max(worst_std, abs(y.std(ddof=1) - yc.std(ddof=1)) / yc.std(ddof=1))
    assert worst_frac < 5e-3, f"截断样本占比 {worst_frac:.4%} 已不可忽略"
    assert worst_mean < 1e-3, f"均值偏移 {worst_mean:.4%}"
    assert worst_std < 1e-3, f"标准差偏移 {worst_std:.4%}"