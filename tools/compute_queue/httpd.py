"""compute_queue HTTP 入口（提交面双入口之二；stdlib only）。

  POST /compute/submit  body={cmd|pytest|cmd_tpl+shards, repo, ref, workdir,
                              cores, mem_g, timeout, submitter}
                         → 200 {jid, queued_ahead} / 400 {error}
  GET  /compute/jobs?jid=cjN   → 作业状态（含 placement/wait_s/cpu_seconds）
  GET  /compute/nodes         → 统一负载表（n315/n330/local-99）
  POST /compute/kill    body={jid}
  GET  /healthz           → ok

body 里带 node 字段 → 400 REFUSED（HPC 语义：提交者永不说跑哪）。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .scheduler import JobSpec


def _jid_int(s) -> int:
    s = str(s).strip()
    return int(s[2:] if s.startswith("cj") else s)


def _handler(cq):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静默
            pass

        def _send(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return None

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/compute/submit":
                d = self._body()
                if d is None:
                    return self._send(400, {"error": "bad json"})
                if d.get("node"):
                    return self._send(400, {"error": "REFUSED: 不接受 node 字段"
                                                  "（HPC 语义）"})
                spec = JobSpec(cmd=d.get("cmd") or "",
                               pytest_target=d.get("pytest") or "",
                               repo=d.get("repo") or "",
                               ref=d.get("ref") or "main",
                               workdir=d.get("workdir") or "",
                               cores=int(d.get("cores") or 1),
                               mem_g=int(d.get("mem_g") or 0),
                               timeout=int(d.get("timeout") or 3600),
                               cmd_tpl=d.get("cmd_tpl") or "",
                               shards=int(d.get("shards") or 0),
                               submitter=d.get("submitter") or "agent")
                out = cq.submit(spec)
                return self._send(400 if "error" in out else 200, out)
            if path == "/compute/kill":
                d = self._body() or {}
                jid = str(d.get("jid") or "")
                try:
                    cq.ledger.kill(_jid_int(jid))
                except ValueError:
                    return self._send(400, {"error": "jid 需 cjN"})
                return self._send(200, {"jid": d.get("jid"), "killed": True})
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self._send(200, {"ok": True, "svc": "compute_queue"})
            if u.path == "/compute/nodes":
                hosts = cq.build_load_table()
                return self._send(200, {
                    h.id: {"ok": h.ok, "loadr": round(h.loadr, 3),
                            "cores": h.cores, "mem_free_g": h.mem_free_g,
                            "used_cores": h.used_cores,
                            "running_jobs": h.running_jobs}
                    for h in hosts.values()})
            if u.path == "/compute/jobs":
                q = parse_qs(u.query)
                jid = (q.get("jid") or [None])[0]
                if jid:
                    r = cq.ledger.get(_jid_int(jid))
                    return self._send(200 if r else 404,
                                    r or {"error": "not found"})
                return self._send(200, {"queued": cq.ledger.queued_jobs(100)})
            return self._send(404, {"error": "not found"})

    return H


def serve(cq, port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _handler(cq))
    print("compute_queue HTTP 入口监听 127.0.0.1:%d" % port)
    srv.serve_forever()
    return srv
