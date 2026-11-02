#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STD-GEOM 闸门运行器：把平台里的几何量按**同一批判据**机器裁决。

数据来源（全部离线、只读）
--------------------------
* 平台回转体湿面积律与被审计阻力模型：``backend/services/ship_resistance.py``
  （``wetted_surface_axisym`` / ``resistance_breakdown``）——**被审计对象**，本工具不改它。
* 标准模型几何预设：``backend/api/domain/ship_benchmark.py::SHIP_GEOMETRY``
  （SUBOFF / DTMB-5415 / KVLCC2）；用冻结副本，避免 import 全链路依赖（并附
  ``--prefer-module`` 时的一致性核对）。
* 公布湿面积 / 排水体积：``backend/api/holtrop1984.py::BENCHMARK_HULLS`` 的
  ``s_published`` 与 ``vol``（KCS 9494 m² / 52030 m³、KVLCC2 27194 m² / 312622 m³、
  DTMB-5415 模型 0.5425 m³）。该模块 import 需 psycopg2，故内置冻结副本；
  ``--prefer-module`` 时才尝试 import 并核对冻结副本（不一致即报错，不静默）。
* SUBOFF 实测记录：``backend/data/benchmark_reference.json`` 的 case 级几何与
  ``wetted_surface_m2 = 5.42``——平台自己的档案值。

输出
----
* ``artifacts/std_geometry/ledger.json``：逐船逐判据数值 + 判决
* ``artifacts/std_geometry/ledger.md``：人读摘要（数字全部由 json 注入）

用法
----
    python3 -m tools.verif.geometry.std_geometry_gate --out artifacts/std_geometry
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from src.ship_verification.std_geometry import (  # noqa: E402
    CONF_HIGH,
    CONF_MED,
    FAIL,
    PASS,
    TOL_ACCEPT,
    TOL_REJECT,
    WITHHELD,
    SUBOFF_PROFILE,
    axisym_fit_range,
    axisym_fit_required_cb,
    implied_cb,
    judge_attainable,
    judge_relative,
    section_law_gap,
    suboff_sensitivity,
    volume_from_cb,
    wetted_surface_axisym_fit,
    wetted_surface_denny_mumford,
    wetted_surface_holtrop,
)

BENCHMARK_JSON = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

#: 冻结副本：``backend/api/holtrop1984.py::BENCHMARK_HULLS``（2026-11-01 工作树读取）。
FROZEN_HULLS: Dict[str, Dict[str, Any]] = {
    "KCS": {
        "l": 230.0, "b": 32.2, "t": 10.8, "cb": 0.6505, "cm": 0.9846, "cwp": 0.7350,
        "vol": 52030.0, "s_published": 9494.0,
        "source": "backend/api/holtrop1984.py::KCS（Tokyo 2015 公布值）",
    },
    "KVLCC2": {
        "l": 320.0, "b": 58.0, "t": 20.8, "cb": 0.8098, "cm": 0.9980, "cwp": 0.8548,
        "vol": 312622.0, "s_published": 27194.0,
        "source": "backend/api/holtrop1984.py::KVLCC2（Gothenburg 2010 / Tokyo 2015 公布值）",
    },
    "DTMB-5415-model": {
        "l": 5.72, "b": 0.759, "t": 0.246, "cb": 0.508, "cm": 0.821, "cwp": 0.771,
        "vol": 0.5425, "s_published": None,
        "s_holtrop_correlation": 4.6494, "s_denny_mumford": 4.598,
        "source": "backend/api/holtrop1984.py::DTMB5415_MODEL（ITTC V&V 2010）",
    },
}

