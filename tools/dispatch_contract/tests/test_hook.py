"""派单挂钩测试：三模式 / 永不抛异常 / BLOCK 只在 enforce 下拦。"""

from __future__ import annotations

from tools.dispatch_contract.contract_schema import load_config
from tools.dispatch_contract.dispatch_hook import pre_dispatch

CFG = load_config()

BLOCKING_CONTRACT = {
    "node_id": "n", "contract_version": "dispatch-contract/v1",
    "input_contract": [{"kind": "upstream_delivery", "ref": "7001", "required": True}],
    "output_contract": [],
}
UPSTREAM_BAD = {"7001": {"found": True, "status": "failed", "delivered_before_dispatch": True}}
UPSTREAM_OK = {"7001": {"found": True, "status": "succeeded", "delivered_before_dispatch": True}}


def test_report_mode_does_not_block():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=UPSTREAM_BAD,
                       mode="report", config=CFG)
    assert res["verdict"] == "BLOCK"
    assert res["blocked"] is False and res["enforced"] is False
    assert res["reason_codes"] == ["UPSTREAM_STATUS_NOT_CONFORMANT"]


def test_enforce_mode_blocks():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=UPSTREAM_BAD,
                       mode="enforce", config=CFG)
    assert res["blocked"] is True


def test_enforce_mode_passes_ready_node():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=UPSTREAM_OK,
                       mode="enforce", config=CFG)
    assert res["blocked"] is False


def test_off_mode_skips_computation():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=UPSTREAM_BAD,
                       mode="off", config=CFG)
    assert res["verdict"] == "READY" and res["degraded"] is True


def test_missing_contract_degrades_not_block():
    res = pre_dispatch(node_id="n", contract=None, upstream={}, mode="enforce", config=CFG)
    assert res["blocked"] is False and res["degraded"] is True


def test_hook_never_raises_on_bad_config():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=UPSTREAM_BAD,
                       mode="enforce", config={"gate": "not-a-mapping"})
    assert res["verdict"] in ("READY", "BLOCK")
    assert res["schema"] == "dispatch-contract/dispatch_hook/v1"


def test_hook_never_raises_on_malformed_upstream():
    res = pre_dispatch(node_id="n", contract=BLOCKING_CONTRACT, upstream=[1, 2, 3],
                       mode="enforce", config=CFG)
    assert "verdict" in res and res["blocked"] in (True, False)