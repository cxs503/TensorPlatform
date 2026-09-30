# -*- coding: utf-8 -*-
"""
代码生成单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.code_generator import (
    generate_code,
    optimize_code,
    generate_tests,
    explain_code,
    list_models,
    chat
)
from api.code_generator import CodeGenerateRequest, CodeOptimizeRequest, TestGenerateRequest, CodeExplainRequest


class TestCodeGeneratorUnit:
    """代码生成单元测试"""

    @pytest.fixture
    def mock_llm_service(self):
        """模拟LLM服务"""
        with patch('api.code_generator.get_llm_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    @pytest.mark.asyncio
    async def test_generate_code_success(self, mock_llm_service):
        """测试成功生成代码"""
        mock_llm_service.generate_code.return_value = "def hello():\n    print('Hello World')"
        
        req = CodeGenerateRequest(requirement="Create a hello world function", language="python")
        result = await generate_code(req)
        assert result["success"] is True
        assert "Hello World" in result["code"]
        assert result["language"] == "python"
        mock_llm_service.generate_code.assert_called_once_with(
            requirement="Create a hello world function", language="python", model=None
        )

    @pytest.mark.asyncio
    async def test_generate_code_failure(self, mock_llm_service):
        """测试生成代码失败"""
        mock_llm_service.generate_code.side_effect = Exception("LLM service unavailable")
        
        from fastapi import HTTPException
        req = CodeGenerateRequest(requirement="Create function", language="python")
        with pytest.raises(HTTPException) as exc_info:
            await generate_code(req)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_optimize_code_success(self, mock_llm_service):
        """测试成功优化代码"""
        mock_result = {
            "optimized_code": "def optimized_hello():\n    print('Optimized Hello')",
            "explanation": "Made the function more efficient"
        }
        mock_llm_service.optimize_code.return_value = mock_result
        
        req = CodeOptimizeRequest(code="def hello():\n    print('Hello')", language="python")
        result = await optimize_code(req)
        assert result["success"] is True
        assert "Optimized Hello" in result["optimized_code"]
        assert "efficient" in result["explanation"]
        assert result["original_code"] == "def hello():\n    print('Hello')"
        mock_llm_service.optimize_code.assert_called_once_with(
            code="def hello():\n    print('Hello')", language="python", model=None
        )

    @pytest.mark.asyncio
    async def test_optimize_code_failure(self, mock_llm_service):
        """测试优化代码失败"""
        mock_llm_service.optimize_code.side_effect = Exception("Optimization failed")
        
        from fastapi import HTTPException
        req = CodeOptimizeRequest(code="def test(): pass", language="python")
        with pytest.raises(HTTPException) as exc_info:
            await optimize_code(req)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generate_tests_success(self, mock_llm_service):
        """测试成功生成测试代码"""
        mock_llm_service.generate_tests.return_value = "def test_hello():\n    assert hello() == 'Hello'"
        
        req = TestGenerateRequest(code="def hello():\n    return 'Hello'", language="python")
        result = await generate_tests(req)
        assert result["success"] is True
        assert "test_hello" in result["test_code"]
        assert result["language"] == "python"
        mock_llm_service.generate_tests.assert_called_once_with(
            code="def hello():\n    return 'Hello'", language="python", model=None
        )

    @pytest.mark.asyncio
    async def test_generate_tests_failure(self, mock_llm_service):
        """测试生成测试代码失败"""
        mock_llm_service.generate_tests.side_effect = Exception("Test generation failed")
        
        from fastapi import HTTPException
        req = TestGenerateRequest(code="def func(): pass", language="python")
        with pytest.raises(HTTPException) as exc_info:
            await generate_tests(req)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_explain_code_success(self, mock_llm_service):
        """测试成功解释代码"""
        mock_llm_service.explain_code.return_value = "This function prints 'Hello World' to the console."
        
        req = CodeExplainRequest(code="print('Hello World')")
        result = await explain_code(req)
        assert result["success"] is True
        assert "prints" in result["explanation"]
        assert "Hello World" in result["explanation"]
        mock_llm_service.explain_code.assert_called_once_with(code="print('Hello World')", model=None)

    @pytest.mark.asyncio
    async def test_explain_code_failure(self, mock_llm_service):
        """测试解释代码失败"""
        mock_llm_service.explain_code.side_effect = Exception("Explanation failed")
        
        from fastapi import HTTPException
        req = CodeExplainRequest(code="invalid code")
        with pytest.raises(HTTPException) as exc_info:
            await explain_code(req)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_list_models(self):
        """测试列出模型"""
        result = await list_models()
        assert result["models"] is not None
        assert len(result["models"]) > 0
        # Check that it contains expected models
        model_ids = [model["id"] for model in result["models"]]
        assert "minimax/MiniMax-M2.1" in model_ids
        assert "meta-llama/llama-3.1-8b-instruct" in model_ids
        assert "qwen/qwen-2-72b-instruct" in model_ids

    @pytest.mark.asyncio
    async def test_chat_success(self, mock_llm_service):
        """测试成功对话"""
        mock_llm_service.chat.return_value = "Hello! How can I help you today?"
        
        result = await chat("Hello", "minimax/MiniMax-M2.1")
        assert result["success"] is True
        assert "Hello!" in result["response"]
        mock_llm_service.chat.assert_called_once_with(prompt="Hello", model="minimax/MiniMax-M2.1")

    @pytest.mark.asyncio
    async def test_chat_failure(self, mock_llm_service):
        """测试对话失败"""
        mock_llm_service.chat.side_effect = Exception("Chat service error")
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await chat("Hello")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_chat_with_default_model(self, mock_llm_service):
        """测试使用默认模型对话"""
        mock_llm_service.chat.return_value = "Response without model specified"
        
        result = await chat("What's up?")
        assert result["success"] is True
        mock_llm_service.chat.assert_called_once_with(prompt="What's up?", model=None)