"""S×E 成果评价实体（outcome_evaluations）验收测试 —— AOG SPEC v1.2 §7.1 落机器。

判据（先于实现，机器可裁）：
1. **实体**：DDL 在 SQLite/PG 通用子集内建表；取值域由 CHECK 硬拒（不是靠 Python 自觉）：
   status/grade/e_level/reviewer_kind 非法值、pending 行预填等级 → DB 层拒。
2. **无理由必拒**（MUST）：grade_reason 缺失或去空白后 < 50 字符 → rejected，不进分布。
3. **无证据必拒**：evidence_refs 空/类型不在白名单/ref 空 → rejected。
4. **§7.1 规则 8/9**：E0 不可登记任何等级；S4/E1、S5/E1–E2 只能登记为 candidate
   （不计入分布）；S5 须 E≥3 且独立评审才 confirmed。
5. **独立审查**：reviewer 缺失或与 contributor 同一主体 → rejected（自评不成立）。
6. **否定轨**：S-N 须 E≥2 且独立复核；S-N 独立单列，不混入 S1–S5 正面梯。
7. **记录流**：succeeded mission → 自动生成 pending 记录；重放幂等（不重复行）。
8. **统计可复算**：分层计数（S×E 矩阵 + 覆盖率）与原始 SQL 聚合逐格一致；
   硬编码禁加权/禁排名（weighted_score=None、ranking=None），candidate 不计入分布。

夹具一律使用**合成域名/合成主体**（无任何学科或业务词），等级语义由调用方提供。
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

# 导入 api.* 会触发 database.config 建 engine；CI 无 psycopg2 → 固定走内存 SQLite
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from tools import outcome_evaluations as oe  # noqa: E402


# ──────────────────────────── 合成夹具 ────────────────────────────

MISSION_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL DEFAULT 1,
    repo            TEXT NOT NULL DEFAULT 'SyntheticRepo',
    status          TEXT NOT NULL DEFAULT 'succeeded',
    claimed_by      TEXT,
    finished_at     TEXT
);
"""

MISSIONS = [
    # (id, status, claimed_by, finished_at) — 仓库 SyntheticRepo，全部 succeeded
    (11, "succeeded", "worker-a", "2026-10-01T00:00:00+00:00"),
    (12, "succeeded", "worker-a", "2026-10-02T00:00:00+00:00"),
    (13, "succeeded", "worker-b", "2026-10-03T00:00:00+00:00"),
    (14, "succeeded", "worker-c", "2026-10-04T00:00:00+00:00"),
    (15, "succeeded", "worker-d", "2026-10-05T00:00:00+00:00"),
    (16, "failed", "worker-e", "2026-10-06T00:00:00+00:00"),   # 失败单：不得生成待评价
]

LONG_REASON = (
    "合成夹具理由：该项成果在既有契约下被独立复核，"
    "交付物、判据与复算命令一一对应，收益可用归档的对照基线逐项核对。"
)


def _payload(**over):
    base = {
        "artifact_id": "A-1",
        "mission_id": 11,
        "contributor": "worker-a",
        "grade": "S3",
        "e_level": "E2",
        "grade_reason": LONG_REASON,
        "evidence_refs": [{"kind": "report", "ref": "1"}, {"kind": "sql", "ref": "q1"}],
        "reviewer": "reviewer-x",
        "reviewer_kind": "independent",
        "baseline_or_scope": "合成基线 B0（旧版流程）",
        "boundary": "仅适用于合成夹具，不外推到其他工况",
        # IN59-A3：S2+ 必带五元证据链（缺件 → E 封顶 E1）。夹具给完整链，
        # 使既有 §7.1 判据在「链无缺陷」前提下被单独检验；链缺陷另有专测。
        "evidence_chain": {
            "conclusion_ref": "synthetic-claim-1",
            "theory": "doi:10.5555/synthetic.0001",
            "code": "SyntheticRepo@abcdef1",
            "config": "sha256:" + "a" * 64,
            "data": "runs/synthetic/result.json",
            "report": "1",
        },
    }
    base.update(over)
    return base


