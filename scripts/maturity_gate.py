#!/usr/bin/env python3
"""Fail-closed maturity evidence gate for foundation platform modules.

The gate never runs commands declared by evidence and never changes frontend
labels.  A passing evidence record must point to an existing, immutable
(SHA-256 checked) artifact inside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = Path("quality/maturity/policy.json")
DEFAULT_INVENTORY = Path("quality/maturity/modules.json")
REQUIRED_MODULE_FIELDS = {
    "id",
    "name",
    "owner",
    "frontend_label",
    "observed_status",
    "target_status",
    "basis",
    "known_gaps",
    "evidence",
}
PASS_EVIDENCE_FIELDS = {"status", "artifact_type", "artifact", "sha256", "observed_at", "summary"}


class ConfigurationError(ValueError):
    """Raised when policy or inventory structure is invalid."""


@dataclass(frozen=True)
class CheckResult:
    module_id: str
    module_name: str
    requested_level: str
    qualified_level: str
    passed: bool
    blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_name": self.module_name,
            "requested_level": self.requested_level,
            "qualified_level": self.qualified_level,
            "passed": self.passed,
            "blockers": list(self.blockers),
        }


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"top-level JSON must be an object: {path}")
    return value


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MaturityGate:
    def __init__(self, policy: dict[str, Any], inventory: dict[str, Any], repo_root: Path):
        self.policy = policy
        self.inventory = inventory
        self.repo_root = repo_root.resolve()
        self._validate_structure()

    @classmethod
    def from_files(cls, policy_path: Path, inventory_path: Path, repo_root: Path) -> "MaturityGate":
        return cls(load_json(policy_path), load_json(inventory_path), repo_root)

    @property
    def modules(self) -> list[dict[str, Any]]:
        return self.inventory["modules"]

    def _validate_structure(self) -> None:
        errors: list[str] = []
        levels = self.policy.get("levels")
        categories = self.policy.get("evidence_categories")
        statuses = self.policy.get("evidence_statuses")
        artifact_types = self.policy.get("artifact_types")
        category_artifact_types = self.policy.get("category_artifact_types")
        if self.policy.get("schema_version") != 1:
            errors.append("policy.schema_version must be 1")
        if not isinstance(levels, dict) or set(levels) != {"demo", "beta", "rc", "production"}:
            errors.append("policy.levels must define exactly demo, beta, rc and production")
            levels = {}
        if not isinstance(categories, dict) or not categories:
            errors.append("policy.evidence_categories must be a non-empty object")
            categories = {}
        if not isinstance(statuses, list) or "pass" not in statuses or "missing" not in statuses:
            errors.append("policy.evidence_statuses must include pass and missing")
            statuses = []
        if not isinstance(artifact_types, list) or not artifact_types:
            errors.append("policy.artifact_types must be a non-empty list")
            artifact_types = []
        if not isinstance(category_artifact_types, dict) or set(category_artifact_types) != set(categories):
            errors.append("policy.category_artifact_types must map every evidence category")
            category_artifact_types = {}
        else:
            for category, allowed in category_artifact_types.items():
                if not isinstance(allowed, list) or not allowed or any(item not in artifact_types for item in allowed):
                    errors.append(f"policy.category_artifact_types.{category} contains invalid artifact types")

        ranks: list[int] = []
        for level_id, definition in levels.items():
            if not isinstance(definition, dict):
                errors.append(f"policy.levels.{level_id} must be an object")
                continue
            rank = definition.get("rank")
            required = definition.get("required_evidence")
            if not isinstance(rank, int):
                errors.append(f"policy.levels.{level_id}.rank must be an integer")
            else:
                ranks.append(rank)
            if not isinstance(required, list) or any(item not in categories for item in required):
                errors.append(f"policy.levels.{level_id}.required_evidence contains unknown categories")
        if ranks and sorted(ranks) != list(range(len(ranks))):
            errors.append("policy level ranks must be consecutive from zero")

        if self.inventory.get("schema_version") != 1:
            errors.append("inventory.schema_version must be 1")
        modules = self.inventory.get("modules")
        if not isinstance(modules, list) or not modules:
            errors.append("inventory.modules must be a non-empty array")
            modules = []
        seen: set[str] = set()
        for index, module in enumerate(modules):
            prefix = f"modules[{index}]"
            if not isinstance(module, dict):
                errors.append(f"{prefix} must be an object")
                continue
            missing_fields = REQUIRED_MODULE_FIELDS - set(module)
            if missing_fields:
                errors.append(f"{prefix} missing fields: {', '.join(sorted(missing_fields))}")
            module_id = module.get("id")
            if not isinstance(module_id, str) or not module_id or not module_id.replace("_", "").isalnum():
                errors.append(f"{prefix}.id must contain only letters, digits and underscores")
            elif module_id in seen:
                errors.append(f"duplicate module id: {module_id}")
            else:
                seen.add(module_id)
            for key in ("name", "owner", "frontend_label", "basis"):
                if not isinstance(module.get(key), str) or not module.get(key, "").strip():
                    errors.append(f"{prefix}.{key} must be a non-empty string")
            observed = module.get("observed_status")
            target = module.get("target_status")
            if observed not in levels:
                errors.append(f"{prefix}.observed_status is not a policy level")
            if target not in levels:
                errors.append(f"{prefix}.target_status is not a policy level")
            if observed in levels and target in levels and levels[target]["rank"] < levels[observed]["rank"]:
                errors.append(f"{prefix}.target_status cannot be below observed_status")
            if not isinstance(module.get("known_gaps"), list) or any(
                not isinstance(item, str) or not item.strip() for item in module.get("known_gaps", [])
            ):
                errors.append(f"{prefix}.known_gaps must be an array of non-empty strings")
            evidence = module.get("evidence")
            if not isinstance(evidence, dict):
                errors.append(f"{prefix}.evidence must be an object")
                continue
            for category, record in evidence.items():
                if category not in categories:
                    errors.append(f"{prefix}.evidence contains unknown category: {category}")
                    continue
                if not isinstance(record, dict):
                    errors.append(f"{prefix}.evidence.{category} must be an object")
                    continue
                if record.get("status") not in statuses:
                    errors.append(f"{prefix}.evidence.{category}.status is invalid")
                if record.get("status") == "pass":
                    missing_pass = PASS_EVIDENCE_FIELDS - set(record)
                    if missing_pass:
                        errors.append(
                            f"{prefix}.evidence.{category} missing pass fields: "
                            f"{', '.join(sorted(missing_pass))}"
                        )
                    if record.get("artifact_type") not in artifact_types:
                        errors.append(f"{prefix}.evidence.{category}.artifact_type is invalid")
                    elif record.get("artifact_type") not in category_artifact_types.get(category, []):
                        errors.append(
                            f"{prefix}.evidence.{category}.artifact_type is not allowed for this category"
                        )
                    digest = record.get("sha256", "")
                    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        errors.append(f"{prefix}.evidence.{category}.sha256 must be lowercase SHA-256")
                    try:
                        parse_timestamp(record.get("observed_at"))
                    except (TypeError, ValueError) as exc:
                        errors.append(f"{prefix}.evidence.{category}.observed_at invalid: {exc}")
                    if not isinstance(record.get("summary"), str) or not record.get("summary", "").strip():
                        errors.append(f"{prefix}.evidence.{category}.summary must be non-empty")
        if errors:
            raise ConfigurationError("\n".join(errors))

    def _artifact_error(self, record: dict[str, Any], module_id: str, category: str) -> str | None:
        raw_path = record.get("artifact")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "artifact path is empty"
        path = (self.repo_root / raw_path).resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError:
            return "artifact is outside repository"
        if not path.is_file():
            return f"artifact not found: {raw_path}"
        if path.suffix.lower() != ".json":
            return "artifact must be a machine-readable JSON report"
        actual = sha256_file(path)
        if actual != record["sha256"]:
            return f"artifact checksum mismatch: {raw_path}"
        try:
            report = load_json(path)
        except ConfigurationError as exc:
            return str(exc)
        if report.get("schema_version") != 1:
            return "artifact.schema_version must be 1"
        if report.get("module_id") != module_id:
            return f"artifact.module_id must be {module_id}"
        report_categories = report.get("evidence_categories")
        if not isinstance(report_categories, list) or category not in report_categories:
            return f"artifact does not declare evidence category: {category}"
        if report.get("passed") is not True:
            return "artifact.passed must be true"
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks:
            return "artifact.checks must be a non-empty array"
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                return f"artifact.checks[{index}] must be an object"
            if not isinstance(check.get("id"), str) or not check.get("id", "").strip():
                return f"artifact.checks[{index}].id must be non-empty"
            if check.get("passed") is not True:
                return f"artifact.checks[{index}] did not pass"
        environment = report.get("environment")
        if not isinstance(environment, str) or not environment.strip():
            return "artifact.environment must be non-empty"
        data_classification = report.get("data_classification")
        allowed_data = {"fixture", "synthetic", "replay", "anonymized", "production"}
        if data_classification not in allowed_data:
            return "artifact.data_classification is invalid"
        if category == "production_data" and data_classification not in {"anonymized", "production"}:
            return "production_data evidence requires anonymized or production data"
        for timestamp_field in ("started_at", "completed_at"):
            try:
                parse_timestamp(report.get(timestamp_field))
            except (TypeError, ValueError) as exc:
                return f"artifact.{timestamp_field} invalid: {exc}"
        valid_until = record.get("valid_until")
        if valid_until is not None:
            try:
                expiry = parse_timestamp(valid_until)
            except (TypeError, ValueError) as exc:
                return f"valid_until invalid: {exc}"
            if expiry <= datetime.now(timezone.utc):
                return f"evidence expired at {valid_until}"
        return None

    def _category_error(self, module: dict[str, Any], category: str) -> str | None:
        record = module["evidence"].get(category)
        if record is None:
            return "no evidence record"
        status = record.get("status")
        if status != "pass":
            note = record.get("summary") or record.get("reason") or "no passing artifact"
            return f"status={status}: {note}"
        return self._artifact_error(record, module["id"], category)

    def qualified_level(self, module: dict[str, Any]) -> str:
        ordered = sorted(self.policy["levels"].items(), key=lambda item: item[1]["rank"])
        qualified = "demo"
        for level_id, definition in ordered:
            if all(self._category_error(module, category) is None for category in definition["required_evidence"]):
                qualified = level_id
            else:
                break
        return qualified

    def check(self, module: dict[str, Any], requested_level: str) -> CheckResult:
        required = self.policy["levels"][requested_level]["required_evidence"]
        blockers = tuple(
            f"{category}: {error}"
            for category in required
            if (error := self._category_error(module, category)) is not None
        )
        return CheckResult(
            module_id=module["id"],
            module_name=module["name"],
            requested_level=requested_level,
            qualified_level=self.qualified_level(module),
            passed=not blockers,
            blockers=blockers,
        )

    def check_all(self, requested_field: str, selected: Iterable[str] = ()) -> list[CheckResult]:
        selected_set = set(selected)
        known = {module["id"] for module in self.modules}
        unknown = selected_set - known
        if unknown:
            raise ConfigurationError(f"unknown module(s): {', '.join(sorted(unknown))}")
        results = []
        for module in self.modules:
            if selected_set and module["id"] not in selected_set:
                continue
            level = module[f"{requested_field}_status"]
            results.append(self.check(module, level))
        return results


def resolve_under(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def print_human(results: list[CheckResult]) -> None:
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(
            f"[{state}] {result.module_id}: requested={result.requested_level} "
            f"qualified={result.qualified_level}"
        )
        for blocker in result.blockers:
            print(f"  - {blocker}")
    passed = sum(result.passed for result in results)
    print(f"summary: {passed}/{len(results)} modules passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate module maturity claims against immutable evidence.")
    parser.add_argument("command", choices=("lint", "check", "report"), nargs="?", default="check")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--level", choices=("observed", "target"), default="target")
    parser.add_argument("--module", action="append", default=[], help="Module id; repeat to select multiple.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    try:
        gate = MaturityGate.from_files(
            resolve_under(root, args.policy),
            resolve_under(root, args.inventory),
            root,
        )
        if args.command == "lint":
            print("maturity policy and inventory structure: valid")
            return 0
        results = gate.check_all(args.level, args.module)
    except ConfigurationError as exc:
        print(f"maturity gate configuration error:\n{exc}", file=sys.stderr)
        return 2

    payload = {
        "policy_id": gate.policy["policy_id"],
        "checked_claim": args.level,
        "passed": all(result.passed for result in results),
        "results": [result.as_dict() for result in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(results)
    # `report` is still fail-closed: it differs only in intent/readability, not exit policy.
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
