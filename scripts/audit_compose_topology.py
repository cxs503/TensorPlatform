#!/usr/bin/env python3
"""Compare merged Compose intent with read-only Docker inspect data.

The JSON report deliberately records and compares environment variable *keys*
only.  Inspect values are parsed as part of the input document but never emitted
or compared.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any


DEFAULT_SERVICES = ("backend", "celery-worker", "frontend")


def _env_keys(values: Any) -> list[str]:
    if isinstance(values, dict):
        return sorted(str(key) for key in values)
    return sorted({str(item).split("=", 1)[0] for item in (values or [])})


def _expected_mounts(service: dict[str, Any]) -> list[dict[str, Any]]:
    mounts = []
    for item in service.get("volumes", []):
        if isinstance(item, str):
            parts = item.split(":")
            target = parts[1] if len(parts) > 1 else parts[0]
            mounts.append({
                "target": target,
                "type": "bind" if parts[0].startswith((".", "/")) else "volume",
                "read_only": "ro" in parts[2:],
            })
        else:
            mounts.append({
                "target": item.get("target"),
                "type": item.get("type"),
                "read_only": bool(item.get("read_only")),
            })
    return sorted(mounts, key=lambda item: (item["target"] or "", item["type"] or ""))


def _actual_mounts(container: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(({
        "target": item.get("Destination"),
        "type": item.get("Type"),
        "read_only": not bool(item.get("RW")),
    } for item in container.get("Mounts", [])), key=lambda item: (item["target"] or "", item["type"] or ""))


def build_report(
    compose: dict[str, Any],
    containers: list[dict[str, Any]],
    services: tuple[str, ...] = DEFAULT_SERVICES,
) -> dict[str, Any]:
    project = compose.get("name")
    results = []
    for service_name in services:
        expected = compose.get("services", {}).get(service_name)
        candidates = []
        for container in containers:
            labels = container.get("Config", {}).get("Labels") or {}
            if (labels.get("com.docker.compose.project") == project and
                    labels.get("com.docker.compose.service") == service_name):
                candidates.append(container)

        if expected is None:
            results.append({"service": service_name, "status": "compose_service_missing", "drift": ["service"]})
            continue
        if not candidates:
            results.append({"service": service_name, "status": "running_container_missing", "drift": ["service"]})
            continue
        if len(candidates) != 1:
            results.append({
                "service": service_name,
                "status": "ambiguous_running_containers",
                "candidate_names": sorted(item.get("Name", "").lstrip("/") for item in candidates),
                "drift": ["service"],
            })
            continue

        actual = candidates[0]
        expected_env = _env_keys(expected.get("environment"))
        actual_env = _env_keys(actual.get("Config", {}).get("Env"))
        expected_networks = sorted((expected.get("networks") or {}).keys() if isinstance(expected.get("networks"), dict) else expected.get("networks") or [])
        actual_networks = sorted((actual.get("NetworkSettings", {}).get("Networks") or {}).keys())
        expected_mounts = _expected_mounts(expected)
        actual_mounts = _actual_mounts(actual)
        comparisons = {
            "container_name": {
                "expected": expected.get("container_name"),
                "actual": actual.get("Name", "").lstrip("/"),
            },
            "image": {
                "expected": expected.get("image"),
                "actual": actual.get("Config", {}).get("Image"),
            },
            "networks": {"expected": expected_networks, "actual": actual_networks},
            "mounts": {"expected": expected_mounts, "actual": actual_mounts},
            "environment": {
                "expected_keys": expected_env,
                "actual_keys": actual_env,
                "missing_keys": sorted(set(expected_env) - set(actual_env)),
                # Image-level ENV keys are not represented by Compose config;
                # record them for review without treating them as drift.
                "runtime_only_keys": sorted(set(actual_env) - set(expected_env)),
            },
        }
        drift = [name for name in ("container_name", "image", "networks", "mounts")
                 if comparisons[name]["expected"] != comparisons[name]["actual"]]
        if comparisons["environment"]["missing_keys"]:
            drift.append("environment_keys")
        results.append({
            "service": service_name,
            "status": "drift" if drift else "match",
            "container_id": actual.get("Id", "")[:12],
            "labels": {
                "project": project,
                "service": service_name,
            },
            "drift": drift,
            "comparison": comparisons,
        })

    return {
        "schema_version": 1,
        "project": project,
        "verdict": "drift" if any(item["drift"] for item in results) else "match",
        "secrets_redacted": True,
        "services": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-config", required=True, type=Path)
    parser.add_argument("--inspect", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--services", nargs="+", default=list(DEFAULT_SERVICES))
    args = parser.parse_args()
    compose = json.loads(args.compose_config.read_text(encoding="utf-8"))
    containers = json.loads(args.inspect.read_text(encoding="utf-8"))
    report = build_report(compose, containers, tuple(args.services))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_parent = args.output.parent.resolve()
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=output_parent, delete=False
        ) as handle:
            handle.write(rendered)
            temporary_name = handle.name
        os.replace(temporary_name, args.output)
    else:
        print(rendered, end="")
    return 0 if report["verdict"] == "match" else 2


if __name__ == "__main__":
    raise SystemExit(main())
