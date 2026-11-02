#!/usr/bin/env python3
"""MOCK-fingerprint gate for ship-performance API endpoints.

Why this module exists
----------------------
The platform self-declares capability evidence levels (``audit_capabilities``:
REAL/MOCK/STUB).  Historical audits of the ship-performance serving layer showed
that a self-declared REAL endpoint can in fact return synthesised numbers: the
open-water propeller endpoint answered ``KQ = KT / 10`` exactly for every
advance ratio and every design point, which makes
``eta0 = (J/2pi) * KT/KQ = J * 10 / (2 pi)`` a *pure linear function of J* that
is blind to pitch ratio, blade number and blade-area ratio, and which exceeds
the physical bound ``eta0 < 1`` for ``J > 0.628``.

Detectors implemented here are **reference-free physics invariants** that do not
need an experimental baseline:

* ``ratio_lock``            – two outputs stay locked in an exact ratio over a sweep
                              (a regression cannot do that: B-series KQ/KT varies
                              with J, P/D, Ae/Ao and Z);
* ``parameter_blindness``   – an output is invariant to a design parameter it must
                              depend on;
* ``physical_bound``        – an output violates a hard physical bound;
* ``reference_deviation``   – deviation against an independent reference
                              implementation (Wageningen B-series 39/47-term
                              regressions in ``tools/ref_anchor_lib.py``).

Each detector returns a dict with ``triggered: bool`` and the evidence numbers,
so a ledger (JSON) can be machine-audited and a report can quote it verbatim.

Usage
-----
    python3 tools/mock_fingerprint/__init__.py --probe     # live :8001 probe
                                                           # -> artifacts/mock_fingerprint/
No network access happens at import time; the pytest suite feeds synthetic
responses through the same detectors.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

SHIP_API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1").rstrip("/")
OUT_DIR = ROOT / "artifacts" / "mock_fingerprint"


# ─────────────────────────────────────────────────────────────────────────
# Detectors (pure functions over recorded responses)
# ─────────────────────────────────────────────────────────────────────────
def ratio_lock(records: Sequence[Dict], num: str, den: str,
               rel_tol: float = 1e-12, min_points: int = 3) -> Dict:
    """Detect ``records[i][num] / records[i][den] == const`` over the sweep."""
    ratios = []
    for r in records:
        n, d = r.get(num), r.get(den)
        if n is None or d in (None, 0):
            continue
        ratios.append(float(n) / float(d))
    if len(ratios) < min_points:
        return {"name": "ratio_lock", "triggered": False, "reason": "insufficient_points",
                "n_points": len(ratios)}
    lo, hi = min(ratios), max(ratios)
    spread = 0.0 if lo == hi else (hi - lo) / max(abs(lo), abs(hi))
    return {
        "name": "ratio_lock",
        "expr": f"{num}/{den}",
        "triggered": bool(spread <= rel_tol),
        "ratio": lo if spread <= rel_tol else None,
        "ratio_min": lo,
        "ratio_max": hi,
        "rel_spread": spread,
        "rel_tol": rel_tol,
        "n_points": len(ratios),
        "verdict": ("LOCKED (synthetic relation)" if spread <= rel_tol
                    else "ratio varies with the sweep (consistent with a regression)"),
    }


def parameter_blindness(records: Sequence[Dict], param: str, metric: str,
                        rel_tol: float = 1e-12) -> Dict:
    """Detect ``metric`` invariance across a sweep of an independent ``param``.

    ``records`` must be grouped: each record carries ``param`` and ``metric``.
    """
    pts = [(r[param], float(r[metric])) for r in records
           if r.get(param) is not None and r.get(metric) is not None]
    distinct = {round(v, 12) for _, v in pts}
    if len(pts) < 2:
        return {"name": "parameter_blindness", "triggered": False,
                "reason": "insufficient_points", "n_points": len(pts)}
    vmin, vmax = min(distinct), max(distinct)
    spread = 0.0 if vmin == vmax else (vmax - vmin) / max(abs(vmin), abs(vmax))
    return {
        "name": "parameter_blindness",
        "param": param,
        "metric": metric,
        "triggered": bool(spread <= rel_tol),
        "metric_min": vmin,
        "metric_max": vmax,
        "rel_spread": spread,
        "values": [{"param": p, "metric": m} for p, m in pts],
        "verdict": ("BLIND to %s (design parameter ignored)" % param if spread <= rel_tol
                    else "depends on %s" % param),
    }


def physical_bound(records: Sequence[Dict], metric: str, bound: float,
                   sense: str = "gt") -> Dict:
    """Detect violations of a hard physical bound (``gt``: value must stay <= bound)."""
    vals = [(r.get("J"), float(r[metric])) for r in records if r.get(metric) is not None]
    if sense == "gt":
        bad = [{"J": j, metric: v} for j, v in vals if v > bound]
    else:
        bad = [{"J": j, metric: v} for j, v in vals if v < bound]
    return {
        "name": "physical_bound",
        "metric": metric,
        "bound": bound,
        "sense": sense,
        "triggered": bool(bad),
        "n_violations": len(bad),
        "worst": max(bad, key=lambda d: d[metric]) if bad else None,
        "violations": bad,
        "verdict": ("bound violated at %d/%d points" % (len(bad), len(vals)) if bad
                    else "bound respected at all %d points" % len(vals)),
    }


def reference_deviation(records: Sequence[Dict], ref_fn, metrics: Sequence[str],
                        tol: float = 0.15) -> Dict:
    """Compare recorded values against an independent reference callable.

    ``ref_fn(record) -> {metric: value}``; deviation is reported per point and the
    detector triggers when **any** point exceeds ``tol`` relative error.
    """
    rows = []
    for r in records:
        ref = ref_fn(r)
        for m in metrics:
            if r.get(m) is None or ref.get(m) in (None, 0):
                continue
            rel = (float(r[m]) - float(ref[m])) / float(ref[m])
            rows.append({"J": r.get("J"), "metric": m, "platform": float(r[m]),
                         "reference": float(ref[m]), "rel_err": rel})
    worst = max(rows, key=lambda d: abs(d["rel_err"])) if rows else None
    n_fail = sum(1 for d in rows if abs(d["rel_err"]) > tol)
    return {
        "name": "reference_deviation",
        "tolerance": tol,
        "triggered": bool(n_fail),
        "n_fail": n_fail,
        "n_points": len(rows),
        "worst": worst,
        "rows": rows,
        "verdict": ("%d/%d points exceed %.0f%%" % (n_fail, len(rows), tol * 100) if n_fail
                    else "all %d points within %.0f%%" % (len(rows), tol * 100)),
    }


def clamp_plateau(records: Sequence[Dict], metric: str, j_min: float,
                  rel_tol: float = 1e-12) -> Dict:
    """Detect a hard clamp: ``metric`` pinned to one value over the upper sweep.

    A physical efficiency curve falls after its optimum; a value that stays bitwise
    identical over ``J >= j_min`` is a programme-level cap, not physics.
    """
    pts = [(r.get("J"), float(r[metric])) for r in records
           if r.get(metric) is not None and r.get("J") is not None and r["J"] >= j_min]
    if len(pts) < 3:
        return {"name": "clamp_plateau", "triggered": False, "reason": "insufficient_points",
                "n_points": len(pts)}
    vals = [v for _, v in pts]
    spread = (max(vals) - min(vals)) / max(abs(max(vals)), abs(min(vals)))
    return {
        "name": "clamp_plateau",
        "metric": metric,
        "j_min": j_min,
        "triggered": bool(spread <= rel_tol),
        "plateau_value": max(vals) if spread <= rel_tol else None,
        "rel_spread": spread,
        "n_points": len(pts),
        "points": [{"J": j, metric: v} for j, v in pts],
        "verdict": ("metric clamped at %g for J >= %.2f (%d points)" % (max(vals), j_min, len(pts))
                    if spread <= rel_tol else "metric varies over the upper sweep"),
    }


def judge(detectors: Sequence[Dict]) -> Dict:
    """Map fired detectors to an evidence level (any structural fingerprint => MOCK)."""
    fired = [d["name"] for d in detectors if d.get("triggered")]
    structural = {"ratio_lock", "parameter_blindness", "physical_bound", "clamp_plateau"}
    if structural & set(fired):
        level, why = "MOCK", "structural fingerprint fired: " + ", ".join(sorted(structural & set(fired)))
    elif "reference_deviation" in fired:
        level, why = "WITHHELD", "no structural fingerprint, but reference deviation > tolerance"
    else:
        level, why = "REAL", "no fingerprint fired and reference agreement within tolerance"
    return {"level": level, "reason": why, "fired": fired}


# ─────────────────────────────────────────────────────────────────────────
# Live probe of the open-water propeller endpoint
# ─────────────────────────────────────────────────────────────────────────
J_SWEEP = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]
DESIGNS = [
    {"Z": 4, "Ae_Ao": 0.40, "P_D": 1.0},
    {"Z": 4, "Ae_Ao": 0.70, "P_D": 1.0},
    {"Z": 5, "Ae_Ao": 0.75, "P_D": 1.4},
    {"Z": 4, "Ae_Ao": 0.70, "P_D": 0.8},
]
ENDPOINT = "propeller-design/wageningen-b"


def _post(path: str, body: Dict, timeout: float = 20.0):
    req = urllib.request.Request(
        f"{SHIP_API}/{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        return exc.code, {"detail": str(exc)}
    except Exception as exc:  # pragma: no cover - network dependent
        return -1, {"detail": repr(exc)}


def probe(endpoint: str = ENDPOINT, designs: Sequence[Dict] = tuple(DESIGNS),
          j_sweep: Sequence[float] = tuple(J_SWEEP)) -> Dict:
    """Query the endpoint over (J x design) and run all detectors."""
    import ref_anchor_lib as ref

    series, flat = {}, []
    for d in designs:
        key = f"Z{d['Z']}_Ae{d['Ae_Ao']:.2f}_PD{d['P_D']:.1f}"
        rows = []
        for J in j_sweep:
            code, data = _post(endpoint, {**d, "J": J})
            row = {"J": J, "design": d, "status": code}
            if code == 200 and isinstance(data, dict):
                row.update({k: data.get(k) for k in ("KT", "KQ", "eta0", "method")})
            else:
                row["error"] = str(data)[:200]
            rows.append(row)
            if row.get("KT") is not None:
                flat.append({**row, "design_key": key})
        series[key] = rows

    detectors: List[Dict] = []
    # D1: exact ratio lock KQ/KT over the whole sweep (J > 0 points only)
    nonzero = [r for r in flat if r["J"] > 0 and r.get("KT")]
    d1 = ratio_lock(nonzero, "KQ", "KT")
    d1["scope"] = "all designs, J>0"
    detectors.append(d1)

    # D2: eta0 blind to the design parameters (fixed J = 0.5)
    jref = min((J for J in j_sweep if J > 0), key=lambda J: abs(J - 0.5))
    at_j = [{"P_D": r["design"]["P_D"], "eta0": r["eta0"], "KT": r["KT"], "KQ": r["KQ"],
             "design_key": r["design_key"]} for r in flat if abs(r["J"] - jref) < 1e-9]
    detectors.append(parameter_blindness(at_j, "P_D", "eta0"))
    detectors.append(parameter_blindness(at_j, "P_D", "KT"))

    # D3: eta0 = (J/2pi) KT/KQ must stay below the physical bound 1.0
    detectors.append(physical_bound(flat, "eta0", 1.0))
    # D4: eta0 must vanish when KT does (no thrust => no efficiency)
    zero_thrust = [r for r in flat if (r.get("KT") or 0) <= 0]
    detectors.append({
        "name": "physical_bound",
        "metric": "eta0@KT<=0",
        "bound": 0.0,
        "sense": "gt",
        "triggered": any((r.get("eta0") or 0) > 1e-9 for r in zero_thrust),
        "n_violations": sum(1 for r in zero_thrust if (r.get("eta0") or 0) > 1e-9),
        "worst": max(zero_thrust, key=lambda r: r.get("eta0") or 0) if zero_thrust else None,
        "verdict": "eta0 non-zero without thrust" if any(
            (r.get("eta0") or 0) > 1e-9 for r in zero_thrust) else "consistent",
    })

    # D4b: eta0 clamped at a constant over the upper J range (programme cap, not physics)
    detectors.append(clamp_plateau(flat, "eta0", 0.8))

    # D5: reference deviation against the B4-70 (P/D=1.0, Ae/A0=0.70) regression
    def ref_fn(r):
        pt = ref.open_water_point(r["J"], P_D=1.0, Ae_Ao=0.70, Z=4)
        return {"KT": pt["KT"], "KQ": pt["KQ"],
                "eta0": pt["eta0"] if pt.get("KQ") else 0.0}

    b4 = [r for r in flat if r["design_key"] == "Z4_Ae0.70_PD1.0"]
    d5 = reference_deviation(b4, ref_fn, ("KT", "KQ"))
    d5["reference"] = "Wageningen B4-70 39/47-term regression (Oosterveld & van Oossanen 1975), tools/ref_anchor_lib.py"
    detectors.append(d5)

    # reference zero-thrust advance ratio (KT = 0 crossing of the regression)
    jzt = None
    grid = [i / 1000.0 for i in range(0, 1501)]
    prev = None
    for J in grid:
        kt = ref.b_series_kt(J, 1.0, 0.70, 4)
        if prev is not None and prev[1] > 0 >= kt:
            jzt = prev[0] + (J - prev[0]) * prev[1] / (prev[1] - kt)
            break
        prev = (J, kt)

    verdict = judge(detectors)
    return {
        "endpoint": f"{SHIP_API}/{endpoint}",
        "scenario": "open-water propeller (Wageningen B-series front door)",
        "j_sweep": list(j_sweep),
        "designs": list(designs),
        "series": series,
        "detectors": detectors,
        "verdict": verdict,
        "reference_j_zero_thrust": jzt,
    }


# ─────────────────────────────────────────────────────────────────────────
# Figure + CLI
# ─────────────────────────────────────────────────────────────────────────
def make_figure(ledger: Dict, path: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import ref_anchor_lib as ref

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    js = [i / 100 for i in range(0, 121)]
    ax = axes[0]
    ax.plot(js, [ref.b_series_kt(j, 1.0, 0.70, 4) for j in js], "k-", lw=2,
            label="B4-70 regression $K_T$")
    ax.plot(js, [10 * ref.b_series_kq(j, 1.0, 0.70, 4) for j in js], "k--", lw=2,
            label="B4-70 regression $10K_Q$")
    key = "Z4_Ae0.70_PD1.0"
    rows = [r for r in ledger["series"][key] if r.get("KT") is not None]
    ax.plot([r["J"] for r in rows], [r["KT"] for r in rows], "o", ms=5, color="#c0392b",
            label="endpoint $K_T$")
    ax.plot([r["J"] for r in rows], [10 * r["KQ"] for r in rows], "s", ms=4,
            color="#e67e22", label="endpoint $10K_Q$")
    ax.set_xlabel("advance ratio $J$"); ax.set_ylabel("$K_T$, $10K_Q$")
    ax.set_title("Open-water endpoint vs Wageningen B4-70 regression")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)

    ax = axes[1]
    jgrid = [i / 100 for i in range(1, 121)]
    ax.plot(jgrid, [j * 10.0 / (2 * math.pi) for j in jgrid], "k-", lw=1.5,
            label=r"synthetic law $\eta_0 = 10J/2\pi$")
    ax.plot(jgrid, [ref.open_water_point(j, 1.0, 0.70, 4).get("eta0", float("nan"))
                    for j in jgrid], "k--", lw=1.5, label="B4-70 regression $\\eta_0$")
    styles = ["o", "s", "^", "d"]
    for i, (k, srows) in enumerate(ledger["series"].items()):
        pts = [r for r in srows if r.get("eta0") is not None]
        ax.plot([r["J"] for r in pts], [r["eta0"] for r in pts], styles[i % 4], ms=4,
                label="endpoint " + k)
    ax.axhline(1.0, color="grey", ls=":", lw=1)
    ax.text(0.02, 1.02, r"physical bound $\eta_0<1$", fontsize=8, color="grey")
    ax.set_xlabel("advance ratio $J$"); ax.set_ylabel(r"open-water efficiency $\eta_0$")
    ax.set_title("Efficiency collapses onto one design-blind line")
    ax.grid(alpha=0.3); ax.legend(fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--probe" not in argv:
        print(__doc__)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger = probe()
    fig = make_figure(ledger, OUT_DIR / "propeller_endpoint_mock_gate.png")
    ledger["figure"] = str(Path(fig).relative_to(ROOT))
    (OUT_DIR / "propeller_endpoint_mock_gate.json").write_text(
        json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"verdict": ledger["verdict"],
                      "detectors": [{k: d[k] for k in ("name", "triggered", "verdict")}
                                    for d in ledger["detectors"]],
                      "figure": ledger["figure"]}, indent=1, ensure_ascii=False))
    return 0 if ledger["verdict"]["level"] == "REAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())