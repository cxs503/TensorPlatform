"""``gate_lifecycle`` 验收测试（全程合成数据，零领域词条 —— 平台铁律）。

判据（先于实现，机器可裁）：
  C1 scope_hash 变更检测率 100%：任一条上游证据被改动 → 该门（及其下游）标 stale；
  C2 失效传播延迟 ≤1s：改动后一轮重判的墙钟时间 < 预算，且下游闭包被标出；
  C3 幂等重判 3 次逐位一致：``diff_results`` 无差异、``fingerprint`` 相同，
     同结果连写 ledger 逐字节相同；
  C4 三态语义覆盖：PENDING（前置未完成）/ WITHHELD（证据不足或缺判据）/
     FAIL（判据不满足或上游 FAIL 传染）各至少 1 例，外加 PASS 正例；
  C5 零领域语义：模块与测试自身通过 ``tools/evidence_domain_scan`` 扫描 0 命中；
  C6 CLI 退出码：全绿 0 / 非 PASS 或 stale 一律 2 / 输入错误 3（未就绪绝不返回 0）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from ops.taskplane import gate_lifecycle as gl
from ops.taskplane.gate_lifecycle import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_PENDING,
    STATUS_WITHHELD,
    adjudicate,
    canonical_json,
    evaluate_lifecycle,
    main,
    resolve_evidence,
    update_ledger,
    verify_contracts,
)

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = [f"n{i}" for i in range(1, 10)]
GATE = "gate"


# ─────────────────────────── 夹具 ───────────────────────────

def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(canonical_json(payload), encoding="utf-8")
    return path


def _criteria(name="err_pct", op="<=", target=3.0, value=2.1):
    return {"checks": [{"name": name, "op": op, "target": target, "value": value}]}


def _campaign(tmp: Path, *, nine_files=True, gate_criteria=None, node_status="done",
              consumer_gate=True) -> dict:
    """9 个上游任务 + 1 个汇总门；每个上游节点各有一条独立证据文件。"""
    nodes = []
    for nid in UPSTREAM:
        ev = []
        if nine_files:
            ref = f"evidence/{nid}.json"
            _write(tmp / ref, {"node": nid, "payload": "v1"})
            ev = [{"ref": ref, "kind": "file"}]
        nodes.append({
            "id": nid, "kind": "task", "status": node_status,
            "dependencies": [], "evidence": ev,
            "criteria": _criteria(),
        })
    if consumer_gate:
        gate_ev = [{"ref": f"evidence/{n}.json", "kind": "file",
                    "required": True} for n in UPSTREAM] if nine_files else []
        nodes.append({
            "id": GATE, "kind": "gate", "status": "done",
            "dependencies": list(UPSTREAM), "evidence": gate_ev,
            "criteria": gate_criteria if gate_criteria is not None else
            {"checks": [{"name": "all_nodes_pass", "op": "==", "target": 9, "value": 9}]},
        })
    return {"plan": 9001, "label": "synthetic-lifecycle", "nodes": nodes}


def _first(tmp: Path, campaign: dict) -> dict:
    return evaluate_lifecycle(campaign, None, roots=[tmp])


def _second(tmp: Path, campaign: dict, ledger: dict) -> dict:
    return evaluate_lifecycle(campaign, ledger, roots=[tmp])


# ───────────────────────── C1 scope_hash 绑定 ─────────────────────────

def test_scope_hash_binds_evidence_content(tmp_path):
    camp = _campaign(tmp_path)
    res = _first(tmp_path, camp)
    gate = next(r for r in res["gates"] if r["id"] == GATE)
    before = gate["scope_hash"]

    _write(tmp_path / "evidence/n3.json", {"node": "n3", "payload": "v2"})
    res2 = _second(tmp_path, camp, update_ledger(None, res))
    gate2 = next(r for r in res2["gates"] if r["id"] == GATE)
    assert gate2["scope_hash"] != before
    assert gate2["stale"] is True
    assert "evidence" in gate2["stale_cause"]["components"]
    changed_refs = [d.get("ref") for d in gate2["stale_cause"]["detail"]
                    if d["kind"] == "evidence"]
    assert "evidence/n3.json" in changed_refs


def test_c1_detection_rate_is_100_percent(tmp_path):
    """C1：逐个改动 9 条上游证据，每一次都必须被检出为 stale（9/9）。"""
    camp = _campaign(tmp_path)
    ledger = update_ledger(None, _first(tmp_path, camp))
    detections = []
    for nid in UPSTREAM:
        _write(tmp_path / f"evidence/{nid}.json", {"node": nid, "payload": f"mut-{nid}"})
        res = _second(tmp_path, camp, ledger)
        rows = {r["id"]: r for r in res["gates"]}
        detections.append({
            "node": nid,
            "node_stale": rows[nid]["stale"],
            "gate_stale": rows[GATE]["stale"],
            "gate_has_upstream_cause": any(
                d["kind"] == "upstream" and d["upstream"] == nid
                for d in rows[GATE]["stale_cause"]["detail"]),
        })
        ledger = update_ledger(ledger, res)
    rate = sum(1 for d in detections if d["node_stale"] and d["gate_stale"]) / len(detections)
    assert rate == 1.0, detections
    # 每条改动都要能在门的 stale 成因里指到具体上游节点（可审计，不是黑箱）
    assert all(d["gate_has_upstream_cause"] for d in detections), detections


def test_criteria_file_change_invalidates_scope(tmp_path):
    crit = _write(tmp_path / "criteria/gate.json", _criteria())
    camp = _campaign(tmp_path, gate_criteria={"criteria_ref": "criteria/gate.json"})
    res = _first(tmp_path, camp)
    _write(tmp_path / "criteria/gate.json", _criteria(target=5.0))
    res2 = _second(tmp_path, camp, update_ledger(None, res))
    gate2 = next(r for r in res2["gates"] if r["id"] == GATE)
    assert gate2["stale"] is True
    assert "criteria" in gate2["stale_cause"]["components"]
    assert crit.exists()


# ───────────────────────── C2 失效传播 ─────────────────────────

def test_c2_propagation_within_budget_and_reaches_downstream(tmp_path):
    camp = _campaign(tmp_path)
    ledger = update_ledger(None, _first(tmp_path, camp))
    _write(tmp_path / "evidence/n5.json", {"node": "n5", "payload": "changed"})
    t0 = time.perf_counter()
    res = _second(tmp_path, camp, ledger)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms <= 1000.0, elapsed_ms
    assert res["propagation"]["within_budget"] is True
    assert GATE in res["propagation"]["invalidated_nodes"]
    assert res["propagation"]["max_hops"] >= 1
    assert "n5" in res["propagation"]["changed_nodes"]


def test_upstream_status_change_propagates_to_gate(tmp_path):
    camp = _campaign(tmp_path)
    ledger = update_ledger(None, _first(tmp_path, camp))
    camp2 = json.loads(json.dumps(camp))
    next(n for n in camp2["nodes"] if n["id"] == "n2")["status"] = "failed"
    res = _second(tmp_path, camp2, ledger)
    rows = {r["id"]: r for r in res["gates"]}
    assert rows["n2"]["status"] == STATUS_FAIL          # 节点自身状态来自声明
    assert rows[GATE]["status"] == STATUS_FAIL          # 上游 FAIL 传染
    assert rows[GATE]["stale"] is True                  # 上游状态进 scope → 门过期
    assert "upstream" in rows[GATE]["stale_cause"]["components"]
    assert rows[GATE]["propagated_from"] == [GATE] or "n2" in [
        d["upstream"] for d in rows[GATE]["stale_cause"]["detail"] if d["kind"] == "upstream"]


# ───────────────────────── C3 幂等 ─────────────────────────

def test_c3_three_runs_bitwise_identical(tmp_path):
    camp = _campaign(tmp_path)
    runs = [evaluate_lifecycle(camp, None, roots=[tmp_path]) for _ in range(3)]
    for other in runs[1:]:
        d = gl.diff_results(runs[0], other)
        assert d["identical"] is True, d["diffs"][:3]
        assert other["fingerprint"] == runs[0]["fingerprint"]


def test_ledger_write_is_idempotent(tmp_path):
    camp = _campaign(tmp_path)
    res = _first(tmp_path, camp)
    l1 = update_ledger(None, res)
    l2 = update_ledger(l1, res)
    assert canonical_json(l1) == canonical_json(l2)
    assert l1["gates"][GATE]["revision"] == 1


def test_fingerprint_ignores_wall_clock(tmp_path):
    camp = _campaign(tmp_path)
    a = evaluate_lifecycle(camp, None, roots=[tmp_path])
    time.sleep(0.01)
    b = evaluate_lifecycle(camp, None, roots=[tmp_path])
    assert a["fingerprint"] == b["fingerprint"]
    assert gl.diff_results(a, b)["identical"] is True


# ───────────────────────── C4 三态语义 ─────────────────────────

def test_c4_pending_when_prerequisites_incomplete(tmp_path):
    camp = _campaign(tmp_path)
    res = _first(tmp_path, camp)
    camp2 = json.loads(json.dumps(camp))
    next(n for n in camp2["nodes"] if n["id"] == "n1")["status"] = "in-flight"
    res2 = evaluate_lifecycle(camp2, update_ledger(None, res), roots=[tmp_path])
    gate = next(r for r in res2["gates"] if r["id"] == GATE)
    assert gate["status"] == STATUS_PENDING
    assert gate["reasons"][0]["code"] == "prerequisites_incomplete"
    assert gate["reasons"][0]["dependencies"] == ["n1"]


def test_c4_withheld_when_evidence_missing(tmp_path):
    camp = _campaign(tmp_path)
    Path(tmp_path / "evidence/n7.json").unlink()
    res = _first(tmp_path, camp)
    rows = {r["id"]: r for r in res["gates"]}
    assert rows["n7"]["status"] == STATUS_WITHHELD
    assert rows[GATE]["status"] == STATUS_WITHHELD
    # 缺证据可经两条路径暴露：本门自身证据缺失，或上游 WITHHELD 向上传染
    assert rows[GATE]["reasons"][0]["code"] in ("evidence_missing", "upstream_withheld")
    assert "evidence/n7.json" in rows[GATE]["missing_evidence"]


def test_c4_withheld_when_no_criteria_declared(tmp_path):
    camp = _campaign(tmp_path)
    next(n for n in camp["nodes"] if n["id"] == GATE)["criteria"] = {}
    res = _first(tmp_path, camp)
    gate = next(r for r in res["gates"] if r["id"] == GATE)
    assert gate["status"] == STATUS_WITHHELD
    assert gate["reasons"][0]["code"] == "no_criteria_declared"


def test_c4_withheld_when_criterion_unevaluable(tmp_path):
    camp = _campaign(tmp_path, gate_criteria={"checks": [
        {"name": "pending_metric", "op": "<=", "target": 1.0, "value": None}]})
    res = _first(tmp_path, camp)
    gate = next(r for r in res["gates"] if r["id"] == GATE)
    assert gate["status"] == STATUS_WITHHELD
    assert gate["reasons"][0]["code"] == "criteria_unevaluable"


def test_c4_fail_when_criteria_not_met(tmp_path):
    camp = _campaign(tmp_path, gate_criteria={"checks": [
        {"name": "err_pct", "op": "<=", "target": 3.0, "value": 15.0},
        {"name": "coverage", "op": ">=", "target": 1.0, "value": 1.0}]})
    res = _first(tmp_path, camp)
    gate = next(r for r in res["gates"] if r["id"] == GATE)
    assert gate["status"] == STATUS_FAIL
    assert gate["reasons"][0]["code"] == "criteria_not_met"
    assert gate["reasons"][0]["checks"] == ["err_pct"]
    assert len(gate["checks"]) == 2 and gate["checks"][1]["ok"] is True


def test_c4_pass_when_all_criteria_met(tmp_path):
    res = _first(tmp_path, _campaign(tmp_path))
    gate = next(r for r in res["gates"] if r["id"] == GATE)
    assert gate["status"] == STATUS_PASS
    assert all(g["status"] == STATUS_PASS for g in res["gates"])
    assert res["summary"]["all_states_covered"] is False  # 本夹具只覆盖 PASS


def test_adjudicate_state_priority(tmp_path):
    """证据缺失 + 前置未完成并存时，报 PENDING（该等优先于该补）。"""
    node = {"id": "x", "dependencies": ["a"], "evidence": [{"ref": "nope.json"}]}
    verdict = adjudicate(node, {"a": {"status": "in-flight"}},
                         resolve_evidence(node["evidence"], [tmp_path]),
                         {"criteria": {"checks": []}})
    assert verdict["status"] == STATUS_PENDING


def test_check_operators():
    ok = gl.evaluate_check({"name": "x", "op": "abs<=", "target": 3.0, "value": -2.5})
    bad = gl.evaluate_check({"name": "y", "op": "abs<=", "target": 3.0, "value": -4.0})
    unknown = gl.evaluate_check({"name": "z", "op": "~=", "target": 1, "value": 1})
    assert ok["evaluable"] and ok["ok"]
    assert bad["evaluable"] and not bad["ok"]
    assert not unknown["evaluable"]


# ───────────────────────── DAG 健壮性 ─────────────────────────

def test_cycle_is_withheld_not_crash(tmp_path):
    camp = {"plan": 1, "nodes": [
        {"id": "a", "dependencies": ["b"], "evidence": [], "criteria": _criteria()},
        {"id": "b", "dependencies": ["a"], "evidence": [], "criteria": _criteria()},
    ]}
    res = evaluate_lifecycle(camp, None, roots=[tmp_path])
    assert res["summary"]["cyclic"] == ["a", "b"]
    assert all(r["status"] == STATUS_WITHHELD for r in res["gates"])
    assert res["summary"]["errors"][0]["code"] == "dependency_cycle"


def test_unknown_dependency_is_withheld(tmp_path):
    camp = {"plan": 1, "nodes": [
        {"id": "a", "dependencies": ["ghost"], "evidence": [], "criteria": _criteria()}]}
    res = evaluate_lifecycle(camp, None, roots=[tmp_path])
    assert res["gates"][0]["status"] == STATUS_WITHHELD
    assert res["gates"][0]["reasons"][0]["code"] == "unknown_dependency"


def test_optional_evidence_missing_does_not_block(tmp_path):
    camp = {"plan": 1, "nodes": [{
        "id": "a", "dependencies": [],
        "evidence": [{"ref": "ghost.json", "kind": "file", "required": False}],
        "criteria": _criteria()}]}
    res = evaluate_lifecycle(camp, None, roots=[tmp_path])
    assert res["gates"][0]["status"] == STATUS_PASS


def test_topo_order_is_deterministic(tmp_path):
    camp = _campaign(tmp_path)
    o1 = evaluate_lifecycle(camp, None, roots=[tmp_path])["summary"]["topo_order"]
    o2 = evaluate_lifecycle(camp, None, roots=[tmp_path])["summary"]["topo_order"]
    assert o1 == o2
    assert o1[-1] == GATE  # 汇总门必在全部上游之后


# ───────────────────────── 跨仓接口契约 ─────────────────────────

def _contracts(overrides=None):
    spec = {
        "id": "iface-n1-gate",
        "producer": "n1",
        "consumer": GATE,
        "interface": "evidence/n1.json",
        "producer_ref": "evidence/n1.json",
        "consumer_ref": "evidence/n1.json",
        "producer_fields": ["node", "payload"],
        "consumer_requires": ["payload"],
    }
    spec.update(overrides or {})
    return {"schema": "gate-lifecycle-contracts/1.0", "contracts": [spec]}


def test_contracts_green(tmp_path):
    camp = _campaign(tmp_path)
    res = evaluate_lifecycle(camp, None, roots=[tmp_path], contracts=_contracts())
    assert res["contracts"]["checked"] == 1
    assert res["contracts"]["all_ok"] is True


def test_contracts_broken_on_field_gap(tmp_path):
    camp = _campaign(tmp_path)
    res = evaluate_lifecycle(camp, None, roots=[tmp_path],
                             contracts=_contracts({"consumer_requires": ["payload", "absent"]}))
    assert res["contracts"]["broken"] == 1
    kinds = {c["name"]: c["ok"] for c in res["contracts"]["contracts"][0]["checks"]}
    assert kinds["K3_field_coverage"] is False
    assert kinds["K2_interface_digest"] is True


def test_contracts_broken_when_producer_not_pass(tmp_path):
    camp = _campaign(tmp_path)
    next(n for n in camp["nodes"] if n["id"] == "n1")["criteria"] = {"checks": [
        {"name": "err_pct", "op": "<=", "target": 3.0, "value": 9.0}]}
    res = evaluate_lifecycle(camp, None, roots=[tmp_path], contracts=_contracts())
    row = res["contracts"]["contracts"][0]
    kinds = {c["name"]: c["ok"] for c in row["checks"]}
    assert kinds["K1_producer_pass"] is False
    assert row["ok"] is False


def test_contracts_broken_when_consumer_does_not_declare_ref(tmp_path):
    camp = _campaign(tmp_path)
    res = evaluate_lifecycle(camp, None, roots=[tmp_path],
                             contracts=_contracts({"consumer_ref": "evidence/absent.json"}))
    assert res["contracts"]["broken"] == 1
    assert verify_contracts(_contracts(), res["gates"])["all_ok"] is True


def test_contracts_missing_node(tmp_path):
    camp = _campaign(tmp_path)
    res = evaluate_lifecycle(camp, None, roots=[tmp_path],
                             contracts=_contracts({"consumer": "ghost"}))
    assert res["contracts"]["broken"] == 1


# ───────────────────────── C5 零领域语义 ─────────────────────────

def test_c5_zero_domain_vocabulary_in_module_and_test():
    sys.path.insert(0, str(ROOT))
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    result = scan_paths(
        ["ops/taskplane/gate_lifecycle.py", "tests/test_gate_lifecycle.py"],
        lexicon=load_lexicon(), root=ROOT)
    assert result["scanned_files"] == 2
    assert result["hits"] == [], result["hits"][:5]


# ───────────────────────── C6 CLI 行为 ─────────────────────────

def _cli_args(tmp_path, camp, ledger=None, extra=()):
    camp_path = tmp_path / "campaign.json"
    _write(camp_path, camp)
    args = ["--campaign", str(camp_path), "--roots", str(tmp_path),
            "--json-out", str(tmp_path / "out/result.json"),
            "--md-out", str(tmp_path / "out/result.md"),
            "--ledger-out", str(tmp_path / "out/ledger.json")]
    if ledger is not None:
        lp = tmp_path / "in_ledger.json"
        _write(lp, ledger)
        args += ["--ledger", str(lp)]
    args.extend(extra)
    return args


def test_cli_exit_zero_when_all_pass(tmp_path):
    camp = _campaign(tmp_path)
    assert main(_cli_args(tmp_path, camp)) == 0
    payload = json.loads((tmp_path / "out/result.json").read_text(encoding="utf-8"))
    assert payload["summary"]["pass_count"] == payload["summary"]["node_count"]
    assert (tmp_path / "out/ledger.json").is_file()


def test_cli_exit_two_when_not_ready(tmp_path):
    camp = _campaign(tmp_path, gate_criteria={"checks": [
        {"name": "err_pct", "op": "<=", "target": 3.0, "value": 20.0}]})
    assert main(_cli_args(tmp_path, camp)) == 2


def test_cli_exit_two_when_stale(tmp_path):
    camp = _campaign(tmp_path)
    assert main(_cli_args(tmp_path, camp)) == 0
    ledger = json.loads((tmp_path / "out/ledger.json").read_text(encoding="utf-8"))
    _write(tmp_path / "evidence/n4.json", {"node": "n4", "payload": "drift"})
    assert main(_cli_args(tmp_path, camp, ledger=ledger)) == 2


def test_cli_exit_three_on_bad_input(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["--campaign", str(bad)]) == 3
    assert main(["--campaign", str(tmp_path / "absent.json")]) == 3


def test_cli_repeat_selfcheck_is_identical(tmp_path):
    camp = _campaign(tmp_path)
    assert main(_cli_args(tmp_path, camp, extra=("--repeat", "3"))) == 0
    payload = json.loads((tmp_path / "out/result.json").read_text(encoding="utf-8"))
    assert payload["idempotency"]["identical"] is True
    assert len(set(payload["idempotency"]["fingerprints"])) == 1


# ───────────────────────── DB 装载器（只读，离线可测） ─────────────────────────

def test_build_campaign_from_db_uses_injected_psql(monkeypatch):
    rows = [{
        "id": 11, "name": "task-a", "node_type": "task", "status": "done",
        "dependencies": [], "metadata": {"lifecycle": {"criteria": _criteria()}},
    }, {
        "id": 12, "name": "gate-b", "node_type": "gate", "status": "in-flight",
        "dependencies": [11], "metadata": {},
    }]
    monkeypatch.setattr(gl, "_psql_json", lambda sql, cmd=None: rows)
    camp = gl.build_campaign_from_db(1)
    assert [n["id"] for n in camp["nodes"]] == ["11", "12"]
    assert camp["nodes"][0]["criteria"]["checks"][0]["name"] == "err_pct"
    # 未声明证据时自动派生依赖状态证据（值寻址），保证状态变化能进 scope
    assert camp["nodes"][1]["evidence"][0]["kind"] == "value"
    assert camp["nodes"][1]["dependencies"] == ["11"]


def test_render_markdown_mentions_states(tmp_path):
    res = _first(tmp_path, _campaign(tmp_path))
    md = gl.render_markdown(res)
    assert "PASS" in md and "stale" in md