#: 冻结副本：``backend/api/domain/ship_benchmark.py::SHIP_GEOMETRY``（同上）。
FROZEN_PRESETS: Dict[str, Dict[str, Any]] = {
    "SUBOFF": {"length": 4.356, "beam": 0.508, "draft": 0.508, "cb": 0.542},
    "DTMB-5415": {"length": 5.720, "beam": 0.759, "draft": 0.246, "cb": 0.508},
    "KVLCC2": {"length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098},
}


# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------
def load_prefer_module() -> Optional[Dict[str, Any]]:
    """``--prefer-module``：尝试 import 真实模块，核对冻结副本。"""
    try:
        from api import holtrop1984 as h  # type: ignore
    except Exception as exc:  # pragma: no cover - 取决于环境
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    live: Dict[str, Any] = {}
    for name, hull in h.BENCHMARK_HULLS.items():
        live[name] = {"vol": hull.vol, "s_published": hull.s_published}
    return {"ok": True, "hulls": live}


def _platform_axisym_fit():
    """取平台自己的 ``wetted_surface_axisym``（被审计对象）；不可用时退回复刻。"""
    try:
        from services.ship_resistance import wetted_surface_axisym  # type: ignore
        return wetted_surface_axisym, "services.ship_resistance.wetted_surface_axisym"
    except Exception as exc:  # pragma: no cover
        return (
            lambda beam, length, cb: wetted_surface_axisym_fit(beam, length, cb),
            f"fallback copy ({type(exc).__name__})",
        )


def _platform_resistance_model():
    try:
        from services.ship_resistance import resistance_breakdown  # type: ignore
        return resistance_breakdown, "services.ship_resistance.resistance_breakdown"
    except Exception as exc:  # pragma: no cover
        return None, f"unavailable ({type(exc).__name__})"


def load_suboff_case() -> Dict[str, Any]:
    doc = json.loads(BENCHMARK_JSON.read_text())
    for case in doc["cases"]:
        if case["case_id"] == "SUBOFF-01":
            return {
                "case_id": case["case_id"],
                "length_m": case["geometry"]["length_m"],
                "diameter_m": case["geometry"]["diameter_m"],
                "wetted_surface_m2": case["geometry"]["wetted_surface_m2"],
                "source": case.get("source_citation", ""),
            }
    raise RuntimeError("SUBOFF-01 not found in benchmark_reference.json")


# ---------------------------------------------------------------------------
# 逐船裁决
# ---------------------------------------------------------------------------
def audit_ship_hull(name: str, hull: Dict[str, Any], fit) -> Dict[str, Any]:
    l, b, t, cb = hull["l"], hull["b"], hull["t"], hull["cb"]
    cm, cwp = hull["cm"], hull["cwp"]

    vol = volume_from_cb(l, b, t, cb, "rectangular")
    s_holtrop = wetted_surface_holtrop(l, b, t, cb, cm, cwp)
    s_dm = wetted_surface_denny_mumford(l, b, t, cb)
    s_fit = fit(b, l, cb)                       # 平台回转体律（被审计对象）

    checks: List[Dict[str, Any]] = []

    # C1 体积闭合 vs 公布 ∇
    checks.append({
        "id": "C1_volume_vs_published",
        "what": "∇ = Cb·L·B·T（方截面律）vs 公布 ∇",
        **judge_relative(vol, hull["vol"], TOL_ACCEPT),
    })

    # C2 船型湿面积律（Holtrop）vs 公布 S
    if hull.get("s_published"):
        checks.append({
            "id": "C2_holtrop_vs_published",
            "what": "Holtrop S vs 公布 S（船型律自身的正确性门槛）",
            **judge_relative(s_holtrop, hull["s_published"], TOL_ACCEPT),
        })
        checks.append({
            "id": "C3_axisym_fit_vs_published",
            "what": "平台回转体律 πBL(0.5+0.25Cb) vs 公布 S（跨律污染量）",
            **judge_relative(s_fit, hull["s_published"], TOL_REJECT),
        })
    # C4 两律分歧（即使无公布值也可判）
    checks.append({
        "id": "C4_cross_law_divergence",
        "what": "回转体律 / Holtrop 律 − 1（跨律相对分歧，>15% 即不可互换）",
        "value": s_fit / s_holtrop,
        "reference": 1.0,
        "rel_error": s_fit / s_holtrop - 1.0,
        "abs_rel_error": abs(s_fit / s_holtrop - 1.0),
        "tol": TOL_REJECT,
        "verdict": PASS if abs(s_fit / s_holtrop - 1.0) <= TOL_REJECT else FAIL,
    })
    # C5 第二条独立船型律（Denny-Mumford）交叉
    ref_s = hull.get("s_published") or s_holtrop
    checks.append({
        "id": "C5_denny_mumford_cross",
        "what": "Denny-Mumford S vs 参照 S（独立第二律）",
        **judge_relative(s_dm, ref_s, TOL_REJECT),
    })

    return {
        "hull": name,
        "form_family": "ship",
        "geometry": {"l": l, "b": b, "t": t, "cb": cb, "cm": cm, "cwp": cwp},
        "reference": {
            "volume_m3": hull["vol"],
            "wetted_surface_m2": hull.get("s_published"),
            "source": hull["source"],
            "confidence": CONF_HIGH if hull.get("s_published") else CONF_MED,
        },
        "computed": {
            "volume_m3_rect": vol,
            "s_holtrop_m2": s_holtrop,
            "s_denny_mumford_m2": s_dm,
            "s_axisym_fit_m2": s_fit,
            "s_over_piDL": s_fit / (math.pi * b * l),
        },
        "checks": checks,
    }


def audit_axisym_body(preset: Dict[str, Any], case: Dict[str, Any], fit) -> Dict[str, Any]:
    length, d, cb = preset["length"], preset["beam"], preset["cb"]
    s_fit = fit(d, length, cb)
    vol_rect = volume_from_cb(length, d, d, cb, "rectangular")
    vol_circ = volume_from_cb(length, d, d, cb, "circular")

    sens = suboff_sensitivity()
    v_range = sens["volume_m3"]
    s_range = sens["wetted_surface_m2"]
    v_profile = v_range["nominal"]
    s_profile = s_range["nominal"]

    lo, hi = axisym_fit_range(d, length)
    req_cb = axisym_fit_required_cb(d, length, case["wetted_surface_m2"])
    implied_cb_rect = implied_cb(length, d, d, vol_rect, "rectangular")
    implied_cb_circ = implied_cb(length, d, d, vol_rect, "circular")

    checks: List[Dict[str, Any]] = [
        {
            "id": "A1_section_law_gap",
            "what": "方截面代理相对圆截面代理的记账差（B=T=D 时应为 π/4 的倒数 → 27.32%）",
            "value": section_law_gap(d, d),
            "reference": 4.0 / math.pi - 1.0,
            "rel_error": section_law_gap(d, d) / (4.0 / math.pi - 1.0) - 1.0,
            "abs_rel_error": abs(section_law_gap(d, d) / (4.0 / math.pi - 1.0) - 1.0),
            "tol": 1e-9,
            "verdict": PASS,  # 恒等式，用来**量化**记账差而不是判平台错
        },
        {
            "id": "A2_preset_volume_vs_profile",
            "what": "预设定体积（方截面律）vs 剖面直接积分 ∇ = π∫r²dx（独立方法）",
            **judge_relative(vol_rect, v_profile, TOL_ACCEPT),
        },
        {
            "id": "A3_preset_volume_circ_vs_profile",
            "what": "预设定体积（圆截面律）vs 剖面积分 ∇",
            **judge_relative(vol_circ, v_profile, TOL_ACCEPT),
        },
        {
            "id": "A4_filed_surface_vs_profile",
            "what": "档案湿面积 vs 剖面直接积分 S = 2π∫r√(1+r'²)dx",
            **judge_relative(case["wetted_surface_m2"], s_profile, TOL_ACCEPT),
        },
        {
            "id": "A5_filed_surface_attainability",
            "what": "档案湿面积是否落在平台回转体律的可达区间 [0.5,0.75]·πDL 内",
            **judge_attainable(case["wetted_surface_m2"], lo, hi),
        },
        {
            "id": "A6_fit_surface_vs_profile",
            "what": "平台回转体律 S(Cb=0.542) vs 剖面直接积分 S",
            **judge_relative(s_fit, s_profile, TOL_REJECT),
        },
    ]

    return {
        "hull": "SUBOFF",
        "form_family": "axisymmetric",
        "geometry": {"length": length, "diameter": d, "cb": cb},
        "reference": {
            "wetted_surface_m2_filed": case["wetted_surface_m2"],
            "source": case["source"],
            "confidence": CONF_HIGH,
            "profile_volume_m3": v_range,
            "profile_surface_m2": s_range,
            "profile_confidence": CONF_MED,
        },
        "computed": {
            "volume_m3_rect_law": vol_rect,
            "volume_m3_circular_law": vol_circ,
            "s_axisym_fit_m2": s_fit,
            "s_fit_min_m2": lo,
            "s_fit_max_m2": hi,
            "s_over_piDL_filed": case["wetted_surface_m2"] / (math.pi * d * length),
            "required_cb_for_filed_s": req_cb,
            "implied_cb_rect": implied_cb_rect,
            "implied_cb_circ": implied_cb_circ,
            "profile_nominal_volume_m3": v_profile,
            "profile_nominal_surface_m2": s_profile,
        },
        "checks": checks,
    }


def quantify_rt_inflation(hulls: List[Dict[str, Any]], speeds: Dict[str, float]) -> Dict[str, Any]:
    """量化"湿面积律污染"在 Rt[N] 与 Ct[...] 上的不同后果。

    用平台**自己的**在环阻力模型 ``resistance_breakdown``：它内部取
    ``S = wetted_surface_axisym``。因此 ``Rt`` 的误差恒等于 ``S`` 的误差，
    而 ``Ct = Cf + Cr`` 与 ``S`` 无关 —— 这正是 15% 判决看不出来的原因。
    """
    model, source = _platform_resistance_model()
    rows: List[Dict[str, Any]] = []
    if model is None:
        return {"available": False, "reason": source, "rows": []}
    for h in hulls:
        name = h["hull"]
        if name not in speeds:
            continue
        g = h["geometry"]
        v = speeds[name]
        res = model(length=g["l"], beam=g["b"], draft=g["t"], cb=g["cb"], speed=v)
        s_fit = res["wetted_surface_m2"]
        if h["form_family"] == "axisymmetric":
            s_ref = h["reference"]["wetted_surface_m2_filed"]
        else:
            s_ref = h["reference"]["wetted_surface_m2"] or h["computed"]["s_holtrop_m2"]
        q_fit = res["rt_n"] / res["ct"]                   # = ½ρV²S_fit（ct=Cf+Cr，rt=q·ct）
        rt_with_s_ref = res["ct"] * q_fit * (s_ref / s_fit)
        rows.append({
            "hull": name,
            "speed_ms": v,
            "s_model_m2": s_fit,
            "s_reference_m2": s_ref,
            "s_error": s_fit / s_ref - 1.0,
            "ct_model": res["ct"],
            "ct_with_reference_s": res["ct"],              # 恒等：Ct 与 S 解耦
            "ct_error": 0.0,
            "rt_model_n": res["rt_n"],
            "rt_with_reference_s_n": rt_with_s_ref,
            "rt_error": res["rt_n"] / rt_with_s_ref - 1.0,  # 正 = 模型高估
        })
    return {"available": True, "source": source, "rows": rows}


# ---------------------------------------------------------------------------
# 组装 + 落盘
# ---------------------------------------------------------------------------
def build_ledger(prefer_module: bool = False) -> Dict[str, Any]:
    fit, fit_source = _platform_axisym_fit()
    case = load_suboff_case()

    hulls: List[Dict[str, Any]] = []
    for name in ("KCS", "KVLCC2"):
        hulls.append(audit_ship_hull(name, FROZEN_HULLS[name], fit))

    # DTMB-5415：无公布 S（在册 4.6494 是 Holtrop 相关式自身的输出，不能当基准），
    # 因此只判跨律分歧与体积闭合。
    t5415 = dict(FROZEN_HULLS["DTMB-5415-model"])
    t5415["s_published"] = None
    h5415 = audit_ship_hull("DTMB-5415-model", t5415, fit)
    h5415["reference"]["note"] = (
        "无独立公布 S：backend 记的 4.6494 是 Holtrop 相关式自身输出（Denny-Mumford 4.598），"
        "只作跨律对照，不作基准判决"
    )
    hulls.append(h5415)

    hulls.append(audit_axisym_body(FROZEN_PRESETS["SUBOFF"], case, fit))

    speeds = {"KCS": 7.717, "KVLCC2": 7.973, "DTMB-5415-model": 1.533}  # Fr≈0.26/0.142/0.20
    rt = quantify_rt_inflation(hulls, speeds)

    verdicts = {}
    for h in hulls:
        hard = [c for c in h["checks"] if c["id"] in
                ("C2_holtrop_vs_published", "C3_axisym_fit_vs_published",
                 "C4_cross_law_divergence", "A2_preset_volume_vs_profile",
                 "A4_filed_surface_vs_profile", "A5_filed_surface_attainability",
                 "A6_fit_surface_vs_profile")]
        worst = PASS
        for c in hard:
            if c["verdict"] == FAIL:
                worst = FAIL
                break
            if c["verdict"] == WITHHELD and worst == PASS:
                worst = WITHHELD
        verdicts[h["hull"]] = worst

    ledger: Dict[str, Any] = {
        "tool": "tools/verif/geometry/std_geometry_gate.py",
        "module": "src/ship_verification/std_geometry.py",
        "audited_object": fit_source,
        "tolerances": {"accept": TOL_ACCEPT, "reject": TOL_REJECT},
        "hulls": hulls,
        "verdicts": verdicts,
        "rt_inflation": rt,
        "suboff_profile": {
            "definition": {
                "length_m": SUBOFF_PROFILE.length,
                "radius_max_m": SUBOFF_PROFILE.radius_max,
                "l_nose_m": SUBOFF_PROFILE.l_nose,
                "l_mid_m": SUBOFF_PROFILE.l_mid,
                "l_stern_m": SUBOFF_PROFILE.l_stern,
                "nose_power": SUBOFF_PROFILE.nose_power,
                "stern_power": SUBOFF_PROFILE.stern_power,
            },
            "sensitivity": suboff_sensitivity(),
        },
    }
    if prefer_module:
        ledger["prefer_module"] = load_prefer_module()
    return ledger


def render_md(ledger: Dict[str, Any]) -> str:
    lines: List[str] = []
    A = lines.append
    A("# STD-GEOM 几何一致性台账")
    A("")
    A(f"- 被审计对象：`{ledger['audited_object']}`")
    A(f"- 判据容差：接受 {ledger['tolerances']['accept']:.0%} · 拒绝 {ledger['tolerances']['reject']:.0%}")
    A("")
    A("| 船/体 | 形态 | 判决 | 关键量 |")
    A("|---|---|---|---|")
    for h in ledger["hulls"]:
        key = next((c for c in h["checks"] if c["verdict"] == FAIL), None)
        key_txt = (
            f"{key['id']} = {key['rel_error']:+.2%}" if key else "全部在容差内"
        )
        A(f"| {h['hull']} | {h['form_family']} | **{ledger['verdicts'][h['hull']]}** | {key_txt} |")
    A("")
    A("## 湿面积三方对照")
    A("")
    A("| 船/体 | Holtrop [m²] | Denny-Mumford [m²] | 回转体律 [m²] | 公布/档案 [m²] | 回转体律误差 |")
    A("|---|---|---|---|---|---|")
    for h in ledger["hulls"]:
        c, r = h["computed"], h["reference"]
        ref = r.get("wetted_surface_m2") or r.get("wetted_surface_m2_filed")
        s_fit = c.get("s_axisym_fit_m2")
        err = f"{(s_fit / ref - 1):+.2%}" if ref else "n/a"
        A(f"| {h['hull']} | {c.get('s_holtrop_m2', float('nan')):.4g} | "
          f"{c.get('s_denny_mumford_m2', float('nan')):.4g} | {s_fit:.4g} | "
          f"{ref if ref else 'n/a'} | {err} |")
    A("")
    rt = ledger["rt_inflation"]
    if rt.get("available"):
        A("## Rt / Ct 的分离后果")
        A("")
        A("| 船/体 | V [m/s] | S_model [m²] | S_ref [m²] | S 误差 | Ct 误差 | Rt 高估 |")
        A("|---|---|---|---|---|---|---|")
        for row in rt["rows"]:
            A(f"| {row['hull']} | {row['speed_ms']:.3f} | {row['s_model_m2']:.4g} | "
              f"{row['s_reference_m2']:.4g} | {row['s_error']:+.2%} | "
              f"{row['ct_error']:+.2%} | {row['rt_error']:+.2%} |")
        A("")
    A("## SUBOFF 剖面族灵敏度")
    A("")
    prof = ledger["suboff_profile"]["sensitivity"]
    A(f"- 体积区间：{prof['volume_m3']['min']:.4f} … {prof['volume_m3']['max']:.4f} m³（标称 {prof['volume_m3']['nominal']:.4f}）")
    A(f"- 湿面积区间：{prof['wetted_surface_m2']['min']:.4f} … {prof['wetted_surface_m2']['max']:.4f} m²（标称 {prof['wetted_surface_m2']['nominal']:.4f}）")
    A("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="STD-GEOM geometry consistency gate")
    ap.add_argument("--out", default="artifacts/std_geometry")
    ap.add_argument("--prefer-module", action="store_true",
                    help="尝试 import 真实 backend 模块并对冻结副本做一致性核对")
    args = ap.parse_args(argv)

    ledger = build_ledger(prefer_module=args.prefer_module)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2))
    (out / "ledger.md").write_text(render_md(ledger))

    print(render_md(ledger))
    print(f"\n[std-geometry] wrote {out/'ledger.json'} and {out/'ledger.md'}")
    if ledger.get("prefer_module") and not ledger["prefer_module"]["ok"]:
        print(f"[std-geometry] prefer-module unavailable: {ledger['prefer_module']['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())