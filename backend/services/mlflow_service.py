"""
MLflow实验追踪服务
集成MLflow进行实验管理
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import mlflow
    from mlflow.tracking import MlflowClient

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False
    print("MLflow not available, using mock")


class MLflowService:
    """MLflow实验追踪服务"""

    def __init__(self):
        # MLflow配置
        database_uri = os.getenv(
            "DATABASE_URL", "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"
        )
        default_tracking_uri = (
            database_uri.replace("postgresql://", "postgresql+psycopg2://", 1).rsplit("/", 1)[0]
            + "/mlflow"
        )
        self.tracking_uri = os.getenv("MLFLOW_TRACKING_URI", default_tracking_uri)
        self.artifacts_uri = os.getenv("MLFLOW_ARTIFACTS_URI", "http://localhost:5000")

        self._client = None

        if MLFLOW_AVAILABLE:
            mlflow.set_tracking_uri(self.tracking_uri)
            try:
                self._client = MlflowClient()
            except:
                pass

    def create_experiment(self, name: str, tags: Dict = None) -> str:
        """创建实验"""
        if not MLFLOW_AVAILABLE:
            exp_id = f"exp_{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            return exp_id

        try:
            exp = mlflow.create_experiment(name, tags=tags)
            return exp
        except Exception as e:
            print(f"Create experiment failed: {e}")
            # 实验可能已存在，获取已存在的
            exp = mlflow.get_experiment_by_name(name)
            if exp:
                return exp.experiment_id
            raise

    def get_experiment(self, experiment_id: str = None, name: str = None):
        """获取实验"""
        if not MLFLOW_AVAILABLE:
            return {"id": experiment_id, "name": name, "status": "mock"}

        try:
            if experiment_id:
                return mlflow.get_experiment(experiment_id)
            elif name:
                return mlflow.get_experiment_by_name(name)
        except:
            return None

    def list_experiments(self, view_type: int = 1) -> List[Dict]:
        """列出实验 — 走 MLflow API(适配 postgres/filestore 任意后端)"""
        if not MLFLOW_AVAILABLE or not self._client:
            return []
        try:
            exps = []
            for exp in self._client.search_experiments(view_type=mlflow.entities.ViewType.ALL):
                runs = self._client.search_runs([exp.experiment_id], max_results=1000)
                exps.append({
                    "id": exp.experiment_id,
                    "name": exp.name,
                    "status": exp.lifecycle_stage,
                    "created_at": int(exp.creation_time) if exp.creation_time else 0,
                    "run_count": len(runs),
                })
            return exps
        except Exception as e:
            raise RuntimeError(f"MLflow 实验查询失败: {e}") from e

    def start_run(self, experiment_id: str, run_name: str = None, tags: Dict = None):
        """开始运行"""
        if not MLFLOW_AVAILABLE:
            return {
                "run_id": f"run_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "status": "mock",
            }

        try:
            run = mlflow.start_run(
                experiment_id=experiment_id, run_name=run_name, tags=tags
            )
            return {"run_id": run.info.run_id, "experiment_id": experiment_id}
        except Exception as e:
            print(f"Start run failed: {e}")
            return {"error": str(e)}

    def log_metric(self, key: str, value: float, step: int = None):
        """记录指标"""
        if not MLFLOW_AVAILABLE:
            return True

        try:
            mlflow.log_metric(
                mlflow.entities.Metric(
                    key, value, int(datetime.now().timestamp() * 1000), step or 0
                )
            )
            return True
        except Exception as e:
            print(f"Log metric failed: {e}")
            return False

    def log_param(self, key: str, value: str):
        """记录参数"""
        if not MLFLOW_AVAILABLE:
            return True

        try:
            mlflow.log_param(key, value)
            return True
        except Exception as e:
            print(f"Log param failed: {e}")
            return False

    def log_params(self, params: Dict):
        """批量记录参数"""
        for key, value in params.items():
            self.log_param(key, value)

    def log_metrics(self, metrics: Dict, step: int = None):
        """批量记录指标"""
        for key, value in metrics.items():
            self.log_metric(key, value, step)

    def log_artifact(self, local_path: str):
        """记录artifact"""
        if not MLFLOW_AVAILABLE:
            return True

        try:
            mlflow.log_artifact(local_path)
            return True
        except Exception as e:
            print(f"Log artifact failed: {e}")
            return False

    def log_model(self, model, artifact_path: str):
        """记录模型"""
        if not MLFLOW_AVAILABLE:
            return True

        try:
            mlflow.sklearn.log_model(model, artifact_path)
            return True
        except Exception as e:
            # 可能是PyTorch模型
            try:
                mlflow.pytorch.log_model(model, artifact_path)
                return True
            except:
                print(f"Log model failed: {e}")
                return False

    def end_run(self, status: str = "FINISHED"):
        """结束运行"""
        if not MLFLOW_AVAILABLE:
            return True

        try:
            mlflow.end_run(status=status)
            return True
        except Exception as e:
            print(f"End run failed: {e}")
            return False

    def get_run(self, run_id: str) -> Dict:
        """获取运行信息"""
        if not MLFLOW_AVAILABLE:
            return {"run_id": run_id, "status": "mock", "metrics": {}, "params": {}}

        try:
            run = mlflow.get_run(run_id)
            return {
                "run_id": run.info.run_id,
                "status": run.info.status,
                "metrics": run.data.metrics,
                "params": run.data.params,
                "start_time": run.info.start_time,
                "end_time": run.info.end_time,
            }
        except Exception as e:
            print(f"Get run failed: {e}")
            return {"error": str(e)}

    def search_runs(
        self, experiment_id: str, filter_str: str = None, max_results: int = 100
    ) -> List[Dict]:
        """搜索运行"""
        if not MLFLOW_AVAILABLE:
            return []

        try:
            runs = mlflow.search_runs(
                experiment_ids=[experiment_id],
                filter_string=filter_str,
                max_results=max_results,
            )

            return [
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "metrics": run.data.metrics,
                    "params": run.data.params,
                    "start_time": run.info.start_time,
                    "end_time": run.info.end_time,
                }
                for run in runs.itertuples()
            ]
        except Exception as e:
            print(f"Search runs failed: {e}")
            return []

    def get_artifacts(self, run_id: str, path: str = None) -> List[Dict]:
        """获取artifacts列表"""
        if not MLFLOW_AVAILABLE or not self._client:
            return []

        try:
            artifacts = self._client.list_artifacts(run_id, path)
            return [
                {
                    "name": artifact.name,
                    "path": artifact.path,
                    "is_dir": artifact.is_dir,
                }
                for artifact in artifacts
            ]
        except Exception as e:
            print(f"Get artifacts failed: {e}")
            return []


# 全局实例
_mlflow_service: Optional[MLflowService] = None


def get_mlflow_service() -> MLflowService:
    """获取MLflow服务"""
    global _mlflow_service
    if _mlflow_service is None:
        _mlflow_service = MLflowService()
    return _mlflow_service
