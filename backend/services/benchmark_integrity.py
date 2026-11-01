"""Benchmark dataset integrity audit (基准数据集物理自洽性审计).

背景（验证欠账）：``backend/data/benchmark_reference.json`` 的 12 条记录被
``/calculations/benchmark_validate`` 直接当作"实验参考值"用来判 PASS/FAIL，但数据集自身
从未被机检过。本模块只做一件事：用可复算的物理关系去交叉验证数据集里的每一个数字，
把"参考值可信 / 不可信 / 缺信息"变成机器可裁的结论。

五条判据（全部只依赖数据集内的字段与代数关系，不引入外部"文献值"）：

    C1 froude_consistency  : 声明的 Fn 是否等于 V/√(g·L)；不等时反解 implied L，
                             用于定位"模型尺度几何 + 实船尺度航速"这类尺度混用。
    C2 cb_closure          : 声明的 Cb 是否等于 ∇/(L·B·T)（几何发生闭合）。
    C3 surface_closure     : 声明湿表面积 vs 平台闭式 π·B·L·(0.5+0.25·Cb) 的偏差
                             —— 直接量化阻力端点里 S 的系统偏差。
    C4 ct_rt_identity      : Ct 与 Rt 是否满足 Ct = Rt/(0.5·ρ·S·V²)。
    C5 ct_monotonicity     : 同船型下 Ct 随 Fn 单调不降（给定几何，Ct 随航速上升是
                             物理必然；出现显著下降说明两列数字不同源）。

证据分级（映射平台口径）：
    REAL      —— 五条判据全部 PASS；
    WITHHELD  —— 有判据因缺字段不可算，且无判据 FAIL（信息不足，非矛盾）；
    MOCK      —— 至少一条判据 FAIL：数据集里的数字无法由任何物理关系重现，不能作为
                 精度判定基准（此时端点的 PASS/FAIL 判决不可信）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from services.hull_geometry import (
    CB_BAND_HI,
    CB_BAND_LO,
    CB_REL_TOL,
    FAIL,
    FN_REL_TOL,
    PASS,
    RHO_MODEL_FRESH,
    RHO_SEAWATER,
    SURFACE_REL_TOL,
    UNAVAILABLE,
    axisymmetric_wetted_surface,
    cb_from_volume,
    froude_number,
    implied_length_from_froude,
    parse_case_geometry,
    relative_difference,
)

CT_RT_REL_TOL = 2.0e-2     # Ct↔Rt 恒等式的相对容差
MONOTONIC_DROP_TOL = 0.10  # 允许的 Ct 回落幅度（大于此值判非单调）



REAL = "REAL"
WITHHELD = "WITHHELD"
MOCK = "MOCK"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_froude(case: Dict[str, Any]) -> Dict[str, Any]:
    """C1: 声明 Fn 与 V/√(gL) 的一致性。"""
    geo = parse_case_geometry(case)
    speed = case.get("speed_ms")
    fn_declared = case.get("froude_number")
    if speed is None or fn_declared is None or geo["length_m"] is None:
        return {"id": "C1_froude_consistency", "status": UNAVAILABLE,
                "reason": "speed_ms / froude_number / length_m missing"}

    fn_derived = froude_number(speed, geo["length_m"])
    rel = relative_difference(fn_declared, fn_derived)
    implied_l = implied_length_from_froude(speed, fn_declared)
    return {
        "id": "C1_froude_consistency",
        "status": PASS if rel <= FN_REL_TOL else FAIL,
        "fn_declared": float(fn_declared),
        "fn_derived": round(fn_derived, 6),
        "rel_err": float(f"{rel:.6e}"),
        "implied_length_m": round(implied_l, 3),
        "geometry_length_m": geo["length_m"],
        "length_ratio": round(implied_l / geo["length_m"], 3),
        "tolerance": FN_REL_TOL,
    }


def check_cb_closure(case: Dict[str, Any]) -> Dict[str, Any]:
    """C2: 声明 Cb 与 ∇/(L·B·T) 的闭合。"""
    geo = parse_case_geometry(case)
    if geo["cb"] is None or geo.get("cb_from_volume") is None:
        return {"id": "C2_cb_closure", "status": UNAVAILABLE,
                "reason": "Cb or displacement volume missing"}
    rel = relative_difference(geo["cb"], geo["cb_from_volume"])
    volume_from_cb = geo["cb"] * geo["length_m"] * geo["beam_m"] * geo["draft_m"]
    # ρ 敏感性：模型尺度用淡水 998、实船尺度用海水 1025；两种假设下结论是否翻转
    rel_seawater = None
    if geo["volume_m3"] is not None:
        cb_seawater = cb_from_volume(
            geo["volume_m3"] * RHO_MODEL_FRESH / RHO_SEAWATER,
            geo["length_m"], geo["beam_m"], geo["draft_m"],
        )
        rel_seawater = float(f"{relative_difference(geo['cb'], cb_seawater):.6e}")
    return {
        "id": "C2_cb_closure",
        "status": PASS if rel <= CB_REL_TOL else FAIL,
        "cb_declared": geo["cb"],
        "cb_implied": round(geo["cb_from_volume"], 6),
        "rel_err": float(f"{rel:.6e}"),
        "rel_err_if_seawater": rel_seawater,
        # 反解：若以声明 Cb 为准，排水体积应为多少（定位到底哪个字段错）
        "volume_declared_m3": round(geo["volume_m3"], 3),
        "volume_implied_by_cb_m3": round(volume_from_cb, 3),
        "tolerance": CB_REL_TOL,
    }


def check_surface_closure(case: Dict[str, Any]) -> Dict[str, Any]:
    """C3: 声明湿表面积 vs 平台闭式 π·B·L·(0.5+0.25·Cb)。

    Cb 缺失时不做点估计，而在几何允许区间 Cb ∈ (0, π/4] 上取**对闭式最有利**的
    Cb（即偏差绝对值最小者），据此判定闭式能否重现声明值：若最有利情形仍超差，
    则闭式对这型船存在结构性的系统偏差，与 Cb 取值无关。

    注意（物理）：Ct = Rt/(½ρSV²) 而 Rt = ½ρSV²(Cf+Cr)，故 Ct 与 S 无关；
    S 的偏差只作用于**力**（Rt），不作用于 Ct。因此 C3 的后果必须通过 Rt 口径体现。
    """
    geo = parse_case_geometry(case)
    s_declared = geo["wetted_surface_m2"]
    if s_declared is None:
        return {"id": "C3_surface_closure", "status": UNAVAILABLE,
                "reason": "no declared wetted area to cross-check against"}
    if geo["length_m"] is None or geo["beam_m"] is None:
        return {"id": "C3_surface_closure", "status": UNAVAILABLE,
                "reason": "length / beam missing"}

    def bias_at(cb: float) -> float:
        return (axisymmetric_wetted_surface(cb, geo["length_m"], geo["beam_m"]) - s_declared) / s_declared

    if geo["cb"] is not None:
        cb_used = geo["cb"]
        basis = "declared_cb"
        s_closure = axisymmetric_wetted_surface(cb_used, geo["length_m"], geo["beam_m"])
        bias = (s_closure - s_declared) / s_declared
    else:
        # 闭式对 Cb 单调递增 → 最有利 Cb 即区间内使 |bias| 最小者
        candidates = [CB_BAND_LO, (CB_BAND_LO + CB_BAND_HI) / 2.0, CB_BAND_HI]
        cb_used = min(candidates, key=lambda c: abs(bias_at(c)))
        basis = "best_case_over_cb_band"
        s_closure = axisymmetric_wetted_surface(cb_used, geo["length_m"], geo["beam_m"])
        bias = (s_closure - s_declared) / s_declared

    return {
        "id": "C3_surface_closure",
        "status": PASS if abs(bias) <= SURFACE_REL_TOL else FAIL,
        "basis": basis,
        "cb_used": round(cb_used, 6),
        "declared_m2": round(s_declared, 4),
        "closure_m2": round(s_closure, 4),
        "closure_bias": float(f"{bias:.6e}"),
        "bias_at_cb_band_hi": float(f"{bias_at(CB_BAND_HI):.6e}"),
        "bias_at_cb_band_lo": float(f"{bias_at(CB_BAND_LO):.6e}"),
        "consequence": "Rt 偏差 = S 偏差（Cf+Cr 定值下 R ∝ S）；Ct 不受影响",
        "tolerance": SURFACE_REL_TOL,
    }


def check_ct_rt_identity(case: Dict[str, Any]) -> Dict[str, Any]:
    """C4: Ct ≡ Rt/(0.5·ρ·S·V²)。"""
    geo = parse_case_geometry(case)
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")
    speed = case.get("speed_ms")
    surface = geo["wetted_surface_m2"]
    if None in (ct, rt, speed, surface):
        return {"id": "C4_ct_rt_identity", "status": UNAVAILABLE,
                "reason": "Ct / Rt / speed / wetted area missing"}
    q = 0.5 * RHO_MODEL_FRESH * surface * speed ** 2
    rt_implied = ct * q
    rt_derived_from_ct = rt_implied
    rel = relative_difference(rt_derived_from_ct, rt)
    return {
        "id": "C4_ct_rt_identity",
        "status": PASS if rel <= CT_RT_REL_TOL else FAIL,
        "ct": float(ct),
        "rt_declared_n": float(rt),
        "rt_implied_by_ct_n": round(rt_implied, 4),
        "dynamic_pressure_area_n": round(q, 4),
        "rel_err": float(f"{rel:.6e}"),
        "rt_declared_over_implied": round(rt / rt_implied, 6) if rt_implied else None,
        "tolerance": CT_RT_REL_TOL,
    }


def check_ct_monotonicity(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """C5: 同船型 Ct 随 Fn 单调不降（回落 >10% 判 FAIL）。"""
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        if case.get("froude_number") is None or case.get("experimental_ct") is None:
            continue
        by_type.setdefault(case["ship_type"], []).append(case)

    violations: List[Dict[str, Any]] = []
    evaluated: List[str] = []
    for ship_type, group in by_type.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda c: c["froude_number"])
        evaluated.append(ship_type)
        for lo, hi in zip(group, group[1:]):
            if hi["froude_number"] <= lo["froude_number"]:
                continue
            drop = (lo["experimental_ct"] - hi["experimental_ct"]) / lo["experimental_ct"]
            if drop > MONOTONIC_DROP_TOL:
                violations.append({
                    "ship_type": ship_type,
                    "from_case": lo["case_id"],
                    "to_case": hi["case_id"],
                    "fn_from": round(lo["froude_number"], 4),
                    "fn_to": round(hi["froude_number"], 4),
                    "ct_from": lo["experimental_ct"],
                    "ct_to": hi["experimental_ct"],
                    "drop": round(drop, 6),
                })
    if not evaluated:
        return {"id": "C5_ct_monotonicity", "status": UNAVAILABLE, "reason": "no group with >=2 Fn points"}
    return {
        "id": "C5_ct_monotonicity",
        "status": PASS if not violations else FAIL,
        "groups_evaluated": evaluated,
        "violations": violations,
        "tolerance": MONOTONIC_DROP_TOL,
    }


# ---------------------------------------------------------------------------
# Case / dataset audit
# ---------------------------------------------------------------------------

def grade_case(case_checks: Dict[str, Dict[str, Any]]) -> str:
    """由判据结果定证据等级（REAL / WITHHELD / MOCK）。"""
    statuses = [c.get("status") for c in case_checks.values()]
    if FAIL in statuses:
        return MOCK
    if UNAVAILABLE in statuses:
        return WITHHELD
    return REAL


def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """单条基准记录的四项判据（C5 是船型级，见 audit_dataset）。"""
    checks = {
        c["id"]: c
        for c in (
            check_froude(case),
            check_cb_closure(case),
            check_surface_closure(case),
            check_ct_rt_identity(case),
        )
    }
    failed = [cid for cid, c in checks.items() if c["status"] == FAIL]
    unavailable = [cid for cid, c in checks.items() if c["status"] == UNAVAILABLE]
    return {
        "case_id": case.get("case_id"),
        "ship_type": case.get("ship_type"),
        "evidence": grade_case(checks),
        "failed_checks": failed,
        "unavailable_checks": unavailable,
        "checks": checks,
    }


def audit_dataset(data: Dict[str, Any]) -> Dict[str, Any]:
    """整份基准数据集的审计：逐条判据 + 证据分级统计 + C5 跨案例判据。"""
    cases = list(data.get("cases") or [])
    case_audits = [audit_case(c) for c in cases]
    monotonic = check_ct_monotonicity(cases)

    grades = {REAL: 0, WITHHELD: 0, MOCK: 0}
    for entry in case_audits:
        grades[entry["evidence"]] = grades.get(entry["evidence"], 0) + 1

    # 判据级统计
    check_stats: Dict[str, Dict[str, int]] = {}
    for entry in case_audits:
        for cid, check in entry["checks"].items():
            bucket = check_stats.setdefault(cid, {PASS: 0, FAIL: 0, UNAVAILABLE: 0})
            bucket[check["status"]] += 1
    check_stats[monotonic["id"]] = {PASS: 1 if monotonic["status"] == PASS else 0,
                                    FAIL: 1 if monotonic["status"] == FAIL else 0,
                                    UNAVAILABLE: 1 if monotonic["status"] == UNAVAILABLE else 0}

    total = len(cases)
    if total == 0:
        dataset_grade = WITHHELD
    elif grades[MOCK] == 0 and grades[WITHHELD] == 0:
        dataset_grade = REAL
    elif grades[MOCK] > total / 2:
        dataset_grade = MOCK
    else:
        dataset_grade = WITHHELD

    return {
        "n_cases": total,
        "dataset_evidence": dataset_grade,
        "grade_counts": grades,
        "check_stats": check_stats,
        "monotonicity": monotonic,
        "cases": case_audits,
        "verdict": _verdict(dataset_grade, case_audits, monotonic),
    }


def _verdict(dataset_grade: str, case_audits: List[Dict[str, Any]], monotonic: Dict[str, Any]) -> str:
    if dataset_grade == REAL:
        return "数据集物理自洽，可作为精度判定基准。"
    failed = sorted({cid for entry in case_audits for cid in entry["failed_checks"]})
    parts = [
        f"数据集证据等级 {dataset_grade}：{len(case_audits)} 条记录中 "
        f"{sum(1 for e in case_audits if e['evidence'] == MOCK)} 条至少一条物理判据不闭合。"
    ]
    if failed:
        parts.append("失败判据：" + "、".join(failed) + "。")
    if monotonic["status"] == FAIL:
        parts.append(
            "船型内 Ct 随 Fn 非单调（" +
            "、".join(sorted({v['ship_type'] for v in monotonic['violations']})) +
            "），说明两列数字不同源。"
        )
    parts.append("在此条件下 benchmark_validate 的 PASS/FAIL 判决不可作为精度证据，须先修数据。")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Platform accuracy evaluation (same code path as the endpoint)
# ---------------------------------------------------------------------------

def platform_vs_reference(data: Dict[str, Any]) -> Dict[str, Any]:
    """用端点同一份物理核（services.resistance_core）复算每条基准工况。

    给出两条互相独立的口径：
        ct : 无量纲总阻力系数（与湿表面积无关，Ct = Cf + Cr）——与 experimental_ct 比；
        rt : 总阻力（力，∝ S）——与 experimental_rt_n 比，并给出 closure / declared
             两种湿表面积下的差异，用于量化 S 闭式偏差的实际后果。

    Cb 缺失时（数据集未给排水体积）不做点估计：Ct 在几何允许区间 Cb ∈ (0, π/4]
    内给误差带（Cr 弱依赖 Cb），避免用编造的 Cb 换出"精确"数字。
    """
    from services.resistance_core import estimate_resistance

    rows: List[Dict[str, Any]] = []
    for case in data.get("cases") or []:
        geo = parse_case_geometry(case)
        speed = case.get("speed_ms")
        if speed is None or geo["length_m"] is None or geo["beam_m"] is None or geo["draft_m"] is None:
            rows.append({"case_id": case.get("case_id"), "status": UNAVAILABLE,
                         "reason": "incomplete geometry/speed"})
            continue

        cb = geo["cb"]
        cb_values = [cb] if cb is not None else [CB_BAND_LO, CB_BAND_HI]
        base = dict(length=geo["length_m"], beam=geo["beam_m"], draft=geo["draft_m"], speed_ms=speed)
        row: Dict[str, Any] = {
            "case_id": case.get("case_id"),
            "ship_type": case.get("ship_type"),
            "speed_ms": speed,
            "ref_ct": case.get("experimental_ct"),
            "ref_rt_n": case.get("experimental_rt_n"),
            "cb": cb,
            "cb_source": "declared" if cb is not None else "band_over_geometry",
            "cb_band": [CB_BAND_LO, CB_BAND_HI] if cb is None else [cb, cb],
            "froude_derived": round(froude_number(speed, geo["length_m"]), 6),
        }

        ct_points = []
        for cb_try in cb_values:
            est = estimate_resistance(**base, cb=cb_try)
            ct_points.append({
                "cb": round(cb_try, 4),
                "ct": est["ct"],
                "rt_closure_n": est["total_resistance_n"],
                "wetted_surface_closure_m2": est["wetted_surface_m2"],
                "err_pct_ct": (round(abs(est["ct"] - row["ref_ct"]) / row["ref_ct"] * 100.0, 2)
                               if row["ref_ct"] else None),
            })
            row["in_validity_range"] = est["validity"]["in_validity_range"]
        row["points"] = ct_points
        errs = [p["err_pct_ct"] for p in ct_points if p["err_pct_ct"] is not None]
        if errs:
            row["err_pct_ct"] = errs[0]
            row["err_pct_ct_min"] = round(min(errs), 2)
            row["err_pct_ct_max"] = round(max(errs), 2)

        # 力口径：同一 Cb 下 closure S vs declared S
        cb_ref = cb_values[0]
        rt_closure = ct_points[0]["rt_closure_n"]
        row["rt"] = {"closure_n": rt_closure,
                     "wetted_surface_closure_m2": ct_points[0]["wetted_surface_closure_m2"]}
        if geo["wetted_surface_m2"] is not None:
            est_d = estimate_resistance(**base, cb=cb_ref, wetted_surface_m2=geo["wetted_surface_m2"])
            row["rt"]["declared_n"] = est_d["total_resistance_n"]
            row["rt"]["declared_wetted_surface_m2"] = est_d["wetted_surface_m2"]
            row["rt"]["surface_bias_pct"] = round((est_d["wetted_surface_m2"] - est_d["wetted_surface_closure_m2"])
                                                  / est_d["wetted_surface_m2"] * 100.0, 2)
            if row["ref_rt_n"]:
                row["rt"]["err_pct_closure"] = round(abs(rt_closure - row["ref_rt_n"]) / row["ref_rt_n"] * 100.0, 2)
                row["rt"]["err_pct_declared"] = round(
                    abs(est_d["total_resistance_n"] - row["ref_rt_n"]) / row["ref_rt_n"] * 100.0, 2)
        rows.append(row)

    return {"rows": rows, "summary": _accuracy_summary(rows)}


def _accuracy_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总两条口径的精度统计。判定线：3% 目标线、15% 不可接受线（平台精度标准）。"""
    def _band(key: str, sub: Optional[str] = None) -> Dict[str, Any]:
        lo: List[float] = []
        hi: List[float] = []
        for r in rows:
            if sub is None:
                if r.get(key) is None:
                    continue
                lo.append(r.get(f"{key}_min", r[key]))
                hi.append(r.get(f"{key}_max", r[key]))
            else:
                value = (r.get(key) or {}).get(sub)
                if value is None:
                    continue
                lo.append(value)
                hi.append(value)
        if not lo:
            return {"n": 0}
        return {
            "n": len(lo),
            "best_case_min_pct": round(min(lo), 2),
            "worst_case_max_pct": round(max(hi), 2),
            "mean_pct": round(sum(lo) / len(lo), 2),
            "n_within_3pct_best_case": sum(1 for e in lo if e <= 3.0),
            "n_within_15pct_best_case": sum(1 for e in lo if e <= 15.0),
        }

    return {
        "ct_vs_ref": _band("err_pct_ct"),
        "rt_vs_ref_closure_surface": _band("rt", "err_pct_closure"),
        "rt_vs_ref_declared_surface": _band("rt", "err_pct_declared"),
        "thresholds": {"acceptable_pct": 3.0, "unacceptable_pct": 15.0},
        "note": "best_case 指 Cb 缺失时在几何区间内取的最有利取值；Ct 与 S 无关，Rt ∝ S",
    }
