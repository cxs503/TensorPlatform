"""
超参搜索 API — DB持久化 + 对接实际训练
"""
import json
import uuid
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from database.config import get_db
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/hyperopt", tags=["超参数优化"])


class StudyCreateReq(BaseModel):
    name: str
    dataset_id: int
    objective_metric: str = "r2"
    optimization_direction: str = "maximize"
    search_algorithm: str = "bayesian"
    n_trials: int = 30
    parameters: Dict[str, Dict[str, Any]] = {}


# ─── 参数采样 ───

# 默认参数搜索空间:用户未指定 parameters 时使用,确保每个 trial 真正变化参数。
# n_estimators/max_depth/min_samples_split 用 int(树模型必需整数),
# learning_rate/min_samples_leaf 用 float。
DEFAULT_PARAM_SPACE = {
    "n_estimators": {"type": "int", "low": 30, "high": 300},
    "max_depth": {"type": "int", "low": 3, "high": 20},
    "min_samples_split": {"type": "int", "low": 2, "high": 20},
    "learning_rate": {"type": "uniform", "low": 0.01, "high": 0.3},
}


def _resolve_param_space(param_space: Optional[dict]) -> dict:
    """若用户未提供任何参数空间,回退到默认空间。

    原bug:parameters={} 时 _sample_params 返回 {},每个 trial 用相同默认参数训练,
    objective 全部相同(~0.6071),等于没搜索。
    """
    if not param_space:
        return dict(DEFAULT_PARAM_SPACE)
    # 过滤掉无效条目(name 为空 / 缺 type)
    clean = {}
    for name, cfg in (param_space or {}).items():
        if not name or not isinstance(cfg, dict):
            continue
        clean[name] = cfg
    return clean if clean else dict(DEFAULT_PARAM_SPACE)


def _sample_params(param_space: dict) -> dict:
    """根据参数空间定义采样一组参数。

    返回值均为 Python 原生类型(int/float/str),可直接喂给 sklearn/LightGBM 并安全 JSON 序列化。
    """
    params = {}
    for pname, pconfig in param_space.items():
        ptype = pconfig.get("type", "uniform")
        low = float(pconfig.get("low", 0))
        high = float(pconfig.get("high", 1))
        if ptype == "uniform":
            params[pname] = float(round(np.random.uniform(low, high), 6))
        elif ptype == "loguniform":
            params[pname] = float(round(np.exp(np.random.uniform(np.log(low + 1e-10), np.log(high + 1e-10))), 6))
        elif ptype == "int":
            # sklearn 的 n_estimators/max_depth/min_samples_split 必须是 Python int,
            # 传 numpy.int64 或 float 会导致训练失败或被静默忽略(原 bug: objective 全为 0)。
            lo, hi = int(low), int(high)
            if hi < lo:
                lo, hi = hi, lo
            params[pname] = int(np.random.randint(lo, hi + 1))
        elif ptype == "categorical":
            choices = pconfig.get("choices", [low, high])
            picked = np.random.choice(choices)
            # numpy 的 choice 返回 numpy 标量/str_,转 Python 原生类型
            if isinstance(picked, np.generic):
                picked = picked.item()
            params[pname] = picked
        else:
            params[pname] = float(round(np.random.uniform(low, high), 6))
    return params


# ─── 后台训练线程 ───

