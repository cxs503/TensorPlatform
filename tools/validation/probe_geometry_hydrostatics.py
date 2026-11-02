#!/usr/bin/env python3
"""Machine-adjudicable verification of the ship platform's geometry/hydrostatics surface.

Targets (audit_capabilities grades both ``REAL``):

* ``GET /api/v1/geometry/hydro-curves/{ship_name}``  -> ``geometry.hydro_curves``
* ``GET /api/v1/geometry/verify/{ship_name}``       -> ``geometry.verify_official``

Why anchors and not literature tables
-------------------------------------
Every criterion below is either an exact mathematical identity of hydrostatics or a
hard physical bound, so it is checkable offline and cannot be dismissed as "a table
value someone remembered":

A1 box bound            nabla(T) = W(T)/rho <= L*B*T            (body cannot displace
                                                                 more than its own
                                                                 bounding box)
A2 block-coefficient    Cb_reported == nabla/(L*B*T) and Cb <= 1
A3 design-point Cb      Cb(design draft) == meta Cb            (the same ship must have
                                                                 the same Cb twice)
A4 fundamental theorem  dW/dT / rho == Awp(T)                   (d(nabla)/dT = Awp
                                                                 exactly, by definition
                                                                 of waterplane area)
A5 wall-sided dichotomy if Awp is (numerically) constant over T then a prismatic body
                        requires VCB == T/2; a VCB that drifts with T while Awp does
                        not is an internal contradiction
A6 TPC identity         TPC == rho*Awp/100
A7 waterplane bound     Awp <= L*B
A8 curve hygiene        W strictly increasing, Awp >= 0, no quantisation step
                        coarser than 1% of the design displacement
A9 official-form check  /geometry/verify must return stations>0 and a finite error;
                        a zero-station "PASS" is WITHHELD, never PASS
                        (zero-station responses are graded WITHHELD, not PASS)

Verdict vocabulary (platform evidence grading): REAL / MOCK / STUB / WITHHELD /
NOT_SUPPORTED.  A capability graded REAL that violates A1/A2/A3/A7 or contradicts
itself under A4/A5 is graded MOCK -- the numbers are produced by a closed-form
parametric stand-in, not by the hull form.

Usage
-----
    python3 tools/validation/probe_geometry_hydrostatics.py --probe-live
    python3 tools/validation/probe_geometry_hydrostatics.py --from-raw   # offline

Both modes write ``artifacts/geometry_hydrostatics_verdicts.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

RHO = 1.025  # t/m^3, sea water as used by the platform's TPC identity
GATE_ABS_PCT = 3.0  # exact identities: anything above is a defect, not a model choice
GATE_BOUND_PCT = 15.0  # documented-model gate used elsewhere in this repo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW_DIR = os.path.join(ROOT, "artifacts", "geometry_hydro_raw")
OUT_JSON = os.path.join(ROOT, "artifacts", "geometry_hydrostatics_verdicts.json")
SHIPS = ("KCS", "KVLCC2", "SUBOFF")
BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _rel(a: float, b: float) -> float:
    """Relative deviation in percent, guarded against a zero reference."""
    if b == 0:
        return float("inf") if a else 0.0
    return abs(a - b) / abs(b) * 100.0


def _central_diff(xs, ys, i):
    """d y / d x at index i (one-sided at the ends)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    if i == 0:
        return (ys[1] - ys[0]) / (xs[1] - xs[0])
    if i == n - 1:
        return (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    return (ys[i + 1] - ys[i - 1]) / (xs[i + 1] - xs[i - 1])


def _anchor(name, value, reference, err_pct, gate, detail, kind):
    """Build one criterion record. ``kind`` = 'identity' | 'bound' | 'hygiene'."""
    return {
        "anchor": name,
        "kind": kind,
        "value": value,
        "reference": reference,
        "rel_err_pct": None if err_pct is None else round(float(err_pct), 4),
        "gate_pct": gate,
        "pass": bool(err_pct is not None and err_pct <= gate),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# anchors over one /hydro-curves payload
# ---------------------------------------------------------------------------
def evaluate_hydro_curves(payload: dict) -> dict:
    """Evaluate A1..A8 on one ``/geometry/hydro-curves`` response body."""
    meta = payload.get("meta", {}) or {}
    cur = payload.get("curves", {}) or {}
    L = float(meta.get("L") or 0.0)
    B = float(meta.get("B") or 0.0)
    T_design = float(meta.get("T") or 0.0)
    cb_meta = float(meta.get("Cb") or 0.0)

    drafts = [float(v) for v in cur.get("drafts", [])]
    disp = [float(v) for v in cur.get("displacement", [])]
    awp = [float(v) for v in cur.get("Awp", [])]
    cb = [float(v) for v in cur.get("Cb", [])]
    vcb = [float(v) for v in cur.get("VCB", [])]
    tpc = [float(v) for v in cur.get("TPC", [])]

    anchors = []
    n = min(len(drafts), len(disp), len(awp))
    if n < 3 or L <= 0 or B <= 0:
        return {
            "grade": "WITHHELD",
            "reason": "payload too short to integrate (need >=3 drafts and L,B in meta)",
            "anchors": anchors,
            "meta": meta,
            "n_points": n,
        }

    lbt = [L * B * t for t in drafts]
    vol = [w / RHO for w in disp]

    # --- A1 box bound ------------------------------------------------------
    worst_i = max(range(n), key=lambda i: vol[i] / lbt[i])
    a1_err = (vol[worst_i] / lbt[worst_i] - 1.0) * 100.0
    anchors.append(
        _anchor(
            "A1_box_bound",
            round(vol[worst_i] / lbt[worst_i], 6),
            1.0,
            max(a1_err, 0.0),
            0.0,
            f"max nabla/(L*B*T) at T={drafts[worst_i]} m; violating volume "
            f"requires displaced volume L*B*T = {lbt[worst_i]:.1f} m3, reported "
            f"{vol[worst_i]:.1f} m3",
            "bound",
        )
    )

    # --- A2 Cb internal consistency ---------------------------------------
    a2 = []
    for i in range(n):
        derived = vol[i] / lbt[i]
        a2.append(
            {
                "T": drafts[i],
                "cb_reported": cb[i] if i < len(cb) else None,
                "cb_from_volume": round(derived, 6),
                "rel_err_pct": round(_rel(cb[i], derived), 4) if i < len(cb) else None,
            }
        )
    fin = [abs(r["rel_err_pct"]) for r in a2 if r["rel_err_pct"] is not None
           and r["rel_err_pct"] != float("inf")]
    a2_err = max(fin) if fin else 0.0
    cb_max = max(cb) if cb else None
    anchors.append(
        _anchor(
            "A2_block_coefficient",
            cb_max,
            1.0,
            max((cb_max - 1.0) * 100.0, a2_err) if cb_max is not None else None,
            0.0,
            f"reported Cb range [{min(cb):.4f}, {max(cb):.4f}] must equal "
            f"nabla/(L*B*T) and must not exceed 1",
            "bound",
        )
    )

    # --- A3 design-point Cb == meta Cb ------------------------------------
    design_i = min(range(n), key=lambda i: abs(drafts[i] - T_design))
    cb_design = cb[design_i] if design_i < len(cb) else float("nan")
    anchors.append(
        _anchor(
            "A3_design_cb",
            cb_design,
            cb_meta,
            _rel(cb_design, cb_meta) if cb_meta else None,
            GATE_ABS_PCT,
            f"curve Cb at T={drafts[design_i]} m vs /ship-hull template Cb={cb_meta}",
            "identity",
        )
    )

    # --- A4 fundamental theorem d(nabla)/dT = Awp -------------------------
    a4 = []
    for i in range(n):
        dvol = _central_diff(drafts, vol, i)
        a4.append(
            {
                "T": drafts[i],
                "dvol_dT": round(dvol, 4),
                "awp": awp[i],
                "rel_err_pct": round(_rel(dvol, awp[i]), 4),
            }
        )
    a4_err = max(r["rel_err_pct"] for r in a4)
    anchors.append(
        _anchor(
            "A4_fundamental_theorem",
            a4[design_i]["dvol_dT"],
            awp[design_i],
            a4_err,
            GATE_ABS_PCT,
            "d(nabla)/dT from the reported displacement curve vs the reported Awp(T); "
            "worst point "
            f"T={max(a4, key=lambda r: r['rel_err_pct'])['T']} m",
            "identity",
        )
    )

    # --- A5 wall-sided dichotomy: constant Awp  =>  VCB == T/2 ------------
    awp_spread = _rel(max(awp), min(awp)) if min(awp) > 0 else float("inf")
    wall_sided = awp_spread <= 0.5
    a5 = []
    if wall_sided and vcb:
        for i in range(n):
            half = drafts[i] / 2.0
            a5.append(
                {
                    "T": drafts[i],
                    "vcb": vcb[i],
                    "wall_sided_reference": round(half, 4),
                    "rel_err_pct": round(_rel(vcb[i], half), 4),
                }
            )
        a5_err = max(r["rel_err_pct"] for r in a5)
        anchors.append(
            _anchor(
                "A5_wall_sided_vcb",
                vcb[design_i],
                round(drafts[design_i] / 2.0, 4),
                a5_err,
                GATE_ABS_PCT,
                f"Awp spread only {awp_spread:.3f}% over the draft sweep => prismatic "
                "body => VCB must equal T/2 at every draft",
                "identity",
            )
        )
    else:
        anchors.append(
            {
                "anchor": "A5_wall_sided_vcb",
                "kind": "identity",
                "value": None,
                "reference": None,
                "rel_err_pct": None,
                "gate_pct": GATE_ABS_PCT,
                "pass": None,
                "detail": f"not applicable: Awp spread {awp_spread:.3f}% > 0.5%",
            }
        )

    # --- A6 TPC identity --------------------------------------------------
    a6_err = max(_rel(tpc[i], RHO * awp[i] / 100.0) for i in range(min(n, len(tpc))))
    anchors.append(
        _anchor(
            "A6_tpc_identity",
            round(RHO * awp[design_i] / 100.0, 4),
            tpc[design_i],
            a6_err,
            GATE_ABS_PCT,
            "TPC = rho*Awp/100 the platform uses internally; cross-check on reported Awp",
            "identity",
        )
    )

    # --- A7 waterplane bound ---------------------------------------------
    a7 = max(range(n), key=lambda i: awp[i] / (L * B))
    a7_ratio = awp[a7] / (L * B)
    anchors.append(
        _anchor(
            "A7_waterplane_bound",
            round(a7_ratio, 6),
            1.0,
            max((a7_ratio - 1.0) * 100.0, 0.0),
            0.0,
            f"max Awp/(L*B) at T={drafts[a7]} m (upper bound is 1 for a wall-sided hull)",
            "bound",
        )
    )

    # --- A8 curve hygiene (quantisation detector) -------------------------
    # A hydrostatic displacement curve of a real hull is strictly increasing in
    # draft.  Equal consecutive values ("zero steps") can therefore only come
    # from rounding the output, i.e. from a precision defect: the curve is stored
    # at a coarser resolution than the physics needs.
    steps = [disp[i + 1] - disp[i] for i in range(n - 1)]
    w_max = max(disp)
    zero_steps = sum(1 for x in steps if x == 0)
    neg_steps = sum(1 for x in steps if x < 0)
    zero_pct = 100.0 * zero_steps / len(steps) if steps else 0.0
    min_pos_step = min((abs(x) for x in steps if x != 0), default=float("nan"))
    anchors.append(
        _anchor(
            "A8_curve_hygiene",
            round(zero_pct, 4),
            0.0,
            zero_pct,
            0.0,
            f"monotone={'yes' if neg_steps == 0 else 'NO'}; {zero_steps}/{len(steps)} "
            f"consecutive displacement steps are exactly zero (rounding), smallest "
            f"non-zero step {min_pos_step:.4g} t vs design displacement {w_max:.4g} t "
            f"({min_pos_step / w_max * 100.0:.3f}% of it)" if w_max and min_pos_step == min_pos_step else
            f"monotone={'yes' if neg_steps == 0 else 'NO'}; {zero_steps}/{len(steps)} zero steps",
            "hygiene",
        )
    )
    if neg_steps:
        anchors[-1]["pass"] = False

    # --- verdict ----------------------------------------------------------
    hard = [a for a in anchors if a["kind"] in ("bound", "identity") and a["pass"] is False]
    soft = [a for a in anchors if a["kind"] == "hygiene" and a["pass"] is False]
    grade = "MOCK" if hard else ("REAL" if not soft else "REAL_WITH_DEFECTS")
    return {
        "grade": grade,
        "reason": "; ".join(f"{a['anchor']} failed ({a['detail']})" for a in hard)
        or ("hygiene only: " + "; ".join(a["detail"] for a in soft) if soft else "all anchors pass"),
        "meta": meta,
        "n_points": n,
        "design_draft": drafts[design_i],
        "awp_spread_pct": round(awp_spread, 4),
        "anchors": anchors,
        "a2_block_coefficient": a2,
        "a4_fundamental_theorem": a4,
        "a5_wall_sided_vcb": a5,
        "series": {"drafts": drafts, "displacement_t": disp, "awp": awp, "cb": cb, "vcb": vcb},
    }


# ---------------------------------------------------------------------------
# anchor A9: /geometry/verify
# ---------------------------------------------------------------------------
def evaluate_verify(payload, http_status) -> dict:
    if http_status != 200 or not isinstance(payload, dict):
        return {
            "grade": "NOT_SUPPORTED",
            "reason": f"HTTP {http_status}: {str(payload)[:200]}",
            "stations_checked": 0,
            "max_error_pct": None,
        }
    stations = int(payload.get("stations_checked") or 0)
    max_err = payload.get("max_error_pct")
    rows = payload.get("results") or []
    if stations == 0 or not rows:
        return {
            "grade": "WITHHELD",
            "reason": "zero stations checked -- an empty comparison is not a PASS",
            "stations_checked": stations,
            "max_error_pct": max_err,
            "rows": rows,
        }
    grade = "REAL" if max_err is not None and max_err <= GATE_BOUND_PCT else "MOCK"
    return {
        "grade": grade,
        "reason": f"{stations} stations, max |error| {max_err}% vs {GATE_BOUND_PCT}% gate",
        "stations_checked": stations,
        "avg_error_pct": payload.get("avg_error_pct"),
        "max_error_pct": max_err,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------
def _get(path, timeout=90):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, None
    except Exception as e:  # noqa: BLE001
        return None, {"detail": f"{type(e).__name__}: {e}"}


def collect_live(ships=SHIPS) -> dict:
    """Probe the live service and persist the raw bodies next to the verdicts."""
    os.makedirs(RAW_DIR, exist_ok=True)
    out = {}
    for s in ships:
        rec = {}
        for tag, path in (
            ("curves", f"/geometry/hydro-curves/{s}"),
            ("verify", f"/geometry/verify/{s}"),
        ):
            st, body = _get(path)
            json.dump(
                {"status": st, "body": body},
                open(os.path.join(RAW_DIR, f"{s}__{tag}.json"), "w"),
                ensure_ascii=False,
                indent=1,
            )
            rec[tag] = {"status": st, "body": body}
        out[s] = rec
    return out


def collect_raw(ships=SHIPS) -> dict:
    out = {}
    for s in ships:
        rec = {}
        for tag in ("curves", "verify"):
            fn = os.path.join(RAW_DIR, f"{s}__{tag}.json")
            if not os.path.exists(fn):
                rec[tag] = {"status": None, "body": {"detail": "raw file missing"}}
                continue
            rec[tag] = json.load(open(fn))
        out[s] = rec
    return out


def adjudicate(raw: dict) -> dict:
    verdicts = {"rho_t_m3": RHO, "source": "geometry/hydro-curves + geometry/verify", "ships": {}}
    for ship, rec in raw.items():
        cur = rec.get("curves", {})
        ver = rec.get("verify", {})
        hv = (
            evaluate_hydro_curves(cur.get("body"))
            if cur.get("status") == 200 and isinstance(cur.get("body"), dict)
            else {
                "grade": "WITHHELD",
                "reason": f"hydro-curves HTTP {cur.get('status')}",
                "anchors": [],
            }
        )
        verdicts["ships"][ship] = {
            "hydro_curves": {
                "http_status": cur.get("status"),
                "grade": hv["grade"],
                "reason": hv["reason"],
                "anchors": hv["anchors"],
                "a2_block_coefficient": hv.get("a2_block_coefficient"),
                "a4_fundamental_theorem": hv.get("a4_fundamental_theorem"),
                "a5_wall_sided_vcb": hv.get("a5_wall_sided_vcb"),
                "meta": hv.get("meta"),
                "awp_spread_pct": hv.get("awp_spread_pct"),
                "series": hv.get("series"),
            },
            "verify_official": evaluate_verify(ver.get("body"), ver.get("status")),
        }
    grades = [v["hydro_curves"]["grade"] for v in verdicts["ships"].values()]
    verdicts["capability_grade"] = {
        "geometry.hydro_curves": _worst(grades),
        "geometry.verify_official": _worst(
            [v["verify_official"]["grade"] for v in verdicts["ships"].values()]
        ),
    }
    return verdicts


_ORDER = {"MOCK": 0, "STUB": 1, "NOT_SUPPORTED": 2, "WITHHELD": 3, "REAL_WITH_DEFECTS": 4, "REAL": 5}


def _worst(grades):
    """Capability grade = worst ship-level grade (conservative)."""
    if not grades:
        return "WITHHELD"
    return sorted(grades, key=lambda g: _ORDER.get(g, 3))[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-live", action="store_true", help="query the live ship API")
    ap.add_argument("--from-raw", action="store_true", help="re-adjudicate saved raw bodies")
    ap.add_argument("--ships", default=",".join(SHIPS))
    args = ap.parse_args(argv)
    ships = [s.strip() for s in args.ships.split(",") if s.strip()]
    if args.probe_live:
        raw = collect_live(ships)
    elif args.from_raw:
        raw = collect_raw(ships)
    else:
        ap.error("choose --probe-live or --from-raw")
    verdicts = adjudicate(raw)
    verdicts["mode"] = "live" if args.probe_live else "from_raw"
    json.dump(verdicts, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    for ship, v in verdicts["ships"].items():
        hc = v["hydro_curves"]
        vo = v["verify_official"]
        print(f"{ship:9s} hydro_curves={hc['grade']:18s} verify_official={vo['grade']}")
        for a in hc["anchors"]:
            print(
                f"    {a['anchor']:24s} pass={a['pass']} value={a['value']} "
                f"ref={a['reference']} err={a['rel_err_pct']}%"
            )
    print("capability:", json.dumps(verdicts["capability_grade"], ensure_ascii=False))
    print("wrote", os.path.relpath(OUT_JSON, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())