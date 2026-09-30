#!/usr/bin/env python3
"""Generate fail-closed Stage 5 offline maturity evidence.

This command is deliberately local-only: it creates a private scratch registry,
uses deterministic synthetic scenarios and a file replay sink, and removes the
entire run before reporting success.  It never contacts a platform service and
does not update the maturity inventory unless ``--update-inventory`` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quality.offline_validation import RunRegistry, ValidationError  # noqa: E402
from scenarios.lib.core import (  # noqa: E402
    MODES,
    SCENARIO_IDS,
    fingerprint,
    generate,
    load_manifest,
    write_csv,
)
from tools.industrial_replay.replay import (  # noqa: E402
    FaultPlan,
    FreezeRule,
    ReplayEngine,
    ReplayError,
    SequenceRange,
    build_sink,
    load_records,
)


MODULE_ID = "steel_energy_apps"
EVIDENCE_CATEGORIES = (
    "deterministic_data",
    "positive_negative_paths",
    "cleanup",
)
DEFAULT_REPORT = Path(
    "quality/maturity/evidence/steel_energy_apps/stage5-offline-validation.json"
)
DEFAULT_INVENTORY = Path("quality/maturity/modules.json")
DEFAULT_ROWS = 32


class EvidenceError(RuntimeError):
    """Raised when evidence cannot be produced without weakening its claim."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay_fingerprint(envelopes: list[dict[str, Any]]) -> str:
    """Hash replay semantics while deliberately excluding the unique run id."""
    normalized = []
    for envelope in envelopes:
        replay = dict(envelope["replay"])
        replay.pop("run_id", None)
        normalized.append({"replay": replay, "data": envelope["data"]})
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_inside_repo(value: Path, repo_root: Path) -> Path:
    candidate = value if value.is_absolute() else repo_root / value
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise EvidenceError(f"path must remain inside repository: {value}") from exc
    return resolved


def resolve_report_path(value: Path, repo_root: Path) -> Path:
    resolved = resolve_inside_repo(value, repo_root)
    evidence_root = (repo_root / "quality/maturity/evidence").resolve()
    try:
        resolved.relative_to(evidence_root)
    except ValueError as exc:
        raise EvidenceError(
            "evidence output must be below quality/maturity/evidence"
        ) from exc
    if resolved.suffix.lower() != ".json":
        raise EvidenceError("evidence output must be a .json report")
    return resolved


