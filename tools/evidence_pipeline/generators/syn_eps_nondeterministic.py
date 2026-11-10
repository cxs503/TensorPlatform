#!/usr/bin/env python3
"""Negative control: a *non-deterministic* generator (must be rejected).

The payload contract and the numeric gates are satisfied, but the emitted number
depends on ``time.perf_counter_ns()``, so a replay cannot reproduce the payload
digest.  This is the generator that proves the ingest gate's replay check is
live: without it, "replay consistent" would be an untested claim.

It is used by the pytest suite and the acceptance driver only — never wired into
a passing benchmark.

Usage::

    python3 tools/evidence_pipeline/generators/syn_eps_nondeterministic.py \
        --params-json '{}' --out /tmp/payload.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evidence_pipeline import PAYLOAD_SCHEMA  # noqa: E402

NODE = "syn-eps-nondeterministic"


def compute(params: dict) -> dict:
    # deliberately irreproducible: nanoseconds since an arbitrary epoch
    jitter = (time.perf_counter_ns() % 1000003) / 1000003.0
    return {
        "schema": PAYLOAD_SCHEMA,
        "node": NODE,
        "metrics": {
            "value": 1.0 + 1e-4 * (0.5 - jitter),
            "jitter": jitter,
        },
        "fields": {},
        "notes": "negative control: non-deterministic metric, gates pass, replay cannot",
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