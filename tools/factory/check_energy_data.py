"""A bounded, read-only pilot tool. It audits data; it does not control equipment."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path


def audit(path: Path) -> dict:
    payload = path.read_bytes()
    rows = list(csv.DictReader(payload.decode("utf-8-sig").splitlines()))
    if len(rows) < 2:
        raise ValueError("at least two measurements are required")
    previous = None
    energy_kwh = 0.0
    for row in rows:
        stamp = datetime.fromisoformat(row["timestamp"])
        if stamp.tzinfo is None:
            raise ValueError("timestamps require a UTC offset")
        power = float(row["power_kw"])
        if not math.isfinite(power) or power < 0:
            raise ValueError("power_kw must be finite and non-negative")
        if previous:
            seconds = (stamp - previous[0]).total_seconds()
            if seconds <= 0:
                raise ValueError("timestamps must increase strictly")
            energy_kwh += (previous[1] + power) * 0.5 * seconds / 3600
        previous = stamp, power
    if not math.isfinite(energy_kwh):
        raise ValueError("integrated energy is not finite")
    return {"tool": "energy-data-audit-v1", "status": "passed", "rows": len(rows),
            "energy_kwh_trapezoidal": energy_kwh,
            "input_sha256": hashlib.sha256(payload).hexdigest(),
            "scope": "data integrity and trapezoidal integration only",
            "physical_validation": "not_established", "equipment_actions": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = audit(args.input)
    except (ValueError, KeyError, OSError) as exc:
        result = {"tool": "energy-data-audit-v1", "status": "failed", "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
