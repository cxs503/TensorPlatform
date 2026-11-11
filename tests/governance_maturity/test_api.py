"""治理成熟化 API 测试（端点可达 + 非法输入 422 + 报告工件读取）。"""
from __future__ import annotations

from pathlib import Path

import pytest

BASE = "/api/v1/governance/maturity"
ROOT = Path(__file__).resolve().parents[2]


def test_contracts_endpoint(api_client) -> None:
    resp = api_client.get(f"{BASE}/contracts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    inv = body["inventory"]
    assert len(inv["coupling_contracts"]) == 9
    assert inv["vocab_contracts"][0]["sha256"]


def test_audit_endpoint(api_client) -> None:
    resp = api_client.post(f"{BASE}/audit", json={"scope": ["metrics", "field"], "runtime": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics"]["total_diff"] == 0
    assert body["field"]["total_diff"] == 0
    assert body["total_diff"] == 0
    assert body["status"] == "PASS"


def test_audit_single_scope(api_client) -> None:
    resp = api_client.post(f"{BASE}/audit", json={"scope": ["field"], "runtime": False})
    assert resp.status_code == 200
    assert "metrics" not in resp.json()


def test_audit_unknown_scope_422(api_client) -> None:
    resp = api_client.post(f"{BASE}/audit", json={"scope": ["bogus"]})
    assert resp.status_code == 422


def test_audit_empty_scope_422(api_client) -> None:
    resp = api_client.post(f"{BASE}/audit", json={"scope": []})
    assert resp.status_code == 422


def test_e2e_run_endpoint(api_client) -> None:
    resp = api_client.post(f"{BASE}/e2e-run",
                           json={"num_agents": 4, "ticks": 30, "exchange_every": 10,
                                 "mission_every": 10})
    assert resp.status_code == 200
    run = resp.json()["run"]
    assert run["loss"]["loss_rate"] == 0.0
    assert run["acceptance"]["all_pass"] is True


@pytest.mark.parametrize("payload", [
    {"num_agents": 0},
    {"ticks": 0},
    {"drop_rate": 0.9},
    {"num_agents": 500, "ticks": 5000},
    {"exchange_every": 0},
])
def test_e2e_invalid_inputs_422(api_client, payload) -> None:
    resp = api_client.post(f"{BASE}/e2e-run", json=payload)
    assert resp.status_code == 422


def test_report_endpoint_matches_artifact(api_client) -> None:
    path = ROOT / "artifacts" / "governance-maturity" / "acceptance.json"
    resp = api_client.get(f"{BASE}/report")
    if path.is_file():
        assert resp.status_code == 200
        body = resp.json()
        assert body["report"]["mission"] == "p465w1-n07"
        assert "criteria" in body["report"]
    else:  # 工件尚未生成时端点必须显式 404，而不是返回空壳
        assert resp.status_code == 404
        assert "REPORT_NOT_FOUND" in resp.json()["detail"]

def test_library_module_coexists_with_integration_api() -> None:
    """回归护栏：集成 API 不得覆盖 m8339 的治理成熟化**库**模块。

    教训：两者曾同名（api/governance_maturity.py），新建 API 时若直接写同名文件，
    会把报表门/证据链/能力台账/自愈库整个删掉且测试全绿（因为没人 import 它）。
    """
    import api.governance_maturity as lib
    import api.governance_maturity_integration as api

    assert hasattr(lib, "check_report_completeness")
    assert hasattr(lib, "ProvenanceChain")
    assert hasattr(lib, "CapabilityLedger")
    assert api is not lib
    assert api.router is not None
