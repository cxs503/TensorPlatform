"""Knowledge Factory 试点调度器：门与契约的单元测试（合成语料，不碰 DB，不含领域词）。"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))

from knowledge_factory import contract as kc      # noqa: E402
from knowledge_factory import dashboard, discover, gates  # noqa: E402

DOC = ("# alpha 记录\n\n"
       "本轮把 beta 面板的网格加密到 512，残差从 3.1e-4 降到 8.0e-6（下降 38 倍）。\n\n"
       "## 参数表\n\n"
       "| 量 | 值 |\n|---|---|\n| 步长 | 0.001 |\n| 迭代 | 4000 |\n")
QUOTE = "残差从 3.1e-4 降到 8.0e-6"


@pytest.fixture(scope="module")
def cfg_pair():
    cfg = kc.load_config()
    return cfg, kc.load_contracts(cfg)


def build(cfg, contracts, report_id=7, task_type="01", lane="C"):
    report = {"id": report_id, "title": "alpha", "scenario_key": "demo", "markdown": DOC}
    return kc.build_package(report, task_type, lane, cfg, contracts)


def payload(pkg, quote=QUOTE, claims=None):
    return {
        "package_id": pkg["package_id"], "task_type": pkg["task_type"],
        "report_id": pkg["input"]["report_id"],
        "provenance": {"source_hash": gates.sha256_text(DOC), "source_field": "markdown",
                       "producer": "unit-test"},
        "outputs": {"asset_manifest.json": {"ok": True}},
        "claims": claims if claims is not None else [{
            "claim_id": f"c-{pkg['input']['report_id']}-1", "text": quote, "status": "supported",
            "evidence": [{"quote": quote, "source_field": "markdown"}]}],
    }


def run_gates(pkg, obj, seen=None, expected=None):
    sg, sok = gates.structure_gate(obj, gates.load_schema(), pkg)
    want = expected or pkg.get("report_id") or (pkg.get("input") or {}).get("report_id")
    eg, eok, stats = gates.evidence_gate(obj, DOC, seen or set(), 12,
                                         expected_report_id=want)
    return gates.summarize(sg + eg, sok and eok), stats


# ---------------- 契约 ----------------
def test_package_id_and_hash(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    assert pkg["package_id"] == "wp-7-01"
    assert pkg["input"]["source_hash"] == "sha256:" + hashlib.sha256(DOC.encode()).hexdigest()
    assert pkg["task_type"] == "01-asset-registration"
    for key in ("objective", "constraints", "output", "acceptance", "review", "delivery"):
        assert key in pkg


def test_goal_injects_full_yaml_and_channel(cfg_pair):
    cfg, contracts = cfg_pair
    goal = kc.render_goal(build(cfg, contracts), cfg)
    assert "package_id: wp-7-01" in goal          # 契约 YAML 全文注入
    assert "kf-wp:wp-7-01" in goal                # 结果信道
    assert "原文获取" in goal and "tensor_reports where id=7" in goal
    assert "不得读/写/覆盖其它 report_id" in goal  # 互不覆盖


def test_03_gated_pending_spec(cfg_pair):
    cfg, contracts = cfg_pair
    assert "03" not in kc.enabled_task_types(contracts)
    assert contracts["03"]["status"] == "pending_spec"


# ---------------- 结构门 ----------------
def test_structure_gate_control_pass(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    summary, _ = run_gates(pkg, payload(pkg))
    assert summary["ok"], summary


def test_structure_gate_rejects_wrong_report(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg)
    obj["report_id"] = "8"          # 越界碰别人的报告
    summary, _ = run_gates(pkg, obj)
    assert not summary["ok"] and "g3_identity" in summary["failed"]


def test_structure_gate_rejects_missing_channel(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj, err = gates.extract_payload("没有代码块的正文")
    assert obj is None and err == "no_json_block"


def test_extract_payload_needs_exactly_one_block():
    obj, err = gates.extract_payload('```json\n{"a":1}\n```\n```json\n{"b":2}\n```')
    assert obj is None and err.startswith("multiple_json_blocks")


# ---------------- 证据门 ----------------
def test_evidence_gate_hash_mismatch(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg)
    obj["provenance"]["source_hash"] = "sha256:" + "0" * 64
    summary, _ = run_gates(pkg, obj)
    assert not summary["ok"] and "g4_source_hash_matches" in summary["failed"]


def test_evidence_gate_fake_quote(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    summary, stats = run_gates(pkg, payload(pkg, quote="原文里没有这句话呀"))
    assert not summary["ok"] and "g5_source_locator_resolvable" in summary["failed"]
    assert stats["locator_rate"] == 0.0


def test_evidence_gate_short_quote_rejected(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    summary, _ = run_gates(pkg, payload(pkg, quote="残差"))
    assert "g5_source_locator_resolvable" in summary["failed"]


def test_evidence_gate_duplicate_claim_id(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg)
    obj["claims"].append(dict(obj["claims"][0]))
    summary, _ = run_gates(pkg, obj)
    assert "g6_no_duplicate_ids" in summary["failed"]


def test_evidence_gate_cross_package_duplicate(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    summary, _ = run_gates(pkg, payload(pkg), seen={"c-7-1"})
    assert "g6_no_duplicate_ids" in summary["failed"]


def test_evidence_gate_claim_without_evidence(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg, claims=[{"claim_id": "c-7-9", "text": "无证据", "status": "supported"}])
    summary, _ = run_gates(pkg, obj)
    assert "g5b_all_claims_located_or_unresolved" in summary["failed"]


def test_zero_claims_is_legal_and_rate_is_null(cfg_pair):
    """零 claim 是合法完整结果；定位率必须记 None 而不是 100%。"""
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    summary, stats = run_gates(pkg, payload(pkg, claims=[]))
    assert summary["ok"], summary
    assert stats["locator_rate"] is None


def test_unresolved_with_reason_is_legal(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg, claims=[{"claim_id": "c-7-3", "text": "原文未述", "status": "unresolved",
                                "unresolved_reason": "报告中未给出该量与分辨率的关系"}])
    summary, _ = run_gates(pkg, obj)
    assert summary["ok"], summary


def test_claim_namespace_guard(cfg_pair):
    cfg, contracts = cfg_pair
    pkg = build(cfg, contracts)
    obj = payload(pkg, claims=[{"claim_id": "c-8-1", "text": "x", "status": "supported",
                                "evidence": [{"quote": QUOTE}]}])
    summary, _ = run_gates(pkg, obj)
    assert "g7_claim_namespace" in summary["failed"]


def test_review_gate_independence():
    pkg = {"package_id": "wp-7-01"}
    obj = {"outputs": {"review_report.json": {"verdict": "pass", "reviewed_package_id": "wp-7-01"}}}
    ok_gates, ok, verdict = gates.review_gate(obj, pkg, producer_mission_id=11,
                                              reviewer_mission_id=12)
    assert ok and verdict == "pass"
    _, bad, _ = gates.review_gate(obj, pkg, producer_mission_id=11, reviewer_mission_id=11)
    assert not bad, "同一 mission 自审必须被拦"
    obj2 = {"outputs": {"review_report.json": {"verdict": "needs-fix",
                                              "reviewed_package_id": "wp-8-01"}}}
    _, bad2, _ = gates.review_gate(obj2, pkg, 11, 12)
    assert not bad2, "审错包必须被拦"


# ---------------- Discover / 看板 ----------------
class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def q(self, sql, params=()):
        self.calls.append(sql)
        return self.rows


def test_len_bucket_and_lane_split():
    assert discover.len_bucket(100) == "s"
    assert discover.len_bucket(5000) == "m"
    assert discover.len_bucket(99999) == "l"
    from knowledge_factory.scheduler import Scheduler
    sch = Scheduler.__new__(Scheduler)
    sch.cfg = {"pilot": {"lane_split": {"C": 0.8, "B": 0.15, "A": 0.05}}}
    lanes = [sch.lane_for(i) for i in range(100)]
    assert lanes.count("C") == 80 and lanes.count("B") == 15 and lanes.count("A") == 5


def test_prestratify_round_robin_covers_strata():
    rows = [[1, "a", 9000, '["repoX"]'], [2, "a", 3000, '["repoX"]'],
            [3, "b", 9000, '["repoY"]'], [4, "b", 1200, '["repoY"]']]
    picked, extra = discover.prestratify(FakeStore(rows), batch_size=4, min_chars=200)
    assert len(picked) == 4
    assert len({p["stratum"] for p in picked}) == 4   # repo×scenario×长度 四层全覆盖


def test_dashboard_renders_all_eight_metrics():
    m = {"pending_reports": 3, "dispatched_tasks": 2, "processed_ok": 1,
         "failed_and_rework": 1, "review_pass_rate": 0.5, "locator_success_rate": 0.75,
         "avg_cost_per_report": {"tokens": 100.0, "compute_seconds": 5.0, "metered_tokens": True},
         "knowledge_reuse": 0}
    line = dashboard.render(m)
    assert len(dashboard.METRIC_TITLES) == 8, "看板必须覆盖 owner 八指标"
    for _, title in dashboard.METRIC_TITLES:
        assert title in line
    assert "50.0%" in line and "75.0%" in line