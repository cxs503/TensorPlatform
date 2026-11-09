"""通用数值核：张量矩 / 有限对称群 / 细化扫描拟合。

**零领域语义**：晶格权重集、几何参数、扫描网格等一律由调用方（探针 + JSON 工件）
传入；本模块只做张量代数与收敛率拟合这类与物理域无关的数学。
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# 晶格权重集（数据文件注入）
# --------------------------------------------------------------------------- #


def load_lattice(data_dir: str | Path, name: str) -> Tuple[np.ndarray, np.ndarray, int]:
    """从 ``lattices.json`` 读取速度集，返回 (velocities[q,dim], weights[q], dim)。"""
    payload = json.loads(Path(data_dir).joinpath("lattices.json").read_text(encoding="utf-8"))
    entry = payload["lattices"][name]
    velocities = np.asarray(entry["velocities"], dtype=float)
    weights = np.asarray(entry["weights"], dtype=float)
    if velocities.ndim != 2 or velocities.shape[0] != weights.shape[0]:
        raise ValueError(f"lattice {name}: velocities/weights shape mismatch")
    return velocities, weights, velocities.shape[1]


# --------------------------------------------------------------------------- #
# 矩张量与各向同性恒等式
# --------------------------------------------------------------------------- #


def moment_tensor(weights: np.ndarray, velocities: np.ndarray, order: int) -> np.ndarray:
    """权重集 speed 张量：M_{i1..iorder} = Σ_c w_c c_{i1}…c_{iorder}。"""
    if order == 2:
        return np.einsum("q,qi,qj->ij", weights, velocities, velocities)
    if order == 4:
        return np.einsum("q,qi,qj,qk,ql->ijkl", weights, velocities, velocities,
                         velocities, velocities)
    raise ValueError(f"unsupported moment order {order}")


def delta(dim: int) -> np.ndarray:
    return np.eye(dim)


def isotropic_moment(order: int, dim: int, cs2: float) -> np.ndarray:
    """解析各向同性矩：2 阶 = c_s² δ；4 阶 = c_s⁴(δδ+δδ+δδ)。"""
    if order == 2:
        return cs2 * delta(dim)
    if order == 4:
        d = delta(dim)
        out = np.zeros((dim,) * 4)
        for i, j, k, l in itertools.product(range(dim), repeat=4):
            out[i, j, k, l] = cs2 ** 2 * (
                d[i, j] * d[k, l] + d[i, k] * d[j, l] + d[i, l] * d[j, k]
            )
        return out
    raise ValueError("only orders 2 and 4 have a closed isotropic form here")


def rel_max_error(measured: np.ndarray, exact: np.ndarray) -> float:
    """相对最大误差：max|Δ| / max|exact|（分母为 0 时退回绝对误差）。"""
    scale = float(np.max(np.abs(exact)))
    diff = float(np.max(np.abs(np.asarray(measured) - np.asarray(exact))))
    return diff / scale if scale > 0.0 else diff


def quadrupole_from_second_moment(second: np.ndarray, cs2: float) -> np.ndarray:
    """体积四极子 Q_ij = M2_ij - c_s² δ_ij（无迹部分）。"""
    dim = second.shape[0]
    return second - cs2 * delta(dim)


def trace_free_residual(q: np.ndarray, scale: float | None = None) -> float:
    """无迹残差：|tr Q| / scale。

    尺度由调用方给出（通常取 |tr M2|，即物理矩量级）；未给时退回 max|Q|。
    注意：若 Q ≡ 0（各向同性算子），用 max|Q| 作分母会把 1e-16 的舍入噪声放大成
    O(1) 的假残差 —— 这是本轮实测踩到的坑，故尺度必须外部给定。
    """
    tr = abs(float(np.trace(q)))
    if scale is None:
        scale = float(np.max(np.abs(q)))
    return tr / float(scale) if scale else tr


# --------------------------------------------------------------------------- #
# 有限对称群（超立方体符号置换群 B_dim，dim=3 → 48 元 = O_h）
# --------------------------------------------------------------------------- #


def signed_permutation_group(dim: int) -> List[np.ndarray]:
    """超八面体群 B_dim：所有符号置换矩阵（dim=3 → 48 元，立方群 O_h）。"""
    ops: List[np.ndarray] = []
    for perm in itertools.permutations(range(dim)):
        for signs in itertools.product((-1.0, 1.0), repeat=dim):
            mat = np.zeros((dim, dim))
            for row, col in enumerate(perm):
                mat[row, col] = signs[row]
            ops.append(mat)
    # 去重（按字典序展平），保证确定性
    unique: Dict[Tuple[float, ...], np.ndarray] = {}
    for mat in ops:
        unique.setdefault(tuple(np.round(mat.ravel(), 12)), mat)
    return [unique[key] for key in sorted(unique)]


def group_closure_residual(ops: Sequence[np.ndarray], tol: float = 1e-12) -> float:
    """群公理残差：闭包（乘积仍在集合内）+ 逆元存在，返回最差残差（0 = 精确饱和）。"""
    keys = {tuple(np.round(op.ravel(), 12)) for op in ops}
    worst = 0.0
    for a in ops:
        for b in ops:
            prod = a @ b
            key = tuple(np.round(prod.ravel(), 12))
            if key not in keys:
                # 找最近元，计残差
                dist = min(float(np.max(np.abs(prod - c))) for c in ops)
                worst = max(worst, dist)
    if worst <= tol:
        return 0.0
    return worst


def transform_tensor(tensor: np.ndarray, op: np.ndarray, rank: int) -> np.ndarray:
    """张量在坐标变换 op 下的像：T' = op^{⊗rank} · T。"""
    out = tensor
    for _ in range(rank):
        out = np.tensordot(op, out, axes=([1], [0]))
        out = np.moveaxis(out, 0, -1)
    return out


