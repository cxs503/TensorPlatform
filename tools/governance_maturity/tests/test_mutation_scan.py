"""Tests for the AST mutation scanner (synthetic modules only)."""

from __future__ import annotations

import ast

import pytest

from tools.governance_maturity.mutation_scan import (
    ALL_OPERATORS,
    CONTROL_OPERATORS,
    Mutant,
    TargetSpec,
    aggregate,
    bootstrap_interval,
    enumerate_mutants,
    prepare_sandbox,
    render_mutant,
    wilson_interval,
)

SOURCE = '''\
def pick(flag: bool, items, a, b):
    ready = False
    total = 2
    if flag < 1:
        total = total + 1
    return items[1] + g(a, b) + total
'''


def _only(source: str, operator: str) -> Mutant:
    mutants = [m for m in enumerate_mutants("t", source, operators=(operator,))]
    assert mutants, f"no mutant for {operator}"
    return mutants[0]


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #
def test_enumeration_finds_every_operator():
    mutants = enumerate_mutants("t", SOURCE)
    seen = {m.operator for m in mutants}
    assert set(ALL_OPERATORS) <= seen


def test_control_sampling_is_capped():
    mutants = enumerate_mutants("t", SOURCE, max_controls=2)
    controls = [m for m in mutants if m.kind == "control"]
    assert len(controls) <= 2
    assert all(m.kind == "semantic" for m in mutants if m.operator not in CONTROL_OPERATORS)


def test_mutant_ids_unique():
    mutants = enumerate_mutants("t", SOURCE)
    ids = [m.mutant_id for m in mutants]
    assert len(ids) == len(set(ids))


def test_mutant_dict_roundtrip():
    mutant = _only(SOURCE, "scale_constant")
    restored = Mutant.from_dict(mutant.as_dict())
    assert restored == mutant


# --------------------------------------------------------------------------- #
# rendering / operator semantics
# --------------------------------------------------------------------------- #
def test_render_scales_constant():
    mutant = _only("def f():\n    return 2\n", "scale_constant")
    rendered = render_mutant("def f():\n    return 2\n", mutant)
    assert rendered is not None
    assert "2000.0" in rendered
    ast.parse(rendered)


def test_render_shifts_index():
    source = "def f(items):\n    return items[1]\n"
    mutant = _only(source, "shift_index")
    rendered = render_mutant(source, mutant)
    assert "items[2]" in rendered


def test_render_negates_comparison():
    source = "def f(x):\n    if x < 1:\n        return 1\n    return 0\n"
    mutant = _only(source, "negate_condition")
    rendered = render_mutant(source, mutant)
    assert ">=" in rendered


def test_render_deletes_statement():
    source = "def f():\n    x = 1\n    return x\n"
    mutants = [m for m in enumerate_mutants("t", source, operators=("delete_statement",))]
    mutant = next(m for m in mutants if len(m.path) >= 4)
    rendered = render_mutant(source, mutant)
    assert "x = 1" not in rendered
    assert "return x" in rendered


def test_render_swaps_arguments():
    source = "def f(a, b):\n    return g(a, b)\n"
    mutant = _only(source, "swap_args")
    rendered = render_mutant(source, mutant)
    assert "g(b, a)" in rendered


def test_render_flips_boolean():
    source = "def f():\n    return True\n"
    mutant = _only(source, "flip_bool")
    rendered = render_mutant(source, mutant)
    assert "False" in rendered


def test_render_insert_pass_is_behaviour_preserving():
    source = "def f():\n    return 3\n"
    mutant = _only(source, "insert_pass")
    rendered = render_mutant(source, mutant)
    namespace: dict = {}
    exec(compile(source, "<pristine>", "exec"), namespace)
    pristine = namespace["f"]()
    namespace = {}
    exec(compile(rendered, "<mutant>", "exec"), namespace)
    assert namespace["f"]() == pristine


def test_render_dead_branch_is_behaviour_preserving():
    source = "def f():\n    return 3\n"
    mutant = _only(source, "dead_branch")
    rendered = render_mutant(source, mutant)
    namespace: dict = {}
    exec(compile(source, "<pristine>", "exec"), namespace)
    pristine = namespace["f"]()
    namespace = {}
    exec(compile(rendered, "<mutant>", "exec"), namespace)
    assert namespace["f"]() == pristine


def test_render_rejects_out_of_range_path():
    mutant = _only("def f():\n    return 1\n", "scale_constant")
    bogus = Mutant("t::x", "t", "scale_constant", "semantic", 1, (("f", "nope"),))
    assert render_mutant("def f():\n    return 1\n", bogus) is None
    assert render_mutant("def f():\n    return 1\n", mutant) is not None


