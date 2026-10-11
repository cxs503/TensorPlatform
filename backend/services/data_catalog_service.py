"""
数据目录服务 - 集成虚拟数据生成
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from models.data_catalog import (
    AssetMetadata,
    DataAsset,
    DataClassification,
    DataLineage,
    DataTag,
)
from sqlalchemy import text
from sqlalchemy.orm import Session


class DataCatalogService:
    """数据目录服务"""

    def __init__(self, db: Session):
        self.db = db

    def init_default_classifications(self):
        """初始化默认分类"""
        default_classifications = [
            {
                "name": "业务数据",
                "description": "核心业务相关数据",
                "color": "#409EFF",
                "icon": "Briefcase",
            },
            {
                "name": "技术数据",
                "description": "技术系统产生的数据",
                "color": "#67C23A",
                "icon": "Cpu",
            },
            {
                "name": "运维数据",
                "description": "系统运维相关数据",
                "color": "#E6A23C",
                "icon": "Setting",
            },
            {
                "name": "安全数据",
                "description": "安全敏感数据",
                "color": "#F56C6C",
                "icon": "Lock",
            },
            {
                "name": "日志数据",
                "description": "系统日志数据",
                "color": "#909399",
                "icon": "Document",
            },
            {
                "name": "传感器数据",
                "description": "IoT传感器采集数据",
                "color": "#9c27b0",
                "icon": "Connection",
            },
        ]

        for cls in default_classifications:
            existing = (
                self.db.query(DataClassification)
                .filter(DataClassification.name == cls["name"])
                .first()
            )
            if not existing:
                classification = DataClassification(**cls)
                self.db.add(classification)

        self.db.commit()

    def init_default_tags(self):
        """初始化默认标签"""
        default_tags = [
            {
                "name": "高价值",
                "color": "#F56C6C",
                "category": "business",
                "description": "高业务价值数据",
            },
            {
                "name": "实时",
                "color": "#67C23A",
                "category": "technical",
                "description": "实时数据",
            },
            {
                "name": "历史",
                "color": "#909399",
                "category": "technical",
                "description": "历史数据",
            },
            {
                "name": "PII",
                "color": "#E6A23C",
                "category": "security",
                "description": "包含个人身份信息",
            },
            {
                "name": "敏感",
                "color": "#F56C6C",
                "category": "security",
                "description": "敏感数据",
            },
            {
                "name": "公开",
                "color": "#409EFF",
                "category": "security",
                "description": "公开数据",
            },
            {
                "name": "已验证",
                "color": "#67C23A",
                "category": "general",
                "description": "数据已验证",
            },
            {
                "name": "待审核",
                "color": "#E6A23C",
                "category": "general",
                "description": "数据待审核",
            },
        ]

        for tag_data in default_tags:
            existing = (
                self.db.query(DataTag).filter(DataTag.name == tag_data["name"]).first()
            )
            if not existing:
                tag = DataTag(**tag_data)
                self.db.add(tag)

        self.db.commit()

    def register_asset_from_virtual_data(
        self,
        name: str,
        data_type: str,
        description: str,
        source: str,
        schema_info: Optional[Dict] = None,
        storage_info: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        classification: Optional[str] = None,
        owner: str = "system",
    ) -> DataAsset:
        """从虚拟数据注册资产到目录"""

        # 创建资产
        asset = DataAsset(
            name=name,
            type=data_type,
            description=description,
            owner=owner,
            source=source,
            schema_info=schema_info,
            storage_info=storage_info,
            quality_score=85,  # 虚拟数据默认质量得分
            sensitivity_level="internal",
        )

        self.db.add(asset)
        self.db.flush()  # 获取ID

        # 添加标签
        if tags:
            for tag_name in tags:
                tag = self.db.query(DataTag).filter(DataTag.name == tag_name).first()
                if not tag:
                    tag = DataTag(name=tag_name, category="general")
                    self.db.add(tag)
                    self.db.flush()
                asset.tag_objects.append(tag)

        # 添加分类
        if classification:
            cls = (
                self.db.query(DataClassification)
                .filter(DataClassification.name == classification)
                .first()
            )
            if cls:
                asset.classification_objects.append(cls)

        # 添加默认元数据
        metadata_entries = [
            {
                "key": "source_type",
                "value": "virtual_generated",
                "source": "system",
                "confidence": 1.0,
            },
            {
                "key": "generated_at",
                "value": datetime.now().isoformat(),
                "source": "system",
                "confidence": 1.0,
            },
            {
                "key": "data_format",
                "value": "json",
                "source": "system",
                "confidence": 1.0,
            },
        ]

        for meta in metadata_entries:
            metadata = AssetMetadata(asset_id=asset.id, **meta)
            self.db.add(metadata)

        self.db.commit()
        self.db.refresh(asset)

        return asset

    def auto_extract_metadata(
        self, asset_id: int, data_sample: List[Dict]
    ) -> List[AssetMetadata]:
        """自动从数据样本提取元数据"""
        if not data_sample:
            return []

        metadata_list = []

        # 提取字段信息
        fields = set()
        for record in data_sample:
            fields.update(record.keys())

        # 记录字段元数据
        metadata_list.append(
            AssetMetadata(
                asset_id=asset_id,
                key="field_count",
                value=str(len(fields)),
                source="auto",
                confidence=0.95,
            )
        )

        metadata_list.append(
            AssetMetadata(
                asset_id=asset_id,
                key="fields",
                value=",".join(sorted(fields)),
                source="auto",
                confidence=0.95,
            )
        )

        # 提取记录数
        metadata_list.append(
            AssetMetadata(
                asset_id=asset_id,
                key="record_count",
                value=str(len(data_sample)),
                source="auto",
                confidence=1.0,
            )
        )

        # 分析字段类型
        field_types = {}
        for field in fields:
            values = [r.get(field) for r in data_sample if r.get(field) is not None]
            if values:
                sample_type = type(values[0]).__name__
                field_types[field] = sample_type

        metadata_list.append(
            AssetMetadata(
                asset_id=asset_id,
                key="field_types",
                value=str(field_types),
                source="auto",
                confidence=0.8,
            )
        )

        # 批量添加元数据
        for meta in metadata_list:
            self.db.add(meta)

        self.db.commit()

        return metadata_list

    def create_lineage(
        self,
        source_asset_id: int,
        target_asset_id: int,
        relationship: str = "derives",
        transform_logic: Optional[str] = None,
    ) -> DataLineage:
        """创建血缘关系"""
        lineage = DataLineage(
            source_id=source_asset_id,
            target_id=target_asset_id,
            relationship=relationship,
            transform_logic=transform_logic,
        )
        self.db.add(lineage)
        self.db.commit()
        self.db.refresh(lineage)
        return lineage

    def get_asset_lineage_graph(self, asset_id: int, depth: int = 3) -> Dict[str, Any]:
        """获取资产血缘图谱"""
        asset = self.db.query(DataAsset).filter(DataAsset.id == asset_id).first()
        if not asset:
            return {}

        def get_upstream(aid: int, current_depth: int, visited: set) -> List[Dict]:
            if current_depth > depth or aid in visited:
                return []
            visited.add(aid)

            lineages = (
                self.db.query(DataLineage).filter(DataLineage.target_id == aid).all()
            )

            result = []
            for lineage in lineages:
                source = (
                    self.db.query(DataAsset)
                    .filter(DataAsset.id == lineage.source_id)
                    .first()
                )
                if source:
                    result.append(
                        {
                            "id": source.id,
                            "name": source.name,
                            "type": source.type,
                            "relationship": lineage.relationship,
                            "children": get_upstream(
                                source.id, current_depth + 1, visited.copy()
                            ),
                        }
                    )

            return result

        def get_downstream(aid: int, current_depth: int, visited: set) -> List[Dict]:
            if current_depth > depth or aid in visited:
                return []
            visited.add(aid)

            lineages = (
                self.db.query(DataLineage).filter(DataLineage.source_id == aid).all()
            )

            result = []
            for lineage in lineages:
                target = (
                    self.db.query(DataAsset)
                    .filter(DataAsset.id == lineage.target_id)
                    .first()
                )
                if target:
                    result.append(
                        {
                            "id": target.id,
                            "name": target.name,
                            "type": target.type,
                            "relationship": lineage.relationship,
                            "children": get_downstream(
                                target.id, current_depth + 1, visited.copy()
                            ),
                        }
                    )

            return result

        return {
            "node": {"id": asset.id, "name": asset.name, "type": asset.type},
            "upstream": get_upstream(asset_id, 1, set()),
            "downstream": get_downstream(asset_id, 1, set()),
        }


def init_catalog_database(db: Session):
    """初始化数据目录数据库表"""
    # 创建表
    from database.config import Base, engine

    # 确保所有模型已注册
    from models.data_catalog import (
        AssetMetadata,
        DataAsset,
        DataCatalogStats,
        DataClassification,
        DataLineage,
        DataTag,
        asset_classifications,
        asset_tags,
    )

    Base.metadata.create_all(bind=engine)

    # 初始化默认数据
    service = DataCatalogService(db)
    service.init_default_classifications()
    service.init_default_tags()

    return {"message": "数据目录数据库初始化完成"}
