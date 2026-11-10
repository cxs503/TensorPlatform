"""判定台账：每条判定（门级 + 逐判据）落 SQLite，可 SQL 回溯、零手抄。

审计契约：一行 = (mission_id, gate_id, criteria_id, op, threshold, unit, measured,
verdict, code, artifact_path, artifact_sha256, gate_sha256, verdict_digest,
gate_path, run_cmd, decided_utc)。回溯 = 用 artifact_sha256 回盘复算。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import canonical_json, sha256_file

DDL = """
CREATE TABLE IF NOT EXISTS gate_verdicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_utc     TEXT NOT NULL,
    mission_id      INTEGER,
    gate_id         TEXT,
    criteria_id     TEXT NOT NULL,
    op              TEXT,
    threshold       TEXT,
    unit            TEXT,
    measured        TEXT,
    verdict         TEXT NOT NULL,
    code            TEXT,
    artifact_path   TEXT,
    artifact_sha256 TEXT,
    gate_sha256     TEXT,
    verdict_digest  TEXT,
    gate_path       TEXT,
    run_cmd         TEXT
);
CREATE INDEX IF NOT EXISTS ix_gate_verdicts_mission ON gate_verdicts(mission_id);
CREATE INDEX IF NOT EXISTS ix_gate_verdicts_gate ON gate_verdicts(gate_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.executescript(DDL)
    return con


def append_verdict(con: sqlite3.Connection, result: dict, *, run_cmd: str | None = None,
                   decided_utc: str | None = None) -> int:
    """把一次复算判定写入台账；返回写入行数。"""
    stamp = decided_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = result.get("criteria") or []
    payload = [
        (stamp, result.get("mission_id"), result.get("gate_id"), r["id"], r.get("op"),
         canonical_json(r.get("threshold")), r.get("unit"), canonical_json(r.get("measured")),
         r["status"] if r["status"] != "withheld" else "WITHHELD",
         r.get("code"), r.get("artifact"), r.get("sha256"), result.get("gate_sha256"),
         result.get("verdict_digest"), result.get("gate_path"), run_cmd)
        for r in rows
    ]
    payload.append((stamp, result.get("mission_id"), result.get("gate_id"), "*",
                    None, None, None, None, result.get("grade"),
                    (result.get("issues") or [{}])[0].get("code"),
                    None, None, result.get("gate_sha256"), result.get("verdict_digest"),
                    result.get("gate_path"), run_cmd))
    con.executemany(
        "INSERT INTO gate_verdicts (decided_utc, mission_id, gate_id, criteria_id, op,"
        " threshold, unit, measured, verdict, code, artifact_path, artifact_sha256,"
        " gate_sha256, verdict_digest, gate_path, run_cmd)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    con.commit()
    return len(payload)


def rows(con: sqlite3.Connection, *, mission_id: int | None = None,
         limit: int | None = None) -> list[dict]:
    sql = ("SELECT * FROM gate_verdicts"
           + (" WHERE mission_id = ?" if mission_id is not None else "")
           + " ORDER BY id" + (f" LIMIT {int(limit)}" if limit else ""))
    con.row_factory = sqlite3.Row
    args = (mission_id,) if mission_id is not None else ()
    return [dict(r) for r in con.execute(sql, args)]


def trace_back(con: sqlite3.Connection, *, limit: int = 20) -> dict:
    """抽查：台账里的 artifact_sha256 是否与磁盘一致（复算，不信任写入值）。"""
    out: list[dict[str, Any]] = []
    scanned = rows(con, limit=limit)
    for row in scanned:
        art = row.get("artifact_path")
        sha = row.get("artifact_sha256")
        gate_path = row.get("gate_path") or ""
        status = "gate_level" if not art else "missing"
        actual = None
        if art and sha:
            base = Path(gate_path).parent if gate_path.endswith("gate.json") else Path(".")
            candidate = base / art
            if candidate.is_file():
                actual = sha256_file(candidate)
                status = "match" if actual == sha else "mismatch"
        out.append({"id": row["id"], "mission_id": row["mission_id"],
                    "gate_id": row["gate_id"], "criteria_id": row["criteria_id"],
                    "verdict": row["verdict"], "artifact": art, "status": status,
                    "artifact_sha256": sha, "recomputed_sha256": actual,
                    "verdict_digest": row["verdict_digest"]})
    gate_level = [r for r in out if r["status"] == "gate_level"]
    evidence_rows = [r for r in out if r["status"] != "gate_level"]
    traced = sum(1 for r in evidence_rows if r["status"] == "match")
    return {"checked": len(out), "gate_level": len(gate_level),
            "evidence_rows": len(evidence_rows), "traceable": traced,
            "ratio": (traced / len(evidence_rows)) if evidence_rows else 0.0,
            "rows": out}