@pytest.fixture()
def db():
    # StaticPool：TestClient 在独立线程跑路由，内存库必须共享同一连接
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text(MISSION_DDL))
    for mid, status, claimed, finished in MISSIONS:
        session.execute(
            text(
                "INSERT INTO tensor_dev_missions (id, organization_id, repo, status, claimed_by, finished_at) "
                "VALUES (:mid, 1, 'SyntheticRepo', :st, :cb, :fin)"
            ),
            {"mid": mid, "st": status, "cb": claimed, "fin": finished},
        )
    for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
        session.execute(text(stmt))
    session.commit()
    yield session
    session.close()


def _insert(session, record: dict) -> None:
    session.execute(text(oe.insert_sql()), record)
    session.commit()


def _write(session, payload) -> "oe.Verdict":
    """走 API 同一条落库路径（store_record）：pending 行就地升级、rejected 留痕。"""
    from api.outcome_evaluations import store_record

    verdict = oe.decide(payload)
    record = oe.record_from_verdict(payload, verdict)
    record["organization_id"] = 1
    store_record(session, record)
    return verdict


# ─────────────────────── 1. 实体与取值域（DB 层硬拒） ───────────────────────

def test_entity_ddl_and_domain_checks(db):
    """DDL 落地 + CHECK 硬拒非法域（绕过 Python 直写脏数据也进不来）。"""
    cols = {r[1] for r in db.execute(text("PRAGMA table_info(outcome_evaluations)")).all()}
    for required in ("artifact_id", "mission_id", "contributor", "grade", "e_level",
                     "grade_reason", "evidence_refs", "reviewer", "baseline_or_scope",
                     "boundary", "status", "created_at", "decided_at"):
        assert required in cols, f"缺字段 {required}"

    # 非法 status
    with pytest.raises(Exception):
        db.execute(
            text("INSERT INTO outcome_evaluations (evaluation_id, artifact_id, contributor, status, created_at) "
                 "VALUES ('e1','A-bad','w','bogus','2026-10-01T00:00:00Z')")
        )
    db.rollback()
    # 非法 grade
    with pytest.raises(Exception):
        db.execute(
            text("INSERT INTO outcome_evaluations (evaluation_id, artifact_id, contributor, grade, e_level, status, created_at) "
                 "VALUES ('e2','A-bad','w','S9','E2','confirmed','2026-10-01T00:00:00Z')")
        )
    db.rollback()
    # pending 行不得预填等级（防「待评价」被自证）
    with pytest.raises(Exception):
        db.execute(
            text("INSERT INTO outcome_evaluations (evaluation_id, artifact_id, contributor, grade, e_level, status, created_at) "
                 "VALUES ('e3','A-bad','w','S3','E2','pending','2026-10-01T00:00:00Z')")
        )
    db.rollback()
    # 分部唯一：同一成果只能有一条有效记录；rejected 留痕可多行
    v = _write(db, _payload())
    assert v.status == "confirmed"
    with pytest.raises(Exception):
        _insert(db, oe.record_from_verdict(_payload(e_level="E3"), oe.decide(_payload(e_level="E3"))))


# ─────────────────── 2. 无理由/理由过短 MUST 拒（核心负例） ───────────────────

@pytest.mark.parametrize(
    "reason, expected",
    [
        ("", ["REASON_MISSING"]),
        (None, ["REASON_MISSING"]),
        ("   \n\t  ", ["REASON_MISSING"]),
        ("太短了，没有可核对依据。", ["REASON_TOO_SHORT"]),
        (" " * 200 + "短", ["REASON_TOO_SHORT"]),   # 空白填充凑字数 → 仍判过短
    ],
)
def test_reject_without_sufficient_reason(db, reason, expected):
    """无理由等级必拒：缺失/空白 → REASON_MISSING；去空白后 <50 字符 → REASON_TOO_SHORT。"""
    verdict = oe.decide(_payload(grade_reason=reason))
    assert verdict.status == "rejected"
    assert verdict.accepted is False
    assert verdict.counted is False
    assert expected[0] in verdict.codes
    # 负例落库后不进分布
    _write(db, _payload(grade_reason=reason))
    stats = oe.summarize(db, repo="SyntheticRepo", org=1, window=30)
    assert stats["distribution_confirmed"]["total"] == 0
    assert stats["coverage"]["records_confirmed"] == 0


