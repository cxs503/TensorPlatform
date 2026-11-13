#!/usr/bin/env python3
"""证据生成器（demand_intake 事后审计，m17298）—— 报告里的数字全部由本脚本产出，零手抄。

用法::

    python3 docs/demand_intake/m17298/make_evidence.py            # 打 JSON 到 stdout
    python3 docs/demand_intake/m17298/make_evidence.py --out x.json

做法：在**临时 schema**（``m17298_evidence_scratch``）里建同构表，种 8 条「已上架待审」
合成锚（5 条验收合规 / 3 条验收过薄）与 9 张单子（8 未终态 + 1 已 succeeded），然后依次跑
dry-run → 日帽 4 的两轮 → 放开帽的收尾轮，最后统计账本/单子/留痕的终态并 ``DROP SCHEMA``。
生产库一行不写；跑完不留残留（脚本末尾 DROP，可用 \\dt 自查）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demand_intake import audit as A, store as S  # noqa: E402

SCHEMA = "m17298_evidence_scratch"
GOOD = "pytest 全绿且覆盖率 >90%"
THIN = "偏差 <3%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    t = S.PsqlTransport(db="tensorplatform")
    t.execute(f"""DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.feature_ledger (
  id serial PRIMARY KEY, slug text NOT NULL UNIQUE, title text NOT NULL,
  domain text NOT NULL, source text NOT NULL, acceptance text NOT NULL,
  ledger_group text, status text NOT NULL DEFAULT 'planned', evidence text,
  mission_id bigint, created_at timestamptz DEFAULT now(), done_at timestamptz);
CREATE TABLE {SCHEMA}.tensor_dev_missions (
  id bigserial PRIMARY KEY, goal text NOT NULL, status text NOT NULL DEFAULT 'succeeded',
  result jsonb NOT NULL DEFAULT '{{}}'::jsonb, finished_at timestamptz);
CREATE TABLE {SCHEMA}.chronicle_snapshots (ts timestamptz DEFAULT now(), payload jsonb);""")
    S.ensure_audit_schema(t, SCHEMA)

    def seed_mission(mid: int, status: str) -> None:
        t.execute(f"INSERT INTO {S.q('tensor_dev_missions', SCHEMA)} (id, goal, status) "
                  f"VALUES ({mid}, 'synthetic', {S.sql_literal(status)});")

    def seed(slug: str, acceptance: str, mid: int) -> None:
        t.execute(f"INSERT INTO {S.q('feature_ledger', SCHEMA)} "
                  f"(slug,title,domain,source,acceptance,status,mission_id) VALUES "
                  f"({S.sql_literal(slug)}, {S.sql_literal('合成锚 ' + slug)}, 'platform', "
                  f"'agent-proposed', {S.sql_literal(acceptance)}, 'open', {mid});")
        t.execute(f"INSERT INTO {S.q('demand_intake_log', SCHEMA)} "
                  f"(cand_key,source_kind,source_ref,slug,title,acceptance,status,mission_id) "
                  f"VALUES ({S.sql_literal(S.cand_key('mission_finish', slug, slug))}, "
                  f"'mission_finish', {S.sql_literal(slug)}, {S.sql_literal(slug)}, "
                  f"{S.sql_literal('合成锚 ' + slug)}, {S.sql_literal(acceptance)}, "
                  f"'open', {mid});")

    for i in range(5):
        seed_mission(9700 + i, "queued")
        seed(f"FEAT-SYNTH-OK{i}", GOOD, 9700 + i)
    for i in range(3):
        seed_mission(9800 + i, "running")
        seed(f"FEAT-SYNTH-THIN{i}", THIN, 9800 + i)
    seed_mission(9901, "succeeded")
    t.execute(f"UPDATE {S.q('feature_ledger', SCHEMA)} SET mission_id = 9901 "
              f"WHERE slug = 'FEAT-SYNTH-OK0';")

    out = {"fixture": {"claimable_anchors": 8, "compliant": 5, "thin_acceptance": 3,
                       "nonterminal_missions": 8, "terminal_missions": 1,
                       "acceptance_chars_thin": len(THIN),
                       "acceptance_chars_good": len(GOOD)},
           "thresholds": {"gate_min_chars": 4,
                          "audit_min_chars": S.AUDIT_MIN_ACCEPTANCE_CHARS,
                          "audit_daily_cap": S.AUDIT_DAILY_CAP}}

    dry = A.run_audit(t, dry_run=True, schema=SCHEMA)
    out["dry_run"] = {k: dry[k] for k in ("sampled", "passed", "failed", "dropped",
                                          "revoked_missions", "uncovered", "reasons")}
    for name, cap in (("capped_pass1", 4), ("capped_pass2", 4), ("final_sweep", 100)):
        r = A.run_audit(t, daily_cap=cap, schema=SCHEMA)
        out[name] = {k: r[k] for k in ("daily_cap", "audits_used_today", "budget",
                                       "pending_total", "sampled", "passed", "failed",
                                       "dropped", "revoked_missions", "uncovered",
                                       "reasons")}

    def n(sql: str) -> int:
        return int(t.query(sql)[0]["n"])

    led = S.q("feature_ledger", SCHEMA)
    mis = S.q("tensor_dev_missions", SCHEMA)
    out["after"] = {
        "ledger_open": n(f"SELECT count(*) n FROM {led} WHERE status = 'open'"),
        "ledger_dropped": n(f"SELECT count(*) n FROM {led} WHERE status = 'dropped'"),
        "thin_still_open": n(f"SELECT count(*) n FROM {led} "
                             f"WHERE status = 'open' AND slug LIKE 'FEAT-SYNTH-THIN%'"),
        "missions_revoked": n(f"SELECT count(*) n FROM {mis} WHERE status = 'revoked'"),
        "missions_succeeded": n(f"SELECT count(*) n FROM {mis} WHERE status = 'succeeded'"),
        "chronicle_events": n(f"SELECT count(*) n FROM {S.q('chronicle_snapshots', SCHEMA)}"),
        "chronicle_phases": sorted({r["phase"] for r in t.query(
            f"SELECT payload->>'phase' phase FROM {S.q('chronicle_snapshots', SCHEMA)}")}),
    }
    t.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")
    out["scratch_schema_dropped"] = not t.query(
        "SELECT schema_name FROM information_schema.schemata "
        f"WHERE schema_name = '{SCHEMA}'")

    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())