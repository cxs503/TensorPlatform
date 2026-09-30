"""
项目模块
"""
from typing import Dict, List, Optional


class Projects:
    """项目管理"""

    def __init__(self, client):
        self.client = client

    def list(self, page: int = 1, size: int = 20) -> Dict:
        """获取项目列表"""
        return self.client.get(f"/projects?page={page}&size={size}")

    def get(self, project_id: int) -> Dict:
        """获取项目详情"""
        return self.client.get(f"/projects/{project_id}")

    def create(
        self,
        name: str,
        description: str = "",
        project_type: str = "custom",
        tags: List[str] = None,
    ) -> Dict:
        """创建项目"""
        return self.client.post(
            "/projects",
            json={
                "name": name,
                "description": description,
                "project_type": project_type,
                "tags": tags or [],
            },
        )

    def update(self, project_id: int, **kwargs) -> Dict:
        """更新项目"""
        return self.client.put(f"/projects/{project_id}", json=kwargs)

    def delete(self, project_id: int) -> Dict:
        """删除项目"""
        return self.client.delete(f"/projects/{project_id}")
