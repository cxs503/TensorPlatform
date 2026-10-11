"""
数据同步服务 - 将虚拟数据同步到数据管理模块
修复：使用ASCII安全的标签避免编码问题
"""
import json
from datetime import datetime

from sqlalchemy import text


class DataSyncService:
    """数据同步服务"""

    DATA_TYPE_CONFIG = {
        "pv_systems": {
            "table": "photovoltaic_systems",
            "display_name": "PV Systems",
            "description": "Photovoltaic system data",
            "tags": ["pv", "realtime", "virtual"],
            "classification": "business",
        },
        "pv_power": {
            "table": "photovoltaic_systems",
            "display_name": "PV Power Data",
            "description": "PV power time series data",
            "tags": ["pv", "power", "virtual"],
            "classification": "business",
        },
        "equipment": {
            "table": "equipment",
            "display_name": "Equipment Data",
            "description": "Equipment basic information",
            "tags": ["equipment", "ops", "virtual"],
            "classification": "ops",
        },
        "storage_systems": {
            "table": "storage_systems",
            "display_name": "Storage Systems",
            "description": "Energy storage system data",
            "tags": ["storage", "realtime", "virtual"],
            "classification": "business",
        },
        "energy_consumption": {
            "table": "energy_consumption",
            "display_name": "Energy Consumption",
            "description": "Energy consumption data",
            "tags": ["energy", "history", "virtual"],
            "classification": "business",
        },
        "carbon_emission": {
            "table": "carbon_emissions",
            "display_name": "Carbon Emissions",
            "description": "Carbon emission data",
            "tags": ["carbon", "eco", "virtual"],
            "classification": "business",
        },
    }

    @staticmethod
    def sync_to_datasets(db, data_type: str) -> dict:
        config = DataSyncService.DATA_TYPE_CONFIG.get(data_type)
        if not config:
            return {"success": False, "error": f"Unknown data type: {data_type}"}

        table_name = config["table"]

        try:
            result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.scalar() or 0

            if count == 0:
                return {"success": False, "error": "No data in table"}

            existing = db.execute(
                text("SELECT id FROM datasets WHERE name = :name"),
                {"name": config["display_name"]},
            ).fetchone()

            if existing:
                db.execute(
                    text(
                        """
                    UPDATE datasets
                    SET status = 'ready',
                        file_size = :count
                    WHERE id = :id
                """
                    ),
                    {"count": count, "id": existing[0]},
                )
            else:
                db.execute(
                    text(
                        """
                    INSERT INTO datasets (name, data_type, status, file_path, file_size)
                    VALUES (:name, :data_type, 'ready', :path, :size)
                """
                    ),
                    {
                        "name": config["display_name"],
                        "data_type": "virtual_data",
                        "path": f"table://{table_name}",
                        "size": count,
                    },
                )

            db.commit()
            return {"success": True, "count": count, "table": table_name}

        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_to_lineage(db, data_type: str) -> dict:
        config = DataSyncService.DATA_TYPE_CONFIG.get(data_type)
        if not config:
            return {"success": False, "error": f"Unknown data type: {data_type}"}

        table_name = config["table"]
        node_id = f"ln-vd-{data_type}"

        try:
            result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.scalar() or 0

            if count == 0:
                return {"success": False, "error": "No data in table"}

            existing = db.execute(
                text("SELECT id FROM lineage_nodes WHERE id = :id"), {"id": node_id}
            ).fetchone()

            metadata_dict = {"row_count": count, "source": "virtual_data_generator"}
            tags_list = config["tags"]

            if existing:
                db.execute(
                    text(
                        """
                    UPDATE lineage_nodes
                    SET extra_metadata = :metadata,
                        tags = :tags
                    WHERE id = :id
                """
                    ),
                    {
                        "id": node_id,
                        "metadata": json.dumps(metadata_dict),
                        "tags": json.dumps(tags_list),
                    },
                )
            else:
                db.execute(
                    text(
                        """
                    INSERT INTO lineage_nodes
                    (id, name, display_name, node_type, system, database, schema_name,
                     full_path, description, extra_metadata, tags, owner, quality_score, is_active)
                    VALUES
                    (:id, :name, :display_name, 'table', 'PostgreSQL', 'open_digital_foundation', 'public',
                     :full_path, :description, :metadata, :tags, 'system', 85, 1)
                """
                    ),
                    {
                        "id": node_id,
                        "name": table_name,
                        "display_name": config["display_name"],
                        "full_path": f"PostgreSQL.open_digital_foundation.public.{table_name}",
                        "description": config["description"],
                        "metadata": json.dumps(metadata_dict),
                        "tags": json.dumps(tags_list),
                    },
                )

            db.commit()
            return {"success": True, "count": count, "node_id": node_id}

        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_to_catalog(db, data_type: str) -> dict:
        config = DataSyncService.DATA_TYPE_CONFIG.get(data_type)
        if not config:
            return {"success": False, "error": f"Unknown data type: {data_type}"}

        table_name = config["table"]

        try:
            result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.scalar() or 0

            if count == 0:
                return {"success": False, "error": "No data in table"}

            existing = db.execute(
                text("SELECT id FROM data_assets WHERE name = :name"),
                {"name": config["display_name"]},
            ).fetchone()

            if existing:
                db.execute(
                    text(
                        """
                    UPDATE data_assets
                    SET quality_score = 85
                    WHERE id = :id
                """
                    ),
                    {"id": existing[0]},
                )
                asset_id = existing[0]
            else:
                result = db.execute(
                    text(
                        """
                    INSERT INTO data_assets
                    (name, type, description, owner, source, quality_score, sensitivity_level, status)
                    VALUES
                    (:name, 'table', :description, 'system', :source, 85, 'internal', 'active')
                    RETURNING id
                """
                    ),
                    {
                        "name": config["display_name"],
                        "description": config["description"],
                        "source": f"virtual_data://{table_name}",
                    },
                )
                asset_id = result.fetchone()[0]

            db.commit()
            return {"success": True, "count": count, "asset_id": asset_id}

        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_all(db) -> dict:
        results = {}

        for data_type in DataSyncService.DATA_TYPE_CONFIG.keys():
            results[data_type] = {
                "datasets": DataSyncService.sync_to_datasets(db, data_type),
                "lineage": DataSyncService.sync_to_lineage(db, data_type),
                "catalog": DataSyncService.sync_to_catalog(db, data_type),
            }

        return results