def _run_study(study_id: str, db_session_factory):
    """后台运行超参搜索：每轮采样参数 → 训练模型 → 记录指标"""
    db = db_session_factory()
    try:
        row = db.execute(text(
            "SELECT study_id, name, dataset_id, objective_metric, direction, n_trials, parameters, status "
            "FROM hyperopt_studies WHERE study_id = :sid"
        ), {"sid": study_id}).fetchone()
        if not row:
            return

        name = row[1]
        dataset_id = row[2]
        metric = row[3]
        direction = row[4]
        n_trials = row[5]
        param_space = _resolve_param_space(row[6] if isinstance(row[6], dict) else json.loads(row[6]))

        db.execute(text(
            "UPDATE hyperopt_studies SET status='running' WHERE study_id=:sid"
        ), {"sid": study_id})
        db.commit()

        from services.real_training_service import TrainingService
        from services.data_loader import load_dataset_data

        is_maximize = direction == "maximize"
        best_value = float("-inf") if is_maximize else float("inf")
        best_params = {}

        for i in range(n_trials):
            # 检查是否已被停止
            check = db.execute(text(
                "SELECT status FROM hyperopt_studies WHERE study_id=:sid"
            ), {"sid": study_id}).fetchone()
            if not check or check[0] == "stopped":
                break

            params = _sample_params(param_space)
            hp_str = json.dumps(params)

            # 创建训练任务
            config = {
                "name": f"HyperOpt-{study_id[:8]}-trial{i}",
                "task_type": "regression",
                "model_type": "random_forest",
                "dataset_id": dataset_id,
                "optimization_metric": metric,
                "skip_model_registration": True,
            }
            reserved_keys = {"name", "task_type", "model_type", "dataset_id", "X", "y", "feature_columns"}
            config.update({k: v for k, v in params.items() if k not in reserved_keys})

            # 加载数据
            try:
                X, y, feature_names = load_dataset_data(db, dataset_id)
                X_list = X.tolist() if hasattr(X, 'tolist') else list(X)
                y_list = y.tolist() if hasattr(y, 'tolist') else list(y)
                config["X"] = X_list
                config["y"] = y_list
                config["feature_columns"] = feature_names
            except Exception as e:
                db.execute(text(
                    "INSERT INTO hyperopt_trials (study_id, trial_number, parameters, status, metrics) "
                    "VALUES (:sid, :tn, :p, 'failed', :m)"
                ), {"sid": study_id, "tn": i, "p": hp_str,
                    "m": json.dumps({"error": str(e)})})
                db.commit()
                continue

            job_id = TrainingService.create_job(config)
            TrainingService.start_training(job_id, {"X": X_list, "y": y_list})

            # 等待训练完成（同步等待，超参搜索是串行的）
            import time
            waited = 0
            max_wait = 120
            while waited < max_wait:
                time.sleep(2)
                waited += 2
                try:
                    status = TrainingService.get_job_status(job_id)
                    if status.get("status") in ("completed", "failed"):
                        break
                except Exception:
                    pass

            # 获取指标
            final_status = TrainingService.get_job_status(job_id)
            job_state = final_status.get("status")
            metrics = final_status.get("best_metrics") or final_status.get("metrics") or {}

            # 训练失败或没有目标指标时,标记为 failed 而非静默写入 objective=0 的 completed
            # (原 bug:metrics.get(metric, 0) 在失败时返回 0,污染最优值搜索并掩盖错误)
            if job_state == "failed" or metric not in metrics:
                err_msg = final_status.get("error") or f"trial 训练未产出指标 {metric}"
                db.execute(text(
                    "INSERT INTO hyperopt_trials (study_id, trial_number, parameters, objective_value, metrics, job_id, status, started_at, completed_at) "
                    "VALUES (:sid, :tn, :p, NULL, :m, :jid, 'failed', NOW(), NOW())"
                ), {
                    "sid": study_id, "tn": i, "p": hp_str,
                    "m": json.dumps({"error": str(err_msg), **metrics}),
                    "jid": job_id,
                })
                db.commit()
                print(f"[HyperOpt] Study {study_id} trial {i} FAILED: {err_msg} | params={hp_str}")
                continue

            obj_val = float(metrics.get(metric))

            # 更新最佳值
            improved = (is_maximize and obj_val > best_value) or (not is_maximize and obj_val < best_value)
            if improved:
                best_value = obj_val
                best_params = params

            # 记录试验
            db.execute(text(
                "INSERT INTO hyperopt_trials (study_id, trial_number, parameters, objective_value, metrics, job_id, status, started_at, completed_at) "
                "VALUES (:sid, :tn, :p, :ov, :m, :jid, 'completed', NOW(), NOW())"
            ), {
                "sid": study_id, "tn": i, "p": hp_str, "ov": obj_val,
                "m": json.dumps(metrics), "jid": job_id,
            })

            db.execute(text(
                "UPDATE hyperopt_studies SET best_value=:bv, best_params=:bp, updated_at=NOW() WHERE study_id=:sid"
            ), {"bv": best_value, "bp": json.dumps(best_params), "sid": study_id})
            db.commit()
            print(f"[HyperOpt] Study {study_id} trial {i} OK: {metric}={obj_val:.4f} | params={hp_str}")

        # 完成 — 用最优参数训练最终模型并注册
        # 若没有成功 trial(best_params 为空 / best_value 仍为初始 inf),不注册假的最优模型
        if not best_params or best_value in (float("-inf"), float("inf")):
            db.execute(text(
                "UPDATE hyperopt_studies SET status='completed', best_value=NULL, best_params=NULL, updated_at=NOW() WHERE study_id=:sid"
            ), {"sid": study_id})
            db.commit()
            print(f"[HyperOpt] Study {study_id} completed — no successful trial, no best model registered")
            return

        try:
            config_final = {
                "name": f"HyperOpt-best-{study_id[:8]}",
                "task_type": "regression",
                "model_type": "random_forest",
                "dataset_id": dataset_id,
                "optimization_metric": metric,
                "skip_model_registration": True,
            }
            # 应用最优参数
            config_final.update(best_params)
            X, y, feature_names = load_dataset_data(db, dataset_id)
            X_list = X.tolist() if hasattr(X, 'tolist') else list(X)
            y_list = y.tolist() if hasattr(y, 'tolist') else list(y)
            config_final["X"] = X_list
            config_final["y"] = y_list
            config_final["feature_columns"] = feature_names

            final_job_id = TrainingService.create_job(config_final)
            TrainingService.start_training(final_job_id, {"X": X_list, "y": y_list})

            # 等待最终训练完成
            waited_final = 0
            while waited_final < 120:
                time.sleep(2)
                waited_final += 2
                try:
                    fs = TrainingService.get_job_status(final_job_id)
                    if fs.get("status") in ("completed", "failed"):
                        break
                except Exception:
                    pass

            # 在 models 表创建模型记录
            final_status = TrainingService.get_job_status(final_job_id)
            final_metrics = final_status.get("best_metrics") or final_status.get("metrics") or {}
            model_name = f"HyperOpt-{name}-best"
            db.execute(text(
                "INSERT INTO models (name, description, model_type, framework, status, created_at, updated_at) "
                "VALUES (:name, :desc, :mtype, :fw, 'READY', NOW(), NOW())"
            ), {
                "name": model_name,
                "desc": f"超参搜索最优模型 | 数据集#{dataset_id} | {metric}={best_value:.4f}",
                "mtype": "regression",
                "fw": "sklearn",
            })
            db.commit()
            id_row = db.execute(text(
                "SELECT id FROM models WHERE name=:n ORDER BY id DESC LIMIT 1"
            ), {"n": model_name}).fetchone()
            best_model_id = id_row[0] if id_row else None

            db.execute(text(
                "UPDATE hyperopt_studies SET status='completed', best_model_id=:mid, updated_at=NOW() WHERE study_id=:sid"
            ), {"sid": study_id, "mid": best_model_id})
            db.commit()
            print(f"[HyperOpt] Study {study_id} completed — model #{best_model_id} registered")
        except Exception as e:
            print(f"[HyperOpt] Final model creation failed: {e}")
            db.execute(text(
                "UPDATE hyperopt_studies SET status='completed', updated_at=NOW() WHERE study_id=:sid"
            ), {"sid": study_id})
            db.commit()

    except Exception as e:
        try:
            db.execute(text(
                "UPDATE hyperopt_studies SET status='failed', updated_at=NOW() WHERE study_id=:sid"
            ), {"sid": study_id})
            db.commit()
        except Exception:
            pass
        print(f"[HyperOpt] Study {study_id} failed: {e}")
    finally:
        db.close()


