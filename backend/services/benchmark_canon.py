"""船舶阻力基准参考数据的**正典引擎**（mission m1561：六分支裁决）。

背景
----
同一个基准数据集被六张成熟分支各自独立重推导，产生了互相矛盾的值：

    分支      主张
    m1395     Rt 按淡水 ρ=998.2 派生（SUBOFF-01 → 92.52 N）+ geometry 富化字段
    m1396     参考值语料 / verifier 体系（数值未改）
    m1397     参考值审计：Rt 按 ρ=1000 派生（SUBOFF-01 → 92.68 N）
    m1400     benchmark_integrity + resistance_core + hull_geometry
    m1402     dataset v1.0→v2.0 迁移 + 前后审计（Rt 列保留原位）
    m1404     几何 Cb/L/B/T 一致性审计（displacement 修正）
    基线 2.0  SUBOFF-01 = 95.0 N（ρ=1025 海水），并把 v1 的 12.5 N 存为 legacy

本模块不猜、不抄：每个有争议的量都按解析恒等式**程序化复算**，并给出密度/黏度
溯源。裁决规则（全部可机器判）：

R1 阻力恒等式  Rt = Ct·½ρV²S                          [容差 1 %]
R2 流体溯源    船模拖曳水池 = 淡水 (ρ=998.2, ν=1.004e-6 @20 °C)
               实船全尺度     = 海水 (ρ=1025,  ν=1.188e-6 @15 °C)
               平台物理核 ``services/ship_resistance.DEFAULT_WATER_DENSITY = 998.0``
               也是淡水，参考数据必须与之同介质，否则契约漂移
R3 Froude 闭合 Fr = V/√(gL)                            [容差 2 %]
R4 排水量闭合  Δ = Cb·L·B·T·ρ                         [容差 2 %]
R5 摩擦下限    Ct ≥ Cf_ITTC57(Re)                     （总阻力不可能低于平板摩擦）
R6 形体系数带  L/B、B/T、Cb、S/(L·B)、S/(πDL)、L/D

判定词汇（与平台证据分级一致）
------------------------------
``DERIVED``  可由恒等式唯一复算且全部闭合 → 可作判据（REAL 级参考值）
``WITHHELD`` 记录缺关键量 → 值不可复核，禁止参与 15 % 判决
``REJECTED`` 与恒等式冲突（密度/尺度张冠李戴）→ 值必须作废

产物
----
``build_canon()`` 生成唯一正典 ``backend/data/benchmark_reference.json`` (v3.0)；
``cli()`` 提供 ``--check`` / ``--write`` / ``--table``；三栏（基线 / m1395 / 裁决）
对照表由 ``competitor_table()`` 直接从 git 对象库读六张分支的数据集，零手抄。
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

G = 9.81

REPO_ROOT = Path(__file__).resolve().parents[2]
CANON_PATH = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"
#: 冻结的裁决输入（v2.0 记录值快照）。正典必须可从冻结输入确定性复算，
#: 不能从上一轮派生结果反推，否则无法 golden 比对。
SOURCES_PATH = REPO_ROOT / "backend" / "data" / "benchmark_reference_sources_v2.json"
CANON_DOC_DIR = REPO_ROOT / "docs" / "reports" / "m1561"

DERIVED = "DERIVED"
WITHHELD = "WITHHELD"
REJECTED = "REJECTED"

TOL_IDENTITY = 0.01
TOL_FROUDE = 0.02
TOL_DISPLACEMENT = 0.02

#: 流体溯源表。密度/黏度只允许经由这张表引用。
FLUIDS: Dict[str, Dict[str, Any]] = {
    "fresh_water_20c": {
        "rho": 998.2,
        "nu": 1.004e-06,
        "label": "淡水 20 °C（ITTC 7.5-02-01-01 物性表）——船模拖曳水池常规介质",
    },
    "sea_water_15c": {
        "rho": 1025.0,
        "nu": 1.188e-06,
        "label": "海水 15 °C（ITTC-2011 标准海水）——实船全尺度",
    },
}

BAND_S_OVER_PIDL = (0.50, 1.00)   # 回转体：湿面积 / (πDL)
BAND_S_OVER_LB = (1.00, 3.20)     # 排水型：湿面积 / (L·B)
BAND_LD = (5.0, 15.0)             # 回转体 L/D
BAND_LB = (3.5, 12.0)             # 排水型 L/B
BAND_CB = (0.40, 0.90)

COMPETING_BRANCHES: Tuple[str, ...] = (
    "agent/m1395", "agent/m1396", "agent/m1397",
    "agent/m1400", "agent/m1402", "agent/m1404",
)
BASELINE_LABEL = "integration/m13xx-wave(v2.0)"
CANON_VERSION = "3.0"
CANON_SUPERSEDES = "2.0"


# ---------------------------------------------------------------------------
# 物理原语（纯解析，无拟合、无随机）
# ---------------------------------------------------------------------------

def ittc57_cf(reynolds: float) -> float:
    if reynolds <= 0:
        return float("nan")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def reynolds_number(speed: float, length: float, nu: float) -> float:
    return speed * length / nu


def derive_rt(ct: float, speed: float, wetted_surface_m2: float, rho: float) -> float:
    """R1: Rt = Ct·½ρV²S [N]。"""
    return ct * 0.5 * rho * speed ** 2 * wetted_surface_m2


def implied_wetted_surface(ct: float, speed: float, rt_n: float, rho: float) -> float:
    """R1 反解：由记录 Rt 反推表观湿面积 S_imp [m²]。"""
    return rt_n / (ct * 0.5 * rho * speed ** 2)


def derive_froude(speed: float, length: float) -> float:
    """R3: Fr = V/√(gL)。"""
    return speed / math.sqrt(G * length)


def implied_length(speed: float, froude: float) -> float:
    """R3 反解：(V/Fr)²/g —— 用来证明 Fr 列是拿哪个船长算出来的。"""
    return (speed / froude) ** 2 / G


def rel_error(value: float, reference: float) -> float:
    if reference == 0:
        return float("inf")
    return abs(value - reference) / abs(reference)


def in_band(value: float, band: Tuple[float, float]) -> bool:
    return band[0] <= value <= band[1]


# ---------------------------------------------------------------------------
# 数据集访问
# ---------------------------------------------------------------------------

def load_dataset(path: Any = CANON_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def case_map(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["case_id"]: c for c in doc.get("cases", [])}


def form_type_of(case: Dict[str, Any]) -> str:
    if case.get("form_type"):
        return case["form_type"]
    return "body_of_revolution" if case.get("ship_type") == "SUBOFF" else "displacement_hull"


def fluid_for_case(case: Dict[str, Any]) -> Tuple[str, float, float]:
    """R2: 由介质/尺度决定 (key, rho, nu)，禁止在别处写裸密度。"""
    key = case.get("reference_fluid")
    if key is None:
        key = "sea_water_15c" if case.get("scale") == "full" else "fresh_water_20c"
    fluid = FLUIDS[key]
    return key, float(fluid["rho"]), float(fluid["nu"])


def load_branch_dataset(branch: str,
                        rel_path: str = "backend/data/benchmark_reference.json"
                        ) -> Optional[Dict[str, Any]]:
    """从 git 对象库直接读某分支的数据集（零手抄）。读不到返回 None。"""
    try:
        out = subprocess.run(["git", "show", f"{branch}:{rel_path}"],
                             cwd=str(REPO_ROOT), capture_output=True, check=True).stdout
        return json.loads(out.decode("utf-8"))
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return None


def load_competitors() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{label: {case_id: case}} —— 基线 + 六张分支（读不到的分支自动跳过）。"""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if SOURCES_PATH.exists():
        out[BASELINE_LABEL] = case_map(load_baseline_doc())
    for branch in COMPETING_BRANCHES:
        doc = load_branch_dataset(branch)
        if doc:
            out[branch] = case_map(doc)
    return out


