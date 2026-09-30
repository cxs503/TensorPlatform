"""
ONNX模型导出与推理加速服务

功能:
1. Sklearn/LightGBM/XGBoost 模型转 ONNX 格式
2. PyTorch 模型转 ONNX 格式
3. ONNX Runtime 推理服务
4. sklearn vs ONNX 推理速度对比

路径规范: 所有路径使用 /app/... （Docker容器内部路径）
"""

import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import numpy as np
import sklearn

logger = logging.getLogger(__name__)

# ============================================================
# 可选依赖检测
# ============================================================

try:
    import onnx
    ONNX_AVAILABLE = True
except ImportError:
    onnx = None
    ONNX_AVAILABLE = False

try:
    import onnxruntime as ort
    ONNXRUNTIME_AVAILABLE = True
except ImportError:
    ort = None
    ONNXRUNTIME_AVAILABLE = False

try:
    import skl2onnx
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType
    SKL2ONNX_AVAILABLE = True
except ImportError:
    skl2onnx = None
    convert_sklearn = None
    FloatTensorType = None
    SKL2ONNX_AVAILABLE = False

try:
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm as _onnxml_convert_lightgbm
    from onnxmltools.convert import convert_xgboost as _onnxml_convert_xgboost
    from onnxmltools.convert.common.data_types import FloatTensorType as OnnxMLFloatTensorType
    ONNXMLTOOLS_AVAILABLE = True
except ImportError:
    onnxmltools = None
    OnnxMLFloatTensorType = None
    ONNXMLTOOLS_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    nn = None
    TORCH_AVAILABLE = False

try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    lgb = None
    LIGHTGBM_AVAILABLE = False

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    xgb = None
    XGBOOST_AVAILABLE = False

# ============================================================
# 模型存储路径
# ============================================================
ONNX_MODEL_DIR = Path("/app/uploads/models")
ONNX_MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Sklearn 模型导出器
# ============================================================

