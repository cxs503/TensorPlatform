"""Persistent polling monitor for real industrial data-source queries.

This service deliberately reads the configured data source instead of the demo
collection buffer.  A monitor consumes only new rows, keeps a small per-field
state, and records actionable stale/freeze/jump alerts in PostgreSQL.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from database.config import SessionLocal
from services.datasource_service import get_datasource_manager


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RealtimeSensorMonitorService:
    _schema_lock = threading.Lock()

    def ensure_schema(self) -> None:
        with self._schema_lock, SessionLocal() as db:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS realtime_sensor_monitors (
                    id VARCHAR(64) PRIMARY KEY,
                    organization_id INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    name VARCHAR(160) NOT NULL,
                    source_id VARCHAR(128) NOT NULL,
                    query_sql TEXT NOT NULL,
                    time_column VARCHAR(128) NOT NULL,
                    fields JSONB NOT NULL DEFAULT '[]'::jsonb,
                    poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
                    freeze_points INTEGER NOT NULL DEFAULT 20,
                    jump_sigma DOUBLE PRECISION NOT NULL DEFAULT 6,
                    stale_seconds INTEGER NOT NULL DEFAULT 300,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    state JSONB NOT NULL DEFAULT '{}'::jsonb,
                    latest_timestamp TIMESTAMP NULL,
                    last_checked_at TIMESTAMP NULL,
                    last_error TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS realtime_sensor_alerts (
                    id SERIAL PRIMARY KEY,
                    monitor_id VARCHAR(64) NOT NULL REFERENCES realtime_sensor_monitors(id) ON DELETE CASCADE,
                    organization_id INTEGER NOT NULL,
                    alert_type VARCHAR(32) NOT NULL,
                    field_name VARCHAR(128),
                    level VARCHAR(16) NOT NULL DEFAULT 'warning',
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    message TEXT NOT NULL,
                    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                    occurred_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    resolved_at TIMESTAMP NULL
                )
            """))
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_realtime_monitor_org ON realtime_sensor_monitors(organization_id, created_at DESC)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_realtime_alert_monitor ON realtime_sensor_alerts(monitor_id, status, occurred_at DESC)"))
            db.commit()

    @staticmethod
    def _decode(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return default

    def list_monitors(self, organization_id: int) -> list[dict]:
        self.ensure_schema()
        with SessionLocal() as db:
            rows = db.execute(text("""
                SELECT m.*, COALESCE(a.active_alerts, 0) AS active_alerts
                FROM realtime_sensor_monitors m
                LEFT JOIN (
                    SELECT monitor_id, COUNT(*) AS active_alerts FROM realtime_sensor_alerts
                    WHERE status='active' GROUP BY monitor_id
                ) a ON a.monitor_id=m.id
                WHERE m.organization_id=:organization_id
                ORDER BY m.created_at DESC
            """), {"organization_id": organization_id}).mappings().all()
            return [self._monitor_dict(row) for row in rows]

    def get_monitor(self, monitor_id: str, organization_id: int) -> dict | None:
        self.ensure_schema()
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT m.*, (SELECT COUNT(*) FROM realtime_sensor_alerts a WHERE a.monitor_id=m.id AND a.status='active') AS active_alerts
                FROM realtime_sensor_monitors m WHERE m.id=:id AND m.organization_id=:organization_id
            """), {"id": monitor_id, "organization_id": organization_id}).mappings().first()
            return self._monitor_dict(row) if row else None

    def _monitor_dict(self, row: Any) -> dict:
        return {
            "id": row["id"], "name": row["name"], "source_id": row["source_id"],
            "time_column": row["time_column"], "fields": self._decode(row["fields"], []),
            "poll_interval_seconds": row["poll_interval_seconds"], "freeze_points": row["freeze_points"],
            "jump_sigma": row["jump_sigma"], "stale_seconds": row["stale_seconds"],
            "enabled": bool(row["enabled"]), "latest_timestamp": row["latest_timestamp"].isoformat() if row["latest_timestamp"] else None,
            "last_checked_at": row["last_checked_at"].isoformat() if row["last_checked_at"] else None,
            "last_error": row["last_error"], "active_alerts": int(row.get("active_alerts", 0)),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }

    def create_monitor(self, owner: dict, payload: dict) -> dict:
        self.ensure_schema()
        monitor_id = f"sensor_monitor_{uuid.uuid4().hex[:10]}"
        with SessionLocal() as db:
            db.execute(text("""
                INSERT INTO realtime_sensor_monitors
                (id, organization_id, created_by, name, source_id, query_sql, time_column, fields,
                 poll_interval_seconds, freeze_points, jump_sigma, stale_seconds)
                VALUES (:id,:organization_id,:created_by,:name,:source_id,:query_sql,:time_column,
                        CAST(:fields AS JSONB),:poll_interval_seconds,:freeze_points,:jump_sigma,:stale_seconds)
            """), {**payload, "id": monitor_id, "organization_id": owner["organization_id"],
                   "created_by": owner["id"], "fields": json.dumps(payload["fields"], ensure_ascii=False)})
            db.commit()
        return self.get_monitor(monitor_id, owner["organization_id"])

    def update_monitor(self, monitor_id: str, organization_id: int, payload: dict) -> bool:
        self.ensure_schema()
        allowed = {"name", "query_sql", "time_column", "fields", "poll_interval_seconds", "freeze_points", "jump_sigma", "stale_seconds", "enabled"}
        fields = {key: value for key, value in payload.items() if key in allowed}
        if not fields:
            return self.get_monitor(monitor_id, organization_id) is not None
        assignments = []
        params = {"id": monitor_id, "organization_id": organization_id}
        for key, value in fields.items():
            if key == "fields":
                assignments.append("fields=CAST(:fields AS JSONB)")
                params[key] = json.dumps(value, ensure_ascii=False)
            else:
                assignments.append(f"{key}=:{key}")
                params[key] = value
        assignments.append("updated_at=NOW()")
        with SessionLocal() as db:
            changed = db.execute(text(f"UPDATE realtime_sensor_monitors SET {', '.join(assignments)} WHERE id=:id AND organization_id=:organization_id"), params)
            db.commit()
            return bool(changed.rowcount)

    def delete_monitor(self, monitor_id: str, organization_id: int) -> bool:
        self.ensure_schema()
        with SessionLocal() as db:
            result = db.execute(text("DELETE FROM realtime_sensor_monitors WHERE id=:id AND organization_id=:organization_id"), {"id": monitor_id, "organization_id": organization_id})
            db.commit()
            return bool(result.rowcount)

    def list_alerts(self, organization_id: int, monitor_id: str | None = None, limit: int = 200) -> list[dict]:
        self.ensure_schema()
        sql = "SELECT * FROM realtime_sensor_alerts WHERE organization_id=:organization_id"
        params = {"organization_id": organization_id, "limit": max(1, min(limit, 500))}
        if monitor_id:
            sql += " AND monitor_id=:monitor_id"; params["monitor_id"] = monitor_id
        sql += " ORDER BY occurred_at DESC LIMIT :limit"
        with SessionLocal() as db:
            rows = db.execute(text(sql), params).mappings().all()
        return [{**dict(row), "detail": self._decode(row["detail"], {}),
                 "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                 "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None} for row in rows]

    def _set_alert(self, db, monitor: dict, alert_type: str, field: str | None, message: str, detail: dict, active: bool = True) -> None:
        row = db.execute(text("""
            SELECT id FROM realtime_sensor_alerts WHERE monitor_id=:monitor_id AND alert_type=:alert_type
            AND field_name IS NOT DISTINCT FROM :field_name AND status='active' ORDER BY id DESC LIMIT 1
        """), {"monitor_id": monitor["id"], "alert_type": alert_type, "field_name": field}).mappings().first()
        if active:
            if row:
                db.execute(text("UPDATE realtime_sensor_alerts SET message=:message, detail=CAST(:detail AS JSONB), occurred_at=NOW() WHERE id=:id"), {"id": row["id"], "message": message, "detail": json.dumps(detail, ensure_ascii=False)})
            else:
                db.execute(text("""
                    INSERT INTO realtime_sensor_alerts(monitor_id,organization_id,alert_type,field_name,message,detail)
                    VALUES(:monitor_id,:organization_id,:alert_type,:field_name,:message,CAST(:detail AS JSONB))
                """), {"monitor_id": monitor["id"], "organization_id": monitor["organization_id"], "alert_type": alert_type, "field_name": field, "message": message, "detail": json.dumps(detail, ensure_ascii=False)})
        elif row:
            db.execute(text("UPDATE realtime_sensor_alerts SET status='resolved', resolved_at=NOW() WHERE id=:id"), {"id": row["id"]})

    def run_monitor(self, monitor_id: str, organization_id: int) -> dict:
        self.ensure_schema()
        with SessionLocal() as db:
            record = db.execute(text("SELECT * FROM realtime_sensor_monitors WHERE id=:id AND organization_id=:organization_id"), {"id": monitor_id, "organization_id": organization_id}).mappings().first()
            if not record:
                raise KeyError("监测任务不存在")
            monitor = dict(record); monitor["fields"] = self._decode(monitor["fields"], []); state = self._decode(monitor["state"], {})
            try:
                source = get_datasource_manager().get_config(monitor["source_id"], organization_id)
                if not source:
                    raise RuntimeError("关联数据源不存在或无访问权限")
                last = state.get("last_timestamp")
                query = monitor["query_sql"].replace("{last_timestamp}", str(last or "1970-01-01 00:00:00"))
                rows = get_datasource_manager().query(monitor["source_id"], query, organization_id)
                frame = pd.DataFrame(rows)
                if monitor["time_column"] not in frame.columns:
                    raise RuntimeError(f"查询结果不含时间列“{monitor['time_column']}”")
                frame[monitor["time_column"]] = pd.to_datetime(frame[monitor["time_column"]], errors="coerce", utc=True)
                frame = frame.dropna(subset=[monitor["time_column"]]).sort_values(monitor["time_column"])
                if last:
                    frame = frame[frame[monitor["time_column"]] > pd.to_datetime(last, utc=True)]
                if frame.empty:
                    latest = pd.to_datetime(state.get("last_timestamp"), utc=True, errors="coerce")
                    stale = bool(pd.notna(latest) and (_now() - latest.to_pydatetime()).total_seconds() > monitor["stale_seconds"])
                    self._set_alert(db, monitor, "stale", None, "数据源在设定时限内没有新增数据", {"stale_seconds": monitor["stale_seconds"], "latest_timestamp": state.get("last_timestamp")}, stale)
                    db.execute(text("UPDATE realtime_sensor_monitors SET last_checked_at=NOW(), last_error=NULL WHERE id=:id"), {"id": monitor_id}); db.commit()
                    return {"monitor_id": monitor_id, "new_rows": 0, "status": "stale" if stale else "idle"}
                values = state.get("values", {}); diffs = state.get("diffs", {}); freeze = state.get("freeze", {})
                findings: list[dict] = []
                for field in monitor["fields"]:
                    if field not in frame.columns:
                        findings.append({"field": field, "status": "missing_column"}); continue
                    series = pd.to_numeric(frame[field], errors="coerce")
                    previous = values.get(field); counter = int(freeze.get(field, 0)); history = [float(x) for x in diffs.get(field, [])[-100:] if x is not None]
                    field_frozen = field_jump = False
                    for stamp, value in zip(frame[monitor["time_column"]], series):
                        if pd.isna(value):
                            continue
                        value = float(value)
                        if previous is not None:
                            delta = abs(value - float(previous)); tolerance = max(abs(float(previous)) * 1e-9, 1e-12)
                            counter = counter + 1 if delta <= tolerance else 1
                            if counter >= monitor["freeze_points"]: field_frozen = True
                            if len(history) >= 10:
                                median = float(pd.Series(history).median()); mad = float((pd.Series(history) - median).abs().median())
                                limit = median + monitor["jump_sigma"] * 1.4826 * max(mad, median * 1e-6, 1e-12)
                                if delta > limit: field_jump = True
                            history.append(delta); history = history[-100:]
                        else: counter = 1
                        previous = value
                    values[field], freeze[field], diffs[field] = previous, counter, history
                    self._set_alert(db, monitor, "freeze", field, f"测点“{field}”连续 {counter} 个采样点无变化", {"freeze_points": counter}, field_frozen)
                    self._set_alert(db, monitor, "jump", field, f"测点“{field}”出现异常突跳", {"jump_sigma": monitor["jump_sigma"]}, field_jump)
                    findings.append({"field": field, "freeze_points": counter, "freeze": field_frozen, "jump": field_jump})
                newest = frame[monitor["time_column"]].iloc[-1].isoformat()
                state.update({"last_timestamp": newest, "values": values, "diffs": diffs, "freeze": freeze})
                self._set_alert(db, monitor, "stale", None, "数据源在设定时限内没有新增数据", {}, False)
                db.execute(text("""
                    UPDATE realtime_sensor_monitors SET state=CAST(:state AS JSONB), latest_timestamp=:latest_timestamp,
                    last_checked_at=NOW(), last_error=NULL, updated_at=NOW() WHERE id=:id
                """), {"id": monitor_id, "state": json.dumps(state), "latest_timestamp": pd.to_datetime(newest).to_pydatetime().replace(tzinfo=None)})
                db.commit()
                return {"monitor_id": monitor_id, "new_rows": int(len(frame)), "latest_timestamp": newest, "findings": findings}
            except Exception as exc:
                db.rollback()
                db.execute(text("UPDATE realtime_sensor_monitors SET last_checked_at=NOW(), last_error=:error WHERE id=:id"), {"id": monitor_id, "error": str(exc)[:1000]}); db.commit()
                raise

    def run_due(self) -> int:
        self.ensure_schema()
        now = _now(); due = []
        with SessionLocal() as db:
            rows = db.execute(text("SELECT id, organization_id, last_checked_at, poll_interval_seconds FROM realtime_sensor_monitors WHERE enabled=true")).mappings().all()
            for row in rows:
                checked = row["last_checked_at"]
                if not checked or (now.replace(tzinfo=None) - checked).total_seconds() >= row["poll_interval_seconds"]:
                    due.append((row["id"], row["organization_id"]))
        for monitor_id, organization_id in due:
            try: self.run_monitor(monitor_id, organization_id)
            except Exception: pass
        return len(due)


_service: RealtimeSensorMonitorService | None = None


def get_realtime_sensor_monitor_service() -> RealtimeSensorMonitorService:
    global _service
    if _service is None:
        _service = RealtimeSensorMonitorService()
    return _service
