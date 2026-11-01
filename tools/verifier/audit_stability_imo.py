"""
Independent audit of the platform's intact-stability / IMO gate
(verification-debt paydown, verifier seat tp-ver-3).

Scope
-----
Two endpoints of the live ship-performance service (:8001) expose intact
stability, and they are *different implementations*:

  A. POST /api/v1/calculations/stability   -> backend/api/calculations.py
  B. POST /api/v1/stability/imo-check      -> backend/api/stability_imo.py
  B'. POST /api/v1/stability/gz-curve      -> same module as B, returns the curve

This script treats both as black boxes (no import of platform code), compares
them against a first-principles reference for the one hull whose large-angle
hydrostatics are known exactly -- a rectangular pontoon / box barge -- and
re-derives the IMO IS Code 2008 §2.2.1 verdicts from the platform's own
reported areas, so that *criterion errors* and *GZ-model errors* are separated.

Reference chain (all independent of the platform):
  1. box-barge section integration (exact, any heel, deck immersion included)
  2. within the wall-sided range it must reproduce the closed form
     GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi))  -- asserted in the tests
  3. IMO IS Code 2008 Part A Ch.2 §2.2.1 thresholds transcribed from the Code

Usage
-----
    python -m tools.verifier.audit_stability_imo --out artifacts/verifier/stability_imo_audit
    python -m tools.verifier.audit_stability_imo --offline   # replay captures only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .box_barge_reference import BoxBarge
from .stability_reference import IMO_IS_CODE_2008, imo_criteria_from_curve

API_BASE = os.environ.get("TP_SHIP_API", "http://127.0.0.1:8001/api/v1")

# Accuracy standard of the platform (owner-level): >15% is unacceptable.
TOL_ACCEPT = 0.15
TOL_TARGET = 0.03

# ---------------------------------------------------------------------------
# Benchmark cases (pontoon forms -> exact reference available)
# ---------------------------------------------------------------------------
CASES = [
    {
        "id": "barge-B20-T8-D12-KG7",
        "label": "Pontoon 100x20x8, D=12, KG=7 (deep loading)",
        "L": 100.0, "B": 20.0, "T": 8.0, "D": 12.0, "KG": 7.0, "Cb": 1.0, "Cm": 1.0,
    },
    {
        "id": "barge-B30-T10-D16-KG8",
        "label": "Pontoon 120x30x10, D=16, KG=8 (wide, shallow GM)",
        "L": 120.0, "B": 30.0, "T": 10.0, "D": 16.0, "KG": 8.0, "Cb": 1.0, "Cm": 1.0,
    },
]


def _post(path: str, body: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _rel(err_abs: float, ref: float) -> float:
    return abs(err_abs) / abs(ref) if ref else float("nan")


def reference_case(case: dict) -> dict:
    """Exact reference values for one pontoon case."""
    b = BoxBarge(beam=case["B"], draft=case["T"], depth=case["D"], kg=case["KG"])
    curve = b.gz_curve(step=5.0, max_angle=90.0)
    exact = {
        "kb": b.kb,
        "bm": b.bm,
        "gm": b.gm,
        "deck_edge_angle": b.deck_edge_angle,
        "gz_max": max(p["gz"] for p in curve),
        "gz_max_angle": max(curve, key=lambda p: p["gz"])["angle"],
        "gz_at_30": b.gz(30.0),
        "gz_at_40": b.gz(40.0),
        "area_0_30": b.area(0.0, 30.0),
        "area_30_40": b.area(30.0, 40.0),
        "area_0_40": b.area(0.0, 40.0),
        "gz_curve": curve,
        "displacement_m3_per_m": b.area0,
    }
    exact["imo"] = imo_criteria_from_curve(curve, b.gm)
    return exact


def audit_case(case: dict, offline: dict | None = None) -> dict:
    """Compare both platform endpoints against the exact reference."""
    ref = reference_case(case)
    L, B, T, D, KG, Cb, Cm = (case[k] for k in ("L", "B", "T", "D", "KG", "Cb", "Cm"))
    disp = 1.025 * L * B * T * Cb  # tonnes, sea water 1.025 t/m3

    if offline:
        resp_a = offline["a"]
        resp_b = offline["b"]
        resp_curve = offline["curve"]
    else:
        resp_a = _post("calculations/stability", {
            "length": L, "beam": B, "draft": T, "depth": D,
            "displacement": disp, "cb": Cb, "kg": KG,
        })
        body_b = {"L": L, "B": B, "T": T, "D": D, "displacement": disp,
                  "Cb": Cb, "Cm": Cm, "KG": KG}
        resp_b = _post("stability/imo-check", body_b)
        resp_curve = _post("stability/gz-curve", body_b)

    # ---- A: /calculations/stability (flat schema) ------------------------
    a = {
        "kb": resp_a.get("kb"), "bm": resp_a.get("bm"), "gm": resp_a.get("gm"),
        "gz_max": resp_a.get("gz_max"), "gz_max_angle": resp_a.get("gz_max_angle"),
        "area_0_30": resp_a.get("area_0_30"), "area_30_40": resp_a.get("area_30_40"),
        "area_0_40": resp_a.get("area_0_40"), "vanishing_angle": resp_a.get("vanishing_angle"),
        "criteria": resp_a.get("imo_criteria", {}),
        "raw": resp_a,
    }
    # ---- B: /stability/imo-check ----------------------------------------
    b = {
        "kb": resp_b.get("KB"), "bm": resp_b.get("BM"), "gm": resp_b.get("GM"),
        "gz_max": resp_b.get("GZ_max"), "gz_max_angle": resp_b.get("GZ_max_angle"),
        "area_0_30": resp_b.get("area_0_30"), "area_30_40": resp_b.get("area_30_40"),
        "area_0_40": resp_b.get("area_0_40"), "vanishing_angle": resp_b.get("vanishing_angle"),
        "criteria": resp_b.get("criteria_results", {}),
        "all_criteria_met": resp_b.get("all_criteria_met"),
        "failed_criteria": resp_b.get("failed_criteria", []),
        "raw": resp_b,
    }

    def dev_table(mod: dict) -> dict:
        out = {}
        for key in ("kb", "bm", "gm", "gz_max", "gz_max_angle", "area_0_30",
                    "area_30_40", "area_0_40"):
            v = mod.get(key)
            r = ref.get(key)
            if v is None or r is None:
                out[key] = {"value": v, "ref": r, "rel_err": None}
                continue
            out[key] = {"value": v, "ref": r, "rel_err": _rel(float(v) - float(r), float(r)),
                        "acceptable_15pct": bool(_rel(float(v) - float(r), float(r)) <= TOL_ACCEPT),
                        "within_3pct": bool(_rel(float(v) - float(r), float(r)) <= TOL_TARGET)}
        return out

    deviations = {"A": dev_table(a), "B": dev_table(b)}

    # ---- criterion-level check: apply IS Code thresholds to platform values
    crit_a = {}
    crit_a["gm0"] = {"platform_met": a["criteria"].get("GM >= 0.15m"),
                     "ref_met": ref["imo"]["gm0"]["met"], "value": a["gm"],
                     "is_threshold": 0.15}
    crit_a["area_0_40"] = {"platform_met": a["criteria"].get("Area_0_40 >= 0.055 m·rad"),
                           "ref_met": ref["imo"]["area_0_40"]["met"], "value": a["area_0_40"],
                           "platform_threshold": 0.055,
                           "is_threshold": IMO_IS_CODE_2008["area_0_40"]["threshold"]}
    crit_a["gz_at_30"] = {"platform_met": a["criteria"].get("GZmax >= 0.20m"),
                          "ref_met": ref["imo"]["gz_at_30"]["met"],
                          "platform_metric": "GZ_max", "is_metric": "GZ(30 deg)",
                          "value_gz_max": a["gz_max"],
                          "is_threshold": IMO_IS_CODE_2008["gz_at_30"]["threshold"]}
    crit_a["angle_gz_max"] = {"platform_met": a["criteria"].get("φ_GZmax >= 25°"),
                              "ref_met": ref["imo"]["angle_gz_max"]["met"],
                              "value": a["gz_max_angle"], "is_threshold": 25.0}
    crit_a["area_30_40"] = {"platform_met": a["criteria"].get("Area_30_40 >= 0.03 m·rad"),
                            "ref_met": ref["imo"]["area_30_40"]["met"],
                            "value": a["area_30_40"], "is_threshold": 0.03}
    crit_a["area_0_30"] = {"platform_met": None,
                           "ref_met": ref["imo"]["area_0_30"]["met"],
                           "value": a["area_0_30"], "is_threshold": 0.055,
                           "note": "criterion absent from endpoint A"}
    crit_a["vanishing_angle"] = {"platform_met": a["criteria"].get("Vanishing angle >= 55°"),
                                 "ref_met": None,
                                 "value": a["vanishing_angle"], "is_threshold": None,
                                 "note": "no such criterion in IS Code 2008 Part A 2.2.1"}

    # Re-derive the IS Code verdict from the platform's OWN reported areas:
    # this isolates threshold/definition errors from GZ-model errors.
    rederived = {
        "area_0_30_met": a["area_0_30"] >= IMO_IS_CODE_2008["area_0_30"]["threshold"],
        "area_0_40_met": a["area_0_40"] >= IMO_IS_CODE_2008["area_0_40"]["threshold"],
        "area_30_40_met": a["area_30_40"] >= IMO_IS_CODE_2008["area_30_40"]["threshold"],
        "gm0_met": a["gm"] >= IMO_IS_CODE_2008["gm0"]["threshold"],
        "angle_gz_max_met": a["gz_max_angle"] >= IMO_IS_CODE_2008["angle_gz_max"]["threshold"],
    }
    a_claims_all = all(bool(v) for v in a["criteria"].values()) if a["criteria"] else None
    gate_a = {
        "platform_all_met": a_claims_all,
        "is_code_all_met_on_platform_values": all(rederived.values()),
        "per_criterion": rederived,
        "agreement": None if a_claims_all is None else (a_claims_all == all(rederived.values())),
    }

    # A vs B intra-platform disagreement on the same input
    cross = {}
    for key in ("kb", "bm", "gm", "gz_max", "area_0_30", "area_30_40", "area_0_40"):
        va, vb = a.get(key), b.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va:
            cross[key] = {"A": va, "B": vb, "rel_diff": abs(vb - va) / abs(va)}

    return {
        "case": case,
        "reference": {k: v for k, v in ref.items() if k != "gz_curve"},
        "reference_curve": ref["gz_curve"],
        "platform_curve_B": {"angles": resp_curve.get("angles"),
                             "gz": resp_curve.get("gz_values")},
        "platform_A": a, "platform_B": b,
        "deviations": deviations,
        "criteria_A": crit_a,
        "gate_A": gate_a,
        "cross_endpoint_diff": cross,
    }


def summarise(results: list[dict]) -> dict:
    """Machine-readable verdict summary (the acceptance judgement)."""
    fails = []
    for r in results:
        cid = r["case"]["id"]
        for mod in ("A", "B"):
            for key, d in r["deviations"][mod].items():
                if d.get("rel_err") is None:
                    continue
                if d["rel_err"] > TOL_ACCEPT:
                    fails.append({"case": cid, "endpoint": mod, "quantity": key,
                                  "rel_err": d["rel_err"], "value": d["value"],
                                  "ref": d["ref"]})
        if r["gate_A"].get("agreement") is False:
            fails.append({"case": cid, "endpoint": "A", "quantity": "IMO gate verdict",
                          "rel_err": None,
                          "note": "platform gate verdict disagrees with IS Code 2008 "
                                  "applied to the platform's own reported values"})
        for key, d in r["cross_endpoint_diff"].items():
            if d["rel_diff"] > 1.0:
                fails.append({"case": cid, "endpoint": "A vs B", "quantity": key,
                              "rel_err": d["rel_diff"],
                              "note": "two platform endpoints disagree by >100% on the same input"})

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api_base": API_BASE,
        "acceptance_standard": {"unacceptable_above": TOL_ACCEPT, "target": TOL_TARGET},
        "verdict": "FAIL" if fails else "PASS",
        "failures": fails,
        "n_failures": len(fails),
    }


def _fmt(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def to_markdown(results: list[dict], summary: dict) -> str:
    L = []
    L.append("# 完整稳性 / IMO 衡准 独立复核（box barge 解析基准）\n")
    L.append(f"生成时间：{summary['generated_at']}　API：`{summary['api_base']}`\n")
    L.append(f"**判定：{summary['verdict']}**（不可接受误差阈值 {int(TOL_ACCEPT*100)}%）\n")
    for r in results:
        c = r["case"]
        ref = r["reference"]
        L.append(f"\n## 算例 {c['id']} — {c['label']}\n")
        L.append(f"L={c['L']} m, B={c['B']} m, T={c['T']} m, D={c['D']} m, KG={c['KG']} m, "
                 f"Cb={c['Cb']} (pontoon ⇒ 解析解可用)\n")
        L.append(f"解析基准：KB={_fmt(ref['kb'])} m, BM={_fmt(ref['bm'])} m, GM={_fmt(ref['gm'])} m, "
                 f"甲板边缘入水角={_fmt(ref['deck_edge_angle'],2)}°\n")
        L.append("\n| 量 | 解析基准 | 端点A /calculations/stability | 误差A | 端点B /stability/imo-check | 误差B | <3%？ |")
        L.append("|---|---|---|---|---|---|---|")
        for key in ("kb", "bm", "gm", "gz_max", "area_0_30", "area_30_40", "area_0_40"):
            da, db = r["deviations"]["A"][key], r["deviations"]["B"][key]
            ok = "A通过" if da.get("within_3pct") else "A不通过"
            ok += " / " + ("B通过" if db.get("within_3pct") else "B不通过")
            L.append(f"| {key} | {_fmt(ref.get(key))} | {_fmt(da['value'])} | "
                     f"{_fmt(da.get('rel_err'),3) if da.get('rel_err') is not None else '—'} | "
                     f"{_fmt(db['value'])} | "
                     f"{_fmt(db.get('rel_err'),3) if db.get('rel_err') is not None else '—'} | {ok} |")
        L.append("\n衡准级复核（端点A）：\n")
        L.append("\n| 衡准 | IS Code 阈值 | 平台阈值/口径 | 平台判定 | IS Code 判定 | 一致？ |")
        L.append("|---|---|---|---|---|---|")
        for key, d in r["criteria_A"].items():
            if key == "vanishing_angle":
                L.append(f"| {key} | 无此条 | ≥55° | {d['platform_met']} | — | 不可比（条目不存在） |")
                continue
            thr = d.get("is_threshold")
            pthr = d.get("platform_threshold", thr)
            metric = d.get("is_metric") or key
            pmet = d.get("platform_met")
            rmet = d.get("ref_met")
            agree = "—" if pmet is None or rmet is None else ("是" if pmet == rmet else "**否**")
            pmetric = d.get("platform_metric", key)
            L.append(f"| {key} | {thr} ({metric}) | {pthr} ({pmetric}) | {pmet} | {rmet} | {agree} |")
        L.append("\n端点一致性（同一输入，A vs B 相对差）：")
        for key, d in r["cross_endpoint_diff"].items():
            L.append(f"- {key}: A={_fmt(d['A'])} / B={_fmt(d['B'])} → {d['rel_diff']*100:.1f}%")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/verifier/stability_imo_audit")
    ap.add_argument("--offline", action="store_true", help="replay stored captures")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "api_captures"), exist_ok=True)

    results = []
    for case in CASES:
        offline = None
        if args.offline:
            p = os.path.join(args.out, "api_captures", f"{case['id']}.json")
            offline = json.load(open(p, encoding="utf-8"))
        r = audit_case(case, offline=offline)
        if not args.offline:
            json.dump({"a": r["platform_A"]["raw"], "b": r["platform_B"]["raw"],
                       "curve": r["platform_curve_B"]},
                      open(os.path.join(args.out, "api_captures", f"{case['id']}.json"), "w"),
                      ensure_ascii=False, indent=1)
        results.append(r)

    summary = summarise(results)
    out = {"summary": summary, "results": results}
    json.dump(out, open(os.path.join(args.out, "audit.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    md = to_markdown(results, summary)
    open(os.path.join(args.out, "audit.md"), "w", encoding="utf-8").write(md)
    print(f"verdict={summary['verdict']} failures={summary['n_failures']} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())