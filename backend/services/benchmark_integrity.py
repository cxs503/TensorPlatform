"""基准数据集一致性门（Benchmark Integrity Gates）。

背景
----
``backend/data/benchmark_reference.json`` 被 ``/api/v1/calculations/benchmark_validate``
直接消费，用来给阻力计算打 PASS/FAIL。一旦参考记录自身内部矛盾（例如声明 Ct 与
声明 Rt 在给定 S、V 下隐含一个非物理的水密度），下游所有 PASS/FAIL 判决都失去意义。

本模块把这些"内部矛盾"变成机器可判的门：

G1  Fr 自洽      Fr_declared ?= V/sqrt(gL)                      容差 1%
G2  Ct-Rt-ρ      ρ_implied = Rt/(0.5·Ct·S·V²) ∈ 水的物理区间    反解，不预设 ρ
G3  Cb-排水量    Cb_declared ?= m/(ρ·L·B·T)                     容差 2%
G4b 几何预设     SHIP_GEOMETRY 跨字段同值（复制粘贴污染）

设计约定
--------
* 纯标准库、无 IO 副作用（``load_benchmark`` 之外），便于被 API、CLI、pytest 共用。
* 不修数：本模块只报矛盾，不猜哪个数对——修数需要母型船/文献依据。
* 每条 finding: {gate, case_id, severity: FAIL|WARN|INFO, quantity, declared,
  computed, residual_rel, note, action}。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

G_STANDARD = 9.80665
RHO_WATER_RANGE = (990.0, 1035.0)   # 覆盖淡水 4℃ ~ 海水 25℃，超出即非水
FR_TOL_REL = 0.01                    # 声明 Fr 常只给 3 位小数
CB_TOL_REL = 0.02
WETTED_SURFACE_TOL_REL = 0.05

CONTAMINATION_KEYS = ("length", "beam", "draft", "diameter", "displacement", "cb")


def relative_error(calc: float, ref: float) -> float:
    if ref == 0:
        return abs(calc - ref)
    return abs(calc - ref) / abs(ref)


def froude(speed: float, length: float, g: float = G_STANDARD) -> float:
    return speed / math.sqrt(g * length)


def implied_density(rt: float, ct: float, s: float, speed: float) -> float:
    """Rt = Ct·0.5·ρ·S·V² 反解 ρ；不预设密度，避免"选密度凑数"。"""
    denom = 0.5 * ct * s * speed * speed
    return rt / denom if denom > 0 else float("nan")


def implied_cb(mass_kg: float, length: float, beam: float, draft: float, rho: float) -> float:
    vol = length * beam * draft
    return mass_kg / (rho * vol) if vol > 0 else float("nan")


def finding(gate: str, case_id: str, severity: str, quantity: str,
            declared: Any = None, computed: Any = None, note: str = "",
            action: str = "", residual_rel: Optional[float] = None) -> Dict[str, Any]:
    return {
        "gate": gate,
        "case_id": case_id,
        "severity": severity,
        "quantity": quantity,
        "declared": declared,
        "computed": computed,
        "residual_rel": residual_rel,
        "note": note,
        "action": action,
    }


def displacement_kg(geometry: Dict[str, Any]) -> Optional[float]:
    """读排水量并统一到 kg（数据集里 kg / t 两种单位并存，是易错点）。"""
    if geometry.get("displacement_kg") is not None:
        return float(geometry["displacement_kg"])
    if geometry.get("displacement_t") is not None:
        return float(geometry["displacement_t"]) * 1000.0
    return None


# ---------------------------------------------------------------------------
# G1
# ---------------------------------------------------------------------------
def gate_froude(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for case in cases:
        cid = case.get("case_id", "?")
        geo = case.get("geometry") or {}
        speed, length, fr_d = case.get("speed_ms"), geo.get("length_m"), case.get("froude_number")
        if speed is None or length is None or fr_d is None:
            out.append(finding("G1", cid, "WARN", "froude_number", fr_d, None,
                               "工况缺 speed_ms/length_m/froude_number，无法自洽校验",
                               "补齐工况三元组"))
            continue
        fr_c = froude(float(speed), float(length))
        res = relative_error(fr_c, float(fr_d))
        if res <= FR_TOL_REL:
            out.append(finding("G1", cid, "INFO", "froude_number", fr_d, round(fr_c, 6),
                               f"Fr 自洽（残差 {res:.2%}，容差 {FR_TOL_REL:.0%}）",
                               residual_rel=res))
        else:
            implied_len = float(speed) ** 2 / (G_STANDARD * float(fr_d) ** 2)
            out.append(finding(
                "G1", cid, "FAIL", "froude_number", fr_d, round(fr_c, 6),
                f"Fr=V/√(gL) 与声明值不符：按 L={length} m 应为 {fr_c:.3f}，声明 {fr_d}"
                f"（残差 {res:.1%}）；声明值隐含 L≈{implied_len:.1f} m，"
                f"说明该条记录把不同尺度的航速与长度写在了一起",
                "该记录不可作 Fr 相关判据基准；须统一尺度后重算航速或显式标注 scale",
                residual_rel=res))
    return out


# ---------------------------------------------------------------------------
# G2
# ---------------------------------------------------------------------------
def gate_ct_resistance(cases: List[Dict[str, Any]],
                       rho_range: tuple = RHO_WATER_RANGE) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    lo, hi = rho_range
    for case in cases:
        cid = case.get("case_id", "?")
        geo = case.get("geometry") or {}
        s, speed = geo.get("wetted_surface_m2"), case.get("speed_ms")
        ct, rt = case.get("experimental_ct"), case.get("experimental_rt_n")
        if None in (s, speed, ct, rt):
            out.append(finding("G2", cid, "WARN", "resistance_decomposition", None, None,
                               "缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系",
                               "补湿表面积，否则 Ct 不可复核（判决不可采信）"))
            continue
        rho_i = implied_density(float(rt), float(ct), float(s), float(speed))
        if lo <= rho_i <= hi:
            out.append(finding("G2", cid, "INFO", "implied_density", None, round(rho_i, 2),
                               f"Rt 与 Ct 隐含 ρ={rho_i:.1f} kg/m³，落在水的物理区间 {rho_i and rho_range}内"))
        else:
            out.append(finding(
                "G2", cid, "FAIL", "implied_density", None, round(rho_i, 2),
                f"Rt={rt} N 与 Ct={ct} 在 S={s} m²、V={speed} m/s 下隐含 ρ={rho_i:.1f} kg/m³，"
                f"超出水的物理区间 {rho_range}（{rho_i / 1025.0:.2f}× 海水）；"
                f"两者必有一错，或 Ct 用了别的参考面积口径",
                "修数或显式声明 Ct 的参考面积/密度口径，否则 benchmark 判决无效"))
    return out


# ---------------------------------------------------------------------------
# G3
# ---------------------------------------------------------------------------
def gate_cb_displacement(cases: List[Dict[str, Any]],
                         rho: float = 1025.0, rho_alt: float = 998.2,
                         tol: float = CB_TOL_REL) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for case in cases:
        cid = case.get("case_id", "?")
        geo = case.get("geometry") or {}
        length, beam, draft = geo.get("length_m"), geo.get("beam_m"), geo.get("draft_m")
        cb, mass = geo.get("cb"), displacement_kg(geo)
        if length is None or beam is None or draft is None or cb is None:
            out.append(finding("G3", cid, "WARN", "cb_geometry", cb, None,
                               "缺 L/B/T/Cb 之一：几何未验证，不得进入阻力口径",
                               "补齐主尺度，或标注为非排水型体（并按体积系数另行定义）"))
            continue
        vol = float(length) * float(beam) * float(draft)
        if mass is None:
            out.append(finding("G3", cid, "WARN", "displacement", None, round(float(cb) * vol, 4),
                               f"未给排水量：按 Cb·L·B·T 得 ∇={float(cb) * vol:.4f} m³",
                               "补排水量以闭合几何自洽门"))
            continue
        cb_main = implied_cb(mass, float(length), float(beam), float(draft), rho)
        cb_alt = implied_cb(mass, float(length), float(beam), float(draft), rho_alt)
        res = relative_error(cb_main, float(cb))
        if res <= tol:
            out.append(finding("G3", cid, "INFO", "cb", cb, round(cb_main, 4),
                               f"Cb–排水量自洽（ρ={rho:g} 反解 Cb={cb_main:.4f}，残差 {res:.2%}）",
                               residual_rel=res))
        else:
            out.append(finding(
                "G3", cid, "FAIL", "cb", cb, round(cb_main, 4),
                f"声明 Cb={cb} 与排水量 {mass:.0f} kg、L·B·T={vol:.2f} m³ 矛盾："
                f"ρ={rho:g} 反解得 Cb={cb_main:.4f}（淡水下 {cb_alt:.4f}），残差 {res:.1%}",
                "几何必须先过 Cb/L/B 自洽门再谈阻力误差判据；修数须引母型船资料",
                residual_rel=res))
    return out


# ---------------------------------------------------------------------------
# G4b 几何预设污染
# ---------------------------------------------------------------------------
def preset_contamination(presets: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ship, geo in presets.items():
        buckets: Dict[float, List[str]] = {}
        for key, val in geo.items():
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            if key.split("_")[0] not in CONTAMINATION_KEYS:
                continue
            buckets.setdefault(float(val), []).append(key)
        hits = {v: ks for v, ks in buckets.items() if len(ks) >= 3}
        if hits:
            detail = "; ".join(f"{v:g} → {ks}" for v, ks in hits.items())
            out.append(finding(
                "G4b", ship, "FAIL", "geometry_preset", geo, None,
                f"SHIP_GEOMETRY['{ship}'] 跨字段同值（复制粘贴污染）：{detail}；"
                f"主尺度/排水量/棱形系数不可能同值",
                "逐字段按母型船资料填，否则阻力/排水量口径全链路失真"))
        else:
            out.append(finding("G4b", ship, "INFO", "geometry_preset", geo, None,
                               "SHIP_GEOMETRY 预设无跨字段同值污染"))
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def load_benchmark(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def summarise(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
    per_gate: Dict[str, Dict[str, int]] = {}
    for f in findings:
        sev = f["severity"]
        counts[sev] = counts.get(sev, 0) + 1
        per_gate.setdefault(f["gate"], {"FAIL": 0, "WARN": 0, "INFO": 0})[sev] += 1
    if counts.get("FAIL", 0):
        verdict = "FAIL"
        message = "参考数据/公式存在内部矛盾：下游 PASS/FAIL 判决不可信（见 findings）"
    elif counts.get("WARN", 0):
        verdict = "WARN"
        message = "无内部矛盾，但溯源/几何证据不足，不可升 REAL"
    else:
        verdict = "PASS"
        message = "全部一致性门通过"
    return {"counts": counts, "per_gate": per_gate, "verdict": verdict, "message": message}


def audit_benchmark(data: Dict[str, Any],
                    presets: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """对一份基准数据集跑 G1–G3（+可选 G4b），返回 {summary, findings}。"""
    cases = data.get("cases", []) or []
    findings = gate_froude(cases) + gate_ct_resistance(cases) + gate_cb_displacement(cases)
    if presets:
        findings += preset_contamination(presets)
    return {"summary": summarise(findings), "findings": findings}