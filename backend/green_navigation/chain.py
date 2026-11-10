"""端到端装配：算例 → 几何门 → 匹配 → EEDI / CII / 退化轨迹 → 验收判据 E1–E8。

代码里没有任何船名或域常数：算例、SFOC 参考表、主机目录、CII 边界、外部锚点
全部从 ``data/*.json`` 注入（路径可覆盖），这满足"平台仓零领域语义"的边界
铁律 —— 领域数据是工件，不是代码。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cii as cii_mod
from . import eedi as eedi_mod
from . import engine as engine_mod
from . import geometry as geometry_mod
from . import matching as matching_mod

from services import green_navigation as gn  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

# 验收判据（任务书给定；数字在这里集中定义一次）
CRITERIA = {
    "E1_sfoc_vs_reference_rel_err_pct": 5.0,
    "E2_power_fraction_center": 0.85,
    "E2_power_fraction_tol": 0.05,
    "E3_eedi_vs_reference_rel_err_pct": 10.0,
    "E5_degradation_band_pct_per_year": (0.5, 1.5),
}


def load_json(name: str, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    p = (Path(data_dir) if data_dir else DATA_DIR) / name
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_cases(data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    return load_json("cases.json", data_dir)["cases"]


def load_anchors(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    return load_json("external_anchors.json", data_dir)["anchors"]


def calibrate_sfoc(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """SFOC 图标定 + 逐点复核（E1 判据的数据来源）。"""
    ref = load_json("sfoc_reference.json", data_dir)
    model = engine_mod.fit_sfoc_map(ref["points"])
    validation = engine_mod.validate_sfoc_map(
        model, ref["points"], tol_pct=CRITERIA["E1_sfoc_vs_reference_rel_err_pct"])
    return {
        "model": model,
        "model_dict": model.as_dict(),
        "reference_provenance": ref["provenance"],
        "reference_points": ref["points"],
        "validation": validation,
    }


def _select_engine(case: Dict[str, Any], brake_power_design_kw: float,
                   data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """按算例声明的方式确定主机 MCR：固定额定或目录选型规则（≥P_B/0.85 最小档）。"""
    spec = dict(case["engine"])
    if spec.get("selection") == "fixed":
        if not spec.get("mcr_kw"):
            raise ValueError(f"{case['case_id']}: fixed 选型缺 mcr_kw")
        spec["selection_basis"] = "厂家额定档位（独立输入，可作 E2 独立验证）"
        spec["independent"] = True
        return spec
    catalogue = load_json("engine_catalogue.json", data_dir)["rated"]
    family = spec.get("family")
    need = brake_power_design_kw / CRITERIA["E2_power_fraction_center"]
    pool = [r for r in catalogue if family is None or r["family"] == family]
    pool = [r for r in pool if r["mcr_kw"] >= need]
    if not pool:
        raise ValueError(f"{case['case_id']}: 主机目录无 ≥{need:.0f} kW 的档位")
    chosen = min(pool, key=lambda r: r["mcr_kw"])
    spec.update({
        "model": chosen["model"], "mcr_kw": chosen["mcr_kw"],
        "mcr_rpm": chosen["mcr_rpm"],
        "selection_basis": f"目录选型规则：最小 ≥ P_B(设计)/0.85 = {need:.0f} kW",
        "independent": False,
        "catalogue_provenance": load_json("engine_catalogue.json", data_dir)["provenance"],
    })
    return spec


def _ship_parts(case: Dict[str, Any]):
    geo = case["geometry"]
    geometry_mod.validate_geometry(
        geo["L_m"], geo["B_m"], geo["T_m"], geo["cb"],
        displacement_t=case.get("displacement_t"), dwt_t=case.get("dwt_t"), strict=True)
    hull = case.get("hull_coeffs", {})
    ship = matching_mod.ShipGeometry(
        length_m=geo["L_m"], beam_m=geo["B_m"], draft_m=geo["T_m"], cb=geo["cb"],
        displacement_t=case.get("displacement_t"),
        cm=hull.get("cm"), cwp=hull.get("cwp"),
        lcb_pct=hull.get("lcb_pct", 0.0), i_e_deg=hull.get("i_e_deg", 25.0),
        a_bt=hull.get("a_bt", 0.0), h_b=hull.get("h_b", 0.0), a_tr=hull.get("a_tr", 0.0),
        published_wetted_surface_m2=case.get("published_wetted_surface_m2"),
    )
    p = case["propeller"]
    prop = matching_mod.PropellerSpec(
        diameter_m=p["diameter_m"], pitch_ratio=p["pitch_ratio"], ae_ao=p["ae_ao"],
        blades=p["blades"], wake_fraction=p["wake_fraction"],
        thrust_deduction=p["thrust_deduction"],
        relative_rotative_efficiency=p.get("relative_rotative_efficiency", 1.0),
    )
    pt = case["powertrain"]
    train = matching_mod.PowertrainSpec(eta_shaft=pt["eta_shaft"], eta_gear=pt["eta_gear"])
    return ship, prop, train


def _wetted_surface_check(ship: matching_mod.ShipGeometry,
                          tol_pct: float = 2.0) -> Optional[Dict[str, Any]]:
    """Holtrop 湿表面积 vs 公开值（几何/系数处理是否忠实的独立校验）。"""
    if not ship.published_wetted_surface_m2:
        return None
    res = matching_mod.resistance_at(ship, 1.0)
    got = float(res["wetted_surface"])
    ref = float(ship.published_wetted_surface_m2)
    err = abs(got - ref) / ref * 100.0
    return {"holtrop_m2": got, "published_m2": ref, "rel_err_pct": err,
            "within_2pct": err < tol_pct, "evidence_grade": gn.EVIDENCE_REAL}


def evaluate_case(case: Dict[str, Any], sfoc: Dict[str, Any],
                  anchors: Dict[str, Any],
                  data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """单个算例的完整链：几何门 → 匹配 → EEDI → CII + 5 年退化轨迹。"""
    ship, prop, train = _ship_parts(case)
    sfoc_model: engine_mod.SfocMap = sfoc["model"]

    # --- 设计点匹配（设计航速，静水，新船粗糙度）
    design_point = matching_mod.match_at_speed(ship, prop, train, case["design_speed_kn"])
    engine = _select_engine(case, design_point.brake_power_kw, data_dir)
    mcr = float(engine["mcr_kw"])
    power_fraction = design_point.brake_power_kw / mcr

    # --- EEDI 参考点：75% MCR
    p_me = eedi_mod.EEDI_REFERENCE_LOAD * mcr
    v_ref_kn, eedi_point = matching_mod.speed_for_brake_power(
        ship, prop, train, p_me, ks_m=matching_mod.DEFAULT_ROUGHNESS_KS)
    cf_me = gn.carbon_factor(case["fuel"]["main"])
    cf_ae = gn.carbon_factor(case["fuel"]["aux"])
    sfoc_me = sfoc_model.sfoc(eedi_mod.EEDI_REFERENCE_LOAD)
    capacity = eedi_mod.capacity_for_ship_type(
        case["ship_type"], case["dwt_t"], case.get("capacity_rule"))
    eedi_attained = eedi_mod.eedi_reference_point(
        brake_power_kw=p_me, capacity_t=capacity["capacity_t"], v_ref_kn=v_ref_kn,
        sfoc_me_g_kwh=sfoc_me, cf_me=cf_me,
        sfoc_ae_g_kwh=170.0, cf_ae=cf_ae,
    )
    eedi_verdicts = {
        phase: eedi_mod.evaluate_eedi(case["ship_type"],
                                      eedi_attained["attained_g_per_t_nm"],
                                      capacity["capacity_t"], phase=phase)
        for phase in ("phase1", "phase2", "phase3")
    }

    # --- CII + 退化（两个曝露场景：年均清洗维护 15 µm/年；5 年不清洁 30 µm/年）
    scenarios = {}
    for key, growth in (("maintained_15um_per_year", 15e-6),
                        ("uncleaned_30um_per_year", 30e-6)):
        scenarios[key] = cii_mod.degradation_trajectory(
            ship, prop, train, sfoc_model, case["ship_type"], case["dwt_t"],
            capacity["capacity_t"], case["design_speed_kn"], mcr,
            years=5, first_year=2023, roughness_growth_m_per_year=growth,
            profile=cii_mod.OperatingProfile(),
        )
    trajectory = scenarios["maintained_15um_per_year"]

    # --- 外部锚对照（E3）
    anchor = anchors["imo_eedi_class_mean"]["by_case"].get(case["case_id"], {})
    eedi_anchor_cmp = None
    if anchor.get("value_g_per_t_nm"):
        ref = float(anchor["value_g_per_t_nm"])
        err = abs(eedi_attained["attained_g_per_t_nm"] - ref) / ref * 100.0
        band = anchor.get("band_g_per_t_nm")
        eedi_anchor_cmp = {
            "reference_g_per_t_nm": ref,
            "reference_band_g_per_t_nm": band,
            "reference_group": anchor.get("group"),
            "rel_err_pct": err,
            "within_10pct": err < CRITERIA["E3_eedi_vs_reference_rel_err_pct"],
            "in_reference_band": bool(band and band[0] <= eedi_attained["attained_g_per_t_nm"] <= band[1]),
            "use_for_e3": bool(anchor.get("use_for_e3", False)),
            "anchor_note": anchor.get("note"),
            "evidence_grade": anchors["imo_eedi_class_mean"]["grade"],
            "caveat": anchors["imo_eedi_class_mean"]["caveat"],
        }

    return {
        "case_id": case["case_id"],
        "label": case["label"],
        "ship_type": case["ship_type"],
        "provenance": case.get("provenance", {}),
        "geometry_check": geometry_mod.validate_geometry(
            case["geometry"]["L_m"], case["geometry"]["B_m"], case["geometry"]["T_m"],
            case["geometry"]["cb"], displacement_t=case.get("displacement_t"),
            dwt_t=case.get("dwt_t"), strict=False).as_dict(),
        "design_point": design_point.as_dict(),
        "engine": engine,
        "power_fraction_at_design_speed": power_fraction,
        "eedi": {
            "reference_load": eedi_mod.EEDI_REFERENCE_LOAD,
            "p_me_kw": p_me,
            "v_ref_kn": v_ref_kn,
            "match_point_75pct": eedi_point.as_dict(),
            "capacity": capacity,
            "attained": eedi_attained,
            "verdicts": eedi_verdicts,
            "external_anchor": eedi_anchor_cmp,
        },
        "cii": {
            "trajectory": trajectory,
            "sensitivity_scenarios": scenarios,
            "wetted_surface_check": _wetted_surface_check(ship),
            "boundaries": cii_mod.boundaries_for(case["ship_type"]).as_dict(),
        },
    }


def negative_controls(sfoc: Dict[str, Any]) -> Dict[str, Any]:
    """E6/E7 负控：P_ME=0 → EEDI=0；V_ref→∞ → EEDI→0。"""
    sfoc_model: engine_mod.SfocMap = sfoc["model"]
    zero = eedi_mod.eedi_reference_point(
        brake_power_kw=0.0, capacity_t=320000.0, v_ref_kn=14.0,
        sfoc_me_g_kwh=sfoc_model.sfoc(0.75), cf_me=gn.carbon_factor("hfo"),
        sfoc_ae_g_kwh=170.0, cf_ae=gn.carbon_factor("mdo"),
    )
    base = eedi_mod.eedi_reference_point(
        brake_power_kw=19050.0, capacity_t=320000.0, v_ref_kn=14.8,
        sfoc_me_g_kwh=sfoc_model.sfoc(0.75), cf_me=gn.carbon_factor("hfo"),
        sfoc_ae_g_kwh=170.0, cf_ae=gn.carbon_factor("mdo"),
    )
    huge = eedi_mod.eedi_reference_point(
        brake_power_kw=19050.0, capacity_t=320000.0, v_ref_kn=1.0e9,
        sfoc_me_g_kwh=sfoc_model.sfoc(0.75), cf_me=gn.carbon_factor("hfo"),
        sfoc_ae_g_kwh=170.0, cf_ae=gn.carbon_factor("mdo"),
    )
    return {
        "p_zero_eedi": zero["attained_g_per_t_nm"],
        "p_zero_p_ae_kw": zero["p_ae_kw"],
        "baseline_eedi": base["attained_g_per_t_nm"],
        "v_inf_eedi": huge["attained_g_per_t_nm"],
        "v_inf_rel_to_baseline": huge["attained_g_per_t_nm"] / base["attained_g_per_t_nm"],
    }


def evaluate_acceptance(case_results: List[Dict[str, Any]], sfoc: Dict[str, Any],
                        neg: Dict[str, Any]) -> Dict[str, Any]:
    """E1–E8 判据逐条评估（每条给出判据数字与产物字段，不写空话）。"""
    criteria: Dict[str, Any] = {}

    sfoc_val = sfoc["validation"]
    criteria["E1"] = {
        "criterion": "SFOC 与厂家部分负荷表相对误差 <5%（P/P_MCR 0.75–0.90 段单独列出）",
        "value_pct": sfoc_val["max_rel_err_pct_075_090"],
        "value_all_loads_pct": sfoc_val["max_rel_err_pct"],
        "threshold_pct": CRITERIA["E1_sfoc_vs_reference_rel_err_pct"],
        "passed": sfoc_val["within_tol"],
        "metric": "max relative error over reference table",
        "evidence_grade": gn.EVIDENCE_REAL,
        "artifact": "sfoc.validation.points",
    }

    independent = [r for r in case_results if r["engine"].get("independent")]
    e2_rows = [{"case_id": r["case_id"],
                "power_fraction": r["power_fraction_at_design_speed"],
                "mcr_kw": r["engine"]["mcr_kw"],
                "p_b_design_kw": r["design_point"]["brake_power_kw"],
                "engine_independent": r["engine"].get("independent")}
               for r in case_results]
    indep_ok = all(
        abs(row["power_fraction"] - CRITERIA["E2_power_fraction_center"])
        <= CRITERIA["E2_power_fraction_tol"] for row in e2_rows if row["engine_independent"])
    criteria["E2"] = {
        "criterion": "设计航速下 P_engine/P_MCR = 0.85 ± 0.05（仅对厂家独立额定算例判定）",
        "rows": e2_rows,
        "independent_cases": [r["case_id"] for r in e2_rows if r["engine_independent"]],
        "value": min((row["power_fraction"] for row in e2_rows if row["engine_independent"]),
                     default=None),
        "threshold": [round(CRITERIA["E2_power_fraction_center"] - CRITERIA["E2_power_fraction_tol"], 6),
                      round(CRITERIA["E2_power_fraction_center"] + CRITERIA["E2_power_fraction_tol"], 6)],
        "passed": bool(indep_ok and independent),
        "metric": "brake power at design speed / engine MCR",
        "evidence_grade": gn.EVIDENCE_REAL,
        "note": "目录选型规则产生的 MCR（非独立额定）不参与判定，避免循环论证。",
        "artifact": "cases[*].power_fraction_at_design_speed",
    }

    e3_rows = []
    for r in case_results:
        cmp = r["eedi"]["external_anchor"]
        if cmp:
            e3_rows.append({"case_id": r["case_id"], **cmp})
    judged = [row for row in e3_rows if row["use_for_e3"]]
    criteria["E3"] = {
        "criterion": "EEDI_attained 与 IMO 数据库船型分组均值相对误差 <10%（任务书指定主判据：KVLCC2 油轮）",
        "rows": e3_rows,
        "judged_cases": [row["case_id"] for row in judged],
        "value_pct": max((row["rel_err_pct"] for row in judged), default=None),
        "value_pct_all_rows": max((row["rel_err_pct"] for row in e3_rows), default=None),
        "threshold_pct": CRITERIA["E3_eedi_vs_reference_rel_err_pct"],
        "passed": bool(judged) and all(row["within_10pct"] for row in judged),
        "metric": "max relative error vs class-mean anchor",
        "evidence_grade": gn.EVIDENCE_WITHHELD,
        "note": ("对照物是 IMO EEDI 数据库的**船型分组统计均值**（转录，离线不可回查 "
                 "GISIS），故本条判 WITHHELD：算术比较是 REAL，锚点本身不是单船记录。"
                 "置信度不足的行（集装箱船，锚点随 TEU 档位强变化）标 use_for_e3=false，"
                 "不参与判决但如实列出。"),
        "artifact": "cases[*].eedi.external_anchor",
    }

    rating_rows = [{
        "case_id": r["case_id"],
        "ship_type": r["ship_type"],
        "ratings_by_year": r["cii"]["trajectory"]["rating_sequence"],
        "ratio_year1": r["cii"]["trajectory"]["years"][0]["ratio_to_required"],
        "dd": r["cii"]["boundaries"]["dd"],
    } for r in case_results]
    criteria["E4"] = {
        "criterion": "CII 评级与 DNV 计算器一致",
        "rows": rating_rows,
        "value": [row["ratings_by_year"][0] for row in rating_rows],
        "passed": None,
        "status": "WITHHELD",
        "metric": "grade agreement vs external calculator",
        "evidence_grade": gn.EVIDENCE_WITHHELD,
        "reason": ("离线环境无网络/无 DNV API 凭据，无法在线逐船对照 → honest WITHHELD。"
                   "替代证据：G4 边界表反解一致性、逐级区间归属、单调性，"
                   "见 tests/test_cii.py。"),
        "artifact": "cases[*].cii.boundaries",
    }

    deg_rows = [{
        "case_id": r["case_id"],
        "scenario": "maintained_15um_per_year",
        "degradation_pct_per_year": r["cii"]["trajectory"]["degradation_pct_per_year"],
        "rating_sequence": r["cii"]["trajectory"]["rating_sequence"],
        "within_band": r["cii"]["trajectory"]["within_external_band"],
        "uncleaned_30um_pct_per_year":
            r["cii"]["sensitivity_scenarios"]["uncleaned_30um_per_year"]["degradation_pct_per_year"],
    } for r in case_results]
    criteria["E5"] = {
        "criterion": "年度 CII 退化率落在行业统计带 0.5–1.5 %/年",
        "rows": deg_rows,
        "value_pct_per_year": [row["degradation_pct_per_year"] for row in deg_rows],
        "threshold_pct_per_year": list(CRITERIA["E5_degradation_band_pct_per_year"]),
        "passed": all(row["within_band"] for row in deg_rows),
        "metric": "mean attained-CII growth per year (hull roughness channel only)",
        "evidence_grade": gn.EVIDENCE_WITHHELD,
        "note": "只含船体粗糙度通道；统计带含螺旋桨污损/主机退化，故带内偏下限属预期。",
        "artifact": "cases[*].cii.trajectory.degradation_pct_per_year",
    }

    criteria["E6"] = {
        "criterion": "负控：P_engine = 0 → EEDI = 0",
        "value": neg["p_zero_eedi"],
        "p_ae_kw": neg["p_zero_p_ae_kw"],
        "passed": neg["p_zero_eedi"] == 0.0 and neg["p_zero_p_ae_kw"] == 0.0,
        "metric": "attained EEDI at zero power",
        "evidence_grade": gn.EVIDENCE_REAL,
        "artifact": "negative_controls.p_zero_eedi",
    }
    criteria["E7"] = {
        "criterion": "负控：V_ref → ∞ → EEDI → 0",
        "value": neg["v_inf_eedi"],
        "rel_to_baseline": neg["v_inf_rel_to_baseline"],
        "passed": neg["v_inf_eedi"] > 0.0 and neg["v_inf_rel_to_baseline"] < 1e-6,
        "metric": "attained EEDI at V_ref=1e9 kn, relative to baseline",
        "evidence_grade": gn.EVIDENCE_REAL,
        "artifact": "negative_controls.v_inf_eedi",
    }

    e8_rows = []
    for r in case_results:
        for phase, v in r["eedi"]["verdicts"].items():
            e8_rows.append({
                "case_id": r["case_id"], "phase": phase,
                "attained": v["attained_g_per_t_nm"],
                "required": v["required_g_per_t_nm"],
                "compliant": v["compliant"],
                "consistent": v["compliant"] == (v["attained_g_per_t_nm"] <= v["required_g_per_t_nm"]),
            })
    # 判据逻辑本身的双侧检验：取实测 required 构造 attained<required 与 >required 两种输入
    logic_pairs = []
    for r in case_results:
        v = r["eedi"]["verdicts"]["phase2"]
        for factor, expect in ((0.90, True), (1.10, False)):
            pair = eedi_mod.evaluate_eedi(
                r["ship_type"], v["required_g_per_t_nm"] * factor,
                v["capacity_t"] if "capacity_t" in v else
                r["eedi"]["capacity"]["capacity_t"], phase="phase2")
            logic_pairs.append({
                "case_id": r["case_id"], "attained_factor": factor,
                "attained": pair["attained_g_per_t_nm"],
                "required": pair["required_g_per_t_nm"],
                "compliant": pair["compliant"], "expected": expect,
                "ok": pair["compliant"] is expect,
            })
    criteria["E8"] = {
        "criterion": "EEDI_attained < EEDI_required 时 compliant == true，反之为 false（双侧逻辑）",
        "rows_cases": e8_rows,
        "rows_logic_pairs": logic_pairs,
        "passed": all(row["consistent"] for row in e8_rows)
                  and all(p["ok"] for p in logic_pairs),
        "metric": "flag/inequality consistency (measured pairs + constructed two-sided pairs)",
        "evidence_grade": gn.EVIDENCE_REAL,
        "note": ("实测对的 compliant 全为 false（三算例均不满足转录参考线 phase2），故另构造 "
                 "attained=0.9/1.1×required 的双侧输入验证 flag 逻辑本身，避免'恒真/恒假'式通过。"),
        "artifact": "cases[*].eedi.verdicts + acceptance.criteria.E8.rows_logic_pairs",
    }

    passed_all = all(c.get("passed") is True for c in criteria.values())
    return {
        "criteria": criteria,
        "passed_count": sum(1 for c in criteria.values() if c.get("passed") is True),
        "withheld_count": sum(1 for c in criteria.values() if c.get("passed") is None),
        "total": len(criteria),
        "all_passed": passed_all,
        "honest_fail_or_withheld": sorted(
            k for k, c in criteria.items() if c.get("passed") is not True),
    }


def _machine_acceptance(acceptance: Dict[str, Any]) -> Dict[str, Any]:
    """把 E1..E8 映射成任务书里的机器判据名（名称/数值/单位/通过态一一对应）。"""
    c = acceptance["criteria"]
    return {
        "SFOCvsWartsila < 5%[相对误差]": {
            "value_pct": c["E1"]["value_pct"],
            "judged_window": "P/P_MCR 0.75-0.90",
            "threshold_pct": c["E1"]["threshold_pct"],
            "passed": c["E1"]["passed"],
            "artifact": c["E1"]["artifact"],
        },
        "P_engine/P_MCR in 0.85±0.05[比值]": {
            "values": {row["case_id"]: row["power_fraction"] for row in c["E2"]["rows"]},
            "judged_cases": c["E2"]["independent_cases"],
            "threshold": c["E2"]["threshold"],
            "passed": c["E2"]["passed"],
            "artifact": c["E2"]["artifact"],
        },
        "EEDIvsIMO < 10%[相对误差]": {
            "values_pct": {row["case_id"]: row["rel_err_pct"] for row in c["E3"]["rows"]},
            "judged_cases": c["E3"]["judged_cases"],
            "threshold_pct": c["E3"]["threshold_pct"],
            "passed": c["E3"]["passed"],
            "evidence_grade": c["E3"]["evidence_grade"],
            "artifact": c["E3"]["artifact"],
        },
        "CII评级vsDNV == 一致[一致]": {
            "values": c["E4"]["value"],
            "status": c["E4"]["status"],
            "passed": c["E4"]["passed"],
            "reason": c["E4"]["reason"],
            "artifact": c["E4"]["artifact"],
        },
        "P_engine=0时EEDI=0 == true[布尔]": {
            "value": c["E6"]["value"],
            "passed": c["E6"]["passed"],
            "artifact": c["E6"]["artifact"],
        },
        "EEDI<EEDI_req时compliant == true[布尔]": {
            "two_sided_pairs_ok": all(p["ok"] for p in c["E8"]["rows_logic_pairs"]),
            "measured_pairs_ok": all(r["consistent"] for r in c["E8"]["rows_cases"]),
            "passed": c["E8"]["passed"],
            "artifact": c["E8"]["artifact"],
        },
        "summary": {
            "passed": acceptance["passed_count"],
            "total": acceptance["total"],
            "withheld": acceptance["honest_fail_or_withheld"],
            "all_passed": acceptance["all_passed"],
        },
    }


def run_all(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """跑全部算例 + 判据，返回可落盘的结果字典。"""
    sfoc = calibrate_sfoc(data_dir)
    anchors = load_anchors(data_dir)
    cases = load_cases(data_dir)
    results = [evaluate_case(c, sfoc, anchors, data_dir) for c in cases]
    neg = negative_controls(sfoc)
    acceptance = evaluate_acceptance(results, sfoc, neg)
    machine = _machine_acceptance(acceptance)
    return {
        "schema": "green_navigation.results.v1",
        "module": "backend/green_navigation",
        "evidence_layers": {
            "carbon_factor": gn.evidence_summary()["layers"][0],
            "attained_eedi_cii": "REAL (energy→fuel→CO2 chain + matched V_ref)",
            "required_lines": "WITHHELD (transcribed MEPC coefficients)",
            "cii_rating_boundaries": "WITHHELD_PROVENANCE (MEPC.354(78) G4 dd vectors)",
            "sfoc_reference_table": "WITHHELD_PROVENANCE (maker ±5% tolerance)",
            "resistance": "REAL* (Holtrop-Mennen regression, ±5-10% per original paper)",
        },
        "sfoc": {
            "model": sfoc["model_dict"],
            "reference_provenance": sfoc["reference_provenance"],
            "validation": sfoc["validation"],
        },
        "cases": results,
        "negative_controls": neg,
        "acceptance": acceptance,
        "machine_acceptance": machine,
    }