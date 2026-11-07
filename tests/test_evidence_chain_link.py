"""IN59-A3 结论五元证据链（evidence chain）验收测试。

判据（先于实现，机器可裁）
--------------------------
C1 **五元契约**：``theory/code/config/data/report`` 各自形态判据可机裁；形态非法不
   计入「已提供」（不算完整）。
C2 **缺件封顶**：S2+ 声明五元不全 → E 级封顶 ``E1``（只降不升，E4/E3/E2 都被压到 E1）；
   S1 豁免（不要求链）。
C3 **降级联动**：封顶后低于该 S 级 E 门槛（S3/S4/S-N 需 E≥2、S5 需 E≥3）→
   status=candidate（不计分布、不给信用），而不是「带病 confirmed」。
C4 **红队（本节点硬判据）**：S3 声明缺原始数据（``data``）→ 必须被拦并降级：
   status≠confirmed、e_level=E1、命中 ``CHAIN_RAW_DATA_MISSING``。
C5 **schema**：``outcome_evidence_chains`` 在 SQLite/PG 通用子集内建表，取值域由 CHECK
   硬拒；同一评价写链幂等（重判不叠行）；审计视图 ``outcome_eval_chain_audit`` 可查。
C6 **结算时链检接线**：结算守卫 SQL 只放行链完整的行——S3+ 无完整链的 confirmed 行
   被 ``chain_blocked`` 拦下（不按申报等级入信用账）；candidate 行本就不入账。
C7 **质量中心展示链完整度**：``/tensor/centers/quality`` 给出完整/缺件/全缺计数、
   平均完整度、封顶数与结算拦付数，逐项与手写 SQL 对拍。
C8 **落库链与判定一致**：API 写入后链行 ``e_level_granted`` = 判定 E、``capped`` = 是否封顶。

夹具全部合成域名（SyntheticRepo / claim-x / agent-a），不含任何学科语义。
"""
from __future__ import annotations

import importlib.util
import json
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

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from tools import evidence_chain_link as ecl  # noqa: E402
from tools import outcome_evaluations as oe  # noqa: E402

LONG_REASON = (
    "合成夹具理由：该结论的交付物、判据、复算命令与归档基线一一对应，"
    "五元引用均可机器解析，适用边界已显式声明，故按本夹具等级登记。"
)

FULL_CHAIN = {
    "conclusion_ref": "claim-x1",
    "theory": "doi:10.5555/synthetic.0042",
    "code": "SyntheticRepo@abcdef1",
    "config": "sha256:" + "c" * 64,
    "data": "runs/m1/result.json",
    "report": "7",
}

CONFIG_HASH = "sha256:" + "c" * 64

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


def _payload(**over):
    base = {
        "artifact_id": "A-chain-1",
        "mission_id": 11,
        "contributor": "agent-a",
        "grade": "S3",
        "e_level": "E3",
        "grade_reason": LONG_REASON,
        "evidence_refs": [{"kind": "report", "ref": "7"}],
        "reviewer": "reviewer-x",
        "reviewer_kind": "independent",
        "baseline_or_scope": "合成基线 B0",
        "boundary": "仅合成夹具",
        "repo": "SyntheticRepo",
        "evidence_chain": dict(FULL_CHAIN),
    }
    base.update(over)
    return base


def _without(chain: dict, *keys: str) -> dict:
    return {k: v for k, v in chain.items() if k not in keys}


# ──────────────────────────── 夹具 ──────────────────────────── #

@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text(MISSION_DDL))
    session.execute(text(
        "INSERT INTO tensor_dev_missions (id, organization_id, repo, status, claimed_by, finished_at) "
        "VALUES (11, 1, 'SyntheticRepo', 'succeeded', 'agent-a', '2026-10-01T00:00:00+00:00')"
    ))
    for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
        session.execute(text(stmt))
    ecl.ensure_chain_schema(session)
    session.commit()
    yield session
    session.close()


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


# ──────────────────── 1. 五元形态判据（逐元素） ──────────────────── #

@pytest.mark.parametrize(
    "key, good, bad",
    [
        ("theory", "doi:10.5555/abc.1", "记得某篇论文好像说过"),
        ("theory", "https://example.org/paper.pdf", "paper"),
        ("theory", "derivation:#eq3-energy-balance", "derivation:"),
        ("code", "abcdef1", "main"),
        ("code", "SyntheticRepo@0123abc", "SyntheticRepo@"),
        ("config", "sha256:" + "d" * 64, "configs/run.yaml"),
        ("config", "d" * 64, "sha256:deadbeef"),
        ("data", "runs/m1/output.csv", "见报告"),
        ("data", "artifact://bucket/key.bin", "3"),
        ("report", "report:118", "latest"),
        ("report", "#42", "0"),
    ],
)
def test_element_validators(key, good, bad):
    """每个元素都有可机裁的形态判据：合法形态过、含糊说法被拒。"""
    ok, code = ecl.VALIDATORS[key]
    assert ok(good) is True, f"{key} 应接受 {good!r}"
    assert ok(bad) is False, f"{key} 应拒 {bad!r}（{code}）"


