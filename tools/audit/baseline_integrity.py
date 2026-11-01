#!/usr/bin/env python3
"""Benchmark baseline integrity audit (verification-debt tool).

Purpose
-------
Every accuracy claim on this platform is of the form "computed X vs reference Y,
error < tol".  That chain is only as trustworthy as the *reference set* itself.
This tool audits the reference set **before** any solver output is compared to it,
using identities that must hold for any physically consistent baseline:

C1  Ct <-> Rt identity      Ct = Rt / (0.5 * rho * S * V^2)
C2  Froude identity         Fr = V / sqrt(g * L)
C3  unit coherence          V[m/s] == V[kn] * 0.514444
C4  physicality             Ct >= (1+k_min) * Cf_ITTC57(Re)   (friction lower bound)
C5  cross-store agreement   BENCHMARK_REFERENCE (services/ship_proxy.py) vs dataset
C6  gate policy             tolerance_pct <= POLICY_MAX_TOLERANCE_PCT (a gate that
                            cannot fail is not a gate)

Every check is dimensionless-ratio based, so a *correct* baseline passes regardless
of model/full scale, and a corrupt one fails loudly.  Exit code is non-zero if any
check fails -- this tool is meant to be usable as a CI gate (PB-005: gates must be
able to fail).

Usage
-----
    python3 tools/audit/baseline_integrity.py            # human readable
    python3 tools/audit/baseline_integrity.py --json out.json --markdown out.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.81
KN_TO_MS = 0.514444
POLICY_MAX_TOLERANCE_PCT = 15.0  # platform精度标准: >15% 误差不可接受
# Identity checks are exact up to the reference set's own rounding; the reported
# per-case numbers are quoted to 2-4 significant figures.
IDENTITY_TOL_PCT = 2.0
FROUDE_TOL_PCT = 1.0
UNITS_TOL_PCT = 0.5
# Van Oossanen / ITTC form factor lower bound for slender to full hulls.
MIN_FORM_FACTOR = 1.03
# Kinematic viscosity used for the friction lower bound (fresh water 15 C for
# model-basin cases, sea water 15 C for full-scale cases).
NU_FRESH = 1.139e-6
NU_SEAWATER = 1.189e-6

DATASET = Path(__file__).resolve().parents[2] / "backend" / "data" / "benchmark_reference.json"


# --------------------------------------------------------------------------- #
# physics helpers
# --------------------------------------------------------------------------- #
def cf_ittc57(re: float) -> float:
    """ITTC-57 friction line Cf = 0.075 / (log10(Re) - 2)^2."""
    if re <= 0:
        return float("nan")
    return 0.075 / ((math.log10(re) - 2.0) ** 2)


def froude(v_ms: float, length_m: float) -> float:
    return v_ms / math.sqrt(G * length_m)


def holtrop_wetted_surface(length: float, beam: float, draft: float, cb: float,
                           cm: float = 0.82) -> float:
    """Holtrop (1982) wetted-surface estimate, m^2.

    Used only when the baseline record carries no measured wetted surface; the
    results of C1 are then flagged ``indicative`` (estimate accuracy ~2-5%).
    """
    cwp = (1.0 + 2.0 * cb) / 3.0
    return length * (2.0 * draft + beam) * math.sqrt(cm) * (
        0.453
        + 0.4425 * cb
        - 0.2862 * cm
        - 0.003467 * beam / draft
        + 0.3696 * cwp
    )


def ct_from_rt(rt_n: float, rho: float, s_m2: float, v_ms: float) -> float:
    return rt_n / (0.5 * rho * s_m2 * v_ms * v_ms)


def rho_for(ship_type: str) -> float:
    return 1025.0 if ship_type.upper().startswith("KVLCC") else 1000.0


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def _rel_pct(value: float, ref: float) -> float:
    if ref == 0:
        return float("inf")
    return abs(value - ref) / abs(ref) * 100.0


def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run C1..C4 on one baseline record."""
    cid = case["case_id"]
    st = case["ship_type"]
    geom = case.get("geometry", {}) or {}
    v_ms = float(case.get("speed_ms") or 0.0)
    ctn = float(case.get("experimental_ct") or 0.0)
    rt = case.get("experimental_rt_n")
    length = geom.get("length_m")
    beam = geom.get("beam_m")
    draft = geom.get("draft_m")
    cb = geom.get("cb")
    rho = float(case.get("fluid_density_kg_m3") or rho_for(st))

    out: Dict[str, Any] = {"case_id": cid, "ship_type": st, "check": "C_case", "speed_ms": v_ms,
                           "fluid_density_kg_m3": rho, "checks": {}}

    # --- wetted surface (measured, or Holtrop estimate flagged indicative) ---
    s_meas = geom.get("wetted_surface_m2")
    if s_meas:
        s_m2, s_src, indicative = float(s_meas), "measured", False
    elif all(x is not None for x in (length, beam, draft, cb)):
        s_m2, s_src, indicative = (
            holtrop_wetted_surface(length, beam, draft, cb), "holtrop_estimate", True)
    else:
        s_m2, s_src, indicative = None, "unavailable", True
    out["wetted_surface_m2"] = round(s_m2, 2) if s_m2 else None
    out["wetted_surface_source"] = s_src

    # --- C1 Ct <-> Rt identity -------------------------------------------- #
    if s_m2 and rt is not None and v_ms > 0:
        ct_rt = ct_from_rt(float(rt), rho, s_m2, v_ms)
        d = _rel_pct(ct_rt, ctn) if ctn else float("inf")
        ratio = ct_rt / ctn if ctn else float("inf")
        out["checks"]["C1_ct_rt"] = {
            "status": "PASS" if d <= IDENTITY_TOL_PCT else "FAIL",
            "ct_declared": ctn, "ct_from_rt": round(ct_rt, 8),
            "deviation_pct": round(d, 2), "ratio": round(ratio, 4),
            "rt_implied_by_ct_n": round(0.5 * rho * s_m2 * v_ms * v_ms * ctn, 2),
            "indicative": indicative,
        }
    else:
        out["checks"]["C1_ct_rt"] = {"status": "WITHHELD",
                                     "reason": "missing wetted surface or Rt or speed"}

    # --- C2 Froude identity ------------------------------------------------ #
    fr_decl = case.get("froude_number")
    if fr_decl and length:
        fr_calc = froude(v_ms, float(length))
        d = _rel_pct(fr_calc, float(fr_decl))
        # back-solve the length the author implicitly used -> gives the reader a
        # concrete root cause (e.g. "L=181.2 m instead of 5.72 m")
        implied_l = (v_ms / float(fr_decl)) ** 2 / G if fr_decl else None
        out["checks"]["C2_froude"] = {
            "status": "PASS" if d <= FROUDE_TOL_PCT else "FAIL",
            "declared": float(fr_decl), "computed": round(fr_calc, 4),
            "deviation_pct": round(d, 2),
            "implied_length_m": round(implied_l, 2) if implied_l else None,
            "recorded_length_m": float(length),
        }
    else:
        out["checks"]["C2_froude"] = {"status": "WITHHELD", "reason": "no Fr or L"}

    # --- C3 unit coherence ------------------------------------------------- #
    # 目的：抓**未声明的**单位混用（实船节数当模型 m/s 之类）。若记录里显式
    # 说明了两个速度各自的尺度基准（``speed_basis``/``speed_basis_kn`` 或
    # ``legacy_values``），该偏差是被记录、可复核的，判 WARN 并回显依据；
    # 没有任何说明的偏差才判 FAIL。
    v_kn = case.get("speed_kn")
    if v_kn and v_ms:
        d = _rel_pct(v_ms, float(v_kn) * KN_TO_MS)
        basis = " ".join(
            str(case.get(k, "") or "") for k in
            ("speed_basis", "speed_kn_basis", "speed_basis_kn", "legacy_values")
        ).strip()
        if d <= UNITS_TOL_PCT:
            out["checks"]["C3_units"] = {
                "status": "PASS",
                "deviation_pct": round(d, 3),
            }
        elif basis:
            out["checks"]["C3_units"] = {
                "status": "WARN",
                "deviation_pct": round(d, 3),
                "reason": "declared scale mix: " + basis[:200],
            }
        else:
            out["checks"]["C3_units"] = {
                "status": "FAIL",
                "deviation_pct": round(d, 3),
                "reason": "undocumented kn <-> m/s incoherence",
            }
    else:
        out["checks"]["C3_units"] = {"status": "WITHHELD", "reason": "kn speed absent"}

    # --- C4 physicality: Ct must exceed the friction lower bound ----------- #
    if ctn and length and v_ms > 0:
        nu = NU_SEAWATER if rho > 1010 else NU_FRESH
        re = v_ms * float(length) / nu
        cf = cf_ittc57(re)
        lb = MIN_FORM_FACTOR * cf
        out["checks"]["C4_physicality"] = {
            "status": "PASS" if ctn >= lb else "FAIL",
            "reynolds": f"{re:.3e}", "cf_ittc57": round(cf, 6),
            "friction_lower_bound_ct": round(lb, 6),
            "ct_over_cf": round(ctn / cf, 3),
        }
    else:
        out["checks"]["C4_physicality"] = {"status": "WITHHELD", "reason": "insufficient data"}

    # per-case verdict
    verdicts = [c["status"] for c in out["checks"].values()]
    out["verdict"] = "FAIL" if "FAIL" in verdicts else (
        "PASS" if all(v == "PASS" for v in verdicts) else "PARTIAL")
    return out


