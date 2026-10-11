"""
安全合规 API
"""
from fastapi import APIRouter, HTTPException
from services.compliance_service import ComplianceStandard, get_compliance_service

router = APIRouter(prefix="/compliance", tags=["安全合规"])


# ========== 合规检查 ==========


@router.get("/checks")
def list_checks(standard: str = None, status: str = None):
    """列出合规检查项"""
    service = get_compliance_service()
    return service.list_checks(standard, status)


@router.post("/checks/{check_id}/run")
def run_check(check_id: str):
    """执行检查"""
    service = get_compliance_service()
    check = service.run_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail="检查项不存在")
    return {
        "id": check.id,
        "name": check.name,
        "status": check.status,
        "last_checked": check.last_checked,
    }


@router.post("/checks/run")
def run_all_checks(standard: str = None):
    """执行所有检查"""
    service = get_compliance_service()
    return service.run_all_checks(standard)


# ========== 合规报告 ==========


@router.get("/reports")
def list_reports(standard: str = None):
    """列出合规报告"""
    service = get_compliance_service()
    return service.list_reports(standard)


@router.post("/reports")
def generate_report(req: dict):
    """生成合规报告"""
    service = get_compliance_service()
    report = service.generate_report(
        name=req.get("name"), standard=req.get("standard", ComplianceStandard.GB22239)
    )
    return {
        "id": report.id,
        "name": report.name,
        "standard": report.standard,
        "status": report.status,
        "overall_score": report.overall_score,
        "total_checks": report.total_checks,
        "passed_checks": report.passed_checks,
        "failed_checks": report.failed_checks,
    }


@router.get("/reports/{report_id}")
def get_report(report_id: str):
    """获取报告"""
    service = get_compliance_service()
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "id": report.id,
        "name": report.name,
        "standard": report.standard,
        "status": report.status,
        "overall_score": report.overall_score,
        "total_checks": report.total_checks,
        "passed_checks": report.passed_checks,
        "failed_checks": report.failed_checks,
        "warning_checks": report.warning_checks,
        "findings": report.findings,
        "created_at": report.created_at,
        "completed_at": report.completed_at,
    }


# ========== 安全发现 ==========


@router.get("/findings")
def list_findings(severity: str = None, status: str = None):
    """列出安全发现"""
    service = get_compliance_service()
    return service.list_findings(severity, status)


@router.post("/findings/{finding_id}/resolve")
def resolve_finding(finding_id: str):
    """解决安全发现"""
    service = get_compliance_service()
    finding = service.resolve_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="发现不存在")
    return {"message": "已解决", "status": finding.status}


# ========== 统计 ==========


@router.get("/stats")
def get_compliance_stats():
    """获取合规统计"""
    service = get_compliance_service()
    return service.get_compliance_stats()


# ========== 合规标准 ==========


@router.get("/standards")
def list_standards():
    """列出支持的合规标准"""
    return {
        "standards": [
            {"value": s.value, "description": _get_standard_description(s.value)}
            for s in ComplianceStandard
        ]
    }


def _get_standard_description(standard: str) -> str:
    """获取标准描述"""
    descriptions = {
        "gb22239": "等保2.0 - 中国网络安全等级保护",
        "gdpr": "欧盟通用数据保护条例",
        "iso27001": "ISO27001信息安全管理体系",
        "soc2": "SOC2服务组织控制审计",
    }
    return descriptions.get(standard, "")
