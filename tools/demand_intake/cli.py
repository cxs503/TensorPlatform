"""cli —— demand_intake 的唯一入口（dispatcher/operator 只调这个）。

子命令
------
``init``            建表（幂等 DDL：demand_intake_log / demand_backlog）
``ingest-missions`` succeeded 单增量扫描 → 候选锚 → ``demand_intake_log`` + ``feature_ledger``
                    (status=pending_review, source=agent-proposed)。``--dry-run`` 只读。
``gate``            pending_review → open/dropped（三判据，见 :mod:`tools.demand_intake.gate`）
``audit``           事后抽样审计：可领锚按日帽重审，不过则 dropped + 未终态单 revoked
                    （见 :mod:`tools.demand_intake.audit`）。不带 ``--apply`` 即 dry-run。
``ingest-reports``  ``tensor_reports.markdown`` 的「局限/待办」节 → ``demand_backlog``
``measure``         入榜率口径（detected / landed / open / dropped 原因码分布）
``redteam``         在**临时 schema** 里跑全链路红队（断链 + 垃圾锚拦截），跑完 DROP
``soak``            七日浸泡审计：覆盖率与帽拒率周报（见 :mod:`tools.demand_intake.soak`）
``status``          一行摘要

纪律
----
- 一切写操作走 :mod:`tools.demand_intake.store` 的 SQL builder，不拼裸 SQL；
- ``--dry-run`` 全程零写（redteam 除外，它只在临时 schema 里写）；
- 每个子命令结束都往 ``chronicle_snapshots`` 追加一条事件（失败不阻断主链）。

用法::

    python3 -m tools.demand_intake.cli ingest-missions --limit 200
    python3 -m tools.demand_intake.cli gate --apply
    python3 -m tools.demand_intake.cli audit --cap 20 --apply
    python3 -m tools.demand_intake.cli ingest-reports --since-id 9000 --limit 200
    python3 -m tools.demand_intake.cli measure
    python3 -m tools.demand_intake.cli redteam --out artifacts/demand_intake/redteam.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # python3 tools/demand_intake/cli.py 直跑兼容
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.demand_intake import audit as A         # noqa: E402
from tools.demand_intake import gate as G           # noqa: E402
from tools.demand_intake import store as S         # noqa: E402
from tools.demand_intake import soak as SO         # noqa: E402
from tools.demand_intake.parser import (           # noqa: E402
    backlog_items_from_report, parse_mission_row,
)


def _transport(args: argparse.Namespace) -> S.Transport:
    return S.PsqlTransport(db=getattr(args, "db", "") or "",
                           statement_timeout_ms=getattr(args, "timeout_ms", 30000))


# ── 子命令 ──────────────────────────────────────────────────────────────────
def cmd_init(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    S.ensure_schema(t)
    S.chronicle(t, "init", {"transport": t.name, "schema": args.schema or ""})
    return {"ok": True, "transport": t.name, "tables": ["demand_intake_log", "demand_backlog"]}


def cmd_ingest_missions(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    S.ensure_schema(t)
    rows = S.fetch_missions(t, since_id=args.since_id, mission_id=args.mission_id,
                            limit=args.limit)
    tot = S.IngestResult()
    per_mission: List[Dict[str, Any]] = []
    for r in rows:
        cands = parse_mission_row(r)
        if not cands:
            continue
        res = S.ingest_candidates(t, cands, mission_id=r.get("id"),
                                  dry_run=args.dry_run)
        tot.inserted += res.inserted
        tot.duplicate += res.duplicate
        tot.rejected += res.rejected
        per_mission.append({"mission_id": r.get("id"), "candidates": len(cands),
                            "inserted": res.inserted, "duplicate": res.duplicate,
                            "rejected": res.rejected})
    out = {"missions_scanned": len(rows), "inserted": tot.inserted,
           "duplicate": tot.duplicate, "rejected": tot.rejected,
           "missions_with_candidates": len(per_mission), "dry_run": bool(args.dry_run),
           "sample": per_mission[:5]}
    if not args.dry_run:
        S.chronicle(t, "ingest_missions", out)
    return out


def cmd_gate(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    S.ensure_schema(t)
    res = S.apply_gate(t, dry_run=not args.apply)
    if args.apply:
        S.chronicle(t, "gate", {k: v for k, v in res.items() if k != "decisions"})
    return res


def cmd_audit(args: argparse.Namespace) -> Dict[str, Any]:
    """事后抽样审计（缺省 dry-run；``--apply`` 才回写 dropped/revoked）。"""
    t = _transport(args)
    S.ensure_audit_schema(t, args.schema or "")
    return A.run_audit(t, daily_cap=args.cap,
                       limit=(args.limit or None), dry_run=not args.apply,
                       schema=args.schema or "")


def cmd_ingest_reports(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    S.ensure_schema(t)
    tbl = S.q("tensor_reports", args.schema or "")
    where = [f"id > {int(args.since_id)}"] if args.since_id else []
    if args.mission_id:
        where.append(f"mission_id = {int(args.mission_id)}")
    sql = (f"SELECT id, title, markdown FROM {tbl} "
           f"{'WHERE ' + ' AND '.join(where) if where else ''} "
           f"ORDER BY id LIMIT {int(args.limit)}")
    reports = t.query(sql)
    items: List[Dict[str, Any]] = []
    for rep in reports:
        items.extend(backlog_items_from_report(rep))
    written = S.ingest_backlog_items(t, items, dry_run=args.dry_run, schema=args.schema or "")
    out = {"reports_scanned": len(reports), "backlog_items": len(items),
           "backlog_written": written, "dry_run": bool(args.dry_run),
           "sample": items[:5]}
    if not args.dry_run:
        S.chronicle(t, "ingest_reports", {k: v for k, v in out.items() if k != "sample"})
    return out


def cmd_measure(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    S.ensure_schema(t)
    return S.measure(t, args.schema or "")


def cmd_status(args: argparse.Namespace) -> Dict[str, Any]:
    m = cmd_measure(args)
    return {"detected": m["detected"], "landed": m["landed"],
            "intake_rate": m["intake_rate"], "claimable_rate": m["claimable_rate"],
            "by_status": m["by_status"]}


# ── 红队（临时 schema 内全链路，真 PG） ────────────────────────────────────
SCRATCH_DDL = """CREATE TABLE {s}.feature_ledger (
  id serial PRIMARY KEY, slug text NOT NULL UNIQUE, title text NOT NULL,
  domain text NOT NULL, source text NOT NULL, acceptance text NOT NULL,
  ledger_group text, status text NOT NULL DEFAULT 'planned', evidence text,
  mission_id bigint, created_at timestamptz DEFAULT now(), done_at timestamptz);
