#!/usr/bin/env python3
"""Specificity-gate replay: frozen bench (18 topics) + false-covered probes (6).

Two questions, one artifact:

* **no regression** — with the gate wired in, the frozen replay benchmark still
  reproduces every label (``18/18``, ``blank`` false positives ``0``);
* **the gate has teeth** — every adversarial "false covered" probe (topics whose
  discriminative probes exist in the pool but never in the same document) is
  capped to ``generic_only`` and carries the generic-wording ledger.

The two runs are sealed with the same canonical-JSON/sha256 envelope as the rest
of the radar, and each row echoes the gate record (rule, blocking codes, witness
counts) so a reviewer can re-derive the verdict instead of trusting it.

    python tools/whitespace_gate_replay.py --json-out artifacts/whitespace_radar/gate_replay.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

if __package__ in (None, ""):  # allow ``python tools/whitespace_gate_replay.py``
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.whitespace_radar import DEFAULT_BENCH, DEFAULT_POOL, _abs, _load_index, _write_json  # noqa: E402
from tools.whitespace_radar_core.adjudicate import GENERIC_ONLY, Thresholds, analyze  # noqa: E402
from tools.whitespace_radar_core.bench import (  # noqa: E402
    load_bench,
    load_false_covered,
    replay,
    replay_false_covered,
)
from tools.whitespace_radar_core.evidence import dumps, seal  # noqa: E402

SCHEMA = "radar-gate-replay/v1"
DEFAULT_PROBES = "data/whitespace_radar/false_covered_probes.json"


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def build_report(
    index,
    bench: Dict[str, Any],
    probes: Dict[str, Any],
    *,
    thresholds: Optional[Thresholds] = None,
    pool_digest: str = "",
    bench_path: str = "",
    probes_path: str = "",
) -> Dict[str, Any]:
    th = thresholds or Thresholds()
    bench_rep = replay(index, bench, thresholds=th, pool_digest=pool_digest)
    fc_rep = replay_false_covered(index, probes, thresholds=th, pool_digest=pool_digest)

    gate_rows: List[Dict[str, Any]] = []
    for item in bench.get("topics") or []:
        env = analyze(index, item["topic"], thresholds=th, pool_digest=pool_digest)
        gate = (env.get("specificity") or {}).get("gate") or {}
        gate_rows.append(
            {
                "set": "bench",
                "id": item["id"],
                "label": item["label"],
                "verdict": env["verdict"],
                "correct": env["verdict"] == item["label"],
                "gate_cap_applied": gate.get("cap_applied"),
                "gate_blocking": gate.get("blocking"),
                "specific_hits": gate.get("specific_hits"),
                "witness_probes": gate.get("witness_probes"),
                "generic_list_kept": gate.get("generic_list_kept"),
            }
        )
    for item in probes.get("probes") or []:
        env = analyze(index, item["topic"], thresholds=th, pool_digest=pool_digest)
        gate = (env.get("specificity") or {}).get("gate") or {}
        gate_rows.append(
            {
                "set": "false_covered",
                "id": item["id"],
                "label": item.get("expected_verdict", GENERIC_ONLY),
                "verdict": env["verdict"],
                "correct": env["verdict"] == item.get("expected_verdict", GENERIC_ONLY),
                "gate_cap_applied": gate.get("cap_applied"),
                "gate_blocking": gate.get("blocking"),
                "specific_hits": gate.get("specific_hits"),
                "witness_probes": gate.get("witness_probes"),
                "generic_list_kept": gate.get("generic_list_kept"),
                "generic_terms_kept": [
                    r["term"] for r in ((env.get("generic_ledger") or {}).get("generic_terms") or [])
                ],
            }
        )

    return {
        "schema": SCHEMA,
        "thresholds": th.as_dict(),
        "pool_digest": pool_digest,
        "bench": {
            "path": bench_path,
            "sha256": _sha256_file(_abs(bench_path)) if bench_path else "",
            "n_topics": bench_rep["n_topics"],
            "n_correct": bench_rep["n_correct"],
            "accuracy": bench_rep["accuracy"],
            "blank_false_positive": bench_rep["blank_false_positive"],
            "mismatches": bench_rep["mismatches"],
            "labels_frozen_in_file": sorted({t.get("label", "") for t in bench.get("topics") or []}),
        },
        "false_covered": {
            "path": probes_path,
            "sha256": _sha256_file(_abs(probes_path)) if probes_path else "",
            "n_probes": fc_rep["n_probes"],
            "n_detected": fc_rep["n_detected"],
            "detection_rate": fc_rep["detection_rate"],
            "missed": [m["id"] for m in fc_rep["missed"]],
        },
        "gate_rows": gate_rows,
        "acceptance": {
            "bench_full_accuracy": bench_rep["n_correct"] == bench_rep["n_topics"] == 18,
            "bench_blank_zero_false_positive": bench_rep["blank_false_positive"] == 0,
            "false_covered_all_capped": fc_rep["n_detected"] == fc_rep["n_probes"] == 6,
            "gate_caps_only_for_generic": all(
                (r["verdict"] == GENERIC_ONLY) or not r["gate_cap_applied"] for r in gate_rows
            ),
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--bench", default=DEFAULT_BENCH)
    ap.add_argument("--probes", default=DEFAULT_PROBES)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    index, _docs, digest, _drift = _load_index(args.pool, use_cache=not args.no_cache)
    bench = load_bench(_abs(args.bench))
    probes = load_false_covered(_abs(args.probes))
    report = build_report(
        index,
        bench,
        probes,
        thresholds=Thresholds(),
        pool_digest=digest,
        bench_path=args.bench,
        probes_path=args.probes,
    )
    report["acceptance"]["passed"] = all(report["acceptance"].values())
    envelope = seal(report)
    if args.json_out:
        _write_json(args.json_out, envelope)
    else:
        sys.stdout.write(dumps(envelope))
    print(
        f"[gate] bench {report['bench']['n_correct']}/{report['bench']['n_topics']} "
        f"acc={report['bench']['accuracy']} | false-covered "
        f"{report['false_covered']['n_detected']}/{report['false_covered']['n_probes']} "
        f"| passed={report['acceptance']['passed']} sha256={envelope['digest']['sha256'][:16]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())