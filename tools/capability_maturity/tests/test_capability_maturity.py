"""P250-N02 能力成熟化升级框架测试（域无关；合成域名作夹具）。

覆盖：审计锚一致性(A1)、升级路径覆盖率(A3)、判据不可事后调整、
独立提供方强制、误差裁决、证据链完整性门(A4)、负控(A5)、只升不降不变量。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.capability_maturity import (
    PREDECLARED, AcceptanceCriteria, EvidenceChain, MaturityUpgradeEngine,
    ProbeRegistry, coverage, load_audit_table, parse_audit_text, plan_paths,
    run_trial,
)
from tools.capability_maturity.model import AuditRow, AuditTable
from tools.capability_maturity.paths import STAGE_SPECS
from tools.capability_maturity.register import compare_with_renderer
from tools.capability_maturity.trials import synthetic_kernels as K
from tools.capability_maturity.trials.probes import (
    TRIAL_SEED, build_registry, build_trial_specs, trial_audit_table,
)

FIXTURE_TEXT = """能力清单 (3 个；证据行 2；分歧 OVERCLAIM 1 / OK 2):
alpha.solve                自述 REAL          对账 WITHHELD      OVERCLAIM  域 合成
beta.stub                  自述 STUB          对账 STUB          OK         域 合成
gamma.real                 自述 REAL          对账 REAL          OK         域 合成
[真值来源] /tmp/fixture.json
[台账契约] status=OK path=fixture.json sha256=deadbeef schema=2.0 未证实=1 OVERCLAIM=1 reasons=-
"""


# ── 审计锚 ─────────────────────────────────────────────────────────────
def test_parse_audit_text_rows_levels_and_footer():
    table = parse_audit_text(FIXTURE_TEXT)
    assert table.counted == 3
    assert [r.cap_id for r in table.rows] == ["alpha.solve", "beta.stub", "gamma.real"]
    assert table.rows[0].declared == "REAL" and table.rows[0].reconciled == "WITHHELD"
    assert table.rows[0].divergence == "OVERCLAIM"
    assert table.rows[0].domain == "合成"
    assert table.source_path == "/tmp/fixture.json"
    assert table.contract["status"] == "OK" and table.contract["sha256"] == "deadbeef"
    assert table.self_consistent()["ok"] is True


def test_A1_audit_table_matches_renderer_exactly():
    """A1：审计表与 ``audit_capabilities`` 正文逐行一致（21/21）。"""
    table = load_audit_table()
    cmp = compare_with_renderer(table)
    assert len(table) == 21, f"期望 21 项能力，实得 {len(table)}"
    assert table.counted == 21
    assert cmp["symmetric_difference"] == 0, cmp
    assert cmp["level_mismatch"] == {} and cmp["pass"] is True
    # 自检：解析行数 = 渲染器自报条数 = 唯一 id 数
    assert table.self_consistent()["ok"] is True


def test_audit_row_upgradable_flags():
    row = AuditRow("x", "REAL", "MOCK", "OVERCLAIM", "合成")
    assert row.upgradable and not row.is_real and row.gap_rank == 2
    real = AuditRow("y", "REAL", "REAL", "OK", "合成")
    assert (not real.upgradable) and real.is_real and real.gap_rank == 0


# ── 升级路径锚 ─────────────────────────────────────────────────────────
def test_A3_paths_cover_all_non_real_capabilities():
    table = load_audit_table()
    paths = plan_paths(table.rows, PREDECLARED)
    cov = coverage(paths, table.rows)
    assert cov["pass"] is True, cov
    assert cov["coverage_pct"] == 100.0
    assert cov["required_count"] == len(table.upgradable_rows()) == 20
    for p in paths:
        assert [s["stage"] for s in p.stages] == [s[0] for s in STAGE_SPECS]
        assert all(s["tolerance_pct"] == 15.0 for s in p.stages)
        assert p.to_level == "REAL" and p.criteria_sha256 == PREDECLARED.sha256()


def test_paths_marked_blocked_keep_all_stages():
    table = parse_audit_text(FIXTURE_TEXT)
    paths = plan_paths(table.rows, PREDECLARED,
                       blockers={"alpha.solve": ("域边界",)}, done={"beta.stub": ["algorithm"]})
    by_id = {p.capability_id: p for p in paths}
    assert by_id["alpha.solve"].blocked is True
    assert {s["status"] for s in by_id["alpha.solve"].stages} == {"blocked"}
    assert len(by_id["alpha.solve"].stages) == len(STAGE_SPECS)
    assert by_id["beta.stub"].stage_status()["algorithm"] == "done"
    assert by_id["beta.stub"].stage_status()["benchmark"] == "planned"


# ── 判据（预声明、不可事后调） ────────────────────────────────────────
def test_criteria_are_predeclared_and_hashed():
    assert PREDECLARED.tolerance_pct == 15.0
    assert PREDECLARED.min_cases == 3
    assert PREDECLARED.required_chain == ("test_report", "benchmark_data", "error_analysis")
    assert len(PREDECLARED.sha256()) == 64
    # 同参数 → 同指纹；改容差 → 指纹必变（事后调阈值会被机器发现）
    assert AcceptanceCriteria().sha256() == PREDECLARED.sha256()
    tweaked = AcceptanceCriteria(tolerance_pct=20.0)
    assert tweaked.sha256() != PREDECLARED.sha256()


# ── 执行器裁决 ─────────────────────────────────────────────────────────
def test_trial_rejects_same_provider_reference():
    reg = ProbeRegistry()
    reg.register("impl", "same-provider", "implementation", lambda x: x)
    reg.register("ref", "same-provider", "reference", lambda x: x * 2)
    spec = {"capability_id": "synth.x", "implementation": "impl", "reference": "ref",
            "cases": [{"case_id": f"c{i}", "args": [i]} for i in range(3)]}
    trial = run_trial(spec, reg, PREDECLARED)
    assert trial.verdict == "rejected_not_independent"


def test_trial_rejects_too_few_cases():
    reg = ProbeRegistry()
    reg.register("impl", "a", "implementation", lambda x: x)
    reg.register("ref", "b", "reference", lambda x: x)
    spec = {"capability_id": "synth.x", "implementation": "impl", "reference": "ref",
            "cases": [{"case_id": "c0", "args": [1]}]}
    assert run_trial(spec, reg, PREDECLARED).verdict == "rejected_insufficient_cases"


def test_trial_rejects_over_tolerance_and_no_improvement():
    reg = ProbeRegistry()
    reg.register("impl", "a", "implementation", lambda x: x * 1.5)     # 50% 偏
    reg.register("ref", "b", "reference", lambda x: float(x))
    spec = {"capability_id": "synth.x", "implementation": "impl", "reference": "ref",
            "cases": [{"case_id": f"c{i}", "args": [i + 1]} for i in range(3)]}
    trial = run_trial(spec, reg, PREDECLARED)
    assert trial.verdict == "rejected_criteria" and trial.max_error_pct == pytest.approx(50.0)

    reg2 = ProbeRegistry()
    reg2.register("impl", "a", "implementation", lambda x: float(x))
    reg2.register("base", "a", "baseline", lambda x: float(x))          # 旧实现已达标
    reg2.register("ref", "b", "reference", lambda x: float(x))
    spec2 = dict(spec, baseline="base")
    assert run_trial(spec2, reg2, PREDECLARED).verdict == "rejected_no_improvement"


# ── 证据链门 ───────────────────────────────────────────────────────────
def test_evidence_chain_completeness_gate():
    chain = EvidenceChain("synth.x")
    chain.add("benchmark_data", "b.json", {"k": 1})
    assert chain.complete(PREDECLARED)["complete"] is False
    chain.add("error_analysis", "e.json", {"k": 2})
    chain.add("test_report", "t.json", {"k": 3})
    assert chain.complete(PREDECLARED)["complete"] is True
    assert len(chain.digest()) == 64
    # 内容变了 → 摘要变（防手改）
    other = EvidenceChain("synth.x")
    other.add("benchmark_data", "b.json", {"k": 999})
    assert other.artifacts[0].sha256 != chain.artifacts[0].sha256


# ── 试验：升级后实现 vs 独立参考 ───────────────────────────────────────
def _run_spec(cap_id):
    engine = MaturityUpgradeEngine(trial_audit_table(), PREDECLARED, build_registry())
    spec = next(s for s in build_trial_specs() if s["capability_id"] == cap_id)
    return engine.execute(spec)


def test_trial_cholesky_matches_independent_reference():
    trial = _run_spec("synth.linalg.cholesky_solve")
    assert trial.verdict == "passed", trial.reasons
    assert trial.max_error_pct < 1e-8
    assert trial.max_baseline_error_pct > PREDECLARED.tolerance_pct   # 旧实现确实不达标
    assert trial.independence["independent"] is True


def test_trial_normal_ppf_matches_independent_reference():
    trial = _run_spec("synth.stats.normal_ppf")
    assert trial.verdict == "passed", trial.reasons
    assert trial.max_error_pct < 1e-6
    assert trial.max_baseline_error_pct > PREDECLARED.tolerance_pct
    # 旧 STUB：恒 ±1.96 → 与 p 无关（结构性缺陷已消失）
    assert K.baseline_normal_ppf(0.6) == K.baseline_normal_ppf(0.9)


def test_trial_adaptive_simpson_matches_independent_reference():
    trial = _run_spec("synth.integrate.adaptive_simpson")
    assert trial.verdict == "passed", trial.reasons
    assert trial.max_error_pct < 1e-6
    assert trial.max_baseline_error_pct > PREDECLARED.tolerance_pct


def test_trial_mean_reference_negative_control():
    trial = _run_spec("synth.stats.mean_reference")
    assert trial.verdict == "passed" and trial.max_error_pct == 0.0


def test_trial_seed_is_recorded_and_deterministic():
    assert TRIAL_SEED == 20261110
    a = build_trial_specs()[0]["cases"]
    b = build_trial_specs()[0]["cases"]
    assert json.dumps(a) == json.dumps(b)   # 同种子 → 同用例


# ── 引擎裁定 ───────────────────────────────────────────────────────────
def _engine_with_rows(rows):
    table = AuditTable(rows=list(rows), counted=len(rows))
    return MaturityUpgradeEngine(table, PREDECLARED, build_registry())


def _passing_trial(engine, cap_id="synth.stats.normal_ppf"):
    spec = next(s for s in build_trial_specs() if s["capability_id"] == cap_id)
    return engine.execute(spec)


def _full_chain(cap_id):
    chain = EvidenceChain(cap_id)
    for kind in PREDECLARED.required_chain:
        chain.add(kind, f"{kind}.json", {"ok": True})
    return chain


def test_engine_promotes_only_with_complete_chain():
    row = AuditRow("synth.stats.normal_ppf", "STUB", "STUB", "OK", "合成/统计分布")
    engine = _engine_with_rows([row])
    trial = _passing_trial(engine)
    partial = EvidenceChain(row.cap_id)
    partial.add("benchmark_data", "b.json", {"ok": True})
    decision = engine.promote(trial, partial)
    assert decision.status == "rejected_chain_incomplete"
    assert decision.after == "STUB" and not decision.level_changed
    decision = engine.promote(trial, _full_chain(row.cap_id))
    assert decision.status == "promoted" and decision.after == "REAL"
    assert engine.level(row.cap_id) == "REAL"


def test_A5_negative_control_real_level_unchanged():
    row = AuditRow("synth.stats.mean_reference", "REAL", "REAL", "OK", "合成/统计分布")
    engine = _engine_with_rows([row])
    trial = _passing_trial(engine, "synth.stats.mean_reference")
    decision = engine.promote(trial, _full_chain(row.cap_id))
    assert decision.status == "no_op_already_real"
    assert decision.before == decision.after == "REAL" and not decision.level_changed
    assert engine.negative_control(["synth.stats.mean_reference"])["pass"] is True


def test_engine_blocked_by_domain_boundary_and_never_demotes():
    rows = [AuditRow("alpha.solve", "REAL", "MOCK", "OVERCLAIM", "合成/线性代数")]
    engine = _engine_with_rows(rows)
    engine.blockers = {"alpha.solve": ("域边界：实现在领域仓",)}
    trial = _passing_trial(engine)
    decision = engine.promote(trial, _full_chain("alpha.solve"), cap_id="alpha.solve")
    assert decision.status == "blocked_domain_boundary"
    assert decision.after == decision.before          # 不升；更不降
    assert engine.level("alpha.solve") == "MOCK"


def test_real_register_plan_and_blockers():
    table = load_audit_table()
    assert [r.cap_id for r in table.real_rows()] == ["calc.stability"]
    from tools.capability_maturity.run_acceptance import register_blockers
    blockers = register_blockers(table)
    assert set(blockers) == {r.cap_id for r in table.upgradable_rows()}
    assert all("域边界" in " ".join(v) for v in blockers.values())
