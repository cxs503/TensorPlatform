"""
配置中心服务
提供分布式配置管理、热更新、配置版本、灰度发布
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ConfigType(str, Enum):
    """配置类型"""

    PROPERTIES = "properties"
    YAML = "yaml"
    JSON = "json"
    TEXT = "text"


class ConfigScope(str, Enum):
    """配置作用域"""

    GLOBAL = "global"
    APPLICATION = "application"
    CLUSTER = "cluster"
    SERVICE = "service"


class ConfigStatus(str, Enum):
    """配置状态"""

    DRAFT = "draft"
    RELEASED = "released"
    ROLLED_BACK = "rolled_back"


class ConfigFile:
    """配置文件"""

    def __init__(
        self,
        name: str,
        config_type: str,
        content: str,
        scope: str = ConfigScope.APPLICATION,
        application: str = None,
    ):
        self.id = f"config_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.config_type = config_type
        self.content = content
        self.scope = scope
        self.application = application
        self.version = 1
        self.status = ConfigStatus.DRAFT
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.created_by = None
        self.description = None


class ConfigVersion:
    """配置版本"""

    def __init__(self, config_id: str, version: int, content: str, comment: str = None):
        self.id = f"version_{uuid.uuid4().hex[:12]}"
        self.config_id = config_id
        self.version = version
        self.content = content
        self.comment = comment
        self.created_at = datetime.now().isoformat()
        self.created_by = None


class ConfigRelease:
    """配置发布"""

    def __init__(self, config_id: str, target_scope: str, target_name: str = None):
        self.id = f"release_{uuid.uuid4().hex[:12]}"
        self.config_id = config_id
        self.target_scope = target_scope
        self.target_name = target_name
        self.status = "pending"
        self.released_at = None
        self.created_at = datetime.now().isoformat()


class ConfigListener:
    """配置监听器"""

    def __init__(self, config_id: str, callback_url: str, events: List[str] = None):
        self.id = f"listener_{uuid.uuid4().hex[:12]}"
        self.config_id = config_id
        self.callback_url = callback_url
        self.events = events or ["update", "delete"]
        self.enabled = True


class Namespace:
    """命名空间"""

    def __init__(self, name: str, description: str = None):
        self.id = f"ns_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.description = description
        self.created_at = datetime.now().isoformat()


class ConfigCenterService:
    """配置中心服务"""

    def __init__(self):
        self.configs: Dict[str, ConfigFile] = {}
        self.versions: Dict[str, List[ConfigVersion]] = {}
        self.releases: Dict[str, ConfigRelease] = {}
        self.listeners: Dict[str, List[ConfigListener]] = {}
        self.namespaces: Dict[str, Namespace] = {}
        self._init_default_namespace()

    def _init_default_namespace(self):
        """初始化默认命名空间"""
        ns = Namespace("default", "默认命名空间")
        self.namespaces[ns.id] = ns

    # ========== 命名空间管理 ==========

    def create_namespace(self, name: str, description: str = None) -> Namespace:
        """创建命名空间"""
        ns = Namespace(name, description)
        self.namespaces[ns.id] = ns
        return ns

    def get_namespace(self, namespace_id: str) -> Optional[Namespace]:
        """获取命名空间"""
        return self.namespaces.get(namespace_id)

    def list_namespaces(self) -> List[Dict]:
        """列出命名空间"""
        return [
            {
                "id": ns.id,
                "name": ns.name,
                "description": ns.description,
                "created_at": ns.created_at,
            }
            for ns in self.namespaces.values()
        ]

    # ========== 配置管理 ==========

    def create_config(
        self,
        name: str,
        config_type: str,
        content: str,
        scope: str = ConfigScope.APPLICATION,
        application: str = None,
        description: str = None,
    ) -> ConfigFile:
        """创建配置"""
        config = ConfigFile(name, config_type, content, scope, application)
        config.description = description
        self.configs[config.id] = config
        self.versions[config.id] = []
        return config

    def update_config(
        self, config_id: str, content: str, comment: str = None
    ) -> Optional[ConfigFile]:
        """更新配置"""
        config = self.configs.get(config_id)
        if not config:
            return None

        # 保存历史版本
        version = ConfigVersion(
            config_id=config_id,
            version=config.version,
            content=config.content,
            comment=comment,
        )
        self.versions[config_id].append(version)

        # 更新配置
        config.content = content
        config.version += 1
        config.updated_at = datetime.now().isoformat()
        config.status = ConfigStatus.DRAFT

        return config

    def release_config(self, config_id: str) -> Optional[ConfigFile]:
        """发布配置"""
        config = self.configs.get(config_id)
        if not config:
            return None

        config.status = ConfigStatus.RELEASED

        # 通知监听器
        self._notify_listeners(config_id, "update")

        return config

    def rollback_config(self, config_id: str, version: int = None) -> bool:
        """回滚配置"""
        config = self.configs.get(config_id)
        if not config:
            return False

        versions = self.versions.get(config_id, [])
        if not versions:
            return False

        # 找到目标版本
        target_version = None
        if version:
            target_version = next((v for v in versions if v.version == version), None)
        else:
            # 回滚到上一版本
            target_version = versions[-1] if versions else None

        if not target_version:
            return False

        # 保存当前版本
        current_version = ConfigVersion(
            config_id=config_id,
            version=config.version,
            content=config.content,
            comment="before rollback",
        )
        versions.append(current_version)

        # 回滚
        config.content = target_version.content
        config.version += 1
        config.status = ConfigStatus.ROLLED_BACK
        config.updated_at = datetime.now().isoformat()

        # 通知监听器
        self._notify_listeners(config_id, "update")

        return True

    def get_config(self, config_id: str) -> Optional[ConfigFile]:
        """获取配置"""
        return self.configs.get(config_id)

    def get_config_by_name(
        self, name: str, scope: str = None, application: str = None
    ) -> Optional[ConfigFile]:
        """根据名称获取配置"""
        for config in self.configs.values():
            if config.name == name:
                if scope and config.scope != scope:
                    continue
                if application and config.application != application:
                    continue
                return config
        return None

    def delete_config(self, config_id: str) -> bool:
        """删除配置"""
        if config_id in self.configs:
            # 通知监听器
            self._notify_listeners(config_id, "delete")

            del self.configs[config_id]
            if config_id in self.versions:
                del self.versions[config_id]
            return True
        return False

    def list_configs(
        self, scope: str = None, application: str = None, status: str = None
    ) -> List[Dict]:
        """列出配置"""
        configs = self.configs.values()
        if scope:
            configs = [c for c in configs if c.scope == scope]
        if application:
            configs = [c for c in configs if c.application == application]
        if status:
            configs = [c for c in configs if c.status == status]

        return [
            {
                "id": c.id,
                "name": c.name,
                "config_type": c.config_type,
                "scope": c.scope,
                "application": c.application,
                "version": c.version,
                "status": c.status,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "description": c.description,
            }
            for c in configs
        ]

    # ========== 配置版本 ==========

    def get_versions(self, config_id: str) -> List[Dict]:
        """获取配置版本历史"""
        versions = self.versions.get(config_id, [])
        return [
            {
                "version": v.version,
                "content": v.content,
                "comment": v.comment,
                "created_at": v.created_at,
            }
            for v in versions
        ]

    def get_version(self, config_id: str, version: int) -> Optional[Dict]:
        """获取指定版本"""
        versions = self.versions.get(config_id, [])
        v = next((ver for ver in versions if ver.version == version), None)
        if not v:
            return None
        return {
            "version": v.version,
            "content": v.content,
            "comment": v.comment,
            "created_at": v.created_at,
        }

    # ========== 配置监听 ==========

    def add_listener(
        self, config_id: str, callback_url: str, events: List[str] = None
    ) -> ConfigListener:
        """添加配置监听器"""
        listener = ConfigListener(config_id, callback_url, events)
        if config_id not in self.listeners:
            self.listeners[config_id] = []
        self.listeners[config_id].append(listener)
        return listener

    def remove_listener(self, config_id: str, listener_id: str) -> bool:
        """移除配置监听器"""
        listeners = self.listeners.get(config_id, [])
        for i, listener in enumerate(listeners):
            if listener.id == listener_id:
                listeners.pop(i)
                return True
        return False

    def list_listeners(self, config_id: str) -> List[Dict]:
        """列出配置监听器"""
        listeners = self.listeners.get(config_id, [])
        return [
            {
                "id": l.id,
                "callback_url": l.callback_url,
                "events": l.events,
                "enabled": l.enabled,
            }
            for l in listeners
        ]

    def _notify_listeners(self, config_id: str, event: str):
        """通知监听器"""
        listeners = self.listeners.get(config_id, [])
        for listener in listeners:
            if listener.enabled and event in listener.events:
                # 实际应调用callback_url
                pass

    # ========== 统计 ==========

    def get_config_stats(self) -> Dict:
        """获取配置统计"""
        total_configs = len(self.configs)
        released = sum(
            1 for c in self.configs.values() if c.status == ConfigStatus.RELEASED
        )
        draft = sum(1 for c in self.configs.values() if c.status == ConfigStatus.DRAFT)

        return {
            "total_configs": total_configs,
            "released": released,
            "draft": draft,
            "total_namespaces": len(self.namespaces),
            "total_listeners": sum(len(l) for l in self.listeners.values()),
        }


# 全局实例
_config_center: Optional[ConfigCenterService] = None


def get_config_center_service() -> ConfigCenterService:
    """获取配置中心服务"""
    global _config_center
    if _config_center is None:
        _config_center = ConfigCenterService()
    return _config_center
