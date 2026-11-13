"""redteam —— 三处真空档的可失败红队（v2 验收证据来源）。

每条场景都是**期望失败**的构造：如果机门是「先查后写」的假门，这些场景会当场红。

1. ``concurrent_same_slug``：N 线程同时铸造同一 slug → 必须只成 1（锚 1 行、单子 1 张）。
2. ``cap_exceeded``：同 agent 连铸 cap+1 条 → 第 cap+1 条必须 ``cap_exceeded``，
   且 ledger 只多 cap 行（超发=红）。
3. ``strike_downgrade``：strike ≥ 3 → 帽降为 1/日（第 2 条被拒）+ ``worker_credit_tiers``
   信用档按政策下压（联动不落库=红）。
4. ``audit_fail_revokes``：铸造期放行的垃圾锚（验收无量化）→ 事后审计必须 dropped、
   关联未终态单 revoked、chronicle 留 audit_fail、strike +1。
5. ``audit_daily_cap``：25 条待审锚 → 单日只审剩下的帽内条数，其余 ``uncovered``（帽失效=红）。
   注：审计帽按**日**共享，场景之间不复位（复位等于假装每天重启，掩盖帽的真实语义），
   所以本场景按「当日已用审计数」现场推算期望值。

用法：``python -m tools.demand_intake.cli redteam``（自建一次性 schema，用完即弃）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from . import audit, mint
from .dispatch import RecordingDispatcher
from .store import (DEFAULT_DAILY_CAP, STRIKE_THRESHOLD, Transport, create_schema,
                    ensure_schema, ensure_support_schema, q, sql_literal)


class SchemaRecordingDispatcher(RecordingDispatcher):
    """红队用建单桩：在一次性 schema 的 ``tensor_dev_missions`` 里**真建单行**。

    审计回撤（revoke）要验的是「关联未终态单被置 revoked」——桩只回 id 不落行的话，
    revoke 永远 0 行，红队就成了自证清白的假绿。故这里落真行。
    """

    def __init__(self, t: Transport, schema: str, status: str = "queued") -> None:
        super().__init__()
        self.t = t
        self.schema = schema
        self.status = status

    def create_mission(self, payload: Dict[str, Any]) -> int:
        self.calls.append(payload)
        out = self.t.execute(
            f"INSERT INTO {q('tensor_dev_missions', self.schema)} "
            f"(goal, status, origin, task_key) VALUES "
            f"({sql_literal(str(payload.get('goal', ''))[:2000])}, "
            f"{sql_literal(self.status)}, 'agent', "
            f"{sql_literal(str(payload.get('task_key', ''))[:120])}) RETURNING id;")
        ids = [ln for ln in (x.strip() for x in out.splitlines()) if ln.isdigit()]
        return int(ids[0])


def _int(t: Transport, sql: str) -> int:
    rows = t.query(sql)
    return int(rows[0]["n"]) if rows else 0


def _cand(slug: str, *, title: str = "", acceptance: str = "误差 <3%（实测 1.2%）",
          repo: str = "TensorPlatform") -> Dict[str, Any]:
    return {"slug": slug, "title": title or f"synthetic anchor {slug}",
            "acceptance": acceptance, "domain": "platform", "repo": repo,
            "source_kind": "redteam", "source_ref": slug}


def _ledger_status(t: Transport, slug: str, schema: str) -> str:
    rows = t.query(f"SELECT status FROM {q('feature_ledger', schema)} "
                   f"WHERE slug = {sql_literal(slug)}")
    return str(rows[0]["status"]) if rows else ""


def scenario_concurrent_same_slug(t: Transport, schema: str, *, threads: int = 4) -> Dict[str, Any]:
    slug = "FEAT-SYNTH-RACE-01"
    disp = SchemaRecordingDispatcher(t, schema)
    def worker(_i: int):
        return mint.mint(t, _cand(slug), mint_kind="agent", minted_by="rt-race",
                         dispatcher=disp, schema=schema)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        outcomes = list(pool.map(worker, range(threads)))
    ok_n = sum(1 for o in outcomes if o.status == mint.MINTED)
    ledger_n = _int(t, f"SELECT count(*) AS n FROM {q('feature_ledger', schema)} "
                       f"WHERE slug = {sql_literal(slug)}")
    mission_rows = _int(t, f"SELECT count(*) AS n FROM {q('tensor_dev_missions', schema)} "
                           f"WHERE task_key = {sql_literal(slug)}")
    duplicates = sum(1 for o in outcomes if o.status == mint.DUPLICATE)
    ok = (ok_n == 1 and ledger_n == 1 and len(disp.calls) == 1
          and mission_rows == 1 and duplicates == threads - 1)
    return {"scenario": "concurrent_same_slug", "ok": ok, "threads": threads,
            "minted": ok_n, "ledger_rows": ledger_n, "missions_created": len(disp.calls),
            "mission_rows": mission_rows, "duplicates": duplicates,
            "detail": [o.status for o in outcomes]}


def scenario_cap_exceeded(t: Transport, schema: str, *, cap: int = DEFAULT_DAILY_CAP) -> Dict[str, Any]:
    agent = "rt-cap"
    disp = SchemaRecordingDispatcher(t, schema)
    outcomes = [mint.mint(t, _cand(f"FEAT-SYNTH-CAP-{i:02d}"), mint_kind="agent",
                          minted_by=agent, dispatcher=disp, schema=schema)
                for i in range(cap + 1)]
    minted_n = sum(1 for o in outcomes if o.status == mint.MINTED)
    rejected_n = sum(1 for o in outcomes if o.status == mint.CAP_EXCEEDED)
    ledger_n = _int(t, f"SELECT count(*) AS n FROM {q('feature_ledger', schema)} "
                       f"WHERE slug LIKE 'FEAT-SYNTH-CAP-%'")
    quota = t.query(f"SELECT minted, cap FROM {q('demand_mint_quota', schema)} "
                    f"WHERE agent = {sql_literal(agent)} AND day = current_date")
    ok = (minted_n == cap and rejected_n == 1 and ledger_n == cap
          and len(disp.calls) == cap and quota and int(quota[0]["minted"]) == cap)
    return {"scenario": "cap_exceeded", "ok": ok, "cap": cap, "minted": minted_n,
            "cap_exceeded": rejected_n, "ledger_rows": ledger_n,
            "missions_created": len(disp.calls),
            "quota": quota[0] if quota else None}


def scenario_strike_downgrade(t: Transport, schema: str, *,
                              threshold: int = STRIKE_THRESHOLD) -> Dict[str, Any]:
    """降级要连中两处：帽变 1/日（且对当日已铸造者立即生效）+ credit_tiers 联动落库。"""
    disp = SchemaRecordingDispatcher(t, schema)
    # A：先正常铸 1 条（cap=10），再吃满 strike → 帽变 1，当日已用 1 → 立刻再铸必被拒
    agent_a = "rt-strike-a"
    first = mint.mint(t, _cand("FEAT-SYNTH-STRIKE-A1"), mint_kind="agent",
                      minted_by=agent_a, dispatcher=disp, schema=schema)
    for _ in range(threshold):
        mint.apply_strike(t, agent_a, note="redteam: synthetic audit fail", schema=schema)
    a_after = mint.mint(t, _cand("FEAT-SYNTH-STRIKE-A2"), mint_kind="agent",
                        minted_by=agent_a, dispatcher=disp, schema=schema)
    # B：干净 agent 预置 strike=3 → 当日帽恰好 1 条
    agent_b = "rt-strike-b"
    for _ in range(threshold):
        mint.apply_strike(t, agent_b, note="redteam: pre-set strikes", schema=schema)
    b1 = mint.mint(t, _cand("FEAT-SYNTH-STRIKE-B1"), mint_kind="agent",
                   minted_by=agent_b, dispatcher=disp, schema=schema)
    b2 = mint.mint(t, _cand("FEAT-SYNTH-STRIKE-B2"), mint_kind="agent",
                   minted_by=agent_b, dispatcher=disp, schema=schema)
    tier = t.query(f"SELECT worker_id, credit_level, rationale FROM "
                   f"{q('worker_credit_tiers', schema)} "
                   f"WHERE worker_id IN ({sql_literal(agent_a)}, {sql_literal(agent_b)}) "
                   f"ORDER BY worker_id")
    levels = {r["worker_id"]: int(r["credit_level"]) for r in tier}
    ok = (first.cap == DEFAULT_DAILY_CAP and first.status == mint.MINTED
          and a_after.cap == 1 and a_after.status == mint.CAP_EXCEEDED
          and b1.cap == 1 and b1.status == mint.MINTED
          and b2.status == mint.CAP_EXCEEDED
          and levels.get(agent_a, 9) <= 2 and levels.get(agent_b, 9) <= 2)
    return {"scenario": "strike_downgrade", "ok": ok,
            "baseline_cap": first.cap, "first_status": first.status,
            "cap_after_strikes": a_after.cap, "already_used_today": a_after.status,
            "fresh_agent_first": b1.status, "fresh_agent_second": b2.status,
            "credit_levels": levels,
            "rationale": {r["worker_id"]: r["rationale"] for r in tier}}


def scenario_audit_fail_revokes(t: Transport, schema: str) -> Dict[str, Any]:
    agent = "rt-audit"
    disp = SchemaRecordingDispatcher(t, schema)
    bad = _cand("FEAT-SYNTH-AUDIT-BAD", title="synthetic vague anchor",
                acceptance="做得更好一些即可")
    out = mint.mint(t, bad, mint_kind="agent", minted_by=agent, dispatcher=disp,
                    schema=schema)
    if out.status != mint.MINTED:
        return {"scenario": "audit_fail_revokes", "ok": False,
                "error": f"铸造期拦住了本该事后才判的锚: {out.status}"}
    summary = audit.run_audit(t, daily_cap=audit.AUDIT_DAILY_CAP,
                              slugs=[bad["slug"]], schema=schema)
    status = _ledger_status(t, bad["slug"], schema)
    missions = t.query(f"SELECT status FROM {q('tensor_dev_missions', schema)} "
                       f"WHERE id = {int(out.mission_id)}")
    mission_status = str(missions[0]["status"]) if missions else "missing"
    chron = t.query(f"SELECT payload->>'phase' AS phase FROM {q('chronicle_snapshots', schema)} "
                    f"WHERE payload->>'slug' = {sql_literal(bad['slug'])}")
    phases = [r["phase"] for r in chron]
    strikes = _int(t, f"SELECT coalesce(max(strikes),0) AS n FROM "
                      f"{q('tensor_agent_integrity', schema)} "
                      f"WHERE model = {sql_literal(agent)}")
    ok = (status == "dropped" and mission_status == "revoked"
          and "audit_fail" in phases and strikes == 1
          and bad["slug"] in [d["slug"] for d in summary["decisions"] if d["verdict"] == "fail"])
    return {"scenario": "audit_fail_revokes", "ok": ok, "ledger_status": status,
            "mission_status": mission_status, "chronicle_phases": phases,
            "strikes": strikes, "audit_summary": {k: summary[k] for k in
                                                 ("sampled", "passed", "failed", "uncovered")}}


def scenario_audit_daily_cap(t: Transport, schema: str, *, total: int = 25) -> Dict[str, Any]:
    from .store import count_audits_today
    disp = RecordingDispatcher()
    for i in range(total):
        mint.mint(t, _cand(f"FEAT-SYNTH-AUDITCAP-{i:02d}"), mint_kind="agent",
                  minted_by=f"rt-auditcap-{i:02d}", dispatcher=disp, schema=schema)
    used_prior = count_audits_today(t, schema)
    expected = max(0, audit.AUDIT_DAILY_CAP - used_prior)
    summary = audit.run_audit(t, daily_cap=audit.AUDIT_DAILY_CAP, schema=schema)
    sampled = summary["sampled"]
    second = audit.run_audit(t, daily_cap=audit.AUDIT_DAILY_CAP, schema=schema)
    ok = (used_prior + sampled == min(audit.AUDIT_DAILY_CAP, used_prior + total)
          and sampled <= expected and sampled > 0
          and second["sampled"] == 0 and second["budget"] == 0
          and summary["uncovered"] + sampled >= total)
    return {"scenario": "audit_daily_cap", "ok": ok, "used_prior": used_prior,
            "sampled": sampled, "expected_budget": expected,
            "uncovered": summary["uncovered"], "second_run_sampled": second["sampled"],
            "second_run_budget": second["budget"]}


SCENARIOS = (scenario_concurrent_same_slug, scenario_cap_exceeded,
             scenario_strike_downgrade, scenario_audit_fail_revokes,
             scenario_audit_daily_cap)


def run_all(t: Transport, schema: str, *, bootstrap: bool = False) -> Dict[str, Any]:
    """在给定 schema 上跑全部场景；``bootstrap`` 时先建表（一次性 schema 用）。"""
    if bootstrap:
        create_schema(t, schema)
        ensure_schema(t, schema)
        ensure_support_schema(t, schema)
    results: List[Dict[str, Any]] = [fn(t, schema) for fn in SCENARIOS]
    return {"schema": schema, "all_ok": all(r["ok"] for r in results),
            "results": results}