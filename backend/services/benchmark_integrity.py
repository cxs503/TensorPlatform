"""船舶基准参考数据的完整性闸门（物理闭合校验）。

背景
----
平台的 `calc.resistance` / `calc.stability` 能力以"与实验参考值的误差 <= 15%"作为
REAL 证据，但**参考值本身从未被校验过**。本模块把"几何必须先验证 Cb/L/B"这条
硬规则落成机器可判的闭合检查：任何一条参考数据行，若其 (L, B, T, Cb, Δ) 与
(V, Fr, S, Ct, Rt) 之间无法同时闭合，则判定参考行不可作为判据来源。

检查族（全部为纯解析恒等式，无经验拟合、无随机数）
--------------------------------------------------
C1 `froude_closure`      Fr == V / sqrt(g·L)                      [容差 2%]
C2 `displacement_closure` Cb·L·B·T·ρ == Δ（排水型）/ N.A.（回转体）
C3 `resistance_closure`  Rt == Ct·½ρV²S  ⇒  反解表观湿面积 S_imp，
                         要求 S_imp 落在该形体的无量纲合理带内
C4 `friction_floor`      Ct >= (1+k_min)·Cf_ITTC57(Re)  （总阻力不可能低于摩擦）
C5 `form_bands`          L/B、B/T、Cb、S/(L·B) 或 S/(πDL) 合理带

判级（severity）
----------------
PASS(0) < WITHHELD(1) < WARN(2) < FAIL(3)，行级 verdict 取最差项。
WITHHELD 表示"记录里缺少做该检查所需的量"，不得当成 PASS。

来源标注
--------
- ITTC-57 平板摩擦线：ITTC 1957 Recommended Procedure 7.5-02-01-01（0.075/(log10Re-2)²）
- 矩形浮箱静水力学：KB=T/2、BM=B²/(12T) 为矩形水线面的精确解（可手算复核）
- 合理性带取常规船型范围，只用于"扯淡值"拦截，不作为精度判据
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

G = 9.81
RHO_SEA = 1025.0            # kg/m³，海水 15 °C
RHO_FRESH_20C = 998.2       # kg/m³，淡水 20 °C（m1561：参考行自带 reference_density_kg_m3 时以其为准）
RHO_FRESH = 998.0           # kg/m³，淡水 20 °C（船模水池常规）
NU_SEA_15C = 1.1883e-6      # m²/s，海水 15 °C
NU_FRESH_20C = 1.004e-6     # m²/s，淡水 20 °C

# 判级
PASS, WITHHELD, WARN, FAIL = "PASS", "WITHHELD", "WARN", "FAIL"
_RANK = {PASS: 0, WITHHELD: 1, WARN: 2, FAIL: 3}

# 合理性带（只拦截物理不可能/明显扯淡，不作为精度判据）
BAND_LB = (3.5, 12.0)          # 排水型 L/B
BAND_BT = (2.0, 6.5)           # 排水型 B/T
BAND_CB = (0.40, 0.90)
BAND_S_LB = (1.00, 3.20)       # 湿面积 / (L·B)
BAND_S_PIDL = (0.50, 1.00)     # 回转体：湿面积 / (πDL)
BAND_LD = (5.0, 15.0)          # 回转体 L/D

TOL_FROUDE = 0.02
TOL_DISPLACEMENT = 0.02


# ---------------------------------------------------------------------------
# 解析锚（可手算复核）
# ---------------------------------------------------------------------------

def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 平板摩擦阻力系数 Cf = 0.075 / (log10(Re) - 2)²。

    锚点：Re=1e6 → 0.075/16 = 4.6875e-3（ITTC 表值 0.00469）。
    """
    if reynolds <= 1.0e4:
        raise ValueError(f"Reynolds 太小，ITTC-57 不适用: {reynolds}")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def pontoon_hydrostatics(length: float, beam: float, draft: float) -> Dict[str, float]:
    """矩形浮箱（box barge）静水力学精确解：KB=T/2，BM=B²/(12T)，KM=KB+BM。

    length 仅用于接口对称（矩形水线面惯性矩与 L 的一次方相关，但 B²/12T 与之无关）。
    """
    kb = draft / 2.0
    bm = beam ** 2 / (12.0 * draft)
    return {"kb": kb, "bm": bm, "km": kb + bm}


