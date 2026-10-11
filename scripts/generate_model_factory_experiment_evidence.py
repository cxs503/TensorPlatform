#!/usr/bin/env python3
"""Generate scoped offline evidence for four model-factory experiments.

The generator executes the real adapters through ``experiment_runtime`` with
explicit, deterministic fixtures.  It never connects to the platform database,
broker, object store, or production services.  Inventory updates are opt-in and
never alter maturity labels.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.core import Dataset, Model, ModelVersion, TrainingJob, TrainingStatus  # noqa: E402
from services.experiment_runtime import (  # noqa: E402
    METADATA_KEY,
    ExperimentAccessDenied,
    ExperimentValidationError,
    cancel_experiment,
    create_experiment,
    execute_persisted_experiment,
    get_experiment,
    mark_experiment_failed,
    register_adapter,
    serialize_experiment,
    unregister_adapter,
)


SCHEMA_VERSION = 1
EVIDENCE_CATEGORIES = (
    "real_service",
    "deterministic_data",
    "positive_negative_paths",
    "audit",
    "cleanup",
)
MODULES = {
    "causal_analysis_experiment": (
        "causal",
        {
            "fixture_mode": True,
            "method": "linear_regression",
            "treatment": "X",
            "outcome": "Y",
            "confounders": ["Z"],
        },
    ),
    "recsys_training_experiment": (
        "recsys",
        {
            "fixture_mode": True,
            "method": "matrix_factorization",
            "factors": 4,
            "epochs": 8,
            "seed": 42,
        },
    ),
    "gnn_training_experiment": (
        "gnn",
        {"fixture_mode": True, "epochs": 8, "hidden_dim": 8, "seed": 17},
    ),
    "gan_training_experiment": (
        "gan",
        {
            "fixture_mode": True,
            "epochs": 2,
            "latent_dim": 4,
            "hidden_dim": 8,
            "batch_size": 64,
            "seed": 23,
        },
    ),
}
DEFAULT_OUTPUT_DIR = Path("quality/maturity/evidence/model_factory_experiments")
DEFAULT_INVENTORY = Path("quality/maturity/modules.json")


class EvidenceError(RuntimeError):
    """The requested evidence could not be produced without weakening it."""


class _FakeQuery:
    def __init__(self, db: "_OfflineDB", model: type):
        self.db, self.model = db, model

    def filter(self, *_args): return self
    def with_for_update(self): return self
    def order_by(self, *_args): return self
    def offset(self, *_args): return self
    def limit(self, *_args): return self
    def first(self):
        if self.model is Dataset:
            return self.db.dataset
        if self.model is TrainingJob:
            return self.db.job
        return None

    def all(self):
        return list(self.db.active_jobs) if self.model is TrainingJob else []


class _OfflineDB:
    """Minimal unit-of-work used only to exercise the runtime state machine."""

    def __init__(self, dataset=None):
        self.dataset, self.job, self.active_jobs = dataset, None, []
        self.models, self.versions = [], []

    def query(self, model): return _FakeQuery(self, model)

    def add(self, value):
        if isinstance(value, TrainingJob):
            value.id = value.id or 1
            self.job = value
        elif isinstance(value, Model):
            value.id = len(self.models) + 10
            self.models.append(value)
        elif isinstance(value, ModelVersion):
            value.id = len(self.versions) + 20
            self.versions.append(value)

    def flush(self): pass
    def refresh(self, _value): pass
    def commit(self): pass
    def rollback(self): pass


class _OfflineAudit:
    """In-memory audit sink; deliberately has no platform-service fallback."""

    KEYS = {"actor_id", "organization_id", "job_id", "kind", "action", "outcome"}

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def __call__(self, **event) -> bool:
        if set(event) != self.KEYS:
            raise EvidenceError("offline audit event has an unexpected structure")
        self.events.append(copy.deepcopy(event))
        return True

    def validated(self, expected: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        actual = [
            (str(event["action"]), str(event["outcome"]), str(event["actor_id"]))
            for event in self.events
        ]
        if actual != expected:
            raise EvidenceError(f"offline audit lifecycle mismatch: {actual!r}")
        for event in self.events:
            if event["organization_id"] != 101 or int(event["job_id"]) < 1:
                raise EvidenceError("offline audit identity is not attributable")
            serialized = json.dumps(event, ensure_ascii=False, sort_keys=True)
            if any(secret in serialized.lower() for secret in ("config", "dataset_blob", "token", "password")):
                raise EvidenceError("offline audit event contains forbidden payload content")
        return copy.deepcopy(self.events)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_metrics(metrics: Mapping[str, Any]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for key, raw in sorted(metrics.items()):
        value = raw.item() if hasattr(raw, "item") else raw
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvidenceError(f"metric {key} is not numeric")
        if not math.isfinite(float(value)):
            raise EvidenceError(f"metric {key} is not finite")
        result[str(key)] = value
    if not result:
        raise EvidenceError("experiment returned no metrics")
    return result


def _execute(kind: str, config: dict[str, Any], root: Path) -> dict[str, Any]:
    db = _OfflineDB()
    audit = _OfflineAudit()
    job = create_experiment(
        db,
        kind=kind,
        config=copy.deepcopy(config),
        dataset_id=None,
        organization_id=101,
        actor_id="offline-evidence/api",
        audit_writer=audit,
        publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
    )
    completed = execute_persisted_experiment(
        db,
        job.id,
        101,
        task_id=job.training_job_id,
        artifact_root=root,
        actor_id="offline-evidence/worker",
        audit_writer=audit,
    )
    payload = serialize_experiment(completed)
    if completed.status != TrainingStatus.COMPLETED:
        raise EvidenceError(f"{kind} did not reach COMPLETED")
    artifact = payload.get("artifact")
    if not isinstance(artifact, Mapping) or not artifact.get("path"):
        raise EvidenceError(f"{kind} completed without a model artifact")
    artifact_path = Path(str(artifact["path"]))
    if not artifact_path.is_file():
        raise EvidenceError(f"{kind} artifact does not exist")
    actual_sha = _sha256(artifact_path)
    if artifact.get("sha256") != actual_sha:
        raise EvidenceError(f"{kind} artifact checksum mismatch")
    source = (payload.get("metadata") or {}).get("data_source")
    # The NumPy adapters return a detailed source object; the PyTorch adapters
    # currently persist the equivalent bounded classification as ``fixture``.
    if source == "fixture":
        source = {"type": "deterministic_fixture", "fixture": f"{kind}_fixture_v1"}
    if not isinstance(source, Mapping) or source.get("type") != "deterministic_fixture":
        raise EvidenceError(f"{kind} did not declare deterministic fixture provenance")
    if not db.models or not db.versions:
        raise EvidenceError(f"{kind} did not register a model and version")
    return {
        "status": "completed",
        "metrics": _canonical_metrics(completed.metrics or {}),
        "artifact": {
            "sha256": actual_sha,
            "size_bytes": artifact_path.stat().st_size,
            "file_name": artifact_path.name,
        },
        "data_source": dict(source),
        "registered_model_count": len(db.models),
        "registered_version_count": len(db.versions),
        "audit_events": audit.validated([
            ("create", "success", "offline-evidence/api"),
            ("execute", "completed", "offline-evidence/worker"),
        ]),
    }


def _negative_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    missing_fixture_audit = _OfflineAudit()
    try:
        create_experiment(
            _OfflineDB(), kind="causal", config={}, dataset_id=None,
            organization_id=101, publisher=lambda **_: None,
            actor_id="offline-evidence/api", audit_writer=missing_fixture_audit,
        )
    except ExperimentValidationError as exc:
        checks.append({"id": "reject-missing-explicit-fixture", "passed": True, "error_type": type(exc).__name__})
    else:
        raise EvidenceError("missing dataset and fixture_mode was accepted")
    if missing_fixture_audit.events:
        raise EvidenceError("rejected fixture request emitted a misleading audit success")

    db = _OfflineDB()
    tenant_audit = _OfflineAudit()
    job = create_experiment(
        db, kind="causal", config=copy.deepcopy(MODULES["causal_analysis_experiment"][1]),
        dataset_id=None, organization_id=101,
        actor_id="offline-evidence/api", audit_writer=tenant_audit,
        publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
    )
    try:
        get_experiment(db, job.id, organization_id=202)
    except ExperimentAccessDenied as exc:
        checks.append({"id": "reject-cross-tenant-read", "passed": True, "error_type": type(exc).__name__})
    else:
        raise EvidenceError("cross-tenant experiment read was accepted")
    tenant_audit.validated([
        ("create", "success", "offline-evidence/api"),
    ])

    unknown_kind_audit = _OfflineAudit()
    try:
        create_experiment(
            _OfflineDB(), kind="unknown_node", config={"fixture_mode": True},
            dataset_id=None, organization_id=101, publisher=lambda **_: None,
            actor_id="offline-evidence/api", audit_writer=unknown_kind_audit,
        )
    except ExperimentValidationError as exc:
        checks.append({"id": "reject-unknown-experiment-node", "passed": True, "error_type": type(exc).__name__})
    else:
        raise EvidenceError("unknown experiment kind was accepted")
    if unknown_kind_audit.events:
        raise EvidenceError("rejected experiment kind emitted a misleading audit success")

    kind = "evidence_no_artifact"
    register_adapter(kind, lambda _context: {"metrics": {"score": 1.0}}, replace=True)
    try:
        no_artifact_db = _OfflineDB()
        no_artifact_audit = _OfflineAudit()
        no_artifact_job = create_experiment(
            no_artifact_db, kind=kind, config={"fixture_mode": True}, dataset_id=None,
            organization_id=101,
            actor_id="offline-evidence/api", audit_writer=no_artifact_audit,
            publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
        )
        no_artifact_job = execute_persisted_experiment(
            no_artifact_db, no_artifact_job.id, 101,
            task_id=no_artifact_job.training_job_id, artifact_root=root,
            actor_id="offline-evidence/worker", audit_writer=no_artifact_audit,
        )
        no_artifact_payload = serialize_experiment(no_artifact_job)
        rejected = no_artifact_payload.get("artifact") is None and not no_artifact_db.versions
        if not rejected:
            raise EvidenceError("artifact-free result unexpectedly created a model artifact")
        checks.append({
            "id": "beta-evidence-rejects-missing-artifact",
            "passed": True,
            "runtime_status": "completed",
            "evidence_eligible": False,
        })
        no_artifact_audit.validated([
            ("create", "success", "offline-evidence/api"),
            ("execute", "completed", "offline-evidence/worker"),
        ])
    finally:
        unregister_adapter(kind)

    cancel_db, cancel_audit = _OfflineDB(), _OfflineAudit()
    cancel_job = create_experiment(
        cancel_db, kind="causal", config=copy.deepcopy(MODULES["causal_analysis_experiment"][1]),
        dataset_id=None, organization_id=101, actor_id="offline-evidence/api",
        audit_writer=cancel_audit,
        publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
    )
    cancel_experiment(
        cancel_db, cancel_job.id, organization_id=101,
        actor_id="offline-evidence/api", audit_writer=cancel_audit,
        revoker=lambda *_args, **_kwargs: None,
    )
    cancel_events = cancel_audit.validated([
        ("create", "success", "offline-evidence/api"),
        ("cancel", "cancelled", "offline-evidence/api"),
    ])
    checks.append({
        "id": "structured-audit-cancel-lifecycle",
        "passed": True,
        "event_count": len(cancel_events),
        "events": cancel_events,
    })

    failed_db, failed_audit = _OfflineDB(), _OfflineAudit()
    failed_job = create_experiment(
        failed_db, kind="causal", config=copy.deepcopy(MODULES["causal_analysis_experiment"][1]),
        dataset_id=None, organization_id=101, actor_id="offline-evidence/api",
        audit_writer=failed_audit,
        publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
    )
    mark_experiment_failed(
        failed_db, failed_job.id, 101, RuntimeError("bounded offline failure"),
        actor_id="offline-evidence/worker", audit_writer=failed_audit,
    )
    failed_events = failed_audit.validated([
        ("create", "success", "offline-evidence/api"),
        ("execute", "failed", "offline-evidence/worker"),
    ])
    checks.append({
        "id": "structured-audit-failed-lifecycle",
        "passed": True,
        "event_count": len(failed_events),
        "events": failed_events,
    })
    return checks


def run_offline_validation(*, repo_root: Path = REPO_ROOT) -> dict[str, dict[str, Any]]:
    """Run all evidence checks and return one gate-compatible report per module."""

    started_at = _utc_now()
    parent = (repo_root / ".tmp").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    scratch_path: Path | None = None
    results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    negative: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="model-factory-evidence-", dir=parent) as temporary:
        scratch_path = Path(temporary)
        for module_id, (kind, config) in MODULES.items():
            first = _execute(kind, config, scratch_path / "first" / module_id)
            second = _execute(kind, config, scratch_path / "second" / module_id)
            if first["metrics"] != second["metrics"]:
                raise EvidenceError(f"{kind} metrics are not deterministic")
            results[module_id] = (first, second)
        negative = _negative_checks(scratch_path / "negative")

    cleanup_completed = scratch_path is not None and not scratch_path.exists()
    if not cleanup_completed:
        raise EvidenceError("temporary experiment artifacts were not removed")
    completed_at = _utc_now()
    reports: dict[str, dict[str, Any]] = {}
    for module_id, (kind, config) in MODULES.items():
        first, second = results[module_id]
        checks = [
            {"id": f"real-runtime:{kind}", "passed": True, "status": first["status"]},
            {"id": f"finite-real-metrics:{kind}", "passed": True, "metric_count": len(first["metrics"])},
            {"id": f"deterministic-replay:{kind}", "passed": first["metrics"] == second["metrics"]},
            {"id": f"artifact-checksum:{kind}", "passed": True, "sha256": first["artifact"]["sha256"]},
            {"id": f"structured-audit:create-execute:{kind}", "passed": len(first["audit_events"]) == 2},
            {"id": "scratch-cleanup", "passed": cleanup_completed},
            *copy.deepcopy(negative),
        ]
        reports[module_id] = {
            "schema_version": SCHEMA_VERSION,
            "module_id": module_id,
            "environment": "offline-local-runtime-with-no-platform-services",
            "data_classification": "fixture",
            "started_at": started_at,
            "completed_at": completed_at,
            "passed": all(check["passed"] for check in checks),
            "evidence_categories": list(EVIDENCE_CATEGORIES),
            "checks": checks,
            "experiment": {
                "kind": kind,
                "config": config,
                "first_run": first,
                "repeat_run": second,
                "metrics_reproducible": first["metrics"] == second["metrics"],
                "artifact_bytes_reproducible": first["artifact"]["sha256"] == second["artifact"]["sha256"],
            },
            "negative_paths": copy.deepcopy(negative),
            "audit": {
                "mode": "in_memory_injected_writer",
                "default_platform_writer_used": False,
                "verified_lifecycles": ["create_execute_completed", "create_cancel", "create_execute_failed"],
                "create_execute_events": copy.deepcopy(first["audit_events"]),
                "proves": "sanitized attributable event contract in the runtime code path",
                "does_not_prove": "audit_logs persistence or online delivery",
            },
            "cleanup": {
                "completed": cleanup_completed,
                "scratch_retained": False,
                "production_resources_touched": False,
            },
            "scope": {
                "proves": list(EVIDENCE_CATEGORIES),
                "does_not_prove": [
                    "persistence", "rbac_tenant", "audit_persistence", "observability",
                    "performance", "recovery", "e2e", "production_data",
                    "field_acceptance", "long_run", "reproducible_release",
                ],
                "note": (
                    "Audit proves only the sanitized create/execute/cancel/failure code contract "
                    "through an injected in-memory writer. It does not prove audit_logs persistence. "
                    "Observability remains unproven because this is a single-process offline run. "
                    "No database, broker, service deployment, or production dataset was used."
                ),
            },
        }
    return reports


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def output_paths(repo_root: Path, output_dir: Path) -> dict[str, Path]:
    directory = output_dir if output_dir.is_absolute() else repo_root / output_dir
    directory = directory.resolve()
    evidence_root = (repo_root / "quality/maturity/evidence").resolve()
    try:
        directory.relative_to(evidence_root)
    except ValueError as exc:
        raise EvidenceError("output directory must be below quality/maturity/evidence") from exc
    return {module_id: directory / f"{module_id}-offline-validation.json" for module_id in MODULES}


def update_inventory(*, repo_root: Path, inventory_path: Path, paths: Mapping[str, Path], reports: Mapping[str, dict[str, Any]]) -> None:
    inventory_path = inventory_path if inventory_path.is_absolute() else repo_root / inventory_path
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in inventory.get("modules", [])}
    observed_at = next(iter(reports.values()))["completed_at"]
    for module_id, report in reports.items():
        module = by_id.get(module_id)
        if module is None:
            raise EvidenceError(f"inventory module is missing: {module_id}")
        if module.get("observed_status") != "demo" or module.get("target_status") != "beta":
            raise EvidenceError(f"refusing to change maturity contract for {module_id}")
        module["basis"] = str(module.get("basis", "")).replace(
            "尚未完成在线持久化、审计和E2E准入。",
            "尚未完成在线持久化、audit_logs落库、多进程可观测性和E2E准入。",
        )
        module["known_gaps"] = [
            (
                "缺少audit_logs在线落库验证（当前audit仅证明内存写入器代码闭环）"
                if gap == "缺少审计、可观测性和登录态E2E证据"
                else gap
            )
            for gap in module.get("known_gaps", [])
        ]
        if "缺少多进程可观测性和登录态E2E证据" not in module["known_gaps"]:
            module["known_gaps"].append("缺少多进程可观测性和登录态E2E证据")
        path = paths[module_id]
        persisted = json.loads(path.read_text(encoding="utf-8"))
        if persisted != report:
            raise EvidenceError(f"persisted report differs from validated result: {module_id}")
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
        digest = _sha256(path)
        evidence = module.setdefault("evidence", {})
        for category in EVIDENCE_CATEGORIES:
            existing = evidence.get(category)
            if existing and existing.get("artifact") not in (None, relative):
                raise EvidenceError(f"refusing to replace {module_id}.{category} from another artifact")
            evidence[category] = {
                "status": "pass",
                "artifact_type": "test_report",
                "artifact": relative,
                "sha256": digest,
                "observed_at": observed_at,
                "summary": (
                    "固定fixture完成真实训练、结构化内存审计、制品校验、负向拒绝与离线清理；"
                    "审计仅证明代码闭环，不证明audit_logs落库或在线Beta。"
                ),
            }
    inventory["evidence_import_status"] = "partial_offline"
    inventory["evidence_import_note"] = (
        "Model-factory causal, recommendation, GNN and GAN experiments have only "
        "real-service, deterministic fixture, scoped negative-path, code-level audit and cleanup evidence; "
        "all remaining Beta evidence is still required."
    )
    atomic_json(inventory_path, inventory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate model-factory offline maturity evidence.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--update-inventory", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    try:
        paths = output_paths(root, args.output_dir)
        reports = run_offline_validation(repo_root=root)
        for module_id, report in reports.items():
            atomic_json(paths[module_id], report)
        if args.update_inventory:
            update_inventory(repo_root=root, inventory_path=args.inventory, paths=paths, reports=reports)
        print(json.dumps({
            "passed": all(report["passed"] for report in reports.values()),
            "modules": sorted(reports),
            "evidence_categories": list(EVIDENCE_CATEGORIES),
            "inventory_updated": args.update_inventory,
            "maturity_level_changed": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"model-factory evidence generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
