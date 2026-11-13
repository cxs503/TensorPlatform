"""提交面 HTTP 入口单测（InMemory 账本 + fake 探测，hermetic）。"""
import json
import threading
import urllib.request

import pytest

from tools.compute_queue.compute_queue import ComputeQueue
from tools.compute_queue.ledger import InMemoryLedger
from tools.compute_queue.scheduler import HostInfo, LOCAL_ID


def _fake_nodes():
    return {"n315": {"ok": True, "loadr": 0.3, "cores": 128, "mem_free_g": 400},
            "n330": {"ok": True, "loadr": 0.5, "cores": 128, "mem_free_g": 400}}


def _fake_local():
    return HostInfo(LOCAL_ID, ok=True, loadr=0.2, cores=128,
                    mem_free_g=800, used_cores=0, running_jobs=0)


@pytest.fixture()
def server():
    L = InMemoryLedger()
    cq = ComputeQueue(L, node_probe=_fake_nodes, local_probe=_fake_local)
    from tools.compute_queue.httpd import serve
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = serve.__wrapped__(cq, port) if hasattr(serve, "__wrapped__") else None
    # serve() 会 serve_forever；这里直接建 ThreadingHTTPServer 跑后台线程
    from http.server import ThreadingHTTPServer
    from tools.compute_queue.httpd import _handler
    ts = ThreadingHTTPServer(("127.0.0.1", port), _handler(cq))
    t = threading.Thread(target=ts.serve_forever, daemon=True)
    t.start()
    yield port, L
    ts.shutdown()


def _post(port, path, body):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def _get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                               timeout=10) as r:
        return r.status, json.loads(r.read())


def test_http_submit_and_ps(server):
    port, L = server
    st, d = _post(port, "/compute/submit",
                  {"cmd": "echo hi", "repo": "R", "cores": 4})
    assert st == 200 and d["jid"].startswith("cj")
    st, d = _get(port, "/compute/jobs?jid=" + d["jid"])
    assert st == 200 and d["state"] == "queued"


def test_http_rejects_node_field(server):
    port, _ = server
    try:
        _post(port, "/compute/submit",
               {"cmd": "x", "repo": "R", "node": "n315"})
        raise AssertionError("应 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400
        assert "REFUSED" in json.loads(e.read())["error"]


def test_http_submit_validation(server):
    port, _ = server
    try:
        _post(port, "/compute/submit", {"cmd": "x"})  # 无工作树来源
        raise AssertionError("应 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_http_nodes_includes_local(server):
    port, _ = server
    st, d = _get(port, "/compute/nodes")
    assert st == 200
    assert set(d) == {"n315", "n330", LOCAL_ID}
    assert d[LOCAL_ID]["ok"] is True


def test_http_healthz(server):
    port, _ = server
    st, d = _get(port, "/healthz")
    assert st == 200 and d["ok"] is True


def test_http_kill(server):
    port, L = server
    st, d = _post(port, "/compute/submit",
                  {"cmd": "echo hi", "repo": "R", "cores": 2})
    st, d2 = _post(port, "/compute/kill", {"jid": d["jid"]})
    assert st == 200 and d2["killed"] is True
    assert L.get(int(d["jid"][2:]))["state"] == "killed"