# ---------------------------------------------------------------------------
# 逐值裁决
# ---------------------------------------------------------------------------

def _row(quantity: str, baseline: Any, competitor: Any, canon: Any,
         verdict: str, basis: str, **extra: Any) -> Dict[str, Any]:
    return {"quantity": quantity, "baseline_v2": baseline, "m1395": competitor,
            "canon": canon, "verdict": verdict, "basis": basis, **extra}


def adjudicate_case(case: Dict[str, Any],
                    competitors: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """对一条参考行逐量裁决；competitors={label: case} 时给出三栏对照。"""
    competitors = competitors or {}
    cid = case["case_id"]
    base = competitors.get(BASELINE_LABEL, {}).get(cid, {})
    m1395 = competitors.get("agent/m1395", {}).get(cid, {})
    geo = dict(case.get("geometry") or {})
    form = form_type_of(case)
    length = geo.get("length_m")
    beam = geo.get("beam_m") or geo.get("diameter_m")
    diameter = geo.get("diameter_m")
    draft = geo.get("draft_m") or geo.get("diameter_m")
    cb = geo.get("cb")
    wetted = geo.get("wetted_surface_m2")
    fluid_key, rho, nu = fluid_for_case(case)
    speed = case.get("speed_ms")
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")

    rows: List[Dict[str, Any]] = []
    flags: List[str] = []

    # --- R1 阻力恒等式 / 反解 ---------------------------------------------
    if rt is not None and ct and speed and wetted:
        derived_rt = derive_rt(ct, speed, wetted, rho)
        err = rel_error(derived_rt, rt)
        verdict = DERIVED if err <= TOL_IDENTITY else REJECTED
        rows.append(_row("experimental_rt_n", base.get("experimental_rt_n"),
                         m1395.get("experimental_rt_n"), round(derived_rt, 3), verdict,
                         f"R1 Rt=Ct·½ρV²S：{ct:g}·0.5·{rho:g}·{speed:g}²·{wetted:g} = "
                         f"{derived_rt:.3f} N vs 记录 {rt:g} N（误差 {err*100:.2f}%，容差 "
                         f"{TOL_IDENTITY*100:.0f}%）；ρ 取自 {fluid_key}",
                         error_pct=round(err * 100, 4), rho=rho))
        if verdict == REJECTED:
            flags.append("resistance_identity_fail")
    elif rt is not None and ct and speed and not wetted:
        s_imp = implied_wetted_surface(ct, speed, rt, rho)
        if form == "body_of_revolution" and diameter and length:
            ratio, band, ref = s_imp / (math.pi * diameter * length), BAND_S_OVER_PIDL, "S_imp/(πDL)"
        elif length and beam:
            ratio, band, ref = s_imp / (length * beam), BAND_S_OVER_LB, "S_imp/(L·B)"
        else:
            ratio, band, ref = float("nan"), (0.0, 0.0), "n/a"
        inside = in_band(ratio, band)
        rows.append(_row("experimental_rt_n", base.get("experimental_rt_n"),
                         m1395.get("experimental_rt_n"), None,
                         WITHHELD if inside else REJECTED,
                         f"R1 反解 S_imp={s_imp:.2f} m²，{ref}={ratio:.3f}，合理带 {list(band)} → "
                         f"{'可复核' if inside else '越界，Rt 列不可复核（记录缺湿面积）'}；ρ={rho:g}（{fluid_key}）",
                         implied_wetted_surface_m2=round(s_imp, 3),
                         dimensionless_ratio=round(ratio, 3), band=list(band), rho=rho))
        if not inside:
            flags.append("resistance_not_reviewable")
    else:
        rows.append(_row("experimental_rt_n", base.get("experimental_rt_n"),
                         m1395.get("experimental_rt_n"), None, WITHHELD,
                         "记录缺 Rt / Ct / 湿面积之一，R1 不可运行"))

    # --- R1' 密度选择本身（SUBOFF 争议的根）--------------------------------
    if rt is not None and ct and speed and wetted:
        alt_key = "sea_water_15c" if fluid_key != "sea_water_15c" else "fresh_water_20c"
        alt_rho = FLUIDS[alt_key]["rho"]
        alt_rt = derive_rt(ct, speed, wetted, alt_rho)
        canon_rt = derive_rt(ct, speed, wetted, rho)
        rows.append(_row("reference_density_kg_m3", base.get("reference_density_kg_m3"),
                         m1395.get("reference_density_kg_m3"), rho, DERIVED,
                         f"R2 介质溯源：本行是{'实船（海水）' if case.get('scale') == 'full' else '模型（拖曳水池=淡水）'} → "
                         f"ρ={rho:g}；若误用 ρ={alt_rho:g} 得 Rt={alt_rt:.3f} N（偏差 "
                         f"{rel_error(alt_rt, canon_rt)*100:.2f}%）",
                         rejected_alternative_rho=alt_rho,
                         rejected_alternative_rt_n=round(alt_rt, 3)))

    # --- R3 Froude 闭合 ----------------------------------------------------
    fr = case.get("froude_number")
    if speed and length and fr:
        fr_calc = derive_froude(speed, length)
        err = rel_error(fr_calc, fr)
        ok = err <= TOL_FROUDE
        rows.append(_row("froude_number", base.get("froude_number"), m1395.get("froude_number"),
                         round(fr, 4), DERIVED if ok else WITHHELD,
                         f"R3 Fr=V/√(gL)：V={speed:g}，L={length:g} → Fr={fr_calc:.4f} vs 记录 {fr:.4f}"
                         f"（误差 {err*100:.2f}%）；反解 (V/Fr)²/g = {implied_length(speed, fr):.1f} m "
                         f"{'与 L 一致' if ok else '与 L 不符 → Fr 与 V 不可能同时是本行的试验条件'}",
                         computed=round(fr_calc, 4), error_pct=round(err * 100, 2),
                         implied_length_m=round(implied_length(speed, fr), 1)))
        if not ok:
            flags.append("froude_closure_fail")

    # --- R4 排水量闭合 -----------------------------------------------------
    disp = geo.get("displacement_t")
    if disp is None and geo.get("displacement_kg") is not None:
        disp = geo["displacement_kg"] / 1000.0
    if form != "body_of_revolution" and all([length, beam, draft, cb, disp]):
        derived_disp = cb * length * beam * draft * rho / 1000.0
        err = rel_error(derived_disp, disp)
        ok = err <= TOL_DISPLACEMENT
        rows.append(_row("displacement_t", disp, None, round(derived_disp, 3),
                         DERIVED if ok else REJECTED,
                         f"R4 Δ=Cb·L·B·T·ρ={cb:g}·{length:g}·{beam:g}·{draft:g}·{rho:g}="
                         f"{derived_disp:.3f} t vs 记录 {disp:g} t（误差 {err*100:.2f}%，容差 "
                         f"{TOL_DISPLACEMENT*100:.0f}%）", error_pct=round(err * 100, 2), rho=rho))
        if not ok:
            flags.append("displacement_closure_fail")

    # --- R5 摩擦下限 -------------------------------------------------------
    if ct and speed and length:
        re = reynolds_number(speed, length, nu)
        cf = ittc57_cf(re)
        ok = ct >= cf
        one_plus_k = ct / cf
        note = ""
        if ok and form == "body_of_revolution" and one_plus_k > 1.5:
            note = "（(1+k)>1.5，对回转体偏高，标 form_factor_high）"
            flags.append("form_factor_high")
        rows.append(_row("experimental_ct", base.get("experimental_ct"), m1395.get("experimental_ct"),
                         ct, DERIVED if ok else REJECTED,
                         f"R5 Re={re:.3e}（ν={nu:g}），Cf_ITTC57={cf:.6f}，Ct={ct:g} → "
                         f"(1+k)=Ct/Cf={one_plus_k:.2f} "
                         f"{'≥1 物理可行' if ok else '<1 物理不可能'}{note}",
                         reynolds=round(re, 1), cf=round(cf, 6), one_plus_k=round(one_plus_k, 3)))
        if not ok:
            flags.append("friction_floor_fail")

    # --- R6 形体带 ---------------------------------------------------------
    if length and beam and draft:
        ratios: Dict[str, Tuple[float, Tuple[float, float]]] = {}
        if form == "body_of_revolution" and diameter:
            ratios["L/D"] = (length / diameter, BAND_LD)
        else:
            ratios["L/B"] = (length / beam, BAND_LB)
            if cb is not None:
                ratios["Cb"] = (cb, BAND_CB)
        if wetted:
            if form == "body_of_revolution" and diameter:
                ratios["S/(πDL)"] = (wetted / (math.pi * diameter * length), BAND_S_OVER_PIDL)
            else:
                ratios["S/(L·B)"] = (wetted / (length * beam), BAND_S_OVER_LB)
        bad = [k for k, (v, b) in ratios.items() if not in_band(v, b)]
        rows.append(_row("form_bands", None, None,
                         {k: round(v, 4) for k, (v, _b) in ratios.items()},
                         DERIVED if not bad else REJECTED,
                         "R6 无量纲带：" + "；".join(f"{k}={v:.4f}∈{list(b)}"
                                                   for k, (v, b) in ratios.items())
                         + ("" if not bad else f" → 越界 {bad}")))
        if bad:
            flags.append("form_bands_fail")

    verdicts = [r["verdict"] for r in rows]
    overall = REJECTED if REJECTED in verdicts else (WITHHELD if WITHHELD in verdicts else DERIVED)
    return {"case_id": cid, "ship_type": case.get("ship_type"), "form_type": form,
            "fluid": fluid_key, "rho": rho, "nu": nu, "verdict": overall,
            "flags": sorted(set(flags)), "rows": rows}


def adjudicate_dataset(doc: Dict[str, Any],
                       competitors: Optional[Dict[str, Dict[str, Any]]] = None
                       ) -> Dict[str, Any]:
    competitors = competitors if competitors is not None else load_competitors()
    results = []
    for case in doc.get("cases", []):
        cand = {label: cases.get(case["case_id"], {}) for label, cases in competitors.items()}
        results.append(adjudicate_case(case, cand))
    summary = {"DERIVED": 0, "WITHHELD": 0, "REJECTED": 0}
    for r in results:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    return {"dataset_version": doc.get("version"), "summary": summary, "cases": results}


# ---------------------------------------------------------------------------
# 三栏对照表（基线 / m1395 / 裁决值）
# ---------------------------------------------------------------------------

def competitor_table(competitors: Optional[Dict[str, Dict[str, Any]]] = None
                     ) -> List[Dict[str, Any]]:
    """逐 case/逐量给出『基线值 / m1395 值 / 裁决值 + 依据』。"""
    competitors = competitors if competitors is not None else load_competitors()
    canon = build_canon()
    canon_map = case_map(canon)
    fields = ("experimental_rt_n", "froude_number", "speed_ms", "reference_density_kg_m3")
    table: List[Dict[str, Any]] = []
    base_cases = competitors.get(BASELINE_LABEL, {})
    for cid in sorted(canon_map):
        ccase = canon_map[cid]
        bcase = base_cases.get(cid, {})
        mcase = competitors.get("agent/m1395", {}).get(cid, {})
        others: Dict[str, Any] = {}
        for label, cases in competitors.items():
            if label in (BASELINE_LABEL, "agent/m1395"):
                continue
            row = cases.get(cid, {})
            others[label] = {f: row.get(f) for f in ("experimental_rt_n", "froude_number")}
        for field in fields:
            b_val = bcase.get(field)
            if field == "speed_ms" and bcase.get("legacy_speed_ms") is not None:
                b_val = bcase.get("legacy_speed_ms")
            m_val = mcase.get(field)
            c_val = ccase.get(field)
            basis = ""
            if field == "experimental_rt_n":
                basis = ccase.get("rt_basis", "")
            elif field == "froude_number":
                basis = ccase.get("froude_basis", "")
            elif field == "speed_ms":
                basis = ccase.get("speed_basis", "")
            else:
                basis = ccase.get("fluid_basis", "")
            same = _num_eq(b_val, c_val) and _num_eq(m_val, c_val)
            table.append({
                "case_id": cid,
                "quantity": field,
                "baseline_v2": b_val,
                "m1395": m_val,
                "canon": c_val,
                "other_branches": others,
                "agrees_with_canon": same,
                "basis": basis,
            })
    return table


def _num_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return a == b


# ---------------------------------------------------------------------------
# 正典构建
# ---------------------------------------------------------------------------

#: 逐算例裁决决定（理由落盘；数值一律由恒等式复算，不手抄）
DECISIONS: Dict[str, Dict[str, Any]] = {
    "SUBOFF-01": {
        "scale": "model", "fluid": "fresh_water_20c", "rt": "derive",
        "why": "DTRC 拖曳水池是淡水，模型 L=4.356 m；记录自带湿面积 S=5.42 m²，Rt 可由 R1 唯一复算。"
               "基线 v2 对模型试验行套用海水 1025（尺度/介质张冠李戴）→ 95.0 N 偏高 2.68%；"
               "v1 的 12.5 N 反解湿面积仅 0.71 m²，与 R1 差 7.6 倍，直接作废。两者均留 legacy。",
    },
    "SUBOFF-02": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},
    "SUBOFF-03": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},
    "SUBOFF-04": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},
    "DTMB5415-01": {
        "scale": "model", "fluid": "fresh_water_20c", "rt": "withhold", "speed_from": "froude",
        "why": "记录缺湿面积 → Rt 列不可复核；且航速列 5/10/20/30 kn 是实船服务航速，与模型主尺度 "
               "L=5.72 m 搭配时 Fr 达 0.34–2.06，物理不可能；Fr 列的隐含船长是 181 m，也不是 5.72 m。"
               "航速与 Fr 两列互不自洽且都无法溯源到 ITTC-2010 试验矩阵 → 整列 WITHHELD。",
    },
    "DTMB5415-02": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",
                    "speed_from": "froude", "why": "同 DTMB5415-01。"},
    "DTMB5415-03": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",
                    "speed_from": "froude", "why": "同 DTMB5415-01。"},
    "DTMB5415-04": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",
                    "speed_from": "froude", "why": "同 DTMB5415-01。"},
    "KVLCC2-01": {
        "scale": "full", "fluid": "sea_water_15c", "rt": "withhold",
        "why": "实船全尺度（L=320 m，海水密度适用）；记录缺湿面积，由记录 Rt 反解的表观湿面积 "
               "S/(L·B)≈0.40–0.55 远低于物理下限 1.0 → Rt 列不可复核，须回 SIMMAN 原始表取湿面积。",
    },
    "KVLCC2-02": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},
    "KVLCC2-03": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},
    "KVLCC2-04": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},
}


