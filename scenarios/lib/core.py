"""Small, deterministic scenario generator with no third-party dependencies."""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_IDS = (
    "sintering_endpoint_temperature",
    "sintering_feo",
    "dust_collection_efficiency",
    "plant_load_forecast",
    "photovoltaic_power",
    "rotating_equipment_fault",
)
MODES = ("baseline", "drift", "fault")
DEFAULT_ROWS = 96
MAX_ROWS = 10_000


def load_manifest(scenario_id: str) -> dict[str, Any]:
    """Load and minimally validate a scenario manifest."""
    if scenario_id not in SCENARIO_IDS:
        raise ValueError(f"unknown scenario: {scenario_id}")
    path = ROOT / scenario_id / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "id", "version", "seed", "frequency_seconds", "modes", "fields"}
    missing = required.difference(manifest)
    if missing:
        raise ValueError(f"{path}: missing keys: {sorted(missing)}")
    if manifest["id"] != scenario_id:
        raise ValueError(f"{path}: id does not match directory")
    if set(manifest["modes"]) != set(MODES):
        raise ValueError(f"{path}: modes must be {MODES}")
    names = [field["name"] for field in manifest["fields"]]
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: field names must be unique")
    if "timestamp" not in names or "scenario_mode" not in names:
        raise ValueError(f"{path}: common fields are missing")
    if not any(field.get("role") == "ground_truth" for field in manifest["fields"]):
        raise ValueError(f"{path}: at least one ground_truth field is required")
    return manifest


def _stable_seed(scenario_id: str, seed: int) -> int:
    # Modes deliberately share the same noise stream.  Their pre-injection
    # prefix is therefore a controlled counterfactual, not a different sample.
    payload = f"foundation-scenario-v1:{scenario_id}:{seed}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def generate(
    scenario_id: str,
    mode: str = "baseline",
    rows: int | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Generate bounded, deterministic records in memory.

    No file, database, network, or service is touched by this function.
    """
    manifest = load_manifest(scenario_id)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    rows = manifest["default_rows"] if rows is None else rows
    if not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_ROWS:
        raise ValueError(f"rows must be an integer between 1 and {MAX_ROWS}")
    effective_seed = manifest["seed"] if seed is None else seed
    if not isinstance(effective_seed, int) or isinstance(effective_seed, bool):
        raise ValueError("seed must be an integer")

    rng = random.Random(_stable_seed(scenario_id, effective_seed))
    module = importlib.import_module(f"scenarios.{scenario_id}.generator")
    start = datetime.fromisoformat(manifest["start_time"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    generated: list[dict[str, Any]] = []
    for index in range(rows):
        record = module.generate_row(index, rows, mode, rng)
        record = {
            "timestamp": (start + timedelta(seconds=index * manifest["frequency_seconds"]))
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "scenario_mode": mode,
            **record,
        }
        _validate_record(manifest, record, index)
        generated.append(record)
    return generated


def _validate_record(manifest: dict[str, Any], record: dict[str, Any], index: int) -> None:
    fields = manifest["fields"]
    expected = {field["name"] for field in fields}
    if set(record) != expected:
        missing = sorted(expected.difference(record))
        extra = sorted(set(record).difference(expected))
        raise ValueError(f"row {index}: fields mismatch; missing={missing}, extra={extra}")
    for field in fields:
        value = record[field["name"]]
        dtype = field["type"]
        if dtype == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"row {index}: {field['name']} must be numeric")
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"row {index}: {field['name']} below minimum")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"row {index}: {field['name']} above maximum")
        elif dtype == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"row {index}: {field['name']} must be integer")
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"row {index}: {field['name']} below minimum")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"row {index}: {field['name']} above maximum")
        elif dtype == "string" and not isinstance(value, str):
            raise ValueError(f"row {index}: {field['name']} must be string")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"row {index}: {field['name']} outside enum")


def write_csv(records: Iterable[dict[str, Any]], output: str | Path) -> Path:
    """Write records only when explicitly requested by the caller."""
    records = list(records)
    if not records:
        raise ValueError("records cannot be empty")
    destination = Path(output).expanduser().resolve()
    configured = os.getenv(
        "ODF_VALIDATION_ALLOWED_ROOTS",
        "/tmp,/srv/tensor/operator-b/foundation-validation-runs",
    )
    allowed = [Path(item).expanduser().resolve() for item in configured.split(",") if item]
    if not any(destination == root or root in destination.parents for root in allowed):
        raise ValueError(f"scenario output must be below an approved validation root: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return destination


def fingerprint(records: Iterable[dict[str, Any]]) -> str:
    """Stable content fingerprint used by regression gates."""
    payload = json.dumps(list(records), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clip(value: float, low: float, high: float, digits: int = 3) -> float:
    return round(min(high, max(low, value)), digits)


def progress(index: int, rows: int) -> float:
    return index / max(1, rows - 1)
