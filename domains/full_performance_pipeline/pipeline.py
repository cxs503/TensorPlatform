"""端到端流程编排：DAG 接线 + 契约校验 + 数据流延迟 + 证据统计 + 全性能汇总。

流程（拓扑序）：
    CFG-vessel / CFG-environment  →  N04 导航  →  N05 风载  →  N02 污底
        → N01 失速（功率平衡）  →  N08 砰击 / N09 拖曳 / N10 甲板积水
反馈边：N01.achievable_speed_ms → N04.speed_ms（遭遇速度自洽），固定点迭代至收敛。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import (ADAPTED, MISSING, NATIVE, TYPE_MISMATCH, UNIT_MISMATCH, check_edge,
                        convert, validate_payload)
from .modules_core import FoulingModule, NavigationModule, SpeedLossModule, WindLoadModule
from .modules_ext import DeckWaterModule, PlaceholderModule, SlammingModule, TowingModule
from .providers import EnvironmentConfigModule, VesselConfigModule
from .synthetic import Scenario, build_scenarios

ORDER: Tuple[str, ...] = (
    "CFG-vessel", "CFG-environment", "N04-navigation", "N05-wind-load", "N02-fouling",
    "N01-speed-loss", "N08-slamming", "N09-towing", "N10-deck-water",
)

CONFIG_NODES = ("CFG-vessel", "CFG-environment")


def default_modules() -> Dict[str, Any]:
    return {
        "CFG-vessel": VesselConfigModule(),
        "CFG-environment": EnvironmentConfigModule(),
        "N04-navigation": NavigationModule(),
        "N05-wind-load": WindLoadModule(),
        "N02-fouling": FoulingModule(),
        "N01-speed-loss": SpeedLossModule(),
        "N08-slamming": SlammingModule(),
        "N09-towing": TowingModule(),
        "N10-deck-water": DeckWaterModule(),
    }


@dataclass(frozen=True)
class Edge:
    producer: str
    consumer: str
    mapping: Dict[str, str] = dc_field(default_factory=dict)
    feedback: bool = False
    note: str = ""


EDGES: Tuple[Edge, ...] = (
    Edge("CFG-environment", "N04-navigation"),
    Edge("CFG-vessel", "N05-wind-load"),
    Edge("N04-navigation", "N05-wind-load",
         {"ship_speed_ms": "speed_ms", "ship_heading_deg": "initial_course_deg"},
         note="航速/首向接线：下游字段名 != 上游字段名，显式登记"),
    Edge("N04-navigation", "N02-fouling", note="航速→雷诺数（反馈环的一部分）"),
    Edge("CFG-vessel", "N02-fouling"),
    Edge("CFG-vessel", "N01-speed-loss"),
    Edge("CFG-environment", "N01-speed-loss"),
    Edge("N02-fouling", "N01-speed-loss", note="摩擦阻力 → 失速功率平衡"),
    Edge("N05-wind-load", "N01-speed-loss", note="纵向风载 → 失速"),
    Edge("CFG-vessel", "N08-slamming"),
    Edge("CFG-environment", "N08-slamming"),
    Edge("N01-speed-loss", "N08-slamming", {"ship_speed_ms": "achievable_speed_ms"}),
    Edge("CFG-environment", "N09-towing"),
    Edge("N01-speed-loss", "N09-towing", note="总阻力 → 拖曳张力"),
    Edge("CFG-vessel", "N10-deck-water"),
    Edge("CFG-environment", "N10-deck-water"),
    Edge("N01-speed-loss", "N04-navigation", {"speed_ms": "achievable_speed_ms"},
         feedback=True, note="反馈边：可达航速→遭遇速度，迭代至固定点"),
)

#: 迭代 0 的反馈回退（尚未算过 N01 时用静水参考航速起步）
FEEDBACK_FALLBACK = {"speed_ms": "speed_reference_ms"}


# ------------------------------------------------------------------ wiring helpers
def _resolve(consumer: str, field_name: str,
             mods: Mapping[str, Any], edges: Sequence[Edge]) -> Optional[Tuple[str, str, bool]]:
    """为下游字段找上游来源：(producer_id, 上游字段名, 是否反馈边)。"""
    for e in edges:
        if e.consumer != consumer:
            continue
        src = e.mapping.get(field_name, field_name)
        if e.producer in mods and any(f.name == src for f in mods[e.producer].contract.provides):
            return e.producer, src, e.feedback
    return None


def gather_inputs(consumer: str, mods: Mapping[str, Any], edges: Sequence[Edge],
                  cfg: Mapping[str, Any], produced: Mapping[str, Mapping[str, Any]],
                  prev: Mapping[str, Mapping[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """组装下游模块输入：优先上游输出（含单位换算），其次配置注入，反馈边回退到迭代 0 值。"""
    inputs: Dict[str, Any] = {}
    prov: Dict[str, str] = {}
    for f in mods[consumer].contract.requires:
        src = _resolve(consumer, f.name, mods, edges)
        if src is None:
            if f.name in cfg:
                inputs[f.name] = cfg[f.name]
                prov[f.name] = "config"
                continue
            raise KeyError(f"UNWIRED:{consumer}.{f.name}")
        pid, pname, feedback = src
        pool = prev if feedback else produced
        if pid in pool and pname in pool[pid]:
            val = pool[pid][pname]
            src_unit = mods[pid].contract.provided_unit(pname)
            inputs[f.name] = convert(val, src_unit, f.unit)
            prov[f.name] = f"{pid}.{pname}" + ("(feedback)" if feedback else "")
        elif feedback and FEEDBACK_FALLBACK.get(f.name) in cfg:
            inputs[f.name] = cfg[FEEDBACK_FALLBACK[f.name]]
            prov[f.name] = f"config.{FEEDBACK_FALLBACK[f.name]}(iter0)"
        elif f.name in cfg:
            inputs[f.name] = cfg[f.name]
            prov[f.name] = "config"
        else:
            raise KeyError(f"UNWIRED:{consumer}.{f.name}")
    return inputs, prov


# ------------------------------------------------------------------ contract audit
def verify_contracts(mods: Mapping[str, Any], edges: Sequence[Edge],
                     cfg_keys: Sequence[str]) -> Dict[str, Any]:
    """静态契约审计。

    两层判据：
    1) 逐边：该边**负责**的字段（_resolve 归属该 producer）必须名字/类型/单位全匹配；
       单位不同但已登记换算 → adapted（记为“非原生直连”，计入报告）；
    2) 逐模块：每个 requires 字段必须有归属（上游边）或明确标注为配置注入，否则算未接线。
    """
    cfg_set = set(cfg_keys)
    edge_rows: List[Dict[str, Any]] = []
    for e in edges:
        mod_p, mod_c = mods[e.producer], mods[e.consumer]
        in_scope: List[Dict[str, Any]] = []
        for f in mod_c.contract.requires:
            src = _resolve(e.consumer, f.name, mods, edges)
            if src is None or src[0] != e.producer:
                continue
            src_name = src[1]
            src_unit = mod_p.contract.provided_unit(src_name)
            src_kind = next(x.kind for x in mod_p.contract.provides if x.name == src_name)
            if {src_kind, f.kind} - {"float", "int"} and src_kind != f.kind:
                status = TYPE_MISMATCH
            else:
                try:
                    convert(1.0, src_unit, f.unit)
                    status = NATIVE if src_unit == f.unit else ADAPTED
                except KeyError:
                    status = UNIT_MISMATCH
            in_scope.append({"field": f.name, "from": f"{e.producer}.{src_name}",
                             "src_unit": src_unit, "dst_unit": f.unit, "status": status,
                             "renamed": src_name != f.name})
        counts: Dict[str, int] = {}
        for r in in_scope:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        ok = all(r["status"] in (NATIVE, ADAPTED) for r in in_scope)
        edge_rows.append({
            "producer": e.producer, "consumer": e.consumer, "feedback": e.feedback,
            "note": e.note, "fields_in_scope": len(in_scope), "counts": counts,
            "contract_ok": ok, "checks": in_scope,
        })

    module_rows: List[Dict[str, Any]] = []
    g = {"requires": 0, "wired_native": 0, "wired_adapted": 0, "config": 0, "missing": 0}
    for mid in ORDER:
        mod = mods[mid]
        rows: List[Dict[str, Any]] = []
        for f in mod.contract.requires:
            src = _resolve(mid, f.name, mods, edges)
            if src is None:
                status = "config" if f.name in cfg_set else MISSING
                rows.append({"field": f.name, "unit": f.unit, "status": status,
                             "source": "config" if status == "config" else "-"})
            else:
                src_unit = mods[src[0]].contract.provided_unit(src[1])
                try:
                    convert(1.0, src_unit, f.unit)
                    status = NATIVE if src_unit == f.unit else ADAPTED
                except KeyError:
                    status = UNIT_MISMATCH
                rows.append({"field": f.name, "unit": f.unit, "status": status,
                             "source": f"{src[0]}.{src[1]}"})
        c: Dict[str, int] = {}
        for r in rows:
            c[r["status"]] = c.get(r["status"], 0) + 1
        module_rows.append({
            "module": mid, "requires": len(rows),
            "wired": c.get(NATIVE, 0) + c.get(ADAPTED, 0),
            "native": c.get(NATIVE, 0), "adapted": c.get(ADAPTED, 0),
            "config": c.get("config", 0), "missing": c.get(MISSING, 0),
            "mismatch": c.get(UNIT_MISMATCH, 0) + c.get(TYPE_MISMATCH, 0),
            "fields": rows,
        })
        g["requires"] += len(rows)
        g["wired_native"] += c.get(NATIVE, 0)
        g["wired_adapted"] += c.get(ADAPTED, 0)
        g["config"] += c.get("config", 0)
        g["missing"] += c.get(MISSING, 0) + c.get(UNIT_MISMATCH, 0) + c.get(TYPE_MISMATCH, 0)
    return {
        "edges": edge_rows,
        "modules": module_rows,
        "globals": g,
        "edge_count": len(edges),
        "feedback_edges": sum(1 for e in edges if e.feedback),
        "all_ok": g["missing"] == 0 and all(e["contract_ok"] for e in edge_rows),
    }


def evidence_stats(mods: Mapping[str, Any]) -> Dict[str, Any]:
    """证据等级统计（逐字段 + 模块级最弱项）。"""
    per_field: List[Dict[str, Any]] = []
    for mid in ORDER:
        per_field.extend(mods[mid].graded_provides())
    counts: Dict[str, int] = {}
    for r in per_field:
        counts[r["grade"]] = counts.get(r["grade"], 0) + 1
    total = len(per_field)
    module_grades = [{"module": mid, "grade": mods[mid].module_grade()} for mid in ORDER]
    by_module: Dict[str, int] = {}
    for r in module_grades:
        by_module[r["grade"]] = by_module.get(r["grade"], 0) + 1
    return {
        "fields_total": total,
        "field_counts": counts,
        "field_ratio": {k: round(v / total, 6) for k, v in counts.items()},
        "module_grades": module_grades,
        "module_counts": by_module,
        "fields": per_field,
    }


# ------------------------------------------------------------------ scenario run
def run_scenario(scn: Scenario, mods: Optional[Mapping[str, Any]] = None,
                 tol: float = 1e-7, max_iter: int = 200,
                 relax: float = 0.5) -> Dict[str, Any]:
    """跑一个算例的完整 DAG（含反馈固定点迭代），返回结果字典（不含报告级加工）。"""
    mods = mods or default_modules()
    cfg = dict(scn.config_flat())
    cfg.setdefault("wind_coeffs", None)

    stage_ms: Dict[str, List[float]] = {mid: [] for mid in ORDER}
    prev: Dict[str, Dict[str, Any]] = {}
    produced: Dict[str, Dict[str, Any]] = {}
    iteration_rows: List[Dict[str, Any]] = []
    # 反馈变量：遭遇速度初值 = 静水参考航速（欠松弛固定点迭代）
    fb_speed = float(cfg["speed_reference_ms"])
    t_all0 = time.perf_counter()

    for it in range(max_iter):
        prev = {"N01-speed-loss": {"achievable_speed_ms": fb_speed}}
        produced = {}
        for mid in ORDER:
            mod = mods[mid]
            t0 = time.perf_counter()
            if mid in CONFIG_NODES:      # 配置节点：直接由场景配置填充
                need = [f.name for f in mod.contract.provides]
                inputs = {k: cfg[k] for k in need if k in cfg}
                prov = {k: "config" for k in inputs}
            else:
                inputs, prov = gather_inputs(mid, mods, EDGES, cfg, produced, prev)
            out = mod.compute(inputs)
            dt = (time.perf_counter() - t0) * 1000.0
            stage_ms[mid].append(dt)
            produced[mid] = out
            produced.setdefault("__prov__", {})[mid] = prov
        v_raw = float(produced["N01-speed-loss"]["achievable_speed_ms"])
        dv = abs(v_raw - fb_speed)                  # 固定点残差 |V_R(V_fb) − V_fb|
        iteration_rows.append({"iteration": it, "speed_ms": v_raw,
                               "fb_speed_ms": fb_speed, "delta_m_s": dv})
        if dv < tol:
            fb_speed = v_raw
            break
        fb_speed = relax * v_raw + (1.0 - relax) * fb_speed
    e2e_ms = (time.perf_counter() - t_all0) * 1000.0

    payload = {mid: validate_payload(mods[mid].contract, produced[mid], "output")
               for mid in ORDER}
    contract = verify_contracts(mods, EDGES, list(cfg.keys()))
    ev = evidence_stats(mods)

    # ---- 全性能汇总（整合层 KPI，定义式，非新物理）
    nav = produced["N04-navigation"]
    fou = produced["N02-fouling"]
    loss = produced["N01-speed-loss"]
    wind = produced["N05-wind-load"]
    slam = produced["N08-slamming"]
    tow = produced["N09-towing"]
    deck = produced["N10-deck-water"]
    v_ref = float(cfg["speed_reference_ms"])
    v_ach = float(loss["achievable_speed_ms"])
    dist_nm = float(nav["gc_distance_nm"])
    eta_weather_h = float(nav["eta_h"])
    eta_calm_h = dist_nm * 1852.0 / v_ref / 3600.0
    sfoc = float(cfg.get("sfoc_g_per_kwh", 175.0))
    fuel_t = float(loss["required_power_kw"]) * eta_weather_h * sfoc / 1e6

    performance = {
        "distance_nm": dist_nm,
        "speed_reference_ms": v_ref,
        "speed_achievable_ms": v_ach,
        "speed_loss_pct": float(loss["speed_loss_pct"]),
        "friction_resistance_N": float(fou["friction_resistance_N"]),
        "dcf_roughness": float(fou["dcf_roughness"]),
        "added_resistance_wave_N": float(loss["added_resistance_wave_N"]),
        "added_resistance_wind_N": float(loss["added_resistance_wind_N"]),
        "total_resistance_N": float(loss["total_resistance_N"]),
        "required_power_kw": float(loss["required_power_kw"]),
        "eta_calm_h": eta_calm_h,
        "eta_weather_h": eta_weather_h,
        "eta_delay_h": eta_weather_h - eta_calm_h,
        "voyage_fuel_t": fuel_t,
        "wind_heeling_moment_Nm": float(wind["heeling_moment_Nm"]),
        "wind_yaw_moment_Nm": float(wind["yaw_moment_Nm"]),
        "slamming_pressure_kpa": float(slam["peak_pressure_kpa"]),
        "slamming_probability": float(slam["slamming_probability"]),
        "slamming_per_hour": float(slam["encounters_per_hour"]),
        "tow_top_tension_N": float(tow["top_tension_N"]),
        "tow_bollard_utilization": float(tow["bollard_utilization"]),
        "tow_ok": int(tow["tow_ok"]),
        "deck_water_mass_t": float(deck["water_mass_t"]),
        "deck_free_surface_correction_m": float(deck["free_surface_correction_m"]),
        "deck_gm_effective_m": float(deck["gm_effective_m"]),
        "deck_heel_deg": float(deck["equilibrium_heel_deg"]),
        "deck_stability_margin_m": float(deck["stability_margin_m"]),
        "deck_heel_ok": int(deck["heel_ok"]),
        "deck_overflow": int(deck["overflow_flag"]),
    }

    return {
        "scenario": scn.key,
        "outputs": {mid: produced[mid] for mid in ORDER},
        "provenance": produced.get("__prov__", {}),
        "iterations": iteration_rows,
        "converged": iteration_rows[-1]["delta_m_s"] < tol,
        "timing": {
            "stage_mean_ms": {mid: (sum(v) / len(v) if v else 0.0) for mid, v in stage_ms.items()},
            "stage_max_ms": {mid: max(v) if v else 0.0 for mid, v in stage_ms.items()},
            "e2e_ms": e2e_ms,
            "n_iterations": len(iteration_rows),
        },
        "payload_checks": payload,
        "contract": contract,
        "evidence": ev,
        "performance": performance,
    }


def run_all(mods: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    return [run_scenario(s, mods) for s in build_scenarios()]


def make_placeholder_modules(grades: Mapping[str, str]) -> Dict[str, Any]:
    """把指定模块换成 MOCK/STUB 占位（证据门自检用）。"""
    mods = default_modules()
    for mid, grade in grades.items():
        mods[mid] = PlaceholderModule(mods[mid], grade)
    return mods