def invariance_residual(ops: Sequence[np.ndarray], tensor: np.ndarray, rank: int) -> float:
    """张量在群作用下的不变残差（相对最大分量）。"""
    worst = 0.0
    scale = float(np.max(np.abs(tensor))) or 1.0
    for op in ops:
        img = transform_tensor(tensor, op, rank)
        worst = max(worst, float(np.max(np.abs(img - tensor))) / scale)
    return worst


def symmetric_basis(dim: int) -> List[np.ndarray]:
    """Sym(dim) 的正交归一基（内积 <A,B> = tr(AᵀB)）。"""
    basis: List[np.ndarray] = []
    for i in range(dim):
        mat = np.zeros((dim, dim))
        mat[i, i] = 1.0
        basis.append(mat)
    for i, j in itertools.combinations(range(dim), 2):
        mat = np.zeros((dim, dim))
        mat[i, j] = mat[j, i] = 1.0 / np.sqrt(2.0)
        basis.append(mat)
    return basis


def invariant_subspace_dimension(ops: Sequence[np.ndarray], dim: int = 3, tol: float = 1e-9) -> int:
    """Sym(dim) 上平凡表示重数 = 群不变张量子空间维数（SVD 数值秩）。

    对 O_h（dim=3）应恰为 1：只有 δ_ij 是 2 阶对称不变张量。这是"不变性群的
    不可约分解是否被正确数值化"的可裁判据，而不是一句声明。
    """
    basis = symmetric_basis(dim)
    n = len(basis)
    projector = np.zeros((n, n))
    for op in ops:
        rep = np.zeros((n, n))
        for col, bvec in enumerate(basis):
            img = op @ bvec @ op.T
            for row, avec in enumerate(basis):
                rep[row, col] = float(np.sum(avec * img))
        projector += rep
    projector /= len(ops)
    sv = np.linalg.svd(projector, compute_uv=False)
    return int(np.sum(sv > tol))


def isotropic_rank2(dim: int = 3) -> np.ndarray:
    return delta(dim)


def isotropic_rank4(dim: int = 3, cs2: float = 1.0 / 3.0) -> np.ndarray:
    return isotropic_moment(4, dim, cs2)


# --------------------------------------------------------------------------- #
# 收敛率（细化扫描）拟合
# --------------------------------------------------------------------------- #


