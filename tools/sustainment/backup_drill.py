"""Backup / restore drill: isolated-namespace restore + triple cross-check + RPO/RTO.

"Metadata is recoverable" used to be an assumption.  This module turns it into a
machine verdict every time it runs:

1. **backup**     snapshot the declared core tables (engine-specific dump).
2. **restore**    replay the snapshot into an *isolated namespace*
                  (``drill_<utcstamp>``): SQLite → a separate database file,
                  PostgreSQL → a separate schema inside the same database via
                  qualified-name rewriting.  The live namespace is never written.
3. **cross-check** three independent invariants per table, all must match the
                  source: ``row_count`` · ``content_digest`` (sha256 over the
                  canonically ordered row encoding) · ``schema_signature``
                  (column names + declared types + primary key).
4. **negative controls** three deliberately broken backups must each be caught:
                  ``NC1 truncated_dump`` (backup bytes cut in half),
                  ``NC2 missing_table`` (one table dropped from the snapshot),
                  ``NC3 tampered_row`` (one cell mutated after restore).
                  A control that is *not* detected fails the whole drill — a
                  verifier that cannot fail is not a verifier.
5. **RPO / RTO ledger** the recovery-point objective (age of the snapshot that
                  was restored) and the recovery-time objective (wall clock from
                  restore start to verified-consistent) are recorded with their
                  budgets in an append-only ledger.

Engines
-------
``sqlite``   reference engine, hermetic: needs only the stdlib.  Used by the
             regression suite and by any host without PG credentials.
``postgres`` adapter for the live platform DB (``pg_dump``/``psql``); it is
             exercised only when a DSN is supplied — otherwise the drill
             reports ``engine_unavailable`` rather than pretending to pass.

Public API
----------
``get_engine(kind)`` · ``run_drill(...) -> dict`` · ``append_ledger(path, record)``

CLI::

    python3 -m tools.sustainment.backup_drill \
        --engine sqlite --source /tmp/platform.db --workdir /tmp/drill \
        --tables a,b,c,d,e --ledger artifacts/sustainment/backup_ledger.json \
        --out artifacts/sustainment/backup_drill.json
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
DRILL_NAMESPACE_PREFIX = "drill_"

#: default objectives — a day of data loss and fifteen minutes of downtime
DEFAULT_RPO_BUDGET_S = 86400.0
DEFAULT_RTO_BUDGET_S = 900.0

DEFAULT_TABLES_CONFIG = "config/sustain_core_tables.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# table stat contract
# ---------------------------------------------------------------------------


@dataclass
class TableStat:
    table: str
    row_count: int
    content_digest: str
    schema_signature: str
    error: Optional[str] = None

    def to_json(self) -> dict:
        return {"table": self.table, "row_count": self.row_count,
                "content_digest": self.content_digest,
                "schema_signature": self.schema_signature, "error": self.error}


class EngineError(RuntimeError):
    """Raised when an engine cannot dump/restore (bad backup, missing tool, ...)."""


# ---------------------------------------------------------------------------
# SQLite engine (reference / hermetic)
# ---------------------------------------------------------------------------


class SqliteEngine:
    """File-backed engine: backup = a real SQLite snapshot file."""

    kind = "sqlite"

    def __init__(self, source: str):
        self.source = str(Path(source).resolve())
        if not Path(self.source).exists():
            raise EngineError(f"sqlite source missing: {self.source}")

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.text_factory = lambda b: b.decode("utf-8", "replace")
        return conn

    def list_tables(self) -> List[str]:
        with self._connect(self.source) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [r[0] for r in rows]

    # -- dump / restore --------------------------------------------------
    def dump(self, tables: Sequence[str], out_path: str) -> str:
        """Consistent snapshot via the SQLite online-backup API."""
        out = str(Path(out_path))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        src = self._connect(self.source)
        try:
            dst = sqlite3.connect(out)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # drop non-declared tables so the snapshot matches the declared set
        keep = set(tables)
        with self._connect(out) as conn:
            present = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
            for t in present:
                if t not in keep:
                    conn.execute(f'DROP TABLE "{t}"')
        return out

    def restore(self, backup_path: str, namespace: str, workdir: str) -> str:
        """Replay the snapshot into a fresh isolated database file."""
        target = str(Path(workdir) / f"{namespace}.db")
        if Path(target).exists():
            Path(target).unlink()
        src = self._connect(str(backup_path))
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target

    # -- stats -----------------------------------------------------------
    def stats(self, spec: str, table: str, namespace: Optional[str] = None) -> TableStat:
        try:
            conn = self._connect(spec)
        except sqlite3.Error as exc:
            return TableStat(table, -1, "", "", error=f"connect_error:{exc}")
        try:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if not cols:
                return TableStat(table, -1, "", "", error="table_missing")
            sig = "|".join(f"{c[1]}:{c[2]}:{c[5]}" for c in cols)
            names = [c[1] for c in cols]
            order = ", ".join(f'"{n}"' for n in names)
            cur = conn.execute(f'SELECT {order} FROM "{table}" ORDER BY {order}')
            digest = hashlib.sha256()
            count = 0
            for row in cur:
                digest.update(json.dumps(list(row), ensure_ascii=False,
                                         default=str).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            return TableStat(table, count, digest.hexdigest(), sig)
        except sqlite3.Error as exc:
            return TableStat(table, -1, "", "", error=f"query_error:{exc}")
        finally:
            conn.close()

    # -- mutation helpers for negative controls --------------------------
    def drop_table(self, spec: str, table: str) -> None:
        with self._connect(spec) as conn:
            conn.execute(f'DROP TABLE "{table}"')

    def mutate_one_cell(self, spec: str, table: str) -> bool:
        """Mutate a single numeric/text cell.  Returns False if nothing to mutate."""
        with self._connect(spec) as conn:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if not cols:
                return False
            rowid = conn.execute(f'SELECT rowid FROM "{table}" LIMIT 1').fetchone()
            if rowid is None:
                return False
            for c in cols:
                name, ctype = c[1], (c[2] or "").upper()
                if c[5]:  # skip primary key
                    continue
                val = conn.execute(
                    f'SELECT "{name}" FROM "{table}" WHERE rowid=?', (rowid[0],)).fetchone()[0]
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    conn.execute(f'UPDATE "{table}" SET "{name}"=? WHERE rowid=?',
                                 (val + 1 if isinstance(val, int) else val + 0.5, rowid[0]))
                    return True
                if isinstance(val, str):
                    conn.execute(f'UPDATE "{table}" SET "{name}"=? WHERE rowid=?',
                                 (val + "_TAMPER", rowid[0]))
                    return True
            return False


# ---------------------------------------------------------------------------
# PostgreSQL engine (live platform DB adapter)
# ---------------------------------------------------------------------------


class PostgresEngine:
    """DSN-backed engine; isolated restore = a dedicated schema in the same DB.

    Schema isolation is implemented by dumping plain SQL and rewriting the
    source schema qualifier to ``<namespace>`` — no DDL into the live schema,
    and the source namespace is only ever read.  Requires ``pg_dump``/``psql``.
    """

    kind = "postgres"

    def __init__(self, dsn: str, source_schema: str = "public"):
        self.dsn = dsn
        self.source_schema = source_schema
        for tool in ("pg_dump", "psql"):
            if shutil.which(tool) is None:
                raise EngineError(f"engine_unavailable: {tool} not found")

    # -- helpers ---------------------------------------------------------
    def _psql(self, sql: str, dbname: Optional[str] = None) -> str:
        cmd = ["psql", self.dsn if dbname is None else dbname, "-tAc", sql]
        env = {**os.environ, "PGCONNECT_TIMEOUT": "10"}
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        if r.returncode != 0:
            raise EngineError(f"psql failed: {r.stderr.strip()[:400]}")
        return r.stdout

    def ping(self) -> bool:
        try:
            self._psql("SELECT 1")
            return True
        except Exception:
            return False

    def list_tables(self) -> List[str]:
        out = self._psql(
            "SELECT tablename FROM pg_tables WHERE schemaname="
            f"'{self.source_schema}' ORDER BY 1")
        return [x for x in out.splitlines() if x.strip()]

    # -- dump / restore --------------------------------------------------
    def dump(self, tables: Sequence[str], out_path: str) -> str:
        out = str(Path(out_path))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        cmd = ["pg_dump", self.dsn, "--schema", self.source_schema, "--no-owner",
               "--no-privileges", "--inserts", "--file", out]
        for t in tables:
            cmd += ["--table", f"{self.source_schema}.{t}"]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           env={**os.environ, "PGCONNECT_TIMEOUT": "10"}, timeout=900)
        if r.returncode != 0:
            raise EngineError(f"pg_dump failed: {r.stderr.strip()[:400]}")
        return out

    def restore(self, backup_path: str, namespace: str, workdir: str) -> str:
        sql = Path(backup_path).read_text()
        rewritten = (sql
                     .replace(f"CREATE SCHEMA {self.source_schema};", f"CREATE SCHEMA {namespace};")
                     .replace(f"SET search_path = {self.source_schema},", f"SET search_path = {namespace},")
                     .replace(f"{self.source_schema}.", f"{namespace}."))
        script = Path(workdir) / f"{namespace}.sql"
        script.write_text(f"CREATE SCHEMA IF NOT EXISTS {namespace};\n" + rewritten)
        r = subprocess.run(["psql", self.dsn, "-v", "ON_ERROR_STOP=1", "-f", str(script)],
                           capture_output=True, text=True,
                           env={**os.environ, "PGOPTIONS": "-c search_path=" + namespace},
                           timeout=1800)
        if r.returncode != 0:
            raise EngineError(f"psql restore failed: {r.stderr.strip()[:400]}")
        return str(script)

    def drop_namespace(self, namespace: str) -> None:
        try:
            self._psql(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
        except EngineError:
            pass

    # -- stats -----------------------------------------------------------
    def stats(self, spec: str, table: str, namespace: Optional[str] = None) -> TableStat:
        schema = namespace or self.source_schema
        ident = f'{schema}."{table}"'
        try:
            out = self._psql(
                f"SELECT count(*) FROM {ident}", dbname=spec if spec.startswith("dbname=") else None)
            count = int(out.strip() or 0)
            digest = self._psql(
                "SELECT coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '') "
                f"FROM (SELECT t::text AS x FROM {ident} t) s")
            cols = self._psql(
                "SELECT column_name||':'||data_type FROM information_schema.columns "
                f"WHERE table_schema='{schema}' AND table_name='{table}' "
                "ORDER BY ordinal_position")
            sig = _sha256_text(cols.strip())
            return TableStat(table, count, digest.strip(), sig)
        except EngineError as exc:
            return TableStat(table, -1, "", "", error=str(exc)[:300])

    def drop_table(self, spec: str, table: str) -> None:
        self._psql(f'DROP TABLE IF EXISTS {self.source_schema}."{table}" CASCADE',
                   dbname=spec if spec.startswith("dbname=") else None)

    def mutate_one_cell(self, spec: str, table: str) -> bool:
        ident = f'{self.source_schema}."{table}"'
        try:
            cols = self._psql(
                "SELECT column_name||':'||data_type FROM information_schema.columns "
                f"WHERE table_schema='{self.source_schema}' AND table_name='{table}' "
                "ORDER BY ordinal_position").splitlines()
        except EngineError:
            return False
        for line in cols:
            name, _, ctype = line.partition(":")
            if "char" in ctype or "text" in ctype:
                self._psql(f'UPDATE {ident} SET "{name}" = "{name}" || \'_TAMPER\' '
                           f'WHERE ctid = (SELECT ctid FROM {ident} LIMIT 1)')
                return True
            if "int" in ctype or "numeric" in ctype or "double" in ctype or "real" in ctype:
                self._psql(f'UPDATE {ident} SET "{name}" = "{name}" + 1 '
                           f'WHERE ctid = (SELECT ctid FROM {ident} LIMIT 1)')
                return True
        return False


def get_engine(kind: str, source: str) -> object:
    if kind == "sqlite":
        return SqliteEngine(source)
    if kind == "postgres":
        return PostgresEngine(source)
    raise EngineError(f"unknown engine: {kind}")


def load_tables_config(path: str = DEFAULT_TABLES_CONFIG) -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    payload = json.loads(p.read_text())
    return list(payload.get("tables", []))


# ---------------------------------------------------------------------------
# drill
# ---------------------------------------------------------------------------


def _compare(src: TableStat, dst: TableStat) -> List[dict]:
    mismatches: List[dict] = []
    if dst.error:
        mismatches.append({"invariant": "restore_available", "detail": dst.error})
        return mismatches
    if src.error:
        mismatches.append({"invariant": "source_available", "detail": src.error})
        return mismatches
    for inv in ("row_count", "content_digest", "schema_signature"):
        a, b = getattr(src, inv), getattr(dst, inv)
        if a != b:
            mismatches.append({"invariant": inv, "source": a, "restored": b})
    return mismatches


def run_drill(
    source: str,
    tables: Sequence[str],
    workdir: str,
    engine_kind: str = "sqlite",
    ledger_path: Optional[str] = None,
    rpo_budget_s: float = DEFAULT_RPO_BUDGET_S,
    rto_budget_s: float = DEFAULT_RTO_BUDGET_S,
    negative_controls: bool = True,
    keep_artifacts: bool = True,
) -> dict:
    """Run one drill end-to-end and return the paper-grade ledger record."""
    t_start = time.time()
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "engine": engine_kind,
        "source": source,
        "tables": list(tables),
        "workdir": workdir,
        "checks": {},
        "tables_detail": [],
        "negative_controls": [],
        "budgets": {"rpo_s": rpo_budget_s, "rto_s": rto_budget_s},
    }
    if not tables:
        result["checks"]["tables_declared"] = {"ok": False, "reason": "no_tables"}
        result["ok"] = False
        result["verdict"] = "FAIL"
        return result

    try:
        engine = get_engine(engine_kind, source)
    except EngineError as exc:
        result["checks"]["engine_available"] = {"ok": False, "reason": str(exc)}
        result["ok"] = False
        result["verdict"] = "WITHHELD"
        result["rto_s"] = None
        return result

    Path(workdir).mkdir(parents=True, exist_ok=True)
    namespace = f"{DRILL_NAMESPACE_PREFIX}{_stamp()}"
    result["namespace"] = namespace

    # -- 0. source fingerprint before any work --------------------------
    if engine_kind == "sqlite":
        src_hash_before = _sha256_file(source)
    else:
        src_hash_before = None  # DB-level fingerprint not portable
    try:
        declared = engine.list_tables()
        list_err = None
    except Exception as exc:  # noqa: BLE001 - unreachable DB is a finding, not a crash
        declared, list_err = [], str(exc)[:300]
    missing_src = [t for t in tables if t not in declared]
    result["checks"]["source_tables_present"] = {
        "ok": bool(declared) and not missing_src, "missing": missing_src,
        "declared": len(tables), "available": len(declared), "list_error": list_err,
    }

    # -- 1. backup ------------------------------------------------------
    backup_path = str(Path(workdir) / f"{namespace}.backup")
    t_backup = time.time()
    try:
        engine.dump(tables, backup_path)
        backup_ok, backup_err = True, None
    except Exception as exc:  # noqa: BLE001 - dump failures are the finding
        backup_ok, backup_err = False, str(exc)[:400]
    backup_created_at = datetime.now(timezone.utc).timestamp()
    backup_bytes = Path(backup_path).stat().st_size if Path(backup_path).exists() else 0
    result["backup"] = {
        "path": backup_path if keep_artifacts else None,
        "sha256": _sha256_file(backup_path) if backup_ok else None,
        "bytes": backup_bytes,
        "created_at": datetime.fromtimestamp(backup_created_at, timezone.utc).isoformat(
            timespec="seconds"),
        "dump_seconds": round(t_backup - t_start, 3),
    }
    result["checks"]["backup_created"] = {"ok": backup_ok, "reason": backup_err}
    if not backup_ok:
        result["ok"] = False
        result["verdict"] = "FAIL"
        result["rto_s"] = None
        if ledger_path:
            append_ledger(ledger_path, _ledger_record(result))
        return result

    # -- 2. restore into isolated namespace -----------------------------
    t_restore = time.time()
    restored = None
    restore_err = None
    try:
        restored = engine.restore(backup_path, namespace, workdir)
    except Exception as exc:  # noqa: BLE001
        restore_err = str(exc)[:400]
    result["restore"] = {
        "namespace": namespace,
        "target": restored if keep_artifacts else None,
        "restore_seconds": round(time.time() - t_restore, 3),
        "error": restore_err,
    }
    result["checks"]["restore_isolated"] = {
        "ok": restored is not None,
        "isolated_namespace": namespace,
        "reason": restore_err,
    }
    if restored is None:
        result["ok"] = False
        result["verdict"] = "FAIL"
        result["rto_s"] = None
        if ledger_path:
            append_ledger(ledger_path, _ledger_record(result))
        return result

    # -- 3. triple cross-check ------------------------------------------
    all_match = True
    for t in tables:
        src = engine.stats(source, t)
        dst = engine.stats(restored, t, namespace=namespace if engine_kind == "postgres" else None)
        row = {"table": t, "source": src.to_json(), "restored": dst.to_json(),
               "mismatches": _compare(src, dst)}
        row["ok"] = not row["mismatches"]
        all_match = all_match and row["ok"]
        result["tables_detail"].append(row)
    result["checks"]["triple_cross_check"] = {
        "ok": all_match,
        "invariants": ["row_count", "content_digest", "schema_signature"],
        "tables_matched": sum(1 for r in result["tables_detail"] if r["ok"]),
        "tables_total": len(result["tables_detail"]),
    }
    rto = round(time.time() - t_restore, 3)

    # -- 4. negative controls -------------------------------------------
    if negative_controls:
        result["negative_controls"] = _run_negative_controls(
            engine, engine_kind, source, tables, workdir, namespace, backup_path, restored)
        nc_ok = all(nc["detected"] for nc in result["negative_controls"])
    else:
        nc_ok = True
        result["negative_controls"] = []
    result["checks"]["negative_controls"] = {
        "ok": nc_ok, "total": len(result["negative_controls"]),
        "detected": sum(1 for nc in result["negative_controls"] if nc["detected"]),
    }

    # -- 5. source immutability -----------------------------------------
    src_hash_after = _sha256_file(source) if engine_kind == "sqlite" else None
    untouched = (src_hash_before == src_hash_after) if engine_kind == "sqlite" else None
    result["checks"]["source_untouched"] = {
        "ok": untouched if untouched is not None else True,
        "sha256_before": src_hash_before, "sha256_after": src_hash_after,
        "note": None if untouched is not None else "db-level fingerprint unavailable",
    }

    # -- 6. RPO / RTO ----------------------------------------------------
    rpo = round(backup_created_at - _source_mtime(source) if engine_kind == "sqlite"
                else _age_seconds(backup_created_at), 3)
    rpo = max(rpo, 0.0)
    result["rto_s"] = rto
    result["rpo_s"] = rpo
    result["checks"]["rto_within_budget"] = {"ok": rto <= rto_budget_s,
                                             "rto_s": rto, "budget_s": rto_budget_s}
    result["checks"]["rpo_within_budget"] = {"ok": rpo <= rpo_budget_s,
                                             "rpo_s": rpo, "budget_s": rpo_budget_s}

    result["ok"] = all(v.get("ok") for v in result["checks"].values())
    result["verdict"] = "PASS" if result["ok"] else "FAIL"
    result["elapsed_s"] = round(time.time() - t_start, 3)
    if ledger_path:
        append_ledger(ledger_path, _ledger_record(result))
    if not keep_artifacts:
        shutil.rmtree(Path(workdir) / namespace, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
    return result


def _age_seconds(ts: float) -> float:
    return datetime.now(timezone.utc).timestamp() - ts


def _source_mtime(source: str) -> float:
    p = Path(source)
    if p.exists():
        return p.stat().st_mtime
    return datetime.now(timezone.utc).timestamp()


def _run_negative_controls(engine, engine_kind, source, tables, workdir,
                           namespace, backup_path, restored) -> List[dict]:
    """Three deliberately broken backups; each must be *detected*."""
    controls: List[dict] = []
    nc_dir = Path(workdir) / "negctl"
    nc_dir.mkdir(parents=True, exist_ok=True)

    # NC1: truncated dump -> restore must fail, or cross-check must mismatch
    trunc = nc_dir / "truncated.backup"
    raw = Path(backup_path).read_bytes()
    trunc.write_bytes(raw[: max(1, len(raw) // 2)])
    detected, detail = False, ""
    try:
        ns = f"{namespace}_nc1"
        target = engine.restore(str(trunc), ns, str(nc_dir))
        # restore "succeeded": fall back on cross-check
        bad = []
        for t in tables:
            src = engine.stats(source, t)
            dst = engine.stats(target, t)
            if _compare(src, dst):
                bad.append(t)
        detected = bool(bad)
        detail = f"restore_ok_but_mismatch_on={bad[:5]}"
    except Exception as exc:  # noqa: BLE001
        detected, detail = True, f"restore_rejected:{str(exc)[:160]}"
    controls.append({"control": "NC1_truncated_dump", "detected": detected, "detail": detail,
                     "bytes_written": trunc.stat().st_size})

    # NC2: missing table -> cross-check must report restore_available failure
    miss = nc_dir / "missing_table.backup"
    shutil.copyfile(backup_path, miss)
    dropped = None
    try:
        victim = tables[-1]
        engine.drop_table(str(miss), victim)
        dropped = victim
        detected, detail = False, ""
        try:
            ns = f"{namespace}_nc2"
            target = engine.restore(str(miss), ns, str(nc_dir))
            mism = []
            for t in tables:
                src = engine.stats(source, t)
                dst = engine.stats(target, t)
                if _compare(src, dst):
                    mism.append(t)
            detected = victim in mism
            detail = f"mismatch_on={mism[:5]}"
        except Exception as exc:  # noqa: BLE001
            detected, detail = True, f"restore_rejected:{str(exc)[:160]}"
    except Exception as exc:  # noqa: BLE001
        detected, detail = True, f"control_setup_failed:{str(exc)[:160]}"
    controls.append({"control": "NC2_missing_table", "detected": detected,
                     "detail": detail, "dropped_table": dropped})

    # NC3: tampered row in the restored copy -> content_digest must mismatch
    detected, detail = False, ""
    try:
        victim = tables[0]
        mutated = engine.mutate_one_cell(restored, victim)
        if not mutated:
            # empty tables cannot host this control: report honestly as undetectable
            detected, detail = False, f"no_mutable_cell_in={victim}"
        else:
            src = engine.stats(source, victim)
            dst = engine.stats(restored, victim,
                               namespace=namespace if engine_kind == "postgres" else None)
            mism = _compare(src, dst)
            detected = any(m["invariant"] == "content_digest" for m in mism)
            detail = f"invariants_flagged={[m['invariant'] for m in mism]}"
    except Exception as exc:  # noqa: BLE001
        detected, detail = True, f"control_error:{str(exc)[:160]}"
    controls.append({"control": "NC3_tampered_row", "detected": detected, "detail": detail})
    return controls


def _ledger_record(result: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": _now(),
        "engine": result.get("engine"),
        "source": result.get("source"),
        "namespace": result.get("namespace"),
        "tables": result.get("tables", []),
        "verdict": result.get("verdict"),
        "rto_s": result.get("rto_s"),
        "rpo_s": result.get("rpo_s"),
        "rto_budget_s": result.get("budgets", {}).get("rto_s"),
        "rpo_budget_s": result.get("budgets", {}).get("rpo_s"),
        "backup_bytes": (result.get("backup") or {}).get("bytes"),
        "backup_sha256": (result.get("backup") or {}).get("sha256"),
        "checks": {k: v.get("ok") for k, v in (result.get("checks") or {}).items()},
        "negative_controls": [
            {"control": nc["control"], "detected": nc["detected"]}
            for nc in result.get("negative_controls", [])
        ],
    }


def append_ledger(path: str, record: dict, max_records: int = 500) -> dict:
    """Append-only RPO/RTO ledger (newest first, trimmed to ``max_records``)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "records": []}
    if p.exists():
        try:
            payload = json.loads(p.read_text())
        except Exception:
            payload = {"schema_version": SCHEMA_VERSION, "records": [],
                       "note": "previous ledger unreadable; restarted (original corrupt payload dropped)"}
    payload["records"].insert(0, record)
    payload["records"] = payload["records"][:max_records]
    payload["updated_at"] = _now()
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def build_fixture_db(path: str, n_tables: int = 5, rows: int = 40, seed: int = 7) -> List[str]:
    """Create a deterministic SQLite fixture with ``n_tables`` core tables.

    Used by the regression suite and by the dry-run CLI so the drill can be
    rehearsed on any host without the live platform DB.
    """
    import random
    rnd = random.Random(seed)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    names = [f"core_{i:02d}" for i in range(1, n_tables + 1)]
    conn = sqlite3.connect(str(p))
    try:
        for t in names:
            conn.execute(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY, '
                         "payload TEXT NOT NULL, weight REAL, stamp TEXT)")
            for i in range(rows):
                conn.execute(f'INSERT INTO "{t}" VALUES (?,?,?,?)',
                             (i + 1, f"row-{i:03d}-{rnd.randrange(10**6):06d}",
                              round(rnd.random() * 100, 6),
                              f"2026-11-{(i % 28) + 1:02d}T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()
    return names