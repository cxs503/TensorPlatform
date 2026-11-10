"""Run-record schema: what one arm of the gate measured, and on what machine.

A *record* is the immutable unit that travels between architectures.  It holds
exactly three things:

* ``arm`` — the provenance of the measurement (ISA string from the kernel, CPU
  model, Python/compiler versions, variant switches, wall time).  Without this a
  cross-architecture comparison degenerates into anonymous number soup.
* ``fields`` — every emitted quantity as an exact ``%a`` hexadecimal token plus
  its kind, so nothing is ever re-parsed from a rounded decimal.
* ``digest`` — a single SHA-256 over the ordered ``name=kind:token`` triples,
  which makes "the two files are the same measurement" a one-line check.

The record is deliberately plain JSON (no pickles, no binary blobs) so it can be
archived as evidence and diffed in review.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from . import fixtures, numerics

INT = fixtures.INT
FP = fixtures.FP
DIAG = fixtures.DIAG

SCHEMA_VERSION = "1.0"


def _compiler_version() -> str:
    try:
        out = subprocess.run(["cc", "--version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0].strip() if out.stdout else "unknown"
    except Exception:                                    # pragma: no cover
        return "unavailable"


def provenance(arm: str, arith: str = "native", sum_variant: str = "naive",
               libm: str = "stdlib") -> Dict[str, Any]:
    return {
        "arm": arm,
        "machine": platform.machine(),
        "system": f"{platform.system()} {platform.release()}",
        "node": platform.node(),
        "python": sys.version.split()[0],
        "compiler": _compiler_version(),
        "cpu_model": _cpu_model(),
        "variants": {"arith": arith, "sum": sum_variant, "libm": libm},
        "timestamp_unix": int(time.time()),
    }


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "cpu model")):
                return line.split(":", 1)[1].strip()
    except Exception:                                    # pragma: no cover
        pass
    return platform.processor() or "unknown"


def encode_fields(fields: Mapping[str, Tuple[str, Any]]) -> Dict[str, Dict[str, Any]]:
    encoded: Dict[str, Dict[str, Any]] = {}
    for name in sorted(fields):
        kind, value = fields[name]
        if kind == fixtures.INT:
            encoded[name] = {"kind": kind, "int": int(value)}
        else:
            encoded[name] = {
                "kind": kind,
                "hex": numerics.hex_token(float(value)),
                "bits": format(numerics.bits(float(value)), "016x"),
            }
    return encoded


def field_digest(fields: Mapping[str, Mapping[str, Any]]) -> str:
    tokens = []
    for name in sorted(fields):
        row = fields[name]
        tokens.append(f"{name}={row['kind']}:{row.get('hex', row.get('int'))}")
    return numerics.sha256_of_tokens(tokens)


def build_record(arm: str, arith: str = "native", sum_variant: str = "naive",
                 libm: str = "stdlib") -> Dict[str, Any]:
    started = time.perf_counter()
    raw = fixtures.run_all(arith=arith, sum_variant=sum_variant, libm=libm)
    fields = encode_fields(raw)
    elapsed = time.perf_counter() - started
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance(arm, arith, sum_variant, libm),
        "elapsed_s": round(elapsed, 6),
        "field_count": len(fields),
        "digest": field_digest(fields),
        "fields": fields,
    }


def record_from_fields(arm: str, fields: Mapping[str, Tuple[str, Any]],
                       variants: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Wrap *any* ``{name: (kind, value)}`` mapping into a gate record.

    This is the integration point for real workloads: a benchmark that can emit
    its outputs as ``name -> float``/``name -> int`` needs three lines to become
    cross-architecture gateable —

        from tools.cross_arch_gate import record
        fields = {name: (record.FP, value) for name, value in my_benchmark().items()}
        record.save_record(record.record_from_fields("loong64-<job>", fields), out)

    — and nothing in the platform has to learn what those numbers mean.
    """
    encoded = encode_fields(fields)
    switches = dict(variants or {"arith": "native", "sum": "naive", "libm": "stdlib"})
    prov = provenance(arm, arith=switches.get("arith", "native"),
                      sum_variant=switches.get("sum", "naive"),
                      libm=switches.get("libm", "stdlib"))
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": prov,
        "elapsed_s": 0.0,
        "field_count": len(encoded),
        "digest": field_digest(encoded),
        "fields": encoded,
    }


def save_record(record: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_record(path: Path) -> Dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"record schema {record.get('schema_version')} != {SCHEMA_VERSION}")
    if field_digest(record["fields"]) != record["digest"]:
        raise ValueError(f"record digest mismatch in {path}")
    return record


def decode_field(row: Mapping[str, Any]) -> Any:
    if row["kind"] == fixtures.INT:
        return int(row["int"])
    return numerics.decode_hex(row["hex"])


def validate_record(record: Mapping[str, Any]) -> Optional[str]:
    """Return an error string when a record is structurally unusable."""
    if "fields" not in record or not record["fields"]:
        return "empty fields"
    for name, row in record["fields"].items():
        kind = row.get("kind")
        if kind not in (fixtures.INT, fixtures.FP, fixtures.DIAG):
            return f"{name}: unknown kind {kind!r}"
        if kind == fixtures.INT:
            if not isinstance(row.get("int"), (int, str)):
                return f"{name}: integer field without an integer payload"
            continue
        if "hex" not in row:
            return f"{name}: floating field without an exact %a token"
        try:
            numerics.decode_hex(row["hex"])
        except (ValueError, TypeError):
            return f"{name}: unparsable hex token {row['hex']!r}"
    return None