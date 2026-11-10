"""Generator execution and payload validation (domain-neutral).

Contract
--------
A generator is a standalone script invoked as::

    python3 <generator.py> --params-json '<json>' --out <payload.json>

and it must write a payload object::

    {"schema": "evidence-pipeline/payload/1",
     "node": "<same node id as the benchmark>",
     "metrics": {"<name>": <finite float>, ...},
     "fields": {"<role>": {"mesh": {"lo": [...], "hi": [...], "name": "..."},
                           "values": [...], "kind": "density", "name": "..."}},
     "notes": "<free text>"}

The runner never imports the generator: it is executed in a subprocess with a
deterministic environment (``PYTHONHASHSEED=0``, repo root on ``PYTHONPATH``)
and a hard timeout, so a crashing or hanging generator can only ever produce a
*failed* run, never a half-written artifact.

Payload validation is strict and fail-closed: a missing metric, a non-finite
number, a wrong node id or a non-numeric value is reported as an error rather
than silently coerced.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import PAYLOAD_SCHEMA, PipelineError
from .config import ROOT, BenchmarkConfig

FIELD_KINDS: Tuple[str, ...] = ("density", "extensive", "flux", "scalar")


@dataclass
class ExecResult:
    """Outcome of one generator execution."""

    returncode: Optional[int]
    payload: Optional[Dict[str, Any]]
    payload_path: str
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.payload is not None and not self.timed_out


def generator_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # keep the child from picking up user site-packages differences
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def execute_generator(
    cfg: BenchmarkConfig,
    out_path: Optional[str | Path] = None,
    workdir: Optional[str | Path] = None,
) -> ExecResult:
    """Execute the configured generator once and read back the payload."""
    gen = cfg.generator_path
    if not gen.is_file():
        raise PipelineError(f"generator not found: {gen} (fail-closed)")

    tmpdir = None
    if out_path is None:
        tmpdir = tempfile.mkdtemp(prefix="evidence_pipeline_")
        out_path = Path(tmpdir) / "payload.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()  # a stale payload must never be mistaken for output

    params_json = json.dumps(cfg.params, sort_keys=True)
    cmd = [sys.executable, str(gen), "--params-json", params_json, "--out", str(out_path)]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir or ROOT),
            env=generator_env(),
            capture_output=True,
            text=True,
            timeout=float(cfg.timeout_s),
        )
        returncode: Optional[int] = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = time.time() - started

    payload: Optional[Dict[str, Any]] = None
    if out_path.is_file():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
    del tmpdir
    return ExecResult(
        returncode=returncode,
        payload=payload,
        payload_path=str(out_path),
        stdout=stdout[-4000:],
        stderr=stderr[-4000:],
        duration_s=duration,
        timed_out=timed_out,
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_payload(payload: Any, cfg: BenchmarkConfig) -> List[str]:
    """Return a list of contract violations (empty list == valid)."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]
    if payload.get("schema") != PAYLOAD_SCHEMA:
        errors.append(f"payload.schema must be {PAYLOAD_SCHEMA!r}, got {payload.get('schema')!r}")
    if payload.get("node") != cfg.node:
        errors.append(f"payload.node {payload.get('node')!r} != benchmark node {cfg.node!r}")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        errors.append("payload.metrics must be a non-empty object")
        metrics = {}
    for name, value in metrics.items():
        if not _is_number(value):
            errors.append(f"metric {name!r} is not a number ({value!r})")
        elif not math.isfinite(float(value)):
            errors.append(f"metric {name!r} is not finite ({value!r})")
    fields = payload.get("fields", {})
    if not isinstance(fields, dict):
        errors.append("payload.fields must be an object when present")
        fields = {}
    for role, desc in fields.items():
        if not isinstance(desc, dict):
            errors.append(f"field {role!r} must be an object")
            continue
        if "mesh" not in desc or "values" not in desc:
            errors.append(f"field {role!r} needs 'mesh' and 'values'")
            continue
        kind = desc.get("kind", "density")
        if kind not in FIELD_KINDS:
            errors.append(f"field {role!r} has unsupported kind {kind!r}")
        mesh = desc.get("mesh")
        if not isinstance(mesh, dict) or "lo" not in mesh or "hi" not in mesh:
            errors.append(f"field {role!r}.mesh needs 'lo' and 'hi'")
        values = desc.get("values")
        if not isinstance(values, list) or not values:
            errors.append(f"field {role!r}.values must be a non-empty list")
        elif not all(_is_number(v) and math.isfinite(float(v)) for v in values):
            errors.append(f"field {role!r}.values must be finite numbers")
    if errors:
        return errors
    return []


def run_and_validate(cfg: BenchmarkConfig, out_path: Optional[str | Path] = None) -> ExecResult:
    """Execute once; raise :class:`PipelineError` if the payload is invalid."""
    result = execute_generator(cfg, out_path=out_path)
    if not result.ok:
        return result
    errors = validate_payload(result.payload, cfg)
    if errors:
        raise PipelineError("; ".join(errors))
    return result