def fit_decay_exponent(hs: Sequence[float], errors: Sequence[float]) -> float:
    """log e = p·log h + log C 的最小二乘斜率 p（观测阶）。"""
    h = np.asarray(hs, dtype=float)
    e = np.asarray(errors, dtype=float)
    if np.any(h <= 0) or np.any(e <= 0):
        raise ValueError("hs and errors must be positive")
    slope, _ = np.polyfit(np.log(h), np.log(e), 1)
    return float(slope)


def refinement_scan(err_fn: Callable[[int], float], resolutions: Iterable[int],
                    length: float = 1.0) -> Dict[str, object]:
    """在 resolutions 上跑细化扫描，返回 {"hs", "errors", "observed_exponent"}。"""
    res = [int(n) for n in resolutions]
    hs = [length / n for n in res]
    errors = [float(err_fn(n)) for n in res]
    return {
        "resolutions": res,
        "hs": hs,
        "errors": errors,
        "observed_exponent": fit_decay_exponent(hs, errors),
    }


def error_constant(hs: Sequence[float], errors: Sequence[float], exponent: float) -> float:
    """误差常数 C = e / h^p（取最细网格，用于跨剪切对比）。"""
    return float(np.asarray(errors)[-1] / np.asarray(hs)[-1] ** exponent)


# --------------------------------------------------------------------------- #
# 体积分算例（解析闭式解）—— 纯几何/微积分，无物理域语义
# --------------------------------------------------------------------------- #


def quadrupole_midpoint(N: int, length: float = 1.0, amplitude: float = 0.5,
                        modulation: str = "cos") -> Tuple[np.ndarray, float]:
    """余弦调制密度场 ρ = 1 + A·cos(2πx/L) 的体积四极子（中点求积，N³ 单元）。

    闭式解：Q_xx = -A L⁵/(3π²)，Q_yy = Q_zz = +A L⁵/(6π²)，无对角外分量。
    返回 (Q 数值张量, 质量)。
    """
    h = length / N
    axis = (np.arange(N) + 0.5) * h - length / 2.0
    X, Y, Z = np.meshgrid(axis, axis, axis, indexing="ij")
    rho = 1.0 + amplitude * np.cos(2.0 * np.pi * X / length)
    dV = h ** 3
    mass = float(rho.sum() * dV)
    r2 = X * X + Y * Y + Z * Z
    second = np.array([
        [(X * X * rho).sum(), (X * Y * rho).sum(), (X * Z * rho).sum()],
        [(Y * X * rho).sum(), (Y * Y * rho).sum(), (Y * Z * rho).sum()],
        [(Z * X * rho).sum(), (Z * Y * rho).sum(), (Z * Z * rho).sum()],
    ]) * dV
    quad = second - np.trace(second) / 3.0 * np.eye(3)
    _ = r2  # r² 只用于说明无迹投影，显式保留以免误读
    return quad, mass


def quadrupole_midpoint_exact(length: float = 1.0, amplitude: float = 0.5) -> np.ndarray:
    """上面的闭式解。"""
    scale = -amplitude * length ** 5 / (3.0 * np.pi ** 2)
    return np.array([
        [scale, 0.0, 0.0],
        [0.0, -scale / 2.0, 0.0],
        [0.0, 0.0, -scale / 2.0],
    ])


