"""
部署策略服务
支持蓝绿部署和金丝雀发布
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class DeploymentStrategy(str, Enum):
    """部署策略"""

    ROLLING = "rolling"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"
    RECREATE = "recreate"


class DeploymentStatus(str, Enum):
    """部署状态"""

    DEPLOYING = "deploying"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    ROLLING_BACK = "rolling_back"


class DeploymentVersion:
    """部署版本"""

    def __init__(self, version: str, image: str, replicas: int = 1):
        self.version = version
        self.image = image
        self.replicas = replicas
        self.created_at = datetime.now().isoformat()
        self.status = DeploymentStatus.HEALTHY


class Deployment:
    """部署"""

    def __init__(
        self,
        name: str,
        strategy: str,
        model_id: str = None,
    ):
        self.id = f"deploy_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.strategy = strategy
        self.model_id = model_id
        self.versions: List[DeploymentVersion] = []
        self.active_version: Optional[str] = None
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()


class CanaryRelease:
    """金丝雀发布"""

    def __init__(self, deployment_id: str):
        self.deployment_id = deployment_id
        self.traffic_rules: Dict[str, int] = {}  # version -> percentage
        self.auto_promote = False
        self.promote_threshold = 0.95


class DeploymentStrategyService:
    """部署策略服务"""

    def __init__(self):
        self.deployments: Dict[str, Deployment] = {}
        self.canary_releases: Dict[str, CanaryRelease] = {}

    def create_deployment(
        self,
        name: str,
        strategy: str,
        model_id: str,
        version: str,
        image: str,
        replicas: int = 2,
    ) -> Deployment:
        """创建部署"""
        deployment = Deployment(name, strategy, model_id)
        deployment.versions.append(DeploymentVersion(version, image, replicas))
        deployment.active_version = version

        self.deployments[deployment.id] = deployment

        return deployment

    def add_version(
        self,
        deployment_id: str,
        version: str,
        image: str,
        replicas: int = 2,
    ) -> Deployment:
        """添加新版本"""
        deployment = self.deployments.get(deployment_id)
        if not deployment:
            raise ValueError("Deployment not found")

        # 检查策略
        if deployment.strategy == DeploymentStrategy.BLUE_GREEN.name.lower():
            # 蓝绿部署：添加新版本作为候选
            deployment.versions.append(DeploymentVersion(version, image, replicas))

        elif deployment.strategy == DeploymentStrategy.CANARY.name.lower():
            # 金丝雀：新版本默认10%流量
            deployment.versions.append(DeploymentVersion(version, image, replicas))

            # 创建或更新金丝雀规则
            canary = self.canary_releases.get(deployment_id)
            if not canary:
                canary = CanaryRelease(deployment_id)
                self.canary_releases[deployment_id] = canary

            # 旧版本90%，新版本10%
            for v in deployment.versions[:-1]:
                canary.traffic_rules[v.version] = 90
            canary.traffic_rules[version] = 10

        deployment.updated_at = datetime.now().isoformat()
        return deployment

    def switch_version(self, deployment_id: str, version: str) -> bool:
        """切换到指定版本"""
        deployment = self.deployments.get(deployment_id)
        if not deployment:
            return False

        # 检查版本是否存在
        if not any(v.version == version for v in deployment.versions):
            return False

        deployment.active_version = version
        deployment.updated_at = datetime.now().isoformat()
        return True

    def blue_green_promote(self, deployment_id: str, new_version: str) -> bool:
        """蓝绿部署：切换到新版本"""
        deployment = self.deployments.get(deployment_id)
        if not deployment:
            return False

        success = self.switch_version(deployment_id, new_version)

        # 可选：删除旧版本（保留以便于回滚）
        return success

    def canary_adjust_traffic(
        self, deployment_id: str, version: str, percentage: int
    ) -> bool:
        """金丝雀：调整流量"""
        deployment = self.deployments.get(deployment_id)
        if not deployment:
            return False

        canary = self.canary_releases.get(deployment_id)
        if not canary:
            return False

        # 确保总和为100
        canary.traffic_rules[version] = percentage

        # 调整其他版本
        other_percentage = (
            (100 - percentage) / (len(deployment.versions) - 1)
            if len(deployment.versions) > 1
            else 0
        )
        for v in deployment.versions:
            if v.version != version:
                canary.traffic_rules[v.version] = int(other_percentage)

        return True

    def canary_promote(self, deployment_id: str) -> bool:
        """金丝雀：完全切换到新版本"""
        deployment = self.deployments.get(deployment_id)
        if not deployment:
            return False

        # 获取最新版本
        if not deployment.versions:
            return False

        new_version = deployment.versions[-1]
        return self.switch_version(deployment_id, new_version.version)

    def rollback(self, deployment_id: str) -> bool:
        """回滚到上一版本"""
        deployment = self.deployments.get(deployment_id)
        if not deployment or len(deployment.versions) < 2:
            return False

        # 回滚到上一个版本
        deployment.active_version = deployment.versions[-2].version
        deployment.updated_at = datetime.now().isoformat()
        return True

    def get_deployment(self, deployment_id: str) -> Optional[Deployment]:
        """获取部署详情"""
        return self.deployments.get(deployment_id)

    def list_deployments(self) -> List[Dict]:
        """列出所有部署"""
        return [
            {
                "id": d.id,
                "name": d.name,
                "strategy": d.strategy,
                "model_id": d.model_id,
                "versions": [
                    {"version": v.version, "status": v.status, "replicas": v.replicas}
                    for v in d.versions
                ],
                "active_version": d.active_version,
                "canary_rules": self.canary_releases.get(d.id).traffic_rules
                if d.id in self.canary_releases
                else {},
            }
            for d in self.deployments.values()
        ]


# 全局实例
_strategy_service: Optional[DeploymentStrategyService] = None


def get_deployment_strategy_service() -> DeploymentStrategyService:
    """获取部署策略服务"""
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = DeploymentStrategyService()
    return _strategy_service
