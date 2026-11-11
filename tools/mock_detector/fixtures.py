"""Synthetic fixtures: a local service with *known* answers, used to prove the
detector is not vacuous.

The fixtures deliberately use meaningless names (``x_in``, ``k_gain``,
``y_out``, ``/api/v1/synthetic/...``): the point is that the detector must reach
the right verdict **without** any domain knowledge, so a fixture suite that spoke
a domain language would beg the question.

| fixture | behaviour | expected verdict |
|---|---|---|
| ``linear``        | ``y = 2*k*x + 1``                     | REAL |
| ``hardcoded``     | constant regardless of input          | MOCK (INPUT_INVARIANT) |
| ``random``        | ``random()`` per call                 | MOCK (NONDETERMINISTIC) |
| ``counter``       | ``y = x + n_calls``                   | MOCK (NON_IDEMPOTENT) |
| ``order_state``   | ``y = 2x + flips`` where ``flips`` grows only when the input changes | MOCK (ORDER_DEPENDENT_STATE) |
| ``quantized``     | two-bucket lookup                     | MOCK (QUANTIZED_OUTPUT) |
| ``global_only``   | responds only when *all* inputs are scaled (driver outside the swept set) | WITHHELD (GLOBAL_RESPONSE_ONLY) |
| ``closure``       | 422 unless ``y_out_in == 2*x_in`` (closed payload); only a uniform sweep stays valid | REAL (uniform-axis fallback) |
| ``metadata``      | ``{"status": "ok"}`` only             | STUB |
| ``missing``       | 404                                   | NOT_SUPPORTED |
| ``boom``          | 500                                   | WITHHELD |
"""
from __future__ import annotations

import argparse
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from .classify import classify
from .httpc import JsonClient
from .model import Target
from .probes import ProbeConfig, run_probes

BASE = "/api/v1/synthetic"

EXPECTED: Dict[str, str] = {
    f"{BASE}/linear": "REAL",
    f"{BASE}/hardcoded": "MOCK",
    f"{BASE}/random": "MOCK",
    f"{BASE}/counter": "MOCK",
    f"{BASE}/order_state": "MOCK",
    f"{BASE}/quantized": "MOCK",
    f"{BASE}/global_only": "WITHHELD",
    f"{BASE}/closure": "REAL",
    f"{BASE}/metadata": "STUB",
    f"{BASE}/missing": "NOT_SUPPORTED",
    f"{BASE}/boom": "WITHHELD",
}

DEGENERATE = ("MOCK", "STUB")


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.flips = 0
        self.last_x: Optional[float] = None


STATE = _State()


def _handle(path: str, body: Dict[str, Any]) -> tuple:
    """Return ``(status, payload)`` for one synthetic route."""
    x = float(body.get("x_in", 1.0))
    k = float(body.get("k_gain", 1.0))
    if path == f"{BASE}/linear":
        return 200, {"y_out": 2.0 * k * x + 1.0}
    if path == f"{BASE}/hardcoded":
        return 200, {"y_out": 42.0, "note": "fixed"}
    if path == f"{BASE}/random":
        return 200, {"y_out": random.random() * 100.0}
    if path == f"{BASE}/counter":
        with STATE.lock:
            STATE.calls += 1
            n = STATE.calls
        return 200, {"y_out": x + n}
    if path == f"{BASE}/order_state":
        with STATE.lock:
            if STATE.last_x is not None and x != STATE.last_x:
                STATE.flips += 1
            STATE.last_x = x
            flips = STATE.flips
        return 200, {"y_out": 2.0 * x + flips}
    if path == f"{BASE}/quantized":
        return 200, {"y_out": 1.0 if x < 10.0 else 2.0}
    if path == f"{BASE}/closure":
        y = float(body.get("y_out_in", 0.0))
        if abs(y - 2.0 * x) > 1e-9:          # linear closure: preserved by uniform scaling
            return 422, {"detail": "closed payload required: y_out_in == 2 * x_in"}
        return 200, {"y_out": y}
    if path == f"{BASE}/global_only":
        # invariant along either axis alone, but responds when both inputs move:
        # emulates an endpoint whose real driver is outside the swept field set
        return 200, {"y_out": 1.0 if x * k <= 15.0 else 5.0}
    if path == f"{BASE}/metadata":
        return 200, {"status": "ok", "message": "accepted"}
    return 404, {"detail": "Not Found"}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # silence
        return

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):                   # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        path = self.path.split("?")[0]
        if path == f"{BASE}/boom":
            self._send(500, {"detail": "synthetic server error"})
            return
        status, payload = _handle(path, body)
        self._send(status, payload)

    def do_GET(self):                    # noqa: N802
        self._send(404, {"detail": "Not Found"})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