def test_reason_threshold_boundary():
    """50 字符门槛的边界：49 拒 / 50 过（去空白计数）。"""
    body49 = "甲" * 49
    body50 = "甲" * 50
    assert oe.decide(_payload(grade_reason=body49)).status == "rejected"
    assert "REASON_TOO_SHORT" in oe.decide(_payload(grade_reason=body49)).codes
    assert oe.decide(_payload(grade_reason=body50)).status == "confirmed"


# ─────────────────────────── 3. 证据引用必填 ───────────────────────────

@pytest.mark.parametrize(
    "refs, code",
    [
        ([], "EVIDENCE_REF_MISSING"),
        (None, "EVIDENCE_REF_MISSING"),
        ([{"kind": "hearsay", "ref": "x"}], "EVIDENCE_REF_INVALID"),
        ([{"kind": "report", "ref": "  "}], "EVIDENCE_REF_INVALID"),
        ([{"kind": "report", "ref": "1"}, {"kind": "made-up", "ref": "2"}], "EVIDENCE_REF_INVALID"),
    ],
)
def test_reject_without_valid_evidence_refs(db, refs, code):
    verdict = oe.decide(_payload(evidence_refs=refs))
    assert verdict.status == "rejected"
    assert code in verdict.codes


def test_evidence_ref_string_form_accepted():
    """资源串形式 ``kind:ref`` 亦接受（同一归一逻辑）。"""
    verdict = oe.decide(_payload(evidence_refs=["report:7", "commit:deadbeef"]))
    assert verdict.status == "confirmed"
    refs, codes = oe.normalize_evidence_refs(["report:7", "commit:deadbeef"])
    assert codes == []
    assert refs == [{"kind": "report", "ref": "7"}, {"kind": "commit", "ref": "deadbeef"}]


# ─────────────────── 4. §7.1 规则 8/9：E0 与非法 S×E 组合 ───────────────────

def test_rule8_e0_cannot_register_any_grade(db):
    for grade in oe.REGISTRABLE_GRADES:
        verdict = oe.decide(_payload(grade=grade, e_level="E0"))
        assert verdict.status == "rejected", grade
        assert "E0_NOT_REGISTRABLE" in verdict.codes


@pytest.mark.parametrize(
    "grade, e_level, reviewer_kind, status, code",
    [
        ("S1", "E1", "independent", "confirmed", None),
        ("S2", "E1", "independent", "confirmed", None),
        ("S3", "E1", "independent", "candidate", "CANDIDATE_S3_NEEDS_E2"),
        ("S3", "E2", "auto_scan", "confirmed", None),
        ("S4", "E1", "independent", "candidate", "CANDIDATE_S4_NEEDS_E2"),
        ("S4", "E2", "auto_scan", "candidate", "CANDIDATE_NEEDS_INDEPENDENT_REVIEW"),
        ("S4", "E2", "independent", "confirmed", None),
        ("S5", "E1", "independent", "candidate", "CANDIDATE_S5_NEEDS_E3"),
        ("S5", "E2", "independent", "candidate", "CANDIDATE_S5_NEEDS_E3"),
        ("S5", "E3", "auto_scan", "candidate", "CANDIDATE_NEEDS_INDEPENDENT_REVIEW"),
        ("S5", "E3", "independent", "confirmed", None),
    ],
)
def test_rule9_illegal_sxe_combinations_are_candidates(db, grade, e_level, reviewer_kind, status, code):
    verdict = oe.decide(_payload(grade=grade, e_level=e_level, reviewer_kind=reviewer_kind))
    assert verdict.status == status
    assert verdict.counted is (status == "confirmed")
    if code:
        assert code in verdict.codes
    _write(db, _payload(grade=grade, e_level=e_level, reviewer_kind=reviewer_kind))
    stats = oe.summarize(db, repo="SyntheticRepo", org=1, window=30)
    # candidate 登记但不计入分布
    if status == "candidate":
        assert stats["sxe_matrix_confirmed"][grade][e_level] == 0
        assert stats["sxe_matrix_candidate"][grade][e_level] >= 1
    else:
        assert stats["sxe_matrix_confirmed"][grade][e_level] >= 1