def implied_wetted_surface(ct: float, speed: float, rt_n: float, rho: float = RHO_SEA) -> Optional[float]:
    """由 Rt = Ct·½ρV²S 反解表观湿面积 S_imp（m²）。"""
    if ct <= 0 or speed <= 0 or rt_n <= 0:
        return None
    return rt_n / (ct * 0.5 * rho * speed ** 2)


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------

def _check(name: str, status: str, detail: str, **data: Any) -> Dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, **data}


def check_froude_closure(speed: float, length: float, froude: Optional[float],
                         tol: float = TOL_FROUDE) -> Dict[str, Any]:
    if not froude or speed <= 0 or length <= 0:
        return _check("froude_closure", WITHHELD, "缺少 Fr 或 V/L，无法闭合")
    computed = speed / math.sqrt(G * length)
    implied_len = (speed / froude) ** 2 / G
    err = abs(computed - froude) / froude
    status = PASS if err <= tol else FAIL
    detail = (f"Fr_computed={computed:.4f} vs Fr_stated={froude:.4f} (误差 {err*100:.1f}%)"
              if status is PASS else
              f"Fr 不闭合：V={speed:g} m/s 与 L={length:g} m 给出 Fr={computed:.4f}，"
              f"但记录写 {froude:.4f}；反解 (V/Fr)²/g 得隐含船长 {implied_len:.1f} m，与 L 不符")
    return _check("froude_closure", status, detail,
                  computed=round(computed, 4), stated=froude, error_pct=round(err * 100, 2),
                  implied_length_m=round(implied_len, 1))


def check_displacement_closure(length: float, beam: float, draft: float, cb: Optional[float],
                               displacement_t: Optional[float], rho: float = RHO_SEA,
                               tol: float = TOL_DISPLACEMENT,
                               form_type: str = "displacement_hull") -> Dict[str, Any]:
    if form_type == "body_of_revolution":
        return _check("displacement_closure", WITHHELD,
                      "回转体（潜艇）不适用排水型 Cb·L·B·T 闭式，需另行给出排水体积")
    if not cb or not displacement_t or min(length, beam, draft) <= 0:
        return _check("displacement_closure", WITHHELD, "缺少 Cb 或 Δ，无法闭合")
    derived = cb * length * beam * draft * rho / 1000.0  # t
    err = abs(derived - displacement_t) / displacement_t
    status = PASS if err <= tol else FAIL
    detail = (f"Cb·L·B·T·ρ = {derived:.1f} t vs Δ = {displacement_t:g} t (误差 {err*100:.1f}%)"
              if status is PASS else
              f"排水量不闭合：Cb·L·B·T·ρ = {derived:.1f} t，记录 Δ = {displacement_t:g} t，"
              f"差 {err*100:.1f}%（> {tol*100:.0f}%）；两者必有一个是错尺度/错单位")
    return _check("displacement_closure", status, detail,
                  derived_t=round(derived, 1), stated_t=displacement_t, error_pct=round(err * 100, 2))


