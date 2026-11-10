"""Read-only history export for the readiness backtest.

Pulls the dispatch history out of the platform database with the fixed SQL in
``sql/dump_history.sql`` and writes a provenance-stamped JSON snapshot:

* the SQL text itself,
* its sha256,
* the sha256 of the produced snapshot,
* the row counts per status.

The exporter only ever reads. When the database is unreachable it raises with a
precise message instead of silently producing an empty artefact.

Connection is configurable through ``TP_PSQL_CMD`` (defaults to
``runuser -u postgres -- psql -d tensorplatform``).
"""

import hashlib
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_PSQL = "runuser -u postgres -- psql -d tensorplatform"
SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "dump_history.sql")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_sql(sql: str, psql_cmd: Optional[str] = None, timeout: int = 120) -> str:
    cmd = shlex.split(psql_cmd or os.getenv("TP_PSQL_CMD", DEFAULT_PSQL))
    cmd += ["-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"history export SQL failed: {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def export_history(
    out_path: str,
    psql_cmd: Optional[str] = None,
    sql_path: str = SQL_PATH,
) -> Dict[str, Any]:
    """Fetch the history snapshot and persist it with provenance."""
    with open(sql_path, "r", encoding="utf-8") as handle:
        sql = handle.read()
    raw = run_sql(sql, psql_cmd=psql_cmd)
    payload = json.loads(raw)
    nodes: List[Dict[str, Any]] = payload.get("nodes") or []
    missions: List[Dict[str, Any]] = payload.get("missions") or []

    status_counts: Dict[str, int] = {}
    for node in nodes:
        status_counts[node["status"]] = status_counts.get(node["status"], 0) + 1

    snapshot = {
        "schema": "dispatch-readiness-history/1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": {
            "sql_path": os.path.relpath(sql_path),
            "sql_sha256": _sha256(sql),
            "source": "campaign_nodes + tensor_dev_missions (read-only export)",
            "psql_cmd": psql_cmd or os.getenv("TP_PSQL_CMD", DEFAULT_PSQL),
        },
        "counts": {"nodes": len(nodes), "missions": len(missions), "by_status": status_counts},
        "nodes": nodes,
        "missions": missions,
    }
    body = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    snapshot["provenance"]["snapshot_sha256"] = _sha256(body)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    return snapshot


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "artifacts/dispatch_readiness/history_snapshot.json"
    snap = export_history(target)
    print(json.dumps(snap["counts"], ensure_ascii=False))