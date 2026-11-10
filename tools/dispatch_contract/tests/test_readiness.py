"""就绪门核心语义测试：fail-closed / 降级恒等放行 / 口径与版本 / 不崩。

全部夹具用合成节点名与中性量纲（无任何领域词条）。
"""

from __future__ import annotations

import json

import pytest

from tools.dispatch_contract.contract_schema import (
    canonical_unit,
    load_config,
    units_match,
    validate_contract,
    version_compatible,
)
from tools.dispatch_contract.readiness import VERDICT_BLOCK, VERDICT_READY, assess

CFG = load_config()


def _contract(input_reqs, output_reqs=None):
    return {
        "node_id": "node-1",
        "contract_version": "dispatch-contract/v1",
        "input_contract": input_reqs,
        "output_contract": output_reqs or [],
    }


def test_missing_contract_degrades_to_identity_pass():
    """AC5：缺契约 → 恒等放行、不崩、带降级标记。"""
    res = assess({"node_id": "node-x", "contract": None}, CFG)
    assert res["verdict"] == VERDICT_READY
    assert res["degraded"] is True
    assert res["degrade_reason"] == "CONTRACT_MISSING"
    assert res["unmet"] == []


def test_satisfied_upstream_is_ready():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1001", "required": True}])
    dispatch = {"node_id": "node-1", "contract": contract,
                "upstream": {"1001": {"found": True, "status": "succeeded",
                                      "delivered_before_dispatch": True}}}
    res = assess(dispatch, CFG)
    assert res["verdict"] == VERDICT_READY
    assert res["degraded"] is False
    assert res["score"] == 0.0


