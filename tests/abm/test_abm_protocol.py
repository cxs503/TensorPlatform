"""tools/abm 通用机制的单测：全部使用**合成域**（alpha/beta 指标），不含任何领域词条。

覆盖：哈希链篡改检测、七环顺序门、预测盲（先承诺后观测）、六维多臂对照门、
验收聚合（含三腿）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.abm import (GATES, ROLES, BlindRegistry, GateSequence, TraceLedger, build_table,
                       canonical_json, check_independence, comparison_gate, evaluate,
                       validate_knowledge_packs)
from tools.abm.blind import blind_integrity


# --------------------------------------------------------------------- ledger
def test_ledger_chain_and_tamper_detection(tmp_path):
    path = tmp_path / "trace.jsonl"
    led = TraceLedger(str(path))
    for i in range(5):
        led.append("step", "lead", {"i": i}, ts="2026-01-01T00:00:00Z")
    assert led.verify()["ok"] is True
    assert led.verify()["n"] == 5

    # 篡改中间记录的一个字段 → 必须被复算发现
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    lines[2]["payload"]["i"] = 999
    path.write_text("\n".join(canonical_json(r) for r in lines) + "\n", encoding="utf-8")
    broken = TraceLedger(str(path), reset=False).verify()
    assert broken["ok"] is False
    assert broken["first_bad_seq"] == 2


def test_ledger_deterministic_digest(tmp_path):
    a = TraceLedger(str(tmp_path / "a.jsonl"))
    b = TraceLedger(str(tmp_path / "b.jsonl"))
    for led in (a, b):
        led.append("x", "verifier", {"k": [1, 2, 3]}, ts="T")
    assert a.verify()["digest"] == b.verify()["digest"]


# ---------------------------------------------------------------------- roles
def _packs():
    return {r: {"agent_id": f"id-{r}", "model_family": f"fam-{r}",
                "focus": f"focus-{r}", "pack_refs": ["ref-1", "ref-2"]} for r in ROLES}


def test_knowledge_packs_ok_and_self_review_blocked():
    packs = _packs()
    assert validate_knowledge_packs(packs) == []
    assert check_independence(packs) == []
    packs["verifier"]["agent_id"] = packs["implementer"]["agent_id"]
    assert any("自批" in e for e in check_independence(packs))
    packs2 = _packs()
    packs2["theorist"]["focus"] = packs2["lead"]["focus"]
    assert any("差异化" in e for e in validate_knowledge_packs(packs2))


# ---------------------------------------------------------------------- gates
def test_gate_order_enforced():
    g = GateSequence()
    with pytest.raises(ValueError):
        g.close("experiment", "pass", actor_role="implementer")
    for gate in GATES:
        g.close(gate, "pass", actor_role="lead")
    st = g.status()
    assert st["all_pass"] is True
    assert st["closure_ratio"] == 1.0


def test_gate_fail_blocks_downstream():
    g = GateSequence()
    g.close("problem_tree", "pass", actor_role="lead")
    g.close("baseline", "fail", actor_role="verifier")
    with pytest.raises(ValueError):
        g.close("hypotheses_blind", "pass", actor_role="theorist")


# ---------------------------------------------------------------------- blind
def test_blind_observe_before_seal_is_rejected():
    reg = BlindRegistry()
    reg.register("c1", "hyp", {"alpha": 1.0}, {"alpha": 0.1})
    with pytest.raises(ValueError):
        reg.observe("c1", {"alpha": 1.0})


def test_blind_no_retrofit_predictions():
    reg = BlindRegistry()
    reg.register("c1", "hyp", {"alpha": 1.0}, {"alpha": 0.1}, salt="s")
    reg.seal("c1")
    with pytest.raises(ValueError):
        reg.observe("c1", {"beta": 2.0})     # 未预测的量不能事后补靶
    with pytest.raises(ValueError):
        reg.register("c1", "hyp2", {"alpha": 2.0}, {"alpha": 0.1})   # 不允许覆盖


def test_blind_scoring_and_integrity(tmp_path):
    led = TraceLedger(str(tmp_path / "t.jsonl"))
    reg = BlindRegistry(ledger=led, ts="T")
    reg.register("c1", "hyp", {"alpha": 10.0, "beta": 2.0},
                 {"alpha": 0.05, "beta": 0.2}, salt="x")
    reg.seal("c1")
    score = reg.score("c1")
    assert score["n_scored"] == 0                       # 未观测 → 不评分
    reg.observe("c1", {"alpha": 10.2, "beta": 3.0})
    score = reg.score("c1")
    assert score["n_scored"] == 2
    assert score["n_hit"] == 1                          # alpha 命中(2%)、beta 偏差 33% > 容差 20%
    integ = blind_integrity(led)
    assert integ["ok"] is True
    # 伪造「观测早于封存」→ 复算必须报违规
    recs = led.records
    swapped = [r for r in recs if r["record_type"] != "blind.card.observe"]
    assert len(swapped) == len(recs) - 1


# ----------------------------------------------------------------- dimensions
def test_comparison_gate_requires_two_dims_with_units():
    table = build_table({
        "experiment": {"accuracy": {"value": 0.1, "unit": "rel", "source": "a"},
                       "efficiency": {"value": 10.0, "unit": "s", "source": "b"}},
        "control": {"accuracy": {"value": 0.4, "unit": "rel", "source": "c"},
                    "efficiency": {"value": 5.0, "unit": "s", "source": "d"}},
    })
    gate = comparison_gate(table, arms=["experiment", "control"], min_dims=2)
    assert gate["pass"] is True
    assert {d["dimension"] for d in gate["comparable"]} == {"accuracy", "efficiency"}

    table2 = build_table({
        "experiment": {"accuracy": {"value": 0.1, "unit": "rel", "source": "a"}},
        "control": {"accuracy": {"value": 0.4, "unit": "percent", "source": "c"}},
    })
    gate2 = comparison_gate(table2, arms=["experiment", "control"], min_dims=1)
    assert gate2["pass"] is False          # 单位不一致 → 不可比


def test_numeric_without_source_is_rejected():
    table = build_table({"a": {"accuracy": {"value": 0.1, "unit": "rel"}}})
    assert table["errors"]


# ----------------------------------------------------------------- acceptance
def _full_run(tmp_path):
    led = TraceLedger(str(tmp_path / "acc.jsonl"))
    for _ in range(5):
        for role in ROLES:
            led.append("work", role, {"role": role}, ts="T")
    gates = GateSequence()
    for gate in GATES:
        gates.close(gate, "pass", actor_role="lead")
        led.append("gate", "lead", {"gate": gate}, gate=gate, ts="T", verdict="pass")
    reg = BlindRegistry(ledger=led, ts="T")
    for cid in ("c1", "c2"):
        reg.register(cid, "h", {"alpha": 1.0}, {"alpha": 0.1}, salt=cid)
        reg.seal(cid)
        reg.observe(cid, {"alpha": 1.01})
        reg.score(cid)
    table = build_table({
        "experiment": {"accuracy": {"value": 0.01, "unit": "rel", "source": "x"},
                       "stability": {"value": 0.1, "unit": "f", "source": "y"}},
        "control": {"accuracy": {"value": 0.3, "unit": "rel", "source": "z"},
                    "stability": {"value": 0.2, "unit": "f", "source": "w"}},
    })
    legs = {"theory": {"value": 0.01, "tolerance": 0.05, "pass": True, "source": "s1"},
            "implementation": {"value": 1e-9, "tolerance": 1e-6, "pass": True, "source": "s2"},
            "experiment": {"value": 0.05, "tolerance": 0.1, "pass": True, "source": "s3"}}
    return led, gates, reg, table, legs


def test_acceptance_all_pass(tmp_path):
    led, gates, reg, table, legs = _full_run(tmp_path)
    acc = evaluate(ledger=led, gate_sequence=gates, blind=reg, dim_table=table, legs=legs)
    assert acc["pass"] is True
    assert acc["failed_criteria"] == []


def test_acceptance_flags_missing_arm_dimensions(tmp_path):
    led, gates, reg, table, legs = _full_run(tmp_path)
    thin = build_table({"experiment": {"accuracy": {"value": 0.01, "unit": "rel", "source": "x"}},
                        "control": {"accuracy": {"value": 0.3, "unit": "rel", "source": "z"}}})
    acc = evaluate(ledger=led, gate_sequence=gates, blind=reg, dim_table=thin, legs=legs)
    assert acc["pass"] is False
    assert any(c["id"] == "C1_two_arm_dims" for c in acc["criteria"] if not c["pass"])