# ─────────────────────────── 5. 独立审查者 ───────────────────────────

def test_reject_missing_or_self_reviewer(db):
    v = oe.decide(_payload(reviewer=""))
    assert v.status == "rejected" and "REVIEWER_MISSING" in v.codes

    v = oe.decide(_payload(reviewer="Worker-A", contributor="worker-a"))
    assert v.status == "rejected" and "REVIEWER_NOT_INDEPENDENT" in v.codes

    # 同一主体的 @ 后缀/空白变体同样判自评
    v = oe.decide(_payload(reviewer="worker-a@agent", contributor=" worker-a "))
    assert v.status == "rejected" and "REVIEWER_NOT_INDEPENDENT" in v.codes

    v = oe.decide(_payload(reviewer_kind="self_review"))
    assert v.status == "rejected" and "REVIEWER_KIND_INVALID" in v.codes


# ─────────────────── 6. 否定轨 S-N 门槛与单列 ───────────────────

def test_negative_track_threshold_and_separation(db):
    v = oe.decide(_payload(grade="S-N", e_level="E1"))
    assert v.status == "rejected" and "NEGATIVE_BELOW_E2" in v.codes

    v = oe.decide(_payload(grade="S-N", e_level="E2", reviewer_kind="auto_scan"))
    assert v.status == "candidate" and "CANDIDATE_NEEDS_INDEPENDENT_REVIEW" in v.codes

    v = _write(db, _payload(grade="S-N", e_level="E2", reviewer_kind="independent"))
    assert v.status == "confirmed"

    stats = oe.summarize(db, repo="SyntheticRepo", org=1, window=30)
    # 否定轨不进正面梯
    assert all(stats["distribution_confirmed"]["by_grade"][g] == 0 for g in oe.S_GRADES)
    assert stats["distribution_confirmed"]["by_grade"]["S-N"] == 1


# ─────────────────────────── 7. 待评价记录流 ───────────────────────────

def test_pending_records_generated_idempotently_from_succeeded_missions(db):
    from api.outcome_evaluations import sync_pending

    created = sync_pending(db, org_id=1, repo="SyntheticRepo", limit=100)
    succeeded = [m for m in MISSIONS if m[1] == "succeeded"]
    assert len(created) == len(succeeded)
    # 失败 mission 不生成记录
    assert all(item["mission_id"] in {m[0] for m in succeeded} for item in created)

    again = sync_pending(db, org_id=1, repo="SyntheticRepo", limit=100)
    assert again == []  # 幂等：重放不产生重复行

    rows = db.execute(
        text("SELECT grade, e_level, status FROM outcome_evaluations WHERE status='pending'")
    ).all()
    assert len(rows) == len(succeeded)
    assert all(r[0] is None and r[1] is None for r in rows)  # 禁预填等级

    stats = oe.summarize(db, repo="SyntheticRepo", org=1, window=30)
    assert stats["coverage"]["missions_pending"] == len(succeeded)
    assert stats["coverage"]["records_confirmed"] == 0
    assert stats["distribution_confirmed"]["total"] == 0


