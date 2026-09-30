"""
Enhanced Experiment Tracking API - 增强实验追踪
提供原生可视化、系统指标集成、团队协作
"""
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/experiments", tags=["增强实验"])

# ============ 数据模型 ============


class ExperimentStatus(str, Enum):
    """实验状态"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MetricType(str, Enum):
    """指标类型"""

    TRAINING = "training"
    VALIDATION = "validation"
    TEST = "test"
    SYSTEM = "system"
    CUSTOM = "custom"


class Metric(BaseModel):
    """指标"""

    name: str
    value: float
    step: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)
    metric_type: MetricType = MetricType.TRAINING


class Experiment(BaseModel):
    """实验"""

    name: str
    description: Optional[str] = None
    project: str = "default"
    tags: Dict[str, str] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ExperimentResponse(Experiment):
    """实验响应"""

    id: str
    status: ExperimentStatus
    created_at: datetime
    updated_at: datetime
    created_by: str = "system"
    metrics: List[Metric] = Field(default_factory=list)


class SystemMetrics(BaseModel):
    """系统指标"""

    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    gpu_percent: Optional[float] = None
    gpu_memory_used_mb: Optional[float] = None
    gpu_memory_total_mb: Optional[float] = None
    gpu_utilization: Optional[float] = None
    disk_percent: float
    network_rx_mb: float
    network_tx_mb: float
    timestamp: datetime = Field(default_factory=datetime.now)


class RunComparison(BaseModel):
    """运行比较"""

    run_ids: List[str]
    metrics: List[str]
    group_by: Optional[str] = None


class TeamCollaboration(BaseModel):
    """团队协作"""

    experiment_id: str
    collaborators: List[str]
    permission: str = "view"  # view, edit, admin


class Comment(BaseModel):
    """评论"""

    experiment_id: str
    run_id: Optional[str] = None
    content: str
    author: str


# ============ 内存存储 ============

experiments_db: Dict[str, ExperimentResponse] = {}
runs_db: Dict[str, Dict[str, Any]] = {}
system_metrics_db: Dict[str, List[SystemMetrics]] = {}
comments_db: List[Dict[str, Any]] = []
collaborations_db: Dict[str, TeamCollaboration] = {}

# ============ API 端点 ============


@router.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "enhanced-experiments",
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/status")
async def get_status():
    """获取服务状态"""
    return {
        "status": "ready",
        "total_experiments": len(experiments_db),
        "total_runs": len(runs_db),
        "features": {
            "native_visualization": True,
            "system_metrics": True,
            "team_collaboration": True,
            "comparison": True,
            "comments": True,
        },
    }


# ============ 实验管理 ============


@router.post("/", response_model=ExperimentResponse)
async def create_experiment(experiment: Experiment):
    """创建实验"""
    exp_id = str(uuid.uuid4())
    now = datetime.now()

    response = ExperimentResponse(
        id=exp_id,
        name=experiment.name,
        description=experiment.description,
        project=experiment.project,
        tags=experiment.tags,
        parameters=experiment.parameters,
        status=ExperimentStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )

    experiments_db[exp_id] = response
    return response


@router.get("/", response_model=List[ExperimentResponse])
async def list_experiments(
    project: Optional[str] = None,
    status: Optional[ExperimentStatus] = None,
    tags: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
):
    """列出实验"""
    result = list(experiments_db.values())

    if project:
        result = [e for e in result if e.project == project]

    if status:
        result = [e for e in result if e.status == status]

    if tags:
        tag_filters = dict(tag.split("=") for tag in tags.split(","))
        result = [
            e for e in result if all(e.tags.get(k) == v for k, v in tag_filters.items())
        ]

    result.sort(key=lambda x: x.updated_at, reverse=True)
    return result[skip : skip + limit]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(experiment_id: str):
    """获取实验详情"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")
    return experiments_db[experiment_id]


