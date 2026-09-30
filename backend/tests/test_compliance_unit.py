# -*- coding: utf-8 -*-
"""
安全合规单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.compliance import (
    list_checks,
    run_check,
    run_all_checks,
    list_reports,
    generate_report,
    get_report,
    list_findings,
    resolve_finding,
    get_compliance_stats,
    list_standards
)


class TestComplianceUnit:
    """安全合规单元测试"""

    @pytest.fixture
    def mock_compliance_service(self):
        """模拟合规服务"""
        with patch('api.compliance.get_compliance_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_checks_all(self, mock_compliance_service):
        """测试列出所有合规检查项"""
        mock_checks = [{"id": "check1", "name": "Password Policy"}, {"id": "check2", "name": "Firewall Rules"}]
        mock_compliance_service.list_checks.return_value = mock_checks
        
        result = list_checks()
        assert len(result) == 2
        mock_compliance_service.list_checks.assert_called_once_with(None, None)

    def test_list_checks_filtered(self, mock_compliance_service):
        """测试带过滤条件列出合规检查项"""
        mock_checks = [{"id": "check1", "name": "GB22239 Check", "standard": "gb22239"}]
        mock_compliance_service.list_checks.return_value = mock_checks
        
        result = list_checks("gb22239", "passed")
        assert len(result) == 1
        mock_compliance_service.list_checks.assert_called_once_with("gb22239", "passed")

    def test_run_check_exists(self, mock_compliance_service):
        """测试执行存在的检查项"""
        mock_check = Mock()
        mock_check.id = "check123"
        mock_check.name = "Security Audit"
        mock_check.status = "passed"
        mock_check.last_checked = "2026-04-07T20:00:00Z"
        mock_compliance_service.run_check.return_value = mock_check
        
        result = run_check("check123")
        assert result["id"] == "check123"
        assert result["name"] == "Security Audit"
        assert result["status"] == "passed"
        mock_compliance_service.run_check.assert_called_once_with("check123")

    def test_run_check_not_exists(self, mock_compliance_service):
        """测试执行不存在的检查项"""
        mock_compliance_service.run_check.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            run_check("nonexistent")
        assert exc_info.value.status_code == 404

    def test_run_all_checks_default(self, mock_compliance_service):
        """测试执行所有检查（默认标准）"""
        mock_result = {"total": 25, "passed": 20, "failed": 3, "warning": 2}
        mock_compliance_service.run_all_checks.return_value = mock_result
        
        result = run_all_checks()
        assert result["total"] == 25
        mock_compliance_service.run_all_checks.assert_called_once_with(None)

    def test_run_all_checks_specific_standard(self, mock_compliance_service):
        """测试执行特定标准的所有检查"""
        mock_result = {"total": 15, "passed": 12, "failed": 2, "warning": 1}
        mock_compliance_service.run_all_checks.return_value = mock_result
        
        result = run_all_checks("gdpr")
        assert result["total"] == 15
        mock_compliance_service.run_all_checks.assert_called_once_with("gdpr")

    def test_list_reports_all(self, mock_compliance_service):
        """测试列出所有合规报告"""
        mock_reports = [{"id": "report1", "name": "Q1 Compliance"}, {"id": "report2", "name": "Annual Audit"}]
        mock_compliance_service.list_reports.return_value = mock_reports
        
        result = list_reports()
        assert len(result) == 2
        mock_compliance_service.list_reports.assert_called_once_with(None)

    def test_list_reports_by_standard(self, mock_compliance_service):
        """测试按标准列出合规报告"""
        mock_reports = [{"id": "report1", "name": "GDPR Report", "standard": "gdpr"}]
        mock_compliance_service.list_reports.return_value = mock_reports
        
        result = list_reports("gdpr")
        assert len(result) == 1
        mock_compliance_service.list_reports.assert_called_once_with("gdpr")

    def test_generate_report(self, mock_compliance_service):
        """测试生成合规报告"""
        mock_report = Mock()
        mock_report.id = "report123"
        mock_report.name = "Security Assessment"
        mock_report.standard = "iso27001"
        mock_report.status = "completed"
        mock_report.overall_score = 85.5
        mock_report.total_checks = 50
        mock_report.passed_checks = 45
        mock_report.failed_checks = 5
        mock_compliance_service.generate_report.return_value = mock_report
        
        req = {"name": "Security Assessment", "standard": "iso27001"}
        result = generate_report(req)
        assert result["id"] == "report123"
        assert result["name"] == "Security Assessment"
        assert result["overall_score"] == 85.5
        assert result["total_checks"] == 50
        mock_compliance_service.generate_report.assert_called_once()

    def test_get_report_exists(self, mock_compliance_service):
        """测试获取存在的报告"""
        mock_report = Mock()
        mock_report.id = "report123"
        mock_report.name = "Compliance Report"
        mock_report.standard = "soc2"
        mock_report.status = "completed"
        mock_report.overall_score = 92.0
        mock_report.total_checks = 30
        mock_report.passed_checks = 28
        mock_report.failed_checks = 1
        mock_report.warning_checks = 1
        mock_report.findings = [{"id": "finding1", "severity": "high"}]
        mock_report.created_at = "2026-04-07T10:00:00Z"
        mock_report.completed_at = "2026-04-07T10:30:00Z"
        mock_compliance_service.get_report.return_value = mock_report
        
        result = get_report("report123")
        assert result["id"] == "report123"
        assert result["findings"] == [{"id": "finding1", "severity": "high"}]
        assert result["created_at"] == "2026-04-07T10:00:00Z"
        mock_compliance_service.get_report.assert_called_once_with("report123")

    def test_get_report_not_exists(self, mock_compliance_service):
        """测试获取不存在的报告"""
        mock_compliance_service.get_report.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_report("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_findings_all(self, mock_compliance_service):
        """测试列出所有安全发现"""
        mock_findings = [{"id": "finding1", "severity": "high"}, {"id": "finding2", "severity": "medium"}]
        mock_compliance_service.list_findings.return_value = mock_findings
        
        result = list_findings()
        assert len(result) == 2
        mock_compliance_service.list_findings.assert_called_once_with(None, None)

    def test_list_findings_filtered(self, mock_compliance_service):
        """测试带过滤条件列出安全发现"""
        mock_findings = [{"id": "finding1", "severity": "critical", "status": "open"}]
        mock_compliance_service.list_findings.return_value = mock_findings
        
        result = list_findings("critical", "open")
        assert len(result) == 1
        mock_compliance_service.list_findings.assert_called_once_with("critical", "open")

    def test_resolve_finding_exists(self, mock_compliance_service):
        """测试解决存在的安全发现"""
        mock_finding = Mock()
        mock_finding.status = "resolved"
        mock_compliance_service.resolve_finding.return_value = mock_finding
        
        result = resolve_finding("finding123")
        assert result["message"] == "已解决"
        assert result["status"] == "resolved"
        mock_compliance_service.resolve_finding.assert_called_once_with("finding123")

    def test_resolve_finding_not_exists(self, mock_compliance_service):
        """测试解决不存在的安全发现"""
        mock_compliance_service.resolve_finding.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_finding("nonexistent")
        assert exc_info.value.status_code == 404

    def test_get_compliance_stats(self, mock_compliance_service):
        """测试获取合规统计"""
        mock_stats = {
            "total_reports": 15,
            "total_checks": 500,
            "passed_checks": 450,
            "failed_checks": 30,
            "warning_checks": 20,
            "compliance_score": 90.0,
            "standards_used": ["gb22239", "iso27001", "gdpr"]
        }
        mock_compliance_service.get_compliance_stats.return_value = mock_stats
        
        result = get_compliance_stats()
        assert result == mock_stats
        mock_compliance_service.get_compliance_stats.assert_called_once()

    def test_list_standards(self):
        """测试列出支持的合规标准"""
        result = list_standards()
        assert "standards" in result
        standards = result["standards"]
        assert len(standards) > 0
        
        # Check that it contains expected standards
        standard_values = [s["value"] for s in standards]
        assert "gb22239" in standard_values
        assert "gdpr" in standard_values
        assert "iso27001" in standard_values
        assert "soc2" in standard_values
        
        # Check descriptions
        descriptions = [s["description"] for s in standards]
        assert any("等保2.0" in desc for desc in descriptions)
        assert any("GDPR" in desc or "通用数据保护" in desc for desc in descriptions)
        assert any("ISO27001" in desc for desc in descriptions)
        assert any("SOC2" in desc for desc in descriptions)