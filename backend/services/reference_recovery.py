"""真值可供给性重判（truth-supply recovery adjudication）— m1641

正典 ``backend/services/benchmark_canon.py``（v3.0）回答的是
「**已落盘的数字**在解析恒等式下是否自洽」；12 行里有 8 行因此被判 ``WITHHELD``。
本模块回答互补的第二个问题：

    若允许**补一个独立的湿表面积 S**（这是文献里最容易补到的量），
    这一行能不能救回来？还是无论 S 取船型物理带内何值都不可能自洽？

把一句 ``WITHHELD`` 变成一份**可派单的供数清单**（谁缺什么量、要以多大不确定度补）
是平台「验证欠账」从口号变成工单的唯一机器化路径。

判据（全部由归档数字算出，不引入任何未溯源的文献数字）
--------------------------------------------------------
- **R1 反解** ``S_implied = R_t / (C_t · ½ ρ V²)``（精确，无近似）
- **物理带** ``S_band = S_hull · (1 ± BAND)``：``S_hull`` 由
  Holtrop–Mennen (1982) 湿表面积回归给出（`cm`/`cwp` 缺省时由其内建回归派生并
  在 ``derived`` 里留痕），``BAND = 0.15`` 为回归离散度声明值；迴转体
  （SUBOFF）不用 Holtrop（那是排水型船体的回归），改用归档的实测 S 或
  迴转体近似式，并显式标注 ``basis``。
- **供给判定**：``S_implied ∈ S_band`` → ``RECOVERABLE_BY_SURFACE``（补 S 即可救回）；
  否则 → ``IRRECOVERABLE_FILED_RT``（补 S 无用，必须重新供 Rt 或 Ct）。
  缺 Rt 或 Ct → ``NEEDS_DATUM``。
- **失败模式假设扫描**：对每个候选误用模式（航速 kn↔m/s、模型尺度 S、淡水/海水密度、
  湿面积折半、只记摩擦分量、Ct 实为剩余系数）算 ``Rt_predicted`` 与归档 Rt 的相对
  误差，``|err| ≤ 15 %`` 才「未被否决」。全被否决 → 归档值无法由单一误用模式解释。
- **速度歧义鲁棒性**：DTMB-5415 的航速是「按 Fr 反算的条件值」，原文另有实测航速
  （在 ``legacy_values.filed_speed_ms``）。判定必须在**两种速度解释下同结论**才算稳。

供数容差预算
------------
15 % 精度闸门要留给「求解器自身误差」，真值必须先占一小份预算。本模块按
``truth ≤ 15 % / 3 = 5 %`` 给出每个 ``datum_request`` 的 ``required_uncertainty_pct``，
并显式写出这条分配理由（不隐藏自由度）。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import benchmark_canon as canon
from . import holtrop_mennen as hm
from .ship_resistance import ittc57_cf

REPO_ROOT = Path(__file__).resolve().parents[2]
CANON_PATH = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

#: Holtrop–Mennen (1982) 湿表面积回归的声明离散度（±15 %）。
#: 取 15 % 而非更紧的 5 %：回归式在 Cb>0.8 的肥大型船体上残差本来就大，
#: 收紧带宽只会把「本来可救」的行误判成不可救（假阴性）。
SURFACE_BAND = 0.15

#: 精度闸门（%）。平台标准：对标实验/参考值误差 >15 % 不可接受。
ACCURACY_GATE_PCT = 15.0

#: 真值允许占用的误差预算比例：真值不确定度 ≤ 闸门 / 3。
TRUTH_BUDGET_DIVISOR = 3.0
TRUTH_UNCERTAINTY_PCT = ACCURACY_GATE_PCT / TRUTH_BUDGET_DIVISOR  # = 5.0

# 判定取值
USABLE = "USABLE"
RECOVERABLE_BY_SURFACE = "RECOVERABLE_BY_SURFACE"
IRRECOVERABLE_FILED_RT = "IRRECOVERABLE_FILED_RT"
NEEDS_DATUM = "NEEDS_DATUM"

#: 平台指南 KPI「对标算例 ≥10（SUBOFF/DTMB/KVLCC2）」。
BENCHMARK_KPI_TARGET = 10

KNOT = 0.5144444444444445


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def froude(speed_ms: float, length_m: float, g: float = 9.80665) -> float:
    return speed_ms / math.sqrt(g * length_m)


def resistance_from_ct(ct: float, speed_ms: float, s_wet: float, rho: float) -> float:
    """R1：``R_t = C_t · ½ ρ V² S``。"""
    return ct * 0.5 * rho * speed_ms ** 2 * s_wet


def implied_surface(ct: float, speed_ms: float, rt_n: float, rho: float) -> float:
    """R1 反解：由 (C_t, V, R_t) 唯一确定湿面积。"""
    denom = ct * 0.5 * rho * speed_ms ** 2
    if denom <= 0.0:
        raise ValueError("implied_surface: non-positive denominator")
    return rt_n / denom


def hull_surface_estimate(case: Dict[str, Any]) -> Dict[str, Any]:
    """船型湿面积的独立估计 + 溯源（不含任何文献硬编码数字）。

    排水型船体：Holtrop–Mennen (1982) 回归；'迴转体'：归档实测 S 优先，
    否则迴转体近似式（``π D L (0.5 + 0.25 Cb)``）。
    """
    geo = case.get("geometry") or {}
    length = float(geo.get("length_m"))
    form = str(case.get("form_type") or "")

    if form == "body_of_revolution":
        beam = float(geo.get("diameter_m") or 0.0)
        cb = float(geo.get("cb") or 0.5)
        declared = geo.get("wetted_surface_m2")
        if declared:
            return {
                "s_m2": float(declared),
                "basis": "archived_measured_surface",
                "derivation": f"归档实测湿面积 S={float(declared)} m²（唯一锚定量）",
                "derived": {},
            }
        s = math.pi * beam * length * (0.5 + 0.25 * cb)
        return {
            "s_m2": s,
            "basis": "axisymmetric_estimate",
            "derivation": "π·D·L·(0.5+0.25·Cb)",
            "derived": {"cb": "缺省 0.5（未提供）"},
        }

    beam = float(geo.get("beam_m"))
    draft = float(geo.get("draft_m"))
    cb = float(geo.get("cb"))
    res = hm.holtrop_resistance(
        length, beam, draft, cb, max(float(case.get("speed_ms") or 1.0), 0.5),
        rho=float(case.get("reference_density_kg_m3") or hm.RHO_SEAWATER),
        nu=float(case.get("reference_kinematic_viscosity_m2_s") or hm.NU_SEAWATER),
    )
    return {
        "s_m2": res.wetted_surface_m2,
        "basis": "holtrop_mennen_1982_regression",
        "derivation": (
            f"S=L(2T+B)√Cm·[0.453+0.4425Cb−0.2862Cm−0.003467B/T+0.3696Cwp]，"
            f"Cm={res.cm:.4f} Cwp={res.cwp:.4f}"
        ),
        "derived": dict(res.derived),
    }


# ---------------------------------------------------------------------------
# 单行判定
# ---------------------------------------------------------------------------

@dataclass
class RowVerdict:
    case_id: str
    ship_type: str
    canon_status: str
    filed_ct: Optional[float]
    filed_rt_n: Optional[float]
    speed_ms: Optional[float]
    speed_basis: str
    rho: float
    s_hull_m2: Optional[float]
    s_hull_basis: str
    s_band_m2: Optional[Tuple[float, float]]
    s_implied_m2: Optional[float]
    s_ratio: Optional[float]
    verdict: str
    rt_required_n: Optional[Tuple[float, float]] = None
    rt_factor_center: Optional[float] = None
    rt_factor_alt_speed: Optional[float] = None
    best_hypothesis: Optional[str] = None
    datum_request: Optional[Dict[str, Any]] = None
    speed_robust: Optional[bool] = None
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = dict(self.__dict__)
        if self.s_band_m2 is not None:
            out["s_band_m2"] = list(self.s_band_m2)
        if self.rt_required_n is not None:
            out["rt_required_n"] = list(self.rt_required_n)
        return out


def _filed(case: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], str, Optional[float]]:
    """取「归档当时的」Ct / Rt 与航速（Rt 优先取 legacy 归档值）。

    返回 ``(ct, rt_n, speed_basis, filed_speed_ms)``。
    """
    legacy = case.get("legacy_values") or {}
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")
    if rt is None:
        rt = legacy.get("filed_experimental_rt_n")
        if rt is None:
            rt = legacy.get("superseded_experimental_rt_n")
    speed = case.get("speed_ms")
    basis = "conditional_from_froude"
    filed_speed = legacy.get("filed_speed_ms")
    if filed_speed is None:
        basis = "filed_speed"
    return ct, rt, basis, filed_speed


def _documented_speeds(case: Dict[str, Any]) -> List[float]:
    """记录里出现过的航速（m/s）：条件值 + legacy 归档实测值。"""
    out: List[float] = []
    if case.get("speed_ms"):
        out.append(float(case["speed_ms"]))
    filed = (case.get("legacy_values") or {}).get("filed_speed_ms")
    if filed:
        out.append(float(filed))
    return sorted(set(out))


def _hypotheses(case: Dict[str, Any], ct: float, rt: float, speed: float,
                rho: float, s_hull: float) -> List[Dict[str, Any]]:
    """候选误用模式扫描。

    每个模式给出 (预测 Rt, 它隐含的航速)。判定三态：

    - ``ACCEPTED``  —— Rt 复现（|err| ≤ 15 %）**且**隐含航速落在记录自带的航速
      之一（±2 %），即无需引入记录外的第三个航速；
    - ``RT_ONLY``   —— Rt 复现但隐含航速不在记录内：可能是巧合，也可能是「航速列
      单位错」这类假设；是否系统性由逐船恒定因子检验（见 ``factor_consistency``）裁决；
    - ``REJECTED``  —— Rt 就不复现。
    """
    length = float(case["geometry"]["length_m"])
    nu = float(case.get("reference_kinematic_viscosity_m2_s") or hm.NU_SEAWATER)
    documented = _documented_speeds(case)

    modes: List[Tuple[str, float, float, str]] = []
    v_kn = speed / KNOT
    modes.append((
        "H1_speed_in_knots", v_kn,
        resistance_from_ct(ct, v_kn, s_hull, rho),
        "归档航速实为节（kn）而被当作 m/s：真实航速 = 归档 × 1.852",
    ))
    s_model = s_hull * (min(7.0, length) / length) ** 2
    modes.append((
        "H2_model_scale_surface", speed,
        resistance_from_ct(ct, speed, s_model, rho),
        f"湿面积按 L=7 m 模型尺度缩比 S={s_model:.4g} m²",
    ))
    rho_alt = hm.RHO_FRESHWATER if abs(rho - hm.RHO_SEAWATER) < 1e-9 else hm.RHO_SEAWATER
    modes.append((
        "H3_wrong_fluid_density", speed,
        resistance_from_ct(ct, speed, s_hull, rho_alt),
        f"介质密度取错 ρ={rho_alt:.1f} kg/m³",
    ))
    modes.append((
        "H4_half_wetted_surface", speed,
        resistance_from_ct(ct, speed, 0.5 * s_hull, rho),
        "湿面积折半（单面 vs 双面）",
    ))
    re = speed * length / nu
    cf = ittc57_cf(re)
    modes.append((
        "H5_friction_component_only", speed,
        cf * 0.5 * rho * speed ** 2 * s_hull,
        f"只记摩擦分量 R_f（ITTC-1957 Cf·½ρV²S，Re={re:.3g}）",
    ))
    modes.append((
        "H6_ct_is_residuary", speed,
        max(ct - cf, 0.0) * 0.5 * rho * speed ** 2 * s_hull,
        "Ct 实为剩余阻力系数 Cr → Rt=(Ct−Cf)·½ρV²S",
    ))

    out: List[Dict[str, Any]] = []
    for name, v_impl, pred, why in modes:
        if pred <= 0.0:
            err = None
        else:
            err = (pred - rt) / rt * 100.0
        rt_ok = err is not None and abs(err) <= ACCURACY_GATE_PCT
        speed_ok = any(abs(v_impl - v) / v <= 0.02 for v in documented) if documented else False
        if rt_ok and speed_ok:
            status = "ACCEPTED"
        elif rt_ok:
            status = "RT_ONLY"
        else:
            status = "REJECTED"
        out.append({
            "hypothesis": name,
            "reasoning": why,
            "implied_speed_ms": round(v_impl, 5),
            "implied_speed_documented": speed_ok,
            "predicted_rt_n": round(pred, 2),
            "rel_error_vs_filed_pct": None if err is None else round(err, 1),
            "status": status,
            "rejected": status != "ACCEPTED",
        })
    return out


def adjudicate_case(case: Dict[str, Any]) -> RowVerdict:
    ct, rt, speed_basis, filed_speed = _filed(case)
    rho = float(case.get("reference_density_kg_m3"))
    speed = case.get("speed_ms")
    s_est = hull_surface_estimate(case)
    s_hull = s_est["s_m2"]
    band = (s_hull * (1.0 - SURFACE_BAND), s_hull * (1.0 + SURFACE_BAND))

    notes: List[str] = []
    s_implied = None
    ratio = None
    verdict = NEEDS_DATUM
    request: Optional[Dict[str, Any]] = None
    robust: Optional[bool] = None
    hyps: List[Dict[str, Any]] = []

    if ct and rt and speed:
        s_implied = implied_surface(ct, speed, rt, rho)
        ratio = s_implied / s_hull
        inside = band[0] <= s_implied <= band[1]
        canon_ok = str(case.get("reference_status")) in ("REAL", canon.DERIVED)
        if inside and canon_ok:
            verdict = USABLE
        elif inside:
            verdict = RECOVERABLE_BY_SURFACE
        else:
            verdict = IRRECOVERABLE_FILED_RT
        hyps = _hypotheses(case, ct, rt, speed, rho, s_hull)

        if filed_speed:
            # 判定对「航速歧义」是否鲁棒：换用归档实测航速重算一次
            s_alt = implied_surface(ct, filed_speed, rt, rho)
            robust = (band[0] <= s_alt <= band[1]) == inside
            notes.append(
                f"航速歧义检验：按归档实测航速 {filed_speed:.4g} m/s 反解 "
                f"S_implied={s_alt:.4g} m²（比值 {s_alt / s_hull:.3g}），"
                f"结论{'不变' if robust else '翻转'}。"
            )

        rt_required = (resistance_from_ct(ct, speed, band[0], rho),
                       resistance_from_ct(ct, speed, band[1], rho))

        rt_center = resistance_from_ct(ct, speed, s_hull, rho)
        factor_center = rt / rt_center
        factor_alt = None
        if filed_speed:
            factor_alt = rt / resistance_from_ct(ct, filed_speed, s_hull, rho)
            notes.append(
                f"双读法恒定因子：条件航速读法下 filed/required="
                f"{factor_center:.4g}；归档实测航速读法下 ={factor_alt:.4g}"
                "（R1 要求 1.0）。"
            )

        scored = [h for h in hyps if h["rel_error_vs_filed_pct"] is not None]
        if scored:
            best = min(scored, key=lambda h: abs(h["rel_error_vs_filed_pct"]))
            notes.append(
                f"最接近归档值的候选模式：{best['hypothesis']}"
                f"（误差 {best['rel_error_vs_filed_pct']:+.1f}%，判定 {best['status']}，"
                f"隐含航速 {best['implied_speed_ms']} m/s "
                f"{'在' if best['implied_speed_documented'] else '不在'}记录航速内）。"
            )
            best_name = best["hypothesis"]
        else:
            best_name = None

        if inside and canon_ok:
            request = None
        elif inside:
            request = {
                "missing_datum": "wetted_surface_m2",
                "detail": "独立实测湿面积（船模静水湿表面，不含附件外扩）",
                "required_uncertainty_pct": TRUTH_UNCERTAINTY_PCT,
                "closes_row_because": (
                    f"S_implied={s_implied:.4g} m² 落在船型物理带 [{band[0]:.4g}, "
                    f"{band[1]:.4g}] m² 内 → 补 S 后 Rt 由 R1 唯一复算"
                ),
            }
        else:
            direction = "偏小" if ratio < 1.0 else "偏大"
            request = {
                "missing_datum": "experimental_rt_n",
                "detail": (
                    "原始拖曳总阻力的全尺度/模型尺度归属与航速配对"
                    "（Ct 与 Rt 不可同时成立，S 无法解释这一比值）"
                ),
                "required_uncertainty_pct": TRUTH_UNCERTAINTY_PCT,
                "closes_row_because": (
                    f"S_implied={s_implied:.4g} m² 比船型估计 {s_hull:.4g} m² {direction} "
                    f"{abs(ratio - 1.0) * 100:.0f}%，超出 ±{SURFACE_BAND * 100:.0f}% 带；"
                    "任何该船型可能取的 S 都补不平，必须重新供 Rt/Ct"
                ),
            }
        accepted = [h["hypothesis"] for h in hyps if h["status"] == "ACCEPTED"]
        rt_only = [h["hypothesis"] for h in hyps if h["status"] == "RT_ONLY"]
        if not accepted:
            notes.append(
                "六种候选误用模式无一 ACCEPTED（无模式同时复现 Rt 且隐含航速在记录内）"
                + (f"；RT_ONLY（Rt 复现但航速不在记录内，属巧合候选）：{', '.join(rt_only)}"
                   if rt_only else "")
                + "。是否系统性由 factor_consistency 裁决。"
            )
    else:
        missing = [k for k, v in (("experimental_ct", ct), ("rt", rt),
                                  ("speed_ms", speed)) if not v]
        request = {
            "missing_datum": ",".join(missing),
            "detail": "供齐 Ct / Rt / 航速后才可做 R1 反解",
            "required_uncertainty_pct": TRUTH_UNCERTAINTY_PCT,
            "closes_row_because": "R1 反解需要三个量同时在场",
        }

    return RowVerdict(
        case_id=case["case_id"],
        ship_type=case["ship_type"],
        canon_status=str(case.get("reference_status")),
        filed_ct=ct,
        filed_rt_n=rt,
        speed_ms=speed,
        speed_basis=speed_basis,
        rho=rho,
        s_hull_m2=round(s_hull, 4),
        s_hull_basis=s_est["basis"],
        s_band_m2=(round(band[0], 4), round(band[1], 4)),
        s_implied_m2=None if s_implied is None else round(s_implied, 4),
        s_ratio=None if ratio is None else round(ratio, 4),
        verdict=verdict,
        rt_required_n=(round(rt_required[0], 2), round(rt_required[1], 2))
        if ct and speed else None,
        rt_factor_center=round(factor_center, 4) if ct and speed else None,
        rt_factor_alt_speed=None if factor_alt is None else round(factor_alt, 4),
        best_hypothesis=best_name,
        datum_request=request,
        speed_robust=robust,
        hypotheses=hyps,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 数据集账本
# ---------------------------------------------------------------------------

def load_canon(path: Path = CANON_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def usable_rows(doc: Dict[str, Any]) -> List[str]:
    """正典判为可参与 15 % 判决（REAL/DERIVED）的算例 id。"""
    return [c["case_id"] for c in doc.get("cases", [])
            if str(c.get("reference_status")) in ("REAL", canon.DERIVED)]


def factor_consistency(rows: List[RowVerdict]) -> Dict[str, Dict[str, Any]]:
    """逐船型检验「恒定因子」假设：``filed_Rt / R1_required_Rt`` 是否逐行相同。

    R1 要求该因子恰为 1.0。逐行不同的因子说明归档的 Ct 与 Rt 来自不同来源
    （随机混档）；逐行相同而与 1 不同的因子说明是**系统性**标度/单位误用 ——
    两种情况需要的补救完全不同：前者必须重取原始表，后者只要认出标度约定。
    判据：``max/min ≤ 1 + 15 %`` 视为「单一恒定因子可解释」。
    """
    groups: Dict[str, List[float]] = {}
    for r in rows:
        if r.rt_factor_center is None:
            continue
        groups.setdefault(r.ship_type, []).append(r.rt_factor_center)
    out: Dict[str, Dict[str, Any]] = {}
    for ship, fs in groups.items():
        lo, hi = min(fs), max(fs)
        spread = hi / lo if lo > 0 else math.inf
        out[ship] = {
            "rows": len(fs),
            "factors": [round(f, 4) for f in fs],
            "min": round(lo, 4),
            "max": round(hi, 4),
            "spread_ratio": round(spread, 4),
            "single_factor_explains": spread <= 1.0 + ACCURACY_GATE_PCT / 100.0,
            "unit_factor": abs(lo - 1.0) <= ACCURACY_GATE_PCT / 100.0,
            "reading": (
                "R1 闭合（因子 ≈ 1）" if abs(lo - 1.0) <= 0.02 else
                ("单一恒定因子可解释全部行 → 系统性标度/单位误用"
                 if spread <= 1.0 + ACCURACY_GATE_PCT / 100.0
                 else "逐行因子不一致 → Ct 与 Rt 疑似混档，无恒定因子可解释")
            ),
        }
    return out


def ledger(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc if doc is not None else load_canon()
    rows = [adjudicate_case(c) for c in doc.get("cases", [])]
    usable = usable_rows(doc)
    by_verdict: Dict[str, int] = {}
    for r in rows:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
    recoverable = [r.case_id for r in rows if r.verdict == RECOVERABLE_BY_SURFACE]
    irrecoverable = [r.case_id for r in rows if r.verdict == IRRECOVERABLE_FILED_RT]
    return {
        "canon_version": doc.get("version"),
        "canon_path": str(CANON_PATH),
        "policy": (
            "仅正典 reference_status ∈ {REAL, DERIVED} 的行可参与 15 % 判决；"
            "本账本不改变正典判定，只回答 WITHHELD 行是否「补一个 S 就能救」"
        ),
        "truth_uncertainty_budget_pct": TRUTH_UNCERTAINTY_PCT,
        "surface_band_pct": SURFACE_BAND * 100.0,
        "total_rows": len(rows),
        "by_verdict": by_verdict,
        "usable_cases": usable,
        "usable_count": len(usable),
        "recoverable_by_surface": recoverable,
        "irrecoverable_filed_rt": irrecoverable,
        "kpi_target": BENCHMARK_KPI_TARGET,
        "kpi_gap": max(BENCHMARK_KPI_TARGET - len(usable), 0),
        "kpi_met": len(usable) >= BENCHMARK_KPI_TARGET,
        "factor_consistency": factor_consistency(rows),
        "rows": [r.as_dict() for r in rows],
    }


def to_json(ledger_doc: Dict[str, Any], indent: int = 2) -> str:
    return json.dumps(ledger_doc, ensure_ascii=False, indent=indent)