def ellipsoid_quadrupole(N: int, semi_axes: Sequence[float], length: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """椭球（半轴 a,b,c）体积四极子的中点采样近似与闭式解。

    闭式解：Q_xx = V(a²-b²)/5 等，V = 4πabc/3。
    """
    a, b, c = (float(v) for v in semi_axes)
    h = length / N
    axis = (np.arange(N) + 0.5) * h - length / 2.0
    X, Y, Z = np.meshgrid(axis, axis, axis, indexing="ij")
    inside = (X / a) ** 2 + (Y / b) ** 2 + (Z / c) ** 2 <= 1.0
    dV = h ** 3
    second = np.zeros((3, 3))
    first = np.array([X, Y, Z])
    for i in range(3):
        for j in range(3):
            second[i, j] = float((first[i] * first[j] * inside).sum() * dV)
    quad_num = second - np.trace(second) / 3.0 * np.eye(3)
    vol = 4.0 / 3.0 * np.pi * a * b * c
    exact = np.zeros((3, 3))
    sq = (a * a, b * b, c * c)
    for i in range(3):
        exact[i, i] = vol * (sq[i] - sum(sq) / 3.0) / 5.0
    return quad_num, exact


def rotate_lattice_field(N: int, length: float = 1.0, amplitude: float = 0.5) -> Dict[str, np.ndarray]:
    """构造旋转 90°（绕 z）前后的密度场与体积四极子，用于群/旋转不变性判据。

    场为 ρ = 1 + A cos(2πx/L)，绕 z 旋转 90° 后沿 y 调制。
    """
    h = length / N
    axis = (np.arange(N) + 0.5) * h - length / 2.0
    X, Y, Z = np.meshgrid(axis, axis, axis, indexing="ij")
    rho_x = 1.0 + amplitude * np.cos(2.0 * np.pi * X / length)
    rho_y = 1.0 + amplitude * np.cos(2.0 * np.pi * Y / length)
    dV = h ** 3

    def quad(rho: np.ndarray) -> np.ndarray:
        second = np.array([
            [(X * X * rho).sum(), (X * Y * rho).sum(), (X * Z * rho).sum()],
            [(Y * X * rho).sum(), (Y * Y * rho).sum(), (Y * Z * rho).sum()],
            [(Z * X * rho).sum(), (Z * Y * rho).sum(), (Z * Z * rho).sum()],
        ]) * dV
        return second - np.trace(second) / 3.0 * np.eye(3)

    return {"q_before": quad(rho_x), "q_after": quad(rho_y)}


# --------------------------------------------------------------------------- #
# 斜槽（skewed slot）度量拉普拉斯：剪切网格上的二阶差分
# --------------------------------------------------------------------------- #


def skew_slot_laplacian_error(N: int, shear: float, length: float = 1.0) -> float:
    """剪切（斜槽）网格上度量张量拉普拉斯算子的离散误差（max 范数）。

    计算坐标 ξ 与物理坐标 x = A ξ, A = [[1,0],[s,1]]（斜槽剪切映射）。物理
    拉普拉斯 = G_ij ∂²_{ξiξj}，G = A⁻¹A⁻ᵀ；差分格式（中心二阶）与精确值对比，
    用于测**观测阶**。周期域，无边界污染。
    """
    h = length / N
    xi = np.arange(N) * h
    XI, ETA = np.meshgrid(xi, xi, indexing="ij")
    k = 2.0 * np.pi / length
    field = np.sin(k * XI) * np.cos(k * ETA)

    a_mat = np.array([[1.0, 0.0], [shear, 1.0]])
    g = np.linalg.inv(a_mat) @ np.linalg.inv(a_mat).T

    d2x = (np.roll(field, 1, 0) - 2.0 * field + np.roll(field, -1, 0)) / h ** 2
    d2y = (np.roll(field, 1, 1) - 2.0 * field + np.roll(field, -1, 1)) / h ** 2
    dxy = (np.roll(np.roll(field, 1, 0), 1, 1) - np.roll(np.roll(field, 1, 0), -1, 1)
           - np.roll(np.roll(field, -1, 0), 1, 1) + np.roll(np.roll(field, -1, 0), -1, 1)) / (4.0 * h ** 2)
    lap = g[0, 0] * d2x + 2.0 * g[0, 1] * dxy + g[1, 1] * d2y

    sin_x = np.sin(k * XI)
    cos_x = np.cos(k * XI)
    sin_y = np.sin(k * ETA)
    cos_y = np.cos(k * ETA)
    lap_exact = (-k * k * (g[0, 0] + g[1, 1]) * sin_x * cos_y
                 - 2.0 * g[0, 1] * k * k * cos_x * sin_y)
    return float(np.max(np.abs(lap - lap_exact)))