def test_complete_chain_is_complete_and_uncapped():
    report = ecl.analyze_chain(FULL_CHAIN, "S3")
    assert report.status == "complete"
    assert report.completeness == 1.0
    assert report.cap is None and report.codes == ()
    assert report.conclusion_ref == "claim-x1"


def test_s1_is_exempt_from_chain():
    """S1 不要求链：无链也不封顶、不产生命中码（链只对 S2+ 是必要条件）。"""
    for raw in (None, {}, _without(FULL_CHAIN, "data")):
        report = ecl.analyze_chain(raw, "S1")
        assert report.required is False
        assert report.cap is None and report.codes == ()


def test_partial_chain_names_missing_and_invalid_elements():
    raw = {**_without(FULL_CHAIN, "data"), "code": "not-a-sha"}
    report = ecl.analyze_chain(raw, "S3")
    assert report.status == "partial"
    assert report.missing == ("data",)
    assert report.invalid == ("code",)
    assert report.completeness == round(3 / 5, 4)
    assert "CHAIN_RAW_DATA_MISSING" in report.codes
    assert "CHAIN_INVALID_CODE" in report.codes
    assert "CHAIN_MISSING_DATA" in report.codes
    assert report.cap == "E1"


# ──────────────────── 2. 封顶 / 3. 降级联动（decide） ──────────────────── #

def test_full_chain_keeps_declared_grade():
    v = oe.decide(_payload())
    assert v.status == "confirmed" and v.e_level == "E3" and v.codes == []


@pytest.mark.parametrize("declared, expected_e", [("E4", "E1"), ("E3", "E1"), ("E2", "E1")])
def test_incomplete_chain_caps_e_to_e1(declared, expected_e):
    """缺件封顶：只降不升，申报 E2/E3/E4 一律压到 E1（不是「保留原值再打折」）。"""
    v = oe.decide(_payload(e_level=declared, evidence_chain=_without(FULL_CHAIN, "theory")))
    assert v.e_level == expected_e
    assert "CHAIN_CAPS_E_TO_E1" in v.codes


def test_cap_does_not_raise_e_level():
    """只降不升：申报 E1 + 链完整 → 仍是 E1（链完整不是加分项）。"""
    v = oe.decide(_payload(grade="S1", e_level="E1", evidence_chain=FULL_CHAIN))
    assert v.status == "confirmed" and v.e_level == "E1"
    assert v.codes == []


def test_absent_chain_caps_s2_and_keeps_confirmed():
    """S2 只需 E≥1：无链 → 封顶 E1 后仍可 confirmed（封顶≠一律否决）。"""
    v = oe.decide(_payload(grade="S2", e_level="E3", evidence_chain=None))
    assert v.status == "confirmed" and v.e_level == "E1"
    assert "CHAIN_ABSENT" in v.codes


@pytest.mark.parametrize("grade", ["S3", "S4", "S5"])
def test_cap_below_grade_floor_downgrades_to_candidate(grade):
    """封顶压到 S 级 E 门槛之下 → candidate（不计分布、不给信用）。"""
    v = oe.decide(_payload(grade=grade, e_level="E3", evidence_chain=_without(FULL_CHAIN, "config")))
    assert v.status == "candidate"
    assert v.counted is False
    assert v.e_level == "E1"
    assert "CANDIDATE_CHAIN_CAP_BELOW_GRADE" in v.codes


def test_negative_track_cap_makes_claim_unregistrable():
    """S-N（否定轨）本就要 E≥2：链缺件把 E 压到 E1 → 硬判据 NEGATIVE_BELOW_E2 直接拒收。"""
    v = oe.decide(_payload(grade="S-N", e_level="E3", evidence_chain=_without(FULL_CHAIN, "config")))
    assert v.status == "rejected"
    assert "NEGATIVE_BELOW_E2" in v.codes


# ──────────────────── 4. 红队：S3 缺原始数据必被拦并降级 ──────────────────── #

def test_redteam_s3_missing_raw_data_is_blocked_and_downgraded():
    """S3 声明缺 ``data``（原始计算结果）→ 拦在 confirmed 之外并降级到 E1/candidate。"""
    payload = _payload(evidence_chain=_without(FULL_CHAIN, "data"))
    v = oe.decide(payload)
    assert v.status == "candidate", "缺原始数据的 S3 不得 confirmed"
    assert v.e_level == "E1"
    assert v.counted is False
    assert {"CHAIN_RAW_DATA_MISSING", "CANDIDATE_CHAIN_CAP_BELOW_GRADE"} <= set(v.codes)


