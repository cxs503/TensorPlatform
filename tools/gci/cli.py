"""One-line-JSON CLI for the ITTC GCI chain.

Examples
--------
Three grids given explicitly, plus the experimental reference for the gate::

    python -m tools.gci.cli --f1 0.9998380 --f2 0.9995025 --f3 0.9981661 \\
        --r 2 --reference 1.0 --gate 0.15

Run the in-repo end-to-end case (circular-section added mass) instead::

    python -m tools.gci.cli --case circle-section --base 40 --ratio 2

Replay a sha256-locked anchor::

    python -m tools.gci.cli --anchor ITTC-P2-EXACT --reference 1.0

Output: a single JSON line with ``r21, r32, p, R_G, GCI21, GCI32, U_V,
verdict`` plus the traceability fields.  Exit status is machine-usable:
``0`` = PASS, ``1`` = FAIL, ``2`` = WITHHELD, ``3`` = usage/verification error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from tools.gci.anchors import ANCHORS, load_anchors
from tools.gci.cases import ANALYTIC_CA, circle_added_mass, refinement_levels
from tools.gci.core import (
    GATE_DEFAULT,
    PASS,
    FAIL,
    WITHHELD,
    gci,
    ittc_validation_state,
    verdict,
)

__all__ = ["build_record", "main"]

_EXIT = {PASS: 0, FAIL: 1, WITHHELD: 2}


def build_record(
    f1: float,
    f2: float,
    f3: float,
    r21: float = 2.0,
    *,
    r32: Optional[float] = None,
    fs: float = 1.25,
    u_input: float = 0.0,
    reference: Optional[float] = None,
    gate: float = GATE_DEFAULT,
    p_assumed: Optional[float] = None,
    extra: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Compute the GCI study and the three-state verdict, as a flat record."""
    res = gci(f1, f2, f3, r21, r32, fs=fs, u_input=u_input, p_assumed=p_assumed)
    deviation = None
    if reference is not None:
        if reference == 0:
            raise ValueError("reference must be non-zero")
        deviation = abs(f1 - reference) / abs(reference)
    rec: Dict[str, object] = {
        "r21": res.r21,
        "r32": res.r32,
        "p": res.p_used,
        "p_obs": res.p_obs,
        "p_source": res.p_source,
        "R_G": res.R_G,
        "GCI21": res.gci21,
        "GCI32": res.gci32,
        "U_V": res.u_v,
        "verdict": verdict(res.u_v, deviation, gate),
        "convergence": res.convergence,
        "e_a": res.e_a,
        "phi_ext": res.phi_ext,
        "fs": res.fs,
        "u_input": res.u_input,
        "gate": gate,
        "deviation": deviation,
        "reference": reference,
        "fine_value": f1,
        "ittc_state": (
            None if deviation is None else ittc_validation_state(res.u_v, deviation)
        ),
        "notes": list(res.notes),
    }
    if extra:
        rec.update(extra)
    return rec


def _triplet_from_case(base: int, ratio: int, levels: int) -> Dict[str, object]:
    grids = refinement_levels(
        base_nr=base, base_ntheta=max(8, int(round(base * 1.6))), ratio=ratio, levels=levels
    )
    runs: List[Dict[str, object]] = []
    for g in grids:
        runs.append(circle_added_mass(g["nr"], g["ntheta"]).to_dict())
    return {"grids": runs, "reference": ANALYTIC_CA}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ITTC 7.5-03-02-01 GCI / U_V one-line JSON CLI")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--f1", type=float, help="QoI on the finest grid")
    src.add_argument("--anchor", help="replay a sha256-locked anchor id")
    src.add_argument("--case", choices=["circle-section"], help="run an in-repo case")
    ap.add_argument("--f2", type=float)
    ap.add_argument("--f3", type=float)
    ap.add_argument("--r", type=float, default=2.0, help="refinement ratio (default 2)")
    ap.add_argument("--r32", type=float, default=None)
    ap.add_argument("--fs", type=float, default=1.25, help="safety factor (1.25 or 3.0)")
    ap.add_argument("--u-input", type=float, default=0.0)
    ap.add_argument("--reference", type=float, default=None, help="experimental / benchmark value")
    ap.add_argument("--gate", type=float, default=GATE_DEFAULT)
    ap.add_argument("--p-assumed", type=float, default=None)
    ap.add_argument("--base", type=int, default=40, help="coarsest radial intervals for --case")
    ap.add_argument("--ratio", type=int, default=2)
    ap.add_argument("--levels", type=int, default=3)
    args = ap.parse_args(argv)

    extra: Dict[str, object] = {}
    try:
        if args.anchor:
            lib = {a.id: a for a in load_anchors()}
            if args.anchor not in lib:
                raise SystemExit(f"unknown anchor {args.anchor!r}; known: {sorted(lib)}")
            a = lib[args.anchor]
            f1, f2, f3 = a.f1, a.f2, a.f3
            r, r32, fs = a.r21, a.r32, a.fs
            extra["anchor"] = a.id
            extra["anchor_sha256"] = a.sha256
            extra["anchor_clause"] = a.clause
        elif args.case:
            d = _triplet_from_case(args.base, args.ratio, args.levels)
            runs = d["grids"]
            f1 = float(runs[-1]["ca"])   # finest
            f2 = float(runs[-2]["ca"])
            f3 = float(runs[-3]["ca"])
            r, r32, fs = float(args.ratio), float(args.ratio), 1.25
            extra["case"] = args.case
            extra["grids"] = runs
            if args.reference is None:
                args.reference = float(d["reference"])  # type: ignore[assignment]
        else:
            if args.f2 is None or args.f3 is None:
                raise SystemExit("--f2 and --f3 are required with --f1")
            f1, f2, f3 = args.f1, args.f2, args.f3
            r, r32, fs = args.r, args.r32, args.fs
        rec = build_record(
            f1, f2, f3, r,
            r32=r32,
            fs=fs,
            u_input=args.u_input,
            reference=args.reference,
            gate=args.gate,
            p_assumed=args.p_assumed,
            extra=extra,
        )
    except (ValueError, SystemExit) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 3

    print(json.dumps(rec, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return _EXIT.get(str(rec["verdict"]), 2)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())