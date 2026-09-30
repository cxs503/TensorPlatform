"""
MLflow实验追踪器 - 核心追踪服务
支持auto-logging sklearn/PyTorch模型，自动注册到MLflow Registry
"""
import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import mlflow
import mlflow.sklearn
import mlflow.pytorch
from mlflow.tracking import MlflowClient
from mlflow.entities import ViewType

logger = logging.getLogger(__name__)

# 追踪存储目录
MLRUNS_DIR = Path("/app/uploads/mlruns")
MLRUNS_DIR.mkdir(parents=True, exist_ok=True)

_DATABASE_URI = os.environ.get(
    "DATABASE_URL", "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"
)
TRACKING_URI = os.environ.get(
    "MLFLOW_TRACKING_URI",
    _DATABASE_URI.replace("postgresql://", "postgresql+psycopg2://", 1).rsplit("/", 1)[0] + "/mlflow",
)
# 注:2026-07-15 从 FileStore 切到 PostgreSQL 后端 —— Model Registry 需要 DB 后端(FileStore 已进 maintenance mode 不支持 Registry)。artifact 仍存本地 FS。

# MLflow 3.13+ requires explicit opt-in for file-based tracking store
if "MLFLOW_ALLOW_FILE_STORE" not in os.environ:
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"


class MLflowTracker:
    """MLflow实验追踪器 - 封装MLflow的run/param/metric/model生命周期"""

    def __init__(self):
        self._client: Optional[MlflowClient] = None
        self._active_run = None

        # 设置追踪URI
        mlflow.set_tracking_uri(TRACKING_URI)
        logger.info(f"MLflow tracking URI set to: {TRACKING_URI}")

        try:
            self._client = MlflowClient(tracking_uri=TRACKING_URI)
            logger.info("MLflow client initialized successfully")
        except Exception as e:
            logger.warning(f"MLflow client initialization failed: {e}")

    @property
    def client(self) -> Optional[MlflowClient]:
        return self._client

    @property
    def tracking_uri(self) -> str:
        return TRACKING_URI

    # ==========================================================
    # 实验管理
    # ==========================================================

    def get_or_create_experiment(self, name: str) -> str:
        """获取或创建实验，返回experiment_id"""
        try:
            experiment = mlflow.get_experiment_by_name(name)
            if experiment:
                logger.info(f"Using existing experiment: {name} (id={experiment.experiment_id})")
                return experiment.experiment_id
            exp_id = mlflow.create_experiment(name)
            logger.info(f"Created new experiment: {name} (id={exp_id})")
            return exp_id
        except Exception as e:
            logger.error(f"Failed to get_or_create experiment '{name}': {e}")
            # 返回默认实验ID(0)
            return "0"

    def list_experiments(self) -> List[Dict]:
        """列出所有实验"""
        try:
            experiments = mlflow.search_experiments(view_type=ViewType.ACTIVE_ONLY)
            return [
                {
                    "experiment_id": exp.experiment_id,
                    "name": exp.name,
                    "artifact_location": exp.artifact_location,
                    "lifecycle_stage": exp.lifecycle_stage,
                    "creation_time": exp.creation_time,
                }
                for exp in experiments
            ]
        except Exception as e:
            logger.error(f"List experiments failed: {e}")
            return []

    # ==========================================================
    # Run生命周期
    # ==========================================================

    def start_run(self, run_name: str, experiment_name: str = "default",
                   tags: Dict[str, str] = None) -> Optional[str]:
        """开始一次MLflow run，返回run_id"""
        try:
            exp_id = self.get_or_create_experiment(experiment_name)
            tags = tags or {}
            tags.update({
                "started_at": datetime.now().isoformat(),
            })

            run = mlflow.start_run(
                run_name=run_name,
                experiment_id=exp_id,
                tags=tags,
            )
            self._active_run = run
            logger.info(f"MLflow run started: {run_name} (run_id={run.info.run_id})")
            return run.info.run_id
        except Exception as e:
            logger.error(f"Start run failed: {e}")
            return None

    def end_run(self, status: str = "FINISHED") -> bool:
        """结束当前run"""
        try:
            mlflow.end_run(status=status)
            self._active_run = None
            logger.info(f"MLflow run ended with status: {status}")
            return True
        except Exception as e:
            logger.error(f"End run failed: {e}")
            return False

    def get_active_run_id(self) -> Optional[str]:
        """获取当前活跃run的ID"""
        active = mlflow.active_run()
        if active:
            return active.info.run_id
        return None

    def purge_run(self, run_id: Optional[str], registered_model_name: Optional[str] = None) -> Dict[str, Any]:
        """删除一次短生命周期门禁产生的 Registry 模型和 Run。

        MLflow 的 Run 删除是后端存储支持的软删除；它会立即从正常查询中消失，
        后续可由平台的 MLflow GC 统一物理回收制品。注册模型则直接删除。
        本方法幂等：目标已经不存在时仍视为清理成功。
        """
        result: Dict[str, Any] = {
            "registered_model": registered_model_name,
            "registered_model_deleted": not bool(registered_model_name),
            "run_id": run_id,
            "run_deleted": not bool(run_id),
            "errors": [],
        }
        if not self._client:
            result["errors"].append("MLflow client unavailable")
            return result

        if registered_model_name:
            try:
                self._client.get_registered_model(registered_model_name)
            except Exception as exc:
                if "RESOURCE_DOES_NOT_EXIST" in str(exc) or "not found" in str(exc).lower():
                    result["registered_model_deleted"] = True
                else:
                    result["errors"].append(f"registry lookup failed: {exc}")
            else:
                try:
                    self._client.delete_registered_model(registered_model_name)
                    result["registered_model_deleted"] = True
                except Exception as exc:
                    result["errors"].append(f"registry delete failed: {exc}")

        if run_id:
            try:
                run = self._client.get_run(run_id)
            except Exception as exc:
                if "RESOURCE_DOES_NOT_EXIST" in str(exc) or "not found" in str(exc).lower():
                    result["run_deleted"] = True
                else:
                    result["errors"].append(f"run lookup failed: {exc}")
            else:
                if getattr(run.info, "lifecycle_stage", "active") == "deleted":
                    result["run_deleted"] = True
                else:
                    try:
                        self._client.delete_run(run_id)
                        result["run_deleted"] = True
                    except Exception as exc:
                        result["errors"].append(f"run delete failed: {exc}")

        return result

    # ==========================================================
    # 参数与指标
    # ==========================================================

    def log_params(self, params: Dict[str, Any]) -> bool:
        """批量记录参数（自动转换非字符串值）"""
        try:
            for key, value in params.items():
                # MLflow只接受字符串参数
                if not isinstance(value, str):
                    value = json.dumps(value, ensure_ascii=False)
                mlflow.log_param(key, value)
            logger.info(f"Logged {len(params)} params")
            return True
        except Exception as e:
            logger.error(f"Log params failed: {e}")
            return False

    def log_metrics(self, metrics: Dict[str, float], step: int = 0) -> bool:
        """批量记录指标"""
        try:
            for key, value in metrics.items():
                if isinstance(value, (int, float)):
                    mlflow.log_metric(key, float(value), step=step)
            logger.info(f"Logged {len(metrics)} metrics at step {step}")
            return True
        except Exception as e:
            logger.error(f"Log metrics failed: {e}")
            return False

    def log_metric(self, key: str, value: float, step: int = 0) -> bool:
        """记录单个指标"""
        try:
            mlflow.log_metric(key, value, step=step)
            return True
        except Exception as e:
            logger.error(f"Log metric '{key}' failed: {e}")
            return False

    # ==========================================================
    # 模型日志与注册
    # ==========================================================

    def log_sklearn_model(self, model, artifact_path: str = "model", registered_model_name: str = None) -> bool:
        """记录sklearn模型为mlflow.pyfunc

        registered_model_name: 注册到 MLflow Model Registry 的模型名。
            2026-07-15: 改为显式传入(命名约定 foundation_model_{models.id}),
            与 models 表形成确定性双向映射;不传则回退到旧的日期命名。
        """
        _name = registered_model_name or f"sklearn_model_{datetime.now().strftime('%Y%m%d')}"
        try:
            mlflow.sklearn.log_model(
                sk_model=model,
                artifact_path=artifact_path,
                registered_model_name=_name,
            )
            logger.info(f"Sklearn model logged at: {artifact_path} (registered as {_name})")
            return True
        except Exception as e:
            logger.error(f"Log sklearn model failed: {e}")
            return False

    def log_pytorch_model(self, model, artifact_path: str = "model", registered_model_name: str = None) -> bool:
        """记录PyTorch模型

        registered_model_name: 见 log_sklearn_model 说明(foundation_model_{models.id})。
        """
        _name = registered_model_name or f"pytorch_model_{datetime.now().strftime('%Y%m%d')}"
        try:
            mlflow.pytorch.log_model(
                pytorch_model=model,
                artifact_path=artifact_path,
                registered_model_name=_name,
            )
            logger.info(f"PyTorch model logged at: {artifact_path} (registered as {_name})")
            return True
        except Exception as e:
            logger.error(f"Log pytorch model failed: {e}")
            return False

    def log_artifact(self, local_path: str, artifact_path: str = None) -> bool:
        """记录任意文件/目录为artifact"""
        try:
            mlflow.log_artifact(local_path, artifact_path)
            logger.info(f"Artifact logged: {local_path} -> {artifact_path or 'root'}")
            return True
        except Exception as e:
            logger.error(f"Log artifact failed for '{local_path}': {e}")
            return False

    def register_model(self, model_uri: str, name: str) -> Optional[Dict]:
        """将模型注册到MLflow Registry"""
        try:
            result = mlflow.register_model(model_uri=model_uri, name=name)
            logger.info(f"Model registered: {name} (version={result.version})")
            return {
                "name": result.name,
                "version": result.version,
            }
        except Exception as e:
            logger.error(f"Register model failed: {e}")
            return None

    def register_artifact_model(
        self,
        name: str,
        run_id: str,
        artifact_path: str = "models",
    ) -> Optional[Dict]:
        """Register an existing artifact when a framework flavor is incompatible."""
        if not self._client or not name or not run_id:
            return None
        try:
            from mlflow.exceptions import MlflowException

            try:
                self._client.create_registered_model(name)
            except MlflowException as exc:
                if "already exists" not in str(exc).lower():
                    raise

            run = self._client.get_run(run_id)
            source = f"{run.info.artifact_uri.rstrip('/')}/{artifact_path.strip('/')}"
            version = self._client.create_model_version(
                name=name,
                source=source,
                run_id=run_id,
                description=(
                    "foundation artifact-backed registration: the original framework "
                    "artifact is preserved because the installed MLflow flavor is "
                    "incompatible with the training runtime."
                ),
            )
            self._client.set_model_version_tag(
                name, version.version, "foundation.registration_mode", "artifact_fallback"
            )
            logger.warning(
                "Registered artifact fallback: %s v%s (run=%s, source=%s)",
                name, version.version, run_id, source,
            )
            return {"name": name, "version": version.version, "source": source}
        except Exception as exc:
            logger.error(f"Register artifact fallback failed: {exc}")
            return None

    def transition_model_stage(self, name: str, version: int, stage: str) -> bool:
        """转换模型版本阶段"""
        try:
            if self._client:
                self._client.transition_model_version_stage(
                    name=name,
                    version=version,
                    stage=stage,
                )
                logger.info(f"Model {name} v{version} transitioned to {stage}")
                return True
            return False
        except Exception as e:
            logger.error(f"Transition model failed: {e}")
            return False

    def list_registry_models(self) -> list:
        """列出 MLflow Model Registry 中所有已注册模型及其版本/stage"""
        if not self._client:
            return []
        try:
            out = []
            for rm in self._client.search_registered_models():
                versions = self._client.search_model_versions(f"name='{rm.name}'")
                out.append({
                    "name": rm.name,
                    "version_count": len(versions),
                    "versions": [
                        {"version": v.version, "stage": v.current_stage, "run_id": v.run_id,
                         "status": v.status, "created_at": v.creation_timestamp}
                        for v in versions
                    ],
                })
            return out
        except Exception as e:
            logger.error(f"List registry models failed: {e}")
            return []

    def get_registry_model(self, name: str) -> Optional[Dict]:
        """按 name 查询单个注册模型及其版本/stage/run_id(供 models.id → MLflow 反查)"""
        if not self._client:
            return None
        try:
            versions = self._client.search_model_versions(f"name='{name}'")
            if not versions:
                return None
            latest = versions[0]  # search_model_versions 默认按 creation_timestamp 倒序
            return {
                "name": name,
                "version_count": len(versions),
                "latest_version": latest.version,
                "latest_stage": latest.current_stage,
                "latest_run_id": latest.run_id,
                "latest_status": latest.status,
                "versions": [
                    {"version": v.version, "stage": v.current_stage, "run_id": v.run_id,
                     "status": v.status, "created_at": v.creation_timestamp}
                    for v in versions
                ],
            }
        except Exception as e:
            logger.error(f"Get registry model failed for {name}: {e}")
            return None


    # ==========================================================
    # 查询
    # ==========================================================

    def get_runs(self, experiment_id: str, max_results: int = 100) -> List[Dict]:
        """获取实验下的所有run"""
        try:
            runs = mlflow.search_runs(
                experiment_ids=[experiment_id],
                max_results=max_results,
            )
            if runs.empty:
                return []

            result = []
            for _, row in runs.iterrows():
                result.append({
                    "run_id": row.get("run_id", ""),
                    "status": row.get("status", ""),
                    "start_time": row.get("start_time", ""),
                    "metrics": {k: v for k, v in row.items()
                                if k.startswith("metrics.") and not k.startswith("metrics._")},
                    "params": {k.replace("params.", ""): v for k, v in row.items()
                               if k.startswith("params.")},
                })
            return result
        except Exception as e:
            logger.error(f"Get runs failed: {e}")
            return []

    def get_metrics_history(self, run_id: str) -> List[Dict]:
        """获取run的指标历史"""
        if not self._client:
            return []

        try:
            run = mlflow.get_run(run_id)
            metric_keys = list(run.data.metrics.keys()) if run.data.metrics else []
            history = []
            for key in metric_keys:
                metric_history = self._client.get_metric_history(run_id, key)
                for m in metric_history:
                    history.append({
                        "key": m.key,
                        "value": m.value,
                        "timestamp": m.timestamp,
                        "step": m.step,
                    })
            return history
        except Exception as e:
            logger.error(f"Get metrics history failed: {e}")
            return []

    def get_artifacts(self, run_id: str, path: str = None) -> List[Dict]:
        """列出run的artifacts"""
        if not self._client:
            return []

        try:
            artifacts = self._client.list_artifacts(run_id, path)
            return [
                {
                    "name": a.path,
                    "is_dir": a.is_dir,
                    "file_size": a.file_size,
                }
                for a in artifacts
            ]
        except Exception as e:
            logger.error(f"List artifacts failed: {e}")
            return []


# ==========================================================
# 全局实例
# ==========================================================

_mlflow_tracker: Optional[MLflowTracker] = None


def get_mlflow_tracker() -> MLflowTracker:
    """获取MLflowTracker单例"""
    global _mlflow_tracker
    if _mlflow_tracker is None:
        _mlflow_tracker = MLflowTracker()
    return _mlflow_tracker
