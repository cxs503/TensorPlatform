"""
标签管理系统
提供数据标注、标签管理、标注任务分发、标注审核工作流

持久化: 所有标签/标签组/标注任务/标注结果存 PostgreSQL
(2026-07-15 由内存字典改为 DB 持久化, 表定义见 models/labeling.py)
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.config import SessionLocal
from models.labeling import (
    Annotation as AnnotationORM,
    AnnotationTask as AnnotationTaskORM,
    Label as LabelORM,
    LabelGroup as LabelGroupORM,
    LabelGroupItem,
)


class LabelStatus(str, Enum):
    """标签状态"""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class AnnotationTaskStatus(str, Enum):
    """标注任务状态"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    APPROVED = "approved"


class AnnotationType(str, Enum):
    """标注类型"""

    CLASSIFICATION = "classification"  # 分类
    OBJECT_DETECTION = "object_detection"  # 目标检测
    SEGMENTATION = "segmentation"  # 分割
    NER = "ner"  # 命名实体识别
    TEXT_CLASSIFICATION = "text_classification"  # 文本分类
    SENTIMENT = "sentiment"  # 情感分析


# ========== 领域对象 (DTO) ==========
# 保持与历史 API 完全一致的属性, 由 DB 行构造。


class Label:
    """标签定义"""

    def __init__(
        self, name: str, category: str, color: str = "#000000", description: str = None
    ):
        self.id = f"label_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.category = category
        self.color = color
        self.description = description
        self.status = LabelStatus.DRAFT
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.usage_count = 0


class LabelGroup:
    """标签组"""

    def __init__(self, name: str, description: str = None):
        self.id = f"group_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.description = description
        self.labels: List[str] = []  # label IDs
        self.created_at = datetime.now().isoformat()


class AnnotationTask:
    """标注任务"""

    def __init__(
        self,
        name: str,
        dataset_id: str,
        annotation_type: str,
        label_group_id: str,
        assignee_id: str = None,
    ):
        self.id = f"task_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.dataset_id = dataset_id
        self.annotation_type = annotation_type
        self.label_group_id = label_group_id
        self.assignee_id = assignee_id
        self.status = AnnotationTaskStatus.PENDING
        self.total_items = 0
        self.completed_items = 0
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.deadline = None


class Annotation:
    """标注结果"""

    def __init__(
        self,
        task_id: str,
        data_id: str,
        annotator_id: str,
        labels: List[Dict],
        metadata: Dict = None,
    ):
        self.id = f"ann_{uuid.uuid4().hex[:12]}"
        self.task_id = task_id
        self.data_id = data_id
        self.annotator_id = annotator_id
        self.labels = labels
        self.metadata = metadata or {}
        self.status = AnnotationTaskStatus.SUBMITTED
        self.created_at = datetime.now().isoformat()
        self.reviewed_at = None
        self.reviewer_id = None
        self.review_comment = None


# ========== ORM <-> DTO 转换 ==========


def _label_from_orm(row: LabelORM) -> Label:
    label = Label.__new__(Label)
    label.id = row.id
    label.name = row.name
    label.category = row.category
    label.color = row.color or "#000000"
    label.description = row.description
    # status 历史上是 LabelStatus 枚举实例; 兼容 DB 里的字符串
    status_val = row.status or "draft"
    try:
        label.status = LabelStatus(status_val)
    except ValueError:
        label.status = status_val
    label.usage_count = row.usage_count or 0
    label.created_at = row.created_at.isoformat() if row.created_at else None
    label.updated_at = row.updated_at.isoformat() if row.updated_at else None
    return label


