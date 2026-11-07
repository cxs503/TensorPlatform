"""Metric definitions for the autonomy exit dashboard (IN20-R7).

Design rules
------------
1. **Every number comes from one named SQL statement.**  The statement text is
   stored in the emitted artifact so an operator can paste it into ``psql`` and
   reproduce the number byte for byte (``render_for_psql``).
2. **Actor classes are separated at the data layer, not at the prose layer.**
   The platform is driven through two service identities: the interactive
   channel (a human owner or the AI coordinator typing into the console) and the
   unattended daemon channel.  ``interactive_user_ids`` names the former; every
   human-side action source filters on it.
3. **No fabricated numbers.**  A source whose query raises, or that returns no
   row, contributes ``None`` and the metric is reported ``WITHHELD``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------


class Runner(Protocol):  # pragma: no cover - protocol definition
    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None) -> Sequence[Sequence[Any]]:
        ...


class PgRunner:
    """psycopg (v3 or v2) runner.  Connection string comes from the caller."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("TP_DSN", "dbname=tensorplatform user=postgres host=/var/run/postgresql")
        self._conn = None

    @property
    def conn(self):  # pragma: no cover - thin wrapper
        if self._conn is None:
            import psycopg  # local import: keep the module import-light for tests

            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None):
        cur = self.conn.cursor()
        cur.execute(sql, params or None)
        rows = cur.fetchall()
        cur.close()
        return rows

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> bool:
        """Statement without a result set (INSERT/UPDATE).  Returns True on success."""
        cur = self.conn.cursor()
        cur.execute(sql, params or None)
        cur.close()
        return True


# ---------------------------------------------------------------------------
# metric 1 + 2: human-side action sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionSource:
    """One family of human-side actions.

    ``last_sql`` answers *when did this source last fire, ever?* (metric 1).
    ``count_sql`` answers *how many times inside the window?* (metric 2).
    """

    key: str
    actor_class: str  # "owner" | "coordinator"
    label: str
    last_sql: str
    count_sql: str
    table: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "actor_class": self.actor_class,
            "label": self.label,
            "table": self.table,
            "last_sql": self.last_sql,
            "count_sql": self.count_sql,
        }


def _ids_sql(interactive_user_ids: Iterable[int]) -> str:
    ids = sorted({int(i) for i in interactive_user_ids})
    if not ids:
        raise ValueError("interactive_user_ids must not be empty")
    return ",".join(str(i) for i in ids)


def action_sources(interactive_user_ids: Iterable[int]) -> tuple[ActionSource, ...]:
    """Build the human-side action registry for the given interactive identities."""
    ids = _ids_sql(interactive_user_ids)
    return (
        ActionSource(
            key="owner_adjudication",
            actor_class="owner",
            label="owner 裁决（人工审批落槌）",
            table="tensor_approval_requests",
            last_sql=(
                "SELECT max(decided_at) AS last_ts FROM tensor_approval_requests "
                f"WHERE decided_by IN ({ids})"
            ),
            count_sql=(
                "SELECT count(*) AS n FROM tensor_approval_requests "
                f"WHERE decided_by IN ({ids}) "
                "AND decided_at >= %(since)s AND decided_at < %(until)s"
            ),
        ),
        ActionSource(
            key="owner_dispatch",
            actor_class="owner",
            label="owner 派单（交互通道下发任务）",
            table="tensor_dev_missions",
            last_sql=(
                "SELECT max(created_at) AS last_ts FROM tensor_dev_missions "
                f"WHERE created_by IN ({ids}) AND origin = 'owner'"
            ),
            count_sql=(
                "SELECT count(*) AS n FROM tensor_dev_missions "
                f"WHERE created_by IN ({ids}) AND origin = 'owner' "
                "AND created_at >= %(since)s AND created_at < %(until)s"
            ),
        ),
        ActionSource(
            key="coordinator_session",
            actor_class="coordinator",
            label="协调者会话动作（交互通道开新会话）",
            table="tensor_agent_sessions",
            last_sql=(
                "SELECT max(created_at) AS last_ts FROM tensor_agent_sessions "
                f"WHERE started_by IN ({ids})"
            ),
            count_sql=(
                "SELECT count(*) AS n FROM tensor_agent_sessions "
                f"WHERE started_by IN ({ids}) "
                "AND created_at >= %(since)s AND created_at < %(until)s"
            ),
        ),
    )


