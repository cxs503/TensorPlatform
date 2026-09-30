"""
A/B测试服务
支持模型版本对比、流量分配、统计分析、自动切换
"""
import hashlib
import json
import os
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# 存储
_ab_experiments: Dict[str, Dict] = {}
_ab_results: Dict[str, List] = {}
_traffic_rules: Dict[str, Dict] = {}

# 目录（在非Docker环境下回退到临时目录）
try:
    ABTEST_DIR = Path("/app/uploads/ab_tests")
    ABTEST_DIR.mkdir(parents=True, exist_ok=True)
    (ABTEST_DIR / "experiments").mkdir(exist_ok=True)
    (ABTEST_DIR / "results").mkdir(exist_ok=True)
    MODEL_DIR = Path("/app/uploads/models")
except (PermissionError, FileNotFoundError, OSError):
    import tempfile
    _tmpdir = Path(tempfile.gettempdir()) / "foundation_abtest"
    ABTEST_DIR = _tmpdir
    ABTEST_DIR.mkdir(parents=True, exist_ok=True)
    (ABTEST_DIR / "experiments").mkdir(exist_ok=True)
    (ABTEST_DIR / "results").mkdir(exist_ok=True)
    MODEL_DIR = _tmpdir


class ABExperiment:
    """A/B测试实验"""

    def __init__(self, experiment_id: str, config: Dict):
        self.experiment_id = experiment_id
        self.name = config.get("name", "未命名实验")
        self.description = config.get("description", "")
        self.config = config

        # 模型配置
        self.model_a = config.get("model_a")
        self.model_b = config.get("model_b")

        # 流量分配
        self.traffic_split = config.get("traffic_split", {"a": 50, "b": 50})

        # 指标配置
        self.primary_metric = config.get("primary_metric", "accuracy")
        self.secondary_metrics = config.get("secondary_metrics", ["latency"])

        # 统计配置
        self.significance_level = config.get("significance_level", 0.05)
        self.min_sample_size = config.get("min_sample_size", 100)

        # 状态
        self.status = "created"
        self.winner = None
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.ended_at = None

        # 结果收集
        self.results_a: List[Dict] = []
        self.results_b: List[Dict] = []

        # 统计结果
        self.statistics = {
            "a": {"samples": 0, "metrics": {}},
            "b": {"samples": 0, "metrics": {}},
        }

        # 自动切换配置
        self.auto_switch = config.get("auto_switch", False)
        self.auto_switch_threshold = config.get("auto_switch_threshold", 0.95)

    def to_dict(self) -> Dict:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "description": self.description,
            "config": self.config,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "traffic_split": self.traffic_split,
            "primary_metric": self.primary_metric,
            "secondary_metrics": self.secondary_metrics,
            "significance_level": self.significance_level,
            "min_sample_size": self.min_sample_size,
            "status": self.status,
            "winner": self.winner,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "statistics": self.statistics,
            "auto_switch": self.auto_switch,
            "auto_switch_threshold": self.auto_switch_threshold,
        }


class TrafficRouter:
    """流量路由器"""

    @staticmethod
    def assign_variant(experiment_id: str, user_id: str = None) -> str:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment or experiment.status != "running":
            return "a"

        if user_id:
            hash_value = int(
                hashlib.md5(f"{experiment_id}:{user_id}".encode()).hexdigest(), 16
            )
            ratio = (hash_value % 100) / 100
        else:
            ratio = random.random()

        split = experiment.traffic_split
        a_ratio = split.get("a", 50) / 100

        return "a" if ratio < a_ratio else "b"

    @staticmethod
    def get_model_for_variant(experiment_id: str, variant: str) -> str:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            return None

        return experiment.model_a if variant == "a" else experiment.model_b