def audit_cross_store(dataset: Dict[str, Any],
                      reference: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """C5: does the gate store (ship_proxy.BENCHMARK_REFERENCE) agree with the dataset?"""
    if reference is None:
        try:
            sys.path.insert(0, str(DATASET.parents[1]))
            from services.ship_proxy import BENCHMARK_REFERENCE  # type: ignore
            reference = BENCHMARK_REFERENCE
        except Exception as exc:  # pragma: no cover - import guard
            return [{"check": "C5_cross_store", "status": "WITHHELD", "reason": str(exc)}]

    by_type = {}
    for c in dataset.get("cases", []):
        by_type.setdefault(c["ship_type"], []).append(c)

    rows: List[Dict[str, Any]] = []
    for case_name, ref in (reference.get("resistance") or {}).items():
        ship_type = ref.get("ship_type") or case_name.upper()
        cands = by_type.get(ship_type) or []
        if not cands:
            rows.append({"check": "C5_cross_store", "case": case_name,
                         "status": "WITHHELD", "reason": "no dataset block for ship type"})
            continue
        ref_speed_ms = float(ref.get("speed_ms") or ref["params"]["speed"])
        ds = min(cands, key=lambda c: abs(float(c.get("speed_ms", 0)) - ref_speed_ms))
        deriv = (ds.get("checks", {}).get("C1_ct_rt", {}).get("rt_implied_by_ct_n")
                 if isinstance(ds.get("checks"), dict) else None)
        if deriv is None:  # raw dataset row -> derive here
            geom = ds["geometry"]
            s = geom.get("wetted_surface_m2") or holtrop_wetted_surface(
                geom["length_m"], geom["beam_m"], geom["draft_m"], geom["cb"])
            deriv = 0.5 * rho_for(ds["ship_type"]) * s * ref_speed_ms ** 2 \
                * float(ds["experimental_ct"])
        gate_rt = float(ref["reference_rt"])
        d = _rel_pct(gate_rt, float(deriv))
        rows.append({
            "check": "C5_cross_store", "case": case_name, "dataset_case": ds["case_id"],
            "status": "PASS" if d <= POLICY_MAX_TOLERANCE_PCT else "FAIL",
            "gate_reference_rt_n": gate_rt,
            "dataset_ct_implied_rt_n": round(float(deriv), 2),
            "deviation_pct": round(d, 2),
        })
    return rows


def audit_tolerances(reference: Dict[str, Any]) -> List[Dict[str, Any]]:
    """C6: a gate whose tolerance exceeds the platform standard cannot fail."""
    rows = []
    for calc_type, cases in reference.items():
        if not isinstance(cases, dict):
            continue
        for name, spec in cases.items():
            if not isinstance(spec, dict) or "tolerance_pct" not in spec:
                continue
            tol = float(spec["tolerance_pct"])
            enabled = bool(spec.get("gate_enabled", True))
            if not enabled:
                # 显式声明「不作门禁」的算例：宽容差是历史记录，不是判决依据。
                # 消费侧必须输出 WITHHELD（backend/services/ship_proxy.py）。
                status, reason = "WARN", "declared non-gate (honest-FAIL): verdict withheld at consumption"
            elif tol <= POLICY_MAX_TOLERANCE_PCT:
                status, reason = "PASS", ""
            else:
                status, reason = "FAIL", "tolerance above policy and the gate is enabled"
            rows.append({
                "check": "C6_gate_policy", "case": f"{calc_type}/{name}",
                "tolerance_pct": tol,
                "status": status,
                "gate_enabled": enabled,
                "reason": reason,
            })
    return rows


def run(dataset_path: Path = DATASET) -> Dict[str, Any]:
    dataset = json.loads(Path(dataset_path).read_text())
    cases = [audit_case(c) for c in dataset.get("cases", [])]
    cross = audit_cross_store(dataset)
    try:
        sys.path.insert(0, str(DATASET.parents[1]))
        from services.ship_proxy import BENCHMARK_REFERENCE  # type: ignore
        tolerances = audit_tolerances(BENCHMARK_REFERENCE)
    except Exception as exc:  # pragma: no cover
        tolerances = [{"check": "C6_gate_policy", "status": "WITHHELD", "reason": str(exc)}]

    all_rows = cases + cross + tolerances
    fails = [r for r in all_rows
             if r.get("status") == "FAIL" or r.get("verdict") == "FAIL"]
    partial = [r for r in all_rows if r.get("verdict") == "PARTIAL"]
    return {
        "dataset": str(dataset_path),
        "policy": {"max_tolerance_pct": POLICY_MAX_TOLERANCE_PCT,
                   "identity_tol_pct": IDENTITY_TOL_PCT,
                   "froude_tol_pct": FROUDE_TOL_PCT},
        "summary": {
            "cases": len(cases),
            "fail": len(fails),
            "partial": len(partial),
            "pass": len([r for r in all_rows if r.get("status") == "PASS"]),
            "verdict": "FAIL" if fails else "PASS",
            "failing_checks": sorted({r.get("check", "C_case") for r in fails}),
        },
        "case_results": cases,
        "cross_store": cross,
        "gate_policy": tolerances,
    }


def to_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Benchmark baseline integrity audit",
        "",
        f"- dataset: `{report['dataset']}`",
        f"- policy: max tolerance {report['policy']['max_tolerance_pct']}% "
        f"(platform精度标准), identity {report['policy']['identity_tol_pct']}%, "
        f"Froude {report['policy']['froude_tol_pct']}%",
        f"- **verdict: {s['verdict']}** — cases {s['cases']}, fail {s['fail']}, "
        f"partial {s['partial']}, pass {s['pass']}",
        f"- failing checks: {', '.join(s['failing_checks']) or 'none'}",
        "",
        "## Per-case identity checks",
        "",
        "| case | C1 Ct↔Rt | C1 ratio | C2 Fr | Fr implied L (m) | C3 units | C4 physicality | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["case_results"]:
        c = r["checks"]
        c1 = c["C1_ct_rt"]
        c2 = c["C2_froude"]
        lines.append(
            "| {cid} | {c1} | {ratio} | {c2} | {il} | {c3} | {c4} | {v} |".format(
                cid=r["case_id"],
                c1=c1.get("status"),
                ratio=("{:.3f}".format(c1["ratio"]) + ("*" if c1.get("indicative") else ""))
                if "ratio" in c1 else "—",
                c2=c2.get("status"),
                il=c2.get("implied_length_m", "—"),
                c3=c["C3_units"].get("status"),
                c4=c["C4_physicality"].get("status"),
                v=r.get("verdict"),
            ))
    lines += ["", "`*` wetted surface from Holtrop estimate (indicative).", "",
              "## Cross-store agreement (C5)", "",
              "| case | dataset case | gate R_T (N) | Ct-implied R_T (N) | dev % | status |",
              "|---|---|---|---|---|---|"]
    for r in report["cross_store"]:
        lines.append("| {case} | {ds} | {g} | {d} | {p} | {s} |".format(
            case=r.get("case", "—"), ds=r.get("dataset_case", "—"),
            g=r.get("gate_reference_rt_n", "—"), d=r.get("dataset_ct_implied_rt_n", "—"),
            p=r.get("deviation_pct", "—"), s=r.get("status")))
    lines += ["", "## Gate policy (C6)", "",
              "| case | tolerance % | gate enabled | status |", "|---|---|---|---|"]
    for r in report["gate_policy"]:
        lines.append("| {c} | {t} | {g} | {s} |".format(
            c=r.get("case", "—"), t=r.get("tolerance_pct", "—"),
            g=r.get("gate_enabled", "—"), s=r.get("status")))
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--markdown", dest="md_out")
    args = ap.parse_args(argv)

    report = run(Path(args.dataset))
    md = to_markdown(report)
    print(md)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if args.md_out:
        Path(args.md_out).write_text(md)
    return 0 if report["summary"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())