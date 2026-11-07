#!/usr/bin/env python3
"""Immutable evidence snapshots for the chronicle report line (IN18-T3).

Why this exists
---------------
The paper production line lost two drafts (W2 verdict) to the same failure:
numbers in a manuscript that could not be traced back to any artifact, and
figures whose data was synthesised inside the plotting script. Text-level gates
(``tools/paper_gate/traceability_gate.py`` PG1-PG8) close the *prose* hole;
``figure_provenance.py`` (PG9) closes the *plot call* hole by executing the
generator.

This module closes the third hole: the **data itself had no frozen state to be
rechecked against**. A gate can only say "this number came from query Q" if Q
is re-runnable and the underlying rows cannot move under it. So:

    artifact JSON  --ingest-->  sqlite snapshot  --sha256-->  snapshot_hash

* the snapshot is a real SQLite database built from a real platform artifact
  (``artifacts/capability_ledger/capability_ledger_resolved.json`` by default);
* ``snapshot_hash`` is the SHA-256 of the database file bytes, recorded in
  ``manifest.json`` together with the SHA-256 of the source artifact;
* every provenance record pins ``(sql, params, snapshot_hash, snapshot_ts)``,
  so the verifier can (a) prove the snapshot was not edited after the fact and
  (b) re-execute the exact query and compare the recomputed value.

Honest limits (declared, not silent)
------------------------------------
* A snapshot proves *what the numbers were*, not that the platform behind the
  artifact was correct. Evidence grading (REAL/MOCK/STUB) is inherited from the
  ledger and is not re-adjudicated here.
* SQLite file hashing detects content change, not authorship; it is tamper
  *evidence*, not tamper *proof* (no signing key is held in the worktree).
* Only the ingest of the capability ledger is implemented. Other artifacts can
  be snapshotted by adding an ingester; none is claimed.

Usage
-----
    python3 -m tools.chronicle.report.snapshot --source <artifact.json> \
        --out artifacts/chronicle_report/snapshot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "chronicle-snapshot/1"
DB_NAME = "report.sqlite"
MANIFEST_NAME = "manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = REPO_ROOT / "artifacts/capability_ledger/capability_ledger_resolved.json"

DDL = """
CREATE TABLE capability (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    claimed_level TEXT,
    reconciled_level TEXT,
    divergence TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    latest_evidence_report TEXT,
    latest_evidence_as_of TEXT,
    basis TEXT
);
CREATE TABLE evidence (
    capability_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    stance TEXT,
    asserted_level TEXT,
    line_sha256 TEXT
);
CREATE VIEW capability_evidence AS
    SELECT c.id, c.domain, c.claimed_level, c.reconciled_level, c.divergence,
           c.evidence_count, c.latest_evidence_report, c.latest_evidence_as_of,
           e.report_id, e.stance, e.asserted_level, e.line_sha256
    FROM capability c LEFT JOIN evidence e ON e.capability_id = c.id;
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Snapshot:
    """A frozen, hash-anchored evidence database."""

    root: Path
    manifest: dict

    # -- properties ---------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.root / DB_NAME

    @property
    def snapshot_hash(self) -> str:
        return self.manifest["snapshot_hash"]

    @property
    def created_ts(self) -> str:
        return self.manifest["created_ts"]

    @property
    def source(self) -> dict:
        return self.manifest["source"]

    # -- access ------------------------------------------------------------
    @classmethod
    def load(cls, root: str | Path) -> "Snapshot":
        root = Path(root)
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        return cls(root=root, manifest=manifest)

    def connect(self) -> sqlite3.Connection:
        """Open the snapshot read-only; the URI mode=ro makes writes impossible
        at the connection level (a gate that could mutate its own evidence is
        not a gate)."""
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        con = self.connect()
        try:
            rows = con.execute(sql, list(params)).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> float:
        rows = self.query(sql, params)
        if len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError(f"scalar query must return exactly 1 row x 1 column, got {len(rows)}")
        return float(list(rows[0].values())[0])

    # -- integrity ---------------------------------------------------------
    def integrity(self) -> dict:
        """Recompute both hashes. Any mismatch is FAIL evidence, never a WARN."""
        findings: list[dict] = []
        db_sha = sha256_file(self.db_path)
        if db_sha != self.manifest["snapshot_hash"]:
            findings.append(
                {
                    "code": "SNAP_HASH_MISMATCH",
                    "severity": "FAIL",
                    "detail": f"sqlite sha256 {db_sha[:16]} != manifest {self.manifest['snapshot_hash'][:16]}",
                }
            )
        src = Path(self.source["path"])
        if src.exists():
            src_sha = sha256_file(src)
            if src_sha != self.source["sha256"]:
                findings.append(
                    {
                        "code": "SNAP_SOURCE_DRIFT",
                        "severity": "FAIL",
                        "detail": f"source artifact {src} changed after snapshot",
                    }
                )
        else:
            findings.append(
                {
                    "code": "SNAP_SOURCE_MISSING",
                    "severity": "WARN",
                    "detail": f"source artifact {src} not on disk; snapshot still authoritative",
                }
            )
        return {
            "snapshot_hash": db_sha,
            "declared_hash": self.manifest["snapshot_hash"],
            "created_ts": self.created_ts,
            "ok": not any(f["severity"] == "FAIL" for f in findings),
            "findings": findings,
        }