@router.put("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(experiment_id: str, experiment: Experiment):
    """更新实验"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    existing = experiments_db[experiment_id]

    updated = ExperimentResponse(
        id=experiment_id,
        name=experiment.name,
        description=experiment.description,
        project=experiment.project,
        tags=experiment.tags,
        parameters=experiment.parameters,
        status=existing.status,
        created_at=existing.created_at,
        updated_at=datetime.now(),
    )

    experiments_db[experiment_id] = updated
    return updated


@router.delete("/{experiment_id}")
async def delete_experiment(experiment_id: str):
    """删除实验"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    del experiments_db[experiment_id]
    return {"message": "实验已删除", "experiment_id": experiment_id}


# ============ 运行管理 ============


@router.post("/{experiment_id}/runs")
async def create_run(experiment_id: str, params: Dict[str, Any]):
    """创建实验运行"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    run_id = str(uuid.uuid4())
    now = datetime.now()

    run = {
        "id": run_id,
        "experiment_id": experiment_id,
        "parameters": params,
        "status": "running",
        "started_at": now,
        "finished_at": None,
        "metrics": [],
        "artifacts": [],
        "logs": "",
    }

    runs_db[run_id] = run

    # 更新实验状态
    experiments_db[experiment_id].updated_at = now

    return run


@router.get("/{experiment_id}/runs")
async def list_runs(experiment_id: str, status: Optional[str] = None):
    """列出实验运行"""
    result = [r for r in runs_db.values() if r["experiment_id"] == experiment_id]

    if status:
        result = [r for r in result if r["status"] == status]

    result.sort(key=lambda x: x["started_at"], reverse=True)
    return result


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """获取运行详情"""
    if run_id not in runs_db:
        raise HTTPException(status_code=404, detail="运行不存在")
    return runs_db[run_id]


# ============ 指标管理 ============


@router.post("/{experiment_id}/metrics")
async def log_metrics(
    experiment_id: str, run_id: Optional[str] = None, metrics: List[Metric] = []
):
    """记录指标"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    for metric in metrics:
        exp.metrics.append(metric)

    exp.updated_at = datetime.now()

    if run_id and run_id in runs_db:
        runs_db[run_id]["metrics"].extend([m.dict() for m in metrics])

    return {"message": "指标已记录", "count": len(metrics)}


@router.get("/{experiment_id}/metrics")
async def get_experiment_metrics(
    experiment_id: str,
    metric_name: Optional[str] = None,
    metric_type: Optional[MetricType] = None,
):
    """获取实验指标"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]
    metrics = exp.metrics

    if metric_name:
        metrics = [m for m in metrics if m.name == metric_name]

    if metric_type:
        metrics = [m for m in metrics if m.metric_type == metric_type]

    return metrics


@router.get("/{experiment_id}/metrics/summary")
async def get_metrics_summary(experiment_id: str):
    """获取指标摘要"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    # 按指标名分组统计
    metric_summary = {}
    for metric in exp.metrics:
        if metric.name not in metric_summary:
            metric_summary[metric.name] = {
                "count": 0,
                "min": float("inf"),
                "max": float("-inf"),
                "mean": 0,
                "values": [],
            }

        summary = metric_summary[metric.name]
        summary["count"] += 1
        summary["min"] = min(summary["min"], metric.value)
        summary["max"] = max(summary["max"], metric.value)
        summary["values"].append(metric.value)

    # 计算均值
    for name, summary in metric_summary.items():
        if summary["values"]:
            summary["mean"] = sum(summary["values"]) / len(summary["values"])
        del summary["values"]  # 删除详细值

    return metric_summary


# ============ 系统指标 ============


@router.post("/{experiment_id}/runs/{run_id}/system-metrics")
async def log_system_metrics(experiment_id: str, run_id: str, metrics: SystemMetrics):
    """记录系统指标"""
    if run_id not in runs_db:
        raise HTTPException(status_code=404, detail="运行不存在")

    if experiment_id not in system_metrics_db:
        system_metrics_db[experiment_id] = []

    system_metrics_db[experiment_id].append(metrics.dict())

    return {"message": "系统指标已记录"}


@router.get("/{experiment_id}/runs/{run_id}/system-metrics")
async def get_system_metrics(
    experiment_id: str,
    run_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
):
    """获取系统指标"""
    if experiment_id not in system_metrics_db:
        return []

    metrics = system_metrics_db[experiment_id]

    if start_time:
        metrics = [m for m in metrics if m["timestamp"] >= start_time]
    if end_time:
        metrics = [m for m in metrics if m["timestamp"] <= end_time]

    return metrics


@router.get("/{experiment_id}/runs/{run_id}/system-metrics/summary")
async def get_system_metrics_summary(experiment_id: str, run_id: str):
    """获取系统指标摘要"""
    if experiment_id not in system_metrics_db:
        return {}

    metrics = system_metrics_db[experiment_id]

    if not metrics:
        return {}

    # 计算CPU统计
    cpu_values = [m["cpu_percent"] for m in metrics]
    memory_values = [m["memory_percent"] for m in metrics]

    summary = {
        "cpu": {
            "avg": sum(cpu_values) / len(cpu_values) if cpu_values else 0,
            "min": min(cpu_values) if cpu_values else 0,
            "max": max(cpu_values) if cpu_values else 0,
        },
        "memory": {
            "avg": sum(memory_values) / len(memory_values) if memory_values else 0,
            "min": min(memory_values) if memory_values else 0,
            "max": max(memory_values) if memory_values else 0,
        },
    }

    # GPU统计
    gpu_values = [m["gpu_percent"] for m in metrics if m.get("gpu_percent")]
    if gpu_values:
        summary["gpu"] = {
            "avg": sum(gpu_values) / len(gpu_values),
            "min": min(gpu_values),
            "max": max(gpu_values),
        }

    return summary


# ============ 可视化数据 ============


@router.get("/{experiment_id}/visualization/line-chart")
async def get_line_chart_data(
    experiment_id: str, metric_names: List[str], run_id: Optional[str] = None
):
    """获取折线图数据"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    # 筛选指定指标
    chart_data = {}
    for metric_name in metric_names:
        metric_values = [
            {"step": m.step, "value": m.value, "timestamp": m.timestamp.isoformat()}
            for m in exp.metrics
            if m.name == metric_name
        ]
        chart_data[metric_name] = metric_values

    return chart_data


@router.get("/{experiment_id}/visualization/scatter-plot")
async def get_scatter_plot_data(experiment_id: str, x_metric: str, y_metric: str):
    """获取散点图数据"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    # 提取两个指标的数据
    x_values = {m.step: m.value for m in exp.metrics if m.name == x_metric}
    y_values = {m.step: m.value for m in exp.metrics if m.name == y_metric}

    # 合并数据
    points = []
    for step in set(x_values.keys()) & set(y_values.keys()):
        points.append({"x": x_values[step], "y": y_values[step], "step": step})

    return {"x_metric": x_metric, "y_metric": y_metric, "points": points}


@router.get("/{experiment_id}/visualization/parallel-coordinates")
async def get_parallel_coordinates(experiment_id: str, metrics: List[str]):
    """获取平行坐标图数据"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    # 获取每个指标的最后值
    metric_values = {}
    for metric_name in metrics:
        metric_data = [m.value for m in exp.metrics if m.name == metric_name]
        if metric_data:
            metric_values[metric_name] = metric_data[-1]

    return {"dimensions": metrics, "values": metric_values}


# ============ 运行比较 ============


@router.post("/compare")
async def compare_runs(comparison: RunComparison):
    """比较多个运行"""
    results = {}

    for run_id in comparison.run_ids:
        if run_id not in runs_db:
            continue

        run = runs_db[run_id]
        run_metrics = {}

        for metric_name in comparison.metrics:
            values = [
                m["value"]
                for m in run.get("metrics", [])
                if m.get("name") == metric_name
            ]
            if values:
                run_metrics[metric_name] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                    "final": values[-1],
                }

        results[run_id] = run_metrics

    return results


@router.get("/compare/best")
async def find_best_run(
    experiment_id: str, metric: str, objective: str = "maximize"  # maximize or minimize
):
    """查找最佳运行"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    runs = [r for r in runs_db.values() if r["experiment_id"] == experiment_id]

    best_run = None
    best_value = float("-inf") if objective == "maximize" else float("inf")

    for run in runs:
        for m in run.get("metrics", []):
            if m.get("name") == metric:
                value = m.get("value", 0)

                if objective == "maximize" and value > best_value:
                    best_value = value
                    best_run = run
                elif objective == "minimize" and value < best_value:
                    best_value = value
                    best_run = run

    if best_run:
        return {
            "run_id": best_run["id"],
            "metric": metric,
            "value": best_value,
            "objective": objective,
        }

    return {"message": "未找到符合条件的运行"}


# ============ 团队协作 ============


@router.post("/collaboration")
async def add_collaborator(collaboration: TeamCollaboration):
    """添加协作者"""
    collaboration_id = str(uuid.uuid4())

    record = collaboration.dict()
    record["id"] = collaboration_id

    collaborations_db[collaboration_id] = record

    return record


@router.get("/collaboration/{experiment_id}")
async def get_collaborators(experiment_id: str):
    """获取实验协作者"""
    result = [
        c for c in collaborations_db.values() if c["experiment_id"] == experiment_id
    ]
    return result


@router.delete("/collaboration/{collaboration_id}")
async def remove_collaborator(collaboration_id: str):
    """移除协作者"""
    if collaboration_id in collaborations_db:
        del collaborations_db[collaboration_id]
        return {"message": "协作者已移除"}

    raise HTTPException(status_code=404, detail="协作记录不存在")


# ============ 评论 ============


@router.post("/comments")
async def add_comment(comment: Comment):
    """添加评论"""
    comment_id = str(uuid.uuid4())

    record = {
        "id": comment_id,
        "experiment_id": comment.experiment_id,
        "run_id": comment.run_id,
        "content": comment.content,
        "author": comment.author,
        "created_at": datetime.now().isoformat(),
    }

    comments_db.append(record)
    return record


@router.get("/{experiment_id}/comments")
async def get_comments(experiment_id: str, run_id: Optional[str] = None):
    """获取评论"""
    result = [c for c in comments_db if c["experiment_id"] == experiment_id]

    if run_id:
        result = [c for c in result if c.get("run_id") == run_id]

    return result


# ============ 仪表盘 ============


@router.get("/dashboard")
async def get_experiment_dashboard():
    """获取实验仪表盘"""
    # 按项目统计
    project_stats = {}
    for exp in experiments_db.values():
        project = exp.project
        if project not in project_stats:
            project_stats[project] = {
                "total": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
            }

        stats = project_stats[project]
        stats["total"] += 1
        if exp.status == ExperimentStatus.RUNNING:
            stats["running"] += 1
        elif exp.status == ExperimentStatus.COMPLETED:
            stats["completed"] += 1
        elif exp.status == ExperimentStatus.FAILED:
            stats["failed"] += 1

    # 指标统计
    metric_stats = {}
    for exp in experiments_db.values():
        for metric in exp.metrics:
            if metric.name not in metric_stats:
                metric_stats[metric.name] = {"count": 0}
            metric_stats[metric.name]["count"] += 1

    return {
        "project_stats": project_stats,
        "metric_stats": metric_stats,
        "total_experiments": len(experiments_db),
        "total_runs": len(runs_db),
        "timestamp": datetime.now().isoformat(),
    }


# ============ 导出 ============


@router.get("/{experiment_id}/export")
async def export_experiment(experiment_id: str, format: str = "json"):
    """导出实验数据"""
    if experiment_id not in experiments_db:
        raise HTTPException(status_code=404, detail="实验不存在")

    exp = experiments_db[experiment_id]

    runs = [r for r in runs_db.values() if r["experiment_id"] == experiment_id]

    export_data = {
        "experiment": exp.dict(),
        "runs": runs,
        "exported_at": datetime.now().isoformat(),
    }

    return export_data
