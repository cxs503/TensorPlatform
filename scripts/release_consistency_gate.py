#!/usr/bin/env python3
"""Fail-closed, side-effect-free release consistency validation."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_SERVICES = ("backend", "celery-worker", "celery-beat")
REQUIRED_SERVICES = (*BACKEND_SERVICES, "frontend")
REQUIRED_MIGRATION = "016_pipeline_tenant_isolation.sql"
IMMUTABLE_IMAGE = re.compile(r".+@sha256:[0-9a-f]{64}$")
CODE_TARGETS = ("/app/api", "/app/models", "/app/services", "/app/tasks", "/app/utils", "/app/main.py", "/app/database")


def _env(environment: Any) -> dict[str, str]:
    if isinstance(environment, dict):
        return {str(key): "" if value is None else str(value) for key, value in environment.items()}
    result: dict[str, str] = {}
    for item in environment or []:
        key, separator, value = str(item).partition("=")
        result[key] = value if separator else ""
    return result


def _target(volume: Any) -> tuple[str | None, str | None]:
    if isinstance(volume, dict):
        return volume.get("type"), volume.get("target")
    fields = str(volume).split(":")
    return ("bind" if fields and fields[0].startswith((".", "/")) else "volume", fields[1] if len(fields) > 1 else None)


def _is_code_target(target: str | None) -> bool:
    return bool(target) and any(target == prefix or target.startswith(prefix + "/") for prefix in CODE_TARGETS)


def validate(compose: dict[str, Any], migration_status: dict[str, Any], inspect: list[dict[str, Any]] | None = None) -> list[str]:
    """Return every violation without exposing env values or credentials."""
    violations: list[str] = []
    services = compose.get("services") or {}
    expected_image: str | None = None
    expected_revision: str | None = None
    for name in REQUIRED_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict):
            violations.append(f"missing required service: {name}")
            continue
        image = str(service.get("image") or "")
        if not IMMUTABLE_IMAGE.fullmatch(image):
            violations.append(f"{name}: image is not an immutable sha256 digest")
        if service.get("build") not in (None, False, ""):
            violations.append(f"{name}: build is present; release must use a prebuilt image")
        if expected_image is None:
            expected_image = image
        elif name in BACKEND_SERVICES and image != expected_image:
            violations.append(f"{name}: image differs from backend")
        revision = _env(service.get("environment")).get("ODF_RELEASE_REVISION", "")
        if not revision:
            violations.append(f"{name}: ODF_RELEASE_REVISION is missing")
        elif expected_revision is None:
            expected_revision = revision
        elif revision != expected_revision:
            violations.append(f"{name}: release revision differs from backend")
        for volume in service.get("volumes") or []:
            volume_type, target = _target(volume)
            if volume_type == "bind" and _is_code_target(target):
                violations.append(f"{name}: bind mount masks executable application path {target}")

    applied = migration_status.get("applied") if isinstance(migration_status, dict) else None
    if not isinstance(applied, list):
        violations.append("migration status is malformed or absent")
    elif REQUIRED_MIGRATION not in {str(item) for item in applied}:
        violations.append(f"required migration is pending: {REQUIRED_MIGRATION}")

    if inspect is not None and expected_image:
        by_service: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_SERVICES}
        for container in inspect:
            name = (container.get("Config", {}).get("Labels") or {}).get("com.docker.compose.service")
            if name in by_service:
                by_service[name].append(container)
        for name, candidates in by_service.items():
            if len(candidates) != 1:
                violations.append(f"{name}: expected exactly one running container")
                continue
            container = candidates[0]
            image = str(container.get("Config", {}).get("Image") or "")
            image_id = str(container.get("Image") or "")
            if image != expected_image and "@sha256:" not in image_id:
                violations.append(f"{name}: runtime image cannot be proven immutable")
            for mount in container.get("Mounts") or []:
                if mount.get("Type") == "bind" and _is_code_target(mount.get("Destination")):
                    violations.append(f"{name}: runtime bind mount masks executable application path {mount.get('Destination')}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-config", required=True, type=Path)
    parser.add_argument("--migration-status", required=True, type=Path)
    parser.add_argument("--inspect", type=Path)
    args = parser.parse_args()
    try:
        compose = json.loads(args.compose_config.read_text(encoding="utf-8"))
        migration_status = json.loads(args.migration_status.read_text(encoding="utf-8"))
        inspect = json.loads(args.inspect.read_text(encoding="utf-8")) if args.inspect else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: invalid gate input: {exc}", file=sys.stderr)
        return 2
    violations = validate(compose, migration_status, inspect)
    if violations:
        for violation in violations:
            print(f"FAIL: {violation}", file=sys.stderr)
        return 2
    print("OK: immutable frontend/API/worker/beat release contract and migration status verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
