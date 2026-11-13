"""mint —— 铸造机门（v2 改造③）：帽 + strike 降级 + credit_tiers 联动。

门只有两条硬规矩，都必须是**数据库原子裁决**（并发安全，见 store 模块抬头）：

1. **每日铸造帽**：每 agent 10 条/日；``tensor_agent_integrity.strikes ≥ 3`` 的 agent
   降级为 1 条/日。计数落在 ``demand_mint_quota(day, agent)``，自增与上限判定在
   ``INSERT ... ON CONFLICT DO UPDATE ... WHERE minted < cap RETURNING`` 的同一条语句里
   ——「先查后写」在并发下会给同一 agent 超发。
2. **slug 唯一**：``feature_ledger.slug`` 唯一约束 + ``ON CONFLICT DO NOTHING RETURNING id``
   ——并发同 slug 只成 1（红队 ``concurrent_same_slug`` 的靶子）。

帽降级与信用档联动：strike 越线时把 ``worker_credit_tiers`` 的信用档按 strike 单调
下压（``credit_level_for_strikes``：0-2→3 / 3-4→2 / ≥5→1，取 ``LEAST(现有, 政策)``），
派发准入（103 迁移的 dispatch admission）据此看到同一个 agent 的同一事实——不然
「帽降了但信用档没降」会让两处账不一致。口径幂等：重复调用不会连续掉档。

诚实边界：帽按 **UTC 日界**（``current_date``）计；strike 来源是
``tensor_agent_integrity``（095 迁移，按 ``(organization_id, model)`` 唯一），审计不过
会 +1，repro 判 cheat 也会 +1——两类 strike 共用同一配额，这是有意的（都对「建议可信度」记账）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import chronicle, judge
from .dispatch import DispatchError, Dispatcher, mission_payload
from .store import (DEFAULT_DAILY_CAP, DEFAULT_ORG_ID, DOWNGRADED_DAILY_CAP,
                    LEDGER_GROUP, LEDGER_SOURCE, STRIKE_THRESHOLD, StoreError,
                    Transport, cand_key, q, sql_json, sql_literal)

MINTED, CAP_EXCEEDED, DUPLICATE, REJECTED, DISPATCH_FAILED = (
    "minted", "cap_exceeded", "duplicate", "rejected", "dispatch_failed")


def cap_for_strikes(strikes: int, *, base: int = DEFAULT_DAILY_CAP,
                    downgraded: int = DOWNGRADED_DAILY_CAP,
                    threshold: int = STRIKE_THRESHOLD) -> int:
    """帽政策（纯函数）：``strikes >= threshold`` → 降级帽。"""
    return downgraded if int(strikes or 0) >= int(threshold) else base


def credit_level_for_strikes(strikes: int) -> int:
    """信用档政策（纯函数，单调）。0-2 strike → 3（常规准入），3-4 → 2，≥5 → 1。"""
    s = int(strikes or 0)
    if s >= 5:
        return 1
    if s >= STRIKE_THRESHOLD:
        return 2
    return 3


@dataclass
class MintOutcome:
    status: str
    slug: str
    reason: str = ""
    ledger_id: Optional[int] = None
    mission_id: Optional[int] = None
    cap: int = 0
    minted_today: int = 0
    strikes: int = 0
    chronicle_error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "slug": self.slug, "reason": self.reason,
                "ledger_id": self.ledger_id, "mission_id": self.mission_id,
                "cap": self.cap, "minted_today": self.minted_today,
                "strikes": self.strikes, **self.extra}


def _log_attempt(t: Transport, row: Dict[str, Any], *, schema: str = "") -> None:
    """写铸造轨迹（``cand_key`` 幂等：同 (kind, ref, slug) 重放只更新最后状态）。"""
    t_log = q("demand_intake_log", schema)
    key = cand_key(row.get("source_kind", "finish"), row.get("source_ref", ""),
                   row.get("slug", ""))
    t.execute(
        f"INSERT INTO {t_log} (cand_key, source_kind, source_ref, slug, title, acceptance, "
        f"domain, repo, confidence, src_field, status, reason, ledger_id, mission_id, "
        f"mint_kind, minted_by, org_id, cap_applied, raw) VALUES ("
        f"{sql_literal(key)}, {sql_literal(row.get('source_kind', 'finish'))}, "
        f"{sql_literal(row.get('source_ref', ''))}, {sql_literal(row.get('slug', ''))}, "
        f"{sql_literal(str(row.get('title', ''))[:400])}, "
        f"{sql_literal(str(row.get('acceptance', ''))[:4000])}, "
        f"{sql_literal(row.get('domain') or 'platform')}, "
        f"{sql_literal(row.get('repo', ''))}, "
        f"{sql_literal(row.get('confidence', ''))}, "
        f"{sql_literal(row.get('field', ''))}, {sql_literal(row['status'])}, "
        f"{sql_literal(row.get('reason', ''))}, "
        f"{int(row['ledger_id']) if row.get('ledger_id') else 'NULL'}, "
        f"{int(row['mission_id']) if row.get('mission_id') else 'NULL'}, "
        f"{sql_literal(row.get('mint_kind', 'agent'))}, "
        f"{sql_literal(row.get('minted_by', ''))}, {int(row.get('org_id', DEFAULT_ORG_ID))}, "
        f"{int(row.get('cap_applied', 0))}, {sql_json(row.get('raw') or {})}) "
        f"ON CONFLICT (cand_key) DO UPDATE SET status = excluded.status, "
        f"reason = excluded.reason, ledger_id = excluded.ledger_id, "
        f"mission_id = excluded.mission_id, cap_applied = excluded.cap_applied, "
        f"updated_at = now();")


def _atomic_mint_sql(candidate: Dict[str, Any], *, agent: str, org_id: int, cap: int,
                     mint_kind: str, schema: str = "") -> str:
    """单条语句完成「配额自增 + 锚入账」——两者的原子性就是并发红队的靶子。"""
    t_log = q("demand_intake_log", schema)
    t_led = q("feature_ledger", schema)
    t_quota = q("demand_mint_quota", schema)
    slug = str(candidate.get("slug"))
    title = str(candidate.get("title") or slug)[:400]
    acceptance = str(candidate.get("acceptance") or "")[:4000]
    domain = str(candidate.get("domain") or "platform")
    evidence = sql_json({"intake": LEDGER_SOURCE, "mint_kind": mint_kind,
                         "minted_by": agent, "source_kind": candidate.get("source_kind", ""),
                         "source_ref": candidate.get("source_ref", "")})
    return (
        f"WITH quota_ins AS (\n"
        f"  INSERT INTO {t_quota} AS mq (day, agent, minted, cap)\n"
        f"  VALUES (current_date, {sql_literal(agent)}, 1, {int(cap)})\n"
        f"  ON CONFLICT (day, agent) DO UPDATE\n"
        f"    SET minted = mq.minted + 1, cap = {int(cap)}, updated_at = now()\n"
        f"    WHERE mq.minted < {int(cap)}\n"
        f"  RETURNING minted, cap\n"
        f"), led_ins AS (\n"
        f"  INSERT INTO {t_led} (slug, title, domain, source, acceptance, ledger_group,\n"
        f"                       status, evidence)\n"
        f"  SELECT {sql_literal(slug)}, {sql_literal(title)}, {sql_literal(domain)},\n"
        f"         '{LEDGER_SOURCE}', {sql_literal(acceptance or 'TBD')}, '{LEDGER_GROUP}',\n"
        f"         'open', {evidence}\n"
        f"    FROM quota_ins\n"
        f"  ON CONFLICT (slug) DO NOTHING\n"
        f"  RETURNING id\n"
        f")\n"
        f"SELECT coalesce((SELECT minted FROM quota_ins), -1)::text || '|' ||\n"
        f"       coalesce((SELECT id FROM led_ins), 0)::text || '|' ||\n"
        f"       coalesce((SELECT cap FROM quota_ins), 0)::text;"
    )


def _revert_quota(t: Transport, agent: str, *, schema: str = "") -> None:
    """未入账（slug 重复）时退回这次配额消耗，避免「重复请求吃掉帽」。"""
    t.execute(
        f"UPDATE {q('demand_mint_quota', schema)} SET minted = minted - 1, updated_at = now() "
        f"WHERE day = current_date AND agent = {sql_literal(agent)} AND minted > 0;")


def mint(t: Transport, candidate: Dict[str, Any], *,
         mint_kind: str = "agent", minted_by: str = "", org_id: int = DEFAULT_ORG_ID,
         dispatcher: Optional[Dispatcher] = None, daily_cap: Optional[int] = None,
         schema: str = "") -> MintOutcome:
    """铸造一条锚：机门裁决 → 入账 ``open`` → 同步建单 → chronicle。"""
    slug = str(candidate.get("slug") or "")
    agent = minted_by or "unknown"
    row_base = {"source_kind": candidate.get("source_kind", "finish"),
                "source_ref": str(candidate.get("source_ref", "")),
                "slug": slug, "title": candidate.get("title", ""),
                "acceptance": candidate.get("acceptance", ""),
                "domain": candidate.get("domain", "platform"),
                "repo": candidate.get("repo", ""), "confidence": candidate.get("confidence", ""),
                "field": candidate.get("field", ""), "mint_kind": mint_kind,
                "minted_by": agent, "org_id": org_id, "raw": candidate.get("raw") or {}}

    precheck = judge.mint_precheck(candidate)
    if precheck:
        _log_attempt(t, {**row_base, "status": REJECTED, "reason": precheck}, schema=schema)
        out = MintOutcome(status=REJECTED, slug=slug, reason=precheck)
        out.chronicle_error = chronicle.record(
            t, "mint", {"slug": slug, "status": REJECTED, "reason": precheck,
                        "mint_kind": mint_kind, "minted_by": agent}, schema=schema)
        return out

    strikes = 0
    try:
        from .store import fetch_strikes
        strikes = fetch_strikes(t, agent, org_id, schema)
    except StoreError:
        strikes = 0                      # 完整性表缺省时不阻塞铸造（帽按基础档）
    cap = int(daily_cap) if daily_cap is not None else cap_for_strikes(strikes)

    out_raw = t.execute(_atomic_mint_sql(candidate, agent=agent, org_id=org_id, cap=cap,
                                         mint_kind=mint_kind, schema=schema)).strip()
    line = [ln for ln in out_raw.splitlines() if "|" in ln][-1] if "|" in out_raw else ""
    try:
        minted_n, ledger_id, cap_echo = (int(x) for x in line.split("|"))
    except ValueError as exc:
        raise StoreError(f"unexpected mint output {out_raw[:200]!r}") from exc

    if minted_n < 0:
        reason = f"daily_cap_exceeded(cap={cap},strikes={strikes})"
        _log_attempt(t, {**row_base, "status": CAP_EXCEEDED, "reason": reason,
                         "cap_applied": cap}, schema=schema)
        out = MintOutcome(status=CAP_EXCEEDED, slug=slug, reason=reason, cap=cap,
                          strikes=strikes)
        out.chronicle_error = chronicle.record(
            t, "mint_rejected", {"slug": slug, "status": CAP_EXCEEDED, "reason": reason,
                                 "cap": cap, "strikes": strikes, "mint_kind": mint_kind,
                                 "minted_by": agent}, schema=schema)
        return out

    if not ledger_id:
        _revert_quota(t, agent, schema=schema)
        reason = "ledger_conflict(slug_exists)"
        _log_attempt(t, {**row_base, "status": DUPLICATE, "reason": reason,
                         "cap_applied": cap}, schema=schema)
        out = MintOutcome(status=DUPLICATE, slug=slug, reason=reason, cap=cap,
                          minted_today=max(0, minted_n - 1), strikes=strikes)
        out.chronicle_error = chronicle.record(
            t, "mint_rejected", {"slug": slug, "status": DUPLICATE, "reason": reason,
                                 "mint_kind": mint_kind, "minted_by": agent}, schema=schema)
        return out

    # ── 提案即单子：同步建单 ───────────────────────────────────────────────
    mission_id: Optional[int] = None
    status = MINTED
    reason = ""
    if dispatcher is not None:
        try:
            mission_id = int(dispatcher.create_mission(mission_payload(candidate)))
            t.execute(f"UPDATE {q('feature_ledger', schema)} SET mission_id = {mission_id} "
                      f"WHERE id = {int(ledger_id)};")
        except DispatchError as exc:
            status = DISPATCH_FAILED
            reason = f"dispatch_failed:{str(exc)[:160]}"

    _log_attempt(t, {**row_base, "status": status, "reason": reason,
                     "ledger_id": int(ledger_id), "mission_id": mission_id,
                     "cap_applied": cap}, schema=schema)
    out = MintOutcome(status=status, slug=slug, reason=reason, ledger_id=int(ledger_id),
                      mission_id=mission_id, cap=cap or cap_echo, minted_today=minted_n,
                      strikes=strikes)
    out.chronicle_error = chronicle.record(
        t, "mint", {"slug": slug, "status": status, "reason": reason,
                    "ledger_id": int(ledger_id), "mission_id": mission_id,
                    "cap": cap, "minted_today": minted_n, "strikes": strikes,
                    "mint_kind": mint_kind, "minted_by": agent}, schema=schema)
    return out


def mint_many(t: Transport, candidates: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    """批量铸造（同一 agent，独立裁决）。返回状态分布，供 CLI/周报直接消费。"""
    outcomes = [mint(t, c, **kwargs) for c in candidates]
    by: Dict[str, int] = {}
    for o in outcomes:
        by[o.status] = by.get(o.status, 0) + 1
    return {"total": len(outcomes), "by_status": dict(sorted(by.items())),
            "outcomes": [o.as_dict() for o in outcomes]}


# ── strike / 信用档联动（审计不过时调用） ───────────────────────────────────
def apply_strike(t: Transport, agent: str, *, org_id: int = DEFAULT_ORG_ID,
                 note: str = "", schema: str = "") -> Dict[str, Any]:
    """agent strike +1（``tensor_agent_integrity`` upsert）并同步信用档。"""
    tbl = q("tensor_agent_integrity", schema)
    t.execute(
        f"INSERT INTO {tbl} AS ai (organization_id, model, strikes, last_strike_at, notes) "
        f"VALUES ({int(org_id)}, {sql_literal(agent)}, 1, now(), {sql_literal(note[:400])}) "
        f"ON CONFLICT (organization_id, model) DO UPDATE SET "
        f"  strikes = ai.strikes + 1, last_strike_at = now(), updated_at = now(), "
        f"  notes = left(coalesce(ai.notes, '') || {sql_literal(' | ' + note[:200])}, 1000);")
    from .store import fetch_strikes
    strikes = fetch_strikes(t, agent, org_id, schema)
    tier = sync_credit_tier(t, agent, org_id=org_id, strikes=strikes, schema=schema)
    return {"strikes": strikes, "cap": cap_for_strikes(strikes), "credit": tier}


def sync_credit_tier(t: Transport, agent: str, *, org_id: int = DEFAULT_ORG_ID,
                     strikes: int = 0, schema: str = "") -> Dict[str, Any]:
    """把 strike 事实写进 ``worker_credit_tiers``（派发准入口径）。

    政策档 ``credit_level_for_strikes`` 单调不升；upsert 取 ``LEAST(现有, 政策)``，
    所以重复调用不会连续掉档（幂等），也不会把已经更低的档位「修回来」。
    """
    policy = credit_level_for_strikes(strikes)
    rationale = ("strikes={} mint_cap={} policy=demand-intake-v2"
                 .format(strikes, cap_for_strikes(strikes)))
    tbl = q("worker_credit_tiers", schema)
    try:
        t.execute(
            f"INSERT INTO {tbl} AS wct (organization_id, worker_id, credit_level, rationale) "
            f"VALUES ({int(org_id)}, {sql_literal(agent)}, {int(policy)}, "
            f"{sql_literal(rationale)}) "
            f"ON CONFLICT ON CONSTRAINT uq_worker_credit_tiers_worker DO UPDATE SET "
            f"  credit_level = LEAST(wct.credit_level, {int(policy)}), "
            f"  rationale = excluded.rationale, updated_at = now();")
    except StoreError as exc:
        return {"applied": False, "error": str(exc)[:200], "policy_level": policy}
    rows = t.query(f"SELECT credit_level, rationale FROM {tbl} "
                   f"WHERE organization_id = {int(org_id)} "
                   f"AND worker_id = {sql_literal(agent)}")
    return {"applied": True, "policy_level": policy,
            "credit_level": int(rows[0]["credit_level"]) if rows else None,
            "rationale": rows[0]["rationale"] if rows else ""}