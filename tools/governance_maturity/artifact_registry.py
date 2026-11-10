"""Artifact schema registry with producer/consumer contracts.

The registry answers one question mechanically: *does this upstream artifact
satisfy the contract the downstream module declared?*

Envelope (the only shape the registry validates)::

    {
      "artifact_type": "metric_row",
      "schema_version": "1.1.0",
      "payload": {...},
      "checksum": "sha256:<64 hex>",          # over canonical payload JSON
      "provenance": {
         "producer": "repo::module",           # must be a registered producer
         "created_at": "2026-11-10T12:00:00Z",
         "source_digest": "sha256:<64 hex>",   # digest of the raw upstream file
         "tool_version": "governance-maturity/1.0"
      }
    }

Schemas are data (JSON), not code: :func:`load_registry` reads a document with
``schemas`` and ``consumers``.  Field types are intentionally small
(number/integer/string/boolean/object/array) so the checker is auditable.

Zero domain semantics: this module never mentions a physical quantity, unit or
case name; units are opaque tags supplied by the schema document.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.\-]+)?$")
HEX64_RE = re.compile(r"^sha256:([0-9a-f]{64})$")

ARTIFACT_TYPES = ("number", "integer", "string", "boolean", "object", "array")


# --------------------------------------------------------------------------- #
# canonical hashing
# --------------------------------------------------------------------------- #
def canonical_payload_digest(payload: Mapping[str, Any]) -> str:
    """sha256 over canonical JSON (sorted keys, no whitespace)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_digest(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def parse_semver(version: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(str(version).strip())
    if not m:
        raise ValueError(f"not a semantic version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def semver_matches(version: str, major: int, min_minor: int = 0) -> bool:
    """Consumer contract: same major, minor >= min_minor."""
    try:
        vmajor, vminor, _ = parse_semver(version)
    except ValueError:
        return False
    return vmajor == major and vminor >= min_minor


# --------------------------------------------------------------------------- #
# schema model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool = True
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[Any, ...] | None = None
    items_type: str | None = None

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "FieldSpec":
        ftype = str(doc.get("type", "number"))
        if ftype not in ARTIFACT_TYPES:
            raise ValueError(f"field {doc.get('name')!r}: unsupported type {ftype!r}")
        enum = doc.get("enum")
        return FieldSpec(
            name=str(doc["name"]),
            type=ftype,
            required=bool(doc.get("required", True)),
            unit=doc.get("unit"),
            minimum=None if doc.get("minimum") is None else float(doc["minimum"]),
            maximum=None if doc.get("maximum") is None else float(doc["maximum"]),
            enum=None if enum is None else tuple(enum),
            items_type=doc.get("items_type"),
        )


@dataclass(frozen=True)
class ArtifactSchema:
    artifact_type: str
    version: str
    fields: tuple[FieldSpec, ...]
    producer: str  # "repo::module"
    consumers: tuple[str, ...] = ()
    strict_fields: bool = True

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "ArtifactSchema":
        parse_semver(doc.get("version", ""))
        fields = tuple(FieldSpec.from_dict(f) for f in doc.get("fields", []))
        names = [f.name for f in fields]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate field names in schema {doc.get('artifact_type')!r}")
        return ArtifactSchema(
            artifact_type=str(doc["artifact_type"]),
            version=str(doc["version"]),
            fields=fields,
            producer=str(doc["producer"]),
            consumers=tuple(doc.get("consumers", ())),
            strict_fields=bool(doc.get("strict_fields", True)),
        )

    @property
    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}


@dataclass(frozen=True)
class ConsumerContract:
    name: str
    repo: str
    module: str
    accepts: dict[str, dict[str, int]]  # artifact_type -> {"major": n, "min_minor": m}

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "ConsumerContract":
        accepts = {
            str(t): {"major": int(c.get("major", 1)), "min_minor": int(c.get("min_minor", 0))}
            for t, c in doc.get("accepts", {}).items()
        }
        return ConsumerContract(
            name=str(doc["name"]),
            repo=str(doc.get("repo", "")),
            module=str(doc.get("module", "")),
            accepts=accepts,
        )


@dataclass
class ValidationIssue:
    code: str
    path: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass
class ValidationReport:
    artifact_type: str | None
    schema_version: str | None
    consumer: str | None
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "consumer": self.consumer,
            "errors": [e.as_dict() for e in self.errors],
            "warnings": [w.as_dict() for w in self.warnings],
            "checks": dict(self.checks),
        }


class ArtifactRegistry:
    """Registry of artifact schemas plus consumer contracts."""

    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, ArtifactSchema]] = {}
        self._consumers: dict[str, ConsumerContract] = {}

    # -- registration ------------------------------------------------------ #
    def register(self, schema: ArtifactSchema) -> None:
        parse_semver(schema.version)
        self._schemas.setdefault(schema.artifact_type, {})[schema.version] = schema

    def register_consumer(self, contract: ConsumerContract) -> None:
        self._consumers[contract.name] = contract

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "ArtifactRegistry":
        reg = ArtifactRegistry()
        for sdoc in doc.get("schemas", ()):
            reg.register(ArtifactSchema.from_dict(sdoc))
        for cdoc in doc.get("consumers", ()):
            reg.register_consumer(ConsumerContract.from_dict(cdoc))
        if not reg._schemas:
            raise ValueError("registry document contains no schemas")
        return reg

    # -- lookup ------------------------------------------------------------ #
    def artifact_types(self) -> list[str]:
        return sorted(self._schemas)

    def versions(self, artifact_type: str) -> list[str]:
        return sorted(self._schemas.get(artifact_type, {}), key=parse_semver)

    def schema(self, artifact_type: str, version: str | None = None) -> ArtifactSchema:
        versions = self._schemas.get(artifact_type)
        if not versions:
            raise KeyError(f"unregistered artifact_type: {artifact_type!r}")
        if version is None:
            return versions[sorted(versions, key=parse_semver)[-1]]
        if version not in versions:
            raise KeyError(f"unregistered version {version!r} for {artifact_type!r}")
        return versions[version]

    def consumer(self, name: str) -> ConsumerContract:
        if name not in self._consumers:
            raise KeyError(f"unknown consumer contract: {name!r}")
        return self._consumers[name]

    def consumers(self) -> list[ConsumerContract]:
        return [self._consumers[k] for k in sorted(self._consumers)]

    # -- coverage ---------------------------------------------------------- #
    def coverage(self) -> dict[str, Any]:
        repos: set[str] = set()
        modules: set[str] = set()
        for versions in self._schemas.values():
            for schema in versions.values():
                repo, _, module = schema.producer.partition("::")
                repos.add(repo)
                modules.add(f"{repo}::{module}")
        for contract in self._consumers.values():
            repos.add(contract.repo)
            modules.add(f"{contract.repo}::{contract.module}")
        return {
            "artifact_types": len(self._schemas),
            "schemas": sum(len(v) for v in self._schemas.values()),
            "producers": len({s.producer for v in self._schemas.values() for s in v.values()}),
            "consumers": len(self._consumers),
            "repos": sorted(repos),
            "modules": sorted(modules),
            "n_repos": len(repos),
            "n_modules": len(modules),
        }

    # -- validation -------------------------------------------------------- #
    def validate(
        self,
        envelope: Mapping[str, Any],
        consumer: str | None = None,
    ) -> ValidationReport:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        checks: dict[str, bool] = {}

        def err(code: str, path: str, detail: str) -> None:
            errors.append(ValidationIssue(code, path, detail))

        atype = envelope.get("artifact_type")
        version = envelope.get("schema_version")
        report = ValidationReport(
            artifact_type=atype if isinstance(atype, str) else None,
            schema_version=version if isinstance(version, str) else None,
            consumer=consumer,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )

        # E1 envelope shape
        for key in ("artifact_type", "schema_version", "payload", "checksum", "provenance"):
            if key not in envelope:
                err("envelope_missing_key", key, "required envelope key absent")
        checks["envelope_shape"] = not errors
        if errors:
            return report

        # E2 schema resolution
        try:
            schema = self.schema(str(atype), str(version))
        except KeyError as exc:
            err("schema_unknown", "schema_version", str(exc))
            checks["schema_resolution"] = False
            return report
        checks["schema_resolution"] = True

        # E3 consumer version compatibility
        if consumer is not None:
            try:
                contract = self.consumer(consumer)
            except KeyError as exc:
                err("consumer_unknown", "consumer", str(exc))
                checks["consumer_compatibility"] = False
                contract = None
            if contract is not None:
                if str(atype) not in contract.accepts:
                    err(
                        "consumer_rejects_type",
                        "artifact_type",
                        f"consumer {contract.name!r} does not accept {atype!r}",
                    )
                    checks["consumer_compatibility"] = False
                else:
                    rule = contract.accepts[str(atype)]
                    if not semver_matches(str(version), rule["major"], rule["min_minor"]):
                        err(
                            "consumer_version_mismatch",
                            "schema_version",
                            f"consumer {contract.name!r} accepts major {rule['major']} "
                            f">= minor {rule['min_minor']}, got {version}",
                        )
                        checks["consumer_compatibility"] = False
                    else:
                        checks["consumer_compatibility"] = True
        else:
            checks["consumer_compatibility"] = True

        # E4 checksum integrity
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            err("payload_not_object", "payload", "payload must be a JSON object")
            checks["checksum_integrity"] = False
        else:
            actual = canonical_payload_digest(payload)
            checks["checksum_integrity"] = actual == envelope.get("checksum")
            if not checks["checksum_integrity"]:
                err(
                    "checksum_mismatch",
                    "checksum",
                    f"declared {envelope.get('checksum')!r} != recomputed {actual!r}",
                )

        # E5 provenance
        prov = envelope.get("provenance")
        checks["provenance"] = True
        if not isinstance(prov, Mapping):
            err("provenance_not_object", "provenance", "provenance must be a JSON object")
            checks["provenance"] = False
        else:
            producer = prov.get("producer")
            if not isinstance(producer, str) or "::" not in producer:
                err("producer_malformed", "provenance.producer", "expected 'repo::module'")
                checks["provenance"] = False
            elif producer != schema.producer:
                err(
                    "producer_unregistered",
                    "provenance.producer",
                    f"{producer!r} is not the registered producer {schema.producer!r}",
                )
                checks["provenance"] = False
            digest = prov.get("source_digest")
            if not isinstance(digest, str) or not HEX64_RE.match(digest):
                err("source_digest_malformed", "provenance.source_digest", "expected sha256:<64 hex>")
                checks["provenance"] = False
            created = prov.get("created_at")
            if not isinstance(created, str) or not _is_iso8601(created):
                err("created_at_malformed", "provenance.created_at", "expected ISO-8601 UTC")
                checks["provenance"] = False

        # E6 field-level contract
        checks["field_contract"] = True
        if isinstance(payload, Mapping):
            problems = _validate_fields(payload, schema)
            for code, path, detail in problems:
                err(code, path, detail)
            checks["field_contract"] = not problems
            extra = set(payload) - {f.name for f in schema.fields}
            if extra and schema.strict_fields:
                for name in sorted(extra):
                    err("field_unexpected", f"payload.{name}", "unknown field under strict schema")
                checks["field_contract"] = False
            elif extra:
                for name in sorted(extra):
                    warnings.append(
                        ValidationIssue("field_unexpected", f"payload.{name}", "ignored (non-strict)")
                    )

        return report