def human_input_metrics(
    runner: Runner,
    interactive_user_ids: Sequence[int],
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Compute metric 1 (unbroken autonomy) and metric 2 (interventions/day)."""
    params = {"since": since, "until": until}
    rows: dict[str, dict[str, Any]] = {}
    for src in action_sources(interactive_user_ids):
        entry: dict[str, Any] = {
            "key": src.key,
            "actor_class": src.actor_class,
            "label": src.label,
            "table": src.table,
            "last_action_at": None,
            "count_in_window": None,
            "status": "REAL",
        }
        try:
            last = runner.fetchall(src.last_sql, None)
            entry["last_action_at"] = _as_utc(last[0][0]) if last and last[0] else None
        except Exception as exc:  # pragma: no cover - defensive, exercised via fake runner
            entry["status"] = "WITHHELD"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        try:
            cnt = runner.fetchall(src.count_sql, params)
            entry["count_in_window"] = int(cnt[0][0]) if cnt and cnt[0] else 0
        except Exception as exc:  # pragma: no cover - defensive
            entry["status"] = "WITHHELD"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        if entry["status"] == "REAL" and entry["last_action_at"] is None and not entry["count_in_window"]:
            # the query worked, there is simply no human action on record
            entry["status"] = "WITHHELD"
        rows[src.key] = entry

    measured = [e for e in rows.values() if e["last_action_at"] is not None]
    last_human = max((e["last_action_at"] for e in measured), default=None)
    if last_human is None:
        status = "WITHHELD"
        hours = None
    else:
        status = "REAL"
        hours = max(0.0, (until - last_human).total_seconds() / 3600.0)

    by_class: dict[str, Any] = {}
    for klass in ("owner", "coordinator"):
        entries = [e for e in rows.values() if e["actor_class"] == klass]
        counts = [e["count_in_window"] for e in entries]
        lasts = [e["last_action_at"] for e in entries if e["last_action_at"] is not None]
        by_class[klass] = {
            "count_in_window": None if any(c is None for c in counts) else int(sum(counts)),
            "last_action_at": max(lasts) if lasts else None,
            "sources": [e["key"] for e in entries],
        }
        if by_class[klass]["count_in_window"] is None:
            by_class[klass]["status"] = "WITHHELD"
        else:
            by_class[klass]["status"] = "REAL"

    window_hours = (until - since).total_seconds() / 3600.0
    total = sum(c["count_in_window"] or 0 for c in by_class.values())
    return {
        "M1_unbroken_autonomy": {
            "value_hours": None if hours is None else round(hours, 2),
            "last_human_action_at": _iso(last_human),
            "status": status,
            "definition": "hours between the last human-side action and the evaluation instant",
            "actor_classes": {"owner": "owner 裁决/派单", "coordinator": "AI 协调者会话动作"},
        },
        "M2_human_interventions": {
            "window_hours": window_hours,
            "window_start": _iso(since),
            "window_end": _iso(until),
            "total": total,
            "per_day": None if total is None else round(total * 24.0 / window_hours, 2) if window_hours else None,
            "by_class": by_class,
            "status": "REAL" if all(c["status"] == "REAL" for c in by_class.values()) else "WITHHELD",
        },
        "sources": rows,
    }


# ---------------------------------------------------------------------------
# metric 3: mechanism coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mechanism:
    """One of the eight standing duties that must eventually run unattended."""

    key: str
    label: str
    daemon_globs: tuple[str, ...]
    activity_sql: str
    activity_table: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "daemon_globs": list(self.daemon_globs),
            "activity_table": self.activity_table,
            "activity_sql": self.activity_sql,
        }


#: The denominator is fixed at eight by the task book: 补给/调节/放门/合并/收割/记录/自愈/文档.
MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism(
        key="refill",
        label="补给（任务池补货/派发）",
        daemon_globs=("campaign_dispatcher.py", "ops/tp_worker.py", "selfstarter.py"),
        activity_table="campaign_events",
        activity_sql=(
            "SELECT count(*) AS n FROM campaign_events "
            "WHERE event_type = 'mission_dispatched' "
            "AND created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
    Mechanism(
        key="regulate",
        label="调节（饱和度脉搏/放行）",
        daemon_globs=("saturation_regulator_loop_once.py", "pulse*.py", "ops/operator_shift.py"),
        activity_table="saturation_pulse",
        activity_sql=(
            "SELECT count(*) AS n FROM saturation_pulse "
            "WHERE ts >= %(since)s AND ts < %(until)s"
        ),
    ),
    Mechanism(
        key="gate",
        label="放门（验收/总攻门自动放行）",
        daemon_globs=("auto_approver.py", "ops/auto_approver.py", "ops/auto_approver_loop.sh"),
        activity_table="tensor_agent_session_events",
        activity_sql=(
            "SELECT count(*) AS n FROM tensor_agent_session_events "
            "WHERE kind = 'approval_decided' "
            "AND starts_with(payload->>'note', '[auto-approve]') "
            "AND created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
    Mechanism(
        key="merge",
        label="合并（PR 门与合流通道）",
        daemon_globs=("merge_governor.py", "merge_master.py", "shadow_merge.py"),
        activity_table="mission_lifecycle_log",
        activity_sql=(
            "SELECT count(*) AS n FROM mission_lifecycle_log "
            "WHERE action = 'pr_gate' "
            "AND created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
    Mechanism(
        key="reap",
        label="收割（失联/超时任务清收）",
        daemon_globs=("idle_guard.py", "close_meaningless.py", "reaper*.py"),
        activity_table="mission_lifecycle_log",
        activity_sql=(
            "SELECT count(*) AS n FROM mission_lifecycle_log "
            "WHERE action IN ('write_off', 'suspend') "
            "AND created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
    Mechanism(
        key="record",
        label="记录（编年史快照落账）",
        daemon_globs=("tools/chronicle/collector.py", "tools/chronicle/extract_metrics.py"),
        activity_table="chronicle_snapshots",
        activity_sql=(
            "SELECT count(*) AS n FROM chronicle_snapshots "
            "WHERE ts >= %(since)s AND ts < %(until)s"
        ),
    ),
    Mechanism(
        key="self_heal",
        label="自愈（席位补齐/任务回收重生）",
        daemon_globs=("fleet_supervisor.sh", "ops/fleet_supervisor.sh", "runtime_sync.py"),
        activity_table="tensor_agent_session_events",
        activity_sql=(
            "SELECT count(*) AS n FROM tensor_agent_session_events "
            "WHERE payload->>'subtype' IN ('mission_reclaimed', 'mission_custody_requeue') "
            "AND created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
    Mechanism(
        key="documents",
        label="文档（报告产出/补报/质检）",
        daemon_globs=("report_watch.py", "ops/report_watch.py", "ops/report_qa.py"),
        activity_table="tensor_reports",
        activity_sql=(
            "SELECT count(*) AS n FROM tensor_reports "
            "WHERE created_at >= %(since)s AND created_at < %(until)s"
        ),
    ),
)


def default_daemon_roots(repo_root: str | None = None) -> tuple[str, ...]:
    """Directories searched for daemon artifacts (presence evidence).

    ``TP_DAEMON_ROOTS`` (os.pathsep separated) overrides / extends the default:
    the repository's own ``ops/`` plus the runtime harness directory.
    """
    roots: list[str] = []
    if repo_root:
        roots.append(os.path.join(repo_root, "ops"))
        roots.append(os.path.join(repo_root, "tools"))
    env = os.environ.get("TP_DAEMON_ROOTS", "")
    for item in env.split(os.pathsep):
        item = item.strip()
        if item:
            roots.append(item)
    return tuple(dict.fromkeys(roots))


_SKIP_DIRS = {".git", "__pycache__", "node_modules", "work", ".venv", "venv", "runs", "node_modules"}
_MAX_DEPTH = 4


def _find_daemon(globs: Sequence[str], roots: Sequence[str]) -> str | None:
    """Find the first daemon artifact matching ``globs`` under ``roots``.

    The walk is bounded (depth ``_MAX_DEPTH``) and prunes unrelated trees so a
    sibling worktree can never be credited as this platform's daemon.
    """
    from fnmatch import fnmatch

    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
            if os.path.relpath(dirpath, root).count(os.sep) >= _MAX_DEPTH:
                dirnames[:] = []
            for name in filenames:
                for pattern in globs:
                    base = os.path.basename(pattern)
                    if not fnmatch(name, base):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), root)
                    if "/" in pattern and not (rel == pattern or rel.endswith(pattern)):
                        continue
                    return os.path.join(dirpath, name)
    return None


def audit_mechanisms(
    runner: Runner,
    since: datetime,
    until: datetime,
    daemon_roots: Sequence[str] = (),
    mechanisms: Sequence[Mechanism] = MECHANISMS,
) -> dict[str, Any]:
    """Metric 3: a duty counts as covered only if a daemon exists AND it acted."""
    entries: list[dict[str, Any]] = []
    params = {"since": since, "until": until}
    for mech in mechanisms:
        daemon = _find_daemon(mech.daemon_globs, daemon_roots) if daemon_roots else None
        entry: dict[str, Any] = {
            "key": mech.key,
            "label": mech.label,
            "daemon_artifact": daemon,
            "daemon_present": daemon is not None,
            "activity_count": None,
            "activity_status": "REAL",
        }
        try:
            row = runner.fetchall(mech.activity_sql, params)
            entry["activity_count"] = int(row[0][0]) if row and row[0] else 0
        except Exception as exc:
            entry["activity_count"] = None
            entry["activity_status"] = "WITHHELD"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["covered"] = bool(entry["daemon_present"] and (entry["activity_count"] or 0) > 0)
        if entry["covered"]:
            entry["grade"] = "REAL"
        elif entry["daemon_present"]:
            entry["grade"] = "STUB"  # daemon exists but produced no evidence in window
        else:
            entry["grade"] = "MOCK"  # duty is currently performed by hand / unmeasured
        entries.append(entry)

    denominator = len(mechanisms)
    covered = sum(1 for e in entries if e["covered"])
    return {
        "M3_mechanism_coverage": {
            "covered": covered,
            "denominator": denominator,
            "ratio": round(covered / denominator, 4) if denominator else None,
            "percent": round(100.0 * covered / denominator, 2) if denominator else None,
            "status": "REAL" if all(e["activity_status"] == "REAL" for e in entries) else "WITHHELD",
        },
        "mechanisms": entries,
        "daemon_roots": list(daemon_roots),
    }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


TABLE_RE = re.compile(r"\bfrom\s+([a-z_][a-z0-9_.]*)", re.IGNORECASE)


def tables_in(sql: str) -> list[str]:
    return TABLE_RE.findall(sql)


def render_for_psql(sql: str, params: Mapping[str, Any] | None = None) -> str:
    """Substitute parameters with literals so the statement is paste-ready."""
    out = sql
    for key, value in (params or {}).items():
        if isinstance(value, datetime):
            literal = "'" + _iso(value).replace("T", " ").replace("Z", "+00") + "'"
        elif isinstance(value, str):
            literal = "'" + value.replace("'", "''") + "'"
        else:  # pragma: no cover - numeric branch
            literal = str(value)
        out = out.replace(f"%({key})s", literal)
    return re.sub(r"\s+", " ", out).strip().rstrip(";")


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:  # pragma: no cover - driver dependent
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_baseline(
    runner: Runner,
    now: datetime | None = None,
    window_hours: float = 24.0,
    interactive_user_ids: Sequence[int] = (2,),
    daemon_roots: Sequence[str] = (),
    repo_commit: str | None = None,
) -> dict[str, Any]:
    """Assemble the three-metric baseline payload (pure: no I/O beyond runner)."""
    until = _as_utc(now) or datetime.now(timezone.utc)
    since = until - timedelta(hours=window_hours)
    human = human_input_metrics(runner, tuple(interactive_user_ids), since, until)
    mech = audit_mechanisms(runner, since, until, tuple(daemon_roots))

    sql_index: list[dict[str, Any]] = []
    params = {"since": since, "until": until}
    for src in action_sources(interactive_user_ids):
        sql_index.append(
            {
                "metric": "M1",
                "key": src.key,
                "sql": render_for_psql(src.last_sql),
            }
        )
        sql_index.append(
            {
                "metric": "M2",
                "key": src.key,
                "sql": render_for_psql(src.count_sql, params),
            }
        )
    for m in MECHANISMS:
        sql_index.append(
            {"metric": "M3", "key": m.key, "sql": render_for_psql(m.activity_sql, params)}
        )

    m1 = human["M1_unbroken_autonomy"]
    m2 = human["M2_human_interventions"]
    m3 = mech["M3_mechanism_coverage"]
    return {
        "schema": "autonomy-baseline/1",
        "generated_at": _iso(until),
        "window_hours": window_hours,
        "window_start": _iso(since),
        "window_end": _iso(until),
        "interactive_user_ids": list(interactive_user_ids),
        "metrics": {"M1": m1, "M2": m2, "M3": m3},
        "detail": {"action_sources": human["sources"], "mechanisms": mech["mechanisms"]},
        "sql": sql_index,
        "recompute": {
            "how": "paste any sql entry into psql against the platform database",
            "param_note": "timestamps already substituted; window = [window_start, window_end)",
        },
        "daemon_roots": mech["daemon_roots"],
        "provenance": {
            "producer": "tools/autonomy/dashboard.py",
            "node": os.environ.get("TP_AGENT_ID", "unknown"),
            "commit": repo_commit,
            "actor_class_rule": (
                "interactive channel identities = "
                + ",".join(str(i) for i in interactive_user_ids)
                + "; daemon channel is everything else"
            ),
        },
        "domain_semantics": "none",
    }


def chronicle_payload(baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Payload written into ``chronicle_snapshots`` so the trend is queryable."""
    m1 = baseline["metrics"]["M1"]
    m2 = baseline["metrics"]["M2"]
    m3 = baseline["metrics"]["M3"]
    return {
        "collector": "tools/autonomy/dashboard.py (IN20-R7 autonomy exit dashboard)",
        "autonomy": {
            "M1_unbroken_autonomy_hours": m1["value_hours"],
            "M1_last_human_action_at": m1["last_human_action_at"],
            "M2_owner_interventions": m2["by_class"]["owner"]["count_in_window"],
            "M2_coordinator_interventions": m2["by_class"]["coordinator"]["count_in_window"],
            "M2_total": m2["total"],
            "M2_per_day": m2["per_day"],
            "M3_covered": m3["covered"],
            "M3_denominator": m3["denominator"],
            "M3_percent": m3["percent"],
            "M3_uncovered": [e["key"] for e in baseline["detail"]["mechanisms"] if not e["covered"]],
            "window_hours": baseline["window_hours"],
        },
        "grades": {
            "M1": m1["status"],
            "M2": m2["status"],
            "M3": m3["status"],
        },
        "schema": baseline["schema"],
        "generated_at": baseline["generated_at"],
        "domain_semantics": "none",
    }


__all__ = [
    "ACTION_SOURCES",
    "MECHANISMS",
    "ActionSource",
    "Mechanism",
    "PgRunner",
    "Runner",
    "action_sources",
    "audit_mechanisms",
    "build_baseline",
    "chronicle_payload",
    "default_daemon_roots",
    "human_input_metrics",
    "render_for_psql",
    "tables_in",
]