"""ledger —— 质询账本（SQLite，标准库 sqlite3，无后端依赖）。

表：
  cycles         一次 24h 周期（模式 live/backfill、题数、信号源状态、产物路径）
  interrogations 单条质询事实（occ_id 唯一；fingerprint 是题目的稳定身份）
  conversions    质询 → 真实修补/改进单（含证据文件与摘要）

两级键（关键设计）：
  * ``fingerprint`` = 族:题号，代表「这道题问的是什么」，用于跨周期去重；
  * ``occ_id``      = ``<fingerprint>@<cycle_date>#k``，代表「某天真的问了一次」。
  题目库是有限的（~8 题），机制必须允许在证据变化后复用题目；
  但**同一天不许把同一题问两遍**，否则就是刷量。于是：
  账本唯一键落在 occ_id，去重窗口落在 fingerprint。

不变量（由 tests/test_interrogator.py 锁定）：
  I1  一次周期题数 ∈ [3,5]；
  I2  同一 (fingerprint, cycle_date) 不重复；生成器另在 7 周期窗口内优先不重复；
  I3  转化必须带证据文件（存在 + 非空 + sha256 当场计算），且挂修补/改进单号或幂等键；
  I4  周期与质询只追加；state 只能前进（emitted→answered→converted/closed）；
  I5  审计日志 append-only（JSONL），每次写操作可回溯。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DEFAULT_LEDGER_PATH = os.path.join("runs", "IN27-H4", "interrogator.db")

STATES = ("emitted", "answered", "converted", "closed")
MIN_PER_CYCLE = 3
MAX_PER_CYCLE = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id     TEXT PRIMARY KEY,
    cycle_date   TEXT NOT NULL,          -- UTC 日期 YYYY-MM-DD（连续性判据按此聚合）
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    mode         TEXT NOT NULL,          -- live | backfill
    question_count INTEGER NOT NULL,
    source_status TEXT NOT NULL,         -- ok | partial | unavailable
    artifact_path TEXT,
    notes        TEXT
);
CREATE TABLE IF NOT EXISTS interrogations (
    occ_id       TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    cycle_id     TEXT NOT NULL,
    cycle_date   TEXT NOT NULL,
    family       TEXT NOT NULL,
    qkey         TEXT NOT NULL,
    title        TEXT NOT NULL,
    question     TEXT NOT NULL,
    severity     INTEGER NOT NULL,
    evidence     TEXT NOT NULL,          -- JSON list
    branches     TEXT NOT NULL,          -- JSON list
    criteria     TEXT NOT NULL,          -- JSON list
    convert_rule TEXT NOT NULL,
    state        TEXT NOT NULL,
    mission_id   INTEGER,
    task_key     TEXT,
    answer_verdict TEXT,
    answer_path  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    occ_id       TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- repair | improvement
    fix_mission_id INTEGER,
    fix_task_key TEXT,
    evidence_path TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    note         TEXT,
    recorded_at  TEXT NOT NULL,
    verified     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_interro_state ON interrogations(state);
CREATE INDEX IF NOT EXISTS ix_interro_cycle ON interrogations(cycle_id);
CREATE INDEX IF NOT EXISTS ix_interro_fp ON interrogations(fingerprint);
CREATE INDEX IF NOT EXISTS ix_conv_fp ON conversions(fingerprint);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: str = DEFAULT_LEDGER_PATH):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.audit_path = os.path.abspath(path) + ".audit.jsonl"
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _audit(self, event: str, payload: dict[str, Any]) -> None:
        rec = {"at": _utcnow().isoformat(), "event": event, **payload}
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # -- cycles ------------------------------------------------------------
    def next_cycle_id(self, cycle_date: str) -> str:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM cycles WHERE cycle_date = ?", (cycle_date,)
        ).fetchone()
        return f"{cycle_date}#{int(row['n']) + 1}"

    def record_cycle(
        self,
        cycle_date: str,
        questions: Iterable[Any],
        mode: str = "live",
        source_status: str = "ok",
        artifact_path: str | None = None,
        notes: str = "",
        started_at: str | None = None,
    ) -> str:
        """写入一个周期与其质询；题数必须 ∈ [3,5]（I1）。"""
        if mode not in ("live", "backfill"):
            raise LedgerError(f"mode 必须为 live|backfill，得到 {mode!r}")
        qs = list(questions)
        if not (MIN_PER_CYCLE <= len(qs) <= MAX_PER_CYCLE):
            raise LedgerError(
                f"周期题数必须 ∈ [{MIN_PER_CYCLE},{MAX_PER_CYCLE}]，得到 {len(qs)}")
        fps = [q.fingerprint for q in qs]
        if len(set(fps)) != len(fps):
            raise LedgerError("同一周期内出现重复指纹")
        dup = [fp for fp in fps if self._conn.execute(
            "SELECT 1 FROM interrogations WHERE fingerprint = ? AND cycle_date = ?",
            (fp, cycle_date)).fetchone()]
        if dup:
            raise LedgerError(f"同一天已问过（I2 违反）：{dup}")

        cycle_id = self.next_cycle_id(cycle_date)
        now = _utcnow().isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO cycles(cycle_id, cycle_date, started_at, finished_at, mode,"
                " question_count, source_status, artifact_path, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (cycle_id, cycle_date, started_at or now, now, mode, len(qs),
                 source_status, artifact_path, notes),
            )
            for idx, q in enumerate(qs, start=1):
                occ_id = f"{q.fingerprint}@{cycle_date}#{idx}"
                self._conn.execute(
                    "INSERT INTO interrogations(occ_id, fingerprint, cycle_id, cycle_date,"
                    " family, qkey, title, question, severity, evidence, branches, criteria,"
                    " convert_rule, state, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (occ_id, q.fingerprint, cycle_id, cycle_date, q.family, q.key, q.title,
                     q.text, int(q.severity), json.dumps(q.evidence, ensure_ascii=False),
                     json.dumps(q.branches, ensure_ascii=False),
                     json.dumps(q.criteria, ensure_ascii=False), q.convert_rule,
                     "emitted", now, now),
                )
        self._audit("cycle_recorded", {"cycle_id": cycle_id, "mode": mode,
                                       "questions": fps, "artifact": artifact_path})
        return cycle_id

    # -- resolution --------------------------------------------------------
    def _resolve(self, identifier: str) -> sqlite3.Row:
        """按 occ_id 精确或 fingerprint 取最近一次未终结的质询。"""
        row = self._conn.execute(
            "SELECT * FROM interrogations WHERE occ_id = ?", (identifier,)).fetchone()
        if row is not None:
            return row
        row = self._conn.execute(
            "SELECT * FROM interrogations WHERE fingerprint = ? AND state != 'closed'"
            " ORDER BY created_at DESC LIMIT 1", (identifier,)).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT * FROM interrogations WHERE fingerprint = ?"
                " ORDER BY created_at DESC LIMIT 1", (identifier,)).fetchone()
        if row is None:
            raise LedgerError(f"质询不存在：{identifier}")
        return row

    # -- interrogations ----------------------------------------------------
    def attach_mission(self, identifier: str, mission_id: int | None,
                       task_key: str | None = None) -> str:
        row = self._resolve(identifier)
        with self._conn:
            self._conn.execute(
                "UPDATE interrogations SET mission_id = ?, task_key = COALESCE(?, task_key),"
                " updated_at = ? WHERE occ_id = ?",
                (mission_id, task_key, _utcnow().isoformat(), row["occ_id"]),
            )
        self._audit("mission_attached", {"occ_id": row["occ_id"], "mission_id": mission_id,
                                        "task_key": task_key})
        return row["occ_id"]

    def record_answer(self, identifier: str, verdict: str,
                      answer_path: str | None = None) -> str:
        verdict = (verdict or "").strip()
        if not verdict:
            raise LedgerError("answer 不带 verdict 不算回填")
        row = self._resolve(identifier)
        if row["state"] in ("converted", "closed"):
            raise LedgerError(f"质询已终结（{row['state']}），不可再改答案")
        with self._conn:
            self._conn.execute(
                "UPDATE interrogations SET state='answered', answer_verdict = ?,"
                " answer_path = ?, updated_at = ? WHERE occ_id = ?",
                (verdict, answer_path, _utcnow().isoformat(), row["occ_id"]),
            )
        self._audit("answer_recorded", {"occ_id": row["occ_id"], "verdict": verdict,
                                       "path": answer_path})
        return row["occ_id"]

    # -- conversions -------------------------------------------------------
    def record_conversion(
        self,
        identifier: str,
        kind: str,
        evidence_path: str,
        fix_mission_id: int | None = None,
        fix_task_key: str | None = None,
        note: str = "",
    ) -> int:
        """记录一次转化（I3：证据文件必须存在且非空，摘要当场计算）。"""
        if kind not in ("repair", "improvement"):
            raise LedgerError(f"kind 必须为 repair|improvement，得到 {kind!r}")
        row = self._resolve(identifier)
        if not os.path.isfile(evidence_path):
            raise LedgerError(f"转化证据文件不存在：{evidence_path}")
        if os.path.getsize(evidence_path) == 0:
            raise LedgerError("转化证据文件为空")
        if not fix_mission_id and not fix_task_key:
            raise LedgerError("转化必须挂修补/改进单（fix_mission_id 或 fix_task_key）")
        digest = _sha256_file(evidence_path)
        now = _utcnow().isoformat()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO conversions(occ_id, fingerprint, kind, fix_mission_id,"
                " fix_task_key, evidence_path, evidence_sha256, note, recorded_at, verified)"
                " VALUES (?,?,?,?,?,?,?,?,?,1)",
                (row["occ_id"], row["fingerprint"], kind, fix_mission_id, fix_task_key,
                 evidence_path, digest, note, now),
            )
            self._conn.execute(
                "UPDATE interrogations SET state='converted', updated_at=? WHERE occ_id=?",
                (now, row["occ_id"]),
            )
        cid = int(cur.lastrowid)
        self._audit("conversion_recorded", {"id": cid, "occ_id": row["occ_id"],
                                           "fingerprint": row["fingerprint"], "kind": kind,
                                           "evidence": evidence_path, "sha256": digest,
                                           "mission": fix_mission_id})
        return cid

    def close_interrogation(self, identifier: str, note: str = "") -> str:
        row = self._resolve(identifier)
        with self._conn:
            self._conn.execute(
                "UPDATE interrogations SET state='closed', updated_at=? WHERE occ_id=?",
                (_utcnow().isoformat(), row["occ_id"]))
        self._audit("interrogation_closed", {"occ_id": row["occ_id"], "note": note})
        return row["occ_id"]

    # -- queries -----------------------------------------------------------
    def recent_fingerprints(self, window_cycles: int = 7) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT fingerprint FROM (SELECT fingerprint, created_at FROM interrogations"
            " ORDER BY created_at DESC LIMIT ?)", (window_cycles * MAX_PER_CYCLE,)).fetchall()
        return {r["fingerprint"] for r in rows}

    def stats(self, as_of: datetime | None = None, stale_days: int = 7) -> dict[str, Any]:
        as_of = as_of or _utcnow()
        asked = self._conn.execute("SELECT COUNT(*) AS n FROM interrogations").fetchone()["n"]
        converted = self._conn.execute(
            "SELECT COUNT(DISTINCT fingerprint) AS n FROM conversions").fetchone()["n"]
        stale_rows = self._conn.execute(
            "SELECT fingerprint, created_at FROM interrogations"
            " WHERE state NOT IN ('converted','closed') ORDER BY created_at ASC").fetchall()
        stale: list[str] = []
        for r in stale_rows:
            try:
                created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (as_of - created) >= timedelta(days=stale_days) and r["fingerprint"] not in stale:
                stale.append(r["fingerprint"])
        return {"asked": asked, "converted": converted, "stale": stale,
                "stale_days": stale_days, "as_of": as_of.isoformat()}

    def cycle_days(self) -> dict[str, dict[str, int]]:
        """date -> {"cycles", "questions", "live", "backfill"}。"""
        rows = self._conn.execute(
            "SELECT cycle_date, mode, question_count FROM cycles ORDER BY cycle_date").fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            slot = out.setdefault(r["cycle_date"], {"cycles": 0, "questions": 0,
                                                    "live": 0, "backfill": 0})
            slot["cycles"] += 1
            slot["questions"] += int(r["question_count"])
            if r["mode"] == "live":
                slot["live"] += 1
            else:
                slot["backfill"] += 1
        return out

    def conversions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT c.*, i.family, i.title FROM conversions c"
            " JOIN interrogations i ON i.occ_id = c.occ_id"
            " ORDER BY c.id").fetchall()
        return [dict(r) for r in rows]

    def interrogations(self, state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM interrogations"
        args: tuple = ()
        if state:
            sql += " WHERE state = ?"
            args = (state,)
        sql += " ORDER BY cycle_date, occ_id"
        return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def export_json(self, path: str) -> str:
        payload = {
            "cycles": [dict(r) for r in self._conn.execute(
                "SELECT * FROM cycles ORDER BY cycle_date").fetchall()],
            "interrogations": self.interrogations(),
            "conversions": self.conversions(),
            "exported_at": _utcnow().isoformat(),
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        self._audit("ledger_exported", {"path": path})
        return path