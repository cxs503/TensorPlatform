"""契约装载面与单位量纲解析的测试（N02 侧地基）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tensorplatform.governance_maturity import contracts as C
from src.tensorplatform.governance_maturity.units import (
    UnitParseError,
    parse_unit,
    same_dimension,
    to_dimension_vector,
)


# --------------------------------------------------------------------------- #
# 契约装载
# --------------------------------------------------------------------------- #
def test_vocab_contracts_load_with_fingerprints(repo_root: Path) -> None:
    refs = C.load_vocab_contracts(repo_root)
    assert set(refs) == set(C.VOCAB_CONTRACTS)
    for ref in refs.values():
        assert isinstance(ref.version, int) and ref.version >= 1
        assert len(ref.sha256) == 64
        assert ref.data.get("contract") == ref.name


def test_vocab_contract_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(C.ContractError):
        C.load_vocab_contracts(tmp_path)


def test_corrupt_contract_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "backend" / "contracts").mkdir(parents=True)
    bad = tmp_path / "backend" / "contracts" / "session_event_kinds.v1.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(C.ContractError):
        C.load_vocab_contracts(tmp_path)


def test_metrics_emission_declaration_shape(repo_root: Path) -> None:
    ref = C.load_metrics_emission(repo_root)
    assert ref.data["event_stream_contract"] == "session_event_kinds"
    tools = ref.data["tools"]
    assert {"pulse", "chronicle", "portrait"} <= set(tools)
    for name, decl in tools.items():
        assert (repo_root / decl["root"]).is_dir(), f"{name} 工具目录不存在"


def test_contract_inventory_covers_all_sources(repo_root: Path) -> None:
    inv = C.contract_inventory(repo_root)
    assert len(inv["vocab_contracts"]) == len(C.VOCAB_CONTRACTS)
    assert inv["metrics_emission"]["name"] == "metrics_emission"
    assert len(inv["coupling_contracts"]) == 9
    assert sum(c["ports"] for c in inv["coupling_contracts"]) == 72
    assert len(inv["coupling_schema_sha256"]) == 64


def test_state_machine_states_are_members_of_status_vocab(repo_root: Path) -> None:
    """状态机的键与目标都必须是状态词表成员（跨契约闭包，diff=0）。"""
    refs = C.load_vocab_contracts(repo_root)
    statuses = set(refs["mission_status_vocab"].data["statuses"])
    machine = refs["mission_state_machine"].data
    transitions = machine.get("transitions") or {}
    assert transitions, "状态机缺 transitions"
    assert set(transitions) <= statuses
    targets = {t for spec in transitions.values() for t in spec.get("to", [])}
    assert targets <= statuses


def test_coupling_contract_version_dir_matches_field(repo_root: Path) -> None:
    for c in C.load_coupling_contracts(repo_root):
        dir_version = int(Path(c.rel_path).parent.name.lstrip("v"))
        assert c.version == dir_version, f"{c.rel_path}: version={c.version} 与目录 {dir_version} 不一致"


# --------------------------------------------------------------------------- #
# 单位 → 量纲
# --------------------------------------------------------------------------- #
def test_parse_engineering_units() -> None:
    kw = parse_unit("kW")
    assert kw.dim == parse_unit("W").dim
    assert kw.scale == pytest.approx(1e3)
    assert parse_unit("kN*m").dim == parse_unit("J").dim
    assert parse_unit("kN").dim == parse_unit("N").dim
    assert parse_unit("t/m^3").dim == parse_unit("kg/m^3").dim
    assert parse_unit("kn").scale == pytest.approx(1852.0 / 3600.0)
    assert parse_unit("rpm").scale == pytest.approx(1.0 / 60.0)
    assert parse_unit("1").dim == parse_unit("deg").dim


def test_parse_rejects_unknown_unit() -> None:
    with pytest.raises(UnitParseError):
        parse_unit("furlong/s")


def test_parse_rejects_double_division() -> None:
    with pytest.raises(UnitParseError):
        parse_unit("kg/s/m")


def test_parse_rejects_empty() -> None:
    with pytest.raises(UnitParseError):
        parse_unit("")


def test_same_dimension_across_scaled_units() -> None:
    assert same_dimension("kN", "N")
    assert not same_dimension("kN", "kN*m")
    dv = to_dimension_vector("kN")
    assert (dv.M, dv.L, dv.T) == (1.0, 1.0, -2.0)