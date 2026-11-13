"""cli —— 需求入榜 v2 的命令行入口（人/agent 共用同一套机门）。

子命令::

    ddl     建表（幂等；--with-support 用于一次性 schema）
    mint    铸造一条锚（机门 + 同步建单）；--no-dispatch 只入账不建单
    scan    从 succeeded 单的 finish result 批量抽候选锚并铸造（parser → mint）
    audit   事后抽样审计（--dry-run 只裁决不处置；--slug 定向）
    report  周报对照表（agent vs coordinator；--markdown / --json）
    redteam 五条可失败红队（默认一次性 schema，用完即弃）
    status  当日配额 + 待审积压 + 最近 chronicle

凭据：psql 走 ``TP_PG_*``；建单走 ``TENSOR_API_BASE`` / ``TENSOR_TOKEN``。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

from . import audit, chronicle, judge, mint, parser, report, redteam
from .dispatch import HttpDispatcher, RecordingDispatcher
from .store import (AUDIT_DAILY_CAP, DEFAULT_DAILY_CAP, DEFAULT_ORG_ID, PsqlTransport,
                    StoreError, count_audits_today, drop_schema, ensure_schema,
                    ensure_support_schema, fetch_audit_candidates, fetch_missions,
                    fetch_quota_all, fetch_strikes, q, sql_literal)


def _emit(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(obj)


def cmd_ddl(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    ensure_schema(t, args.schema)
    if args.with_support:
        ensure_support_schema(t, args.schema)
    _emit({"ok": True, "schema": args.schema or "(default)"}, args.json)
    return 0


def _dispatcher(args: argparse.Namespace):
    if getattr(args, "no_dispatch", False):
        return None
    if getattr(args, "fake_dispatch", False):
        return RecordingDispatcher()
    return HttpDispatcher()


def cmd_mint(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    cand = {"slug": args.slug, "title": args.title or args.slug,
            "acceptance": args.acceptance or "", "domain": args.domain,
            "repo": args.repo, "source_kind": args.source_kind,
            "source_ref": args.source_ref or args.slug}
    out = mint.mint(t, cand, mint_kind=args.kind, minted_by=args.minted_by,
                    org_id=args.org_id, dispatcher=_dispatcher(args),
                    daily_cap=args.cap, schema=args.schema)
    _emit(out.as_dict(), args.json)
    return 0 if out.status in (mint.MINTED, mint.DISPATCH_FAILED) else 3


def cmd_scan(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    rows = fetch_missions(t, since_id=args.since_id, mission_id=args.mission_id,
                          limit=args.limit, schema=args.schema)
    cands: List[Dict[str, Any]] = []
    for row in rows:
        for c in parser.parse_mission_row(row):
            d = c.as_row()
            d["source_ref"] = f"mission:{row.get('id')}"
            cands.append(d)
    cands = parser.dedupe_candidates(cands) if hasattr(parser, "dedupe_candidates") else cands
    res = mint.mint_many(t, [c for c in cands], mint_kind=args.kind,
                         minted_by=args.minted_by, org_id=args.org_id,
                         dispatcher=_dispatcher(args), schema=args.schema)
    res["scanned_missions"] = len(rows)
    _emit(res, args.json)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    summary = audit.run_audit(t, daily_cap=args.daily_cap, limit=args.limit,
                              mint_kind=args.kind, slugs=args.slug or None,
                              dry_run=args.dry_run, schema=args.schema)
    _emit(summary, args.json)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    rep = report.weekly_report(t, days=args.days, start=args.start, end=args.end,
                               schema=args.schema)
    if args.json:
        _emit(rep, True)
    else:
        print(report.render_markdown(rep))
    return 0


def cmd_redteam(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    schema = args.schema or f"m17134_rt_{uuid.uuid4().hex[:8]}"
    try:
        res = redteam.run_all(t, schema, bootstrap=True)
    finally:
        if not args.keep and not args.schema:
            drop_schema(t, schema)
    _emit(res, args.json)
    return 0 if res["all_ok"] else 4


def cmd_status(args: argparse.Namespace) -> int:
    t = PsqlTransport(db=args.db)
    pending = len(fetch_audit_candidates(t, 100000, schema=args.schema))
    payload = {
        "audits_used_today": count_audits_today(t, args.schema),
        "audit_daily_cap": AUDIT_DAILY_CAP,
        "pending_audit": pending,
        "quota_today": fetch_quota_all(t, schema=args.schema),
        "strikes": {a: fetch_strikes(t, a, DEFAULT_ORG_ID, args.schema) for a in args.agent},
        "recent_chronicle": chronicle.fetch(t, limit=args.limit, schema=args.schema),
    }
    _emit(payload, True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.demand_intake.cli",
                                description="需求入榜 v2：提案即单子 + 事后审计")
    p.add_argument("--db", default="", help="PG 数据库名（默认 TP_PG_DB）")
    p.add_argument("--schema", default="", help="schema 前缀（一次性测试 schema 用）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("ddl")
    d.add_argument("--with-support", action="store_true")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_ddl)

    m = sub.add_parser("mint")
    m.add_argument("--slug", required=True)
    m.add_argument("--title", default="")
    m.add_argument("--acceptance", default="")
    m.add_argument("--domain", default="platform")
    m.add_argument("--repo", default="TensorPlatform")
    m.add_argument("--kind", default="agent", choices=("agent", "coordinator"))
    m.add_argument("--minted-by", dest="minted_by", default="cli")
    m.add_argument("--org-id", dest="org_id", type=int, default=DEFAULT_ORG_ID)
    m.add_argument("--source-kind", dest="source_kind", default="cli")
    m.add_argument("--source-ref", dest="source_ref", default="")
    m.add_argument("--cap", type=int, default=None, help="覆盖当日帽（红队/回放用）")
    m.add_argument("--no-dispatch", action="store_true", help="只入账不建单")
    m.add_argument("--fake-dispatch", action="store_true", help="建单走本地记录桩（不联网）")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_mint)

    s = sub.add_parser("scan")
    s.add_argument("--since-id", dest="since_id", type=int, default=0)
    s.add_argument("--mission-id", dest="mission_id", type=int, default=None)
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--kind", default="agent", choices=("agent", "coordinator"))
    s.add_argument("--minted-by", dest="minted_by", default="cli")
    s.add_argument("--org-id", dest="org_id", type=int, default=DEFAULT_ORG_ID)
    s.add_argument("--no-dispatch", action="store_true")
    s.add_argument("--fake-dispatch", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scan)

    a = sub.add_parser("audit")
    a.add_argument("--limit", type=int, default=None)
    a.add_argument("--daily-cap", dest="daily_cap", type=int, default=AUDIT_DAILY_CAP)
    a.add_argument("--kind", default="agent", choices=("agent", "coordinator"))
    a.add_argument("--slug", action="append", default=[])
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_audit)

    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=7)
    r.add_argument("--start", default="")
    r.add_argument("--end", default="")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_report)

    rt = sub.add_parser("redteam")
    rt.add_argument("--keep", action="store_true")
    rt.add_argument("--json", action="store_true")
    rt.set_defaults(fn=cmd_redteam)

    st = sub.add_parser("status")
    st.add_argument("--agent", action="append", default=[])
    st.add_argument("--limit", type=int, default=5)
    st.set_defaults(fn=cmd_status)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())