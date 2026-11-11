"""Command line surface: the CLI *is* the integration point for CI and hooks.

Subcommands
-----------
``scan``            scan a file or stdin (ad hoc review)
``gate-commit``     exit 1 — diff-level pre-commit gate (exit code 2 = blocked)
``gate-report``     exit 2 — payload-level report gate (exit code 2 = blocked)
``write-artifact``  exit 3 — artifact writer gate
``calibrate``       run the injected calibration corpus (C1/C2 numbers)
``history``         read-only history rescan (C5 exposure list)
``ci``              the pipeline step: calibration + all three exits + self-scan
``acceptance``      full C1..C9 acceptance run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import gates
from .calibrate import build_corpus
from .engine import Scanner
from .history import scan_history
from .policy import PolicyError, load_policy


def _exit_for(verdict) -> int:
    if verdict.decision == "allow":
        return 0
    return 2


def _emit(payload: Dict[str, Any], path: Optional[str]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)


def _scanner(args) -> Scanner:
    return Scanner(load_policy(args.policy))


def cmd_scan(args) -> int:
    scanner = _scanner(args)
    if args.path:
        with open(args.path, "r", encoding="utf-8", errors="surrogateescape") as handle:
            text = handle.read()
        label = args.path
    else:
        text = sys.stdin.read()
        label = "<stdin>"
    result = scanner.scan_text(text, path=label, decode=not args.no_decode,
                               apply_location_whitelist=not args.no_whitelist)
    _emit(result.to_dict(), args.json)
    return 0 if result.clean else 2


def cmd_gate_commit(args) -> int:
    verdict = gates.gate_pre_commit(args.repo, policy_path=args.policy,
                                   staged_only=not args.all_changes, diff_text=None)
    _emit(verdict.to_dict(), args.json)
    if verdict.blocked and not args.json:
        print("secret-scan: BLOCK ({} findings, {})".format(len(verdict.scan.hits), verdict.decision),
              file=sys.stderr)
    return _exit_for(verdict)


def cmd_gate_report(args) -> int:
    if args.payload == "-":
        payload = json.load(sys.stdin)
    else:
        with open(args.payload, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    verdict = gates.gate_report(payload, policy_path=args.policy)
    _emit(verdict.to_dict(), args.json)
    return _exit_for(verdict)


def cmd_write_artifact(args) -> int:
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8", errors="surrogateescape") as handle:
            content = handle.read()
    else:
        content = sys.stdin.read()
    verdict = gates.gate_artifact(args.path, content, policy_path=args.policy, write=not args.dry_run)
    _emit(verdict.to_dict(), args.json)
    return _exit_for(verdict)


def cmd_calibrate(args) -> int:
    from .acceptance import criterion_c1_c2

    result = criterion_c1_c2(args.policy, seed=args.seed)
    payload = {"C1": result["C1"], "C2": result["C2"],
               "transport_cases": result["transport_cases"],
               "corpus_summary": {"secrets": len(result["corpus"]["secrets"]),
                                  "benign": len(result["corpus"]["benign"]),
                                  "transport": len(result["corpus"]["transport"])}}
    _emit(payload, args.json)
    return 0 if (result["C1"]["pass"] and result["C2"]["pass"]) else 2


def cmd_history(args) -> int:
    report = scan_history(args.policy, limit_missions=args.limit, max_rows=args.max_rows)
    _emit(report, args.json or "artifacts/secret_scan/history_exposure.json")
    print(json.dumps({k: report[k] for k in ("missions_scanned", "files_scanned", "files_with_hits",
                                             "full_profile_hits", "baseline_profile_hits",
                                             "by_confidence", "remediation_residual_hits",
                                             "readonly_verified")}, ensure_ascii=False, indent=2))
    return 0 if report["remediation_residual_hits"] == 0 else 2


def cmd_ci(args) -> int:
    """The pipeline step: it must be able to fail, so it injects on purpose."""
    from .acceptance import criterion_c1_c2, criterion_c3

    failures: List[str] = []
    calib = criterion_c1_c2(args.policy, seed=args.seed)
    if not calib["C1"]["pass"]:
        failures.append("C1")
    if not calib["C2"]["pass"]:
        failures.append("C2")
    exits = criterion_c3(args.policy, seed=args.seed)
    if not exits["pass"]:
        failures.append("C3")

    # self-scan: this module's own sources and the policy must be clean
    policy = load_policy(args.policy)
    scanner = Scanner(policy)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    self_hits = 0
    for name in sorted(os.listdir(pkg_dir)):
        if name.endswith(".py"):
            with open(os.path.join(pkg_dir, name), "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            self_hits += len(scanner.scan_text(text, path="tools/secret_scan/" + name).hits)
    if not args.quiet:
        print(json.dumps({
            "detection_rate": calib["C1"]["detection_rate"],
            "false_positive_rate": calib["C2"]["false_positive_rate"],
            "exits": {k: exits[k]["pass"] for k in ("pre_commit", "report_gate", "artifact_writer")},
            "self_scan_hits": self_hits,
            "policy_sha256": policy.sha256,
        }, ensure_ascii=False, indent=2))
    if self_hits:
        failures.append("self_scan")
    if failures:
        print("secret-scan CI: FAIL {}".format(failures), file=sys.stderr)
        return 2
    if not args.quiet:
        print("secret-scan CI: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.secret_scan.cli",
                                     description="three-exit secret leak gate")
    parser.add_argument("--policy", default=None, help="policy JSON path (default: data/secret_scan/policy.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="scan a file or stdin")
    p.add_argument("--path", default=None)
    p.add_argument("--json", default=None)
    p.add_argument("--no-decode", action="store_true")
    p.add_argument("--no-whitelist", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("gate-commit", help="diff-level pre-commit gate")
    p.add_argument("--repo", default=".")
    p.add_argument("--all-changes", action="store_true", help="scan unstaged diff too")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_gate_commit)

    p = sub.add_parser("gate-report", help="payload-level report gate")
    p.add_argument("--payload", required=True, help="JSON file, or - for stdin")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_gate_report)

    p = sub.add_parser("write-artifact", help="artifact writer gate")
    p.add_argument("--path", required=True)
    p.add_argument("--content-file", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_write_artifact)

    p = sub.add_parser("calibrate", help="injection calibration (C1/C2)")
    p.add_argument("--seed", default="tp-secret-scan-calibration-v1")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("history", help="read-only history rescan (C5)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--max-rows", type=int, default=400)
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("ci", help="pipeline step: calibration + exits + self-scan")
    p.add_argument("--seed", default="tp-secret-scan-calibration-v1")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_ci)

    p = sub.add_parser("acceptance", help="full C1..C9 acceptance")
    p.add_argument("--history-limit", type=int, default=150)
    p.add_argument("--out", default="artifacts/secret_scan/acceptance.json")
    p.add_argument("--skip-history", action="store_true")
    p.set_defaults(func=lambda args: _acceptance(args))
    return parser


def _acceptance(args) -> int:
    from .acceptance import main as acceptance_main

    argv = ["--history-limit", str(args.history_limit), "--out", args.out]
    if args.policy:
        argv += ["--policy", args.policy]
    if args.skip_history:
        argv.append("--skip-history")
    sys.argv = ["acceptance"] + argv
    return acceptance_main()


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PolicyError as exc:
        print("secret-scan: policy error (fail-closed): {}".format(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())