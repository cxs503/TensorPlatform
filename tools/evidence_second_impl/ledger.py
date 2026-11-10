"""SQLite write-back of the re-grade, with a mandatory backup and rollback drill.

A downgrade is a governance act: it must be auditable (every row traceable to a
model, a decision or a cited evidence line), fast (< 5 s, so it can run inside a
gate), and reversible (backup taken before the write, rollback drilled and
verified by content digest, not by "it looked fine").

All numbers reported by the runner are produced by the SQL queries at the bottom
of this module — the report never re-derives a statistic in Python.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

SCHEMA_VERSION = "evidence-second-impl-ledger/1.0"

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS run_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS capabilities (
        capability_id TEXT PRIMARY KEY,
        graded_grade TEXT NOT NULL,
        regraded_grade TEXT NOT NULL,
        reference_source TEXT NOT NULL,
        independence TEXT NOT NULL,
        verdict TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        self_reference INTEGER NOT NULL,
        self_reference_share REAL NOT NULL,
        verified_locators INTEGER NOT NULL,
        reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS evidence_locators (
        capability_id TEXT NOT NULL,
        role TEXT NOT NULL,              -- 'primary' | 'corroboration'
        path TEXT NOT NULL,
        line INTEGER NOT NULL,
        line_sha256 TEXT NOT NULL,
        verified INTEGER NOT NULL,
        note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS metamorphic (
        handle_id TEXT NOT NULL,
        handle_class TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        residual REAL NOT NULL,
        tolerance REAL NOT NULL,
        metric TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS negative_controls (
        handle_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        mutant TEXT NOT NULL,
        honest_status TEXT NOT NULL,
        mutant_status TEXT NOT NULL,
        discriminating INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS injection_relations (
        handle_id TEXT NOT NULL,
        injection TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        residual REAL NOT NULL,
        tolerance REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS injections (
        handle_id TEXT NOT NULL,
        ledger_proxy_for TEXT,
        injection TEXT NOT NULL,
        detected INTEGER NOT NULL,
        detecting_kinds TEXT NOT NULL,
        max_residual_ratio REAL NOT NULL
    )""",
]

TABLES = ("run_meta", "capabilities", "evidence_locators", "metamorphic",
          "negative_controls", "injections", "injection_relations")


# ---------------------------------------------------------------------------
# basic io
# ---------------------------------------------------------------------------

def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path: Path | str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()


def table_digest(path: Path | str) -> str:
    """Canonical digest of all table contents (order-independent, type-stable)."""
    with connect(path) as conn:
        payload = {}
        for table in TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            payload[table] = sorted(json.dumps(dict(r), sort_keys=True) for r in rows)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def backup(path: Path | str, backup_path: Path | str | None = None) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    bak = Path(backup_path) if backup_path else path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return {"backup_path": str(bak),
            "bytes": bak.stat().st_size,
            "sha256": hashlib.sha256(bak.read_bytes()).hexdigest()}


# ---------------------------------------------------------------------------
# write-back
# ---------------------------------------------------------------------------

