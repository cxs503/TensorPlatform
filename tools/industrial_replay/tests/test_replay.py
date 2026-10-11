from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools.industrial_replay.replay import (
    FaultPlan, FreezeRule, HttpSink, JsonLineSink, ReplayEngine, ReplayError,
    SequenceRange, build_sink, load_records, main,
)


def records(count: int = 6) -> list[dict[str, object]]:
    return [
        {"timestamp": f"2026-01-01T00:00:0{index - 1}Z", "device_id": "sim-01", "temperature": index * 10}
        for index in range(1, count + 1)
    ]


class CaptureSink(JsonLineSink):
    def __init__(self) -> None:
        self.buffer = io.StringIO()
        super().__init__(self.buffer)

    def values(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.buffer.getvalue().splitlines()]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


class ReplayTests(unittest.TestCase):
    def test_loads_jsonl_and_csv_without_coercing_csv_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "input.jsonl"
            jsonl.write_text("\n".join(json.dumps(row) for row in records(2)) + "\n", encoding="utf-8")
            csv_file = root / "input.csv"
            csv_file.write_text(
                "event_time,tag,value\n2026-01-01T00:00:00Z,A,001.20\n2026-01-01T00:00:01Z,A,001.30\n",
                encoding="utf-8",
            )
            self.assertEqual(load_records(jsonl)[1]["temperature"], 20)
            self.assertEqual(load_records(csv_file, "event_time")[0]["value"], "001.20")

    def test_rejects_unordered_or_missing_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.jsonl"
            source.write_text('{"timestamp": 2}\n{"timestamp": 1}\n', encoding="utf-8")
            with self.assertRaisesRegex(ReplayError, "must be ordered"):
                load_records(source)
        with self.assertRaisesRegex(ReplayError, "has no"):
            ReplayEngine([{"value": 1}], run_id="fixed").prepare()
        with self.assertRaisesRegex(ReplayError, "must include a timezone"):
            ReplayEngine([{"timestamp": "2026-01-01T00:00:00"}], run_id="fixed").prepare()
        with self.assertRaisesRegex(ReplayError, "must be ordered"):
            ReplayEngine([{"timestamp": 2}, {"timestamp": 1}], run_id="fixed").prepare()

    def test_faults_are_deterministic_and_traceable(self) -> None:
        plan = FaultPlan(
            disconnect=(SequenceRange(5, 5),), duplicate_every=2, duplicate_copies=1,
            reorder_window=2, latency_ms=250, latency_every=3,
            freezes=(FreezeRule("temperature", SequenceRange(3, 4)),),
        )
        engine = ReplayEngine(records(), run_id="scenario-fixed-001", fault_plan=plan)
        first, first_summary = engine.prepare()
        second, second_summary = engine.prepare()
        self.assertEqual(first_summary, {"source_records": 6, "dropped": 1, "duplicates": 3, "output_records": 8})
        self.assertEqual(first_summary, second_summary)
        self.assertEqual([item.sequence for item in first], [2, 2, 1, 4, 4, 3, 6, 6])
        self.assertEqual(
            [(item.sequence, item.payload["temperature"]) for item in first if item.sequence in {3, 4}],
            [(4, 20), (4, 20), (3, 20)],
        )
        self.assertEqual(
            [engine.envelope(item) for item in first],
            [engine.envelope(item) for item in second],
        )
        self.assertIn("latency:250ms", next(item for item in first if item.sequence == 3).faults)

    def test_speed_controls_pacing_and_zero_means_no_wait(self) -> None:
        source = [{"timestamp": 100, "value": 1}, {"timestamp": 110, "value": 2}]
        clock = FakeClock()
        sink = CaptureSink()
        ReplayEngine(source, run_id="paced").run(
            sink, speed=10, sleep=clock.sleep, monotonic=clock.monotonic
        )
        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual(len(sink.values()), 2)
        no_wait = FakeClock()
        ReplayEngine(source, run_id="fast").run(
            CaptureSink(), speed=0, sleep=no_wait.sleep, monotonic=no_wait.monotonic
        )
        self.assertEqual(no_wait.sleeps, [])

    def test_file_sink_writes_envelopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "output.jsonl"
            sink = build_sink(f"file:{output}", headers={}, timeout=1, retries=0)
            ReplayEngine(records(1), run_id="file-run").run(sink, speed=0)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["replay"]["run_id"], "file-run")

    def test_file_and_network_sinks_fail_closed(self) -> None:
        with self.assertRaisesRegex(ReplayError, "approved validation root"):
            build_sink(
                "file:/var/lib/foundation-production/replay.jsonl",
                headers={}, timeout=1, retries=0,
            )
        with self.assertRaisesRegex(ReplayError, "network sinks are disabled"):
            build_sink(
                "http://127.0.0.1:19090/telemetry",
                headers={}, timeout=1, retries=0,
            )
        with self.assertRaisesRegex(ReplayError, "not allowlisted"):
            build_sink(
                "http://production.example/telemetry",
                headers={}, timeout=1, retries=0, allow_network=True,
            )

    def test_http_sink_posts_each_envelope(self) -> None:
        received: list[dict[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
                received.append(json.loads(self.rfile.read(length)))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            sink = HttpSink(
                f"http://127.0.0.1:{server.server_port}/telemetry",
                headers={"X-Replay": "test"}, timeout=2, retries=0,
            )
            ReplayEngine(records(2), run_id="http-run").run(sink, speed=0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual([item["replay"]["sequence"] for item in received], [1, 2])

    def test_cli_dry_run_validates_without_creating_sink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in records(3)), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main([
                    str(source), "--run-id", "dry-fixed", "--dry-run",
                    "--disconnect", "2:2", "--duplicate-every", "3",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue()), {
                "dropped": 1, "duplicates": 1, "output_records": 3,
                "run_id": "dry-fixed", "source_records": 3, "status": "valid",
            })


if __name__ == "__main__":
    unittest.main()