# ─── API ───

@router.post("/studies")
def create_study(req: StudyCreateReq, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """创建超参搜索研究并启动"""
    study_id = f"study_{uuid.uuid4().hex[:8]}"
    db.execute(text(
        "INSERT INTO hyperopt_studies (study_id, name, dataset_id, objective_metric, direction, search_algorithm, n_trials, parameters, status) "
        "VALUES (:sid, :name, :did, :metric, :dir, :algo, :n, :params, 'pending')"
    ), {
        "sid": study_id, "name": req.name, "did": req.dataset_id,
        "metric": req.objective_metric, "dir": req.optimization_direction,
        "algo": req.search_algorithm, "n": req.n_trials,
        "params": json.dumps(req.parameters),
    })
    db.commit()

    # 启动后台线程
    from database.config import SessionLocal
    t = threading.Thread(target=_run_study, args=(study_id, SessionLocal), daemon=True)
    t.start()

    return {"study_id": study_id, "name": req.name, "status": "running",
            "message": f"已启动 {req.n_trials} 轮超参搜索"}


@router.get("/studies")
def list_studies(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT study_id, name, dataset_id, objective_metric, search_algorithm, n_trials, status, best_value, best_params, best_model_id, created_at "
        "FROM hyperopt_studies ORDER BY created_at DESC"
    )).fetchall()
    items = []
    for r in rows:
        items.append({
            "study_id": r[0], "name": r[1], "dataset_id": r[2],
            "objective_metric": r[3], "search_algorithm": r[4],
            "n_trials": r[5], "status": r[6], "best_value": r[7],
            "best_params": r[8] if isinstance(r[8], dict) else (json.loads(r[8]) if r[8] else None),
            "best_model_id": r[9],
            "created_at": r[10].isoformat() if r[10] else None,
        })
    return {"total": len(items), "items": items}


