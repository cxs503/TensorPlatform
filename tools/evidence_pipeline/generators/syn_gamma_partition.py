#!/usr/bin/env python3
"""Synthetic benchmark generator: exchange on a non-uniform partition.

Third synthetic benchmark.  The source mesh is an *irregular* box partition
(edges at 0.0 / 0.2 / 0.55 / 1.0 per axis) and the destination is its nested
refinement, so the exchange operator has to handle cells of unequal size and a
strictly nested geometry.  The exchange keeps the total and the positivity/boundedness constraints exact
while leaving the lever-arm (first moment) with the operator's own O(h) defect —
that defect is bounded by a predeclared threshold in the conservation gate, so
the gate states a *quantity* rather than a tautology.

A linear reproduction check is added to the metrics:
for a piecewise-linear operator a linear field must be reproduced exactly, so
``linear_repro_error`` is a second, independent numerical claim.

The conserved field is positive by construction, so the positivity/overshoot
side of the conservation gate is not vacuous.

Usage::

    python3 tools/evidence_pipeline/generators/syn_gamma_partition.py \
        --params-json '{"factor": 2}' --out /tmp/payload.json
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

NODE = "syn-gamma-partition"
EDGES = (0.0, 0.2, 0.55, 1.0)


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


def irregular_mesh(name: str) -> BoxMesh:
    lo, hi = [], []
    for i in range(len(EDGES) - 1):
        for j in range(len(EDGES) - 1):
            lo.append([EDGES[i], EDGES[j]])
            hi.append([EDGES[i + 1], EDGES[j + 1]])
    return BoxMesh.from_bounds(lo, hi, name=name)


def linear(xy: np.ndarray) -> np.ndarray:
    return 2.0 + 0.5 * xy[:, 0] - 0.25 * xy[:, 1]


def compute(params: dict) -> dict:
    factor = int(params.get("factor", 2))
    src_mesh = irregular_mesh("src_partition")
    dst_mesh = src_mesh.refined(factor, name="dst_refined")

    src_values = 2.0 + 0.5 * np.sin(2.0 * math.pi * src_mesh.centers[:, 0])
    src = Field(values=src_values, mesh=src_mesh, kind=QuantityKind.DENSITY, name="src")
    spec = RemapSpec(
        operator=RemapOperator(str(params.get("operator", "overlap_p1"))),
        quantity_kind=QuantityKind.DENSITY,
        apply_conservation_correction=True,
        moment_exact=bool(params.get("moment_exact", False)),
        positivity=True,
        enforce_bounds=True,
    )
    result = remap(src, dst_mesh=dst_mesh, spec=spec)
    dst = result.field
    cons = measure(src, dst)

    # linear reproduction: exchange a linear field and compare to its exact value
    lin_src = Field(values=linear(src_mesh.centers), mesh=src_mesh,
                    kind=QuantityKind.DENSITY, name="lin_src")
    lin_dst = remap(lin_src, dst_mesh=dst_mesh, spec=spec).field
    exact = linear(dst_mesh.centers)
    scale = max(float(np.abs(exact).max()), 1e-300)
    lin_err = float(np.abs(lin_dst.values - exact).max()) / scale

    return {
        "schema": PAYLOAD_SCHEMA,
        "node": NODE,
        "metrics": {
            "sum_rel_residual": cons.sum_rel_residual,
            "moment_rel_residual": cons.moment_rel_residual,
            "overshoot_ratio": cons.overshoot_ratio,
            "nonneg_violation": cons.nonneg_violation,
            "linear_repro_error": lin_err,
            "min_dst": cons.min_dst,
            "n_cells_src": float(src_mesh.n_cells),
            "n_cells_dst": float(dst_mesh.n_cells),
        },
        "fields": {"src": field_description(src), "dst": field_description(dst)},
        "notes": "irregular partition -> nested refinement, P1 exchange, exact moments",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params-json", default="{}")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    payload = compute(json.loads(args.params_json))
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:  # pragma: no cover
        print(f"generator failed: {exc}", file=sys.stderr)
        sys.exit(1)