CREATE TABLE {s}.tensor_dev_missions (
  id bigserial PRIMARY KEY, goal text NOT NULL, status text NOT NULL DEFAULT 'succeeded',
  result jsonb NOT NULL DEFAULT '{{}}'::jsonb);
CREATE TABLE {s}.tensor_reports (
  id serial PRIMARY KEY, title text NOT NULL, markdown text NOT NULL,
  mission_id integer);
CREATE TABLE {s}.chronicle_snapshots (ts timestamptz DEFAULT now(), payload jsonb)"""


def _payload_structured(mission_id: int) -> Dict[str, Any]:
    """合成夹具（平台中性：无任何领域词条）。"""
    return {
        "id": mission_id,
        "goal": "合成任务：验证接驳链路",
        "status": "succeeded",
        "result": {
            "headline": "合成任务完成；建议新锚：FEAT-SYNTH-ALPHA — 合成锚甲：验收判据 pytest 全绿且偏差 <3%",
            "checks": "pytest 7 passed；偏差 1.2% <3%",
            "proposed_anchors": [
                {"slug": "FEAT-SYNTH-BETA", "title": "合成锚乙",
                 "acceptance": "覆盖 5 类用例且误差 <2%", "domain": "platform"},
            ],
        },
    }


JUNK_PAYLOADS = [
    {"id": 9002, "goal": "合成垃圾锚：坏 slug", "status": "succeeded",
     "result": {"proposed_anchors": [
         {"slug": "A1-A2", "title": "合成步骤编号冒充锚", "acceptance": "误差 <3%"}]}},
    {"id": 9003, "goal": "合成垃圾锚：空验收", "status": "succeeded",
     "result": {"proposed_anchors": [
         {"slug": "FEAT-SYNTH-NOACC", "title": "合成无验收锚", "acceptance": ""}]}},
    {"id": 9004, "goal": "合成垃圾锚：形容词验收", "status": "succeeded",
     "result": {"proposed_anchors": [
         {"slug": "FEAT-SYNTH-VAGUE", "title": "合成模糊验收锚",
          "acceptance": "做得更好更稳定就行"}]}},
    {"id": 9005, "goal": "合成垃圾锚：重复", "status": "succeeded",
     "result": {"proposed_anchors": [
         {"slug": "FEAT-SYNTH-ALPHA", "title": "合成锚甲（重复提出）",
          "acceptance": "偏差 <3%"}]}},
]


def cmd_redteam(args: argparse.Namespace) -> Dict[str, Any]:
    t = _transport(args)
    schema = args.scratch or f"m17133_redteam_{__import__('os').getpid()}"
    S.ensure_schema(t, schema)
    for stmt in SCRATCH_DDL.format(s=schema).split(";\n"):
        if stmt.strip():
            t.execute(stmt.strip().rstrip(";") + ";")
    # 预置一条既有锚：用于「重复检测」红队（既有 open 锚撞标题）
    t.execute(f"INSERT INTO {schema}.feature_ledger (slug, title, domain, source, acceptance, "
              f"status) VALUES ('FEAT-SYNTH-ZETA', '合成锚丙：既有已开锚', 'platform', "
              f"'synthetic', '偏差 <3%', 'open');")
    t.execute(f"INSERT INTO {schema}.tensor_dev_missions (id, goal, status, result) VALUES "
              f"(9001, '合成任务：验证接驳链路', 'succeeded', {S.sql_json(_payload_structured(9001)['result'])});")
    for p in JUNK_PAYLOADS:
        t.execute(f"INSERT INTO {schema}.tensor_dev_missions (id, goal, status, result) VALUES "
                  f"({p['id']}, {S.sql_literal(p['goal'])}, 'succeeded', {S.sql_json(p['result'])});")
    # 标题重复（slug 不同、标题近同）
    t.execute(f"INSERT INTO {schema}.tensor_dev_missions (id, goal, status, result) VALUES "
              f"(9006, '合成垃圾锚：标题重复', 'succeeded', "
              f"{S.sql_json({'proposed_anchors': [{'slug': 'FEAT-SYNTH-ZETA2', 'title': '合成锚丙：既有已开锚', 'acceptance': '偏差 <3%'}]})});")
    syn_md = ("## 五 局限与待办\n- 合成局限一：偏差 >3% 未解释\n"
              "- 合成待办二：补 A/B 对照\n## 六 结论\n无关\n")
    t.execute(f"INSERT INTO {schema}.tensor_reports (id, title, markdown) VALUES "
              f"(9101, {S.sql_literal('合成报告')}, {S.sql_literal(syn_md)});")

    rows = S.fetch_missions(t, since_id=9000, limit=50, schema=schema)
    stats = S.IngestResult()
    for r in rows:
        res = S.ingest_candidates(t, parse_mission_row(r), mission_id=r.get("id"),
                                  schema=schema)
        stats.inserted += res.inserted
        stats.duplicate += res.duplicate
        stats.rejected += res.rejected
    gate_res = S.apply_gate(t, schema=schema)
    rep_rows = t.query(f"SELECT id, title, markdown FROM {schema}.tensor_reports")
    backlog = []
    for rep in rep_rows:
        backlog.extend(backlog_items_from_report(rep))
    backlog_n = S.ingest_backlog_items(t, backlog, schema=schema)
    backlog_n2 = S.ingest_backlog_items(t, backlog, schema=schema)   # 幂等复跑

    anchor_rows = t.query(f"SELECT slug, status, source FROM {schema}.feature_ledger ORDER BY slug")
    open_slugs = sorted(r["slug"] for r in anchor_rows if r["status"] == "open")
    log_rows = t.query(f"SELECT slug, status, reason FROM {schema}.demand_intake_log ORDER BY slug")

    # 断链红队：两条合格锚必须可领（status=open 且 source=agent-proposed）
    linked = [r for r in anchor_rows
              if r["slug"] in ("FEAT-SYNTH-ALPHA", "FEAT-SYNTH-BETA")
              and r["status"] == "open" and r["source"] == "agent-proposed"]
    junk_open = [r["slug"] for r in anchor_rows if r["status"] == "open"
                 and r["source"] == "agent-proposed"
                 and r["slug"] not in ("FEAT-SYNTH-ALPHA", "FEAT-SYNTH-BETA")]
    junk_dropped = {r["slug"]: r["reason"] for r in log_rows if r["status"] == "dropped"}

    checks = {
        "broken_link_no_anchor_left_behind": {"pass": len(linked) == 2,
                                              "evidence": [r["slug"] for r in linked]},
        "junk_intercepted": {"pass": not junk_open,
                             "open_junk": junk_open,
                             "dropped": junk_dropped},
        "duplicate_skipped": {"pass": stats.duplicate >= 1,
                              "duplicate": stats.duplicate},
        "backlog_idempotent": {"pass": backlog_n == len(backlog) and backlog_n2 == 0,
                               "first": backlog_n, "second": backlog_n2},
        "reingest_idempotent": None,     # 下面复跑一次全链
    }
    res2 = S.IngestResult()
    for r in rows:
        rr = S.ingest_candidates(t, parse_mission_row(r), mission_id=r.get("id"),
                                 schema=schema)
        res2.inserted += rr.inserted
        res2.duplicate += rr.duplicate
    checks["reingest_idempotent"] = {"pass": res2.inserted == 0,
                                     "second_pass_inserted": res2.inserted}
    verdict = all(v["pass"] for v in checks.values() if isinstance(v, dict))
    out = {"schema": schema, "missions": len(rows), "inserted": stats.inserted,
           "duplicate": stats.duplicate, "gate": gate_res,
           "open_slugs": open_slugs, "checks": checks, "verdict": "PASS" if verdict else "FAIL",
           "measured": S.measure(t, schema)}
    t.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out



def cmd_soak(args: argparse.Namespace) -> Dict[str, Any]:
    """七日浸泡审计：覆盖率与帽拒率周报。"""
    t = _transport(args)
    S.ensure_audit_schema(t, args.schema or "")
    report = SO.run_soak_audit(t, days=args.days,
                               apply_cap_flag=args.apply_cap,
                               schema=args.schema or "")
    out = SO.report_to_dict(report)
    if not args.schema:  # 生产环境才写 chronicle
        S.chronicle(t, "soak_weekly", out)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="需求自动接驳（demand_intake）")
    ap.add_argument("--db", default="", help="库名（缺省 TP_PG_DB 或 tensorplatform）")
    ap.add_argument("--schema", default="", help="表所在 schema（红队/测试用）")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    m = sub.add_parser("ingest-missions")
    m.add_argument("--since-id", type=int, default=0)
    m.add_argument("--mission-id", type=int, default=0)
    m.add_argument("--limit", type=int, default=200)
    m.add_argument("--dry-run", action="store_true")
    g = sub.add_parser("gate")
    g.add_argument("--apply", action="store_true", help="不带此参数为 dry-run（只读）")
    a = sub.add_parser("audit")
    a.add_argument("--apply", action="store_true", help="不带此参数为 dry-run（只读）")
    a.add_argument("--cap", type=int, default=S.AUDIT_DAILY_CAP,
                   help="日帽（今日已审数会先扣掉）")
    a.add_argument("--limit", type=int, default=0, help="本次最多审几条（0=吃满预算）")
    r = sub.add_parser("ingest-reports")
    r.add_argument("--since-id", type=int, default=0)
    r.add_argument("--mission-id", type=int, default=0)
    r.add_argument("--limit", type=int, default=100)
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("soak")
    s.add_argument("--days", type=int, default=SO.SOAK_WINDOW_DAYS,
                   help="浸泡窗口天数（默认 7）")
    s.add_argument("--apply-cap", action="store_true",
                   help="自动把推荐审计帽写回 demand_mint_quota")
    sub.add_parser("measure")
    sub.add_parser("status")
    rt = sub.add_parser("redteam")
    rt.add_argument("--scratch", default="")
    rt.add_argument("--out", default="")
    return ap


HANDLERS = {
    "init": cmd_init, "ingest-missions": cmd_ingest_missions, "gate": cmd_gate,
    "audit": cmd_audit, "soak": cmd_soak,
    "ingest-reports": cmd_ingest_reports, "measure": cmd_measure,
    "status": cmd_status, "redteam": cmd_redteam,
}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "mission_id", 0) == 0 and hasattr(args, "mission_id"):
        args.mission_id = None
    out = HANDLERS[args.cmd](args)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("verdict", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())