"""
安全合规服务
提供等保/GDPR合规报告自动生成、安全审计、合规检查项
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ComplianceStandard(str, Enum):
    """合规标准"""

    GB22239 = "gb22239"  # 等保2.0
    GDPR = "gdpr"  # 欧盟通用数据保护条例
    ISO27001 = "iso27001"  # ISO27001信息安全管理
    SOC2 = "soc2"  # SOC2审计


class CheckStatus(str, Enum):
    """检查状态"""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"


class ComplianceCheck:
    """合规检查项"""

    def __init__(
        self, name: str, standard: str, category: str, description: str, weight: int = 1
    ):
        self.id = f"check_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.standard = standard
        self.category = category
        self.description = description
        self.weight = weight
        self.status = CheckStatus.NOT_APPLICABLE
        self.details = None
        self.last_checked = None


class ComplianceReport:
    """合规报告"""

    def __init__(self, name: str, standard: str):
        self.id = f"report_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.standard = standard
        self.status = "generating"
        self.overall_score = 0
        self.total_checks = 0
        self.passed_checks = 0
        self.failed_checks = 0
        self.warning_checks = 0
        self.findings: List[Dict] = []
        self.created_at = datetime.now().isoformat()
        self.completed_at = None


class SecurityFinding:
    """安全发现"""

    def __init__(
        self,
        severity: str,  # critical, high, medium, low, info
        title: str,
        description: str,
        check_id: str,
        recommendation: str = None,
    ):
        self.id = f"finding_{uuid.uuid4().hex[:12]}"
        self.severity = severity
        self.title = title
        self.description = description
        self.check_id = check_id
        self.recommendation = recommendation
        self.status = "open"
        self.created_at = datetime.now().isoformat()
        self.resolved_at = None


class ComplianceService:
    """安全合规服务"""

    def __init__(self):
        self.checks: Dict[str, ComplianceCheck] = {}
        self.reports: Dict[str, ComplianceReport] = {}
        self.findings: Dict[str, SecurityFinding] = {}
        self._init_default_checks()

    def _init_default_checks(self):
        """初始化默认检查项"""
        # 等保2.0检查项
        gb22239_checks = [
            ("身份鉴别", "应对登录的用户进行身份标识和鉴别", ["检查是否启用密码策略", "检查是否有多因素认证"]),
            ("访问控制", "应启用访问控制功能", ["检查是否配置RBAC", "检查权限分配"]),
            ("安全审计", "应启用安全审计功能", ["检查审计日志配置", "检查日志存储"]),
            ("数据完整性和保密性", "应采用加密技术保护数据", ["检查数据传输加密", "检查数据存储加密"]),
            ("备份恢复", "应提供数据备份和恢复功能", ["检查备份策略", "检查恢复测试"]),
        ]

        # GDPR检查项
        gdpr_checks = [
            ("数据收集", "合法收集个人数据", ["检查隐私政策", "检查数据收集同意"]),
            ("数据处理", "合法处理个人数据", ["检查处理合法性基础", "检查数据处理记录"]),
            ("数据主体权利", "保障数据主体权利", ["检查访问权实现", "检查删除权实现"]),
            ("数据泄露通知", "数据泄露时及时通知", ["检查泄露响应流程", "检查通知机制"]),
        ]

        # 创建检查项
        for category, description, _ in gb22239_checks:
            check = ComplianceCheck(
                name=category,
                standard=ComplianceStandard.GB22239,
                category="安全",
                description=description,
            )
            self.checks[check.id] = check

        for category, description, _ in gdpr_checks:
            check = ComplianceCheck(
                name=category,
                standard=ComplianceStandard.GDPR,
                category="隐私",
                description=description,
            )
            self.checks[check.id] = check

    # ========== 合规检查 ==========

    def run_check(self, check_id: str) -> Optional[ComplianceCheck]:
        """Do not invent a compliance conclusion before a real probe exists."""
        check = self.checks.get(check_id)
        if not check:
            return None
        check.status = CheckStatus.NOT_APPLICABLE
        check.details = "该检查项尚未接入自动核验器"
        check.last_checked = None
        return check

    def run_all_checks(self, standard: str = None) -> List[Dict]:
        """执行所有检查"""
        checks = self.checks.values()
        if standard:
            checks = [c for c in checks if c.standard == standard]

        results = []
        for check in checks:
            self.run_check(check.id)
            results.append(
                {
                    "id": check.id,
                    "name": check.name,
                    "standard": check.standard,
                    "status": check.status,
                    "last_checked": check.last_checked,
                }
            )

        return results

    def get_check(self, check_id: str) -> Optional[ComplianceCheck]:
        """获取检查项"""
        return self.checks.get(check_id)

    def list_checks(self, standard: str = None, status: str = None) -> List[Dict]:
        """列出检查项"""
        checks = self.checks.values()
        if standard:
            checks = [c for c in checks if c.standard == standard]
        if status:
            checks = [c for c in checks if c.status == status]

        return [
            {
                "id": c.id,
                "name": c.name,
                "standard": c.standard,
                "category": c.category,
                "description": c.description,
                "details": c.details,
                "weight": c.weight,
                "status": c.status,
                "last_checked": c.last_checked,
            }
            for c in checks
        ]

    # ========== 合规报告 ==========

    def generate_report(self, name: str, standard: str) -> ComplianceReport:
        """生成合规报告"""
        report = ComplianceReport(name, standard)
        self.reports[report.id] = report

        # 执行检查
        checks = [c for c in self.checks.values() if c.standard == standard]
        report.total_checks = len(checks)

        for check in checks:
            self.run_check(check.id)
            if check.status == CheckStatus.PASS:
                report.passed_checks += 1
            elif check.status == CheckStatus.FAIL:
                report.failed_checks += 1
                # 生成发现
                finding = SecurityFinding(
                    severity="high",
                    title=f"合规项未通过: {check.name}",
                    description=check.description,
                    check_id=check.id,
                    recommendation="请立即修复",
                )
                self.findings[finding.id] = finding
                report.findings.append(
                    {
                        "id": finding.id,
                        "severity": finding.severity,
                        "title": finding.title,
                    }
                )
            elif check.status == CheckStatus.WARNING:
                report.warning_checks += 1

        unavailable = bool(checks) and all(
            check.status == CheckStatus.NOT_APPLICABLE for check in checks
        )
        if unavailable:
            report.status = "unavailable"
            report.overall_score = None
        elif report.total_checks > 0:
            report.overall_score = round(
                report.passed_checks / report.total_checks * 100, 2
            )
            report.status = "completed"
        report.completed_at = datetime.now().isoformat()

        return report

    def get_report(self, report_id: str) -> Optional[ComplianceReport]:
        """获取报告"""
        return self.reports.get(report_id)

    def list_reports(self, standard: str = None) -> List[Dict]:
        """列出报告"""
        reports = self.reports.values()
        if standard:
            reports = [r for r in reports if r.standard == standard]

        return [
            {
                "id": r.id,
                "name": r.name,
                "standard": r.standard,
                "status": r.status,
                "overall_score": r.overall_score,
                "total_checks": r.total_checks,
                "passed_checks": r.passed_checks,
                "failed_checks": r.failed_checks,
                "warning_checks": r.warning_checks,
                "created_at": r.created_at,
                "completed_at": r.completed_at,
            }
            for r in reports
        ]

    # ========== 安全发现 ==========

    def list_findings(self, severity: str = None, status: str = None) -> List[Dict]:
        """列出安全发现"""
        findings = self.findings.values()
        if severity:
            findings = [f for f in findings if f.severity == severity]
        if status:
            findings = [f for f in findings if f.status == status]

        return [
            {
                "id": f.id,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
                "check_id": f.check_id,
                "recommendation": f.recommendation,
                "status": f.status,
                "created_at": f.created_at,
                "resolved_at": f.resolved_at,
            }
            for f in findings
        ]

    def resolve_finding(self, finding_id: str) -> Optional[SecurityFinding]:
        """解决安全发现"""
        finding = self.findings.get(finding_id)
        if not finding:
            return None

        finding.status = "resolved"
        finding.resolved_at = datetime.now().isoformat()
        return finding

    # ========== 统计 ==========

    def get_compliance_stats(self) -> Dict:
        """获取合规统计"""
        total_checks = len(self.checks)
        passed = sum(1 for c in self.checks.values() if c.status == CheckStatus.PASS)
        failed = sum(1 for c in self.checks.values() if c.status == CheckStatus.FAIL)
        warning = sum(
            1 for c in self.checks.values() if c.status == CheckStatus.WARNING
        )

        total_findings = len(self.findings)
        critical_findings = sum(
            1 for f in self.findings.values() if f.severity == "critical"
        )
        high_findings = sum(1 for f in self.findings.values() if f.severity == "high")

        return {
            "total_checks": total_checks,
            "passed": passed,
            "failed": failed,
            "warning": warning,
            "pass_rate": round(passed / total_checks * 100, 2)
            if total_checks > 0
            else 0,
            "total_findings": total_findings,
            "critical_findings": critical_findings,
            "high_findings": high_findings,
            "total_reports": len(self.reports),
        }


# 全局实例
_compliance_service: Optional[ComplianceService] = None


def get_compliance_service() -> ComplianceService:
    """获取合规服务"""
    global _compliance_service
    if _compliance_service is None:
        _compliance_service = ComplianceService()
    return _compliance_service