def _filed_view(case: Dict[str, Any]) -> Dict[str, Any]:
    """把一行规范化成『记录值』视图（兼容 v2.0 顶层 legacy_* 与 v3.0 legacy_values）。

    正典必须幂等：``build_canon(canon_v3) == canon_v3``，所以裁决只吃
    “原始记录值”，不被上一轮的派生值反向污染。
    """
    row = dict(case)
    geo = dict(case.get("geometry") or {})
    legacy = dict(case.get("legacy_values") or {})

    def pick(*cands: Any) -> Any:
        for c in cands:
            if c is not None:
                return c
        return None

    row["speed_ms"] = pick(legacy.get("filed_speed_ms"), case.get("legacy_speed_ms"),
                           case.get("speed_ms"))
    row["froude_number"] = pick(legacy.get("filed_froude_number"),
                               case.get("legacy_froude_number"), case.get("froude_number"))
    row["experimental_rt_n"] = pick(legacy.get("filed_experimental_rt_n"),
                                    legacy.get("superseded_experimental_rt_n"),
                                    case.get("experimental_rt_n"),
                                    case.get("legacy_experimental_rt_n"))
    for key in ("displacement_t", "displacement_kg"):
        if geo.get("legacy_" + key) is not None:
            geo[key] = geo["legacy_" + key]
    row["geometry"] = geo
    return row