def resolve_scratch_parent(value: Path | None) -> Path | None:
    if value is None:
        return None
    requested = value.expanduser()
    if requested.is_symlink():
        raise EvidenceError("scratch parent cannot be a symlink")
    resolved = requested.resolve()
    configured = os.getenv(
        "ODF_EVIDENCE_SCRATCH_ROOTS",
        "/tmp,/srv/tensor/operator-b/foundation-validation-runs",
    )
    roots = [Path(item).expanduser().resolve() for item in configured.split(",") if item]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise EvidenceError("scratch parent must be below an approved local scratch root")
    return resolved


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git_context(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip()

    revision = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=no")
    return {
        "revision": revision or "unavailable",
        "workspace_dirty": None if status is None else bool(status),
    }


@contextmanager
def validation_root(path: Path) -> Iterator[None]:
    previous = os.environ.get("ODF_VALIDATION_ALLOWED_ROOTS")
    os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = str(path.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ODF_VALIDATION_ALLOWED_ROOTS", None)
        else:
            os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = previous


def passed_check(check_id: str, **details: Any) -> dict[str, Any]:
    return {"id": check_id, "passed": True, "details": details}


def expected_rejection(check_id: str, operation: Any) -> dict[str, Any]:
    try:
        operation()
    except (ValidationError, ReplayError, ValueError) as exc:
        return passed_check(check_id, rejection=type(exc).__name__, message=str(exc))
    raise EvidenceError(f"negative-path check unexpectedly succeeded: {check_id}")


def run_offline_validation(
    *,
    repo_root: Path,
    rows: int = DEFAULT_ROWS,
    scratch_parent: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(rows, int) or isinstance(rows, bool) or not 8 <= rows <= 1000:
        raise EvidenceError("rows must be an integer between 8 and 1000")
    scratch_parent = resolve_scratch_parent(scratch_parent)

    started_at = utcnow()
    checks: list[dict[str, Any]] = []
    scenario_results: list[dict[str, Any]] = []
    replay_result: dict[str, Any] = {}
    run_id: str | None = None
    cleanup_completed = False

    with tempfile.TemporaryDirectory(
        prefix="foundation-stage5-evidence-", dir=scratch_parent
    ) as temporary:
        scratch = Path(temporary)
        scratch.chmod(0o700)
        registry_root = scratch / "registry"
        with validation_root(scratch):
            registry = RunRegistry(registry_root)
            checks.append(
                expected_rejection(
                    "production-environment-rejected",
                    lambda: registry.create(
                        "steel_energy_apps_bundle", environment="production"
                    ),
                )
            )
            run = registry.create(
                "steel_energy_apps_bundle",
                scenario_version="1.0.0",
                environment="test",
                asset_type="synthetic",
                lease_seconds=120,
                metadata={
                    "purpose": "stage5-offline-maturity-evidence",
                    "rows_per_mode": rows,
                },
            )
            run_id = run["run_id"]
            run_dir = registry_root / run_id

            try:
                for scenario_id in SCENARIO_IDS:
                    manifest = load_manifest(scenario_id)
                    modes: dict[str, Any] = {}
                    for mode in MODES:
                        records = generate(
                            scenario_id, mode, rows=rows, seed=manifest["seed"]
                        )
                        first_fingerprint = fingerprint(records)
                        repeated_fingerprint = fingerprint(
                            generate(
                                scenario_id,
                                mode,
                                rows=rows,
                                seed=manifest["seed"],
                            )
                        )
                        if first_fingerprint != repeated_fingerprint:
                            raise EvidenceError(
                                f"deterministic fingerprint mismatch: {scenario_id}/{mode}"
                            )
                        output = write_csv(
                            records, run_dir / "scenarios" / scenario_id / f"{mode}.csv"
                        )
                        asset = registry.register_asset(
                            run_id,
                            kind="scenario-csv",
                            uri=str(output),
                        )
                        modes[mode] = {
                            "rows": rows,
                            "fingerprint": first_fingerprint,
                            "csv_sha256": asset["checksum"],
                        }
                    scenario_results.append(
                        {
                            "id": scenario_id,
                            "version": manifest["version"],
                            "seed": manifest["seed"],
                            "modes": modes,
                        }
                    )
                    checks.append(
                        passed_check(
                            f"deterministic-scenario:{scenario_id}",
                            version=manifest["version"],
                            seed=manifest["seed"],
                            modes=list(MODES),
                        )
                    )

                source = run_dir / "scenarios" / "photovoltaic_power" / "fault.csv"
                checks.append(
                    expected_rejection(
                        "asset-checksum-tamper-rejected",
                        lambda: registry.register_asset(
                            run_id,
                            kind="tampered-scenario-csv",
                            uri=str(source),
                            checksum="0" * 64,
                        ),
                    )
                )
                checks.append(
                    expected_rejection(
                        "network-replay-sink-disabled",
                        lambda: build_sink(
                            "http://127.0.0.1:9/ingest",
                            headers={},
                            timeout=1,
                            retries=0,
                        ),
                    )
                )

                records = load_records(source)
                fault_plan = FaultPlan(
                    disconnect=(SequenceRange(5, 6),),
                    duplicate_every=7,
                    reorder_window=3,
                    latency_ms=25,
                    latency_every=4,
                    freezes=(
                        FreezeRule(
                            "global_irradiance_w_m2", SequenceRange(8, 10)
                        ),
                    ),
                )
                replay_path = run_dir / "replay" / "photovoltaic-fault.jsonl"
                replay_summary = ReplayEngine(
                    records, run_id=run_id, fault_plan=fault_plan
                ).run(
                    build_sink(
                        f"file:{replay_path}", headers={}, timeout=1, retries=0
                    ),
                    speed=0,
                )
                replay_asset = registry.register_asset(
                    run_id,
                    kind="fault-replay-jsonl",
                    uri=str(replay_path),
                )
                envelopes = [
                    json.loads(line)
                    for line in replay_path.read_text(encoding="utf-8").splitlines()
                ]
                faults = sorted(
                    {
                        fault
                        for envelope in envelopes
                        for fault in envelope["replay"]["faults"]
                    }
                )
                required_faults = {
                    "duplicate",
                    "reorder:3",
                    "latency:25ms",
                    "sensor_freeze:global_irradiance_w_m2",
                }
                if not required_faults.issubset(faults):
                    raise EvidenceError(
                        f"fault replay did not cover required faults: {sorted(required_faults - set(faults))}"
                    )
                if replay_summary["dropped"] != 2:
                    raise EvidenceError("fault replay did not drop the configured records")
                replay_result = {
                    "source_scenario": "photovoltaic_power/fault",
                    "fault_plan": {
                        "disconnect": ["5:6"],
                        "duplicate_every": 7,
                        "reorder_window": 3,
                        "latency_ms": 25,
                        "latency_every": 4,
                        "freeze": ["global_irradiance_w_m2:8:10"],
                    },
                    "observed_faults": faults,
                    "summary": replay_summary,
                    "output_sha256": replay_asset["checksum"],
                    "semantic_fingerprint": replay_fingerprint(envelopes),
                }
                checks.append(
                    passed_check(
                        "offline-fault-replay",
                        summary=replay_summary,
                        observed_faults=faults,
                        semantic_fingerprint=replay_result["semantic_fingerprint"],
                    )
                )
                finished = registry.finish(
                    run_id,
                    "passed",
                    summary={
                        "scenarios": len(scenario_results),
                        "scenario_modes": len(scenario_results) * len(MODES),
                        "replay": replay_summary,
                    },
                )
                if finished["status"] != "passed":
                    raise EvidenceError("validation registry did not finish as passed")
            except Exception:
                try:
                    current = registry.load(run_id)
                    if current["status"] == "running":
                        registry.finish(
                            run_id,
                            "failed",
                            summary={"reason": "offline evidence generation failed"},
                        )
                finally:
                    registry.cleanup(run_id, force=True)
                raise

            cleanup_completed = registry.cleanup(run_id)
            if not cleanup_completed or run_dir.exists():
                raise EvidenceError("registry-owned validation assets were not removed")
            checks.append(
                passed_check(
                    "registry-owned-assets-cleaned",
                    run_id=run_id,
                    run_directory_absent=True,
                )
            )

    completed_at = utcnow()
    return {
        "schema_version": 1,
        "module_id": MODULE_ID,
        "evidence_categories": list(EVIDENCE_CATEGORIES),
        "passed": all(check["passed"] is True for check in checks),
        "checks": checks,
        "environment": "isolated-replay",
        "data_classification": "synthetic",
        "started_at": iso(started_at),
        "completed_at": iso(completed_at),
        "scenario_id": "steel_energy_apps_bundle",
        "scenario_version": "1.0.0",
        "run_id": run_id,
        "scenarios": scenario_results,
        "replay": replay_result,
        "cleanup": {
            "policy": "registry-owned",
            "completed": cleanup_completed,
            "run_id": run_id,
        },
        "source": git_context(repo_root),
        "scope": {
            "proves": list(EVIDENCE_CATEGORIES),
            "does_not_prove": [
                "real_service",
                "persistence",
                "rbac_tenant",
                "audit",
                "observability",
                "e2e",
                "performance",
                "recovery",
                "production_data",
                "field_acceptance",
                "long_run",
                "reproducible_release",
            ],
            "note": "File-only offline validation; no platform service or production data was contacted.",
        },
    }


def update_inventory(
    *,
    repo_root: Path,
    inventory_path: Path,
    report_path: Path,
    report: dict[str, Any],
) -> str:
    if report.get("passed") is not True:
        raise EvidenceError("refusing to update inventory from a failed report")
    report_path = resolve_inside_repo(report_path, repo_root)
    inventory_path = resolve_inside_repo(inventory_path, repo_root)
    try:
        persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read persisted evidence report: {exc}") from exc
    if persisted_report != report:
        raise EvidenceError("persisted evidence report differs from validated result")
    if persisted_report.get("module_id") != MODULE_ID or set(
        persisted_report.get("evidence_categories", [])
    ) != set(EVIDENCE_CATEGORIES):
        raise EvidenceError("evidence report scope does not match the offline gate")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read maturity inventory: {exc}") from exc
    modules = inventory.get("modules")
    if not isinstance(modules, list):
        raise EvidenceError("inventory.modules must be an array")
    matches = [module for module in modules if module.get("id") == MODULE_ID]
    if len(matches) != 1:
        raise EvidenceError(f"inventory must contain exactly one {MODULE_ID} module")

    digest = sha256_file(report_path)
    artifact = report_path.relative_to(repo_root.resolve()).as_posix()
    evidence = matches[0].setdefault("evidence", {})
    if not isinstance(evidence, dict):
        raise EvidenceError(f"{MODULE_ID}.evidence must be an object")
    for category in EVIDENCE_CATEGORIES:
        existing = evidence.get(category)
        if existing is not None and not isinstance(existing, dict):
            raise EvidenceError(f"existing {category} evidence must be an object")
        if existing and existing.get("artifact") != artifact:
            raise EvidenceError(
                f"refusing to replace existing {category} evidence from another artifact"
            )
        evidence[category] = {
            "status": "pass",
            "artifact_type": "test_report",
            "artifact": artifact,
            "sha256": digest,
            "observed_at": report["completed_at"],
            "summary": (
                "确定性虚拟场景、离线故障回放及注册资产清理通过；"
                "不代表真实服务、持久化或完整 Beta。"
            ),
        }
    inventory["evidence_import_status"] = "partial_offline"
    inventory["evidence_import_note"] = (
        "Only deterministic_data, scoped offline positive/negative paths and cleanup "
        "are attached for steel_energy_apps; all other Beta evidence remains missing."
    )
    atomic_json(inventory_path, inventory)
    return digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate local-only Stage 5 maturity evidence."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument(
        "--scratch-parent",
        type=Path,
        help="Optional approved local scratch parent; defaults to the system temp directory.",
    )
    parser.add_argument(
        "--update-inventory",
        action="store_true",
        help="Attach the three scoped passing records; never changes a maturity level.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    report_path: Path | None = None
    failure_started_at = utcnow()
    try:
        report_path = resolve_report_path(args.output, repo_root)
        report = run_offline_validation(
            repo_root=repo_root,
            rows=args.rows,
            scratch_parent=args.scratch_parent,
        )
        atomic_json(report_path, report)
        digest = sha256_file(report_path)
        if args.update_inventory:
            update_inventory(
                repo_root=repo_root,
                inventory_path=args.inventory,
                report_path=report_path,
                report=report,
            )
        print(
            json.dumps(
                {
                    "passed": True,
                    "report": report_path.relative_to(repo_root).as_posix(),
                    "sha256": digest,
                    "run_id": report["run_id"],
                    "cleanup_completed": report["cleanup"]["completed"],
                    "inventory_updated": bool(args.update_inventory),
                    "maturity_level_changed": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        if report_path is not None:
            failure_report = {
                "schema_version": 1,
                "module_id": MODULE_ID,
                "evidence_categories": list(EVIDENCE_CATEGORIES),
                "passed": False,
                "checks": [
                    {
                        "id": "stage5-offline-validation",
                        "passed": False,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                ],
                "environment": "isolated-replay",
                "data_classification": "synthetic",
                "started_at": iso(failure_started_at),
                "completed_at": iso(utcnow()),
                "run_id": None,
                "cleanup": {
                    "completed": False,
                    "note": "Scratch is additionally bounded by TemporaryDirectory cleanup.",
                },
                "scope": {
                    "note": "Failed evidence is never attached to the maturity inventory."
                },
            }
            try:
                atomic_json(report_path, failure_report)
            except Exception as write_error:
                print(
                    f"could not persist failure report: {write_error}", file=sys.stderr
                )
        print(f"stage5 offline evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
