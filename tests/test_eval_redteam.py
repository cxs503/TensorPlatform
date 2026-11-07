"""评价真实性红队抽样复算（IN24-E5）验收测试。

判据（先于实现，机器可裁）：
1. **分层覆盖**：抽样 ≥10 单，总体中每个 (S,E) 层至少 1 单被抽中；确定性（同 seed 同结果）。
2. **盲序**：复算输入结构上不含 grade/e_level/grade_reason/reviewer；输出顺序 = 盲序，
   与输入顺序无关（打乱输入不改变盲序号对应的单）。
3. **独立重算**：证据级 E0/E1/E2 由事实决定；等级天花板由类别 ⊕ 等级门槛 ⊕
   独立评审/基线/范围决定（S3 需 E≥2、S4 需独立评审、S5 需 E≥3、S-N 需 E≥2+独立复核）。
4. **冒升级必拒**：申报超过可证天花板（如 S3 而 E1 / 无增量）→ REJECT_UPGRADE；断链证据 →
   REJECT_BROKEN_EVIDENCE。
5. **冒报必降**：申报 E > 重算 E → DOWNGRADE_EVIDENCE；grade_reason 数字/缺失声明与事实
   不符 → DOWNGRADE_REASON（降一级，下限 E1，永不升）。
6. **申报偏低只记录**：UNDERCLAIM 不改变任何等级（红队不追认）。
7. **落库幂等 + 回写生效**：复算记录按 (evaluation_id, run_id) 唯一；reject → status=rejected，
   downgrade → 等级下降；已非 confirmed 的行不被二次覆盖。含 0 发现（MATCH）也留档。
8. **规则漂移可检**：独立映射与回填器映射不一致时 `rule_drift()` 报出差异。

夹具一律合成主体/合成类别（无任何学科或业务词）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools import eval_redteam as rt  # noqa: E402

REASON_OK = (
    "规则化扫描（结构判据）：mission 9 于 2026-10-01T00:00:00+00:00 完结，贡献者 worker-a；"
    "报告 已发布 #101（正文 1200 字符，图 1 张），能力增量类别 new_capability（工件 0 项）；"
    "据此判 E2（双证据）、S3（类别→等级映射）。"
)


def facts(**kw) -> rt.MissionFacts:
    base = dict(
        mission_id=9,
        mission_status="succeeded",
        finished_at="2026-10-01T00:00:00+00:00",
        contributor="worker-a",
        repo="SyntheticRepo",
        reports=(rt.ReportFacts(report_id=101, status="published", chars=1200, figures=1),),
        delta_categories=("new_capability",),
        delta_artifacts=0,
        reviewer_kind="auto_scan",
        baseline_declared=False,
    )
    base.update(kw)
    return rt.MissionFacts(**base)


def record(**kw) -> rt.Record:
    base = dict(
        evaluation_id="eval-1",
        artifact_id="mission:9",
        mission_id=9,
        contributor="worker-a",
        grade="S3",
        e_level="E2",
        grade_reason=REASON_OK,
        evidence_refs=(
            {"kind": "api", "ref": "/api/v1/tensor/missions/9/provenance"},
            {"kind": "report", "ref": "101"},
            {"kind": "artifact", "ref": "tensor_capability_deltas:mission=9"},
        ),
        baseline_or_scope="执行性成果：交付物与判据对应（无对照基线需求）",
        boundary="扫描边界：仅平台自有记录",
        reviewer="platform-evidence-scan",
        reviewer_kind="auto_scan",
        repo="SyntheticRepo",
    )
    base.update(kw)
    return rt.Record(**base)


def make_population(n_by_stratum: dict[str, int]) -> list[rt.Record]:
    out: list[rt.Record] = []
    i = 0
    for stratum, n in n_by_stratum.items():
        grade, e = stratum.split("/")
        for _ in range(n):
            i += 1
            out.append(record(evaluation_id=f"eval-{i:03d}", artifact_id=f"mission:{i}",
                              mission_id=i, grade=grade, e_level=e))
    return out


# ──────────────────────────── 1. 分层抽样 ────────────────────────────

def test_stratified_sample_covers_every_stratum_with_min_size():
    pop = make_population({"S1/E1": 3, "S1/E2": 27, "S3/E2": 2, "S2/E1": 4})
    picked = rt.stratified_sample(pop, size=12, seed="s")
    assert len(picked) == 12
    assert {r.stratum for r in picked} == {"S1/E1", "S1/E2", "S3/E2", "S2/E1"}
    # 小层被全取（容量 3 / 2 / 4 均可 >=1）
    assert sum(1 for r in picked if r.stratum == "S1/E1") >= 1


def test_stratified_sample_is_deterministic_and_order_invariant():
    pop = make_population({"S1/E1": 5, "S1/E2": 20, "S3/E2": 1})
    a = rt.stratified_sample(pop, size=11, seed="seed-x")
    b = rt.stratified_sample(list(reversed(pop)), size=11, seed="seed-x")
    assert [r.evaluation_id for r in a] == [r.evaluation_id for r in b]
    c = rt.stratified_sample(pop, size=11, seed="seed-y")
    assert [r.evaluation_id for r in a] != [r.evaluation_id for r in c] or len(a) == len(pop)


def test_stratified_sample_never_exceeds_capacity():
    pop = make_population({"S1/E1": 2, "S1/E2": 3})
    picked = rt.stratified_sample(pop, size=50, seed="s")
    assert len(picked) == 5
    assert len({r.evaluation_id for r in picked}) == 5


# ──────────────────────────── 2. 盲序 ────────────────────────────

def test_blind_payload_structurally_excludes_declared_grade():
    payload = rt.blind_payload(facts())
    for key in ("grade", "e_level", "grade_reason", "reviewer", "reason_codes"):
        assert key not in payload
    assert payload["mission_id"] == 9 and payload["delta_categories"] == ["new_capability"]


def test_blind_order_is_assigned_after_recompute_and_is_stable():
    pop = make_population({"S1/E1": 4, "S1/E2": 6})
    picked = rt.stratified_sample(pop, size=10, seed="s")
    rechecks = rt.assign_blind_order(rt.judge(r, facts(mission_id=r.mission_id,
                                                       contributor=r.contributor)) for r in picked)
    assert [r.blind_order for r in rechecks] == list(range(1, 11))
    assert [r.evaluation_id for r in rechecks] == [r.evaluation_id for r in picked]


# ──────────────────────────── 3. 独立重算 ────────────────────────────

def test_recompute_evidence_level_three_cases():
    assert rt.recompute_evidence_level(facts())[0] == "E2"
    assert rt.recompute_evidence_level(facts(delta_categories=()))[0] == "E1"
    assert rt.recompute_evidence_level(facts(reports=(), delta_categories=()))[0] == "E0"
    # 草稿报告不算「已发布」证据
    draft = facts(reports=(rt.ReportFacts(1, "draft", 100, 0),))
    level, codes = rt.recompute_evidence_level(draft)
    assert level == "E1" and "REPORT_NOT_PUBLISHED" in codes


@pytest.mark.parametrize("e_level,category,independent,baseline,scope,expected", [
    ("E0", "new_capability", True, True, True, None),
    ("E1", "new_capability", True, True, True, "S2"),      # S3 需 E≥2 → 落 S2
    ("E2", "new_capability", False, True, True, "S3"),
    ("E2", "capability_extension", False, False, False, "S1"),  # S2 需基线
    ("E2", "capability_extension", False, True, False, "S2"),
    ("E2", "capability_extension", True, True, False, "S2"),    # 类别目标 S2 封顶（S4/S5 无类别支撑）
    ("E2", "new_capability", True, True, True, "S3"),           # 类别目标 S3 封顶
    ("E2", "falsified_hypothesis", False, True, False, None),    # S-N 需独立复核
    ("E2", "falsified_hypothesis", True, True, False, "S-N"),
])
def test_confirm_ceiling_matrix(e_level, category, independent, baseline, scope, expected):
    assert rt.confirm_ceiling(
        e_level=e_level, category=category, baseline_declared=baseline,
        scope_declared=scope, reviewer_kind="independent" if independent else "auto_scan",
    ) == expected


# ──────────────────────────── 4/5/6. 判定 ────────────────────────────

def test_judge_match_when_reason_and_evidence_agree():
    rc = rt.judge(record(), facts())
    assert rc.verdict == "MATCH" and rc.action == "none"
    assert rc.recomputed_e == "E2" and rc.recomputed_grade == "S3"
    assert rc.numbers_declared["report_chars"] == 1200
    assert rc.numbers_recomputed["report_chars"] == 1200


def test_judge_rejects_upgrade_when_s3_has_single_evidence():
    rc = rt.judge(record(grade="S3", e_level="E1"), facts(delta_categories=()))
    assert rc.verdict == "REJECT_UPGRADE" and rc.action == "reject"
    assert any(c.startswith("UPGRADE_EXCEEDS_CEILING") for c in rc.mismatch_codes)


def test_judge_rejects_broken_evidence_reference():
    broken = dict(broken_refs=("report:999",))
    rc = rt.judge(record(), facts(**broken))
    assert rc.verdict == "REJECT_BROKEN_EVIDENCE" and rc.action == "reject"


def test_judge_downgrades_e_overclaim():
    rc = rt.judge(record(grade="S1", e_level="E2"), facts(delta_categories=()))
    assert rc.verdict == "DOWNGRADE_EVIDENCE" and rc.action == "downgrade"
    assert rc.recomputed_e == "E1"


def test_judge_downgrades_reason_number_mismatch():
    rc = rt.judge(record(), facts(reports=(rt.ReportFacts(101, "published", 999, 1),)))
    assert rc.verdict == "DOWNGRADE_REASON" and rc.action == "downgrade"
    assert "MISMATCH_REPORT_CHARS" in rc.mismatch_codes
    assert "REASON_NOT_REPRODUCIBLE" in rc.mismatch_codes


def test_judge_flags_contradicted_absence_claim():
    reason = REASON_OK.replace("已发布 #101（正文 1200 字符，图 1 张）", "未关联已发布报告")
    rc = rt.judge(record(grade="S1", e_level="E1", grade_reason=reason),
                  facts(delta_categories=()))
    assert rc.verdict == "DOWNGRADE_REASON"
    assert "CONTRADICTED_REPORT_ABSENCE" in rc.mismatch_codes


def test_judge_underclaim_is_recorded_but_never_promoted():
    rc = rt.judge(record(grade="S1", e_level="E2"), facts())
    assert rc.verdict == "UNDERCLAIM" and rc.action == "none"
    assert rc.declared_grade == "S1" and rc.recomputed_grade == "S3"


def test_rule_drift_detects_divergence():
    assert rt.rule_drift(dict(rt.CATEGORY_GRADE)) == []
    drift = rt.rule_drift({"new_capability": "S2", "defect_fix": "S3"})
    assert {"new_capability", "defect_fix"} <= {d["category"] for d in drift}


# ──────────────────────────── 7. 落库幂等与回写 ────────────────────────────

EVAL_DDL = """
CREATE TABLE outcome_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    artifact_id   TEXT NOT NULL,
    mission_id    BIGINT,
    grade         TEXT,
    e_level       TEXT,
    status        TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reason_codes  TEXT NOT NULL DEFAULT '[]',
    decided_at    TEXT
);
"""


@pytest.fixture()
def conn():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True,
                           poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        connection.execute(text(EVAL_DDL))
        connection.execute(text("""
            INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, grade,
                                             e_level, status, reason_codes)
            VALUES ('eval-1','mission:9',9,'S3','E1','confirmed','[]'),
                   ('eval-2','mission:10',10,'S1','E1','confirmed','[]')
        """))
        connection.commit()
        yield connection


def test_apply_actions_writes_back_and_is_idempotent(conn):
    bad = rt.judge(record(grade="S3", e_level="E1", evaluation_id="eval-1",
                          artifact_id="mission:9"), facts(delta_categories=()))
    reason2 = ("规则化扫描（结构判据）：mission 10 于 2026-10-01T00:00:00+00:00 完结，贡献者 worker-a；"
               "报告 已发布 #102（正文 800 字符，图 0 张），能力增量类别 no_substantive_delta（工件 0 项）。")
    good = rt.judge(record(evaluation_id="eval-2", artifact_id="mission:10", mission_id=10,
                           grade="S1", e_level="E1", grade_reason=reason2,
                           evidence_refs=({"kind": "api", "ref": "/api/v1/tensor/missions/10/provenance"},
                                          {"kind": "report", "ref": "102"})),
                    facts(mission_id=10, reports=(rt.ReportFacts(102, "published", 800, 0),),
                          delta_categories=("no_substantive_delta",)))
    assert bad.verdict == "REJECT_UPGRADE" and good.verdict == "MATCH"
    stats = rt.apply_actions(conn, run_id="run-1", rechecks=[bad, good])
    assert stats["records"] == 2 and stats["rejected"] == 1 and stats["matched"] == 1

    rows = conn.execute(text(
        "SELECT evaluation_id, verdict, action, blind_order FROM eval_redteam_rechecks "
        "ORDER BY evaluation_id")).all()
    assert [(r[0], r[1]) for r in rows] == [("eval-1", "REJECT_UPGRADE"), ("eval-2", "MATCH")]
    status = conn.execute(text("SELECT status FROM outcome_evaluations WHERE evaluation_id='eval-1'")).scalar()
    assert status == "rejected"
    codes = conn.execute(text("SELECT reason_codes FROM outcome_evaluations WHERE evaluation_id='eval-1'")).scalar()
    assert "UPGRADE_EXCEEDS_CEILING" in codes

    again = rt.apply_actions(conn, run_id="run-1", rechecks=[bad, good])
    assert again["records"] == 0 and again["skipped"] == 2
    total = conn.execute(text("SELECT count(*) FROM eval_redteam_rechecks")).scalar()
    assert total == 2


def test_downgrade_target_never_promotes():
    rc = rt.judge(record(grade="S1", e_level="E2"), facts(delta_categories=()))
    assert rc.verdict == "DOWNGRADE_EVIDENCE"
    e, grade = rt._downgrade_target(rc)
    assert e == "E1" and grade == "S1"          # S 只降不升
    reason_rc = rt.judge(record(), facts(reports=(rt.ReportFacts(101, "published", 1, 1),)))
    e2, _ = rt._downgrade_target(reason_rc)
    assert e2 == "E1"                            # 降一级，下限 E1

# ──────────────────────────── 9. 非空洞性（0 发现也要能报警） ────────────────────────────

def test_mutation_controls_detect_every_injected_fault():
    pairs = [(record(), facts())]
    assert rt.judge(*pairs[0]).verdict == "MATCH"      # 基线单本身一致
    controls = rt.mutation_controls(pairs)
    assert controls["by_mutation"]["M2_GRADE_S5"]["detected"] == 1
    assert controls["by_mutation"]["M3_CHARS_PLUS1"]["detected"] == 1
    assert controls["by_mutation"]["M4_DROP_DELTA"]["detected"] == 1
    assert controls["total"] == 4 and controls["detected"] == controls["total"]
    assert controls["detection_rate"] == 1.0