def test_failed_upstream_blocks_fail_closed():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1002", "required": True}])
    dispatch = {"node_id": "node-1", "contract": contract,
                "upstream": {"1002": {"found": True, "status": "failed",
                                      "delivered_before_dispatch": True}}}
    res = assess(dispatch, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert any(u["code"] == "UPSTREAM_STATUS_NOT_CONFORMANT" for u in res["unmet"])
    assert res["score"] == 1.0


def test_late_upstream_delivery_blocks():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1003", "required": True}])
    dispatch = {"node_id": "node-1", "contract": contract,
                "upstream": {"1003": {"found": True, "status": "succeeded",
                                      "delivered_before_dispatch": False}}}
    res = assess(dispatch, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert any(u["code"] == "UPSTREAM_AFTER_DISPATCH" for u in res["unmet"])


def test_missing_upstream_blocks():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1004", "required": True}])
    res = assess({"node_id": "n", "contract": contract, "upstream": {"1004": {"found": False}}}, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert res["unmet"][0]["code"] == "UPSTREAM_NOT_FOUND"


def test_artifact_missing_blocks():
    contract = _contract([{"kind": "artifact", "ref": "artifacts/a.json", "required": True}])
    res = assess({"node_id": "n", "contract": contract,
                  "upstream": {"artifacts/a.json": {"found": False}}}, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert res["unmet"][0]["code"] == "ARTIFACT_NOT_FOUND"


def test_optional_requirement_does_not_block():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1005", "required": False}])
    res = assess({"node_id": "n", "contract": contract, "upstream": {"1005": {"found": False}}}, CFG)
    assert res["verdict"] == VERDICT_READY


def test_unit_mismatch_blocks():
    """口径不符：声明 m/s，上游给 deg —— 必须拦下。"""
    contract = _contract([{"kind": "schema", "ref": "artifact-x", "required": True,
                           "fields": ["v"], "units": {"v": "m/s"}}])
    dispatch = {"node_id": "n", "contract": contract,
                "upstream": {"artifact-x": {"found": True, "status": "succeeded",
                                            "payload": {"v": {"value": 1.0, "unit": "deg"}}}}}
    res = assess(dispatch, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert any(u["code"] == "UNIT_MISMATCH:v" for u in res["unmet"])


def test_unit_alias_is_accepted():
    contract = _contract([{"kind": "schema", "ref": "artifact-x", "required": True,
                           "fields": ["v"], "units": {"v": "m/s"}}])
    dispatch = {"node_id": "n", "contract": contract,
                "upstream": {"artifact-x": {"found": True, "status": "succeeded",
                                            "payload": {"v": {"value": 1.0, "unit": "m s^-1"}}}}}
    assert assess(dispatch, CFG)["verdict"] == VERDICT_READY


def test_missing_field_blocks():
    contract = _contract([{"kind": "schema", "ref": "a", "required": True, "fields": ["f1", "f2"]}])
    dispatch = {"node_id": "n", "contract": contract,
                "upstream": {"a": {"found": True, "status": "succeeded", "payload": {"f1": 1}}}}
    res = assess(dispatch, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert any(u["code"] == "FIELD_MISSING:f2" for u in res["unmet"])


def test_payload_missing_blocks_when_fields_required():
    contract = _contract([{"kind": "schema", "ref": "a", "required": True, "fields": ["f1"]}])
    dispatch = {"node_id": "n", "contract": contract,
                "upstream": {"a": {"found": True, "status": "succeeded", "payload": None}}}
    assert any(u["code"] == "PAYLOAD_MISSING" for u in assess(dispatch, CFG)["unmet"])


def test_version_incompatible_blocks():
    contract = _contract([{"kind": "artifact", "ref": "artifacts/a.json", "required": True,
                           "version": {"major": 2, "min_minor": 1}}])
    dispatch = {"node_id": "n", "contract": contract,
                "upstream": {"artifacts/a.json": {"found": True, "status": "succeeded",
                                                  "payload": {"version": "2.0.7"}}}}
    assert any(u["code"] == "VERSION_INCOMPATIBLE" for u in assess(dispatch, CFG)["unmet"])


def test_invalid_contract_blocks_with_schema_codes():
    contract = {"node_id": "n", "contract_version": "dispatch-contract/v1",
                "input_contract": [{"kind": "bogus", "ref": "x"}], "output_contract": []}
    res = assess({"node_id": "n", "contract": contract}, CFG)
    assert res["verdict"] == VERDICT_BLOCK
    assert any("REQ_UNKNOWN_KIND" in u["code"] for u in res["unmet"])


def test_contract_missing_required_key_blocks():
    contract = {"node_id": "n", "input_contract": [], "output_contract": []}
    ok, errs = validate_contract(contract, CFG)
    assert ok is False and any("CONTRACT_MISSING_KEY" in e for e in errs)


def test_assess_never_raises_on_garbage():
    for junk in (None, 42, "text", [], {"contract": 7}, {"contract": {"input_contract": 5}}):
        res = assess(junk, CFG)  # 不得抛异常
        assert res["verdict"] in (VERDICT_READY, VERDICT_BLOCK)
        assert "degraded" in res


def test_empty_required_set_degrades():
    contract = _contract([{"kind": "upstream_delivery", "ref": "1", "required": False}])
    res = assess({"node_id": "n", "contract": contract}, CFG)
    assert res["degraded"] is True
    assert res["degrade_reason"] == "NO_REQUIRED_INPUT"


def test_unit_and_version_helpers():
    assert canonical_unit("m s^-1", CFG) == "m/s"
    assert canonical_unit("unknown-unit", CFG) is None
    assert units_match("m/s", "ms-1", CFG) is True
    assert units_match("m/s", "deg", CFG) is False
    assert version_compatible({"major": 1, "min_minor": 2}, "1.2.9", CFG) is True
    assert version_compatible({"major": 1, "min_minor": 2}, "1.1.9", CFG) is False
    assert version_compatible({"major": 1, "min_minor": 0}, "2.0.0", CFG) is False
    assert version_compatible(None, None, CFG) is True


def test_json_roundtrip_contract_is_stable():
    """判定结果可序列化（供派单链路与审计落盘）。"""
    res = assess({"node_id": "n", "contract": None}, CFG)
    assert json.loads(json.dumps(res))["schema"] == "dispatch-contract/readiness/v1"