def v1_reference_rt(case: Dict[str, Any]) -> Any:
    """v1 记录值（12.5 N 那批），无论当前文档是 v2 还是 v3 布局。"""
    legacy = case.get("legacy_values") or {}
    if "v1_experimental_rt_n" in legacy:
        return legacy["v1_experimental_rt_n"]
    return case.get("legacy_experimental_rt_n")


def load_baseline_doc(path: Any = SOURCES_PATH) -> Dict[str, Any]:
    """冻结的 v2.0 输入快照（= 裁决前基线）。"""
    return load_dataset(path)


def sources_from_baseline(path: Any = SOURCES_PATH) -> Dict[str, Dict[str, Any]]:
    """裁决输入 = 记录的 filed 值视图（不依赖上一轮派生结果，保证幂等）。"""
    doc = load_dataset(path)
    return {c["case_id"]: _filed_view(c) for c in doc["cases"]}


def build_canon(sources: Optional[Dict[str, Dict[str, Any]]] = None,
                baseline_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成唯一正典（v3.0）。所有数值由恒等式复算，文档里不出现手抄常量。"""
    sources = sources if sources is not None else sources_from_baseline()
    baseline_cases = case_map(baseline_doc) if baseline_doc else case_map(load_baseline_doc())

    cases_out: List[Dict[str, Any]] = []
    for case_id in sorted(sources):
        src = sources[case_id]
        dec = DECISIONS[case_id]
        geo = dict(src.get("geometry") or {})
        geo.pop("rt_note", None)        # v2 的旧叙述字段，已由 rt_basis 取代，避免两套口径并存
        form = form_type_of(src)
        fluid_key = dec["fluid"]
        rho = FLUIDS[fluid_key]["rho"]
        nu = FLUIDS[fluid_key]["nu"]
        length = geo.get("length_m")
        beam = geo.get("beam_m") or geo.get("diameter_m")
        draft = geo.get("draft_m") or geo.get("diameter_m")
        cb = geo.get("cb")
        ct = float(src["experimental_ct"])
        flags: List[str] = []
        legacy: Dict[str, Any] = {}

        # --- 航速 / Fr ---
        filed_speed = float(src.get("speed_ms"))
        filed_fr = src.get("froude_number")
        if dec.get("speed_from") == "froude" and filed_fr:
            fr = float(filed_fr)
            speed = fr * math.sqrt(G * length)
            speed_basis = (f"条件值：由记录 Fr={fr:g} 与 L={length:g} m 反算 V=Fr√(gL)={speed:.4f} m/s；"
                           f"原始航速 {filed_speed:g} m/s 是实船服务航速，与模型尺度不自洽，移入 legacy")
            froude_status = WITHHELD
            froude_basis = (f"R3 反解：记录 Fr 的隐含船长 (V_filed/Fr)²/g = "
                            f"{implied_length(filed_speed, fr):.1f} m ≠ L={length:g} m → Fr 列不是本几何的"
                            f"试验条件；Fr 与航速对同一行不可能同时成立")
            flags.append("froude_provenance_withheld")
            legacy["filed_speed_ms"] = filed_speed
            legacy["filed_froude_number"] = filed_fr
        else:
            speed = filed_speed
            fr = derive_froude(speed, length)
            err = rel_error(fr, float(filed_fr)) if filed_fr else 0.0
            if filed_fr and err > TOL_FROUDE:
                froude_status = REJECTED
                fr = float(filed_fr)
                flags.append("froude_closure_fail")
            else:
                froude_status = DERIVED
            speed_basis = "记录航速；与 L 的 Fr 闭合通过 R3"
            froude_basis = (f"R3 复算 Fr=V/√(gL)={fr:.4f}；记录 {filed_fr:g}"
                            f"（误差 {err*100:.2f}%，容差 {TOL_FROUDE*100:.0f}%）")
            if filed_fr is not None and abs(float(filed_fr) - fr) > 1e-6:
                legacy["filed_froude_number"] = filed_fr

        # --- 阻力 ---
        wetted = geo.get("wetted_surface_m2")
        filed_rt = src.get("experimental_rt_n")
        if dec["rt"] == "derive":
            if not wetted:
                raise ValueError(f"{case_id}: 裁决要求派生 Rt，但记录缺湿面积 S")
            rt = round(derive_rt(ct, speed, wetted, rho), 2)
            rt_status = DERIVED
            rt_basis = (f"R1 复算 Rt=Ct·½ρV²S = {ct:g}·0.5·{rho:g}·{speed:g}²·{wetted:g} = {rt:.2f} N；"
                        f"ρ 取 {fluid_key}（{FLUIDS[fluid_key]['label']}）")
            if filed_rt is not None and rel_error(rt, float(filed_rt)) > TOL_IDENTITY:
                legacy["superseded_experimental_rt_n"] = filed_rt
        else:
            rt = None
            rt_status = WITHHELD
            if filed_rt:
                s_imp = implied_wetted_surface(ct, speed, float(filed_rt), rho)
                ratio = s_imp / (length * beam) if (length and beam) else float("nan")
                rt_basis = (f"R1 反解：由记录 Rt={float(filed_rt):g} N 得 S_imp={s_imp:.2f} m²，"
                            f"S_imp/(L·B)={ratio:.3f} 落在物理带 {list(BAND_S_OVER_LB)} 之外 → "
                            f"Rt 与 Ct 不能同时成立；ρ={rho:g}（{fluid_key}）")
                legacy["filed_experimental_rt_n"] = filed_rt
            else:
                rt_basis = "记录缺 Rt 与湿面积，R1 不可运行"
            flags.append("resistance_not_reviewable")

        # --- 排水量 ---
        disp_t = geo.pop("displacement_t", None)
        disp_kg = geo.pop("displacement_kg", None)
        if form != "body_of_revolution" and all([length, beam, draft, cb]) and (disp_t or disp_kg):
            filed_disp_t = float(disp_t) if disp_t else float(disp_kg) / 1000.0
            derived_t = cb * length * beam * draft * rho / 1000.0
            err = rel_error(derived_t, filed_disp_t)
            if err > TOL_DISPLACEMENT:
                flags.append("displacement_closure_fail")
                geo["legacy_" + ("displacement_t" if disp_t else "displacement_kg")] = (
                    filed_disp_t if disp_t else filed_disp_t * 1000.0)
            geo[("displacement_t" if disp_t else "displacement_kg")] = round(
                derived_t if disp_t else derived_t * 1000.0, 1)
            unit = "t" if disp_t else "kg"
            scale_factor = 1.0 if disp_t else 1000.0
            geo["displacement_basis"] = (
                f"R4 派生 Δ=Cb·L·B·T·ρ（ρ={rho:g}）= {derived_t * scale_factor:.1f} {unit}；"
                f"v1/v2 记录 {filed_disp_t * scale_factor:.1f} {unit} 误差 {err*100:.1f}%"
                + ("，已移入 legacy" if err > TOL_DISPLACEMENT else ""))
        elif disp_kg is not None:
            geo["displacement_kg"] = disp_kg
        elif disp_t is not None:
            geo["displacement_t"] = disp_t

        overall = WITHHELD if (rt_status == WITHHELD or froude_status == WITHHELD or flags) else DERIVED
        row: Dict[str, Any] = {
            "case_id": case_id,
            "ship_type": src.get("ship_type"),
            "form_type": form,
            "scale": dec["scale"],
            "reference_fluid": fluid_key,
            "reference_density_kg_m3": rho,
            "reference_kinematic_viscosity_m2_s": nu,
            "fluid_basis": FLUIDS[fluid_key]["label"],
        }
        if src.get("speed_kn") is not None:
            row["speed_kn"] = src["speed_kn"]
        row.update({
            "speed_ms": round(speed, 4),
            "speed_basis": speed_basis,
            "froude_number": round(fr, 4),
            "froude_status": froude_status,
            "froude_basis": froude_basis,
            "experimental_ct": ct,
            "experimental_rt_n": rt,
            "rt_status": rt_status,
            "rt_basis": rt_basis,
            "geometry": geo,
            "source_citation": src.get("source_citation"),
            "reference_status": overall,
            "audit_flags": sorted(set(flags)),
            "adjudication_reason": dec["why"],
        })
        v1 = v1_reference_rt(baseline_cases[case_id]) if case_id in baseline_cases else None
        if v1 is not None and (filed_rt is None or not _num_eq(v1, filed_rt)):
            legacy["v1_experimental_rt_n"] = v1
        if legacy:
            row["legacy_values"] = legacy
        cases_out.append(row)

    return {
        "description": (
            "船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、"
            "ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 "
            "backend/services/benchmark_canon.py 的解析恒等式复算，含密度/黏度溯源。"),
        "version": CANON_VERSION,
        "supersedes": CANON_SUPERSEDES,
        "changelog": (
            "3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 "
            "ρ=998.2 kg/m³、ν=1.004e-6 m²/s，实船（KVLCC2）海水 1025/1.188e-6；v2 把海水密度用在 "
            "DTRC 模型拖曳水池行上，SUBOFF-01 的 95.0 N 因此偏高 2.68%，正典为 92.52 N；v1 的 12.5 N "
            "与 R1 差 7.6 倍作废，两者均留 legacy_values。②DTMB-5415 的 Fr/航速互不自洽（Fr 列隐含"
            "船长 181 m），整列 WITHHELD，航速按 Fr 反算只作条件值。③排水量按 R4（Cb·L·B·T·ρ）派生，"
            "记录值移入 legacy。④Rt 为空的算例一律 rt_status=WITHHELD，禁止参与 15% 判决。"),
        "provenance": {
            "adjudication_engine": "backend/services/benchmark_canon.py",
            "filed_sources": "backend/data/benchmark_reference_sources_v2.json（冻结的 v2.0 记录值，裁决输入）",
            "gate_engine": "backend/services/benchmark_integrity.py",
            "adjudication_report": "docs/reports/m1561_benchmark_canon_adjudication.md",
            "superseded_branches": "docs/superseded/",
            "rules": ["R1 Rt=Ct·½ρV²S", "R2 流体溯源（模型=淡水/实船=海水）",
                      "R3 Fr=V/√(gL)", "R4 Δ=Cb·L·B·T·ρ", "R5 Ct≥Cf_ITTC57", "R6 形体带"],
        },
        "integrity_gate": {
            "module": "services/benchmark_integrity.py",
            "canon_engine": "services/benchmark_canon.py",
            "checks": ["froude_closure", "displacement_closure", "resistance_closure",
                       "friction_floor", "form_bands"],
            "policy": "仅 reference_status=DERIVED 的行可参与 15% 精度判决；WITHHELD 行如实标注、不出判决",
        },
        "cases": cases_out,
    }


def canon_json() -> str:
    """确定性序列化（生成物可直接进 git 做 golden 比对）。"""
    return json.dumps(build_canon(), indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# 门禁 / CLI
# ---------------------------------------------------------------------------

def check_canon(path: Any = CANON_PATH) -> Tuple[bool, List[str]]:
    """正典文件是否仍等于冻结输入的复算结果，且自身审计无 REJECTED。"""
    problems: List[str] = []
    if not Path(path).exists():
        return False, [f"正典文件不存在：{path}"]
    if not SOURCES_PATH.exists():
        return False, [f"冻结输入快照缺失：{SOURCES_PATH}"]
    have = json.loads(Path(path).read_text(encoding="utf-8"))
    want = build_canon()
    if have.get("version") != want["version"]:
        problems.append(f"版本号 {have.get('version')} != {want['version']}")
    have_cases, want_cases = case_map(have), case_map(want)
    if set(have_cases) != set(want_cases):
        problems.append(f"算例集合不一致：{sorted(set(have_cases) ^ set(want_cases))}")
    for cid in sorted(set(have_cases) & set(want_cases)):
        for field in ("experimental_rt_n", "froude_number", "speed_ms",
                      "reference_density_kg_m3", "reference_status", "rt_status"):
            h, w = have_cases[cid].get(field), want_cases[cid].get(field)
            if not _num_eq(h, w) and h != w:
                problems.append(f"{cid}.{field}: 文件 {h!r} != 复算 {w!r}")
        for field in ("reference_density_kg_m3", "experimental_rt_n"):
            geo = have_cases[cid].get("geometry") or {}
            for key in ("displacement_t", "displacement_kg"):
                if key in geo and geo[key] is None:
                    problems.append(f"{cid}.geometry.{key} 为 null")
    src_doc = load_baseline_doc()
    if src_doc.get("version") != CANON_SUPERSEDES:
        problems.append(f"冻结输入版本 {src_doc.get('version')} 应为 {CANON_SUPERSEDES}")
    audit = adjudicate_dataset(have, competitors={})
    for r in audit["cases"]:
        if r["verdict"] == REJECTED:
            problems.append(f"{r['case_id']} 自审 REJECTED：{r['flags']}")
    return not problems, problems


def _cli_write() -> int:
    CANON_PATH.write_text(canon_json(), encoding="utf-8")
    CANON_DOC_DIR.mkdir(parents=True, exist_ok=True)
    table = competitor_table()
    (CANON_DOC_DIR / "adjudication_table.json").write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    audit = adjudicate_dataset(load_dataset())
    (CANON_DOC_DIR / "canon_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {CANON_PATH} and {CANON_DOC_DIR}/*.json")
    return 0


def cli(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--write" in argv:
        return _cli_write()
    if "--table" in argv:
        print(json.dumps(competitor_table(), indent=2, ensure_ascii=False))
        return 0
    ok, problems = check_canon()
    if ok:
        print("canon OK: 文件与复算一致，自审无 REJECTED")
        return 0
    print("canon FAIL:")
    for p in problems:
        print("  -", p)
    return 1


if __name__ == "__main__":
    raise SystemExit(cli())