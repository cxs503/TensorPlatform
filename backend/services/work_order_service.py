"""
工单管理服务
提供运维工单全生命周期管理
"""
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkOrderStatus(str, Enum):
    """工单状态"""

    PENDING = "pending"  # 待处理
    ASSIGNED = "assigned"  # 已派发
    IN_PROGRESS = "in_progress"  # 处理中
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 已取消


class WorkOrderPriority(str, Enum):
    """工单优先级"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class WorkOrderType(str, Enum):
    """工单类型"""

    MAINTENANCE = "maintenance"  # 维护工单
    REPAIR = "repair"  # 维修工单
    INSPECTION = "inspection"  # 巡检工单
    EMERGENCY = "emergency"  # 应急工单


class WorkOrder:
    """工单"""

    def __init__(
        self,
        title: str,
        description: str,
        work_order_type: str,
        priority: str,
        equipment_id: str = None,
        location: str = None,
        created_by: str = "system",
    ):
        self.id = f"wo_{uuid.uuid4().hex[:12]}"
        self.title = title
        self.description = description
        self.work_order_type = work_order_type
        self.priority = priority
        self.equipment_id = equipment_id
        self.location = location
        self.created_by = created_by
        self.assigned_to = None
        self.status = WorkOrderStatus.PENDING
        self.created_at = datetime.now().isoformat()
        self.assigned_at = None
        self.started_at = None
        self.completed_at = None
        self.completed_by = None
        self.comments = []
        self.attachments = []
        self.estimated_hours = 0
        self.actual_hours = 0


class WorkOrderService:
    """工单管理服务"""

    def __init__(self):
        self.work_orders: Dict[str, WorkOrder] = {}

    # ========== 工单创建与派发 ==========

    def create_work_order(
        self,
        title: str,
        description: str,
        work_order_type: str,
        priority: str,
        equipment_id: str = None,
        location: str = None,
        created_by: str = "system",
        estimated_hours: float = 0,
    ) -> WorkOrder:
        """创建工单"""
        wo = WorkOrder(
            title=title,
            description=description,
            work_order_type=work_order_type,
            priority=priority,
            equipment_id=equipment_id,
            location=location,
            created_by=created_by,
        )
        wo.estimated_hours = estimated_hours
        self.work_orders[wo.id] = wo
        return wo

    def assign_work_order(
        self, work_order_id: str, assigned_to: str
    ) -> Optional[WorkOrder]:
        """派发工单"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return None

        wo.assigned_to = assigned_to
        wo.status = WorkOrderStatus.ASSIGNED
        wo.assigned_at = datetime.now().isoformat()
        return wo

    def batch_assign(self, work_order_ids: List[str], assigned_to: str) -> int:
        """批量派发工单"""
        count = 0
        for wo_id in work_order_ids:
            if self.assign_work_order(wo_id, assigned_to):
                count += 1
        return count

    # ========== 工单处理 ==========

    def start_work_order(self, work_order_id: str) -> Optional[WorkOrder]:
        """开始处理工单"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return None

        wo.status = WorkOrderStatus.IN_PROGRESS
        wo.started_at = datetime.now().isoformat()
        return wo

    def add_comment(self, work_order_id: str, comment: str, user: str) -> bool:
        """添加备注"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return False

        wo.comments.append(
            {"user": user, "content": comment, "timestamp": datetime.now().isoformat()}
        )
        return True

    def update_progress(
        self, work_order_id: str, progress: int, comment: str = None
    ) -> Optional[WorkOrder]:
        """更新进度"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return None

        if comment:
            self.add_comment(work_order_id, f"进度更新: {progress}% - {comment}", "system")
        return wo

    # ========== 工单完成 ==========

    def complete_work_order(
        self,
        work_order_id: str,
        completed_by: str,
        actual_hours: float = 0,
        comment: str = None,
    ) -> Optional[WorkOrder]:
        """完成工单"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return None

        wo.status = WorkOrderStatus.COMPLETED
        wo.completed_at = datetime.now().isoformat()
        wo.completed_by = completed_by
        wo.actual_hours = actual_hours

        if comment:
            wo.comments.append(
                {
                    "user": completed_by,
                    "content": f"完成备注: {comment}",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        return wo

    def cancel_work_order(self, work_order_id: str, reason: str) -> Optional[WorkOrder]:
        """取消工单"""
        wo = self.work_orders.get(work_order_id)
        if not wo:
            return None

        wo.status = WorkOrderStatus.CANCELLED
        wo.comments.append(
            {
                "user": "system",
                "content": f"取消原因: {reason}",
                "timestamp": datetime.now().isoformat(),
            }
        )
        return wo

    # ========== 工单查询 ==========

    def get_work_order(self, work_order_id: str) -> Optional[WorkOrder]:
        """获取工单"""
        return self.work_orders.get(work_order_id)

    def list_work_orders(
        self,
        status: str = None,
        priority: str = None,
        work_order_type: str = None,
        assigned_to: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """列出工单"""
        orders = self.work_orders.values()

        if status:
            orders = [o for o in orders if o.status == status]
        if priority:
            orders = [o for o in orders if o.priority == priority]
        if work_order_type:
            orders = [o for o in orders if o.work_order_type == work_order_type]
        if assigned_to:
            orders = [o for o in orders if o.assigned_to == assigned_to]

        orders = sorted(orders, key=lambda o: o.created_at, reverse=True)

        return [
            {
                "id": o.id,
                "title": o.title,
                "description": o.description,
                "work_order_type": o.work_order_type,
                "priority": o.priority,
                "equipment_id": o.equipment_id,
                "location": o.location,
                "created_by": o.created_by,
                "assigned_to": o.assigned_to,
                "status": o.status,
                "created_at": o.created_at,
                "assigned_at": o.assigned_at,
                "started_at": o.started_at,
                "completed_at": o.completed_at,
                "estimated_hours": o.estimated_hours,
                "actual_hours": o.actual_hours,
                "comments_count": len(o.comments),
            }
            for o in orders[:limit]
        ]

    def get_my_work_orders(self, user: str) -> List[Dict]:
        """获取我的工单"""
        orders = [
            o
            for o in self.work_orders.values()
            if o.assigned_to == user and o.status != WorkOrderStatus.COMPLETED
        ]
        return [
            {
                "id": o.id,
                "title": o.title,
                "priority": o.priority,
                "status": o.status,
                "created_at": o.created_at,
            }
            for o in orders
        ]

    # ========== 统计 ==========

    def get_statistics(self) -> Dict:
        """获取工单统计"""
        total = len(self.work_orders)

        by_status = {}
        for o in self.work_orders.values():
            by_status[o.status] = by_status.get(o.status, 0) + 1

        by_priority = {}
        for o in self.work_orders.values():
            by_priority[o.priority] = by_priority.get(o.priority, 0) + 1

        by_type = {}
        for o in self.work_orders.values():
            by_type[o.work_order_type] = by_type.get(o.work_order_type, 0) + 1

        # 计算完成率
        completed = by_status.get(WorkOrderStatus.COMPLETED, 0)
        completion_rate = completed / total * 100 if total > 0 else 0

        # 平均处理时间
        completed_orders = [
            o
            for o in self.work_orders.values()
            if o.status == WorkOrderStatus.COMPLETED and o.completed_at and o.started_at
        ]
        if completed_orders:
            total_hours = sum(o.actual_hours for o in completed_orders)
            avg_hours = total_hours / len(completed_orders)
        else:
            avg_hours = 0

        return {
            "total": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_type": by_type,
            "completion_rate": round(completion_rate, 1),
            "avg_completion_hours": round(avg_hours, 1),
        }

    def get_overdue_orders(self) -> List[Dict]:
        """获取逾期工单"""
        overdue = []
        now = datetime.now()

        for wo in self.work_orders.values():
            if wo.status in [
                WorkOrderStatus.PENDING,
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.IN_PROGRESS,
            ]:
                created = datetime.fromisoformat(wo.created_at)
                hours_elapsed = (now - created).total_seconds() / 3600

                # 根据优先级设置逾期阈值
                threshold = {"low": 168, "normal": 72, "high": 24, "urgent": 8}
                priority_threshold = threshold.get(wo.priority, 72)

                if hours_elapsed > priority_threshold:
                    overdue.append(
                        {
                            "id": wo.id,
                            "title": wo.title,
                            "priority": wo.priority,
                            "status": wo.status,
                            "hours_overdue": round(
                                hours_elapsed - priority_threshold, 1
                            ),
                        }
                    )

        return overdue


# 全局实例
_work_order_service: Optional[WorkOrderService] = None


def get_work_order_service() -> WorkOrderService:
    """获取工单服务"""
    global _work_order_service
    if _work_order_service is None:
        _work_order_service = WorkOrderService()
    return _work_order_service
