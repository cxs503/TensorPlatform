"""
数据集模块
"""
from typing import Dict, List


class Datasets:
    """数据集管理"""

    def __init__(self, client):
        self.client = client

    def list(self, page: int = 1, size: int = 20) -> Dict:
        """获取数据集列表"""
        return self.client.get(f"/datasets?page={page}&size={size}")

    def get(self, dataset_id: int) -> Dict:
        """获取数据集详情"""
        return self.client.get(f"/datasets/{dataset_id}")

    def create(self, name: str, data_type: str = "tabular") -> Dict:
        """创建数据集"""
        return self.client.post(
            "/datasets", json={"name": name, "data_type": data_type}
        )

    def upload(self, dataset_id: int, file_path: str) -> Dict:
        """上传数据集文件"""
        import files

        with open(file_path, "rb") as f:
            files = {"file": f}
            response = self.client.session.post(
                f"{self.client.base_url}/api/v1/datasets/{dataset_id}/upload",
                files=files,
            )
        return response.json()

    def preview(self, dataset_id: int, limit: int = 10) -> Dict:
        """预览数据集"""
        return self.client.get(f"/datasets/{dataset_id}/preview?limit={limit}")

    def delete(self, dataset_id: int) -> Dict:
        """删除数据集"""
        return self.client.delete(f"/datasets/{dataset_id}")
