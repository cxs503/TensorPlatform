#!/usr/bin/env python3
"""补测①（PR #22 初审第 1 点）：端点池全军覆没必须显式失败，
旧单端点（无配置）形态保持兼容。

判据：
  T1 无配置文件 → Gateway 合成 env-default 端点，pick() 命中（旧形态兼容）
  T2 双端点全部冷却 → pick() == (None, {})；native harness _llm 抛 GatewayUnavailable
  T3 双端点 env 缺凭证 → 同上（且绝不回退 env 默认绕过冷却）
  T4 双端点不服务该模型 → 同上
  T5 冷却未到期端点不被选中（冷却不被静默绕过）

运行：tensor_agent venv python（native_harness 依赖 litellm/openhands）。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="gw_unavail_"))
CFG = TMP / "llm_endpoints.json"
STATE = TMP / "gw_state.json"
# 必须在 import llm_gateway 前设置：模块在导入时读取这两个路径
os.environ["LLM_ENDPOINTS_CONFIG"] = str(CFG)
os.environ["LLM_GATEWAY_STATE"] = str(STATE)
os.environ.setdefault("LLM_BASE_URL", "http://legacy-env-default:1/v1")
os.environ.setdefault("LLM_API_KEY", "legacy-key")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS: list[str] = []


def fresh_gateway():
    importlib.reload(llm_gateway)
    return llm_gateway.Gateway()


import llm_gateway  # noqa: E402  (路径注入后再导入)
import native_harness  # noqa: E402

MODEL = "qwen3.7-plus"


def make_harness(gw) -> native_harness.NativeHarness:
    """绕过 __init__（不建会话不读 mission），只装配 _llm 用到的属性。"""
    nh = object.__new__(native_harness.NativeHarness)
    nh.gateway = gw
    nh.model = MODEL
    nh.bare_model = MODEL
    nh.messages = []
    nh.tools = []
    return nh


def two_endpoint_cfg() -> dict:
    return {"endpoints": [
        {"name": "ep-a", "base_url": "http://a:1/v1", "api_key_env": "GW_A_KEY",
         "models": [MODEL]},
        {"name": "ep-b", "base_url": "http://b:1/v1", "api_key_env": "GW_B_KEY",
         "models": [MODEL]},
    ], "cooldown_seconds": 600}


# T1 旧形态：无配置 → env-default 合成端点可用
CFG.unlink(missing_ok=True)
STATE.unlink(missing_ok=True)
gw = fresh_gateway()
ep, params = gw.pick(MODEL)
assert ep is not None and ep.name == "env-default" and params["base_url"], \
    f"T1 FAIL: {ep} {params}"
PASS.append("T1 无配置→env-default 合成端点命中（旧单端点兼容）")

# T2 全部冷却 → (None, {}) + native _llm 显式抛 GatewayUnavailable
CFG.write_text(json.dumps(two_endpoint_cfg()), encoding="utf-8")
os.environ["GW_A_KEY"] = os.environ["GW_B_KEY"] = "k"
STATE.write_text(json.dumps({
    "ep-a": {"blocked_until": time.time() + 300, "reason": "quota"},
    "ep-b": {"blocked_until": time.time() + 300, "reason": "quota"},
}), encoding="utf-8")
gw = fresh_gateway()
ep, params = gw.pick(MODEL)
assert ep is None and params == {}, f"T2a FAIL: {ep} {params}"
try:
    make_harness(gw)._llm()
    raise AssertionError("T2b FAIL: _llm 未在无端点时显式失败")
except llm_gateway.GatewayUnavailable:
    pass
PASS.append("T2 双端点全冷却→pick=(None,{{}})；_llm 抛 GatewayUnavailable")

# T3 缺凭证 → 不可用（不回退 env 默认 = 不绕过冷却）
STATE.unlink(missing_ok=True)
os.environ.pop("GW_A_KEY", None)
os.environ.pop("GW_B_KEY", None)
gw = fresh_gateway()
ep, params = gw.pick(MODEL)
assert ep is None and params == {}, f"T3a FAIL: {ep} {params}"
try:
    make_harness(gw)._llm()
    raise AssertionError("T3b FAIL: 缺凭证时未显式失败（可能回退 env 默认）")
except llm_gateway.GatewayUnavailable:
    pass
PASS.append("T3 双端点 env 缺凭证→(None,{{}})；_llm 抛 GatewayUnavailable")

# T4 不服务该模型 → 不可用
CFG.write_text(json.dumps({"endpoints": [
    {"name": "ep-a", "base_url": "http://a:1/v1", "api_key_env": "GW_A_KEY",
     "models": ["other-model"]},
]}), encoding="utf-8")
os.environ["GW_A_KEY"] = "k"
gw = fresh_gateway()
ep, params = gw.pick(MODEL)
assert ep is None and params == {}, f"T4a FAIL: {ep} {params}"
try:
    make_harness(gw)._llm()
    raise AssertionError("T4b FAIL: 模型不服务时未显式失败")
except llm_gateway.GatewayUnavailable:
    pass
PASS.append("T4 端点不服务该模型→(None,{{}})；_llm 抛 GatewayUnavailable")

# T5 冷却端点不被选中：ep-a 冷却、ep-b 健康 → 只命中 ep-b
CFG.write_text(json.dumps(two_endpoint_cfg()), encoding="utf-8")
os.environ["GW_A_KEY"] = os.environ["GW_B_KEY"] = "k"
STATE.write_text(json.dumps({
    "ep-a": {"blocked_until": time.time() + 300, "reason": "quota"},
}), encoding="utf-8")
gw = fresh_gateway()
ep, params = gw.pick(MODEL)
assert ep is not None and ep.name == "ep-b", f"T5 FAIL: {ep}"
PASS.append("T5 冷却中端点不被选中（轮转到健康端点 ep-b）")

print("\n".join(f"PASS {x}" for x in PASS))
print(f"OK {len(PASS)}/5")