def check_resistance_closure(ct: Optional[float], speed: float, rt_n: Optional[float],
                             length: Optional[float], beam: Optional[float],
                             wetted_surface_m2: Optional[float] = None,
                             rho: float = RHO_SEA,
                             form_type: str = "displacement_hull",
                             diameter: Optional[float] = None,
                             tol: float = 0.15) -> Dict[str, Any]:
    """Rt 与 Ct 的一致性：有实测 S 则直接比；没有则用反解 S_imp 的无量纲带判定。

    ``tol`` 是"记录值自洽"的容差：诊断用途 15%，**证据闸门用途 1%**——一条要被
    当成判据（并支撑 15% 精度声明）的参考行，其自身恒等式必须远严于判决阈值，
    否则"用 15% 容差验证 15% 误差"是循环论证。
    """
    if not ct or not rt_n or speed <= 0:
        return _check("resistance_closure", WITHHELD, "缺少 Ct 或 Rt 或 V，无法闭合")

    if wetted_surface_m2:
        derived = ct * 0.5 * rho * speed ** 2 * wetted_surface_m2
        err = abs(derived - rt_n) / rt_n
        status = PASS if err <= tol else FAIL
        return _check("resistance_closure", status,
                      (f"Ct·½ρV²S = {derived:.2f} N vs Rt = {rt_n:.2f} N (误差 {err*100:.1f}%)"
                       if status is PASS else
                       f"Rt 与 Ct 不自洽：按 Ct={ct:g}, V={speed:g} m/s, S={wetted_surface_m2:g} m² 应为 "
                       f"{derived:.2f} N，记录 {rt_n:.2f} N（相差 {max(derived, rt_n)/min(derived, rt_n):.1f} 倍）"),
                      derived_rt_n=round(derived, 2), stated_rt_n=rt_n, error_pct=round(err * 100, 2))

    s_imp = implied_wetted_surface(ct, speed, rt_n, rho=rho)
    if s_imp is None:
        return _check("resistance_closure", WITHHELD, "反解湿面积失败")
    if form_type == "body_of_revolution" and diameter:
        ratio = s_imp / (math.pi * diameter * length)
        lo, hi = BAND_S_PIDL
        ref = f"S_imp/(πDL)"
    elif length and beam:
        ratio = s_imp / (length * beam)
        lo, hi = BAND_S_LB
        ref = f"S_imp/(L·B)"
    else:
        return _check("resistance_closure", WITHHELD, "缺少 L/B/D，无法做无量纲带判定")

    inside = lo <= ratio <= hi
    status = PASS if inside else FAIL
    detail = (f"表观湿面积 S_imp = {s_imp:.2f} m²，{ref} = {ratio:.3f} 落在带内 [{lo}, {hi}]"
              if inside else
              f"Rt 与 Ct 无法同时成立：反解表观湿面积 S_imp = {s_imp:.2f} m²，"
              f"{ref} = {ratio:.3f} 远低于物理下限 {lo}（合理带 [{lo}, {hi}]）")
    return _check("resistance_closure", status, detail,
                  implied_wetted_surface_m2=round(s_imp, 3), dimensionless_ratio=round(ratio, 3),
                  band=[lo, hi])


def check_friction_floor(ct: Optional[float], speed: float, length: float,
                         nu: float = NU_SEA_15C, k_min: float = 0.0) -> Dict[str, Any]:
    if not ct or speed <= 0 or length <= 0:
        return _check("friction_floor", WITHHELD, "缺少 Ct 或 V/L")
    re = speed * length / nu
    cf = ittc57_cf(re)
    floor = (1.0 + k_min) * cf
    status = PASS if ct >= floor else FAIL
    return _check("friction_floor", status,
                  (f"Re={re:.3e}, Cf_ITTC57={cf:.6f}, Ct/Cf={ct/cf:.2f} ≥ 1"
                   if status is PASS else
                   f"总阻力系数 Ct={ct:g} 低于平板摩擦 Cf={cf:.6f}（Re={re:.3e}），物理不可能"),
                  reynolds=round(re, 1), cf=round(cf, 6), ct_over_cf=round(ct / cf, 3))


def check_form_bands(form_type: str, length: float, beam: float, draft: float,
                     cb: Optional[float], wetted_surface_m2: Optional[float] = None,
                     diameter: Optional[float] = None) -> Dict[str, Any]:
    out: List[str] = []
    bad: List[str] = []
    if form_type == "body_of_revolution" and diameter:
        ld = length / diameter
        if not (BAND_LD[0] <= ld <= BAND_LD[1]):
            bad.append(f"L/D={ld:.2f} ∉ [{BAND_LD[0]}, {BAND_LD[1]}]")
        out.append(f"L/D={ld:.2f}")
        if wetted_surface_m2:
            r = wetted_surface_m2 / (math.pi * diameter * length)
            if not (BAND_S_PIDL[0] <= r <= BAND_S_PIDL[1]):
                bad.append(f"S/(πDL)={r:.3f} ∉ [{BAND_S_PIDL[0]}, {BAND_S_PIDL[1]}]")
            out.append(f"S/(πDL)={r:.3f}")
    else:
        lb, bt = length / beam, beam / draft
        if not (BAND_LB[0] <= lb <= BAND_LB[1]):
            bad.append(f"L/B={lb:.2f} ∉ [{BAND_LB[0]}, {BAND_LB[1]}]")
        if not (BAND_BT[0] <= bt <= BAND_BT[1]):
            bad.append(f"B/T={bt:.2f} ∉ [{BAND_BT[0]}, {BAND_BT[1]}]")
        if cb is not None and not (BAND_CB[0] <= cb <= BAND_CB[1]):
            bad.append(f"Cb={cb:.3f} ∉ [{BAND_CB[0]}, {BAND_CB[1]}]")
        if wetted_surface_m2:
            r = wetted_surface_m2 / (length * beam)
            if not (BAND_S_LB[0] <= r <= BAND_S_LB[1]):
                bad.append(f"S/(L·B)={r:.3f} ∉ [{BAND_S_LB[0]}, {BAND_S_LB[1]}]")
            out.append(f"S/(L·B)={r:.3f}")
        out.append(f"L/B={lb:.2f}, B/T={bt:.2f}" + (f", Cb={cb:.3f}" if cb is not None else ""))
    status = FAIL if bad else PASS
    return _check("form_bands", status, "; ".join(bad) if bad else "; ".join(out),
                  values=out, violations=bad)