class StatisticsAnalyzer:
    """统计分析器"""

    @staticmethod
    def calculate_statistics(results: List[Dict], metric: str) -> Dict:
        if not results:
            return {"mean": 0, "std": 0, "count": 0}

        values = [r.get(metric, 0) for r in results if metric in r]

        if not values:
            return {"mean": 0, "std": 0, "count": 0}

        return {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "count": len(values),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "median": float(np.median(values)),
        }

    @staticmethod
    def t_test(results_a: List[Dict], results_b: List[Dict], metric: str) -> Dict:
        try:
            from scipy import stats
        except:
            return {
                "significant": False,
                "p_value": 1.0,
                "message": "scipy not available",
            }

        values_a = [r.get(metric, 0) for r in results_a if metric in r]
        values_b = [r.get(metric, 0) for r in results_b if metric in r]

        if len(values_a) < 2 or len(values_b) < 2:
            return {"significant": False, "p_value": 1.0, "message": "样本不足"}

        t_stat, p_value = stats.ttest_ind(values_a, values_b)

        return {
            "t_statistic": float(t_stat),
            "p_value": float(p_value),
            "significant": p_value < 0.05,
            "effect_size": float(
                (np.mean(values_b) - np.mean(values_a))
                / max(np.std(values_a + values_b), 0.001)
            ),
        }

    @staticmethod
    def proportion_test(
        successes_a: int, total_a: int, successes_b: int, total_b: int
    ) -> Dict:
        try:
            from scipy import stats
        except:
            return {"significant": False, "p_value": 1.0}

        if total_a == 0 or total_b == 0:
            return {"significant": False, "p_value": 1.0}

        p_a = successes_a / total_a
        p_b = successes_b / total_b
        p_pooled = (successes_a + successes_b) / (total_a + total_b)

        se = np.sqrt(p_pooled * (1 - p_pooled) * (1 / total_a + 1 / total_b))

        if se == 0:
            return {"significant": False, "p_value": 1.0}

        z_stat = (p_b - p_a) / se
        p_value = 2 * (1 - stats.norm.cdf(abs(z_stat)))

        return {
            "z_statistic": float(z_stat),
            "p_value": float(p_value),
            "significant": p_value < 0.05,
            "lift": float((p_b - p_a) / p_a * 100) if p_a > 0 else 0,
            "conversion_a": float(p_a),
            "conversion_b": float(p_b),
        }


