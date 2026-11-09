"""谱系继承契约机检（ops/taskplane/inheritance_contract.py）验收测试。

判据先于实现、全程合成数据（零领域词条 —— TensorPlatform 铁律）：

1. 提取完备：plan/节点 metadata 的 honest_fail / kpi_gap / limitation 四路来源
   100% 提取，kind/severity/溯源 pointer 正确；
2. 去重合并：同一缺口两种写法 → 一条 gap、sources 双源；goal 局限节按标题切段；
3. 状态派生：父节点 status=failed/blocked 且无书面局限 → 记 gap（欠账不得静默）；
4. 覆盖：判据引用同一 metric 且含算符+数值 → covered(lexical)；偏差表近失（散文/无算符）
   不算覆盖（criterion_not_machine_checkable）；
5. 显式引用：metadata.covers_gaps 点名 gap_id → covered(explicit)，不依赖词面；
6. 拦截：blocking 级缺口未覆盖 → verdict=block、intercepted=True、blockers 点名 gap_id，
   拦截率 100%（N/N）；warn 级未覆盖 → verdict=warn 且不拦截；
7. 负控：父计划无任何缺口 → rows 空、verdict=pass、intercepted=False、空矩阵；
8. 负控：全部缺口均被覆盖 → 无拦截、blockers 空；
9. 确定性：同输入两次裁决逐字节相同；节点输入顺序打乱 digest 不变（顺序无关）；
   改一个缺口文本 → digest 变（可复算非恒真）；
10. 播种器接口 seeding_block_reasons 与 DB 适配（row_to_json 单列解析，含 \\x02 字符）；
11. CLI：block 退出码 2、pass 退出码 0，JSON/MD 双产物落盘；
12. 集成：campaign_dispatcher._validate_new_plan 内确实接入本门（静态接线回归）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.taskplane import inheritance_contract as ic
from ops.taskplane.inheritance_contract import (
    build_coverage_matrix,
    evaluate_seeding,
    extract_gaps,
    machine_checkable,
    parse_row_json,
    render_markdown,
    seeding_block_reasons,
    terms,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────── 合成载荷工厂 ───────────────────────────

def _plan(pid, metadata=None, status="active"):
    return {"id": pid, "status": status, "metadata": metadata or {}}


def _node(nid, ext="", criteria=None, goal="", status="done", **meta):
    m = {"acceptance_criteria": criteria or [], "goal": goal}
    if ext:
        m["ext_id"] = ext
    m.update(meta)
    return {"id": nid, "status": status, "milestone_id": 1, "metadata": m}


def _parent(nodes, metadata=None, pid=11):
    return _plan(pid, metadata), nodes


def _payload(plan, nodes):
    return {"plan": plan, "nodes": nodes}


def _no_gap_child(nodes=None):
    return _payload(_plan(12), nodes or [
        _node(201, "P12-N1", criteria=["err_alpha_metric < 3 %"]),
    ])


# ─────────────────────────── 1. 提取完备 ───────────────────────────

def test_extracts_all_four_sources_100pct():
    parent = _payload(_plan(11, {
        "honest_fail": ["metric_zeta 实测 0.42 超限，判据未达成"],
        "kpi_gaps": [{"text": "metric_eta 覆盖率仅 55 %，目标 90 %", "metric": "metric_eta"}],
        "limitations": ["仅覆盖稳态区间，瞬态未做"],
    }), [
        _node(101, "P11-N1", status="done",
              limitations=["单源数据，无交叉裁决"],
              goal="【物理目标】合成任务。【机器可算验收判据】err_beta < 5 %。【已知局限】仅线性区间。"),
        _node(102, "P11-N2", status="failed", fail_reason="tool_chain 断裂，未产出测量值"),
    ])
    gaps = extract_gaps(parent)
    kinds = sorted(g["kind"] for g in gaps)
    # plan 3 条（honest_fail/kpi_gap/limitation）+ node101 局限键 1 + goal 局限节 1
    # + node102 状态派生 1 = 6
    assert len(gaps) == 6, [g["text"] for g in gaps]
    assert kinds.count("honest_fail") == 1
    assert kinds.count("kpi_gap") == 1
    assert kinds.count("limitation") == 3
    assert kinds.count("status_failed") == 1
    # 溯源 pointer 完整、每条都带来源
    for g in gaps:
        assert g["sources"] and all(isinstance(s, str) and s for s in g["sources"])
    pointers = " ".join(s for g in gaps for s in g["sources"])
    for want in ("plan:11.metadata.honest_fail[0]", "plan:11.metadata.kpi_gaps[0]",
                 "node:101.metadata.limitations[0]", "node:102.status=failed"):
        assert want in pointers
    assert any("【已知局限】" in s for g in gaps for s in g["sources"])
    # metric 从 dict 缺口里带出来
    assert any(g["metric"] == "metric_eta" for g in gaps)


def test_severity_mapping_from_kind():
    parent = _payload(_plan(11, {"honest_fail": ["alpha_missing"]}), [])
    gap = extract_gaps(parent)[0]
    assert gap["kind"] == "honest_fail" and gap["severity"] == "block"
    cfg = {"severity_by_kind": {"honest_fail": "warn"}}
    assert extract_gaps(parent, cfg)[0]["severity"] == "warn"


# ─────────────────────────── 2. 去重合并 / 3. 状态派生 ───────────────────────────

def test_dedup_merges_sources_and_is_stable():
    text = "metric_gamma 未覆盖负控场景"
    parent = _payload(_plan(11, {"kpi_gaps": [text], "limitations": [text],
                                 "known_limitations": [{"text": text}]}),
                      [_node(101, "P11-N1", kpi_gaps=[text])])
    gaps = extract_gaps(parent)
    lim = [g for g in gaps if g["kind"] == "limitation"]
    kg = [g for g in gaps if g["kind"] == "kpi_gap"]
    assert len(lim) == 1 and len(lim[0]["sources"]) == 2  # 两个局限键同文合并
    assert len(kg) == 1 and len(kg[0]["sources"]) == 2    # plan + node 同文合并
    assert lim[0]["sources"] == sorted(lim[0]["sources"])
    # 同文同 kind 的 gap_id 与来源顺序/来源数量无关
    again = extract_gaps(_payload(_plan(11, {"limitations": [text]}), []))
    assert lim[0]["gap_id"] == again[0]["gap_id"]


def test_status_gap_skipped_when_node_declares_gap():
    parent = _payload(_plan(11), [
        _node(101, "P11-N1", status="blocked", limitations=["依赖上游未合并分支"]),
    ])
    gaps = extract_gaps(parent)
    assert [g["kind"] for g in gaps] == ["limitation"]  # 已记账 → 不再派生状态缺口


def test_derive_from_status_can_be_disabled():
    parent = _payload(_plan(11), [_node(101, "P11-N1", status="failed")])
    assert len(extract_gaps(parent)) == 1
    assert extract_gaps(parent, {"derive_from_status": False}) == []


def test_failed_node_without_reason_gets_explicit_placeholder():
    parent = _payload(_plan(11), [_node(101, "P11-N1", status="failed")])
    gap = extract_gaps(parent)[0]
    assert "P11-N1" in gap["text"] and "failed" in gap["text"]
    assert gap["severity"] == "warn"  # 派生缺口默认只记风险


# ─────────────────────────── 4. 覆盖判定 ───────────────────────────

def test_lexical_coverage_requires_machine_checkable_criterion():
    parent = _payload(_plan(11, {"kpi_gaps": [{"text": "err_alpha_metric 越限", "metric": "err_alpha_metric"}]}), [])
    good = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_alpha_metric < 3 %"])])
    prose = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_alpha_metric 已充分理解"])])
    assert not machine_checkable("err_alpha_metric 已充分理解")
    assert machine_checkable("err_alpha_metric < 3 %")

    m_good = build_coverage_matrix(extract_gaps(parent), good)
    m_prose = build_coverage_matrix(extract_gaps(parent), prose)
    assert m_good["rows"][0]["status"] == "covered"
    assert m_good["rows"][0]["covered_by"][0]["level"] == "lexical"
    assert m_prose["rows"][0]["status"] == "uncovered"
    nm = m_prose["rows"][0]["near_misses"][0]
    assert nm["reason"] == "criterion_not_machine_checkable"
    # 严格纪律：关掉 machine_checkable 也只是配置项，默认开
    m_relaxed = build_coverage_matrix(extract_gaps(parent), prose,
                                      {"require_machine_checkable": False})
    assert m_relaxed["rows"][0]["status"] == "covered"


def test_anchor_mismatch_is_not_coverage():
    parent = _payload(_plan(11, {"kpi_gaps": [{"text": "err_delta_metric 未达标",
                                              "metric": "err_delta_metric"}]}), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_other_metric < 5 %"])])
    row = build_coverage_matrix(extract_gaps(parent), child)["rows"][0]
    assert row["status"] == "uncovered"
    assert row["covered_by"] == []


def test_explicit_reference_covers_without_lexical_overlap():
    parent = _payload(_plan(11, {"honest_fail": ["metric_omega 判决不足"]}), [])
    gap_id = extract_gaps(parent)[0]["gap_id"]
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_nu < 2 %"],
                                       covers_gaps=[gap_id])])
    row = build_coverage_matrix(extract_gaps(parent), child)["rows"][0]
    assert row["status"] == "covered"
    assert row["covered_by"][0]["level"] == "explicit"


def test_alias_reference_covers():
    parent = _payload(_plan(11, {"kpi_gaps": [{"text": "metric_sigma 缺口",
                                              "id": "KPI-77"}]}), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_x < 1 %"],
                                       covers_gaps=["KPI-77"])])
    assert build_coverage_matrix(extract_gaps(parent), child)["rows"][0]["status"] == "covered"


# ─────────────────────────── 5. 拦截 ───────────────────────────

def test_uncovered_blocking_gaps_intercept_100pct():
    parent = _payload(_plan(11, {
        "honest_fail": ["metric_a 未达成", "metric_b 未达成", "metric_c 未达成"],
        "kpi_gaps": [{"text": "err_d 覆盖率 40 %，目标 90 %", "metric": "err_d"}],
    }), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["metric_a < 1 %"])])
    rep = evaluate_seeding(parent, child)
    assert rep["verdict"] == "block" and rep["intercepted"] is True
    assert len(rep["blockers"]) == 3  # 4 条 block 级缺口，1 条被覆盖 → 拦截 3/3（100%）
    assert rep["summary"]["n_covered"] == 1
    assert rep["summary"]["n_intercepted"] == 3
    assert rep["summary"]["n_intercepted"] == rep["summary"]["n_uncovered"]
    ids = {b["gap_id"] for b in rep["blockers"]}
    assert ids == {r["gap_id"] for r in rep["matrix"]["rows"] if r["intercept"]}
    # 拦截项必须点名最近失（供播种者定向补判据）
    assert all("best_near_miss" in b for b in rep["blockers"])


def test_warn_gap_uncovered_does_not_intercept():
    parent = _payload(_plan(11, {"limitations": ["仅覆盖稳态区间"]}), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_x < 1 %"])])
    rep = evaluate_seeding(parent, child)
    assert rep["verdict"] == "warn"
    assert rep["intercepted"] is False
    assert rep["blockers"] == [] and len(rep["warnings"]) == 1


def test_blocking_severities_configurable():
    parent = _payload(_plan(11, {"limitations": ["仅覆盖稳态区间"]}), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_x < 1 %"])])
    rep = evaluate_seeding(parent, child, {"blocking_severities": ["block", "warn"]})
    assert rep["verdict"] == "block"


# ─────────────────────────── 6. 负控 ───────────────────────────

def test_negative_control_no_gaps_empty_matrix():
    rep = evaluate_seeding(_payload(_plan(11), [_node(101, "P11-N1")]), _no_gap_child())
    assert rep["verdict"] == "pass" and rep["intercepted"] is False
    assert rep["matrix"]["rows"] == []
    assert rep["matrix"]["summary"]["n_gaps"] == 0
    assert rep["matrix"]["summary"]["coverage_ratio"] is None
    assert rep["blockers"] == [] and rep["warnings"] == []
    assert render_markdown(rep)


def test_negative_control_fully_covered_no_interception():
    parent = _payload(_plan(11, {"honest_fail": ["metric_a 未达成"],
                                 "kpi_gaps": [{"text": "err_b 覆盖不足", "metric": "err_b"}],
                                 "limitations": ["仅稳态"]}), [])
    child = _payload(_plan(12), [
        _node(201, "P12-N1", criteria=["metric_a < 1 %"]),
        _node(202, "P12-N2", criteria=["err_b >= 90 %"]),
        _node(203, "P12-N3", criteria=["仅稳态 interval_check = 1"]),
    ])
    rep = evaluate_seeding(parent, child)
    assert rep["intercepted"] is False
    assert rep["blockers"] == []
    assert rep["summary"]["n_uncovered"] == 0


# ─────────────────────────── 7. 确定性 ───────────────────────────

def _det_fixture():
    parent = _payload(_plan(11, {
        "honest_fail": ["metric_a 未达成"],
        "kpi_gaps": [{"text": "err_b 覆盖不足", "metric": "err_b"}],
    }), [_node(101, "P11-N1", status="done", goal="【已知局限】仅线性区间。")])
    child = _payload(_plan(12), [
        _node(201, "P12-N1", criteria=["metric_a < 1 %"]),
        _node(202, "P12-N2", criteria=["err_b >= 90 %"]),
    ])
    return parent, child


def test_determinism_byte_identical_recompute():
    parent, child = _det_fixture()
    r1 = evaluate_seeding(parent, child)
    r2 = evaluate_seeding(json.loads(json.dumps(parent)), json.loads(json.dumps(child)))
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert r1["matrix_digest"] == r2["matrix_digest"]


def test_determinism_order_invariant_wrt_node_input():
    parent, child = _det_fixture()
    shuffled = {"plan": child["plan"], "nodes": list(reversed(child["nodes"]))}
    a = evaluate_seeding(parent, child)["matrix_digest"]
    b = evaluate_seeding(parent, shuffled)["matrix_digest"]
    assert a == b


def test_digest_changes_when_gap_changes():
    parent, child = _det_fixture()
    base = evaluate_seeding(parent, child)["matrix_digest"]
    parent2 = json.loads(json.dumps(parent))
    parent2["plan"]["metadata"]["honest_fail"] = ["metric_a 未达成（补充：还有量纲错位）"]
    assert evaluate_seeding(parent2, child)["matrix_digest"] != base


# ─────────────────────────── 8. 播种器接口 / DB 适配 ───────────────────────────

def test_seeding_block_reasons_interface():
    parent, child = _det_fixture()
    ok, reasons, rep = seeding_block_reasons(parent, child)
    assert ok is True and rep["intercepted"] is False
    parent_bad = _payload(_plan(11, {"honest_fail": ["metric_zzz 未达成"]}), [])
    ok2, reasons2, rep2 = seeding_block_reasons(parent_bad, child)
    assert ok2 is False and rep2["intercepted"] is True
    assert any("播种拦截" in r for r in reasons2)


def test_parse_row_json_tolerates_separator_chars():
    # 文本里含 0x02（旧 \x02 分隔解包的炸点）：row_to_json 单列解析必须完好保留原文
    blob = json.dumps({"id": 11, "status": "active",
                       "metadata": {"limitations": ["a\x02b", "c|d"]}}, ensure_ascii=False)
    parsed = parse_row_json([[blob]])
    assert parsed[0]["id"] == 11
    assert parsed[0]["metadata"]["limitations"] == ["a\x02b", "c|d"]
    assert parse_row_json([[""]]) == [] and parse_row_json([]) == []


def test_seeding_gate_from_db_uses_parent_plan_metadata(monkeypatch):
    parent = _payload(_plan(11, {"honest_fail": ["metric_a 未达成"]}), [])
    child = _payload(_plan(12, {"parent_plan": 11}), [_node(201, "P12-N1", criteria=["err_x < 1 %"])])

    import re as _re

    def fake_q(sql):
        m = _re.search(r"id=(\d+)", sql)
        payload = child if int(m.group(1)) == 12 else parent
        if "campaign_plans" in sql:
            return [[json.dumps(payload["plan"])]]
        return [[json.dumps({**n, "ext_id": (n.get("metadata") or {}).get("ext_id", "")})]
                for n in payload["nodes"]]

    ok, reasons, rep = ic.seeding_gate_from_db(12, fake_q)
    assert ok is False and rep["intercepted"] is True
    assert rep["parent_plan_id"] == 11 and rep["child_plan_id"] == 12


def test_seeding_gate_from_db_no_parent_is_pass():
    payload = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_x < 1 %"])])

    def fake_q(sql):
        if "campaign_plans" in sql:
            return [[json.dumps(payload["plan"])]]
        return [[json.dumps(payload["nodes"][0])]]

    ok, reasons, rep = ic.seeding_gate_from_db(12, fake_q)
    assert ok is True and reasons == [] and rep["verdict"] == "skip"


# ─────────────────────────── 9. CLI / 10. 接线 ───────────────────────────

def test_cli_exit_codes_and_artifacts(tmp_path):
    parent = _payload(_plan(11, {"honest_fail": ["metric_a 未达成"]}), [])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_x < 1 %"])])
    pp, cp = tmp_path / "parent.json", tmp_path / "child.json"
    pp.write_text(json.dumps(parent), encoding="utf-8")
    cp.write_text(json.dumps(child), encoding="utf-8")
    jo, mo = tmp_path / "out" / "gate.json", tmp_path / "out" / "gate.md"
    rc = ic.main(["--parent", str(pp), "--child", str(cp), "--json-out", str(jo),
                  "--md-out", str(mo), "--quiet"])
    assert rc == 2  # block
    doc = json.loads(jo.read_text(encoding="utf-8"))
    assert doc["verdict"] == "block" and doc["matrix"]["rows"]
    assert "覆盖矩阵" in mo.read_text(encoding="utf-8")

    child_ok = _payload(_plan(12), [_node(201, "P12-N1", criteria=["metric_a < 1 %"])])
    cp.write_text(json.dumps(child_ok), encoding="utf-8")
    assert ic.main(["--parent", str(pp), "--child", str(cp), "--quiet"]) == 0
    assert ic.main(["--parent", str(pp)]) == 3


def test_dispatcher_seeding_flow_is_wired():
    src = (REPO_ROOT / "ops/taskplane/campaign_dispatcher.py").read_text(encoding="utf-8")
    assert "inheritance_contract" in src
    fn_start = src.index("def _validate_new_plan")
    fn_body = src[fn_start:src.index("def _collect_new_plan")]
    assert "seeding_gate_from_db" in fn_body, "播种机器门未接入继承契约机检"


def test_terms_and_helpers():
    assert {"metric_a"} <= terms("Metric_A < 1 %")
    assert "稳态" in terms("稳态区间")
    assert machine_checkable("覆盖率 90 %") is True
    assert machine_checkable("无数字的比较") is False

# ─────────────────────────── 11. 未验收节点缺口升格 ───────────────────────────

def test_unaccepted_node_limitation_escalates_to_block():
    """父节点 blocked/failed → 它的「局限」是未偿欠账，必须能拦播种（否则门无齿）。"""
    parent = _payload(_plan(11), [
        _node(101, "P11-N1", status="blocked", limitations=["依赖上游未合并分支"]),
        _node(102, "P11-N2", status="done", limitations=["仅线性区间"]),
    ])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_z < 1 %"])])
    gaps = {g["text"]: g for g in extract_gaps(parent)}
    esc = gaps["依赖上游未合并分支"]
    assert esc["severity"] == "block"
    assert esc["escalated_from"]["reason"] == "node_not_accepted:blocked"
    assert gaps["仅线性区间"]["severity"] == "warn"  # 已验收节点的局限不升格
    rep = evaluate_seeding(parent, child)
    assert rep["intercepted"] is True
    assert [b["text"] for b in rep["blockers"]] == ["依赖上游未合并分支"]


def test_escalation_configurable():
    parent = _payload(_plan(11), [_node(101, "P11-N1", status="failed",
                                        limitations=["只做到单点"])])
    child = _payload(_plan(12), [_node(201, "P12-N1", criteria=["err_z < 1 %"])])
    rep = evaluate_seeding(parent, child)
    assert rep["verdict"] == "block"
    rep2 = evaluate_seeding(parent, child, {"escalate_unaccepted_gaps": False})
    assert rep2["verdict"] == "warn" and rep2["blockers"] == []


def test_goal_limitation_section_of_blocked_node_escalates():
    parent = _payload(_plan(11), [
        _node(101, "P11-N1", status="blocked",
              goal="【物理目标】合成任务。【已知局限】二维剖面，不含三维效应。"),
    ])
    gap = extract_gaps(parent)[0]
    assert gap["severity"] == "block" and gap["kind"] == "limitation"
    assert "【已知局限】" in gap["sources"][0]
