"""A1–A8 验收入口：跑批 → 工件 → 图 → 报告。

    python3 -m tools.capability_drift.run_acceptance [--workers 4] [--keep-sandbox]

退出码 0 = 全部判据通过；1 = 有判据未过（honest FAIL，不覆盖）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from . import acceptance, report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="capability drift monitor acceptance (A1-A8)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-sandbox", action="store_true", help="保留临时沙箱仓（调试用）")
    ap.add_argument("--no-report", action="store_true", help="不生成 markdown 报告")
    args = ap.parse_args(argv)

    results = acceptance.run_acceptance(workers=args.workers, keep_sandbox=args.keep_sandbox)
    if not args.no_report:
        results["report_path"] = str(
            report.build_report(results).relative_to(acceptance.ROOT)
        )
        acceptance._json(acceptance.ART / "acceptance_report.json", results)
    print(json.dumps({
        "overall_passed": results.get("overall_passed"),
        "criteria": results.get("criteria_summary"),
        "acceptance_report": "artifacts/capability_drift/acceptance_report.json",
        "report": results.get("report_path", ""),
    }, ensure_ascii=False, indent=2))
    return 0 if results.get("overall_passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())