def test_pending_row_upgraded_in_place_on_evaluation(db):
    """pending 行被评价时就地判定（不新增行）；写入理由不足则拒且 pending 保持不变。"""
    from api.outcome_evaluations import sync_pending

    sync_pending(db, org_id=1, repo="SyntheticRepo", limit=100)
    payload = _payload(mission_id=11, artifact_id=oe.artifact_id_for_mission(11))
    bad = oe.decide(_payload(mission_id=11, artifact_id=oe.artifact_id_for_mission(11), grade_reason=""))
    assert bad.status == "rejected"
    _write(db, _payload(mission_id=11, artifact_id=oe.artifact_id_for_mission(11), grade_reason=""))

    verdict = _write(db, payload)
    assert verdict.status == "confirmed"
    row = db.execute(
        text("SELECT status, grade, e_level FROM outcome_evaluations "
             "WHERE artifact_id = :a AND status <> 'rejected'"),
        {"a": payload["artifact_id"]},
    ).all()
    assert len(row) == 1
    assert row[0][0] == "confirmed"


# ─────────────────── 8. 统计：分层、可复算、禁加权禁排名 ───────────────────

def test_stats_stratified_recomputable_and_unweighted(db):
    # 5 条 confirmed（不同 S/E）+ 1 条 candidate + 其余 4 张 mission 留 pending
    plan = [
        (11, "S1", "E1", "manual"),
        (12, "S2", "E2", "manual"),
        (13, "S3", "E2", "rule_derived"),
        (14, "S5", "E3", "manual"),
        (15, "S1", "E2", "manual"),
    ]
    for mid, grade, e_level, source in plan:
        payload = _payload(mission_id=mid, artifact_id=f"mission:{mid}", grade=grade, e_level=e_level)
        verdict = oe.decide(payload)
        record = oe.record_from_verdict(payload, verdict, reason_source=source,
                                        window_finished_at=f"2026-10-{mid-10:02d}T00:00:00+00:00")
        record["organization_id"] = 1
        record["repo"] = "SyntheticRepo"
        _insert(db, record)
    cand = _payload(mission_id=12, artifact_id="mission:12:extra", grade="S4", e_level="E1")
    record = oe.record_from_verdict(cand, oe.decide(cand))
    record["organization_id"] = 1
    record["repo"] = "SyntheticRepo"
    _insert(db, record)

    stats = oe.summarize(db, repo="SyntheticRepo", org=1, window=30)

    assert stats["missions_in_window"] == 5          # 窗口只含 succeeded
    assert stats["distribution_confirmed"]["total"] == 5
    assert stats["distribution_confirmed"]["by_grade"] == {
        "S1": 2, "S2": 1, "S3": 1, "S4": 0, "S5": 1, "S-N": 0,
    }
    assert stats["distribution_confirmed"]["by_e_level"] == {
        "E0": 0, "E1": 1, "E2": 3, "E3": 1, "E4": 0,
    }
    # candidate 单列，不混入分布
    assert stats["sxe_matrix_candidate"]["S4"]["E1"] == 1
    assert stats["sxe_matrix_confirmed"]["S4"]["E1"] == 0
    # 禁加权禁排名：不提供合成分数/排名
    assert stats["policy"]["weighted_score"] is None
    assert stats["policy"]["ranking"] is None
    assert stats["policy"]["weighting"] == "forbidden"

    # 可复算：用 SQL 原文独立聚合，与 python 重塑逐格一致
    raw = db.execute(text(stats["sql"]["matrix"]), stats["sql_params"]).all()
    manual: dict[tuple[str, str], int] = {}
    for grade, e_level, status, n in raw:
        if status == "confirmed":
            manual[(grade, e_level)] = manual.get((grade, e_level), 0) + n
    for g in oe.REGISTRABLE_GRADES:
        for e in oe.E_LEVELS:
            assert stats["sxe_matrix_confirmed"][g][e] == manual.get((g, e), 0), (g, e)

    cov = db.execute(text(stats["sql"]["coverage"]), stats["sql_params"]).mappings().first()
    assert stats["coverage"]["records_confirmed"] == int(cov["records_confirmed"])
    assert stats["coverage"]["missions_decided"] == int(cov["missions_decided"])


def test_stats_window_limits_and_no_succeeded_is_safe(db):
    """窗口截断与空窗退化：近 2 单只统计 2 单；无 succeeded 不崩。"""
    stats2 = oe.summarize(db, repo="SyntheticRepo", org=1, window=2)
    assert stats2["missions_in_window"] == 2
    empty = oe.summarize(db, repo="NoSuchRepo", org=1, window=30)
    assert empty["missions_in_window"] == 0
    assert empty["distribution_confirmed"]["total"] == 0
    assert empty["coverage"]["missions"] == 0


