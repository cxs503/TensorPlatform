#!/usr/bin/env python3
"""真值可供给性账本 CLI（m1641）。

用法::

    PYTHONPATH=backend python3 tools/audit/reference_recovery_ledger.py
    PYTHONPATH=backend python3 tools/audit/reference_recovery_ledger.py --out artifacts/reference_recovery/ledger.json

只读：仅读取 ``backend/data/benchmark_reference.json`` 与 ``services.holtrop_mennen``，
不写回正典、不改任何判定（正典判定归 ``benchmark_canon``）。纯 CPU 毫秒级。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services import reference_recovery as rr  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "artifacts/reference_recovery/ledger.json"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    doc = rr.ledger()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rr.to_json(doc) + "\n", encoding="utf-8")

    if not args.quiet:
        print(f"rows={doc['total_rows']} usable={doc['usable_count']} "
              f"kpi_target={doc['kpi_target']} gap={doc['kpi_gap']}")
        print(f"by_verdict={doc['by_verdict']}")
        for ship, fc in doc["factor_consistency"].items():
            print(f"  {ship:10s} factors={fc['factors']} spread={fc['spread_ratio']} "
                  f"single_factor_explains={fc['single_factor_explains']} :: {fc['reading']}")
        for r in doc["rows"]:
            print(f"  {r['case_id']:12s} {r['verdict']:24s} "
                  f"s_implied={r['s_implied_m2']} band={r['s_band_m2']} "
                  f"factor={r['rt_factor_center']}")
        print(f"ledger -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())