def test_redteam_legacy_confirmed_row_is_barred_by_settlement_guard(db):
    """即使在链判据上线前已 confirmed 的 S3 行（无链），结算守卫也必须拦付。"""
    db.execute(text(
        "INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, contributor, "
        "grade, e_level, status, organization_id, created_at) "
        "VALUES ('eval-legacy', 'A-legacy', 11, 'agent-a', 'S3', 'E3', 'confirmed', 1, "
        "'2026-09-01T00:00:00+00:00')"
    ))
    db.commit()
    row = db.execute(text(ecl.settlement_guard_sql())).mappings().all()
    assert [r["evaluation_id"] for r in row] == []
    blocked = db.execute(text(
        "SELECT chain_blocked FROM outcome_eval_chain_audit WHERE evaluation_id = 'eval-legacy'"
    )).scalar()
    assert bool(blocked) is True


def test_settlement_guard_admits_complete_chain(db):
    db.execute(text(
        "INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, contributor, "
        "grade, e_level, status, organization_id, created_at) "
        "VALUES ('eval-good', 'A-good', 11, 'agent-a', 'S3', 'E3', 'confirmed', 1, "
        "'2026-09-01T00:00:00+00:00')"
    ))
    report = ecl.analyze_chain(FULL_CHAIN, "S3")
    ecl.store_chain(db, ecl.chain_record(_payload(artifact_id="A-good"), report,
                                        evaluation_id="eval-good", e_level_requested="E3",
                                        grade="S3"), commit=True)
    rows = db.execute(text(ecl.settlement_guard_sql())).mappings().all()
    assert [r["evaluation_id"] for r in rows] == ["eval-good"]


# ──────────────────── 5. schema：建表 / CHECK / 幂等 ──────────────────── #

def test_chain_schema_and_domain_checks(db):
    cols = {r[1] for r in db.execute(text("PRAGMA table_info(outcome_evidence_chains)")).all()}
    for required in ("chain_id", "evaluation_id", "conclusion_ref", "theory_ref", "code_ref",
                     "config_ref", "data_ref", "report_ref", "present_elements",
                     "missing_elements", "completeness", "chain_status", "capped"):
        assert required in cols, f"缺字段 {required}"
    view_cols = {r[1] for r in db.execute(text("PRAGMA table_info(outcome_eval_chain_audit)")).all()}
    assert {"chain_status", "chain_completeness", "chain_blocked"} <= view_cols

    base = {
        "chain_id": "chain:bad", "evaluation_id": "bad", "artifact_id": "A-bad",
        "completeness": 0.5, "chain_status": "bogus", "created_at": "2026-10-01T00:00:00+00:00",
    }
    with pytest.raises(Exception):        # 非法 chain_status
        db.execute(text(ecl.chain_insert_sql()), base)
    db.rollback()
    with pytest.raises(Exception):        # 完整度越界
        db.execute(text(ecl.chain_insert_sql()), {**base, "completeness": 1.5,
                                                 "chain_status": "partial"})
    db.rollback()


def test_store_chain_is_idempotent_per_evaluation(db):
    """重判/重跑同一条评价 → 链表只一行（先删同键再插），且内容为最新判定。"""
    for requested, chain in (("E4", _without(FULL_CHAIN, "data")), ("E2", FULL_CHAIN)):
        report = ecl.analyze_chain(chain, "S3")
        ecl.store_chain(db, ecl.chain_record(_payload(), report, evaluation_id="eval-idem",
                                             e_level_requested=requested, grade="S3"), commit=True)
    rows = db.execute(text(
        "SELECT completeness, chain_status, capped, e_level_requested, e_level_granted "
        "FROM outcome_evidence_chains WHERE evaluation_id = 'eval-idem'"
    )).mappings().all()
    assert len(rows) == 1
    assert rows[0]["chain_status"] == "complete" and rows[0]["capped"] in (0, False)
    assert rows[0]["e_level_requested"] == "E2" and rows[0]["e_level_granted"] == "E2"


# ──────────────────── 6/8. API 接线 + 落库一致性 ──────────────────── #