def _is_iso8601(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _validate_fields(payload: Mapping[str, Any], schema: ArtifactSchema) -> list[tuple[str, str, str]]:
    problems: list[tuple[str, str, str]] = []
    for spec in schema.fields:
        path = f"payload.{spec.name}"
        if spec.name not in payload:
            if spec.required:
                problems.append(("field_missing", path, "required field absent"))
            continue
        value = payload[spec.name]
        ok = True
        if spec.type == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            if ok and not math.isfinite(float(value)):
                problems.append(("field_not_finite", path, "value is NaN or infinite"))
                ok = False
        elif spec.type == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif spec.type == "string":
            ok = isinstance(value, str)
        elif spec.type == "boolean":
            ok = isinstance(value, bool)
        elif spec.type == "object":
            ok = isinstance(value, Mapping)
        elif spec.type == "array":
            ok = isinstance(value, list)
            if ok and spec.items_type:
                for i, item in enumerate(value):
                    if not _is_type(item, spec.items_type):
                        problems.append(
                            ("array_item_type", f"{path}[{i}]", f"expected {spec.items_type}")
                        )
                        ok = False
        if not ok:
            if not any(p[1].startswith(path) for p in problems):
                problems.append(("field_type", path, f"expected {spec.type}"))
            continue
        if spec.unit is not None:
            unit = payload.get(f"{spec.name}__unit")
            if unit is not None and str(unit) != spec.unit:
                problems.append(
                    ("field_unit_mismatch", f"{path}__unit", f"expected {spec.unit!r}, got {unit!r}")
                )
        if spec.enum is not None and value not in spec.enum:
            problems.append(("field_enum", path, f"value {value!r} not in {list(spec.enum)!r}"))
        if spec.type in ("number", "integer"):
            if spec.minimum is not None and float(value) < spec.minimum:
                problems.append(("field_below_minimum", path, f"{value} < {spec.minimum}"))
            if spec.maximum is not None and float(value) > spec.maximum:
                problems.append(("field_above_maximum", path, f"{value} > {spec.maximum}"))
    return problems


def _is_type(value: Any, type_name: str) -> bool:
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    return False


def make_envelope(
    registry: ArtifactRegistry,
    artifact_type: str,
    payload: Mapping[str, Any],
    producer: str,
    source_digest: str,
    created_at: str | None = None,
    schema_version: str | None = None,
    tool_version: str = "governance-maturity/1.0",
) -> dict[str, Any]:
    """Build a canonical envelope; producer must match the registered schema."""
    schema = registry.schema(artifact_type, schema_version)
    if producer != schema.producer:
        raise ValueError(f"producer {producer!r} != registered {schema.producer!r}")
    created = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "artifact_type": artifact_type,
        "schema_version": schema.version,
        "payload": dict(payload),
        "checksum": canonical_payload_digest(payload),
        "provenance": {
            "producer": producer,
            "created_at": created,
            "source_digest": source_digest,
            "tool_version": tool_version,
        },
    }


def load_registry(path: str | Path) -> ArtifactRegistry:
    with open(path, encoding="utf-8") as fh:
        return ArtifactRegistry.from_dict(json.load(fh))