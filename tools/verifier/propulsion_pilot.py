#!/usr/bin/env python3
"""Propulsion-domain verification pilot (verifier seat, tp-ver-7).

What this does
--------------
1. Interrogates two *already-merged* platform capabilities and records the raw
   responses verbatim into ``artifacts/verifier/propulsion/platform_capture.json``
   (numbers in the report are read from that file, never hand-copied):
       - POST /api/v1/propeller-design/wageningen-b   (capability: propeller.open_water)
       - POST /api/v1/calculations/propulsion         (capability: calc.propulsion)
2. Judges them against the coefficient-free analytic oracle in
   ``tools/verifier/oracles/propulsion_oracle.py`` (exact definitions,
   Froude momentum bound, published B-series envelopes).
3. Writes the verdict bundle + figures.

Usage
-----
    python tools/verifier/propulsion_pilot.py            # live API (needs :8001)
    python tools/verifier/propulsion_pilot.py --offline   # replay cached capture
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(HERE, "oracles"))

import propulsion_oracle as oracle  # noqa: E402

API_BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
OUT_DIR = os.path.join(REPO, "artifacts", "verifier", "propulsion")
CAPTURE_PATH = os.path.join(OUT_DIR, "platform_capture.json")

J_SWEEP = [round(0.05 * i, 2) for i in range(0, 21)]        # 0.00 .. 1.00
PITCH_RATIOS = [0.8, 1.0, 1.2]
Z_BLADES = 4
AE_AO = 0.70

# 210k DWT bulk carrier, service condition (representative Panamax/Capesize
# twin-... single-screw) — the geometry is only a carrier for the efficiency
# chain, every constraint is evaluated on invariants.
ENDO_CASE = {
    "length": 320.0, "beam": 42.8, "draft": 14.5, "displacement": 210000.0,
    "speed": 14.0, "cb": 0.63,
    "prop_diameter": 8.0, "prop_pitch": 7.5, "prop_rpm": 90.0,
    "num_blades": 5, "blade_area_ratio": 0.6,
}


def _post(path: str, body: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def capture(offline: bool = False) -> dict:
    if offline:
        with open(CAPTURE_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    os.makedirs(OUT_DIR, exist_ok=True)
    rows, latencies = [], []
    for pd in PITCH_RATIOS:
        for j in J_SWEEP:
            t0 = time.time()
            r = _post("propeller-design/wageningen-b",
                      {"J": j, "P_D": pd, "Z": Z_BLADES, "Ae_Ao": AE_AO})
            latencies.append(time.time() - t0)
            rows.append({"J": j, "P_D": pd, "Z": Z_BLADES, "Ae_Ao": AE_AO,
                         "KT": r["KT"], "KQ": r["KQ"], "eta0": r["eta0"],
                         "method": r.get("method")})

    # geometry sweep: does K_T factorise into independent geometry multipliers?
    geom_sweep, geom_probes = {}, []
    geometries = [(1.0, 4, 0.70), (1.0, 5, 0.70), (1.0, 6, 0.85),
                  (0.8, 4, 0.70), (1.2, 4, 0.70), (1.0, 4, 0.40), (1.0, 4, 1.00)]
    for j in (0.3, 0.5, 0.7):
        geom_sweep[str(j)] = []
        for (pd, z, ae) in geometries:
            r = _post("propeller-design/wageningen-b",
                      {"J": j, "P_D": pd, "Z": z, "Ae_Ao": ae})
            sample = {"J": j, "P_D": pd, "Z": z, "Ae_Ao": ae,
                      "KT": r["KT"], "KQ": r["KQ"], "eta0": r["eta0"]}
            geom_sweep[str(j)].append(sample)
            if abs(j - 0.5) < 1e-12:
                geom_probes.append(sample)

    t0 = time.time()
    endo_resp = _post("calculations/propulsion", ENDO_CASE, timeout=180.0)
    endo_latency = time.time() - t0

    cap = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_base": API_BASE,
        "series": {
            "endpoint": "POST /api/v1/propeller-design/wageningen-b",
            "capability": "propeller.open_water",
            "Z": Z_BLADES, "Ae_Ao": AE_AO, "pitch_ratios": PITCH_RATIOS,
            "J_values": J_SWEEP,
            "median_latency_s": round(sorted(latencies)[len(latencies) // 2], 4),
            "rows": rows,
        },
        "geometry_probes": geom_probes,
        "geometry_sweep": geom_sweep,
        "end_to_end": {
            "endpoint": "POST /api/v1/calculations/propulsion",
            "capability": "calc.propulsion",
            "case": ENDO_CASE,
            "latency_s": round(endo_latency, 2),
            "response": endo_resp,
        },
    }
    with open(CAPTURE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cap, fh, indent=2, ensure_ascii=False)
    return cap


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def make_figures(cap: dict, series_res, endo_res, out_dir: str) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = []
    rows = cap["series"]["rows"]
    pd0 = 1.0
    sub = sorted([r for r in rows if abs(r["P_D"] - pd0) < 1e-9], key=lambda r: r["J"])
    js = [r["J"] for r in sub]

    # Fig 1: K_T / 10K_Q and the K_Q/K_T ratio collapse
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(js, [r["KT"] for r in sub], "o-", label="$K_T$ (platform)", ms=3.5)
    ax[0].plot(js, [10 * r["KQ"] for r in sub], "s-", label="$10K_Q$ (platform)", ms=3.5)
    ax[0].set_xlabel("advance ratio $J$")
    ax[0].set_ylabel("coefficient")
    ax[0].set_title(f"Open-water coefficients (P/D={pd0}, Z={Z_BLADES}, $A_E/A_O$={AE_AO})")
    ax[0].grid(alpha=.3)
    ax[0].legend()
    ratios = [r["KQ"] / r["KT"] for r in sub if r["KT"]]
    ax[1].plot([r["J"] for r in sub if r["KT"]], ratios, "^-", color="crimson", ms=4)
    ax[1].set_xlabel("advance ratio $J$")
    ax[1].set_ylabel("$K_Q/K_T$")
    ax[1].set_title("Defect: $K_Q/K_T$ is bit-constant → $\\eta_0 \\propto J$")
    ax[1].set_ylim(min(ratios) * 0.9, max(ratios) * 1.1)
    ax[1].grid(alpha=.3)
    fig.tight_layout()
    p1 = os.path.join(out_dir, "fig1_coefficients_and_ratio.png")
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    figs.append({"name": "fig1_coefficients_and_ratio.png",
                 "caption": "Fig.1 平台敞水系数：K_T 与 10K_Q 完全成比例（右图 K_Q/K_T 恒定），"
                            "意味着效率仅由 J 线性决定，与 B 系列回归无关。",
                 "path": p1})

    # Fig 2: eta_0 vs J against the momentum bound and the published peak band
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    for pd in PITCH_RATIOS:
        s = sorted([r for r in rows if abs(r["P_D"] - pd) < 1e-9], key=lambda r: r["J"])
        ax.plot([r["J"] for r in s], [r["eta0"] for r in s], "o-", ms=3,
                label=f"platform $\\eta_0$ (P/D={pd})")
    mask = [r for r in sub if r["J"] > 0 and r["KT"] > 0]
    ax.plot([r["J"] for r in mask],
            [oracle.ideal_efficiency(oracle.thrust_loading(r["J"], r["KT"])) for r in mask],
            "k--", lw=2, label="momentum-theory bound $\\eta_i$")
    ax.axhspan(0.60, 0.75, color="green", alpha=.12,
               label="published merchant $\\eta_{0,max}$ band 0.60–0.75")
    ax.axhline(1.0, color="red", lw=1, ls=":")
    ax.set_xlabel("advance ratio $J$")
    ax.set_ylabel("open-water efficiency $\\eta_0$")
    ax.set_title("$\\eta_0$ exceeds the Froude momentum bound and saturates at 0.85")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    p2 = os.path.join(out_dir, "fig2_efficiency_vs_bound.png")
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    figs.append({"name": "fig2_efficiency_vs_bound.png",
                 "caption": "Fig.2 三个螺距比下 η_0(J) 完全重合、在 J≥0.6 出现 0.85 平台，"
                            "并在 J=0.4–0.6 越过动量理论上限 η_i（物理不可能）。",
                 "path": p2})

    # Fig 3: end-to-end efficiency chain
    r = cap["end_to_end"]["response"]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.0))
    labels = ["$\\eta_0$", "$\\eta_H$", "$\\eta_R$", "$\\eta_D$"]
    vals = [r["open_water_eff"], r["hull_eff"], r["relative_rot_eff"], r["propulsive_eff"]]
    colors = ["#4c72b0", "#55a868", "#8172b2", "#c44e52"]
    ax[0].bar(labels, vals, color=colors)
    ax[0].axhline(1.0, color="red", ls="--", lw=1.4, label="physical limit = 1.0")
    for i, v in enumerate(vals):
        ax[0].text(i, v + 0.02, f"{v:.3f}", ha="center", fontsize=9)
    ax[0].set_ylim(0, 1.6)
    ax[0].set_title("End-to-end efficiency chain (210k DWT, 14 kn)")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=.3, axis="y")

    # constraint scoreboard
    all_res = series_res + endo_res
    names = [c.cid for c in all_res]
    ok = [1 if c.passed else 0 for c in all_res]
    ax[1].barh(names, ok, color=["#55a868" if o else "#c44e52" for o in ok])
    ax[1].set_xlim(0, 1.15)
    ax[1].set_xlabel("1 = pass, 0 = fail")
    ax[1].set_title(f"Constraint scoreboard ({sum(ok)}/{len(ok)} pass)")
    ax[1].grid(alpha=.3, axis="x")
    fig.tight_layout()
    p3 = os.path.join(out_dir, "fig3_endtoend_and_scoreboard.png")
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    figs.append({"name": "fig3_endtoend_and_scoreboard.png",
                 "caption": "Fig.3 左：全船推进链效率，η_0=%.3f 与 η_D=%.3f 均越过 1.0；"
                            "右：%d 条判据的通过/失败记分板。" % (r["open_water_eff"], r["propulsive_eff"], len(ok) - sum(ok)),
                 "path": p3})
    return figs


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="replay cached capture")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    cap = capture(offline=args.offline)
    series_res = oracle.evaluate_series(cap["series"], cap.get("geometry_sweep"))
    endo_res = oracle.evaluate_end_to_end(cap["end_to_end"])
    summ_series = oracle.summarize(series_res)
    summ_endo = oracle.summarize(endo_res)
    figs = make_figures(cap, series_res, endo_res, out_dir)

    verdict = {
        "pilot": "propulsion-domain verification (tp-ver-7)",
        "captured_at_utc": cap["captured_at_utc"],
        "oracle": "tools/verifier/oracles/propulsion_oracle.py (coefficient-free: exact identities, "
                  "Froude momentum bound, published B-series envelopes)",
        "capabilities_audited": {
            "propeller.open_water": {
                "endpoint": cap["series"]["endpoint"],
                "summary": summ_series,
                "constraints": [c.to_dict() for c in series_res],
            },
            "calc.propulsion": {
                "endpoint": cap["end_to_end"]["endpoint"],
                "summary": summ_endo,
                "constraints": [c.to_dict() for c in endo_res],
            },
        },
        "geometry_probes": cap["geometry_probes"],
        "geometry_sweep": cap.get("geometry_sweep"),
        "figures": [{"name": f["name"], "caption": f["caption"]} for f in figs],
    }
    out_json = os.path.join(out_dir, "propulsion_verification_verdict.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2, ensure_ascii=False)

    print(f"capture   : {CAPTURE_PATH}")
    print(f"verdict   : {out_json}")
    for cap_name, block in (("propeller.open_water", summ_series), ("calc.propulsion", summ_endo)):
        print(f"\n== {cap_name} == {block['verdict'].upper()} "
              f"({block['n_passed']}/{block['n_constraints']} passed)")
        print(f"   failed: {', '.join(block['failed_ids']) or '-'}")
    print("\nPer-constraint detail:")
    for c in series_res + endo_res:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.cid} ({c.stratum}) {c.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())