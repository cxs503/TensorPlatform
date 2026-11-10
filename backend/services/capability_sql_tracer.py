"""
能力台账证据链 — SQL 溯源引擎

设计目标：
- 每个能力等级的判定可 SQL 溯源到具体测试/基准/误差值
- 零领域语义：只认能力 ID/指标/数值/阈值
- 支持 SQLite（轻量级，适合本地开发与 CI）

用法:
    from services.capability_sql_tracer import CapabilitySQLTracer

    tracer = CapabilitySQLTracer(db_path="data/capability_evidence.db")
    tracer.init_schema()

    # 注册能力
    tracer.register_capability("calc.resistance", description="阻力计算", domain="resistance")

    # 存储证据
    tracer.store_evidence_item({...})
    tracer.store_evidence_bundle({...})

    # 存储裁定
    tracer.store_adjudication({...})

    # 存储台账变更
    tracer.store_ledger_change({...})

    # 溯源查询
    trace = tracer.get_capability_trace("calc.resistance")
    details = tracer.get_evidence_details("calc.resistance")
    history = tracer.get_change_history("calc.resistance")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 证据等级常量（与 capability_evidence_chain 保持一致）
EVIDENCE_REAL = "REAL"
EVIDENCE_MOCK = "MOCK"
EVIDENCE_STUB = "STUB"
EVIDENCE_WITHHELD = "WITHHELD"
EVIDENCE_NOT_SUPPORTED = "NOT_SUPPORTED"

VALID_LEVELS = {EVIDENCE_REAL, EVIDENCE_MOCK, EVIDENCE_STUB, EVIDENCE_WITHHELD, EVIDENCE_NOT_SUPPORTED}


class CapabilitySQLTracer:
    """
    SQL 溯源引擎：将能力注册、证据采集、等级判定、台账变更全部持久化到 SQLite，
    并提供 SQL 溯源查询接口。
    """

    def __init__(self, db_path: str | Path = "data/capability_evidence.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_schema(self, sql_path: str | Path | None = None):
        """
        初始化数据库 schema。

        Args:
            sql_path: SQL 迁移文件路径。如果为 None，使用内置 schema。
        """
        if sql_path:
            sql = Path(sql_path).read_text(encoding="utf-8")
        else:
            sql = self._builtin_schema()

        self.conn.executescript(sql)
        self.conn.commit()
        logger.info(f"Schema initialized: {self.db_path}")

    def _builtin_schema(self) -> str:
        """内置 schema（与 099_capability_evidence_sql.sql 一致）。"""
        return """
        CREATE TABLE IF NOT EXISTS capability_registry (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            capability_id   TEXT NOT NULL UNIQUE,
            description     TEXT,
            domain          TEXT,
            claimed_level   TEXT DEFAULT 'WITHHELD',
            expected_level  TEXT DEFAULT 'WITHHELD',
            registered_at   TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            metadata        TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS evidence_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_uuid   TEXT NOT NULL UNIQUE,
            capability_id   TEXT NOT NULL,
            source          TEXT NOT NULL,
            source_path     TEXT NOT NULL,
            metric_name     TEXT NOT NULL,
            measured_value  REAL NOT NULL,
            reference_value REAL NOT NULL,
            error_pct       REAL NOT NULL,
            passed          INTEGER NOT NULL,
            threshold_pct   REAL NOT NULL,
            run_id          TEXT,
            collected_at    TEXT DEFAULT (datetime('now')),
            extra           TEXT DEFAULT '{}',
            FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_capability ON evidence_items(capability_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_items(run_id);

        CREATE TABLE IF NOT EXISTS evidence_bundles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bundle_uuid     TEXT NOT NULL UNIQUE,
            capability_id   TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            total_items     INTEGER NOT NULL DEFAULT 0,
            pass_count      INTEGER NOT NULL DEFAULT 0,
            fail_count      INTEGER NOT NULL DEFAULT 0,
            max_error_pct   REAL NOT NULL DEFAULT 0.0,
            avg_error_pct   REAL NOT NULL DEFAULT 0.0,
            source_summary  TEXT DEFAULT '{}',
            collected_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
        );

        CREATE TABLE IF NOT EXISTS adjudication_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            adjudication_uuid TEXT NOT NULL UNIQUE,
            capability_id   TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            evidence_grade  TEXT NOT NULL,
            confidence      REAL NOT NULL,
            reason          TEXT NOT NULL,
            evidence_count  INTEGER NOT NULL DEFAULT 0,
            pass_count      INTEGER NOT NULL DEFAULT 0,
            fail_count      INTEGER NOT NULL DEFAULT 0,
            thresholds_used TEXT NOT NULL DEFAULT '{}',
            adjudicated_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
        );

        CREATE INDEX IF NOT EXISTS idx_adjudication_capability ON adjudication_results(capability_id);
        CREATE INDEX IF NOT EXISTS idx_adjudication_grade ON adjudication_results(evidence_grade);

        CREATE TABLE IF NOT EXISTS ledger_changes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            change_uuid     TEXT NOT NULL UNIQUE,
            capability_id   TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            old_level       TEXT NOT NULL,
            new_level       TEXT NOT NULL,
            evidence_grade  TEXT NOT NULL,
            action          TEXT NOT NULL,
            reason          TEXT NOT NULL,
            rollback_point  TEXT,
            prev_hash       TEXT,
            entry_hash      TEXT NOT NULL,
            changed_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ledger_capability ON ledger_changes(capability_id);

        CREATE VIEW IF NOT EXISTS v_capability_trace AS
        SELECT
            cr.capability_id,
            cr.claimed_level,
            ar.evidence_grade,
            ar.confidence,
            ar.reason AS adjudication_reason,
            ar.run_id,
            ar.adjudicated_at,
            eb.total_items,
            eb.pass_count,
            eb.fail_count,
            eb.max_error_pct,
            eb.avg_error_pct,
            lc.old_level,
            lc.new_level,
            lc.action,
            lc.changed_at
        FROM capability_registry cr
        LEFT JOIN adjudication_results ar ON cr.capability_id = ar.capability_id
            AND ar.id = (SELECT MAX(id) FROM adjudication_results WHERE capability_id = cr.capability_id)
        LEFT JOIN evidence_bundles eb ON cr.capability_id = eb.capability_id
            AND eb.run_id = ar.run_id
        LEFT JOIN ledger_changes lc ON cr.capability_id = lc.capability_id
            AND lc.id = (SELECT MAX(id) FROM ledger_changes WHERE capability_id = cr.capability_id);

        CREATE VIEW IF NOT EXISTS v_evidence_detail AS
        SELECT
            ei.capability_id,
            ei.evidence_uuid,
            ei.source,
            ei.source_path,
            ei.metric_name,
            ei.measured_value,
            ei.reference_value,
            ei.error_pct,
            ei.passed,
            ei.threshold_pct,
            ei.run_id,
            ei.collected_at,
            ar.evidence_grade,
            ar.adjudicated_at
        FROM evidence_items ei
        LEFT JOIN evidence_bundles eb ON ei.capability_id = eb.capability_id AND ei.run_id = eb.run_id
        LEFT JOIN adjudication_results ar ON ei.capability_id = ar.capability_id AND ei.run_id = ar.run_id;
        """

    # ── 能力注册 ──────────────────────────────────────────────────────

    def register_capability(
        self,
        capability_id: str,
        description: str = "",
        domain: str = "",
        claimed_level: str = EVIDENCE_WITHHELD,
        expected_level: str = EVIDENCE_WITHHELD,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """
        注册新能力到台账。

        Returns:
            {"status": "created"|"updated", "capability_id": str, ...}
        """
        if claimed_level not in VALID_LEVELS:
            raise ValueError(f"Invalid claimed_level: {claimed_level}")
        if expected_level not in VALID_LEVELS:
            raise ValueError(f"Invalid expected_level: {expected_level}")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        try:
            self.conn.execute(
                """INSERT INTO capability_registry
                   (capability_id, description, domain, claimed_level, expected_level, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (capability_id, description, domain, claimed_level, expected_level, now, meta_json),
            )
            self.conn.commit()
            return {
                "status": "created",
                "capability_id": capability_id,
                "description": description,
                "domain": domain,
                "claimed_level": claimed_level,
                "expected_level": expected_level,
                "registered_at": now,
            }
        except sqlite3.IntegrityError:
            # 已存在，更新
            self.conn.execute(
                """UPDATE capability_registry
                   SET description=?, domain=?, claimed_level=?, expected_level=?, updated_at=?, metadata=?
                   WHERE capability_id=?""",
                (description, domain, claimed_level, expected_level, now, meta_json, capability_id),
            )
            self.conn.commit()
            return {
                "status": "updated",
                "capability_id": capability_id,
                "updated_at": now,
            }

    def get_capability(self, capability_id: str) -> dict | None:
        """获取能力注册信息。"""
        row = self.conn.execute(
            "SELECT * FROM capability_registry WHERE capability_id=?", (capability_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_capabilities(self) -> list[dict]:
        """列出所有已注册能力。"""
        rows = self.conn.execute("SELECT * FROM capability_registry ORDER BY capability_id").fetchall()
        return [dict(r) for r in rows]

    # ── 证据存储 ──────────────────────────────────────────────────────

    def store_evidence_item(self, item: dict[str, Any]) -> str:
        """
        存储单条证据条目。

        Args:
            item: {capability_id, source, source_path, metric_name, measured_value,
                   reference_value, error_pct, passed, threshold_pct, run_id, extra}

        Returns:
            evidence_uuid
        """
        evidence_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        extra_json = json.dumps(item.get("extra", {}), ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO evidence_items
               (evidence_uuid, capability_id, source, source_path, metric_name,
                measured_value, reference_value, error_pct, passed, threshold_pct,
                run_id, collected_at, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_uuid,
                item["capability_id"],
                item["source"],
                item["source_path"],
                item["metric_name"],
                item["measured_value"],
                item["reference_value"],
                item["error_pct"],
                1 if item["passed"] else 0,
                item["threshold_pct"],
                item.get("run_id", ""),
                now,
                extra_json,
            ),
        )
        self.conn.commit()
        return evidence_uuid

    def store_evidence_bundle(self, bundle: dict[str, Any]) -> str:
        """
        存储证据包（聚合统计）。

        Args:
            bundle: {capability_id, run_id, total_items, pass_count, fail_count,
                     max_error_pct, avg_error_pct, source_summary}

        Returns:
            bundle_uuid
        """
        bundle_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        source_json = json.dumps(bundle.get("source_summary", {}), ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO evidence_bundles
               (bundle_uuid, capability_id, run_id, total_items, pass_count, fail_count,
                max_error_pct, avg_error_pct, source_summary, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bundle_uuid,
                bundle["capability_id"],
                bundle["run_id"],
                bundle.get("total_items", 0),
                bundle.get("pass_count", 0),
                bundle.get("fail_count", 0),
                bundle.get("max_error_pct", 0.0),
                bundle.get("avg_error_pct", 0.0),
                source_json,
                now,
            ),
        )
        self.conn.commit()
        return bundle_uuid

    # ── 裁定存储 ──────────────────────────────────────────────────────

    def store_adjudication(self, adj: dict[str, Any]) -> str:
        """
        存储裁定结果。

        Args:
            adj: {capability_id, run_id, evidence_grade, confidence, reason,
                  evidence_count, pass_count, fail_count, thresholds_used}

        Returns:
            adjudication_uuid
        """
        if adj["evidence_grade"] not in VALID_LEVELS:
            raise ValueError(f"Invalid evidence_grade: {adj['evidence_grade']}")

        adjudication_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        thresholds_json = json.dumps(adj.get("thresholds_used", {}), ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO adjudication_results
               (adjudication_uuid, capability_id, run_id, evidence_grade, confidence,
                reason, evidence_count, pass_count, fail_count, thresholds_used, adjudicated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                adjudication_uuid,
                adj["capability_id"],
                adj["run_id"],
                adj["evidence_grade"],
                adj["confidence"],
                adj["reason"],
                adj.get("evidence_count", 0),
                adj.get("pass_count", 0),
                adj.get("fail_count", 0),
                thresholds_json,
                now,
            ),
        )
        self.conn.commit()
        return adjudication_uuid

    # ── 台账变更存储 ──────────────────────────────────────────────────

    def store_ledger_change(self, change: dict[str, Any]) -> str:
        """
        存储台账变更（含哈希链）。

        Args:
            change: {capability_id, run_id, old_level, new_level, evidence_grade,
                     action, reason, rollback_point, prev_hash}

        Returns:
            change_uuid
        """
        change_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # 计算本条哈希
        payload = {
            "change_uuid": change_uuid,
            "capability_id": change["capability_id"],
            "run_id": change["run_id"],
            "old_level": change["old_level"],
            "new_level": change["new_level"],
            "evidence_grade": change["evidence_grade"],
            "action": change["action"],
            "reason": change["reason"],
            "rollback_point": change.get("rollback_point", ""),
            "prev_hash": change.get("prev_hash", ""),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        self.conn.execute(
            """INSERT INTO ledger_changes
               (change_uuid, capability_id, run_id, old_level, new_level, evidence_grade,
                action, reason, rollback_point, prev_hash, entry_hash, changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                change_uuid,
                change["capability_id"],
                change["run_id"],
                change["old_level"],
                change["new_level"],
                change["evidence_grade"],
                change["action"],
                change["reason"],
                change.get("rollback_point", ""),
                change.get("prev_hash", ""),
                entry_hash,
                now,
            ),
        )
        self.conn.commit()
        return change_uuid

    # ── 溯源查询 ──────────────────────────────────────────────────────

    def get_capability_trace(self, capability_id: str) -> dict | None:
        """
        获取能力最新溯源信息（一条 SQL 查询）。

        Returns:
            {capability_id, claimed_level, evidence_grade, confidence, ...} 或 None
        """
        row = self.conn.execute(
            "SELECT * FROM v_capability_trace WHERE capability_id=?", (capability_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_evidence_details(self, capability_id: str) -> list[dict]:
        """
        获取能力所有证据明细（SQL 溯源）。

        Returns:
            [{evidence_uuid, source, metric_name, measured_value, reference_value,
              error_pct, passed, threshold_pct, ...}]
        """
        rows = self.conn.execute(
            "SELECT * FROM v_evidence_detail WHERE capability_id=? ORDER BY collected_at",
            (capability_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_change_history(self, capability_id: str) -> list[dict]:
        """
        获取能力台账变更历史（完整记录）。

        Returns:
            [{change_uuid, old_level, new_level, action, reason, entry_hash, changed_at, ...}]
        """
        rows = self.conn.execute(
            "SELECT * FROM ledger_changes WHERE capability_id=? ORDER BY changed_at",
            (capability_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_adjudications(self, run_id: str | None = None) -> list[dict]:
        """获取所有裁定结果（可按 run_id 过滤）。"""
        if run_id:
            rows = self.conn.execute(
                "SELECT * FROM adjudication_results WHERE run_id=? ORDER BY adjudicated_at",
                (run_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM adjudication_results ORDER BY adjudicated_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_evidence_by_run(self, run_id: str) -> dict[str, list[dict]]:
        """按 run_id 获取所有证据（按能力分组）。"""
        rows = self.conn.execute(
            "SELECT * FROM evidence_items WHERE run_id=? ORDER BY capability_id, collected_at",
            (run_id,),
        ).fetchall()

        grouped: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            cap = d["capability_id"]
            if cap not in grouped:
                grouped[cap] = []
            grouped[cap].append(d)
        return grouped

    def verify_hash_chain(self, capability_id: str) -> dict[str, Any]:
        """
        验证台账变更哈希链完整性。

        Returns:
            {valid: bool, total: int, broken_at: int|None, details: [...]}
        """
        changes = self.get_change_history(capability_id)
        if not changes:
            return {"valid": True, "total": 0, "broken_at": None, "details": []}

        details = []
        for i, change in enumerate(changes):
            # 重新计算哈希
            payload = {
                "change_uuid": change["change_uuid"],
                "capability_id": change["capability_id"],
                "run_id": change["run_id"],
                "old_level": change["old_level"],
                "new_level": change["new_level"],
                "evidence_grade": change["evidence_grade"],
                "action": change["action"],
                "reason": change["reason"],
                "rollback_point": change.get("rollback_point", ""),
                "prev_hash": change.get("prev_hash", ""),
            }
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            hash_match = expected_hash == change["entry_hash"]
            prev_match = True
            if i > 0:
                prev_match = change["prev_hash"] == changes[i - 1]["entry_hash"]

            details.append({
                "index": i,
                "change_uuid": change["change_uuid"],
                "hash_match": hash_match,
                "prev_hash_match": prev_match,
                "old_level": change["old_level"],
                "new_level": change["new_level"],
            })

            if not hash_match or not prev_match:
                return {
                    "valid": False,
                    "total": len(changes),
                    "broken_at": i,
                    "details": details,
                }

        return {"valid": True, "total": len(changes), "broken_at": None, "details": details}

    # ── 统计查询 ──────────────────────────────────────────────────────

    def get_grade_distribution(self) -> dict[str, int]:
        """获取各等级能力数量分布。"""
        rows = self.conn.execute(
            """SELECT evidence_grade, COUNT(*) as cnt
               FROM adjudication_results
               WHERE id IN (
                   SELECT MAX(id) FROM adjudication_results GROUP BY capability_id
               )
               GROUP BY evidence_grade"""
        ).fetchall()
        return {r["evidence_grade"]: r["cnt"] for r in rows}

    def get_accuracy_report(self, run_id: str) -> dict[str, Any]:
        """
        获取指定 run 的裁定准确率报告（用于 C2 验证）。

        Returns:
            {total, correct, accuracy, details: [{capability_id, predicted, expected, correct}]}
        """
        rows = self.conn.execute(
            """SELECT ar.capability_id, ar.evidence_grade, cr.expected_level
               FROM adjudication_results ar
               JOIN capability_registry cr ON ar.capability_id = cr.capability_id
               WHERE ar.run_id=?""",
            (run_id,),
        ).fetchall()

        total = len(rows)
        correct = 0
        details = []
        for r in rows:
            is_correct = r["evidence_grade"] == r["expected_level"]
            if is_correct:
                correct += 1
            details.append({
                "capability_id": r["capability_id"],
                "predicted": r["evidence_grade"],
                "expected": r["expected_level"],
                "correct": is_correct,
            })

        accuracy = correct / total if total > 0 else 0.0
        return {
            "run_id": run_id,
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "details": details,
        }

    def export_sql_trace(self, capability_id: str) -> str:
        """
        导出能力的完整 SQL 溯源语句（可用于复现）。

        Returns:
            SQL 语句字符串
        """
        trace = self.get_capability_trace(capability_id)
        evidence = self.get_evidence_details(capability_id)
        history = self.get_change_history(capability_id)

        lines = [f"-- SQL Trace for capability: {capability_id}"]
        lines.append(f"-- Generated at: {datetime.now(timezone.utc).isoformat()}")
        lines.append("")

        if trace:
            lines.append("-- Latest trace:")
            for k, v in trace.items():
                lines.append(f"--   {k}: {v}")
            lines.append("")

        if evidence:
            lines.append(f"-- Evidence items ({len(evidence)}):")
            for e in evidence:
                lines.append(
                    f"--   [{e['source']}] {e['metric_name']}: "
                    f"measured={e['measured_value']}, ref={e['reference_value']}, "
                    f"error={e['error_pct']}%, passed={bool(e['passed'])}"
                )
            lines.append("")

        if history:
            lines.append(f"-- Change history ({len(history)}):")
            for h in history:
                lines.append(
                    f"--   {h['old_level']} → {h['new_level']} ({h['action']}): {h['reason']}"
                )

        return "\n".join(lines)
