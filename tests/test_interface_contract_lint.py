#!/usr/bin/env python3
"""interface_contract_lint 单测（合成域名：alpha_key/unit_x —— 零领域语义）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops/taskplane"))

import interface_contract_lint as lint  # noqa: E402


def _plan(*spec):
    """spec: (id, ext_id, deps)"""
    return [{"id": i, "node_type": "task", "status": "in-flight",
             "dependencies": list(deps), "metadata": {"ext_id": ext}}
            for i, ext, deps in spec]


def _contract(nodes):
    return {"schema_version": "synthetic/v1", "nodes": nodes}


def _rules(report):
    return [f["rule"] for f in report["findings"]]


def test_clean_contract_ok():
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B",
              "requires": [{"key": "alpha_key", "type": "number", "unit": "unit_x"}]},
    })
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is True
    assert rep["counts"]["errors"] == 0
    assert rep["counts"]["nodes"] == 2


def test_l1_unresolved_node_id():
    plan = _plan((1, "N-A", []))
    contract = _contract({"a": {"node_id": 99, "ext_id": "N-A"}})
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert "L1_node_resolved" in _rules(rep)


def test_l2_duplicate_identity():
    plan = _plan((1, "N-A", []), (2, "N-B", []))
    contract = _contract({"a": {"node_id": 1, "ext_id": "N-A"},
                          "b": {"node_id": 1, "ext_id": "N-A"}})
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert rep["rule_violations"]["L2_identity_unique"] >= 2


def test_l3_dependency_without_contract():
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    contract = _contract({"b": {"node_id": 2, "ext_id": "N-B",
                                "requires": ["alpha_key"]}})
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert "L3_dep_contract_present" in _rules(rep)


def test_l4_requires_declared_by_nobody():
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B", "requires": ["beta_key"]},
    })
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert "L4_requires_declared" in _rules(rep)


def test_l5_unit_mismatch_across_edge():
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B",
              "requires": [{"key": "alpha_key", "type": "number", "unit": "unit_y"}]},
    })
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert "L5_unit_type_conflict" in _rules(rep)


def test_l5_ambiguous_declaration_same_key_two_units():
    plan = _plan((1, "N-A", []), (2, "N-B", []))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_y"}}},
    })
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert any("ambiguous_declaration" in f["detail"] for f in rep["findings"])


def test_l6_self_supplied_requirement():
    plan = _plan((1, "N-A", []))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}},
              "requires": ["alpha_key"]},
    })
    rep = lint.lint_contract(contract, plan)
    assert rep["ok"] is False
    assert "L6_orphan_requires" in _rules(rep)


def test_lint_is_deterministic():
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    contract = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B",
              "requires": [{"key": "alpha_key", "type": "number", "unit": "unit_x"}]},
    })
    a = json.dumps(lint.lint_contract(contract, plan), sort_keys=True)
    b = json.dumps(lint.lint_contract(contract, plan), sort_keys=True)
    assert a == b


def test_cli_exit_codes(tmp_path):
    plan = _plan((1, "N-A", []), (2, "N-B", [1]))
    good = _contract({
        "a": {"node_id": 1, "ext_id": "N-A",
              "produces": {"alpha_key": {"type": "number", "unit": "unit_x"}}},
        "b": {"node_id": 2, "ext_id": "N-B",
              "requires": [{"key": "alpha_key", "type": "number", "unit": "unit_x"}]},
    })
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"plan": {"id": 1}, "nodes": plan}), encoding="utf-8")
    cpath = tmp_path / "contract.json"
    cpath.write_text(json.dumps(good), encoding="utf-8")
    out = tmp_path / "lint.json"
    assert lint.main(["--contract", str(cpath), "--snapshot", str(snap),
                      "--json-out", str(out)]) == 0
    assert json.loads(out.read_text())["ok"] is True

    broken = _contract({"a": {"node_id": 42, "ext_id": "N-A"}})
    cpath.write_text(json.dumps(broken), encoding="utf-8")
    assert lint.main(["--contract", str(cpath), "--snapshot", str(snap),
                      "--json-out", str(out)]) == 2
    assert lint.main(["--contract", str(tmp_path / "absent.json"),
                      "--snapshot", str(snap)]) == 3


def test_render_markdown_flags_design_only():
    plan = _plan((1, "N-A", []))
    md = lint.render_markdown(lint.lint_contract(_contract({"a": {"node_id": 1,
                                                                 "ext_id": "N-A"}}), plan))
    assert "不代表证据面已可验收" in md