from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import text
import pytest

ROOT = Path(__file__).resolve().parents[2]


def request(f, method, path, ident=1, **kwargs):
    return f["client"].request(method, "/api/v1" + path, headers=f["headers"](ident), **kwargs)


def create(f):
    r = request(f, "POST", "/tensor/missions", ident=2,
                json={"goal": "Audit the specified energy dataset and produce a verified report",
                      "repo": "factory-project", "runner": "native"})
    assert r.status_code == 200, r.text
    return r.json()


def claim(f, ident=3):
    r = request(f, "POST", "/tensor/missions/claim", ident=ident,
                json={"agent_id": "pilot-worker", "runner": "native"})
    assert r.status_code == 200, r.text
    return r.json()


def start(f, mid, token):
    r = request(f, "POST", f"/tensor/missions/{mid}/start", ident=3,
                json={"claim_token": token})
    assert r.status_code == 200, r.text


def finish(f, mid, token):
    r = request(f, "POST", f"/tensor/missions/{mid}/complete", ident=3,
                json={"claim_token": token, "status": "succeeded", "acceptance": True,
                      "summary": {"headline": "Actual data audit passed"}, "checks": {"exit_code": 0}})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_and_scope_configuration(factory):
    c = factory["client"]
    assert c.get("/api/v1/tensor/factory").status_code == 401
    response = c.post("/api/v1/auth/login", json={"username": "engineer", "password": factory["password"]})
    assert response.status_code == 200
    config = c.get("/api/v1/tensor/factory", headers={"Authorization": "Bearer " + response.json()["access_token"]}).json()
    assert config["projects"] == [{"id": "factory-project", "name": "TensorPlatform 智能体协作平台"}]
    assert "state_root" not in config
    assert c.post("/api/v1/auth/register", json={}).status_code == 404
    assert c.get("/api/v1/tensorphysics/runs").status_code == 404


def test_project_allowlist_and_viewer_cannot_submit(factory):
    r = request(factory, "POST", "/tensor/missions", ident=2,
                json={"goal": "Work outside of authorized workspace", "repo": "TensorLBM"})
    assert r.status_code == 422
    r = request(factory, "POST", "/tensor/missions", ident=5,
                json={"goal": "Unauthorized viewer submission", "repo": "factory-project"})
    assert r.status_code == 403


def test_claim_is_tenant_scoped_and_requires_worker(factory):
    m = create(factory)
    assert claim(factory, 6)["claimed"] is False
    r = request(factory, "POST", "/tensor/missions/claim", ident=5, json={"agent_id": "spoof"})
    assert r.status_code == 403
    assert claim(factory)["mission"]["id"] == m["id"]


def test_only_one_concurrent_claim_wins(factory):
    create(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda ident: claim(factory, ident), [3, 4]))
    assert sum(r["claimed"] for r in replies) == 1