@router.get("/studies/{study_id}")
def get_study(study_id: str, db: Session = Depends(get_db)):
    r = db.execute(text(
        "SELECT * FROM hyperopt_studies WHERE study_id=:sid"
    ), {"sid": study_id}).fetchone()
    if not r:
        raise HTTPException(404, "研究不存在")
    cols = [c[0] for c in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='hyperopt_studies'")).fetchall()]
    d = dict(zip(cols, r))
    for k in ("parameters", "best_params"):
        if d.get(k) and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


@router.get("/studies/{study_id}/trials")
def get_trials(study_id: str, db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, study_id, trial_number, parameters, objective_value, metrics, job_id, status, started_at, completed_at "
        "FROM hyperopt_trials WHERE study_id=:sid ORDER BY trial_number"
    ), {"sid": study_id}).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r[0], "study_id": r[1], "trial_number": r[2],
            "parameters": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
            "objective_value": r[4], "metrics": r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else {}),
            "job_id": r[6], "status": r[7],
            "started_at": r[8].isoformat() if r[8] else None,
            "completed_at": r[9].isoformat() if r[9] else None,
        })
    return {"total": len(items), "items": items}


@router.get("/studies/{study_id}/best")
def get_best(study_id: str, db: Session = Depends(get_db)):
    r = db.execute(text(
        "SELECT study_id, best_value, best_params, objective_metric FROM hyperopt_studies WHERE study_id=:sid"
    ), {"sid": study_id}).fetchone()
    if not r:
        raise HTTPException(404, "研究不存在")
    return {
        "study_id": r[0], "best_value": r[1],
        "best_params": r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else None),
        "objective_metric": r[3],
    }


@router.post("/studies/{study_id}/stop")
def stop_study(study_id: str, db: Session = Depends(get_db)):
    db.execute(text("UPDATE hyperopt_studies SET status='stopped', updated_at=NOW() WHERE study_id=:sid"), {"sid": study_id})
    db.commit()
    return {"study_id": study_id, "status": "stopped"}


@router.delete("/studies/{study_id}")
def delete_study(study_id: str, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM hyperopt_trials WHERE study_id=:sid"), {"sid": study_id})
    db.execute(text("DELETE FROM hyperopt_studies WHERE study_id=:sid"), {"sid": study_id})
    db.commit()
    return {"study_id": study_id, "deleted": True}


@router.get("/algorithms")
def list_algorithms():
    return {"algorithms": [
        {"id": "bayesian", "name": "贝叶斯优化", "desc": "适合连续参数空间"},
        {"id": "random", "name": "随机搜索", "desc": "快速基线探索"},
        {"id": "grid", "name": "网格搜索", "desc": "小规模穷举"},
    ]}


# 兼容 Optuna 前端
@router.get("/tasks")
def get_tasks(db: Session = Depends(get_db)):
    return list_studies(db)
