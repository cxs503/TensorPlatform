"""Verification harness: platform righting-arm endpoint vs independent hydrostatics.

Runs the *real* endpoint coroutine ``backend/api/calculations.py::calc_stability``
headlessly (only the timing-middleware module is stubbed, because importing the
``services`` package pulls the DB engine) and compares it against
``tools/verify/stability_gz_reference.py``, which recomputes GZ from section
geometry rather than from the closed form under test.

Cases
-----
* PONTOON -- rectangular section, i.e. the wall-sided hull family for which the
  closed form is exact.  Agreement proves the implementation; the sweep also
  locates the end of the validity envelope (bilge emergence / deck immersion).
* FLARE -- trapezoid section (8 m bottom -> 12 m deck).  Same formula applied to
  a hull that is *not* wall-sided: quantifies the error a real hull suffers and
  motivates an explicit validity guard.

Outputs
-------
* ``artifacts/stability_gz_verification.json`` -- machine-readable evidence.
* ``artifacts/stability_gz_verification.png`` -- GZ curves + error panel.

Usage::

    python3 tools/verify/run_stability_verification.py [--outdir artifacts]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stability_gz_reference import (  # noqa: E402
    BoxSection,
    FlareSection,
    gz_curve,
    imo_is_code_criteria,
    righting_arm,
    validity_limit_angle,
    wall_sided_area,
    wall_sided_gz,
)

PONTOON = dict(beam=10.0, depth=4.0, draught=2.0, kg=3.0, disp_t=1000.0)
FLARE = dict(beam_bottom=8.0, beam_deck=12.0, depth=4.0, draught=2.0, kg=3.0,
             disp_t=1000.0)


# ---------------------------------------------------------------------------
# Load the production endpoint without booting the service container
# ---------------------------------------------------------------------------

def load_endpoint():
    """Import the real ``calc_stability`` coroutine by file path."""
    backend = REPO / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    def load_by_path(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    if "services.ship_timing" not in sys.modules:
        timing = load_by_path("services.ship_timing",
                              backend / "services" / "ship_timing.py")
        pkg = types.ModuleType("services")
        pkg.ship_timing = timing
        sys.modules["services"] = pkg
        sys.modules["services.ship_timing"] = timing

    return load_by_path("platform_calculations", backend / "api" / "calculations.py")


def api_gz(mod, *, displacement_t: float, gm: float, kb: float, bm: float,
           heel_deg: float) -> dict:
    req = mod.StabilityRequest(
        displacement_t=displacement_t, gm_m=gm, kb_m=kb, bm_m=bm,
        heel_angle_deg=heel_deg,
    )
    resp = asyncio.run(mod.calc_stability(req))
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------

def build_case(kind: str):
    if kind == "pontoon":
        sec = BoxSection(**{k: PONTOON[k] for k in ("beam", "depth", "draught")})
        kg, disp = PONTOON["kg"], PONTOON["disp_t"]
    else:
        sec = FlareSection(**{k: FLARE[k] for k in
                              ("beam_bottom", "beam_deck", "depth", "draught")})
        kg, disp = FLARE["kg"], FLARE["disp_t"]
    kb, bm = sec.kb, sec.bm
    return sec, kg, disp, kb, bm, kb + bm - kg


def analytic_limit_deg(sec) -> float:
    """Closed-form end of the wall-sided envelope for a trapezoid section.

    Bilge emergence happens when ``tan(phi) = 2*T / b_bottom``; deck-edge
    immersion when ``tan(phi) = 2*(D-T) / b_deck``.  The envelope ends at the
    smaller of the two.
    """
    b_bottom = (sec.beam if isinstance(sec, BoxSection) else sec.beam_bottom)
    b_deck = (sec.beam if isinstance(sec, BoxSection) else sec.beam_deck)
    cands = []
    if sec.draught > 0:
        cands.append(math.atan(2.0 * sec.draught / b_bottom))
    freeboard = sec.depth - sec.draught
    if freeboard > 1e-9:
        cands.append(math.atan(2.0 * freeboard / b_deck))
    return math.degrees(min(cands)) if cands else 0.0


def sweep(mod, kind: str, angles_deg, limit_deg: float):
    sec, kg, disp, kb, bm, gm = build_case(kind)
    rows = []
    for a in angles_deg:
        phi = math.radians(a)
        ref = righting_arm(sec, phi, kg)
        api = api_gz(mod, displacement_t=disp, gm=gm, kb=kb, bm=bm, heel_deg=a)
        val = api["righting_arm_m"]
        rel = 100.0 * (val - ref) / abs(ref) if abs(ref) > 1e-6 else None
        rows.append({
            "heel_deg": a,
            "gz_reference_m": round(ref, 9),
            "gz_api_m": round(val, 9),
            "abs_err_m": round(abs(val - ref), 9),
            "rel_err_pct": None if rel is None else round(rel, 4),
            "righting_moment_knm": api["righting_moment_knm"],
            "rm_reference_knm": round(disp * 9810.0 * ref / 1000.0, 4),
            "inside_envelope": a <= limit_deg + 1e-9,
        })
    meta = {
        "kind": kind,
        "kb_m": round(kb, 6), "bm_m": round(bm, 6), "kg_m": round(kg, 6),
        "gm_m": round(gm, 6), "displacement_t": disp,
        "envelope_end_deg": round(limit_deg, 4),
        "envelope_end_analytic_deg": round(analytic_limit_deg(sec), 4),
    }
    return meta, rows


def metrics(rows, label=""):
    """Error summary that is robust against near-zero reference arms.

    ``max_abs_rel_err_pct`` alone explodes when the true GZ passes through zero
    (large heel), which is technically true but useless as a yardstick.  We
    therefore also report the worst error restricted to angles where the
    reference arm is at least 10 % of its maximum, plus the first angle at which
    the endpoint exceeds the 15 % acceptance band.
    """
    ref_max = max(abs(r["gz_reference_m"]) for r in rows) or 1.0
    sig = [r for r in rows
           if r["rel_err_pct"] is not None
           and abs(r["gz_reference_m"]) >= 0.1 * ref_max]
    over = [r["heel_deg"] for r in rows
            if r["rel_err_pct"] is not None and abs(r["rel_err_pct"]) > 15.0]
    all_vals = [abs(r["rel_err_pct"]) for r in rows if r["rel_err_pct"] is not None]
    return {
        "label": label,
        "max_abs_rel_err_pct": round(max(all_vals), 4) if all_vals else None,
        "max_abs_rel_err_pct_significant": round(max(abs(r["rel_err_pct"]) for r in sig), 4)
        if sig else None,
        "max_abs_err_m": round(max(r["abs_err_m"] for r in rows), 6),
        "first_angle_over_15pct_deg": over[0] if over else None,
        "n_angles": len(rows),
    }


def worst(rows):
    vals = [abs(r["rel_err_pct"]) for r in rows if r["rel_err_pct"] is not None]
    return round(max(vals), 4) if vals else None


def imo_block(kind: str, angles_deg):
    sec, kg, _disp, _kb, bm, gm = build_case(kind)
    gz = gz_curve(sec, kg, angles_deg)
    ref = imo_is_code_criteria(list(angles_deg), gz, gm)
    ws_curve = [wall_sided_gz(math.radians(a), gm, bm) for a in angles_deg]
    ws = imo_is_code_criteria(list(angles_deg), ws_curve, gm)
    limit = validity_limit_angle(sec)["angle_deg"]
    area_analytic = (round(wall_sided_area(math.radians(30.0), gm, bm), 6)
                     if limit >= 30.0 else None)
    return {
        "reference_curve": ref,
        "wall_sided_curve": ws,
        "area_0_30_numeric_m_rad": ref["area_0_30_m_rad"],
        "area_0_30_wall_sided_analytic_m_rad": area_analytic,
        "area_0_30_consistency_pct": (None if area_analytic is None else round(
            100.0 * (area_analytic - ref["area_0_30_m_rad"])
            / ref["area_0_30_m_rad"], 4)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=str(REPO / "artifacts"))
    args = ap.parse_args(argv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    mod = load_endpoint()

    # --- small-angle sanity: 1e-3 deg must equal GM*sin(phi) ---------------
    sec_p, kg_p, disp_p, kb_p, bm_p, gm_p = build_case("pontoon")
    small = api_gz(mod, displacement_t=disp_p, gm=gm_p, kb=kb_p, bm=bm_p,
                   heel_deg=1e-3)
    sanity = {
        "kb_m": round(kb_p, 6), "bm_m": round(bm_p, 6),
        "bm_analytic_B2_12T": round(PONTOON["beam"] ** 2 / (12 * PONTOON["draught"]), 6),
        "gz_api_1e-3_deg": small["righting_arm_m"],
        "gm_sin_phi": round(gm_p * math.sin(math.radians(1e-3)), 12),
        "gm_echoed": small["gm_effective_m"],
    }

    # --- pontoon: inside the envelope -------------------------------------
    lim = validity_limit_angle(sec_p)
    lim_deg = lim["angle_deg"]
    env_angles = sorted({0.0, 5.0, 10.0, 15.0, 20.0,
                         round(1.0, 2) if False else 0.0, round(lim_deg, 2)})
    env_angles = sorted({a for a in [0.0, 5.0, 10.0, 15.0, 20.0, round(lim_deg, 2)]
                         if a <= lim_deg + 1e-9})
    env_meta, env_rows = sweep(mod, "pontoon", env_angles, lim_deg)
    env_meta["envelope_event"] = lim["event"]
    env_meta["envelope_end_numeric_vs_analytic_delta_deg"] = round(
        abs(env_meta["envelope_end_deg"] - env_meta["envelope_end_analytic_deg"]), 6)

    # --- pontoon: beyond the envelope -------------------------------------
    post_angles = [a for a in [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 60.0, 70.0, 80.0]
                   if a > lim_deg + 1e-9]
    _, post_rows = sweep(mod, "pontoon", post_angles, lim_deg)

    # --- flare (not wall-sided) -------------------------------------------
    sec_f = build_case("flare")[0]
    lim_f = validity_limit_angle(sec_f)
    flare_angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 26.0,
                    40.0, 50.0, 60.0, 75.0]
    flare_meta, flare_rows = sweep(mod, "flare", flare_angles, lim_f["angle_deg"])
    flare_meta["envelope_event"] = lim_f["event"]
    flare_meta["waterline_beam_m"] = round(sec_f.waterline_beam, 6)

    # --- IMO IS Code 2008 criteria ----------------------------------------
    curve_angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0]
    imo_pontoon = imo_block("pontoon", curve_angles)
    imo_flare = imo_block("flare", curve_angles)

    payload = {
        "mission": "m1392",
        "subject": "calc.stability righting-arm endpoint (backend/api/calculations.py)",
        "reference_implementation": "tools/verify/stability_gz_reference.py "
                                    "(heeled-section clipping, area conservation, "
                                    "first moment about G)",
        "sanity_small_angle": sanity,
        "pontoon_in_envelope": {
            "meta": env_meta, "rows": env_rows,
            "max_abs_rel_err_pct": worst(env_rows),
            "metrics": metrics(env_rows, "pontoon inside envelope"),
            "acceptance_pct": 3.0,
            "pass": (worst(env_rows) or 0.0) < 3.0,
        },
        "pontoon_beyond_envelope": {
            "angles_deg": post_angles, "rows": post_rows,
            "max_abs_rel_err_pct": worst(post_rows),
            "metrics": metrics(post_rows, "pontoon beyond envelope"),
            "note": "the closed form keeps growing like tan^2(phi) after the "
                    "waterplane starts shrinking; the endpoint still returns it "
                    "unflagged",
        },
        "flare_section": {
            "meta": flare_meta, "rows": flare_rows,
            "max_abs_rel_err_pct": worst(flare_rows),
            "metrics": metrics(flare_rows, "flare section"),
            "analytic_envelope_applicable": False,
            "analytic_envelope_note": "the tan(phi)=2h/b closed form assumes "
                                      "vertical sides; only the pontoon case is "
                                      "checked against it",
        },
        "imo_is_code_2008": {"pontoon": imo_pontoon, "flare": imo_flare},
        "api_observations": {
            "kb_m_parameter_used": False,
            "kb_note": "calc_stability accepts kb_m but never uses it; KG is "
                       "implicit in the caller-supplied gm_m.",
            "gm_effective_m_is_echo": True,
            "single_angle_only": True,
            "validity_guard": False,
            "imo_criteria_endpoint": False,
        },
    }

    js = outdir / "stability_gz_verification.json"
    js.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    _plot(outdir / "stability_gz_verification.png", env_rows, post_rows,
          flare_rows, env_meta, flare_meta)

    print(json.dumps({
        "json": str(js),
        "pontoon_max_err_pct": payload["pontoon_in_envelope"]["max_abs_rel_err_pct"],
        "envelope_end_deg": env_meta["envelope_end_deg"],
        "envelope_event": env_meta["envelope_event"],
        "post_envelope_max_err_pct":
            payload["pontoon_beyond_envelope"]["max_abs_rel_err_pct"],
        "flare_max_err_pct": payload["flare_section"]["max_abs_rel_err_pct"],
        "imo_pontoon_all_pass": imo_pontoon["reference_curve"]["all_pass"],
        "imo_flare_all_pass": imo_flare["reference_curve"]["all_pass"],
    }, indent=2))
    return 0


def _plot(path: Path, env_rows, post_rows, flare_rows, env_meta, flare_meta) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2))
    ax = axes[0]
    ev = [r["heel_deg"] for r in env_rows]
    pv = [r["heel_deg"] for r in post_rows]
    ax.plot(ev + pv, [r["gz_reference_m"] for r in env_rows + post_rows], "o-",
            color="#1f77b4", ms=4, label="pontoon · reference integration")
    ax.plot(ev + pv, [r["gz_api_m"] for r in env_rows + post_rows], "x--",
            color="#d62728", ms=6, label="pontoon · platform endpoint")
    fv = [r["heel_deg"] for r in flare_rows]
    ax.plot(fv, [r["gz_reference_m"] for r in flare_rows], "s-", color="#2ca02c",
            ms=4, label="flare · reference integration")
    ax.plot(fv, [r["gz_api_m"] for r in flare_rows], "^--", color="#ff7f0e",
            ms=5, label="flare · platform endpoint")
    ax.axvline(env_meta["envelope_end_deg"], color="gray", ls=":",
               label=f"envelope end ({env_meta['envelope_end_deg']:.1f}° "
                     f"{env_meta['envelope_event']})")
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("righting arm GZ [m]")
    ax.set_title("Righting arm: endpoint vs independent hydrostatics")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.plot(ev, [r["rel_err_pct"] for r in env_rows], "o-", color="#1f77b4",
            label="pontoon · inside envelope")
    if post_rows:
        ax.plot(pv, [r["rel_err_pct"] for r in post_rows], "x--", color="#d62728",
                label="pontoon · beyond envelope")
    ax.plot(fv, [r["rel_err_pct"] for r in flare_rows], "s-", color="#2ca02c",
            label="flare · not wall-sided")
    ax.axhline(0.0, color="k", lw=0.6)
    ax.axhline(3.0, color="orange", ls="--", lw=1.2, label="3% acceptance")
    ax.axhline(15.0, color="red", ls="--", lw=1.2, label="15% hard limit")
    ax.set_yscale("symlog", linthresh=1.0)
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("relative error of GZ [%]")
    ax.set_title("Verification error of calc.stability")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    env_errs = [abs(r["rel_err_pct"]) for r in env_rows
                if r["rel_err_pct"] is not None]
    fig.suptitle("m1392 · stability verification — pontoon max |err| inside "
                 f"envelope = {max(env_errs):.2e} % (n={len(env_rows)} angles)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())