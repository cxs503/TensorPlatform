"""Immutable, portable workflow releases for cross-user reproduction."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from models.core import AlgorithmModel, Dataset, Pipeline, PipelineTask

RELEASE_ROOT = Path(os.getenv("WORKFLOW_RELEASE_DIR", "/app/uploads/workflow_releases"))
DATASET_ROOT = Path("/app/uploads/datasets")
ALGORITHM_ROOT = Path("/app/uploads/algorithms")
MAX_ASSET_BYTES = 512 * 1024 * 1024
RELEASE_SCHEMA = "foundation.workflow-release/v1"


class WorkflowReleaseError(ValueError):
    pass


def _safe_token(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkflowReleaseError("工作流复现链接无效") from exc


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise WorkflowReleaseError(f"复现资源文件不存在：{source.name}")
    if source.stat().st_size > MAX_ASSET_BYTES:
        raise WorkflowReleaseError(f"资源文件超过 {MAX_ASSET_BYTES // 1024 // 1024} MiB，暂不能生成复现链接")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _pipeline_definition(pipeline: Pipeline) -> dict:
    tasks = []
    for task in pipeline.tasks:
        config = _json_copy(task.config or {})
        if config.get("runtime_environment_id"):
            raise WorkflowReleaseError("含专用运行环境的流程暂不能发布复现链接，请改用平台默认环境后再发布")
        tasks.append({
            "name": task.name,
            "task_type": task.task_type,
            "command": task.command,
            "dependencies": list(task.dependencies or []),
            "config": config,
            "timeout": int(task.timeout or 1800),
            "retry": int(task.retry or 0),
            "order_index": int(task.order_index or 0),
        })
    return {
        "name": pipeline.name,
        "description": pipeline.description,
        "trigger_type": "manual",
        "tasks": tasks,
    }


def _referenced_ids(definition: dict, key: str) -> set[int]:
    values: set[int] = set()
    for task in definition.get("tasks") or []:
        raw = (task.get("config") or {}).get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.add(value)
    return values


def _referenced_cleaning_rule_ids(definition: dict) -> set[int]:
    result: set[int] = set()
    for task in definition.get("tasks") or []:
        config = task.get("config") or {}
        if str(config.get("operation") or "") != "clean_rule":
            continue
        try:
            rule_id = int(config.get("cleaning_rule_id"))
        except (TypeError, ValueError):
            continue
        if rule_id > 0:
            result.add(rule_id)
    return result


def _cleaning_rule_snapshot(db, rule_id: int, organization_id: int | None) -> dict:
    row = db.execute(
        text("""
            SELECT id, name, description, source_mode, definition, code_text, file_name, is_active, organization_id
            FROM data_cleaning_rules WHERE id=:id
        """),
        {"id": rule_id},
    ).mappings().first()
    if not row or (organization_id is not None and row.get("organization_id") not in {None, organization_id}):
        raise WorkflowReleaseError(f"流程引用的清洗规则 {rule_id} 不存在或无权发布")
    definition = row.get("definition")
    if isinstance(definition, str):
        definition = json.loads(definition)
    return {
        "source_id": int(row["id"]),
        "name": row.get("name") or "未命名清洗规则",
        "description": row.get("description"),
        "source_mode": row.get("source_mode") or "guided",
        "definition": _json_copy(definition or {}),
        "code_text": row.get("code_text"),
        "file_name": row.get("file_name"),
        "is_active": bool(row.get("is_active", True)),
    }


def _dataset_snapshot(dataset: Dataset, target_dir: Path) -> dict:
    suffix = ".csv"
    source = Path(dataset.file_path) if dataset.file_path else None
    if source and source.is_file():
        suffix = source.suffix.lower() or ".csv"
        destination = target_dir / f"dataset_{dataset.id}{suffix}"
        _copy_file(source, destination)
    elif dataset.data_blob:
        destination = target_dir / f"dataset_{dataset.id}.csv"
        if len(dataset.data_blob) > MAX_ASSET_BYTES:
            raise WorkflowReleaseError(f"数据集 {dataset.id} 超过大小上限")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(dataset.data_blob))
    else:
        raise WorkflowReleaseError(f"数据集 {dataset.id} 没有可导出的数据文件")
    return {
        "source_id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "row_count": int(dataset.row_count or 0),
        "size_bytes": int(destination.stat().st_size),
        "columns_info": _json_copy(dataset.columns_info or {}),
        "stage": dataset.stage or "raw",
        "file": str(destination.relative_to(target_dir.parent)),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def _algorithm_snapshot(algorithm: AlgorithmModel, target_dir: Path) -> dict:
    metadata = _json_copy(algorithm.metrics or {})
    artifact_path = Path(str(metadata.get("artifact_path") or ""))
    if not artifact_path.is_file():
        raise WorkflowReleaseError(f"算法 {algorithm.name} 缺少可复现的源码或制品文件")
    source_dir = artifact_path.parent
    if not source_dir.is_dir() or ALGORITHM_ROOT.resolve() not in source_dir.resolve().parents and source_dir.resolve() != ALGORITHM_ROOT.resolve():
        raise WorkflowReleaseError(f"算法 {algorithm.name} 的源码目录不受平台管理，无法安全发布")
    destination = target_dir / str(algorithm.id)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_dir, destination)
    copied_artifact = destination / artifact_path.name
    if not copied_artifact.is_file():
        raise WorkflowReleaseError(f"算法 {algorithm.name} 的制品复制不完整")
    attrs = {
        key: _json_copy(getattr(algorithm, key))
        for key in (
            "name", "category", "subcategory", "description", "algorithm",
            "framework", "difficulty", "version", "status", "input_features",
            "output_type", "use_case", "tags", "entry_point", "params_schema",
            "estimated_training_time",
        )
    }
    return {
        "source_id": algorithm.id,
        "attributes": attrs,
        "metrics": metadata,
        "directory": str(destination.relative_to(target_dir.parent)),
        "artifact_name": artifact_path.name,
        "sha256": hashlib.sha256(copied_artifact.read_bytes()).hexdigest(),
    }


def create_release(db, pipeline: Pipeline) -> dict:
    definition = _pipeline_definition(pipeline)
    token = str(uuid.uuid4())
    root = RELEASE_ROOT / token
    root.mkdir(parents=True, exist_ok=False)
    try:
        datasets_dir = root / "datasets"
        algorithms_dir = root / "algorithms"
        datasets = []
        for dataset_id in sorted(_referenced_ids(definition, "dataset_id") | _referenced_ids(definition, "evaluation_dataset_id")):
            dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
            if not dataset:
                raise WorkflowReleaseError(f"流程引用的数据集 {dataset_id} 不存在")
            datasets.append(_dataset_snapshot(dataset, datasets_dir))
        cleaning_rules = [
            _cleaning_rule_snapshot(db, rule_id, pipeline.organization_id)
            for rule_id in sorted(_referenced_cleaning_rule_ids(definition))
        ]
        algorithms = []
        for algorithm_id in sorted(_referenced_ids(definition, "algorithm_id")):
            algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == algorithm_id).first()
            if not algorithm:
                raise WorkflowReleaseError(f"流程引用的算法 {algorithm_id} 不存在")
            algorithms.append(_algorithm_snapshot(algorithm, algorithms_dir))
        manifest = {
            "schema_version": RELEASE_SCHEMA,
            "release_id": token,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": definition,
            "datasets": datasets,
            "cleaning_rules": cleaning_rules,
            "algorithms": algorithms,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def load_release(token: str) -> tuple[Path, dict]:
    safe_token = _safe_token(token)
    root = (RELEASE_ROOT / safe_token).resolve()
    if RELEASE_ROOT.resolve() not in root.parents or not root.is_dir():
        raise WorkflowReleaseError("工作流复现链接已失效或不存在")
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowReleaseError("工作流复现快照损坏") from exc
    if manifest.get("schema_version") != RELEASE_SCHEMA or manifest.get("release_id") != safe_token:
        raise WorkflowReleaseError("工作流复现快照格式不兼容")
    return root, manifest


def release_summary(token: str) -> dict:
    _, manifest = load_release(token)
    return {
        "release_id": manifest["release_id"],
        "name": manifest["pipeline"].get("name") or "未命名工作流",
        "node_count": len(manifest["pipeline"].get("tasks") or []),
        "dataset_count": len(manifest.get("datasets") or []),
        "cleaning_rule_count": len(manifest.get("cleaning_rules") or []),
        "algorithm_count": len(manifest.get("algorithms") or []),
        "created_at": manifest.get("created_at"),
        "schema_version": manifest.get("schema_version"),
    }


def _copy_release_relative(root: Path, relative: str, target: Path) -> Path:
    source = (root / relative).resolve()
    if root not in source.parents and source != root:
        raise WorkflowReleaseError("复现快照包含非法文件路径")
    _copy_file(source, target)
    return target


def import_release(db, token: str, organization_id: int | None, owner_id: int | None) -> dict:
    root, manifest = load_release(token)
    dataset_map: dict[int, int] = {}
    cleaning_rule_map: dict[int, int] = {}
    algorithm_map: dict[int, int] = {}
    try:
        for item in manifest.get("datasets") or []:
            dataset = Dataset(
                name=f"{item.get('name') or '数据集'}（复现快照）",
                description=(item.get("description") or "") + " [由工作流复现链接导入]",
                row_count=int(item.get("row_count") or 0),
                size_bytes=int(item.get("size_bytes") or 0),
                columns_info=item.get("columns_info") or {},
                stage="raw",
                organization_id=organization_id,
            )
            db.add(dataset)
            db.flush()
            relative = str(item.get("file") or "")
            suffix = Path(relative).suffix or ".csv"
            destination = DATASET_ROOT / f"{dataset.id}_reproduction{suffix}"
            _copy_release_relative(root, relative, destination)
            dataset.file_path = str(destination)
            dataset.size_bytes = destination.stat().st_size
            dataset_map[int(item["source_id"])] = dataset.id

        for item in manifest.get("cleaning_rules") or []:
            definition = _json_copy(item.get("definition") or {})
            row = db.execute(
                text("""
                    INSERT INTO data_cleaning_rules
                    (name, description, source_mode, definition, code_text, file_name, is_active, organization_id, created_by, created_at, updated_at)
                    VALUES (:name, :description, :source_mode, CAST(:definition AS JSONB), :code_text, :file_name, :is_active, :organization_id, :created_by, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "name": str(item.get("name") or "清洗规则")[:200] + "（复现快照）",
                    "description": ((item.get("description") or "") + " [由工作流复现链接导入]")[:2000],
                    "source_mode": item.get("source_mode") or "guided",
                    "definition": json.dumps(definition, ensure_ascii=False),
                    "code_text": item.get("code_text"),
                    "file_name": item.get("file_name"),
                    "is_active": bool(item.get("is_active", True)),
                    "organization_id": organization_id,
                    "created_by": owner_id,
                },
            ).scalar()
            cleaning_rule_map[int(item["source_id"])] = int(row)

        for item in manifest.get("algorithms") or []:
            attrs = item.get("attributes") or {}
            metadata = _json_copy(item.get("metrics") or {})
            algorithm = AlgorithmModel(
                name=f"{attrs.get('name') or '算法'}（复现快照）",
                category=attrs.get("category") or "custom",
                subcategory=attrs.get("subcategory"),
                description=(attrs.get("description") or "") + " [由工作流复现链接导入]",
                algorithm=attrs.get("algorithm"),
                framework=attrs.get("framework") or "python",
                difficulty=attrs.get("difficulty") or "intermediate",
                version=attrs.get("version") or "1.0.0",
                status="published",
                input_features=attrs.get("input_features") or [],
                output_type=attrs.get("output_type"),
                use_case=attrs.get("use_case"),
                tags=attrs.get("tags") or [],
                entry_point=attrs.get("entry_point"),
                params_schema=attrs.get("params_schema"),
                estimated_training_time=attrs.get("estimated_training_time"),
                metrics=metadata,
            )
            db.add(algorithm)
            db.flush()
            source_dir = (root / str(item.get("directory") or "")).resolve()
            if root not in source_dir.parents or not source_dir.is_dir():
                raise WorkflowReleaseError("算法复现快照目录无效")
            destination_dir = ALGORITHM_ROOT / str(algorithm.id)
            shutil.copytree(source_dir, destination_dir)
            artifact_name = Path(str(item.get("artifact_name") or "")).name
            metadata["artifact_path"] = str(destination_dir / artifact_name)
            if metadata.get("source_project_root"):
                metadata["source_project_root"] = str(destination_dir / "project")
            algorithm.metrics = metadata
            algorithm_map[int(item["source_id"])] = algorithm.id

        definition = _json_copy(manifest["pipeline"])
        for task in definition.get("tasks") or []:
            config = task.get("config") or {}
            for key, mapping in (
                ("dataset_id", dataset_map),
                ("evaluation_dataset_id", dataset_map),
                ("algorithm_id", algorithm_map),
                ("cleaning_rule_id", cleaning_rule_map),
            ):
                if config.get(key) not in (None, ""):
                    try:
                        original = int(config[key])
                    except (TypeError, ValueError):
                        raise WorkflowReleaseError(f"流程节点 {task.get('name')} 的 {key} 无效")
                    if original not in mapping:
                        raise WorkflowReleaseError(f"复现快照缺少节点 {task.get('name')} 所需的 {key}")
                    config[key] = mapping[original]

        pipeline = Pipeline(
            name=f"{definition.get('name') or '工作流'}（复现）",
            description=(definition.get("description") or "") + " [由工作流复现链接导入]",
            trigger_type="manual",
            status="draft",
            owner_id=owner_id,
            organization_id=organization_id,
        )
        db.add(pipeline)
        db.flush()
        for index, task in enumerate(definition.get("tasks") or []):
            db.add(PipelineTask(
                pipeline_id=pipeline.id,
                name=str(task.get("name") or f"node_{index + 1}"),
                task_type=str(task.get("task_type") or "custom"),
                command=task.get("command"),
                config=task.get("config") or {},
                dependencies=list(task.get("dependencies") or []),
                order_index=int(task.get("order_index") or index),
                timeout=int(task.get("timeout") or 1800),
                retry=int(task.get("retry") or 0),
            ))
        db.commit()
        db.refresh(pipeline)
        return pipeline.to_dict()
    except Exception:
        db.rollback()
        raise
