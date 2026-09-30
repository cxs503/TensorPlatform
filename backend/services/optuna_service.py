"""
Optuna 超参数优化服务
与算法商店 + 训练服务对接
"""
import json
import time
import numpy as np
import optuna
from optuna.samplers import TPESampler
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.model_selection import cross_val_score
from xgboost import XGBRegressor, XGBClassifier


class OptunaHyperOpt:
    """Optuna 超参数优化引擎"""

    def __init__(self, n_trials=30, timeout=300, direction="maximize"):
        self.n_trials = n_trials
        self.timeout = timeout
        self.direction = direction
        self.study = None
        self.best_params = None
        self.best_score = None

    def optimize(self, X, y, model_type="random_forest", task_type="regression",
                 cv=3, scoring=None, n_jobs=-1):
        """运行超参数优化"""
        X = np.asarray(X)
        y = np.asarray(y)

        if scoring is None:
            scoring = "r2" if task_type == "regression" else "accuracy"

        sampler = TPESampler(seed=42)
        self.study = optuna.create_study(
            direction=self.direction,
            sampler=sampler,
            study_name=f"optuna_{model_type}_{task_type}"
        )

        def objective(trial):
            if model_type == "random_forest":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 500),
                    "max_depth": trial.suggest_int("max_depth", 3, 30),
                    "min_samples_split": trial.suggest_int("min_samples_split", 2, 20),
                    "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 10),
                    "max_features": trial.suggest_categorical("max_features", ["sqrt", "log2", None]),
                }
                if task_type == "regression":
                    model = RandomForestRegressor(**params, random_state=42, n_jobs=n_jobs)
                else:
                    model = RandomForestClassifier(**params, random_state=42, n_jobs=n_jobs)

            elif model_type == "xgboost":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 500),
                    "max_depth": trial.suggest_int("max_depth", 3, 15),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                    "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                    "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                    "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                }
                if task_type == "regression":
                    model = XGBRegressor(**params, random_state=42, n_jobs=n_jobs)
                else:
                    model = XGBClassifier(**params, random_state=42, n_jobs=n_jobs)
            else:
                raise ValueError(f"Unsupported model_type: {model_type}")

            scores = cross_val_score(model, X, y, cv=cv, scoring=scoring, n_jobs=n_jobs)
            return scores.mean()

        start_time = time.time()
        self.study.optimize(
            objective,
            n_trials=self.n_trials,
            timeout=self.timeout,
            n_jobs=1,
            show_progress_bar=False,
        )

        self.best_params = self.study.best_params
        self.best_score = self.study.best_value
        elapsed = time.time() - start_time

        return {
            "best_params": self.best_params,
            "best_score": float(self.best_score),
            "n_trials": len(self.study.trials),
            "elapsed_seconds": round(elapsed, 1),
            "direction": self.direction,
            "model_type": model_type,
            "task_type": task_type,
            "trials": [
                {
                    "number": t.number,
                    "value": float(t.value) if t.value else None,
                    "params": t.params,
                    "state": str(t.state),
                }
                for t in self.study.trials[-10:]  # last 10 trials
            ],
        }
