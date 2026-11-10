"""``tools/maturity_inference`` 流水线面用例（黄金集/迁移/装配器/报告；合成夹具）。

覆盖判据：黄金集 40 行与冻结 sha256、naive 基线对照、node.* 迁移幂等与残留 0、
装配器（合成仓库）三档产物、报告渲染含六要素骨架。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.maturity_inference.golden import (
    DEFAULT_GOLDEN,
    evaluate_golden,
    load_golden,
    naive_level,
)
from tools.maturity_inference.ledger_adapter import (
    INTERFACE_CONTRACT,
    normalize_ledger_row,
    universe,
)
from tools.maturity_inference.linker import (
    discover_sources,
    discover_tests,
    link_capabilities,
    run_pytest_batch,
)
from tools.maturity_inference.migrate import apply_migration, plan_migration
from tools.maturity_inference.report import level_table_md, render  # noqa: F401

TS = "2026-11-10T00:00:00+00:00"


# ---------------------------------------------------------------- golden
def test_golden_has_40_rows_and_unique_keys():
    g = load_golden()
    keys = [r["capability_key"] for r in g["rows"]]
    assert len(keys) == 40 and len(set(keys)) == 40


def test_golden_keys_are_synthetic_namespace():
    g = load_golden()
    assert all(r["capability_key"].startswith("synth.") for r in g["rows"])


def test_golden_sha_matches_frozen_manifest():
    sha_file = DEFAULT_GOLDEN.with_name("golden_set.sha256")
    assert sha_file.exists(), "冻结清单缺失"
    assert sha_file.read_text().strip() == load_golden()["_sha256"]


def test_golden_f1_above_threshold():
    rep = evaluate_golden(load_golden())
    assert rep.macro_f1 >= 0.9 and rep.passes


def test_golden_beats_naive_baseline():
    rep = evaluate_golden(load_golden())
    naive = rep.naive_baseline
    assert naive["accuracy"] < rep.accuracy
    assert naive["differing_rows"] > 0


def test_naive_level_reads_max_rung():
    row = {"nodes": [{"rung": "implemented"}, {"rung": "tested"}]}
    assert naive_level(row) == "tested"
    assert naive_level({"nodes": []}) == "not_supported"


# ---------------------------------------------------------------- migrate
def _live_rows():
    return [
        {"id": "synth.alpha.beta", "claimed_level": "REAL"},
        {"id": "node.abc_n01.undelivered", "claimed_level": "NOT_SUPPORTED",
         "note": "bookkeeping"},
        {"id": "node.abc_n02.undelivered", "claimed_level": "WITHHELD"},
        {"id": "node.abc_n03.pending", "claimed_level": "WITHHELD"},
    ]


def test_plan_migration_targets_registry():
    plan = plan_migration(_live_rows())
    assert plan["count"] == 3
    assert {e["target_store"] for e in plan["entries"]} == {"campaign_node_registry"}
    assert {e["target_key"] for e in plan["entries"]} == {"abc_n01", "abc_n02", "abc_n03"}
    assert {e["target_field"] for e in plan["entries"]} == {"delivery_status"}
    assert plan["plan_sha256"]


def test_apply_migration_leaves_zero_node_rows():
    rows = _live_rows()
    out = apply_migration(rows, plan_migration(rows))
    assert out["node_prefix_remaining"] == 0
    assert out["rows_after"] == 1 and out["migrated"] == 3
    assert out["before_sha256"] != out["after_sha256"]


def test_apply_migration_is_idempotent():
    rows = _live_rows()
    plan = plan_migration(rows)
    once = apply_migration(rows, plan)
    twice = apply_migration(once["rows"], plan)
    assert twice["rows_after"] == once["rows_after"]
    assert twice["after_sha256"] == once["after_sha256"]
    assert twice["migrated"] == 0


def test_migration_carries_fields():
    rows = _live_rows()
    plan = plan_migration(rows)
    entry = [e for e in plan["entries"] if e["target_key"] == "abc_n01"][0]
    assert entry["carried_fields"]["claimed_level"] == "NOT_SUPPORTED"
    assert entry["action"] == "deregister_from_capability_ledger"
    assert entry["reversible"] is True


# ---------------------------------------------------------------- adapter
def test_normalize_ledger_row_separates_contradictions():
    row = {"id": "synth.alpha.beta", "claimed_level": "real", "domain": "synthetic",
           "evidence": [{"stance": "contradicts", "asserted_level": "MOCK"},
                        {"stance": "supports_real", "asserted_level": "REAL"}]}
    out = normalize_ledger_row(row, {"reconciled_level": "MOCK", "divergence": "OVERCLAIM"})
    assert out["claimed_level"] == "REAL"
    assert out["contradictions"] == ["MOCK"]
    assert out["narrative_support"][0]["asserted_level"] == "REAL"
    assert out["reconciled_level"] == "MOCK"


def test_universe_reports_ledger_defs_diff():
    out = universe([{"id": "a.b"}], [{"capability_id": "a.b"}, {"capability_id": "c.d"}])
    assert out["both"] == ["a.b"] and out["defs_only"] == ["c.d"]
    assert out["ledger_count"] == 1 and out["defs_count"] == 2


def test_interface_contract_declares_engine_io():
    assert "engine_input" in INTERFACE_CONTRACT and "engine_output" in INTERFACE_CONTRACT
    assert INTERFACE_CONTRACT["migration"]["invariant"].endswith("= 0")


# ---------------------------------------------------------------- linker
def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "services").mkdir(parents=True)
    impl = tmp_path / "backend" / "services" / "widget_engine.py"
    impl.write_text("def solve():\n    return 1\n")
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    test = tmp_path / "backend" / "tests" / "test_widget.py"
    test.write_text(
        "def test_one():\n    assert 1 + 1 == 2\n\n\n"
        "def test_two():\n    assert 1 + 1 == 2\n")
    return tmp_path


CAP = {"capability_id": "synth.widget.engine",
       "code_patterns": ["widget_engine"], "test_patterns": ["test_widget"]}


def test_discover_sources_and_tests(tmp_path):
    root = _fake_repo(tmp_path)
    src = discover_sources(CAP, root)
    tst = discover_tests(CAP, root)
    assert src[0]["path"].endswith("widget_engine.py") and len(src[0]["sha256"]) == 64
    assert tst[0]["path"].endswith("test_widget.py")


def test_link_without_execute_cannot_support_tested(tmp_path):
    root = _fake_repo(tmp_path)
    payload = link_capabilities([CAP], root, execute=False)
    summary = payload["summary"]
    assert summary["implemented_links"] == 1
    assert summary["tests_passed"] == 0 and summary["tests_executed"] == 0
    # 未执行 → 不产生 tested 证据节点（fail-closed：没跑过就不能说测过）
    tested = [n for n in payload["nodes"] if n["rung"] == "tested"]
    assert tested == []
    assert all(t["status"] == "not_run" for link in payload["links"] for t in link["tests"])


def test_link_with_execute_records_pass(tmp_path):
    root = _fake_repo(tmp_path)
    payload = link_capabilities([CAP], root, execute=True, timeout=180)
    assert payload["summary"]["tests_passed"] == 1
    tested = [n for n in payload["nodes"] if n["rung"] == "tested"]
    assert tested[0]["rerun"]["status"] == "passed"
    assert len(tested[0]["rerun"]["log_sha256"]) == 64


def test_run_pytest_batch_reports_failures(tmp_path):
    root = _fake_repo(tmp_path)
    bad = root / "backend" / "tests" / "test_widget_bad.py"
    bad.write_text("def test_bad():\n    assert False\n")
    out = run_pytest_batch(["backend/tests/test_widget_bad.py"], root, timeout=180)
    assert out["status_by_path"]["backend/tests/test_widget_bad.py"] == "failed"


def test_run_pytest_batch_empty_is_not_run():
    out = run_pytest_batch([], Path("."))
    assert out["status_by_path"] == {} and out["counts"]["not_run"] == 0


def test_link_skips_unknown_patterns(tmp_path):
    root = _fake_repo(tmp_path)
    payload = link_capabilities([{"capability_id": "synth.none.thing",
                                  "code_patterns": [], "test_patterns": []}], root)
    assert payload["summary"]["implemented_links"] == 0
    assert payload["nodes"] == []


# ---------------------------------------------------------------- report
def test_level_table_md_renders_rows():
    md = level_table_md({"rows": [
        {"capability_key": "synth.alpha.beta", "claimed_level": "REAL",
         "inferred_level": "tested", "inferred_rank": 2, "claim_status": "unbacked_overclaim",
         "break_rung": "benchmark_verified", "executable_links": 1}]})
    assert "`synth.alpha.beta`" in md and "**tested**" in md


def test_golden_report_json_roundtrip():
    rep = evaluate_golden(load_golden()).as_dict()
    blob = json.dumps(rep, ensure_ascii=False)
    assert json.loads(blob)["golden_set_sha256"] == rep["golden_set_sha256"]
    assert len(rep["labels"]) == 6