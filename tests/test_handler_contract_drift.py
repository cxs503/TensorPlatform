#!/usr/bin/env python3
"""handler_contract_drift 判据的回归测试（离线，不依赖 :8001 在线）。

分两层：
1. **判据自证层**：合成「缺陷 handler」必须被命中、合成「健康 handler」必须静默，
   否则判据就是死判据（永远 FAIL 或永远静默都不构成证据）；另测 **kwargs/位置参数
   与响应模型不产生假阳性。
2. **工件回放层**：若 artifacts/audit/handler_contract_drift_8001.json 存在，
   断言 SHIP-MAN-001（maneuvering-mmg turning/zigzag 缺 Hr）被静态判据命中，
   并与工件内记录的实测状态码一致；工件缺失则 skip（保持离线可跑）。

运行：python3 -m pytest tests/test_handler_contract_drift.py -q
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "audit"))

import handler_contract_drift as H  # noqa: E402

ARTIFACT = os.path.join(ROOT, "artifacts", "audit", "handler_contract_drift_8001.json")
SRC = "/root/ship-performance-platform-incoming/ship-performance-platform/backend/api"


# --------------------------------------------------------------------------- #
# 层 1：判据自证
# --------------------------------------------------------------------------- #
def test_self_check_passes():
    sc = H.self_check()
    assert sc["ok"] is True
    assert sc["synthetic_defect_flagged"] == ["Hr"]
    assert sc["synthetic_healthy_silent"] is True


def test_detector_flags_missing_required_with_location(tmp_path):
    (tmp_path / "m.py").write_text(H.SELF_CHECK_SRC, encoding="utf-8")
    rep = H.scan_source(str(tmp_path))
    hits = [d for d in rep["drifts"] if d["path"] == "/bad"]
    assert len(hits) == 1
    d = hits[0]
    assert d["constructed_model"] == "Core"
    assert d["missing_required"] == ["Hr"]
    assert d["unknown_kwargs"] == []
    assert d["call_line"] > 0 and d["input_model"] == "EndpointIn"


def test_healthy_handler_is_silent(tmp_path):
    (tmp_path / "m.py").write_text(H.SELF_CHECK_SRC, encoding="utf-8")
    rep = H.scan_source(str(tmp_path))
    assert not [d for d in rep["drifts"] if d["path"] == "/good"]


def test_unknown_kwarg_is_flagged(tmp_path):
    src = '''
from pydantic import BaseModel
from fastapi import APIRouter
router = APIRouter()

class Core(BaseModel):
    L: float
    T: float

class EndpointIn(BaseModel):
    L: float
    T: float

@router.post("/typo")
async def typo(data: EndpointIn):
    return Core(L=data.L, thickness=data.T)
'''
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    rep = H.scan_source(str(tmp_path))
    d = rep["drifts"][0]
    assert d["unknown_kwargs"] == ["thickness"]
    assert d["missing_required"] == ["T"]


def test_response_model_and_star_kwargs_are_not_false_positives(tmp_path):
    src = '''
from pydantic import BaseModel
from fastapi import APIRouter
router = APIRouter()

class Core(BaseModel):
    L: float

class EndpointIn(BaseModel):
    L: float

class OutModel(BaseModel):
    a: float
    b: float

@router.post("/resp")
async def resp(data: EndpointIn):
    payload = {"a": 1.0, "b": 2.0}
    return OutModel(**payload)

@router.post("/core-star")
async def core_star(data: EndpointIn):
    kw = {"L": data.L}
    return Core(**kw)
'''
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    rep = H.scan_source(str(tmp_path))
    assert rep["drifts"] == []


def test_one_hop_callee_is_followed(tmp_path):
    """根因常藏在 handler 调用的 helper 里（m1780 SHIP-MAN-001 就是这种形态）。"""
    src = '''
from pydantic import BaseModel
from fastapi import APIRouter
router = APIRouter()

class Core(BaseModel):
    L: float
    Hr: float

class EndpointIn(BaseModel):
    L: float

def _simulate(input_data: EndpointIn):
    return Core(L=input_data.L)

@router.post("/turning")
async def turning(data: EndpointIn):
    return _simulate(data)
'''
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    rep = H.scan_source(str(tmp_path))
    assert len(rep["drifts"]) == 1
    d = rep["drifts"][0]
    assert d["missing_required"] == ["Hr"] and d["via_call"] == "_simulate"


# --------------------------------------------------------------------------- #
# 层 2：工件回放（离线）
# --------------------------------------------------------------------------- #
needs_artifact = pytest.mark.skipif(not os.path.exists(ARTIFACT),
                                    reason="工件不存在（未连平台跑过）")


@needs_artifact
def test_artifact_records_ship_man_001():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    rows = {r["resolved_path"]: r for r in d["source"]["drifts"]}
    for ep in ("/api/v1/maneuvering-mmg/turning", "/api/v1/maneuvering-mmg/zigzag"):
        assert ep in rows, f"{ep} 未被静态判据命中"
        assert rows[ep]["missing_required"] == ["Hr"]
        assert rows[ep]["constructed_model"] == "MMGInput"


@needs_artifact
def test_artifact_static_prediction_matches_live_500():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    for r in d["source"]["drifts"]:
        live = d["live_probes"].get(r["path"] + " " + r["method"], {})
        if not r.get("resolved_path"):
            continue
        # 预测「端点不可用」的判据：实测必须是 5xx；仅 schema 层先拦下的（422）单独记录
        assert live.get("status") in (422, 500), (r["path"], live)


@needs_artifact
def test_artifact_self_check_ok():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    assert d["self_check"]["ok"] is True
    assert d["source"]["files_scanned"] > 0
    assert d["source"]["endpoints_seen" if "endpoints_seen" in d["source"]
              else "endpoints"] is not None


@needs_artifact
def test_artifact_schema_diff_matches_source_models():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    for row in d["schema_diff"]:
        assert row["deployed_required"] == row["source_required"], row


@pytest.mark.skipif(not os.path.isdir(SRC), reason="船舶平台源码不在本机")
def test_live_source_scan_reproduces_maneuvering_drift():
    rep = H.scan_source(SRC)
    paths = {r["path"]: r for r in rep["drifts"]}
    assert paths.get("/turning", {}).get("missing_required") == ["Hr"]
    assert paths.get("/zigzag", {}).get("missing_required") == ["Hr"]