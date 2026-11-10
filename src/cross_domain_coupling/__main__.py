"""CLI: ``python -m src.cross_domain_coupling --spec <pack/spec.json> --out <report.json>``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gate import run_gate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-domain coupling consistency gate")
    ap.add_argument("--spec", required=True, help="path to case-pack spec JSON")
    ap.add_argument("--out", required=True, help="path to write gate report JSON")
    ap.add_argument("--allow-root", default=None, help="sandbox root for evaluator module")
    args = ap.parse_args(argv)
    report = run_gate(args.spec, allow_root=args.allow_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"overall_verdict": report["overall_verdict"], "summary": report["summary"]}, indent=2))
    return 0 if report["overall_verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
