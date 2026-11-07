"""pytest suite for the autonomy exit dashboard (IN20-R7).

The suite is database-free: every test drives the metric code through
``FakeRunner`` so the arithmetic, the actor-class split, the coverage rule and
the SQL audit are all pinned without a live Postgres.  One optional test runs
against the live platform database when ``TP_DSN`` is set.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.autonomy import metrics as M  # noqa: E402
from tools.autonomy import dashboard as D  # noqa: E402

NOW = datetime(2026, 11, 7, 4, 0, 0, tzinfo=timezone.utc)
WINDOW = 24.0


class FakeRunner:
    """Rows keyed by a substring of the SQL; unmatched queries return defaults."""

    def __init__(self, rows_by_token=None, raiser=None):
        self.rows_by_token = rows_by_token or {}
        self.raiser = raiser
        self.calls = []

    def fetchall(self, sql, params=None):
        self.calls.append((sql, params))
        if self.raiser and self.raiser(sql):
            raise RuntimeError("simulated query failure")
        for token, rows in self.rows_by_token.items():
            if token in sql:
                return rows
        return [(None,)] if "max(" in sql else [(0,)]

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.raiser and self.raiser(sql):
            raise RuntimeError("simulated query failure")
        return True

    # convenience -------------------------------------------------------
    def sql_for(self, token):
        return [c[0] for c in self.calls if token in c[0]]


def _hours_ago(h):
    return NOW - timedelta(hours=h)


# ---------------------------------------------------------------------------
# M1 — unbroken autonomy
# ---------------------------------------------------------------------------


def test_m1_uses_the_most_recent_human_action():
    runner = FakeRunner(
        {
            "max(decided_at)": [(_hours_ago(120),)],
            "max(created_at) AS last_ts FROM tensor_dev_missions": [(_hours_ago(48),)],
            "max(created_at) AS last_ts FROM tensor_agent_sessions": [(_hours_ago(30),)],
        }
    )
    res = M.human_input_metrics(runner, (2,), _hours_ago(WINDOW), NOW)
    m1 = res["M1_unbroken_autonomy"]
    assert m1["status"] == "REAL"
    # newest of -120h/-48h/-30h wins
    assert m1["last_human_action_at"] == _hours_ago(30).isoformat().replace("+00:00", "Z")
    assert m1["value_hours"] == pytest.approx(30.0, abs=1e-6)


def test_m1_is_withheld_when_no_human_action_exists():
    runner = FakeRunner()  # every max() query returns NULL
    res = M.human_input_metrics(runner, (2,), _hours_ago(WINDOW), NOW)
    m1 = res["M1_unbroken_autonomy"]
    assert m1["status"] == "WITHHELD"
    assert m1["value_hours"] is None
    assert m1["last_human_action_at"] is None


def test_m1_is_withheld_when_a_source_query_fails():
    runner = FakeRunner({"max(decided_at)": [(_hours_ago(5),)]}, raiser=lambda sql: "tensor_agent_sessions" in sql)
    res = M.human_input_metrics(runner, (2,), _hours_ago(WINDOW), NOW)
    assert res["sources"]["coordinator_session"]["status"] == "WITHHELD"
    # the remaining sources still produce a number: partial evidence is kept and flagged
    assert res["M1_unbroken_autonomy"]["value_hours"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# M2 — interventions per day, owner vs coordinator
# ---------------------------------------------------------------------------


def test_m2_splits_owner_and_coordinator_counts():
    runner = FakeRunner(
        {
            "count(*) AS n FROM tensor_approval_requests": [(7,)],
            "count(*) AS n FROM tensor_dev_missions": [(3,)],
            "count(*) AS n FROM tensor_agent_sessions": [(2,)],
            "max(decided_at)": [(_hours_ago(6),)],
            "max(created_at) AS last_ts FROM tensor_dev_missions": [(_hours_ago(9),)],
            "max(created_at) AS last_ts FROM tensor_agent_sessions": [(_hours_ago(4),)],
        }
    )
    res = M.human_input_metrics(runner, (2,), _hours_ago(WINDOW), NOW)
    m2 = res["M2_human_interventions"]
    assert m2["by_class"]["owner"]["count_in_window"] == 10  # approval 7 + dispatch 3
    assert m2["by_class"]["coordinator"]["count_in_window"] == 2
    assert m2["total"] == 12
    assert m2["per_day"] == pytest.approx(12.0)


def test_m2_per_day_scales_with_window_length():
    runner = FakeRunner(
        {
            "count(*) AS n FROM tensor_approval_requests": [(1,)],
            "count(*) AS n FROM tensor_dev_missions": [(0,)],
            "count(*) AS n FROM tensor_agent_sessions": [(2,)],
            "max(decided_at)": [(_hours_ago(14),)],
            "max(created_at) AS last_ts FROM tensor_dev_missions": [(_hours_ago(200),)],
            "max(created_at) AS last_ts FROM tensor_agent_sessions": [(_hours_ago(15),)],
        }
    )
    res = M.human_input_metrics(runner, (2,), _hours_ago(12), NOW)  # 12 h window
    m2 = res["M2_human_interventions"]
    assert m2["window_hours"] == pytest.approx(12.0)
    assert m2["total"] == 3
    assert m2["per_day"] == pytest.approx(6.0)


def test_window_bounds_are_passed_to_the_sql():
    runner = FakeRunner()
    M.human_input_metrics(runner, (2,), _hours_ago(24), NOW)
    stamped = [params for _sql, params in runner.calls if params]
    assert stamped, "count queries must receive the window parameters"
    for params in stamped:
        assert set(params) == {"since", "until"}
        assert params["until"] == NOW
        assert params["since"] == _hours_ago(24)


# ---------------------------------------------------------------------------
# M3 — mechanism coverage
# ---------------------------------------------------------------------------


def test_m3_denominator_is_the_eight_standing_duties():
    assert len(M.MECHANISMS) == 8
    assert [m.key for m in M.MECHANISMS] == [
        "refill",
        "regulate",
        "gate",
        "merge",
        "reap",
        "record",
        "self_heal",
        "documents",
    ]
    res = M.audit_mechanisms(FakeRunner(), _hours_ago(WINDOW), NOW, daemon_roots=())
    assert res["M3_mechanism_coverage"]["denominator"] == 8
    assert res["M3_mechanism_coverage"]["covered"] == 0


def test_m3_coverage_needs_both_daemon_and_activity(tmp_path):
    # daemon artifacts for exactly three duties
    (tmp_path / "saturation_regulator_loop_once.py").write_text("# daemon\n")
    (tmp_path / "auto_approver.py").write_text("# daemon\n")
    (tmp_path / "idle_guard.py").write_text("# daemon\n")
    runner = FakeRunner(
        {
            "FROM saturation_pulse": [(4,)],  # daemon + activity -> covered
            "FROM tensor_agent_session_events WHERE kind = 'approval_decided'": [(6,)],  # covered
            "action IN ('write_off', 'suspend')": [(0,)],  # daemon but silent -> STUB
        }
    )
    res = M.audit_mechanisms(runner, _hours_ago(WINDOW), NOW, daemon_roots=(str(tmp_path),))
    by_key = {e["key"]: e for e in res["mechanisms"]}
    assert by_key["regulate"]["covered"] and by_key["regulate"]["grade"] == "REAL"
    assert by_key["gate"]["covered"]
    assert by_key["reap"]["daemon_present"] and not by_key["reap"]["covered"]
    assert by_key["reap"]["grade"] == "STUB"
    assert by_key["merge"]["grade"] == "MOCK"
    assert res["M3_mechanism_coverage"]["covered"] == 2
    assert res["M3_mechanism_coverage"]["percent"] == pytest.approx(25.0)


def test_m3_reports_withheld_when_a_query_fails():
    runner = FakeRunner(raiser=lambda sql: "saturation_pulse" in sql)
    res = M.audit_mechanisms(runner, _hours_ago(WINDOW), NOW, daemon_roots=())
    assert res["M3_mechanism_coverage"]["status"] == "WITHHELD"
    assert res["mechanisms"][1]["activity_status"] == "WITHHELD"


# ---------------------------------------------------------------------------
# SQL audit: recomputability
# ---------------------------------------------------------------------------


def test_every_sql_in_the_artifact_is_paste_ready():
    baseline = M.build_baseline(FakeRunner(), now=NOW, window_hours=WINDOW, daemon_roots=())
    assert len(baseline["sql"]) == 3 * 2 + 8  # M1+M2 for three sources, M3 for eight duties
    for entry in baseline["sql"]:
        sql = entry["sql"]
        assert sql.lower().startswith("select"), entry
        assert "%(" not in sql, f"unsubstituted parameter in {entry['key']}"
        assert not sql.endswith(";")
        assert M.tables_in(sql), f"no table referenced by {entry['key']}"
    assert {e["metric"] for e in baseline["sql"]} == {"M1", "M2", "M3"}


def test_sql_only_touches_known_bookkeeping_tables():
    allowed = {
        "tensor_approval_requests",
        "tensor_dev_missions",
        "tensor_agent_sessions",
        "tensor_agent_session_events",
        "campaign_events",
        "saturation_pulse",
        "mission_lifecycle_log",
        "chronicle_snapshots",
        "tensor_reports",
    }
    for entry in M.build_baseline(FakeRunner(), now=NOW, daemon_roots=())["sql"]:
        for table in M.tables_in(entry["sql"]):
            assert table in allowed, f"{entry['key']} reads unexpected table {table}"


def test_render_for_psql_substitutes_timestamp_literals():
    sql = "SELECT count(*) AS n FROM t WHERE x >= %(since)s AND x < %(until)s"
    rendered = M.render_for_psql(sql, {"since": _hours_ago(24), "until": NOW})
    assert "%(" not in rendered
    assert "2026-11-06 04:00:00+00" in rendered


# ---------------------------------------------------------------------------
# artifact / chronicle plumbing
# ---------------------------------------------------------------------------


def test_baseline_payload_contract():
    baseline = M.build_baseline(FakeRunner(), now=NOW, window_hours=WINDOW, daemon_roots=(), repo_commit="deadbeef")
    assert baseline["schema"] == "autonomy-baseline/1"
    assert baseline["domain_semantics"] == "none"
    assert baseline["window_hours"] == WINDOW
    assert set(baseline["metrics"]) == {"M1", "M2", "M3"}
    assert baseline["provenance"]["commit"] == "deadbeef"
    assert baseline["interactive_user_ids"] == [2]
    json.dumps(baseline, default=str)  # must be serialisable as-is


def test_chronicle_payload_carries_the_three_metrics_for_the_trend():
    baseline = M.build_baseline(FakeRunner(), now=NOW, daemon_roots=())
    payload = M.chronicle_payload(baseline)
    assert payload["schema"] == "autonomy-baseline/1"
    assert payload["autonomy"]["M3_denominator"] == 8
    assert payload["autonomy"]["M3_covered"] == 0
    assert len(payload["autonomy"]["M3_uncovered"]) == 8
    assert set(payload["grades"]) == {"M1", "M2", "M3"}
    json.dumps(payload, ensure_ascii=False)


def test_write_chronicle_reports_status_without_raising():
    baseline = M.build_baseline(FakeRunner(), now=NOW, daemon_roots=())
    ok = D.write_chronicle(FakeRunner(), baseline, ts=NOW)
    assert ok["status"] == "written" and ok["table"] == "chronicle_snapshots"

    def boom(sql):
        return "INSERT INTO chronicle_snapshots" in sql

    bad = D.write_chronicle(FakeRunner(raiser=boom), baseline, ts=NOW)
    assert bad["status"] == "failed" and "simulated" in bad["error"]


def test_markdown_scoreboard_and_figure(tmp_path):
    baseline = M.build_baseline(
        FakeRunner({"FROM saturation_pulse": [(3,)]}),
        now=NOW,
        daemon_roots=(),
    )
    md = D.render_markdown(baseline)
    for needle in ("无人类输入续航时长", "机制覆盖率", "SQL 可复算索引", "WITHHELD"):
        assert needle in md
    fig_path = tmp_path / "fig.png"
    made = D.render_figure(baseline, str(fig_path))
    if made is not None:  # matplotlib present
        assert os.path.getsize(made) > 5000


# ---------------------------------------------------------------------------
# discipline gates
# ---------------------------------------------------------------------------

SHIP_TERMS = ("阻力", "推进效率", "耐波", "操纵性", "稳性", "冰区", "螺旋桨", "船体", "hydrodynamic", "seakeeping")


def test_module_is_domain_neutral():
    offenders = []
    for path in sorted((REPO_ROOT / "tools" / "autonomy").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for term in SHIP_TERMS:
            if term in text:
                offenders.append(f"{path.name}:{term}")
    assert not offenders, f"domain semantics leaked into the dashboard: {offenders}"


@pytest.mark.skipif(not os.environ.get("TP_DSN"), reason="live database not configured")
def test_live_database_recomputes_the_same_numbers():  # pragma: no cover - live only
    runner = M.PgRunner(os.environ["TP_DSN"])
    baseline = M.build_baseline(runner, window_hours=24.0, daemon_roots=())
    assert baseline["metrics"]["M3"]["denominator"] == 8
    for entry in baseline["sql"]:
        rows = runner.fetchall(entry["sql"])
        assert rows is not None