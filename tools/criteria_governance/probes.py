"""探针注册表：判据 → 可执行数值仪器的映射（名字 + 参数 → 测量值）。

探针本身是**通用数值仪器**（张量矩、对称群、细化扫描），参数全部来自判据工件；
领域内容（哪条判据用哪个探针、阈值多少）由 ``data/criteria_governance/*.json`` 注入，
本模块不出现任何领域判据文字。

每个探针返回::

    {"kind": "identity"|"refinement",
     "value": float,                 # 与 threshold 比较的量
     "detail": {...},                # 审计细节（可溯源）
     "hs": [...], "errors": [...],   # 仅 refinement：细化扫描序列
     "observed_exponent": float}     # 仅 refinement：实测衰减指数
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

from . import kernels

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "criteria_governance"


def _lattice(name: str):
    return kernels.load_lattice(DATA_DIR, name)


# --------------------------------------------------------------------------- #
# 恒等式类探针
# --------------------------------------------------------------------------- #


def lattice_moment_isotropy(params: Dict) -> Dict:
    """权重集 order 阶矩与解析各向同性矩的相对最大误差（算子级恒等式残差）。"""
    velocities, weights, dim = _lattice(params["lattice"])
    order = int(params.get("order", 4))
    cs2 = float(params.get("cs2", 1.0 / 3.0))
    measured = kernels.moment_tensor(weights, velocities, order)
    exact = kernels.isotropic_moment(order, dim, cs2)
    err = kernels.rel_max_error(measured, exact)
    return {"kind": "identity", "value": err,
            "detail": {"lattice": params["lattice"], "order": order, "dim": dim,
                       "max_measured": float(np.max(np.abs(measured))),
                       "max_analytic": float(np.max(np.abs(exact)))}} 


def weight_normalisation(params: Dict) -> Dict:
    """多权重集权重和归一化最大偏差 |Σw − 1|。"""
    names = params["lattices"] if "lattices" in params else [params["lattice"]]
    worst, per = 0.0, {}
    for name in names:
        _, weights, _ = _lattice(name)
        dev = abs(float(weights.sum()) - 1.0)
        per[name] = dev
        worst = max(worst, dev)
    return {"kind": "identity", "value": worst, "detail": {"per_lattice": per}}


def second_moment_isotropy(params: Dict) -> Dict:
    """二阶矩 vs c_s²δ 的相对最大误差。"""
    velocities, weights, dim = _lattice(params["lattice"])
    cs2 = float(params.get("cs2", 1.0 / 3.0))
    measured = kernels.moment_tensor(weights, velocities, 2)
    err = kernels.rel_max_error(measured, kernels.isotropic_moment(2, dim, cs2))
    return {"kind": "identity", "value": err,
            "detail": {"lattice": params["lattice"], "cs2": cs2}}


def trace_free_residual(params: Dict) -> Dict:
    """四极子无迹残差 |tr Q| / max|Q|。"""
    velocities, weights, dim = _lattice(params["lattice"])
    cs2 = float(params.get("cs2", 1.0 / 3.0))
    second = kernels.moment_tensor(weights, velocities, 2)
    quad = kernels.quadrupole_from_second_moment(second, cs2)
    scale = abs(float(np.trace(second)))  # 物理矩量级（≈1），避免 0/0 放大舍入噪声
    return {"kind": "identity", "value": kernels.trace_free_residual(quad, scale=scale),
            "detail": {"lattice": params["lattice"], "trace_quadrupole": float(np.trace(quad)),
                       "trace_second_moment": float(np.trace(second)),
                       "residual_scale": scale}}


def rotation_invariance_90(params: Dict) -> Dict:
    """90°（绕 z）旋转下体积四极子的协变残差。"""
    N = int(params.get("resolution", 48))
    length = float(params.get("length", 1.0))
    amplitude = float(params.get("amplitude", 0.5))
    fields = kernels.rotate_lattice_field(N, length=length, amplitude=amplitude)
    rot = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    q_before = np.eye(3) @ fields["q_before"] @ np.eye(3).T
    q_rot = rot @ q_before @ rot.T
    residual = kernels.rel_max_error(q_rot, fields["q_after"])
    return {"kind": "identity", "value": residual,
            "detail": {"resolution": N,
                       "q_before_xx": float(fields["q_before"][0, 0]),
                       "q_after_yy": float(fields["q_after"][1, 1])}}


def cubic_group_closure(params: Dict) -> Dict:
    """超八面体群（O_h，48 元）的闭包/逆元精确性残差。"""
    dim = int(params.get("dim", 3))
    ops = kernels.signed_permutation_group(dim)
    residual = kernels.group_closure_residual(ops)
    return {"kind": "identity", "value": residual,
            "detail": {"group_order": len(ops), "dim": dim}}


def rank4_isotropic_invariance(params: Dict) -> Dict:
    """各向同性 4 阶张量在全群下的不变残差。"""
    dim = int(params.get("dim", 3))
    cs2 = float(params.get("cs2", 1.0 / 3.0))
    ops = kernels.signed_permutation_group(dim)
    tensor = kernels.isotropic_rank4(dim, cs2)
    return {"kind": "identity", "value": kernels.invariance_residual(ops, tensor, 4),
            "detail": {"group_order": len(ops), "dim": dim}}


def invariant_subspace_dimension(params: Dict) -> Dict:
    """Sym(dim) 上平凡表示重数（应恰为 1）。"""
    dim = int(params.get("dim", 3))
    ops = kernels.signed_permutation_group(dim)
    k = kernels.invariant_subspace_dimension(ops, dim)
    return {"kind": "identity", "value": float(k),
            "detail": {"group_order": len(ops), "dim": dim}}


def inversion_parity(params: Dict) -> Dict:
    """空间反演 (−I) 下 2 阶对称张量的宇称残差（偶 → 0）。"""
    dim = int(params.get("dim", 3))
    tensor = kernels.isotropic_rank2(dim)
    ops = [np.eye(dim), -np.eye(dim)]
    return {"kind": "identity", "value": kernels.invariance_residual(ops, tensor, 2),
            "detail": {"dim": dim, "rank": 2}}


def ellipsoid_quadrupole_error(params: Dict) -> Dict:
    """椭球体积四极子：中点采样 vs 闭式解（经验型：数值实测容差）。"""
    N = int(params.get("resolution", 64))
    length = float(params.get("length", 1.0))
    semi = params["semi_axes"]
    quad_num, exact = kernels.ellipsoid_quadrupole(N, semi, length=length)
    return {"kind": "identity", "value": kernels.rel_max_error(quad_num, exact),
            "detail": {"resolution": N, "semi_axes": list(semi),
                       "q_xx_numeric": float(quad_num[0, 0]),
                       "q_xx_exact": float(exact[0, 0])}}


# --------------------------------------------------------------------------- #
# 细化扫描类探针（观测阶）
# --------------------------------------------------------------------------- #


def quadrupole_refinement(params: Dict) -> Dict:
    """体积四极子的中点求积误差随 h 的衰减（声明阶在判据工件里）。"""
    resolutions = [int(n) for n in params.get("resolutions", [16, 32, 64, 128])]
    length = float(params.get("length", 1.0))
    amplitude = float(params.get("amplitude", 0.5))
    exact = kernels.quadrupole_midpoint_exact(length, amplitude)
    scale = float(np.max(np.abs(exact)))

    def err(n: int) -> float:
        quad, _ = kernels.quadrupole_midpoint(n, length, amplitude)
        return float(np.max(np.abs(quad - exact))) / scale

    scan = kernels.refinement_scan(err, resolutions, length)
    scan.update({"kind": "refinement", "value": scan["errors"][-1],
                 "detail": {"length": length, "amplitude": amplitude,
                            "exact_q_xx": float(exact[0, 0]),
                            "exact_q_yy": float(exact[1, 1]),
                            "final_relative_error": scan["errors"][-1]}})
    return scan


def skew_slot_refinement(params: Dict) -> Dict:
    """斜槽剪切网格上度量拉普拉斯算子的观测阶。"""
    resolutions = [int(n) for n in params.get("resolutions", [16, 32, 64, 128])]
    shear = float(params.get("shear", 0.5))
    length = float(params.get("length", 1.0))
    scan = kernels.refinement_scan(
        lambda n: kernels.skew_slot_laplacian_error(n, shear, length), resolutions, length)
    scan.update({"kind": "refinement", "value": scan["errors"][-1],
                 "detail": {"shear": shear, "length": length,
                            "final_error": scan["errors"][-1]}})
    return scan


def shear_sweep_order_stability(params: Dict) -> Dict:
    """剪切扫描下观测阶的极差 max p − min p（经验型稳定性判据）。"""
    resolutions = [int(n) for n in params.get("resolutions", [16, 32, 64, 128])]
    shears = [float(s) for s in params.get("shears", [0.0, 0.2, 0.3, 0.5, 0.8])]
    length = float(params.get("length", 1.0))
    per: Dict[str, float] = {}
    for s in shears:
        scan = kernels.refinement_scan(
            lambda n, s=s: kernels.skew_slot_laplacian_error(n, s, length), resolutions, length)
        per[f"{s:.2f}"] = scan["observed_exponent"]
    values = list(per.values())
    return {"kind": "identity", "value": float(max(values) - min(values)),
            "detail": {"per_shear_exponent": per, "shears": shears}}


def shear_error_constant_ratio(params: Dict) -> Dict:
    """剪切误差常数比 C(s)/C(0)（经验型：斜槽畸变放大预算）。"""
    resolutions = [int(n) for n in params.get("resolutions", [16, 32, 64, 128])]
    shear = float(params.get("shear", 0.5))
    exponent = float(params.get("exponent", 2.0))
    length = float(params.get("length", 1.0))

    constants = {}
    for s in (0.0, shear):
        scan = kernels.refinement_scan(
            lambda n, s=s: kernels.skew_slot_laplacian_error(n, s, length), resolutions, length)
        constants[f"{s:.2f}"] = kernels.error_constant(scan["hs"], scan["errors"], exponent)
    ratio = constants[f"{shear:.2f}"] / constants["0.00"]
    return {"kind": "identity", "value": float(ratio),
            "detail": {"constants": constants, "shear": shear, "exponent": exponent}}



# --------------------------------------------------------------------------- #
# 来源绑定类探针（阈值注册表 ↔ 现网代码常量，零领域语义）
# --------------------------------------------------------------------------- #
def source_constant(params: Dict) -> Dict:
    """读取指定源文件里某个模块级数值常量，用于"注册阈值 == 源码阈值"的机器裁决。

    只做 AST 解析（不 import 目标文件，避免副作用）；路径与符号名一律来自 ``params``。
    """
    import ast
    from pathlib import Path

    rel = str(params["path"])
    symbol = str(params["symbol"])
    root = Path(__file__).resolve().parents[2]
    target = Path(rel)
    if not target.is_absolute():
        target = root / rel
    if not target.is_file():
        raise FileNotFoundError(f"source file missing: {rel}")
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if symbol not in names:
            continue
        value = node.value
        sign = 1.0
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
            sign, value = -1.0, value.operand
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) \
                and not isinstance(value.value, bool):
            return {"kind": "identity", "value": sign * float(value.value),
                    "detail": {"path": rel, "symbol": symbol, "line": node.lineno}}
    raise KeyError(f"symbol {symbol!r} not found as numeric constant in {rel}")


PROBES: Dict[str, Callable[[Dict], Dict]] = {
    "lattice_moment_isotropy": lattice_moment_isotropy,
    "weight_normalisation": weight_normalisation,
    "second_moment_isotropy": second_moment_isotropy,
    "trace_free_residual": trace_free_residual,
    "rotation_invariance_90": rotation_invariance_90,
    "cubic_group_closure": cubic_group_closure,
    "rank4_isotropic_invariance": rank4_isotropic_invariance,
    "invariant_subspace_dimension": invariant_subspace_dimension,
    "inversion_parity": inversion_parity,
    "ellipsoid_quadrupole_error": ellipsoid_quadrupole_error,
    "quadrupole_refinement": quadrupole_refinement,
    "skew_slot_refinement": skew_slot_refinement,
    "shear_sweep_order_stability": shear_sweep_order_stability,
    "shear_error_constant_ratio": shear_error_constant_ratio,
    "source_constant": source_constant,
}


def available_probes() -> List[str]:
    return sorted(PROBES)