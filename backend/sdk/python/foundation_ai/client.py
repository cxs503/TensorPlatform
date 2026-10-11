"""
开放数字化底座客户端
"""
from typing import Any, Dict, List, Optional

import requests


class foundationClient:
    """开放数字化底座客户端"""

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        api_key: str = None,
        token: str = None,
    ):
        """
        初始化客户端

        Args:
            base_url: API基础URL
            api_key: API密钥
            token: JWT令牌
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.token = token
        self.session = requests.Session()

        # 设置默认headers
        self.session.headers.update({"Content-Type": "application/json"})
        if api_key:
            self.session.headers.update({"X-API-Key": api_key})
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

        # 初始化子模块
        from .datasets import Datasets
        from .deployment import Deployment
        from .models import Models
        from .projects import Projects
        from .training import Training

        self.projects = Projects(self)
        self.datasets = Datasets(self)
        self.models = Models(self)
        self.training = Training(self)
        self.deployment = Deployment(self)

    def request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """发送API请求"""
        url = f"{self.base_url}/api/v1/{endpoint.lstrip('/')}"
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get(self, endpoint: str, **kwargs) -> Dict:
        """GET请求"""
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs) -> Dict:
        """POST请求"""
        return self.request("POST", endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs) -> Dict:
        """PUT请求"""
        return self.request("PUT", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs) -> Dict:
        """DELETE请求"""
        return self.request("DELETE", endpoint, **kwargs)

    def login(self, username: str, password: str) -> Dict:
        """用户登录"""
        response = self.post(
            "/auth/login", json={"username": username, "password": password}
        )
        if response.get("access_token"):
            self.token = response["access_token"]
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        return response

    def health_check(self) -> Dict:
        """健康检查"""
        return self.get("/health")
