"""
参考值语料库 + 复核判定器（reference corpus & verdict engine）。

用法：
    python3 tools/validate/reference_corpus.py build    # 生成/刷新语料（sha256 锚定）
    python3 tools/validate/reference_corpus.py verify   # 复核并输出判定 JSON

设计（为什么这样才叫「验证」而不叫「自洽」）
------------------------------------------------------------------
语料条目分三类，判定口径各不相同，判定结果里逐条写明：

  A. analytic        解析式/代数式，参考值由 40 位精度算术（mpmath）独立求值，
                     容差 0.05%。这类条目查实现是否忠实实现公式（可字节级复核）。
  B. cross_method    同一公开公式集的**独立第二实现**（tools/validate/
                     holtrop_ref_independent.py，numpy 写法、零代码复用）给出的
                     R_T/C_T。平台引擎与它的偏差容差 0.5%（捕捉笔误/量纲错）。
                     同时记录「历史实现」（legacy_worktree / legacy_deployed）
                     与本参考的偏差——历史实现若偏差 ≤15%，说明本判据没有分辨力。
  C. experimental    文献/试验基准。**离线环境无原文可查，本语料库不含伪造的
                     试验数值**，条目一律标 withheld 并写明缺什么；绝不手抄。

输出：outputs/reference_corpus_verdict.json（机器可读）+ 控制台摘要。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)

from tools.validate import holtrop_ref_independent as indep  # noqa: E402
from tools.validate import legacy_resistance as legacy      # noqa: E402
from services import resistance_holtrop as engine            # noqa: E402

CORPUS_PATH = os.path.join(ROOT, "tests", "data", "reference_corpus",
                           "resistance_reference.json")
OUT_DIR = os.path.join(ROOT, "outputs")

#: 复核船型集（几何参数均为公开基准船型主尺度；几何先验见 Cb·L·B·T vs Δ）
CASE_HULLS: List[Dict[str, Any]] = [
    {"case_id": "kvlcc2_design_15kn", "hull": "KVLCC2 (MOERI 320m VLCC)",
     "L": 320.0, "B": 58.0, "T": 20.8, "cb": 0.8098, "disp_t": 310000.0,
     "Vs_ms": 15.0 * 0.5144, "i_e_deg": 25.0, "lcb_pct": 1.5,
     "note": "设计点 Fn≈0.138；双桨? 否，单桨尾机型"},
    {"case_id": "kvlcc2_slow_13kn", "hull": "KVLCC2 (MOERI 320m VLCC)",
     "L": 320.0, "B": 58.0, "T": 20.8, "cb": 0.8098, "disp_t": 310000.0,
     "Vs_ms": 13.0 * 0.5144, "i_e_deg": 25.0, "lcb_pct": 1.5,
     "note": "低速点 Fn≈0.119"},
    {"case_id": "kcs_design_24kn", "hull": "KCS (KRISO 230m container)",
     "L": 230.0, "B": 32.2, "T": 10.8, "cb": 0.651, "disp_t": 52030.0,
     "Vs_ms": 24.0 * 0.5144, "i_e_deg": 30.0, "lcb_pct": -1.5,
     "note": "Fn≈0.26，细长高速段"},
    {"case_id": "dtmb5415_15kn", "hull": "DTMB 5415 (destroyer-like)",
     "L": 142.0, "B": 19.0, "T": 6.15, "cb": 0.506, "disp_t": 8630.0,
     "Vs_ms": 15.0 * 0.5144, "i_e_deg": 32.0, "lcb_pct": -1.0,
     "note": "Fn≈0.21，军船型；Holtrop 对军船外推，误差带加大"},
    {"case_id": "panamax_tanker_14kn", "hull": "Panamax tanker (230m)",
     "L": 230.0, "B": 32.2, "T": 12.5, "cb": 0.82, "disp_t": 77000.0,
     "Vs_ms": 14.0 * 0.5144, "i_e_deg": 22.0, "lcb_pct": 1.8,
     "note": "Fn≈0.153，Holtrop 主适用区"},
]

ANALYTIC_CASES = [
    {"case_id": "ittc57_cf_re_1e6", "kind": "ittc57_cf", "re": 1.0e6, "tol_pct": 0.05},
    {"case_id": "ittc57_cf_re_1e7", "kind": "ittc57_cf", "re": 1.0e7, "tol_pct": 0.05},
    {"case_id": "ittc57_cf_re_1e8", "kind": "ittc57_cf", "re": 1.0e8, "tol_pct": 0.05},
    {"case_id": "ittc57_cf_re_1e9", "kind": "ittc57_cf", "re": 1.0e9, "tol_pct": 0.05},
    {"case_id": "ittc57_cf_re_kvlcc2", "kind": "ittc57_cf",
     "re": 320.0 * 15.0 * 0.5144 / 1.19e-6, "tol_pct": 0.05},
    {"case_id": "ca_ittc78_L100", "kind": "ca_ittc78", "L": 100.0, "tol_pct": 0.05},
    {"case_id": "ca_ittc78_L320", "kind": "ca_ittc78", "L": 320.0, "tol_pct": 0.05},
]


# ---------------------------------------------------------------------------
# 高精度独立求值（mpmath，40 位）
# ---------------------------------------------------------------------------

def _mp():
    try:
        from mpmath import mp
        mp.dps = 40
        return mp
    except Exception:  # pragma: no cover - mpmath 缺失时退化为 log10 双精度
        return None


def high_precision(kind: str, **kw) -> float:
    mp = _mp()
    if mp is None:
        if kind == "ittc57_cf":
            return 0.075 / (math.log10(kw["re"]) - 2.0) ** 2
        return (105.0 * (150e-6 / kw["L"]) ** (1.0 / 3.0) - 0.64) * 1e-3
    if kind == "ittc57_cf":
        re = mp.mpf(str(kw["re"]))
        return float(mp.mpf("0.075") / (mp.log10(re) - 2) ** 2)
    if kind == "ca_ittc78":
        L = mp.mpf(str(kw["L"]))
        return float((105 * (mp.mpf("0.00015") / L) ** (mp.mpf(1) / 3) - mp.mpf("0.64")) * mp.mpf("0.001"))
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# 语料构建
# ---------------------------------------------------------------------------

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _anchor(entry: Dict[str, Any]) -> str:
    payload = {k: v for k, v in entry.items() if k != "sha256"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def build() -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []

    for spec in ANALYTIC_CASES:
        ref = high_precision(spec["kind"], re=spec.get("re"), L=spec.get("L"))
        e = {"case_id": spec["case_id"], "class": "analytic",
             "quantity": spec["kind"], "inputs": {k: v for k, v in spec.items()
                                                  if k in ("re", "L")},
             "reference": {"value": ref}, "tolerance_pct": spec["tol_pct"],
             "source": "closed-form formula; reference evaluated with mpmath dps=40"}
        e["sha256"] = _anchor(e)
        entries.append(e)

    for spec in CASE_HULLS:
        r = indep.rt_holtrop(spec["L"], spec["B"], spec["T"], spec["cb"], spec["Vs_ms"],
                             disp_t=spec["disp_t"], ie=spec["i_e_deg"], lcb=spec["lcb_pct"])
        e = {"case_id": spec["case_id"], "class": "cross_method", "hull": spec["hull"],
             "inputs": {k: spec[k] for k in ("L", "B", "T", "cb", "disp_t",
                                             "Vs_ms", "i_e_deg", "lcb_pct")},
             "note": spec["note"],
             "reference": {"rt_n": r["rt"], "ct": r["ct"], "pe_w": r["pe"],
                           "one_plus_k1": r["one_k1"], "wetted_surface": r["s"],
                           "cf": r["cf"], "fn": r["fn"]},
             "tolerance_pct": 0.5,
             "source": ("Holtrop & Mennen (1982) + Holtrop (1984) formula set, "
                        "second independent implementation "
                        "tools/validate/holtrop_ref_independent.py (no code shared)")}
        e["sha256"] = _anchor(e)
        entries.append(e)

    corpus = {"corpus_id": "resistance-reference-v1",
              "version": 1,
              "units": {"rt_n": "N", "pe_w": "W"},
              "withheld": [
                  {"class": "experimental",
                   "why": "离线环境无文献/试验原文可查；不手抄数值以免污染基准",
                   "needed": "KVLCC2/KCS/DTMB5415 拖曳试验 R_T 或 ITTC 工作坊 measured C_T 表"
                             " + 实船试航 R_T；引入时须附 sha256 原文锚与容差"},
              ],
              "entries": entries}
    os.makedirs(os.path.dirname(CORPUS_PATH), exist_ok=True)
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return corpus


# ---------------------------------------------------------------------------
# 复核
# ---------------------------------------------------------------------------

def load() -> Dict[str, Any]:
    with open(CORPUS_PATH, encoding="utf-8") as f:
        return json.load(f)


def verify(persist: bool = True) -> Dict[str, Any]:
    corpus = load()
    results = []

    for e in corpus["entries"]:
        rec: Dict[str, Any] = {"case_id": e["case_id"], "class": e["class"]}

        # 1) 语料完整性（sha256 锚）
        rec["sha256_ok"] = (_anchor(e) == e["sha256"])
        if not rec["sha256_ok"]:
            rec["verdict"] = "CORPUS_CORRUPT"
            results.append(rec)
            continue

        # 2) 引擎值 + 独立实现值 + 历史实现值
        if e["class"] == "analytic":
            q = e["quantity"]
            if q == "ittc57_cf":
                eng = engine.ittc57_cf(e["inputs"]["re"])
            else:
                eng = engine.correlation_allowance_ca(e["inputs"]["L"])
            ref = e["reference"]["value"]
            rec.update({"quantity": q, "engine": eng, "reference": ref,
                        "err_pct": abs(eng - ref) / abs(ref) * 100.0})
        else:
            i = e["inputs"]
            eng = engine.compute_resistance(engine.ResistanceInput(
                length=i["L"], beam=i["B"], draft=i["T"], cb=i["cb"],
                speed_ms=i["Vs_ms"], displacement_t=i["disp_t"],
                i_e_deg=i["i_e_deg"], lcb_pct=i["lcb_pct"]))
            idp = indep.rt_holtrop(i["L"], i["B"], i["T"], i["cb"], i["Vs_ms"],
                                   disp_t=i["disp_t"], ie=i["i_e_deg"], lcb=i["lcb_pct"])
            lw = legacy.legacy_worktree(i["L"], i["B"], i["T"], i["cb"], i["Vs_ms"],
                                        rho=1025.0, nu=1.19e-6)
            ld = legacy.legacy_deployed(i["L"], i["B"], i["T"], i["cb"],
                                        i["Vs_ms"] / 0.5144)
            ref = e["reference"]["rt_n"]
            rec.update({
                "engine_rt_n": eng["rt"], "independent_rt_n": idp["rt"],
                "reference_rt_n": ref,
                "err_pct": abs(eng["rt"] - ref) / ref * 100.0,
                "indep_vs_ref_pct": abs(idp["rt"] - ref) / ref * 100.0,
                "legacy_worktree_rt_n": lw["rt"],
                "legacy_worktree_err_pct": abs(lw["rt"] - ref) / ref * 100.0,
                "legacy_deployed_rt_n": ld["rt"],
                "legacy_deployed_err_pct": abs(ld["rt"] - ref) / ref * 100.0,
                "engine_pe_mw": eng["pe"] / 1e6,
                "legacy_deployed_pe_mw": ld["pe"] / 1e6,
            })

        rec["tolerance_pct"] = e["tolerance_pct"]
        rec["verdict"] = ("PASS" if rec["err_pct"] <= e["tolerance_pct"] else "FAIL")
        results.append(rec)

    counted = [r for r in results if r.get("verdict") in ("PASS", "FAIL")]
    summary = {
        "corpus_id": corpus["corpus_id"],
        "n_entries": len(results),
        "n_counted": len(counted),
        "n_pass": sum(1 for r in counted if r["verdict"] == "PASS"),
        "n_fail": sum(1 for r in counted if r["verdict"] == "FAIL"),
        "withheld_classes": [w["class"] for w in corpus.get("withheld", [])],
        "max_err_pct": max((r["err_pct"] for r in counted), default=0.0),
    }
    out = {"summary": summary, "results": results}

    if persist:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, "reference_corpus_verdict.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return out


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "verify"
    if cmd == "build":
        c = build()
        print(f"built {CORPUS_PATH}: {len(c['entries'])} entries")
        return 0
    out = verify()
    s = out["summary"]
    print(json.dumps(s, ensure_ascii=False))
    for r in out["results"]:
        if r["class"] == "analytic":
            print(f"  [{r['verdict']:4s}] {r['case_id']:24s} err={r['err_pct']:.4f}% "
                  f"(tol {r['tolerance_pct']}%)")
        else:
            print(f"  [{r['verdict']:4s}] {r['case_id']:24s} "
                  f"engine={r['engine_rt_n']/1e3:8.1f} kN "
                  f"ref={r['reference_rt_n']/1e3:8.1f} kN err={r['err_pct']:.2f}% "
                  f"| legacy_deployed err={r['legacy_deployed_err_pct']:.1f}%")
    return 0 if s["n_fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))