def ingest_capability_ledger(payload: dict, con: sqlite3.Connection) -> dict:
    """Real ingest: capability ledger -> rows. No value is invented here."""
    caps = payload.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise ValueError("capability ledger has no capabilities[] to snapshot")
    n_ev = 0
    for cap in caps:
        con.execute(
            "INSERT INTO capability (id, domain, claimed_level, reconciled_level, divergence,"
            " evidence_count, latest_evidence_report, latest_evidence_as_of, basis)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                cap["id"],
                cap.get("domain") or "unknown",
                cap.get("claimed_level"),
                cap.get("reconciled_level"),
                cap.get("divergence"),
                int(cap.get("evidence_count") or len(cap.get("evidence_refs") or [])),
                cap.get("latest_evidence_report"),
                cap.get("latest_evidence_as_of"),
                cap.get("basis"),
            ),
        )
        for ref in cap.get("evidence_refs") or []:
            con.execute(
                "INSERT INTO evidence (capability_id, report_id, stance, asserted_level, line_sha256)"
                " VALUES (?,?,?,?,?)",
                (
                    cap["id"],
                    str(ref.get("report_id", "")),
                    ref.get("stance"),
                    ref.get("asserted_level"),
                    ref.get("line_sha256"),
                ),
            )
            n_ev += 1
    return {"capability_rows": len(caps), "evidence_rows": n_ev}


def build_snapshot(
    source: str | Path = DEFAULT_SOURCE,
    out_dir: str | Path = REPO_ROOT / "artifacts/chronicle_report/snapshot",
    ts: str | None = None,
) -> Snapshot:
    """Build (or rebuild) a snapshot directory. Deterministic given the source."""
    source = Path(source).resolve()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / DB_NAME
    if db_path.exists():
        db_path.unlink()

    payload = json.loads(source.read_text(encoding="utf-8"))
    con = sqlite3.connect(db_path)
    try:
        con.executescript(DDL)
        counts = ingest_capability_ledger(payload, con)
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()

    created = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_ts": created,
        "source": {"path": str(source), "sha256": sha256_file(source)},
        "db": {"file": DB_NAME, "bytes": db_path.stat().st_size},
        "rowcounts": counts,
        "snapshot_hash": sha256_file(db_path),
        "tables": ["capability", "evidence", "capability_evidence"],
        "ingester": "ingest_capability_ledger",
    }
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return Snapshot(root=out, manifest=manifest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="build an immutable evidence snapshot")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts/chronicle_report/snapshot"))
    ap.add_argument("--ts", default=None, help="override created_ts (ISO-8601, tests only)")
    args = ap.parse_args(argv)
    snap = build_snapshot(args.source, args.out, args.ts)
    print(json.dumps({"snapshot": str(snap.root), **snap.integrity()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())