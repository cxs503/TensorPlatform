"""campaign_nodes 节点状态层验收测试（清偿 D-R5-1）。

判据（先于实现，机器可裁）：
1. 取值域：status 必须 ∈ {not_started,in_progress,verified,blocked} 且非空，
   SQLite 层由 CHECK 约束硬拒非法值（不是靠 Python 自觉）。
2. 幂等：同一输入连跑两次（**跨进程**，防 hash 随机化偷渡），campaign_status.json 逐字节相同。
3. 对账：状态层节点数 − master_plan.json 节点数 = 0；报告证据的 mission_id 必须能在
   missions 侧查到（0 悬空）；注入一条指向不存在 mission 的报告 → 必被抓出。
4. 非恒空：至少 1 个节点归并为 verified 或 in_progress，且 evidence_ref 可解析回真实工件。
5. 退化：无 missions/reports 时不得崩，全部 not_started、evidence_kind=none。
6. 规则可解释：显式节点号优先于通用词项重合；自排除与盲评期剔除生效。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.campaign_nodes import (
    DDL,
    EVIDENCE_KINDS,
    STATUS_DOMAIN,
    build_signatures,
    build_status,
    derive_status_full,
    dumps_json,
    prepare_evidence_pool,
    read_node_rows,
    read_status_counts,
    render_markdown,
    score_evidence,
    assign_nodes,
    write_status,
    Match,
)

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts"
PLAN_PATH = ART / "m611_master_plan.json"
STATUS_PATH = ART / "campaign_status.json"
CACHE_PATH = ART / "campaign_evidence_cache.json"

# --------------------------------------------------------------------------- #
# 合成夹具：小计划 + 小证据池（不联网、不依赖真实工件）
# --------------------------------------------------------------------------- #
SYNTH_PLAN = {
    "schema_version": "2.0.0",
    "mission_id": "synth",
    "title": "合成主计划（测试夹具）",
    "created_utc": "2026-10-01T00:00:00Z",
    "milestones": {"M0": {"name": "地基"}, "M1": {"name": "标定"}},
    "nodes": [
        {
            "id": "F01",
            "layer": "L1_foundation",
            "title": "Winkler 弹性地基板连续模型",
            "goal": "实现 Winkler 地基板：k_w 参数化，板中心挠度与 Hetényi 解析解对比",
            "acceptance_criteria": [{"metric": "center_deflection_relative_error", "threshold": 0.05}],
            "milestone": "M0",
            "risk": "low",
            "physics_domain": "ice_mechanics",
        },
        {
            "id": "F02",
            "layer": "L1_foundation",
            "title": "板-水附加质量集成到 FEM 质量矩阵",
            "goal": "把 water_added_mass_per_area 接入动力学质量矩阵，修正破冰频率",
            "acceptance_criteria": [{"metric": "pytest_test_added_mass_integration", "threshold": "PASS"}],
            "milestone": "M1",
            "risk": "medium",
            "physics_domain": "fluid_structure_interaction",
        },
        {
            "id": "V01",
            "layer": "L2_validation",
            "title": "Hetényi 级数解验证锚",
            "goal": "简支方板 Winkler 地基 Hetényi 级数解复现，误差 < 1e-3",
            "acceptance_criteria": [{"metric": "series_solution_error", "threshold": 0.001}],
            "milestone": "M0",
            "risk": "low",
            "physics_domain": "validation",
        },
    ],
}


def _mission(mid, goal, status="succeeded", created="2026-10-02T00:00:00Z", **kw):
    row = {
        "id": mid,
        "goal": goal,
        "result": "",
        "status": status,
        "created_at": created,
        "campaign_id": None,
        "campaign_phase": None,
        "repo": "TensorPlatform",
    }
    row.update(kw)
    return row


def _report(rid, mission_id, title, summary="", created="2026-10-03T00:00:00Z"):
    return {
        "id": rid,
        "mission_id": mission_id,
        "title": title,
        "summary": summary,
        "scenario_key": "synthetic",
        "tags": ["synthetic"],
        "created_at": created,
    }


SYNTH_CACHE = {
    "missions": [
        _mission("101", "【F01】Winkler 弹性地基板：center_deflection_relative_error 对标 Hetényi"),
        _mission("102", "【F02】pytest_test_added_mass_integration 质量矩阵正定校验", status="accepted"),
        _mission("103", "【V01】Hetényi 级数解 series_solution_error 复现", status="failed"),
        _mission("104", "campaign_nodes 状态层建设：本仪表自身的任务，不应算作任何节点证据"),
        _mission("105", "【F01】evo1- 盲评期工件，应被剔除", status="accepted"),
        _mission("106", "无关的绿色航行风速场标定任务", status="succeeded"),
    ],
    "reports": [
        _report("901", "101", "F01 Winkler 板挠度对标 Hetényi 报告"),
        _report("902", "102", "F02 附加质量集成报告：pytest_test_added_mass_integration 全绿"),
    ],
}


def _synth_doc(cache=None, plan=None, plan_mission_id="synth"):
    plan = plan or SYNTH_PLAN
    pool = prepare_evidence_pool(cache or SYNTH_CACHE, plan_mission_id)
    return build_status(plan, pool, plan_mission_id)


# --------------------------------------------------------------------------- #
# 1. 取值域 / schema 约束
# --------------------------------------------------------------------------- #
def test_status_domain_constants():
    assert STATUS_DOMAIN == ("not_started", "in_progress", "verified", "blocked")
    assert EVIDENCE_KINDS == ("mission", "report", "commit", "none")


def test_schema_rejects_out_of_domain_status(tmp_path):
    """SQLite CHECK 约束必须硬拒非法 status（不是靠 Python 自觉）。"""
    db = tmp_path / "nodes.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(DDL)
    base = ("m611", "F01", "M0", "L1_foundation", "low", "verified", "mission",
            "mission:1", "2026-10-01T00:00:00Z", "t", 1, "status:verified_mission")
    con.execute(
        "INSERT INTO campaign_nodes (plan_id,node_id,milestone,layer,risk,status,evidence_kind,"
        "evidence_ref,updated_utc,title,evidence_count,match_rule) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        base,
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO campaign_nodes (plan_id,node_id,milestone,layer,risk,status,evidence_kind,"
            "evidence_ref,updated_utc,title,evidence_count,match_rule)"
            " VALUES ('m611','F02','M0','L1','low','done','mission','x','t','t',0,'r')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO campaign_nodes (plan_id,node_id,milestone,layer,risk,status,evidence_kind,"
            "evidence_ref,updated_utc,title,evidence_count,match_rule)"
            " VALUES ('m611','F03','M0','L1','low','verified','git','x','t','t',0,'r')"
        )
    con.close()


def test_all_nodes_present_with_nonempty_status():
    doc = _synth_doc()
    assert [r["node_id"] for r in doc["nodes"]] == ["F01", "F02", "V01"]
    for row in doc["nodes"]:
        assert row["status"] in STATUS_DOMAIN and row["status"]
        assert row["evidence_kind"] in EVIDENCE_KINDS
        assert row["milestone"] and row["layer"] and row["risk"]
        assert row["updated_utc"]


# --------------------------------------------------------------------------- #
# 2. 幂等
# --------------------------------------------------------------------------- #
def test_build_status_is_pure_and_idempotent_in_process():
    a = dumps_json(_synth_doc())
    b = dumps_json(_synth_doc())
    assert a == b


def test_cli_two_runs_byte_identical_across_processes(tmp_path):
    """跨进程跑两次（不同 PYTHONHASHSEED），产物必须逐字节相同。"""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(SYNTH_PLAN, ensure_ascii=False), encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(SYNTH_CACHE, ensure_ascii=False), encoding="utf-8")
    outs = []
    for i, seed in enumerate(("0", "12345")):
        out = tmp_path / f"status_{i}.json"
        db = tmp_path / f"nodes_{i}.sqlite3"
        subprocess.run(
            [sys.executable, "tools/campaign_nodes.py", "build",
             "--plan", str(plan), "--cache", str(cache),
             "--out-json", str(out), "--out-db", str(db)],
            cwd=str(REPO), check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
        )
        outs.append(out.read_bytes())
    assert outs[0] == outs[1], "campaign_status.json 两次运行不逐字节相同（幂等性破坏）"


# --------------------------------------------------------------------------- #
# 3. 对账 / 悬空引用
# --------------------------------------------------------------------------- #
def test_reconciliation_diff_zero_on_synth():
    doc = _synth_doc()
    rec = doc["reconciliation"]
    assert rec["plan_nodes"] == len(SYNTH_PLAN["nodes"])
    assert rec["status_nodes"] == rec["plan_nodes"]
    assert rec["diff"] == 0
    assert rec["dangling_count"] == 0


def test_dangling_report_mission_id_is_detected():
    """注入一条 mission_id 不存在的报告 → 必须计入悬空引用（不能静默通过）。"""
    cache = json.loads(json.dumps(SYNTH_CACHE))
    cache["reports"].append(
        _report("999", "777777", "F01 Winkler 板挠度对标 Hetényi 幽灵报告")
    )
    doc = _synth_doc(cache)
    assert doc["reconciliation"]["dangling_count"] >= 1
    flagged = {(d["ref_id"], d["missing_mission_id"]) for d in doc["reconciliation"]["dangling_refs"]}
    assert ("999", "777777") in flagged


def test_empty_inputs_degrade_gracefully():
    doc = _synth_doc({"missions": [], "reports": []})
    assert len(doc["nodes"]) == len(SYNTH_PLAN["nodes"])
    for row in doc["nodes"]:
        assert row["status"] == "not_started"
        assert row["evidence_kind"] == "none"
        assert row["evidence_ref"] is None
        assert row["evidence_count"] == 0
    assert doc["counts"]["by_status"]["not_started"] == len(SYNTH_PLAN["nodes"])
    assert doc["coverage"]["with_evidence"] == 0
    assert doc["reconciliation"]["diff"] == 0
    # 退化输入仍要能出 markdown / JSON，不得抛异常
    assert "not_started" in render_markdown(doc)
    assert json.loads(dumps_json(doc))["plan_id"] == "synth"


# --------------------------------------------------------------------------- #
# 4. 归并规则（显式优先 / 自排除 / 盲评 / 状态映射）
# --------------------------------------------------------------------------- #
def test_explicit_node_id_beats_keyword_overlap():
    """一段文本既提到 F02 的节点号、又堆满 F01 的通用词 → 应归并到 F02。"""
    signatures, _ = build_signatures(SYNTH_PLAN["nodes"])
    milestones = {n["id"]: n["milestone"] for n in SYNTH_PLAN["nodes"]}
    text = (
        "【F02】pytest_test_added_mass_integration。"
        "Winkler 弹性地基板 挠度 解析解 Hetényi k_w 板中心 挠度 地基板 弹性 系列 级数"
    )
    scores = score_evidence(text, signatures, milestones)
    node_id, detail = assign_nodes(scores)
    assert node_id == "F02"
    assert detail["rule"].startswith("node_id")
    assert scores["F01"]["score"] > 0  # F01 也有词项命中，但更弱


def test_self_instrument_and_blind_period_excluded():
    pool = prepare_evidence_pool(SYNTH_CACHE, "synth")
    goals = " ".join(m["goal"] for m in pool["missions"])
    assert "campaign_nodes" not in goals
    assert "evo1-" not in goals
    assert pool["excluded"]["missions_self"] >= 1
    assert pool["excluded"]["missions_blind"] >= 1


def test_primary_node_slot_beats_plain_mention():
    """|F02| 主播种槽位优先于正文里随口提到的 F01 节点号（同一档最高优先级）。"""
    signatures, _ = build_signatures(SYNTH_PLAN["nodes"])
    milestones = {n["id"]: n["milestone"] for n in SYNTH_PLAN["nodes"]}
    text = "【SIR-PC2|F02|附加质量集成】F01 只是顺带提及：Winkler 地基板 挠度 解析解 Hetényi"
    node_id, detail = assign_nodes(score_evidence(text, signatures, milestones))
    assert node_id == "F02"
    assert detail["rule"] == "node_id_primary"
    assert detail["reasons"][0] == "node_id_primary:F02"


def test_weak_only_evidence_is_flagged():
    """只有词项重合的节点：状态兜底但必须标 +weak_only（不冒充强证据）。"""
    cache = {"missions": [_mission("201",
             "Winkler 弹性地基板 挠度 解析解 Hetényi 简支方板 级数 地基 弹性板 k_w 中心挠度")],
             "reports": []}
    doc = _synth_doc(cache)
    by_id = {r["node_id"]: r for r in doc["nodes"]}
    assert by_id["F01"]["evidence_count"] >= 1
    assert by_id["F01"]["evidence_strong"] == 0
    assert by_id["F01"]["match_rule"].endswith("+weak_only")
    assert by_id["V01"]["status"] == "not_started"  # 无证据节点保持未启动


def test_status_mapping_covers_verified_in_progress_blocked():
    ev = [Match(kind="mission", ref_id="1", mission_id="1", score=9,
                match_rule="strong_id", created_at="2026-10-02T00:00:00Z")]
    assert derive_status_full(ev, ["accepted"])[0] == "verified"
    assert derive_status_full(ev, ["succeeded"])[0] == "in_progress"
    assert derive_status_full(ev, ["awaiting_acceptance"])[0] == "in_progress"
    assert derive_status_full(ev, ["failed"])[0] == "blocked"
    assert derive_status_full(ev, [""])[0] == "in_progress"      # 有证据、状态未知 → 保守
    assert derive_status_full([], [])[0] == "not_started"


def test_synth_statuses_are_evidence_driven():
    doc = _synth_doc()
    by_id = {r["node_id"]: r for r in doc["nodes"]}
    assert by_id["F01"]["status"] == "in_progress"      # 只有 succeeded 交付
    assert by_id["F02"]["status"] == "verified"          # accepted + 报告
    assert by_id["V01"]["status"] == "blocked"           # 唯一证据 failed
    assert by_id["F02"]["evidence_kind"] == "report"
    assert by_id["F02"]["evidence_ref"] == "report:902"


def test_sqlite_rows_match_json_document(tmp_path):
    doc = _synth_doc()
    db = tmp_path / "nodes.sqlite3"
    write_status(db, doc)
    rows = read_node_rows(db, "synth")
    assert [r["node_id"] for r in rows] == [r["node_id"] for r in doc["nodes"]]
    for db_row, js_row in zip(rows, doc["nodes"]):
        assert db_row["status"] == js_row["status"]
        assert db_row["evidence_ref"] == js_row["evidence_ref"]
        assert db_row["updated_utc"] == js_row["updated_utc"]
    counts = dict((st, c) for _, st, c in read_status_counts(db, "synth"))
    # GROUP BY 只返回非零桶；与 JSON 里的全量桶（含 0）对齐后逐项相等
    assert counts == {k: v for k, v in doc["counts"]["by_status"].items() if v > 0}
    assert sum(counts.values()) == len(doc["nodes"])
    # 重复写入不得产生重复行（幂等落库）
    write_status(db, doc)
    assert len(read_node_rows(db, "synth")) == len(doc["nodes"])


# --------------------------------------------------------------------------- #
# 5. 真实工件（m611 27 节点）验收
# --------------------------------------------------------------------------- #
def test_pagination_truncation_is_detected(monkeypatch):
    """服务端忽略 offset（末页重复返回）时必须标记 offset_ignored，不得假装取全。"""
    import tools.campaign_nodes as cn

    def fake_http(url, token=None, payload=None):
        if url.endswith("/auth/login"):
            return {"access_token": "t"}
        # 无论 offset 多少都返回同一页 3 条 → 模拟 offset 被忽略
        return {"items": [{"id": i, "status": "succeeded"} for i in (1, 2, 3)], "total": 3}

    monkeypatch.setattr(cn, "_http_json", fake_http)
    pool = cn.fetch_pool("http://x/api/v1", "u", "p", page_size=3)
    assert pool["reports_offset_ignored"] is True
    assert len(pool["missions"]) == 3
    assert len(pool["reports"]) == 3


pytestmark_artifacts = pytest.mark.skipif(
    not (PLAN_PATH.exists() and STATUS_PATH.exists() and CACHE_PATH.exists()),
    reason="真实工件缺失（先跑 tools/campaign_nodes.py fetch/build）",
)


@pytestmark_artifacts
def test_real_artifact_covers_all_27_plan_nodes():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    doc = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    plan_ids = [n["id"] for n in plan["nodes"]]
    doc_ids = [r["node_id"] for r in doc["nodes"]]
    assert len(plan_ids) == 27
    assert sorted(doc_ids) == sorted(plan_ids)
    assert doc["reconciliation"]["diff"] == 0
    for row in doc["nodes"]:
        assert row["status"] in STATUS_DOMAIN and row["status"]
        assert row["evidence_kind"] in EVIDENCE_KINDS
        assert row["milestone"] and row["layer"] and row["risk"] and row["updated_utc"]


@pytestmark_artifacts
def test_real_artifact_has_no_dangling_mission_refs():
    doc = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    mission_ids = {str(m["id"]) for m in cache["missions"]}
    assert doc["reconciliation"]["dangling_count"] == 0
    for row in doc["nodes"]:
        for ev in row["evidence"]:
            if ev["kind"] == "report" and ev["mission_id"]:
                assert ev["mission_id"] in mission_ids, ev
            if ev["kind"] == "mission":
                assert ev["ref_id"] in mission_ids, ev


@pytestmark_artifacts
def test_real_artifact_has_resolvable_nonempty_evidence():
    """非恒空：至少 1 个节点 verified/in_progress，且其 evidence_ref 能解析回真实工件。"""
    doc = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    mission_ids = {str(m["id"]) for m in cache["missions"]}
    report_ids = {str(r["id"]) for r in cache["reports"]}
    active = [r for r in doc["nodes"] if r["status"] in ("verified", "in_progress")]
    assert active, "归并器恒空：没有任何节点进入 verified/in_progress"
    for row in active:
        ref = row["evidence_ref"]
        assert ref and ":" in ref
        kind, ref_id = ref.split(":", 1)
        if kind == "report":
            assert ref_id in report_ids
        elif kind == "mission":
            assert ref_id in mission_ids


@pytestmark_artifacts
def test_real_artifact_labelled_subset_has_zero_misassignment():
    """误配率量化：以「任务书标题里的主播种节点号槽位 |Fxx|」为弱标注真值，
    统计归并器在该子集上的误配率，必须为 0（这是本层唯一可机器复算的精度判据）。"""
    doc = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    node_ids = [n["id"] for n in plan["nodes"]]
    labelled = {}
    for m in cache["missions"]:
        hits = [i for i in node_ids if f"|{i}|" in (m.get("goal") or "")]
        if len(hits) == 1:
            labelled[str(m["id"])] = hits[0]
    assigned = {
        ev["ref_id"]: row["node_id"]
        for row in doc["nodes"] for ev in row["evidence"] if ev["kind"] == "mission"
    }
    assert len(labelled) >= 20, "标注子集太小，判据失去意义"
    wrong = {mid: (exp, assigned.get(mid)) for mid, exp in labelled.items()
             if assigned.get(mid) != exp}
    assert wrong == {}, f"标注子集存在误配: {wrong}"
    covered = {exp for mid, exp in labelled.items() if assigned.get(mid) == exp}
    assert covered == set(node_ids), f"有节点没有归到自己的播种任务: {set(node_ids) - covered}"


@pytestmark_artifacts
def test_real_artifact_is_reproducible_from_cache(tmp_path):
    """用同一 cache/plan 重跑 build → 与仓库内 campaign_status.json 逐字节相同。"""
    out = tmp_path / "status.json"
    subprocess.run(
        [sys.executable, "tools/campaign_nodes.py", "build",
         "--plan", str(PLAN_PATH), "--cache", str(CACHE_PATH), "--out-json", str(out)],
        cwd=str(REPO), check=True, capture_output=True,
        env={"PYTHONHASHSEED": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert out.read_bytes() == STATUS_PATH.read_bytes()