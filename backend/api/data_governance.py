"""
数据治理 API
"""
import json
from datetime import datetime
from typing import Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

router = APIRouter(prefix="/data-governance", tags=["数据治理"])


# 获取虚拟数据统计
def get_virtual_data_summary(db):
    """从数据库获取虚拟数据统计"""
    summary = {}
    try:
        # 查询各个虚拟数据表的记录数
        tables = [
            ("photovoltaic_systems", "光伏系统"),
            ("equipment", "设备"),
            ("storage_systems", "储能系统"),
            ("energy_consumption", "能耗"),
            ("carbon_emissions", "碳排放"),
        ]
        for table_name, display_name in tables:
            try:
                result = db.execute(text(f"SELECT COUNT(*) as count FROM {table_name}"))
                count = result.scalar() or 0
                if count > 0:
                    summary[table_name] = {"count": count, "name": display_name}
            except:
                pass
    except Exception as e:
        print(f"获取虚拟数据统计失败: {e}")
    return summary


@router.get("/stats")
def get_governance_stats(db=Depends(get_db)):
    """获取数据治理统计"""
    # 从血缘系统获取节点数
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM lineage_nodes WHERE is_active = 1")
        )
        lineage_count = result.scalar() or 0
    except:
        lineage_count = 0

    # 获取虚拟数据统计
    virtual_summary = get_virtual_data_summary(db)

    # 表数量 = 血缘节点 + 虚拟数据表
    tables_count = lineage_count + len(virtual_summary)

    return {
        "tables": tables_count,
        "lineages": lineage_count,
        "metadata": lineage_count * 3,  # 假设每个表有3个元数据
        "standards": 5,
        "virtual_data": virtual_summary,
    }


@router.get("/tables")
def get_governance_tables(db=Depends(get_db)):
    """获取数据治理中的表列表"""
    items = []

    # 从血缘系统获取表节点
    try:
        result = db.execute(
            text(
                """
            SELECT id, name, display_name, node_type, system, database,
                   schema_name, full_path, description, extra_metadata,
                   tags, owner, quality_score
            FROM lineage_nodes
            WHERE is_active = 1 AND node_type = 'table'
        """
            )
        )
        rows = result.fetchall()
        for row in rows:
            items.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "display_name": row[2],
                    "node_type": row[3],
                    "system": row[4],
                    "database": row[5],
                    "schema_name": row[6],
                    "full_path": row[7],
                    "description": row[8],
                    "metadata": row[9] or {},
                    "tags": row[10] or [],
                    "owner": row[11],
                    "quality_score": row[12] or 0,
                    "records": row[9].get("row_count", 0) if row[9] else 0,
                    "size": row[9].get("size", 0) if row[9] else 0,
                    "status": "active",
                }
            )
    except Exception as e:
        print(f"获取血缘表失败: {e}")

    # 添加虚拟数据表
    virtual_summary = get_virtual_data_summary(db)
    for table_name, info in virtual_summary.items():
        # 检查是否已存在
        if not any(t["name"] == table_name for t in items):
            items.append(
                {
                    "id": f"vd-{table_name}",
                    "name": table_name,
                    "display_name": info["name"],
                    "node_type": "table",
                    "system": "PostgreSQL",
                    "database": "open_digital_foundation",
                    "schema_name": "public",
                    "full_path": f"PostgreSQL.open_digital_foundation.public.{table_name}",
                    "description": f"虚拟数据 - {info['name']}",
                    "metadata": {"row_count": info["count"]},
                    "tags": ["虚拟数据", "自动生成"],
                    "owner": "system",
                    "quality_score": 85,
                    "records": info["count"],
                    "size": info["count"] * 100,  # 估算大小
                    "status": "active",
                }
            )

    return {"items": items, "total": len(items)}


@router.get("/standards")
def get_standards(db=Depends(get_db)):
    """获取标准规范列表"""
    # 从数据标准表获取
    items = []
    try:
        result = db.execute(
            text(
                "SELECT id, name, description, category, status FROM data_standards WHERE is_active = 1"
            )
        )
        rows = result.fetchall()
        for row in rows:
            items.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "category": row[3],
                    "status": row[4],
                }
            )
    except:
        # 如果表不存在，返回默认标准
        items = [
            {
                "id": 1,
                "name": "光伏数据标准",
                "description": "光伏发电数据格式规范",
                "category": "能源",
                "status": "active",
            },
            {
                "id": 2,
                "name": "设备数据标准",
                "description": "设备运行数据格式规范",
                "category": "设备",
                "status": "active",
            },
            {
                "id": 3,
                "name": "能耗数据标准",
                "description": "能耗数据格式规范",
                "category": "能源",
                "status": "active",
            },
            {
                "id": 4,
                "name": "碳排放标准",
                "description": "碳排放核算数据规范",
                "category": "环保",
                "status": "active",
            },
            {
                "id": 5,
                "name": "储能数据标准",
                "description": "储能系统数据格式规范",
                "category": "能源",
                "status": "active",
            },
        ]
    return {"items": items}