class ABTestService:
    """A/B测试主服务"""

    @staticmethod
    def create_experiment(config: Dict) -> str:
        import uuid

        experiment_id = f"exp_{uuid.uuid4().hex[:8]}"

        experiment = ABExperiment(experiment_id, config)
        _ab_experiments[experiment_id] = experiment

        ABTestService._save_experiment(experiment)

        return experiment_id

    @staticmethod
    def start_experiment(experiment_id: str) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        experiment.status = "running"
        experiment.started_at = datetime.now().isoformat()

        ABTestService._save_experiment(experiment)

        return {"experiment_id": experiment_id, "status": "running"}

    @staticmethod
    def pause_experiment(experiment_id: str) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        experiment.status = "paused"
        ABTestService._save_experiment(experiment)

        return {"experiment_id": experiment_id, "status": "paused"}

    @staticmethod
    def end_experiment(experiment_id: str, winner: str = None) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        experiment.status = "completed"
        experiment.ended_at = datetime.now().isoformat()

        if winner:
            experiment.winner = winner

        ABTestService._save_experiment(experiment)

        return {"experiment_id": experiment_id, "status": "completed", "winner": winner}

    @staticmethod
    def record_result(
        experiment_id: str, variant: str, result: Dict, user_id: str = None
    ):
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        result_record = {
            "timestamp": datetime.now().isoformat(),
            "variant": variant,
            "user_id": user_id,
            **result,
        }

        if variant == "a":
            experiment.results_a.append(result_record)
        else:
            experiment.results_b.append(result_record)

        ABTestService._update_statistics(experiment)
        ABTestService._save_experiment(experiment)

    @staticmethod
    def _update_statistics(experiment: ABExperiment):
        for variant, results in [
            ("a", experiment.results_a),
            ("b", experiment.results_b),
        ]:
            experiment.statistics[variant]["samples"] = len(results)

            for metric in [experiment.primary_metric] + experiment.secondary_metrics:
                experiment.statistics[variant]["metrics"][
                    metric
                ] = StatisticsAnalyzer.calculate_statistics(results, metric)

    @staticmethod
    def analyze_experiment(experiment_id: str) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        results_a = experiment.results_a
        results_b = experiment.results_b

        stats_a = experiment.statistics["a"]
        stats_b = experiment.statistics["b"]

        primary_metric = experiment.primary_metric

        # 判断指标类型
        if primary_metric in ["conversion", "click_rate", "success_rate"]:
            successes_a = sum(1 for r in results_a if r.get(primary_metric, 0) == 1)
            successes_b = sum(1 for r in results_b if r.get(primary_metric, 0) == 1)

            test_result = StatisticsAnalyzer.proportion_test(
                successes_a, len(results_a), successes_b, len(results_b)
            )
        else:
            test_result = StatisticsAnalyzer.t_test(
                results_a, results_b, primary_metric
            )

        # 确定胜者
        winner = None
        if test_result.get("significant"):
            mean_a = stats_a["metrics"].get(primary_metric, {}).get("mean", 0)
            mean_b = stats_b["metrics"].get(primary_metric, {}).get("mean", 0)

            if mean_b > mean_a:
                winner = "b"
            else:
                winner = "a"

        confidence = 1 - test_result.get("p_value", 1)
        conclusion = "significant" if test_result.get("significant") else "inconclusive"

        return {
            "experiment_id": experiment_id,
            "status": experiment.status,
            "samples": {"a": stats_a["samples"], "b": stats_b["samples"]},
            "primary_metric": primary_metric,
            "statistics": {
                "a": stats_a["metrics"].get(primary_metric, {}),
                "b": stats_b["metrics"].get(primary_metric, {}),
            },
            "test_result": test_result,
            "winner": winner,
            "confidence": confidence,
            "conclusion": conclusion,
        }

    @staticmethod
    def get_experiment(experiment_id: str) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            exp_file = ABTEST_DIR / "experiments" / f"{experiment_id}.json"
            if exp_file.exists():
                with open(exp_file) as f:
                    data = json.load(f)
                    experiment = ABExperiment(experiment_id, data.get("config", {}))
                    experiment.__dict__.update(data)
                    _ab_experiments[experiment_id] = experiment
                    return experiment.to_dict()
            raise ValueError(f"Experiment {experiment_id} not found")

        return experiment.to_dict()

    @staticmethod
    def list_experiments(status: str = None) -> List[Dict]:
        experiments = []
        for exp_file in (ABTEST_DIR / "experiments").glob("*.json"):
            with open(exp_file) as f:
                data = json.load(f)
                if status is None or data.get("status") == status:
                    experiments.append(data)

        return sorted(experiments, key=lambda x: x["created_at"], reverse=True)

    @staticmethod
    def _save_experiment(experiment: ABExperiment):
        exp_file = ABTEST_DIR / "experiments" / f"{experiment.experiment_id}.json"
        with open(exp_file, "w") as f:
            json.dump(experiment.to_dict(), f, indent=2, default=str)

    @staticmethod
    def predict_with_ab_test(experiment_id: str, X: List, user_id: str = None) -> Dict:
        experiment = _ab_experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        if experiment.status != "running":
            variant = experiment.winner if experiment.winner else "a"
        else:
            variant = TrafficRouter.assign_variant(experiment_id, user_id)

        model_id = TrafficRouter.get_model_for_variant(experiment_id, variant)

        import joblib
        import numpy as np

        model_path = MODEL_DIR / f"{model_id}_model.pkl"
        if not model_path.exists():
            model_path = MODEL_DIR / f"{model_id}.pkl"

        if model_path.exists():
            model = joblib.load(model_path)
            predictions = model.predict(np.array(X)).tolist()
        else:
            predictions = [random.random() for _ in X]

        return {
            "experiment_id": experiment_id,
            "variant": variant,
            "model_id": model_id,
            "predictions": predictions,
        }

    @staticmethod
    def calculate_sample_size(baseline_rate: float, min_effect: float = 0.05) -> Dict:
        try:
            from scipy import stats

            z_alpha = stats.norm.ppf(0.975)
            z_beta = stats.norm.ppf(0.8)

            p1 = baseline_rate
            p2 = baseline_rate * (1 + min_effect)

            n = (
                (
                    z_alpha * np.sqrt(2 * p1 * (1 - p1))
                    + z_beta * np.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
                )
                ** 2
            ) / ((p2 - p1) ** 2)

            return {
                "baseline_rate": baseline_rate,
                "min_effect": min_effect,
                "sample_per_variant": int(np.ceil(n)),
                "total_sample": int(np.ceil(n * 2)),
            }
        except:
            return {"sample_per_variant": 1000, "total_sample": 2000}


def get_abtest_service():
    return ABTestService
