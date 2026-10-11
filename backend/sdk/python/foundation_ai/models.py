"""
模型模块
"""
from typing import Dict, List


class Models:
    """模型管理"""

    def __init__(self, client):
        self.client = client

    def list(self, page: int = 1, size: int = 20) -> Dict:
        """获取模型列表"""
        return self.client.get(f"/models?page={page}&size={size}")

    def get(self, model_id: int) -> Dict:
        """获取模型详情"""
        return self.client.get(f"/models/{model_id}")

    def create(self, name: str, model_type: str = "sklearn", **kwargs) -> Dict:
        """创建模型"""
        return self.client.post(
            "/models", json={"name": name, "model_type": model_type, **kwargs}
        )

    def delete(self, model_id: int) -> Dict:
        """删除模型"""
        return self.client.delete(f"/models/{model_id}")


class Training:
    """模型训练"""

    def __init__(self, client):
        self.client = client

    def list_jobs(self, page: int = 1, size: int = 20) -> Dict:
        """获取训练任务列表"""
        return self.client.get(f"/model-training/jobs?page={page}&size={size}")

    def get_job(self, job_id: int) -> Dict:
        """获取训练任务详情"""
        return self.client.get(f"/model-training/jobs/{job_id}")

    def create_job(
        self, name: str, dataset_id: int, model_type: str = "random_forest"
    ) -> Dict:
        """创建训练任务"""
        return self.client.post(
            "/model-training/jobs",
            json={"name": name, "dataset_id": dataset_id, "model_type": model_type},
        )

    def train_pytorch(self, **config) -> Dict:
        """PyTorch训练"""
        return self.client.post("/model-training/pytorch/train", json=config)

    def list_models(self) -> Dict:
        """获取已注册模型"""
        return self.client.get("/model-training/models")

    def predict(self, model_id: str, input_data: List) -> Dict:
        """模型预测"""
        return self.client.post(
            f"/model-training/models/{model_id}/predict",
            json={"input_data": input_data},
        )


class Deployment:
    """模型部署"""

    def __init__(self, client):
        self.client = client

    def list(self, page: int = 1, size: int = 20) -> Dict:
        """获取部署列表"""
        return self.client.get(f"/deployments?page={page}&size={size}")

    def get(self, deployment_id: int) -> Dict:
        """获取部署详情"""
        return self.client.get(f"/deployments/{deployment_id}")

    def create(self, model_id: int, name: str, deployment_type: str = "online") -> Dict:
        """创建部署"""
        return self.client.post(
            "/deployments",
            json={
                "model_id": model_id,
                "name": name,
                "deployment_type": deployment_type,
            },
        )

    def delete(self, deployment_id: int) -> Dict:
        """删除部署"""
        return self.client.delete(f"/deployments/{deployment_id}")

    def inference(self, deployment_id: int, data: List) -> Dict:
        """在线推理"""
        return self.client.post(f"/inference/{deployment_id}", json={"data": data})
