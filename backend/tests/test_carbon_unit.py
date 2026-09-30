# -*- coding: utf-8 -*-
"""
碳排放单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.carbon import (
    list_emission_factors,
    add_emission_factor,
    calculate_emission,
    record_emission,
    get_emissions,
    get_total_emission,
    get_emission_trend,
    create_carbon_credit,
    get_carbon_balance,
    get_reduction_suggestions
)


class TestCarbonUnit:
    """碳排放单元测试"""

    @pytest.fixture
    def mock_carbon_service(self):
        """模拟碳排放服务"""
        with patch('api.carbon.get_carbon_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_emission_factors(self, mock_carbon_service):
        """测试列出排放因子"""
        mock_factors = [{"id": "factor1", "name": "electricity"}, {"id": "factor2", "name": "gas"}]
        mock_carbon_service.list_emission_factors.return_value = mock_factors
        
        result = list_emission_factors()
        assert len(result) == 2
        mock_carbon_service.list_emission_factors.assert_called_once()

    def test_add_emission_factor(self, mock_carbon_service):
        """测试添加排放因子"""
        mock_factor = Mock()
        mock_factor.id = "new_factor"
        mock_factor.name = "custom_factor"
        mock_factor.factor_value = 0.5
        mock_carbon_service.add_emission_factor.return_value = mock_factor
        
        req = {"name": "custom_factor", "factor_value": 0.5, "unit": "kg/kwh"}
        result = add_emission_factor(req)
        assert result["id"] == "new_factor"
        assert result["name"] == "custom_factor"
        assert result["factor_value"] == 0.5
        mock_carbon_service.add_emission_factor.assert_called_once()

    def test_calculate_emission(self, mock_carbon_service):
        """测试计算碳排放"""
        mock_emission = Mock()
        mock_emission.id = "emission123"
        mock_emission.emission_amount = 150.5
        mock_emission.unit = "tCO2"
        mock_carbon_service.calculate_emission.return_value = mock_emission
        
        req = {
            "entity_id": "entity1",
            "scope": "scope1",
            "source": "electricity",
            "activity_data": 1000,
            "factor_name": "electricity_factor"
        }
        result = calculate_emission(req)
        assert result["id"] == "emission123"
        assert result["emission_amount"] == 150.5
        assert result["unit"] == "tCO2"
        mock_carbon_service.calculate_emission.assert_called_once()

    def test_record_emission(self, mock_carbon_service):
        """测试记录碳排放"""
        mock_emission = Mock()
        mock_emission.id = "record123"
        mock_emission.entity_id = "entity1"
        mock_emission.emission_amount = 200.0
        mock_carbon_service.record_emission.return_value = mock_emission
        
        req = {
            "entity_id": "entity1",
            "scope": "scope2",
            "source": "gas",
            "emission_amount": 200.0
        }
        result = record_emission(req)
        assert result["id"] == "record123"
        assert result["entity_id"] == "entity1"
        assert result["emission_amount"] == 200.0
        mock_carbon_service.record_emission.assert_called_once()

    def test_get_emissions_all(self, mock_carbon_service):
        """测试查询所有碳排放"""
        mock_emissions = [{"id": "e1", "amount": 100}, {"id": "e2", "amount": 200}]
        mock_carbon_service.get_emissions.return_value = mock_emissions
        
        result = get_emissions()
        assert len(result) == 2
        mock_carbon_service.get_emissions.assert_called_once_with(None, None, None, None)

    def test_get_emissions_with_filters(self, mock_carbon_service):
        """测试带过滤条件查询碳排放"""
        mock_emissions = [{"id": "e1", "amount": 150}]
        mock_carbon_service.get_emissions.return_value = mock_emissions
        
        start_time = "2026-01-01T00:00:00"
        end_time = "2026-12-31T23:59:59"
        result = get_emissions("entity1", "scope1", start_time, end_time)
        assert len(result) == 1
        mock_carbon_service.get_emissions.assert_called_once_with(
            "entity1", "scope1", 
            datetime.fromisoformat("2026-01-01T00:00:00"),
            datetime.fromisoformat("2026-12-31T23:59:59")
        )

    def test_get_total_emission(self, mock_carbon_service):
        """测试获取总排放量"""
        mock_result = {"total": 500.5, "unit": "tCO2"}
        mock_carbon_service.get_total_emission.return_value = mock_result
        
        result = get_total_emission("entity1")
        assert result == mock_result
        mock_carbon_service.get_total_emission.assert_called_once_with("entity1", None, None)

    def test_get_total_emission_with_dates(self, mock_carbon_service):
        """测试带日期范围获取总排放量"""
        mock_result = {"total": 300.0, "unit": "tCO2"}
        mock_carbon_service.get_total_emission.return_value = mock_result
        
        start_time = "2026-01-01T00:00:00"
        end_time = "2026-06-30T23:59:59"
        result = get_total_emission("entity1", start_time, end_time)
        assert result == mock_result
        mock_carbon_service.get_total_emission.assert_called_once_with(
            "entity1",
            datetime.fromisoformat("2026-01-01T00:00:00"),
            datetime.fromisoformat("2026-06-30T23:59:59")
        )

    def test_get_emission_trend_default(self, mock_carbon_service):
        """测试获取默认排放趋势"""
        mock_trend = [{"date": "2026-04-01", "emission": 10.5}, {"date": "2026-04-02", "emission": 12.3}]
        mock_carbon_service.get_emission_trend.return_value = mock_trend
        
        result = get_emission_trend("entity1")
        assert len(result) == 2
        mock_carbon_service.get_emission_trend.assert_called_once_with("entity1", "day", 30)

    def test_get_emission_trend_custom(self, mock_carbon_service):
        """测试获取自定义排放趋势"""
        mock_trend = [{"date": "2026-04", "emission": 300.0}]
        mock_carbon_service.get_emission_trend.return_value = mock_trend
        
        result = get_emission_trend("entity1", "month", 12)
        assert len(result) == 1
        mock_carbon_service.get_emission_trend.assert_called_once_with("entity1", "month", 12)

    def test_create_carbon_credit(self, mock_carbon_service):
        """测试创建碳配额"""
        mock_credit = Mock()
        mock_credit.id = "credit123"
        mock_credit.amount = 1000.0
        mock_credit.credit_type = "renewable"
        mock_carbon_service.create_carbon_credit.return_value = mock_credit
        
        req = {
            "entity_id": "entity1",
            "credit_type": "renewable",
            "amount": 1000.0,
            "expiration_date": "2027-12-31"
        }
        result = create_carbon_credit(req)
        assert result["id"] == "credit123"
        assert result["amount"] == 1000.0
        assert result["credit_type"] == "renewable"
        mock_carbon_service.create_carbon_credit.assert_called_once()

    def test_get_carbon_balance(self, mock_carbon_service):
        """测试获取碳配额余额"""
        mock_balance = {"available": 850.5, "used": 149.5, "total": 1000.0}
        mock_carbon_service.get_carbon_balance.return_value = mock_balance
        
        result = get_carbon_balance("entity1")
        assert result == mock_balance
        mock_carbon_service.get_carbon_balance.assert_called_once_with("entity1")

    def test_get_reduction_suggestions(self, mock_carbon_service):
        """测试获取减排建议"""
        mock_suggestions = [
            {"id": "s1", "title": "Switch to LED", "potential_saving": 50.0},
            {"id": "s2", "title": "Optimize HVAC", "potential_saving": 120.0}
        ]
        mock_carbon_service.get_reduction_suggestions.return_value = mock_suggestions
        
        result = get_reduction_suggestions("entity1")
        assert len(result) == 2
        assert result[0]["title"] == "Switch to LED"
        mock_carbon_service.get_reduction_suggestions.assert_called_once_with("entity1")