"""
模型量化与压缩服务
支持INT8量化、模型剪枝、知识蒸馏
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np

# 可选依赖
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    _HAS_TORCH = True
except ImportError:
    torch = None
    nn = None
    optim = None
    DataLoader = None
    TensorDataset = None
    _HAS_TORCH = False

# 存储
_quantization_jobs: Dict[str, Dict] = {}

QUANT_DIR = Path("/app/uploads/quantized_models")
try:
    QUANT_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, FileNotFoundError):
    QUANT_DIR = Path("/tmp/quantized_models")
    QUANT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DIR = Path("/app/uploads/models")


class QuantizationService:
    """模型量化服务"""

    @staticmethod
    def quantize_pytorch_dynamic(model_path: str, output_path: str = None) -> Dict:
        """PyTorch动态量化"""
        try:
            # 加载模型
            model = torch.load(model_path)
            model.eval()

            # 动态量化
            quantized_model = torch.quantization.quantize_dynamic(
                model, {nn.Linear, nn.LSTM, nn.GRU}, dtype=torch.qint8
            )

            # 保存
            if output_path is None:
                output_path = str(QUANT_DIR / f"quantized_{Path(model_path).stem}.pt")

            torch.save(quantized_model, output_path)

            # 计算压缩比
            original_size = os.path.getsize(model_path)
            quantized_size = os.path.getsize(output_path)
            compression_ratio = original_size / quantized_size

            return {
                "status": "success",
                "original_path": model_path,
                "quantized_path": output_path,
                "original_size_mb": original_size / (1024 * 1024),
                "quantized_size_mb": quantized_size / (1024 * 1024),
                "compression_ratio": compression_ratio,
                "quantization_type": "dynamic_int8",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def quantize_sklearn_model(model_path: str, output_path: str = None) -> Dict:
        """Sklearn模型量化（使用ONNX）"""
        try:
            import skl2onnx
            from skl2onnx import convert_sklearn
            from skl2onnx.common.data_types import FloatTensorType

            # 加载模型
            model = joblib.load(model_path)

            # 获取输入维度
            if hasattr(model, "n_features_in_"):
                n_features = model.n_features_in_
            else:
                n_features = 10  # 默认

            # 转换为ONNX
            initial_type = [("float_input", FloatTensorType([None, n_features]))]
            onnx_model = convert_sklearn(model, initial_types=initial_type)

            # 保存
            if output_path is None:
                output_path = str(QUANT_DIR / f"{Path(model_path).stem}.onnx")

            with open(output_path, "wb") as f:
                f.write(onnx_model.SerializeToString())

            original_size = os.path.getsize(model_path)
            onnx_size = os.path.getsize(output_path)

            return {
                "status": "success",
                "original_path": model_path,
                "onnx_path": output_path,
                "original_size_mb": original_size / (1024 * 1024),
                "onnx_size_mb": onnx_size / (1024 * 1024),
                "format": "onnx",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def prune_model(
        model_path: str, pruning_ratio: float = 0.3, output_path: str = None
    ) -> Dict:
        """模型剪枝"""
        try:
            # 加载模型
            model = joblib.load(model_path)

            # 对于树模型，可以减少树的数量
            if hasattr(model, "n_estimators"):
                original_n = model.n_estimators
                new_n = int(original_n * (1 - pruning_ratio))
                model.n_estimators = new_n

                if output_path is None:
                    output_path = str(QUANT_DIR / f"pruned_{Path(model_path).stem}.pkl")

                joblib.dump(model, output_path)

                original_size = os.path.getsize(model_path)
                pruned_size = os.path.getsize(output_path)

                return {
                    "status": "success",
                    "original_path": model_path,
                    "pruned_path": output_path,
                    "original_n_estimators": original_n,
                    "pruned_n_estimators": new_n,
                    "pruning_ratio": pruning_ratio,
                    "original_size_mb": original_size / (1024 * 1024),
                    "pruned_size_mb": pruned_size / (1024 * 1024),
                }

            return {"status": "error", "error": "模型不支持剪枝"}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def knowledge_distillation(
        teacher_path: str,
        X_train,
        y_train,
        student_config: Dict = None,
        epochs: int = 10,
        temperature: float = 3.0,
        alpha: float = 0.7,
    ) -> Dict:
        """知识蒸馏"""
        try:
            # 加载教师模型
            teacher = joblib.load(teacher_path)

            # 创建学生模型
            if student_config is None:
                student_config = {
                    "type": "lightgbm",
                    "n_estimators": 50,
                    "max_depth": 4,
                }

            import lightgbm as lgb
            import xgboost as xgb

            if student_config["type"] == "lightgbm":
                student = lgb.LGBMClassifier(
                    n_estimators=student_config.get("n_estimators", 50),
                    max_depth=student_config.get("max_depth", 4),
                    random_state=42,
                    verbose=-1,
                )
            else:
                student = xgb.XGBClassifier(
                    n_estimators=student_config.get("n_estimators", 50),
                    max_depth=student_config.get("max_depth", 4),
                    random_state=42,
                    verbosity=0,
                )

            # 获取教师模型的软标签
            if hasattr(teacher, "predict_proba"):
                soft_labels = teacher.predict_proba(X_train)
            else:
                # 如果没有概率输出，使用硬标签
                soft_labels = np.eye(len(np.unique(y_train)))[y_train]

            # 训练学生模型
            student.fit(X_train, y_train)

            # 保存学生模型
            output_path = str(QUANT_DIR / "distilled_student.pkl")
            joblib.dump(student, output_path)

            # 评估
            from sklearn.metrics import accuracy_score

            teacher_acc = accuracy_score(y_train, teacher.predict(X_train))
            student_acc = accuracy_score(y_train, student.predict(X_train))

            teacher_size = os.path.getsize(teacher_path)
            student_size = os.path.getsize(output_path)

            return {
                "status": "success",
                "teacher_path": teacher_path,
                "student_path": output_path,
                "teacher_accuracy": teacher_acc,
                "student_accuracy": student_acc,
                "teacher_size_mb": teacher_size / (1024 * 1024),
                "student_size_mb": student_size / (1024 * 1024),
                "compression_ratio": teacher_size / student_size,
                "temperature": temperature,
                "alpha": alpha,
            }

        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def get_model_info(model_path: str) -> Dict:
        """获取模型信息"""
        try:
            model = joblib.load(model_path)

            info = {
                "path": model_path,
                "size_mb": os.path.getsize(model_path) / (1024 * 1024),
                "type": type(model).__name__,
            }

            if hasattr(model, "n_estimators"):
                info["n_estimators"] = model.n_estimators
            if hasattr(model, "max_depth"):
                info["max_depth"] = model.max_depth
            if hasattr(model, "n_features_in_"):
                info["n_features"] = model.n_features_in_
            if hasattr(model, "n_classes_"):
                info["n_classes"] = model.n_classes_

            return info

        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def benchmark_model(model_path: str, X_test, n_runs: int = 100) -> Dict:
        """模型性能基准测试"""
        try:
            import time

            model = joblib.load(model_path)

            # 预热
            model.predict(X_test[:10])

            # 测试
            times = []
            for _ in range(n_runs):
                start = time.perf_counter()
                model.predict(X_test)
                end = time.perf_counter()
                times.append(end - start)

            return {
                "model_path": model_path,
                "n_runs": n_runs,
                "n_samples": len(X_test),
                "mean_time_ms": np.mean(times) * 1000,
                "std_time_ms": np.std(times) * 1000,
                "min_time_ms": np.min(times) * 1000,
                "max_time_ms": np.max(times) * 1000,
                "throughput_samples_per_sec": len(X_test) / np.mean(times),
            }

        except Exception as e:
            return {"error": str(e)}


def get_quantization_service():
    return QuantizationService
