"""pytest suite for the capability evidence auto-upgrade pipeline (P218-N01).

Fixtures use **synthetic** capability names / metrics only (zero domain
semantics, C7).  Each acceptance criterion of the campaign node maps onto at
least one test:

C1 evidence association ............ test_c1_*
C2 auto rejudge ≤1s + consistency .. test_c2_*
C3 upgrade history fields .......... test_c3_*
C4 WITHHELD→REAL self-healing ...... test_c4_*
C5 negative control stays WITHHELD . test_c5_*
C6 pytest ≥10 green ................ this file
C7 zero domain semantics ........... test_c7_*
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[3]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from tools.capability_upgrade import (  # noqa: E402
    EvidencePipeline,
    RejudgeEngine,
    Store,
    apply_registry,
    load_registry,
)
from tools.capability_upgrade.api import create_app  # noqa: E402

PKG_DIR = Path(__file__).resolve().parents[1]


def sha(text: str) -> str:
    """A syntactically valid provenance sha256 (content-addressed in real use)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


REGISTRY = {
    "capabilities": [
        {"capability_id": "synth.alpha", "label": "synthetic alpha"},
        {"capability_id": "synth.beta", "label": "synthetic beta"},
    ],
    "criteria": [
        {
            "criterion_id": "crit.alpha.real",
            "capability_id": "synth.alpha",
            "target_level": "REAL",
            "description": "two independent numeric verifications, each error <= 3%",
            "spec": {
                "evidence_kinds": ["numeric_verification"],
                "min_matching": 2,
                "min_sources": 2,
                "require_source_sha256": True,
                "forbid_observed_levels": ["MOCK", "STUB"],
                "metrics": [
                    {"name": "error_pct", "op": "<=", "value": 3.0, "agg": "all"},
                    {"name": "n_cases", "op": ">=", "value": 3, "agg": "min"},
                ],
            },
        },
        {
            "criterion_id": "crit.beta.real",
            "capability_id": "synth.beta",
            "target_level": "REAL",
            "description": "one numeric verification with error <= 3%",
            "spec": {
                "evidence_kinds": ["numeric_verification"],
                "min_matching": 1,
                "require_source_sha256": True,
                "metrics": [{"name": "error_pct", "op": "<=", "value": 3.0, "agg": "all"}],
            },
        },
    ],
}


@pytest.fixture()
def store(tmp_path) -> Store:
    st = Store(tmp_path / "pipeline.db")
    apply_registry(st, REGISTRY)
    yield st
    st.close()


@pytest.fixture()
def pipe(store) -> EvidencePipeline:
    return EvidencePipeline(store, engine=RejudgeEngine(store))


def evidence(capability_id, tag, kind="numeric_verification", error_pct=1.0,
             n_cases=4, observed_level=None, with_sha=True, source_path=None):
    return {
        "capability_id": capability_id,
        "kind": kind,
        "metrics": {"error_pct": error_pct, "n_cases": n_cases},
        "observed_level": observed_level,
        "source": {
            "path": source_path or f"cases/{capability_id}/{tag}.json",
            "locator": f"cases::{tag}",
            "sha256": sha(f"{capability_id}:{tag}") if with_sha else "",
        },
        "payload": {"note": tag},
    }


# ── C1: capability ↔ evidence association ────────────────────────────────
def test_c1_withheld_slot_without_evidence_is_reported_as_uncovered(store, pipe):
    coverage = pipe.coverage()
    assert coverage["withheld_slots"] == 2
    assert coverage["coverage_ok"] is False
    assert {u["capability_id"] for u in coverage["withheld_without_evidence"]} == {
        "synth.alpha",
        "synth.beta",
    }

    pipe.ingest(**evidence("synth.alpha", "absence_audit", kind="absence_audit",
                           error_pct=None, n_cases=0))
    pipe.ingest(**evidence("synth.beta", "absence_audit", kind="absence_audit",
                           error_pct=None, n_cases=0))
    after = pipe.coverage()
    assert after["coverage_ok"] is True
    assert after["withheld_with_evidence"] == 2
    # association rows exist even though nothing was upgraded
    assert store.evidence_ids("synth.alpha")


# ── C2: auto re-judge within deadline + agreement with manual ────────────
def test_c2_auto_rejudge_completes_within_one_second(store, pipe):
    result = pipe.ingest(**evidence("synth.beta", "auto_1", error_pct=1.5))
    assert result["rejudged"] is True
    assert result["deadline_ok"] is True
    assert result["latency_ms"] < 1000.0
    event = store.list_events("synth.beta")[-1]
    assert event["event_type"] == "evidence_ingested"
    assert event["completed_at"] is not None
    assert event["latency_ms"] is not None and event["latency_ms"] < 1000.0
    assert event["rejudge_run_id"] == result["run"]["run_id"]


