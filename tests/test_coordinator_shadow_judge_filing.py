#!/usr/bin/env python3
"""coordinator_shadow v1 —— 判读与立单纪律测试（IN27-H1）。

判据：
1. LLM 解析健壮：围栏/前后废话/嵌套 JSON 都能抠出；坏 JSON → 明确失败（不编）。
2. 幻觉门：字典外器官/指标一律丢弃并记 ``ungrounded``，不能变成可立单项。
3. LLM 只能否决/解释，**不能新增越界**（规则层定越界）。
4. 立单纪律：持久性 ≥2 拍、签名冷却 6h、器官级去重（一病灶一单）、开单上限、每拍上限。
5. 立单幂等：同 task_key 二次提交 → duplicate（库级唯一索引兜底）。
6. 写边界：立单通道只放行那一条 INSERT。
7. 诊断单 goal 内嵌可复算证据（SQL 原文 + 参数 + 指纹）。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.coordinator_shadow import diagnose as dg  # noqa: E402
from tools.coordinator_shadow import filing as fl  # noqa: E402
from tools.coordinator_shadow import judge as jd  # noqa: E402
from tools.coordinator_shadow import pulse as pu  # noqa: E402
from tools.coordinator_shadow.pulse import Metric, Pulse  # noqa: E402

T0 = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)


def _pulse(hours: float = 12.0, *, credit=30, oldest=45, stuck=1, decl=0.85, running=4,
           error=0, warn=3, blocked=1, deltas=12, await_pr=3, queued=6, pending=12) -> Pulse:
    vals = {
        "credit_backlog": credit, "oldest_queued_minutes": oldest, "queued_stuck_2h": stuck,
        "declaration_rate": decl, "running": running, "error_total": error, "warn_total": warn,
        "campaign_blocked": blocked, "capability_deltas_24h": deltas,
        "awaiting_pr_merge": await_pr, "queued_open": queued, "campaign_pending": pending,
    }
    units = {"declaration_rate": "ratio", "oldest_queued_minutes": "minutes"}
    organs = {k: Metric(name=k, value=v, sql=f"SELECT {k} -- synthetic",
                        params={"cut": pu.iso(T0)}, unit=units.get(k, "count"))
              for k, v in vals.items()}
    warn_stream = {"window_minutes": 60.0, "warn_total": warn, "error_total": error,
                   "clusters": [], "paths_read": ["x.log"]}
    chronicle = {"ts": pu.iso(T0 + timedelta(hours=hours)), "age_minutes": 10.0, "stale": False,
                 "facets": {"s1_missions": {"queued": 6}}, "facets_present": ["s1_missions"]}
    return Pulse(as_of=pu.iso(T0 + timedelta(hours=hours)), organs=organs, chronicle=chronicle,
                 warn=warn_stream, blocked={"counts": {"blocked": blocked}})


# ─────────────────────────── 1) LLM 解析 ───────────────────────────


def test_extract_json_handles_fences_and_chatter():
    body = '{"findings":[{"organ":"credit_backlog","metric":"credit_backlog","opinion":"confirm"}],"rationale":"x"}'
    assert jd.extract_json(body)["rationale"] == "x"
    assert jd.extract_json("```json\n" + body + "\n```")["rationale"] == "x"
    assert jd.extract_json("好的，我的判读如下：\n" + body + "\n以上。")["rationale"] == "x"
    assert jd.extract_json('前缀 {"a":{"b":1}} 后缀')["a"]["b"] == 1
    assert jd.extract_json("这不是 JSON") is None
    assert jd.extract_json('{"a": 1') is None      # 不配平 → 不猜


def test_validate_verdict_drops_hallucinated_organs_and_metrics():
    obj = {"findings": [
        {"organ": "queue_latency", "metric": "queue_latency_p99", "opinion": "confirm", "severity": "high"},
        {"organ": "credit_backlog", "metric": "credit_backlog", "opinion": "veto", "severity": "mid"},
        {"organ": "s2_campaign", "metric": "credit_backlog", "opinion": "note"},   # 器官/指标错配
        {"organ": "", "metric": "oldest_queued_minutes", "opinion": "weird", "severity": "urgent"},
    ], "rationale": "r", "urgent": True}
    verdict, problems = jd.validate_verdict(obj)
    assert [f["metric"] for f in verdict["findings"]] == ["credit_backlog", "oldest_queued_minutes"]
    assert verdict["findings"][1]["opinion"] == "note"      # 非法 opinion 降级为 note
    assert verdict["findings"][1]["severity"] == "mid"      # 非法 severity 降级
    assert any(p.startswith("unknown-metric") for p in problems)
    assert any(p.startswith("organ-metric-mismatch") for p in problems)


def test_llm_judge_never_adds_filable_boundary():
    """LLM 报一个**未越界**的器官 → 只能成 advisory（不入单）。"""
    pulse = _pulse(credit=5, oldest=45)   # 健康
    llm = {"findings": [{"organ": "declaration_rate", "metric": "declaration_rate",
                         "opinion": "confirm", "severity": "high", "symptom": "声明率堪忧"},
                        {"organ": "s5_outcomes", "metric": "declaration_rate",   # 器官错配 → ungrounded
                         "opinion": "confirm", "severity": "high", "symptom": "错配"}],
           "rationale": "担心声明率", "urgent": True}
    diag = dg.build_diagnosis(pulse, dg.Policy(), llm=llm,
                              llm_provenance={"judge": "scripted", "parse_ok": True})
    assert diag.filable(dg.Policy()) == []
    advisories = [f for f in diag.findings if f.advisory]
    assert [f.metric for f in advisories] == ["declaration_rate"]
    proposals, _ = dg.decide_filings(diag, pulse, dg.Policy())
    assert proposals == []
    assert any("declaration_rate" in note for note in diag.provenance.get("ungrounded", []))


def test_llm_veto_blocks_filing_but_keeps_evidence():
    pulse = _pulse(credit=30)
    policy = dg.Policy(require_persistence=1)
    llm = {"findings": [{"organ": "credit_backlog", "metric": "credit_backlog",
                         "opinion": "veto", "severity": "low", "symptom": "批量补账中，瞬时"}],
           "rationale": "瞬时", "urgent": False}
    diag = dg.build_diagnosis(pulse, policy, llm=llm,
                              llm_provenance={"judge": "scripted", "parse_ok": True})
    proposals, _ = dg.decide_filings(diag, pulse, policy)
    assert proposals == []
    finding = [f for f in diag.findings if not f.advisory][0]
    assert finding.vetoed and "瞬时" in (finding.veto_reason or "")
    assert finding.to_dict()["evidence"]["sql"].startswith("SELECT credit_backlog")


def test_rule_judge_never_vetoes_and_reports_rule_provenance():
    pulse = _pulse(credit=30)
    res = jd.RuleJudge().judge(pulse, [{"organ": "credit_backlog", "metric": "credit_backlog",
                                        "value": 30, "threshold": 20, "severity": "mid",
                                        "symptom": "s", "zero_flow": True}])
    assert res.ok and res.judge == "rule" and res.model == "rule/1"
    assert res.verdict["findings"][0]["opinion"] == "confirm"


# ─────────────────────────── 2) 定量越界与严重度 ───────────────────────────


def test_rule_findings_severities_and_zero_flow_flags():
    findings = dg.rule_findings(_pulse(credit=60, oldest=300, decl=0.35), dg.Policy())
    by_metric = {f.metric: f for f in findings}
    assert by_metric["credit_backlog"].severity == "high"            # crit 50
    assert by_metric["credit_backlog"].zero_flow is True
    assert by_metric["oldest_queued_minutes"].severity == "high"     # crit 240
    assert by_metric["declaration_rate"].severity == "high"          # crit 0.40（lower_worse）
    assert by_metric["declaration_rate"].threshold == 0.40
    assert by_metric["credit_backlog"].signature == "credit_backlog:higher_worse"
    assert len(by_metric["credit_backlog"].fingerprint) == 16


def test_thresholds_injectable():
    policy = dg.Policy(thresholds_override={"credit_backlog": (5.0, 10.0)})
    findings = dg.rule_findings(_pulse(credit=12), policy)
    f = {x.metric: x for x in findings}["credit_backlog"]
    assert f.severity == "high" and f.threshold == 10.0


def test_undefined_metric_is_not_a_finding():
    pulse = _pulse()
    pulse.organs["declaration_rate"] = Metric(name="declaration_rate", value=None, sql="SELECT 1",
                                              unit="ratio", extra={"undefined_reason": "no-finished"})
    assert all(f.metric != "declaration_rate" for f in dg.rule_findings(pulse, dg.Policy()))


# ─────────────────────────── 3) 立单纪律 ───────────────────────────


def test_persistence_gate_blocks_single_beat_spike():
    pulse = _pulse(credit=30)
    policy = dg.Policy(require_persistence=2)
    diag = dg.build_diagnosis(pulse, policy, streaks={})       # 第一拍 → consecutive=1
    assert diag.filable(policy) == []
    diag2 = dg.build_diagnosis(pulse, policy, streaks={"credit_backlog:higher_worse": 1})
    assert [f.metric for f in diag2.filable(policy)] == ["credit_backlog"]
    proposals, _ = dg.decide_filings(diag2, pulse, policy)
    assert len(proposals) == 1


def test_one_filing_per_organ_dedupes_adoption_stall_metrics():
    pulse = _pulse(credit=5, oldest=300, stuck=20)   # 只让 adoption_stall 越界
    policy = dg.Policy(require_persistence=1)
    diag = dg.build_diagnosis(pulse, policy, streaks={"oldest_queued_minutes:higher_worse": 5,
                                                      "queued_stuck_2h:higher_worse": 5})
    filable = diag.filable(policy)
    assert {f.organ for f in filable} == {"adoption_stall"} and len(filable) >= 2
    proposals, suppressed = dg.decide_filings(diag, pulse, policy,
                                              last_filed_organ={})
    assert len(proposals) == 1 and proposals[0].organ == "adoption_stall"
    assert any(s["reason"] == "organ-taken-this-beat" for s in suppressed)


def test_cooldown_and_organ_cooldown():
    pulse = _pulse(credit=30)
    policy = dg.Policy(require_persistence=1, cooldown_hours=6)
    diag = dg.build_diagnosis(pulse, policy, streaks={"credit_backlog:higher_worse": 3})
    sig = "credit_backlog:higher_worse"
    recent = {"credit_backlog": pu.iso(T0 + timedelta(hours=11))}   # 1 小时前刚立过
    proposals, suppressed = dg.decide_filings(diag, pulse, policy, last_filed_organ=recent)
    assert proposals == [] and suppressed[0]["reason"] == "organ-cooldown"
    old = {"credit_backlog": pu.iso(T0 + timedelta(hours=1))}       # 11 小时前 → 冷却已过
    proposals, _ = dg.decide_filings(diag, pulse, policy, last_filed_organ=old)
    assert len(proposals) == 1


def test_open_cap_and_beat_cap():
    pulse = _pulse(credit=60, oldest=300, stuck=20, decl=0.3, error=20, warn=40,
                   blocked=20, deltas=0, await_pr=50, queued=80, pending=100, running=0)
    policy = dg.Policy(require_persistence=1, max_filings_per_beat=2, max_open_diagnosis=10)
    diag = dg.build_diagnosis(pulse, policy, streaks={k: 3 for k in (
        "credit_backlog:higher_worse", "oldest_queued_minutes:higher_worse",
        "declaration_rate:lower_worse", "error_total:higher_worse",
        "campaign_blocked:higher_worse", "capability_deltas_24h:lower_worse")})
    proposals, suppressed = dg.decide_filings(diag, pulse, policy)
    assert len(proposals) == 2
    assert any(s["reason"] == "beat-cap" for s in suppressed)

    policy2 = dg.Policy(require_persistence=1, max_open_diagnosis=1)
    proposals2, suppressed2 = dg.decide_filings(diag, pulse, policy2)
    assert len(proposals2) == 1
    assert any(s["reason"] == "open-cap" for s in suppressed2)


def test_goal_embeds_reproducible_evidence():
    pulse = _pulse(credit=30)
    policy = dg.Policy(require_persistence=1)
    diag = dg.build_diagnosis(pulse, policy, streaks={"credit_backlog:higher_worse": 3})
    proposals, _ = dg.decide_filings(diag, pulse, policy)
    goal = proposals[0].goal
    assert "shadowdiag-" in goal and "credit_backlog" in goal
    assert "SELECT credit_backlog" in goal            # SQL 原文
    assert '"cut"' in goal                            # 参数
    assert "只读" in goal and "不得改动本影子模块的只读边界" in goal
    assert proposals[0].repo == "TensorPlatform"


# ─────────────────────────── 4) 立单通道 ───────────────────────────


DDL = """
CREATE TABLE tensor_dev_missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL,
    goal TEXT NOT NULL, repo TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
    runner TEXT NOT NULL DEFAULT 'native', origin TEXT NOT NULL DEFAULT 'owner',
    model TEXT, task_key TEXT, budget TEXT, report_owed INTEGER DEFAULT 0,
    claimed_by TEXT, session_id INTEGER, result TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '2026-11-06T00:00:00+00:00', finished_at TEXT,
    report_id INTEGER);