class SklearnModelExporter:
    """将 sklearn/LightGBM/XGBoost 模型转换为 ONNX 格式"""

    @staticmethod
    def convert_sklearn_to_onnx(
        model: Any,
        input_dim: int,
        output_dim: int = 1,
        onnx_filename: Optional[str] = None,
        model_dir: Optional[Path] = None,
    ) -> str:
        """
        将 sklearn 模型转换为 ONNX 并保存。

        参数:
            model: sklearn/LightGBM/XGBoost 模型对象
            input_dim: 输入特征维度
            output_dim: 输出维度（回归=1，分类=类别数）
            onnx_filename: 输出 .onnx 文件名（不含扩展名）
            model_dir: 输出目录（默认 /app/uploads/models）

        返回:
            str: 保存的 .onnx 文件路径
        """
        if not ONNX_AVAILABLE:
            raise RuntimeError("onnx 包未安装，请执行: pip install onnx")
        if not ONNXRUNTIME_AVAILABLE:
            raise RuntimeError("onnxruntime 包未安装，请执行: pip install onnxruntime")

        save_dir = model_dir or ONNX_MODEL_DIR
        save_dir.mkdir(parents=True, exist_ok=True)

        if onnx_filename is None:
            import uuid
            onnx_filename = f"sklearn_{uuid.uuid4().hex[:8]}"

        onnx_path = str(save_dir / f"{onnx_filename}.onnx")
        initial_type = [("float_input", FloatTensorType([None, input_dim]))]

        # 判断模型类型
        model_type = SklearnModelExporter._detect_model_type(model)

        if model_type == "lightgbm" and ONNXMLTOOLS_AVAILABLE and OnnxMLFloatTensorType:
            # 使用 onnxmltools 转换 LightGBM
            logger.info("使用 onnxmltools 转换 LightGBM 模型...")
            onnx_model = onnxmltools.convert_lightgbm(
                model,
                initial_types=[("float_input", OnnxMLFloatTensorType([None, input_dim]))],
            )
        elif model_type == "xgboost" and ONNXMLTOOLS_AVAILABLE and OnnxMLFloatTensorType:
            # 使用 onnxmltools 转换 XGBoost
            logger.info("使用 onnxmltools 转换 XGBoost 模型...")
            onnx_model = onnxmltools.convert_xgboost(
                model,
                initial_types=[("float_input", OnnxMLFloatTensorType([None, input_dim]))],
            )
        elif SKL2ONNX_AVAILABLE:
            # 使用 skl2onnx 转换 sklearn 模型（包括 RandomForest）
            logger.info(f"使用 skl2onnx 转换 {model_type} 模型...")
            onnx_model = convert_sklearn(model, initial_types=initial_type)
        elif ONNXMLTOOLS_AVAILABLE:
            # 使用 onnxmltools 作为备选
            logger.info(f"使用 onnxmltools 转换 {model_type} 模型（备选方案）...")
            onnx_model = onnxmltools.convert_sklearn(
                model,
                initial_types=[("float_input", OnnxMLFloatTensorType([None, input_dim]))],
            )
        else:
            raise RuntimeError(
                "需要 skl2onnx 或 onnxmltools 来转换模型。"
                "请执行: pip install skl2onnx 或 pip install onnxmltools"
            )

        # 保存 ONNX 模型
        onnx.save_model(onnx_model, onnx_path)
        logger.info(f"ONNX 模型已保存到: {onnx_path}")

        # 验证导出的模型
        onnx.checker.check_model(onnx_path)
        logger.info("ONNX 模型验证通过")

        return onnx_path

    @staticmethod
    def _detect_model_type(model: Any) -> str:
        """检测模型类型"""
        # 检查 LightGBM
        if LIGHTGBM_AVAILABLE and isinstance(model, lgb.LGBMModel):
            return "lightgbm"
        if LIGHTGBM_AVAILABLE and hasattr(model, "booster_"):
            return "lightgbm"

        # 检查 XGBoost
        if XGBOOST_AVAILABLE and isinstance(model, (xgb.XGBModel, xgb.Booster)):
            return "xgboost"

        # 检查 Booster 类（lightgbm 或 xgboost 都可能）
        model_class_name = type(model).__name__
        if "LGBM" in model_class_name.upper():
            return "lightgbm"
        if "XGB" in model_class_name.upper():
            return "xgboost"

        # 默认当作 sklearn 模型处理
        return "sklearn"


# ============================================================
# PyTorch 模型导出器
# ============================================================