def test_api_persists_chain_and_exposes_summary(client, db):
    r = client.post("/api/v1/tensor/outcome-evaluations", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed" and body["e_level"] == "E3"
    summary = body["evidence_chain"]
    assert summary["chain_status"] == "complete" and summary["completeness"] == 1.0
    assert summary["capped"] is False

    got = client.get(f"/api/v1/tensor/outcome-evaluations/{body['evaluation_id']}/chain")
    assert got.status_code == 200
    row = got.json()
    assert row["chain_status"] == "complete"
    assert row["e_level_requested"] == "E3" and row["e_level_granted"] == "E3"
    assert json.loads(row["present_elements"]) == list(ecl.ELEMENT_KEYS)

    stats = client.get("/api/v1/tensor/outcome-evaluations/chains/stats")
    assert stats.status_code == 200
    assert stats.json()["by_status"] == {"complete": 1}
    assert stats.json()["completeness_avg"] == 1.0


def test_api_redteam_missing_raw_data_lands_capped_chain(client, db):
    """API 全链路红队：S3 缺 data → candidate/E1；链表记录封顶结果，结算守卫不放行。"""
    r = client.post("/api/v1/tensor/outcome-evaluations",
                    json=_payload(evidence_chain=_without(FULL_CHAIN, "data")))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "candidate"
    assert body["e_level"] == "E1"
    assert "CHAIN_RAW_DATA_MISSING" in body["reason_codes"]
    chain = body["evidence_chain"]
    assert chain["missing_elements"] == ["data"] and chain["capped"] is True
    assert chain["e_level_capped"] == "E1"

    stored = db.execute(text(
        "SELECT e_level_requested, e_level_granted, capped FROM outcome_evidence_chains "
        "WHERE evaluation_id = :eid"), {"eid": body["evaluation_id"]}
    ).mappings().one()
    assert stored["e_level_requested"] == "E3" and stored["e_level_granted"] == "E1"
    assert bool(stored["capped"]) is True

    # 结算面：candidate 不入账；即使手工改成 confirmed 也被链检拦下
    assert db.execute(text(ecl.settlement_guard_sql())).mappings().all() == []
    db.execute(text(
        "UPDATE outcome_evaluations SET status='candidate' WHERE evaluation_id = :eid"),
        {"eid": body["evaluation_id"]})
    db.commit()
    assert db.execute(text(
        "SELECT chain_blocked FROM outcome_eval_chain_audit WHERE evaluation_id = :eid"
    ), {"eid": body["evaluation_id"]}).scalar() in (0, False)


# ──────────────────── 7. 质量中心展示链完整度 ──────────────────── #

def _centers_module():
    path = REPO / "backend" / "tests" / "test_tensor_centers_contract.py"
    spec = importlib.util.spec_from_file_location("centers_contract_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quality_center_displays_chain_completeness():
    """质量中心：完整/缺件/全缺计数、平均完整度、封顶、结算拦付 —— 与手写 SQL 对拍。"""
    mod = _centers_module()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    session = Session()
    for ddl in mod.SCHEMA:
        session.execute(text(ddl))
    for stmt in mod.SEED:
        session.execute(text(stmt))
    for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
        session.execute(text(stmt))
    ecl.ensure_chain_schema(session)
    # 一条完整链 + 一条缺件链（confirmed 的 S3 → 结算拦付）
    for eid, artifact, chain, status, grade in (
        ("e-full", "A-full", FULL_CHAIN, "confirmed", "S3"),
        ("e-part", "A-part", _without(FULL_CHAIN, "data"), "confirmed", "S3"),
    ):
        session.execute(text(
            "INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, contributor, "
            "grade, e_level, status, organization_id, created_at) VALUES "
            "(:eid, :aid, 1, 'agent-a', :grade, 'E2', :st, 1, '2026-10-01T00:00:00+00:00')"
        ), {"eid": eid, "aid": artifact, "grade": grade, "st": status})
        report = ecl.analyze_chain(chain, grade)
        ecl.store_chain(session, ecl.chain_record(
            _payload(artifact_id=artifact, organization_id=1), report, evaluation_id=eid,
            e_level_requested="E2", grade=grade), commit=False)
    session.commit()

    from api.tensor_centers import quality_center

    body = quality_center(db=session, user={
        "id": 9, "username": "synth-admin", "role": "ADMIN", "organization_id": 1,
    })
    metrics = {m["key"]: m for m in body["metrics"]}
    assert metrics["evidence_chain_total"]["value"] == 2
    assert metrics["evidence_chain_complete"]["value"] == 1
    assert metrics["evidence_chain_partial"]["value"] == 1
    assert metrics["evidence_chain_missing"]["value"] == 0
    assert metrics["evidence_chain_completeness_avg"]["value"] == 0.9
    assert metrics["evidence_chain_capped"]["value"] == 1
    assert metrics["evidence_chain_blocked"]["value"] == 1
    gates = {g["key"]: g for g in body["gates"]}
    assert gates["evidence_chain_gate"]["state"] == "有缺链"
    assert body["lists"]["evidence_chains_incomplete"][0]["chain_status"] == "partial"

    # 手写 SQL 对拍（端点值 == 独立聚合）
    avg_sql = session.execute(text(
        "SELECT avg(completeness) FROM outcome_evidence_chains WHERE organization_id = 1"
    )).scalar()
    assert round(float(avg_sql), 4) == metrics["evidence_chain_completeness_avg"]["value"]