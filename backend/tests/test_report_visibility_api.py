"""API-level acceptance tests — report write-read visibility & lost-write healing.

Node ``p285w2b-n01``.  These tests drive the real FastAPI routers against a
file-backed SQLite slice of the platform (same harness as the report ownership
gate: real SQL, real HTTP, ``get_db``/``require_auth`` overridden only).  The
point is to prove the wiring, not the logic — the logic is covered by
``tests/test_report_visibility.py``.

Failure being reproduced
------------------------
"POST answers 200 but a later GET cannot see the row."  Here it is reproduced
honestly: the row is committed through the API, then removed behind the API's
back (``DELETE FROM tensor_reports``), which is exactly what a lost commit /
replica divergence looks like to a reader.  The suite asserts that

* a landed write verifies itself (readback block in the POST response),
* a vanished write is *detected* (409 ``record_missing``), not silently ignored,
* the probe finds every injected loss and flags no healthy row,
* the healer restores the row, proves it readable, and records the audit trail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests import report_gate_support as rgs  # noqa: E402  (real-sql harness)
from api import report_visibility as rv  # noqa: E402
from api import tensor_reports as tr  # noqa: E402
from api.deps import require_auth  # noqa: E402
from database.config import get_db  # noqa: E402

UID = 4242
MISSION = 8811
MARKDOWN = (
    "## ① case\n\nsynthetic platform payload — no domain content.\n\n"
    "## ③ numbers\n\n| k | v |\n|---|---|\n| a | 1.25 |\n\n"
    + "".join(f"- line {i}: value {i * 1.5:.2f} ± 0.01\n" for i in range(80))
)


def make_client(engine, figure_dir: Path) -> TestClient:
    """One app with both routers; identity via ``X-Test-User`` header."""
    users = {UID: rgs.fake_user(UID)}
    tr.FIGURE_DIR = figure_dir
    Session = sessionmaker(bind=engine, future=True)
    app = FastAPI()
    app.include_router(tr.router)
    app.include_router(rv.router)

    def _user(request: Request) -> dict:
        uid = int(request.headers.get("X-Test-User", "0"))
        if uid not in users:
            raise HTTPException(status_code=401, detail="unknown test user")
        return dict(users[uid])

    def _db() -> Iterator:
        session = Session()
        try:
            yield rgs._CompatSession(session)
        finally:
            session.close()

    app.dependency_overrides[require_auth] = _user
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = rgs.build_engine(tmp_path / "platform.db")
    rgs.seed_mission(engine, MISSION, owner_uid=UID)
    monkeypatch.setenv("TENSOR_REPORT_LEDGER_DIR", str(tmp_path / "ledger"))
    client = make_client(engine, tmp_path / "figures")
    return {"engine": engine, "client": client, "tmp": tmp_path}


def body(markdown: str = MARKDOWN, *, figures: list[dict] | None = None,
         mission_id: int = MISSION) -> dict:
    return {
        "title": "visibility acceptance report",
        "summary": "write-read contract + lost-write healing",
        "scenario_key": "platform-report-visibility",
        "mission_id": mission_id,
        "tags": ["visibility", "synthetic"],
        "markdown": markdown,
        "figures": figures or [],
    }


def post_report(env, markdown: str = MARKDOWN, figures: list[dict] | None = None) -> dict:
    resp = env["client"].post("/tensor/reports", json=body(markdown, figures=figures),
                              headers={"X-Test-User": str(UID)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def delete_row(env, report_id: int) -> None:
    """Simulate a lost commit / divergent replica: the row disappears."""
    with env["engine"].begin() as conn:
        conn.execute(text("DELETE FROM tensor_reports WHERE id = :rid"), {"rid": report_id})


def row_count(env) -> int:
    with env["engine"].connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM tensor_reports")).scalar() or 0)


# ─────────────────────── C1: write-read identity end to end ───────────────────────

def test_post_report_returns_verified_readback(env):
    out = post_report(env)
    readback = out["readback"]
    assert readback["ok"] is True, readback
    assert readback["readback"]["codes"] == []
    assert readback["submission_id"], "submission must be ledgered"
    assert readback["ledger_ok"] is True


def test_get_returns_markdown_byte_identical_to_the_post(env):
    """Acceptance: markdown read back from GET is 100 % identical (5/5 posts)."""
    identical = 0
    for i in range(5):
        md = MARKDOWN + f"\n- unique tail {i}\n"
        out = post_report(env, md)
        got = env["client"].get(f"/tensor/reports/{out['id']}",
                                headers={"X-Test-User": str(UID)})
        assert got.status_code == 200
        identical += int(got.json()["markdown"] == md)
    assert identical / 5 == 1.0


def test_figures_and_mission_id_round_trip(env):
    out = post_report(env, figures=[rgs.figure("fig_a")])
    assert out["readback"]["ok"] is True
    got = env["client"].get(f"/tensor/reports/{out['id']}",
                            headers={"X-Test-User": str(UID)}).json()
    assert [f["name"] for f in got["figures"]] == ["fig_a"]
    assert got["mission_id"] == MISSION


# ─────────────────────── C3: detection of the silent loss ───────────────────────

def test_readback_endpoint_confirms_a_visible_write(env):
    out = post_report(env)
    resp = env["client"].post(f"/tensor/reports/{out['id']}/readback",
                              json=body(), headers={"X-Test-User": str(UID)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_readback_endpoint_detects_mutated_body(env):
    """Negative control: the assertion must not rubber-stamp any body."""
    out = post_report(env)
    resp = env["client"].post(f"/tensor/reports/{out['id']}/readback",
                              json=body("totally different body — but looks fine"),
                              headers={"X-Test-User": str(UID)})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["ok"] is False
    assert detail["readback"]["codes"] == ["markdown_mismatch"]
    assert resp.headers.get("X-Error-Code") == "WRITE_READ_VISIBILITY_VIOLATION"


def test_readback_endpoint_reports_record_missing_after_row_loss(env):
    """The exact failure mode: POST said 200, the reader sees nothing."""
    out = post_report(env)
    delete_row(env, out["id"])
    resp = env["client"].post(f"/tensor/reports/{out['id']}/readback",
                              json=body(), headers={"X-Test-User": str(UID)})
    assert resp.status_code == 409
    assert resp.json()["detail"]["readback"]["codes"] == ["record_missing"]


def test_scan_detects_every_injected_loss_without_false_positives(env):
    """Acceptance: 10 injected losses → 10/10 detected, healthy rows untouched."""
    ids = [post_report(env, MARKDOWN + f"\n- scan case {i}\n")["id"] for i in range(12)]
    dropped = ids[:10]            # 10 of the 12 disappear
    assert len(dropped) == 10
    for rid in dropped:
        delete_row(env, rid)

    resp = env["client"].post("/tensor/reports/visibility/scan", json={},
                              headers={"X-Test-User": str(UID)})
    assert resp.status_code == 200, resp.text
    scan = resp.json()
    found = set(scan["scan"]["missing"])
    assert found == set(dropped), scan["scan"]
    assert scan["scan"]["checked"] == len(ids)
    # No healthy row may be flagged.
    assert found.isdisjoint(set(ids) - set(dropped))
    assert scan["ledger"]["chain"]["ok"] is True


def test_scan_is_clean_when_nothing_is_lost(env):
    """Negative control: a healthy pipeline produces zero detections."""
    for i in range(4):
        post_report(env, MARKDOWN + f"\n- healthy {i}\n")
    resp = env["client"].post("/tensor/reports/visibility/scan", json={},
                              headers={"X-Test-User": str(UID)})
    assert resp.json()["scan"]["missing"] == []


# ─────────────────────── C4: self-healing end to end ───────────────────────

def test_heal_resends_lost_reports_and_verifies_readback(env):
    ids = [post_report(env, MARKDOWN + f"\n- heal case {i}\n")["id"] for i in range(4)]
    lost = ids[:2]
    for rid in lost:
        delete_row(env, rid)
    assert row_count(env) == 2

    scan = env["client"].post("/tensor/reports/visibility/scan", json={},
                              headers={"X-Test-User": str(UID)}).json()
    assert set(scan["scan"]["missing"]) == set(lost)

    resp = env["client"].post("/tensor/reports/visibility/heal", json={"max_attempts": 3},
                              headers={"X-Test-User": str(UID)})
    assert resp.status_code == 200, resp.text
    heal = resp.json()["heal"]
    assert heal["attempted"] == 2
    assert heal["healed"] == 2
    assert heal["success_rate"] == 1.0
    assert row_count(env) == 4          # both reports are back

    # The restored rows are readable *and* identical to the original payload.
    for outcome in heal["outcomes"]:
        new_id = outcome["new_report_id"]
        assert new_id is not None
        got = env["client"].get(f"/tensor/reports/{new_id}",
                                headers={"X-Test-User": str(UID)})
        assert got.status_code == 200
        assert got.json()["markdown"] in {
            MARKDOWN + f"\n- heal case {i}\n" for i in range(4)}

    # …and a re-scan is clean: the loss is gone, not merely re-labelled.
    rescan = env["client"].post("/tensor/reports/visibility/scan", json={},
                                headers={"X-Test-User": str(UID)}).json()
    assert rescan["scan"]["missing"] == []
    assert rescan["ledger"]["chain"]["ok"] is True


def test_heal_escalates_when_payload_snapshot_is_gone(env, tmp_path):
    """Honest escalation: without a payload the loss cannot be repaired."""
    out = post_report(env)
    delete_row(env, out["id"])
    env["client"].post("/tensor/reports/visibility/scan", json={},
                       headers={"X-Test-User": str(UID)})
    recent = env["client"].get("/tensor/reports/visibility/ledger",
                               headers={"X-Test-User": str(UID)}).json()["recent"]
    lost_events = [e for e in recent if e["kind"] == "lost"]
    assert lost_events, "lost detection must be ledgered"
    submission_id = lost_events[0]["submission_id"]
    payload_file = Path(tmp_path / "ledger" / "payloads" / f"{submission_id}.json")
    assert payload_file.is_file()
    payload_file.unlink()

    resp = env["client"].post("/tensor/reports/visibility/heal", json={},
                              headers={"X-Test-User": str(UID)})
    heal = resp.json()["heal"]
    assert heal["healed"] == 0
    assert heal["outcomes"][0]["reason"] == "payload_missing_or_tampered"
    assert any(a["code"] == "LOST_WRITE_UNHEALABLE" for a in resp.json()["alerts"])


# ─────────────────────── C6: audit surface ───────────────────────

def test_ledger_endpoint_exposes_chain_and_event_kinds(env):
    out = post_report(env)
    delete_row(env, out["id"])
    env["client"].post("/tensor/reports/visibility/scan", json={},
                       headers={"X-Test-User": str(UID)})
    doc = env["client"].get("/tensor/reports/visibility/ledger",
                            headers={"X-Test-User": str(UID)}).json()
    assert doc["summary"]["chain"]["ok"] is True
    kinds = set(doc["summary"]["by_kind"])
    assert {"submitted", "verified", "lost"} <= kinds
    assert doc["recent"], "audit trail must be inspectable"
    assert all({"seq", "ts", "kind", "submission_id", "hash"} <= set(e)
               for e in doc["recent"])


def test_readback_payload_snapshot_reproduces_the_write_exactly(env, tmp_path):
    """The ledger snapshot must be enough to rebuild the row byte-for-byte."""
    out = post_report(env, figures=[rgs.figure("fig_rb")])
    sid = out["readback"]["submission_id"]
    snap = json.loads((Path(tmp_path / "ledger" / "payloads" / f"{sid}.json"))
                      .read_text(encoding="utf-8"))["payload"]
    assert snap["markdown"] == MARKDOWN
    assert [f["name"] for f in snap["figures"]] == ["fig_rb"]
    assert snap["mission_id"] == MISSION
    assert snap["created_by"] == UID