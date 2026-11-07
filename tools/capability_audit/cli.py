"""Command line entry point for the platform capability audit.

Subcommands
-----------
audit    run the four probe families against the live capability endpoints
report   render Markdown + figures + acceptance JSON from the audit results
anchor   build the Merkle manifest, sign it with Ed25519, attach a git time anchor
verify   re-hash the artifacts, re-check the signature, the commit and the chain

Typical use::

    python3 -m tools.capability_audit.cli audit  --out artifacts/capability_audit
    python3 -m tools.capability_audit.cli report --out artifacts/capability_audit
    python3 -m tools.capability_audit.cli anchor --out artifacts/capability_audit
    python3 -m tools.capability_audit.cli verify --out artifacts/capability_audit

Exit codes: 0 success, 1 audit found falsified capabilities, 2 verification failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # allow `python tools/capability_audit/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.capability_audit import adapters, anchor as anchor_mod, probes, report  # noqa: E402
from tools.capability_audit.transport import HttpTransport, probe_transport  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8001/api/v1"
DEFAULT_OUT = "artifacts/capability_audit"
SCHEMA = "capability-audit-results/1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_audit(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = adapters.SPECS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        specs = [s for s in specs if s.cap_id in wanted]
        missing = wanted - {s.cap_id for s in specs}
        if missing:
            print(f"unknown capability ids: {sorted(missing)}", file=sys.stderr)
            return 2
    transport = HttpTransport(args.api_base, timeout=args.timeout)
    reachable, note = probe_transport(transport, args.api_base)
    claims = adapters.load_claims(args.snapshot)
    # Align the claimed level with the platform snapshot when the id is registered.
    for spec in specs:
        if spec.cap_id in claims:
            spec.claimed_level = claims[spec.cap_id]
            spec.claim_source = f"capabilities_snapshot.json::{spec.cap_id}"
    if not reachable:
        print(f"WARNING: API {args.api_base} unreachable ({note}); capabilities -> WITHHELD",
              file=sys.stderr)
    payload = probes.audit_all(specs, transport, api_base=args.api_base,
                               reachable=reachable, reachability_note=note)
    result = {
        "schema": SCHEMA,
        "mission_id": args.mission_id,
        "generated_at": _now(),
        "tool": "tools.capability_audit",
        "api_base": args.api_base,
        "api_calls": getattr(transport, "calls", 0),
        "snapshot_claims": claims,
        "caps": payload["caps"],
        "criteria": payload["criteria"],
    }
    _write_json(out_dir / "audit_results.json", result)
    crit = result["criteria"]
    print(f"audited {crit['capabilities_audited']} capabilities, "
          f"{crit['checks_total']} checks ({crit['checks_passed']} pass / "
          f"{crit['checks_failed']} fail / {crit['checks_skipped']} skipped)")
    for cap in result["caps"]:
        flag = "OVERCLAIM" if cap["overclaim"] else cap["observed_level"]
        print(f"  {cap['cap_id']:<28} claim={cap['claimed_level']:<12} "
              f"mechanism={cap['observed_level']:<9} accuracy={cap['accuracy_level']:<10} "
              f"{flag}")
    print(f"results -> {out_dir / 'audit_results.json'}")
    if args.strict and crit["checks_failed"]:
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    audit_path = Path(args.audit) if args.audit else out_dir / "audit_results.json"
    if not audit_path.exists():
        print(f"missing audit results: {audit_path}", file=sys.stderr)
        return 2
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    anchor = None
    anchor_path = out_dir / "anchor.json"
    if anchor_path.exists():
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    verification = None
    ver_path = out_dir / "verification.json"
    if ver_path.exists():
        verification = json.loads(ver_path.read_text(encoding="utf-8"))
    rendered = report.render_report(audit, out_dir, anchor=anchor, verification=verification,
                                    dpi=args.dpi,
                                    regenerate_figures=not args.reuse_figures)
    crit = rendered["criteria"]
    print(f"report -> {out_dir / 'report.md'}")
    print(f"criteria -> {out_dir / 'criteria.json'}")
    print(f"figures  -> {len(rendered['figures'])} png in {out_dir / 'figures'}")
    print(f"acceptance: {json.dumps(crit['pass'], ensure_ascii=False)} all_passed={crit['all_passed']}")
    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    repo = Path(args.repo) if args.repo else _repo_root()
    prev = Path(args.prev) if args.prev else (out_dir / "anchor.json")
    prev_arg = prev if prev.exists() and prev != (out_dir / "anchor.json") else (
        prev if prev.exists() else None)
    index = args.index
    if args.index is None:
        index = 1
        if prev_arg is not None:
            try:
                index = int(json.loads(prev_arg.read_text())["chain"]["index"]) + 1
            except (KeyError, ValueError, json.JSONDecodeError):
                index = 2
    if prev_arg is not None and Path(prev_arg).resolve() == (out_dir / "anchor.json").resolve():
        # chaining onto ourselves is meaningless: keep the file as the predecessor record
        prev_arg = None
    key_file = Path(args.key_file) if args.key_file else (out_dir / "keys" / "audit_signing.key")
    record = anchor_mod.create_anchor(out_dir, repo, key_file=key_file,
                                      pin_ref=args.pin_ref, prev_anchor=prev_arg,
                                      index=index)
    _write_json(out_dir / "anchor.json", record)
    print(f"artifacts: {record['artifact_count']}")
    print(f"merkle root: {record['artifact_root']}")
    print(f"signature: ed25519:{record['signature']['fingerprint']}")
    print(f"git anchor: {record['external_time_anchor'].get('commit', 'n/a')} "
          f"({record['external_time_anchor'].get('committer_date', 'n/a')})")
    print(f"anchor -> {out_dir / 'anchor.json'}")
    print("\ncommit message latch (quote the root when committing the artifacts):")
    print(f"  CapabilityAudit-Anchor-Root: {record['artifact_root']}")
    print(f"  CapabilityAudit-Anchor-Key: {record['signature']['fingerprint']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    repo = Path(args.repo) if args.repo else _repo_root()
    anchor_path = Path(args.anchor) if args.anchor else out_dir / "anchor.json"
    if not anchor_path.exists():
        print(f"missing anchor: {anchor_path}", file=sys.stderr)
        return 2
    result = anchor_mod.verify_anchor(anchor_path, out_dir, repo,
                                      require_commit_message=args.require_commit_message)
    _write_json(out_dir / "verification.json", result)
    for check in result["checks"]:
        print(f"  [{'OK' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    print(f"verification -> {out_dir / 'verification.json'}")
    print(f"VERIFIED={result['ok']}")
    return 0 if result["ok"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capability_audit",
        description="Automated REAL/MOCK/STUB falsification + Merkle/Ed25519 audit anchor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("audit", help="run invariance/sensitivity/analytic/monotonic probes")
    p.add_argument("--api-base", default=DEFAULT_API)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--snapshot", default=None, help="capability snapshot JSON (claim source)")
    p.add_argument("--only", default=None, help="comma separated capability ids")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--mission-id", type=int, default=5299)
    p.add_argument("--strict", action="store_true", help="exit 1 when any fatal check fails")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("report", help="render markdown, figures and acceptance JSON")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--audit", default=None)
    p.add_argument("--dpi", type=int, default=160)
    p.add_argument("--reuse-figures", action="store_true",
                   help="keep existing PNGs (they are part of the anchored manifest)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("anchor", help="Merkle manifest + Ed25519 signature + git time anchor")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help="artifact directory: manifest is built from here, anchor.json written here")
    p.add_argument("--repo", default=None)
    p.add_argument("--key-file", default=None)
    p.add_argument("--pin-ref", default="HEAD")
    p.add_argument("--prev", default=None, help="previous anchor.json to chain onto")
    p.add_argument("--index", type=int, default=None)
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("verify", help="verify digests, Merkle root, signature, commit and chain")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help="artifact directory: artifacts are re-hashed from here and "
                        "verification.json is written here")
    p.add_argument("--repo", default=None)
    p.add_argument("--anchor", default=None)
    p.add_argument("--require-commit-message", action="store_true")
    p.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())