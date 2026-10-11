"""
Dashboard 数据代理 API
统一接口，前端无需直连 MLflow/Optuna/Ray
"""
import json
import requests
try:
    import optuna
except ImportError:
    optuna = None
try:
    import numpy as np
except ImportError:
    np = None
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/api/v1/dashboard", tags=["开源工具面板"])

MLFLOW_BASE = "http://localhost:5000"
OPTUNA_DB = "sqlite:////app/uploads/optuna.db"


# ================== MLflow 代理 ==================

@router.get("/mlflow/experiments")
def mlflow_experiments(max_results: int = Query(50)):
    """获取 MLflow 实验列表"""
    try:
        r = requests.post(
            f"{MLFLOW_BASE}/api/2.0/mlflow/experiments/search",
            json={"max_results": max_results}, timeout=5
        )
        data = r.json()
        experiments = []
        for exp in data.get("experiments", []):
            experiments.append({
                "id": exp.get("experiment_id"),
                "name": exp.get("name"),
                "artifact_location": exp.get("artifact_location"),
                "lifecycle_stage": exp.get("lifecycle_stage"),
                "tags": exp.get("tags", []),
            })
        return {"total": len(experiments), "experiments": experiments}
    except Exception as e:
        return {"error": str(e), "experiments": [], "note": "MLflow UI may not be running"}

@router.get("/mlflow/health")
def mlflow_health():
    """MLflow 服务健康检查"""
    import socket
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(('localhost', 5000))
        return {"status": "running", "url": "/mlflow/"}
    except:
        return {"status": "stopped", "url": None, "note": "Start with: mlflow ui --port 5000"}
    finally:
        s.close()


@router.get("/mlflow/experiments/{experiment_id}")
def mlflow_experiment_detail(experiment_id: str):
    """获取实验详情 + runs"""
    try:
        r = requests.get(
            f"{MLFLOW_BASE}/api/2.0/mlflow/experiments/get?experiment_id={experiment_id}",
            timeout=5
        )
        exp = r.json().get("experiment", {})

        # Get runs
        r2 = requests.post(
            f"{MLFLOW_BASE}/api/2.0/mlflow/runs/search",
            json={"experiment_ids": [experiment_id], "max_results": 100},
            timeout=5
        )
        runs = []
        for run in r2.json().get("runs", []):
            info = run.get("info", {})
            data_tags = run.get("data", {})
            runs.append({
                "run_id": info.get("run_id"),
                "run_name": info.get("run_name"),
                "status": info.get("status"),
                "start_time": info.get("start_time"),
                "metrics": data_tags.get("metrics", {}),
                "params": data_tags.get("params", {}),
            })

        return {
            "id": exp.get("experiment_id"),
            "name": exp.get("name"),
            "runs": runs,
            "total_runs": len(runs),
        }
    except Exception as e:
        return {"error": str(e), "runs": []}


@router.get("/mlflow/runs/{run_id}/metrics")
def mlflow_run_metrics(run_id: str):
    """获取单个 run 的指标历史"""
    try:
        r = requests.get(
            f"{MLFLOW_BASE}/api/2.0/mlflow/metrics/get-history?run_id={run_id}",
            timeout=5
        )
        return {"run_id": run_id, "metrics": r.json().get("metrics", [])}
    except Exception as e:
        return {"error": str(e), "metrics": []}


# ================== Optuna 代理 ==================

@router.get("/optuna/studies")
def optuna_studies():
    """获取 Optuna 实验列表"""
    try:
        studies = []
        names = []
        try:
            names = optuna.get_all_study_names(storage=OPTUNA_DB)
        except:
            pass
        for name in names:
            try:
                study = optuna.load_study(study_name=name, storage=OPTUNA_DB)
                studies.append({
                    "name": name,
                    "direction": str(study.direction),
                    "n_trials": len(study.trials),
                    "best_value": study.best_value,
                    "best_params": study.best_params,
                })
            except:
                pass
        if not studies:
            studies.append({"name": "demo", "direction": "maximize", "n_trials": 0, "best_value": None, "best_params": None})
        return {"total": len(studies), "studies": studies, "note": "Start training to populate real data"}
    except Exception as e:
        return {"error": str(e), "studies": []}


@router.get("/optuna/studies/{study_name}/trials")
def optuna_trials(study_name: str, n_top: int = Query(20)):
    """获取 Optuna 实验的 trial 数据"""
    try:
        study = optuna.load_study(study_name=study_name, storage=OPTUNA_DB)
        trials = study.trials
        completed = [t for t in trials if t.state == optuna.trial.TrialState.COMPLETE]
        completed.sort(key=lambda t: t.value if t.value is not None else float("inf"),
                       reverse=study.direction == optuna.study.StudyDirection.MAXIMIZE)

        result = []
        for t in completed[:n_top]:
            result.append({
                "number": t.number,
                "value": float(t.value) if t.value is not None else None,
                "params": t.params,
                "datetime_start": str(t.datetime_start) if t.datetime_start else None,
                "duration": t.duration.total_seconds() if t.duration else None,
            })

        return {
            "study_name": study_name,
            "direction": str(study.direction),
            "n_total": len(trials),
            "n_completed": len(completed),
            "best_value": study.best_value,
            "best_params": study.best_params,
            "trials": result,
        }
    except Exception as e:
        return {"error": str(e), "trials": []}


@router.get("/optuna/studies/{study_name}/importance")
def optuna_importance(study_name: str):
    """获取超参数重要性"""
    try:
        study = optuna.load_study(study_name=study_name, storage=OPTUNA_DB)
        importance = optuna.importance.get_param_importances(study)
        result = [{"param": k, "importance": float(v)} for k, v in
                   sorted(importance.items(), key=lambda x: x[1], reverse=True)]
        return {"study_name": study_name, "importance": result}
    except Exception as e:
        return {"error": str(e), "importance": []}


# ================== Ray 代理 ==================

@router.get("/ray/cluster")
def ray_cluster():
    """获取 Ray 集群信息"""
    try:
        import ray
        if not ray.is_initialized():
            ray.init(num_cpus=2, ignore_reinit_error=True, logging_level="ERROR")
        nodes = ray.nodes()
        resources = ray.cluster_resources()
        return {
            "num_nodes": len(nodes),
            "resources": {
                "cpu": int(resources.get("CPU", 0)),
                "gpu": int(resources.get("GPU", 0)),
                "memory_gb": round(resources.get("memory", 0) / (1024**3), 1),
                "object_store_gb": round(resources.get("object_store_memory", 0) / (1024**3), 1),
            },
            "nodes": [
                {"name": n.get("NodeName", "?"), "alive": n.get("Alive", True),
                 "node_ip": n.get("NodeManagerAddress", "?")}
                for n in nodes
            ],
        }
    except Exception as e:
        return {"error": str(e), "num_nodes": 0, "resources": {}}


@router.get("/ray/tasks")
def ray_tasks():
    """获取 Ray 任务统计"""
    try:
        import ray
        if not ray.is_initialized():
            return {"tasks": [], "n_pending": 0, "n_running": 0}
        # Simple status
        return {"ray_initialized": True, "note": "Ray cluster active"}
    except Exception as e:
        return {"error": str(e)}
