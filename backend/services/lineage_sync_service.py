"""把平台中的真实数据集、训练任务和模型幂等同步为血缘图。"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session


def _upsert_node(
    db: Session, *, node_id: str, name: str, display_name: str, node_type: str,
    system: str, description: str = "", metadata: dict | None = None,
    resource_type: str | None = None, resource_id: int | None = None,
    tags: list | None = None, created_by: str = "lineage_sync",
    organization_id: int | None = None,
) -> None:
    db.execute(text("""
        INSERT INTO lineage_nodes
            (id, name, display_name, node_type, system, description, extra_metadata,
             tags, is_active, resource_type, resource_id, organization_id,
             created_by, created_at, updated_at)
        VALUES
            (:id, :name, :display_name, :node_type, :system, :description,
             CAST(:metadata AS JSON), CAST(:tags AS JSON), 1, :resource_type,
             :resource_id, :organization_id, :created_by, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, display_name = EXCLUDED.display_name,
            description = EXCLUDED.description, extra_metadata = EXCLUDED.extra_metadata,
            resource_type = COALESCE(EXCLUDED.resource_type, lineage_nodes.resource_type),
            resource_id = COALESCE(EXCLUDED.resource_id, lineage_nodes.resource_id),
            organization_id = COALESCE(EXCLUDED.organization_id, lineage_nodes.organization_id),
            updated_at = NOW()
    """), {
        "id": node_id, "name": name, "display_name": display_name or name,
        "node_type": node_type, "system": system, "description": description,
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "resource_type": resource_type, "resource_id": resource_id,
        "organization_id": organization_id, "created_by": created_by,
    })


def _upsert_edge(
    db: Session, *, edge_id: str, source_id: str, target_id: str,
    edge_type: str, transformation: str = "", organization_id: int | None = None,
) -> None:
    db.execute(text("""
        INSERT INTO lineage_edges
            (id, source_id, target_id, edge_type, transformation, organization_id,
             is_active, created_at, updated_at)
        VALUES (:id, :source, :target, :etype, :trans, :organization_id, 1, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            edge_type = EXCLUDED.edge_type,
            transformation = EXCLUDED.transformation,
            organization_id = COALESCE(EXCLUDED.organization_id, lineage_edges.organization_id),
            updated_at = NOW()
    """), {
        "id": edge_id, "source": source_id, "target": target_id,
        "etype": edge_type, "trans": transformation, "organization_id": organization_id,
    })


def upsert_dataset_node(db: Session, dataset_id: int, *, commit: bool = True) -> None:
    row = db.execute(text(
        "SELECT name, organization_id FROM datasets WHERE id = :id"
    ), {"id": dataset_id}).fetchone()
    name = row[0] if row else f"数据集#{dataset_id}"
    organization_id = row[1] if row else None
    _upsert_node(
        db, node_id=f"ln-dataset-{dataset_id}", name=name, display_name=name,
        node_type="table", system="data_platform", description=f"数据集 #{dataset_id}",
        metadata={"dataset_id": dataset_id}, resource_type="dataset",
        resource_id=dataset_id, organization_id=organization_id, tags=["自动同步"],
    )
    if commit:
        db.commit()


def upsert_training_lineage(db: Session, job_db_id: int) -> None:
    row = db.execute(text("""
        SELECT tj.id, tj.training_job_id, tj.name, tj.dataset_id, tj.status,
               COALESCE(tj.registered_model_id, tj.model_id) AS model_id,
               d.name AS dataset_name, m.name AS model_name, tj.organization_id
        FROM training_jobs tj
        LEFT JOIN datasets d ON d.id = tj.dataset_id
        LEFT JOIN models m ON m.id = COALESCE(tj.registered_model_id, tj.model_id)
        WHERE tj.id = :jid
    """), {"jid": job_db_id}).fetchone()
    if not row:
        return
    job_id, job_ref, job_name, dataset_id, status, model_id, dataset_name, model_name, org_id = row
    if not dataset_id:
        return
    dataset_name = dataset_name or f"数据集#{dataset_id}"
    job_label = job_name or job_ref or f"训练任务#{job_db_id}"
    _upsert_node(
        db, node_id=f"ln-dataset-{dataset_id}", name=dataset_name,
        display_name=dataset_name, node_type="table", system="data_platform",
        description=f"数据集 #{dataset_id}", metadata={"dataset_id": dataset_id},
        resource_type="dataset", resource_id=dataset_id, organization_id=org_id,
        tags=["自动同步"],
    )
    _upsert_node(
        db, node_id=f"ln-job-{job_db_id}", name=job_label, display_name=job_label,
        node_type="pipeline", system="training_engine", description="模型训练任务",
        metadata={"training_job_id": job_ref, "training_job_db_id": job_db_id,
                  "status": str(status) if status else None, "dataset_id": dataset_id},
        resource_type="training_job", resource_id=job_db_id,
        organization_id=org_id, tags=["自动同步"],
    )
    db.flush()
    _upsert_edge(
        db, edge_id=f"ln-edge-train-{job_db_id}", source_id=f"ln-dataset-{dataset_id}",
        target_id=f"ln-job-{job_db_id}", edge_type="train",
        transformation=f"训练任务 {job_ref or job_db_id} 读取数据集 #{dataset_id}",
        organization_id=org_id,
    )
    if model_id:
        _upsert_node(
            db, node_id=f"ln-model-{model_id}", name=model_name or f"模型#{model_id}",
            display_name=model_name or f"模型#{model_id}", node_type="model",
            system="model_registry", description=f"训练任务 {job_ref or job_db_id} 生成的模型",
            metadata={"model_id": model_id}, resource_type="model", resource_id=model_id,
            organization_id=org_id, tags=["自动同步"],
        )
        db.flush()
        _upsert_edge(
            db, edge_id=f"ln-edge-model-{job_db_id}", source_id=f"ln-job-{job_db_id}",
            target_id=f"ln-model-{model_id}", edge_type="produces",
            transformation=f"训练任务 {job_ref or job_db_id} 注册模型 #{model_id}",
            organization_id=org_id,
        )
    db.commit()


def sync_training_lineage_batch(
    db: Session, limit: int = 200, organization_id: int | None = None,
) -> dict:
    rows = db.execute(text("""
        SELECT id FROM training_jobs
        WHERE dataset_id IS NOT NULL
          AND (:organization_id IS NULL OR organization_id = :organization_id)
        ORDER BY id DESC LIMIT :limit
    """), {"limit": limit, "organization_id": organization_id}).fetchall()
    synced = 0
    failed = 0
    for row in rows:
        try:
            upsert_training_lineage(db, int(row[0]))
            synced += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            print(f"[lineage_sync] job {row[0]} 同步失败: {exc}")
    return {"jobs": len(rows), "synced": synced, "failed": failed}


STAGE_LABELS = {
    "cleaned": "数据清洗", "governed": "清洗分析/治理", "engineered": "特征工程",
    "labeled": "数据标注", "aligned": "数据对齐", "masked": "数据脱敏",
    "split_train": "数据分割（训练）", "split_test": "数据分割（测试）",
}


def _dataset_operation(stage: str | None, name: str | None, description: str | None) -> str:
    if stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    hint = f"{name or ''} {description or ''}"
    for keyword, label in (
        ("对齐", "数据对齐"), ("脱敏", "数据脱敏"), ("标注", "数据标注"),
        ("特征", "特征工程"), ("测试", "数据分割（测试）"),
        ("训练", "数据分割（训练）"), ("清洗", "数据清洗"), ("治理", "数据治理"),
    ):
        if keyword in hint:
            return label
    return "数据处理"


def upsert_derived_dataset_lineage(
    db: Session, output_dataset_id: int, *, commit: bool = True,
) -> bool:
    row = db.execute(text("""
        SELECT child.id, child.name, child.stage, child.description, child.governed_from,
               child.organization_id, parent.name
        FROM datasets child JOIN datasets parent ON parent.id = child.governed_from
        WHERE child.id = :id
    """), {"id": output_dataset_id}).fetchone()
    if not row:
        return False
    child_id, child_name, stage, description, parent_id, org_id, parent_name = row
    operation = _dataset_operation(stage, child_name, description)
    for dataset_id, dataset_name, role in (
        (parent_id, parent_name, "源数据集"), (child_id, child_name, "派生数据集")
    ):
        _upsert_node(
            db, node_id=f"ln-dataset-{dataset_id}", name=dataset_name,
            display_name=dataset_name, node_type="table", system="data_platform",
            description=f"{role} #{dataset_id}",
            metadata={"dataset_id": dataset_id, "stage": stage if role == "派生数据集" else None},
            resource_type="dataset", resource_id=dataset_id, organization_id=org_id,
            tags=["自动同步", role],
        )
    db.flush()
    _upsert_edge(
        db, edge_id=f"ln-edge-derived-{parent_id}-{child_id}",
        source_id=f"ln-dataset-{parent_id}", target_id=f"ln-dataset-{child_id}",
        edge_type="derived", transformation=operation, organization_id=org_id,
    )
    if commit:
        db.commit()
    return True


def upsert_dataset_relation(
    db: Session, source_dataset_id: int, output_dataset_id: int,
    operation: str, *, edge_key: str | None = None, commit: bool = True,
) -> bool:
    """记录显式的单来源或多来源处理关系；适用于数据对齐等双输入流程。"""
    rows = db.execute(text("""
        SELECT id, name, organization_id FROM datasets WHERE id IN (:source_id, :output_id)
    """), {"source_id": source_dataset_id, "output_id": output_dataset_id}).fetchall()
    assets = {int(row[0]): row for row in rows}
    if source_dataset_id not in assets or output_dataset_id not in assets:
        return False
    source = assets[source_dataset_id]
    output = assets[output_dataset_id]
    org_id = output[2] or source[2]
    for dataset_id, row, role in (
        (source_dataset_id, source, "源数据集"), (output_dataset_id, output, "派生数据集")
    ):
        _upsert_node(
            db, node_id=f"ln-dataset-{dataset_id}", name=row[1], display_name=row[1],
            node_type="table", system="data_platform", description=f"{role} #{dataset_id}",
            metadata={"dataset_id": dataset_id}, resource_type="dataset",
            resource_id=dataset_id, organization_id=org_id, tags=["自动同步", role],
        )
    db.flush()
    suffix = edge_key or f"{source_dataset_id}-{output_dataset_id}"
    _upsert_edge(
        db, edge_id=f"ln-edge-derived-{suffix}", source_id=f"ln-dataset-{source_dataset_id}",
        target_id=f"ln-dataset-{output_dataset_id}", edge_type="derived",
        transformation=operation, organization_id=org_id,
    )
    if commit:
        db.commit()
    return True


def sync_dataset_lineage_batch(
    db: Session, limit: int = 1000, organization_id: int | None = None,
) -> dict:
    rows = db.execute(text("""
        SELECT id FROM datasets
        WHERE governed_from IS NOT NULL
          AND (:organization_id IS NULL OR organization_id = :organization_id)
        ORDER BY id DESC LIMIT :limit
    """), {"limit": limit, "organization_id": organization_id}).fetchall()
    synced = 0
    failed = 0
    for row in rows:
        try:
            synced += int(upsert_derived_dataset_lineage(db, int(row[0]), commit=False))
        except Exception as exc:
            failed += 1
            print(f"[lineage_sync] dataset {row[0]} 同步失败: {exc}")
    # 旧版对齐结果没有 governed_from；从平台生成的标准描述中恢复两条输入边。
    alignment_rows = db.execute(text("""
        SELECT id, description FROM datasets
        WHERE file_path LIKE '%_aligned.csv'
          AND (:organization_id IS NULL OR organization_id = :organization_id)
        ORDER BY id DESC LIMIT :limit
    """), {"limit": limit, "organization_id": organization_id}).fetchall()
    alignment_relations = 0
    import re
    for output_id, description in alignment_rows:
        source_ids = [int(value) for value in re.findall(r"数据集\s*#(\d+)", description or "")]
        for index, source_id in enumerate(dict.fromkeys(source_ids[:2])):
            try:
                alignment_relations += int(upsert_dataset_relation(
                    db, source_id, int(output_id),
                    "数据对齐（主数据集）" if index == 0 else "数据对齐（辅助数据集）",
                    edge_key=f"align-{source_id}-{output_id}", commit=False,
                ))
            except Exception as exc:
                failed += 1
                print(f"[lineage_sync] alignment {source_id}->{output_id} 同步失败: {exc}")
    db.commit()
    return {
        "derived_datasets": len(rows), "synced": synced,
        "alignment_relations": alignment_relations, "failed": failed,
    }


def safe_sync_derived_dataset(db: Session, output_dataset_id: int) -> bool:
    """尽力同步血缘；血缘异常不得把已经成功的数据处理结果回滚。"""
    try:
        return upsert_derived_dataset_lineage(db, output_dataset_id)
    except Exception as exc:
        db.rollback()
        print(f"[lineage_sync] dataset {output_dataset_id} 实时同步失败: {exc}")
        return False


def safe_sync_dataset_relation(
    db: Session, source_dataset_id: int, output_dataset_id: int,
    operation: str, *, edge_key: str | None = None,
) -> bool:
    try:
        return upsert_dataset_relation(
            db, source_dataset_id, output_dataset_id, operation,
            edge_key=edge_key, commit=True,
        )
    except Exception as exc:
        db.rollback()
        print(f"[lineage_sync] relation {source_dataset_id}->{output_dataset_id} 同步失败: {exc}")
        return False