@router.get("/quality-rules")
def get_quality_rules(db=Depends(get_db)):
    """获取质量规则"""
    return {
        "items": [
            {
                "id": 1,
                "name": "完整性检查",
                "rule": "NOT NULL",
                "status": "active",
                "pass_rate": 95,
                "last_run": "10:30",
            },
            {
                "id": 2,
                "name": "唯一性检查",
                "rule": "UNIQUE",
                "status": "active",
                "pass_rate": 98,
                "last_run": "10:30",
            },
            {
                "id": 3,
                "name": "格式检查",
                "rule": "VALUE_RANGE",
                "status": "active",
                "pass_rate": 92,
                "last_run": "10:30",
            },
            {
                "id": 4,
                "name": "范围检查",
                "rule": "RANGE",
                "status": "active",
                "pass_rate": 88,
                "last_run": "10:30",
            },
        ]
    }


@router.post("/scan")
def scan_data_sources(db=Depends(get_db)):
    """扫描数据源"""
    # 扫描虚拟数据表
    virtual_summary = get_virtual_data_summary(db)
    return {
        "message": "扫描完成",
        "tables_found": len(virtual_summary),
        "details": virtual_summary,
    }


@router.get("/tables/{table_id}")
def get_table_detail(table_id: str, db=Depends(get_db)):
    """获取表详情"""
    # 先从血缘系统查找
    try:
        result = db.execute(
            text("SELECT * FROM lineage_nodes WHERE id = :id"), {"id": table_id}
        )
        row = result.fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "node_type": row[3],
                "system": row[4],
                "database": row[5],
                "schema_name": row[6],
                "full_path": row[7],
                "description": row[8],
            }
    except:
        pass

    # 从虚拟数据表查找
    virtual_summary = get_virtual_data_summary(db)
    for table_name, info in virtual_summary.items():
        if f"vd-{table_name}" == table_id:
            return {
                "id": table_id,
                "name": table_name,
                "display_name": info["name"],
                "records": info["count"],
                "status": "active",
            }

    raise HTTPException(status_code=404, detail="表不存在")


# 以下是原有API
@router.post("/check")
def check_data_quality(req: dict, db=Depends(get_db)):
    """数据质量检查 — 基于真实 quality_check_results 表聚合,无随机生成"""
    dataset_id = req.get("dataset_id")
    results = None
    data_source = None

    # 1) 优先: 聚合真实质量检查记录
    try:
        rows = db.execute(
            text(
                """
                SELECT total_count, passed_count, failed_count, score, details
                FROM quality_check_results
                WHERE (:ds_id IS NULL OR CAST(dataset_id AS TEXT) = :ds_id)
                ORDER BY checked_at DESC NULLS LAST
                LIMIT 100
                """
            ),
            {"ds_id": str(dataset_id) if dataset_id is not None else None},
        ).fetchall()

        if rows:
            total = sum((r[0] or 0) for r in rows)
            passed = sum((r[1] or 0) for r in rows)
            failed = sum((r[2] or 0) for r in rows)
            # score 列存的是该条检查的得分(0-100),取平均
            scored = [r[3] for r in rows if r[3] is not None and (r[0] or 0) > 0]
            avg_score_pct = (sum(scored) / len(scored)) if scored else None

            if total > 0:
                pass_rate = passed / total  # 真实通过率
                fail_rate = failed / total
                # 完整性 = 非失败记录占比; 一致性/准确率 由真实 score 列映射
                consistency = avg_score_pct / 100.0 if avg_score_pct is not None else pass_rate
                results = {
                    "completeness": round(pass_rate, 4),
                    "consistency": round(consistency, 4),
                    "accuracy": round(consistency, 4),
                    "timeliness": round(pass_rate, 4),
                    "overall_score": round(pass_rate, 4),
                }
                data_source = {
                    "type": "real",
                    "table": "quality_check_results",
                    "records_aggregated": len(rows),
                    "total_checked": total,
                    "passed": passed,
                    "failed": failed,
                    "avg_rule_score_pct": round(avg_score_pct, 2) if avg_score_pct is not None else None,
                }
    except Exception as e:
        print(f"查询 quality_check_results 失败: {e}")

    # 2) 兜底: 无真实记录时,诚实标注演示态(不编造假分数)
    if results is None:
        results = {
            "completeness": None,
            "consistency": None,
            "accuracy": None,
            "timeliness": None,
            "overall_score": None,
        }
        data_source = {
            "type": "demo",
            "note": "无对应数据集的质量检查记录,未执行真实检查。请先配置并运行质量规则。",
        }

    return {
        "dataset_id": dataset_id,
        "results": results,
        "data_source": data_source,
        "checked_at": datetime.now().isoformat(),
    }


@router.post("/rules")
def create_quality_rule(req: dict, db=Depends(get_db)):
    """创建质量规则"""
    return {"id": 4, "name": req.get("name"), "status": "active"}


@router.get("/history")
def get_quality_history(db=Depends(get_db)):
    """质量检查历史"""
    return {"items": []}
