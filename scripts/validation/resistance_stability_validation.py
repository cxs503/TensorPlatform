"""Validation evidence for the resistance + stability chain (self-contained).

Run from ``backend/``:

    PYTHONPATH=. python3 ../scripts/validation/resistance_stability_validation.py

Writes ``docs/validation/figures/*.png`` and
``docs/validation/results_<date>.json``. Every number in the report is read
from that JSON — nothing is transcribed by hand.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.resistance_benchmark import validate as validate_benchmark  # noqa: E402
from services.resistance_model import (  # noqa: E402
    ittc57_cf,
    ittc78_ca,
    legacy_predict,
    predict_resistance,
    schoenherr_cf,
    wetted_surface_box_bound,
    wetted_surface_holtrop,
)
from services.stability_hydrostatics import (  # noqa: E402
    box_metacentric_height,
    deck_edge_heel_deg,
    numeric_gz_box,
    wall_sided_gz,
)

OUT = Path(__file__).resolve().parents[2] / "docs" / "validation"
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)


def friction_table() -> dict:
    rows = []
    for re_log in (6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0):
        re = 10.0 ** re_log
        cf57, cfsch = ittc57_cf(re), schoenherr_cf(re)
        rows.append({
            "Re": re,
            "cf_ittc57": round(cf57, 8),
            "cf_schoenherr": round(cfsch, 8),
            "rel_diff_pct": round((cf57 - cfsch) / cfsch * 100.0, 3),
        })
    return {"rows": rows}


def surface_table() -> dict:
    hulls = {
        "KVLCC2": dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098),
        "SUBOFF(axisym.)": dict(length=4.356, beam=0.508, draft=0.508, cb=0.508),
    }
    rows = []
    for name, g in hulls.items():
        fixed = wetted_surface_holtrop(**g)
        box = wetted_surface_box_bound(g["length"], g["beam"], g["draft"])
        legacy = legacy_predict(g["length"], g["beam"], g["draft"], g["cb"], 5.0)["wetted_surface_m2"]
        rows.append({
            "hull": name,
            "S_legacy_m2": round(legacy, 2),
            "S_holtrop_m2": round(fixed, 2),
            "S_box_bound_m2": round(box, 2),
            "legacy_exceeds_box": legacy > box,
            "legacy_vs_box_pct": round((legacy - box) / box * 100.0, 2),
            "holtrop_vs_box_pct": round((fixed - box) / box * 100.0, 2),
        })
    # Cb sweep for the figure
    sweep = []
    for i in range(11):
        cb = 0.50 + 0.04 * i
        g = dict(length=200.0, beam=32.0, draft=11.0)
        sweep.append({
            "cb": round(cb, 3),
            "S_legacy": round(legacy_predict(g["length"], g["beam"], g["draft"], cb, 5.0)["wetted_surface_m2"], 1),
            "S_holtrop": round(wetted_surface_holtrop(g["length"], g["beam"], g["draft"], cb), 1),
            "S_box": round(wetted_surface_box_bound(g["length"], g["beam"], g["draft"]), 1),
        })
    return {"rows": rows, "sweep": sweep}


def gate_table() -> dict:
    out = {}
    for hull in ("KVLCC2", "SUBOFF", "DTMB-5415"):
        res = validate_benchmark(hull)
        out[hull] = res
    return out


def stability_table() -> dict:
    beam, draft, depth, kg = 20.0, 8.0, 12.0, 6.0
    bm, gm = box_metacentric_height(beam, draft, kg)
    limit = deck_edge_heel_deg(beam, depth - draft)
    rows = []
    for heel in (0.5, 2.0, 5.0, 10.0, 15.0, 20.0, 21.0, 22.0, 25.0, 30.0, 45.0, 60.0):
        num = numeric_gz_box(beam, draft, depth, kg, heel)
        closed = wall_sided_gz(gm, bm, heel)
        rows.append({
            "heel_deg": heel,
            "gz_numeric_m": round(num, 6),
            "gz_wall_sided_m": round(closed, 6),
            "abs_diff_m": float(f"{abs(num - closed):.3e}"),
            "rel_diff_pct": round(abs(num - closed) / abs(closed) * 100.0, 4) if closed else 0.0,
            "within_validity": heel <= limit + 1e-9,
        })
    return {
        "box": {"beam_m": beam, "draft_m": draft, "depth_m": depth, "kg_m": kg,
                "bm_m": round(bm, 4), "gm_m": round(gm, 4),
                "deck_edge_heel_deg": round(limit, 3)},
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def fig_friction(ftab: dict) -> str:
    re = [r["Re"] for r in ftab["rows"]]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.loglog(re, [r["cf_ittc57"] for r in ftab["rows"]], "o-", label="ITTC-1957 (model)")
    ax.loglog(re, [r["cf_schoenherr"] for r in ftab["rows"]], "s--", label="Schoenherr (1932)")
    ax.set_xlabel("Re")
    ax.set_ylabel("C_F")
    ax.set_title("Friction lines: platform implementation vs independent line")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    p = FIG / "fig_friction_lines.png"
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return str(p.relative_to(OUT.parents[0]))


def fig_surface(stab: dict) -> str:
    sweep = stab["sweep"]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot([s["cb"] for s in sweep], [s["S_box"] for s in sweep], "k--", label="box-hull bound (Cb=1 limit)")
    ax.plot([s["cb"] for s in sweep], [s["S_holtrop"] for s in sweep], "o-", label="Holtrop-Mennen 1982 (fixed)")
    ax.plot([s["cb"] for s in sweep], [s["S_legacy"] for s in sweep], "^-", label="legacy pi*B*L*(0.5+0.25Cb)")
    ax.set_xlabel("Cb  (L=200 m, B=32 m, T=11 m)")
    ax.set_ylabel("wetted surface S [m$^2$]")
    ax.set_title("Wetted surface: legacy formula crosses the physical bound")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)
    p = FIG / "fig_wetted_surface.png"
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return str(p.relative_to(OUT.parents[0]))


def fig_gate(gate: dict) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.2))
    for ax, hull in zip(axes, ("KVLCC2", "SUBOFF")):
        res = gate[hull]["results"]
        fn = [r["froude_number"] for r in res]
        ax.plot(fn, [r["ref_ct"] for r in res], "ks", label="experiment (reference Ct)")
        ax.plot(fn, [r["calc_ct"] for r in res], "o-", label="platform (viscous lower bound)")
        for r in res:
            legacy = legacy_predict(
                *({"KVLCC2": (320.0, 58.0, 20.8, 0.8098),
                   "SUBOFF": (4.356, 0.508, 0.508, 0.508)}[hull]),
                r["speed_ms"],
            )
            ax.plot([r["froude_number"]], [legacy["ct_total"]], "x", color="tab:red")
        ax.plot([], [], "x", color="tab:red", label="legacy model")
        ax.set_title(f"{hull}: C_T vs F_n")
        ax.set_xlabel("F_n"); ax.set_ylabel("C_T")
        ax.grid(alpha=0.3); ax.legend(fontsize=7)
    p = FIG / "fig_gate_ct_comparison.png"
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return str(p.relative_to(OUT.parents[0]))


def fig_stability(st: dict) -> str:
    rows = st["rows"]
    heel = [r["heel_deg"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot(heel, [r["gz_wall_sided_m"] for r in rows], "o-", label="wall-sided closed form (API)")
    ax.plot(heel, [r["gz_numeric_m"] for r in rows], "s--", label="numeric section integration")
    ax.axvline(st["box"]["deck_edge_heel_deg"], color="k", ls=":",
               label=f"deck-edge limit {st['box']['deck_edge_heel_deg']:.1f} deg")
    ax.set_xlabel("heel angle [deg]"); ax.set_ylabel("GZ [m]")
    ax.set_title("Stability: closed form vs independent hydrostatics")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)
    p = FIG / "fig_gz_validity.png"
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return str(p.relative_to(OUT.parents[0]))


def main() -> dict:
    ftab = friction_table()
    stab = surface_table()
    gate = gate_table()
    st = stability_table()
    payload = {
        "generated": str(date.today()),
        "friction": ftab,
        "wetted_surface": stab,
        "gate": gate,
        "stability": st,
        "figures": {
            "friction": fig_friction(ftab),
            "surface": fig_surface(stab),
            "gate": fig_gate(gate),
            "stability": fig_stability(st),
        },
    }
    target = OUT / f"results_{payload['generated']}.json"
    target.write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: payload["figures"][k] for k in payload["figures"]}, indent=2))
    print("written:", target)
    return payload


if __name__ == "__main__":
    main()