#!/usr/bin/env python3
"""Independent recompute of the SR metrics directly in SQL (IN27-H7 audit chain).

The instrument (``tools/autonomy/sr_dashboard.py``) computes SR1..SR4 in Python
from a handful of feeds.  This script re-derives the countable ones **inside
PostgreSQL** for exactly the sample the artifact audited, so a mismatch would
expose an arithmetic or matching bug:

* SR3: per-check pass counts over ``audited_ids`` (six checks, one SQL round-trip).
* SR2: streak days and hand-typed decision count (interval arithmetic in SQL).
* SR4: routine hand-typed decisions in the 7-day window.
* SR1: disposition hits per ``would_do`` target inside its look-ahead window.

Usage::

    TP_DSN="dbname=tensorplatform user=postgres host=/var/run/postgresql" \
    python3 scripts/verify_sr_metrics.py \
        --artifact artifacts/sr/sr_baseline.json \
        --out artifacts/sr/sr_verify.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

#: Same regexes the Python auditor uses (passed as parameters, never inlined: a
#: literal ``%`` in a psycopg statement would be parsed as a placeholder).
DELIVERABLE_RE = r"(docs/|artifacts/|runs/|/tmp/|\.(md|json|png|csv|npz|yaml|yml)\M|#[0-9]{2,})"
COMMIT_RE = r"^[0-9a-f]{7,40}$"
QUANTITY_RE = r"[0-9]+(\.[0-9]+)?\s*(%|％|mm|kg|MPa|deg|°|m/s|s|m)($|[^0-9a-zA-Z])"

SR3_SQL = """
SELECT
  count(*) AS audited,
  count(*) FILTER (WHERE length(coalesce(evidence->'summary'->>'headline', evidence->>'headline','')) >= 30) AS headline_ok,
  count(*) FILTER (WHERE length(coalesce(evidence->'summary'->>'checks', evidence->>'checks','')) >= 20) AS verification_ok,
  count(*) FILTER (WHERE evidence::text ~ %(deliverable_re)s OR lower(coalesce(evidence->>'head_commit','')) ~ %(commit_re)s) AS deliverable_ok,
  count(*) FILTER (WHERE evidence::text ~ %(quantity_re)s) AS numbers_ok,
  count(*) FILTER (WHERE a.mission_id IS NOT NULL AND coalesce(a.decision_note,'') <> '') AS traceable,
  count(*) FILTER (WHERE a.status = 'approved' AND coalesce(m.status,'') IN ('succeeded','accepted','done','merged')) AS outcome_consistent,
  count(*) FILTER (
    WHERE length(coalesce(evidence->'summary'->>'headline', evidence->>'headline','')) >= 30
      AND length(coalesce(evidence->'summary'->>'checks', evidence->>'checks','')) >= 20
      AND (evidence::text ~ %(deliverable_re)s OR lower(coalesce(evidence->>'head_commit','')) ~ %(commit_re)s)
      AND evidence::text ~ %(quantity_re)s
      AND a.mission_id IS NOT NULL AND coalesce(a.decision_note,'') <> ''
      AND a.status = 'approved' AND coalesce(m.status,'') IN ('succeeded','accepted','done','merged')
  ) AS passed
FROM tensor_approval_requests a
LEFT JOIN tensor_dev_missions m ON m.id = a.mission_id
WHERE a.id = ANY(%(ids)s)
"""

MANUAL_SQL = """
SELECT count(*) AS n, max(created_at) AS last_ts
FROM tensor_agent_session_events
WHERE kind = 'approval_decided'
  AND coalesce(payload->>'note','') <> ''
  AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]')
  AND created_at >= %(since)s AND created_at < %(until)s
"""

MANUAL_KINDS_SQL = """
SELECT coalesce(payload->>'kind','') AS kind, count(*) AS n
FROM tensor_agent_session_events
WHERE kind = 'approval_decided'
  AND coalesce(payload->>'note','') <> ''
  AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]')
  AND created_at >= %(since)s AND created_at < %(until)s
GROUP BY 1 ORDER BY 2 DESC
"""

SHADOW_SQL = "SELECT id, ts, verdict->'would_do' AS would_do FROM coordinator_shadow WHERE ts >= %(since)s AND ts < %(until)s"

HIT_SQL = """
SELECT
 (SELECT count(*) FROM tensor_dev_missions WHERE created_at >= %(a)s AND created_at < %(b)s AND goal ILIKE %(p)s)
+(SELECT count(*) FROM campaign_events WHERE event_type='mission_dispatched' AND created_at >= %(a)s AND created_at < %(b)s AND detail::text ILIKE %(p)s)
+(SELECT count(*) FROM campaign_events WHERE event_type='escalation_created' AND created_at >= %(a)s AND created_at < %(b)s AND detail::text ILIKE %(p)s)
+(SELECT count(*) FROM alerts WHERE created_at >= %(a)s AND created_at < %(b)s
   AND (coalesce(title,'') ILIKE %(p)s OR coalesce(message,'') ILIKE %(p)s OR coalesce(entity_id,'') ILIKE %(p)s OR coalesce(source,'') ILIKE %(p)s))
+(SELECT count(*) FROM tensor_agent_session_events WHERE kind='approval_decided' AND created_at >= %(a)s AND created_at < %(b)s
   AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]') AND coalesce(payload->>'note','') ILIKE %(p)s)