def _task_from_orm(row: AnnotationTaskORM) -> AnnotationTask:
    task = AnnotationTask.__new__(AnnotationTask)
    task.id = row.id
    task.name = row.name
    task.dataset_id = row.dataset_id
    task.annotation_type = row.annotation_type
    task.label_group_id = row.label_group_id
    task.assignee_id = row.assignee_id
    status_val = row.status or "pending"
    try:
        task.status = AnnotationTaskStatus(status_val)
    except ValueError:
        task.status = status_val
    task.total_items = row.total_items or 0
    task.completed_items = row.completed_items or 0
    task.deadline = row.deadline.isoformat() if row.deadline else None
    task.created_at = row.created_at.isoformat() if row.created_at else None
    task.updated_at = row.updated_at.isoformat() if row.updated_at else None
    return task


def _annotation_from_orm(row: AnnotationORM) -> Annotation:
    ann = Annotation.__new__(Annotation)
    ann.id = row.id
    ann.task_id = row.task_id
    ann.data_id = row.data_id
    ann.annotator_id = row.annotator_id
    ann.labels = row.labels or []
    ann.metadata = row.annotation_metadata or {}
    status_val = row.status or "submitted"
    try:
        ann.status = AnnotationTaskStatus(status_val)
    except ValueError:
        ann.status = status_val
    ann.created_at = row.created_at.isoformat() if row.created_at else None
    ann.reviewed_at = row.reviewed_at.isoformat() if row.reviewed_at else None
    ann.reviewer_id = row.reviewer_id
    ann.review_comment = row.review_comment
    return ann


