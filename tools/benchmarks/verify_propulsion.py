#!/usr/bin/env python3
"""m1413 — propulsion verification-debt harness (CLI).

Two halves:

  A. anchors   — self-contained, literature-free judges (tools/benchmarks/propulsion_anchors.py)
                 exercised on closed-form/invariance grounds. Must be all-green.
  B. live      — if the ship calculation service is reachable, its propulsion
                 endpoints are audited against those judges (J1..J7 in
                 platform_propulsion_judge.py).  A missing service is recorded
                 as WITHHELD, never as a pass.

Outputs
  artifacts/m1413/propulsion_anchor_ledger.json   machine-readable ledger
  docs/asset/m1413/propulsion_anchors.png         report figure

Exit code 0 when the anchors pass AND the sweep bound holds (the anchor suite is
the deliverable that must be green); the live-platform verdict is reported, not
asserted, so a failing platform does not crash the harness.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import propulsion_anchors as pa  # noqa: E402
import platform_propulsion_judge as judge  # noqa: E402

CANDIDATE_ENDPOINTS = ("http://127.0.0.1:8001", "http://127.0.0.1:8002")

PROPULSION_CASE = {
    "length": 150.0, "beam": 22.0, "draft": 8.5, "displacement": 25000.0,
    "speed": 14.0, "cb": 0.78, "prop_diameter": 5.0, "prop_pitch": 4.5,
    "prop_rpm": 120.0, "num_blades": 4, "blade_area_ratio": 0.55,
}

# ------------------------------------------------------------------ service
def _post(base: str, path: str, body: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(base + "/api/v1" + path,
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        return {"_error": "HTTP %d" % exc.code}
    except (urllib.error.URLError, OSError) as exc:
        return {"_error": type(exc).__name__}


def probe_ship_service(timeout: float = 2.0) -> dict:
    """Honest reachability probe; WITHHELD when nothing answers."""
    out = {"probed": [], "status": "WITHHELD", "base": None, "detail": ""}
    for base in CANDIDATE_ENDPOINTS:
        host, port = base.split("//")[1].rsplit(":", 1)
        row = {"base": base, "tcp": False, "health": None}
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                row["tcp"] = True
        except OSError:
            out["probed"].append(row)
            continue
        try:
            with urllib.request.urlopen(base + "/health", timeout=timeout) as r:
                row["health"] = r.status
                if out["status"] != "AVAILABLE":
                    out["status"] = "AVAILABLE"
                    out["base"] = base
                    out["detail"] = "%s/health -> %d" % (base, r.status)
        except (urllib.error.URLError, OSError) as exc:
            row["health"] = "error: %s" % type(exc).__name__
        out["probed"].append(row)
    if out["status"] != "AVAILABLE":
        out["detail"] = ("ship service unreachable; propulsion absolute-value and "
                         "live-consistency audit WITHHELD (not a pass)")
    return out


# -------------------------------------------------------------- live audit
def audit_live(base: str) -> dict:
    """Exercise the propulsion endpoints and apply judges J1..J7."""
    rows, cases = [], []

    # -- J sweep at fixed geometry (also feeds J5 saturation detection)
    for J in (0.2, 0.4, 0.6, 0.8, 1.0):
        d = _post(base, "/propeller-design/wageningen-b",
                  {"J": J, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.55})
        if "_error" in d:
            continue
        r = {"J": J, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.55,
             "KT": d.get("KT"), "KQ": d.get("KQ"), "eta0": d.get("eta0"),
             "_sweep": "J"}
        rows.append(r)
        cases.append(judge.judge_open_water_case(
            "WB-J%.1f" % J, J, float(d.get("KT")), float(d.get("KQ")),
            float(d.get("eta0"))))

    # -- P/D, Z, Ae/Ao sweeps at J = 0.6 (structure checks J4)
    for pd in (0.8, 1.0, 1.2, 1.4):
        d = _post(base, "/propeller-design/wageningen-b",
                  {"J": 0.6, "P_D": pd, "Z": 4, "Ae_Ao": 0.55})
        if "_error" not in d:
            rows.append({"J": 0.6, "P_D": pd, "Z": 4, "Ae_Ao": 0.55,
                         "KT": d.get("KT"), "KQ": d.get("KQ"),
                         "eta0": d.get("eta0"), "_sweep": "PD"})
    for Z in (3, 5, 6):
        d = _post(base, "/propeller-design/wageningen-b",
                  {"J": 0.6, "P_D": 1.0, "Z": Z, "Ae_Ao": 0.55})
        if "_error" not in d:
            rows.append({"J": 0.6, "P_D": 1.0, "Z": Z, "Ae_Ao": 0.55,
                         "KT": d.get("KT"), "KQ": d.get("KQ"),
                         "eta0": d.get("eta0"), "_sweep": "Z"})
    for ae in (0.35, 0.85):
        d = _post(base, "/propeller-design/wageningen-b",
                  {"J": 0.6, "P_D": 1.0, "Z": 4, "Ae_Ao": ae})
        if "_error" not in d:
            rows.append({"J": 0.6, "P_D": 1.0, "Z": 4, "Ae_Ao": ae,
                         "KT": d.get("KT"), "KQ": d.get("KQ"),
                         "eta0": d.get("eta0"), "_sweep": "AEAO"})

    structure = judge.judge_b_series_structure(rows)

    # -- propulsion chain over speed
    chain_runs, chain = {}, None
    for sp in (10.0, 14.0, 20.0):
        body = dict(PROPULSION_CASE, speed=sp)
        d = _post(base, "/calculations/propulsion", body)
        if "_error" in d:
            continue
        chain_runs["%.0f" % sp] = d
        if chain is None:
            chain = judge.judge_propulsion_chain(d)
            chain["reference_speed"] = sp

    # -- displacement coupling
    pairs = []
    for disp in (25000.0, 250000.0):
        d = _post(base, "/calculations/propulsion",
                  dict(PROPULSION_CASE, displacement=disp))
        pairs.append(d)
    coupling = {"n_pairs": 0, "identical_pairs": 0, "J7_coupling_ok": False,
                "failures": [], "note": "not evaluated"}
    if len(pairs) == 2 and not any("_error" in p for p in pairs):
        a = {k: v for k, v in pairs[0].items() if k != "displacement"}
        b = {k: v for k, v in pairs[1].items() if k != "displacement"}
        coupling = judge.judge_displacement_coupling([{"a": a, "b": b}])

    # -- cavitation definitional cross-check
    cav = _post(base, "/propeller-design/cavitation",
                {"D": 0.3048, "P_D": 1.0, "Ae_Ao": 0.55, "Z": 4, "n": 10.0,
                 "Va": 2.54, "thrust": 252.0, "depth": 3.0})
    cav_cmp = {}
    if "_error" not in cav:
        mine = pa.cavitation_number(3.0, 2.54, 10.0, 0.3048)
        theirs = float(cav.get("cavitation_number"))
        cav_cmp = {
            "platform": theirs,
            "anchor_0.7R_definition": mine,
            "rel_error": abs(theirs - mine) / mine,
            "within_3pct": abs(theirs - mine) / mine <= 0.03,
            "platform_method": cav.get("method"),
        }

    verdict = None
    if cases and chain:
        verdict = judge.overall_verdict(cases, structure, chain, coupling)

    return {
        "b_series_rows": rows,
        "open_water_cases": cases,
        "structure": structure,
        "propulsion_chain": chain,
        "propulsion_chain_runs": chain_runs,
        "displacement_coupling": coupling,
        "cavitation_cross_check": cav_cmp,
        "verdict": verdict,
    }


# ------------------------------------------------------------------ figure
def make_figure(path: str, sweep: dict, live: dict | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(1, 3, figsize=(15.8, 4.7))

    # panel A — eta_o vs J : platform reported/implied vs momentum ceiling
    if live and live["open_water_cases"]:
        cs = sorted(live["open_water_cases"], key=lambda c: c["J"])
        Js = [c["J"] for c in cs]
        ax[0].plot(Js, [c["eta_ideal"] for c in cs], "--", color="#c0392b", lw=2,
                   label=r"momentum ceiling $\eta_{ideal}(C_T)$")
        ax[0].plot(Js, [c["eta_reported"] for c in cs], "o-", color="#1f5fa8", lw=2,
                   ms=6, label=r"platform reported $\eta_0$")
        ax[0].plot(Js, [c["eta_implied"] for c in cs], "s--", color="#e67e22", lw=1.6,
                   ms=5, label=r"implied by $J K_T/(2\pi K_Q)$")
        ax[0].axhline(1.0, color="#555", lw=1.0, ls=":")
        ax[0].annotate("unphysical $\\eta>1$", xy=(Js[-1], 1.0), xytext=(0.42, 1.28),
                       fontsize=8, color="#c0392b")
        ax[0].set_title("J1/J2/J3 @ B4-70 (P/D=1.0, Z=4, $A_E/A_O$=0.55)")
    else:
        ax[0].text(0.5, 0.5, "ship service WITHHELD", ha="center", va="center")
        ax[0].set_title("J1/J2/J3 (not available)")
    ax[0].set_xlabel(r"advance coefficient $J$")
    ax[0].set_ylabel(r"open-water efficiency $\eta_0$")
    ax[0].legend(fontsize=8, loc="upper left")
    ax[0].grid(alpha=0.3)

    # panel B — KQ/KT ratio across all B-series evaluations
    if live and live["b_series_rows"]:
        rows = live["b_series_rows"]
        ratios = [r["KQ"] / r["KT"] for r in rows]
        labels = ["%s|P/D%.1f|Z%d|AE%.2f" % (r["_sweep"], r["P_D"], r["Z"], r["Ae_Ao"])
                  for r in rows]
        ax[1].bar(range(len(ratios)), ratios, color="#8e44ad")
        ax[1].axhline(0.1, color="#c0392b", lw=1.2, ls="--")
        ax[1].set_xticks(range(len(labels)))
        ax[1].set_xticklabels(labels, rotation=90, fontsize=6)
        ax[1].set_ylim(0, 0.16)
        ax[1].set_ylabel(r"$K_Q/K_T$")
        ax[1].set_title("J4: $K_Q/K_T$ pinned at %.3f for every input"
                        % (sum(ratios) / len(ratios)))
    else:
        ax[1].text(0.5, 0.5, "no B-series data", ha="center", va="center")
        ax[1].set_title("J4 (not available)")
    ax[1].grid(alpha=0.3, axis="y")

    # panel C — propulsive coefficient vs ship speed
    if live and live["propulsion_chain_runs"]:
        runs = live["propulsion_chain_runs"]
        sp = sorted(float(k) for k in runs)
        ax[2].plot(sp, [float(runs["%.0f" % s]["propulsive_eff"]) for s in sp],
                   "o-", color="#1f5fa8", lw=2, ms=6, label=r"$\eta_D$ (platform)")
        ax[2].plot(sp, [float(runs["%.0f" % s]["open_water_eff"]) for s in sp],
                   "s--", color="#e67e22", lw=1.6, ms=5, label=r"$\eta_0$ (platform)")
        ax[2].axhline(1.0, color="#c0392b", lw=1.4, ls="--")
        ax[2].text(sp[0], 1.02, r"energy conservation limit $\eta\leq 1$",
                   fontsize=8, color="#c0392b")
        ax[2].set_title("J6/J7: $\\eta_D$ exceeds unity; $\\eta_H$ hardcoded 1.1")
    else:
        ax[2].text(0.5, 0.5, "no propulsion data", ha="center", va="center")
        ax[2].set_title("J6/J7 (not available)")
    ax[2].set_xlabel("ship speed [kn]")
    ax[2].set_ylabel(r"efficiency $\eta$")
    ax[2].legend(fontsize=8, loc="upper left")
    ax[2].grid(alpha=0.3)

    fig.suptitle("m1413 propulsion verification debt: momentum-theory judges vs live platform "
                 "(anchors %.0e worst rel. err)" % sweep["summary"]["worst_rel_error"],
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=135)
    plt.close(fig)


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/m1413")
    ap.add_argument("--fig", default="docs/asset/m1413/propulsion_anchors.png")
    ap.add_argument("--offline", action="store_true", help="skip live audit")
    args = ap.parse_args()

    rows = pa.run_anchors()
    summ = pa.summary(rows)
    sw = pa.sweep_admissibility()
    probe = probe_ship_service()
    live = None
    if probe["status"] == "AVAILABLE" and not args.offline:
        try:
            live = audit_live(probe["base"])
        except Exception as exc:               # never let the live half crash the harness
            live = {"_error": "%s: %s" % (type(exc).__name__, exc)}

    ledger = {
        "mission": 1413,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": "TensorPlatform",
        "discipline": "propulsion",
        "anchors": rows,
        "anchor_summary": summ,
        "admissibility_sweep": {k: v for k, v in sw.items() if k != "points"},
        "sweep_points": sw["points"],
        "ship_service_probe": probe,
        "live_audit": live,
        "evidence_grade": {
            "propeller.open_water": (live or {}).get("verdict", {}).get("evidence_grade", "WITHHELD"),
            "calculations.propulsion": (
                "MOCK" if live and live.get("propulsion_chain")
                and (not live["propulsion_chain"].get("eta_D_below_unity")
                     or not live["propulsion_chain"].get("eta_o_below_unity"))
                else ("REAL" if live else "WITHHELD")),
        },
    }
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "propulsion_anchor_ledger.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, ensure_ascii=False)
    make_figure(args.fig, {"summary": summ}, live)

    print("A. anchors        : %d/%d pass, worst rel err %.3e (<3%%: %s)"
          % (summ["n_pass"], summ["n_anchors"], summ["worst_rel_error"], summ["within_3pct"]))
    print("   sweep bound    : %s (min margin %.2f%%, peak eta_o %.4f @ J=%.2f)"
          % (sw["all_bound_ok"], sw["min_margin"] * 100, sw["peak"]["eta_o"], sw["peak"]["J"]))
    print("B. ship service   : %s (%s)" % (probe["status"], probe["detail"]))
    if live and "verdict" in live and live["verdict"]:
        v = live["verdict"]
        print("   open-water grade: %s (claimed %s), %d failures"
              % (v["evidence_grade"], v["claimed_grade"], v["n_failures"]))
        ch = live.get("propulsion_chain") or {}
        if ch:
            print("   chain: eta_o=%.3f eta_H=%.2f eta_D=%.3f  eta_D<1: %s"
                  % (ch["eta_o"], ch["eta_H"], ch["eta_D"], ch["eta_D_below_unity"]))
        cp = live.get("displacement_coupling") or {}
        if cp.get("n_pairs"):
            print("   displacement coupling ok: %s" % cp["J7_coupling_ok"])
        cc = live.get("cavitation_cross_check") or {}
        if cc:
            print("   sigma_n: platform %.3f vs anchor %.3f (%.1f%% off)"
                  % (cc["platform"], cc["anchor_0.7R_definition"], cc["rel_error"] * 100))
    print("ledger :", out)
    print("figure :", args.fig)

    ok = summ["all_pass"] and sw["all_bound_ok"] and sw["all_eta_below_unity"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())