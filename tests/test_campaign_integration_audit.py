"""campaign_integration_audit 验收测试（合成域：N-A/N-B/N-C + alpha/beta 字段，零领域语义）。

覆盖八条机裁判据的正/负两态 + 四分类 + 确定性 + CLI 往返 + 图件冒烟：

  g1_lineage_intact        父计划缺失 / 父计划状态非法 / 链正常
  g2_ext_id_unique         计划内重复 / 与全库注册表撞车
  g3_repo_whitelist        白名单外仓 / 别名归一后可接受
  g4_dag_acyclic           环 / 外计划引用
  g5_node_evidence         在飞无证据 → unproven 阻塞 / 全 pass → 就绪
  g6_ledger_coverage       台账缺条目 / 失败节点被标 REAL（honest FAIL 不可覆盖）
  g7_precision_band        rel_error>15% 条目计入不可接受带
  g8_no_undocumented_fail  失败未申报 → fail 类阻塞；申报+报告 → honest_fail

设计纪律：夹具全部用合成域名（``N-A``/``N-B``/``N-C``/``alpha``/``unit_x``/``RepoAlpha``），
所以本文件既是 TensorPlatform 零领域语义铁律的守法证明，也是任意战役的复用样板。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops" / "taskplane"
sys.path.insert(0, str(OPS))

import campaign_integration_audit as cia  # noqa: E402

PLAN = 9001
PARENT = 9000


# ─────────────────────────── 合成夹具 ───────────────────────────

def crit(key: str, threshold: float = 0.03) -> dict:
    return {"key": key, "op": "<=", "threshold": threshold}


def evidence(acceptance: dict | None = None, artifacts=None) -> dict:
    return {"inheritance_evidence": {
        "acceptance": acceptance if acceptance is not None else {"alpha_err": {"value": 0.02}},
        "artifacts": artifacts if artifacts is not None else ["artifacts/syn/alpha.json"],
    }}


def make_plan(pid: int, parent: int | None = None, status: str = "active") -> dict:
    meta = {"ext_id": f"P{pid}"} if parent is None else {"parent_plan": parent, "ext_id": f"P{pid}"}
    return {"id": pid, "name": f"synthetic-plan-{pid}", "status": status, "metadata": meta}


def make_node(nid: int, ext: str, *, status: str = "done", verdict: str | None = "pass",
              mission_id: int | None = None, repo: str = "RepoAlpha",
              deps: list[int] | None = None, caps: list[str] | None = None,
              plan_id: int = PLAN) -> dict:
    return {"id": nid, "plan_id": plan_id, "status": status,
            "reproduction_verdict": verdict, "mission_id": mission_id,
            "dependencies": deps or [],
            "metadata": {"ext_id": ext, "repo": repo,
                         "acceptance_criteria": [crit("alpha_err")],
                         "capability_ids": caps or []}}


def make_mission(mid: int, status: str = "succeeded") -> dict:
    return {"id": mid, "status": status, "task_key": f"p{PLAN}-{mid}-a1"}


def make_report(rid: int, mid: int, acc: dict | None = None) -> dict:
    payload = evidence(acc)
    return {"id": rid, "mission_id": mid, "status": "published", **payload}


def make_ledger(caps: list[tuple[str, str]]) -> dict:
    return {"schema_version": "tp.ledger/v1",
            "capabilities": [{"id": cid, "level": level} for cid, level in caps]}


def ready_inputs() -> dict:
    """最小就绪战役：3 节点 done+pass+证据齐全，台账 3 条，谱系 9001→9000。"""
    nodes = [make_node(11, "N-A", mission_id=111, deps=[], caps=["cap.alpha"]),
             make_node(12, "N-B", mission_id=112, deps=[11], caps=["cap.beta"]),
             make_node(13, "N-C", mission_id=113, deps=[12], caps=["cap.gamma"])]
    missions = [make_mission(111), make_mission(112), make_mission(113)]
    reports = [make_report(1, 111), make_report(2, 112), make_report(3, 113)]
    return {
        "plan_id": PLAN,
        "plans": [make_plan(PARENT), make_plan(PLAN, parent=PARENT)],
        "nodes": nodes,
        "missions": missions,
        "reports": reports,
        "ext_id_registry": [{"id": n["id"], "plan_id": PLAN,
                             "ext_id": n["metadata"]["ext_id"]} for n in nodes],
        "ledger": make_ledger([("cap.alpha", "REAL"), ("cap.beta", "REAL"), ("cap.gamma", "MOCK")]),
    }


def att_all(rel: float = 0.02) -> list[dict]:
    """三节点各一条显式 rel_error 的 attestation（精度证据的最小合法形态）。"""
    return [{"ext_id": ext, "criteria": [{"id": "alpha_err", "rel_error": rel,
                                          "verdict": "pass"}]}
            for ext in ("N-A", "N-B", "N-C")]


def run(inputs: dict, config: dict | None = None, attestations=None) -> dict:
    cfg = {"plan_id": inputs["plan_id"], "repo_whitelist": ["RepoAlpha", "RepoBeta"]}
    cfg.update(config or {})
    return cia.audit(inputs["plans"], inputs["nodes"], inputs["missions"], inputs["reports"],
                     cfg, ledger=inputs.get("ledger"),
                     ext_id_registry=inputs.get("ext_id_registry"),
                     attestations=attestations, plan_id=inputs["plan_id"])


def crit_by_id(report: dict, cid: str) -> dict:
    return next(c for c in report["criteria"] if c["id"] == cid)


# ─────────────────────────── 正例：八条全过 ───────────────────────────

def test_ready_campaign_passes_all_eight_gates():
    report = run(ready_inputs(), attestations=att_all())
    failed = [c["id"] for c in report["criteria"] if not c["passed"]]
    assert failed == [], failed
    assert report["verdict"] == "ready"
    assert report["classes"] == {"pass": 3, "honest_fail": 0, "unproven": 0, "fail": 0}
    assert report["lineage"]["chain"][-1]["plan_id"] == PARENT
    assert report["dag"]["order"] == [11, 12, 13]
    assert report["precision"]["bands"]["excellent"] == 3
    assert report["precision"]["n_measured"] == 3
    assert report["ledger"]["coverage"] == 1.0
    # 每条判据都带可复算的 measured/threshold，不是空话
    for c in report["criteria"]:
        assert isinstance(c["measured"], (int, float)) and c["threshold"] is not None


def test_schema_version_and_input_digest_stable():
    a = run(ready_inputs(), attestations=att_all())
    b = run(ready_inputs(), attestations=att_all())
    assert a["schema_version"] == cia.SCHEMA_VERSION
    assert a["input_digest"].startswith("sha256:")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_digest_changes_with_input():
    a = run(ready_inputs(), attestations=att_all())
    inputs = ready_inputs()
    inputs["reports"][0]["id"] = 999
    b = run(inputs)
    assert a["input_digest"] != b["input_digest"]


# ─────────────────────────── g1 谱系 ───────────────────────────

def test_g1_missing_parent_blocks():
    inputs = ready_inputs()
    inputs["plans"] = [make_plan(PLAN, parent=777777)]
    report = run(inputs)
    assert not crit_by_id(report, "g1_lineage_intact")["passed"]
    assert any("parent_plan_missing" in p for p in report["lineage"]["problems"])
    assert report["verdict"] == "not_ready"


def test_g1_illegal_parent_state_blocks():
    inputs = ready_inputs()
    inputs["plans"] = [make_plan(PARENT, status="archived"), make_plan(PLAN, parent=PARENT)]
    report = run(inputs)
    assert not crit_by_id(report, "g1_lineage_intact")["passed"]
    assert any("lineage_state_not_allowed" in p for p in report["lineage"]["problems"])


def test_g1_lineage_cycle_detected():
    p1 = make_plan(9002, parent=9003)
    p2 = make_plan(9003, parent=9002)
    problems = cia.check_lineage([p1, p2], 9002, {"max_lineage_depth": 5})
    assert problems["ok"] is False
    assert any("lineage_cycle" in p or "too_deep" in p for p in problems["problems"])


# ─────────────────────────── g2 ext_id ───────────────────────────

def test_g2_duplicate_and_global_collision():
    inputs = ready_inputs()
    inputs["nodes"][2]["metadata"]["ext_id"] = "N-A"  # 计划内重复
    dup_report = run(inputs)
    assert not crit_by_id(dup_report, "g2_ext_id_unique")["passed"]
    assert dup_report["ext_ids"]["duplicates"] == ["N-A"]

    inputs = ready_inputs()
    inputs["ext_id_registry"].append({"id": 88888, "plan_id": 7777, "ext_id": "N-B"})
    col_report = run(inputs)
    g2 = crit_by_id(col_report, "g2_ext_id_unique")
    assert not g2["passed"] and g2["measured"] == 1
    assert col_report["ext_ids"]["collisions"][0]["other_node_ids"] == [88888]


def test_g2_missing_ext_id_blocks():
    inputs = ready_inputs()
    inputs["nodes"][0]["metadata"].pop("ext_id")
    report = run(inputs)
    assert not crit_by_id(report, "g2_ext_id_unique")["passed"]
    assert report["ext_ids"]["missing_ext_id"] == [11]


# ─────────────────────────── g3 仓白名单 ───────────────────────────

def test_g3_repo_outside_whitelist_blocks():
    inputs = ready_inputs()
    inputs["nodes"][1]["metadata"]["repo"] = "RepoRogue"
    report = run(inputs)
    g3 = crit_by_id(report, "g3_repo_whitelist")
    assert not g3["passed"] and g3["measured"] == 1
    assert report["repos"]["violations"][0]["normalized"] == "RepoRogue"


def test_g3_alias_normalization_accepted():
    res = cia.check_repos([{"id": 1, "metadata": {"repo": "alpha"}}],
                          whitelist=["RepoAlpha"], aliases={"alpha": "RepoAlpha"})
    assert res["ok"] and res["violations"] == []


# ─────────────────────────── g4 DAG ───────────────────────────

def test_g4_cycle_blocks_with_readable_path():
    inputs = ready_inputs()
    inputs["nodes"][0]["dependencies"] = [13]  # 13 → 12 → 11 → 13 成环
    report = run(inputs)
    g4 = crit_by_id(report, "g4_dag_acyclic")
    assert not g4["passed"]
    assert report["dag"]["cycles"], report["dag"]
    assert report["verdict"] == "not_ready"


def test_g4_out_of_plan_dependency_blocks():
    inputs = ready_inputs()
    inputs["nodes"][2]["dependencies"] = [9999]
    report = run(inputs)
    assert not crit_by_id(report, "g4_dag_acyclic")["passed"]
    refs = {int(k): [int(x) for x in v] for k, v in report["dag"]["out_of_plan"].items()}
    assert refs == {13: [9999]}
    assert crit_by_id(report, "g4_dag_acyclic")["measured"] == 1


# ─────────────────────────── g5 证据 ───────────────────────────

def test_g5_inflight_node_is_unproven_and_blocks():
    inputs = ready_inputs()
    inputs["missions"][1]["status"] = "running"
    report = run(inputs)
    g5 = crit_by_id(report, "g5_node_evidence")
    assert not g5["passed"]
    assert g5["measured"] == 2 and g5["threshold"] == 3
    assert report["classes"]["unproven"] == 1
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-B")
    assert row["class"] == "unproven" and row["class_reason"] == "in_flight"


def test_g5_missing_report_is_not_evidence():
    inputs = ready_inputs()
    inputs["reports"] = [r for r in inputs["reports"] if r["mission_id"] != 112]
    report = run(inputs)
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-B")
    assert row["class"] == "unproven" and row["class_reason"] == "no_report_evidence"
    assert not crit_by_id(report, "g5_node_evidence")["passed"]


def test_g5_failed_criterion_is_not_pass():
    inputs = ready_inputs()
    inputs["reports"][1] = make_report(2, 112, {"alpha_err": {"value": 0.5}})
    report = run(inputs)
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-B")
    assert row["class"] == "fail" and row["n_failed"] == 1
    # 未声明 error_metric_keys 时判据不冒充精度证据（band=unmeasured）
    assert row["band"] == "unmeasured"


def test_error_metric_keys_make_criterion_value_a_rel_error():
    inputs = ready_inputs()
    inputs["reports"][0] = make_report(1, 111, {"alpha_err": {"value": 0.22}})
    report = run(inputs, config={"error_metric_keys": ["alpha_err"]})
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-A")
    assert row["worst_rel_error"] == pytest.approx(0.22)
    assert row["band"] == "unacceptable"


# ─────────────────────────── g6 台账 ───────────────────────────

def test_g6_missing_ledger_entry_blocks():
    inputs = ready_inputs()
    inputs["ledger"] = make_ledger([("cap.alpha", "REAL")])
    report = run(inputs)
    g6 = crit_by_id(report, "g6_ledger_coverage")
    assert not g6["passed"]
    assert g6["measured"] == pytest.approx(1 / 3)
    assert "N-B::cap.beta" in report["ledger"]["missing"]


def test_g6_ledger_real_for_failed_node_is_violation():
    inputs = ready_inputs()
    inputs["missions"][0]["status"] = "failed"
    inputs["nodes"][0]["status"] = "failed"
    inputs["nodes"][0]["metadata"]["honest_fail"] = True  # 已申报的诚实失败
    report = run(inputs)
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-A")
    assert row["class"] == "honest_fail"
    assert report["ledger"]["honest_fail_violations"], report["ledger"]
    assert report["ledger"]["honest_fail_violations"][0]["capability_id"] == "cap.alpha"


def test_g6_no_capability_declared_fails_closed():
    inputs = ready_inputs()
    for node in inputs["nodes"]:
        node["metadata"]["capability_ids"] = []
    report = run(inputs)
    g6 = crit_by_id(report, "g6_ledger_coverage")
    assert not g6["passed"] and g6["measured"] == 0.0
    assert report["ledger"]["note"] == "no_capability_declared_by_any_node"


# ─────────────────────────── g7 精度带 ───────────────────────────

def test_g7_unacceptable_band_from_attestations():
    att = [{"ext_id": "N-A", "criteria": [
        {"id": "alpha", "rel_error": 0.012, "verdict": "pass"},
        {"id": "beta", "rel_error": 0.42, "verdict": "fail"},
    ]}]
    report = run(ready_inputs(), attestations=att)
    g7 = crit_by_id(report, "g7_precision_band")
    assert not g7["passed"] and g7["measured"] == 1
    bands = report["precision"]["bands"]
    assert bands["excellent"] >= 1 and bands["unacceptable"] == 1
    assert report["precision"]["worst_rel_error"] == pytest.approx(0.42)


def test_g7_no_precision_evidence_fails_closed():
    report = run(ready_inputs())  # 无 attestation、无 error_metric_keys
    g7 = crit_by_id(report, "g7_precision_band")
    assert not g7["passed"] and "no_precision_evidence" in g7["note"]
    assert report["precision"]["n_measured"] == 0


def test_g7_band_edges():
    assert cia.band_of(0.0) == "excellent"
    assert cia.band_of(0.03) == "excellent"
    assert cia.band_of(0.0300001) == "acceptable"
    assert cia.band_of(0.15) == "acceptable"
    assert cia.band_of(0.1500001) == "unacceptable"
    assert cia.band_of(None) == "unmeasured"


def test_g7_measured_from_threshold_when_no_rel_error():
    att = [{"ext_id": "N-A", "criteria": [{"id": "x", "measured": 0.20, "threshold": 0.10}]}]
    summary = cia.precision_summary([], attestations=att)
    assert summary["items"][0]["rel_error"] == pytest.approx(1.0)
    assert summary["bands"]["unacceptable"] == 1


# ─────────────────────────── g8 honest FAIL ───────────────────────────

def test_g8_undeclared_failure_blocks():
    inputs = ready_inputs()
    inputs["missions"][0]["status"] = "failed"
    inputs["nodes"][0]["status"] = "failed"
    report = run(inputs)
    g8 = crit_by_id(report, "g8_no_undocumented_fail")
    assert not g8["passed"] and g8["measured"] == 1
    row = next(r for r in report["nodes"] if r["ext_id"] == "N-A")
    assert row["class"] == "fail" and row["class_reason"] == "failure_not_declared"
    assert report["honest_fail"]["undocumented"] == ["N-A"]


def test_g8_declared_honest_fail_with_report_passes_gate():
    inputs = ready_inputs()
    inputs["missions"][0]["status"] = "failed"
    inputs["nodes"][0]["status"] = "failed"
    cfg = {"honest_fail_registry": ["N-A"]}  # min_pass_nodes 默认 all_peers
    report = run(inputs, config=cfg)
    assert crit_by_id(report, "g8_no_undocumented_fail")["passed"]
    assert report["honest_fail"]["declared"] == ["N-A"]
    assert report["classes"]["honest_fail"] == 1
    # honest FAIL 不可覆盖：它仍让 g5 达不到 all_peers 下界
    assert not crit_by_id(report, "g5_node_evidence")["passed"]
    assert report["verdict"] == "not_ready"


# ─────────────────────────── 多维聚合 & 排除 ───────────────────────────

def test_multiple_violations_reported_together():
    inputs = ready_inputs()
    inputs["nodes"][0]["metadata"]["repo"] = "RepoRogue"
    inputs["nodes"][1]["dependencies"] = [13]
    inputs["nodes"][2]["metadata"]["ext_id"] = "N-B"
    report = run(inputs)
    failed = {c["id"] for c in report["criteria"] if not c["passed"]}
    assert {"g2_ext_id_unique", "g3_repo_whitelist", "g4_dag_acyclic"} <= failed


def test_excluded_self_node_not_counted():
    inputs = ready_inputs()
    inputs["nodes"].append(make_node(14, "N-D", status="in-flight", verdict=None, mission_id=114))
    report = run(inputs, config={"exclude_node_ids": [14]}, attestations=att_all())
    assert report["n_nodes_audited"] == 3
    assert report["excluded_node_ids"] == [14]
    assert report["verdict"] == "ready"


def test_report_markdown_contains_all_sections():
    md = cia.render_markdown(run(ready_inputs()))
    for token in ("八条机裁判据", "节点能力矩阵", "精度带汇总", "能力台账覆盖", "honest FAIL"):
        assert token in md
    assert "g8_no_undocumented_fail" in md


# ─────────────────────────── CLI 往返 ───────────────────────────

def test_cli_roundtrip_ready_and_not_ready(tmp_path):
    inputs = ready_inputs()
    inputs.pop("ledger")
    inputs_path = tmp_path / "inputs.json"
    inputs_path.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(make_ledger([("cap.alpha", "REAL"), ("cap.beta", "REAL"),
                                                   ("cap.gamma", "REAL")])), encoding="utf-8")
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"plan_id": PLAN, "repo_whitelist": ["RepoAlpha"]}),
                        encoding="utf-8")
    att_path = tmp_path / "att.json"
    att_path.write_text(json.dumps(att_all()), encoding="utf-8")
    out = tmp_path / "audit.json"
    md = tmp_path / "audit.md"
    code = cia.main(["--plan", str(PLAN), "--inputs", str(inputs_path), "--config", str(cfg_path),
                     "--ledger", str(ledger_path), "--json-out", str(out), "--md-out", str(md),
                     "--attestations", str(att_path), "--strict"])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"] == "ready" and payload["plan_id"] == PLAN
    assert md.read_text(encoding="utf-8").startswith("# 战役级集成审计")

    # 未就绪 → --strict 返回 3（绝不返回 0）
    inputs["missions"][0]["status"] = "running"
    inputs_path.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    code = cia.main(["--plan", str(PLAN), "--inputs", str(inputs_path), "--config", str(cfg_path),
                     "--ledger", str(ledger_path), "--json-out", str(out),
                     "--attestations", str(att_path), "--strict"])
    assert code == 3
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "not_ready"


def test_cli_missing_input_returns_2(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"plan_id": PLAN}), encoding="utf-8")
    code = cia.main(["--plan", str(PLAN), "--inputs", str(tmp_path / "nope.json"),
                     "--config", str(cfg_path)])
    assert code == 2


# ─────────────────────────── 图件冒烟 ───────────────────────────

def test_figures_render_from_report(tmp_path):
    matplotlib = pytest.importorskip("matplotlib")
    import campaign_integration_audit_figures as figs  # noqa: F401
    report = run(ready_inputs(), attestations=[{"ext_id": "N-A", "criteria": [
        {"id": "alpha", "rel_error": 0.02}, {"id": "beta", "rel_error": 0.30}]}])
    manifest = figs.render_all(report, tmp_path)
    names = [f["figure"] for f in manifest["figures"]]
    assert names == ["fig1_capability_matrix.png", "fig2_precision_bands.png",
                     "fig3_gate_and_classes.png"]
    for name in names:
        assert (tmp_path / name).stat().st_size > 3000
    assert manifest["input_digest"] == report["input_digest"]