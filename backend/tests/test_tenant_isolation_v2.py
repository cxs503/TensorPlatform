"""Mechanism tests for the tenant isolation v1 (platform-generic, zero domain wording).

All fixtures are synthetic: tenant names come from the injected registry document,
tenants used for boundary cases are the first/second registered entries, and the
domain vocabulary is consumed from config data at runtime (never spelled in source),
so this file itself stays domain-neutral (asserted by ``test_mechanism_code_is_domain_neutral``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services.tenant_isolation import (
    DomainTextGate,
    IsolationEngine,
    RegistryError,
    TenantRegistry,
)
from services.tenant_isolation.registry import default_registry_path

DENYLIST = Path(__file__).resolve().parents[2] / "config" / "domain_word_denylist.json"
MECHANISM_DIR = Path(__file__).resolve().parents[1] / "services" / "tenant_isolation"


@pytest.fixture()
def engine(tmp_path):
    eng = IsolationEngine(workspace_root=tmp_path)
    yield eng
    eng.store.close()


@pytest.fixture()
def pair(engine):
    names = engine.tenant_names()
    assert len(names) >= 2
    a, b = names[0], names[1]
    for t in (a, b):
        (Path(engine.workspace_root) / engine.registry.repo_of(t)).mkdir(
            parents=True, exist_ok=True
        )
    (Path(engine.workspace_root) / "shared").mkdir(parents=True, exist_ok=True)
    return engine, a, b


# --------------------------------------------------------------------------
# C1 - tenant registration
# --------------------------------------------------------------------------
def test_registry_document_registers_twelve_repos(engine):
    doc = json.loads(default_registry_path().read_text(encoding="utf-8"))
    completeness = engine.registry.completeness()
    assert completeness["complete"] is True
    assert completeness["tenant_count"] == len(doc["tenants"]) == 12
    assert len({r["repo"] for r in completeness["rows"]}) == 12
    for row in completeness["rows"]:
        assert row["fields_present"] and row["quota_complete"]


def test_registration_lands_in_feature_ledger(engine):
    rows = engine.store.ledger_query(item_type="tenant_registration")
    assert len(rows) == 12
    assert all(r["payload"]["resource_quota"] for r in rows)
    assert engine.store.ledger_query(tenant=rows[0]["tenant"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"drop": "resource_quota"},
        {"drop": "allowed_domains"},
        {"empty_domains": True},
        {"duplicate_repo": True},
        {"quota_missing_key": True},
        {"quota_not_numeric": True},
        {"bad_classification": True},
    ],
)
def test_incomplete_registry_document_is_rejected(mutation):
    doc = json.loads(default_registry_path().read_text(encoding="utf-8"))
    rows = doc["tenants"]
    if mutation.get("drop"):
        rows[0].pop(mutation["drop"])
    elif mutation.get("empty_domains"):
        rows[0]["allowed_domains"] = []
    elif mutation.get("duplicate_repo"):
        rows[1]["repo"] = rows[0]["repo"]
    elif mutation.get("quota_missing_key"):
        rows[0]["resource_quota"].pop(doc["resource_keys"][0])
    elif mutation.get("quota_not_numeric"):
        rows[0]["resource_quota"][doc["resource_keys"][0]] = "many"
    elif mutation.get("bad_classification"):
        rows[0]["data_classification"] = "top-secret-ish"
    with pytest.raises(RegistryError):
        TenantRegistry(doc)


# --------------------------------------------------------------------------
# C2 - resource quota admission
# --------------------------------------------------------------------------
OVER_BUDGET_CASES = [
    ("cpu_core_hours", 1e9, "quota_exceeded"),
    ("memory_gb_hours", 1e9, "quota_exceeded"),
    ("storage_gb", 1e9, "quota_exceeded"),
    ("concurrent_tasks", 1e6, "quota_exceeded"),
    ("periodic_tasks", 1e6, "quota_exceeded"),
    ("cpu_core_hours", -5, "invalid_amount"),
    ("cpu_core_hours", 0, "invalid_amount"),
    ("cpu_core_hours", float("nan"), "invalid_amount"),
    ("no_such_resource", 1, "unknown_resource"),
    ("concurrent_tasks", 1.5, "fractional_gauge"),
]


@pytest.mark.parametrize("resource,amount,reason", OVER_BUDGET_CASES)
def test_over_budget_claim_is_rejected(engine, resource, amount, reason):
    tenant = engine.tenant_names()[0]
    decision = engine.budget.claim(tenant, resource, amount, cycle="c1")
    assert decision.accepted is False
    assert decision.reason == reason


def test_unknown_tenant_claim_is_rejected(engine):
    decision = engine.budget.claim("tenant-not-registered", "cpu_core_hours", 1.0, cycle="c1")
    assert decision.accepted is False and decision.reason == "unknown_tenant"


def test_claim_within_budget_accumulates_then_blocks(engine):
    tenant = engine.tenant_names()[0]
    limit = engine.registry.require(tenant).resource_quota["cpu_core_hours"]
    assert engine.budget.claim(tenant, "cpu_core_hours", limit, cycle="c2").accepted is True
    again = engine.budget.claim(tenant, "cpu_core_hours", 0.1, cycle="c2")
    assert again.accepted is False and again.reason == "quota_exceeded"
    assert engine.budget.used_of(tenant, "cpu_core_hours", "c2") == pytest.approx(limit)


def test_gauge_claim_then_release(engine):
    tenant = engine.tenant_names()[0]
    cap = engine.registry.require(tenant).resource_quota["concurrent_tasks"]
    accepted = [engine.budget.claim(tenant, "concurrent_tasks", 1, cycle="c3") for _ in range(int(cap))]
    assert all(d.accepted for d in accepted)
    assert engine.budget.claim(tenant, "concurrent_tasks", 1, cycle="c3").accepted is False
    assert engine.budget.release(tenant, "concurrent_tasks", 1, cycle="c3") == cap - 1
    assert engine.budget.claim(tenant, "concurrent_tasks", 1, cycle="c3").accepted is True


def test_rejection_rate_is_auditable(engine):
    tenant = engine.tenant_names()[0]
    for resource, amount, _ in OVER_BUDGET_CASES:
        engine.budget.claim(tenant, resource, amount, cycle="c4")
    engine.budget.claim(tenant, "cpu_core_hours", 1.0, cycle="c4")
    stats = engine.budget.rejection_rate()
    assert stats["total"] == 11
    assert stats["rejection_rate"] == pytest.approx(10 / 11)
    assert any(r["decision"] == "REJECT" for r in engine.store.access_entries(tenant))


# --------------------------------------------------------------------------
# C3 - cross-repo write boundary
# --------------------------------------------------------------------------
def _cross_repo_cases(engine, a, b, tmp_path):
    ws = Path(engine.workspace_root)
    repo_a, repo_b = engine.registry.repo_of(a), engine.registry.repo_of(b)
    outside = tmp_path.parent / "outside-workspace-target"
    (ws / repo_a / "link_to_b").symlink_to(ws / repo_b)
    (ws / repo_a / "link_out").symlink_to(tmp_path.parent)
    return [
        (f"{repo_b}/pkg/mod.py", "cross_tenant_write"),
        (f"{repo_a}/../{repo_b}/pkg/mod.py", "path_traversal"),
        (str(ws / repo_b / "deep" / "x.py"), "cross_tenant_write"),
        (str(outside / "x.py"), "outside_tenant_namespace"),
        ("shared/x.py", "shared_root_read_only"),
        (f"{repo_a}/link_to_b/x.py", "cross_tenant_write"),
        (f"{repo_a}/link_out/x.py", "outside_tenant_namespace"),
        (f"{repo_b}/./x.py", "cross_tenant_write"),
        (f"{repo_b}/", "cross_tenant_write"),
        ("../" + repo_b + "/x.py", "path_traversal"),
    ]


def test_cross_repo_write_rejected(pair):
    engine, a, b = pair
    cases = _cross_repo_cases(engine, a, b, Path(engine.workspace_root))
    assert len(cases) == 10
    decisions = [engine.paths.check_write(a, path) for path, _ in cases]
    for (path, expected), decision in zip(cases, decisions):
        assert decision.allowed is False, (path, decision)
        assert decision.reason == expected, (path, decision.reason)
    assert all(not d.allowed for d in decisions)


def test_unknown_tenant_write_rejected(engine):
    decision = engine.paths.check_write("tenant-not-registered", "anything/x.py")
    assert decision.allowed is False and decision.reason == "unknown_tenant"


def test_own_repo_write_accepted(pair):
    engine, a, _ = pair
    decision = engine.paths.check_write(a, f"{engine.registry.repo_of(a)}/new_module.py")
    assert decision.allowed is True and decision.reason == "own_repo"


def test_read_own_and_shared_but_not_foreign(pair):
    engine, a, b = pair
    repo_a, repo_b = engine.registry.repo_of(a), engine.registry.repo_of(b)
    assert engine.paths.check_read(a, f"{repo_a}/x.py").allowed is True
    assert engine.paths.check_read(a, "shared/report.json").reason == "shared_root"
    denied = engine.paths.check_read(a, f"{repo_b}/x.py")
    assert denied.allowed is False and denied.reason == "cross_tenant_read_denied"


def test_grant_upgrades_read_only(pair):
    engine, a, b = pair
    repo_a, repo_b = engine.registry.repo_of(a), engine.registry.repo_of(b)
    ws = Path(engine.workspace_root)
    engine.grants.grant(b, a, str(ws / repo_b), scope="read", ttl_seconds=60)
    assert engine.paths.check_read(a, f"{repo_b}/x.py").reason == "explicit_grant"
    # a read grant must never become write authority
    assert engine.paths.check_write(a, f"{repo_b}/x.py").allowed is False
    engine.grants.grant(b, a, str(ws / repo_a), scope="read", ttl_seconds=60)
    expired = engine.grants.grant(b, a, str(ws / repo_b), scope="read", ttl_seconds=-1)
    engine.grants.revoke(expired)


# --------------------------------------------------------------------------
# C4 - pre-submission domain gate
# --------------------------------------------------------------------------
def _synthetic_payloads():
    lex = json.loads(DENYLIST.read_text(encoding="utf-8"))
    ascii_words = lex["ascii_words"]
    cjk_words = lex["cjk_words"]
    return {
        "standalone": f"LABEL = '{ascii_words[0]}'\n",
        "mixed_case": f"note = \"{ascii_words[1].upper()} suite\"\n",
        "json_value": json.dumps({"tag": ascii_words[2]}),
        "comment_only": f"# {ascii_words[3]} handled elsewhere\ndef f():\n    return 1\n",
        "cjk_embedded": f"TITLE = '关于{cjk_words[0]}的说明'\n",
    }


def test_domain_gate_rejects_each_synthetic_hit(engine):
    gate = engine.domain_gate()
    payloads = _synthetic_payloads()
    assert len(payloads) == 5
    for label, text in payloads.items():
        verdict = gate.check_submission({f"backend/pkg/{label}.py": text})
        assert verdict.allowed is False, label
        assert verdict.total_hits >= 1


def test_domain_gate_accepts_clean_submission(engine):
    gate = engine.domain_gate()
    verdict = gate.check_submission({"backend/pkg/mod.py": "def add(a, b):\n    return a + b\n"})
    assert verdict.allowed is True and verdict.total_hits == 0


def test_domain_gate_warn_mode_records_hits(engine):
    gate = engine.domain_gate(mode="warn")
    payloads = _synthetic_payloads()
    verdict = gate.check_submission({"x.py": payloads["standalone"]})
    assert verdict.allowed is True and verdict.total_hits >= 1
    assert verdict.mode == "warn"


def test_domain_gate_exempt_path_is_skipped(engine):
    gate = engine.domain_gate()
    payloads = _synthetic_payloads()
    exempt_name = str(gate.lexicon.get("exempt", ["config/domain_word_denylist.json"])[0])
    verdict = gate.check_submission({exempt_name: payloads["standalone"]})
    assert verdict.allowed is True and verdict.files_checked == 0


# --------------------------------------------------------------------------
# C5 - data isolation
# --------------------------------------------------------------------------
def test_artifact_cross_tenant_denied(pair):
    engine, a, b = pair
    assert engine.artifacts.write(a, f"{a}/runs/out.json", "{}").allowed is True
    denied = engine.artifacts.read(b, f"{a}/runs/out.json")
    assert denied.allowed is False and denied.reason == "cross_tenant_artifact_denied"
    wrong = engine.artifacts.read(b, "../" + f"{a}/runs/out.json")
    assert wrong.allowed is False and wrong.reason == "path_traversal"


def test_artifact_access_with_grant(pair):
    engine, a, b = pair
    ws = Path(engine.workspace_root)
    engine.artifacts.write(a, f"{a}/runs/out.json", "{}")
    engine.grants.grant(a, b, str(ws / engine.registry.policy["artifact_root"] / a), "read", 60)
    ok = engine.artifacts.read(b, f"{a}/runs/out.json")
    assert ok.allowed is True and ok.reason == "explicit_grant"


def test_report_cross_tenant_denied(pair):
    engine, a, b = pair
    engine.reports.write(a, f"{a}/r1/report.md", "# r1")
    denied = engine.reports.read(b, f"{a}/r1/report.md")
    assert denied.allowed is False and denied.reason == "cross_tenant_artifact_denied"


def test_event_cross_tenant_denied_unless_marked(pair):
    engine, a, b = pair
    private_id = engine.events.publish(a, "run.finished", {"k": 1})
    assert engine.events.read_event(b, private_id).allowed is False
    shared_id = engine.events.publish(a, "run.finished", {"k": 2}, visibility="shared")
    assert engine.events.read_event(b, shared_id).allowed is True
    b_view = engine.events.read(b)
    assert all(e["tenant"] == b or e["visibility"] == "shared" for e in b_view)


def test_artifact_listing_hides_foreign_names(pair):
    engine, a, b = pair
    engine.artifacts.write(a, f"{a}/runs/only_a.txt", "x")
    assert engine.artifacts.list_own(b) == []
    assert engine.artifacts.list_own(a) == [f"{a}/runs/only_a.txt"]


# --------------------------------------------------------------------------
# C7 - zero domain semantics inside the mechanism itself
# --------------------------------------------------------------------------
def test_mechanism_code_is_domain_neutral():
    gate = DomainTextGate.from_config()
    files = sorted(MECHANISM_DIR.glob("*.py")) + [Path(__file__)]
    rel = []
    for f in files:
        try:
            rel.append(str(f.relative_to(Path(__file__).resolve().parents[2])))
        except ValueError:
            rel.append(str(f))
    verdict = gate.check_paths(rel)
    assert verdict.files_checked >= len(files)
    assert verdict.total_hits == 0, verdict.violations[:5]


def test_self_check_surface(engine):
    report = engine.self_check()
    assert report["tenant_count"] == 12
    assert report["registry_complete"] is True
    assert report["budget_admission"] is True

# --------------------------------------------------------------------------
# API surface (thin pass-through, denials must not look like success)
# --------------------------------------------------------------------------
def test_api_surface_enforces_isolation(engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import tenant_isolation as api_module

    api_module.set_engine(engine)
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/v1")
    client = TestClient(app)
    base = "/api/v1/tenant-isolation"
    a, b = engine.tenant_names()[0], engine.tenant_names()[1]
    try:
        assert client.get(f"{base}/health").json()["tenant_count"] == 12
        assert client.get(f"{base}/tenants").json()["complete"] is True
        assert client.post(f"{base}/claim", json={
            "tenant": a, "resource": "cpu_core_hours", "amount": 1e9}).status_code == 409
        assert client.post(f"{base}/claim", json={
            "tenant": a, "resource": "cpu_core_hours", "amount": 1.0}).status_code == 200
        assert client.post(f"{base}/write-check", json={
            "tenant": a, "path": f"{engine.registry.repo_of(b)}/x.py"}).status_code == 403
        assert client.post(f"{base}/gate/scan", json={
            "files": {"backend/pkg/ok.py": "z = 0\n"}}).status_code == 200
        assert client.post(f"{base}/artifacts/read", json={
            "tenant": a, "path": f"{b}/runs/x.json"}).status_code == 403
    finally:
        api_module.set_engine(None)
