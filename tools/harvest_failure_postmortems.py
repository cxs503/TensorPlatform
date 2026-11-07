#!/usr/bin/env python3
"""failure_postmortems 的存储层与收割运行器（DB 适配 + 两闸定稿 + 引用登记）.

职责
----
1. **建表**：``Repo.ensure_schema`` 按方言执行 ``tools.failure_postmortems`` 的
   DDL（SQLite 走 JSON 文本列；PostgreSQL 走真 ``text[]`` 列）。
2. **收割**：读 ``tensor_dev_missions`` 的 failed 单 → 机械派生草稿
   （``draft_from_mission``）→ 结构挑战（``challenge_draft``）→ 独立复算
   （``independent_review``）→ 由**独立挑战主体**定稿（``confirm_draft``）。
3. **引用**：后收割的单在起草前先 ``retrieve_rules`` 检索已定稿规则，命中即
   登记 ``later_postmortem_consult``；并扫描后续 mission 文本里的 rule_id
   显式引用（``mission_text_citation``）与 claim 期注入（``claim_injection``）。
4. **统计**：``summarize`` 的验收口径落 JSON，供报告程序化引用。

纪律：收割者（harvested_by）与定稿者（confirmed_by）必须是不同主体；
定稿者角色只能 challenger/coordinator。机器挑战者在记录里显式标注
``machine``，不得冒充人工同行评审。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tools import failure_postmortems as fpm

HARVESTER_ID = "harvester:postmortem-scan@1.0"
CHALLENGER_ID = "challenger:postmortem-rulecheck@1.0"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── 方言适配 ───────────────────────────

def _is_pg(engine: Engine) -> bool:
    return engine.dialect.name.startswith("postgres")


def _enc_array(values: Sequence[str], engine: Engine) -> Any:
    """PG 交给驱动做 text[] 适配；SQLite 存 JSON 文本。"""
    seq = [str(v) for v in values]
    return seq if _is_pg(engine) else json.dumps(seq, ensure_ascii=False)


def _dec_array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return [part.strip() for part in stripped.strip("[]").split(",") if part.strip()]
        return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
    return [str(value)]


def _dec_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    return {"_value": value}


def engine_from_env(url: str | None = None) -> Engine:
    target = url or os.getenv("DATABASE_URL")
    if not target:
        raise RuntimeError("DATABASE_URL 未设置：无法连接 mission 库")
    if target.startswith("postgresql://"):
        target = target.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(target, pool_pre_ping=True, future=True)


# ─────────────────────────── 存储层 ───────────────────────────

@dataclass
class Repo:
    engine: Engine

    # ---- schema ----
    def ensure_schema(self) -> None:
        ddl = fpm.ddl_for(self.engine.dialect.name)
        with self.engine.begin() as conn:
            for statement in [s.strip() for s in ddl.split(";\n") if s.strip()]:
                conn.execute(text(statement))

    # ---- missions ----
    def failed_missions(self, *, days: int = 7, limit: int = 40) -> list[fpm.MissionView]:
        sql = text(
            """
            SELECT id, goal, repo, status, organization_id, fail_stage,
                   finished_at, claimed_by, model, result
              FROM tensor_dev_missions
             WHERE status = 'failed'
               AND finished_at > now() - make_interval(days => :days)
             ORDER BY finished_at ASC
             LIMIT :limit
            """
            if _is_pg(self.engine)
            else """
            SELECT id, goal, repo, status, organization_id, fail_stage,
                   finished_at, claimed_by, model, result
              FROM tensor_dev_missions
             WHERE status = 'failed'
             ORDER BY finished_at ASC
             LIMIT :limit
            """
        )
        params: dict[str, Any] = {"limit": int(limit)}
        if _is_pg(self.engine):
            params["days"] = int(days)
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(sql, params)]
        views = [fpm.MissionView.from_row(r) for r in rows]
        if not _is_pg(self.engine) and days:  # SQLite 测试库：日期在 Python 侧裁
            cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
            views = [
                v
                for v in views
                if not v.finished_at or _ts(v.finished_at) >= cutoff
            ]
        return views

    def mission(self, mission_id: int) -> fpm.MissionView | None:
        sql = text(
            """
            SELECT id, goal, repo, status, organization_id, fail_stage,
                   finished_at, claimed_by, model, result
              FROM tensor_dev_missions WHERE id = :mid
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"mid": mission_id}).first()
        return fpm.MissionView.from_row(dict(row._mapping)) if row else None

    # ---- hypothesis ----
    def upsert_hypothesis(self, hypothesis_id: str, mission_id: int, statement: str) -> None:
        sql = text(
            """
            INSERT INTO hypothesis_registry
                (hypothesis_id, mission_id, statement, source, status, created_at)
            VALUES (:hid, :mid, :stmt, 'mission_goal', 'refuted', :ts)
            """
        )
        with self.engine.begin() as conn:
            try:
                conn.execute(
                    sql, {"hid": hypothesis_id, "mid": mission_id, "stmt": statement, "ts": now()}
                )
            except Exception:  # 幂等：已登记即跳过
                pass

    # ---- postmortems ----
    def existing_postmortem(self, mission_id: int) -> dict[str, Any] | None:
        sql = text(
            "SELECT * FROM failure_postmortems WHERE mission_id = :mid"
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"mid": mission_id}).first()
        if not row:
            return None
        raw = dict(row._mapping)
        raw["invalidated_paths"] = _dec_array(raw.get("invalidated_paths"))
        raw["avoidance_rules"] = _dec_array(raw.get("avoidance_rules"))
        raw["evidence"] = _dec_json(raw.get("evidence"))
        raw["challenge"] = _dec_json(raw.get("challenge"))
        return raw

    def insert_postmortem(self, draft: Mapping[str, Any]) -> None:
        sql = text(
            """
            INSERT INTO failure_postmortems
                (postmortem_id, mission_id, organization_id, repo, hypothesis_id,
                 initial_hypothesis, failed_step, failure_stage, legacy_stage,
                 error_class, invalidated_paths, avoidance_rules, status,
                 harvested_by, challenge, confirmed_by, confirm_role, confirmed_at,
                 rejected_reason, evidence, created_at, updated_at)
            VALUES (:postmortem_id, :mission_id, :organization_id, :repo, :hypothesis_id,
                    :initial_hypothesis, :failed_step, :failure_stage, :legacy_stage,
                    :error_class, :invalidated_paths, :avoidance_rules, :status,
                    :harvested_by, :challenge, :confirmed_by, :confirm_role, :confirmed_at,
                    :rejected_reason, :evidence, :created_at, :updated_at)
            """
        )
        params = dict(draft)
        params["invalidated_paths"] = _enc_array(draft.get("invalidated_paths") or [], self.engine)
        params["avoidance_rules"] = _enc_array(draft.get("avoidance_rules") or [], self.engine)
        params["evidence"] = json.dumps(draft.get("evidence") or {}, ensure_ascii=False)
        params["challenge"] = json.dumps(draft.get("challenge") or {}, ensure_ascii=False)
        for key in ("confirmed_by", "confirm_role", "confirmed_at", "rejected_reason", "legacy_stage"):
            params.setdefault(key, None)
        with self.engine.begin() as conn:
            conn.execute(sql, params)

    def update_postmortem(self, postmortem_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = []
        params: dict[str, Any] = {"postmortem_id": postmortem_id}
        for key, value in fields.items():
            if key in ("invalidated_paths", "avoidance_rules"):
                value = _enc_array(value or [], self.engine)
            elif key in ("evidence", "challenge"):
                value = json.dumps(value or {}, ensure_ascii=False)
            assignments.append(f"{key} = :{key}")
            params[key] = value
        params["updated_at"] = now()
        assignments.append("updated_at = :updated_at")
        sql = text(
            f"UPDATE failure_postmortems SET {', '.join(assignments)} WHERE postmortem_id = :postmortem_id"
        )
        with self.engine.begin() as conn:
            conn.execute(sql, params)

    # ---- rules ----
    def insert_rules(
        self, draft: Mapping[str, Any], rows: Sequence[Mapping[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        rows = [dict(r) for r in rows] if rows is not None else fpm.rule_rows(draft)
        sql = text(
            """
            INSERT INTO postmortem_rules
                (rule_id, postmortem_id, mission_id, rule_index, rule_text,
                 failure_stage, error_class, created_at)
            VALUES (:rule_id, :postmortem_id, :mission_id, :rule_index, :rule_text,
                    :failure_stage, :error_class, :created_at)
            """
        )
        inserted: list[dict[str, Any]] = []
        with self.engine.begin() as conn:
            for row in rows:
                exists = conn.execute(
                    text("SELECT 1 FROM postmortem_rules WHERE rule_id = :rid"),
                    {"rid": row["rule_id"]},
                ).first()
                if exists:
                    continue
                conn.execute(sql, row)
                inserted.append(dict(row))
        return inserted

    def confirmed_rule_texts(self) -> dict[str, str]:
        """rule_text -> rule_id：同文规则复用（规则库收敛，引用可审计）。"""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.rule_text, r.rule_id FROM postmortem_rules r
                      JOIN failure_postmortems p ON p.postmortem_id = r.postmortem_id
                     WHERE p.status = 'confirmed'
                     ORDER BY r.created_at ASC, r.rule_id ASC
                    """
                )
            )
            return {str(r[0]): str(r[1]) for r in rows}

    def rules(self, *, only_confirmed: bool = True) -> list[dict[str, Any]]:
        if only_confirmed:
            sql = text(
                """
                SELECT r.* FROM postmortem_rules r
                  JOIN failure_postmortems p ON p.postmortem_id = r.postmortem_id
                 WHERE p.status = 'confirmed'
                 ORDER BY r.created_at ASC, r.rule_id ASC
                """
            )
        else:
            sql = text("SELECT * FROM postmortem_rules ORDER BY created_at ASC, rule_id ASC")
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sql)]

    # ---- references ----
    def insert_reference(self, ref: Mapping[str, Any]) -> bool:
        sql = text(
            """
            INSERT INTO postmortem_rule_references
                (rule_id, referencing_mission_id, reference_kind, evidence, created_at)
            VALUES (:rule_id, :referencing_mission_id, :reference_kind, :evidence, :created_at)
            """
        )
        with self.engine.begin() as conn:
            try:
                conn.execute(sql, dict(ref))
            except Exception:  # UNIQUE 冲突=已登记，幂等
                return False
        return True

    def references(self) -> list[dict[str, Any]]:
        sql = text(
            "SELECT * FROM postmortem_rule_references ORDER BY reference_id ASC"
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sql)]

    def postmortems(self) -> list[dict[str, Any]]:
        sql = text("SELECT * FROM failure_postmortems ORDER BY mission_id ASC")
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(sql)]
        for row in rows:
            row["invalidated_paths"] = _dec_array(row.get("invalidated_paths"))
            row["avoidance_rules"] = _dec_array(row.get("avoidance_rules"))
            row["evidence"] = _dec_json(row.get("evidence"))
            row["challenge"] = _dec_json(row.get("challenge"))
        return rows


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ─────────────────────────── 收割运行器 ───────────────────────────

@dataclass
class HarvestReport:
    harvested: int = 0
    confirmed: int = 0
    disputed: int = 0
    draft_pending: int = 0
    withheld: int = 0
    skipped_existing: int = 0
    references_new: int = 0
    drafts: list[dict[str, Any]] = None  # type: ignore[assignment]
    objections: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.drafts = self.drafts or []
        self.objections = self.objections or []


def harvest(
    repo: Repo,
    *,
    days: int = 7,
    limit: int = 40,
    confirm: bool = True,
    apply: bool = True,
    harvester: str = HARVESTER_ID,
    challenger: str = CHALLENGER_ID,
    refresh: bool = False,
    mission_ids: Sequence[int] | None = None,
) -> HarvestReport:
    """主线：failed 单 → 草稿 → 双闸 → 定稿 → 规则 → 引用。

    ``mission_ids`` 非空时只处理这些单（终态结算钩子单条触发用）。
    """
    report = HarvestReport()
    missions = repo.failed_missions(days=days, limit=limit)
    if mission_ids:
        wanted = {int(m) for m in mission_ids}
        missions = [m for m in missions if m.mission_id in wanted]
        missing = wanted - {m.mission_id for m in missions}
        for mid in sorted(missing):
            one = repo.mission(mid)
            if one is not None:
                missions.append(one)
    for mission in missions:
        if mission.status != "failed":
            continue
        existing = repo.existing_postmortem(mission.mission_id)
        if existing and not refresh:
            report.skipped_existing += 1
            # 已定稿的单仍参与“后续文本引用”扫描，其规则由 stored_rules 提供。
            continue

        stage = fpm.failure_stage(mission)
        klass = fpm.error_class(mission, stage)
        # ① 起草前检索已定稿规则（命中即"后续任务引用"留痕）
        consult = fpm.retrieve_rules(
            " ".join(
                [
                    mission.goal[:400],
                    fpm.failure_reason(mission),
                    stage,
                    klass,
                    " ".join(fpm.raw_stage_tokens(mission)),
                ]
            ),
            repo.rules(only_confirmed=True),
            stage=stage,
            klass=klass,
            top_k=2,
            min_score=0.5,
        )
        draft = fpm.draft_from_mission(mission, harvested_by=harvester, consulted=consult)
        if draft is None:
            report.withheld += 1
            report.objections.append(
                {"mission_id": mission.mission_id, "objections": ["证据不足（无七环信号），WITHHELD"]}
            )
            continue
        report.harvested += 1

        # ② 双闸：结构挑战 + 独立复算
        verdict = fpm.challenge_draft(draft)
        review = fpm.independent_review(mission, draft)
        combine = fpm.ChallengeVerdict(
            ok=verdict.ok and review.ok,
            objections=verdict.objections + review.objections,
        )
        draft["challenge"] = combine.as_dict()

        # ③ 定稿（挑战角色，非起草者）
        result = fpm.confirm_draft(
            draft, reviewer=challenger, role="challenger", mission=mission, challenge=combine
        )
        draft["status"] = result.status
        if result.ok:
            draft["confirmed_by"] = challenger
            draft["confirm_role"] = "challenger(machine)"
            draft["confirmed_at"] = now()
            report.confirmed += 1
        else:
            if result.status == "disputed":
                report.disputed += 1
            else:
                report.draft_pending += 1
            report.objections.append(
                {"mission_id": mission.mission_id, "objections": combine.objections,
                 "confirm_reason": result.reason}
            )

        report.drafts.append(
            {
                "postmortem_id": draft["postmortem_id"],
                "mission_id": draft["mission_id"],
                "failure_stage": draft["failure_stage"],
                "legacy_stage": draft["legacy_stage"],
                "error_class": draft["error_class"],
                "status": draft["status"],
                "avoidance_rules": draft["avoidance_rules"],
                "invalidated_paths": draft["invalidated_paths"],
                "consulted_rule_ids": draft["consulted_rule_ids"],
            }
        )

        if not apply:
            continue

        repo.upsert_hypothesis(draft["hypothesis_id"], draft["mission_id"], draft["hypothesis_statement"])
        if existing:  # refresh：先删旧行（含其规则与指向这些规则的引用，避免 FK 悬挂）
            with repo.engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        DELETE FROM postmortem_rule_references
                         WHERE rule_id IN (SELECT rule_id FROM postmortem_rules
                                            WHERE postmortem_id = :pid)
                        """
                    ),
                    {"pid": draft["postmortem_id"]},
                )
                conn.execute(
                    text("DELETE FROM postmortem_rules WHERE postmortem_id = :pid"),
                    {"pid": draft["postmortem_id"]},
                )
                conn.execute(
                    text("DELETE FROM failure_postmortems WHERE postmortem_id = :pid"),
                    {"pid": draft["postmortem_id"]},
                )
        repo.insert_postmortem(draft)

        # ④ 定稿才生成可引用规则 + 登记"后续引用"
        if result.ok:
            known = repo.confirmed_rule_texts()
            fresh_rows = []
            for row in fpm.rule_rows(draft):
                canonical = known.get(row["rule_text"])
                if canonical:
                    inserted = repo.insert_reference(
                        fpm.reference_row(
                            canonical,
                            referencing_mission_id=mission.mission_id,
                            kind="later_postmortem_consult",
                            evidence=(
                                f"mission {mission.mission_id} 的规则与既有 {canonical} 同文"
                                f"（模板收敛，stage={stage}, class={klass}）"
                            ),
                        )
                    )
                    report.references_new += int(inserted)
                else:
                    fresh_rows.append(row)
            if fresh_rows:
                repo.insert_rules(draft, rows=fresh_rows)
            for ref in consult:
                inserted = repo.insert_reference(
                    fpm.reference_row(
                        str(ref["rule_id"]),
                        referencing_mission_id=mission.mission_id,
                        kind="later_postmortem_consult",
                        evidence=(
                            f"mission {mission.mission_id} 起草前检索命中"
                            f"（stage={stage}, class={klass}, score={ref['score']}）"
                        ),
                    )
                )
                report.references_new += int(inserted)

    # ⑤ 显式文本引用扫描（后续任务文本里写 rule_id）
    if apply:
        rules = repo.rules(only_confirmed=True)
        cited = fpm.scan_text_citations(repo.failed_missions(days=days, limit=200), rules)
        for ref in cited:
            report.references_new += int(repo.insert_reference(ref))
        report.cited = len(cited)  # type: ignore[attr-defined]
    return report


def load_confirmed_rules(repo: Repo | None = None) -> list[dict[str, Any]]:
    repo = repo or Repo(engine_from_env())
    return repo.rules(only_confirmed=True)


def run_harvest(
    *,
    days: int = 7,
    limit: int = 40,
    apply: bool = False,
    confirm: bool = True,
    out: Path | None = None,
    url: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    repo = Repo(engine_from_env(url))
    if apply:
        repo.ensure_schema()
    report = harvest(repo, days=days, limit=limit, confirm=confirm, apply=apply, refresh=refresh)
    postmortems = repo.postmortems() if apply else []
    rules = repo.rules(only_confirmed=False) if apply else []
    refs = repo.references() if apply else []
    summary = fpm.summarize(postmortems, rules, refs)
    payload = {
        "generated_at": now(),
        "days": days,
        "limit": limit,
        "apply": apply,
        "harvest": {
            "harvested": report.harvested,
            "confirmed": report.confirmed,
            "disputed": report.disputed,
            "draft_pending": report.draft_pending,
            "withheld": report.withheld,
            "skipped_existing": report.skipped_existing,
            "references_new": report.references_new,
        },
        "drafts": report.drafts,
        "objections": report.objections,
        "db_summary": summary,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload