"""综合继承门（ops/taskplane/inheritance_gate.py）验收测试。

判据先于实现，机器可裁，全程合成数据（零领域词条  平台铁律）：

1. 判据解析：``<key> <op> <number>[%]`` 四种算符 + 不可解析文本；
2. 判据求值：百分点判据缺 unit → unproven（不猜量纲）；越限 → failed；eq 容差；
3. 全绿快照 → verdict=ready，score>=min_score，四条硬门全 True；
4. 在飞/缺失完成态的节点 → not_ready，阻塞项点名该节点（honest FAIL 不可粉饰）；
5. 伪造完成（done 但无证据 / 有判据无测量值 / 测量值越限）→ 不验收；
6. 跨节点一致性：同一量两源 spread 越容差 → 硬门失败；容差内 → 通过；
   单源量不算交叉裁决（不得白拿分）；
7. 治理分量：治理节点未验收 → 硬门失败（分量不可用或缺省时权重重归一化）；
8. 复现判决纪律：verdict=fail 一律不验收；require_reproduction_verdict 时缺失判决不算完成；
9. 确定性：同一 snapshot+config 两次裁决逐字节相同（input_digest 内容派生）；
10. CLI：not_ready 退出码 2、ready 退出码 0，JSON/MD 双产物落盘；
11. 证据来源：报告 markdown 的 ```inheritance-evidence 围栏可解析，本地工件路径可解析。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.taskplane.inheritance_gate import (
    DEFAULT_WEIGHTS,
    evaluate_criterion,
    evaluate_inheritance_gate,
    extract_evidence,
    load_evidence_file,
    normalize_key,
    parse_criterion,
    parse_criteria,
    render_markdown,
)


# ─────────────────────────── 合成快照工厂 ───────────────────────────

def _node(nid, mid, mission, status="done", verdict="pass", criteria=None,
          name=None, itype=None):
    meta = {"ext_id": None, "acceptance_criteria": criteria or []}
    if itype:
        meta["inheritance_class"] = itype
    return {"id": nid, "milestone_id": mid, "mission_id": mission,
            "name": name or f"node-{nid}", "node_type": "task", "status": status,
            "reproduction_verdict": verdict, "dependencies": [], "metadata": meta}


def _evidence(acceptance=None, quantities=None):
    block = {}
    if acceptance is not None:
        block["acceptance"] = acceptance
    if quantities is not None:
        block["quantities"] = quantities
    return block


def _report(rid, mission, acceptance=None, quantities=None, fence=False):
    ev = _evidence(acceptance, quantities)
    rep = {"id": str(rid), "mission_id": str(mission), "status": "published",
           "created_at": f"2026-01-0{min(int(rid) % 8 + 1, 9)}T00:00:00+00:00"}
    if fence:
        rep["markdown"] = "# report\n\n```inheritance-evidence\n" + json.dumps(ev) + "\n```\n"
    else:
        rep["inheritance_evidence"] = ev
    return rep


def _snapshot(nodes, milestones=None, missions=None, reports=None, plan=None):
    return {
        "plan": plan or {"id": 9, "name": "synthetic-plan"},
        "milestones": milestones if milestones is not None else [
            {"id": 90, "name": "M-A", "status": "in-flight"},
            {"id": 91, "name": "M-B", "status": "in-flight"},
        ],
        "nodes": nodes,
        "missions": missions if missions is not None else {},
        "reports": reports if reports is not None else {},
    }


def _missions(*ids, status="done"):
    return {str(i): {"id": i, "status": status} for i in ids}


def _cfg(**over):
    cfg = {
        "min_score": 0.7,
        "weights": {"milestone": 0.4, "criteria": 0.25, "consistency": 0.2,
                    "governance": 0.15},
        "milestones": [90, 91],
        "consistency": {"tolerance": 0.05, "min_shared_keys": 1},
        "governance": {"node_ids": [4]},
        "require_reproduction_verdict": True,
        "require_mission": True,
        "require_evidence": True,
        "roots": ["."],
    }
    cfg.update(over)
    return cfg


def _green_snapshot(fence: bool = False):
    """4 节点 / 2 里程碑全绿：M-A=节点1,2；M-B=节点3,4（节点4 为治理类）。"""
    nodes = [
        _node(1, 90, 101, criteria=["alpha_err < 8%"]),
        _node(2, 90, 102, criteria=["beta_err <= 0.05"]),
        _node(3, 91, 103, criteria=["gamma_rho = 1"]),
        _node(4, 91, 104, criteria=["delta_gain >= 20"], itype="governance"),
    ]
    missions = _missions(101, 102, 103, 104)
    reports = {
        "101": [_report(1, 101, acceptance={"alpha_err": {"value": 0.061, "unit": "fraction"}},
                        quantities={"q_shared": 1.00, "q_ref": 4.0}, fence=fence)],
        "102": [_report(2, 102, acceptance={"beta_err": {"value": 0.041, "unit": None}},
                        quantities={"q_shared": 1.02, "q_ref": 4.2})],
        "103": [_report(3, 103, acceptance={"gamma_rho": {"value": 1.0, "unit": None}},
                        quantities={"q_shared": 1.01})],
        "104": [_report(4, 104, acceptance={"delta_gain": {"value": 22.0, "unit": None}},
                        quantities={"q_shared": 1.03})],
    }
    return _snapshot(nodes, missions=missions, reports=reports)


# ─────────────────────────── 1. 判据解析 ───────────────────────────

@pytest.mark.parametrize("raw,key,op,threshold,percent", [
    # parse 保留原文阈值 + percent 标记；归一化（/100）在求值阶段做，便于审计
    ("alpha_err < 8%", "alpha_err", "lt", 8.0, True),
    ("beta_err <= 0.05", "beta_err", "le", 0.05, False),
    ("gamma_rho = 1", "gamma_rho", "eq", 1.0, False),
    ("delta_gain >= 20", "delta_gain", "ge", 20.0, False),
    ("eps > 1e-3", "eps", "gt", 0.001, False),
    ("zeta_ratio ≤ 5%", "zeta_ratio", "le", 5.0, True),
])
def test_parse_criterion_forms(raw, key, op, threshold, percent):
    c = parse_criterion(raw)
    assert c["parsed"] is True
    assert (c["key"], c["op"], c["threshold"], c["percent"]) == (key, op, threshold, percent)


def test_parse_criterion_unparsed_keeps_raw():
    c = parse_criterion("this criterion has no number")
    assert c["parsed"] is False and c["op"] is None
    d = parse_criterion("rho equals exactly one")
    assert d["parsed"] is False


def test_parse_criteria_accepts_structured_and_text_mixed():
    items = [{"key": "A err", "op": "<=", "threshold": 0.1, "percent": True},
             "b < 2"]
    out = parse_criteria(items)
    assert out[0]["key"] == "a_err" and out[0]["op"] == "le" and out[0]["percent"] is True
    assert out[1]["key"] == "b" and out[1]["threshold"] == 2.0


def test_normalize_key_is_literal_only():
    assert normalize_key("  C_x  vs  Y ") == "c_x_vs_y"
    assert normalize_key("σ-ratio") == "ratio"          # 非 ascii 不猜同义，只去字符
    assert normalize_key(None) == ""


# ─────────────────────────── 2. 判据求值 ───────────────────────────

def test_evaluate_criterion_pct_requires_unit_never_guesses_scale():
    crit = parse_criterion("alpha_err < 8%")
    missing = evaluate_criterion(crit, {"value": 6.1})
    assert missing["status"] == "unproven"
    assert missing["reason"] == "pct_criterion_requires_unit"
    # 6.1 若被静默当成 0.061 就会假通过 —— 两类显式 unit 必须给出相反结论
    assert evaluate_criterion(crit, {"value": 6.1, "unit": "%"})["status"] == "passed"
    assert evaluate_criterion(crit, {"value": 6.1, "unit": "fraction"})["status"] == "failed"


def test_evaluate_criterion_no_measurement_and_missing_value():
    crit = parse_criterion("alpha_err < 8%")
    assert evaluate_criterion(crit, None)["reason"] == "no_measurement_for_criterion"
    assert evaluate_criterion(crit, {"unit": "%"})["reason"] == "measurement_missing_value"


def test_evaluate_criterion_eq_tolerance():
    crit = parse_criterion("gamma_rho = 1")
    assert evaluate_criterion(crit, {"value": 1.0})["status"] == "passed"
    assert evaluate_criterion(crit, {"value": 0.9995}, eq_abs_tol=1e-3)["status"] == "passed"
    assert evaluate_criterion(crit, {"value": 0.99}, eq_abs_tol=1e-3)["status"] == "failed"


# ─────────────────────────── 3/10. 全绿 → ready ───────────────────────────

def test_all_green_verdict_ready_and_score_above_threshold():
    rep = evaluate_inheritance_gate(_green_snapshot(), _cfg())
    assert rep["verdict"] == "ready", rep["blockers"]
    assert rep["score"] >= 0.7
    assert all(rep["hard_gates"].values())
    assert rep["node_summary"]["n_accepted"] == 4
    assert rep["components"]["criteria"] == 1.0
    assert rep["blockers"] == []
    for key in ("schema_version", "input_digest", "config_echo", "nodes", "milestones",
                "consistency", "governance", "hard_gates", "verdict", "score"):
        assert key in rep


def test_markdown_fence_evidence_is_parsed():
    rep = evaluate_inheritance_gate(_green_snapshot(fence=True), _cfg())
    assert rep["verdict"] == "ready", rep["blockers"]
    assert any(str(ev.get("evidence_source", "")).endswith("#fence") for ev in rep["nodes"])


def test_cli_exit_codes_and_artifacts(tmp_path):
    snap_p = tmp_path / "snap.json"
    cfg_p = tmp_path / "cfg.json"
    out_p = tmp_path / "out" / "gate.json"
    md_p = tmp_path / "out" / "gate.md"
    snap_p.write_text(json.dumps(_green_snapshot()), encoding="utf-8")
    cfg_p.write_text(json.dumps(_cfg()), encoding="utf-8")

    from ops.taskplane.inheritance_gate import main as gate_main
    rc = gate_main(["--snapshot", str(snap_p), "--config", str(cfg_p),
                    "--json-out", str(out_p), "--md-out", str(md_p), "--quiet"])
    assert rc == 0 and out_p.is_file() and md_p.is_file()
    assert json.loads(out_p.read_text(encoding="utf-8"))["verdict"] == "ready"

    bad = _green_snapshot()
    bad["nodes"][0]["status"] = "in-flight"
    snap_p.write_text(json.dumps(bad), encoding="utf-8")
    rc2 = gate_main(["--snapshot", str(snap_p), "--config", str(cfg_p),
                     "--json-out", str(out_p), "--quiet"])
    assert rc2 == 2
    assert json.loads(out_p.read_text(encoding="utf-8"))["verdict"] == "not_ready"


# ─────────────────────────── 4. 在飞 / 未完成 ───────────────────────────

def test_in_flight_node_blocks_honestly():
    snap = _green_snapshot()
    snap["nodes"][0]["status"] = "in-flight"
    rep = evaluate_inheritance_gate(snap, _cfg())
    assert rep["verdict"] == "not_ready"
    assert rep["hard_gates"]["all_covered_milestones_complete"] is False
    assert any("node_not_done" in b for b in rep["blockers"])
    assert rep["milestones"]["milestones"][0]["complete"] is False
    assert rep["milestones"]["milestones"][0]["blocking_nodes"] == [1]


def test_all_nodes_in_flight_scores_zero_without_padding():
    nodes = [_node(i, 90 if i < 3 else 91, 100 + i, status="in-flight", verdict=None)
             for i in (1, 2, 3, 4)]
    rep = evaluate_inheritance_gate(_snapshot(nodes, missions=_missions(101, 102, 103, 104,
                                                                       status="running")),
                                    _cfg())
    assert rep["verdict"] == "not_ready"
    assert rep["score"] == 0.0
    assert rep["node_summary"]["n_accepted"] == 0


# ─────────────────────────── 5. 伪造完成三连 ───────────────────────────

def test_done_without_evidence_is_not_accepted():
    snap = _green_snapshot()
    snap["reports"].pop("101")
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 1)
    assert ev["accepted"] is False
    assert "no_evidence" in ev["blockers"]
    assert rep["verdict"] == "not_ready"


def test_declared_criterion_without_measurement_is_unproven_not_pass():
    snap = _green_snapshot()
    snap["reports"]["101"] = [_report(1, 101, acceptance={"other_metric": {"value": 1}},
                                     quantities={"q_shared": 1.0, "q_ref": 4.0})]
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 1)
    assert ev["accepted"] is False
    crit = ev["criteria"][0]
    assert crit["status"] == "unproven" and crit["reason"] == "no_measurement_for_criterion"
    assert ev["criteria_proven"] == 0


def test_measurement_out_of_limit_fails_node():
    snap = _green_snapshot()
    snap["reports"]["101"] = [_report(1, 101,
                                     acceptance={"alpha_err": {"value": 0.12, "unit": "fraction"}},
                                     quantities={"q_shared": 1.0, "q_ref": 4.0})]
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 1)
    assert ev["accepted"] is False
    assert ev["criteria"][0]["status"] == "failed"
    assert rep["components"]["criteria"] < 1.0


def test_failed_reproduction_verdict_never_accepted():
    snap = _green_snapshot()
    snap["nodes"][0]["reproduction_verdict"] = "fail"
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 1)
    assert ev["accepted"] is False
    assert "reproduction_verdict=fail" in ev["blockers"]


def test_missing_reproduction_verdict_blocked_when_required_but_allowed_when_disabled():
    snap = _green_snapshot()
    snap["nodes"][0]["reproduction_verdict"] = None
    strict = evaluate_inheritance_gate(snap, _cfg())
    assert next(e for e in strict["nodes"] if e["node_id"] == 1)["accepted"] is False
    lenient = evaluate_inheritance_gate(snap, _cfg(require_reproduction_verdict=False))
    assert next(e for e in lenient["nodes"] if e["node_id"] == 1)["accepted"] is True


def test_failed_mission_blocks_node():
    snap = _green_snapshot()
    snap["missions"]["101"]["status"] = "failed"
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 1)
    assert ev["accepted"] is False
    assert any("mission_failed" in b for b in ev["blockers"])


# ─────────────────────────── 6. 跨节点一致性 ───────────────────────────

def test_consistency_violation_fails_hard_gate():
    snap = _green_snapshot()
    # q_shared: 1.00 / 1.02 / 1.01 / 1.03 → spread=0.0296 通过；把节点2 改成 1.5 触发越限
    snap["reports"]["102"] = [_report(2, 102, acceptance={"beta_err": {"value": 0.041}},
                                     quantities={"q_shared": 1.5, "q_ref": 4.2})]
    rep = evaluate_inheritance_gate(snap, _cfg())
    assert rep["hard_gates"]["cross_consistency_within_tolerance"] is False
    assert any("consistency_violation:q_shared" in b for b in rep["blockers"])
    row = next(k for k in rep["consistency"]["keys"] if k["key"] == "q_shared")
    assert row["n_sources"] == 4 and row["passed"] is False
    assert row["spread"] > row["tolerance"]


def test_reference_deviation_checked_when_supplied():
    snap = _green_snapshot()
    snap["reports"]["101"] = [_report(1, 101,
                                     acceptance={"alpha_err": {"value": 0.061, "unit": "fraction"}},
                                     quantities={"q_shared": 1.0,
                                                 "q_ref": {"value": 4.8, "reference": 4.0,
                                                           "rtol": 0.25}})]
    ok = evaluate_inheritance_gate(snap, _cfg())
    row = next(k for k in ok["consistency"]["keys"] if k["key"] == "q_ref")
    assert [c["passed"] for c in row["checks"] if c["kind"] == "reference"] == [True]
    # 收紧 rtol → 同一数据必须翻成 fail
    snap["reports"]["101"][0]["inheritance_evidence"]["quantities"]["q_ref"]["rtol"] = 0.05
    bad = evaluate_inheritance_gate(snap, _cfg())
    row2 = next(k for k in bad["consistency"]["keys"] if k["key"] == "q_ref")
    assert [c["passed"] for c in row2["checks"] if c["kind"] == "reference"] == [False]


def test_single_source_quantity_is_not_cross_judged():
    snap = _green_snapshot()
    rep = evaluate_inheritance_gate(snap, _cfg(consistency={"tolerance": 0.05,
                                                            "min_shared_keys": 1}))
    assert rep["consistency"]["n_keys_cross_judged"] >= 1
    strict = evaluate_inheritance_gate(snap, _cfg(consistency={"tolerance": 0.05,
                                                              "min_shared_keys": 5}))
    assert any("insufficient_cross_judged_keys" in b for b in strict["blockers"])
    assert strict["verdict"] == "not_ready"


def test_no_quantities_at_all_cannot_earn_consistency_score():
    nodes = [_node(i, 90, 100 + i, criteria=["a_err < 1"]) for i in (1, 2)]
    missions = _missions(101, 102)
    reports = {str(101 + k): [_report(1 + k, 101 + k, acceptance={"a_err": {"value": 0.001}})]
               for k in (0, 1)}
    rep = evaluate_inheritance_gate(_snapshot(nodes, missions=missions, reports=reports),
                                    _cfg(milestones=[90], governance={"node_ids": [1]}))
    assert rep["consistency"]["score"] == 0.0
    assert "no_shared_quantities" in rep["blockers"]


# ─────────────────────────── 7. 治理分量 ───────────────────────────

def test_governance_node_failure_blocks_ready_even_with_high_score():
    snap = _green_snapshot()
    snap["reports"]["104"] = [_report(4, 104, acceptance={"delta_gain": {"value": 5.0}},
                                     quantities={"q_shared": 1.03})]
    rep = evaluate_inheritance_gate(snap, _cfg())
    assert rep["hard_gates"]["governance_nodes_accepted"] is False
    assert rep["verdict"] == "not_ready"
    assert rep["governance"]["n_accepted"] == 0 and rep["governance"]["n_nodes"] == 1


def test_governance_injected_via_metadata_class():
    snap = _green_snapshot()
    rep = evaluate_inheritance_gate(snap, _cfg(governance={}))
    assert rep["governance"]["source"] == "metadata.inheritance_class"
    assert rep["governance"]["node_ids"] == [4]


def test_weight_renormalized_when_governance_not_applicable():
    nodes = [_node(1, 90, 101, criteria=["a_err < 1"])]
    reports = {"101": [_report(1, 101, acceptance={"a_err": {"value": 0.001}},
                              quantities={"q_shared": 1.0})]}
    cfg = _cfg(milestones=[90], weights=dict(DEFAULT_WEIGHTS))
    rep = evaluate_inheritance_gate(_snapshot(nodes, milestones=[{"id": 90, "name": "M"}],
                                              missions=_missions(101), reports=reports), cfg)
    assert rep["components_applicable"]["governance"] is False
    w = rep["config_echo"]["weights_effective"]
    assert abs(sum(w.values()) - 1.0) < 1e-9 and w["governance"] == 0.0
    assert abs(w["milestone"] - 0.4 / 0.85) < 1e-6


# ─────────────────────────── 9. 确定性 ───────────────────────────

def test_deterministic_across_runs():
    snap = _green_snapshot()
    a = json.dumps(evaluate_inheritance_gate(snap, _cfg()), sort_keys=True)
    b = json.dumps(evaluate_inheritance_gate(snap, _cfg()), sort_keys=True)
    assert a == b
    other = _green_snapshot()
    other["nodes"][0]["name"] = "renamed"
    c = json.dumps(evaluate_inheritance_gate(other, _cfg()), sort_keys=True)
    assert c != a  # digest 随内容变化（可审计）


# ─────────────────────────── 11. 证据来源 ───────────────────────────

def test_extract_evidence_prefers_latest_and_warns_on_multiple():
    old = _report(1, 101, acceptance={"a": {"value": 1}})
    new = _report(2, 101, acceptance={"a": {"value": 2}})
    new["created_at"] = "2026-02-01T00:00:00+00:00"
    ev, source, warns = extract_evidence([old, new])
    assert ev["acceptance"]["a"]["value"] == 2
    assert source.startswith("report:2")
    assert any(w.startswith("multiple_evidence_sources") for w in warns)


def test_evidence_json_file_resolution(tmp_path):
    p = tmp_path / "artifacts" / "ev.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"answers": 1}), encoding="utf-8")
    assert load_evidence_file("artifacts/ev.json", [tmp_path]) == {"answers": 1}

    wrapped = tmp_path / "artifacts" / "ev2.json"
    wrapped.write_text(json.dumps({"inheritance_evidence": {"answers": 2}}), encoding="utf-8")
    assert load_evidence_file("artifacts/ev2.json", [tmp_path]) == {"answers": 2}
    assert load_evidence_file("artifacts/missing.json", [tmp_path]) is None


def test_node_evidence_from_local_file(tmp_path):
    p = tmp_path / "artifacts" / "node1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"acceptance": {"a_err": {"value": 0.001}},
                             "quantities": {"q_shared": 1.0}}), encoding="utf-8")
    node = _node(1, 90, 101, criteria=["a_err < 1"])
    node["metadata"]["inheritance_evidence"] = "artifacts/node1.json"
    node2 = _node(2, 90, 102, criteria=["a_err < 1"])
    node2["metadata"]["inheritance_evidence"] = "artifacts/node1.json"
    nodes = [node, node2]
    rep = evaluate_inheritance_gate(
        _snapshot(nodes, milestones=[{"id": 90, "name": "M"}], missions=_missions(101, 102)),
        _cfg(milestones=[90], governance={"node_ids": [1]}, roots=[str(tmp_path)]))
    assert rep["node_summary"]["n_accepted"] == 2
    assert any(str(ev.get("evidence_source", "")).startswith("file:") for ev in rep["nodes"])


# ─────────────────────────── 报告渲染 ───────────────────────────

def test_render_markdown_contains_verdict_and_blockers():
    rep = evaluate_inheritance_gate(_green_snapshot(), _cfg())
    md = render_markdown(rep)
    assert "裁决" in md and "ready" in md
    assert "PASS" in md and "硬门" in md
    bad = evaluate_inheritance_gate(_green_snapshot(), _cfg(consistency={"min_shared_keys": 9}))
    md_bad = render_markdown(bad)
    assert "not_ready" in md_bad and "insufficient_cross_judged_keys" in md_bad

# ─────────────────── 12. 阳性对照暴露的两处语法缺口（真实战役 24 条判据） ───────────────────

def test_fraction_threshold_is_evaluated_as_ratio():
    c = parse_criterion("pass_rate >= 2/3")
    assert c["parsed"] is True and c["op"] == "ge"
    assert abs(c["threshold"] - 2 / 3) < 1e-12
    assert evaluate_criterion(c, {"value": 0.8})["status"] == "passed"
    assert evaluate_criterion(c, {"value": 0.6})["status"] == "failed"


def test_boolean_criterion_requires_attestation_plus_artifact():
    """质性判据（无算符无数值）只能凭「通过声明 + 工件指针」验收，二者缺一不可。"""
    crit = parse_criterion("regression_suite_green")
    assert crit["parsed"] is False
    assert evaluate_criterion(crit, None)["reason"] == "no_measurement_for_criterion"
    only_flag = evaluate_criterion(crit, {"pass": True})
    assert only_flag["status"] == "unproven"
    assert only_flag["reason"] == "attestation_without_artifact"
    assert evaluate_criterion(crit, {"pass": True, "evidence": "artifacts/x.log"})["status"] == "passed"
    assert evaluate_criterion(crit, {"pass": False, "evidence": "artifacts/x.log"})["status"] == "failed"


def test_boolean_criterion_green_cycle_in_gate():
    snap = _green_snapshot()
    snap["nodes"][2]["metadata"]["acceptance_criteria"] = ["regression_suite_green"]
    snap["reports"]["103"] = [_report(3, 103, acceptance={"regression_suite_green": {"pass": True}},
                                     quantities={"q_shared": 1.01})]
    rep = evaluate_inheritance_gate(snap, _cfg())
    ev = next(e for e in rep["nodes"] if e["node_id"] == 3)
    assert ev["accepted"] is False  # 声明通过但无工件指针 → unproven
    assert ev["criteria"][0]["reason"] == "attestation_without_artifact"
    snap["reports"]["103"][0]["inheritance_evidence"]["acceptance"]["regression_suite_green"] = {
        "pass": True, "evidence": "artifacts/suite.log"}
    rep2 = evaluate_inheritance_gate(snap, _cfg())
    assert next(e for e in rep2["nodes"] if e["node_id"] == 3)["accepted"] is True


def test_excluded_node_is_left_out_of_scoring_but_recorded():
    snap = _green_snapshot()
    # 把节点 4 的判据改成不可能通过：若被排除，则分数不受影响且留痕
    snap["nodes"][3]["metadata"]["acceptance_criteria"] = ["never_measured_metric < 1"]
    rep = evaluate_inheritance_gate(snap, _cfg(exclude_node_ids=[4]))
    assert rep["excluded_nodes"] == [{"node_id": 4, "name": "node-4",
                                      "reason": "excluded_by_config"}]
    assert all(ev["node_id"] != 4 for ev in rep["nodes"])
    with_excluded = evaluate_inheritance_gate(snap, _cfg(governance={"node_ids": [3]}))
    assert with_excluded["node_summary"]["n_accepted"] == 3
    assert rep["node_summary"]["n_accepted"] == 3  # 节点 4 在两种读数下都不影响其余节点