def test_c2_auto_rejudge_matches_manual_rejudge(store, pipe):
    pipe.ingest(**evidence("synth.beta", "cmp_1", error_pct=1.5))
    auto_run = store.list_runs("synth.beta")[-1]
    auto_level = auto_run["result_level"]

    manual_run = pipe.engine.judge("synth.beta", trigger="manual")
    assert manual_run["result_level"] == auto_level
    assert manual_run["before_level"] == auto_run["result_level"]
    auto_verdicts = [(v["criterion_id"], v["passed"]) for v in auto_run["verdicts"]]
    manual_verdicts = [(v["criterion_id"], v["passed"]) for v in manual_run["verdicts"]]
    assert auto_verdicts == manual_verdicts
    # latency is recorded for both paths
    assert auto_run["latency_ms"] is not None
    assert manual_run["latency_ms"] is not None


# ── C3: upgrade history carries evidence ids / criteria / time / result ──
def test_c3_history_records_evidence_criteria_timestamp_and_verdict(store, pipe):
    result = pipe.ingest(**evidence("synth.beta", "hist_1", error_pct=2.0))
    history = store.list_history("synth.beta")
    assert len(history) == 1
    row = history[0]
    assert row["evidence_ids"], "history must cite the evidence it decided on"
    assert result["evidence_id"] in row["evidence_ids"]
    assert row["criteria"] and all("spec" in c for c in row["criteria"])
    assert row["criteria"][0]["criterion_id"] == "crit.beta.real"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", row["evaluated_at"])
    assert row["outcome"] in {"upgraded", "held", "unconfigured"}
    assert row["after_level"] == "REAL"


# ── C4: WITHHELD → REAL self-healing once criteria are satisfied ─────────
def test_c4_withheld_upgrades_to_real_when_all_criteria_pass(store, pipe):
    first = pipe.ingest(**evidence("synth.alpha", "c4_a", error_pct=1.2))
    assert first["level"] == "WITHHELD"
    assert first["outcome"] == "held"
    second = pipe.ingest(**evidence("synth.alpha", "c4_b", error_pct=0.4))
    assert second["upgraded"] is True
    assert second["level"] == "REAL"
    assert store.get_slot("synth.alpha").current_level == "REAL"
    hist = store.list_history("synth.alpha")[-1]
    assert hist["before_level"] == "WITHHELD" and hist["after_level"] == "REAL"


def test_c4_upgrade_is_monotone_and_criteria_cited(store, pipe):
    pipe.ingest(**evidence("synth.alpha", "mono_a", error_pct=2.9))
    result = pipe.ingest(**evidence("synth.alpha", "mono_b", error_pct=2.8))
    assert result["level"] == "REAL"
    # a later weaker-but-passing evidence keeps REAL (no spurious downgrade)
    later = pipe.ingest(**evidence("synth.alpha", "mono_c", error_pct=2.95))
    assert later["level"] == "REAL"


# ── C5: negative controls — insufficient / contradicted evidence ─────────
def test_c5_insufficient_evidence_keeps_withheld(store, pipe):
    result = pipe.ingest(**evidence("synth.alpha", "only_one", error_pct=0.1))
    assert result["level"] == "WITHHELD"
    assert result["outcome"] == "held"
    failures = store.list_history("synth.alpha")[-1]["failures"]
    assert any(f["check"] == "min_matching" for f in failures)


def test_c5_accurate_but_mock_labelled_evidence_keeps_withheld(store, pipe):
    pipe.ingest(**evidence("synth.beta", "mockish", error_pct=0.01, observed_level="MOCK"))
    assert store.get_slot("synth.beta").current_level == "WITHHELD"
    failures = store.list_history("synth.beta")[-1]["failures"]
    assert any(f["check"] == "contradiction_gate" for f in failures)


def test_c5_missing_provenance_hash_keeps_withheld(store, pipe):
    pipe.ingest(**evidence("synth.beta", "nohash", error_pct=0.5, with_sha=False))
    assert store.get_slot("synth.beta").current_level == "WITHHELD"
    failures = store.list_history("synth.beta")[-1]["failures"]
    assert any(f["check"] == "provenance_sha256" for f in failures)


def test_c5_error_above_threshold_keeps_withheld(store, pipe):
    pipe.ingest(**evidence("synth.beta", "toolarge", error_pct=4.2))
    assert store.get_slot("synth.beta").current_level == "WITHHELD"
    failures = store.list_history("synth.beta")[-1]["failures"]
    assert any(f["check"].startswith("metric:error_pct") for f in failures)


def test_c5_withheld_labelled_evidence_does_not_count_as_proof(store, pipe):
    """A record that merely says "not established" must not upgrade the slot."""
    pipe.ingest(**evidence("synth.beta", "insufficient_label", error_pct=0.2,
                           observed_level="WITHHELD"))
    assert store.get_slot("synth.beta").current_level == "WITHHELD"
    failures = store.list_history("synth.beta")[-1]["failures"]
    assert any(f["check"] == "below_target_assertion" for f in failures)


def test_slot_without_criteria_is_reported_unconfigured(store, pipe):
    store.upsert_slot("synth.epsilon", initial_level="WITHHELD")
    result = pipe.ingest(capability_id="synth.epsilon", kind="note", metrics={"x": 1})
    assert result["level"] == "WITHHELD"
    assert result["outcome"] == "unconfigured"
    assert store.list_history("synth.epsilon")[-1]["criteria"] == []


