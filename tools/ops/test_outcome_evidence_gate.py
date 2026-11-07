#!/usr/bin/env python3
"""outcome_evidence_gate 的杀伤性测试面（SPEC v1.2 §7.1 E 级强制）。

QUALITY_BAR R1 适用于本门自身：每条判据至少一个「应拒样例」必须变红，
同时给正例防止门恒假。夹具域名一律合成（synthetic-alpha 等），零领域语义。

跑法：python -m pytest tools/ops/test_outcome_evidence_gate.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from outcome_evidence_gate import (  # noqa: E402
    CANDIDATE, CONFIRMED, FAIL, PASS, REJECTED, DeclarationError,
    OutcomeDeclaration, evaluate, evaluate_all, gate_rules_doc, summarize,
)

REASON = "按 v1.2 判据逐条复算并留存溯源记录，结论可检查"


def _decl(**kw) -> OutcomeDeclaration:
    base = dict(outcome_id="out-1", mission_id="m-1", grade="S3",
                evidence_level="E2", artifacts=("artifacts/run/report.json",),
                reason=REASON, baseline="baseline-v1",
                novelty_search_scope="检索来源: 合成索引库 SYN-1..2；时段 2020-2026",
                domain="synthetic-alpha")
    base.update(kw)
    return OutcomeDeclaration(**base)


# --- 正例：门不能恒假 -------------------------------------------------------
def test_valid_s3_e2_is_confirmed_and_countable():
    v = evaluate(_decl())
    assert v["verdict"] == PASS, v["rules"]
    assert v["classification"] == CONFIRMED and v["countable"] is True


def test_s5_high_evidence_is_confirmed():
    v = evaluate(_decl(grade="S5", evidence_level="E3"))
    assert v["verdict"] == PASS
    assert v["classification"] == CONFIRMED


def test_s4_declared_candidate_is_registrable_but_uncountable():
    v = evaluate(_decl(grade="S4", evidence_level="E1", declared_confirmed=False))
    assert v["verdict"] == PASS
    assert v["classification"] == CANDIDATE and v["countable"] is False


# --- 应拒样例 ①「无证据」 --------------------------------------------------
@pytest.mark.mutation
def test_mutation_no_evidence_e0_is_red():
    v = evaluate(_decl(evidence_level="E0"))
    assert v["verdict"] == FAIL
    assert "G1" in v["failed_rules"]
    assert v["classification"] == REJECTED and v["countable"] is False


@pytest.mark.mutation
def test_mutation_e1_without_artifacts_is_red():
    """E1 定义为「有可检查制品」：自报 E1 而制品为空 = 伪证据。"""
    v = evaluate(_decl(evidence_level="E1", artifacts=()))
    assert v["verdict"] == FAIL
    assert "G1" in v["failed_rules"]


# --- 应拒样例 ②「低报高」 --------------------------------------------------
@pytest.mark.mutation
def test_mutation_s5_with_e2_is_red():
    v = evaluate(_decl(grade="S5", evidence_level="E2"))
    assert v["verdict"] == FAIL
    assert "G2" in v["failed_rules"]
    assert v["countable"] is False


@pytest.mark.mutation
def test_mutation_s5_candidate_flag_does_not_bypass_e3():
    """v1.2 收紧：S5/E<3 不得以「候选」旗标绕过 E≥3。"""
    v = evaluate(_decl(grade="S5", evidence_level="E2", declared_confirmed=False))
    assert v["verdict"] == FAIL and "G2" in v["failed_rules"]


@pytest.mark.mutation
def test_mutation_s4_confirmed_without_baseline_is_red():
    v = evaluate(_decl(grade="S4", evidence_level="E2", baseline=""))
    assert v["verdict"] == FAIL and "G4" in v["failed_rules"]


# --- 应拒样例 ③「无理由等级」 ----------------------------------------------
@pytest.mark.mutation
@pytest.mark.parametrize("reason", ["", "   ", "做完了"])
def test_mutation_grade_without_reason_is_red(reason):
    v = evaluate(_decl(reason=reason))
    assert v["verdict"] == FAIL
    assert "G3" in v["failed_rules"]


@pytest.mark.mutation
def test_mutation_s5_without_novelty_scope_is_red():
    v = evaluate(_decl(grade="S5", evidence_level="E3", novelty_search_scope=""))
    assert v["verdict"] == FAIL and "G5" in v["failed_rules"]


@pytest.mark.mutation
def test_mutation_negative_grade_s_n_below_e2_is_red():
    v = evaluate(_decl(grade="S-N", evidence_level="E1"))
    assert v["verdict"] == FAIL and "G6" in v["failed_rules"]


# --- 标签空间与统计口径 ----------------------------------------------------
def test_unknown_labels_rejected_at_construction():
    with pytest.raises(DeclarationError):
        _decl(grade="S9")
    with pytest.raises(DeclarationError):
        _decl(evidence_level="E5")


def test_summary_excludes_candidates_and_rejects_from_counts():
    decls = [
        _decl(outcome_id="a"),                                   # confirmed
        _decl(outcome_id="b", grade="S4", evidence_level="E1",
              declared_confirmed=False),                         # candidate
        _decl(outcome_id="c", evidence_level="E0"),              # rejected
        _decl(outcome_id="d", grade="S5", evidence_level="E2"),  # rejected (low)
    ]
    s = summarize(evaluate_all(decls))
    assert s["total"] == 4 and s["confirmed"] == 1
    assert s["candidate"] == 1 and s["rejected"] == 2
    assert s["countable"] == 1
    assert s["failed_rule_counts"]["G1"] == 1
    assert s["failed_rule_counts"]["G2"] == 1


def test_rule_catalogue_covers_every_rule():
    codes = {r["rule"] for r in gate_rules_doc()}
    assert codes == {"G1", "G2", "G3", "G4", "G5", "G6"}


# --- 零领域语义守卫（域词禁硬编码） ---------------------------------------
DOMAIN_LEAK_WORDS = (
    "resistance", "seakeeping", "seaworthiness", "manoeuvring", "maneuvering",
    "propulsion", "hydrostatics", "cavitation", "ship", "hull", "suboff",
    "wigley", "ice-going", "fatigue",
)


def test_module_source_is_domain_neutral():
    src = (HERE / "outcome_evidence_gate.py").read_text(encoding="utf-8").lower()
    leaks = [w for w in DOMAIN_LEAK_WORDS if re.search(rf"\b{re.escape(w)}\b", src)]
    assert leaks == [], f"模块出现领域词: {leaks}"
    # 夹具域名为合成标签
    assert _decl().domain.startswith("synthetic-")