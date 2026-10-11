"""
数据订阅/共享服务
支持数据变更通知、跨租户数据共享、数据订阅管理
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class SubscriptionStatus(str, Enum):
    """订阅状态"""

    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SharePermission(str, Enum):
    """共享权限"""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class Subscription:
    """数据订阅"""

    def __init__(
        self,
        name: str,
        dataset_id: str,
        subscriber_id: str,
        filter_config: Dict = None,
        notification_channels: List[str] = None,
        schedule: str = "realtime",  # realtime, hourly, daily
    ):
        self.id = f"sub_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.dataset_id = dataset_id
        self.subscriber_id = subscriber_id
        self.filter_config = filter_config or {}
        self.notification_channels = notification_channels or ["webhook"]
        self.schedule = schedule
        self.status = SubscriptionStatus.ACTIVE
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.last_triggered = None
        self.trigger_count = 0


class DataShare:
    """数据共享"""

    def __init__(
        self,
        dataset_id: str,
        owner_id: str,
        target_tenant_id: str,
        permission: str = SharePermission.READ,
        expires_at: str = None,
    ):
        self.id = f"share_{uuid.uuid4().hex[:12]}"
        self.dataset_id = dataset_id
        self.owner_id = owner_id
        self.target_tenant_id = target_tenant_id
        self.permission = permission
        self.expires_at = expires_at
        self.created_at = datetime.now().isoformat()
        self.status = "active"


class DataChangeEvent:
    """数据变更事件"""

    def __init__(
        self,
        dataset_id: str,
        change_type: str,  # insert, update, delete
        data: Dict,
        timestamp: str = None,
    ):
        self.id = f"event_{uuid.uuid4().hex[:12]}"
        self.dataset_id = dataset_id
        self.change_type = change_type
        self.data = data
        self.timestamp = timestamp or datetime.now().isoformat()


class SubscriptionEvent:
    """订阅事件"""

    def __init__(
        self, subscription_id: str, dataset_id: str, changes: List[DataChangeEvent]
    ):
        self.id = f"subevent_{uuid.uuid4().hex[:12]}"
        self.subscription_id = subscription_id
        self.dataset_id = dataset_id
        self.changes = changes
        self.created_at = datetime.now().isoformat()
        self.delivered = False


class DataSubscriptionService:
    """数据订阅/共享服务"""

    def __init__(self):
        self.subscriptions: Dict[str, Subscription] = {}
        self.shares: Dict[str, DataShare] = {}
        self.events: Dict[str, DataChangeEvent] = {}
        self.subscription_events: Dict[str, SubscriptionEvent] = {}
        self.subscribers: Dict[str, List[str]] = {}  # dataset_id -> subscription_ids

    # ========== 订阅管理 ==========

    def create_subscription(
        self,
        name: str,
        dataset_id: str,
        subscriber_id: str,
        filter_config: Dict = None,
        notification_channels: List[str] = None,
        schedule: str = "realtime",
    ) -> Subscription:
        """创建订阅"""
        subscription = Subscription(
            name,
            dataset_id,
            subscriber_id,
            filter_config,
            notification_channels,
            schedule,
        )
        self.subscriptions[subscription.id] = subscription

        # 添加到数据集订阅者列表
        if dataset_id not in self.subscribers:
            self.subscribers[dataset_id] = []
        self.subscribers[dataset_id].append(subscription.id)

        return subscription

    def get_subscription(self, subscription_id: str) -> Optional[Subscription]:
        """获取订阅"""
        return self.subscriptions.get(subscription_id)

    def update_subscription(
        self,
        subscription_id: str,
        status: str = None,
        filter_config: Dict = None,
        notification_channels: List[str] = None,
    ) -> Optional[Subscription]:
        """更新订阅"""
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            return None

        if status:
            sub.status = status
        if filter_config:
            sub.filter_config = filter_config
        if notification_channels:
            sub.notification_channels = notification_channels
        sub.updated_at = datetime.now().isoformat()

        return sub

    def delete_subscription(self, subscription_id: str) -> bool:
        """删除订阅"""
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            return False

        # 从订阅者列表中移除
        if sub.dataset_id in self.subscribers:
            self.subscribers[sub.dataset_id].remove(subscription_id)

        del self.subscriptions[subscription_id]
        return True

    def list_subscriptions(self, subscriber_id: str = None) -> List[Dict]:
        """列出订阅"""
        subs = self.subscriptions.values()
        if subscriber_id:
            subs = [s for s in subs if s.subscriber_id == subscriber_id]

        return [
            {
                "id": s.id,
                "name": s.name,
                "dataset_id": s.dataset_id,
                "subscriber_id": s.subscriber_id,
                "filter_config": s.filter_config,
                "notification_channels": s.notification_channels,
                "schedule": s.schedule,
                "status": s.status,
                "last_triggered": s.last_triggered,
                "trigger_count": s.trigger_count,
                "created_at": s.created_at,
            }
            for s in subs
        ]

    # ========== 数据变更通知 ==========

    def record_data_change(
        self, dataset_id: str, change_type: str, data: Dict
    ) -> DataChangeEvent:
        """记录数据变更"""
        event = DataChangeEvent(dataset_id, change_type, data)
        self.events[event.id] = event

        # 触发相关订阅
        self._trigger_subscriptions(dataset_id, event)

        return event

    def _trigger_subscriptions(self, dataset_id: str, event: DataChangeEvent):
        """触发相关订阅"""
        subscription_ids = self.subscribers.get(dataset_id, [])

        for sub_id in subscription_ids:
            sub = self.subscriptions.get(sub_id)
            if not sub or sub.status != SubscriptionStatus.ACTIVE:
                continue

            # 检查过滤器
            if not self._match_filter(event.data, sub.filter_config):
                continue

            # 创建订阅事件
            sub_event = SubscriptionEvent(sub_id, dataset_id, [event])
            self.subscription_events[sub_event.id] = sub_event

            # 更新订阅状态
            sub.last_triggered = datetime.now().isoformat()
            sub.trigger_count += 1

            # 发送通知（模拟）
            self._send_notification(sub, event)

    def _match_filter(self, data: Dict, filter_config: Dict) -> bool:
        """检查数据是否匹配过滤器"""
        if not filter_config:
            return True

        for key, value in filter_config.items():
            if key not in data:
                return False
            if isinstance(value, dict):
                # 支持范围查询
                if "min" in value and data[key] < value["min"]:
                    return False
                if "max" in value and data[key] > value["max"]:
                    return False
                if "in" in value and data[key] not in value["in"]:
                    return False
            elif data[key] != value:
                return False

        return True

    def _send_notification(self, subscription: Subscription, event: DataChangeEvent):
        """发送通知"""
        for channel in subscription.notification_channels:
            # 这里可以集成实际的通知服务
            pass

    # ========== 数据共享 ==========

    def share_dataset(
        self,
        dataset_id: str,
        owner_id: str,
        target_tenant_id: str,
        permission: str = SharePermission.READ,
        expires_at: str = None,
    ) -> DataShare:
        """共享数据集"""
        share = DataShare(
            dataset_id, owner_id, target_tenant_id, permission, expires_at
        )
        self.shares[share.id] = share
        return share

    def get_shared_dataset(
        self, dataset_id: str, tenant_id: str
    ) -> Optional[DataShare]:
        """获取共享的数据集"""
        for share in self.shares.values():
            if share.dataset_id == dataset_id and share.target_tenant_id == tenant_id:
                if share.status == "active":
                    if share.expires_at:
                        # 检查是否过期
                        expires = datetime.fromisoformat(share.expires_at)
                        if expires < datetime.now():
                            share.status = "expired"
                            return None
                    return share
        return None

    def list_shared_by_me(self, owner_id: str) -> List[Dict]:
        """列出我共享的数据集"""
        return [
            {
                "id": s.id,
                "dataset_id": s.dataset_id,
                "target_tenant_id": s.target_tenant_id,
                "permission": s.permission,
                "expires_at": s.expires_at,
                "status": s.status,
                "created_at": s.created_at,
            }
            for s in self.shares.values()
            if s.owner_id == owner_id
        ]

    def list_shared_with_me(self, tenant_id: str) -> List[Dict]:
        """列出共享给我的数据集"""
        result = []
        for share in self.shares.values():
            if share.target_tenant_id == tenant_id and share.status == "active":
                if (
                    not share.expires_at
                    or datetime.fromisoformat(share.expires_at) > datetime.now()
                ):
                    result.append(
                        {
                            "id": share.id,
                            "dataset_id": share.dataset_id,
                            "owner_id": share.owner_id,
                            "permission": share.permission,
                            "expires_at": share.expires_at,
                            "created_at": share.created_at,
                        }
                    )
        return result

    def revoke_share(self, share_id: str, owner_id: str) -> bool:
        """撤销共享"""
        share = self.shares.get(share_id)
        if not share or share.owner_id != owner_id:
            return False

        share.status = "revoked"
        return True

    def update_share_permission(
        self, share_id: str, owner_id: str, new_permission: str
    ) -> Optional[DataShare]:
        """更新共享权限"""
        share = self.shares.get(share_id)
        if not share or share.owner_id != owner_id:
            return None

        share.permission = new_permission
        return share


# 全局实例
_subscription_service: Optional[DataSubscriptionService] = None


def get_subscription_service() -> DataSubscriptionService:
    """获取数据订阅服务"""
    global _subscription_service
    if _subscription_service is None:
        _subscription_service = DataSubscriptionService()
    return _subscription_service
