"""Offline absolute-accuracy grading for Wageningen B-series open-water paths.

Why this file exists
--------------------
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`` closed with one explicit
hole: *absolute* accuracy against published charts was **WITHHELD**, on the
stated grounds that the machine has no network and therefore no chart point
values are retrievable.  That conclusion was too pessimistic for this repo: the
Oosterveld & van Oossanen (1975) regression tables are already transcribed in
``tools/verify/propeller_open_water/reference_bseries.py`` (39 ``KT`` + 47
``KQ`` terms, cross-checked term-by-term against the platform's
``backend/data/official/Wageningen_Bseries_coeffs.dat``).  With those tables in
hand, *absolute* accuracy is computable offline — no network, no remembered
chart values.

This module turns that into a repeatable judgement:

    python3 tools/verify/propeller_open_water/absolute_accuracy.py \
        --live http://127.0.0.1:8001/api/v1 \
        --out outputs/propeller_open_water_absolute

It grades any open-water implementation against the transcribed polynomial over
a (P/D, Ae/A0, Z, J) grid and applies the platform's documented bar:

    PASS      mean and p95 relative error < 15 %   (platform acceptance bar)
    TIGHT     max relative error < 3 %             (publication-grade bar)
    FAIL      otherwise

Relative errors are only meaningful where the propeller is actually loaded, so
points with reference ``KT < KT_FLOOR`` (default 0.05) are reported separately
and excluded from the relative-error statistics — otherwise the near-zero-thrust
tail dominates the mean and hides the real agreement.

Honesty notes
-------------
* The polynomial is a *fit to* B-series model-test data, not the data itself; it
  is the accepted industry reference and is the best offline absolute anchor.
  Its provenance (one transcription, corroborated by a second transcription in
  the sibling repository) is recorded in ``reference_bseries.py``.
* ``git``-tracked numbers only: nothing here is hand-copied into the docs; the
  Markdown tables are generated from the emitted JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

#: Platform acceptance bar and publication bar (fractions, not percent).
BAR_ACCEPT = 0.15
BAR_TIGHT = 0.03

#: Upper edge of the "design" (normally loaded) advance-ratio region.
DESIGN_J = 0.75

#: Relative errors are only taken where the reference thrust is non-negligible.
KT_FLOOR = 0.05

#: Advance-ratio grid; the polynomial validity box is J = 0..1.5.
GRID_J: Tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 25))

#: (P/D, Ae/A0, Z) grid, kept inside the polynomial validity box.
GRID_GEOM: Tuple[Tuple[float, float, int], ...] = (
    (0.80, 0.55, 4),
    (1.00, 0.35, 4),
    (1.00, 0.55, 4),
    (1.00, 0.75, 4),
    (1.00, 0.55, 5),
    (1.20, 0.55, 4),
)

PointFn = Callable[[float, float, float, int], Dict[str, float]]


# --------------------------------------------------------------------------- #
# implementations under test
# --------------------------------------------------------------------------- #
def reference_point(J: float, P_D: float, Ae_Ao: float, Z: int) -> Dict[str, float]:
    """The transcribed published polynomial (the anchor)."""
    d = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
    return {"KT": d["KT"], "KQ": d["KQ"], "eta0": d["eta_0"]}


def bem_point(J: float, P_D: float, Ae_Ao: float, Z: int,
              sections: int = 40) -> Dict[str, float]:
    """The in-repo blade-element / momentum model.

    Geometry is mapped as: constant-pitch helix of pitch ratio ``P/D``, declared
    blade-area ratio honoured exactly, ``Z`` blades, hub at ``r/R = 0.18``.  That
    is *not* the B-series chord/pitch distribution (which is wider at the tip),
    so a residual difference is expected and is the subject of the grading.
    """
    from backend.services.propeller_open_water import (  # local import: optional dep
        PropellerGeometry,
        open_water_point,
    )

    geom = PropellerGeometry(
        diameter_m=5.0,
        pitch_ratio=P_D,
        blade_area_ratio=Ae_Ao,
        blade_count=int(Z),
        sections=sections,
    )
    d = open_water_point(geom, J, rho=1025.0, n_rps=2.0)
    return {"KT": d["KT"], "KQ": d["KQ"], "eta0": d["eta0"]}


def live_endpoint_point(base_url: str, timeout: float = 15.0) -> PointFn:
    """Wrap the running platform's ``/propeller-design/wageningen-b`` endpoint."""
    import urllib.error
    import urllib.request

    base = base_url.rstrip("/")

    def _point(J: float, P_D: float, Ae_Ao: float, Z: int) -> Dict[str, float]:
        body = json.dumps({
            "P_D": P_D, "Z": int(Z), "Ae_Ao": Ae_Ao, "D": 5.0, "n": 120.0, "J": J,
        }).encode()
        req = urllib.request.Request(
            base + "/propeller-design/wageningen-b", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
        data = payload.get("data", payload)
        if isinstance(data, str):
            data = json.loads(data)
        return {"KT": float(data["KT"]), "KQ": float(data["KQ"]),
                "eta0": float(data["eta0"])}

    return _point


# --------------------------------------------------------------------------- #
# grading
# --------------------------------------------------------------------------- #
def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else float("nan")


def _stats(values: Sequence[float]) -> Dict[str, float]:
    vals = sorted(v for v in values if not math.isnan(v))
    if not vals:
        return {"n": 0, "mean": float("nan"), "p95": float("nan"), "max": float("nan")}
    idx = min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "p95": vals[idx],
        "max": vals[-1],
    }