# ── idempotency & chain traceability ────────────────────────────────────
def test_duplicate_ingest_is_idempotent(store, pipe):
    payload = evidence("synth.beta", "dup", error_pct=1.0, source_path="cases/dup.json")
    payload["evidence_id"] = "ev-fixed-dup"
    first = pipe.ingest(**payload)
    second = pipe.ingest(**payload)
    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["rejudged"] is False
    assert store.stats()["evidence"] == 1
    assert len(store.list_events("synth.beta")) == 2  # both ingests are logged


def test_chain_is_fully_traceable(store, pipe):
    pipe.ingest(**evidence("synth.beta", "chain_1", error_pct=1.1))
    chain = pipe.chain("synth.beta")
    assert chain["capability"]["current_level"] == "REAL"
    assert chain["evidence"][0]["source"]["sha256"]
    assert chain["evidence"][0]["digest"]
    assert chain["criteria"][0]["criterion_id"] == "crit.beta.real"
    assert chain["runs"] and chain["history"]
    assert chain["traceability"]["evidence_with_source"] == 1
    assert chain["traceability"]["evidence_used_in_runs"] == 1


# ── registry / API surface ──────────────────────────────────────────────
def test_registry_rejects_criterion_for_unknown_capability(tmp_path):
    bad = {"capabilities": [{"capability_id": "synth.gamma"}],
           "criteria": [{"criterion_id": "c", "capability_id": "synth.missing", "spec": {}}]}
    path = tmp_path / "bad_registry.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_registry(path)


def test_api_end_points_serve_the_pipeline(tmp_path):
    from fastapi.testclient import TestClient

    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    app = create_app(db_path=tmp_path / "api.db", registry_path=reg)
    client = TestClient(app)

    caps = client.get("/capabilities").json()["capabilities"]
    assert {c["capability_id"] for c in caps} == {"synth.alpha", "synth.beta"}

    r = client.post("/evidence", json=evidence("synth.beta", "api_1", error_pct=1.4))
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "REAL" and body["deadline_ok"] is True

    assert client.get("/capabilities/synth.beta/chain").json()["capability"]["current_level"] == "REAL"
    hist = client.get("/capabilities/synth.beta/history").json()["history"]
    assert hist and hist[-1]["after_level"] == "REAL"
    assert client.get("/coverage").json()["withheld_slots"] == 1
    assert client.get("/events").json()["events"]
    assert client.post("/capabilities/synth.beta/rejudge").json()["run"]["result_level"] == "REAL"
    health = client.get("/health").json()
    assert health["status"] == "OK" and health["max_latency_ms"] < 1000.0

    assert client.post("/evidence", json=evidence("synth.unknown", "x")).status_code == 404
    assert client.get("/capabilities/synth.unknown/chain").status_code == 404


def test_router_can_be_mounted_under_a_prefix(store, pipe):
    """The same router mounts into the platform app under a namespaced prefix."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tools.capability_upgrade.api import build_router

    app = FastAPI()
    app.include_router(build_router(pipe, store), prefix="/api/v1/capability-upgrade")
    client = TestClient(app)
    assert client.get("/api/v1/capability-upgrade/capabilities").status_code == 200
    assert client.get("/api/v1/capability-upgrade/coverage").json()["slots"] == 2


# ── C7: zero domain semantics ───────────────────────────────────────────
def test_c7_package_source_contains_no_domain_vocabulary():
    lexicon = json.loads((WORKTREE / "tools" / "evidence_domain_lexicon.json").read_text(
        encoding="utf-8"))
    sources = {
        p.name: p.read_text(encoding="utf-8")
        for p in list(PKG_DIR.glob("*.py")) + list((PKG_DIR / "tests").glob("*.py"))
    }
    ascii_terms = lexicon.get("ascii_terms", [])
    cjk_terms = lexicon.get("cjk_terms", [])
    hits = []
    for name, text in sources.items():
        low = text.lower()
        for term in ascii_terms:
            if re.search(rf"\b{re.escape(term.lower())}\b", low):
                hits.append((name, term))
        for term in cjk_terms:
            if term in text:
                hits.append((name, term))
    assert not hits, f"domain vocabulary found in package source: {hits}"


def test_c7_criteria_and_metrics_are_fully_injected(store, pipe):
    # The engine knows nothing about metric semantics: an arbitrary metric name
    # works exactly the same way once a criterion declares it.
    store.upsert_slot("synth.delta", initial_level="WITHHELD")
    from tools.capability_upgrade import Criterion

    store.upsert_criterion(Criterion(
        criterion_id="crit.delta.real",
        capability_id="synth.delta",
        target_level="REAL",
        spec={"evidence_kinds": ["numeric_verification"], "min_matching": 1,
              "metrics": [{"name": "arbitrary_score", "op": ">=", "value": 0.9}]},
    ))
    result = pipe.ingest(capability_id="synth.delta", kind="numeric_verification",
                         metrics={"arbitrary_score": 0.95})
    assert result["level"] == "REAL"