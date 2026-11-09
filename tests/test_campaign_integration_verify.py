"""campaign_integration_verify 验收测试（合成域：N-A/N-B/N-C + alpha/beta 字段，零领域语义）。

覆盖六条门判据 + 四分类 + 确定性 + CLI 往返：

  a_schema_all_valid      合法 / schema 违规 / 工件缺失 / schema 缺失 四态
  b_edges_consistent      键缺失 / 类型不符 / 量纲不符 / 上游未签约（不可验）
  c_contract_coverage     计划节点未签约 → 阻塞
  d_acceptance_budget     pass 下限 + honest-FAIL 预算上限（含"无证据的 honest FAIL 不算"）
  e_no_undocumented_fail  判据实测越限 = fail 类（不可被 unproven 吞掉）
  f_no_unproven           在飞/无证据节点零容忍（allow_unproven 可显式放开）

设计纪律：夹具全部用合成域名（``N-A``/``N-B``/``N-C``/``alpha``/``unit_x``），
所以本文件既是 TensorPlatform 零领域语义铁律的守法证明，也是任意战役的复用样板。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops" / "taskplane"
sys.path.insert(0, str(OPS))

import campaign_integration_verify as civ  # noqa: E402


# ─────────────────────────── 合成夹具 ───────────────────────────

ALPHA_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "alpha_value"],
    "properties": {
        "schema_version": {"type": "string"},
        "alpha_value": {"type": "number"},
        "beta_value": {"type": "number"},
    },
}


def _evidence(acceptance: dict, quantities: dict | None = None, artifacts=None) -> dict:
    block = {"acceptance": acceptance, "quantities": quantities or {}}
    block["artifacts"] = artifacts if artifacts is not None else ["artifacts/syn/node.json"]
    return {"inheritance_evidence": block}


def make_node(node_id: int, criteria: list[str], status: str = "done",
              verdict: str = "pass", deps: list[int] | None = None,
              mission_id: int | None = None) -> dict:
    return {"id": node_id, "status": status, "reproduction_verdict": verdict,
            "mission_id": mission_id or (1000 + node_id),
            "dependencies": deps or [],
            "metadata": {"acceptance_criteria": criteria}}


def make_snapshot(nodes: list[dict], reports: dict | None = None,
                  missions: dict | None = None) -> dict:
    reports = reports or {}
    missions = missions or {}
    for n in nodes:
        mid = n["mission_id"]
        missions.setdefault(str(mid), {"id": mid, "status": "done"})
        reports.setdefault(str(mid), [])
    return {"plan": {"id": 999, "name": "synthetic-plan"}, "milestones": [],
            "nodes": nodes, "missions": missions, "reports": reports}


def write_artifact(tmp_path: Path, rel: str, payload: dict) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def contract_entry(node_id: int, produces=None, requires=None, artifacts=None,
                   ext_id: str = "", repo: str = "") -> dict:
    return {"node_id": node_id, "ext_id": ext_id, "repo": repo,
            "produces": produces or [], "requires": requires or [],
            "artifacts": artifacts or []}


def good_contract(tmp_path: Path, node_ids=(101, 102, 103)) -> dict:
    """三条全绿合约：N-A 供养 alpha，N-B 依赖 N-A 并供养 beta，N-C 依赖 N-B。

    供给面写在**工件里的证据包**（``inheritance_evidence.quantities``）——这正是
    "声明不是证据"的实现面：合约里的 ``requires`` 只能被上游工件满足。
    """
    write_artifact(tmp_path, "artifacts/syn/a.json",
                   {"schema_version": "1.0", "alpha_value": 1.5,
                    "inheritance_evidence": {"quantities": {"alpha": {"value": 1.5, "unit": "1", "type": "number"}}}})
    write_artifact(tmp_path, "artifacts/syn/b.json",
                   {"schema_version": "1.0", "alpha_value": 1.5, "beta_value": 2.0,
                    "inheritance_evidence": {"quantities": {"beta": {"value": 2.0, "unit": "unit_x", "type": "number"}}}})
    write_artifact(tmp_path, "artifacts/syn/c.json",
                   {"schema_version": "1.0", "alpha_value": 0.5})
    nodes = {}
    a, b, c = node_ids
    nodes["N-A"] = contract_entry(
        a, produces=[{"key": "alpha", "type": "number", "unit": "1"}],
        artifacts=[{"path": "artifacts/syn/a.json", "schema": ALPHA_SCHEMA}])
    nodes["N-B"] = contract_entry(
        b, produces=[{"key": "beta", "type": "number", "unit": "unit_x"}],
        requires=[{"key": "alpha", "type": "number", "unit": "1"}],
        artifacts=[{"path": "artifacts/syn/b.json", "schema": ALPHA_SCHEMA}])
    nodes["N-C"] = contract_entry(
        c, requires=[{"key": "beta", "type": "number", "unit": "unit_x"}],
        artifacts=[{"path": "artifacts/syn/c.json", "schema": ALPHA_SCHEMA}])
    return {"schema_version": civ.CONTRACT_VERSION, "plan_id": 999, "nodes": nodes}


def good_snapshot() -> dict:
    nodes = [make_node(101, ["alpha_err < 3%"], deps=[]),
             make_node(102, ["beta_err < 3%"], deps=[101]),
             make_node(103, ["gamma_err < 5%"], deps=[102])]
    reports = {
        "1101": [_evidence({"alpha_err": {"value": 1.2, "unit": "%"}})],
        "1102": [_evidence({"beta_err": {"value": 0.9, "unit": "%"}})],
        "1103": [_evidence({"gamma_err": {"value": 2.0, "unit": "%"}})],
    }
    return make_snapshot(nodes, reports)


CFG = {"roots": [], "min_pass_nodes": 3, "max_honest_fail_nodes": 0,
       "inheritance_gate": {"require_evidence": True}}


def cfg(tmp_path: Path, **over) -> dict:
    base = dict(CFG)
    base["roots"] = [str(tmp_path)]
    base.update(over)
    return base


# ─────────────────────────── 全绿基线 ───────────────────────────

def test_all_green_gate_passes(tmp_path):
    report = civ.verify(good_snapshot(), good_contract(tmp_path), cfg(tmp_path))
    assert report["verdict"] == "pass", report["blockers"]
    assert report["counts"] == {"pass": 3, "honest_fail": 0, "unproven": 0, "fail": 0, "nodes": 3}
    assert all(c["passed"] for c in report["criteria"])
    assert report["edges"]["n_inconsistent"] == 0
    assert report["edges"]["n_unverifiable"] == 0
    assert report["edges"]["n_edges"] == 2          # N-A→N-B, N-B→N-C


def test_digest_is_deterministic(tmp_path):
    snap, con, c = good_snapshot(), good_contract(tmp_path), cfg(tmp_path)
    r1, r2 = civ.verify(snap, con, c), civ.verify(snap, con, c)
    assert r1["input_digest"] == r2["input_digest"]
    assert civ.render_markdown(r1) == civ.render_markdown(r2)


# ─────────────────────────── (a) schema 契约 ───────────────────────────

def test_schema_violation_blocks_node_and_gate(tmp_path):
    contract = good_contract(tmp_path)
    write_artifact(tmp_path, "artifacts/syn/b.json", {"schema_version": "1.0"})  # 缺 alpha_value
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    assert report["verdict"] == "not_ready"
    row = next(r for r in report["nodes"] if r["node_key"] == "N-B")
    assert row["artifact_status"] == "invalid"
    assert row["classification"] == "unproven"
    assert "artifact_contract:invalid" in row["reason"]
    crit = next(c for c in report["criteria"] if c["id"] == "a_schema_all_valid")
    assert crit["passed"] is False and crit["detail"]["valid"] == 2  # N-A/N-C 合法，N-B 被改坏


def test_missing_artifact_is_not_pass(tmp_path):
    contract = good_contract(tmp_path)
    (tmp_path / "artifacts/syn/a.json").unlink()
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-A")
    assert row["artifact_status"] == "incomplete"
    assert row["artifacts"][0]["status"] == "missing"
    assert row["classification"] != "pass"


def test_undeclared_schema_counts_as_unvalidated(tmp_path):
    contract = good_contract(tmp_path)
    contract["nodes"]["N-A"]["artifacts"] = [{"path": "artifacts/syn/a.json"}]
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-A")
    assert row["artifacts"][0]["status"] == "unvalidated"
    assert row["artifact_status"] == "incomplete"


def test_schema_file_channel(tmp_path):
    contract = good_contract(tmp_path)
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas/alpha.json").write_text(json.dumps(ALPHA_SCHEMA), encoding="utf-8")
    contract["nodes"]["N-A"]["artifacts"] = [{"path": "artifacts/syn/a.json",
                                              "schema": "schemas/alpha.json"}]
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-A")
    assert row["artifacts"][0]["status"] == "valid"
    assert row["artifacts"][0]["schema_source"] == "schemas/alpha.json"


EVIDENCE_SCHEMA = {
    "type": "object",
    "required": ["acceptance", "artifacts"],
    "properties": {
        "acceptance": {"type": "object"},
        "artifacts": {"type": "array"},
        "quantities": {"type": "object"},
    },
}


def test_evidence_bundle_channel_valid(tmp_path):
    """跨仓主通道：平台报告证据包按 schema 校验（本地工件路径在别的仓里解析不到）。"""
    contract = good_contract(tmp_path)
    for key in ("N-A", "N-B", "N-C"):
        contract["nodes"][key]["evidence_schema"] = EVIDENCE_SCHEMA
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    rows = {r["node_key"]: r for r in report["nodes"]}
    assert rows["N-A"]["artifact_status"] == "valid"
    assert rows["N-A"]["artifacts"][0]["path"] == "report:evidence"
    assert rows["N-A"]["artifacts"][0]["checker"] == "jsonschema"


def test_evidence_bundle_missing_blocks(tmp_path):
    contract = good_contract(tmp_path)
    for key in ("N-A", "N-B", "N-C"):
        contract["nodes"][key]["evidence_schema"] = EVIDENCE_SCHEMA
    snap = good_snapshot()
    snap["reports"]["1101"] = []                       # N-A 没发布证据包
    report = civ.verify(snap, contract, cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-A")
    rec = next(i for i in row["artifacts"] if i["path"] == "report:evidence")
    assert rec["status"] == "missing"
    assert rec["errors"] == ["no_evidence_bundle_published"]
    assert row["classification"] != "pass"


def test_evidence_bundle_shape_violation(tmp_path):
    contract = good_contract(tmp_path)
    for key in ("N-A", "N-B", "N-C"):
        contract["nodes"][key]["evidence_schema"] = EVIDENCE_SCHEMA
    snap = good_snapshot()
    snap["reports"]["1102"] = [{"inheritance_evidence": {"acceptance": {}}}]  # 缺 artifacts
    report = civ.verify(snap, contract, cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-B")
    rec = next(i for i in row["artifacts"] if i["path"] == "report:evidence")
    assert rec["status"] == "invalid" and rec["errors"]


# ─────────────────────────── (b) DAG 边接口 ───────────────────────────

def test_edge_missing_required_key(tmp_path):
    contract = good_contract(tmp_path)
    contract["nodes"]["N-C"]["requires"] = [{"key": "delta", "type": "number", "unit": "1"}]
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    edge = report["edges"]["edges"][-1]
    assert edge["downstream"] == "N-C" and edge["status"] == "inconsistent"
    assert edge["violations"][0]["kind"] == "missing_required_key"
    # 供给面形状全绿，但拓扑契约阻塞 → 门不过
    assert next(c for c in report["criteria"] if c["id"] == "b_edges_consistent")["passed"] is False
    assert report["verdict"] == "not_ready"


def test_edge_type_and_unit_mismatch(tmp_path):
    contract = good_contract(tmp_path)
    contract["nodes"]["N-C"]["requires"] = [
        {"key": "beta", "type": "string", "unit": "unit_y"}]
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    kinds = {v["kind"] for v in report["edges"]["edges"][-1]["violations"]}
    assert kinds == {"type_mismatch", "unit_mismatch"}


def test_edge_without_upstream_contract_is_unverifiable(tmp_path):
    contract = good_contract(tmp_path)
    del contract["nodes"]["N-B"]                      # 上游未签约
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    edge = next(e for e in report["edges"]["edges"] if e["downstream"] == "N-C")
    assert edge["status"] == "unverifiable_no_upstream_contract"
    crit = next(c for c in report["criteria"] if c["id"] == "b_edges_consistent")
    assert crit["passed"] is False and crit["detail"]["n_unverifiable"] >= 1


def test_transitive_dependency_closure(tmp_path):
    nodes = [make_node(101, ["alpha_err < 3%"]), make_node(102, ["beta_err < 3%"], deps=[101]),
             make_node(103, ["gamma_err < 5%"], deps=[102])]
    assert civ._dep_closure(nodes, 103, "direct") == [102]
    assert civ._dep_closure(nodes, 103, "transitive") == [102, 101]


def test_edge_supply_must_come_from_published_artifact(tmp_path):
    """声明不是证据：上游只在合约里写 produces、工件里没有该量 → consistent_declared_only。"""
    contract = good_contract(tmp_path)
    write_artifact(tmp_path, "artifacts/syn/b.json",
                   {"schema_version": "1.0", "alpha_value": 1.5, "beta_value": 2.0})
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    edge = next(e for e in report["edges"]["edges"] if e["downstream"] == "N-C")
    assert edge["status"] == "consistent_declared_only"
    assert edge["declared_only"] == ["beta"]
    assert next(c for c in report["criteria"] if c["id"] == "b_edges_consistent")["passed"] is False
    relaxed = civ.verify(good_snapshot(), contract,
                         cfg(tmp_path, allow_declared_only_edges=True))
    assert next(e for e in relaxed["edges"]["edges"] if e["downstream"] == "N-C")["status"] == "consistent"


def test_edge_missing_upstream_artifact_is_unverifiable(tmp_path):
    contract = good_contract(tmp_path)
    contract["nodes"]["N-B"]["produces"] = []          # 无声明兜底
    write_artifact(tmp_path, "artifacts/syn/b.json",
                   {"schema_version": "1.0", "alpha_value": 1.5, "beta_value": 2.0})
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    edge = next(e for e in report["edges"]["edges"] if e["downstream"] == "N-C")
    assert {v["kind"] for v in edge["violations"]} == {"missing_required_key"}
    # 供给只来自 N-B 的平台报告证据（工件里已无该量），且报告里没有 ``beta``
    assert edge["upstream_supply_artifacts"]["N-B"]
    assert all(src.startswith("report:") for src in edge["upstream_supply_artifacts"]["N-B"])
    assert report["verdict"] == "not_ready"


def test_edge_supply_via_platform_report_channel(tmp_path):
    """多仓战役的真实发布通道是平台报告证据块：上游报告里的量即满足下游需求。"""
    contract = good_contract(tmp_path)
    (tmp_path / "artifacts/syn/b.json").write_text(
        json.dumps({"schema_version": "1.0", "alpha_value": 1.5, "beta_value": 2.0}),
        encoding="utf-8")
    snap = good_snapshot()
    snap["reports"]["1102"] = [_evidence({"beta_err": {"value": 0.9, "unit": "%"}},
                                        quantities={"beta": {"value": 2.0,
                                                             "unit": "unit_x",
                                                             "type": "number"}})]
    report = civ.verify(snap, contract, cfg(tmp_path))
    edge = next(e for e in report["edges"]["edges"] if e["downstream"] == "N-C")
    assert edge["status"] == "consistent" and edge["declared_only"] == []


# ─────────────────────────── (c) 覆盖率 ───────────────────────────

def test_uncovered_plan_node_blocks(tmp_path):
    contract = good_contract(tmp_path)
    del contract["nodes"]["N-C"]
    report = civ.verify(good_snapshot(), contract, cfg(tmp_path))
    crit = next(c for c in report["criteria"] if c["id"] == "c_contract_coverage")
    assert crit["passed"] is False and crit["detail"]["uncovered_node_ids"] == [103]
    assert report["verdict"] == "not_ready"


# ─────────────────────────── (d)(e)(f) 四分类与预算 ───────────────────────────

def test_honest_fail_within_budget_passes(tmp_path):
    snap = good_snapshot()
    snap["reports"]["1103"] = [_evidence({"gamma_err": {"value": 9.9, "unit": "%"}})]  # 实测越限
    c = cfg(tmp_path, min_pass_nodes=2, max_honest_fail_nodes=1,
            honest_fail={"N-C": {"reason": "physical limit documented",
                                 "evidence": "artifacts/syn/a.json"}})
    report = civ.verify(snap, good_contract(tmp_path), c)
    assert report["counts"]["honest_fail"] == 1 and report["counts"]["fail"] == 0
    assert report["verdict"] == "pass", report["blockers"]


def test_honest_fail_over_budget_blocks(tmp_path):
    snap = good_snapshot()
    snap["reports"]["1103"] = [_evidence({"gamma_err": {"value": 9.9, "unit": "%"}})]
    c = cfg(tmp_path, min_pass_nodes=2, max_honest_fail_nodes=0,
            honest_fail={"N-C": {"reason": "documented", "evidence": "artifacts/syn/a.json"}})
    report = civ.verify(snap, good_contract(tmp_path), c)
    crit = next(c for c in report["criteria"] if c["id"] == "d_acceptance_budget")
    assert crit["passed"] is False
    assert report["verdict"] == "not_ready"


def test_honest_fail_without_evidence_is_fail(tmp_path):
    snap = good_snapshot()
    snap["reports"]["1103"] = [_evidence({"gamma_err": {"value": 9.9, "unit": "%"}})]
    c = cfg(tmp_path, min_pass_nodes=2, max_honest_fail_nodes=1,
            honest_fail={"N-C": {"reason": "documented"}})   # 无 evidence 指针
    report = civ.verify(snap, good_contract(tmp_path), c)
    row = next(r for r in report["nodes"] if r["node_key"] == "N-C")
    assert row["classification"] == "fail"
    assert row["reason"] == "honest_fail_declared_without_evidence"
    assert next(c2 for c2 in report["criteria"] if c2["id"] == "e_no_undocumented_fail")["passed"] is False


def test_in_flight_node_is_unproven_not_pass(tmp_path):
    snap = good_snapshot()
    snap["nodes"][0]["status"] = "in-flight"
    snap["nodes"][0]["reproduction_verdict"] = None
    snap["missions"]["1101"]["status"] = "running"
    report = civ.verify(snap, good_contract(tmp_path), cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-A")
    assert row["classification"] == "unproven"
    crit = next(c for c in report["criteria"] if c["id"] == "f_no_unproven")
    assert crit["passed"] is False and crit["detail"]["nodes"] == ["N-A"]
    # 显式放开也只是"不阻塞 f"，但 d 的 pass 计数照样不认它
    relaxed = civ.verify(snap, good_contract(tmp_path), cfg(tmp_path, allow_unproven=True))
    assert relaxed["counts"]["unproven"] == 1
    assert next(c for c in relaxed["criteria"] if c["id"] == "d_acceptance_budget")["passed"] is False


def test_criterion_failed_is_fail_class(tmp_path):
    snap = good_snapshot()
    snap["reports"]["1102"] = [_evidence({"beta_err": {"value": 40.0, "unit": "%"}})]
    report = civ.verify(snap, good_contract(tmp_path), cfg(tmp_path))
    row = next(r for r in report["nodes"] if r["node_key"] == "N-B")
    assert row["classification"] == "fail"
    assert row["reason"].startswith("criterion_failed")


# ─────────────────────────── CLI 往返 ───────────────────────────

def test_cli_roundtrip_offline(tmp_path, capsys):
    snap_path = tmp_path / "snapshot.json"
    con_path = tmp_path / "contract.json"
    cfg_path = tmp_path / "config.json"
    snap_path.write_text(json.dumps(good_snapshot()), encoding="utf-8")
    con_path.write_text(json.dumps(good_contract(tmp_path)), encoding="utf-8")
    cfg_path.write_text(json.dumps(cfg(tmp_path)), encoding="utf-8")
    out_json = tmp_path / "out/gate.json"
    out_md = tmp_path / "out/gate.md"

    rc = civ.main(["--snapshot", str(snap_path), "--contract", str(con_path),
                   "--config", str(cfg_path), "--json-out", str(out_json),
                   "--md-out", str(out_md)])
    assert rc == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["verdict"] == "pass"
    md = out_md.read_text(encoding="utf-8")
    assert "跨域集成验证门报告" in md and "| N-A |" in md
    capsys.readouterr()

    # 未就绪必须返回 2（绝不吞成 0）
    bad = good_snapshot()
    bad["nodes"][0]["status"] = "in-flight"
    snap_path.write_text(json.dumps(bad), encoding="utf-8")
    rc2 = civ.main(["--snapshot", str(snap_path), "--contract", str(con_path),
                    "--config", str(cfg_path), "--json-out", str(out_json)])
    assert rc2 == 2


def test_cli_bad_input_returns_3(tmp_path):
    assert civ.main(["--contract", str(tmp_path / "nope.json"), "--snapshot", "x"]) == 3
    con = tmp_path / "c.json"
    con.write_text(json.dumps(good_contract(tmp_path)), encoding="utf-8")
    assert civ.main(["--contract", str(con)]) == 3      # 既无 --snapshot 也无 --fetch-api


# ─────────────────────────── 单位/类型归一化 ───────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Float", "number"), ("int", "number"), ("STRING", "string"),
    ("list", "array"), ("", "any"), ("Mapping", "object"),
])
def test_norm_type(raw, expected):
    assert civ.norm_type(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1", "1"), ("", "1"), ("-", "1"), ("Dimensionless", "1"),
    (" unit_x ", "unit_x"), ("m/s", "m/s"),
])
def test_norm_unit(raw, expected):
    assert civ.norm_unit(raw) == expected