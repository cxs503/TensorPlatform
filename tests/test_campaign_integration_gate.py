"""战役集成与验收门机制测试（合成域夹具：不使用任何真实学科词条）。

覆盖矩阵
--------
* 收集层：artifact sha256 + 协议块探测；缺失 → ``delivered=false``（缺就写缺）；
  自述交付态随 ``completed_states`` 配置走（不写死单一状态词）
* 规格层：由快照程序化生成、剔除集成节点自身、能被严格解析器接受
* 契约层：单位/轴/样本一致 → PASS；不一致 → 具名违反；**任一侧缺证据 → WITHHELD（反空真）**
* 注册表：节点键/mission 重复、报告自述 mission ≠ 索引 mission、跨 mission 重复标题
* 成熟化：四道准入全过才 REAL；SQL 幂等形态 + 回滚覆盖 + 零 REAL 时为空操作
* 端到端：合成战役全绿 → 交付层 PASS；零交付 → 证据层 WITHHELD + 交付层 FAIL
* 真战役重放：提交的判定体守住反造假不变量（无证据不 PASS / WITHHELD 不写 REAL）
* 确定性：同一输入两次运行判定体逐字节相同
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.campaign_integration import cli as CI  # noqa: E402
from tools.campaign_integration import contracts as C  # noqa: E402
from tools.campaign_integration import maturity as M  # noqa: E402
from tools.campaign_integration import sources as S  # noqa: E402
from tools.maturity_gate.spec import parse_spec  # noqa: E402

OWN = "node-omega"


# ---------------------------------------------------------------- 夹具
def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _evidence(delivered: bool = True, declared: int = 2, covered: int = 2,
              claim_all: bool = True, interfaces=None, pytest_rc: int = 0) -> dict:
    doc = {
        "claim": {"delivered": delivered},
        "delivery": {"delivered": delivered, "artifact_count": 1 if delivered else 0,
                     "artifacts": [{"path": "artifacts/ev/x.json", "sha256": "0" * 64,
                                    "bytes": 10}]},
        "pytest": {"returncode": pytest_rc, "passed": 5, "failed": 0 if pytest_rc == 0 else 1},
        "acceptance": {"declared_total": declared, "covered_values": covered,
                       "claim_all_pass": claim_all},
    }
    if interfaces:
        doc["interfaces"] = interfaces
    return doc


def _iface(samples=(0, 1, 2), qsamples=(1.0, 2.0, 3.0), unit="N", axis_unit="s"):
    return {"iface-1": {"axis": {"field": "t", "unit": axis_unit, "samples": list(samples)},
                        "quantities": {"q_a": {"unit": unit, "samples": list(qsamples)}}}}


def _snapshot(keys=("node-alpha", "node-beta"), status="done", acceptance=2) -> dict:
    nodes = [{
        "node_key": k, "campaign_node_id": i, "mission_id": 9000 + i,
        "title": f"task {k}", "repo": "repo-x", "status": status, "block_reason": None,
        "consecutive_fails": 0, "actual_end": None,
        "metadata": {"acceptance_criteria": [f"crit-{j}" for j in range(acceptance)]},
    } for i, k in enumerate(keys)]
    nodes.append({"node_key": OWN, "campaign_node_id": 99, "mission_id": 9099,
                  "title": "task own", "repo": "repo-x", "status": "in-flight",
                  "block_reason": None, "consecutive_fails": 0, "actual_end": None,
                  "metadata": {"acceptance_criteria": ["crit-0"]}})
    return {"schema": "campaign-snapshot/1.0", "fetched_at": "2026-01-01T00:00:00+00:00",
            "spec_id": "campaign-unit", "plan": {"plan_id": 1, "name": "unit-campaign",
                                                 "description": None, "status": "active"},
            "nodes": nodes, "reports": {}}


def _contracts(keys=("node-alpha", "node-beta")) -> dict:
    return {"schema": "campaign-contracts/1.0", "contracts": [{
        "id": "C1", "kind": "interface", "producer": keys[0], "consumer": keys[1],
        "interface": "iface-1", "required_fields": ["axis", "quantities"],
        "axis": {"field": "t", "unit": "s", "monotonic": "increasing"},
        "quantities": [{"name": "q_a", "unit": "N", "tolerance_rel": 1.0e-09}],
    }]}


def _cfg(tmp_path: Path, work: Path, min_real: int = 1) -> dict:
    return {
        "schema": "campaign-sources/1.0", "work_root": str(work),
        "worktree_template": "m{mission_id}", "evidence_dir": "artifacts/ev",
        "evidence_doc_template": "artifacts/ev/{node_key}.json",
        "collect_patterns": ["artifacts/ev/*.json"],
        "integration_out": str(tmp_path / "out"),
        "report_path": str(tmp_path / "report.md"),
        "spec_path": str(tmp_path / "spec.json"),
        "integration_node_key": OWN,
        "defaults": {"min_real_registrations": min_real,
                     "completed_states": ["completed", "done"]},
        "nodes": {},
    }


def _env(tmp_path: Path, keys=("node-alpha", "node-beta"), deliver=True,
         min_real: int = 1, interfaces=None):
    work = tmp_path / "work"
    for i, k in enumerate(keys):
        if deliver:
            _write(work / f"m{9000 + i}" / "artifacts" / "ev" / f"{k}.json",
                   _evidence(interfaces=interfaces or _iface()))
    snap = _snapshot(keys)
    cfg = _cfg(tmp_path, work, min_real)
    snap_p = _write(tmp_path / "snapshot.json", snap)
    cfg_p = _write(tmp_path / "sources.json", cfg)
    con_p = _write(tmp_path / "contracts.json", _contracts(keys))
    return {"snapshot": snap_p, "sources": cfg_p, "contracts": con_p,
            "snap": snap, "cfg": cfg, "work": work}


def _run(env, write_figures=False):
    return CI.deliver(env["snapshot"], env["sources"], env["contracts"],
                      out_dir=env["cfg"]["integration_out"],
                      report_path=env["cfg"]["report_path"],
                      spec_path=env["cfg"]["spec_path"],
                      write_figures=write_figures)


# ---------------------------------------------------------------- 收集层
def test_scan_artifacts_records_sha_and_protocol_blocks(tmp_path):
    work = tmp_path / "m9000"
    _write(work / "artifacts" / "ev" / "node-alpha.json", _evidence())
    found = S.scan_artifacts(work, ["artifacts/ev/*.json"])
    assert len(found) == 1
    rec = found[0]
    assert rec["json"] is True
    assert set(S.PROTOCOL_BLOCKS).issubset(rec["protocol_blocks"])
    assert len(rec["sha256"]) == 64


def test_presence_absent_is_recorded_not_skipped(tmp_path):
    env = _env(tmp_path, deliver=False)
    collected = S.collect(env["snap"], env["cfg"])
    doc = collected["presence"]["node-alpha"]
    assert doc["delivery"]["delivered"] is False
    assert doc["delivery"]["artifact_count"] == 0
    assert doc["delivery"]["protocol_conformant"] is False
    # 集成节点自身不进上游分母
    assert collected["summary"]["nodes_total"] == 2
    assert collected["summary"]["artifact_coverage"] == 0.0


def test_presence_claim_follows_completed_states(tmp_path):
    env = _env(tmp_path)
    done = S.build_presence(env["snap"]["nodes"][0], env["cfg"], [])
    assert done["claim"]["delivered"] is True          # status = done
    blocked = dict(env["snap"]["nodes"][0], status="blocked")
    assert S.build_presence(blocked, env["cfg"], [])["claim"]["delivered"] is False


# ---------------------------------------------------------------- 规格层
def test_spec_is_generated_strict_and_excludes_integration_node(tmp_path):
    env = _env(tmp_path)
    spec = S.build_spec(env["snap"], env["cfg"])
    parsed = parse_spec(spec)                           # 严格解析器必须接受
    assert len(parsed.nodes) == 2
    assert OWN not in {n.node_key for n in parsed.nodes}
    ids = [c.criterion_id for n in parsed.nodes for c in n.criteria]
    assert ids.count("delivery") == 2 and ids.count("acceptance") == 2
    # 验收条目数直接来自注册表 metadata（2 条）→ 阈值 = 2
    acc = [c for n in parsed.nodes for c in n.criteria if c.criterion_id == "acceptance"][0]
    assert any(chk.threshold == 2 for chk in acc.checks)


# ---------------------------------------------------------------- 契约层
def test_interface_contract_passes_when_both_sides_consistent(tmp_path):
    env = _env(tmp_path)
    presence = S.collect(env["snap"], env["cfg"])["presence"]
    res = C.check_interface_contract(_contracts()["contracts"][0], presence)
    assert res["status"] == C.PASS
    assert res["violations"] == []
    assert res["checks"] > 0


def test_interface_contract_flags_unit_mismatch(tmp_path):
    env = _env(tmp_path)
    # 生产端单位改成 kN：同名不同单位必须被抓
    _write(env["work"] / "m9000" / "artifacts" / "ev" / "node-alpha.json",
           _evidence(interfaces=_iface(unit="kN")))
    presence = S.collect(env["snap"], env["cfg"])["presence"]
    res = C.check_interface_contract(_contracts()["contracts"][0], presence)
    assert res["status"] == C.FAIL
    assert any(v["rule"] == "quantity_unit_mismatch" for v in res["violations"])


def test_interface_contract_flags_non_monotonic_axis(tmp_path):
    env = _env(tmp_path)
    _write(env["work"] / "m9001" / "artifacts" / "ev" / "node-beta.json",
           _evidence(interfaces=_iface(samples=(0, 2, 1))))
    presence = S.collect(env["snap"], env["cfg"])["presence"]
    res = C.check_interface_contract(_contracts()["contracts"][0], presence)
    assert res["status"] == C.FAIL
    assert any(v["rule"] == "axis_not_monotonic" for v in res["violations"])


def test_interface_contract_withheld_when_a_side_missing(tmp_path):
    """反空真：一侧没交付 → WITHHELD，绝不算 0 违反。"""
    env = _env(tmp_path)
    (env["work"] / "m9001" / "artifacts" / "ev" / "node-beta.json").unlink()
    presence = S.collect(env["snap"], env["cfg"])["presence"]
    res = C.check_interface_contract(_contracts()["contracts"][0], presence)
    assert res["status"] == C.WITHHELD
    assert "interface_absent" in res["reason"]
    overall = C.evaluate(_contracts(), env["snap"], presence)
    assert overall["verdict"] == C.WITHHELD          # 不允许「未检 = 0 违反 = PASS」
    assert overall["interface_contracts_checked"] == 0


def test_registry_flags_report_link_mismatch_and_duplicate_titles(tmp_path):
    env = _env(tmp_path)
    snap = json.loads(json.dumps(env["snap"]))
    snap["reports"] = {
        "9000": [{"id": 1, "title": "same title", "claimed_missions": [1234],
                  "mission_link_mismatch": True}],
        "9001": [{"id": 2, "title": "same title", "claimed_missions": [],
                  "mission_link_mismatch": False}],
    }
    presence = S.collect(snap, env["cfg"])["presence"]
    res = C.check_registry(snap, presence)
    rules = {v["rule"] for v in res["violations"]}
    assert "report_mission_mismatch" in rules
    assert "report_duplicate_across_missions" in rules
    assert res["status"] == C.FAIL


# ---------------------------------------------------------------- 成熟化
def _gate_stub(**over):
    summary = {"node_key": "node-alpha", "title": "task", "capability": "cap",
               "repo": "repo-x", "mission_id": 9000, "criteria_total": 3, "passed": 3,
               "failed": 0, "withheld": 0, "pass_ratio": 1.0, "claim_mismatches": []}
    summary.update(over)
    return {"campaign": {"criteria_pass_threshold": 0.8},
            "node_summaries": [summary], "input_digest": "digest"}


def _presence_stub(delivered=True):
    return {"node-alpha": {"delivery": {"delivered": delivered, "artifact_count": 1,
                                        "artifacts": [{"path": "a.json", "sha256": "f" * 64}]}}}


def test_maturity_requires_all_four_gates(tmp_path):
    cfg = _cfg(tmp_path, tmp_path / "work")
    ok = M.build_entries(_gate_stub(), _presence_stub(True), cfg)[0]
    assert ok["register"] is True and ok["evidence_level"] == M.LEVEL_REAL
    for over, reason in (({"pass_ratio": 0.5}, "pass_ratio_below_threshold"),
                         ({"failed": 1}, "criteria_failed"),
                         ({"claim_mismatches": ["delivery"]}, "claim_mismatch")):
        entry = M.build_entries(_gate_stub(**over), _presence_stub(True), cfg)[0]
        assert entry["register"] is False
        assert reason in entry["defer_reasons"]
    no_art = M.build_entries(_gate_stub(), _presence_stub(False), cfg)[0]
    assert no_art["register"] is False
    assert no_art["evidence_level"] == M.LEVEL_WITHHELD


def test_maturity_sql_is_idempotent_and_rollback_covers(tmp_path):
    cfg = _cfg(tmp_path, tmp_path / "work")
    entries = M.build_entries(_gate_stub(), _presence_stub(True), cfg)
    entries += M.build_entries(_gate_stub(node_key="node-beta"), _presence_stub(False), cfg)
    campaign = {"plan_id": 1, "name": "unit-campaign"}
    sql = M.render_insert_sql(entries, campaign, "digest")
    rb = M.render_rollback_sql(entries, campaign)
    assert sql.count("WHERE NOT EXISTS") == 1            # 只给 REAL 条目写 INSERT
    assert "node-beta" not in sql                         # WITHHELD 不进 SQL
    assert M.rollback_covers(sql, rb) is True
    assert sql == M.render_insert_sql(entries, campaign, "digest")   # 确定性
    empty_sql = M.render_insert_sql(entries[1:], campaign, "digest")
    assert "INSERT INTO feature_ledger" not in empty_sql  # 零 REAL = 空操作


def test_self_capability_positive_control(tmp_path):
    """集成节点自身机制条目：证据齐 + 测试绿 → REAL；缺证据/测试红 → deferred。"""
    cfg = _cfg(tmp_path, tmp_path / "work")
    ev = tmp_path / "code.py"
    ev.write_text("x = 1\n", encoding="utf-8")
    cfg["self_capability"] = {"slug": "mech.x", "title": "mech", "capability": "mech",
                              "domain": "mech", "evidence_paths": ["code.py"],
                              "require_green_tests": True}
    good = M.build_entries(_gate_stub(), _presence_stub(True), cfg, repo_root=tmp_path,
                           self_tests={"green": True, "returncode": 0})
    self_entry = [e for e in good if e.get("self")][0]
    assert self_entry["register"] is True
    assert self_entry["evidence_level"] == M.LEVEL_REAL
    assert self_entry["evidence"]["artifacts"][0]["sha256"]

    red = M.build_entries(_gate_stub(), _presence_stub(True), cfg, repo_root=tmp_path,
                          self_tests={"green": False, "returncode": 1})
    assert "self_tests_not_green" in [e for e in red if e.get("self")][0]["defer_reasons"]

    cfg["self_capability"]["evidence_paths"] = ["missing.py"]
    missing = M.build_entries(_gate_stub(), _presence_stub(True), cfg, repo_root=tmp_path,
                              self_tests={"green": True, "returncode": 0})
    assert "self_evidence_missing" in [e for e in missing if e.get("self")][0]["defer_reasons"]
    # REAL 自条目必须在 INSERT 里出现，并被回滚覆盖
    sql = M.render_insert_sql([self_entry], {"plan_id": 1, "name": "u"}, "d")
    rb = M.render_rollback_sql([self_entry], {"plan_id": 1, "name": "u"})
    assert "mech.x" in sql and "self capability" in sql
    assert M.rollback_covers(sql, rb) is True


# ---------------------------------------------------------------- 端到端
def test_end_to_end_complete_campaign_passes(tmp_path):
    env = _env(tmp_path, min_real=1)
    res = _run(env, write_figures=True)
    gate = res["gate"]
    assert gate["campaign"]["verdict"] == "PASS"
    assert gate["delivery_verdict"] == "PASS"
    assert {k: v["status"] for k, v in gate["delivery_criteria"].items()} == {
        "a": "PASS", "b": "PASS", "c": "PASS", "d": "PASS", "e": "PASS", "f": "PASS"}
    assert gate["maturity_summary"]["real_total"] == 2
    assert "INSERT INTO feature_ledger" in res["insert_sql"]
    assert res["report_check"]["complete"] is True
    assert Path(gate["report_path"]).is_file()
    for fig in gate["figures"]:
        assert Path(fig["path"]).is_file()


def test_end_to_end_no_delivery_is_withheld_and_fails(tmp_path):
    env = _env(tmp_path, deliver=False, min_real=1)
    res = _run(env)
    gate = res["gate"]
    assert gate["campaign"]["verdict"] == "WITHHELD"      # 不知道 ≠ 不过 ≠ 过
    assert gate["delivery_verdict"] == "FAIL"              # 交付层实测不达标
    assert set(gate["delivery_hard_fail"]) >= {"a", "e"}
    assert gate["maturity_summary"]["real_total"] == 0
    assert "INSERT INTO feature_ledger" not in res["insert_sql"]


def test_end_to_end_claim_mismatch_is_caught(tmp_path):
    """自述交付=true 但 artifact 缺失 → claim_mismatch（不改判但一票入账）。"""
    env = _env(tmp_path, deliver=False, min_real=1)
    # 上游自述「已交付」(claim=true) 但实测 delivery.delivered=false → 互裁必须抓出来
    doc = _evidence(delivered=False)
    doc["claim"]["delivered"] = True
    _write(env["work"] / "m9000" / "artifacts" / "ev" / "node-alpha.json", doc)
    res = _run(env)
    node = [n for n in res["gate"]["nodes"] if n["node_key"] == "node-alpha"][0]
    assert any(c["verdict"] == "FAIL" for c in node["criteria"])
    assert res["gate"]["campaign"]["claim_mismatch_total"] >= 1


def test_end_to_end_is_deterministic(tmp_path):
    env = _env(tmp_path, min_real=1)
    _run(env)
    first = (Path(env["cfg"]["integration_out"]) / "gate.json").read_text(encoding="utf-8")
    _run(env)
    second = (Path(env["cfg"]["integration_out"]) / "gate.json").read_text(encoding="utf-8")
    assert first == second


def test_maturity_sql_smoke_on_scratch_temp_table(tmp_path):
    """成熟化单 SQL 在会话级 TEMP 表上真跑：语法/幂等/回滚干净（不碰线上数据）。"""
    from tools.campaign_integration import sql_smoke as SS

    probe = SS.psql_available()
    if not probe.get("available"):
        pytest.skip(f"psql/TEMP 不可用：{probe.get('reason')}")
    cfg = _cfg(tmp_path, tmp_path / "work")
    entries = M.build_entries(_gate_stub(), _presence_stub(True), cfg)
    entries += M.build_entries(_gate_stub(node_key="node-beta"), _presence_stub(False), cfg)
    campaign = {"plan_id": 1, "name": "unit-campaign"}
    res = SS.verify(M.render_insert_sql(entries, campaign, "digest"),
                    M.render_rollback_sql(entries, campaign))
    assert res["available"] is True
    assert res["ok"] is True
    assert res["after_first"] == 1 and res["after_second"] == 1     # 幂等
    assert res["after_rollback"] == 0                                # 回滚干净


# ---------------------------------------------------------------- 真战役重放（回归守卫）
def test_real_campaign_gate_keeps_anti_fake_pass_invariants():
    out = REPO_ROOT / "artifacts" / "p491w2_maturity"
    gate = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    sql = (out / "feature_ledger.sql").read_text(encoding="utf-8")
    rollback = (out / "feature_ledger_rollback.sql").read_text(encoding="utf-8")
    maturity = json.loads((out / "maturity_plan.json").read_text(encoding="utf-8"))

    camp = gate["campaign"]
    assert camp["nodes_total"] == 9                       # N01–N09，不含集成节点自身
    # 不变量 1：证据层 PASS 只可能在全部节点都交付时出现
    if camp["verdict"] == "PASS":
        assert camp["nodes_delivered"] == camp["nodes_total"]
    # 不变量 2：REAL 条目必须真有 artifact（WITHHELD 不写 REAL）
    for entry in maturity["entries"]:
        if entry["evidence_level"] == M.LEVEL_REAL:
            assert entry["evidence"]["artifacts"], entry["slug"]
    # 不变量 3：SQL 与台账一致；零 REAL 时 SQL 为空操作且回滚覆盖成立
    if maturity["real_total"] == 0:
        assert "INSERT INTO feature_ledger" not in sql
        assert "DELETE FROM feature_ledger" not in rollback
    assert M.rollback_covers(sql, rollback) is True
    # 不变量 4：缺证据的判据不得被记成 PASS
    for node in gate["nodes"]:
        for crit in node["criteria"]:
            if crit["verdict"] == "PASS":
                assert all(chk["evidence"]["sha256"] for chk in crit["checks"]), node["node_key"]
    # 审计链字段齐备
    assert camp["verdict"] in {"PASS", "FAIL", "WITHHELD"}
    assert gate["input_digest"] and gate["spec_path"] and gate["artifacts"]["spec"]