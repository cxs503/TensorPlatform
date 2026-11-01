#!/usr/bin/env python3
"""船舶基准参考数据完整性审计（CI 闸门）。

用法：python3 scripts/audit_benchmark_integrity.py [--json PATH] [--strict]
  退出码 0 = 无 FAIL 行（可作判据）；1 = 存在 FAIL 行（参考数据不可作判据）。
产物：artifacts/benchmark_integrity_audit.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from services.benchmark_integrity import (  # noqa: E402
    audit_dataset, audit_proxy_table, dataset_path,
)


def main() -> int:
    json_out = None
    args = sys.argv[1:]
    if "--json" in args:
        json_out = Path(args[args.index("--json") + 1])
    ds = audit_dataset(dataset_path())
    from services.ship_proxy import BENCHMARK_REFERENCE
    px = audit_proxy_table(BENCHMARK_REFERENCE)
    report = {"dataset": ds, "proxy_table": px,
              "binding": ds["binding"] and px["binding"]}
    out = ROOT / "artifacts" / "benchmark_integrity_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if json_out:
        json_out.write_text(out.read_text())

    for name, blk in (("benchmark_reference.json", ds), ("ship_proxy.BENCHMARK_REFERENCE", px)):
        print(f"[{name}] total={blk['total']} verdicts={blk['verdict_counts']} binding={blk['binding']}")
        for row in blk["rows"]:
            bad = [c for c in row["checks"] if c["status"] in ("FAIL", "WITHHELD")]
            print(f"  {row['case_id']:<28} {row['verdict']:<9} " +
                  "; ".join(f"{c['check']}={c['status']}" for c in bad))
    print(f"\nartifact → {out.relative_to(ROOT)}")
    return 0 if report["binding"] or "--strict" not in args else 1


if __name__ == "__main__":
    sys.exit(main())
