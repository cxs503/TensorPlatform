"""
虚拟数据生成 API
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from database.config import get_db
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from models.lineage import LineageNode
from services.model_training_service import get_model_training_service
from services.virtual_data_service import get_data_generator
from services.industrial_simulation_service import get_industrial_simulation_service
from services.read_only_ingestion_sandbox import ReadOnlyIngestionSandbox
from sqlalchemy import text

router = APIRouter(prefix="/virtual-data", tags=["虚拟数据生成"])
_industrial_simulator = get_industrial_simulation_service()
_read_only_sandbox = ReadOnlyIngestionSandbox(_industrial_simulator)

# 内存存储生成的虚拟数据 (生产环境应写入数据库)
_virtu_data_storage = {}

# ── 钢铁虚拟数据别名:前端 VirtualData.vue 按 /virtual-data/steel-* 调用,
#    但钢铁逻辑实际在 api.steel_vdata(前缀 /steel-vdata)。这里做一层委托,免去改前端。──
from api import steel_vdata as _steel


class IndustrialScenarioRequest(BaseModel):
    hours: int = Field(default=168, ge=24, le=8784)
    seed: int = Field(default=20260720, ge=0, le=2_147_483_647)
    anomaly_rate: float = Field(default=0.03, ge=0, le=0.2)
    configuration: dict[str, float] = Field(default_factory=dict)


class StrategyExperimentRequest(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    name: str = Field(default="策略回放对比", max_length=120)
    review_note: str = Field(default="", max_length=1000)


class RegressionSuiteRequest(BaseModel):
    scenario_key: str
    seeds: list[int] = Field(default_factory=lambda: [20260720, 20260721, 20260722], min_length=1, max_length=12)
    hours: int = Field(default=168, ge=24, le=8784)
    configuration: dict[str, float] = Field(default_factory=dict)


@router.get("/industrial-scenarios")
def industrial_scenarios():
    """可复现的工业虚拟工厂场景及其输出契约。"""
    return {"items": _industrial_simulator.list_scenarios()}


@router.get("/analysis-profiles")
def industrial_analysis_profiles():
    """离线分析/策略版本清单，不等同于可部署工业控制模型。"""
    return {"items": _industrial_simulator.list_analysis_profiles()}


@router.get("/industrial-scenarios/{scenario_key}/configuration")
def industrial_scenario_configuration(scenario_key: str):
    try:
        return _industrial_simulator.scenario_config(scenario_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industrial-run-comparison")
def compare_industrial_runs(baseline_run_id: str, candidate_run_id: str):
    try:
        comparison = _industrial_simulator.compare_runs(baseline_run_id, candidate_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not comparison:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return comparison


@router.post("/strategy-experiments")
def create_strategy_experiment(request: StrategyExperimentRequest):
    try:
        experiment = _industrial_simulator.create_strategy_experiment(
            request.baseline_run_id, request.candidate_run_id, request.name, request.review_note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not experiment:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return experiment


@router.get("/strategy-experiments")
def list_strategy_experiments(limit: int = 50):
    return {"items": _industrial_simulator.list_strategy_experiments(max(1, min(limit, 100)))}


@router.post("/strategy-regression-suites")
def run_strategy_regression_suite(request: RegressionSuiteRequest):
    try:
        return _industrial_simulator.run_regression_suite(
            request.scenario_key, request.seeds, request.hours, request.configuration
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industrial-runs/{run_id}/benchmark-gate")
def industrial_benchmark_gate(run_id: str):
    """固定虚拟回放的最小质量/契约/人工评审门槛。"""
    report = _read_only_sandbox.benchmark_gate(run_id)
    if not report:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return report


@router.post("/industrial-sandbox/csv-validation")
async def validate_industrial_csv_sandbox(
    scenario_key: str = Form(...), file: UploadFile = File(...),
    field_mapping_json: str = Form("{}"), source_units_json: str = Form("{}"),
):
    """只读 CSV 沙箱：校验映射、单位、量程和小时级时间对齐。

    上传内容只在请求内存中解析，不保存文件、不创建数据源连接。
    """
    try:
        mapping, units = json.loads(field_mapping_json), json.loads(source_units_json)
        if not isinstance(mapping, dict) or not isinstance(units, dict):
            raise ValueError("字段映射和单位映射必须为 JSON 对象")
        content = await file.read(ReadOnlyIngestionSandbox.max_bytes + 1)
        return _read_only_sandbox.validate_csv(scenario_key, content, mapping, units)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industrial-scenarios/{scenario_key}/contract")
def industrial_scenario_contract(scenario_key: str):
    try:
        return _industrial_simulator.scenario_contract(scenario_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/industrial-scenarios/{scenario_key}/generate")
def generate_industrial_scenario(
    scenario_key: str, request: IndustrialScenarioRequest
):
    """生成物理约束时序、真值标签与建议-审批-执行回执。

    仿真只用于离线验证，接口不产生任何工业控制指令。
    """
    try:
        return _industrial_simulator.generate(
            scenario_key, request.hours, request.seed, request.anomaly_rate, request.configuration
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industrial-runs/{run_id}")
def get_industrial_run(run_id: str):
    run = _industrial_simulator.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="仿真运行不存在；服务重启后请重新生成")
    return run


@router.get("/industrial-runs/{run_id}/evaluation")
def evaluate_industrial_run(run_id: str):
    evaluation = _industrial_simulator.evaluate_run(run_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return evaluation


@router.get("/industrial-runs/{run_id}/contract-validation")
def validate_industrial_run_contract(run_id: str):
    validation = _industrial_simulator.validate_contract_run(run_id)
    if not validation:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return validation


@router.get("/industrial-runs/{run_id}/analysis")
def analyze_industrial_run(run_id: str):
    analysis = _industrial_simulator.analyze_run(run_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return analysis


@router.get("/industrial-runs/{run_id}/admission")
def industrial_run_admission(run_id: str):
    report = _industrial_simulator.admission_report(run_id)
    if not report:
        raise HTTPException(status_code=404, detail="仿真运行或其产物不存在")
    return report


@router.get("/industrial-runs")
def list_industrial_runs(limit: int = 50):
    """已持久化的数字孪生回放运行，服务重启后仍可查询。"""
    return {"items": _industrial_simulator.list_runs(max(1, min(limit, 100)))}


@router.get("/industrial-runs/{run_id}/download/{artifact}")
def download_industrial_artifact(run_id: str, artifact: str):
    from fastapi.responses import FileResponse

    run = _industrial_simulator.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="仿真运行不存在")
    path = run["timeseries_path"] if artifact == "timeseries" else run["events_path"] if artifact == "events" else None
    if not path or not __import__("os").path.exists(path):
        raise HTTPException(status_code=404, detail="仿真文件不存在")
    return FileResponse(path, filename=f"{run['scenario_key']}_{artifact}.csv", media_type="text/csv")


@router.get("/steel-scenarios")
def alias_steel_scenarios():
    """前端 GET /virtual-data/steel-scenarios → 委托 steel_vdata.scenarios"""
    return _steel.scenarios()


@router.post("/steel-generate/{key}")
def alias_steel_generate(key: str):
    """前端 POST /virtual-data/steel-generate/{key} → 委托 steel_vdata.generate"""
    return _steel.generate(key)


@router.post("/steel-validate/{key}")
def alias_steel_validate(key: str):
    """前端 POST /virtual-data/steel-validate/{key} → 委托 steel_vdata.validate"""
    return _steel.validate(key)


@router.get("/download/{data_type}")
def download_virtual_data(data_type: str, db=Depends(get_db)):
    """导出已生成的虚拟数据为 CSV 下载(前端 VirtualData 能源/钢铁卡片用)"""
    import io
    from fastapi.responses import StreamingResponse
    # 取数(与 get_stored_data 一致)
    if data_type.startswith("steel_"):
        records = _steel._store.get(data_type, [])
    else:
        records = _virtu_data_storage.get(data_type, [])
    # 内存没有则回退数据库表(能源场景数据落库)
    if not records:
        table_map = {
            "pv_power": "photovoltaic_systems", "pv_power_prediction": "photovoltaic_systems", "pv_systems": "photovoltaic_systems",
            "fault_prediction": "equipment", "equipment": "equipment",
            "storage_operation": "storage_systems", "storage_systems": "storage_systems",
            "energy_consumption": "energy_consumption", "load_forecasting": "energy_consumption", "dust_optimization": "energy_consumption",
            "carbon_emission": "carbon_emissions",
        }
        tbl = table_map.get(data_type)
        if tbl:
            try:
                rows = db.execute(text(f'SELECT * FROM "{tbl}" LIMIT 1000')).fetchall()
                if rows:
                    cols = list(rows[0]._mapping.keys())
                    records = [{c: _row._mapping[c] for c in cols} for _row in rows]
            except Exception:
                records = []
    if not records:
        raise HTTPException(404, f"暂无 {data_type} 数据,请先生成后再下载")
    try:
        import pandas as pd
        buf = io.StringIO()
        pd.DataFrame(records).to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode("utf-8-sig")  # 带 BOM,Excel 直开不乱码
    except Exception:
        import csv
        buf = io.StringIO()
        if records and isinstance(records[0], dict):
            w = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
            w.writeheader(); w.writerows(records)
        csv_bytes = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{data_type}.csv"'},
    )

# 数据类型到表名和显示名称的映射
DATA_TYPE_CONFIG = {
    "pv_power": {
        "table": "photovoltaic_systems",
        "display_name": "光伏功率数据",
        "tags": ["光伏", "功率", "虚拟数据"],
    },
    "pv_power_prediction": {
        "table": "photovoltaic_systems",
        "display_name": "光伏功率预测数据",
        "tags": ["光伏", "预测", "虚拟数据"],
    },
    "pv_systems": {
        "table": "photovoltaic_systems",
        "display_name": "光伏系统数据",
        "tags": ["光伏", "系统", "虚拟数据"],
    },
    "fault_prediction": {
        "table": "equipment",
        "display_name": "故障预测数据",
        "tags": ["故障", "预测", "虚拟数据"],
    },
    "equipment": {
        "table": "equipment",
        "display_name": "设备数据",
        "tags": ["设备", "运维", "虚拟数据"],
    },
    "storage_operation": {
        "table": "storage_systems",
        "display_name": "储能运行数据",
        "tags": ["储能", "运行", "虚拟数据"],
    },
    "storage_systems": {
        "table": "storage_systems",
        "display_name": "储能系统数据",
        "tags": ["储能", "系统", "虚拟数据"],
    },
    "energy_consumption": {
        "table": "energy_consumption",
        "display_name": "能耗数据",
        "tags": ["能耗", "虚拟数据"],
    },
    "load_forecasting": {
        "table": "energy_consumption",
        "display_name": "负荷预测数据",
        "tags": ["负荷", "预测", "虚拟数据"],
    },
    "dust_optimization": {
        "table": "energy_consumption",
        "display_name": "除尘优化数据",
        "tags": ["除尘", "优化", "虚拟数据"],
    },
    "carbon_emission": {
        "table": "carbon_emissions",
        "display_name": "碳排放数据",
        "tags": ["碳排放", "环保", "虚拟数据"],
    },
}


# ==================== 数据生成 ====================


@router.post("/generate/{data_type}")
def generate_data(data_type: str, count: int = 100, db=Depends(get_db)):
    """生成虚拟数据并写入数据库"""
    generator = get_data_generator()

    # 前端友好名称映射
    type_mapping = {
        "fault_prediction": "equipment",
        "dust_optimization": "dust_systems",
        "pv_systems": "pv_systems",
        "pv_power": "pv_power",
        "storage_systems": "storage_systems",
        "storage_operation": "storage_operation",
        "sensor": "sensor",
    }
    data_type = type_mapping.get(data_type, data_type)

    generators = {
        "pv_systems": lambda: generator.generate_pv_system_data(count),
        "pv_power": lambda: generator.generate_pv_power_data("pv_default", count // 24),
        "storage_systems": lambda: generator.generate_storage_system_data(count),
        "storage_operation": lambda: generator.generate_storage_operation_data(
            "storage_default", count // 24
        ),
        "equipment": lambda: generator.generate_equipment_data(count),
        "sensor": lambda: generator.generate_sensor_data("eq_default", count),
        "energy_consumption": lambda: generator.generate_energy_consumption_data(
            count // 90, 90
        ),
        "carbon_emission": lambda: generator.generate_carbon_emission_data(
            count // 90, 90
        ),
        "load_forecasting": lambda: generator.generate_load_forecast_data(
            "main_grid", count // 24
        ),
        "dust_systems": lambda: generator.generate_dust_system_data(count),
        "dust_operation": lambda: generator.generate_dust_operation_data(
            "dust_default", count // 24
        ),
    }

    if data_type not in generators:
        raise HTTPException(status_code=400, detail=f"未知的数据类型: {data_type}")

    data = generators[data_type]()

    # 写入数据库
    saved_count = save_to_database(db, data_type, data)

    # 同时存储在内存中供API调用
    _virtu_data_storage[data_type] = data

    return {
        "data_type": data_type,
        "count": len(data),
        "saved_to_db": saved_count,
        "generated_at": datetime.now().isoformat(),
        "data": data[:10],  # 只返回前10条作为预览
    }


def save_to_database(db, data_type: str, data: list) -> int:
    """将虚拟数据写入数据库"""
    saved = 0
    try:
        # 创建表（如果不存在）
        db.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS photovoltaic_systems (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255),
                capacity_kwp DECIMAL(10,2),
                location VARCHAR(255),
                status VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )
        db.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS equipment (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255),
                type VARCHAR(50),
                location VARCHAR(255),
                status VARCHAR(50),
                health_score DECIMAL(5,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )
        db.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS storage_systems (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255),
                capacity_kwh DECIMAL(10,2),
                power_kw DECIMAL(10,2),
                status VARCHAR(50),
                soc DECIMAL(5,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )
        db.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS energy_consumption (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(255),
                entity_type VARCHAR(50),
                energy_type VARCHAR(50),
                consumption DECIMAL(10,2),
                unit VARCHAR(50),
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )
        db.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS carbon_emissions (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(255),
                scope VARCHAR(50),
                source VARCHAR(255),
                emission_tco2 DECIMAL(10,4),
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )
        db.commit()

        if (
            data_type == "pv_systems"
            or data_type == "pv_power"
            or data_type == "pv_power_prediction"
        ):
            # 写入光伏系统表或功率数据
            for item in data[:50]:  # 限制写入数量
                try:
                    if data_type == "pv_systems":
                        db.execute(
                            text(
                                """
                            INSERT INTO photovoltaic_systems (name, capacity_kwp, location, status)
                            VALUES (:name, :capacity, :location, :status)
                        """
                            ),
                            {
                                "name": item.get("name", "光伏电站"),
                                "capacity": item.get("capacity_kwp", 100),
                                "location": item.get("location", "北京"),
                                "status": item.get("status", "running"),
                            },
                        )
                    saved += 1
                except Exception as e:
                    print(f"保存pv_systems失败: {e}")
            db.commit()

        elif data_type == "equipment" or data_type == "fault_prediction":
            # 写入设备表
            for item in data[:50]:
                try:
                    db.execute(
                        text(
                            """
                        INSERT INTO equipment (name, type, location, status, health_score)
                        VALUES (:name, :type, :location, :status, :health)
                    """
                        ),
                        {
                            "name": item.get("name", "设备"),
                            "type": item.get("type", "inverter"),
                            "location": item.get("location", "车间1"),
                            "status": item.get("status", "running"),
                            "health": item.get("health_score", 90),
                        },
                    )
                    saved += 1
                except Exception as e:
                    print(f"保存equipment失败: {e}")
            db.commit()

        elif data_type == "storage_systems" or data_type == "storage_operation":
            # 写入储能系统表
            for item in data[:50]:
                try:
                    db.execute(
                        text(
                            """
                        INSERT INTO storage_systems (name, capacity_kwh, power_kw, status, soc)
                        VALUES (:name, :capacity, :power, :status, :soc)
                    """
                        ),
                        {
                            "name": item.get("name", "储能系统"),
                            "capacity": item.get("capacity_kwh", 500),
                            "power": item.get("power_kw", 200),
                            "status": item.get("status", "running"),
                            "soc": item.get("soc", 50),
                        },
                    )
                    saved += 1
                except Exception as e:
                    print(f"保存storage_systems失败: {e}")
            db.commit()

        elif (
            data_type == "energy_consumption"
            or data_type == "load_forecasting"
            or data_type == "dust_optimization"
        ):
            # 写入能耗数据表
            for item in data[:100]:
                try:
                    db.execute(
                        text(
                            """
                        INSERT INTO energy_consumption (entity_id, entity_type, energy_type, consumption, unit, date)
                        VALUES (:entity_id, :entity_type, :energy_type, :consumption, :unit, :date)
                    """
                        ),
                        {
                            "entity_id": item.get("entity_id", "entity_1"),
                            "entity_type": item.get("entity_type", "workshop"),
                            "energy_type": item.get("energy_type", "electricity"),
                            "consumption": item.get("consumption", 0),
                            "unit": item.get("unit", "kWh"),
                            "date": item.get(
                                "date", datetime.now().strftime("%Y-%m-%d")
                            ),
                        },
                    )
                    saved += 1
                except Exception as e:
                    print(f"保存energy_consumption失败: {e}")
            db.commit()

        elif data_type == "carbon_emission":
            # 写入碳排放表
            for item in data[:100]:
                try:
                    db.execute(
                        text(
                            """
                        INSERT INTO carbon_emissions (entity_id, scope, source, emission_tco2, date)
                        VALUES (:entity_id, :scope, :source, :emission, :date)
                    """
                        ),
                        {
                            "entity_id": item.get("entity_id", "entity_1"),
                            "scope": item.get("scope", "scope1"),
                            "source": item.get("source", "电力消耗"),
                            "emission": item.get("emission_tco2", 0),
                            "date": item.get(
                                "date", datetime.now().strftime("%Y-%m-%d")
                            ),
                        },
                    )
                    saved += 1
                except Exception as e:
                    print(f"保存carbon_emission失败: {e}")
            db.commit()

    except Exception as e:
        print(f"保存虚拟数据失败: {e}")

    # 注册到数据治理系统（LineageNode）
    try:
        config = DATA_TYPE_CONFIG.get(data_type, {})
        table_name = config.get("table", data_type)

        # 检查是否已存在
        existing = (
            db.query(LineageNode)
            .filter(LineageNode.name == table_name, LineageNode.system == "PostgreSQL")
            .first()
        )

        if not existing:
            # 创建新的血缘节点
            node = LineageNode(
                id=str(uuid.uuid4()),
                name=table_name,
                display_name=config.get("display_name", f"{data_type}数据"),
                node_type="table",
                system="PostgreSQL",
                database="open_digital_foundation",
                schema_name="public",
                full_path=f"PostgreSQL.open_digital_foundation.public.{table_name}",
                description=f"虚拟数据生成 - {config.get('display_name', data_type)}",
                extra_metadata={"row_count": saved, "source": "virtual_data_generator"},
                tags=config.get("tags", ["虚拟数据"]),
                owner="system",
                quality_score=85,
                is_active=1,
                created_by="virtual_data_generator",
            )
            db.add(node)
            db.commit()
        else:
            # 更新已有节点
            existing.extra_metadata = existing.extra_metadata or {}
            existing.extra_metadata["row_count"] = saved
            existing.updated_at = datetime.now()
            db.commit()
    except Exception as e:
        print(f"注册到数据治理系统失败: {e}")
        db.rollback()

    return saved


@router.post("/generate-all")
def generate_all_data(db=Depends(get_db)):
    """生成所有业务场景的虚拟数据并写入数据库"""
    generator = get_data_generator()

    datasets = generator.generate_training_datasets()

    # 写入数据库
    db_summary = {}
    for key, records in datasets.items():
        saved = save_to_database(db, key, records)
        db_summary[key] = {"generated": len(records), "saved": saved}
        _virtu_data_storage[key] = records

    return {
        "message": "所有业务场景虚拟数据生成完成",
        "summary": db_summary,
        "generated_at": datetime.now().isoformat(),
    }


@router.get("/stored/{data_type}")
def get_stored_data(data_type: str, db=Depends(get_db)):
    """获取已存储的虚拟数据"""
    # 钢铁虚拟数据存在 steel_vdata 的内存 _store 里(key 形如 steel_sintering)
    if data_type.startswith("steel_"):
        return _steel._store.get(data_type, [])
    # 先从内存获取
    memory_data = _virtu_data_storage.get(data_type, [])

    # 类型映射：前端type -> 数据库表名
    table_mapping = {
        "pv_power": "photovoltaic_systems",
        "pv_power_prediction": "photovoltaic_systems",
        "pv_systems": "photovoltaic_systems",
        "fault_prediction": "equipment",
        "equipment": "equipment",
        "storage_operation": "storage_systems",
        "storage_systems": "storage_systems",
        "energy_consumption": "energy_consumption",
        "load_forecasting": "energy_consumption",
        "dust_optimization": "energy_consumption",
        "carbon_emission": "carbon_emissions",
    }

    table_name = table_mapping.get(data_type)

    # 如果内存有数据或数据库有数据，都返回
    if table_name:
        try:
            result = db.execute(text(f"SELECT COUNT(*) as count FROM {table_name}"))
            db_count = result.scalar() or 0
            return {"data_type": data_type, "count": db_count, "data": memory_data}
        except Exception as e:
            pass

    return {"data_type": data_type, "count": len(memory_data), "data": memory_data}


@router.get("/stored")
def get_all_stored_data(db=Depends(get_db)):
    """获取所有已存储的虚拟数据（从数据库读取）"""

    # 映射关系：数据类型 -> 数据库表名和字段名
    data_type_mapping = {
        "pv_power_prediction": {
            "table": "photovoltaic_systems",
            "key": "pv_power_prediction",
        },
        "pv_power": {"table": "photovoltaic_systems", "key": "pv_power"},
        "pv_systems": {"table": "photovoltaic_systems", "key": "pv_systems"},
        "fault_prediction": {"table": "equipment", "key": "fault_prediction"},
        "equipment": {"table": "equipment", "key": "equipment"},
        "storage_operation": {"table": "storage_systems", "key": "storage_operation"},
        "storage_systems": {"table": "storage_systems", "key": "storage_systems"},
        "energy_consumption": {
            "table": "energy_consumption",
            "key": "energy_consumption",
        },
        "load_forecasting": {"table": "energy_consumption", "key": "load_forecasting"},
        "dust_optimization": {
            "table": "energy_consumption",
            "key": "dust_optimization",
        },
        "carbon_emission": {"table": "carbon_emissions", "key": "carbon_emission"},
    }

    summary = {}
    data_types = []

    # 从数据库统计每个表的记录数
    for data_type, config in data_type_mapping.items():
        table_name = config["table"]
        try:
            result = db.execute(text(f"SELECT COUNT(*) as count FROM {table_name}"))
            count = result.scalar() or 0
            if count > 0:
                summary[config["key"]] = count
                data_types.append(config["key"])
        except Exception as e:
            print(f"查询表 {table_name} 失败: {e}")

    # 同时合并内存中的数据
    for key, value in _virtu_data_storage.items():
        if len(value) > 0:
            if key not in summary:
                summary[key] = 0
            summary[key] = max(summary[key], len(value))
            if key not in data_types:
                data_types.append(key)

    return {"data_types": data_types, "summary": summary}


@router.get("/data-types")
def get_data_types():
    """获取支持的数据类型"""
    return {
        "data_types": [
            {"id": "pv_systems", "name": "光伏系统", "description": "光伏电站基础信息"},
            {"id": "pv_power", "name": "光伏功率", "description": "光伏发电功率时序数据"},
            {"id": "storage_systems", "name": "储能系统", "description": "储能电站基础信息"},
            {"id": "storage_operation", "name": "储能运行", "description": "储能系统运行数据"},
            {"id": "equipment", "name": "设备", "description": "生产设备基础信息"},
            {"id": "sensor", "name": "传感器", "description": "设备传感器数据"},
            {"id": "energy_consumption", "name": "能耗", "description": "能耗消耗数据"},
            {"id": "carbon_emission", "name": "碳排放", "description": "碳排放核算数据"},
            {"id": "load_forecasting", "name": "负荷预测", "description": "电力负荷时序数据"},
            {"id": "dust_systems", "name": "除尘系统", "description": "除尘设备基础信息"},
            {"id": "dust_operation", "name": "除尘运行", "description": "除尘系统运行数据"},
            {"id": "all", "name": "全部", "description": "生成所有业务场景数据"},
        ]
    }


# ==================== 模型训练 ====================


@router.post("/train/{business_type}")
def train_model(business_type: str):
    """基于生成的数据训练模型"""
    training_service = get_model_training_service()

    # 业务类型映射到训练任务
    task_mapping = {
        "pv_power": {"task_type": "regression", "model_type": "lstm"},
        "fault_prediction": {
            "task_type": "classification",
            "model_type": "random_forest",
        },
        "energy_consumption": {"task_type": "regression", "model_type": "xgboost"},
        "load_forecasting": {"task_type": "time_series", "model_type": "prophet"},
        "dust_optimization": {"task_type": "reinforcement", "model_type": "dqn"},
    }

    if business_type not in task_mapping:
        raise HTTPException(status_code=400, detail=f"未知的业务类型: {business_type}")

    task_config = task_mapping[business_type]

    # 创建训练任务
    task = training_service.create_training_task(
        name=f"{business_type}_训练任务",
        model_type=task_config["model_type"],
        task_type=task_config["task_type"],
        dataset_type=business_type,
        hyperparameters={},
    )

    return {
        "task_id": task.id,
        "business_type": business_type,
        "model_type": task_config["model_type"],
        "task_type": task_config["task_type"],
        "status": "created",
        "created_at": task.created_at,
    }


@router.get("/training-tasks")
def list_training_tasks():
    """获取训练任务列表"""
    training_service = get_model_training_service()
    return training_service.list_tasks()


# ==================== 模型部署 ====================


@router.post("/deploy/{task_id}")
def deploy_model(task_id: str):
    """部署训练好的模型"""
    training_service = get_model_training_service()

    # 获取训练任务
    task = training_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="训练任务不存在")

    # 模拟部署
    deployment = training_service.deploy_model(task_id)

    return {
        "deployment_id": deployment.id,
        "model_id": task.model_id,
        "status": "deployed",
        "endpoint": f"/api/v1/inference/{deployment.id}",
        "deployed_at": deployment.deployed_at,
    }


@router.get("/deployments")
def list_deployments():
    """获取部署列表"""
    training_service = get_model_training_service()
    return training_service.list_deployments()


@router.post("/inference/{deployment_id}")
def predict(deployment_id: str, data: dict):
    """使用部署的模型进行推理"""
    training_service = get_model_training_service()

    result = training_service.predict(deployment_id, data)

    return {
        "deployment_id": deployment_id,
        "prediction": result,
        "timestamp": datetime.now().isoformat(),
    }


# ==================== 数据目录集成 ====================


@router.post("/register-to-catalog/{data_type}")
def register_to_catalog(data_type: str, count: int = 100, db=Depends(get_db)):
    """将虚拟数据注册到数据目录"""
    from services.data_catalog_service import DataCatalogService

    catalog_service = DataCatalogService(db)
    generator = get_data_generator()

    # 数据类型映射
    type_mapping = {
        "pv_systems": {
            "asset_type": "dataset",
            "description": "光伏系统配置数据",
            "classification": "业务数据",
            "tags": ["实时", "已验证"],
        },
        "pv_power": {
            "asset_type": "dataset",
            "description": "光伏发电功率数据",
            "classification": "业务数据",
            "tags": ["实时", "高价值"],
        },
        "storage_systems": {
            "asset_type": "dataset",
            "description": "储能系统配置数据",
            "classification": "业务数据",
            "tags": ["实时"],
        },
        "storage_operation": {
            "asset_type": "dataset",
            "description": "储能系统运行数据",
            "classification": "业务数据",
            "tags": ["实时", "高价值"],
        },
        "equipment": {
            "asset_type": "dataset",
            "description": "设备基础信息数据",
            "classification": "运维数据",
            "tags": ["已验证"],
        },
        "sensor": {
            "asset_type": "stream",
            "description": "传感器实时采集数据",
            "classification": "传感器数据",
            "tags": ["实时", "高价值"],
        },
        "energy_consumption": {
            "asset_type": "dataset",
            "description": "能耗数据",
            "classification": "业务数据",
            "tags": ["历史", "已验证"],
        },
        "carbon_emission": {
            "asset_type": "dataset",
            "description": "碳排放数据",
            "classification": "业务数据",
            "tags": ["历史", "敏感"],
        },
        "load_forecasting": {
            "asset_type": "dataset",
            "description": "负荷预测数据",
            "classification": "业务数据",
            "tags": ["高价值"],
        },
        "dust_systems": {
            "asset_type": "dataset",
            "description": "除尘系统数据",
            "classification": "运维数据",
            "tags": ["实时"],
        },
    }

    # 生成器映射
    generators = {
        "pv_systems": lambda: generator.generate_pv_system_data(count),
        "pv_power": lambda: generator.generate_pv_power_data("pv_default", count // 24),
        "storage_systems": lambda: generator.generate_storage_system_data(count),
        "storage_operation": lambda: generator.generate_storage_operation_data(
            "storage_default", count // 24
        ),
        "equipment": lambda: generator.generate_equipment_data(count),
        "sensor": lambda: generator.generate_sensor_data("eq_default", count),
        "energy_consumption": lambda: generator.generate_energy_consumption_data(
            count // 90, 90
        ),
        "carbon_emission": lambda: generator.generate_carbon_emission_data(
            count // 90, 90
        ),
        "load_forecasting": lambda: generator.generate_load_forecast_data(
            "main_grid", count // 24
        ),
        "dust_systems": lambda: generator.generate_dust_system_data(count),
    }

    if data_type not in generators:
        raise HTTPException(status_code=400, detail=f"未知的数据类型: {data_type}")

    # 生成数据
    data = generators[data_type]()

    # 获取配置
    config = type_mapping.get(data_type, {})

    # 注册到目录
    asset = catalog_service.register_asset_from_virtual_data(
        name=f"虚拟{data_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        data_type=config.get("asset_type", "dataset"),
        description=config.get("description", f"虚拟生成的{data_type}数据"),
        source="virtual_data_generator",
        tags=config.get("tags", []),
        classification=config.get("classification"),
        owner="system",
    )

    # 自动提取元数据
    catalog_service.auto_extract_metadata(asset.id, data[:100])

    return {
        "message": "数据已注册到目录",
        "asset_id": asset.id,
        "asset_name": asset.name,
        "record_count": len(data),
        "sample_data": data[:5],
    }


@router.post("/init-catalog")
def init_catalog(db=Depends(get_db)):
    """初始化数据目录"""
    from services.data_catalog_service import init_catalog_database

    result = init_catalog_database(db)
    return result