# ---------------------------------------------------------------------------
# 数据集审计
# ---------------------------------------------------------------------------

def _worst(statuses: Iterable[str]) -> str:
    worst = PASS
    for s in statuses:
        if _RANK[s] > _RANK[worst]:
            worst = s
    return worst


def fluid_of(case: Dict[str, Any], rho: Optional[float] = None,
             nu: Optional[float] = None) -> Tuple[float, float]:
    """m1561：流体物性溯源。

    参考行若自带 ``reference_density_kg_m3`` / ``reference_kinematic_viscosity_m2_s``
    （v3.0 正典的数据集每条都带），闸门必须用行内值；否则同一份数据会被闸门用海水
    密度、被物理核用淡水密度各判一遍（契约漂移）。无行内值时才回落到模块默认。
    """
    rho_out = rho if rho is not None else case.get("reference_density_kg_m3", RHO_SEA)
    nu_out = nu if nu is not None else case.get("reference_kinematic_viscosity_m2_s", NU_SEA_15C)
    return float(rho_out), float(nu_out)


def audit_case(case: Dict[str, Any], rho: Optional[float] = None,
               nu: Optional[float] = None, identity_tol: float = 0.15) -> Dict[str, Any]:
    """审计单条参考用例。case["geometry"] 为长度量纲字典（m / kg / t）。

    rho/nu 为 None 时优先取行内 ``reference_density_kg_m3``（v3.0 正典），
    再回落到海水 15 °C 默认值。
    """
    rho, nu = fluid_of(case, rho, nu)
    geo = case.get("geometry", {}) or {}
    form = case.get("form_type") or ("body_of_revolution" if case.get("ship_type") == "SUBOFF"
                                     else "displacement_hull")
    length = geo.get("length_m")
    beam = geo.get("beam_m") or geo.get("diameter_m")
    draft = geo.get("draft_m") or geo.get("diameter_m")
    diameter = geo.get("diameter_m")
    cb = geo.get("cb")
    disp_t = geo.get("displacement_t")
    if disp_t is None and geo.get("displacement_kg") is not None:
        disp_t = geo["displacement_kg"] / 1000.0
    s = geo.get("wetted_surface_m2")

    checks = []
    if length and case.get("speed_ms"):
        checks.append(check_froude_closure(case["speed_ms"], length, case.get("froude_number")))
    if length and beam and draft:
        if form != "body_of_revolution":
            # 不适用（回转体没有 Cb·L·B·T 闭式）的检查项直接不参与裁决：
            # 把 N.A. 记成 WITHHELD 会让每一艘潜艇行永久不可验证，那是误判而非保守。
            checks.append(check_displacement_closure(length, beam, draft, cb, disp_t, rho=rho,
                                                     form_type=form))
        checks.append(check_form_bands(form, length, beam, draft, cb, s, diameter))
    checks.append(check_resistance_closure(case.get("experimental_ct"), case.get("speed_ms", 0.0),
                                           case.get("experimental_rt_n"), length, beam,
                                           wetted_surface_m2=s, rho=rho, form_type=form,
                                           diameter=diameter, tol=identity_tol))
    checks.append(check_friction_floor(case.get("experimental_ct"), case.get("speed_ms", 0.0),
                                       length or 0.0, nu=nu))

    verdict = _worst(c["status"] for c in checks)
    return {"case_id": case.get("case_id"), "ship_type": case.get("ship_type"),
            "form_type": form, "verdict": verdict, "checks": checks}


