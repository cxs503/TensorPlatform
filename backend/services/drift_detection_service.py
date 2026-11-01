"""
模型漂移检测服务
监控模型预测分布变化，检测数据和概念漂移
"""
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from services.drift_metrics import psi as _psi_metric

try:
    from scipy import stats

    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("scipy not available, using mock")


def _to_native(obj):
    """递归将 numpy 标量/数组转为 Python 原生类型,避免 JSON 序列化 TypeError。

    drift_detected 等字段常来自 `np.float64 > threshold` 这类比较,
    结果是 numpy.bool_,FastAPI/JSON 序列化会抛 TypeError。
    """
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    if isinstance(obj, np.generic):
        # numpy.bool_ / numpy.float64 / numpy.int64 -> python bool/float/int
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


class DriftDetector:
    """漂移检测器"""

    def __init__(self, detector_type: str = "ks", threshold: float = 0.1):
        self.detector_type = detector_type
        self.threshold = threshold
        self.reference_data: List[float] = []
        self.baseline_stats: Dict = {}

    def set_baseline(self, data: List[float]):
        """设置基准数据"""
        self.reference_data = data
        if SCIPY_AVAILABLE:
            self.baseline_stats = {
                "mean": float(np.mean(data)),
                "std": float(np.std(data)),
                "median": float(np.median(data)),
                "q25": float(np.percentile(data, 25)),
                "q75": float(np.percentile(data, 75)),
            }
        else:
            self.baseline_stats = {
                "mean": sum(data) / len(data),
                "std": (sum((x - sum(data) / len(data)) ** 2 for x in data) / len(data))
                ** 0.5,
            }

    def detect(self, data: List[float]) -> Dict[str, Any]:
        """检测漂移"""
        if not self.reference_data:
            return {"drift_detected": False, "error": "No baseline set"}

        if not SCIPY_AVAILABLE:
            return self._mock_detect(data)

        if self.detector_type == "ks":
            return self._ks_test(data)
        elif self.detector_type == "psi":
            return self._psi_test(data)
        elif self.detector_type == "ddm":
            return self._ddm_test(data)
        else:
            return {"drift_detected": False, "error": "Unknown detector type"}

    def _ks_test(self, data: List[float]) -> Dict[str, Any]:
        """Kolmogorov-Smirnov检验"""
        statistic, p_value = stats.ks_2samp(self.reference_data, data)

        drift_detected = bool(p_value < self.threshold)

        return _to_native({
            "drift_detected": drift_detected,
            "method": "KS Test",
            "statistic": float(statistic),
            "p_value": float(p_value),
            "threshold": self.threshold,
            "current_stats": {
                "mean": float(np.mean(data)),
                "std": float(np.std(data)),
            },
            "baseline_stats": self.baseline_stats,
        })

    def _psi_test(self, data: List[float], bins: int = 10) -> Dict[str, Any]:
        """Population Stability Index —— 统一委托 services.drift_metrics.psi。

        分箱边界由基准样本(reference_data)单次确定并复用，保证跨窗口可比；
        旧的"联合 min/max 等宽箱 + 1e-4 兜底"会随当前窗口自我重塑分箱，
        与标准定义偏差最高 +2792%（见 docs/reports/DRIFT_METRICS_AUDIT.md）。
        """
        psi = float(_psi_metric(self.reference_data, data, bins=bins))
        drift_detected = bool(psi > self.threshold)
        return _to_native({
            "drift_detected": drift_detected,
            "method": "PSI",
            "psi": psi,
            "threshold": self.threshold,
            "interpretation": "Significant drift"
            if drift_detected
            else "No significant drift",
        })

    def _ddm_test(self, data: List[float]) -> Dict[str, Any]:
        """Drift Detection Method (DDM)"""
        # 简化的DDM实现
        mean = np.mean(data)
        std = np.std(data)

        # 与基准比较
        drift_score = abs(mean - self.baseline_stats.get("mean", 0)) / (std + 1e-10)

        drift_detected = bool(drift_score > self.threshold)

        return _to_native({
            "drift_detected": drift_detected,
            "method": "DDM",
            "drift_score": float(drift_score),
            "threshold": self.threshold,
            "current_mean": float(mean),
            "baseline_mean": self.baseline_stats.get("mean"),
        })

    def _mock_detect(self, data: List[float]) -> Dict[str, Any]:
        """模拟检测(scipy 不可用时的兜底,结果非真实漂移分析)"""
        import random

        drift_detected = bool(random.random() < 0.1)

        return _to_native({
            "drift_detected": drift_detected,
            "method": "Mock",
            "threshold": self.threshold,
            "mode": "mock",
            "note": "演示模式,结果非真实漂移分析(scipy 未安装,无法执行 KS/PSI/DDM 统计检验)",
        })