def write_back(path: Path | str, *,
               capabilities: Sequence[Mapping[str, Any]],
               handle_results: Sequence[Mapping[str, Any]],
               negative_controls: Sequence[Mapping[str, Any]],
               injections: Sequence[Mapping[str, Any]],
               meta: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Atomically replace the ledger content; backup + timing are returned."""
    path = Path(path)
    init_schema(path)
    pre_digest = table_digest(path)
    bak = backup(path)

    t0 = time.perf_counter()
    with connect(path) as conn:
        for table in ("capabilities", "evidence_locators", "metamorphic",
                      "negative_controls", "injections", "injection_relations"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM run_meta")

        for row in capabilities:
            conn.execute(
                """INSERT INTO capabilities VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (row["capability_id"], row["graded_grade"], row["regraded_grade"],
                 row["reference_source"], row["independence"], row["verdict"],
                 row["reason_code"], int(bool(row["self_reference"])),
                 float(row["self_reference_share"]), int(row.get("verified_locators", 0)),
                 row.get("reason", "")))
            for loc in row.get("locators", []):
                conn.execute(
                    """INSERT INTO evidence_locators VALUES (?,?,?,?,?,?,?)""",
                    (row["capability_id"], "primary", loc["path"], int(loc["line"]),
                     loc["actual_sha256"] or loc["declared_sha256"],
                     int(bool(loc["verified"])), loc.get("note", "")))
            corr = row.get("corroboration")
            if corr:
                conn.execute(
                    """INSERT INTO evidence_locators VALUES (?,?,?,?,?,?,?)""",
                    (row["capability_id"], "corroboration", corr["path"], int(corr["line"]),
                     corr["actual_sha256"] or corr["declared_sha256"],
                     int(bool(corr["verified"])), corr.get("note", "")))

        for verdict in handle_results:
            for res in verdict["results"]:
                if res["status"] == "NOT_APPLICABLE":
                    continue
                conn.execute("""INSERT INTO metamorphic VALUES (?,?,?,?,?,?,?)""",
                             (verdict["handle_id"], verdict["handle_class"], res["kind"],
                              res["status"], float(res["residual"]),
                              float(res["tolerance"]), res["metric"]))

        for nc in negative_controls:
            conn.execute("""INSERT INTO negative_controls VALUES (?,?,?,?,?,?)""",
                         (nc["handle_id"], nc["kind"], nc["mutant"],
                          nc["honest"]["status"], nc["mutant_result"]["status"],
                          int(bool(nc["discriminating"]))))

        for inj in injections:
            for res in inj.get("relation_results") or []:
                conn.execute("""INSERT INTO injection_relations VALUES (?,?,?,?,?,?)""",
                             (inj["handle_id"], inj["injection"], res["kind"], res["status"],
                              float(res["residual"]), float(res["tolerance"])))
            conn.execute("""INSERT INTO injections VALUES (?,?,?,?,?,?)""",
                         (inj["handle_id"], inj.get("ledger_proxy_for", ""),
                          inj["injection"], int(bool(inj["detected"])),
                          ",".join(inj["detecting_kinds"]),
                          float(inj["max_residual_ratio"])))

        for k, v in (meta or {}).items():
            conn.execute("INSERT INTO run_meta VALUES (?,?)", (str(k), json.dumps(v)))
        conn.commit()
    elapsed = time.perf_counter() - t0
    post_digest = table_digest(path)
    return {"elapsed_seconds": round(elapsed, 6),
            "pre_digest": pre_digest,
            "post_digest": post_digest,
            "changed": pre_digest != post_digest,
            "backup": bak}


def rollback_drill(path: Path | str, backup_info: Mapping[str, Any],
                   expected_digest: str) -> Dict[str, Any]:
    """Restore the backup and prove the content digest matches the pre-write state."""
    path = Path(path)
    bak = Path(str(backup_info["backup_path"]))
    t0 = time.perf_counter()
    shutil.copy2(bak, path)
    restored = table_digest(path)
    elapsed = time.perf_counter() - t0
    return {"restored_digest": restored,
            "expected_digest": expected_digest,
            "verified": restored == expected_digest,
            "elapsed_seconds": round(elapsed, 6)}


# ---------------------------------------------------------------------------
# queries (every reported number comes from here)
# ---------------------------------------------------------------------------