CREATE UNIQUE INDEX ux_task_key ON tensor_dev_missions (organization_id, task_key);
"""


def _proposal(task_key="shadowdiag-abc123", organ="credit_backlog", metric="credit_backlog"):
    return dg.FilingProposal(task_key=task_key, fingerprint="abc123", signature=f"{metric}:higher_worse",
                             organ=organ, metric=metric, severity="mid", value=30, threshold=20,
                             consecutive=3, goal="g")


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(DDL)
    c.execute("INSERT INTO tensor_dev_missions (organization_id, goal, repo, task_key) "
              "VALUES (1,'boot','TensorPlatform','coldboot-x')")
    c.commit()
    return c


def test_sqlite_intake_files_once_then_dedupes(conn):
    intake = fl.SqliteIntake(conn)
    first = intake.file(_proposal(), 1)
    second = intake.file(_proposal(), 1)
    assert first.status == "filed" and first.mission_id
    assert second.status == "duplicate"
    rows = conn.execute("SELECT count(*) FROM tensor_dev_missions WHERE task_key='shadowdiag-abc123'").fetchone()
    assert rows[0] == 1


def test_insert_statement_matches_production_schema_constraints():
    """立单语句必须满足生产库 CHECK 约束：origin ∈ {owner, agent}、status ∈ 枚举。

    实跑教训：首版写 ``origin='mechanism'`` 被 ``tensor_dev_missions_origin_check`` 拒；
    这类约束只有连真库才看得见，所以固化成断言防止回归。
    """
    sql = fl.INSERT_MISSION_SQL
    assert "'agent'" in sql and "'mechanism'" not in sql
    assert "'queued'" in sql and "'native'" in sql
    for column in ("organization_id", "goal", "repo", "runner", "origin", "task_key",
                   "status", "report_owed", "budget"):
        assert column in sql


def test_write_guard_rejects_any_other_write(conn):
    guard = fl.WriteGuard(fl.SqliteIntake(conn))
    with pytest.raises(fl.BoundaryViolation):
        guard.write("UPDATE tensor_dev_missions SET status='done'", {})
    with pytest.raises(fl.BoundaryViolation):
        guard.write("INSERT INTO tensor_credit_ledger (id) VALUES (1)", {})
    with pytest.raises(fl.BoundaryViolation):
        guard.write(fl.INSERT_MISSION_SQL.format(budget=":budget") + "; DELETE FROM tensor_dev_missions", {})


def test_open_task_keys_and_org_id(conn):
    fl.SqliteIntake(conn).file(_proposal(), 1)
    keys = fl.open_task_keys(lambda sql, params: conn.execute(sql, params).fetchall())
    assert keys == ["shadowdiag-abc123"]
    assert fl.latest_org_id(lambda sql, params: conn.execute(sql, params).fetchall()) == 1


def test_infer_disposition_buckets():
    now = T0 + timedelta(hours=1)
    assert fl.infer_disposition({"status": "succeeded", "result": {"diagnosis_disposition": "fixed"}}) == "confirmed"
    assert fl.infer_disposition({"status": "succeeded", "result": {"diagnosis_disposition": "not_a_defect"}}) == "false_positive"
    assert fl.infer_disposition({"status": "succeeded", "result": {"honest_fail": True}}) == "inconclusive"
    assert fl.infer_disposition({"status": "queued", "created_at": pu.iso(T0 - timedelta(hours=60))}, now=now) == "unclaimed_expired"
    assert fl.infer_disposition({"status": "claimed", "created_at": pu.iso(T0 - timedelta(hours=60))}, now=now) == "inconclusive"
    assert fl.infer_disposition({"status": "queued", "created_at": pu.iso(T0 - timedelta(minutes=30))}, now=now) == "pending"