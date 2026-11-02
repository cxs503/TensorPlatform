"""Verification-debt probe: /api/v1/seakeeping/{rao,motion} on the *live* platform (:8001).

What is checked
---------------
The repository contains a REAL seakeeping kernel (``backend/api/seakeeping_physics.py``,
strip-theory with frequency-dependent added mass, coupled heave-pitch).  The m1547 report
explicitly left the production replacement **unfinished** ("线上 :8001 未替换").  This probe
closes that open question with machine-checkable evidence and quantifies the gap:

  P1  parametric independence  - /motion amplitudes must react to wave_amplitude / heading
  P2  mode separation          - heave and pitch curves/periods must not be identical
  P3  added mass present       - T33 must exceed the *exact* no-added-mass bound
                                 T33_min = 2*pi*sqrt(m / (rho*g*Cwp*L*B))
  P4  roll period              - compare with the engineering reference
                                 T44_ref = 2*pi*k_xx/sqrt(g*GM), k_xx = 0.36 B
  P5  kernel self-consistency  - the in-repo REAL kernel must satisfy its own
                                 definitions T33 = 2*pi*sqrt((m+A33)/(rho g Aw)) etc.
  P6  long-wave limit          - heave RAO -> 1 for kL -> 0 (exact for any floating body)

Everything that is *not* the live probe (reference formulae, kernel recomputation) is
independent of the platform under test.

Usage:  python3 probe.py            (writes artifacts/gap_results.json + figs/*.png)
        python3 probe.py --offline  (skip HTTP; live section marked WITHHELD)
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "artifacts"
FIG = HERE / "figs"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
RHO, G = 1025.0, 9.81

# 100 m box barge: exact hydrostatics (Cb = Cwp = 1), so every reference below is closed form.
BARGE = dict(L=100.0, B=20.0, T=8.0, D=16.0, displacement=16000.0, Cb=1.0, KG=8.0, GM=2.0,
             speed=0.0)
BARGE_SOFT = dict(BARGE, GM=0.5)          # GM sweep: roll period must scale as 1/sqrt(GM)


# ---------------------------------------------------------------------------
# analytic references (independent of the platform)
# ---------------------------------------------------------------------------
def aw(L, B, Cwp):
    return Cwp * L * B


def t33_no_added_mass(s):
    """Exact lower bound: A33 = 0 (non-physical) -> shortest possible heave period."""
    m = s["displacement"] * 1000.0
    return 2.0 * math.pi * math.sqrt(m / (RHO * G * aw(s["L"], s["B"], 1.0)))


def t33_with_kappa(s, kappa):
    m = s["displacement"] * 1000.0
    return 2.0 * math.pi * math.sqrt(m * (1.0 + kappa) / (RHO * G * aw(s["L"], s["B"], 1.0)))


def t44_reference(s, k_xx_over_B=0.36):
    """Engineering reference: T = 2 pi k_xx / sqrt(g GM) (metacentric roll, no added inertia)."""
    return 2.0 * math.pi * k_xx_over_B * s["B"] / math.sqrt(G * s["GM"])


def t33_from_reported(mass, a33, L, B, Cwp=1.0):
    return 2.0 * math.pi * math.sqrt((mass + a33) / (RHO * G * aw(L, B, Cwp)))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def post(path, body, timeout=25):
    import requests

    try:
        r = requests.post(f"{API}/{path}", json=body, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = r.text
        return {"status": r.status_code, "data": data}
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "error": f"{type(exc).__name__}: {exc}"}


def rel(a, b):
    return abs(a - b) / abs(b) * 100.0 if b else float("nan")


def load_kernel():
    sys.path.insert(0, str(REPO / "backend" / "api"))
    import seakeeping_physics as K  # noqa: E402

    return K


def kernel_case(K, s):
    hull = {k: s[k] for k in ("L", "B", "T", "displacement", "Cb", "KG", "GM")}
    hull["Cwp"] = 1.0
    ship = K.ship_from_request(hull)
    nat = K.natural_periods(ship)
    try:
        rao = K.rao_curves(ship, heading_deg=180.0, speed_kn=0.0)
    except TypeError:
        rao = K.rao_curves(ship)
    try:
        strip = K.strip_theory(ship, 1.0, 180.0, 0.0)
    except TypeError:
        strip = K.strip_theory(ship)
    return ship, nat, rao, strip


def as_list(x):
    if isinstance(x, dict):
        for key in ("heave_rao", "heave", "values", "rao"):
            if key in x:
                return list(np.asarray(x[key], dtype=float))
    return list(np.atleast_1d(np.asarray(x, dtype=float)).ravel())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    offline = "--offline" in sys.argv
    res: dict = {"api_base": API, "hull": BARGE, "offline": offline}

    K = load_kernel()
    ship, nat, krao, kstrip = kernel_case(K, BARGE)
    mass = nat["mass"]
    res["kernel"] = {
        "T33": nat["heave"], "T55": nat["pitch"], "T44": nat["roll"],
        "A33": nat["A33"], "A55": nat["A55"], "I55": nat["I55"],
        "A33_over_mass": nat["A33_over_mass"], "Aw": nat["Aw"], "GM_L": nat["GM_L"],
        "mass": mass,
    }
    # P5: kernel self-consistency against its own definitions
    t33_check = t33_from_reported(mass, nat["A33"], BARGE["L"], BARGE["B"], 1.0)
    t55_check = 2.0 * math.pi * math.sqrt((nat["I55"] + nat["A55"]) / (mass * G * nat["GM_L"]))
    res["kernel"]["T33_from_A33"] = t33_check
    res["kernel"]["T33_self_check_pct"] = rel(t33_check, nat["heave"])
    res["kernel"]["T55_from_A55"] = t55_check
    res["kernel"]["T55_self_check_pct"] = rel(t55_check, nat["pitch"])
    t44_ref = t44_reference(BARGE)
    res["kernel"]["T44_ref_0.36B"] = t44_ref
    res["kernel"]["T44_vs_ref_pct"] = rel(nat["roll"], t44_ref)

    # references for the deployed endpoint
    t_min = t33_no_added_mass(BARGE)
    res["references"] = {
        "T33_no_added_mass_s": t_min,
        "T33_band_kappa_0.7_0.9": [t33_with_kappa(BARGE, 0.7), t33_with_kappa(BARGE, 0.9)],
        "T44_ref_0.36B_s": t44_ref,
        "T44_ref_band_0.30_0.40B": [t44_reference(BARGE, 0.30), t44_reference(BARGE, 0.40)],
        "long_wave_heave_RAO_limit": 1.0,
    }

    live: dict = {}
    if not offline:
        m1 = post("seakeeping/motion", BARGE)
        m2 = post("seakeeping/motion", dict(BARGE, wave_amplitude=3.0, heading=90.0))
        m3 = post("seakeeping/motion", BARGE_SOFT)
        r1 = post("seakeeping/rao", BARGE)
        live = {"motion_base": m1, "motion_wave3_heading90": m2,
                "motion_GM0.5": m3, "rao_base": r1}
        # /seakeeping-advanced/strip-theory needs a fuller body -> probe it too
        live["strip_base"] = post("seakeeping-advanced/strip-theory",
                                  dict(BARGE, Cm=1.0, Cwp=1.0, wave_frequency=1.0))

        p1 = m1["data"].get("motion_amplitudes") if isinstance(m1.get("data"), dict) else None
        p2 = m2["data"].get("motion_amplitudes") if isinstance(m2.get("data"), dict) else None
        n1 = m1["data"].get("natural_periods") if isinstance(m1.get("data"), dict) else None
        n3 = m3["data"].get("natural_periods") if isinstance(m3.get("data"), dict) else None
        rao = r1["data"] if isinstance(r1.get("data"), dict) else {}
        heave, pitch, roll = (np.asarray(rao.get(k, []), dtype=float)
                              for k in ("heave_rao", "pitch_rao", "roll_rao"))

        res["live_motion_base"] = m1
        res["live_motion_wave3_heading90"] = m2
        res["live_motion_GM0.5"] = m3
        res["live_rao_base"] = {"status": r1["status"],
                                "freqs": rao.get("frequencies"),
                                "heave_rao": heave.tolist(), "pitch_rao": pitch.tolist(),
                                "roll_rao": roll.tolist()}
        res["live_strip_base"] = live["strip_base"]

        checks = {}
        if p1 and p2:
            checks["P1_amplitudes_independent_of_wave_and_heading"] = dict(
                amp_wave1=p1, amp_wave3_heading90=p2,
                identical=(p1 == p2),
                verdict="MOCK" if p1 == p2 else "responds")
        if n1 and n3:
            checks["P4b_roll_period_follows_GM"] = dict(
                T44_GM2=n1["roll"], T44_GM0p5=n3["roll"],
                ratio=n3["roll"] / n1["roll"], exact_expectation=math.sqrt(2.0 / 0.5))
        if n1:
            checks["P3_heave_period_vs_no_added_mass_bound"] = dict(
                T33_endpoint=n1["heave"], T33_min=t_min,
                ratio=n1["heave"] / t_min,
                verdict="A33~0 (non-physical)" if abs(n1["heave"] / t_min - 1.0) < 0.02 else "added mass present")
            checks["P2_heave_pitch_identical_period"] = dict(
                T33=n1["heave"], T55=n1["pitch"],
                identical=n1["heave"] == n1["pitch"],
                verdict="COPY" if n1["heave"] == n1["pitch"] else "distinct")
            checks["P4_roll_vs_metacentric_reference"] = dict(
                T44_endpoint=n1["roll"], T44_ref=t44_ref,
                err_pct=rel(n1["roll"], t44_ref))
        if heave.size and pitch.size:
            checks["P2b_rao_heave_pitch_bitwise_identical"] = dict(
                max_abs_diff=float(np.max(np.abs(heave - pitch))),
                identical=bool(np.array_equal(heave, pitch)))
        if heave.size:
            checks["P6_longest_wave_heave_RAO"] = dict(
                omega_min=rao["frequencies"][0], rao_at_omega_min=float(heave[0]),
                exact_limit=1.0, err_pct=rel(float(heave[0]), 1.0),
                note="grid lower limit 0.2 rad/s -> kL large; limit only approaches 1")
        res["live_checks"] = checks

    # kernel long-wave limit (own grid, independent of the endpoint)
    lw = []
    for om in (0.02, 0.05, 0.1, 0.2):
        try:
            row = K.solve_rao(ship, om, 180.0, 0.0)
            val = row.get("heave_rao", row) if isinstance(row, dict) else row
            lw.append({"omega": om, "heave_rao": float(as_list(val)[0])})
        except Exception as exc:  # noqa: BLE001
            lw.append({"omega": om, "error": f"{type(exc).__name__}: {exc}"})
    res["kernel_long_wave"] = lw

    # ---------------- figures ----------------
    labels = ["heave T33", "pitch T55", "roll T44"]
    dep = [res.get("live_motion_base", {}).get("data", {}).get("natural_periods", {}).get(k)
           for k in ("heave", "pitch", "roll")] if not offline else [None] * 3
    ker = [nat["heave"], nat["pitch"], nat["roll"]]
    ref = [t_min, None, t44_ref]
    x = np.arange(3)
    w = 0.27
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - w, [d if d else 0 for d in dep], w, label="/seakeeping/motion (live :8001)")
    ax.bar(x, ker, w, label="in-repo REAL kernel")
    ax.bar(x + w, [r if r else 0 for r in ref], w,
           label="analytic ref (T33 w/ A33=0 bound, T44 0.36B)")
    for i, (d, k, r) in enumerate(zip(dep, ker, ref)):
        for off, v in ((-w, d), (0, k), (w, r)):
            if v:
                ax.text(i + off, v + 0.15, f"{v:.2f}", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("natural period [s]")
    ax.set_title("100 m box barge (Cb=Cwp=1, GM=2 m): live endpoint vs in-repo kernel vs analytic")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_natural_periods.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    if not offline and heave.size:
        fr = rao.get("frequencies")
        axes[0].plot(fr, heave, "o-", ms=3, label="live heave RAO")
        axes[0].plot(fr, pitch, "x--", ms=4, label="live pitch RAO")
        axes[0].plot(fr, roll, "s-", ms=3, label="live roll RAO")
        axes[0].set_title("live :8001 /seakeeping/rao (box barge, head seas)")
    try:
        kf = krao["frequencies"] if isinstance(krao, dict) and "frequencies" in krao else None
        kh = as_list(krao["heave_rao"]) if isinstance(krao, dict) and "heave_rao" in krao else None
        kp = as_list(krao["pitch_rao"]) if isinstance(krao, dict) and "pitch_rao" in krao else None
        if kf and kh:
            axes[1].plot(kf, kh, "-", label="kernel heave RAO")
            if kp:
                axes[1].plot(kf, kp, "--", label="kernel pitch RAO")
        axes[1].axhline(1.0, color="k", lw=0.8, ls=":", label="exact long-wave limit = 1")
    except Exception as exc:  # noqa: BLE001
        axes[1].text(0.05, 0.5, f"kernel RAO unavailable: {exc}", fontsize=8)
    axes[1].set_title("in-repo strip-theory kernel (same hull)")
    for a in axes:
        a.set_xlabel("omega [rad/s]")
        a.set_ylabel("RAO [m/m] / [deg/m]")
        a.legend(fontsize=8)
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "fig2_rao_curves.png", dpi=130)
    plt.close(fig)

    res["figures"] = ["figs/fig1_natural_periods.png", "figs/fig2_rao_curves.png"]
    (OUT / "gap_results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=float))
    print(json.dumps({k: res[k] for k in ("references", "kernel", "live_checks", "kernel_long_wave")
                      if k in res}, indent=1, ensure_ascii=False, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())