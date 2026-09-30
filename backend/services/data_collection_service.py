"""
实时数据采集服务
提供高频时序数据采集、数据预处理、流式处理
"""
import queue
import threading
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from services.sensor_data import generate as sensor


class CollectionStatus(str, Enum):
    """采集状态"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class DataSourceType(str, Enum):
    """数据源类型"""

    INDUSTRIAL_DEVICE = "industrial_device"
    DATABASE = "database"
    API = "api"
    MQTT = "mqtt"
    KAFKA = "kafka"


class CollectionTask:
    """采集任务"""

    def __init__(
        self,
        name: str,
        source_type: str,
        source_config: Dict,
        collection_interval: int = 1,  # 秒
        batch_size: int = 100,
    ):
        self.id = f"task_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.source_type = source_type
        self.source_config = source_config
        self.collection_interval = collection_interval
        self.batch_size = batch_size
        self.status = CollectionStatus.PENDING
        self.total_collected = 0
        self.error_count = 0
        self.last_collection_time = None
        self.created_at = datetime.now().isoformat()


class DataPoint:
    """数据点"""

    def __init__(
        self, timestamp: datetime, tags: Dict, fields: Dict, source: str = None
    ):
        self.id = f"point_{uuid.uuid4().hex[:12]}"
        self.timestamp = timestamp
        self.tags = tags
        self.fields = fields
        self.source = source


class DataPipeline:
    """数据处理流水线"""

    def __init__(
        self,
        name: str,
        steps: List[str] = None,  # filter, transform, aggregate, validate
    ):
        self.id = f"pipeline_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.steps = steps or []
        self.enabled = True


class CollectionService:
    """实时数据采集服务"""

    def __init__(self):
        self.tasks: Dict[str, CollectionTask] = {}
        self.pipelines: Dict[str, DataPipeline] = {}
        self.data_buffer: queue.Queue = queue.Queue(maxsize=10000)
        self.handlers: Dict[str, Callable] = {}
        self._init_default_pipelines()

    def _init_default_pipelines(self):
        """初始化默认流水线"""
        # 实时数据清洗
        clean_pipeline = DataPipeline(name="实时数据清洗", steps=["filter", "transform"])
        self.pipelines[clean_pipeline.id] = clean_pipeline

        # 实时聚合
        agg_pipeline = DataPipeline(name="实时聚合", steps=["aggregate", "transform"])
        self.pipelines[agg_pipeline.id] = agg_pipeline

    # ========== 采集任务管理 ==========

    def create_task(
        self,
        name: str,
        source_type: str,
        source_config: Dict,
        collection_interval: int = 1,
        batch_size: int = 100,
    ) -> CollectionTask:
        """创建采集任务"""
        task = CollectionTask(
            name, source_type, source_config, collection_interval, batch_size
        )
        self.tasks[task.id] = task
        return task

    def start_task(self, task_id: str) -> bool:
        """启动采集任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.status = CollectionStatus.RUNNING
        # 模拟采集
        self._simulate_collection(task)
        return True

    def _simulate_collection(self, task: CollectionTask):
        """基于物理模型的传感器数据采集"""
        sd = sensor.equipment_health()
        timestamp = datetime.now()
        tags = {
            "device_id": task.source_config.get("device_id", "unknown"),
            "location": task.source_config.get("location", "factory"),
        }
        fields = {
            "temperature": sd["temperature_celsius"],
            "pressure": sd["pressure_mpa"],
            "vibration": sd["vibration_mm_s"],
            "power": round(sd["current_a"] * 3.8, 1),
            "efficiency": round(sd["health_score"] * 0.95, 1),
        }

        # 创建数据点
        point = DataPoint(timestamp, tags, fields, task.source_type)
        self.data_buffer.put(point)

        task.total_collected += 1
        task.last_collection_time = datetime.now().isoformat()

    def pause_task(self, task_id: str) -> bool:
        """暂停采集任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        task.status = CollectionStatus.PAUSED
        return True

    def stop_task(self, task_id: str) -> bool:
        """停止采集任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        task.status = CollectionStatus.STOPPED
        return True

    def delete_task(self, task_id: str) -> bool:
        """删除采集任务"""
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False

    def get_task(self, task_id: str) -> Optional[CollectionTask]:
        """获取采集任务"""
        return self.tasks.get(task_id)

    def list_tasks(self, status: str = None) -> List[Dict]:
        """列出采集任务"""
        tasks = self.tasks.values()
        if status:
            tasks = [t for t in tasks if t.status == status]

        return [
            {
                "id": t.id,
                "name": t.name,
                "source_type": t.source_type,
                "collection_interval": t.collection_interval,
                "status": t.status,
                "total_collected": t.total_collected,
                "error_count": t.error_count,
                "last_collection_time": t.last_collection_time,
                "created_at": t.created_at,
            }
            for t in tasks
        ]

    # ========== 流水线管理 ==========

    def create_pipeline(self, name: str, steps: List[str]) -> DataPipeline:
        """创建数据流水线"""
        pipeline = DataPipeline(name, steps)
        self.pipelines[pipeline.id] = pipeline
        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Optional[DataPipeline]:
        """获取流水线"""
        return self.pipelines.get(pipeline_id)

    def list_pipelines(self) -> List[Dict]:
        """列出流水线"""
        return [
            {"id": p.id, "name": p.name, "steps": p.steps, "enabled": p.enabled}
            for p in self.pipelines.values()
        ]

    # ========== 数据处理 ==========

    def process_data(self, pipeline_id: str, data: List[Dict]) -> List[Dict]:
        """处理数据"""
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline or not pipeline.enabled:
            return data

        result = data
        for step in pipeline.steps:
            if step == "filter":
                result = self._filter_data(result)
            elif step == "transform":
                result = self._transform_data(result)
            elif step == "aggregate":
                result = self._aggregate_data(result)
            elif step == "validate":
                result = self._validate_data(result)

        return result

    def _filter_data(self, data: List[Dict]) -> List[Dict]:
        """过滤数据"""
        # 过滤异常值
        return [d for d in data if d.get("fields", {}).get("temperature", 0) > 0]

    def _transform_data(self, data: List[Dict]) -> List[Dict]:
        """转换数据"""
        for d in data:
            # 添加计算字段
            if "fields" in d:
                fields = d["fields"]
                if "power" in fields and "efficiency" in fields:
                    fields["effective_power"] = (
                        fields["power"] * fields["efficiency"] / 100
                    )
        return data

    def _aggregate_data(self, data: List[Dict]) -> List[Dict]:
        """聚合数据"""
        if not data:
            return []

        # 简单聚合
        result = {
            "timestamp": data[-1].get("timestamp"),
            "tags": data[0].get("tags", {}),
            "fields": {},
        }

        # 计算平均值
        for key in ["temperature", "pressure", "vibration", "power", "efficiency"]:
            values = [d.get("fields", {}).get(key, 0) for d in data]
            result["fields"][f"{key}_avg"] = sum(values) / len(values) if values else 0

        return [result]

    def _validate_data(self, data: List[Dict]) -> List[Dict]:
        """验证数据"""
        valid = []
        for d in data:
            fields = d.get("fields", {})
            # 基本验证
            if 0 <= fields.get("temperature", 0) <= 200:
                if 0 <= fields.get("efficiency", 0) <= 100:
                    valid.append(d)
        return valid

    # ========== 数据获取 ==========

    def get_latest_data(self, source: str = None, limit: int = 100) -> List[Dict]:
        """获取最新数据"""
        # 从缓冲区获取
        data = []
        temp_buffer = []

        while not self.data_buffer.empty():
            try:
                point = self.data_buffer.get_nowait()
                if source is None or point.source == source:
                    data.append(
                        {
                            "id": point.id,
                            "timestamp": point.timestamp.isoformat(),
                            "tags": point.tags,
                            "fields": point.fields,
                            "source": point.source,
                        }
                    )
                else:
                    temp_buffer.append(point)
            except queue.Empty:
                break

        # 放回未取用的数据
        for point in temp_buffer:
            self.data_buffer.put(point)

        return data[-limit:]

    def query_historical_data(
        self,
        start_time: datetime,
        end_time: datetime,
        source: str = None,
        tags: Dict = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """查询历史数据（基于物理传感器模型）"""
        data = []
        current = start_time
        sd = sensor.equipment_health()
        while current <= end_time:
            point = DataPoint(
                timestamp=current,
                tags=tags or {"device_id": "demo", "location": "factory"},
                fields={
                    "temperature": sd["temperature_celsius"],
                    "pressure": sd["pressure_mpa"],
                    "power": round(sd["current_a"] * 3.8, 1),
                },
                source=source,
            )
            data.append(
                {
                    "id": point.id,
                    "timestamp": point.timestamp.isoformat(),
                    "tags": point.tags,
                    "fields": point.fields,
                }
            )
            current += timedelta(minutes=1)

        return data[:limit]

    # ========== 统计 ==========

    def get_collection_stats(self) -> Dict:
        """获取采集统计"""
        total_tasks = len(self.tasks)
        running = sum(
            1 for t in self.tasks.values() if t.status == CollectionStatus.RUNNING
        )
        paused = sum(
            1 for t in self.tasks.values() if t.status == CollectionStatus.PAUSED
        )
        total_collected = sum(t.total_collected for t in self.tasks.values())

        return {
            "total_tasks": total_tasks,
            "running_tasks": running,
            "paused_tasks": paused,
            "total_collected": total_collected,
            "buffer_size": self.data_buffer.qsize(),
            "pipelines": len(self.pipelines),
        }


# 全局实例
_collection_service: Optional[CollectionService] = None


def get_collection_service() -> CollectionService:
    """获取数据采集服务"""
    global _collection_service
    if _collection_service is None:
        _collection_service = CollectionService()
    return _collection_service
