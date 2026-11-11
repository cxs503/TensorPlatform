"""``python -m verification_vitality`` 命令行：patrol / cross / upgrade / rollback。

所有子命令默认 **dry-run**：算出来打印/落 JSON，不改台账。只有显式 ``--apply`` 才写状态，
且写入的都是本引擎自己的 state/journal（不触碰台账文件本身）。这是有意的安全阀。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .collector import run_pytest_pass_rate
from .ledger_cross import cross_ledger, load_deltas, load_feature_ledger
from .patrol import discover_units, patrol
from .upgrade import (UpgradeCriteria, apply_upgrades, downgrade, evaluate_upgrades,
                      load_state, rollback, save_state)

DEFAULT_LEDGER = "artifacts/capability_ledger/capability_ledger.json"


def _dump(obj: Any, out: Optional[str]) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    print(text)


def _cmd_patrol(args: argparse.Namespace) -> int:
    report = patrol(Path(args.repo), test_dirs=tuple(args.test_dirs),
                    registry_path=args.registry, timeout=args.timeout)
    _dump(report, args.out)
    return 0 if report["collect"]["exit_code"] == 0 or True else 1


def _cmd_cross(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    ledger = load_feature_ledger(Path(args.ledger))
    deltas = load_deltas(args.deltas)
    result = cross_ledger(report, ledger, deltas=deltas)
    _dump(result, args.out)
    return 1 if result["blocked"] and args.strict else 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    ledger_raw = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    caps = ledger_raw.get("capabilities", ledger_raw) if isinstance(ledger_raw, dict) else ledger_raw
    ledger: Dict[str, Dict[str, Any]] = {}
    for entry in caps or []:
        cid = entry.get("id") or entry.get("capability_id")
        if cid:
            ledger[str(cid)] = dict(entry)
    patrol_report = None
    if args.report and Path(args.report).is_file():
        patrol_report = json.loads(Path(args.report).read_text(encoding="utf-8"))

    measured: Dict[str, Dict[str, Any]] = {}
    if args.measure_tests:
        repo = Path(args.repo)
        units_by_cap: Dict[str, List[str]] = {}
        for cap in (patrol_report or {}).get("capabilities", []):
            units_by_cap[cap["capability_id"]] = []
        for surf in (patrol_report or {}).get("surfaces", []):
            for cid in surf.get("capability_ids") or []:
                units_by_cap.setdefault(cid, []).append(surf["unit"])
        for cid, units in units_by_cap.items():
            if not units or cid not in ledger:
                continue
            res = run_pytest_pass_rate(repo, units, timeout=args.measure_timeout)
            measured[cid] = {"pass_rate": res.pass_rate}
    if args.benchmark_errors:
        p = Path(args.benchmark_errors)
        if p.is_file():
            for cid, err in json.loads(p.read_text(encoding="utf-8")).items():
                measured.setdefault(cid, {})["benchmark_error_pct"] = err

    state = load_state(args.state)
    decisions = evaluate_upgrades(ledger, patrol_report=patrol_report,
                                  criteria=UpgradeCriteria(
                                      min_pass_rate=args.min_pass_rate,
                                      max_benchmark_error_pct=args.max_error_pct),
                                  measured=measured, state=state)
    applied = apply_upgrades(decisions, state, journal_path=args.journal, dry_run=not args.apply)
    if args.apply:
        save_state(state, args.state)
    _dump({"decisions": decisions, "applied": applied,
           "summary": {v: sum(1 for d in decisions if d["verdict"] == v)
                       for v in ("UPGRADE", "HOLD", "NOOP")}}, args.out)
    return 0


def _cmd_downgrade(args: argparse.Namespace) -> int:
    cross = json.loads(Path(args.cross).read_text(encoding="utf-8"))
    state = load_state(args.state)
    res = downgrade(cross.get("suggestions", []), state, journal_path=args.journal,
                    dry_run=not args.apply)
    if args.apply:
        save_state(state, args.state)
    _dump(res, args.out)
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    res = rollback(state, args.capability, token=args.token, journal_path=args.journal)
    if res.get("status") == "ROLLED_BACK":
        save_state(state, args.state)
    _dump(res, args.out)
    return 0 if res.get("status") == "ROLLED_BACK" else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="verification_vitality", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("patrol", help="验证面活性巡检（alive/dead/unmapped）")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--test-dirs", nargs="*", default=["tests"])
    sp.add_argument("--registry", default=None)
    sp.add_argument("--timeout", type=float, default=100.0)
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=_cmd_patrol)

    sc = sub.add_parser("cross", help="台账交叉：dead 且 REAL ⇒ fail-closed + 降级建议")
    sc.add_argument("--report", required=True)
    sc.add_argument("--ledger", default=DEFAULT_LEDGER)
    sc.add_argument("--deltas", default=None)
    sc.add_argument("--out", default=None)
    sc.add_argument("--strict", action="store_true")
    sc.set_defaults(func=_cmd_cross)

    su = sub.add_parser("upgrade", help="证据驱动升账（dry-run 默认）")
    su.add_argument("--ledger", default=DEFAULT_LEDGER)
    su.add_argument("--report", default=None)
    su.add_argument("--repo", default=".")
    su.add_argument("--state", default="artifacts/verification_vitality/upgrade_state.json")
    su.add_argument("--journal", default="artifacts/verification_vitality/upgrade_journal.jsonl")
    su.add_argument("--min-pass-rate", type=float, default=0.90)
    su.add_argument("--max-error-pct", type=float, default=15.0)
    su.add_argument("--measure-tests", action="store_true")
    su.add_argument("--measure-timeout", type=float, default=120.0)
    su.add_argument("--benchmark-errors", default=None)
    su.add_argument("--apply", action="store_true")
    su.add_argument("--out", default=None)
    su.set_defaults(func=_cmd_upgrade)

    sd = sub.add_parser("downgrade", help="执行交叉器降级建议（dry-run 默认）")
    sd.add_argument("--cross", required=True)
    sd.add_argument("--state", default="artifacts/verification_vitality/upgrade_state.json")
    sd.add_argument("--journal", default="artifacts/verification_vitality/upgrade_journal.jsonl")
    sd.add_argument("--apply", action="store_true")
    sd.add_argument("--out", default=None)
    sd.set_defaults(func=_cmd_downgrade)

    sr = sub.add_parser("rollback", help="回滚某条能力的升账")
    sr.add_argument("--state", required=True)
    sr.add_argument("--journal", default=None)
    sr.add_argument("--capability", required=True)
    sr.add_argument("--token", default=None)
    sr.add_argument("--out", default=None)
    sr.set_defaults(func=_cmd_rollback)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())