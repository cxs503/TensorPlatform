#!/usr/bin/env python3
"""否定性知识台账批量加载器。

用法：
    python3 scripts/load_negative_knowledge_ledger.py \
        --input artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json \
        --db-url "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"

SQLite 兼容（测试用）：
    python3 scripts/load_negative_knowledge_ledger.py \
        --input artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json \
        --db-url "sqlite:///backend/foundation_ai.db"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

VALID_FAILURE_KINDS = ("hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression")
VALID_STATUSES = ("active", "superseded", "retracted")


def ensure_schema_sqlite(engine) -> None:
    """SQLite 兼容建表。"""
    sql = """
    CREATE TABLE IF NOT EXISTS negative_knowledge_ledger (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id                  VARCHAR(64) NOT NULL UNIQUE,
        mission_id                BIGINT,
        plan_id                   BIGINT,
        node_id                   VARCHAR(128),
        title                     VARCHAR(500) NOT NULL,
        hypothesis                TEXT NOT NULL,
        falsification_evidence    TEXT,
        failure_reason            TEXT,
        failure_kind              VARCHAR(64) NOT NULL,
        applicable_conditions     TEXT NOT NULL DEFAULT '{}',
        keywords                  TEXT NOT NULL DEFAULT '[]',
        domain_tags               TEXT NOT NULL DEFAULT '[]',
        differentiation_required  TEXT NOT NULL DEFAULT '[]',
        source_artifacts          TEXT NOT NULL DEFAULT '[]',
        confidence                FLOAT NOT NULL DEFAULT 0.8,
        schema_version            VARCHAR(16) NOT NULL DEFAULT '1.0',
        registered_by             VARCHAR(128),
        registered_at             DATETIME NOT NULL DEFAULT (datetime('now')),
        updated_at                DATETIME NOT NULL DEFAULT (datetime('now')),
        status                    VARCHAR(24) NOT NULL DEFAULT 'active'
    )
    """
    with engine.begin() as conn:
        conn.execute(text(sql))
        # 索引
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_nk_status ON negative_knowledge_ledger (status)",
            "CREATE INDEX IF NOT EXISTS idx_nk_failure_kind ON negative_knowledge_ledger (failure_kind)",
            "CREATE INDEX IF NOT EXISTS idx_nk_mission ON negative_knowledge_ledger (mission_id)",
            "CREATE INDEX IF NOT EXISTS idx_nk_registered_at ON negative_knowledge_ledger (registered_at)",
        ]:
            conn.execute(text(idx_sql))


def ensure_schema_postgres(engine) -> None:
    """PostgreSQL 建表（用 migration 文件）。"""
    migration_path = Path(__file__).resolve().parents[1] / "backend/migrations/120_negative_knowledge_ledger.sql"
    with open(migration_path) as f:
        sql = f.read()
    with engine.begin() as conn:
        conn.execute(text(sql))


def load_ledger(db_url: str, input_path: str, dry_run: bool = False) -> dict:
    """加载 JSON 台账到数据库。"""
    engine = create_engine(db_url)
    is_sqlite = db_url.startswith("sqlite")

    # 建表
    if not dry_run:
        if is_sqlite:
            ensure_schema_sqlite(engine)
        else:
            ensure_schema_postgres(engine)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries", [])
    report = {"total": len(entries), "inserted": 0, "skipped": 0, "errors": []}

    for entry in entries:
        try:
            if dry_run:
                report["inserted"] += 1
                continue

            with Session(engine) as session:
                # 幂等检查
                existing = session.execute(
                    text("SELECT id FROM negative_knowledge_ledger WHERE entry_id = :eid"),
                    {"eid": entry["entry_id"]},
                ).first()
                if existing:
                    report["skipped"] += 1
                    continue

                now = datetime.now(timezone.utc).isoformat()
                session.execute(
                    text("""
                        INSERT INTO negative_knowledge_ledger (
                            entry_id, mission_id, plan_id, node_id, title, hypothesis,
                            falsification_evidence, failure_reason, failure_kind,
                            applicable_conditions, keywords, domain_tags,
                            differentiation_required, source_artifacts,
                            confidence, schema_version, registered_by,
                            registered_at, updated_at, status
                        ) VALUES (
                            :entry_id, :mission_id, :plan_id, :node_id, :title, :hypothesis,
                            :falsification_evidence, :failure_reason, :failure_kind,
                            :applicable_conditions, :keywords, :domain_tags,
                            :differentiation_required, :source_artifacts,
                            :confidence, :schema_version, :registered_by,
                            :registered_at, :updated_at, :status
                        )
                    """),
                    {
                        "entry_id": entry["entry_id"],
                        "mission_id": entry.get("mission_id"),
                        "plan_id": entry.get("plan_id"),
                        "node_id": entry.get("node_id"),
                        "title": entry["title"][:500],
                        "hypothesis": entry["hypothesis"],
                        "falsification_evidence": entry.get("falsification_evidence"),
                        "failure_reason": entry.get("failure_reason"),
                        "failure_kind": entry["failure_kind"],
                        "applicable_conditions": json.dumps(entry.get("applicable_conditions", {}), ensure_ascii=False),
                        "keywords": json.dumps(entry.get("keywords", []), ensure_ascii=False),
                        "domain_tags": json.dumps(entry.get("domain_tags", []), ensure_ascii=False),
                        "differentiation_required": json.dumps(entry.get("differentiation_required", []), ensure_ascii=False),
                        "source_artifacts": json.dumps(entry.get("source_artifacts", []), ensure_ascii=False),
                        "confidence": entry.get("confidence", 0.8),
                        "schema_version": "1.0",
                        "registered_by": entry.get("registered_by", "loader:m14521"),
                        "registered_at": now,
                        "updated_at": now,
                        "status": "active",
                    },
                )
                session.commit()
                report["inserted"] += 1

        except Exception as exc:
            report["errors"].append({"entry_id": entry.get("entry_id"), "error": str(exc)})

    return report


def main():
    parser = argparse.ArgumentParser(description="加载否定性知识台账")
    parser.add_argument("--input", required=True, help="JSON 台账文件路径")
    parser.add_argument("--db-url", default="sqlite:///backend/foundation_ai.db", help="数据库连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="只计数不写入")
    args = parser.parse_args()

    report = load_ledger(args.db_url, args.input, args.dry_run)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["errors"]:
        print(f"\n⚠ {len(report['errors'])} 条加载失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ 成功加载 {report['inserted']} 条，跳过 {report['skipped']} 条")


if __name__ == "__main__":
    main()
