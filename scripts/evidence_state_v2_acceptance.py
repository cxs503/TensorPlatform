"""证据等级引擎 v2（五态 + 不可判态）验收实验。

产出（零手抄：所有数字由本脚本程序化写入 JSON，报告只从 JSON 取值）：
    artifacts/evidence_state_v2/scenario_matrix.json   场景矩阵与逐条判决
    artifacts/evidence_state_v2/provenance_chain.json  哈希链审计日志
    artifacts/evidence_state_v2/tamper_detection.json  篡改检测结果
    artifacts/evidence_state_v2/transition_matrix.json 状态转换统计
    artifacts/evidence_state_v2/metrics.json           C1–C7 验收判据数字
    artifacts/evidence_state_v2/figures/decision_map.png   判决分区图（含锚点）
    artifacts/evidence_state_v2/figures/state_delta.png   朴素规则 vs fail-closed 状态分布

运行：
    PYTHONPATH=src python scripts/evidence_state_v2_acceptance.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
import numpy as np  # noqa: E402

from capability_ledger_auto.anchors import (  # noqa: E402
    FAIL,
    PASS,
    UNDECIDABLE as ANCHOR_UNDECIDABLE,
    VerificationAnchor,
    judge_anchor,
)
from capability_ledger_auto.provenance import (  # noqa: E402
    DOWNGRADE,
    HOLD,
    INITIAL,
    REJECTED,
    UNDECIDABLE_LOCK,
    UPGRADE,
    ProvenanceChain,
)
from capability_ledger_auto.scanner import (  # noqa: E402
    CodeEvidence,
    ScanResult,
    TestEvidence,
)
from capability_ledger_auto.state_machine import (  # noqa: E402
    ADMISSIBLE_RANK,
    BLOCK_UNDECIDABLE,
    DEFAULT_POLICY,
    EXTENDED_STATES,
    RULES,
    STATE_SEMANTICS,
    UNDECIDABLE,
    CapabilityAlreadyRegistered,
    Counterexample,
    DowngradeWithoutTrigger,
    EvidenceLevelLedger,
    EvidencePolicy,
    ManualOverrideRejected,
    Observation,
    gate_decision,
)

OUT = ROOT / "artifacts" / "evidence_state_v2"
FIGDIR = OUT / "figures"
CLOCK = lambda: "2026-11-10T00:00:00+00:00"  # noqa: E731
TOL = DEFAULT_POLICY.tolerance


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def anchor(aid, kind="analytic", err=0.02, unc=0.0, key=None, has_ref=True):
    return VerificationAnchor(
        anchor_id=aid, kind=kind, rel_error=err, uncertainty=unc,
        independence_key=key if key is not None else aid, has_reference_value=has_ref,
    )


def real_anchors():
    return (
        anchor("anc-analytic-1", "analytic", 0.02, 0.0, "ds-ref-1"),
        anchor("anc-experiment-2", "experiment", 0.06, 0.01, "ds-ref-2"),
        anchor("anc-benchmark-3", "benchmark", 0.10, 0.0, "ds-ref-3"),
    )


def impl_obs(cap, **kw):
    base = dict(
        capability_id=cap, has_implementation=True, has_tests=True,
        has_reports=True, test_pass_rate=0.95, trace_count=6,
    )
    base.update(kw)
    return Observation(**base)


# ── 场景矩阵（合成能力名，零领域语义） ─────────────────────────
def build_scenarios():
    dup = (
        anchor("dup-a", "analytic", 0.02, 0.0, "shared-dataset"),
        anchor("dup-b", "experiment", 0.03, 0.0, "shared-dataset"),
        anchor("dup-c", "benchmark", 0.04, 0.0, "shared-dataset"),
    )
    same_kind = (
        anchor("sk-a", "analytic", 0.02, 0.0, "k1"),
        anchor("sk-b", "analytic", 0.03, 0.0, "k2"),
        anchor("sk-c", "analytic", 0.04, 0.0, "k3"),
    )
    scenarios = [
        ("S01_no_trace", Observation(capability_id="synth.none"), "NOT_SUPPORTED"),
        ("S02_stub_marker", Observation(
            capability_id="synth.stub", has_implementation=True, trace_count=2,
            stub_markers=("STUB: NotImplementedError",)), "STUB"),
        ("S03_no_anchor", impl_obs("synth.noanchor", has_tests=False), "WITHHELD"),
        ("S04_one_anchor", impl_obs("synth.one", anchors=real_anchors()[:1]), "WITHHELD"),
        ("S05_two_anchors", impl_obs("synth.two", anchors=real_anchors()[:2]), "WITHHELD"),
        ("S06_three_independent", impl_obs("synth.three", anchors=real_anchors()), "REAL"),
        ("S07_dup_source", impl_obs("synth.dup", anchors=dup), "WITHHELD"),
        ("S08_single_kind", impl_obs("synth.singlekind", anchors=same_kind), "WITHHELD"),
        ("S09_test_failure", impl_obs("synth.tf", test_pass_rate=0.40), "MOCK"),
        ("S10_counterexample", impl_obs(
            "synth.cex", anchors=real_anchors(),
            counterexamples=(Counterexample("cex-a", "新算例否证",
                                            "a" * 64, rel_error=0.40),)), "MOCK"),
        ("S11_undecidable_band", impl_obs(
            "synth.band", anchors=(anchor("u-band", "experiment", 0.15, 0.04, "ds-u1"),)),
            UNDECIDABLE),
        ("S12_undecidable_no_ref", impl_obs(
            "synth.noref", anchors=(anchor("u-noref", "reference", None, 0.0, "ds-u2",
                                           has_ref=False),)), UNDECIDABLE),
        ("S13_adverse_undecidable", impl_obs(
            "synth.adverse", anchors=real_anchors()
            + (anchor("u-adv", "field", 0.22, 0.10, "ds-u3"),)), UNDECIDABLE),
        ("S14_partial_undecidable_supportive", impl_obs(
            "synth.partial", anchors=real_anchors()
            + (anchor("u-sup", "field", 0.13, 0.03, "ds-u4"),)), "REAL"),
    ]
    return scenarios


def scenario_matrix():
    ledger = EvidenceLevelLedger(clock=CLOCK)
    rows = []
    for name, obs, expected in build_scenarios():
        d = ledger.register(obs)
        rows.append({
            "scenario": name,
            "capability_id": obs.capability_id,
            "expected_state": expected,
            "actual_state": d.state,
            "match": d.state == expected,
            "rule_id": d.rule_id,
            "rule": RULES[d.rule_id],
            "reasons": list(d.reasons),
            "gate": gate_decision(d.state),
            "admissible_rank": d.admissible_rank,
            "criteria": {k: v for k, v in d.criteria.items() if k != "policy"},
            "anchor_verdicts": list(d.anchor_verdicts),
        })
    return ledger, rows


# ── 判据数字 ────────────────────────────────────────────────────
def decision_region_statistics(step=0.005, err_max=0.40, unc_max=0.12):
    """扫描 (rel_error, uncertainty) 平面：朴素规则 vs 不确定度带规则。"""
    errs = np.arange(0.0, err_max + step / 2, step)
    uncs = np.arange(0.0, unc_max + step / 2, step)
    naive = np.zeros((uncs.size, errs.size))  # 1=PASS 0=FAIL
    banded = np.zeros((uncs.size, errs.size))  # 2=PASS 1=UNDECIDABLE 0=FAIL
    for i, u in enumerate(uncs):
        for j, e in enumerate(errs):
            a = anchor("grid", "analytic", float(e), float(u), "grid")
            v = judge_anchor(a, tol=TOL, coverage_z=DEFAULT_POLICY.coverage_z)
            naive[i, j] = 1.0 if e < TOL else 0.0
            banded[i, j] = {PASS: 2.0, ANCHOR_UNDECIDABLE: 1.0, FAIL: 0.0}[v.verdict]
    naive_pass = int(naive.sum())
    banded_pass = int((banded == 2.0).sum())
    undecidable_cells = int((banded == 1.0).sum())
    flips = int(((naive == 1.0) & (banded != 2.0)).sum())
    total = int(naive.size)
    return {
        "grid": {"err_max": err_max, "unc_max": unc_max, "step": step,
                 "cells": total, "err_points": int(errs.size), "unc_points": int(uncs.size)},
        "tolerance": TOL,
        "coverage_z": DEFAULT_POLICY.coverage_z,
        "naive_pass_cells": naive_pass,
        "banded_pass_cells": banded_pass,
        "undecidable_cells": undecidable_cells,
        "naive_pass_downgraded_to_undecidable": flips,
        "naive_pass_downgraded_fraction": round(flips / naive_pass, 6) if naive_pass else 0.0,
        "banded_pass_fraction": round(banded_pass / total, 6),
        "_grids": (errs, uncs, banded),
    }


def transition_statistics():
    """同一能力沿「发现→积累→反例→再验证」序列的状态转换统计。"""
    ledger = EvidenceLevelLedger(clock=CLOCK)
    seq = [
        impl_obs("synth.lifecycle", has_tests=False),
        impl_obs("synth.lifecycle", anchors=real_anchors()[:1]),
        impl_obs("synth.lifecycle", anchors=real_anchors()[:2]),
        impl_obs("synth.lifecycle", anchors=real_anchors()),
        impl_obs("synth.lifecycle", anchors=real_anchors(),
                 counterexamples=(Counterexample("cex-life", "新算例否证", "b" * 64,
                                                 rel_error=0.55),)),
        impl_obs("synth.lifecycle", anchors=real_anchors()),
        impl_obs("synth.lifecycle", anchors=real_anchors()
                 + (anchor("u-life", "field", 0.25, 0.08, "ds-life"),)),
    ]
    obs0 = seq.pop(0)
    ledger.register(obs0)
    for obs in seq:
        ledger.observe(obs)
    hist = ledger.history("synth.lifecycle")
    counts = {t: 0 for t in (INITIAL, UPGRADE, DOWNGRADE, HOLD, UNDECIDABLE_LOCK, REJECTED)}
    for e in hist:
        counts[e.transition] += 1
    final_state = ledger.state_of("synth.lifecycle")
    # 无触发证据的降级必须被拒：独立台账，REAL → 测试失败（无 evidence_digests）
    guard = EvidenceLevelLedger(clock=CLOCK)
    guard.register(impl_obs("synth.guard", anchors=real_anchors()))
    downgrade_without_trigger_rejected = False
    try:
        guard.observe(Observation(capability_id="synth.guard", has_implementation=True,
                                  has_tests=True, test_pass_rate=0.0, trace_count=3))
    except DowngradeWithoutTrigger:
        downgrade_without_trigger_rejected = True
    return ledger, {
        "sequence": [{"prev": e.prev_state, "new": e.new_state, "rule_id": e.rule_id,
                      "transition": e.transition, "reasons": list(e.reasons)} for e in hist],
        "transition_counts": counts,
        "downgrade_without_trigger_rejected": downgrade_without_trigger_rejected,
        "guard_state_after_reject": guard.state_of("synth.guard"),
        "guard_history_len": len(guard.chain),
        "final_state": final_state,
    }


def override_statistics():
    ledger = EvidenceLevelLedger(clock=CLOCK)
    ledger.register(impl_obs("synth.override", anchors=real_anchors()[:1]))
    attempts = []
    for requested in ("REAL", "MOCK", "WITHHELD"):
        try:
            ledger.manual_override("synth.override", requested, actor="human_operator",
                                   reason="attempt")
            attempts.append({"requested": requested, "rejected": False})
        except ManualOverrideRejected as exc:
            attempts.append({"requested": requested, "rejected": True, "code": exc.code})
    state_after = ledger.state_of("synth.override")
    rejected_entries = [e for e in ledger.chain.entries if e.outcome == REJECTED]
    return {
        "attempts": attempts,
        "rejected_count": sum(1 for a in attempts if a["rejected"]),
        "state_before": "WITHHELD",
        "state_after": state_after,
        "state_unchanged": state_after == "WITHHELD",
        "audited_rejected_entries": len(rejected_entries),
        "all_new_state_none": all(e.new_state is None for e in rejected_entries),
        "all_rule_ids_machine": all(e.rule_id in RULES for e in rejected_entries),
    }, ledger


def discovery_statistics():
    ledger = EvidenceLevelLedger(clock=CLOCK)
    scan_good = ScanResult(
        capability_id="synth.disc_good", code_count=2, test_count=1, report_count=1,
        has_implementation=True, has_tests=True, has_reports=True, test_pass_rate=0.96,
        report_verdicts=["PASS"],
        code_evidence=[CodeEvidence("/w/mod.py", 10, "function", "def solve", "h1", "h2")],
        test_evidence=[TestEvidence("/w/test_mod.py", "test_solve", 0.96, 25, 24, "h3")],
    )
    scan_bad = ScanResult(
        capability_id="synth.disc_bad", code_count=1, test_count=1, has_implementation=True,
        has_tests=True, test_pass_rate=0.25,
    )
    scan_empty = ScanResult(capability_id="synth.disc_empty")
    rows = []
    for scan, anchors in ((scan_good, real_anchors()), (scan_bad, ()), (scan_empty, ())):
        d = ledger.register(Observation.from_scan(scan, anchors=anchors))
        rows.append({
            "capability_id": scan.capability_id,
            "trace_count": scan.code_count + scan.test_count + scan.report_count,
            "test_pass_rate": scan.test_pass_rate,
            "initial_state": d.state,
            "rule_id": d.rule_id,
            "transition": d.transition,
        })
    duplicate_rejected = False
    try:
        ledger.register(Observation(capability_id="synth.disc_good"))
    except CapabilityAlreadyRegistered:
        duplicate_rejected = True
    return {"initial_states": rows, "re_registration_rejected": duplicate_rejected}, ledger


def provenance_statistics():
    ledger = EvidenceLevelLedger(clock=CLOCK)
    ledger.register(impl_obs("synth.audit", has_tests=False))
    ledger.observe(impl_obs("synth.audit", anchors=real_anchors()[:2]))
    ledger.observe(impl_obs("synth.audit", anchors=real_anchors()))
    ledger.observe(impl_obs("synth.audit", anchors=real_anchors(),
                            counterexamples=(Counterexample("cex-audit", "反例", "c" * 64,
                                                            rel_error=0.60),)))
    ok, bad = ledger.verify()
    forged = list(ledger.chain.entries)
    forged[2] = replace(forged[2], new_state="MOCK")
    ok_forged, bad_forged = ledger.chain.verify_entries(forged)
    payload = ledger.chain.to_dict()
    _dump(OUT / "provenance_chain.json", payload)
    _dump(OUT / "tamper_detection.json", {
        "original_chain_valid": ok,
        "original_first_bad_seq": bad,
        "forged_chain_valid": ok_forged,
        "forged_first_bad_seq": bad_forged,
        "tamper_field": "entries[2].new_state REAL→MOCK",
        "head_hash": ledger.chain.head_hash,
        "entry_count": len(ledger.chain),
    })
    return {
        "entry_count": len(ledger.chain),
        "chain_valid": ok,
        "first_bad_seq": bad,
        "tamper_detected": (ok_forged is False),
        "tamper_first_bad_seq": bad_forged,
        "head_hash": ledger.chain.head_hash,
        "roundtrip_valid": ProvenanceChain.from_dict(payload).verify() == (True, None),
        "states_in_history": [e.new_state for e in ledger.chain.entries],
    }


# ── 图 ──────────────────────────────────────────────────────────
def plot_decision_map(region, path: Path):
    errs, uncs, banded = region["_grids"]
    fig, ax = plt.subplots(figsize=(7.6, 5.2), dpi=150)
    cmap = ListedColormap(["#c62828", "#f9a825", "#2e7d32"])
    ax.imshow(banded, origin="lower", aspect="auto", cmap=cmap, vmin=-0.5, vmax=2.5,
              extent=[errs.min(), errs.max(), uncs.min(), uncs.max()], alpha=0.75,
              interpolation="nearest")
    ax.axvline(TOL, color="k", ls="--", lw=1.2, label=f"tolerance = {TOL:.2f}")
    ax.plot([0.0, TOL], [TOL / DEFAULT_POLICY.coverage_z] * 2, color="k", lw=0.8, ls=":")
    # 场景锚点
    for name, obs, _ in build_scenarios():
        for a in obs.anchors:
            if a.rel_error is None:
                continue
            v = judge_anchor(a, tol=TOL, coverage_z=DEFAULT_POLICY.coverage_z)
            ax.scatter(a.rel_error, a.uncertainty, s=46, edgecolor="k", zorder=5,
                       color={"PASS": "#1b5e20", "FAIL": "#b71c1c",
                              "UNDECIDABLE": "#e65100"}[v.verdict])
    handles = [
        mpatches.Patch(color="#2e7d32", label="PASS (decisive pass)"),
        mpatches.Patch(color="#f9a825", label="UNDECIDABLE (fail-closed)"),
        mpatches.Patch(color="#c62828", label="FAIL (decisive refutation)"),
        Line2D([], [], marker="o", ls="", color="w", markeredgecolor="k",
               label="scenario anchor"),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=8, framealpha=0.95)
    ax.set_xlabel("relative error  |calc - ref| / ref")
    ax.set_ylabel("reference relative uncertainty (1 sigma)")
    ax.set_title(
        f"Anchor verdict regions  tol={TOL:.2f}, z={DEFAULT_POLICY.coverage_z:.0f}\n"
        f"banded PASS {region['banded_pass_cells']}/{region['grid']['cells']} cells "
        f"vs naive {region['naive_pass_cells']} "
        f"(flips {region['naive_pass_downgraded_to_undecidable']})",
        fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_state_delta(rows, path: Path):
    """朴素规则（忽略不确定度/参考值缺失）vs fail-closed 的状态分布对照。"""
    states = list(EXTENDED_STATES)
    naive_policy = EvidencePolicy(coverage_z=1e-9)  # 不确定度带被压成 0
    # 朴素规则：只按 rel_error 与 tol 判，缺参考值当 PASS（常见过度乐观做法）
    from capability_ledger_auto.state_machine import EvidenceStateMachine
    naive_machine = EvidenceStateMachine(naive_policy)
    naive_counts = {s: 0 for s in states}
    banded_counts = {s: 0 for s in states}
    for name, obs, _ in build_scenarios():
        banded_counts[decide_state(obs)] += 1
        # 朴素：缺参考值的锚剔除（视为无锚）→ 常见做法是直接采信 code+tests
        naive_obs = replace(obs, anchors=tuple(a for a in obs.anchors if a.rel_error is not None))
        naive_counts[naive_machine.evaluate(naive_obs).state] += 1
    x = np.arange(len(states))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8.2, 4.6), dpi=150)
    ax.bar(x - w / 2, [naive_counts[s] for s in states], w, label="naive rule (ignores uncertainty / missing ref)",
           color="#90a4ae", edgecolor="k")
    ax.bar(x + w / 2, [banded_counts[s] for s in states], w, label="fail-closed v2 (uncertainty band)",
           color="#1565c0", edgecolor="k")
    for xi, s in zip(x, states):
        ax.text(xi - w / 2, naive_counts[s] + 0.05, str(naive_counts[s]), ha="center", fontsize=8)
        ax.text(xi + w / 2, banded_counts[s] + 0.05, str(banded_counts[s]), ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(states, rotation=15)
    ax.set_ylabel("scenario count")
    ax.set_title(f"Scenario matrix (n={len(build_scenarios())}): naive rule vs fail-closed state tally", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return {"naive": naive_counts, "fail_closed": banded_counts}


_ENGINE = None


def decide_state(obs):
    global _ENGINE
    if _ENGINE is None:
        from capability_ledger_auto.state_machine import EvidenceStateMachine
        _ENGINE = EvidenceStateMachine()
    return _ENGINE.evaluate(obs).state


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)

    ledger, rows = scenario_matrix()
    _dump(OUT / "scenario_matrix.json", {
        "scenario_count": len(rows),
        "scenarios_matched": sum(1 for r in rows if r["match"]),
        "all_matched": all(r["match"] for r in rows),
        "rows": rows,
    })

    region = decision_region_statistics()
    tstats_ledger, tstats = transition_statistics()
    ostats, override_ledger = override_statistics()
    dstats, discovery_ledger = discovery_statistics()
    pstats = provenance_statistics()
    _dump(OUT / "transition_matrix.json", tstats)

    naive_vs_fc = plot_state_delta(rows, FIGDIR / "state_delta.png")
    plot_decision_map(region, FIGDIR / "decision_map.png")

    metrics = {
        "module": "src/capability_ledger_auto",
        "policy": DEFAULT_POLICY.to_dict(),
        "C1_semantics": {
            "states": list(EXTENDED_STATES),
            "state_count": len(EXTENDED_STATES),
            "definitions": {s: STATE_SEMANTICS[s] for s in EXTENDED_STATES},
            "admissible_rank": dict(ADMISSIBLE_RANK),
            "rule_count": len(RULES),
            "rule_ids": list(RULES),
            "scenarios": len(rows),
            "scenarios_matched": sum(1 for r in rows if r["match"]),
            "state_tally": {
                s: sum(1 for r in rows if r["actual_state"] == s) for s in EXTENDED_STATES
            },
            "naive_vs_fail_closed_tally": naive_vs_fc,
            "pass": all(r["match"] for r in rows),
        },
        "C2_undecidable_fail_closed": {
            "undecidable_rank": ADMISSIBLE_RANK[UNDECIDABLE],
            "not_supported_rank": ADMISSIBLE_RANK["NOT_SUPPORTED"],
            "rank_ordering_ok": ADMISSIBLE_RANK[UNDECIDABLE] < ADMISSIBLE_RANK["NOT_SUPPORTED"],
            "gate": {
                "REAL": gate_decision("REAL"),
                "WITHHELD": gate_decision("WITHHELD"),
                "MOCK": gate_decision("MOCK"),
                "UNDECIDABLE": gate_decision(UNDECIDABLE),
            },
            "gate_undecidable_blocks_even_with_min_rank_0":
                gate_decision(UNDECIDABLE, min_rank=0) == BLOCK_UNDECIDABLE,
            "undecidable_scenarios": [r["scenario"] for r in rows
                                      if r["actual_state"] == UNDECIDABLE],
            "undecidable_scenario_count": sum(1 for r in rows if r["actual_state"] == UNDECIDABLE),
            "region": {k: v for k, v in region.items() if k != "_grids"},
            "pass": ADMISSIBLE_RANK[UNDECIDABLE] < 0
                    and gate_decision(UNDECIDABLE) == BLOCK_UNDECIDABLE
                    and sum(1 for r in rows if r["actual_state"] == UNDECIDABLE) >= 3,
        },
        "C3_upgrade_rules": {
            "min_independent_anchors": DEFAULT_POLICY.min_independent_anchors,
            "min_distinct_anchor_kinds": DEFAULT_POLICY.min_distinct_anchor_kinds,
            "test_pass_threshold": DEFAULT_POLICY.test_pass_threshold,
            "threshold_scenarios": {
                r["scenario"]: {"state": r["actual_state"],
                                "independent_pass": r["criteria"]["independent_decisive_pass_anchors"],
                                "kinds": r["criteria"]["distinct_pass_kinds"]}
                for r in rows if r["scenario"].startswith(("S04", "S05", "S06", "S07", "S08"))
            },
            "duplicate_sources_collapsed": next(
                r["criteria"]["collapsed_same_source_anchors"] for r in rows
                if r["scenario"] == "S07_dup_source"),
            "upgrade_events_in_lifecycle": tstats["transition_counts"][UPGRADE],
            "rule_id_for_real": next(r["rule_id"] for r in rows if r["actual_state"] == "REAL"),
            "pass": next(r["actual_state"] for r in rows if r["scenario"] == "S06_three_independent") == "REAL"
                    and next(r["actual_state"] for r in rows if r["scenario"] == "S05_two_anchors") == "WITHHELD"
                    and next(r["actual_state"] for r in rows if r["scenario"] == "S07_dup_source") == "WITHHELD",
        },
        "C4_downgrade_rules": {
            "downgrade_events": tstats["transition_counts"][DOWNGRADE],
            "sequence": tstats["sequence"],
            "downgrade_without_trigger_rejected": tstats["downgrade_without_trigger_rejected"],
            "downgrade_rule_id": "R03_COUNTEREXAMPLE",
            "reason_recorded": any(
                r["transition"] == DOWNGRADE and r["reasons"] for r in tstats["sequence"]),
            "final_state_after_sequence": tstats["final_state"],
            "pass": tstats["transition_counts"][DOWNGRADE] >= 1
                    and tstats["downgrade_without_trigger_rejected"]
                    and tstats["final_state"] == UNDECIDABLE,
        },
        "C5_no_manual_override": {
            **ostats,
            "pass": ostats["rejected_count"] == 3 and ostats["state_unchanged"]
                    and ostats["all_new_state_none"],
        },
        "C6_discovery_integration": {
            **dstats,
            "pass": all(row["transition"] == INITIAL for row in dstats["initial_states"])
                    and dstats["re_registration_rejected"]
                    and {row["initial_state"] for row in dstats["initial_states"]}
                        >= {"REAL", "MOCK", "NOT_SUPPORTED"},
        },
        "C7_audit_provenance": {
            **pstats,
            "pass": pstats["chain_valid"] and pstats["tamper_detected"]
                    and pstats["roundtrip_valid"] and pstats["entry_count"] >= 4,
        },
        "figures": [
            {"path": "artifacts/evidence_state_v2/figures/decision_map.png",
             "caption": "单锚判决分区：rel_error × 参考值不确定度 → PASS/FAIL/UNDECIDABLE（场景锚点叠加）"},
            {"path": "artifacts/evidence_state_v2/figures/state_delta.png",
             "caption": "场景矩阵 14 例：朴素规则 vs fail-closed v2 的等级分布对照"},
        ],
    }
    metrics["all_criteria_pass"] = all(
        metrics[k]["pass"] for k in metrics if k.startswith("C")
    )
    _dump(OUT / "metrics.json", metrics)

    # 汇总台账 chain（含所有子实验）审计链长度
    chain_entries = sum(len(lg.chain) for lg in (
        ledger, tstats_ledger, override_ledger, discovery_ledger))
    summary = {
        "all_criteria_pass": metrics["all_criteria_pass"],
        "criteria": {k: metrics[k]["pass"] for k in metrics if k.startswith("C")},
        "scenario_count": len(rows),
        "provenance_entries_all_experiments": chain_entries,
        "artifacts": sorted(str(p.relative_to(ROOT)) for p in OUT.rglob("*") if p.is_file()),
    }
    _dump(OUT / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if metrics["all_criteria_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())