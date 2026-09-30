"""
报表生成服务
提供设备运行、能耗、告警等报表生成
"""
import json
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class ReportType(str, Enum):
    """报表类型"""

    EQUIPMENT_STATUS = "equipment_status"  # 设备状态报表
    ENERGY_CONSUMPTION = "energy_consumption"  # 能耗分析报表
    ALERT_STATISTICS = "alert_statistics"  # 告警统计报表
    PRODUCTION_REPORT = "production_report"  # 生产运行报表
    MAINTENANCE_REPORT = "maintenance_report"  # 维保报表
    CUSTOM = "custom"  # 自定义报表


class ReportFormat(str, Enum):
    """报表格式"""

    PDF = "pdf"
    EXCEL = "excel"
    HTML = "html"
    JSON = "json"


class Report:
    """报表"""

    def __init__(
        self,
        name: str,
        report_type: str,
        report_format: str,
        date_from: str,
        date_to: str,
        created_by: str = "system",
    ):
        self.id = f"report_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.report_type = report_type
        self.report_format = report_format
        self.date_from = date_from
        self.date_to = date_to
        self.created_by = created_by
        self.created_at = datetime.now().isoformat()
        self.status = "generating"
        self.file_url = None
        self.size = 0


class ReportService:
    """报表生成服务"""

    def __init__(self):
        self.reports: Dict[str, Report] = {}
        self.templates: Dict[str, Dict] = {}

    def _init_templates(self):
        """初始化报表模板"""
        self.templates = {
            "equipment_status": {
                "name": "设备状态报表",
                "sections": ["设备清单", "运行时间", "故障统计", "维保记录"],
            },
            "energy_consumption": {
                "name": "能耗分析报表",
                "sections": ["能耗概览", "分项能耗", "趋势分析", "节能建议"],
            },
            "alert_statistics": {
                "name": "告警统计报表",
                "sections": ["告警汇总", "按级别分布", "按类型分布", "处理情况"],
            },
        }

    # ========== 报表模板 ==========

    def list_templates(self) -> List[Dict]:
        """列出报表模板"""
        return [
            {"id": k, "name": v["name"], "sections": v["sections"]}
            for k, v in self.templates.items()
        ]

    # ========== 报表生成 ==========

    def create_report(
        self,
        name: str,
        report_type: str,
        report_format: str,
        date_from: str,
        date_to: str,
        created_by: str = "system",
    ) -> Report:
        """创建报表"""
        report = Report(
            name=name,
            report_type=report_type,
            report_format=report_format,
            date_from=date_from,
            date_to=date_to,
            created_by=created_by,
        )
        self.reports[report.id] = report
        return report

    def generate_report(self, report_id: str) -> Optional[Report]:
        """生成报表"""
        report = self.reports.get(report_id)
        if not report:
            return None

        # 生成报表数据
        if report.report_type == ReportType.EQUIPMENT_STATUS:
            report.data = self._generate_equipment_report(
                report.date_from, report.date_to
            )
        elif report.report_type == ReportType.ENERGY_CONSUMPTION:
            report.data = self._generate_energy_report(report.date_from, report.date_to)
        elif report.report_type == ReportType.ALERT_STATISTICS:
            report.data = self._generate_alert_report(report.date_from, report.date_to)
        elif report.report_type == ReportType.MAINTENANCE_REPORT:
            report.data = self._generate_maintenance_report(
                report.date_from, report.date_to
            )
        else:
            report.data = {}

        # 基于报告数据量计算文件大小（替代 random.randint）
        report.status = "completed"
        report.file_url = f"/reports/{report.id}.{report.report_format}"
        data_size = len(json.dumps(report.data)) if hasattr(report, 'data') and report.data else 100
        report.size = min(max(data_size * 2, 100), 5000)

        return report

    def _generate_equipment_report(self, date_from: str, date_to: str) -> Dict:
        """生成设备状态报表"""
        return {
            "summary": {
                "total_equipment": 156,
                "online": 142,
                "offline": 8,
                "fault": 6,
                "online_rate": 91.0,
            },
            "equipment_list": [
                {"name": "光伏逆变器A1", "status": "online", "runtime": 2150},
                {"name": "光伏逆变器A2", "status": "online", "runtime": 2080},
                {"name": "光伏逆变器B1", "status": "fault", "runtime": 1520},
                {"name": "配电柜T1", "status": "online", "runtime": 2200},
                {"name": "储能单元S1", "status": "online", "runtime": 1800},
            ],
            "fault_summary": [
                {"type": "通信故障", "count": 3},
                {"type": "过温保护", "count": 2},
                {"type": "电网异常", "count": 1},
            ],
            "runtime_distribution": [
                {"range": "0-500h", "count": 10},
                {"range": "500-1000h", "count": 25},
                {"range": "1000-2000h", "count": 80},
                {"range": "2000h+", "count": 41},
            ],
        }

    def _generate_energy_report(self, date_from: str, date_to: str) -> Dict:
        """生成能耗分析报表"""
        return {
            "summary": {
                "total_consumption": 125680,  # kWh
                "peak_power": 2850,  # kW
                "avg_power": 1650,  # kW
                "power_factor": 0.92,
            },
            "daily_data": [
                {"date": "2024-01-01", "consumption": 4250, "cost": 3400},
                {"date": "2024-01-02", "consumption": 4380, "cost": 3504},
                {"date": "2024-01-03", "consumption": 4120, "cost": 3296},
                {"date": "2024-01-04", "consumption": 4450, "cost": 3560},
                {"date": "2024-01-05", "consumption": 4200, "cost": 3360},
            ],
            "by_equipment": [
                {"name": "光伏逆变器", "consumption": 52680, "percent": 41.9},
                {"name": "储能系统", "consumption": 25136, "percent": 20.0},
                {"name": "空调系统", "consumption": 18852, "percent": 15.0},
                {"name": "照明系统", "consumption": 6278, "percent": 5.0},
                {"name": "其他", "consumption": 22774, "percent": 18.1},
            ],
            "recommendations": [
                {
                    "type": "节能建议",
                    "content": "建议在13:00-15:00峰值时段降低非必要负载",
                    "potential": "5%",
                },
                {"type": "节能建议", "content": "储能系统可在电价低谷时段充电", "potential": "8%"},
            ],
        }

    def _generate_alert_report(self, date_from: str, date_to: str) -> Dict:
        """生成告警统计报表"""
        return {
            "summary": {
                "total_alerts": 326,
                "critical": 12,
                "error": 45,
                "warning": 156,
                "info": 113,
            },
            "by_level": [
                {"level": "critical", "count": 12, "color": "#ff0000"},
                {"level": "error", "count": 45, "color": "#ff6600"},
                {"level": "warning", "count": 156, "color": "#ffcc00"},
                {"level": "info", "count": 113, "color": "#0066ff"},
            ],
            "by_type": [
                {"type": "温度告警", "count": 85},
                {"type": "通信告警", "count": 62},
                {"type": "电压告警", "count": 58},
                {"type": "功率告警", "count": 47},
                {"type": "其他", "count": 74},
            ],
            "handling_rate": {"handled": 298, "pending": 28, "rate": 91.4},
            "top_devices": [
                {"device": "逆变器A3", "alerts": 28},
                {"device": "配电柜B2", "alerts": 22},
                {"device": "储能单元S1", "alerts": 18},
            ],
        }

    def _generate_maintenance_report(self, date_from: str, date_to: str) -> Dict:
        """生成维保报表"""
        return {
            "summary": {
                "total_work_orders": 45,
                "completed": 38,
                "in_progress": 5,
                "pending": 2,
                "completion_rate": 84.4,
            },
            "by_type": [
                {"type": "日常维护", "count": 20},
                {"type": "故障维修", "count": 15},
                {"type": "定期巡检", "count": 8},
                {"type": "备件更换", "count": 2},
            ],
            "by_priority": [
                {"priority": "紧急", "count": 8},
                {"priority": "高", "count": 12},
                {"priority": "中", "count": 18},
                {"priority": "低", "count": 7},
            ],
            "work_orders": [
                {
                    "id": "wo_001",
                    "title": "光伏板清洁",
                    "type": "日常维护",
                    "status": "completed",
                    "hours": 4,
                },
                {
                    "id": "wo_002",
                    "title": "逆变器检修",
                    "type": "故障维修",
                    "status": "completed",
                    "hours": 8,
                },
                {
                    "id": "wo_003",
                    "title": "月度巡检",
                    "type": "定期巡检",
                    "status": "in_progress",
                    "hours": 2,
                },
            ],
            "cost_analysis": {
                "labor_cost": 15600,
                "material_cost": 8500,
                "total_cost": 24100,
                "avg_per_order": 535,
            },
        }

    # ========== 报表查询 ==========

    def get_report(self, report_id: str) -> Optional[Report]:
        """获取报表"""
        return self.reports.get(report_id)

    def list_reports(
        self, report_type: str = None, status: str = None, limit: int = 50
    ) -> List[Dict]:
        """列出报表"""
        reports = self.reports.values()

        if report_type:
            reports = [r for r in reports if r.report_type == report_type]
        if status:
            reports = [r for r in reports if r.status == status]

        reports = sorted(reports, key=lambda r: r.created_at, reverse=True)

        return [
            {
                "id": r.id,
                "name": r.name,
                "report_type": r.report_type,
                "report_format": r.report_format,
                "date_from": r.date_from,
                "date_to": r.date_to,
                "created_by": r.created_by,
                "created_at": r.created_at,
                "status": r.status,
                "file_url": r.file_url,
                "size": r.size,
            }
            for r in reports[:limit]
        ]

    # ========== 报表下载 ==========

    def download_report(self, report_id: str) -> Optional[Dict]:
        """下载报表"""
        report = self.reports.get(report_id)
        if not report:
            return None

        if report.status != "completed":
            return {"error": "报表正在生成中"}

        return {
            "file_url": report.file_url,
            "filename": f"{report.name}.{report.report_format}",
            "size": report.size,
            "content_type": f"application/{report.report_format}",
        }


# 全局实例
_report_service: Optional[ReportService] = None


def get_report_service() -> ReportService:
    """获取报表服务"""
    global _report_service
    if _report_service is None:
        _report_service = ReportService()
        _report_service._init_templates()
    return _report_service
