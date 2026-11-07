"""cli —— 质询官命令行（cron/人工共用）。

用法::

  python3 -m src.interrogator.cli probe  --out runs/IN27-H4
  python3 -m src.interrogator.cli cycle  --create-missions        # 真实立单（24h 定时用这个）
  python3 -m src.interrogator.cli cycle  --dry-run                # 只出题不立单（首轮校准）
  python3 -m src.interrogator.cli backfill --from-dir runs/IN27-H4/snapshots  # 历史快照回放（标注 backfill）
  python3 -m src.interrogator.cli answer --fingerprint EB-01 ... --verdict "..." --path docs/x.md
  python3 -m src.interrogator.cli convert --fingerprint EB-01 --kind repair --evidence path --task-key ...
  python3 -m src.interrogator.cli verify --out runs/IN27-H4/verdict.json
  python3 -m src.interrogator.cli daemon --interval 86400 --create-missions

退出码：verify 的 PASS→0，PENDING→3，FAIL→1（便于 cron/守望线直接判读）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from .acceptance import evaluate, write_verdict
from .daemon import DEFAULT_OUT_DIR, InterrogatorDaemon
from .ledger import DEFAULT_LEDGER_PATH, Ledger
from .probes import HttpSignalSource, MappingSignalSource, load_snapshot, save_snapshot


def _ledger(args) -> Ledger:
    return Ledger(args.ledger)


def cmd_probe(args) -> int:
    source = HttpSignalSource(api_base=args.api_base) if args.api_base else HttpSignalSource()
    signals = source.collect(window_days=7)
    out = os.path.join(args.out, "signals_probe.json")
    save_snapshot(signals, out)
    summary = {k: {"value": v.value, "status": v.status, "source": v.source}
               for k, v in signals.items()}
    print(json.dumps({"path": out, "signals": summary}, ensure_ascii=False, indent=2)[:6000])
    return 0


def cmd_cycle(args) -> int:
    source = None
    if args.signals:
        source = MappingSignalSource(
            {k: v.value for k, v in load_snapshot(args.signals).items()},
            label=f"snapshot:{args.signals}")
    daemon = InterrogatorDaemon(
        ledger_path=args.ledger,
        source=source,
        api_base=None if args.dry_run else args.api_base,
        out_dir=args.out,
        repo=args.repo,
    )
    if not args.dry_run and args.create_missions and daemon.poster is None:
        print("需要 --api-base 才能立单", file=sys.stderr)
        return 2
    artifact = daemon.run_once(create_missions=args.create_missions and not args.dry_run)
    print(json.dumps({k: artifact[k] for k in
                      ("cycle_id", "cycle_date", "source_status", "artifact_path",
                       "created_missions", "summary")}, ensure_ascii=False, indent=2))
    return 0


def cmd_backfill(args) -> int:
    """用历史信号快照按日回放（mode=backfill，绝不冒充当日 live 产出）。"""
    entries = sorted(f for f in os.listdir(args.from_dir)
                     if f.startswith("signals_") and f.endswith(".json"))
    if not entries:
        print(json.dumps({"error": "无历史快照", "dir": args.from_dir}, ensure_ascii=False))
        return 1
    done = []
    for name in entries[-args.days:]:
        date = name[len("signals_"):-len(".json")]
        signals = load_snapshot(os.path.join(args.from_dir, name))
        daemon = InterrogatorDaemon(
            ledger_path=args.ledger,
            source=MappingSignalSource({k: v.value for k, v in signals.items()},
                                       label=f"snapshot:{name}"),
            api_base=None,
            out_dir=args.out,
            repo=args.repo,
        )
        artifact = daemon.run_once(create_missions=False, mode="backfill")
        done.append({"cycle_id": artifact["cycle_id"], "date": date,
                     "questions": artifact["summary"]["count"]})
    print(json.dumps({"backfilled": done}, ensure_ascii=False, indent=2))
    return 0


def cmd_answer(args) -> int:
    with _ledger(args) as ledger:
        ledger.record_answer(args.fingerprint, args.verdict, args.path)
        print(json.dumps({"fingerprint": args.fingerprint, "state": "answered"}, ensure_ascii=False))
    return 0


def cmd_convert(args) -> int:
    with _ledger(args) as ledger:
        cid = ledger.record_conversion(
            identifier=args.fingerprint, kind=args.kind, evidence_path=args.evidence,
            fix_mission_id=args.mission_id, fix_task_key=args.task_key, note=args.note)
        print(json.dumps({"conversion_id": cid, "fingerprint": args.fingerprint,
                          "kind": args.kind}, ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    as_of = (datetime.fromisoformat(args.as_of) if args.as_of
             else datetime.now(timezone.utc))
    with _ledger(args) as ledger:
        verdict = evaluate(ledger, as_of=as_of, required_days=args.required_days,
                           required_conversions=args.required_conversions)
        extra = {"ledger_snapshot": ledger.export_json(
            os.path.join(args.out, "ledger_export.json"))} if args.out else None
        out = args.out or os.path.dirname(ledger.path)
        path = write_verdict(verdict, os.path.join(out, "verdict.json"), extra)
    print(json.dumps({"verdict": verdict.verdict, "path": path,
                      "criteria": [{"name": c.name, "ok": c.ok, "numbers": c.numbers}
                                   for c in verdict.criteria]},
                     ensure_ascii=False, indent=2))
    return {"PASS": 0, "PENDING": 3}.get(verdict.verdict, 1)


def cmd_daemon(args) -> int:
    daemon = InterrogatorDaemon(
        ledger_path=args.ledger, api_base=args.api_base, out_dir=args.out,
        interval=args.interval, repo=args.repo)
    daemon.serve_forever(create_missions=args.create_missions, max_cycles=args.max_cycles)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="interrogator", description="IN27-H4 战略质询官")
    p.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    p.add_argument("--api-base", default=None)
    p.add_argument("--out", default=DEFAULT_OUT_DIR)
    p.add_argument("--repo", default="TensorPlatform")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe"); sp.set_defaults(func=cmd_probe)

    sc = sub.add_parser("cycle")
    sc.add_argument("--create-missions", action="store_true")
    sc.add_argument("--dry-run", action="store_true")
    sc.add_argument("--signals", default=None, help="离线信号快照（测试/回放）")
    sc.set_defaults(func=cmd_cycle)

    sb = sub.add_parser("backfill")
    sb.add_argument("--from-dir", required=True)
    sb.add_argument("--days", type=int, default=7)
    sb.set_defaults(func=cmd_backfill)

    sa = sub.add_parser("answer")
    sa.add_argument("--fingerprint", required=True)
    sa.add_argument("--verdict", required=True)
    sa.add_argument("--path", default=None)
    sa.set_defaults(func=cmd_answer)

    sv = sub.add_parser("convert")
    sv.add_argument("--fingerprint", required=True)
    sv.add_argument("--kind", choices=("repair", "improvement"), required=True)
    sv.add_argument("--evidence", required=True)
    sv.add_argument("--mission-id", type=int, default=None)
    sv.add_argument("--task-key", default=None)
    sv.add_argument("--note", default="")
    sv.set_defaults(func=cmd_convert)

    sy = sub.add_parser("verify")
    sy.add_argument("--as-of", default=None)
    sy.add_argument("--required-days", type=int, default=7)
    sy.add_argument("--required-conversions", type=int, default=2)
    sy.set_defaults(func=cmd_verify)

    sd = sub.add_parser("daemon")
    sd.add_argument("--interval", type=int, default=86400)
    sd.add_argument("--create-missions", action="store_true")
    sd.add_argument("--max-cycles", type=int, default=None)
    sd.set_defaults(func=cmd_daemon)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())