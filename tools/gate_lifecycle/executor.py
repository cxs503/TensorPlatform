"""Node executors: how a plan node is turned into measurements + declaration.

Two executors are provided:

``StaticExecutor``
    measurements are already in the plan (replay / regression fixtures).

``JsonCommandExecutor``
    runs a shell command per node, expects the last non-empty stdout line to be
    a JSON object of the form ``{"measurements": {...}, "declaration": {...}}``
    and enforces a timeout.  This is the "machine-computable criteria actually
    executed" path: the gate consumes real program output instead of a claim.

Both are deterministic and side-effect free from the pipeline's point of view;
the pipeline records a digest of the measurements it adjudicated so that a
replay can be audited (C6).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .model import Node, Plan


class Executor(Protocol):
    def run(self, node: Node, plan: Plan) -> Mapping[str, Any]:  # pragma: no cover
        ...


@dataclass
class StaticExecutor:
    """Serve measurements declared inside ``node.meta``."""

    def run(self, node: Node, plan: Plan) -> Mapping[str, Any]:
        meta = node.meta or {}
        return {
            "measurements": dict(meta.get("measurements", {})),
            "declaration": dict(meta.get("declaration", {})),
        }


@dataclass
class JsonCommandExecutor:
    """Run ``cmd`` per node with ``{node}``/``{plan}`` placeholders.

    Substitution is a plain ``str.replace`` on purpose: a command often embeds
    JSON braces (``python -c "{'measurements': ...}"``), which ``str.format``
    would try to interpret as fields.
    """

    cmd: str
    timeout: float = 300.0
    cwd: str | None = None
    env: Mapping[str, str] | None = None

    def run(self, node: Node, plan: Plan) -> Mapping[str, Any]:
        import os
        import shlex

        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        command = self.cmd.replace("{node}", node.node_id).replace("{plan}", plan.plan_id)
        proc = subprocess.run(
            shlex.split(command) if isinstance(command, str) else command,
            capture_output=True, text=True, cwd=self.cwd, env=env,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"node {node.node_id}: executor exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout)[-400:]}"
            )
        payload = _last_json_line(proc.stdout)
        if payload is None:
            raise RuntimeError(f"node {node.node_id}: executor produced no JSON payload")
        out = {"measurements": dict(payload.get("measurements", {}))}
        decl = dict(payload.get("declaration", {}))
        out["declaration"] = decl
        return out


def _last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        if line.startswith("{") and line.endswith("}"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None


@dataclass
class ReplayExecutor:
    """Deterministic fallback: derive measurements from the node's criteria.

    Used when a plan has no measurement source at all (dry-run of the dispatch
    layer).  It deliberately returns **no** measurements so that the gate ends
    in WITHHELD rather than manufacturing a PASS -- missing evidence must never
    be silently upgraded to success.
    """

    def run(self, node: Node, plan: Plan) -> Mapping[str, Any]:
        return {"measurements": {}, "declaration": {"note": "no measurement source"}}


def measurement_digest(payload: Mapping[str, Any]) -> str:
    from .model import digest

    return digest(payload)