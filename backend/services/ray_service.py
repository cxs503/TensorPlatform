"""
Ray 分布式训练服务
支持多核/多节点并行训练 + 超参数搜索
"""
import os
import ray
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import cross_val_score


class RayDistributedTrainer:
    """Ray 分布式训练引擎"""

    def __init__(self, num_workers=None, address=None):
        self.num_workers = num_workers or os.cpu_count() or 4
        self.address = address
        self._initialized = False

    def _lazy_init(self):
        if not self._initialized:
            if not ray.is_initialized():
                ray.init(
                    address=self.address,
                    num_cpus=self.num_workers,
                    ignore_reinit_error=True,
                    logging_level="ERROR",
                )
            self._initialized = True

    def shutdown(self):
        if ray.is_initialized():
            ray.shutdown()
        self._initialized = False

    @ray.remote
    def _train_worker(self, X, y, params, cv=3):
        """单 worker 训练"""
        model = RandomForestRegressor(**params, random_state=42, n_jobs=1)
        scores = cross_val_score(model, X, y, cv=cv, scoring="r2")
        return float(scores.mean())

    def parallel_hyperopt(self, X, y, param_grids: list, cv=3):
        """并行超参数搜索 - 多个参数组合同时训练"""
        self._lazy_init()
        X_ref = ray.put(np.asarray(X))
        y_ref = ray.put(np.asarray(y))

        futures = [
            self._train_worker.remote(self, X_ref, y_ref, params, cv)
            for params in param_grids
        ]
        results = ray.get(futures)

        best_idx = int(np.argmax(results))
        return {
            "n_trials": len(param_grids),
            "n_workers": self.num_workers,
            "best_score": results[best_idx],
            "best_params": param_grids[best_idx],
            "all_scores": results,
        }

    def train_parallel_ensembles(self, X, y, n_models=10, base_params=None):
        """并行训练模型集成"""
        self._lazy_init()
        X_ref = ray.put(np.asarray(X))
        y_ref = ray.put(np.asarray(y))

        if base_params is None:
            base_params = {"n_estimators": 100, "max_depth": 10}

        @ray.remote
        def train_single(X, y, seed, params):
            model = RandomForestRegressor(**params, random_state=seed)
            model.fit(X, y)
            return model

        futures = [
            train_single.remote(X_ref, y_ref, i, {**base_params, "random_state": i})
            for i in range(n_models)
        ]
        models = ray.get(futures)

        return {
            "n_models": n_models,
            "n_workers": self.num_workers,
            "base_params": base_params,
        }

    def cluster_info(self):
        """获取集群信息"""
        self._lazy_init()
        nodes = ray.nodes()
        return {
            "num_nodes": len(nodes),
            "num_cpus": int(ray.cluster_resources().get("CPU", 0)),
            "num_gpus": int(ray.cluster_resources().get("GPU", 0)),
            "nodes": [
                {"name": n.get("NodeName", "?"), "alive": n.get("Alive", False)}
                for n in nodes
            ],
        }