class DriftDetectionService:
    """模型漂移检测服务"""

    def __init__(self):
        self.detectors: Dict[str, Dict[str, DriftDetector]] = defaultdict(dict)
        self.predictions_history: Dict[str, List[Dict]] = defaultdict(list)
        self.alerts: List[Dict] = []
        self.max_history = 10000

    def register_model(
        self,
        model_id: str,
        feature_name: str,
        detector_type: str = "ks",
        threshold: float = 0.1,
    ):
        """注册模型特征检测器"""
        detector = DriftDetector(detector_type, threshold)
        self.detectors[model_id][feature_name] = detector

    def set_baseline(
        self, model_id: str, feature_name: str, baseline_data: List[float]
    ):
        """设置基准数据"""
        if model_id in self.detectors and feature_name in self.detectors[model_id]:
            self.detectors[model_id][feature_name].set_baseline(baseline_data)
            return True
        return False

    def record_prediction(
        self,
        model_id: str,
        prediction: float,
        features: Dict[str, float] = None,
        timestamp: str = None,
    ):
        """记录预测结果"""
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        self.predictions_history[model_id].append(
            {
                "prediction": prediction,
                "features": features or {},
                "timestamp": timestamp,
            }
        )

        # 限制历史大小
        if len(self.predictions_history[model_id]) > self.max_history:
            self.predictions_history[model_id] = self.predictions_history[model_id][
                -self.max_history :
            ]

    def detect_drift(self, model_id: str, window_size: int = 1000) -> Dict[str, Any]:
        """检测漂移"""
        if model_id not in self.predictions_history:
            return {"drift_detected": False, "error": "No predictions recorded"}

        history = self.predictions_history[model_id]
        if len(history) < window_size:
            return {"drift_detected": False, "error": "Insufficient data"}

        # 获取最近窗口的数据
        recent_data = history[-window_size:]

        # 检测预测值漂移
        predictions = [p["prediction"] for p in recent_data]

        # 检测各特征漂移
        feature_results = {}
        if recent_data[0].get("features"):
            feature_names = recent_data[0]["features"].keys()
            for feature_name in feature_names:
                feature_values = [
                    p["features"].get(feature_name, 0) for p in recent_data
                ]

                if (
                    model_id in self.detectors
                    and feature_name in self.detectors[model_id]
                ):
                    detector = self.detectors[model_id][feature_name]
                    result = detector.detect(feature_values)
                    feature_results[feature_name] = result

        # 整体漂移判断
        any_drift = any(
            r.get("drift_detected", False) for r in feature_results.values()
        )

        return _to_native({
            "model_id": model_id,
            "drift_detected": any_drift,
            "timestamp": datetime.now().isoformat(),
            "window_size": window_size,
            "total_predictions": len(history),
            "feature_drift": feature_results,
        })

    def get_prediction_stats(
        self, model_id: str, window_size: int = 100
    ) -> Dict[str, Any]:
        """获取预测统计"""
        if model_id not in self.predictions_history:
            return {"error": "No predictions"}

        history = self.predictions_history[model_id]
        recent = history[-window_size:]

        predictions = [p["prediction"] for p in recent]

        return _to_native({
            "model_id": model_id,
            "count": len(predictions),
            "mean": float(np.mean(predictions)) if predictions else 0,
            "std": float(np.std(predictions)) if predictions else 0,
            "min": float(np.min(predictions)) if predictions else 0,
            "max": float(np.max(predictions)) if predictions else 0,
        })

    def get_alerts(self, model_id: str = None, limit: int = 100) -> List[Dict]:
        """获取告警"""
        alerts = self.alerts
        if model_id:
            alerts = [a for a in alerts if a.get("model_id") == model_id]
        return alerts[-limit:]


# 全局实例
_drift_service: Optional[DriftDetectionService] = None


def get_drift_detection_service() -> DriftDetectionService:
    """获取漂移检测服务"""
    global _drift_service
    if _drift_service is None:
        _drift_service = DriftDetectionService()
    return _drift_service
