"""CLI for the capability-ledger digest audit.

    python tools/capability_ledger_digest/cli.py audit  --spec S --db D --out DIR
    python tools/capability_ledger_digest/cli.py verify --spec S --db D --report R
    python tools/capability_ledger_digest/cli.py scan   --paths tools/capability_ledger_digest tests/capability_ledger_digest

Exit codes: 0 = success / all checks pass, 1 = verification failure or a
domain-vocabulary hit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from capability_ledger_digest.audit import (  # type: ignore
        load_report,
        report_markdown,
        run_audit,
        verify_audit,
        write_artifacts,
    )
    from capability_ledger_digest.spec import AuditSpec  # type: ignore
else:  # imported as a package
    from .audit import (
        load_report,
        report_markdown,
        run_audit,
        verify_audit,
        write_artifacts,
    )
    from .spec import AuditSpec

ROOT = Path(__file__).resolve().parents[2]


def _cmd_audit(args: argparse.Namespace) -> int:
    spec = AuditSpec.load(args.spec)
    report = run_audit(spec, args.db)
    out = write_artifacts(report, args.out)
    (out / "audit_report.md").write_text(report_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "out_dir": str(out),
                "capability_count": report["capability_count"],
                "level_counts": report["level_counts"],
                "known_label_accuracy": report["known_label_score"]["accuracy"],
                "ledger_sha256": report["ledger"]["sha256"],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    spec = AuditSpec.load(args.spec)
    verdict = verify_audit(report, spec, args.db)
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    return 0 if verdict["ok"] else 1


def _cmd_scan(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "tools"))
    import evidence_domain_scan as scan  # type: ignore

    result = scan.scan_paths(args.paths, root=ROOT)
    hits = result.get("hits", [])
    print(
        json.dumps(
            {"status": "ok" if not hits else "hit", "scanned_files": result.get("scanned_files"), "hits": hits},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if not hits else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="run the audit and materialise artefacts")
    p_audit.add_argument("--spec", required=True)
    p_audit.add_argument("--db", required=True)
    p_audit.add_argument("--out", required=True)
    p_audit.set_defaults(func=_cmd_audit)

    p_verify = sub.add_parser("verify", help="re-derive every digest of a report")
    p_verify.add_argument("--spec", required=True)
    p_verify.add_argument("--db", required=True)
    p_verify.add_argument("--report", required=True)
    p_verify.set_defaults(func=_cmd_verify)

    p_scan = sub.add_parser("scan", help="domain-vocabulary scan (must report zero hits)")
    p_scan.add_argument("--paths", nargs="+", required=True)
    p_scan.set_defaults(func=_cmd_scan)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())