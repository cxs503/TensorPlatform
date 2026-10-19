"""llm_gateway 故障注入验收（2026-10-19，owner 架构原则第 4 阶段判据）。

判据不是"备用 API 能调通"，而是：首选端点恒返 429（额度耗尽模拟）时，
同一 NativeHarness 实例（同一 Session）自动轮转到备用端点完成推理；
冷却状态跨 Gateway 实例持久；消息历史 checksum 往返一致。
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
import tempfile
import threading
import time

faulthandler.dump_traceback_later(240, exit=True)   # 卡死自诊断：4 分钟 dump 后退出
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

A_PORT, B_PORT = 18123, 18124
STATE = {"b_hits": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        if self.server.server_port == A_PORT:
            body = json.dumps({"error": {"message": "Insufficient Balance -- "
                                    "quota exhausted", "code": 429}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # 端点 B：合法 OpenAI 流式响应
        STATE["b_hits"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("底座", "B", "在线"):
            chunk = {"choices": [{"delta": {"content": piece}, "index": 0}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gw_fault_"))
    os.environ["LLM_ENDPOINTS_CONFIG"] = str(tmp / "llm_endpoints.json")
    os.environ["LLM_GATEWAY_STATE"] = str(tmp / "gw_state.json")
    os.environ["LLM_KEY_A"] = "sk-mock-a"
    os.environ["LLM_KEY_B"] = "sk-mock-b"
    (tmp / "llm_endpoints.json").write_text(json.dumps({
        "endpoints": [
            {"name": "quota-dead", "base_url": f"http://127.0.0.1:{A_PORT}/v1",
             "api_key_env": "LLM_KEY_A", "models": ["test-model"]},
            {"name": "backup", "base_url": f"http://127.0.0.1:{B_PORT}/v1",
             "api_key_env": "LLM_KEY_B", "models": ["test-model"]},
        ], "cooldown_seconds": 60, "transient_threshold": 3}), encoding="utf-8")

    serve(A_PORT)
    serve(B_PORT)

    import llm_gateway
    # ① 错误分类
    assert llm_gateway.classify_error(
        RuntimeError("HTTP 429: Insufficient Balance")) == "quota"
    assert llm_gateway.classify_error(
        RuntimeError("invalid api key provided")) == "fatal"
    assert llm_gateway.classify_error(
        RuntimeError("connection timed out")) == "transient"
    print("① classify_error 三分类 OK")

    # ② 同一 Session 内轮转：首端点恒 429 → 自动落备用端点完成推理
    import native_harness as nh
    runner = SimpleNamespace(model="test-model", t0=time.monotonic(),
                             max_wall=10 ** 9, stop_reason=None, bridge=None)
    h = nh.NativeHarness(runner, workspace=str(tmp), state_dir=str(tmp))
    assert h.gateway.endpoints[0].name == "quota-dead"
    h.send_message("ping")
    content, tool_calls, finish = h._llm()
    assert content == "底座B在线", f"content={content!r}"
    assert STATE["b_hits"] == 1, STATE
    print("② 429 → 自动轮转备用端点，同一 Session 推理完成 OK")

    # ③ 冷却跨 Gateway 实例持久（daemon 重启不失忆）
    st = json.loads((tmp / "gw_state.json").read_text())
    assert st["quota-dead"]["reason"] == "quota", st
    g2 = llm_gateway.Gateway()
    ep, cred = g2.pick("test-model")
    assert ep.name == "backup", f"重启后仍选中冷却端点: {ep.name}"
    print("③ 冷却状态跨进程持久 OK")

    # ④ 灵魂 checksum 往返
    h.messages.append({"role": "assistant", "content": "底座B在线"})
    h._save_state()
    reloaded = nh.NativeHarness(runner, workspace=str(tmp), state_dir=str(tmp))
    assert reloaded.messages == h.messages
    (tmp / "native_messages.json").write_text(json.dumps(
        {"version": 1, "checksum": "deadbeef",
         "messages": [{"role": "user", "content": "x"}]}), encoding="utf-8")
    tampered = nh.NativeHarness(runner, workspace=str(tmp), state_dir=str(tmp))
    assert tampered.messages == [], "篡改文件必须拒载"
    print("④ 消息历史 checksum 往返一致 + 篡改拒载 OK")
    print("FAULT-INJECTION ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