class PyTorchModelExporter:
    """将 PyTorch 模型转换为 ONNX 格式"""

    @staticmethod
    def convert_pytorch_to_onnx(
        model: Any,
        input_dim: int,
        output_path: str,
        dynamic_batch: bool = True,
        opset_version: int = 14,
    ) -> str:
        """
        将 PyTorch 模型导出为 ONNX 格式。

        参数:
            model: PyTorch nn.Module 模型
            input_dim: 输入特征维度
            output_path: 输出 .onnx 文件路径
            dynamic_batch: 是否使用动态 batch size（默认 True）
            opset_version: ONNX opset 版本（默认 14）

        返回:
            str: 保存的 .onnx 文件路径
        """
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch 未安装")
        if not ONNX_AVAILABLE:
            raise RuntimeError("onnx 包未安装，请执行: pip install onnx")

        model.eval()

        # 创建示例输入
        dummy_input = torch.randn(1, input_dim)

        # 设置动态轴
        dynamic_axes = None
        if dynamic_batch:
            dynamic_axes = {
                "input": {0: "batch_size"},
                "output": {0: "batch_size"},
            }

        # 确保输出路径以 .onnx 结尾
        if not output_path.endswith(".onnx"):
            output_path += ".onnx"

        # 确保目录存在
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        logger.info(f"导出 PyTorch 模型到 ONNX: {output_path}")
        torch.onnx.export(
            model,
            dummy_input,
            output_path,
            export_params=True,
            opset_version=opset_version,
            do_constant_folding=True,
            input_names=["input"],
            output_names=["output"],
            dynamic_axes=dynamic_axes,
        )

        # 验证导出的模型
        onnx_model = onnx.load(output_path)
        onnx.checker.check_model(onnx_model)
        logger.info(f"ONNX 模型验证通过: {output_path}")

        return output_path

    @staticmethod
    def export_from_job_id(
        job_id: str,
        output_path: Optional[str] = None,
    ) -> str:
        """
        从训练任务ID加载 PyTorch 模型并导出为 ONNX。

        参数:
            job_id: 训练任务ID（如 train_abc123）
            output_path: 输出路径（默认 /app/uploads/models/{job_id}.onnx）

        返回:
            str: 保存的 .onnx 文件路径
        """
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch 未安装")

        import json

        # 加载模型配置
        config_path = ONNX_MODEL_DIR / f"{job_id}_config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"PyTorch 模型配置文件不存在: {config_path}")

        with open(config_path) as f:
            pt_config = json.load(f)

        # 加载权重
        pt_path = ONNX_MODEL_DIR / f"{job_id}_model.pt"
        if not pt_path.exists():
            raise FileNotFoundError(f"PyTorch 模型文件不存在: {pt_path}")

        try:
            state_dict = torch.load(str(pt_path), map_location="cpu", weights_only=True)
        except TypeError:
            state_dict = torch.load(str(pt_path), map_location="cpu")

        # 权重文件是训练结果的唯一可信结构来源。早期任务可能保存了过期的
        # config.json（例如配置写 [64, 32]，权重实际为 [128, 64]）；直接按
        # 配置重建会导致 ONNX/Docker 都无法导出。
        linear_weights = [
            value for key, value in state_dict.items()
            if key.endswith(".weight") and getattr(value, "ndim", 0) == 2
        ]
        if not linear_weights:
            raise RuntimeError("PyTorch权重中未找到线性层，无法推断ONNX导出结构")
        inferred_input_dim = int(linear_weights[0].shape[1])
        inferred_output_dim = int(linear_weights[-1].shape[0])
        inferred_hidden_layers = [int(weight.shape[0]) for weight in linear_weights[:-1]]
        configured_layers = pt_config.get("hidden_layers", [128, 64])
        if (
            int(pt_config.get("input_dim", inferred_input_dim)) != inferred_input_dim
            or int(pt_config.get("output_dim", inferred_output_dim)) != inferred_output_dim
            or list(configured_layers) != inferred_hidden_layers
        ):
            logger.warning(
                "PyTorch配置与权重结构不一致，ONNX导出使用权重推断结构: "
                "job=%s config=(%s,%s,%s) weights=(%s,%s,%s)",
                job_id, pt_config.get("input_dim"), pt_config.get("output_dim"), configured_layers,
                inferred_input_dim, inferred_output_dim, inferred_hidden_layers,
            )
        input_dim = inferred_input_dim
        output_dim = inferred_output_dim
        hidden_layers = inferred_hidden_layers
        dropout = pt_config.get("dropout", 0.2)

        # 重建模型结构后再加载权重。当前平台的原生 MLP 采用
        # Linear → ReLU → Dropout 重复结构，最后接一个输出 Linear。
        layers = []
        prev_dim = input_dim
        for hidden_dim in hidden_layers:
            layers.extend([
                nn.Linear(prev_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
            ])
            prev_dim = hidden_dim
        layers.append(nn.Linear(prev_dim, output_dim))
        model = nn.Sequential(*layers)
        model.load_state_dict(state_dict)
        model.eval()

        # 导出
        if output_path is None:
            output_path = str(ONNX_MODEL_DIR / f"{job_id}.onnx")

        return PyTorchModelExporter.convert_pytorch_to_onnx(
            model, input_dim, output_path
        )


# ============================================================
# ONNX 推理服务
# ============================================================

class ONNXInferenceService:
    """ONNX Runtime 推理服务，支持模型加载、推理和性能对比"""

    def __init__(self):
        self._sessions: Dict[str, Any] = {}

    def load_model(self, model_path: str, session_id: Optional[str] = None) -> Any:
        """
        加载 ONNX 模型，创建 ONNX Runtime 推理会话。

        参数:
            model_path: .onnx 模型文件路径
            session_id: 会话标识（默认使用模型路径）

        返回:
            ort.InferenceSession: ONNX Runtime 推理会话
        """
        if not ONNXRUNTIME_AVAILABLE:
            raise RuntimeError("onnxruntime 未安装")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"ONNX 模型文件不存在: {model_path}")

        sid = session_id or model_path

        if sid in self._sessions:
            logger.info(f"使用已缓存的推理会话: {sid}")
            return self._sessions[sid]

        logger.info(f"加载 ONNX 模型: {model_path}")

        # 创建推理会话
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        # 设置线程数
        sess_options.intra_op_num_threads = os.cpu_count() or 4

        session = ort.InferenceSession(
            model_path,
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        self._sessions[sid] = session

        # 打印模型信息
        input_name = session.get_inputs()[0].name
        input_shape = session.get_inputs()[0].shape
        output_name = session.get_outputs()[0].name
        output_shape = session.get_outputs()[0].shape
        logger.info(
            f"ONNX 模型加载成功 - 输入: {input_name}{input_shape}, "
            f"输出: {output_name}{output_shape}"
        )

        return session

    def predict(
        self,
        session: Any,
        X: np.ndarray,
        input_name: Optional[str] = None,
    ) -> np.ndarray:
        """
        使用 ONNX Runtime 进行推理。

        参数:
            session: ort.InferenceSession
            X: 输入特征数组，shape (n_samples, n_features)
            input_name: 输入节点名称（默认自动获取）

        返回:
            np.ndarray: 预测结果
        """
        if not ONNXRUNTIME_AVAILABLE:
            raise RuntimeError("onnxruntime 未安装")

        # 确保 X 是 float32
        if X.dtype != np.float32:
            X = X.astype(np.float32)

        if input_name is None:
            input_name = session.get_inputs()[0].name

        output_name = session.get_outputs()[0].name

        result = session.run([output_name], {input_name: X})
        predictions = np.array(result[0])

        # 如果是 (n, 1) 形状，压平为 (n,)
        if predictions.ndim == 2 and predictions.shape[1] == 1:
            predictions = predictions.flatten()

        return predictions

    def benchmark(
        self,
        sklearn_path: str,
        onnx_path: str,
        X: np.ndarray,
        n_runs: int = 100,
    ) -> Dict[str, Any]:
        """
        对比 sklearn 模型与 ONNX 模型的推理速度。

        参数:
            sklearn_path: sklearn .pkl 模型文件路径
            onnx_path: ONNX .onnx 模型文件路径
            X: 测试数据
            n_runs: 运行次数（默认100次）

        返回:
            Dict: 包含两种方式的推理时间、加速比等信息
        """
        if not ONNXRUNTIME_AVAILABLE:
            raise RuntimeError("onnxruntime 未安装")

        result = {
            "sklearn_time_ms": 0,
            "onnx_time_ms": 0,
            "speedup": 0,
            "n_runs": n_runs,
            "n_samples": len(X),
            "n_features": X.shape[1] if X.ndim > 1 else 1,
        }

        # 加载 sklearn 模型
        if not os.path.exists(sklearn_path):
            result["error"] = f"sklearn 模型文件不存在: {sklearn_path}"
            return result

        sklearn_model = joblib.load(sklearn_path)
        logger.info("sklearn 模型加载完成")

        # 加载 ONNX 模型
        onnx_session = self.load_model(onnx_path)

        # 确保 X 是正确格式
        X_sklearn = X.astype(np.float64) if hasattr(sklearn_model, "predict") else X
        X_onnx = X.astype(np.float32)

        # 预热（排除首次调用的 JIT 编译开销）
        _ = sklearn_model.predict(X_sklearn[:1])
        _ = self.predict(onnx_session, X_onnx[:1])

        # sklearn 推理计时
        sklearn_latencies = []
        for _ in range(n_runs):
            start = time.perf_counter()
            _ = sklearn_model.predict(X_sklearn)
            sklearn_latencies.append((time.perf_counter() - start) * 1000)
        sklearn_total = sum(sklearn_latencies)
        result["sklearn_time_ms"] = round(sklearn_total, 3)
        result["sklearn_time_per_run_ms"] = round(sklearn_total / n_runs, 4)

        # ONNX 推理计时
        onnx_latencies = []
        for _ in range(n_runs):
            start = time.perf_counter()
            _ = self.predict(onnx_session, X_onnx)
            onnx_latencies.append((time.perf_counter() - start) * 1000)
        onnx_total = sum(onnx_latencies)
        result["onnx_time_ms"] = round(onnx_total, 3)
        result["onnx_time_per_run_ms"] = round(onnx_total / n_runs, 4)
        result["iterations"] = n_runs
        result["avg_ms"] = result["onnx_time_per_run_ms"]
        result["p50_ms"] = round(float(np.percentile(onnx_latencies, 50)), 4)
        result["p99_ms"] = round(float(np.percentile(onnx_latencies, 99)), 4)
        result["ops_per_sec"] = round(
            (n_runs * len(X) * 1000) / onnx_total, 2
        ) if onnx_total > 0 else 0

        # 计算加速比
        if onnx_total > 0:
            result["speedup"] = round(sklearn_total / onnx_total, 2)

        logger.info(
            f"性能对比: sklearn={result['sklearn_time_ms']}ms, "
            f"ONNX={result['onnx_time_ms']}ms, "
            f"加速比={result['speedup']}x"
        )

        return result

    def benchmark_onnx(
        self,
        onnx_path: str,
        X: np.ndarray,
        n_runs: int = 100,
    ) -> Dict[str, Any]:
        """测试没有平台原始 PKL 对照产物的原生 ONNX 模型。"""
        session = self.load_model(onnx_path)
        X_onnx = X.astype(np.float32)
        _ = self.predict(session, X_onnx[:1])

        latencies = []
        for _ in range(n_runs):
            start = time.perf_counter()
            _ = self.predict(session, X_onnx)
            latencies.append((time.perf_counter() - start) * 1000)

        total = sum(latencies)
        average = total / n_runs
        return {
            "mode": "onnx_only",
            "n_runs": n_runs,
            "iterations": n_runs,
            "n_samples": len(X),
            "n_features": X.shape[1],
            "onnx_time_ms": round(total, 3),
            "onnx_time_per_run_ms": round(average, 4),
            "avg_ms": round(average, 4),
            "p50_ms": round(float(np.percentile(latencies, 50)), 4),
            "p99_ms": round(float(np.percentile(latencies, 99)), 4),
            "ops_per_sec": round(
                (n_runs * len(X) * 1000) / total, 2
            ) if total > 0 else 0,
        }

    def clear_cache(self):
        """清除缓存的推理会话"""
        self._sessions.clear()
        logger.info("ONNX 推理会话缓存已清除")


# ============================================================
# 全局单例
# ============================================================

_onnx_inference_service: Optional[ONNXInferenceService] = None


def get_onnx_inference_service() -> ONNXInferenceService:
    """获取 ONNXInferenceService 单例"""
    global _onnx_inference_service
    if _onnx_inference_service is None:
        _onnx_inference_service = ONNXInferenceService()
    return _onnx_inference_service
