"""
BentoML 模型服务化
训练完成 → 自动打包为 REST API 推理服务
"""
import os
import pickle
import numpy as np
from pathlib import Path

import bentoml
from bentoml.io import NumpyNdarray, JSON


class BentoMLService:
    """BentoML 模型服务管理器"""

    MODELS_DIR = "/app/uploads/models"

    def package_model(self, model_path: str, model_name: str, metadata: dict = None):
        """将训练好的模型打包为 BentoML 服务"""
        if not os.path.exists(model_path):
            return {"error": f"Model file not found: {model_path}"}

        # Load model
        with open(model_path, "rb") as f:
            model = pickle.load(f)

        # Save with BentoML
        bento_model = bentoml.sklearn.save_model(
            model_name,
            model,
            metadata=metadata or {},
        )

        return {
            "bento_tag": str(bento_model.tag),
            "model_name": model_name,
            "model_path": bento_model.path,
            "framework": "sklearn",
        }

    def list_models(self):
        """列出所有已打包的模型"""
        models = bentoml.models.list()
        return [
            {
                "tag": str(m.tag),
                "name": m.tag.name if hasattr(m.tag, 'name') else str(m.tag),
                "created_at": str(m.info.creation_time) if hasattr(m.info, 'creation_time') else "",
            }
            for m in models
        ]

    def get_model_info(self, model_tag: str):
        """获取模型详情"""
        try:
            model = bentoml.models.get(model_tag)
            return {
                "tag": str(model.tag),
                "path": model.path,
                "labels": dict(model.info.labels) if hasattr(model.info, 'labels') else {},
                "metadata": dict(model.info.metadata) if hasattr(model.info, 'metadata') else {},
            }
        except Exception as e:
            return {"error": str(e)}

    def serve(self, model_tag: str, port: int = 3001):
        """启动推理服务 (返回启动信息)"""
        return {
            "model_tag": model_tag,
            "port": port,
            "endpoint": f"http://localhost:{port}/predict",
            "status": "serving",
            "curl_example": f"curl -X POST http://localhost:{port}/predict -H 'Content-Type: application/json' -d '{{\"instances\": [[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]]}}'"
        }

    def auto_package_latest(self, job_id: str = None):
        """自动打包最近训练完成的模型"""
        models_dir = Path(self.MODELS_DIR)
        if not models_dir.exists():
            return {"error": "Models directory not found"}

        # Find latest model file
        if job_id:
            target = models_dir / f"{job_id}_model.pkl"
        else:
            # Find most recent pkl file
            pkl_files = sorted(models_dir.glob("*_model.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not pkl_files:
                return {"error": "No model files found"}
            target = pkl_files[0]

        model_name = target.stem.replace("_model", "")
        return self.package_model(str(target), model_name)
