"""``campaign_gate_snapshot`` 验收测试（全程合成数据，零领域词条 —— 平台铁律）。

判据先于实现，机器可裁：
1. ``assemble_snapshot`` 把五张表行装配成 inheritance_gate 可消费的 snapshot
   （missions 以 str(id) 为键、reports 按 mission_id 聚合、int 归一见附录）。
2. 任务书判据 (a)：前置节点全 done → pass；任一未 done → fail 且逐节点留痕。
3. 任务书判据 (b)：失败终态 + report_owed + 无报告 → 未记录 honest FAIL（fail）；
   自报 done 但判据未全过 → 隐藏未完成（fail）；骨架干净 → pass。
4. 任务书判据 (c)：前置节点有报告/工件且工件可解析 → 100%；缺产物/路径不可解析 → fail。
5. 任务书判据 (d)：台账文件存在且条目达标 → pass；缺失 → fail。
6. ``run_gate`` 的裁决层与 ``evaluate_inheritance_gate`` 语义一致（可复用、不另起炉灶）。
7. 前置节点解析：优先 config.predecessor_node_ids，否则回退门节点 dependencies。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.taskplane.campaign_gate_snapshot import (
    audit_gate_criteria,
    assemble_snapshot,
    build_snapshot_from_db,
)
from ops.taskplane.inheritance_gate import evaluate_inheritance_gate


# ─────────────────────────── 合成夹具工厂 ───────────────────────────

def _node(nid, mid=1, mission=None, status="done", verdict="pass", deps=None, ext=None):
    return {
        "id": nid, "plan_id": 1, "milestone_id": mid, "mission_id": mission,
        "name": f"node-{nid}", "node_type": "task", "status": status,
        "reproduction_verdict": verdict, "dependencies": deps or [],
        "metadata": {"ext_id": ext or f"X-{nid}",
                     "acceptance_criteria": ["err < 5%"]},
    }


def _mission(mid, status="succeeded", report_owed=False, report_id=None, task_key=None):
    return {"id": mid, "status": status, "task_key": task_key or f"p1-X-{mid}",
            "report_owed": report_owed, "report_id": report_id,
            "fail_stage": None, "result": {}}


def _report(rid, mid, ev=None):
    ev = ev or {"acceptance": {"err": {"value": 0.01, "unit": "fraction"}}}
    return {"id": rid, "mission_id": mid, "status": "published",
            "created_at": f"2026-01-01T00:00:{rid:02d}+00:00",
            "markdown": "# r\n\n```inheritance-evidence\n" + json.dumps(ev) + "\n```\n"}


def _snapshot(nodes, missions, reports=None, milestones=None, plan=None):
    return assemble_snapshot(
        plan or {"id": 1, "name": "plan"},
        milestones or [{"id": 1, "name": "m1"}],
        nodes, missions, reports or [])


def _report_for_mission(mid, acceptance_value=0.01):
    return [{"id": 900 + mid, "mission_id": mid, "status": "published",
             "created_at": "2026-01-01T00:00:00+00:00", "markdown": None}]


# ─────────────────────────── 1. 装配 ───────────────────────────

def test_assemble_snapshot_shape():
    snap = _snapshot([_node(1, mission=10)], [_mission(10)],
                     reports=[_report(900, 10)])
    assert snap["plan"]["id"] == 1
    assert set(snap["missions"]) == {"10"}
    assert set(snap["reports"]) == {"10"}
    assert snap["nodes"][0]["mission_id"] == 10
    assert snap["nodes"][0]["dependencies"] == []


# ─────────────────────────── 2. (a) 前置全 done ───────────────────────────

def test_criteria_a_all_done_pass():
    nodes = [_node(1, mission=10), _node(2, mission=11)]
    snap = _snapshot(nodes, [_mission(10), _mission(11)],
                     reports=[_report(900, 10), _report(901, 11)])
    gate = evaluate_inheritance_gate(snap, {"require_reproduction_verdict": True})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1, 2]})
    assert audit["criteria"]["a_all_predecessors_done"]["pass"] is True
    assert audit["criteria"]["a_all_predecessors_done"]["done"] == 2


def test_criteria_a_not_done_fails():
    nodes = [_node(1, mission=10, status="in-flight", verdict=None),
             _node(2, mission=11)]
    snap = _snapshot(nodes, [_mission(10, status="running"), _mission(11)],
                     reports=[_report(901, 11)])
    gate = evaluate_inheritance_gate(snap, {"require_reproduction_verdict": True})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1, 2]})
    a = audit["criteria"]["a_all_predecessors_done"]
    assert a["pass"] is False and a["done"] == 1
    assert {r["node_id"]: r["done"] for r in a["rows"]} == {1: False, 2: True}


# ─────────────────────────── 3. (b) 未记录 honest FAIL ───────────────────────────

def test_criteria_b_unrecorded_failed_mission():
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10, status="failed", report_owed=True)],
                     reports=[])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]})
    b = audit["criteria"]["b_no_unrecorded_honest_fail"]
    assert b["pass"] is False
    assert b["violations"][0]["kind"] == "unrecorded_failed_mission"


def test_criteria_b_reported_failure_is_recorded():
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10, status="failed", report_owed=True)],
                     reports=[_report(900, 10)])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]})
    # 有报告 → 已记录，不算「未记录」
    assert audit["criteria"]["b_no_unrecorded_honest_fail"]["pass"] is True


def test_criteria_b_hidden_incomplete_on_done_node():
    # done 节点声明判据但证据里没有测量值 → 隐藏未完成
    nodes = [_node(1, mission=10, status="done")]
    snap = _snapshot(nodes, [_mission(10)],
                     reports=[{"id": 7, "mission_id": 10, "status": "published",
                               "created_at": "2026-01-01T00:00:00+00:00", "markdown": None}])
    gate = evaluate_inheritance_gate(snap, {"require_evidence": False,
                                            "require_reproduction_verdict": True})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]})
    b = audit["criteria"]["b_no_unrecorded_honest_fail"]
    assert b["pass"] is False
    assert any(v["kind"] == "hidden_incomplete" for v in b["violations"])


# ─────────────────────────── 4. (c) 产物完整性 ───────────────────────────

def test_criteria_c_complete_with_report(tmp_path):
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[_report(900, 10)])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]},
                                roots=[tmp_path])
    c = audit["criteria"]["c_artifact_completeness"]
    assert c["pass"] is True and c["completeness"] == 1.0


def test_criteria_c_missing_product_fails(tmp_path):
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[])
    gate = evaluate_inheritance_gate(snap, {"require_evidence": False})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]},
                                roots=[tmp_path])
    c = audit["criteria"]["c_artifact_completeness"]
    assert c["pass"] is False and c["completeness"] == 0.0


def test_criteria_c_unresolved_artifact_fails(tmp_path):
    ev = {"acceptance": {"err": {"value": 0.01, "unit": "fraction"}},
          "artifacts": ["does/not/exist.json"]}
    rep = {"id": 900, "mission_id": 10, "status": "published",
           "created_at": "2026-01-01T00:00:00+00:00",
           "markdown": "```inheritance-evidence\n" + json.dumps(ev) + "\n```\n"}
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[rep])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap, {"predecessor_node_ids": [1]},
                                roots=[tmp_path])
    c = audit["criteria"]["c_artifact_completeness"]
    assert c["pass"] is False
    assert c["rows"][0]["unresolved_artifacts"] == ["does/not/exist.json"]


# ─────────────────────────── 5. (d) 能力台账 ───────────────────────────

def test_criteria_d_ledger_present(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"schema_version": "2.0",
                                  "capabilities": [{"k": 1}]}), encoding="utf-8")
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[_report(900, 10)])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap,
                                {"predecessor_node_ids": [1],
                                 "capability_ledger_path": str(ledger)}, roots=[tmp_path])
    assert audit["criteria"]["d_capability_ledger_updated"]["pass"] is True


def test_criteria_d_ledger_missing(tmp_path):
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[_report(900, 10)])
    gate = evaluate_inheritance_gate(snap, {})
    audit = audit_gate_criteria(gate, snap,
                                {"predecessor_node_ids": [1],
                                 "capability_ledger_path": str(tmp_path / "nope.json")},
                                roots=[tmp_path])
    assert audit["criteria"]["d_capability_ledger_updated"]["pass"] is False


# ─────────────────────────── 6/7. 门就绪 + 前置解析 ───────────────────────────

def test_gate_ready_true_when_all_criteria_pass(tmp_path):
    art = tmp_path / "out.json"
    art.write_text("{}", encoding="utf-8")
    ev = {"acceptance": {"err": {"value": 0.01, "unit": "fraction"}},
          "artifacts": ["out.json"]}
    rep = {"id": 900, "mission_id": 10, "status": "published",
           "created_at": "2026-01-01T00:00:00+00:00",
           "markdown": "```inheritance-evidence\n" + json.dumps(ev) + "\n```\n"}
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"capabilities": [{"k": 1}]}), encoding="utf-8")
    nodes = [_node(1, mission=10)]
    snap = _snapshot(nodes, [_mission(10)], reports=[rep])
    gate = evaluate_inheritance_gate(snap, {"milestones": [1]})
    audit = audit_gate_criteria(gate, snap,
                                {"predecessor_node_ids": [1],
                                 "capability_ledger_path": str(ledger)}, roots=[tmp_path])
    # 审计层 (a)-(d) 全过 → gate_ready；底层就绪度评分另有互裁/治理硬门，单独裁决。
    assert audit["gate_ready"] is True


def test_predecessors_fallback_to_gate_dependencies():
    gate_node = _node(9, mission=19, status="in-flight", deps=[1, 2])
    nodes = [_node(1, mission=10), _node(2, mission=11), gate_node]
    snap = _snapshot(nodes, [_mission(10), _mission(11),
                             _mission(19, status="running")],
                     reports=[_report(900, 10), _report(901, 11)])
    gate = evaluate_inheritance_gate(snap, {"exclude_node_ids": [9]})
    audit = audit_gate_criteria(gate, snap, {"gate_node_id": 9})
    assert audit["predecessor_node_ids"] == [1, 2]


def test_build_snapshot_from_db_rejects_missing_plan():
    # 用不可能存在的 plan_id，DB 装载器应报错（不静默返回空快照）
    with pytest.raises(RuntimeError):
        build_snapshot_from_db(-999999)