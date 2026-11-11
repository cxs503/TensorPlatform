"""场交换接口 × 耦合契约的一致性审计测试（F1–F8 + findings，含负控）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tensorplatform.governance_maturity import contracts as C
from src.tensorplatform.governance_maturity import field_audit as FA


def _mutate_contract(root: Path, rel: str, mutate) -> None:
    path = root / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_audit_is_green_on_repo(repo_root: Path) -> None:
    result = FA.audit_field_exchange(repo_root)
    assert result.total_diff == 0, [i.as_dict() for i in result.items if i.diff]
    assert {i.id for i in result.items} == {
        "F1_schema_conformance", "F2_unit_parse", "F3_edge_dimension_parity",
        "F4_edge_port_resolution", "F5_frame_registry", "F6_envelope_build",
        "F7_version_monotonic", "F8_migration_rename"}
    assert result.contracts_checked["n_contracts"] == 9
    assert result.contracts_checked["ports_total"] == 72


def test_schema_violation_detected(fake_root: Path) -> None:
    """端口缺 unit（schema required）→ F1 必须报 schema 错误。"""
    def mutate(data):
        data["ports"][0].pop("unit", None)

    _mutate_contract(fake_root, "contracts/coupling/v2/resistance.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f1 = next(i for i in result.items if i.id == "F1_schema_conformance")
    assert f1.diff >= 1
    assert "contracts/coupling/v2/resistance.json" in f1.detail["errors"]


def test_unknown_unit_detected(fake_root: Path) -> None:
    def mutate(data):
        data["ports"][0]["unit"] = "furlong"

    _mutate_contract(fake_root, "contracts/coupling/v2/propulsion.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f2 = next(i for i in result.items if i.id == "F2_unit_parse")
    assert f2.diff >= 1


def test_edge_dimension_mismatch_detected(fake_root: Path) -> None:
    """把推进侧进流速度单位从 m/s 改成 m → 该耦合边量纲失配必须被 F3 抓住。"""
    def mutate(data):
        for port in data["ports"]:
            if port["name"] == "inflow_speed":
                port["unit"] = "m"

    _mutate_contract(fake_root, "contracts/coupling/v2/propulsion.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f3 = next(i for i in result.items if i.id == "F3_edge_dimension_parity")
    assert f3.diff >= 1
    assert any(m["unit_to"] == "m" for m in f3.detail["mismatches"])


def test_missing_edge_port_detected(fake_root: Path) -> None:
    def mutate(data):
        data["ports"] = [p for p in data["ports"] if p["name"] != "shaft_power"]

    _mutate_contract(fake_root, "contracts/coupling/v1/green_navigation.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f4 = next(i for i in result.items if i.id == "F4_edge_port_resolution")
    assert f4.diff >= 1


def test_unregistered_frame_detected(fake_root: Path) -> None:
    def mutate(data):
        for port in data["ports"]:
            if port.get("tensor_order", 0) >= 1:
                port["frame"] = "not_a_frame"

    _mutate_contract(fake_root, "contracts/coupling/v2/propulsion.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f5 = next(i for i in result.items if i.id == "F5_frame_registry")
    assert f5.diff >= 1


def test_envelope_build_rejects_unparsable_unit() -> None:
    from src.tensorplatform.governance_maturity.units import UnitParseError

    port = {"name": "p", "role": "input", "unit": "furlong", "tensor_order": 0}
    with pytest.raises(UnitParseError):
        FA.build_envelope(port, FA._grid(2, 2))


def test_conservative_map_stub_finding(repo_root: Path) -> None:
    """`_conservative_map` 是回落 nearest 的 STUB —— 这条 finding 必须一直存在。"""
    result = FA.audit_field_exchange(repo_root)
    stub = next(f for f in result.findings if f.id == "conservative_map_is_stub")
    assert stub.evidence["relative_error"] > 1e-12
    assert stub.evidence["n_src_faces"] == 16
    assert stub.severity in {"HIGH", "MEDIUM"}


def test_upstream_gaps_are_pinned(repo_root: Path) -> None:
    """未发布契约/不可执行迁移的集合被钉住：只能缩小，不能悄悄变大。"""
    result = FA.audit_field_exchange(repo_root, measure_stub=False)
    unpublished = next(f for f in result.findings
                       if f.id == "coupling_map_refs_unpublished_contract")
    assert set(unpublished.evidence["contracts"]) == {"solver.manoeuvring", "solver.seakeeping"}

    migration = next(f for f in result.findings if f.id == "migration_without_v2_file")
    assert set(migration.evidence["contracts"]) == {"solver.seakeeping"}

    f8 = next(i for i in result.items if i.id == "F8_migration_rename")
    assert f8.diff == 0
    assert f8.detail["unexecutable"], "不可执行迁移必须登记在 detail 里，不能静默丢弃"


def test_version_monotonic_violation_detected(fake_root: Path) -> None:
    """把 v2 契约的 version 字段降回 1 → F7 必须报版本不单调。"""
    def mutate(data):
        data["version"] = 1

    _mutate_contract(fake_root, "contracts/coupling/v2/resistance.json", mutate)
    result = FA.audit_field_exchange(fake_root, measure_stub=False)
    f7 = next(i for i in result.items if i.id == "F7_version_monotonic")
    assert f7.diff >= 1
    assert any(v["contract"] == "solver.resistance" for v in f7.detail["violations"])