def test_claim_token_alone_does_not_impersonate_another_account(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    r = request(factory, "POST", f'/tensor/missions/{m["id"]}/start', ident=4,
                json={"claim_token": token})
    assert r.status_code == 403


def test_cross_tenant_details_children_stop_refill_and_events_denied(factory):
    m = create(factory); claim(factory)
    for method, path, body in [
        ("GET", f'/tensor/missions/{m["id"]}', None),
        ("GET", f'/tensor/missions/{m["id"]}/children', None),
        ("POST", f'/tensor/missions/{m["id"]}/stop', {}),
        ("POST", f'/tensor/missions/{m["id"]}/refill', {"add_tool_calls": 10}),
        ("GET", f'/tensor/sessions/{m["session_id"]}', None),
        ("POST", f'/tensor/sessions/{m["session_id"]}/events', {"kind": "note"}),
        ("POST", f'/tensor/sessions/{m["session_id"]}/events/batch', {"events": [{"kind": "note"}]}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        # Events require an AGENT; other operations exercise an unrelated org ADMIN.
        ident = 6 if "/events" in path else 7
        assert request(factory, method, path, ident, **kwargs).status_code == 404, path


def test_worker_cannot_self_accept_or_bypass_acceptance(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    start(factory, m["id"], token)
    r = request(factory, "POST", f'/tensor/missions/{m["id"]}/complete', ident=3,
                json={"claim_token": token, "status": "succeeded"})
    assert r.status_code == 422
    completion = finish(factory, m["id"], token)
    aid = completion["approval_id"]
    assert request(factory, "POST", f"/tensor/approvals/{aid}/decide", 3,
                   json={"decision": "approved"}).status_code == 403
    assert request(factory, "POST", f"/tensor/approvals/{aid}/decide", 7,
                   json={"decision": "approved"}).status_code == 404


def test_stopped_mission_cannot_be_approved_later(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    start(factory, m["id"], token); completion = finish(factory, m["id"], token)
    assert request(factory, "POST", f'/tensor/missions/{m["id"]}/stop', json={}).status_code == 200
    assert request(factory, "POST", f'/tensor/approvals/{completion["approval_id"]}/decide',
                   json={"decision": "approved"}).status_code == 409


def test_stop_reaches_worker_and_prevents_late_completion(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    start(factory, m["id"], token)
    request(factory, "POST", f'/tensor/missions/{m["id"]}/stop', json={})
    r = request(factory, "POST", f'/tensor/missions/{m["id"]}/heartbeat', 3,
                json={"claim_token": token})
    assert r.json()["action"] == "stop"
    r = request(factory, "POST", f'/tensor/missions/{m["id"]}/complete', 3,
                json={"claim_token": token, "status": "failed"})
    assert r.status_code == 409


def test_real_tool_artifact_acceptance_and_tamper_detection(factory):
    m = create(factory); mid = m["id"]; token = claim(factory)["mission"]["claim_token"]
    start(factory, mid, token)
    report = factory["runtime"] / "agent" / f"m{mid}" / "artifacts" / "energy-report.json"
    run = subprocess.run([sys.executable, str(ROOT / "tools/factory/check_energy_data.py"),
                          str(ROOT / "examples/factory-pilot/energy_measurements.csv"),
                          "--output", str(report)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    data = json.loads(report.read_text())
    assert data["energy_kwh_trapezoidal"] == 14.0
    content = report.read_bytes()
    ref = {"name": report.name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    r = request(factory, "POST", f"/tensor/missions/{mid}/complete", 3,
                json={"claim_token": token, "status": "succeeded", "acceptance": True,
                      "summary": {"artifacts": [ref]}, "checks": {"exit_code": run.returncode}})
    assert r.status_code == 200, r.text
    aid = r.json()["approval_id"]
    assert request(factory, "POST", f"/tensor/approvals/{aid}/decide",
                   json={"decision": "approved", "note": "Independently checked 14 kWh"}).status_code == 200
    assert request(factory, "GET", f"/tensor/missions/{mid}").json()["status"] == "succeeded"
    url = f"/tensor/factory/missions/{mid}/artifacts/{report.name}"
    assert request(factory, "GET", url).content == content
    assert request(factory, "GET", url, 7).status_code == 404
    report.write_text("tampered")
    assert request(factory, "GET", url).status_code == 409
    events = request(factory, "GET", f'/tensor/sessions/{m["session_id"]}').json()["events"]
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs)+1))
    assert events[-1]["kind"] == "approval_decided"


def test_failed_tool_is_not_reported_as_success(factory, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("timestamp,power_kw\n2026-01-01T00:00:00+00:00,10\n2026-01-01T00:15:00+00:00,-2\n")
    result = tmp_path / "result.json"
    run = subprocess.run([sys.executable, str(ROOT / "tools/factory/check_energy_data.py"),
                          str(bad), "--output", str(result)])
    assert run.returncode == 1
    assert json.loads(result.read_text())["status"] == "failed"


def test_viewer_cannot_forge_audit_events(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    start(factory, m["id"], token)
    assert request(factory, "POST", f'/tensor/sessions/{m["session_id"]}/events', 5,
                   json={"kind": "note", "payload": {"fake": True}}).status_code == 403
    r = request(factory, "POST", f'/tensor/sessions/{m["session_id"]}/events/batch', 3,
                json={"events": [{"kind": "tool_call"}, {"kind": "tool_result"}]})
    assert r.status_code == 200, r.text
    assert r.json()["appended"] == 2


def test_dispatch_uses_factory_projects_and_preserves_native_runner(factory):
    m = create(factory); token = claim(factory)["mission"]["claim_token"]
    start(factory, m["id"], token)
    r = request(factory, "POST", "/tensor/missions/dispatch", 3, json={
        "parent_id": m["id"], "claim_token": token, "goal": "Independently verify the produced energy report",
        "repo": "factory-project", "role": "verifier", "model": "configured-test-model"})
    assert r.status_code == 200, r.text
    child = request(factory, "GET", f'/tensor/missions/{r.json()["id"]}').json()
    assert child["runner"] == "native"
    assert child["parent_mission_id"] == m["id"]


def test_stale_claim_reaping_does_not_touch_another_organization(factory):
    r = request(factory, "POST", "/tensor/missions", 7, json={
        "goal": "Other organization owns this isolated task", "repo": "factory-project", "runner": "native"})
    mid = r.json()["id"]
    claim(factory, 6)
    with factory["engine"].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET heartbeat_at=now()-interval '10 minutes' WHERE id=:mid"), {"mid": mid})
    assert claim(factory)["claimed"] is False
    assert request(factory, "GET", f"/tensor/missions/{mid}", 7).json()["status"] == "claimed"
    assert mid in claim(factory, 6)["reaped"]


def test_append_only_audit_is_enforced_by_postgres(factory):
    from sqlalchemy.exc import DBAPIError

    m = create(factory)
    with pytest.raises(DBAPIError, match="append-only"):
        with factory["engine"].begin() as db:
            db.execute(text("DELETE FROM tensor_agent_session_events WHERE session_id=:sid"), {"sid": m["session_id"]})


def test_failed_evidence_gate_cannot_request_acceptance(factory):
    m=create(factory);token=claim(factory)['mission']['claim_token'];start(factory,m['id'],token)
    summary={'quality_gate':{'passed':False,'checks':[{'kind':'claim','passed':False}]},'continuation':{'action':'continue','reason':'repair evidence mismatch'}}
    body={'claim_token':token,'status':'succeeded','acceptance':True,'summary':summary}
    r=request(factory,'POST',f'/tensor/missions/{m["id"]}/complete',3,json=body)
    assert r.status_code==422
    r=request(factory,'POST',f'/tensor/missions/{m["id"]}/complete',3,json={**body,'status':'failed','acceptance':False})
    assert r.status_code==200,r.text
    saved=request(factory,'GET',f'/tensor/missions/{m["id"]}').json()
    assert saved['status']=='failed' and saved['result']['quality_gate']['passed'] is False
    assert saved['result']['continuation']['action']=='continue'

def test_native_mission_api_remains_usable_without_factory_profile(factory,monkeypatch):
    monkeypatch.delenv('FACTORY_PROFILE',raising=False)
    c=factory['client'];h=factory['headers']
    created=c.post('/api/v1/tensor/missions',headers=h(2),json={'goal':'Inspect TensorFVM regression results and report actual evidence','repo':'TensorFVM','runner':'native'})
    assert created.status_code==200,created.text
    claimed=c.post('/api/v1/tensor/missions/claim',headers=h(3),json={'agent_id':'upstream-native-worker','runner':'native'})
    assert claimed.status_code==200,claimed.text
    assert claimed.json()['claimed'] and claimed.json()['mission']['agent_session_id'] is None
    assert claimed.json()['mission']['repo']=='TensorFVM'