# --------------------------------------------------------------------------- #
# statistics / aggregation
# --------------------------------------------------------------------------- #
def test_wilson_interval_bounds():
    lo, hi = wilson_interval(0, 10)
    assert 0.0 <= lo <= hi <= 1.0
    assert hi < 0.3
    lo, hi = wilson_interval(10, 10)
    assert 0.6 < lo <= 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_bootstrap_interval_brackets_rate():
    lo, hi = bootstrap_interval({"a": (8, 10), "b": (9, 10)}, n_boot=500)
    assert 0.0 <= lo <= 0.9 <= hi <= 1.0


def _ledger(target, shard, n_shards, statuses, controls=("survived",)):
    results = [
        {
            "mutant_id": f"{target}::m{i}",
            "operator": "scale_constant",
            "kind": "semantic",
            "lineno": i,
            "status": status,
            "duration_s": 0.1,
            "returncode": 1 if status == "killed" else 0,
            "output_tail": "",
        }
        for i, status in enumerate(statuses)
    ]
    results += [
        {
            "mutant_id": f"{target}::c{i}",
            "operator": "insert_pass",
            "kind": "control",
            "lineno": i,
            "status": status,
            "duration_s": 0.1,
            "returncode": 0 if status == "survived" else 1,
            "output_tail": "",
        }
        for i, status in enumerate(controls)
    ]
    return {
        "schema": "mutation-shard/1.0",
        "target": target,
        "repo": "RepoA",
        "module": "m.py",
        "module_sha256": "sha256:" + "a" * 64,
        "test_cmd": ["pytest"],
        "shard": shard,
        "n_shards": n_shards,
        "n_mutants_total": 10,
        "n_mutants_selected": len(statuses),
        "baseline": {"status": "pass", "returncode": 0, "duration_s": 0.1, "output_tail": ""},
        "results": results,
    }


def test_aggregate_kill_rate_and_control_precision():
    ledgers = [
        _ledger("a", 1, 2, ["killed", "killed", "survived"]),
        _ledger("a", 2, 2, ["killed", "survived"]),
        _ledger("b", 1, 1, ["killed"] * 3),
    ]
    out = aggregate(ledgers, expected_shards={"a": 2, "b": 1})
    a = out["targets"]["a"]
    assert a["killed"] == 3 and a["survived"] == 2
    assert a["kill_rate"] == pytest.approx(0.6)
    assert a["control_precision"] == 1.0
    assert a["shards_complete"] is True
    assert out["overall"]["all_shards_complete"] is True
    assert out["overall"]["repos"] == ["RepoA"]
    assert out["overall"]["kill_rate"] == pytest.approx(6 / 8)


def test_aggregate_flags_missing_shard():
    ledgers = [_ledger("a", 1, 2, ["killed"]), _ledger("b", 1, 1, ["survived"])]
    out = aggregate(ledgers, expected_shards={"a": 2, "b": 1})
    assert out["targets"]["a"]["shards_complete"] is False
    assert out["overall"]["all_shards_complete"] is False


def test_aggregate_counts_timeouts_separately():
    ledgers = [_ledger("a", 1, 1, ["killed", "timeout", "survived"])]
    out = aggregate(ledgers)
    a = out["targets"]["a"]
    assert a["timeout"] == 1
    assert a["kill_rate"] == pytest.approx(0.5)


def test_aggregate_lists_survivors():
    ledgers = [_ledger("a", 1, 1, ["survived", "killed"])]
    out = aggregate(ledgers)
    assert len(out["targets"]["a"]["survivors"]) == 1
    assert out["targets"]["a"]["survivors"][0]["status"] == "survived"

def test_sandbox_copies_do_not_alias_the_source(tmp_path):
    """Regression guard: a hardlinked sandbox let mutants write the original file."""
    import os

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    target = TargetSpec(
        name="t", repo="R", repo_root=".", module="pkg/mod.py",
        test_cmd=("true",), copy_paths=("pkg/mod.py",),
    )
    sandbox = tmp_path / "sandbox"
    prepare_sandbox(target, repo, sandbox)
    source_inode = os.stat(repo / "pkg" / "mod.py").st_ino
    sandbox_inode = os.stat(sandbox / "pkg" / "mod.py").st_ino
    assert source_inode != sandbox_inode
    (sandbox / "pkg" / "mod.py").write_text("X = 2\n", encoding="utf-8")
    assert (repo / "pkg" / "mod.py").read_text(encoding="utf-8") == "X = 1\n"