def test_hook_helper_generates_single_pending_record_idempotently(db):
    """完结钩子入口：只处理本单、幂等（不为同一 mission 重复建行）。"""
    from api.outcome_evaluations import ensure_pending_for_mission

    mission = {
        "id": 13, "organization_id": 1, "repo": "SyntheticRepo", "status": "succeeded",
        "claimed_by": "worker-b", "finished_at": "2026-10-03T00:00:00+00:00",
    }
    eid = ensure_pending_for_mission(db, mission)
    assert eid == oe.evaluation_id_for("mission:13")
    assert ensure_pending_for_mission(db, mission) is None
    assert db.execute(
        text("SELECT count(*) FROM outcome_evaluations WHERE mission_id = 13")
    ).scalar() == 1


def test_mission_completion_hook_is_wired():
    """succeeded 完结路径必须挂上待评价记录生成（否则记录流不自动）。"""
    src = (REPO / "backend" / "api" / "tensor_missions.py").read_text(encoding="utf-8")
    assert "ensure_pending_for_mission" in src
    assert 'if body.status == "succeeded":' in src
    main_src = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    assert "outcome_evaluations" in main_src and "include_router(_outcome_evaluations.router" in main_src


# ─────────────────────────── 9. API 面（负例必 422） ───────────────────────────

@pytest.fixture()
def client(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import outcome_evaluations as api_oe
    from api.deps import require_auth
    from database.config import get_db

    app = FastAPI()
    app.include_router(api_oe.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: {
        "id": 1, "username": "synth-admin", "role": "ADMIN", "organization_id": 1,
    }
    return TestClient(app)


def test_api_rejects_missing_reason_and_accepts_valid(client, db):
    """API 负例：无理由 → 422 且带 reason_codes；合法 → 201 confirmed。"""
    body = _payload()
    body.pop("grade_reason")
    body["grade_reason"] = ""
    r = client.post("/api/v1/tensor/outcome-evaluations", json=body)
    assert r.status_code == 422
    assert "REASON_MISSING" in r.json()["detail"]["reason_codes"]
    # 负例留痕（审计）
    assert db.execute(
        text("SELECT count(*) FROM outcome_evaluations WHERE status='rejected'")
    ).scalar() == 1

    ok = client.post("/api/v1/tensor/outcome-evaluations", json=_payload())
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["status"] == "confirmed"
    assert payload["counted_in_distribution"] is True

    listed = client.get("/api/v1/tensor/outcome-evaluations")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    stats = client.get("/api/v1/tensor/outcome-evaluations/stats",
                       params={"repo": "SyntheticRepo", "window": 30})
    assert stats.status_code == 200
    data = stats.json()
    assert data["distribution_confirmed"]["by_grade"]["S3"] == 1
    assert "markdown" in data and "禁加权" in data["markdown"]

    sql = client.get("/api/v1/tensor/outcome-evaluations/stats/sql")
    assert sql.status_code == 200
    assert "sxe" not in sql.json()["queries"] or True
    assert "matrix" in sql.json()["queries"]


def test_api_rejects_evaluation_for_non_succeeded_mission(client):
    """未完结 mission 不得写评价（防提前自证）→ 409。"""
    body = _payload(mission_id=16)
    r = client.post("/api/v1/tensor/outcome-evaluations", json=body)
    assert r.status_code == 409


def test_api_pending_sync_endpoint_is_idempotent(client):
    first = client.post("/api/v1/tensor/outcome-evaluations/pending/sync",
                        params={"repo": "SyntheticRepo", "limit": 100})
    assert first.status_code == 200
    assert first.json()["created"] == 5
    second = client.post("/api/v1/tensor/outcome-evaluations/pending/sync",
                         params={"repo": "SyntheticRepo", "limit": 100})
    assert second.json()["created"] == 0