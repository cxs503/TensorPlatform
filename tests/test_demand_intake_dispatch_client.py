"""建单客户端契约测试（本地 stub server；不碰生产 API）。

v2 改造① 的关键动作是「铸造后**同步** POST /tensor/missions」。这条路若只在生产上
偶然跑通、没有契约测试，一旦 payload 字段漂移（origin/task_key/goal 结构）就会静默
退化成「锚在榜上、单子不存在」——正是 v1 pending_review 的同型故障。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tools.demand_intake.dispatch import HttpDispatcher, mission_payload


def _cand(slug: str, acceptance: str = "误差 <3%（实测 1.2%）"):
    return {"slug": slug, "title": f"synthetic anchor {slug}", "acceptance": acceptance,
            "domain": "platform", "repo": "TensorPlatform", "source_kind": "test",
            "source_ref": slug}


def test_http_dispatcher_posts_expected_payload():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):                                  # noqa: N802
            n = int(self.headers.get("Content-Length", "0"))
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(n) or b"{}")
            body = json.dumps({"id": 424242}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):                       # 静音
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        disp = HttpDispatcher(base_url="http://127.0.0.1:%d/api/v1" % srv.server_port,
                              token="tkn")
        mid = disp.create_mission(mission_payload(_cand("FEAT-SYNTH-HTTP")))
    finally:
        srv.shutdown()

    assert mid == 424242
    assert seen["path"] == "/api/v1/tensor/missions"
    assert seen["auth"] == "Bearer tkn"
    payload = seen["body"]
    assert payload["origin"] == "agent"
    assert payload["task_key"] == "FEAT-SYNTH-HTTP"
    assert payload["repo"] == "TensorPlatform"
    assert len(payload["goal"]) >= 10
    assert "FEAT-SYNTH-HTTP" in payload["goal"]
    assert "验收判据" in payload["goal"]


def test_mission_payload_keeps_acceptance_machine_readable():
    payload = mission_payload(_cand("FEAT-SYNTH-HTTP2", acceptance="覆盖率 ≥80%"))
    assert "覆盖率 ≥80%" in payload["goal"]
    assert payload["task_key"] == "FEAT-SYNTH-HTTP2"