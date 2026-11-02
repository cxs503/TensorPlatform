"""m1873 —— 船舶阻力/静水力学「同名多实现漂移 + 解析基准误差谱」审计.

背景
----
平台在 ``backend/`` 下并存两套阻力内核：

* ``backend/api/domain/resistance_core.py`` + ``backend/api/domain/hull_geometry.py``
  （端点侧，带 published-geometry 闸门）；
* ``backend/services/resistance_core.py`` + ``backend/services/hull_geometry.py``
  （自称 "single source of truth"，带 Canonical-form 闭式解 + Holtrop + Denny-Mumford
  + 箱体静水力学）。

两者都实现了**同一个物理量**（Cf、湿表面积 S、Fn^2.5 余阻、箱体 KB/BM/GM），
却没有任何一条判据要求它们给出同一个数。本审计：

1. 在统一输入栅格上并列跑两套实现，量化**逐项相对偏差**（互为对照）；
2. 每一类量另取**独立解析基准**（ITTC-57 定义式、方驳闭式湿面积、矩形剖面精确
   KB/BM、精确横倾复原力臂、published 表 S）并给出误差百分比；
3. 结果机读落盘 ``outputs/m1873_dup_kernel/divergence.json``，
   由 ``tests/audit/test_dup_kernel_divergence.py`` 钉死为回归闸门。

所有计算均为闭式算术，无迭代求解器 / 无网格，可在高负载机器上安全运行。

运行::

    python3 tests/audit/audit_dup_kernel.py            # 生成 JSON + 图
    python3 -m pytest tests/audit/test_dup_kernel_divergence.py -q
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from typing import Dict, List

import dup_kernel_reference as ref  # noqa: E402  (same directory)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
API_DOM = os.path.join(ROOT, "backend", "api", "domain")
SERVICES = os.path.join(ROOT, "backend", "services")
OUTDIR = os.path.join(ROOT, "outputs", "m1873_dup_kernel")

for _p in (HERE, SERVICES, API_DOM):   # API_DOM first on path
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Module loading (both families, no FastAPI / no DB)
# ---------------------------------------------------------------------------
def _load(alias: str, path: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def load_platform():
    # the endpoint core falls back to a flat ``import hull_geometry``; bind that
    # bare name to the *api/domain* copy so the two families never get mixed up
    api_geom = _load("m1873_api_hull_geometry", os.path.join(API_DOM, "hull_geometry.py"))
    sys.modules["hull_geometry"] = api_geom
    api_core = _load("m1873_api_resistance_core", os.path.join(API_DOM, "resistance_core.py"))
    svc_geom = _load("m1873_svc_hull_geometry", os.path.join(SERVICES, "hull_geometry.py"))
    svc_core = _load("m1873_svc_resistance_core", os.path.join(SERVICES, "resistance_core.py"))
    return {"api_geom": api_geom, "api_core": api_core,
            "svc_geom": svc_geom, "svc_core": svc_core}


def rel_pct(x: float, base: float) -> float:
    if base == 0:
        return float("nan")
    return 100.0 * (x - base) / base


def _r(x: float, n: int = 6) -> float:
    return round(float(x), n)


# ---------------------------------------------------------------------------
# A. Wetted-surface estimators vs published table + mutual divergence
# ---------------------------------------------------------------------------
HULLS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, cb=0.8098, cm=0.9980, cwp=0.9038,
                   s_pub=27194.0),
    "DTMB-5415": dict(L=5.720, B=0.760, T=0.248, cb=0.5060, cm=0.9860,
                      cwp=0.7350, s_pub=4.874),
}


def audit_wetted_surface(mods) -> Dict:
    rows: List[Dict] = []
    for name, g in HULLS.items():
        L, B, T, cb, cm, cwp = g["L"], g["B"], g["T"], g["cb"], g["cm"], g["cwp"]
        cwp_default = ref.cwp_schneekluth(cb)
        s_pub = g["s_pub"]

        est = {
            "ref_axisym(pi*B*L*(0.5+0.25Cb))": ref.axisym_surface(L, B, cb),
            "ref_holtrop1982(Cwp=(1+2Cb)/3)": ref.holtrop1982(L, B, T, cb, cm, cwp_default),
            "ref_holtrop1982(Cwp=published)": ref.holtrop1982(L, B, T, cb, cm, cwp),
            "ref_denny_mumford": ref.denny_mumford(L, B, T, cb),
            # platform: endpoint family
            "api.holtrop_wetted_surface(published Cm,Cwp)":
                mods["api_geom"].holtrop_wetted_surface(L, B, T, cb, cm, cwp),
            # platform: service family
            "svc.legacy_wetted_surface": mods["svc_geom"].legacy_wetted_surface(L, B, T, cb),
            "svc.holtrop_wetted_surface(default Cwp)":
                mods["svc_geom"].holtrop_wetted_surface(L, B, T, cb),
            "svc.wetted_surface('ship')": mods["svc_geom"].wetted_surface("ship", L, B, T, cb)[0],
            "svc.denny_mumford": mods["svc_geom"].denny_mumford_wetted_surface(L, B, T, cb),
            # the value the endpoint actually uses when S is not declared
            "api_core default S (axisym)":
                mods["svc_core"].axisym_wetted_surface(L, B, cb),
        }
        for est_name, s in est.items():
            rows.append({
                "hull": name,
                "estimator": est_name,
                "S_m2": _r(s, 4),
                "err_vs_published_pct": _r(rel_pct(s, s_pub), 2),
                "within_platform_15pct_gate": bool(abs(rel_pct(s, s_pub)) <= 15.0),
                "within_3pct_criterion": bool(abs(rel_pct(s, s_pub)) <= 3.0),
            })

        # mutual (same-physics) divergence: legacy/axisym pair must be identical
        pair = (mods["svc_geom"].legacy_wetted_surface(L, B, T, cb),
                mods["svc_core"].axisym_wetted_surface(L, B, cb),
                mods["api_core"].solve_resistance(name, 5.0)["wetted_surface_m2"])
        row_pair = {
            "hull": name,
            "svc.legacy": _r(pair[0], 4),
            "svc_core.axisym": _r(pair[1], 4),
            "api_core.solve_resistance.S": _r(pair[2], 4),
            "max_mutual_divergence_pct": _r(
                max(abs(rel_pct(pair[1], pair[0])), abs(rel_pct(pair[2], pair[0]))), 4),
        }
        rows.append({"hull": name, "estimator": "MUTUAL(svc.legacy vs svc_core.axisym vs api_core)",
                     "S_m2": row_pair["svc.legacy"],
                     "err_vs_published_pct": row_pair["max_mutual_divergence_pct"],
                     "within_platform_15pct_gate": True,
                     "within_3pct_criterion": abs(row_pair["max_mutual_divergence_pct"]) <= 3.0,
                     "detail": row_pair})

        # documented-claim arithmetic: "axisym over-predicts KVLCC2 S by ~+52 %"
        rows.append({
            "hull": name,
            "estimator": "CLAIM(axisym vs published)",
            "S_m2": _r(ref.axisym_surface(L, B, cb), 4),
            "err_vs_published_pct": _r(rel_pct(ref.axisym_surface(L, B, cb), s_pub), 2),
            "within_platform_15pct_gate": False,
            "within_3pct_criterion": False,
            "detail": {"cwp_default": _r(cwp_default, 4), "cwp_published": cwp},
        })
    return {"rows": rows}


# ---------------------------------------------------------------------------
# B. Canonical bodies: closed-form wetted area / Cb (exact baselines)
# ---------------------------------------------------------------------------
CANON = {"box": 1.0, "cylinder": math.pi / 4.0, "sphere": math.pi / 6.0}

#: dimension sets that are *consistent* with each canonical body, so the closed
#: forms are comparable: a box floats at any T, a horizontal cylinder / capsule
#: has T = B = 2R, a sphere has L = B = T = 2R.
CANON_DIMS = {
    "box": (40.0, 10.0, 4.0),
    "cylinder": (40.0, 10.0, 10.0),
    "sphere": (10.0, 10.0, 10.0),
    "capsule": (40.0, 10.0, 10.0),
}


def audit_canonical(mods) -> Dict:
    rows = []
    for form, cb_ref in CANON.items():
        L, B, T = CANON_DIMS[form]
        s_plat = mods["svc_geom"].exact_wetted_surface(form, L, B, T)
        cb_plat = mods["svc_geom"].form_cb(form, L, B, T)
        if form == "box":
            s_ref = ref.box_wetted_surface(L, B, T)
        elif form == "cylinder":
            s_ref = ref.cylinder_wetted_surface(L, B)
        elif form == "sphere":
            s_ref = ref.sphere_wetted_surface(B)
        else:  # capsule
            s_ref = ref.capsule_wetted_surface(L, B)
        rows.append({
            "form": form,
            "S_platform_m2": _r(s_plat, 6),
            "S_reference_m2": _r(s_ref, 6),
            "S_err_pct": _r(rel_pct(s_plat, s_ref), 8),
            "Cb_platform": _r(cb_plat, 6),
            "Cb_reference": _r(cb_ref, 6),
            "Cb_err_pct": _r(rel_pct(cb_plat, cb_ref), 8),
        })
    Lc, Bc, Tc = CANON_DIMS["capsule"]
    rc = 0.5 * Bc
    capsule_cb = (math.pi * rc * rc * Lc + 4.0 / 3.0 * math.pi * rc ** 3) / (Lc * Bc * Tc)
    s_cap_plat = mods["svc_geom"].exact_wetted_surface("capsule", Lc, Bc, Tc)
    s_cap_ref = ref.capsule_wetted_surface(Lc, Bc)
    cb_cap_plat = mods["svc_geom"].form_cb("capsule", Lc, Bc, Tc)
    rows.append({"form": "capsule",
                 "S_platform_m2": _r(s_cap_plat, 6),
                 "S_reference_m2": _r(s_cap_ref, 6),
                 "S_err_pct": _r(rel_pct(s_cap_plat, s_cap_ref), 8),
                 "Cb_platform": _r(cb_cap_plat, 6),
                 "Cb_reference": _r(capsule_cb, 6),
                 "Cb_err_pct": _r(rel_pct(cb_cap_plat, capsule_cb), 8)})

    # guard probe: a physically impossible Cb > 1 must not pass silently
    guard = {}
    for form, dims in (("cylinder", (40.0, 10.0, 4.0)), ("sphere", (40.0, 10.0, 4.0))):
        cb = mods["svc_geom"].form_cb(form, *dims)
        try:
            mods["svc_geom"].verify_geometry(form, *dims, cb)
            caught, how = False, "no exception"
        except Exception as exc:  # noqa: BLE001
            caught, how = True, f"{type(exc).__name__}: {exc}"
        guard[form] = {"dims_inconsistent": list(dims), "Cb_returned": _r(cb, 4),
                       "Cb_above_1": bool(cb > 1.0),
                       "downstream_gate_catches": caught, "gate_behaviour": how}
    return {"rows": rows, "nonphysical_cb_guard_probe": guard}


# ---------------------------------------------------------------------------
# C. ITTC-57 friction line: two implementations vs definition + contract drift
# ---------------------------------------------------------------------------
RE_GRID = [1e4, 3e4, 1e5, 3e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11]


def audit_cf(mods) -> Dict:
    rows = []
    worst = {"api": 0.0, "svc": 0.0}
    for re in RE_GRID:
        c_ref = ref.ittc57_cf(re)
        c_api = mods["api_geom"].ittc57_cf(re)
        c_svc = mods["svc_core"].ittc57_cf(re)
        e_api = rel_pct(c_api, c_ref)
        e_svc = rel_pct(c_svc, c_ref)
        worst["api"] = max(worst["api"], abs(e_api))
        worst["svc"] = max(worst["svc"], abs(e_svc))
        rows.append({"Re": re, "cf_ref": _r(c_ref, 10), "cf_api": _r(c_api, 10),
                     "cf_svc": _r(c_svc, 10), "err_api_pct": _r(e_api, 10),
                     "err_svc_pct": _r(e_svc, 10)})

    # contract drift on low Re (outside ITTC-57 validity of the log argument)
    drift = {}
    for re_bad in (50.0, 99.0, 120.0):
        try:
            v_api = _r(mods["api_geom"].ittc57_cf(re_bad), 10)
            api_raised = False
        except Exception as exc:  # noqa: BLE001
            v_api = type(exc).__name__
            api_raised = True
        v_svc = _r(mods["svc_core"].ittc57_cf(re_bad), 10)
        drift[str(re_bad)] = {"api_raises": api_raised, "api_value": v_api,
                              "svc_value": v_svc}
    return {"rows": rows, "worst_abs_err_pct": {k: _r(v, 10) for k, v in worst.items()},
            "low_re_contract_drift": drift}


# ---------------------------------------------------------------------------
# D. Box hydrostatics & righting arm vs exact section integration
# ---------------------------------------------------------------------------
PHI_GRID = [0.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 38.0, 40.0, 45.0, 50.0]


def audit_box_hydrostatics(mods) -> Dict:
    B, T, depth, KG = 10.0, 4.0, 8.0, 3.0   # KG=3 -> GM=+1.083 (stable box)
    exact = ref.box_hydrostatics(B, T, KG)
    plat = {k.lower(): v for k, v in mods["svc_geom"].box_hydrostatics(B, T, KG).items()}
    hs = {
        "params": {"B": B, "T": T, "depth": depth, "KG": KG},
        "platform": {k: _r(v, 6) for k, v in plat.items()},
        "reference": {k: _r(v, 6) for k, v in exact.items()},
        "err_pct": {k: _r(rel_pct(plat[k], exact[k]), 8) for k in ("kb", "bm", "km", "gm")},
    }
    limit_rad = mods["svc_geom"].wall_sided_limit_rad(B, T, depth)
    limit_deg = math.degrees(limit_rad)
    rows = []
    for phi in PHI_GRID:
        gz_exact = ref.box_gz_exact(B, T, depth, KG, phi)
        gz_ws = mods["svc_geom"].wall_sided_gz(plat["gm"], plat["bm"], math.radians(phi))
        gz_num = mods["svc_geom"].box_gz_numeric(B, T, depth, KG, phi)
        # linearised reference GM*sin(phi) is the small-angle anchor
        gz_lin = exact["gm"] * math.sin(math.radians(phi))
        rows.append({
            "phi_deg": phi,
            "gz_exact_m": _r(gz_exact, 6),
            "gz_wallsided_m": _r(gz_ws, 6),
            "gz_numeric_m": _r(gz_num, 6),
            "gz_linear_GMsin_m": _r(gz_lin, 6),
            "err_wallsided_pct": _r(rel_pct(gz_ws, gz_exact), 3) if gz_exact else 0.0,
            "err_numeric_pct": _r(rel_pct(gz_num, gz_exact), 3) if gz_exact else 0.0,
            "beyond_wallsided_limit": bool(phi > limit_deg),
        })
    return {"hydrostatics": hs, "wall_sided_limit_deg": _r(limit_deg, 4), "rows": rows}


# ---------------------------------------------------------------------------
# E. End-to-end resistance: endpoint core vs service core (same physics?)
# ---------------------------------------------------------------------------
SPEEDS = {"KVLCC2": [5.0, 8.0, 12.0, 15.4], "DTMB-5415": [1.0, 1.5, 2.0, 2.5]}


def audit_resistance(mods) -> Dict:
    rows = []
    for hull, speeds in SPEEDS.items():
        g = HULLS[hull]
        for v in speeds:
            api_out = mods["api_core"].solve_resistance(hull, v)
            svc_out = mods["svc_core"].resistance_core(v, g["L"], g["B"], g["T"], g["cb"])
            ref_out = ref.resistance_chain(v, g["L"], g["B"], g["cb"],
                                           svc_out["wetted_surface_m2"])
            svc_pinned = mods["svc_core"].resistance_core(
                v, g["L"], g["B"], g["T"], g["cb"],
                wetted_surface=api_out["wetted_surface_m2"])
            rows.append({
                "hull": hull,
                "speed_ms": v,
                "div_api_vs_svc_S_pinned_pct": _r(rel_pct(api_out["total_resistance_n"],
                                                          svc_pinned["total_resistance_n"]), 6),
                "svc_default_S_m2": _r(svc_out["wetted_surface_m2"], 3),
                "api_S_m2": _r(api_out["wetted_surface_m2"], 3),
                "rt_api_kN": _r(api_out["total_resistance_n"] / 1e3, 4),
                "rt_svc_kN": _r(svc_out["total_resistance_n"] / 1e3, 4),
                "rt_ref_kN": _r(ref_out["rt"] / 1e3, 4),
                "div_api_vs_svc_pct": _r(rel_pct(api_out["total_resistance_n"],
                                                 svc_out["total_resistance_n"]), 4),
                "div_api_vs_ref_pct": _r(rel_pct(api_out["total_resistance_n"], ref_out["rt"]), 4),
                "cf_api": _r(api_out["cf"], 8),
                "cf_svc": _r(svc_out["cf"], 8),
                "fn": _r(api_out["froude_number"], 4),
                "ct": _r(api_out["ct"], 8),
                "in_validity_envelope": bool(api_out["validity"]["in_envelope"]),
            })
    return {"rows": rows}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_audit(write: bool = True) -> Dict:
    mods = load_platform()
    result = {
        "mission": "m1873",
        "generated_by": "tests/audit/audit_dup_kernel.py",
        "platform_modules": {
            "api": ["backend/api/domain/resistance_core.py",
                    "backend/api/domain/hull_geometry.py"],
            "services": ["backend/services/resistance_core.py",
                         "backend/services/hull_geometry.py"],
        },
        "A_wetted_surface": audit_wetted_surface(mods),
        "B_canonical_exact": audit_canonical(mods),
        "C_friction_line": audit_cf(mods),
        "D_box_hydrostatics": audit_box_hydrostatics(mods),
        "E_resistance_end_to_end": audit_resistance(mods),
    }
    # headline metrics
    ws_rows = [r for r in result["A_wetted_surface"]["rows"]
               if r["estimator"].startswith("ref_holtrop1982(Cwp=published)")]
    headline = {
        "max_api_vs_svc_rt_divergence_after_S_pinned_pct": max(
            abs(r["div_api_vs_svc_S_pinned_pct"]) for r in result["E_resistance_end_to_end"]["rows"]),
        "max_api_vs_svc_rt_divergence_pct": max(
            abs(r["div_api_vs_svc_pct"]) for r in result["E_resistance_end_to_end"]["rows"]),
        "max_cf_err_vs_ittc57_pct": max(result["C_friction_line"]["worst_abs_err_pct"].values()),
        "max_canonical_S_err_pct": max(abs(r["S_err_pct"]) for r in result["B_canonical_exact"]["rows"]),
        "holtrop_published_cwp_err_pct": {r["hull"]: r["err_vs_published_pct"] for r in ws_rows},
        "wetted_surface_gate_failures_15pct": sorted({
            r["estimator"] for r in result["A_wetted_surface"]["rows"]
            if not r["within_platform_15pct_gate"]}),
        "box_KB_BM_max_err_pct": max(abs(v) for k, v in
                                     result["D_box_hydrostatics"]["hydrostatics"]["err_pct"].items()
                                     if k in ("kb", "bm", "km")),
    }
    result["headline"] = headline
    if write:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(os.path.join(OUTDIR, "divergence.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def main() -> int:
    res = run_audit(write=True)
    print(json.dumps(res["headline"], ensure_ascii=False, indent=2))
    print(f"\nwrote {os.path.join(OUTDIR, 'divergence.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())