#: per-fixture sweep fields (the closure fixture additionally closes on y_out_in)
SWEEP_FIELDS: Dict[str, List[str]] = {f"{BASE}/closure": ["x_in", "k_gain", "y_out_in"]}


def fixture_targets() -> List[Target]:
    targets = []
    for path in EXPECTED:
        fields = SWEEP_FIELDS.get(path, ["x_in", "k_gain"])
        payload = {"x_in": 10.0, "k_gain": 1.0}
        if path == f"{BASE}/closure":
            payload["y_out_in"] = 20.0       # 20.0 == 2 * x_in, i.e. closed
        targets.append(Target(cap_id=path.rsplit("/", 1)[-1], method="POST", path=path,
                              base_payload=payload, sweep_fields=list(fields),
                              timeout_s=5.0))
    return targets


def run_selfcheck(out_json: Optional[str] = None, port: int = 0) -> Dict[str, Any]:
    """Serve the fixtures in-process, run the three probes, compare with ``EXPECTED``."""
    global STATE
    STATE = _State()
    server = make_server(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, real_port = server.server_address[:2]
    try:
        client = JsonClient(f"http://{host}:{real_port}", api_prefix="/api/v1")
        cfg = ProbeConfig()
        results = []
        for target in fixture_targets():
            report = classify(run_probes(client, target, cfg))
            expected = EXPECTED[target.path]
            results.append({
                "path": target.path,
                "expected": expected,
                "observed": report.level,
                "fired_rule": report.fired_rule,
                "reason_codes": report.reason_codes,
                "flags": report.flags,
                "detail": report.detail,
                "correct": report.level == expected,
                "probe": report.to_dict(),
            })
    finally:
        server.shutdown()
        server.server_close()

    false_negatives = [r["path"] for r in results
                       if r["expected"] in DEGENERATE and r["observed"] == "REAL"]
    false_positives = [r["path"] for r in results
                       if r["expected"] == "REAL" and r["observed"] in DEGENERATE]
    mismatched = [r["path"] for r in results if not r["correct"]]
    out = {
        "schema": "mock-detector-fixture-selftest/1.0",
        "n_fixtures": len(results),
        "n_correct": sum(1 for r in results if r["correct"]),
        "false_negatives": len(false_negatives),
        "false_positives": len(false_positives),
        "false_positive_list": false_positives,
        "false_negative_list": false_negatives,
        "mismatched": mismatched,
        "results": results,
    }
    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/mock_detector/fixture_selftest.json")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    res = run_selfcheck(args.out, args.port)
    if not args.quiet:
        for r in res["results"]:
            mark = "OK " if r["correct"] else "MISMATCH"
            print(f"{mark:9s} {r['path']:36s} expected={r['expected']:14s} "
                  f"observed={r['observed']:14s} rule={r['fired_rule']}")
        print(f"\n{res['n_correct']}/{res['n_fixtures']} correct, "
              f"FN={res['false_negatives']}, FP={len(res['false_positive_list'])}")
    ok = res["n_correct"] == res["n_fixtures"] and res["false_negatives"] == 0
    return 0 if ok else 1


if __name__ == "__main__":               # pragma: no cover
    raise SystemExit(main())