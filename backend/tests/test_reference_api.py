"""Tests for the reference-corpus HTTP surface and the Wave-8 re-adjudication.

Run:  cd backend && python -m pytest tests/test_reference_api.py -q
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.reference_corpus.adjudicate import build_ledger, readjudicate  # noqa: E402
from services.reference_corpus.api import get_corpus, router  # noqa: E402
from services.reference_corpus.corpus import ReferenceCorpus  # noqa: E402

ROOT = BACKEND_DIR / "data" / "reference_corpus"
WAVE8 = ROOT / "wave8_nodes"

STATUSES = {"PASS", "FAIL", "WITHHELD", "PARTIAL", "NO_STATUS", "NO_ARTEFACT"}


@pytest.fixture(scope="module")
def corpus() -> ReferenceCorpus:
    return ReferenceCorpus(ROOT)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    get_corpus.cache_clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_get_entry_returns_anchored_metadata(client):
    r = client.get("/api/v1/reference/ittc1957.cf_friction_line")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "ittc1957.cf_friction_line"
    for key in ("source", "sha256_of_scan", "tolerance", "k", "validity_range", "provenance"):
        assert key in body
    assert len(body["sha256_of_scan"]) == 64
    assert body["dual_pass"]["max_rel_diff"] <= 1e-3


def test_get_entry_unknown_returns_404(client):
    r = client.get("/api/v1/reference/no.such_entry")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_query_endpoint_filters_by_quantity_and_validity(client):
    r = client.get("/api/v1/reference", params={"quantity": "friction_coefficient", "validity": 8.0})
    assert r.status_code == 200
    body = r.json()
    assert body["n"] >= 1
    assert body["entries"][0]["id"] == "ittc1957.cf_friction_line"

    r2 = client.get("/api/v1/reference", params={"quantity": "friction_coefficient", "validity": 42.0})
    assert r2.json()["n"] == 0


def test_evaluate_endpoint_returns_value_and_provenance(client):
    r = client.get("/api/v1/reference/ittc1957.cf_friction_line/evaluate", params={"x": 9.0})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == pytest.approx(0.075 / 49.0, rel=1e-9)
    assert body["provenance"] == "ANALYTIC"
    assert body["scheme"] == "pchip"


def test_evaluate_out_of_range_returns_416(client):
    r = client.get("/api/v1/reference/ittc1957.cf_friction_line/evaluate", params={"x": 12.0})
    assert r.status_code == 416
    detail = r.json()["detail"]
    assert detail["code"] == "OUT_OF_RANGE"
    assert detail["x_max"] == pytest.approx(9.0)


def test_evaluate_scheme_validation(client):
    assert client.get(
        "/api/v1/reference/ittc1957.cf_friction_line/evaluate", params={"x": 8.0, "scheme": "spline"}
    ).status_code == 422
    ok = client.get("/api/v1/reference/ittc1957.cf_friction_line/evaluate", params={"x": 8.0, "scheme": "linear"})
    assert ok.status_code == 200


def test_dualpass_endpoint(client):
    r = client.get("/api/v1/reference/kerr1964.pasternak_peak_factor/dualpass")
    assert r.status_code == 200
    body = r.json()
    assert body["pass"] is True
    assert body["max_rel_diff"] <= 1e-3
    assert body["n_points"] == body["declared_in_entry"]["n_points"]


def test_audit_endpoint(client):
    r = client.get("/api/v1/reference/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_valid"] and body["anchors_ok"] and body["dual_pass_ok"] and body["interp_ok"]


def test_pending_endpoint_reports_withheld_families(client):
    r = client.get("/api/v1/reference/pending")
    assert r.status_code == 200
    fams = r.json()["families"]
    assert len(fams) >= 2
    assert {f["status"] for f in fams} <= {"WITHHELD", "PARTIAL"}
    assert all(f["reason"] for f in fams)


def test_bit_flip_makes_endpoint_refuse_to_serve(client, tmp_path, monkeypatch):
    dest = tmp_path / "corpus"
    shutil.copytree(ROOT, dest)
    entry = json.loads((dest / "entries" / "ittc1957.cf_friction_line.json").read_text())
    artefact = dest / entry["table"]["path"]
    data = bytearray(artefact.read_bytes())
    data[len(data) // 3] ^= 0x01
    artefact.write_bytes(bytes(data))
    monkeypatch.setenv("REFERENCE_CORPUS_ROOT", str(dest))
    get_corpus.cache_clear()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    c = TestClient(app)
    assert c.get("/api/v1/reference/ittc1957.cf_friction_line").status_code == 409
    assert (
        c.get("/api/v1/reference/ittc1957.cf_friction_line/evaluate", params={"x": 8.0}).status_code == 409
    )
    get_corpus.cache_clear()


# ---------------------------------------------------------------------------
# (f) Wave-8 ledger coverage
# ---------------------------------------------------------------------------


def test_wave8_manifest_covers_eight_nodes():
    manifest = json.loads((WAVE8 / "manifest.json").read_text())
    assert len(manifest["nodes"]) == 8
    assert {n["node"] for n in manifest["nodes"]} == {f"N0{k}" for k in range(1, 9)}
    for node in manifest["nodes"]:
        assert node["origin_branch"]
        assert node["origin_head"]
        for f in node["files"]:
            assert f["status"] in ("COPIED", "MISSING")
            if f["status"] == "COPIED":
                assert len(f["sha256"]) == 64


def test_ledger_covers_every_node_and_item(corpus):
    ledger = build_ledger(ROOT, corpus)
    assert ledger["n_nodes"] == 8
    assert {n["node"] for n in ledger["per_node"]} == {f"N0{k}" for k in range(1, 9)}
    assert ledger["n_items"] >= 50
    for item in ledger["items"]:
        assert item["status"] in STATUSES
        assert item["node"].startswith("N0")
        # every non-passing item must carry an explanation
        if item["status"] in ("WITHHELD", "NO_STATUS", "NO_ARTEFACT"):
            assert item["gap_reason"] or item["status"] == "NO_STATUS"


def test_ledger_totals_are_consistent(corpus):
    ledger = build_ledger(ROOT, corpus)
    assert sum(ledger["totals"].values()) == ledger["n_items"]
    per_node_sum = sum(n["n_items"] for n in ledger["per_node"])
    assert per_node_sum == ledger["n_items"]


def test_ledger_marks_corpus_covered_items(corpus):
    ledger = build_ledger(ROOT, corpus)
    covered = [i for i in ledger["items"] if i["corpus_covered"]]
    assert len(covered) >= 3
    for item in covered:
        assert item["corpus_entries"]
        for eid in item["corpus_entries"]:
            assert eid in corpus


def test_ledger_endpoint(client):
    r = client.get("/api/v1/reference/wave8/ledger")
    assert r.status_code == 200
    assert r.json()["n_nodes"] == 8


# ---------------------------------------------------------------------------
# (e) re-adjudication of delivered nodes
# ---------------------------------------------------------------------------


def test_readjudication_touches_at_least_two_nodes(corpus):
    res = readjudicate(corpus, ROOT)
    assert res["n_nodes_readjudicated"] >= 2
    assert res["n_nodes_with_corpus_effect"] >= 2


def test_readjudication_n07_friction_anchor_is_real(corpus):
    res = readjudicate(corpus, ROOT)
    anchor = res["details"]["N07"]["i_friction_anchor"]
    assert anchor["verdict"] == "PASS_REAL"
    assert anchor["rel_diff"] <= anchor["corpus_rel_tolerance"]
    # the node value must equal the closed-form 1957 line at Re=1e9
    assert anchor["corpus_cf"] == pytest.approx(0.075 / 49.0, rel=1e-9)


def test_readjudication_n07_absolute_resistance_is_not_blindly_upgraded(corpus):
    res = readjudicate(corpus, ROOT)
    dp = res["details"]["N07"]["ii_design_point"]
    # smooth-hull ITTC-1957 reference disagrees by ~30%: the criterion must NOT be
    # reported as PASS on the smooth reference alone (honest FAIL discipline)
    assert dp["rel_diff"] > 0.15
    assert dp["verdict"] == "FAIL"


def test_readjudication_n07_roughness_reconciliation(corpus):
    res = readjudicate(corpus, ROOT)
    rec = res["details"]["N07"]["iii_roughness_reconciliation"]
    assert rec["applicable"] is True
    assert rec["rel_err_vs_node"] <= 2e-2
    assert 1e-6 <= rec["assumed_ks_over_L"] <= 1e-4
    assert rec["verdict"] == "PASS_CONDITIONAL"
    assert set(rec["ks_sensitivity"]) >= {"ks_0.25mm", "ks_0.35mm"}


def test_readjudication_n02_frequencies_reanchored(corpus):
    res = readjudicate(corpus, ROOT)
    freqs = res["details"]["N02"]["frequencies"]
    assert freqs["verdict"] == "PASS_REAL"
    assert len(freqs["rows"]) == 3
    for row in freqs["rows"]:
        assert row["rel_error_vs_corpus"] <= 0.03
        assert row["corpus_entry"] in corpus


def test_readjudication_keeps_failures_and_withheld_honest(corpus):
    res = readjudicate(corpus, ROOT)
    still_fail = {g["criterion"] for g in res["details"]["N02"]["still_failed"]}
    assert {"b_resonant_amplitude_lt_15pct", "c_impact_pressure_lt_20pct"} <= still_fail
    n06 = res["details"]["N06"]
    assert n06["items"]
    assert all(i["verdict"] == "STILL_WITHHELD" for i in n06["items"])
    assert "STILL_WITHHELD" in n06["conclusion"]


def test_readjudication_endpoint(client):
    r = client.get("/api/v1/reference/wave8/readjudication")
    assert r.status_code == 200
    body = r.json()
    assert body["n_nodes_readjudicated"] >= 2
    assert body["summary"]["PASS_REAL"] and body["summary"]["STILL_FAIL"]

# ---------------------------------------------------------------------------
# platform capability bit
# ---------------------------------------------------------------------------


def _normalise(path: str) -> str:
    path = path.split("?")[0].replace("/api/v1", "") or "/"
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "reference" and parts[2] not in (
        "audit",
        "pending",
        "wave8",
        "{entry_id}",
    ):
        parts[2] = "{entry_id}"
    return "/".join(parts)


def test_capability_declaration_matches_router():
    cap = json.loads((ROOT / "capability.json").read_text())
    assert cap["capability_id"] == "reference_corpus.digitisation_service"
    assert cap["evidence_grade"] == "REAL"
    declared = {_normalise(ep.split(" ", 1)[1]) for ep in cap["api_endpoints"]}
    served = {r.path for r in router.routes}
    assert declared <= served, declared - served
    assert cap["withheld"] and cap["limits"]
    for rel in cap["artifacts"].values():
        assert (BACKEND_DIR.parent / rel).exists(), rel


def test_capability_evidence_matches_acceptance_record():
    cap = json.loads((ROOT / "capability.json").read_text())
    acc = json.loads((BACKEND_DIR.parent / cap["artifacts"]["acceptance"]).read_text())
    assert acc["all_pass"] is True
    assert acc["criteria"]["a_schema_completeness"]["pass"] is True
    assert acc["criteria"]["b_dual_pass"]["pass"] is True
    assert acc["criteria"]["e_unlock_effect"]["n_nodes_with_corpus_effect"] >= 2
    assert cap["pytest"]["n_tests"] == acc["criteria"]["g_pytest"]["n_tests_collected"]
