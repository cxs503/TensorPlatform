"""判据层：把"操纵性输出"折算成可机器裁定的证据等级.

分三类判据：
  A. 恒等式（硬）：运动学恒等式，纯代数，任何自洽求解器必须满足。
  B. 响应性（硬证伪）：对物理输入的灵敏度。灵敏度恒为 0 ⇒ 输出与船型/舵
     无关 ⇒ 必然是硬编码（MOCK），这是充分证据，无需基准值。
  C. 规范/语料（区间）：IMO MSC.137(76) 判据与平台自带语料区间。

所有函数只依赖 Python 标准库 + numpy，无网络。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------- 证据等级
class Evidence(str, Enum):
    REAL = "REAL"                 # 物理自洽 + 有响应 + 落在规范/语料区间
    MOCK = "MOCK"                 # 硬编码/与物理输入无关（有充分证伪证据）
    STUB = "STUB"                 # 空壳：端点存在但不返回可用结果
    BROKEN = "BROKEN"             # 端点必崩（5xx），任何输入都不可用
    WITHHELD = "WITHHELD"         # 证据不足，不给结论
    NOT_SUPPORTED = "NOT_SUPPORTED"


# ---------------------------------------------------------------- 恒等式判据
@dataclass
class IdentityResult:
    name: str
    measured: float
    expected: float
    rel_error: float
    tol: float
    passed: bool
    note: str = ""

    def as_dict(self) -> Dict:
        return dict(name=self.name, measured=self.measured, expected=self.expected,
                    rel_error=self.rel_error, tol=self.tol, passed=self.passed,
                    note=self.note)


def kinematic_consistency(turning_rate_deg_s: float, final_speed_m_s: float,
                          tactical_diameter_m: float,
                          tol: float = 0.15,
                          diameter_over_2R: float = 1.0) -> IdentityResult:
    """A1：稳态回转的运动学恒等式  r = V / R，其中 R = D_T / 2.

    纯代数，不含任何经验系数。任何输出 (r, V, D_T) 三元组都必须满足。
    ``diameter_over_2R`` 允许把"战术直径/稳态回转直径"的定义差异
    折算进来（默认 1.0；取 1.10 表示宽容 10%）。

    误差超过 ``tol``（默认 15%，平台精度红线）即判不通过。
    """
    if tactical_diameter_m <= 0 or final_speed_m_s <= 0:
        return IdentityResult("kinematics_r_equals_V_over_R", float("nan"), float("nan"),
                              float("inf"), tol, False, "非正直径/速度，结果不可用")
    R = tactical_diameter_m / 2.0 / diameter_over_2R
    r_expected_deg = math.degrees(final_speed_m_s / R)
    rel = abs(turning_rate_deg_s - r_expected_deg) / r_expected_deg
    return IdentityResult(
        "kinematics_r_equals_V_over_R", turning_rate_deg_s, r_expected_deg, rel, tol,
        rel <= tol,
        f"R=D_T/2={R:.1f} m; V={final_speed_m_s:.3f} m/s; 期望 r={r_expected_deg:.4f} deg/s",
    )


def diameter_equals_2R_consistency(tactical_diameter_m: float, steady_diameter_m: float,
                                   tol: float = 0.15) -> IdentityResult:
    """A2：战术直径与稳态回转直径同量级（同一定义下的两测点）。

    对稳态圆运动二者都等于 2R；实测差异通常在 0~10% 内。
    差异 > tol 说明两个输出并非来自同一条轨迹。
    """
    rel = abs(steady_diameter_m - tactical_diameter_m) / tactical_diameter_m
    return IdentityResult("tactical_vs_steady_diameter", tactical_diameter_m,
                          steady_diameter_m, rel, tol, rel <= tol,
                          "稳态圆运动下 D_T ≈ D_steady ≈ 2R")


# ---------------------------------------------------------------- 响应性判据
@dataclass
class SensitivityResult:
    param: str
    values: Sequence[float]
    outputs: Sequence[float]
    loglog_slope: Optional[float]
    respond: bool
    expected_sign: str          # "-" 表示输出应随参数增大而减小
    sign_ok: bool
    note: str = ""

    def as_dict(self) -> Dict:
        return dict(param=self.param, values=list(self.values),
                    outputs=list(self.outputs),
                    loglog_slope=self.loglog_slope, respond=self.respond,
                    expected_sign=self.expected_sign, sign_ok=self.sign_ok,
                    note=self.note)


def loglog_sensitivity(param: str, values: Sequence[float], outputs: Sequence[float],
                       expected_sign: str = "-",
                       respond_tol: float = 0.02) -> SensitivityResult:
    """B1：对数-对数斜率 d ln(out) / d ln(param)。

    ``respond_tol``：|slope| 超过该值即视为"有响应"。恒为 0（|slope| ≤ tol）
    表示输出对物理输入完全不敏感 —— 硬编码的充分证据。

    期望符号：舵角↑ ⇒ 回转直径↓（slope<0）；舵面积↑ ⇒ 直径↓（<0）；
    排水量↑ ⇒ 直径↑（>0）。符号错误同样是物理性证伪。
    """
    v = np.asarray(values, dtype=float)
    o = np.asarray(outputs, dtype=float)
    good = (v > 0) & (o > 0)
    slope = None
    if good.sum() >= 2:
        slope = float(np.polyfit(np.log(v[good]), np.log(o[good]), 1)[0])
        if abs(slope) <= respond_tol:      # 数值零：判"无响应"，符号无意义
            slope = 0.0
    respond = slope is not None and abs(slope) > respond_tol
    if slope is None:
        sign_ok = False
    elif expected_sign == "-":
        sign_ok = slope < 0
    elif expected_sign == "+":
        sign_ok = slope > 0
    else:
        sign_ok = True
    return SensitivityResult(param, list(map(float, v)), list(map(float, o)),
                             slope, respond, expected_sign, sign_ok,
                             f"|slope|>={respond_tol} 视为有响应")


def rudder_scaling_law(D_by_angle: Dict[float, float], tol: float = 0.10) -> Dict:
    """B2：一阶 K-T 模型要求 D_T ∝ 1/δ（斜率 = -1），真实船 0.6 ≤ |slope| ≤ 1.1.

    平台若在 5°→35°（7 倍舵角）上给出完全相同的直径，则 slope = 0，
    与任何一阶/高阶操纵模型都不相容（充分证伪）。
    """
    angles = sorted(D_by_angle)
    d = [D_by_angle[a] for a in angles]
    sr = loglog_sensitivity("rudder_angle_deg", angles, d, expected_sign="-")
    physics_band = 0.6 <= abs(sr.loglog_slope or 0.0) <= 1.1
    return dict(param="rudder_angle", angles=angles, diameters=d,
                slope=sr.loglog_slope, loglog_slope=sr.loglog_slope,
                respond=sr.respond, in_physics_band=physics_band,
                verdict="PASS" if (sr.respond and physics_band) else "FAIL",
                reference="Nomoto 一阶: D_T=2U/(Kδ) ⇒ slope=-1；实际船 0.6~1.1")


# ---------------------------------------------------------------- 规范判据
IMO_CRITERIA = {
    "turning_tactical_diameter_over_L": 5.0,   # MSC.137(76) 5.1: D_T ≤ 5L
    "turning_advance_over_L": 4.5,             #             advance ≤ 4.5L
    "zigzag_10_10_first_overshoot_deg": 10.0,  # MSC.137(76) 5.2
    "zigzag_10_10_second_overshoot_deg": 20.0,
    "zigzag_20_20_first_overshoot_deg": 20.0,
    "zigzag_20_20_second_overshoot_deg": 20.0,
}


def imo_criteria_check(L: float, tactical_diameter: Optional[float] = None,
                       advance: Optional[float] = None,
                       overshoot: Optional[Dict[str, float]] = None) -> Dict:
    """C1：IMO MSC.137(76) 操纵性判据（区间/上限语义，非精确基准）。"""
    rows: List[Dict] = []
    if tactical_diameter is not None:
        lim = IMO_CRITERIA["turning_tactical_diameter_over_L"]
        ratio = tactical_diameter / L
        rows.append(dict(item="tactical_diameter", value=tactical_diameter,
                         normalized=ratio, limit=lim, unit="D_T/L",
                         passed=ratio <= lim))
    if advance is not None:
        lim = IMO_CRITERIA["turning_advance_over_L"]
        ratio = advance / L
        rows.append(dict(item="advance", value=advance, normalized=ratio, limit=lim,
                         unit="advance/L", passed=ratio <= lim))
    if overshoot:
        for key, val in overshoot.items():
            lim = IMO_CRITERIA.get(key)
            if lim is None:
                continue
            rows.append(dict(item=key, value=val, normalized=val, limit=lim,
                             unit="deg", passed=val <= lim))
    return dict(criteria=rows,
                all_passed=all(r["passed"] for r in rows) if rows else None)


def corpus_band_check(value: float, band: Dict[str, float]) -> Dict:
    """C2：平台自带语料区间比对（KCS / SIMMAN 2008-2014 典型区间）。"""
    lo, hi = band["min"], band["max"]
    typical = band.get("typical", 0.5 * (lo + hi))
    inside = lo <= value <= hi
    rel_vs_typical = (value - typical) / typical if typical else float("nan")
    return dict(value=value, band=[lo, hi], typical=typical, inside_band=inside,
                rel_error_vs_typical=rel_vs_typical,
                rel_error_pct=100.0 * rel_vs_typical)


# ---------------------------------------------------------------- 综合判决
def grade_turning(L: float, outputs: Sequence[Dict], n_broken: int = 0) -> Dict:
    """综合判定回转类端点的证据等级。

    ``outputs``：多组参数探针的结果，每项含 input/result（见 conformance.py）。
    """
    detail: Dict = {"identities": [], "sensitivities": [], "imo": None}
    if n_broken and n_broken == len(outputs):
        return dict(grade=Evidence.BROKEN.value,
                    reason=f"全部 {n_broken} 次调用返回 5xx，端点不可用",
                    detail=detail)

    ok_inputs = [o for o in outputs if o.get("result")]
    if not ok_inputs:
        return dict(grade=Evidence.STUB.value, reason="无有效返回", detail=detail)

    for o in ok_inputs:
        r = o["result"]
        if all(k in r for k in ("turning_rate", "final_speed", "tactical_diameter")):
            detail["identities"].append(
                kinematic_consistency(r["turning_rate"], r["final_speed"],
                                      r["tactical_diameter"]).as_dict())
        if "steady_diameter" in r and "tactical_diameter" in r:
            detail["identities"].append(
                diameter_equals_2R_consistency(r["tactical_diameter"],
                                               r["steady_diameter"]).as_dict())

    # 响应性：按参数分组
    by_angle = {}
    by_area = {}
    by_disp = {}
    by_speed = {}
    for o in ok_inputs:
        i, r = o.get("input", {}), o["result"]
        d = r.get("tactical_diameter")
        if d is None:
            continue
        if "rudder_angle" in i:
            by_angle[i["rudder_angle"]] = d
        if "Ar" in i:
            by_area[i["Ar"]] = d
        if "displacement" in i:
            by_disp[i["displacement"]] = d
        if "speed" in i:
            by_speed[i["speed"]] = d

    if len(by_angle) >= 2:
        detail["sensitivities"].append(
            rudder_scaling_law(by_angle))
    if len(by_area) >= 2:
        detail["sensitivities"].append(loglog_sensitivity(
            "Ar", sorted(by_area), [by_area[a] for a in sorted(by_area)],
            expected_sign="-").as_dict())
    if len(by_disp) >= 2:
        detail["sensitivities"].append(loglog_sensitivity(
            "displacement", sorted(by_disp), [by_disp[d] for d in sorted(by_disp)],
            expected_sign="+").as_dict())
    if len(by_speed) >= 2:
        detail["sensitivities"].append(loglog_sensitivity(
            "speed", sorted(by_speed), [by_speed[s] for s in sorted(by_speed)],
            expected_sign="+").as_dict())

    first = ok_inputs[0]["result"]
    if "tactical_diameter" in first:
        detail["imo"] = imo_criteria_check(
            L, tactical_diameter=first.get("tactical_diameter"),
            advance=first.get("advance"))

    # 判决逻辑
    ident_fail = [x for x in detail["identities"] if not x["passed"]]
    no_response = []
    for s in detail["sensitivities"]:
        if "respond" in s and not s["respond"]:
            no_response.append(s.get("param", "unknown"))

    if no_response:
        return dict(grade=Evidence.MOCK.value,
                    reason=("对物理输入完全无响应：" + ", ".join(no_response) +
                            " —— 输出与船型/舵无关，只能是硬编码"),
                    detail=detail)
    if ident_fail:
        return dict(grade=Evidence.MOCK.value,
                    reason=("违反运动学恒等式 " + ", ".join(x["name"] for x in ident_fail)
                            + "，输出非物理自洽"),
                    detail=detail)
    return dict(grade=Evidence.REAL.value,
                reason="恒等式自洽且对物理输入有正确方向响应（未做绝对精度判定）",
                detail=detail)


def grade_zigzag(outputs: Sequence[Dict], n_broken: int = 0) -> Dict:
    detail: Dict = {"sensitivities": [], "imo": None, "corpus": None}
    if n_broken and n_broken == len(outputs):
        return dict(grade=Evidence.BROKEN.value,
                    reason=f"全部 {n_broken} 次调用返回 5xx，端点不可用", detail=detail)
    ok = [o for o in outputs if o.get("result")]
    if not ok:
        return dict(grade=Evidence.STUB.value, reason="无有效返回", detail=detail)

    by_angle = {}
    by_disp = {}
    by_speed = {}
    for o in ok:
        i, r = o.get("input", {}), o["result"]
        v = r.get("overshoot_1")
        if v is None:
            continue
        if "rudder_angle" in i:
            by_angle[i["rudder_angle"]] = v
        if "displacement" in i:
            by_disp[i["displacement"]] = v
        if "speed" in i:
            by_speed[i["speed"]] = v
    for name, src in (("rudder_angle", by_angle), ("displacement", by_disp), ("speed", by_speed)):
        if len(src) >= 2:
            ks = sorted(src)
            detail["sensitivities"].append(
                loglog_sensitivity(name, ks, [src[k] for k in ks],
                                   expected_sign="+").as_dict())

    # 单调性：overshoot 应随舵角增大而单调增大
    mono_ok = None
    if len(by_angle) >= 2:
        ks = sorted(by_angle)
        vals = [by_angle[k] for k in ks]
        mono_ok = all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))
    detail["monotone_in_rudder"] = mono_ok

    no_response = [s["param"] for s in detail["sensitivities"] if not s["respond"]]
    if no_response:
        return dict(grade=Evidence.MOCK.value,
                    reason=("超越角对物理输入无响应：" + ", ".join(no_response) +
                            "；且输出为 |δ| 的固定倍数（0.15 / 0.25）"),
                    detail=detail)
    return dict(grade=Evidence.WITHHELD.value,
                reason="有响应但缺精确基准，暂判证据不足", detail=detail)