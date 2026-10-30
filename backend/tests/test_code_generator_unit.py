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
from api.code_generator import (
    CodeGenerateRequest, CodeOptimizeRequest,
    TestGenerateRequest as GenerateTestsRequest, CodeExplainRequest,
    _generation_requirement,
)


class TestCodeGeneratorUnit:
    """代码生成单元测试"""

    @pytest.fixture
    def mock_llm_service(self):
        """模拟LLM服务"""
        with patch('api.code_generator.get_llm_service') as mock_get_service:
            mock_service = Mock()
            mock_service.provider = "test-provider"
            mock_service.default_model = "test-model"
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
            requirement=_generation_requirement(req), language="python", model=None
        )

    @pytest.mark.asyncio
    async def test_generate_code_failure(self, mock_llm_service):
        """LLM 失败时必须生成可编译的本地代码并明确报告降级。"""
        mock_llm_service.generate_code.side_effect = Exception("LLM service unavailable")
        
        req = CodeGenerateRequest(requirement="Create function", language="python")
        result = await generate_code(req)
        assert result["success"] is True
        assert result["mode"] == "local_template"
        assert result["provider"] == "local"
        assert "LLM service unavailable" in result["warning"]
        compile(result["code"], "generated.py", "exec")

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
        """LLM 优化失败时保留本地语法优化能力。"""
        mock_llm_service.optimize_code.side_effect = Exception("Optimization failed")
        
        req = CodeOptimizeRequest(code="def test(): pass", language="python")
        result = await optimize_code(req)
        assert result["success"] is True
        assert result["mode"] == "local_template"
        assert result["original_code"] == req.code
        assert result["explanation"]
        compile(result["optimized_code"], "optimized.py", "exec")

    @pytest.mark.asyncio
    async def test_generate_tests_success(self, mock_llm_service):
        """测试成功生成测试代码"""
        mock_llm_service.generate_tests.return_value = "def test_hello():\n    assert hello() == 'Hello'"
        
        req = GenerateTestsRequest(code="def hello():\n    return 'Hello'", language="python")
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
        req = GenerateTestsRequest(code="def func(): pass", language="python")
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
        """LLM 解释失败时返回本地结构说明。"""
        mock_llm_service.explain_code.side_effect = Exception("Explanation failed")
        
        req = CodeExplainRequest(code="def hello():\n    return 1")
        result = await explain_code(req)
        assert result["success"] is True
        assert result["mode"] == "local_template"
        assert "hello" in result["explanation"]

    @pytest.mark.asyncio
    async def test_list_models(self, mock_llm_service):
        """测试列出模型"""
        result = await list_models()
        assert result["models"] is not None
        assert len(result["models"]) > 0
        model_ids = [model["id"] for model in result["models"]]
        assert model_ids == ["test-model"]
        assert result["provider"] == "test-provider"

    @pytest.mark.asyncio
    async def test_unconfigured_provider_does_not_invent_models_or_tests(self, mock_llm_service):
        mock_llm_service.provider = None
        assert (await list_models())["models"] == []
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await generate_tests(GenerateTestsRequest(code="def f(): pass"))
        assert exc_info.value.status_code == 503
        mock_llm_service.generate_tests.assert_not_called()
        with pytest.raises(HTTPException) as exc_info:
            await chat("Hello")
        assert exc_info.value.status_code == 503
        mock_llm_service.chat.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("output", ["", "def broken("])
    async def test_generate_tests_rejects_empty_or_invalid_python(self, mock_llm_service, output):
        mock_llm_service.generate_tests.return_value = output
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await generate_tests(GenerateTestsRequest(code="def f(): pass"))
        assert exc_info.value.status_code == 500

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