class LabelingService:
    """标签管理服务 (DB 持久化)"""

    def __init__(self):
        # 确保表存在(models 注册后 create_all 只建缺失表)
        try:
            from database.config import Base, engine

            # 触发 labeling 模型注册到 Base.metadata
            import models.labeling  # noqa: F401
            Base.metadata.create_all(bind=engine, tables=[
                LabelORM.__table__,
                LabelGroupORM.__table__,
                LabelGroupItem.__table__,
                AnnotationTaskORM.__table__,
                AnnotationORM.__table__,
            ])
        except Exception:
            pass

    # ----- session helper -----
    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    # ========== 标签管理 ==========

    def create_label(
        self, name: str, category: str, color: str = "#000000", description: str = None
    ) -> Label:
        """创建标签"""
        label = Label(name, category, color, description)
        db = self._session()
        try:
            row = LabelORM(
                id=label.id,
                name=label.name,
                category=label.category,
                color=label.color,
                description=label.description,
                status=label.status.value if isinstance(label.status, Enum) else label.status,
                usage_count=0,
            )
            db.add(row)
            db.commit()
            return label
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_label(self, label_id: str, **kwargs) -> Optional[Label]:
        """更新标签"""
        db = self._session()
        try:
            row = db.query(LabelORM).filter(LabelORM.id == label_id).first()
            if not row:
                return None
            for key, value in kwargs.items():
                if hasattr(row, key):
                    # DTO 字段名 metadata 与 ORM 的 annotation_metadata 冲突仅出现在 annotation,
                    # label 这里没有 metadata 字段, 直接 setattr
                    setattr(row, key, value)
            row.updated_at = datetime.now()
            db.commit()
            db.refresh(row)
            return _label_from_orm(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def delete_label(self, label_id: str) -> bool:
        """删除标签"""
        db = self._session()
        try:
            row = db.query(LabelORM).filter(LabelORM.id == label_id).first()
            if not row:
                return False
            db.delete(row)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_label(self, label_id: str) -> Optional[Label]:
        """获取标签"""
        db = self._session()
        try:
            row = db.query(LabelORM).filter(LabelORM.id == label_id).first()
            return _label_from_orm(row) if row else None
        finally:
            db.close()

    def list_labels(self, category: str = None, status: str = None) -> List[Dict]:
        """列出标签"""
        db = self._session()
        try:
            q = db.query(LabelORM)
            if category:
                q = q.filter(LabelORM.category == category)
            if status:
                # 兼容传入枚举或字符串
                sv = status.value if isinstance(status, Enum) else status
                q = q.filter(LabelORM.status == sv)
            rows = q.order_by(LabelORM.created_at).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "category": r.category,
                    "color": r.color,
                    "description": r.description,
                    "status": r.status,
                    "usage_count": r.usage_count or 0,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        finally:
            db.close()

    # ========== 标签组管理 ==========

    def create_label_group(self, name: str, description: str = None) -> LabelGroup:
        """创建标签组"""
        group = LabelGroup(name, description)
        db = self._session()
        try:
            row = LabelGroupORM(
                id=group.id,
                name=group.name,
                description=group.description,
            )
            db.add(row)
            db.commit()
            return group
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_label_to_group(self, group_id: str, label_id: str) -> bool:
        """添加标签到组"""
        db = self._session()
        try:
            group = db.query(LabelGroupORM).filter(LabelGroupORM.id == group_id).first()
            label = db.query(LabelORM).filter(LabelORM.id == label_id).first()
            if not group or not label:
                return False
            exists = (
                db.query(LabelGroupItem)
                .filter(
                    LabelGroupItem.group_id == group_id,
                    LabelGroupItem.label_id == label_id,
                )
                .first()
            )
            if exists:
                return True
            # position = 当前组内最大 position + 1
            max_pos = (
                db.query(LabelGroupItem)
                .filter(LabelGroupItem.group_id == group_id)
                .count()
            )
            db.add(
                LabelGroupItem(
                    group_id=group_id, label_id=label_id, position=max_pos
                )
            )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def remove_label_from_group(self, group_id: str, label_id: str) -> bool:
        """从组移除标签"""
        db = self._session()
        try:
            group = db.query(LabelGroupORM).filter(LabelGroupORM.id == group_id).first()
            if not group:
                return False
            item = (
                db.query(LabelGroupItem)
                .filter(
                    LabelGroupItem.group_id == group_id,
                    LabelGroupItem.label_id == label_id,
                )
                .first()
            )
            if item:
                db.delete(item)
                db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_label_groups(self) -> List[Dict]:
        """列出标签组"""
        db = self._session()
        try:
            groups = db.query(LabelGroupORM).order_by(LabelGroupORM.created_at).all()
            result = []
            for g in groups:
                items = (
                    db.query(LabelGroupItem, LabelORM)
                    .join(LabelORM, LabelGroupItem.label_id == LabelORM.id)
                    .filter(LabelGroupItem.group_id == g.id)
                    .order_by(LabelGroupItem.position)
                    .all()
                )
                labels = [
                    {"id": lbl.id, "name": lbl.name, "color": lbl.color}
                    for _item, lbl in items
                ]
                result.append(
                    {
                        "id": g.id,
                        "name": g.name,
                        "description": g.description,
                        "labels": labels,
                        "created_at": g.created_at.isoformat() if g.created_at else None,
                    }
                )
            return result
        finally:
            db.close()

    # ========== 标注任务管理 ==========

    def create_annotation_task(
        self,
        name: str,
        dataset_id: str,
        annotation_type: str,
        label_group_id: str,
        assignee_id: str = None,
        deadline: str = None,
        total_items: int = 0,
    ) -> AnnotationTask:
        """创建标注任务"""
        ann_type = annotation_type
        if isinstance(ann_type, Enum):
            ann_type = ann_type.value
        task = AnnotationTask(
            name, dataset_id, ann_type, label_group_id, assignee_id
        )
        if deadline:
            task.deadline = deadline
        db = self._session()
        try:
            deadline_dt = None
            if task.deadline:
                try:
                    deadline_dt = datetime.fromisoformat(task.deadline)
                except (ValueError, TypeError):
                    deadline_dt = None
            row = AnnotationTaskORM(
                id=task.id,
                name=task.name,
                dataset_id=task.dataset_id,
                annotation_type=task.annotation_type,
                label_group_id=task.label_group_id,
                assignee_id=task.assignee_id,
                status=task.status.value if isinstance(task.status, Enum) else task.status,
                total_items=max(0, int(total_items or 0)),
                completed_items=0,
                deadline=deadline_dt,
            )
            db.add(row)
            db.commit()
            return task
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_task_status(self, task_id: str, status: str) -> Optional[AnnotationTask]:
        """更新任务状态"""
        db = self._session()
        try:
            row = db.query(AnnotationTaskORM).filter(AnnotationTaskORM.id == task_id).first()
            if not row:
                return None
            sv = status.value if isinstance(status, Enum) else status
            row.status = sv
            row.updated_at = datetime.now()
            db.commit()
            db.refresh(row)
            return _task_from_orm(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def assign_task(self, task_id: str, assignee_id: str) -> bool:
        """分配任务"""
        db = self._session()
        try:
            row = db.query(AnnotationTaskORM).filter(AnnotationTaskORM.id == task_id).first()
            if not row:
                return False
            row.assignee_id = assignee_id
            row.status = AnnotationTaskStatus.IN_PROGRESS.value
            row.updated_at = datetime.now()
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_annotation_task(self, task_id: str) -> Optional[AnnotationTask]:
        """获取标注任务"""
        db = self._session()
        try:
            row = db.query(AnnotationTaskORM).filter(AnnotationTaskORM.id == task_id).first()
            return _task_from_orm(row) if row else None
        finally:
            db.close()

    def list_annotation_tasks(
        self, assignee_id: str = None, status: str = None, dataset_id: str = None
    ) -> List[Dict]:
        """列出标注任务"""
        db = self._session()
        try:
            q = db.query(AnnotationTaskORM)
            if assignee_id:
                q = q.filter(AnnotationTaskORM.assignee_id == assignee_id)
            if status:
                sv = status.value if isinstance(status, Enum) else status
                q = q.filter(AnnotationTaskORM.status == sv)
            if dataset_id:
                q = q.filter(AnnotationTaskORM.dataset_id == dataset_id)
            rows = q.order_by(AnnotationTaskORM.created_at).all()
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "dataset_id": t.dataset_id,
                    "annotation_type": t.annotation_type,
                    "label_group_id": t.label_group_id,
                    "assignee_id": t.assignee_id,
                    "status": t.status,
                    "total_items": t.total_items or 0,
                    "completed_items": t.completed_items or 0,
                    "progress": round(
                        (t.completed_items or 0) / t.total_items * 100, 2
                    )
                    if t.total_items
                    else 0,
                    "deadline": t.deadline.isoformat() if t.deadline else None,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in rows
            ]
        finally:
            db.close()

    # ========== 标注结果管理 ==========

    def submit_annotation(
        self,
        task_id: str,
        data_id: str,
        annotator_id: str,
        labels: List[Dict],
        metadata: Dict = None,
    ) -> Annotation:
        """Create or replace one row annotation without double-counting task progress."""
        db = self._session()
        try:
            task = db.query(AnnotationTaskORM).filter(AnnotationTaskORM.id == task_id).first()
            if not task:
                raise ValueError("Task not found")
            existing = db.query(AnnotationORM).filter(
                AnnotationORM.task_id == task_id,
                AnnotationORM.data_id == str(data_id),
            ).first()
            if existing:
                existing.annotator_id = annotator_id
                existing.labels = labels or []
                existing.annotation_metadata = metadata or {}
                existing.status = AnnotationTaskStatus.SUBMITTED.value
                existing.created_at = datetime.now()
                existing.reviewed_at = None
                existing.reviewer_id = None
                existing.review_comment = None
                row = existing
            else:
                annotation = Annotation(task_id, str(data_id), annotator_id, labels, metadata)
                row = AnnotationORM(
                    id=annotation.id,
                    task_id=annotation.task_id,
                    data_id=annotation.data_id,
                    annotator_id=annotation.annotator_id,
                    labels=annotation.labels,
                    annotation_metadata=annotation.metadata,
                    status=AnnotationTaskStatus.SUBMITTED.value,
                )
                db.add(row)
                task.completed_items = min(
                    int(task.total_items or 0) or int(task.completed_items or 0) + 1,
                    int(task.completed_items or 0) + 1,
                )
            if task.total_items and task.completed_items >= task.total_items:
                task.status = AnnotationTaskStatus.SUBMITTED.value
            else:
                task.status = AnnotationTaskStatus.IN_PROGRESS.value
            label_ids = {item.get("label_id") for item in (labels or []) if isinstance(item, dict) and item.get("label_id")}
            if label_ids and not existing:
                db.query(LabelORM).filter(LabelORM.id.in_(label_ids)).update(
                    {LabelORM.usage_count: LabelORM.usage_count + 1},
                    synchronize_session=False,
                )
            db.commit()
            db.refresh(row)
            return _annotation_from_orm(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def review_annotation(
        self, annotation_id: str, reviewer_id: str, approved: bool, comment: str = None
    ) -> Optional[Annotation]:
        """审核标注"""
        db = self._session()
        try:
            row = db.query(AnnotationORM).filter(AnnotationORM.id == annotation_id).first()
            if not row:
                return None
            row.reviewer_id = reviewer_id
            row.reviewed_at = datetime.now()
            row.review_comment = comment
            row.status = (
                AnnotationTaskStatus.APPROVED.value
                if approved
                else AnnotationTaskStatus.REJECTED.value
            )
            db.commit()
            db.refresh(row)
            return _annotation_from_orm(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_annotation(self, annotation_id: str) -> Optional[Annotation]:
        """获取标注"""
        db = self._session()
        try:
            row = db.query(AnnotationORM).filter(AnnotationORM.id == annotation_id).first()
            return _annotation_from_orm(row) if row else None
        finally:
            db.close()

    def list_annotations(
        self, task_id: str = None, annotator_id: str = None, status: str = None
    ) -> List[Dict]:
        """列出标注"""
        db = self._session()
        try:
            q = db.query(AnnotationORM)
            if task_id:
                q = q.filter(AnnotationORM.task_id == task_id)
            if annotator_id:
                q = q.filter(AnnotationORM.annotator_id == annotator_id)
            if status:
                sv = status.value if isinstance(status, Enum) else status
                q = q.filter(AnnotationORM.status == sv)
            rows = q.order_by(AnnotationORM.created_at).all()
            return [
                {
                    "id": a.id,
                    "task_id": a.task_id,
                    "data_id": a.data_id,
                    "annotator_id": a.annotator_id,
                    "labels": a.labels,
                    "metadata": a.annotation_metadata or {},
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
                    "reviewer_id": a.reviewer_id,
                    "review_comment": a.review_comment,
                }
                for a in rows
            ]
        finally:
            db.close()

    # ========== 统计 ==========

    def get_labeling_stats(self, task_id: str = None) -> Dict:
        """获取标注统计"""
        db = self._session()
        try:
            q = db.query(AnnotationORM)
            if task_id:
                q = q.filter(AnnotationORM.task_id == task_id)
            rows = q.all()
            total = len(rows)
            approved = sum(1 for a in rows if a.status == AnnotationTaskStatus.APPROVED.value)
            rejected = sum(1 for a in rows if a.status == AnnotationTaskStatus.REJECTED.value)
            pending = sum(1 for a in rows if a.status == AnnotationTaskStatus.SUBMITTED.value)

            return {
                "total_annotations": total,
                "approved": approved,
                "rejected": rejected,
                "pending_review": pending,
                "approval_rate": round(approved / total * 100, 2) if total > 0 else 0,
            }
        finally:
            db.close()


# 全局实例
_labeling_service: Optional[LabelingService] = None


def get_labeling_service() -> LabelingService:
    """获取标签管理服务"""
    global _labeling_service
    if _labeling_service is None:
        _labeling_service = LabelingService()
    return _labeling_service