def grade(fn: PointFn, label: str,
          grid_j: Iterable[float] = GRID_J,
          grid_geom: Iterable[Tuple[float, float, int]] = GRID_GEOM,
          kt_floor: float = KT_FLOOR) -> Dict[str, object]:
    """Grade ``fn`` against the transcribed polynomial; return a JSON-able report."""
    points: List[Dict[str, object]] = []
    # region split: the "design" region is the normally loaded propeller used by
    # ship-propulsion matching (J <= DESIGN_J); beyond it the prop is at light
    # load, KT -> 0 and relative errors are ill-conditioned.
    errs: Dict[str, List[float]] = {"KT": [], "KQ": [], "eta0": []}
    errs_design: Dict[str, List[float]] = {"KT": [], "KQ": [], "eta0": []}
    excluded = 0
    for P_D, Ae_Ao, Z in grid_geom:
        for J in grid_j:
            r = reference_point(J, P_D, Ae_Ao, Z)
            got = fn(J, P_D, Ae_Ao, Z)
            row = {
                "P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z, "J": J,
                "KT_ref": r["KT"], "KT": got["KT"],
                "KQ_ref": r["KQ"], "KQ": got["KQ"],
                "eta0_ref": r["eta0"], "eta0": got["eta0"],
            }
            loaded = r["KT"] >= kt_floor
            row["loaded"] = loaded
            if loaded:
                for q in ("KT", "KQ", "eta0"):
                    e = _rel(got[q], r[q])
                    # efficiency is ill-conditioned near J -> 0 (eta0_ref -> 0)
                    if q == "eta0" and abs(r["eta0"]) < 0.10:
                        continue
                    row[q + "_err"] = e
                    if not math.isnan(e):
                        errs[q].append(e)
                        if J <= DESIGN_J:
                            errs_design[q].append(e)
            else:
                excluded += 1
            points.append(row)

    def _block(vals: List[float]) -> Dict[str, float]:
        st = _stats(vals)
        st["within_15pct"] = (sum(1 for v in vals if v < BAR_ACCEPT) / len(vals)) if vals else float("nan")
        st["within_3pct"] = (sum(1 for v in vals if v < BAR_TIGHT) / len(vals)) if vals else float("nan")
        v = "FAIL"
        if st["n"] and st["max"] < BAR_TIGHT:
            v = "TIGHT"
        elif st["n"] and st["mean"] < BAR_ACCEPT and st["p95"] < BAR_ACCEPT:
            v = "PASS"
        st["verdict"] = v
        return st

    summary: Dict[str, object] = {}
    for q, vals in errs.items():
        st = _block(vals)
        st["design_region"] = _block(errs_design[q])
        summary[q] = st

    worst = max(
        (p for p in points if p.get("KT_err") is not None),
        key=lambda p: p["KT_err"], default=None)
    return {
        "implementation": label,
        "kt_floor": kt_floor,
        "points_total": len(points),
        "points_excluded_unloaded": excluded,
        "summary": summary,
        "worst_KT_point": worst,
        "points": points,
    }


def verdict(report: Dict[str, object]) -> str:
    """Collapse per-quantity verdicts into one word for the whole implementation."""
    vs = [report["summary"][q]["verdict"] for q in ("KT", "KQ", "eta0")]  # type: ignore[index]
    if all(v == "TIGHT" for v in vs):
        return "TIGHT"
    if any(v == "FAIL" for v in vs):
        return "FAIL"
    return "PASS"


def print_report(report: Dict[str, object]) -> None:
    print(f"\n=== {report['implementation']}  ->  {verdict(report)} ===")
    print(f"points: {report['points_total']}  unloaded/excluded: {report['points_excluded_unloaded']}")
    print(f"{'q':5s} {'n':>4s} {'mean%':>8s} {'p95%':>8s} {'max%':>8s} "
          f"{'<15%':>6s} {'<3%':>6s}  verdict | design region J<={DESIGN_J:.2f}")
    for q in ("KT", "KQ", "eta0"):
        s = report["summary"][q]  # type: ignore[index]
        d = s["design_region"]
        print(f"{q:5s} {s['n']:4d} {100*s['mean']:8.2f} {100*s['p95']:8.2f} "
              f"{100*s['max']:8.2f} {100*s['within_15pct']:6.0f} "
              f"{100*s['within_3pct']:6.0f}  {s['verdict']:6s} | "
              f"n={d['n']:3d} mean={100*d['mean']:6.2f}% p95={100*d['p95']:6.2f}% "
              f"max={100*d['max']:6.2f}% {d['verdict']}")


