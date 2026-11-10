"""Freeze the end-to-end regression baseline for a coupling-gate case pack.

Baseline = the pack's *independent* reference chain (different friction line
from the pipeline chain).  Frozen once with provenance so that later runs are a
true regression against a pinned artefact, not against themselves.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.cross_domain_coupling.spec import load_spec  # noqa: E402
from src.cross_domain_coupling.gate import EvaluatorRegistry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--evaluator", required=True, help="reference evaluator name in the pack")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-root", default=".")
    args = ap.parse_args()
    root = Path(args.allow_root).resolve()
    spec_path = Path(args.spec).resolve()
    spec = load_spec(spec_path)
    reg = EvaluatorRegistry((root / spec.evaluator_module).resolve(), allow_root=root)
    payload = spec.end_to_end_cases[spec.end_to_end.case_set_key]
    baseline = reg.call(args.evaluator, payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "baseline": str(out),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "n_ships": len(baseline),
        "spec_sha256": spec.sha256,
        "evaluator_sha256": reg.sha256,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
