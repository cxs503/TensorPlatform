"""Hull geometry closure helpers (几何发生/闭合一致性).

平台精度标准第一条是「几何必须先验证 Cb/L/B/T/∇」。历史上这条判据散落在各端点里各自
硬编码（例如 benchmark 预设把 SUBOFF 的 ``cb`` 直接写成 0.508 = 直径数值），互相矛盾且
不可机检。本模块把几何闭合判据集中成一处纯函数，供：

* ``api/calculations.py``      —— 阻力端点入口几何闸门；
* ``services/benchmark_integrity.py`` —— 基准数据集自洽性审计；
* ``scripts/audit_benchmark.py``      —— 离线可复现审计工件。

所有判据只依赖可复算的代数关系（Fn = V/√(gL)、Cb = ∇/(L·B·T)），不引入任何"文献值"
常数，因此不会有伪造风险。

证据分级口径（与平台一致）：
    PASS —— 判据可算且闭合；
    FAIL —— 判据可算但不闭合（可用于判定数据/预设不可信）；
    UNAVAILABLE —— 缺字段，无法判定（对应证据等级 WITHHELD）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

G_STANDARD = 9.81
RHO_MODEL_FRESH = 998.0   # 模型试验淡水 / 20 °C
RHO_SEAWATER = 1025.0     # 实船海水（用于 ρ 敏感性判定）

# 判定阈值（全部为相对量，无量纲）
FN_REL_TOL = 1.0e-3        # 声明 Fn vs V/√(gL) 的相对容差
CB_REL_TOL = 1.0e-2        # Cb = ∇/(L·B·T) 的相对容差（1%）
SURFACE_REL_TOL = 1.0e-2   # 两个湿表面积口径之间的相对容差

# 回转体 Cb 的几何允许区间：∇ ≤ 外切圆柱体积 (π/4)·D²·L ⇒ 0 < Cb ≤ π/4
CB_BAND_LO = 1.0e-3
CB_BAND_HI = math.pi / 4.0

PASS = "PASS"
FAIL = "FAIL"
UNAVAILABLE = "UNAVAILABLE"


class GeometryError(ValueError):
    """Raised when a geometry block violates a machine-checkable closure."""

    def __init__(self, reasons: List[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# ---------------------------------------------------------------------------
# Basic closures
# ---------------------------------------------------------------------------

def froude_number(speed_ms: float, length_m: float, g: float = G_STANDARD) -> float:
    """Fn = V / sqrt(g·L)."""
    if length_m <= 0:
        raise GeometryError([f"length must be > 0, got {length_m}"])
    return float(speed_ms) / math.sqrt(g * float(length_m))


def implied_length_from_froude(speed_ms: float, fn: float, g: float = G_STANDARD) -> float:
    """反解 Fn 对应的特征长度 L = (V/Fn)²/g —— 用于定位尺度不自洽。"""
    if fn <= 0:
        raise GeometryError([f"Froude number must be > 0, got {fn}"])
    return (float(speed_ms) / float(fn)) ** 2 / g


def cb_from_volume(volume_m3: float, length: float, beam: float, draft: float) -> float:
    """Cb = ∇ / (L·B·T)."""
    denom = float(length) * float(beam) * float(draft)
    if denom <= 0:
        raise GeometryError([f"L·B·T must be > 0, got {denom}"])
    return float(volume_m3) / denom


def volume_from_cb(cb: float, length: float, beam: float, draft: float) -> float:
    """∇ = Cb·L·B·T."""
    return float(cb) * float(length) * float(beam) * float(draft)


def displacement_tonnes(volume_m3: float, rho: float = RHO_MODEL_FRESH) -> float:
    """排水量（t）← 排水体积（m³），ρ 默认模型试验淡水 998 kg/m³。"""
    return float(volume_m3) * float(rho) / 1000.0


def axisymmetric_wetted_surface(cb: float, length: float, beam: float) -> float:
    """平台沿用的轴对称体湿表面积闭式：S = π·B·L·(0.5 + 0.25·Cb).

    该闭式是经验近似，不是解析式；本模块把它单独暴露出来，正是为了让它与声明值
    （试验报告给的湿表面积）之间的偏差可以被量化，而不是被隐式吸收进阻力误差。
    """
    return math.pi * float(beam) * float(length) * (0.5 + 0.25 * float(cb))


def relative_difference(value: float, reference: float) -> float:
    """相对偏差 |value - reference| / |reference|；reference 为 0 时返回 inf。"""
    if reference == 0:
        return float("inf")
    return abs(float(value) - float(reference)) / abs(float(reference))


# ---------------------------------------------------------------------------
# Case-level parsing of the benchmark dataset geometry block
# ---------------------------------------------------------------------------

def parse_case_geometry(case: Dict[str, Any], rho: float = RHO_MODEL_FRESH) -> Dict[str, Any]:
    """把 benchmark 数据集的 ``geometry`` 块规范化。

    支持的键（数据集中三种船型写法不同，这里统一）：
        length_m / beam_m / draft_m / diameter_m / wetted_surface_m2 /
        cb / displacement_kg / displacement_t
    返回字典含 ``missing``（无法得到的量）与 ``volume_m3`` / ``cb``（若可推导）。
    不臆造任何值：缺字段进 ``missing``，由调用方按 WITHHELD 处理。
    """
    geo = dict(case.get("geometry") or {})
    out: Dict[str, Any] = {
        "length_m": geo.get("length_m"),
        "beam_m": geo.get("beam_m", geo.get("diameter_m")),
        "draft_m": geo.get("draft_m", geo.get("diameter_m")),
        "wetted_surface_m2": geo.get("wetted_surface_m2"),
        "cb": geo.get("cb"),
        "volume_m3": None,
        "missing": [],
        "basis": {},
    }

    if out["beam_m"] is not None and "beam_m" not in geo:
        out["basis"]["beam_m"] = "diameter_m"
    if out["draft_m"] is not None and "draft_m" not in geo:
        out["basis"]["draft_m"] = "diameter_m"

    vol = geo.get("volume_m3")
    if vol is None and geo.get("displacement_kg") is not None:
        vol = float(geo["displacement_kg"]) / rho
        out["basis"]["volume_m3"] = f"displacement_kg/{rho}"
    if vol is None and geo.get("displacement_t") is not None:
        vol = float(geo["displacement_t"]) * 1000.0 / rho
        out["basis"]["volume_m3"] = f"displacement_t*1000/{rho}"
    out["volume_m3"] = vol

    for key in ("length_m", "beam_m", "draft_m"):
        if out[key] is None:
            out["missing"].append(key)

    # Cb 可由体积闭合推出（当与声明值同时存在时正是交叉校验的来源）
    out["cb_from_volume"] = None
    if vol is not None and not out["missing"]:
        out["cb_from_volume"] = cb_from_volume(vol, out["length_m"], out["beam_m"], out["draft_m"])
    if out["cb"] is None and out.get("cb_from_volume") is not None:
        out["cb"] = out["cb_from_volume"]
        out["basis"]["cb"] = "derived_from_volume"

    return out


# ---------------------------------------------------------------------------
# Geometry gate
# ---------------------------------------------------------------------------

def check_geometry(
    *,
    length: float,
    beam: float,
    draft: float,
    cb: Optional[float] = None,
    volume_m3: Optional[float] = None,
    wetted_surface_m2: Optional[float] = None,
    cb_rel_tol: float = CB_REL_TOL,
) -> Dict[str, Any]:
    """几何闸门：返回逐项判据，任何 FAIL 都意味着这组几何不可用于精度判定。"""
    checks: Dict[str, Any] = {}
    reasons: List[str] = []

    # (1) 正性与物理区间
    if not (length > 0 and beam > 0 and draft > 0):
        reasons.append(f"non-positive dimension: L={length} B={beam} T={draft}")
    if cb is not None and not (0.0 < cb <= 1.0):
        reasons.append(f"Cb={cb} outside (0, 1]")
    checks["positivity"] = PASS if not reasons else FAIL

    # (2) 长宽比 / 长深比的常识区间（只挡明显错配，例如把 320 m 船配 0.508 m 船宽）
    lb_ratio = length / beam if beam else float("inf")
    checks["L_over_B"] = round(lb_ratio, 4)
    if not (2.0 <= lb_ratio <= 20.0):
        reasons.append(f"L/B={lb_ratio:.3f} outside plausible [2, 20]")

    # (3) Cb 闭合：Cb ≡ ∇/(L·B·T)
    if cb is not None and volume_m3 is not None:
        cb_implied = cb_from_volume(volume_m3, length, beam, draft)
        rel = relative_difference(cb, cb_implied)
        checks["cb_closure"] = {
            "status": PASS if rel <= cb_rel_tol else FAIL,
            "cb_declared": round(float(cb), 6),
            "cb_implied": round(cb_implied, 6),
            "rel_err": float(f"{rel:.6e}"),
            "tolerance": cb_rel_tol,
        }
        if rel > cb_rel_tol:
            reasons.append(
                f"Cb closure violated: declared Cb={cb:.4f} vs ∇/(LBT)={cb_implied:.4f} "
                f"(rel {rel * 100:.2f}% > {cb_rel_tol * 100:.1f}%)"
            )
    else:
        checks["cb_closure"] = {"status": UNAVAILABLE, "reason": "Cb or displacement volume missing"}

    # (4) 湿表面积口径：声明值 vs 平台闭式
    if wetted_surface_m2 is not None and cb is not None:
        s_closure = axisymmetric_wetted_surface(cb, length, beam)
        rel = relative_difference(s_closure, wetted_surface_m2)
        checks["wetted_surface_closure"] = {
            "status": PASS if rel <= SURFACE_REL_TOL else FAIL,
            "declared_m2": round(float(wetted_surface_m2), 6),
            "closure_m2": round(s_closure, 6),
            "rel_bias": float(f"{rel:.6e}"),
            "tolerance": SURFACE_REL_TOL,
        }
    else:
        checks["wetted_surface_closure"] = {
            "status": UNAVAILABLE,
            "reason": "wetted area or Cb missing",
        }

    checks["ok"] = not reasons
    checks["violations"] = reasons
    return checks


def validate_geometry(**kwargs: Any) -> Dict[str, Any]:
    """几何闸门（抛异常版）：不闭合时抛 :class:`GeometryError`。"""
    result = check_geometry(**kwargs)
    if not result["ok"]:
        raise GeometryError(result["violations"])
    return result


def summarise_checks(checks: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """统计若干判据块里 PASS/FAIL/UNAVAILABLE 的个数。"""
    counts = {PASS: 0, FAIL: 0, UNAVAILABLE: 0}
    for check in checks:
        status = (check or {}).get("status")
        if status in counts:
            counts[status] += 1
    return counts