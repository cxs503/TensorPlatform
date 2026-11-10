"""Unit tests: selection decisions, fail-closed behaviour, determinism, ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.test_impact_selector.config import SelectorConfig
from tools.test_impact_selector.graph import ImportGraph
from tools.test_impact_selector.ledger import Ledger
from tools.test_impact_selector.matrix import run_matrix
from tools.test_impact_selector.sandbox import make_clean_tree, touch
from tools.test_impact_selector.selector import ImpactSelector


def _setup(tmp_path: Path, **cfg):
    root = make_clean_tree(tmp_path / "repo")
    graph = ImportGraph.build(root, use_history=False, jobs=1)
    return root, graph, ImpactSelector(graph, SelectorConfig(**cfg), root=root)


def test_narrow_change_selects_subset(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    s = sel.select(["beta/calc.py"])
    assert s.mode == "subset"
    assert any(t.startswith("beta/tests/") for t in s.tests)
    assert not any(t.startswith("alpha/tests/") for t in s.tests)
    assert s.ratio < 0.6


def test_selection_includes_safety_set(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    s = sel.select(["beta/calc.py"])
    assert "tests/test_smoke_all.py" in s.tests


def test_docs_only_change_still_selects_at_least_one_test(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    s = sel.select(["docs/guide.md"])
    assert s.mode == "subset"
    assert len(s.tests) >= 1
    assert s.trigger is None


def test_safety_reach_fallback_when_globs_miss(tmp_path: Path):
    root, graph, _ = _setup(tmp_path, safety_tests=(), safety_globs=("tests/test_missing*.py",))
    sel = ImpactSelector(graph, SelectorConfig(safety_tests=(), safety_globs=("tests/test_missing*.py",)), root=root)
    assert len(sel.safety_tests()) == SelectorConfig().safety_reach_topk


def test_dynamic_import_in_closure_fails_closed(tmp_path: Path):
    root, graph, _ = _setup(tmp_path)
    touch(root, "alpha/dyn.py", "import importlib\n\nimport alpha.core\n\n\ndef load(n):\n    return importlib.import_module(n)\n")
    graph = ImportGraph.build(root, use_history=False, jobs=1)
    sel = ImpactSelector(graph, SelectorConfig(), root=root)
    s = sel.select(["alpha/core.py"])
    assert s.mode == "full"
    assert s.trigger == "dynamic_import"


def test_new_file_fails_closed(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    touch(root, "alpha/newness.py", '"""new."""\nVALUE = 1\n')
    s = sel.select(["alpha/newness.py"])
    assert s.mode == "full" and s.trigger == "new_file"


def test_deleted_module_fails_closed(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    (root / "alpha" / "util.py").unlink()
    s = sel.select(["alpha/util.py"])
    assert s.mode == "full" and s.trigger == "deleted_module"


def test_mass_change_fails_closed(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    changed = [f"alpha/pad_{i:02d}.py" for i in range(30)]
    s = sel.select(changed)
    assert s.mode == "full" and s.trigger == "mass_change"


def test_ratio_cap_fails_closed(tmp_path: Path):
    root, graph, _ = _setup(tmp_path, max_selected_ratio=0.05)
    sel = ImpactSelector(graph, SelectorConfig(max_selected_ratio=0.05), root=root)
    s = sel.select(["alpha/core.py"])
    assert s.mode == "full" and s.trigger == "mass_change"


def test_empty_change_is_full_by_default(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    s = sel.select([])
    assert s.mode == "full" and s.trigger == "unknown_change_kind"


def test_unknown_path_fails_closed(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    s = sel.select(["alpha/absent.py"])
    assert s.mode == "full" and s.trigger == "unknown_change_kind"


def test_determinism_ten_repeats(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    hashes = {sel.select(["alpha/core.py"]).selection_hash for _ in range(10)}
    assert len(hashes) == 1


def test_effective_plan_modes(tmp_path: Path):
    root, graph, _ = _setup(tmp_path)
    shadow = ImpactSelector(graph, SelectorConfig(mode="shadow"), root=root)
    s = shadow.select(["beta/calc.py"])
    plan = shadow.effective_plan(s)
    assert plan["executed"] == "full" and plan["subset_size"] < plan["full_size"]

    enforcing = ImpactSelector(graph, SelectorConfig(mode="enforcing"), root=root)
    s2 = enforcing.select(["beta/calc.py"])
    plan2 = enforcing.effective_plan(s2)
    assert plan2["executed"] == "subset" and len(plan2["tests"]) == len(s2.tests)

    off = ImpactSelector(graph, SelectorConfig(mode="off"), root=root)
    s3 = off.select(["beta/calc.py"])
    assert off.effective_plan(s3)["executed"] == "full"


def test_fail_closed_matrix_ten_of_ten():
    res = run_matrix()
    assert res["classes_total"] >= 10
    assert res["classes_fail_closed"] == res["classes_total"], [
        r for r in res["rows"] if not r["ok"]
    ]
    assert res["criterion_C3"] is True
    control = {r["name"]: r for r in res["rows"]}
    assert control["control_subset"]["ok"] is True
    assert control["control_docs_only"]["ok"] is True


def test_ledger_record_stats_and_reproduce(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    ledger = Ledger(tmp_path / "runs" / "ledger.jsonl")
    s = sel.select(["beta/calc.py"], mode="shadow")
    plan = sel.effective_plan(s)
    row = ledger.record(s, plan, duration_ms=1.5)
    assert row["schema"].endswith("ledger/v1")
    assert row["changed_hash"]

    s2 = sel.select(["alpha/newness_absent.py"], mode="shadow")
    ledger.record(s2, sel.effective_plan(s2), duration_ms=2.0)

    stats = ledger.stats()
    assert stats["records"] == 2 and stats["shadow_records"] == 2

    rep = ledger.reproduce(row["run_id"], graph, sel)
    assert rep["found"] and rep["match"] and rep["graph_unchanged"] and rep["config_unchanged"]

    # the ledger is append-only JSONL: every line is parseable on its own
    lines = [json.loads(x) for x in (tmp_path / "runs" / "ledger.jsonl").read_text().splitlines()]
    assert len(lines) == 2


def test_ledger_rerecord_reproduces_for_cwd_independence(tmp_path: Path):
    root, graph, sel = _setup(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")
    s = sel.select([str(root / "beta" / "calc.py")], mode="enforcing")  # absolute input
    ledger.record(s, sel.effective_plan(s), duration_ms=0.5)
    assert s.changed_files == ["beta/calc.py"]  # normalised to repo relative