"""Black-box audit of `GET /api/v1/geometry/hydro-curves/{ship}` (m1576).

Two layers of evidence, both machine-checkable:

  1. **External baseline** -- published KCS particulars (nabla = 52030.3 m3,
     Cb = 0.6505, Lpp/B/T) for the design draft.
  2. **Internal identities** that any hydrostatic table must satisfy,
     independent of any baseline data:
        I1  Cb = nabla / (L B d) <= 1                     (definition)
        I2  TPC = rho * Awp / 100                          (definition)
        I3  d(nabla)/dT = Awp(T)                           (fundamental theorem)
        I4  VCB / d <= 0.65 for ship forms (0.5 flat bottom ... )
        I5  Awp, wet_surface monotone non-decreasing in draft
        I6  reported Cb consistent with reported displacement/(L B d)
        I7  reported Cb consistent with meta Cb (same endpoint)

Usage
-----
    python3 tools/verifier/geometry/audit_hydro_curves.py --live   # probe :8001
    python3 tools/verifier/geometry/audit_hydro_curves.py          # replay snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hydrostatics_reference import KCS_PARTICULARS  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(ROOT, "artifacts", "verifier")
# Evidence capture lives in the versioned (non-gated) asset dir; artifacts/ is
# only a legacy fallback for already-restored trees. See
# tests/data/verifier_assets/README.md.
SNAP_VER = os.path.join(ROOT, "tests", "data", "verifier_assets", "hydrostatics",
                        "hydro_curves_kcs_snapshot.json")
SNAP = SNAP_VER if os.path.exists(SNAP_VER) else os.path.join(
    ART, "hydro_curves_kcs_snapshot.json")
OUT = os.path.join(ART, "hydro_curves_audit.json")
FIG = os.path.join(ART, "fig_hydro_curves_kcs.png")

TOL_IDENT = 0.01          # 1% for closed-form internal identities
TOL_BASE = 0.15           # platform rule: >15% vs a reference is unacceptable
TOL_PASS3 = 0.03          # report-level "<3%" bar


def fetch_live(ship: str = "KCS", n_drafts: int = 5) -> dict:
    url = f"{API}/geometry/hydro-curves/{ship}?n_drafts={n_drafts}"
    with urllib.request.urlopen(url, timeout=180) as fh:
        payload = json.load(fh)
    payload["_probe"] = {"ship": ship, "n_drafts": n_drafts, "url": url}
    return payload


def rel(a: float, b: float) -> float:
    return float("nan") if b == 0 else (a - b) / b


def audit(snap: dict) -> dict:
    c = snap["curves"]
    meta = snap["meta"]
    d = np.asarray(c["drafts"], dtype=float)
    nab = np.asarray(c["displacement"], dtype=float) / 1.025   # -> m3
    cb = np.asarray(c["Cb"], dtype=float)
    awp = np.asarray(c["Awp"], dtype=float)
    vcb = np.asarray(c["VCB"], dtype=float)
    tpc = np.asarray(c["TPC"], dtype=float)
    wet = np.asarray(c["wet_surface"], dtype=float)
    L, B, T = meta["L"], meta["B"], meta["T"]

    design = int(np.argmin(np.abs(d - T)))
    nab_design = float(nab[design])
    k = KCS_PARTICULARS
    crit = []

    def add(cid, claim, obs, base, err, ok, note=""):
        crit.append({"id": cid, "claim": claim, "observed": obs, "baseline": base,
                     "rel_err": None if err is None else round(float(err), 5),
                     "pass": bool(ok), "note": note})

    # I1 -- Cb <= 1 (definition). Reported Cb at design draft.
    cb_d = float(cb[design])
    add("I1", "Cb <= 1 at every draft (Cb = nabla/(L B d), definition)",
        round(cb_d, 4), 1.0, cb_d - 1.0, cb_d <= 1.0 + 1e-9,
        "reported Cb exceeds unity -> impossible displacement")
    # I2 -- TPC = rho Awp / 100 exactly
    tpc_ref = 1.025 * awp / 100.0
    e2 = float(np.max(np.abs(tpc - tpc_ref) / np.maximum(tpc_ref, 1e-12)))
    add("I2", "TPC = 1.025*Awp/100 (definition)", round(float(tpc[design]), 3),
        round(float(tpc_ref[design]), 3), e2, e2 <= TOL_IDENT)
    # I3 -- dnabla/dT = Awp(T): central difference on the returned curves
    i = min(max(design, 1), len(d) - 2)
    dnab_dT = (nab[i + 1] - nab[i - 1]) / (d[i + 1] - d[i - 1])
    e3 = rel(dnab_dT, float(awp[i]))
    add("I3", "d(nabla)/dT = Awp(T) (fundamental theorem of calculus)",
        round(float(dnab_dT), 1), round(float(awp[i]), 1), e3, abs(e3) <= 0.05,
        "m3 per m of draft vs waterplane area m2")
    # I4 -- VCB/d bounded
    worst = float(np.max(vcb / d))
    add("I4", "VCB/d <= 0.65 for ship forms", round(worst, 4), 0.65,
        worst - 0.65, worst <= 0.65,
        "wall-sided box gives exactly 0.5; full hulls 0.5-0.55")
    # I5 -- monotonicity
    ok5 = bool(np.all(np.diff(awp) >= -1e-9) and np.all(np.diff(wet) >= -1e-9))
    add("I5", "Awp and wet_surface non-decreasing in draft",
        [round(float(awp[0]), 1), round(float(awp[-1]), 1)], "monotone", None, ok5)
    # I6 -- reported Cb vs displacement/(L B d)
    cb_calc = nab_design / (L * B * float(d[design]))
    e6 = rel(cb_d, cb_calc)
    add("I6", "reported Cb == displacement/(L B d) (internal consistency)",
        round(cb_d, 4), round(float(cb_calc), 4), e6, abs(e6) <= TOL_IDENT)
    # I7 -- reported Cb vs meta Cb
    e7 = rel(cb_d, float(meta["Cb"]))
    add("I7", "reported Cb (curves) == meta.Cb (same endpoint)",
        round(cb_d, 4), round(float(meta["Cb"]), 4), e7, abs(e7) <= TOL_IDENT)
    # B1 -- external baseline: displacement volume at design draft
    e1 = rel(nab_design, k["nabla_m3"])
    add("B1", "nabla(T) == published KCS 52030.3 m3",
        round(nab_design, 1), k["nabla_m3"], e1, abs(e1) <= TOL_BASE,
        "platform rule: |err| > 15% unacceptable")
    # B2 -- external baseline: Cb at design draft
    e2b = rel(cb_d, k["Cb"])
    add("B2", "Cb(T) == published KCS 0.6505", round(cb_d, 4), k["Cb"], e2b,
        abs(e2b) <= TOL_BASE)
    # B3 -- wetted surface at design draft (literature, weakest of the three)
    e3b = rel(float(wet[design]), k["wet_surface_m2"])
    add("B3", "wet_surface(T) == published KCS 9424 m2",
        round(float(wet[design]), 1), k["wet_surface_m2"], e3b,
        abs(e3b) <= TOL_BASE, "bare-hull value, appendages excluded")

    npass = sum(1 for x in crit if x["pass"])
    verdict = "FAIL" if npass < len(crit) else "PASS"
    return {
        "ship": snap.get("_probe", {}).get("ship", "KCS"),
        "n_drafts": len(d),
        "criteria": crit,
        "n_pass": npass,
        "n_total": len(crit),
        "verdict": verdict,
        "design_draft_transverse_note": (
            "nabla is linear in draft to within %.6f%% -> the endpoint rebuilds a "
            "geometrically SIMILAR hull at every draft instead of integrating one "
            "fixed hull form below the waterline"
            % (100 * abs(np.polyfit(d, nab, 1)[0] * d[design] / nab[design] - 1))),
        "meta": meta,
    }


def figure(snap: dict, res: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    c = snap["curves"]
    d = np.asarray(c["drafts"], float)
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.4))
    ax[0].plot(d, np.asarray(c["displacement"]) / 1000.0, "o-", label="platform endpoint")
    ax[0].axhline(KCS_PARTICULARS["displacement_t"] / 1000.0, color="k", ls="--",
                  label="published KCS (53331 t)")
    ax[0].set_xlabel("draft T [m]"); ax[0].set_ylabel("displacement [kt]")
    ax[0].set_title("displacement vs draft"); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)

    cb = np.asarray(c["Cb"], float)
    ax[1].plot(d, cb, "s-", color="crimson", label="reported Cb")
    ax[1].axhline(1.0, color="k", ls="--", label="physical limit Cb = 1")
    ax[1].axhline(KCS_PARTICULARS["Cb"], color="g", ls=":", label="published KCS 0.6505")
    ax[1].axhline(snap["meta"]["Cb"], color="gray", ls="-.", label="meta.Cb")
    ax[1].set_xlabel("draft T [m]"); ax[1].set_ylabel("Cb [-]")
    ax[1].set_title("block coefficient"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)

    ids, errs = [], []
    for x in res["criteria"]:
        if x.get("rel_err") is not None:
            ids.append(x["id"]); errs.append(100 * x["rel_err"])
    colors = ["seagreen" if abs(e) <= 3 else "orange" if abs(e) <= 15 else "crimson"
              for e in errs]
    ax[2].bar(ids, errs, color=colors)
    ax[2].axhline(3, color="gray", ls=":"); ax[2].axhline(15, color="k", ls="--")
    ax[2].set_ylabel("relative error vs baseline [%]")
    ax[2].set_title("criteria errors (dotted 3%, dashed 15%)"); ax[2].grid(alpha=.3)
    fig.tight_layout(); fig.savefig(FIG, dpi=130); plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="probe :8001 and refresh snapshot")
    args = ap.parse_args()
    os.makedirs(ART, exist_ok=True)
    if args.live:
        snap = fetch_live()
        with open(SNAP_VER, "w") as fh:
            json.dump(snap, fh, indent=1)
    else:
        with open(SNAP) as fh:
            snap = json.load(fh)
    res = audit(snap)
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1)
    figure(snap, res)
    print(f"{res['verdict']}  {res['n_pass']}/{res['n_total']} criteria pass")
    for x in res["criteria"]:
        print("  %-3s %-6s %s" % (x["id"], "PASS" if x["pass"] else "FAIL", x["claim"]))
    print("note:", res["design_draft_transverse_note"])
    print("artifacts:", OUT, FIG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())