#!/usr/bin/env python3
"""Dependency-free, deterministic industrial telemetry replay tool.

This stays outside the runtime application so scenario data can be validated and
replayed without touching platform databases, containers, or production services.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


class ReplayError(ValueError):
    """Invalid replay input or configuration."""


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ReplayError("timestamp must not be empty")
        try:
            timestamp = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReplayError(f"invalid timestamp: {value!r}") from exc
            if parsed.tzinfo is None:
                raise ReplayError("ISO timestamp must include a timezone")
            timestamp = parsed.timestamp()
    else:
        raise ReplayError(f"invalid timestamp type: {type(value).__name__}")
    if not math.isfinite(timestamp):
        raise ReplayError("timestamp must be finite")
    return timestamp


def load_records(path: str | Path, timestamp_field: str = "timestamp") -> list[dict[str, Any]]:
    """Load CSV or JSONL and validate monotonically ordered source timestamps.

    CSV values intentionally remain strings: scenario schemas, rather than this
    transport utility, own industrial tag coercion and precision.
    """

    source = Path(path)
    if not source.is_file():
        raise ReplayError(f"input file does not exist: {source}")
    records: list[dict[str, Any]] = []
    if source.suffix.lower() in {".jsonl", ".ndjson"}:
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReplayError(f"invalid JSON at line {line_number}: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise ReplayError(f"line {line_number} must be a JSON object")
                records.append(record)
    elif source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ReplayError("CSV header is missing")
            records.extend(dict(row) for row in reader)
    else:
        raise ReplayError("input format must be .jsonl, .ndjson or .csv")
    if not records:
        raise ReplayError("input contains no records")
    previous: float | None = None
    for number, record in enumerate(records, 1):
        if timestamp_field not in record:
            raise ReplayError(f"record {number} has no {timestamp_field!r} field")
        current = _parse_timestamp(record[timestamp_field])
        if previous is not None and current < previous:
            raise ReplayError(
                f"source timestamps must be ordered: record {number} is earlier than record {number - 1}"
            )
        previous = current
    return records


@dataclass(frozen=True)
class SequenceRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ReplayError(f"invalid sequence range {self.start}:{self.end}")

    def includes(self, sequence: int) -> bool:
        return self.start <= sequence <= self.end


@dataclass(frozen=True)
class FreezeRule:
    field: str
    window: SequenceRange

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ReplayError("freeze field must not be empty")


@dataclass(frozen=True)
class FaultPlan:
    """A deterministic fault plan; sequence numbers are one-based."""

    disconnect: tuple[SequenceRange, ...] = ()
    duplicate_every: int = 0
    duplicate_copies: int = 1
    reorder_window: int = 1
    latency_ms: int = 0
    latency_every: int = 1
    freezes: tuple[FreezeRule, ...] = ()

    def __post_init__(self) -> None:
        if self.duplicate_every < 0:
            raise ReplayError("duplicate_every must be non-negative")
        if self.duplicate_copies < 1:
            raise ReplayError("duplicate_copies must be at least 1")
        if self.duplicate_copies > 100:
            raise ReplayError("duplicate_copies must not exceed 100")
        if self.reorder_window < 1:
            raise ReplayError("reorder_window must be at least 1")
        if self.reorder_window > 10000:
            raise ReplayError("reorder_window must not exceed 10000")
        if self.latency_ms < 0:
            raise ReplayError("latency_ms must be non-negative")
        if self.latency_every < 1:
            raise ReplayError("latency_every must be at least 1")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FaultPlan":
        allowed = {
            "disconnect", "duplicate_every", "duplicate_copies", "reorder_window",
            "latency_ms", "latency_every", "freeze",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ReplayError(f"unknown fault-plan keys: {', '.join(sorted(unknown))}")
        try:
            return cls(
                disconnect=tuple(_range_from_value(item) for item in value.get("disconnect", [])),
                duplicate_every=int(value.get("duplicate_every", 0)),
                duplicate_copies=int(value.get("duplicate_copies", 1)),
                reorder_window=int(value.get("reorder_window", 1)),
                latency_ms=int(value.get("latency_ms", 0)),
                latency_every=int(value.get("latency_every", 1)),
                freezes=tuple(_freeze_from_value(item) for item in value.get("freeze", [])),
            )
        except (TypeError, ValueError) as exc:
            raise ReplayError(f"fault-plan numeric fields are invalid: {exc}") from exc


def _range_from_value(value: Any) -> SequenceRange:
    if isinstance(value, str):
        pieces = value.split(":", 1)
        if len(pieces) != 2:
            raise ReplayError(f"range must be START:END, got {value!r}")
        try:
            return SequenceRange(int(pieces[0]), int(pieces[1]))
        except ValueError as exc:
            raise ReplayError(f"range must use integers, got {value!r}") from exc
    if isinstance(value, Mapping):
        try:
            return SequenceRange(int(value["start"]), int(value["end"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplayError("range object requires integer start and end") from exc
    raise ReplayError("range must be START:END or an object")


def _freeze_from_value(value: Any) -> FreezeRule:
    if isinstance(value, str):
        pieces = value.split(":")
        if len(pieces) != 3:
            raise ReplayError(f"freeze must be FIELD:START:END, got {value!r}")
        return FreezeRule(pieces[0], _range_from_value(":".join(pieces[1:])))
    if isinstance(value, Mapping):
        try:
            return FreezeRule(str(value["field"]), _range_from_value(value))
        except KeyError as exc:
            raise ReplayError("freeze object requires field, start and end") from exc
    raise ReplayError("freeze must be FIELD:START:END or an object")


@dataclass
class ReplayItem:
    sequence: int
    payload: dict[str, Any]
    source_timestamp: float
    scheduled_offset: float
    faults: list[str] = field(default_factory=list)
    duplicate_number: int = 0


class ReplayEngine:
    def __init__(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        run_id: str,
        timestamp_field: str = "timestamp",
        fault_plan: FaultPlan | None = None,
    ) -> None:
        if not run_id.strip():
            raise ReplayError("run_id must not be empty")
        if not records:
            raise ReplayError("records must not be empty")
        self.records = [dict(record) for record in records]
        self.run_id = run_id
        self.timestamp_field = timestamp_field
        self.fault_plan = fault_plan or FaultPlan()

    def prepare(self) -> tuple[list[ReplayItem], dict[str, int]]:
        timestamps: list[float] = []
        for sequence, record in enumerate(self.records, 1):
            if self.timestamp_field not in record:
                raise ReplayError(f"record {sequence} has no {self.timestamp_field!r} field")
            timestamps.append(_parse_timestamp(record[self.timestamp_field]))
        first_timestamp = timestamps[0]
        for sequence, (previous, current) in enumerate(zip(timestamps, timestamps[1:]), 2):
            if current < previous:
                raise ReplayError(
                    f"source timestamps must be ordered: record {sequence} is earlier than record {sequence - 1}"
                )
        items: list[ReplayItem] = []
        freeze_values: dict[str, Any] = {}
        dropped = 0
        for sequence, (record, timestamp) in enumerate(zip(self.records, timestamps), 1):
            payload = dict(record)
            faults: list[str] = []
            for rule in self.fault_plan.freezes:
                if sequence < rule.window.start and rule.field in payload:
                    freeze_values[rule.field] = payload[rule.field]
                if rule.window.includes(sequence):
                    if rule.field not in freeze_values:
                        raise ReplayError(f"cannot freeze {rule.field!r} at sequence {sequence}: no prior value")
                    payload[rule.field] = freeze_values[rule.field]
                    faults.append(f"sensor_freeze:{rule.field}")
            if any(window.includes(sequence) for window in self.fault_plan.disconnect):
                dropped += 1
                continue
            if self.fault_plan.latency_ms and sequence % self.fault_plan.latency_every == 0:
                faults.append(f"latency:{self.fault_plan.latency_ms}ms")
            items.append(ReplayItem(
                sequence=sequence,
                payload=payload,
                source_timestamp=timestamp,
                scheduled_offset=max(0.0, timestamp - first_timestamp),
                faults=faults,
            ))

        reordered: list[ReplayItem] = []
        size = self.fault_plan.reorder_window
        for position in range(0, len(items), size):
            chunk = items[position:position + size]
            slots = [item.scheduled_offset for item in chunk]
            if size > 1 and len(chunk) > 1:
                chunk.reverse()
                for item in chunk:
                    item.faults.append(f"reorder:{size}")
            for item, offset in zip(chunk, slots):
                item.scheduled_offset = offset
            reordered.extend(chunk)

        emitted: list[ReplayItem] = []
        duplicated = 0
        for item in reordered:
            emitted.append(item)
            if self.fault_plan.duplicate_every and item.sequence % self.fault_plan.duplicate_every == 0:
                for copy_number in range(1, self.fault_plan.duplicate_copies + 1):
                    emitted.append(ReplayItem(
                        sequence=item.sequence,
                        payload=dict(item.payload),
                        source_timestamp=item.source_timestamp,
                        scheduled_offset=item.scheduled_offset,
                        faults=[*item.faults, "duplicate"],
                        duplicate_number=copy_number,
                    ))
                    duplicated += 1
        return emitted, {
            "source_records": len(self.records), "dropped": dropped,
            "duplicates": duplicated, "output_records": len(emitted),
        }

    def envelope(self, item: ReplayItem) -> dict[str, Any]:
        return {
            "replay": {
                "run_id": self.run_id,
                "sequence": item.sequence,
                "duplicate_number": item.duplicate_number,
                "scheduled_offset_ms": round(item.scheduled_offset * 1000),
                "source_timestamp": item.source_timestamp,
                "faults": item.faults,
            },
            "data": item.payload,
        }

    def run(
        self,
        sink: "Sink",
        *,
        speed: float,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> dict[str, int]:
        if speed < 0:
            raise ReplayError("speed must be non-negative")
        items, summary = self.prepare()
        started = monotonic()
        try:
            for item in items:
                latency = self.fault_plan.latency_ms / 1000 if any(
                    fault.startswith("latency:") for fault in item.faults
                ) else 0.0
                target = 0.0 if speed == 0 else item.scheduled_offset / speed + latency
                remaining = started + target - monotonic()
                if remaining > 0:
                    sleep(remaining)
                sink.send(self.envelope(item))
        finally:
            sink.close()
        return summary


class Sink:
    def send(self, envelope: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class JsonLineSink(Sink):
    def __init__(self, handle: Any, *, close_handle: bool = False) -> None:
        self.handle = handle
        self.close_handle = close_handle

    def send(self, envelope: Mapping[str, Any]) -> None:
        self.handle.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush()

    def close(self) -> None:
        if self.close_handle:
            self.handle.close()


class HttpSink(Sink):
    def __init__(self, url: str, *, headers: Mapping[str, str], timeout: float, retries: int) -> None:
        self.url = url
        self.headers = {"Content-Type": "application/json", **headers}
        self.timeout = timeout
        self.retries = retries

    def send(self, envelope: Mapping[str, Any]) -> None:
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if not 200 <= response.status < 300:
                        raise ReplayError(f"HTTP sink returned {response.status}")
                return
            except Exception as exc:  # urllib exposes several transport exceptions
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(0.2 * (2 ** attempt), 2.0))
        raise ReplayError(f"HTTP sink failed after {self.retries + 1} attempt(s): {last_error}")


class KafkaSink(Sink):
    def __init__(self, brokers: str, topic: str) -> None:
        if importlib.util.find_spec("kafka") is None:
            raise ReplayError("kafka-python is not installed; use stdout/file/http or an existing environment")
        from kafka import KafkaProducer  # type: ignore[import-not-found]
        self.producer = KafkaProducer(
            bootstrap_servers=brokers.split(","),
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        )
        self.topic = topic

    def send(self, envelope: Mapping[str, Any]) -> None:
        self.producer.send(self.topic, dict(envelope)).get(timeout=10)

    def close(self) -> None:
        self.producer.flush(timeout=10)
        self.producer.close(timeout=10)


class MqttSink(Sink):
    def __init__(self, host: str, port: int, topic: str) -> None:
        try:
            available = importlib.util.find_spec("paho.mqtt.client") is not None
        except ModuleNotFoundError:
            available = False
        if not available:
            raise ReplayError("paho-mqtt is not installed; use stdout/file/http or an existing environment")
        import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
        self.client = mqtt.Client()
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        self.topic = topic

    def send(self, envelope: Mapping[str, Any]) -> None:
        result = self.client.publish(self.topic, json.dumps(envelope, ensure_ascii=False), qos=1)
        result.wait_for_publish(timeout=10)
        if result.rc != 0:
            raise ReplayError(f"MQTT publish failed with code {result.rc}")

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def _parse_headers(values: Iterable[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition(":")
        if not separator or not name.strip():
            raise ReplayError(f"header must be NAME:VALUE, got {value!r}")
        headers[name.strip()] = content.strip()
    return headers


def _approved_file_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    configured = os.getenv(
        "ODF_VALIDATION_ALLOWED_ROOTS",
        "/tmp,/srv/tensor/operator-b/foundation-validation-runs",
    )
    roots = [Path(item).expanduser().resolve() for item in configured.split(",") if item]
    if not any(path == root or root in path.parents for root in roots):
        raise ReplayError(f"file sink must be below an approved validation root: {path}")
    return path


def build_sink(
    spec: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    retries: int,
    allow_network: bool = False,
    allowed_hosts: Iterable[str] = (),
) -> Sink:
    if spec == "stdout":
        return JsonLineSink(sys.stdout)
    if spec.startswith("file:"):
        path = _approved_file_path(spec[5:])
        if not path.name:
            raise ReplayError("file sink requires a path")
        path.parent.mkdir(parents=True, exist_ok=True)
        return JsonLineSink(path.open("w", encoding="utf-8"), close_handle=True)
    parsed = urllib.parse.urlparse(spec)
    if parsed.scheme in {"http", "https", "kafka", "mqtt"}:
        if not allow_network:
            raise ReplayError("network sinks are disabled; pass --allow-network-sink for a test endpoint")
        host = parsed.hostname
        allowed = {"127.0.0.1", "localhost", "::1", *allowed_hosts}
        if not host or host not in allowed:
            raise ReplayError(f"network sink host is not allowlisted: {host!r}")
    if parsed.scheme in {"http", "https"}:
        if parsed.username or parsed.password:
            raise ReplayError("credentials must not be embedded in a replay URL")
        return HttpSink(spec, headers=headers, timeout=timeout, retries=retries)
    if parsed.scheme == "kafka":
        topic = parsed.path.lstrip("/")
        if not parsed.netloc or not topic:
            raise ReplayError("Kafka sink must be kafka://broker:9092/topic")
        return KafkaSink(parsed.netloc, topic)
    if parsed.scheme == "mqtt":
        topic = parsed.path.lstrip("/")
        if not parsed.hostname or not topic:
            raise ReplayError("MQTT sink must be mqtt://host:1883/topic")
        return MqttSink(parsed.hostname, parsed.port or 1883, topic)
    raise ReplayError("sink must be stdout, file:PATH, http(s)://URL, kafka://BROKER/TOPIC or mqtt://HOST/TOPIC")


def _load_fault_plan(path: str | None, args: argparse.Namespace) -> FaultPlan:
    raw: dict[str, Any] = {}
    if path:
        try:
            raw_value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReplayError(f"cannot read fault plan {path!r}: {exc}") from exc
        if not isinstance(raw_value, dict):
            raise ReplayError("fault plan root must be a JSON object")
        raw.update(raw_value)
    overrides = {
        "disconnect": args.disconnect, "duplicate_every": args.duplicate_every,
        "duplicate_copies": args.duplicate_copies, "reorder_window": args.reorder_window,
        "latency_ms": args.latency_ms, "latency_every": args.latency_every,
        "freeze": args.freeze,
    }
    for key, value in overrides.items():
        if value is not None:
            raw[key] = value
    return FaultPlan.from_mapping(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="ordered .jsonl/.ndjson or .csv scenario data")
    parser.add_argument("--run-id", required=True, help="fixed identifier included in every envelope")
    parser.add_argument("--timestamp-field", default="timestamp")
    parser.add_argument("--speed", choices=("1", "10", "100", "0", "none"), default="0", help="0/none means no wait")
    parser.add_argument("--sink", default="stdout")
    parser.add_argument(
        "--allow-network-sink", action="store_true",
        help="explicitly enable a network sink; localhost remains the only default allowlisted host",
    )
    parser.add_argument(
        "--allow-host", action="append", default=[],
        help="additional test host allowed with --allow-network-sink; repeat as needed",
    )
    parser.add_argument("--fault-config", help="JSON fault plan")
    parser.add_argument("--disconnect", action="append", default=None, metavar="START:END")
    parser.add_argument("--duplicate-every", type=int, default=None)
    parser.add_argument("--duplicate-copies", type=int, default=None)
    parser.add_argument("--reorder-window", type=int, default=None)
    parser.add_argument("--latency-ms", type=int, default=None)
    parser.add_argument("--latency-every", type=int, default=None)
    parser.add_argument("--freeze", action="append", default=None, metavar="FIELD:START:END")
    parser.add_argument("--header", action="append", default=[], metavar="NAME:VALUE")
    parser.add_argument("--http-timeout", type=float, default=10.0)
    parser.add_argument("--http-retries", type=int, default=2)
    parser.add_argument("--max-events", type=int, default=100000, help="safety limit; 0 disables it")
    parser.add_argument("--dry-run", action="store_true", help="validate and print summary without sending or waiting")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.http_timeout <= 0 or args.http_retries < 0 or args.max_events < 0:
            raise ReplayError("timeout must be positive; retries and max-events must be non-negative")
        records = load_records(args.input, args.timestamp_field)
        if args.max_events and len(records) > args.max_events:
            raise ReplayError(f"input has {len(records)} records, exceeding --max-events {args.max_events}")
        engine = ReplayEngine(records, run_id=args.run_id, timestamp_field=args.timestamp_field,
                              fault_plan=_load_fault_plan(args.fault_config, args))
        if args.dry_run:
            _, summary = engine.prepare()
            print(json.dumps({"status": "valid", "run_id": args.run_id, **summary}, sort_keys=True))
            return 0
        sink = build_sink(args.sink, headers=_parse_headers(args.header),
                          timeout=args.http_timeout, retries=args.http_retries,
                          allow_network=args.allow_network_sink,
                          allowed_hosts=args.allow_host)
        speed = 0.0 if args.speed == "none" else float(args.speed)
        summary = engine.run(sink, speed=speed)
        print(json.dumps({"status": "completed", "run_id": args.run_id, **summary}, sort_keys=True), file=sys.stderr)
        return 0
    except (ReplayError, OSError) as exc:
        parser.exit(2, f"industrial-replay: error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
