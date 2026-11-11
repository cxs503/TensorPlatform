"""CLI: run the experiments and write the report bundle.

    python -m tools.dispatch_shadow.cli --out artifacts/dispatch_shadow
    python -m tools.dispatch_shadow.cli --rounds 400 --bootstrap 200 --skip-pytest

Exit code is 0 only when every acceptance criterion in the report passed, so CI
can gate on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .report import OUT_DIR, build_report, run_experiments, write_artifacts

NODE = "p495w4-n01-platform-dispatch-log-shadow-replay"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_DIR, help="artifact directory")
    ap.add_argument("--rounds", type=int, default=1500, help="labelled rounds in the main log")
    ap.add_argument("--bootstrap", type=int, default=500, help="bootstrap replicates")
    ap.add_argument("--mission", type=int, default=13940)
    ap.add_argument("--skip-pytest", action="store_true", help="do not re-run the pytest suite")
    args = ap.parse_args(argv)

    run = run_experiments(
        main_rounds=args.rounds, bootstrap=args.bootstrap, with_pytest=not args.skip_pytest
    )
    report = build_report(run, node=NODE, mission=args.mission)
    write_artifacts(report, args.out)

    print(json.dumps({
        "out": os.path.abspath(args.out),
        "gate_status": report["headline"]["gate_status"],
        "all_criteria_passed": report["all_criteria_passed"],
        "failed": [c["id"] for c in report["criteria"] if not c["passed"]],
        "headline": report["headline"],
    }, indent=2))
    return 0 if report["all_criteria_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())