def make_figures(reports: Sequence[Dict[str, object]], outdir: Path) -> List[Path]:
    """KT / KQ / eta0 vs J and relative-error bands, one case per panel."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    # baseline case used for the shape plots
    case = (1.00, 0.55, 4)
    paths: List[Path] = []

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.0))
    for ax, q in zip(axes, ("KT", "KQ", "eta0")):
        for rep in reports:
            rows = [p for p in rep["points"]  # type: ignore[index]
                    if (p["P_D"], p["Ae_Ao"], p["Z"]) == case]
            js = [p["J"] for p in rows]
            ax.plot(js, [p[q] for p in rows], marker="o", ms=2.6, lw=1.4,
                    label=str(rep["implementation"]))
        rows = [p for p in reports[0]["points"]  # type: ignore[index]
                if (p["P_D"], p["Ae_Ao"], p["Z"]) == case]
        ax.plot([p["J"] for p in rows], [p[q + "_ref"] for p in rows],
                "k-", lw=2.2, label="Oosterveld & van Oossanen (1975)")
        ax.set_xlabel("J = V_a/(nD)")
        ax.set_ylabel(q)
        ax.grid(alpha=0.3)
        ax.set_title(f"{q}(J)  —  P/D=1.0, Ae/A0=0.55, Z=4")
    axes[0].legend(fontsize=7.5)
    fig.suptitle("Wageningen B4-55 open water: transcribed reference vs implementations")
    fig.tight_layout()
    p1 = outdir / "open_water_curves_vs_reference.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.0))
    for ax, q in zip(axes, ("KT", "KQ", "eta0")):
        rows = [p for p in reports[0]["points"]  # type: ignore[index]
                if (p["P_D"], p["Ae_Ao"], p["Z"]) == case]
        for rep in reports[1:]:
            r2 = [p for p in rep["points"]  # type: ignore[index]
                  if (p["P_D"], p["Ae_Ao"], p["Z"]) == case]
            js = [p["J"] for p in r2 if p.get(q + "_err") is not None]
            es = [100 * p[q + "_err"] for p in r2 if p.get(q + "_err") is not None]
            ax.plot(js, es, marker="s", ms=2.6, lw=1.2, label=str(rep["implementation"]))
        ax.axhline(15, color="r", ls="--", lw=1.0, label="15% acceptance bar")
        ax.axhline(-15, color="r", ls="--", lw=1.0)
        ax.axhline(3, color="g", ls=":", lw=1.0)
        ax.axhline(-3, color="g", ls=":", lw=1.0)
        ax.set_xlabel("J")
        ax.set_ylabel(f"relative error {q} [%]")
        ax.set_title(f"{q} error vs reference")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7.5)
    fig.tight_layout()
    p2 = outdir / "open_water_relative_errors.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", help="platform base URL, e.g. http://127.0.0.1:8001/api/v1")
    ap.add_argument("--sections", type=int, default=40,
                    help="blade-element radial sections (discretisation check)")
    ap.add_argument("--out", default="outputs/propeller_open_water_absolute")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    reports: List[Dict[str, object]] = [
        grade(lambda J, p, a, z: bem_point(J, p, a, z, sections=args.sections),
              f"blade-element/momentum model (sections={args.sections})"),
    ]
    # discretisation ladder: gives a numerical-error bound for the BEM path
    ladder = []
    for s in (16, 40, 80):
        rep = grade(lambda J, p, a, z, s=s: bem_point(J, p, a, z, sections=s),
                    f"bem sections={s}")
        ladder.append({"sections": s,
                       "KT_mean_err": rep["summary"]["KT"]["mean"],  # type: ignore[index]
                       "KQ_mean_err": rep["summary"]["KQ"]["mean"],  # type: ignore[index]
                       "eta0_mean_err": rep["summary"]["eta0"]["mean"]})  # type: ignore[index]

    if args.live:
        reports.append(grade(live_endpoint_point(args.live),
                             f"live endpoint {args.live}/propeller-design/wageningen-b"))

    for rep in reports:
        print_report(rep)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    figures: List[Path] = []
    if not args.no_figures:
        figures = make_figures(reports, outdir)

    payload = {
        "bars": {"accept": BAR_ACCEPT, "tight": BAR_TIGHT, "kt_floor": KT_FLOOR},
        "grid_J": list(GRID_J),
        "grid_geometry": [list(g) for g in GRID_GEOM],
        "discretisation_ladder": ladder,
        "implementations": [
            {"label": rep["implementation"], "verdict": verdict(rep),
             "summary": rep["summary"], "points_excluded": rep["points_excluded_unloaded"],
             "worst_KT_point": rep["worst_KT_point"], "points": rep["points"]}
            for rep in reports
        ],
        "reference": "Oosterveld & van Oossanen (1975), as transcribed in "
                     "tools/verify/propeller_open_water/reference_bseries.py",
        "figures": [str(p) for p in figures],
    }
    json_path = outdir / "absolute_accuracy.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"\nwrote {json_path}")
    for p in figures:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())