"""度量工具 × 事件流契约的联合审计测试（M1–M8，含负控）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tensorplatform.governance_maturity import contracts as C
from src.tensorplatform.governance_maturity import metrics_audit as MA


def _rewrite_emission(root: Path, mutate) -> None:
    path = root / C.METRICS_EMISSION_CONTRACT
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_audit_is_green_on_repo(repo_root: Path) -> None:
    result = MA.audit_metrics_contract(repo_root)
    assert result.total_diff == 0, [i.as_dict() for i in result.items if i.diff]
    assert {i.id for i in result.items} == {
        "M1_declared_tools_exist", "M2_event_kinds_in_contract", "M3_event_kinds_scan_parity",
        "M4_internal_kinds_parity", "M5_payload_subtypes_parity", "M6_table_surface_parity",
        "M7_pulse_runtime_payload", "M8_governance_membership"}
    assert result.contract_versions["session_event_kinds"] >= 1


def test_portrait_event_kind_is_a_contract_member(repo_root: Path) -> None:
    refs = C.load_vocab_contracts(repo_root)
    kinds = set(refs["session_event_kinds"].data["kinds"])
    governance = set(refs["session_event_kinds"].data["governance_members"])
    scan = MA.scan_tool_source(repo_root, "tools/worker_portrait", sorted(kinds))
    assert scan["event_kinds"], "portrait 应至少触及一个会话事件种类"
    assert set(scan["event_kinds"]) <= kinds
    assert set(scan["event_kinds"]) <= governance


@pytest.mark.parametrize("bogus", ["heartbeat_started", "mission_claimed"])
def test_non_contract_kind_detected(fake_root: Path, bogus: str) -> None:
    """在声明里塞一个契约外的 kind → M2 必须报 diff>0。"""
    def mutate(data):
        data["tools"]["portrait"]["event_kinds"] = [bogus]

    _rewrite_emission(fake_root, mutate)
    result = MA.audit_metrics_contract(fake_root, runtime=False)
    m2 = next(i for i in result.items if i.id == "M2_event_kinds_in_contract")
    assert m2.diff >= 1
    assert bogus in m2.detail["non_contract"]
    assert result.total_diff > 0


def test_declaration_drift_detected(fake_root: Path) -> None:
    """声明漏掉源码里真实存在的 kind → M3 双向 diff 必须变红。"""
    def mutate(data):
        data["tools"]["portrait"]["event_kinds"] = []

    _rewrite_emission(fake_root, mutate)
    result = MA.audit_metrics_contract(fake_root, runtime=False)
    m3 = next(i for i in result.items if i.id == "M3_event_kinds_scan_parity")
    assert m3.diff >= 1
    assert "note" in m3.detail["portrait"]["diff"]


def test_internal_kind_namespace_drift_detected(fake_root: Path) -> None:
    """chronicle 报表 token 命名空间漏声明一项 → M4 变红。"""
    def mutate(data):
        kinds = data["tools"]["chronicle"]["internal_kinds"]
        data["tools"]["chronicle"]["internal_kinds"] = [k for k in kinds if k != "figure"]

    _rewrite_emission(fake_root, mutate)
    result = MA.audit_metrics_contract(fake_root, runtime=False)
    m4 = next(i for i in result.items if i.id == "M4_internal_kinds_parity")
    assert "figure" in m4.detail["chronicle"]["diff"]


def test_governance_membership_violation_detected(fake_root: Path) -> None:
    """治理消费者声明了非 governance_members 的事件种类 → M8 变红。"""
    refs = C.load_vocab_contracts(fake_root)
    contract = refs["session_event_kinds"].data
    outside = [k for k in contract["kinds"] if k not in set(contract["governance_members"])]
    assert outside, "契约里应存在非治理成员事件种类，否则本负控无意义"

    def mutate(data):
        data["tools"]["portrait"]["event_kinds"] = [outside[0]]

    _rewrite_emission(fake_root, mutate)
    result = MA.audit_metrics_contract(fake_root, runtime=False)
    m8 = next(i for i in result.items if i.id == "M8_governance_membership")
    assert m8.diff >= 1
    assert outside[0] in m8.detail["portrait"]["outside_governance"]


def test_pulse_runtime_payload_keys(repo_root: Path) -> None:
    surface = MA.pulse_runtime_surface(repo_root)
    assert surface["available"] is True
    assert {"online", "busy", "held", "queued", "idle", "v"} <= set(surface["payload_keys"])
    assert surface["online"] >= surface["fresh_workers"] >= 1
    decl = C.load_metrics_emission(repo_root).data["tools"]["pulse"]
    assert sorted(surface["payload_keys"]) == sorted(decl["runtime_payload_keys"])


def test_portrait_runtime_surface(repo_root: Path) -> None:
    surface = MA.portrait_runtime_surface(repo_root)
    assert surface["available"] is True
    assert surface["n_missions"] == 3
    assert {"worker", "n_missions", "success_rate", "wall_s"} <= set(surface["profile_keys"])


def test_bootstrap_declaration_round_trip(repo_root: Path, fake_root: Path) -> None:
    """用现状生成的声明写回副本仓库后，审计应回到 diff=0（自洽性）。"""
    fresh = MA.bootstrap_declaration(fake_root)
    fresh["description"] = json.loads(
        (fake_root / C.METRICS_EMISSION_CONTRACT).read_text(encoding="utf-8"))["description"]
    fresh["scope"] = json.loads(
        (fake_root / C.METRICS_EMISSION_CONTRACT).read_text(encoding="utf-8"))["scope"]
    (fake_root / C.METRICS_EMISSION_CONTRACT).write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
    result = MA.audit_metrics_contract(fake_root, runtime=False)
    assert result.total_diff == 0, [i.as_dict() for i in result.items if i.diff]