#!/usr/bin/env python3
"""Synthetic benchmark generator: cross-library field exchange (domain-neutral).

Two "libraries" hold the same conserved quantity on different meshes: a source
mesh (``n_src`` cells per axis) and a destination mesh (``n_dst`` cells per
axis).  The exchange is the coupling toolkit's overlap projection with the
conservation correction *and* the first-moment constraint switched on, so the
generator emits both:

* ``metrics`` — scalar quality numbers for the ``numeric`` gate, and
* ``fields``  — the full source/destination pair for the ``conservation`` gate,
  which recomputes the residuals independently from the payload.

Nothing here is physical: the field is a smooth polynomial, the meshes are
axis-aligned boxes.

Usage::

    python3 tools/evidence_pipeline/generators/syn_beta_exchange.py \
        --params-json '{"n_src": 4, "n_dst": 5}' --out /tmp/payload.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coupling.conservation import measure  # noqa: E402
from tools.coupling.mesh import BoxMesh, Field, QuantityKind  # noqa: E402
from tools.coupling.remap import RemapOperator, RemapSpec, remap  # noqa: E402
from tools.evidence_pipeline import PAYLOAD_SCHEMA  # noqa: E402

NODE = "syn-beta-exchange"


def field_description(field: Field) -> dict:
    return {
        "mesh": {
            "lo": field.mesh.lo.tolist(),
            "hi": field.mesh.hi.tolist(),
            "name": field.mesh.name,
        },
        "values": [float(v) for v in field.values],
        "kind": str(field.kind.value),
        "name": field.name,
    }


def compute(params: dict) -> dict:
    n_src = int(params.get("n_src", 4))
    n_dst = int(params.get("n_dst", 5))
    src_mesh = BoxMesh.uniform([0.0, 0.0], [1.0, 1.0], [n_src, n_src], name="src")
    dst_mesh = BoxMesh.uniform([0.0, 0.0], [1.0, 1.0], [n_dst, n_dst], name="dst")
    centers = src_mesh.centers
    values = 1.0 + centers[:, 0] + 0.5 * centers[:, 1]
    src = Field(values=values, mesh=src_mesh, kind=QuantityKind.DENSITY, name="src")

    spec = RemapSpec(
        operator=RemapOperator(str(params.get("operator", "overlap_l2"))),
        quantity_kind=QuantityKind.DENSITY,
        apply_conservation_correction=True,
        moment_exact=True,
        positivity=bool(params.get("positivity", False)),
        enforce_bounds=bool(params.get("enforce_bounds", False)),
    )
    result = remap(src, dst_mesh=dst_mesh, spec=spec)
    dst = result.field
    cons = measure(src, dst)

    z = np.zeros((src_mesh.n_cells, 1))
    del z
    return {
        "schema": PAYLOAD_SCHEMA,
        "node": NODE,
        "metrics": {
            "sum_rel_residual": cons.sum_rel_residual,
            "moment_rel_residual": cons.moment_rel_residual,
            "overshoot_ratio": cons.overshoot_ratio,
            "nonneg_violation": cons.nonneg_violation,
            "dst_total": cons.sum_dst,
            "src_total": cons.sum_src,
            "remap_rel_residual": float(result.report.rel_residual),
            "n_cells_src": float(src_mesh.n_cells),
            "n_cells_dst": float(dst_mesh.n_cells),
        },
        "fields": {"src": field_description(src), "dst": field_description(dst)},
        "notes": "overlap projection + mass correction + first-moment constraint",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params-json", default="{}")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    payload = compute(json.loads(args.params_json))
    # strict JSON: refuse NaN/Inf instead of emitting them (a non-finite metric is
    # a generator bug, and the runner would reject the payload anyway)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:  # pragma: no cover - surfaces as generator_error
        print(f"generator failed: {exc}", file=sys.stderr)
        sys.exit(1)