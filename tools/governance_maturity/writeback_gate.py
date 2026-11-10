"""Metric write-back gate: provenance, self-consistency, freshness, idempotency.

A metric ledger is only trustworthy if a write cannot

* invent a value without a registered source artifact and a recomputable
  expression (``provenance`` / ``recompute``);
* break a declared identity between derived and base quantities
  (``identity``);
* be replayed after its freshness SLO expired (``freshness``);
* be appended twice because a client retried (``idempotency``);
* silently roll a KPI back to an older value without new evidence
  (``rollback``), or move it the wrong way against its declared direction.

The store is an append-only JSON line ledger with a sha256 hash chain, so any
edit to history is detectable.  Idempotency keys are derived from the *content*
(metric, window, value, source digest, expression), which makes a retry a no-op
and returns the original receipt.

No metric name, expression or unit is hard-coded: they arrive in the write and
the registry document.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_ALLOWED_BINOPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_ALLOWED_UNARY: dict[type, Callable[[float], float]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_ALLOWED_FUNCS: dict[str, Callable[[float], float]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}


class WritebackRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    """Declared relation between inputs and a target field.

    ``target == "value"`` asserts the declared write value equals
    ``expression(inputs)``; any other target names an input field that must
    equal the expression.
    """

    name: str
    expression: str
    fields: tuple[str, ...]
    target: str = "value"

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "Identity":
        return Identity(
            name=str(doc["name"]),
            expression=str(doc["expression"]),
            fields=tuple(doc.get("fields", ())),
            target=str(doc.get("target", "value")),
        )


def evaluate_expression(expression: str, values: Mapping[str, float]) -> float:
    """Evaluate a numeric expression over ``values`` without ``eval``."""
    tree = ast.parse(expression, mode="eval")

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return float(node.value)
            raise WritebackRejected("expression_invalid", f"constant {node.value!r} not numeric")
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise WritebackRejected("expression_unknown_field", f"missing value {node.id!r}")
            return float(values[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _ALLOWED_FUNCS and len(node.args) == 1:
            return float(_ALLOWED_FUNCS[node.func.id](walk(node.args[0])))
        raise WritebackRejected("expression_invalid", f"unsupported node {type(node).__name__}")

    try:
        result = walk(tree)
    except (ZeroDivisionError, OverflowError) as exc:
        raise WritebackRejected("expression_nonfinite", str(exc)) from exc
    if not math.isfinite(result):
        raise WritebackRejected("expression_nonfinite", f"expression {expression!r} is not finite")
    return result


@dataclass
class MetricWrite:
    metric: str
    window: str
    value: float
    unit: str
    expression: str
    inputs: dict[str, float]
    source_digest: str
    evidence_count: int
    written_at: str | None = None

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "MetricWrite":
        return MetricWrite(
            metric=str(doc["metric"]),
            window=str(doc["window"]),
            value=float(doc["value"]),
            unit=str(doc["unit"]),
            expression=str(doc["expression"]),
            inputs={str(k): float(v) for k, v in dict(doc.get("inputs", {})).items()},
            source_digest=str(doc["source_digest"]),
            evidence_count=int(doc.get("evidence_count", 0)),
            written_at=doc.get("written_at"),
        )

    def content_key(self) -> str:
        blob = json.dumps(
            {
                "metric": self.metric,
                "window": self.window,
                "value": self.value,
                "unit": self.unit,
                "expression": self.expression,
                "inputs": self.inputs,
                "source_digest": self.source_digest,
                "evidence_count": self.evidence_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class GateConfig:
    recompute_rtol: float = 1e-9
    regression_rtol: float = 1e-6
    freshness_slo_s: float = 24 * 3600.0
    allowed_units: tuple[str, ...] | None = None
    identities: tuple[Identity, ...] = ()
    direction: dict[str, str] = field(default_factory=dict)  # metric -> "up"|"down"

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "GateConfig":
        return GateConfig(
            recompute_rtol=float(doc.get("recompute_rtol", 1e-9)),
            regression_rtol=float(doc.get("regression_rtol", 1e-6)),
            freshness_slo_s=float(doc.get("freshness_slo_s", 24 * 3600.0)),
            allowed_units=None if doc.get("allowed_units") is None else tuple(doc["allowed_units"]),
            identities=tuple(Identity.from_dict(x) for x in doc.get("identities", ())),
            direction={str(k): str(v) for k, v in dict(doc.get("direction", {})).items()},
        )


class MetricWritebackGate:
    """Validate and append metric writes to an idempotent, hash-chained ledger."""

    def __init__(self, config: GateConfig, store_path: str | Path) -> None:
        self.config = config
        self.store_path = Path(store_path)
        self.records: list[dict[str, Any]] = []
        if self.store_path.exists():
            for line in self.store_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.records.append(json.loads(line))

    # -- ledger ------------------------------------------------------------ #
    def head_digest(self) -> str:
        return self.records[-1]["chain"] if self.records else "sha256:" + "0" * 64

    def _chain(self, receipt: Mapping[str, Any], previous: str) -> str:
        blob = json.dumps({"receipt": receipt, "previous": previous}, sort_keys=True,
                          separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def history(self, metric: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["metric"] == metric]

    def latest(self, metric: str) -> dict[str, Any] | None:
        rows = self.history(metric)
        return rows[-1] if rows else None

    # -- validation -------------------------------------------------------- #
    def check(self, write: MetricWrite, now: str | None = None) -> dict[str, Any]:
        cfg = self.config
        if cfg.allowed_units is not None and write.unit not in cfg.allowed_units:
            raise WritebackRejected("unit_not_allowed", f"{write.unit!r} not in allow-list")
        if not write.source_digest.startswith("sha256:") or len(write.source_digest) != 71:
            raise WritebackRejected("provenance_malformed", "source_digest must be sha256:<64 hex>")
        if write.evidence_count < 0:
            raise WritebackRejected("evidence_negative", "evidence_count < 0")
        if not math.isfinite(write.value):
            raise WritebackRejected("value_nonfinite", "value is NaN/inf")

        # 1. recompute from declared inputs
        recomputed = evaluate_expression(write.expression, write.inputs)
        tol = cfg.recompute_rtol * max(1.0, abs(recomputed))
        if abs(recomputed - write.value) > tol:
            raise WritebackRejected(
                "recompute_mismatch", f"declared {write.value} != recomputed {recomputed}"
            )

        # 2. derived self-consistency identities
        identity_checks: list[dict[str, Any]] = []
        for identity in cfg.identities:
            values = {k: write.inputs[k] for k in identity.fields if k in write.inputs}
            if len(values) < len(identity.fields):
                continue
            expected = evaluate_expression(identity.expression, values)
            if identity.target == "value":
                actual = write.value
            elif identity.target in write.inputs:
                actual = float(write.inputs[identity.target])
            else:
                continue
            tol_i = cfg.recompute_rtol * max(1.0, abs(expected))
            if abs(actual - expected) > tol_i:
                raise WritebackRejected(
                    "identity_violation",
                    f"{identity.name}: declared {actual} != expression {expected}",
                )
            identity_checks.append({"name": identity.name, "expected": expected, "actual": actual})

        # 3. freshness SLO
        stamp = write.written_at or now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            written = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WritebackRejected("written_at_malformed", str(exc)) from exc
        if written.tzinfo is None:
            written = written.replace(tzinfo=timezone.utc)
        reference = datetime.now(timezone.utc) if now is None else \
            datetime.fromisoformat(now.replace("Z", "+00:00"))
        age = (reference - written).total_seconds()
        if age > cfg.freshness_slo_s:
            raise WritebackRejected("stale_write", f"age {age:.1f}s > SLO {cfg.freshness_slo_s:.0f}s")
        if age < -cfg.freshness_slo_s:
            raise WritebackRejected("future_write", f"written_at is {-age:.1f}s in the future")

        # 4. regression / rollback detection against the accepted history
        previous = self.latest(write.metric)
        regression = None
        if previous is not None:
            prev_value = float(previous["value"])
            changed = abs(write.value - prev_value) > cfg.regression_rtol * max(1.0, abs(prev_value))
            evidence_grew = write.evidence_count > int(previous["evidence_count"])
            if changed:
                rolled_back = any(
                    abs(write.value - float(r["value"])) <= cfg.regression_rtol * max(1.0, abs(write.value))
                    for r in self.history(write.metric)
                )
                if rolled_back:
                    raise WritebackRejected(
                        "rollback_to_historical_value",
                        f"{prev_value} -> {write.value} repeats a historical value",
                    )
                if not evidence_grew:
                    raise WritebackRejected(
                        "regression_without_new_evidence",
                        f"{prev_value} -> {write.value}, evidence {previous['evidence_count']} -> {write.evidence_count}",
                    )
                direction = cfg.direction.get(write.metric)
                if direction == "up" and write.value < prev_value:
                    raise WritebackRejected("direction_violation", f"declared up, {prev_value} -> {write.value}")
                if direction == "down" and write.value > prev_value:
                    raise WritebackRejected("direction_violation", f"declared down, {prev_value} -> {write.value}")
                regression = {
                    "previous": prev_value,
                    "current": write.value,
                    "evidence_previous": int(previous["evidence_count"]),
                    "evidence_current": write.evidence_count,
                }

        return {
            "recomputed": recomputed,
            "identities": identity_checks,
            "regression": regression,
            "written_at": stamp,
        }

    # -- write ------------------------------------------------------------- #
    def write(self, write: MetricWrite, now: str | None = None) -> dict[str, Any]:
        key = write.content_key()
        existing = next((r for r in self.records if r["idempotency_key"] == key), None)
        if existing is not None:
            return {
                "status": "duplicate",
                "idempotency_key": key,
                "chain": existing["chain"],
                "receipt": existing,
                "ledger_length": len(self.records),
            }
        checked = self.check(write, now=now)
        receipt = {
            "metric": write.metric,
            "window": write.window,
            "value": write.value,
            "unit": write.unit,
            "expression": write.expression,
            "inputs": dict(write.inputs),
            "source_digest": write.source_digest,
            "evidence_count": write.evidence_count,
            "written_at": checked["written_at"],
            "recomputed": checked["recomputed"],
            "idempotency_key": key,
            "previous": self.head_digest(),
        }
        receipt["chain"] = self._chain(receipt, receipt["previous"])
        self.records.append(receipt)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True) + "\n")
        return {
            "status": "accepted",
            "idempotency_key": key,
            "chain": receipt["chain"],
            "receipt": receipt,
            "ledger_length": len(self.records),
        }

    def verify_chain(self) -> dict[str, Any]:
        previous = "sha256:" + "0" * 64
        for i, record in enumerate(self.records):
            body = {k: v for k, v in record.items() if k != "chain"}
            if body.get("previous") != previous:
                return {"ok": False, "broken_at": i, "reason": "previous_link"}
            if self._chain(body, previous) != record["chain"]:
                return {"ok": False, "broken_at": i, "reason": "digest"}
            previous = record["chain"]
        return {"ok": True, "length": len(self.records), "head": previous}