def audit_dataset(path: Path, rho: Optional[float] = None,
                  nu: Optional[float] = None, identity_tol: float = 0.01) -> Dict[str, Any]:
    """数据集级闸门。identity_tol 默认 1%：被当成判据的参考行，其自身恒等式
    必须远严于它所支撑的 15% 精度判决。"""
    path = Path(path)
    data = json.loads(path.read_text())
    rows = [audit_case(c, rho=rho, nu=nu, identity_tol=identity_tol)
            for c in data.get("cases", [])]
    return _summarize(rows, source=str(path), dataset_version=data.get("version"))


def audit_proxy_table(table: Dict[str, Any]) -> Dict[str, Any]:
    """审计 ship_proxy.BENCHMARK_REFERENCE（参数式：params + reference_rt/gm）。"""
    rows = []
    for calc_type, cases in table.items():
        for name, spec in cases.items():
            p = spec.get("params", {})
            row = {
                "case_id": f"{calc_type}/{name}",
                "calc_type": calc_type,
                "checks": [],
            }
            L = p.get("length")
            B = p.get("beam")
            T = p.get("draft")
            cb = p.get("cb")
            disp = p.get("displacement")
            # 只有排水型（船体）参数参与 C2/C5；潜艇用回转体带
            form = "body_of_revolution" if "suboff" in name else "displacement_hull"
            if calc_type == "resistance":
                if L and B and T:
                    row["checks"].append(check_displacement_closure(
                        L, B, T, cb, disp, rho=RHO_SEA, form_type=form))
                    row["checks"].append(check_form_bands(form, L, B, T, cb, None, B))
                if L:
                    row["checks"].append(check_friction_floor(None, 0.0, L))  # 参数式无 Ct → WITHHELD
            else:  # stability：只做静水力学闭合（KM 上界）
                if L and B and T and disp:
                    row["checks"].append(check_displacement_closure(L, B, T, cb, disp, rho=RHO_SEA))
                    km = pontoon_hydrostatics(L, B, T)["km"]
                    gm = spec.get("reference_gm")
                    if gm is not None:
                        ok = 0.0 < gm <= km
                        row["checks"].append(_check(
                            "gm_cap", PASS if ok else FAIL,
                            f"参考 GM={gm:g} m 必须满足 0 < GM < KM_pontoon={km:.2f} m"
                            + ("" if ok else "：超出矩形浮箱 KM 上界，物理不可能"),
                            gm=gm, km_pontoon=round(km, 3)))
                        # 矩形等价体 GM = KM - KG；成形船体 KM 更低，故仅作 WARN 提示
                        kg = p.get("kg")
                        if kg is not None:
                            gm_box = km - kg
                            rel = abs(gm - gm_box) / gm_box
                            row["checks"].append(_check(
                                "gm_consistency", PASS if rel <= 0.15 else WARN,
                                f"矩形等价体 GM={gm_box:.2f} m（KM_pontoon - KG={kg:g}），"
                                f"参考 GM={gm:g} m 偏离 {rel*100:.0f}%"
                                + ("" if rel <= 0.15 else "；成形船体 Cwp<1 会使 KM 下降，故记为 WARN 而非 FAIL"),
                                gm_box=round(gm_box, 3), deviation_pct=round(rel * 100, 1)))
            row["verdict"] = _worst(c["status"] for c in row["checks"])
            rows.append(row)
    return _summarize(rows, source="services.ship_proxy.BENCHMARK_REFERENCE")


def _summarize(rows: List[Dict[str, Any]], source: str,
               dataset_version: Optional[str] = None) -> Dict[str, Any]:
    counts = {k: 0 for k in (PASS, WITHHELD, WARN, FAIL)}
    for r in rows:
        counts[r["verdict"]] += 1
    failing = [r["case_id"] for r in rows if r["verdict"] == FAIL]
    return {
        "source": source,
        "dataset_version": dataset_version,
        "total": len(rows),
        "verdict_counts": counts,
        "failing_cases": failing,
        "binding": len(failing) == 0,
        "rows": rows,
    }


def dataset_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json"