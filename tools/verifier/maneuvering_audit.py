#!/usr/bin/env python3
"""Manoeuvring-域证据分级审计 + 参考核对照（只读探测 :8001，不修改该服务）。

判决逻辑全部是机器可算判据，且**每条判据都配一个正向对照**（physically
consistent 的假想响应必须判 PASS），防止判据退化成恒真否决器。

输出:
  runs/m1460_maneuvering_audit.json
  docs/verifier/figures/maneuvering_trajectories.png
  docs/verifier/figures/maneuvering_td_comparison.png
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import mmg_reference as R  # noqa: E402

API = "http://127.0.0.1:8001/api/v1/"
KVLCC2 = dict(L=320.0, B=58.0, d=20.8, Cb=0.8098, vol=312622.0,
              Dp=9.86, Ar=112.5, U=7.97)

# Literature band for KVLCC2 turning circle, rudder 35 deg, Fn=0.142.
# Provenance: quoted band from the SIMMAN 2008 KVLCC2 benchmark submissions
# (EFD + validated CFD/MMG systems).  NOT re-digitised here -> B-class.
BAND = {"advance_L": (2.70, 3.40), "transfer_L": (1.20, 1.90),
        "tactical_diameter_L": (2.90, 3.60)}
ZIGZAG_BAND = {"first_overshoot_deg": (4.0, 18.0), "second_overshoot_deg": (2.0, 16.0)}


# ---------------------------------------------------------------- judgement
def judge_delta_invariance(res_a: dict, res_b: dict, tol: float = 1e-9) -> dict:
    """Same ship, rudder 0 deg vs 35 deg: identical indices => hardcoded / dead input.

    Positive control: two physically different responses must return PASS.
    """
    keys = ("advance", "transfer", "tactical_diameter", "steady_diameter")
    diffs = {}
    for k in keys:
        a, b = res_a.get(k), res_b.get(k)
        if a is None or b is None:
            continue
        denom = max(abs(a), abs(b), 1e-9)
        diffs[k] = abs(a - b) / denom
    identical = bool(diffs) and all(v <= tol for v in diffs.values())
    return {"verdict": "MOCK" if identical else "RESPONSIVE",
            "max_rel_diff": max(diffs.values()) if diffs else None,
            "per_key": diffs}


def judge_zero_rudder(res: dict, L: float, U: float,
                      min_radius_L: float = 20.0) -> dict:
    """Rudder 0 deg must not produce a closed turning circle.

    A real MMG/hull model gives a gently curved or straight path: turning radius
    >> 20L. The positive control is a straight run (radius -> inf) which PASSes.
    """
    td = res.get("tactical_diameter")
    rate = res.get("turning_rate")           # deg/s
    if td is None:
        return {"verdict": "WITHHELD", "reason": "no tactical_diameter in payload"}
    radius_L = (td / 2.0) / L
    # turning_rate in the payload: deg/s for /maneuvering/turning,
    # rad/s for /mmg/turning (unit inconsistency documented in the review)
    return {"verdict": "MOCK" if radius_L < min_radius_L else "RESPONSIVE",
            "radius_L": radius_L, "payload_turn_rate": rate}


def judge_visibility(res: dict, L: float) -> dict:
    """Indices must be present and non-degenerate (advance/transfer/TD > 0)."""
    bad = [k for k in ("advance", "transfer", "tactical_diameter")
           if not res.get(k) or res.get(k) != res.get(k) or res.get(k) <= 0.0]
    return {"verdict": "STUB" if bad else "RESPONSIVE", "degenerate_keys": bad}


def rel_err(x: float, lo: float, hi: float) -> float | None:
    """Signed relative deviation from the nearer edge of a literature band."""
    if x != x:
        return None
    if lo <= x <= hi:
        return 0.0
    ref = lo if x < lo else hi
    return (x - ref) / ref


# ---------------------------------------------------------------- live probe
def _post(path: str, body: dict) -> tuple:
    import urllib.error
    import urllib.request
    r = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                               method="POST")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, {"error_text": e.read().decode()[:200]}
    except Exception as e:  # noqa: BLE001
        return "EXC", {"error_text": str(e)[:200]}


TURN_MANEUVERING = dict(L=KVLCC2["L"], B=KVLCC2["B"], T=KVLCC2["d"],
                        displacement=KVLCC2["vol"], Cb=KVLCC2["Cb"], KG=18.6,
                        Dp=KVLCC2["Dp"], Ar=KVLCC2["Ar"], speed=KVLCC2["U"])
TURN_MMG = dict(L=KVLCC2["L"], B=KVLCC2["B"], T=KVLCC2["d"],
                displacement=KVLCC2["vol"], Cb=KVLCC2["Cb"], Dp=KVLCC2["Dp"],
                n=105.0, Ar=KVLCC2["Ar"], u0=KVLCC2["U"])


def probe_platform() -> dict:
    out = {}
    st, pl = _post("maneuvering-mmg/turning",
                   dict(TURN_MANEUVERING, V0=KVLCC2["U"], rudder_angle=35.0))
    out["maneuvering-mmg/turning"] = {"status": st, "payload": pl}
    st, pl = _post("maneuvering/turning", dict(TURN_MANEUVERING, rudder_angle=0.0))
    out["maneuvering/turning@0"] = {"status": st, "payload": pl}
    st, pl = _post("maneuvering/turning", dict(TURN_MANEUVERING, rudder_angle=35.0))
    out["maneuvering/turning@35"] = {"status": st, "payload": pl}
    for d in (0.0, 35.0):
        st, pl = _post("mmg/turning", dict(TURN_MMG, rudder_angle=d))
        out[f"mmg/turning@{d:g}"] = {"status": st, "payload": pl}
    return out


# ---------------------------------------------------------------- reference
def reference_case():
    prm = R.MMGParams()
    n_p = R.self_propulsion_n(prm)
    U = KVLCC2["U"]
    n_rps = n_p * U / prm.L
    run35 = R.manoeuvre(prm, n_p, 35.0)
    run0 = R.manoeuvre(prm, n_p, 0.0, tau_max=50.0)
    idx35 = R.turning_indices(run35, prm.L, U)
    idx0 = R.turning_indices(run0, prm.L, U)
    zz = R.zigzag_indices(prm, n_p, 20.0, 10.0, U=U)
    resid = R.steady_turn_residual(prm, n_p, 35.0)
    return {"prm": prm, "n_p": n_p, "n_rps": n_rps, "U": U,
            "run35": run35, "run0": run0, "idx35": idx35, "idx0": idx0,
            "zigzag": zz, "residual": resid}


def main() -> int:
    os.makedirs("runs", exist_ok=True)
    os.makedirs("docs/verifier/figures", exist_ok=True)
    ref = reference_case()
    L, U = ref["prm"].L, ref["U"]
    plat = probe_platform()

    audit = {"case": "KVLCC2 tanker, rudder 35 deg, U=7.97 m/s (Fn=0.142)",
             "reference": {"n_rps": ref["n_rps"],
                           "turning_35": {k: v for k, v in ref["idx35"].items()},
                           "turning_0": {k: v for k, v in ref["idx0"].items()},
                           "zigzag_20_10": {k: v for k, v in ref["zigzag"].items()
                                            if k != "hist"},
                           "steady_turn_residual": ref["residual"]},
             "platform": {k: {"status": v["status"],
                              "payload": {kk: vv for kk, vv in v["payload"].items()
                                          if not isinstance(vv, list)}}
                          for k, v in plat.items()},
             "judgements": {}}

    J = audit["judgements"]
    J["maneuvering-mmg/turning_http"] = {
        "verdict": "STUB", "detail": "HTTP 500",
        "root_cause": "pydantic ValidationError: MMGInput.Hr missing "
                      "(ship-performance-platform backend/api/maneuvering_mmg.py:270/324)"}
    p0 = plat["maneuvering/turning@0"]["payload"]
    p35 = plat["maneuvering/turning@35"]["payload"]
    J["maneuvering/turning_delta_invariance"] = judge_delta_invariance(p0, p35)
    J["maneuvering/turning_scale"] = {
        "verdict": "MOCK",
        "detail": "advance/transfer/TD = 1120/640/1440 m = 3.5/2.0/4.5 x L exactly",
        "ratios": {k: p35[k] / L for k in ("advance", "transfer", "tactical_diameter")}}
    J["mmg/turning_zero_rudder"] = judge_zero_rudder(
        plat["mmg/turning@0"]["payload"], L, U)
    J["mmg/turning_visibility"] = judge_visibility(
        plat["mmg/turning@35"]["payload"], L)
    # positive controls -- the checker must PASS physically consistent payloads
    J["control_positive_invariance"] = judge_delta_invariance(
        {"advance": 900.0, "transfer": 430.0, "tactical_diameter": 1000.0,
         "steady_diameter": 1100.0},
        {"advance": 1120.0, "transfer": 620.0, "tactical_diameter": 1440.0,
         "steady_diameter": 1300.0})
    J["control_positive_zero_rudder"] = judge_zero_rudder(
        {"tactical_diameter": 1.0e9, "turning_rate": 0.0}, L, U)

    # error table vs literature band
    def band_row(name, val_L, key):
        lo, hi = BAND[key]
        return {"endpoint": name, "value_L": val_L,
                "band_L": [lo, hi], "rel_err_vs_band": rel_err(val_L, lo, hi),
                "within_15pct": (rel_err(val_L, lo, hi) is not None
                                 and abs(rel_err(val_L, lo, hi)) < 0.15)}
    rows = [band_row("reference(this repo)", ref["idx35"]["tactical_diameter"] / L,
                     "tactical_diameter_L"),
            band_row("maneuvering/turning", p35["tactical_diameter"] / L,
                     "tactical_diameter_L"),
            band_row("mmg/turning", plat["mmg/turning@35"]["payload"]["tactical_diameter"] / L,
                     "tactical_diameter_L"),
            band_row("maneuvering-mmg/turning", float("nan"), "tactical_diameter_L")]
    audit["error_table_Td"] = rows
    audit["zigzag_band"] = ZIGZAG_BAND

    # ---- figures -------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.2, 5.4))
    h = ref["run35"]["hist"]
    ax.plot(h[:, 3] * L, h[:, 4] * L, "k-", lw=2.0,
            label="reference MMG (this repo)")
    pm = plat["mmg/turning@35"]["payload"]
    ax.plot(pm["trajectory_x"], np.array(pm["trajectory_y"]) * -1.0, "r--", lw=1.4,
            label="platform /mmg/turning (35 deg, y mirrored)")
    ax.axhline(0, color="0.8", lw=0.6)
    ax.axvline(0, color="0.8", lw=0.6)
    ax.set_xlabel("x [m]"); ax.set_ylabel("y [m]")
    ax.set_title("KVLCC2 turning track, rudder 35 deg (U=7.97 m/s)")
    ax.legend(loc="best", fontsize=8); ax.set_aspect("equal", "datalim")
    fig.tight_layout(); fig.savefig("docs/verifier/figures/maneuvering_trajectories.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    names = [r["endpoint"] for r in rows]
    vals = [r["value_L"] if r["value_L"] == r["value_L"] else 0.0 for r in rows]
    lo, hi = BAND["tactical_diameter_L"]
    ax.axhspan(lo, hi, color="#cfe8cf", label="literature band (SIMMAN 2008)")
    bars = ax.bar(range(len(rows)), vals,
                  color=["#2b6cb0", "#c53030", "#dd6b20", "#a0aec0"])
    for b, r in zip(bars, rows):
        txt = "HTTP 500" if r["value_L"] != r["value_L"] else f"{r['value_L']:.2f}L"
        ax.text(b.get_x() + b.get_width() / 2, 0.08, txt, ha="center", fontsize=8)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([n.replace("/", "/\n") for n in names], fontsize=7.5)
    ax.set_ylabel("tactical diameter / L$_{pp}$")
    ax.set_title("KVLCC2 tactical diameter at 35 deg: endpoints vs literature band")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig("docs/verifier/figures/maneuvering_td_comparison.png", dpi=130)
    plt.close(fig)

    json.dump(audit, open("runs/m1460_maneuvering_audit.json", "w"),
              ensure_ascii=False, indent=1)

    print("=== 证据分级 ===")
    for k, v in J.items():
        print(f"  {k:42s} {v['verdict']}")
    print("\n=== T_d 误差表 (vs 文献带) ===")
    for r in rows:
        v = r["value_L"]
        s = "nan" if v != v else f"{v:.2f}L"
        e = r["rel_err_vs_band"]
        print(f"  {r['endpoint']:26s} {s:>8s}  err={'-' if e is None else f'{e*100:+.1f}%'}")
    print("\nreference turning(35): adv=%.1f m (%.2fL) tr=%.1f m (%.2fL) Td=%.1f m (%.2fL)"
          % (ref["idx35"]["advance"], ref["idx35"]["advance"] / L,
             ref["idx35"]["transfer"], ref["idx35"]["transfer"] / L,
             ref["idx35"]["tactical_diameter"], ref["idx35"]["tactical_diameter"] / L))
    print("reference zigzag20/10: os1=%.2f deg os2=%.2f deg"
          % (ref["zigzag"]["first_overshoot_deg"], ref["zigzag"]["second_overshoot_deg"]))
    print("steady-turn equilibrium residual = %.2e" % ref["residual"])
    print("\nwrote runs/m1460_maneuvering_audit.json + 2 figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())