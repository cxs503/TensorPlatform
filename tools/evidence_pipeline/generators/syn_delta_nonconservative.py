#!/usr/bin/env python3
"""Negative-control generator: *non-conservative* exchange (must FAIL the gate).

Same geometry and field as ``syn_beta_exchange.py`` but the exchange uses
nearest-cell sampling with the conservation correction switched **off** — the
mistake this pipeline exists to catch.  The payload is well formed and the
generator exits 0; only the ``conservation`` gate can tell the two apart.

This generator is never wired into a passing benchmark: it is used by the
acceptance driver and the pytest suite as the discriminator that proves the
gate is not vacuous (its residual must exceed the conservative residual by
orders of magnitude).

Usage::

    python3 tools/evidence_pipeline/generators/syn_delta_nonconservative.py \
        --params-json '{"n_src": 4, "n_dst": 5}' --out /tmp/payload.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coupling.conservation import measure  # noqa: E402
from tools.coupling.mesh import BoxMesh, Field, QuantityKind  # noqa: E402
from tools.coupling.remap import RemapOperator, RemapSpec, remap  # noqa: E402
from tools.evidence_pipeline import PAYLOAD_SCHEMA  # noqa: E402

NODE = "syn-delta-nonconservative"


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
    src = Field(values=1.0 + centers[:, 0] + 0.5 * centers[:, 1], mesh=src_mesh,
                kind=QuantityKind.DENSITY, name="src")
    spec = RemapSpec(
        operator=RemapOperator.NEAREST_SCALED,
        quantity_kind=QuantityKind.DENSITY,
        apply_conservation_correction=False,
        moment_exact=False,
        positivity=False,
        enforce_bounds=False,
    )
    dst = remap(src, dst_mesh=dst_mesh, spec=spec).field
    cons = measure(src, dst)
    return {
        "schema": PAYLOAD_SCHEMA,
        "node": NODE,
        "metrics": {
            "sum_rel_residual": cons.sum_rel_residual,
            "moment_rel_residual": cons.moment_rel_residual,
            "overshoot_ratio": cons.overshoot_ratio,
            "nonneg_violation": cons.nonneg_violation,
            "src_total": cons.sum_src,
            "dst_total": cons.sum_dst,
        },
        "fields": {"src": field_description(src), "dst": field_description(dst)},
        "notes": "negative control: nearest sampling, conservation correction off",
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