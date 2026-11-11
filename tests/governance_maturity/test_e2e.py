"""端到端治理链路测试（丢失率 / 画像一致性 / 延迟 / 场交换 / 确定性）。"""
from __future__ import annotations

import pytest

from src.tensorplatform.governance_maturity import e2e

SMALL = dict(num_agents=6, ticks=60, exchange_every=10, mission_every=10)


@pytest.fixture(scope="module")
def nominal():
    return e2e.run_governance_chain(e2e.E2EConfig(**SMALL)).as_dict()


@pytest.fixture(scope="module")
def lossy():
    return e2e.run_governance_chain(
        e2e.E2EConfig(drop_rate=0.05, **SMALL)).as_dict()


def test_nominal_run_has_zero_loss(nominal) -> None:
    loss = nominal["loss"]
    assert loss["expected"] == nominal["events"]["emitted"]
    assert loss["missing"] == 0
    assert loss["loss_rate"] == 0.0
    assert loss["duplicates"] == 0
    assert nominal["events"]["heartbeats"] == SMALL["num_agents"] * SMALL["ticks"]


def test_injected_loss_is_detected(lossy) -> None:
    """正对照：注入 5% 丢包，检测器必须报出同量级丢失。"""
    loss = lossy["loss"]
    assert loss["missing"] > 0
    assert 0.02 <= loss["loss_rate"] <= 0.10
    assert loss["agents_with_loss"] > 0


def test_portrait_consistency_within_band(nominal, lossy) -> None:
    assert nominal["portrait"]["consistency_error"] == 0.0
    assert nominal["portrait"]["consistency_error"] < 0.05
    # 丢包必须让画像误差变大（否则"<5%"这条判据没有判别力）
    assert lossy["portrait"]["consistency_error"] > nominal["portrait"]["consistency_error"]


def test_portrait_tool_parity(nominal) -> None:
    tool = nominal["portrait"]["tool"]
    assert tool["available"] is True
    assert tool["mismatched_workers"] == 0
    assert tool["max_count_delta"] == 0
    assert tool["coverage"]["workers_active"] == SMALL["num_agents"]


def test_pulse_runtime_parity(nominal) -> None:
    pulse = nominal["pulse"]
    assert pulse["available"] is True
    assert pulse["online_parity"] is True
    assert pulse["busy_parity"] is True
    assert pulse["running_parity"] is True


def test_kind_contract_enforced(nominal) -> None:
    kc = nominal["kind_contract"]
    assert kc["checked"] == nominal["events"]["delivered"]
    assert kc["violations"] == 0
    assert kc["outside_governance_members"] == 0


def test_field_exchange_in_loop(nominal) -> None:
    fx = nominal["field_exchange"]
    assert fx["exchanges"] == SMALL["ticks"] // SMALL["exchange_every"]
    assert fx["contract_violations"] == 0
    assert fx["identity_integral_error"] == 0.0
    assert fx["checks_performed"] == fx["exchanges"]
    assert fx["edges_available"] >= 1


def test_latency_under_budget(nominal) -> None:
    lat = nominal["latency"]
    assert lat["n_ticks"] == SMALL["ticks"]
    assert lat["p99_s"] < 5.0
    assert 0.0 <= lat["p50_ms"] <= lat["p99_ms"] <= lat["max_ms"]


def test_acceptance_flags(nominal) -> None:
    acc = nominal["acceptance"]
    assert acc["c_pass"] is True
    assert acc["d_pass"] is True
    assert acc["all_pass"] is True


def test_determinism_same_seed() -> None:
    a = e2e.run_governance_chain(e2e.E2EConfig(**SMALL), run_metric_tools=False).as_dict()
    b = e2e.run_governance_chain(e2e.E2EConfig(**SMALL), run_metric_tools=False).as_dict()
    assert a["loss"] == b["loss"]
    assert a["portrait"]["per_field"] == b["portrait"]["per_field"]
    assert a["events"] == b["events"]


def test_config_validation_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        e2e.E2EConfig(drop_rate=0.9).validate()
    with pytest.raises(ValueError):
        e2e.E2EConfig(num_agents=0).validate()
    with pytest.raises(ValueError):
        e2e.E2EConfig(ticks=0).validate()


def test_sweep_is_monotone_in_loss() -> None:
    curve = e2e.sweep_drop_rates([0.0, 0.05], num_agents=4, ticks=40)
    assert curve[0]["detected_loss_rate"] == 0.0
    assert curve[1]["detected_loss_rate"] > 0.0
    assert 0.5 <= curve[1]["detection_ratio"] <= 1.5