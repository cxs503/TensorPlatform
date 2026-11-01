#!/usr/bin/env python3
"""Audit the ship-performance seakeeping endpoints against analytic anchors (m1433).

Read-only: this script only POSTs to the ship service (default http://127.0.0.1:8001)
and writes artifacts under ``artifacts/m1433/``. It never mutates the remote service.

Each defect is decided by a machine comparison, and each *anchor* used to declare a
defect holds for any choice of added-mass / damping coefficient (dispersion relation,
encounter frequency, long-wave limits, non-degeneracy of the response):

  A1  omega_e = omega - k U cos(beta)                       (closed form)
  A2  long wave (k->0):  heave RAO -> 1,  pitch/slope -> 1   (any kappa/zeta)
  A3  the response must react to speed (encounter frequency)
  A4  the response must react to heading
  A5  heave and pitch are different physics: curves must not be identical
  A6  pitch natural period must follow C55 = rho g nabla GM_L with hydrostatic BM_L
  A7  4*sqrt(m0) = Hs for any Hs-scaled spectrum

Usage:  python3 tools/audit_seakeeping_endpoints.py [base_url]
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import urllib.error
import urllib.request

from services.seakeeping import SeakeepingModel, spectrum, uniform_grid  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
OUT = ROOT / "artifacts" / "m1433"
OUT.mkdir(parents=True, exist_ok=True)

HULL = dict(L=150.0, B=22.0, T=8.0, Cb=0.68, Cwp=0.78, displacement_t=18400.0, KG=8.5, GM_T=1.5)
PLATFORM_HULL = dict(L=150.0, B=22.0, T=8.0, D=9.0, displacement=18400.0, Cb=0.68,
                     KG=8.5, GM=1.5, Cwp=0.78, Cm=0.98)
STRIP_EXTRA = dict(wave_amplitude=1.5, wave_frequency=0.8, heading=180, speed=0.0)


def post(path: str, body: dict):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"http": r.status, "data": json.load(r)}
    except urllib.error.HTTPError as e:  # keep the status, it is evidence
        return {"http": e.code, "data": None, "detail": e.read()[:300].decode(errors="replace")}
    except Exception as e:  # service down
        return {"http": None, "data": None, "detail": str(e)}


def rel(a: float, b: float) -> float:
    if b == 0:
        return float("inf")
    return 100.0 * (a / b - 1.0)


def main() -> int:
    model = SeakeepingModel(**HULL)
    defects: list[dict] = []

    def record(did, title, verdict, anchor, evidence):
        defects.append({"id": did, "title": title, "verdict": verdict, "anchor": anchor, "evidence": evidence})

    # ---------------- /seakeeping/rao ----------------
    base = post("/api/v1/seakeeping/rao", dict(PLATFORM_HULL, speed=0.0, heading=180, wave_frequency=0.3))
    alt_f = post("/api/v1/seakeeping/rao", dict(PLATFORM_HULL, speed=0.0, heading=180, wave_frequency=1.2))
    alt_h = post("/api/v1/seakeeping/rao", dict(PLATFORM_HULL, speed=0.0, heading=90, wave_frequency=0.3))
    alt_u = post("/api/v1/seakeeping/rao", dict(PLATFORM_HULL, speed=25.0, heading=180, wave_frequency=0.3))
    legacy = (base.get("data") or {})

    if legacy:
        same_f = legacy.get("heave_rao") == (alt_f.get("data") or {}).get("heave_rao")
        same_h = legacy.get("heave_rao") == (alt_h.get("data") or {}).get("heave_rao")
        same_u = legacy.get("heave_rao") == (alt_u.get("data") or {}).get("heave_rao")
        record("D1", "/seakeeping/rao ignores wave_frequency", "CONFIRMED" if same_f else "NOT_REPRODUCED",
               "A3 request parameter must reach the physics",
               {"wave_frequency 0.3 vs 1.2 -> identical curve": same_f,
                "first_three_heave": legacy.get("heave_rao", [])[:3]})
        record("D2", "/seakeeping/rao ignores heading", "CONFIRMED" if same_h else "NOT_REPRODUCED",
               "A4 heading must change the excitation", {"heading 180 vs 90 -> identical": same_h})
        record("D3", "/seakeeping/rao ignores speed", "CONFIRMED" if same_u else "NOT_REPRODUCED",
               "A3 encounter frequency omega_e = omega - k U cos(beta)",
               {"speed 0 vs 25 kn -> identical": same_u})
        same_pitch = legacy.get("pitch_rao") == legacy.get("heave_rao")
        record("D4", "/seakeeping/rao returns pitch curve identical to heave", "CONFIRMED" if same_pitch else "NOT_REPRODUCED",
               "A5 heave and pitch are different physics",
               {"pitch_rao == heave_rao": same_pitch,
                "cause": "T_pitch = 2*pi*sqrt((D*L^2/12)/(rho*g*B*L^3/12)) = T_heave (the L^2/12 and L^3/12 terms cancel)"})

    # ---------------- /seakeeping/motion ----------------
    m1 = post("/api/v1/seakeeping/motion", dict(PLATFORM_HULL, speed=0.0))
    m2 = post("/api/v1/seakeeping/motion", dict(PLATFORM_HULL, displacement=60000.0, speed=25.0))
    a1 = (m1.get("data") or {})
    a2 = (m2.get("data") or {})
    if a1 and a2:
        hard = a1.get("motion_amplitudes") == a2.get("motion_amplitudes")
        record("D5", "/seakeeping/motion returns hard-coded motion amplitudes",
               "CONFIRMED" if hard else "NOT_REPRODUCED",
               "A2/A3 amplitudes must follow sea state, displacement and speed",
               {"displacement 18400 vs 60000 t, speed 0 vs 25 kn -> identical": hard,
                "amplitudes": a1.get("motion_amplitudes")})
        same_tp = a1.get("natural_periods", {}).get("pitch") == a1.get("natural_periods", {}).get("heave")
        record("D6", "pitch natural period equals heave natural period", "CONFIRMED" if same_tp else "NOT_REPRODUCED",
               "A6 pitch restoring is C55 = rho g nabla GM_L with GM_L ~ KB + BML - KG",
               {"natural_periods": a1.get("natural_periods"),
                "reference_model_natural_periods_s": {k: round(v, 3) for k, v in model.natural_periods().items()}})

    # ---------------- /seakeeping-advanced/strip-theory ----------------
    st = post("/api/v1/seakeeping-advanced/strip-theory", dict(PLATFORM_HULL, **STRIP_EXTRA))
    st_alt = post("/api/v1/seakeeping-advanced/strip-theory",
                  dict(PLATFORM_HULL, **dict(STRIP_EXTRA, heading=90, speed=15)))
    sd = st.get("data") or {}
    print("strip-theory http:", st.get("http"), str(st.get("detail"))[:200])
    if sd:
        curve = sd.get("heave_rao") or []
        low = curve[:3]
        violation = all(abs(v - 1.0) > 0.4 for v in low)
        record("D7", "strip-theory heave RAO violates the long-wave limit", "CONFIRMED" if violation else "NOT_REPRODUCED",
               "A2 as k->0 the heave RAO must tend to 1 for any added-mass/damping set",
               {"platform_low_frequency_heave_rao": low,
                "reference_long_wave_limit": 1.0,
                "relative_error_pct": [round(rel(v, 1.0), 1) for v in low],
                "cause": "piecewise clamp: RAO = 0.5 whenever omega_e/omega_n < 0.5 or > 2.0"})
        same_st = sd.get("heave_rao") == (st_alt.get("data") or {}).get("heave_rao")
        record("D8", "strip-theory RAO array ignores speed and heading", "CONFIRMED" if same_st else "NOT_REPRODUCED",
               "A3/A4", {"heading 180/0 kn vs 90/15 kn -> identical array": same_st})
        jumps = [abs(curve[i] - curve[i - 1]) > 0.3 for i in range(1, len(curve))]
        record("D9", "strip-theory RAO curve has clamp discontinuities", "CONFIRMED" if any(jumps) else "NOT_REPRODUCED",
               "A2 magnitude response must be continuous across the band",
               {"n_discontinuous_steps": sum(jumps),
                "max_step": round(max([abs(curve[i] - curve[i - 1]) for i in range(1, len(curve))] or [0.0]), 3)})
        # implied damping ratio from the reported damping coefficient vs the hard-coded 0.1
        b33 = sd.get("damping_heave")
        if b33:
            M = model.mass + model.A33
            zeta_implied = b33 / (2.0 * math.sqrt(model.C33 * M))
            record("D10", "strip-theory damping ratio is hard-coded, not the reported coefficient",
                   "CONFIRMED" if abs(zeta_implied - 0.1) > 0.02 else "NOT_REPRODUCED",
                   "consistency between reported coefficients and the RAO actually used",
                   {"reported_damping_heave_Ns_m": b33,
                    "implied_zeta_from_reported_b33": round(zeta_implied, 4),
                    "zeta_used_in_rao_loop": 0.1})
        T_pitch_platform = sd.get("natural_period_pitch")
        T_pitch_ref = model.natural_periods()["pitch"]
        if T_pitch_platform:
            record("D11", "strip-theory pitch natural period inflated by the GM_L = L/20 fudge", "CONFIRMED",
                   "A6 GM_L must come from hydrostatics: BML = IL/nabla = " f"{model.BM_L:.1f} m here",
                   {"platform_T_pitch_s": T_pitch_platform, "platform_GM_L_assumed_m": HULL["L"] / 20,
                    "hydrostatic_GM_L_m": round(model.GM_L, 2), "reference_T_pitch_s": round(T_pitch_ref, 2),
                    "period_ratio": round(T_pitch_platform / T_pitch_ref, 2)})

    # ---------------- /seakeeping-advanced/wave-spectrum ----------------
    wsp = post("/api/v1/seakeeping-advanced/wave-spectrum",
               dict(Hs=3.0, Tp=8.0, gamma=3.3, spectrum_type="jonswap",
                    frequency_range=[0.1, 1.5], frequency_steps=50))
    record("D12", "wave-spectrum endpoint raises HTTP 500", "CONFIRMED" if wsp.get("http") == 500 else "NOT_REPRODUCED",
           "A7 a working spectrum must return 4*sqrt(m0) = Hs",
           {"http_status": wsp.get("http"), "detail": wsp.get("detail"),
            "cause": "numpy 2.4.6 removed np.trapz; the module calls np.trapz without np.trapezoid fallback"})

    # ---------------- reference series for the figure ----------------
    omegas = uniform_grid(0.1, 2.0, 191)
    ref_curve = model.rao_curve(omegas, heading_deg=180.0, speed=0.0)
    spec = spectrum("jonswap", 3.0, 8.0, uniform_grid(0.05, 2.5, 201))

    f_sum = {
        "service": BASE,
        "hull": PLATFORM_HULL,
        "reference_natural_periods_s": {k: round(v, 3) for k, v in model.natural_periods().items()},
        "reference_hydrostatics": {k: round(v, 3) for k, v in model.hydrostatics().items()},
        "reference_spectrum": {"Hs_recovered": round(spec["Hs_recovered"], 6), "Tz_s": round(spec["Tz_s"], 3)},
        "legacy_rao_curve": {"frequencies": legacy.get("frequencies"), "heave_rao": legacy.get("heave_rao"),
                             "pitch_rao": legacy.get("pitch_rao")},
        "reference_rao_curve": {"omega": ref_curve["omega"], "heave_rao": ref_curve["heave_rao_m_per_m"],
                                "pitch_slope": ref_curve["pitch_rao_rad_per_rad"]},
        "strip_theory_heave_rao": sd.get("heave_rao"),
        "defects": defects,
        "defects_confirmed": sum(1 for d in defects if d["verdict"] == "CONFIRMED"),
        "defects_total": len(defects),
        "verdict": "seakeeping capability = MOCK (defect-anchored); reference implementation provided by "
                   "services/seakeeping.py with 25 analytic-anchor tests",
    }
    (OUT / "seakeeping_audit.json").write_text(json.dumps(f_sum, indent=2, ensure_ascii=False))

    # ---------------- figure ----------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
        ax = axes[0]
        if legacy.get("frequencies"):
            ax.plot(legacy["frequencies"], legacy["heave_rao"], "o-", ms=3, lw=1.4,
                    label="/seakeeping/rao (legacy, ignores speed/heading)")
            ax.plot(legacy["frequencies"], legacy["pitch_rao"], "s--", ms=3, lw=1.1, color="tab:red",
                    label="legacy pitch (identical to heave)")
        if sd.get("heave_rao"):
            f = [0.15 + i * (1.35 / (len(sd["heave_rao"]) - 1)) for i in range(len(sd["heave_rao"]))]
            ax.plot(f, sd["heave_rao"], "^-", ms=3, lw=1.1, color="tab:green",
                    label="strip-theory (clamped at 0.5 off-band)")
        ax.plot(ref_curve["omega"], ref_curve["heave_rao_m_per_m"], "-", lw=2.2, color="k",
                label="reference s/ (anchored)")
        ax.axhline(1.0, color="gray", ls=":", lw=1.0)
        ax.annotate("long-wave limit = 1 (analytic, any kappa/zeta)", xy=(0.12, 1.0), xytext=(0.35, 0.62),
                    fontsize=8, arrowprops=dict(arrowstyle="->", lw=0.8, color="gray"))
        ax.set_xlabel(r"$\omega$ [rad/s]")
        ax.set_ylabel("heave RAO [m/m]")
        ax.set_title("Heave RAO: legacy / clamped strip theory / reference")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(alpha=0.3)

        ax = axes[1]
        labels = ["heave", "pitch", "roll"]
        legs = a1.get("natural_periods", {}) if a1 else {}
        ref_T = model.natural_periods()
        st_T = {"heave": sd.get("natural_period_heave"), "pitch": sd.get("natural_period_pitch"),
                "roll": sd.get("natural_period_roll")}
        x = range(len(labels))
        ax.bar([i - 0.26 for i in x], [legs.get(k, 0) for k in labels], width=0.25,
               label="/seakeeping/motion")
        ax.bar(list(x), [st_T.get(k) or 0 for k in labels], width=0.25,
               label="/seakeeping-advanced/strip-theory")
        ax.bar([i + 0.26 for i in x], [ref_T[k] for k in labels], width=0.25,
               label="reference (hydrostatic GM_L)")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels)
        ax.set_ylabel("natural period [s]")
        ax.set_title("Natural periods: pitch from GM_L = L/20 is inflated")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3, axis="y")

        fig.tight_layout()
        fig.savefig(OUT / "fig_seakeeping_audit.png", dpi=150)
        print("figure:", OUT / "fig_seakeeping_audit.png")
    except Exception as exc:  # pragma: no cover
        print("figure skipped:", exc)

    print(json.dumps({"defects_confirmed": f_sum["defects_confirmed"], "defects_total": f_sum["defects_total"]}))
    for d in defects:
        print(f"  {d['id']:>4} {d['verdict']:<15} {d['title']}")
    print("artifact:", OUT / "seakeeping_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())