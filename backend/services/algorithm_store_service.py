"""
算法商店服务 — 数据库持久化，预设方法自动播种
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.core import Dataset, AlgorithmModel
from services.algorithm_source_service import (
    algorithm_asset_type, algorithm_summary, sync_builtin_algorithms,
)

# 预设方法（首次启动时播种到数据库）
# 2026-10-16 架构收敛：移除钢铁工艺预设，内置算法统一由
# sync_builtin_algorithms() 以 category="builtin" 播种。
PRESET_METHODS: List[Dict[str, Any]] = []

class AlgorithmStore:
    def __init__(self):
        self._seeded = False

    def _seed_presets(self, db: Session):
        """The canonical built-ins are seeded by sync_builtin_algorithms()."""
        if self._seeded:
            return
        self._seeded = True

    # ─── 查询 ───

    def list_models(self, db: Session, category=None, search=None, page=1, size=20):
        self._seed_presets(db)
        sync_builtin_algorithms(db)
        models = db.query(AlgorithmModel).order_by(
            AlgorithmModel.created_at.desc(), AlgorithmModel.id.desc()
        ).all()
        models = [model for model in models if algorithm_asset_type(model) != "unsupported"]
        if category:
            models = [model for model in models if algorithm_asset_type(model) == category]
        if search:
            term = search.lower()
            models = [
                model for model in models
                if term in (model.name or "").lower()
                or term in (model.description or "").lower()
                or (search.isdigit() and model.id == int(search))
            ]
        total = len(models)
        start = (page - 1) * size
        items = models[start:start + size]
        payload = []
        for model in items:
            item = model.to_dict()
            item.update(algorithm_summary(model))
            payload.append(item)
        return {"total": total, "page": page, "size": size, "items": payload}

    def get_model(self, db: Session, method_id):
        self._seed_presets(db)
        sync_builtin_algorithms(db)
        try:
            mid = int(method_id)
        except (ValueError, TypeError):
            return None
        m = db.query(AlgorithmModel).filter(AlgorithmModel.id == mid).first()
        if not m:
            return None
        result = m.to_dict()
        result.update(algorithm_summary(m))
        return result

    # ─── 兼容数据集 ───

    def get_compatible_datasets(self, db: Session, method_id, page=1, size=20):
        method = self.get_model(db, method_id)
        if not method:
            return {"total": 0, "page": page, "size": size, "items": []}

        tags = [t.lower() for t in method.get("tags", [])]

        # 与真实数据加载器保持一致：只要存在可读取的 CSV 就可训练。
        # 不能只依赖 row_count；部分历史上传记录的行数元数据为 0，但文件有效。
        datasets = db.query(Dataset).all()
        result = []
        for ds in datasets:
            data_file = ds.file_path if ds.file_path and os.path.exists(ds.file_path) else None
            if not data_file:
                candidates = sorted(Path("/app/uploads/datasets").glob(f"{ds.id}_*.csv"))
                data_file = str(candidates[0]) if candidates else None
            if not data_file:
                continue
            row_count = ds.row_count or 0
            if row_count <= 0:
                try:
                    with open(data_file, "rb") as source:
                        row_count = max(sum(1 for _ in source) - 1, 0)
                except OSError:
                    continue
            score = sum(2 for t in tags if t in ds.name.lower())
            if hasattr(ds, 'columns_info') and ds.columns_info:
                try:
                    cols = json.loads(ds.columns_info) if isinstance(ds.columns_info, str) else ds.columns_info
                    col_names = ' '.join([c.get('name','') for c in cols]).lower()
                    score += sum(1 for t in tags if t in col_names)
                except:
                    pass
            result.append({"id": ds.id, "name": ds.name, "row_count": row_count, "match_score": score})

        # 按匹配分数排序
        result.sort(key=lambda x: -x["match_score"])

        # 如果所有分数都是0，则设为1（保证所有数据集都有机会）
        if result and all(r["match_score"] == 0 for r in result):
            for r in result:
                r["match_score"] = 1

        # 分页
        total = len(result)
        start = (page - 1) * size
        end = start + size
        items = result[start:end]

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": items
        }

    # ─── 增删 ───

    def promote_method(self, db: Session, data: dict):
        """AutoML results belong to selection records and cannot become algorithm definitions."""
        raise ValueError("AutoML 结果属于选型记录，不能提升为新的算法定义")

    def delete_method(self, db: Session, method_id):
        mid = int(method_id) if isinstance(method_id, str) else method_id
        m = db.query(AlgorithmModel).filter(AlgorithmModel.id == mid).first()
        if not m:
            raise ValueError(f"方法 {method_id} 不存在")
        name = m.name
        db.delete(m)
        db.commit()
        return {"id": mid, "name": name, "deleted": True}

    def publish_method(self, db: Session, method_id):
        m = db.query(AlgorithmModel).filter(AlgorithmModel.id == int(method_id)).first()
        if not m:
            raise ValueError(f"方法 {method_id} 不存在")
        m.status = "published"
        db.commit()
        return {"id": m.id, "name": m.name, "status": "published"}

    def unpublish_method(self, db: Session, method_id):
        m = db.query(AlgorithmModel).filter(AlgorithmModel.id == int(method_id)).first()
        if not m:
            raise ValueError(f"方法 {method_id} 不存在")
        m.status = "unpublished"
        db.commit()
        return {"id": m.id, "name": m.name, "status": "unpublished"}

    # ─── 分类与推荐 ───

    def get_categories(self):
        return [
            {"key": "builtin", "name": "内置算法"},
            {"key": "uploaded_source", "name": "上传算法"},
            {"key": "finetune_model", "name": "可继续训练模型"},
        ]

    def recommend(self, db: Session, task=None, category=None):
        self._seed_presets(db)
        sync_builtin_algorithms(db)
        items = db.query(AlgorithmModel).order_by(AlgorithmModel.downloads.desc()).all()
        items = [model for model in items if algorithm_asset_type(model) != "unsupported"]
        if category:
            items = [model for model in items if algorithm_asset_type(model) == category]
        items = items[:10]
        payload = []
        for model in items:
            item = model.to_dict()
            item.update(algorithm_summary(model))
            payload.append(item)
        return {"total": len(payload), "items": payload}


_store = AlgorithmStore()

def get_algorithm_store():
    return _store
