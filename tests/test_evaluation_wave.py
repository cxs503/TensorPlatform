"""评价波执行器判据单测（零领域语义：夹具用合成仓名/合成贡献者/合成分子式数字）。

覆盖三类判据：
  结构类 —— 批次上限、派单顺序、席位独立性、通道族两两不同；
  判级类 —— S2 须对照数字、S3 须账本登记、S4/S5 须封顶 candidate、E0 不可登记；
  审计类 —— 理由长度、引用可核、抽查重放稳定、统计可复算、引导回路无加权。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import tools.evaluation_wave as w  # noqa: E402


# ───────────────────────────── 夹具（纯合成） ─────────────────────────────

REPORT_MD = """## 结果

对照基线 100 vs 95（相对偏差 -5.0%），复跑 3 次一致。
执行：pytest -q tests/test_alpha.py -> 12 passed
工件：artifacts/alpha_result.json

## 方法

合成夹具：只描述结构（报告正文长度、图表数量、命令与通过计数、对照数字段），
不含任何学科词，目的是让「可复现工件」的口径（报告 ≥200 字符 + 真跑命令 + 引用路径）
在测试里可判定，而不是靠人工目测。
"""


def mission(mid: int = 1, *, repo: str = "SyntheticRepo", contributor: str = "worker-a",
            model: str = "familyalpha-1") -> dict:
    return {
        "id": mid,
        "repo": repo,
        "claimed_by": contributor,
        "model": model,
        "finished_at": "2026-01-01T00:00:00+00:00",
        "goal": "synthetic goal",
        "result": {"checks": "pytest -q tests/test_alpha.py -> 12 passed; ruff check . -> ok"},
    }


def report(md: str = REPORT_MD, rid: int = 7) -> dict:
    return {"id": rid, "markdown": md, "figures": [{"name": "f1"}]}


def packet(mid: int = 1, *, repo: str = "SyntheticRepo", md: str = REPORT_MD,
           delta: dict | None = None, with_report: bool = True) -> w.EvidencePacket:
    return w.build_packet(
        mission(mid, repo=repo),
        report=report(md) if with_report else None,
        delta=delta,
    )


# ───────────────────────────── 1. 结构类 ─────────────────────────────


def test_batch_cap_enforced_and_lossless():
    missions = [{"id": i, "repo": "SyntheticRepo"} for i in range(1, 131)]
    batches = w.plan_batches(missions, batch_size=60)
    assert [b["size"] for b in batches] == [60, 60, 10]
    assert all(b["size"] <= w.MAX_BATCH for b in batches)
    flat = [mid for b in batches for mid in b["mission_ids"]]
    assert sorted(flat) == sorted(m["id"] for m in missions)  # 不丢不重

    clamped = w.plan_batches(missions, batch_size=10_000)
    assert max(b["size"] for b in clamped) == w.MAX_BATCH


def test_batch_size_zero_rejected():
    with pytest.raises(ValueError):
        w.plan_batches([{"id": 1, "repo": "R"}], batch_size=0)


def test_priority_repos_change_dispatch_order():
    missions = [{"id": 1, "repo": "RepoB"}, {"id": 2, "repo": "RepoA"}]
    plain = w.plan_batches(missions, batch_size=10)
    prio = w.plan_batches(missions, batch_size=10, priority_repos=["RepoA"])
    assert plain[0]["mission_ids"] == [1, 2]
    assert prio[0]["mission_ids"] == [2, 1]          # 引导回路真的改了顺序
    assert prio[0]["priority_repo_hits"] == 1


def test_seats_are_independent_and_channel_families_distinct():
    m = mission(3)
    a = w.assign_seats(m, n_seats=3)
    assert a.contributor_seat not in a.seat_ids
    assert len(set(a.seat_families)) == len(a.seat_families)  # 通道族两两不同
    assert a.families_distinct is True
    assert a.cross_model_verified is False  # 程序化席位不得自称异构模型复核
    # 确定性：同 mission 同 salt 恒定
    assert w.assign_seats(m, n_seats=3).seat_ids == a.seat_ids


def test_cross_model_verified_only_with_model_backed_seats():
    pool = (
        w.Seat("seat-alpha", "report_delivery", "delivery", "model_backed", "familybeta"),
        w.Seat("seat-beta", "execution_checks", "execution", "model_backed", "familygamma"),
    )
    a = w.assign_seats(mission(4, model="familyalpha-9"), pool=pool, n_seats=2)
    assert a.cross_model_verified is True
    assert a.contributor_family == "familyalpha"


# ───────────────────────────── 2. 判级类 ─────────────────────────────

def _facts(md: str, delta: dict | None = None) -> w.Facts:
    return w.extract_facts(packet(md=md, delta=delta))


def test_s2_requires_baseline_number_pair():
    only_one = "基线段落说明：见 7 号附录与归档记录。"          # 标记有数字但没有"对"
    facts = _facts(only_one)
    seat = w.SEAT_BY_ID["seat-delivery"]
    assert len(facts.baseline_comparisons) == 0
    assert w.assess(packet(md=only_one), seat, facts).grade in ("S1", "S2")
    # 真正的对照数字对 → S2
    two = "对照基线 100 vs 95，相对偏差 -5.0%"
    f2 = _facts(two)
    assert len(f2.baseline_comparisons) >= 1
    assert w.assess(packet(md=two), seat, f2).grade == "S2"


def test_s3_requires_registered_delta_with_artifacts():
    seat = w.SEAT_BY_ID["seat-ledger"]
    flat = {"category": "no_substantive_delta", "artifacts": []}
    f = _facts("执行：pytest -q tests/test_alpha.py -> 12 passed", delta=flat)
    assert f.delta_registered is False
    assert w.assess(packet(md="执行：pytest -q tests/test_alpha.py -> 12 passed", delta=flat),
                    seat, f).grade != "S3"

    registered = {"category": "new_capability", "artifacts": ["artifacts/alpha_result.json"]}
    f2 = _facts("新能力落地：artifacts/alpha_result.json", delta=registered)
    assert f2.delta_registered is True
    assert w.assess(packet(md="新能力落地：artifacts/alpha_result.json", delta=registered),
                    seat, f2).grade == "S3"


def test_s4_s5_claims_are_capped_to_candidate():
    md = REPORT_MD + "\n首次实现该机制（突破声明），对照基线 100 vs 95，相对偏差 -5.0%。\n"
    row = w.evaluate_mission(packet(md=md), salt="t")
    assert row.record["grade"] in ("S4", "S5")
    assert row.record["status"] == "candidate"
    assert "MACHINE_SEAT_CAP" in row.record["reason_codes"]

    scope = "查新：新颖性检索范围覆盖三项库，检索范围与关键词见附录。"
    row2 = w.evaluate_mission(packet(md=scope), salt="t")
    assert row2.record["grade"] == "S5" and row2.record["status"] == "candidate"


def test_no_evidence_is_not_registrable():
    pkt = w.build_packet({"id": 9, "repo": "SyntheticRepo", "claimed_by": "worker-a",
                          "model": "", "finished_at": "2026-01-01T00:00:00+00:00",
                          "goal": "", "result": {}})
    row = w.evaluate_mission(pkt, salt="t")
    assert row.facts.any_evidence is False
    assert row.record["status"] == "rejected"      # E0 不可登记（103 判据兜底）
    assert "E0_NOT_REGISTRABLE" in row.record["reason_codes"]


def test_e_level_requires_reproducible_artifact():
    ok_md = REPORT_MD
    row = w.evaluate_mission(packet(md=ok_md), salt="t")
    assert row.record["e_level"] in ("E2", "E3")
    doc_only = "## 说明\n只有文字描述，没有命令、没有工件、没有数字。\n" * 6
    row2 = w.evaluate_mission(packet(md=doc_only), salt="t")
    assert row2.record["e_level"] == "E1"
    assert "REPORT_ONLY" in " ".join(row2.consensus.flags)


# ───────────────────────────── 3. 审计类 ─────────────────────────────

def test_reason_length_and_citation_are_verifiable():
    row = w.evaluate_mission(packet(md=REPORT_MD), salt="t")
    reason = row.record["grade_reason"]
    assert len("".join(reason.split())) >= w.REASON_MIN_CHARS
    assert row.citation["ok"] is True
    assert row.citation["number_hits"] > 0
    assert row.citation["number_misses"] == []


def test_citation_check_catches_planted_number():
    pkt = packet(md=REPORT_MD)
    facts = w.extract_facts(pkt)
    reason = w.compose_reason(
        pkt, facts, w.reach_consensus([w.assess(pkt, w.SEAT_BY_ID["seat-delivery"], facts)]),
        w.assign_seats(mission(1)),
    )
    tampered = reason + "额外申报 99999 次独立复现。"
    check = w.verify_citations(tampered, pkt, facts)
    assert check["ok"] is False
    assert "99999" in check["number_misses"]


def test_disagreement_downgrades_to_candidate():
    p1 = w.Proposal("S2", "E2", "seat-a", "delivery")
    p2 = w.Proposal("S1", "E1", "seat-b", "execution")
    p3 = w.Proposal("S3", "E3", "seat-c", "ledger")
    cons = w.reach_consensus([p1, p2, p3])
    assert cons.confirmable is False
    assert "SEAT_DISAGREEMENT" in cons.flags

    same = w.reach_consensus([w.Proposal("S2", "E2", "a", "delivery"),
                              w.Proposal("S2", "E1", "b", "execution"),
                              w.Proposal("S2", "E2", "c", "ledger")])
    assert same.confirmable is True and same.e_level == "E1"   # 证据级取最小（保守）
    assert "E_LEVEL_SPREAD" in same.flags


def test_spot_check_regrade_is_stable_and_md5_matches(tmp_path):
    pkt = packet(md=REPORT_MD)
    row = w.evaluate_mission(pkt, salt="t")
    spot = w.spot_check([row], {row.mission_id: pkt}, k=1)
    assert spot["checked"] == 1
    assert spot["rows"][0]["regrade_stable"] is True
    assert spot["rows"][0]["report_md5_match"] is True
    assert spot["rows"][0]["citation_ok"] is True


def test_artifact_probe_semantics(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "present.json").write_text("{}", encoding="utf-8")
    got = w.probe_artifacts(
        ["artifacts/present.json", "artifacts/absent.json"],
        root=tmp_path, local_repo="SyntheticRepo", repo="SyntheticRepo",
    )
    assert got["artifacts/present.json"] == "exists"
    assert got["artifacts/absent.json"] == "not_found_local"
    other = w.probe_artifacts(
        ["artifacts/present.json"], root=tmp_path, local_repo="SyntheticRepo", repo="OtherRepo"
    )
    assert other["artifacts/present.json"] == "unverified"      # 别仓不下结论


def test_citation_not_broken_by_truncated_number():
    md = "对照基线 100 vs 95，相对偏差 0.03%（三次复跑一致，见 artifacts/alpha_result.json）"
    row = w.evaluate_mission(packet(md=md), salt="t")
    assert row.citation["ok"] is True, row.citation


def test_reason_length_distribution_and_coverage_and_independence():
    rows = [w.evaluate_mission(packet(md=REPORT_MD, mid=i), salt="t") for i in (1, 2)]
    dist = w.reason_length_distribution([r.record["grade_reason"] for r in rows])
    assert dist["n"] == 2 and dist["min"] >= w.REASON_MIN_CHARS
    assert dist["buckets"]["50-79"] + dist["buckets"]["80-119"] + \
        dist["buckets"]["120-199"] + dist["buckets"]["200+"] == 2

    cov = w.coverage_stats([1, 2, 3], rows)
    assert cov["coverage"] == pytest.approx(2 / 3, abs=1e-4)
    assert cov["undecided_count"] == 1

    ind = w.independence_stats(rows)
    assert ind["seat_independent_share"] == 1.0
    assert ind["cross_model_verified_share"] == 0.0   # 如实：程序化席位


# ───────────────────────── 4. 引导回路（无加权、可量） ─────────────────────────

def test_guidance_policy_is_lexicographic_not_weighted():
    rows = [
        w.evaluate_mission(packet(md="只有执行：pytest -q tests/test_alpha.py -> 3 passed",
                                  mid=i), salt="t")
        for i in (11, 12, 13)
    ]
    g = w.guidance_policy(rows)
    agents = [x for x in g if x.subject_kind == "agent"]
    assert agents and agents[0].tier == "probe"           # 全是 S1 → probe
    assert agents[0].admission == "evidence_required"
    assert agents[0].budget_multiplier == pytest.approx(0.5)
    payload = json.dumps([x.as_dict() for x in g], ensure_ascii=False)
    assert "score" not in payload                         # 禁加权分
    allowed, reasons = w.admission_decision(g, "worker-a")
    assert allowed is False and any("ADMISSION" in r for r in reasons)
    assert w.admission_decision(g, "worker-unknown") == (True, [])


def test_guidance_effects_are_quantified():
    rows = [
        w.evaluate_mission(packet(md="只有执行：pytest -q tests/test_alpha.py -> 3 passed",
                                  mid=i, repo="RepoX"), salt="t")
        for i in range(21, 31)
    ]
    g = w.guidance_policy(rows)
    eff = w.guidance_effects(
        g, rows, [mission(i, repo="RepoX") for i in range(21, 31)], base_seats=3
    )
    assert eff["priority_repos"] == ["RepoX"]             # S1 占比 100% → 加审
    assert eff["missions_reordered"] == 10
    assert eff["extra_seat_missions"] == 10
    assert eff["seat_budget"] == {"base": 3, "priority": 4}
    assert eff["budget_multiplier_delta_total"] == pytest.approx(-5.0)
    assert eff["gated_agent_count"] >= 1


def test_judge_reports_acceptance_criteria():
    rows = [w.evaluate_mission(packet(md=REPORT_MD, mid=i), salt="t") for i in range(1, 13)]
    metrics = w.wave_metrics(list(range(1, 13)), rows)
    report = {
        "metrics": metrics,
        "spot_check": w.spot_check(rows, {r.mission_id: packet(md=REPORT_MD, mid=r.mission_id)
                                          for r in rows}, k=10),
    }
    checks = w.judge(report, pytest_passed=15)
    assert checks["coverage_ge_0.80"]["pass"] is True
    assert checks["independent_share_ge_0.90"]["pass"] is True
    assert checks["reason_length_report_present"]["pass"] is True
    assert checks["spotcheck_10_ok"]["pass"] is True
    assert checks["pytest_ge_8"]["pass"] is True
    assert checks["cross_model_verified_present"]["pass"] is False  # honest FAIL 分项