"""


def _connect():
    import psycopg

    dsn = os.environ.get("TP_DSN", "dbname=tensorplatform user=postgres host=/var/run/postgresql")
    return psycopg.connect(dsn, autocommit=True)


def verify(artifact: dict[str, Any]) -> dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    now = datetime.fromisoformat(artifact["generated_at"].replace("Z", "+00:00"))
    m = artifact["metrics"]
    out: dict[str, Any] = {"artifact": "artifacts/sr/sr_baseline.json", "checked_at": artifact["generated_at"], "checks": []}

    # ---- SR3: same sample, recomputed in SQL
    ids = m["SR3"]["audited_ids"]
    if ids:
        cur.execute(
            SR3_SQL,
            {"ids": ids, "deliverable_re": DELIVERABLE_RE, "quantity_re": QUANTITY_RE, "commit_re": COMMIT_RE},
        )
        audited, *counts, passed = cur.fetchone()
        keys = [k for k, _ in (("headline_ok", 0), ("verification_ok", 0), ("deliverable_ok", 0),
                               ("numbers_ok", 0), ("traceable", 0), ("outcome_consistent", 0))]
        sql_view = dict(zip(keys, counts))
        py_view = {k: m["SR3"]["per_check_pass"][k] for k in keys}
        out["checks"].append({
            "metric": "SR3",
            "audited": audited,
            "python_per_check": py_view,
            "sql_per_check": sql_view,
            "python_passed": round(m["SR3"]["value"] * m["SR3"]["sample_size"]),
            "sql_passed": passed,
            "agree_per_check": py_view == sql_view,
            "agree_passed": round(m["SR3"]["value"] * m["SR3"]["sample_size"]) == passed,
        })

    # ---- SR2 / SR4: hand-typed decision stream
    win = artifact["window_days"]
    week = artifact["week_days"]
    cur.execute(MANUAL_SQL, {"since": now - timedelta(days=win), "until": now})
    n_win, last_ts = cur.fetchone()
    streak = round((now - last_ts).total_seconds() / 86400.0, 2) if last_ts else None
    out["checks"].append({
        "metric": "SR2",
        "python_value": m["SR2"]["value"],
        "sql_value": streak,
        "python_count": m["SR2"]["hand_typed_decisions_in_window"],
        "sql_count": n_win,
        "agree_value": m["SR2"]["value"] == streak,
        "agree_count": m["SR2"]["hand_typed_decisions_in_window"] == n_win,
    })
    cur.execute(MANUAL_KINDS_SQL, {"since": now - timedelta(days=week), "until": now})
    kinds = {row[0]: row[1] for row in cur.fetchall()}
    routine = sum(v for k, v in kinds.items() if k == "dev_acceptance")
    out["checks"].append({
        "metric": "SR4",
        "python_value": m["SR4"]["value"],
        "sql_value": round(routine * 7.0 / week, 2),
        "python_non_redline": m["SR4"]["non_redline_decisions"],
        "sql_by_kind": kinds,
        "agree_value": abs((m["SR4"]["value"] or 0) - routine * 7.0 / week) < 1e-6,
    })

    # ---- SR1: re-probe every target straight from SQL
    cur.execute(SHADOW_SQL, {"since": now - timedelta(days=win), "until": now})
    rows = cur.fetchall()
    horizon = artifact["metrics"]["SR1"]["lookahead_hours"]
    items = []
    for _rid, ts, would_do in rows:
        for entry in would_do or ():
            target = str(entry.get("target") or "")
            clean = "".join(ch for ch in target if ch.isalnum() or ch in "_.-")
            if len(clean) < 3:
                items.append({"target": target, "hits": None, "state": "unobservable"})
                continue
            cur.execute(HIT_SQL, {"a": ts, "b": ts + timedelta(hours=horizon), "p": f"%{clean}%"})
            hits = cur.fetchone()[0]
            elapsed = (now - ts).total_seconds() / 3600.0
            if elapsed < horizon:
                state = "pending"
            else:
                expects = entry.get("action") != "monitor"
                state = "matched" if (hits > 0) == expects else "diverged"
            items.append({"action": entry.get("action"), "target": target, "hits": hits, "state": state})
    counts = {k: sum(1 for i in items if i["state"] == k) for k in ("matched", "diverged", "pending", "unobservable")}
    decided = counts["matched"] + counts["diverged"]
    sql_rate = round(counts["diverged"] / decided, 4) if decided else None
    out["checks"].append({
        "metric": "SR1",
        "python_value": m["SR1"]["value"],
        "sql_value": sql_rate,
        "python_counts": m["SR1"]["counts"],
        "sql_counts": counts,
        "items": items,
        "agree": m["SR1"]["value"] == sql_rate,
    })

    conn.close()
    out["all_agree"] = all(
        c.get("agree", c.get("agree_value", c.get("agree_passed", True))) for c in out["checks"]
    )
    for check in out["checks"]:
        if check.get("agree_per_check") is False:
            out["all_agree"] = False
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="independent SQL recompute of the SR metrics")
    parser.add_argument("--artifact", default="artifacts/sr/sr_baseline.json")
    parser.add_argument("--out", default="artifacts/sr/sr_verify.json")
    args = parser.parse_args(argv)
    with open(args.artifact, encoding="utf-8") as handle:
        artifact = json.load(handle)
    result = verify(artifact)
    directory = os.path.dirname(os.path.abspath(args.out))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({c["metric"]: {k: v for k, v in c.items() if k in
                                    ("python_value", "sql_value", "agree", "agree_value", "agree_passed", "agree_per_check")}
                      for c in result["checks"]}, ensure_ascii=False, indent=2))
    print("all_agree:", result["all_agree"])
    return 0 if result["all_agree"] else 1


if __name__ == "__main__":
    sys.exit(main())