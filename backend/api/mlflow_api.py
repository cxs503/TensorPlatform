"""
MLflow实验追踪API — 直接文件读取版本
通过直接读取 MLflow 文件存储，避免 MlflowClient 在 uvicorn worker 下的兼容性问题
"""
import json
import os
import yaml
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/mlflow", tags=["MLflow实验追踪"])

MLRUNS_DIR = "/app/uploads/mlruns"


def _list_experiments():
    """直接读取 MLflow 文件存储"""
    exps = []
    if not os.path.isdir(MLRUNS_DIR):
        return exps
    for exp_id in sorted(os.listdir(MLRUNS_DIR)):
        exp_dir = os.path.join(MLRUNS_DIR, exp_id)
        meta = os.path.join(exp_dir, "meta.yaml")
        if not os.path.isdir(exp_dir) or not os.path.exists(meta) or not exp_id.isdigit():
            continue
        with open(meta) as f:
            meta_content = yaml.safe_load(f) or {}
        runs = [r for r in os.listdir(exp_dir) if os.path.isdir(os.path.join(exp_dir, r)) and r != ".trash"]
        exps.append({
            "experiment_id": exp_id, "name": meta_content.get("name", "Default"),
            "artifact_location": meta_content.get("artifact_location", ""),
            "lifecycle_stage": meta_content.get("lifecycle_stage", "active"),
            "creation_time": meta_content.get("creation_time", 0),
            "run_count": len(runs),
        })
    return exps


def _list_runs(experiment_id: str, max_results: int = 100):
    """读取实验下的所有 run"""
    exp_dir = os.path.join(MLRUNS_DIR, experiment_id)
    if not os.path.isdir(exp_dir):
        return []
    runs = []
    for run_id in sorted(os.listdir(exp_dir)):
        run_dir = os.path.join(exp_dir, run_id)
        if not os.path.isdir(run_dir) or run_id == ".trash":
            continue
        # Read meta.yaml
        meta = os.path.join(run_dir, "meta.yaml")
        if not os.path.exists(meta):
            continue
        with open(meta) as f:
            meta_content = yaml.safe_load(f) or {}
        # Read metrics
        metrics_dir = os.path.join(run_dir, "metrics")
        metrics = {}
        if os.path.isdir(metrics_dir):
            for mf in os.listdir(metrics_dir):
                if mf == ".trash":
                    continue
                mpath = os.path.join(metrics_dir, mf)
                if os.path.isfile(mpath):
                    with open(mpath) as f:
                        metric_data = yaml.safe_load(f) or []
                    if isinstance(metric_data, list) and metric_data:
                        metrics[mf] = metric_data[-1].get("value", 0) if isinstance(metric_data[-1], dict) else 0
        # Read params
        params_dir = os.path.join(run_dir, "params")
        params = {}
        if os.path.isdir(params_dir):
            for pf in os.listdir(params_dir):
                ppath = os.path.join(params_dir, pf)
                if os.path.isfile(ppath):
                    with open(ppath) as f:
                        params[pf] = f.read().strip()
        runs.append({
            "run_id": run_id, "run_name": meta_content.get("run_name", run_id),
            "status": meta_content.get("status", "UNKNOWN"),
            "start_time": meta_content.get("start_time", 0),
            "end_time": meta_content.get("end_time", 0),
            "experiment_id": experiment_id,
            "metrics": metrics, "params": params,
        })
        if len(runs) >= max_results:
            break
    return runs


# ==========================================================
# API 端点
# ==========================================================

@router.get("/experiments")
def list_experiments():
    items = _list_experiments()
    return {"total": len(items), "items": items, "version": "v2-direct-file-read"}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str):
    for exp in _list_experiments():
        if exp["experiment_id"] == experiment_id:
            return exp
    raise HTTPException(status_code=404, detail=f"实验 {experiment_id} 不存在")


@router.get("/runs/{experiment_id}")
def list_runs(experiment_id: str, max_results: int = Query(100)):
    runs = _list_runs(experiment_id, max_results)
    return {"experiment_id": experiment_id, "total": len(runs), "runs": runs}


@router.get("/runs/{run_id}/metrics")
def get_metrics(run_id: str):
    """查找 run 所属实验并返回其指标"""
    for exp in _list_experiments():
        runs = _list_runs(exp["experiment_id"])
        for r in runs:
            if r["run_id"] == run_id:
                return {"run_id": run_id, "metrics": r["metrics"], "params": r["params"]}
    raise HTTPException(status_code=404, detail=f"Run {run_id} 不存在")


@router.get("/status")
def get_status():
    return {
        "available": True,
        "tracking_uri": f"file://{MLRUNS_DIR}",
        "experiments_count": len(_list_experiments()),
        "mode": "direct_file_read",
    }
