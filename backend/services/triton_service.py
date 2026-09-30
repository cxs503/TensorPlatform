"""
Triton Inference Server 客户端
高性能模型推理服务 (支持 GPU)
"""
import numpy as np
import tritonclient.http as httpclient
from tritonclient.utils import np_to_triton_dtype


class TritonInferenceService:
    """Triton 推理服务客户端"""

    def __init__(self, url="localhost:8000"):
        self.url = url
        self.client = None

    def connect(self):
        """连接 Triton 服务"""
        try:
            self.client = httpclient.InferenceServerClient(url=self.url)
            if self.client.is_server_live():
                return {"status": "connected", "url": self.url, "live": True}
            return {"status": "not_live", "url": self.url}
        except Exception as e:
            return {"status": "error", "url": self.url, "error": str(e)[:200]}

    def list_models(self):
        """列出已部署的模型"""
        if not self.client:
            self.connect()
        try:
            models = self.client.get_model_repository_index()
            return [
                {"name": m.get("name", "?"), "version": m.get("version", "?")}
                for m in models
            ]
        except Exception as e:
            return {"error": str(e)[:200]}

    def infer(self, model_name: str, inputs: np.ndarray,
              input_name="input", output_name="output", model_version=""):
        """执行推理"""
        if not self.client:
            self.connect()

        # Prepare inputs
        triton_inputs = [
            httpclient.InferInput(
                input_name, inputs.shape, np_to_triton_dtype(inputs.dtype)
            )
        ]
        triton_inputs[0].set_data_from_numpy(inputs)

        outputs = [httpclient.InferRequestedOutput(output_name)]

        try:
            result = self.client.infer(
                model_name=model_name,
                inputs=triton_inputs,
                outputs=outputs,
                model_version=model_version,
            )
            output_data = result.as_numpy(output_name)
            return {
                "model": model_name,
                "input_shape": list(inputs.shape),
                "output_shape": list(output_data.shape),
                "predictions": output_data.tolist()[:10],  # first 10
            }
        except Exception as e:
            return {"error": f"Triton inference failed: {str(e)[:200]}"}

    def get_model_status(self, model_name: str):
        """获取模型状态"""
        if not self.client:
            self.connect()
        try:
            status = self.client.get_model_config(model_name)
            return {"name": model_name, "config": str(status)[:500]}
        except Exception as e:
            return {"error": str(e)[:200]}

    def get_health(self):
        """获取服务健康状态"""
        try:
            if not self.client:
                self.connect()
            live = self.client.is_server_live()
            ready = self.client.is_server_ready()
            return {"live": live, "ready": ready, "url": self.url}
        except Exception as e:
            return {"live": False, "ready": False, "error": str(e)[:200],
                    "note": "Triton Server not running (expected in dev mode)"}