def q_grade_counts(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT graded_grade, regraded_grade, COUNT(*) AS n
             FROM capabilities GROUP BY graded_grade, regraded_grade
             ORDER BY graded_grade, regraded_grade"""
    return _rows(path, sql)


def q_downgrades(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT c.capability_id, c.graded_grade, c.regraded_grade, c.reference_source,
                    c.reason_code, l.path AS evidence_path, l.line AS evidence_line,
                    l.line_sha256 AS evidence_line_sha256
             FROM capabilities c
             LEFT JOIN evidence_locators l
               ON l.capability_id = c.capability_id AND l.role = 'primary'
             WHERE c.verdict = 'DOWNGRADE'
             ORDER BY c.capability_id, l.line"""
    return _rows(path, sql)


def q_self_reference(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT capability_id, regraded_grade, reference_source,
                    self_reference, self_reference_share, verdict, reason_code
             FROM capabilities
             WHERE regraded_grade = 'REAL'
             ORDER BY capability_id"""
    return _rows(path, sql)


def q_self_reference_share(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT
               COUNT(*) AS n_real,
               SUM(self_reference) AS n_self_referential,
               ROUND(CAST(SUM(self_reference_share) AS REAL) / MAX(COUNT(*), 1), 6)
                 AS self_reference_share
             FROM capabilities WHERE regraded_grade = 'REAL'"""
    return _rows(path, sql)


def q_injection_rates(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT injection,
                    COUNT(*) AS handles,
                    SUM(detected) AS detected,
                    ROUND(CAST(SUM(detected) AS REAL) / MAX(COUNT(*), 1), 6) AS rate,
                    MAX(max_residual_ratio) AS worst_ratio
             FROM injections GROUP BY injection ORDER BY injection"""
    return _rows(path, sql)


def q_injection_total(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT COUNT(*) AS n, SUM(detected) AS detected,
                    ROUND(CAST(SUM(detected) AS REAL) / MAX(COUNT(*), 1), 6) AS rate
             FROM injections"""
    return _rows(path, sql)


def q_detecting_kinds(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT injection, detecting_kinds, COUNT(*) AS n
             FROM injections WHERE detected = 1
             GROUP BY injection, detecting_kinds ORDER BY injection, n DESC"""
    return _rows(path, sql)


def q_detection_matrix(path: Path | str) -> List[Dict[str, Any]]:
    """relation kind x injection type: how many injected handles each relation caught."""
    sql = """SELECT kind, injection,
                    COUNT(*) AS n_evaluated,
                    SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS n_caught,
                    MAX(CASE WHEN tolerance > 0 THEN residual / tolerance ELSE NULL END)
                      AS worst_ratio
             FROM injection_relations
             GROUP BY kind, injection ORDER BY kind, injection"""
    return _rows(path, sql)


def q_injection_residuals(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT handle_id, injection, detected, max_residual_ratio
             FROM injections ORDER BY injection, handle_id"""
    return _rows(path, sql)


def q_clean_alarms(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT handle_class, COUNT(*) AS n_handles, COUNT(DISTINCT handle_id) AS n_distinct
             FROM metamorphic GROUP BY handle_class ORDER BY handle_class"""
    return _rows(path, sql)


def q_metamorphic_failures(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT handle_id, handle_class, kind, residual, tolerance
             FROM metamorphic WHERE status = 'FAIL' ORDER BY handle_id, kind"""
    return _rows(path, sql)


def q_relation_stats(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT kind,
                    COUNT(*) AS n_applicable,
                    SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS n_pass,
                    MAX(residual / NULLIF(tolerance, 0)) AS worst_ratio
             FROM metamorphic GROUP BY kind ORDER BY kind"""
    return _rows(path, sql)


def q_negative_controls(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT COUNT(*) AS n,
                    SUM(discriminating) AS n_discriminating,
                    SUM(CASE WHEN mutant_status = 'FAIL' THEN 1 ELSE 0 END) AS n_mutant_failed
             FROM negative_controls"""
    return _rows(path, sql)


def q_negative_control_by_kind(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT kind, mutant, COUNT(*) AS n,
                    SUM(CASE WHEN mutant_status = 'FAIL' THEN 1 ELSE 0 END) AS n_failed,
                    SUM(discriminating) AS n_discriminating
             FROM negative_controls GROUP BY kind, mutant ORDER BY kind"""
    return _rows(path, sql)


def q_evidence_lines(path: Path | str) -> List[Dict[str, Any]]:
    sql = """SELECT capability_id, role, path, line, line_sha256, verified
             FROM evidence_locators ORDER BY capability_id, role, line"""
    return _rows(path, sql)


def _rows(path: Path | str, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]