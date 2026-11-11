"""Unit tests for tools.failure_attribution (synthetic, domain-neutral fixtures).

Every fixture below is invented: the engine must not depend on any real
mission text, repo name or domain word to pass.

Run: python3 -m pytest tests/test_failure_attribution.py -q
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.failure_attribution import (
    UNKNOWN_KIND,
    UNKNOWN_STAGE,
    classify,
    emit_backfill_sql,
    evaluate,
    load_config,
    mask_label_tokens,
    mission_from_row,
    run,
    success_metrics,
)
from tools.failure_attribution.engine import ConfigError

CONFIG = load_config()


def _m(**row):
    row.setdefault("id", 1)
    row.setdefault("status", "failed")
    return mission_from_row(row, CONFIG)


def test_declared_stage_beats_rules():
    m = _m(id=11, headline="something unrelated", **{"fail_stage": "budget_exhausted"})
    a = classify(m, CONFIG)
    assert a.source == "declared"
    assert (a.stage, a.kind) == ("budget", "budget_exhausted")
    assert a.confidence == 1.0


def test_declared_compound_token_splits():
    m = _m(id=12, **{"fail_stage": "no_progress+forced_finish"})
    a = classify(m, CONFIG)
    assert a.stage == "agent_loop"
    assert a.kind == "no_progress"


def test_nested_declared_path_is_found():
    m = _m(id=13, result={"evidence": {"checks": {"fail_stage": "wall_exhausted"}}})
    a = classify(m, CONFIG)
    assert a.source == "declared" and a.kind == "wall_exhausted"


def test_inference_from_traceback():
    m = _m(id=14, result={"traceback_tail": "OSError: [Errno 28] No space left on device"})
    a = classify(m, CONFIG)
    assert a.source == "inferred"
    assert (a.stage, a.kind, a.rule_id) == ("worktree", "disk_full", "R-WT-01")
    assert "No space left" in a.snippet


def test_rule_priority_precedence_is_deterministic():
    text = "Connection refused while preparing; budget_exhausted"
    m = _m(id=15, headline=text)
    a = classify(m, CONFIG)
    # R-BUDGET-01 (priority 20) must win over R-NET-01 (priority 50)
    assert a.rule_id == "R-BUDGET-01"
    assert classify(m, CONFIG) == a  # determinism


def test_unknown_when_no_evidence():
    a = classify(_m(id=16), CONFIG)
    assert (a.stage, a.kind, a.source) == (UNKNOWN_STAGE, UNKNOWN_KIND, "unknown")
    assert a.confidence == 0.0


def test_mask_label_tokens_removes_terminal_clause():
    out = mask_label_tokens("未完成（终局: no_progress）", {"no_progress"})
    assert "no_progress" not in out
    assert "<masked>" in out


def test_heldout_accuracy_requires_masking():
    """Leak check: prose that embeds the declared token must not count as signal."""
    missions = [
        _m(id=100 + i, headline=f"terminal reason: {tag}（终局: {tag}）", **{"fail_stage": tag})
        for i, tag in enumerate(["no_progress", "budget_exhausted", "llm_error"])
    ]
    optimistic = evaluate(missions, CONFIG, mask_declared=False)
    masked = evaluate(missions, CONFIG, mask_declared=True)
    assert optimistic["heldout"]["stage_accuracy"] == 1.0
    assert masked["heldout"]["stage_accuracy"] < 1.0  # masked prose alone is ambiguous
    assert masked["coverage"] > 0.0  # declared channel still attributes them


def test_independent_text_still_attributes_after_masking():
    m = _m(
        id=200,
        headline="terminal reason: no_progress（终局: no_progress）",
        result={"traceback_tail": "fatal: unable to access remote: Could not resolve host"},
        **{"fail_stage": "no_progress"},
    )
    a = classify(m, CONFIG, ignore_declared=True, mask_declared=True)
    assert a.source == "inferred"
    assert a.stage == "worktree"  # text signal survives the mask


def test_feature_rule_zero_tool_calls_and_gate():
    rows = [
        _m(id=401, result={"tool_calls": 0}, **{"fail_stage": "llm_error"}),
        _m(id=402, result={"tool_calls": 0}, **{"fail_stage": "llm_error"}),
        _m(id=403, result={"tool_calls": 7}, **{"fail_stage": "no_progress"}),
    ]
    stats = __import__("tools.failure_attribution.engine", fromlist=["rule_stats"]).rule_stats(rows, CONFIG)
    assert stats["R-LLM-F01"]["support"] == 2 and stats["R-LLM-F01"]["precision"] == 1.0
    a = classify(rows[0], CONFIG, ignore_declared=True)
    assert (a.rule_id, a.stage) == ("R-LLM-F01", "llm_infra")
    assert a.field == "result.tool_calls"


def test_config_rejects_mixed_rule(tmp_path: Path):
    bad = json.loads((Path("config/failure_attribution.json")).read_text(encoding="utf-8"))
    bad["rules"][0]["patterns"] = ["x"]
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_config_rejects_unknown_stage(tmp_path: Path):
    bad = json.loads((Path("config/failure_attribution.json")).read_text(encoding="utf-8"))
    bad["rules"][0]["stage"] = "not_a_stage"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_config_rejects_duplicate_rule_id(tmp_path: Path):
    bad = json.loads((Path("config/failure_attribution.json")).read_text(encoding="utf-8"))
    bad["rules"].append(dict(bad["rules"][0]))
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_backfill_sql_is_idempotent_and_skips_unknown():
    attributed = evaluate(
        [
            _m(id=301, result={"traceback_tail": "No space left on device"}),
            _m(id=302),  # unknown -> no statement
            _m(id=303, **{"fail_stage": "llm_error"}),
        ],
        CONFIG,
    )
    from tools.failure_attribution.engine import Attribution

    sql = emit_backfill_sql([Attribution(**a) for a in attributed["attributions"]])
    assert sql.count("UPDATE ") == 2
    assert "id = 301" in sql and "id = 303" in sql
    assert "id = 302" not in sql
    assert sql.count("fail_stage IS NULL OR fail_stage = ''") == 2


def test_success_metrics_window_and_primary_ratio():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    rows = [
        {"status": "succeeded", "finished_at": (now - timedelta(days=1)).isoformat()},
        {"status": "succeeded", "finished_at": (now - timedelta(days=2)).isoformat()},
        {"status": "failed", "finished_at": (now - timedelta(days=2)).isoformat()},
        {"status": "stopped", "finished_at": (now - timedelta(days=3)).isoformat()},
        {"status": "failed", "finished_at": (now - timedelta(days=30)).isoformat()},
    ]
    res = success_metrics(rows, as_of=now, window_days=7)
    assert res["all_time"]["succeeded"] == 2 and res["all_time"]["failed"] == 2
    assert res["all_time"]["success_rate"] == 0.5
    # stopped included in the strict denominator, excluded from the primary one
    assert res["all_time"]["strict_success_rate"] == pytest.approx(2 / 5)
    assert res["last_7d"]["failed"] == 1
    assert res["last_7d"]["success_rate"] == pytest.approx(2 / 3)


def test_run_writes_result_and_artifacts(tmp_path: Path):
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    csv_path = tmp_path / "snap.csv"
    rows = [
        {"id": 1, "status": "succeeded", "created_at": now.isoformat(), "finished_at": now.isoformat(), "fail_stage": "", "result": "{}"},
        {"id": 2, "status": "failed", "created_at": now.isoformat(), "finished_at": now.isoformat(), "fail_stage": "",
         "result": json.dumps({"traceback_tail": "ENOSPC: no space left on device"})},
        {"id": 3, "status": "failed", "created_at": now.isoformat(), "finished_at": now.isoformat(), "fail_stage": "llm_error", "result": "{}"},
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    out = tmp_path / "out"
    result = run(csv_path, out, as_of=now)
    assert result["success"]["all_time"]["success_rate"] == pytest.approx(1 / 3)
    assert result["attribution"]["masked"]["coverage"] == 1.0
    assert (out / "result.json").exists()
    assert (out / "failure_buckets.csv").exists()
    # only trusted attributions are emitted: the declared row. The ENOSPC row is
    # skipped because R-WT-01 has support=0 on the labelled cohort (unvalidated,
    # not passing) -- see trusted_view().
    sql = (out / "backfill_fail_stage.sql").read_text(encoding="utf-8")
    assert sql.count("UPDATE ") == 1 and "id = 3" in sql and "id = 2" not in sql
    assert result["backfill_sql_statements"] == 1