#!/usr/bin/env python3
"""Tests for the criteria-drift auditor (p474w1-n02).

Coverage map (node contract items in brackets):

* parser: 8 criterion modes, canonical units, asserted-bound selection,
  tolerance bands, presence criteria ...................... [a]
* matching: paraphrase, benign re-format, no partner stealing
* comparison: relaxation degree, operator downgrade, direction/kind swap,
  tolerance removal, undeclared drop, declared waiver
* audit report: drift degree + impact + recommended actions + stable hash [d]
* campaign gate: blocked / done transitions on the real SQLite mirror .... [f]
* corpora: label consistency, measured accuracy / FPR on both corpora .... [b][c]
* CLI: artifacts are produced and self-consistent ....................... [e]
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.criteria_drift import (  # noqa: E402
    campaign_integration, compare, corpus_synth, evaluate, matching, model, parser, real_corpus,
)


# ───────────────────────── parser ─────────────────────────

def test_parse_error_threshold_percent_is_canonical_ratio():
    c = parser.parse_with_cn_order("相对误差≤3%")
    assert c.kind == model.KIND_ERROR
    assert c.op == "<="
    assert c.value == pytest.approx(0.03)          # 3% -> ratio
    assert c.display_value == pytest.approx(3.0)   # written number preserved
    assert c.unit == model.UNIT_FRACTION


def test_parse_convergence_order_modes():
    a = parser.parse_with_cn_order("收敛阶≥2")
    assert (a.kind, a.value, a.unit) == (model.KIND_ORDER, 2.0, model.UNIT_ORDER)
    b = parser.parse_with_cn_order("observed order >= 1.8")
    assert b.kind == model.KIND_ORDER and b.value == pytest.approx(1.8)
    c = parser.parse_with_cn_order("二阶收敛")
    assert c.kind == model.KIND_ORDER and c.value == 2.0


def test_parse_conservation_and_pvalue_modes():
    cons = parser.parse_with_cn_order("质量守恒偏差≤1e-12")
    assert cons.kind == model.KIND_CONSERVATION and cons.value == pytest.approx(1e-12)
    p = parser.parse_with_cn_order("p值≥0.05")
    assert p.kind == model.KIND_PVALUE and p.value == pytest.approx(0.05)
    q = parser.parse_with_cn_order("p-value < 0.01")
    assert q.kind == model.KIND_PVALUE and q.op == "<"


def test_parse_coverage_and_count_units():
    cov = parser.parse_with_cn_order("覆盖率≥80%")
    assert cov.kind == model.KIND_COVERAGE and cov.value == pytest.approx(0.80)
    cnt = parser.parse_with_cn_order("pytest≥15例")
    assert cnt.kind == model.KIND_COUNT and cnt.unit == model.UNIT_COUNT
    assert cnt.value == 15.0


def test_parse_selects_asserted_bound_not_context_numbers():
    # d/L>2 is context; the asserted bound is |sigma-1| <= 0.05
    c = parser.parse_with_cn_order("d/L>2时|σ-1|≤0.05")
    assert c.value == pytest.approx(0.05) and c.op == "<="
    assert c.extras                            # context number is auditable
    # the companion bound inside parentheses must not win
    d = parser.parse_with_cn_order("网格加密SCF变化≤2%(GCI≤3%)")
    assert d.value == pytest.approx(0.02)


def test_parse_presence_never_invents_a_threshold():
    c = parser.parse_with_cn_order("报告包含六要素")
    assert c.kind == model.KIND_PRESENCE and c.value is None and c.op == model.PRESENCE_OP
    d = parser.parse_with_cn_order("几何前置门Cb/L/B/∇/S")
    assert d.kind == model.KIND_PRESENCE and d.value is None


def test_parse_tolerance_band():
    c = parser.parse_with_cn_order("相对误差≤3%±0.5%")
    assert c.value == pytest.approx(0.03)
    assert c.tolerance == pytest.approx(0.005)


# ───────────────────────── matching / comparison ─────────────────────────

def _audit(proposal, report, waivers=None):
    rep = {"acceptance_criteria": list(report)}
    if waivers:
        rep["waivers"] = waivers
    return compare.audit({"acceptance_criteria": list(proposal)}, rep,
                         proposal_ref="proposal", report_ref="report")


def test_benign_unit_reformat_is_not_drift():
    for prop, rep in [("相对误差≤3%", "相对误差 <= 0.03"),
                      ("覆盖率≥80%", "覆盖率 >= 0.8"),
                      ("观测阶 >= 1.8阶", "observed  order >= 1.8阶")]:
        a = _audit([prop], [rep])
        assert not a.block, (prop, rep, a.as_dict()["rows"])


def test_cross_language_paraphrase_still_matches():
    a = _audit(["相对误差≤3%"], ["relative error <= 3%"])
    assert not a.block
    assert len(a.rows) == 1 and a.rows[0].report is not None


def test_relaxation_is_quantified_and_carries_impact_and_action():
    a = _audit(["相对误差≤3%"], ["相对误差≤5%"])
    assert a.block
    row = a.rows[0]
    assert row.relaxation_ratio == pytest.approx((0.05 - 0.03) / 0.03)
    assert row.status == "significant" and "threshold_relaxed" in row.reasons
    assert "5" in row.impact and "回退" in row.action
    assert a.summary()["max_relaxation"] > 0.10


def test_relaxation_below_policy_threshold_is_not_drift():
    a = _audit(["相对误差≤3%"], ["相对误差≤3.2%"])
    assert not a.block
    assert a.rows[0].relaxation_ratio == pytest.approx(0.2 / 3.0, rel=1e-3)


def test_operator_downgrade_is_significant():
    a = _audit(["观测阶 > 2阶"], ["观测阶 >= 2阶"])
    assert a.block
    assert a.rows[0].operator_downgrade and "operator_downgrade" in a.rows[0].reasons


def test_declared_waiver_is_not_drift_but_missing_approver_is():
    waivers = [{"criterion": "相对误差≤3%", "reason": "参考值换锚",
                "approved_by": "gate-owner"}]
    ok = _audit(["相对误差≤3%"], ["相对误差≤6%"], waivers)
    assert not ok.block and ok.rows[0].status == "declared_waiver"
    assert ok.rows[0].waiver is not None

    bad = _audit(["相对误差≤3%"], ["相对误差≤6%"],
                 [{"criterion": "相对误差≤3%", "reason": "参考值换锚", "approved_by": ""}])
    assert bad.block, "a waiver without an approver is not a declaration"


def test_undeclared_drop_is_significant_with_impact():
    a = _audit(["相对误差≤3%", "p值≥0.05"], ["p值≥0.05"])
    assert a.block and len(a.significant) == 1
    row = a.significant[0]
    assert row.report is None and "dropped_undeclared" in row.reasons
    assert row.impact and row.action


def test_presence_criterion_drop_is_not_auto_flagged():
    a = _audit(["报告包含六要素", "相对误差≤3%"], ["相对误差≤3%"])
    assert not a.block
    assert any("dropped_unjudgeable" in r.reasons for r in a.rows)


def test_added_stricter_criterion_is_not_drift():
    a = _audit(["相对误差≤3%"], ["相对误差≤3%", "质量守恒偏差≤1e-12"])
    assert not a.block
    assert any(r.status == "added" for r in a.rows)


def test_compound_criterion_losing_one_bound_is_drift():
    prop = "占比>30% 且 merge<50%"
    assert parser.is_compound(prop)
    assert not parser.is_compound("d/L>2时|σ-1|≤0.05")   # context number is not a bound
    a = _audit([prop], ["占比>30%"])
    assert a.block, a.as_dict()["rows"]


def test_tolerance_removal_is_drift():
    a = _audit(["相对误差≤3%±0.5%"], ["相对误差≤3%"])
    assert a.block and "tolerance_removed" in a.rows[0].reasons


def test_exact_assignment_does_not_steal_partners():
    """Two criteria of the same kind must not swap partners (greedy did)."""
    proposal = ["积分类误差≤3%", "守恒类残差≤1%"]
    report = ["守恒类残差≤1%", "积分类误差≤3%"]
    a = _audit(proposal, report)
    assert not a.block, a.as_dict()["rows"]
    assert all(r.report is not None for r in a.rows)


# ───────────────────────── audit report ─────────────────────────

def test_audit_report_contains_degree_impact_action_and_hash():
    a = _audit(["相对误差≤3%", "p值≥0.05"], ["相对误差≤6%"])
    d = a.as_dict()
    for key in ("summary", "rows", "recommended_actions", "block_reason", "audit_sha256", "policy"):
        assert key in d
    assert d["summary"]["drift_score"] > 0 and d["summary"]["block"] is True
    assert d["recommended_actions"] and d["block_reason"].startswith("criteria_drift:")
    assert len(d["audit_sha256"]) == 64


def test_audit_hash_is_stable_and_details_do_not_change_verdict():
    a1 = _audit(["相对误差≤3%", "p值≥0.05"], ["相对误差≤6%", "p值≥0.05"])
    a2 = _audit(["相对误差≤3%", "p值≥0.05"], ["p值≥0.05", "相对误差≤6%"])  # reordered
    assert a1.audit_sha256() == a2.audit_sha256()
    assert a1.block == a2.block is True


def test_block_reason_is_bounded_and_self_describing():
    a = _audit(["相对误差≤3%", "p值≥0.05", "质量守恒偏差≤1e-12"],
               ["相对误差≤6%", "p值≥0.04"])
    reason = a.block_reason()
    assert len(reason) <= campaign_integration.MAX_BLOCK_REASON
    assert "significant=" in reason and "max_relax=" in reason and "audit=" in reason


# ───────────────────────── campaign gate ─────────────────────────

def _seed(conn, status="in-flight"):
    campaign_integration.ensure_schema(conn)
    conn.execute("INSERT INTO campaign_nodes(plan_id,node_id,name,status) VALUES(1,'n1','n1',?)",
                 (status,))
    conn.commit()


def test_gate_blocks_drifted_node_and_writes_reason():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    a = _audit(["相对误差≤3%"], ["相对误差≤6%"])
    out = campaign_integration.apply_sqlite(conn, 1, "n1", a, "pass")
    assert out["before"]["status"] == "in-flight"
    assert out["after"]["status"] == "blocked"
    assert out["after"]["reproduction_verdict"] is None
    assert out["after"]["block_reason"].startswith("criteria_drift:")
    assert out["audit_sha256"] == a.audit_sha256()
    row = conn.execute("SELECT status, block_reason FROM campaign_nodes WHERE node_id='n1'").fetchone()
    assert row[0] == "blocked" and row[1]
    conn.close()


def test_gate_lets_clean_node_become_done_and_failed_stays_failed():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    conn.execute("INSERT INTO campaign_nodes(plan_id,node_id,name,status) VALUES(1,'n2','n2','in-flight')")
    conn.commit()
    clean = _audit(["相对误差≤3%"], ["相对误差≤3%"])
    pass_out = campaign_integration.apply_sqlite(conn, 1, "n1", clean, "pass")
    fail_out = campaign_integration.apply_sqlite(conn, 1, "n2", clean, "fail")
    assert pass_out["after"]["status"] == "done" and pass_out["gated"] is False
    assert fail_out["after"]["status"] == "failed"
    conn.close()


def test_gate_requires_an_existing_node():
    conn = sqlite3.connect(":memory:")
    campaign_integration.ensure_schema(conn)
    with pytest.raises(KeyError):
        campaign_integration.apply_sqlite(conn, 1, "nope", _audit(["a≤3%"], ["a≤3%"]))
    conn.close()


def test_verdict_transition_is_pure_and_total():
    clean = _audit(["相对误差≤3%"], ["相对误差≤3%"])
    t = campaign_integration.verdict_transition(clean, "pass")
    assert (t["gated"], t["status"], t["reproduction_verdict"]) == (False, "done", "pass")
    with pytest.raises(ValueError):
        campaign_integration.verdict_transition(clean, "maybe")


# ───────────────────────── corpora ─────────────────────────

def test_synthetic_labels_agree_with_rendered_artifacts():
    docs = corpus_synth.generate_corpus(n_docs=25, seed=4242)
    for doc in docs:
        for case in doc.criteria:
            if case.mutation in ("relax", "relax_mild") and case.report_text:
                delta = corpus_synth.parsed_relaxation(case.proposal_text, case.report_text)
                assert delta is not None
                assert case.label_drift == (delta > 0.10), (doc.doc_id, case)
            if case.mutation == "drop":
                assert case.report_text == "" and case.label_drift is True
            if case.mutation == "none":
                assert case.label_drift is False


def test_synthetic_accuracy_and_fpr_meet_targets():
    res = evaluate.evaluate_all(n_docs=60, seed=20261111, repo_root=ROOT)
    doc = res["synthetic_full"]["document"]
    pair = res["synthetic_full"]["pair"]
    assert doc["doc_accuracy"] >= 0.95, doc
    assert doc["doc_false_positive_rate"] <= 0.05, doc
    assert pair["pair_accuracy"] >= 0.95, pair
    assert pair["pair_false_positive_rate"] <= 0.05, pair


def test_real_corpus_harvest_and_mutation_families():
    real = real_corpus.harvest(ROOT)
    assert len(real) >= 20, "campaign node exports must be present"
    real_kinds = {r.criterion.kind for r in real}
    synth_kinds = {case.kind for doc in corpus_synth.generate_corpus(n_docs=20, seed=7)
                   for case in doc.criteria}
    required = {model.KIND_ERROR, model.KIND_ORDER, model.KIND_CONSERVATION,
                model.KIND_PVALUE, model.KIND_COVERAGE}
    assert required <= (real_kinds | synth_kinds), (real_kinds, synth_kinds)
    assert len(real_kinds - {model.KIND_PRESENCE}) >= 3, real_kinds

    cases = real_corpus.build_cases(real)
    by_family = {}
    for c in cases:
        by_family.setdefault(c.family, []).append(c)
    assert {"identity", "relax_12", "relax_05", "drop_undeclared", "drop_declared"} <= set(by_family)
    pol = matching.load_policy()
    for c in by_family["identity"]:
        a = compare.audit({"acceptance_criteria": c.proposal}, {"acceptance_criteria": c.report},
                          proposal_ref=c.case_id, report_ref=c.case_id + "/r", policy=pol)
        assert not a.block
    # the label is the relaxation actually written in the artifact, so a
    # zero-scale threshold (>= 0) cannot be labelled drift and must not block
    for family in ("relax_12", "relax_05"):
        for c in by_family.get(family, []):
            a = compare.audit({"acceptance_criteria": c.proposal}, {"acceptance_criteria": c.report},
                              proposal_ref=c.case_id, report_ref=c.case_id + "/r", policy=pol)
            assert a.block == c.label_drift, (family, c.detail, c.label_drift)


# ───────────────────────── CLI / artifacts ─────────────────────────

def test_gate_cli_blocks_drift_and_allows_clean(tmp_path):
    proposal = tmp_path / "proposal.json"
    drifted = tmp_path / "report_drift.json"
    clean = tmp_path / "report_clean.json"
    proposal.write_text(json.dumps({"acceptance_criteria": ["相对误差≤3%", "p值≥0.05"]}), encoding="utf-8")
    drifted.write_text(json.dumps({"acceptance_criteria": ["相对误差≤6%", "p值≥0.05"]}), encoding="utf-8")
    clean.write_text(json.dumps({"acceptance_criteria": ["相对误差≤3%", "p值≥0.05"]}), encoding="utf-8")
    db = tmp_path / "campaign.db"
    conn = sqlite3.connect(db)
    campaign_integration.ensure_schema(conn)
    conn.execute("INSERT INTO campaign_nodes(plan_id,node_id,name,status) VALUES(1,'n1','n1','in-flight')")
    conn.execute("INSERT INTO campaign_nodes(plan_id,node_id,name,status) VALUES(1,'n2','n2','in-flight')")
    conn.commit(); conn.close()

    from tools.criteria_drift import gate_cli
    rc_block = gate_cli.main(["--proposal", str(proposal), "--report", str(drifted),
                              "--plan-id", "1", "--node-id", "n1", "--sqlite", str(db), "--quiet"])
    rc_ok = gate_cli.main(["--proposal", str(proposal), "--report", str(clean),
                           "--plan-id", "1", "--node-id", "n2", "--sqlite", str(db), "--quiet"])
    assert rc_block == 3 and rc_ok == 0
    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT node_id, status FROM campaign_nodes").fetchall())
    reasons = dict(conn.execute("SELECT node_id, block_reason FROM campaign_nodes").fetchall())
    conn.close()
    assert rows == {"n1": "blocked", "n2": "done"}
    assert reasons["n1"].startswith("criteria_drift:") and reasons["n2"] is None


def test_cli_produces_self_consistent_artifacts(tmp_path):
    out = tmp_path / "criteria_drift"
    proc = subprocess.run([sys.executable, "-m", "tools.criteria_drift.run_audit",
                           "--out-dir", str(out), "--n-docs", "20", "--skip-figures"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["schema"] == "criteria-drift-audit/1.0"
    assert metrics["synthetic"]["document"]["doc_accuracy"] >= 0.95
    assert metrics["real"]["overall"]["false_positive_rate"] <= 0.05
    assert metrics["demo"]["identity_control"]["block"] is False
    assert metrics["gate"]["drifted_node"]["after"]["status"] == "blocked"
    assert metrics["gate"]["clean_node"]["after"]["status"] in ("done", "in-flight")
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## 6 误差判定" in report and "是否 <3% 误差" in report
    assert (out / "audit_demo.json").exists() and (out / "campaign_gate_demo.json").exists()