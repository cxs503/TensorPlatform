#!/usr/bin/env python3
"""Synthetic benchmark generator: quadrature convergence order (domain-neutral).

Emits a *numeric* gate target only — no fields.  The generator integrates a
smooth analytic function with the midpoint rule on two mesh widths and reports
the observed order of accuracy

    p = log2( e(n) / e(2n) ),      e(n) = |Q_n - I_exact|

so the pipeline has a benchmark whose gate is a genuine numerical-quality claim
rather than a tautology.  Everything is deterministic: no RNG, no wall clock,
no environment lookups.

Usage::

    python3 tools/evidence_pipeline/generators/syn_alpha_quadrature.py \
        --params-json '{"n0": 8}' --out /tmp/payload.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evidence_pipeline import PAYLOAD_SCHEMA  # noqa: E402

NODE = "syn-alpha-quadrature"


def midpoint(f, a: float, b: float, n: int) -> float:
    h = (b - a) / n
    total = 0.0
    for i in range(n):
        total += f(a + (i + 0.5) * h)
    return total * h


def f_exact(x: float) -> float:
    return math.sin(math.pi * x)


def integral_exact(a: float, b: float) -> float:
    return (-math.cos(math.pi * b) + math.cos(math.pi * a)) / math.pi


def compute(params: dict) -> dict:
    n0 = int(params.get("n0", 8))
    a, b = 0.0, 1.0
    ref = integral_exact(a, b)
    e_coarse = abs(midpoint(f_exact, a, b, n0) - ref)
    e_fine = abs(midpoint(f_exact, a, b, 2 * n0) - ref)
    order = math.log2(e_coarse / e_fine) if e_fine > 0.0 else float("inf")
    total_fine = midpoint(f_exact, a, b, 2 * n0)
    return {
        "schema": PAYLOAD_SCHEMA,
        "node": NODE,
        "metrics": {
            "p_order": order,
            "err_coarse": e_coarse,
            "err_fine": e_fine,
            "n_fine": float(2 * n0),
            "integral_fine": total_fine,
            "integral_ref": ref,
            "integral_rel_error": abs(total_fine - ref) / abs(ref),
        },
        "fields": {},
        "notes": "midpoint rule, smooth analytic integrand, observed order of accuracy",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params-json", default="{}")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    payload = compute(json.loads(args.params_json))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())