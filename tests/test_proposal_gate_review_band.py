"""复核带（near_duplicate）裁决通道的测试：模型层 + API 层。

背景（供给链缺口）：提案进了复核带（``review_threshold ≤ score < dup_threshold``）后
既不被自动拒、也不入池，账本状态是 ``quarantined``——**此前没有任何处置通道**：
带上的行要么永久悬空，要么被 ``/gate/adopt`` 一步放过（等于 fail-closed 被绕过）。

本文件钉住三件事（全部 fail-closed）：
1. 带上可见：``review_band`` / ``GET /gate/review`` 只列 ``near_duplicate`` 带行，
   契约失败行（verdict=quarantined）**不**出现在队列里；
2. 出带须显式裁决：``admit`` → 入池（``proposal``），``reject`` → ``dedup_rejected``，
   复核人 + 理由（长度下限来自配置）一并入 ``reasons`` 与事件流；
3. 守卫不可绕：未裁决的带行 ``adopt`` 直接 409，契约失败行裁决也 409。

夹具纪律：合成域名 foo/bar/widget（见 ``tests/proposal_gate_support.py``），零领域语义。
检测阈值全部来自 ``config/proposal_gate.json``，本文件只断言区间关系，不写死阈值。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))  # 仓库根须在 backend 之前

from proposal_gate_support import synth_config, v2_payload  # noqa: E402
from tools.proposal_gate.config import load_config  # noqa: E402
from tools.proposal_gate.gate import GateTransitionError, ProposalGate  # noqa: E402
from tools.proposal_gate.ledger import LedgerStore  # noqa: E402

# 与被采纳提案「同义改写」的孪生文本：score 落在 [review_threshold, dup_threshold)。
# 数值由真实判定代码算出（本文件断言区间，不硬编码阈值）。
TWIN = dict(
    title="foo 通道 产物契约 校验 相似提案 拦截 播种核对",
    objective="为 foo 通道建立相似提案拦截与播种核对两道机器闸",
)
BAD_CRITERIA = ("效果好", "更稳", "更省")  # 无数值阈值 → 契约门 fail-closed


# ── 模型层夹具 ──────────────────────────────────────────────────────────────
@pytest.fixture()
def cfg():
    return synth_config()


@pytest.fixture()
def store(tmp_path, cfg):
    s = LedgerStore.for_sqlite(str(tmp_path / "review.sqlite"), cfg)
    yield s
    s.dispose()


@pytest.fixture()
def gate(tmp_path, cfg, store):
    return ProposalGate(
        cfg,
        store,
        quarantine_dir=tmp_path / "quarantine",
        events_file=tmp_path / "events.jsonl",
    )


def _seed_accepted(gate):
    res = gate.evaluate(v2_payload(mission_id=7101), mission_id=7101)
    assert res.verdict == "accepted", res
    return res


def _seed_band_row(gate):
    """先入池一份提案，再提交同义改写 → near_duplicate（进复核带）。"""
    _seed_accepted(gate)
    res = gate.evaluate(v2_payload(mission_id=7102, **TWIN), mission_id=7102)
    assert res.verdict == "near_duplicate", res
    return res


def _seed_contract_failure(gate):
    res = gate.evaluate(
        v2_payload(mission_id=7103, criteria=BAD_CRITERIA),
        mission_id=7103,
        persist_quarantine=False,
    )
    assert res.verdict == "quarantined", res
    return res


def _events(gate) -> list[dict]:
    path = Path(gate.events_file)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── 带上可见 ────────────────────────────────────────────────────────────────
def test_review_band_lists_only_near_duplicate_rows(gate, cfg):
    band_row = _seed_band_row(gate)
    _seed_contract_failure(gate)

    items = gate.review_band()
    verdicts = {item["verdict"] for item in items}
    assert verdicts == {"near_duplicate"}  # 契约失败行不在队列里
    assert [i["sha256"] for i in items] == [band_row.sha256]
    review_threshold = cfg.need("dedup.review_threshold")
    dup_threshold = cfg.need("dedup.dup_threshold")
    assert review_threshold <= items[0]["score"] < dup_threshold
    assert items[0]["dup_of"]  # 首次命中条目可追溯
    assert items[0]["decisions"] == ["admit", "reject"]  # 可选裁决来自配置


def test_review_band_is_empty_when_nothing_in_band(gate):
    _seed_accepted(gate)
    assert gate.review_band() == []


# ── 显式裁决 ────────────────────────────────────────────────────────────────
def test_adjudicate_admit_moves_row_into_pool_and_is_adoptable(gate):
    band_row = _seed_band_row(gate)
    row = gate.adjudicate(
        mission_id=7102,
        sha256=band_row.sha256,
        decision="admit",
        reviewer="agent:m77",
        note="复核认为目标与依赖不同，非同一立项",
    )
    assert (row.state, row.verdict) == ("proposal", "accepted")
    joined = " | ".join(row.reasons)
    assert "review_decision=admit" in joined and "reviewer=agent:m77" in joined
    assert "prev_verdict=near_duplicate" in joined  # 前后状态都留痕
    assert gate.review_band() == []  # 已出带

    adopted = gate.adopt(mission_id=7102, sha256=band_row.sha256, plan_id=11)
    assert adopted.state == "adopted"

    kinds = [e["kind"] for e in _events(gate)]
    assert "proposal_review_adjudicated" in kinds and "proposal_adopted" in kinds
    decision_event = next(e for e in _events(gate) if e["kind"] == "proposal_review_adjudicated")
    assert decision_event["decision"] == "admit"
    assert decision_event["reviewer"] == "agent:m77"
    assert decision_event["prev_state"] == "quarantined"


def test_adjudicate_reject_moves_row_to_dedup_rejected(gate):
    band_row = _seed_band_row(gate)
    row = gate.adjudicate(
        mission_id=7102,
        sha256=band_row.sha256,
        decision="reject",
        reviewer="agent:m77",
        note="复核确认与既有提案为同义改写，拒",
    )
    assert row.state == "dedup_rejected"
    assert "review_decision=reject" in " | ".join(row.reasons)
    assert gate.review_band() == []
    counts = gate.ledger.stats().counts
    assert counts["dedup_rejected"] == 1


# ── 守卫不可绕 ──────────────────────────────────────────────────────────────
def test_adopt_of_unadjudicated_band_row_is_rejected(gate):
    band_row = _seed_band_row(gate)
    with pytest.raises(GateTransitionError) as excinfo:
        gate.adopt(mission_id=7102, sha256=band_row.sha256)
    assert excinfo.value.code == "adopt_requires_state"
    # 失败不落状态：行仍在带上
    assert [i["sha256"] for i in gate.review_band()] == [band_row.sha256]


def test_contract_failure_cannot_be_adjudicated_nor_adopted(gate):
    bad = _seed_contract_failure(gate)
    with pytest.raises(GateTransitionError) as excinfo:
        gate.adjudicate(
            mission_id=7103,
            sha256=bad.sha256,
            decision="admit",
            reviewer="agent:m77",
            note="想直接放行一个契约不合格的提案",
        )
    assert excinfo.value.code == "not_in_review_band"

    with pytest.raises(GateTransitionError) as excinfo2:
        gate.adopt(mission_id=7103, sha256=bad.sha256)
    assert excinfo2.value.code == "adopt_requires_state"

    row = gate.ledger.get(7103, bad.sha256)
    assert row.state == "quarantined"  # fail-closed 未被绕过


def test_unknown_decision_is_rejected(gate):
    band_row = _seed_band_row(gate)
    with pytest.raises(GateTransitionError) as excinfo:
        gate.adjudicate(
            mission_id=7102,
            sha256=band_row.sha256,
            decision="force_pass",
            reviewer="agent:m77",
            note="未登记的裁决名不得生效",
        )
    assert excinfo.value.code == "unknown_decision"


def test_reviewer_and_note_are_mandatory(gate):
    band_row = _seed_band_row(gate)
    with pytest.raises(GateTransitionError) as excinfo:
        gate.adjudicate(
            mission_id=7102, sha256=band_row.sha256, decision="admit", reviewer="  ", note="理由充分"
        )
    assert excinfo.value.code == "reviewer_required"

    with pytest.raises(GateTransitionError) as excinfo2:
        gate.adjudicate(
            mission_id=7102, sha256=band_row.sha256, decision="admit", reviewer="agent:m77", note="短"
        )
    assert excinfo2.value.code == "note_too_short"


def test_note_min_length_comes_from_config(tmp_path, store):
    """零硬编码：把 ``review.min_note_length`` 抬到 64，同一段理由就不够长。"""
    strict_cfg = synth_config(**{"review.min_note_length": 64})
    strict = ProposalGate(
        strict_cfg,
        LedgerStore.for_sqlite(str(tmp_path / "strict.sqlite"), strict_cfg),
        quarantine_dir=tmp_path / "q",
        events_file=tmp_path / "e.jsonl",
    )
    _seed_band_row(strict)
    row_sha = strict.review_band()[0]["sha256"]
    with pytest.raises(GateTransitionError) as excinfo:
        strict.adjudicate(
            mission_id=7102,
            sha256=row_sha,
            decision="admit",
            reviewer="agent:m77",
            note="复核认为目标与依赖不同，非同一立项",
        )
    assert excinfo.value.code == "note_too_short"
    assert strict_cfg.need("review.min_note_length") == 64


def test_only_review_thresholds_live_in_config():
    """复核通道的判定边界只来自配置：代码侧不出现阈值字面量。"""
    review_cfg = load_config().need("review")
    assert set(review_cfg["decisions"]) >= {"admit", "reject"}
    assert review_cfg["queue_verdicts"] and review_cfg["queue_states"]
    assert isinstance(review_cfg["min_note_length"], int)
    src = (REPO_ROOT / "tools" / "proposal_gate" / "gate.py").read_text(encoding="utf-8")
    import re

    assert not re.search(r"min_note_length\s*=\s*\d", src)
    assert "0.55" not in src and "0.85" not in src


# ── API 层 ──────────────────────────────────────────────────────────────────
@pytest.fixture()
def api(tmp_path, monkeypatch):
    from api import proposal_gate as pg
    from api.deps import require_auth
    from database.config import get_db

    base_cfg = json.loads((REPO_ROOT / "config" / "proposal_gate.json").read_text(encoding="utf-8"))
    base_cfg["repo_whitelist"] = ["foo", "bar", "widget"]
    base_cfg["compat"]["v1_defaults"]["repo"] = "bar"
    base_cfg["ledger"]["quarantine_dir"] = str(tmp_path / "quarantine")
    base_cfg["ledger"]["events_file"] = str(tmp_path / "events.jsonl")
    cfg_path = tmp_path / "proposal_gate.json"
    cfg_path.write_text(json.dumps(base_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_GATE_CONFIG", str(cfg_path))
    pg._STORE_CACHE.clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE campaign_nodes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, metadata TEXT)")
        )
    Session = sessionmaker(bind=engine, future=True)

    def _db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    role = {"value": "manager"}

    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(pg.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = lambda: {
        "username": "tester",
        "role": role["value"],
    }
    client = TestClient(app)

    # 直接用 API 同源的引擎/配置写账本，制造带上行（判定逻辑已由模型层测试覆盖）
    store = LedgerStore.for_engine(engine, pg._config())

    def seed_band(mission_id: int, sha: str) -> None:
        store.record(
            mission_id=mission_id,
            sha256=sha,
            verdict="near_duplicate",
            schema_version="2.0",
            dup_of="ledger:" + "b" * 64,
            score=0.7,
            reasons=("near_duplicate_review 0.700000 in [review, dup)",),
        )

    def seed_contract_failure(mission_id: int, sha: str) -> None:
        store.record(
            mission_id=mission_id,
            sha256=sha,
            verdict="quarantined",
            schema_version="2.0",
            reasons=("contract: criteria 缺可量化阈值",),
        )

    def seed_accepted(mission_id: int, sha: str) -> None:
        store.record(
            mission_id=mission_id,
            sha256=sha,
            verdict="accepted",
            schema_version="2.0",
            score=0.0,
            reasons=("unit-test",),
        )

    yield {
        "client": client,
        "store": store,
        "role": role,
        "seed_band": seed_band,
        "seed_contract_failure": seed_contract_failure,
        "seed_accepted": seed_accepted,
    }
    pg._STORE_CACHE.clear()
    engine.dispose()


def test_api_review_queue_lists_band_rows_only(api):
    api["seed_band"](7201, "1" * 64)
    api["seed_contract_failure"](7202, "2" * 64)
    api["seed_accepted"](7203, "3" * 64)

    body = api["client"].get("/api/v1/proposals/gate/review").json()
    assert body["count"] == 1
    assert body["items"][0]["sha256"] == "1" * 64
    assert body["items"][0]["verdict"] == "near_duplicate"
    assert body["decisions"] == ["admit", "reject"]
    assert body["queue_verdicts"] == ["near_duplicate"]
    assert "SELECT" in body["sql_trace"].upper()


def test_api_adjudicate_admit_then_adopt(api):
    api["seed_band"](7211, "a" * 64)
    client = api["client"]

    # 未裁决直接采纳 → 409（守卫）
    blocked = client.post(
        "/api/v1/proposals/gate/adopt", json={"mission_id": 7211, "sha256": "a" * 64}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "adopt_requires_state"

    res = client.post(
        "/api/v1/proposals/gate/review/resolve",
        json={
            "mission_id": 7211,
            "sha256": "a" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "复核确认目标与依赖集合不同，非同一立项",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["row"]["state"] == "proposal"
    assert body["row"]["verdict"] == "accepted"
    assert body["remaining"] == 0

    adopt = client.post(
        "/api/v1/proposals/gate/adopt", json={"mission_id": 7211, "sha256": "a" * 64}
    )
    assert adopt.status_code == 200, adopt.text
    assert adopt.json()["row"]["state"] == "adopted"


def test_api_adjudicate_reject_and_conflicts(api):
    api["seed_band"](7221, "c" * 64)
    api["seed_contract_failure"](7222, "d" * 64)
    client = api["client"]
    url = "/api/v1/proposals/gate/review/resolve"

    rej = client.post(
        url,
        json={
            "mission_id": 7221,
            "sha256": "c" * 64,
            "decision": "reject",
            "reviewer": "agent:m77",
            "note": "复核确认与既有提案同义改写，拒",
        },
    )
    assert rej.status_code == 200, rej.text
    assert rej.json()["row"]["state"] == "dedup_rejected"

    again = client.post(
        url,
        json={
            "mission_id": 7221,
            "sha256": "c" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "二次裁决不允许（已出带）",
        },
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "not_in_review_band"

    fail_closed = client.post(
        url,
        json={
            "mission_id": 7222,
            "sha256": "d" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "试图放行契约不合格提案",
        },
    )
    assert fail_closed.status_code == 409
    assert fail_closed.json()["detail"]["code"] == "not_in_review_band"

    unknown = client.post(
        url,
        json={
            "mission_id": 7221,
            "sha256": "c" * 64,
            "decision": "force_pass",
            "reviewer": "agent:m77",
            "note": "未登记裁决名",
        },
    )
    assert unknown.status_code == 409
    assert unknown.json()["detail"]["code"] == "unknown_decision"

    missing = client.post(
        url,
        json={
            "mission_id": 999999,
            "sha256": "e" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "账本里没有这行",
        },
    )
    assert missing.status_code == 404

    short_note = client.post(
        url,
        json={
            "mission_id": 7221,
            "sha256": "c" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "短",
        },
    )
    assert short_note.status_code == 409
    # 顺序无关：note 太短与已出带都属 409，但原因码必须是二者之一且非空
    assert short_note.json()["detail"]["code"] in ("note_too_short", "not_in_review_band")


def test_api_review_resolve_requires_governance_role(api):
    api["seed_band"](7231, "f" * 64)
    client = api["client"]
    api["role"]["value"] = "engineer"
    res = client.post(
        "/api/v1/proposals/gate/review/resolve",
        json={
            "mission_id": 7231,
            "sha256": "f" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "工程角色不得做治理裁决",
        },
    )
    assert res.status_code == 403, res.text

    api["role"]["value"] = "admin"
    ok = client.post(
        "/api/v1/proposals/gate/review/resolve",
        json={
            "mission_id": 7231,
            "sha256": "f" * 64,
            "decision": "admit",
            "reviewer": "agent:m77",
            "note": "管理员复核后裁决放行（目标与依赖不同）",
        },
    )
    assert ok.status_code == 200, ok.text


def test_api_config_exposes_review_parameters(api):
    body = api["client"].get("/api/v1/proposals/gate/config").json()
    review = body["review"]
    assert review["queue_verdicts"] == ["near_duplicate"]
    assert review["decisions"] == ["admit", "reject"]
    assert review["adopt_allowed_states"] == ["proposal"]
    assert review["min_note_length"] >= 1