"""CLI: run a GCI discretization-error study on an existing three-grid dataset.

Intended for closing verification debt on *real* solver results, e.g. a
resistance or C_D value computed on three grids.  Input is JSON so nothing has
to be retyped::

    {"h": [1/64, 1/32, 1/16], "phi": [1.0002, 1.0008, 1.0032],
     "quantity": "C_D_mean", "fs": 1.25}

``h`` may be given as plain numbers or as strings such as ``"1/64"``; the list
must be ordered **finest first** (or pass ``"sort": true`` to let the tool do
it).  Exit code is 1 when the grids are not in the asymptotic range, so this
can gate CI: a platform number with no valid uncertainty band fails loudly
instead of shipping an unqualified claim.

Usage::

    python3 tools/gci_report.py study.json [--out result.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from fractions import Fraction

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.verification import FS_THREE_GRIDS, gci  # noqa: E402


def _num(v) -> float:
    """Accept 0.015625, "1/64" or "1.5625e-2"."""
    if isinstance(v, str):
        v = v.strip()
        try:
            return float(v)
        except ValueError:
            return float(Fraction(v))
    return float(v)


def load_study(path: str) -> dict:
    with open(path) as fh:
        data = json.load(fh)
    if "h" not in data or "phi" not in data:
        raise ValueError("input JSON must contain 'h' and 'phi' arrays of length 3")
    h = [_num(v) for v in data["h"]]
    phi = [_num(v) for v in data["phi"]]
    if data.get("sort"):
        order = sorted(range(len(h)), key=lambda i: h[i])
        h = [h[i] for i in order]
        phi = [phi[i] for i in order]
    data["h"], data["phi"] = h, phi
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("study", help="JSON file with h and phi (three grid levels)")
    ap.add_argument("--out", help="optional path for the JSON result")
    args = ap.parse_args(argv)

    data = load_study(args.study)
    fs = float(data.get("fs", FS_THREE_GRIDS))
    res = gci(data["h"], data["phi"], fs=fs,
              p_assumed=data.get("p_assumed"))
    out = {
        "quantity": data.get("quantity", "unspecified"),
        "source": os.path.basename(args.study),
        "result": res.as_dict(),
        "verification_status": "PASS" if res.asymptotic else "UNVERIFIED",
    }
    print(f"quantity          : {out['quantity']}")
    print(f"h (finest first)  : {res.h}")
    print(f"r21, r32          : {res.r21:.4f}, {res.r32:.4f}")
    print(f"R (eps21/eps32)   : {res.R:.4f}  -> regime {res.regime}")
    print(f"observed order p  : {'n/a' if res.p is None else '%.4f' % res.p}")
    print(f"fine-grid value   : {res.phi[0]!r}")
    print(f"Richardson phi_ext: {'n/a' if res.phi_ext is None else repr(res.phi_ext)}")
    print(f"GCI (Fs={res.fs})   : {res.gci_fine:.6e}  ({res.gci_fine_pct:.4g} %)")
    if res.phi_ext is not None:
        lo = res.phi[0] - res.gci_fine * abs(res.phi[0])
        hi = res.phi[0] + res.gci_fine * abs(res.phi[0])
        print(f"band on fine value: [{lo!r}, {hi!r}]")
    for n in res.notes:
        print(f"note: {n}")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=2)
        print("wrote", args.out)
    return 0 if res.asymptotic else 1


if __name__ == "__main__":
    raise SystemExit(main())