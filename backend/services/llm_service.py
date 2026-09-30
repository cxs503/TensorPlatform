"""
LLM服务模块 - 集成OpenRouter + 智谱AI
支持Claude、GPT、GLM等模型，自动检测可用Provider
"""
import json
import os
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from services.llm_trace_context import record_llm_trace

logger = logging.getLogger(__name__)

# Provider配置
PROVIDERS = {
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-3-haiku",
        "headers_extra": {
            "HTTP-Referer": "https://open-digital-foundation.com",
            "X-Title": "Open Digital Foundation",
        },
    },
    "zhipu": {
        "env_key": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "headers_extra": {},
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "headers_extra": {},
    },
}


class LLMService:
    """LLM服务类 — 自动检测可用Provider(OpenRouter优先，智谱兜底)"""

    def __init__(self, api_key: Optional[str] = None, provider: Optional[str] = None):
        self._detect_provider(api_key, provider)

    def _detect_provider(self, api_key: Optional[str], provider: Optional[str]):
        """自动检测可用Provider"""
        # 指定提供方时绝不悄然改用其他密钥，避免跨功能误用模型。
        if provider:
            cfg = PROVIDERS.get(provider)
            if not cfg:
                self.api_key = ""; self.base_url = ""; self.default_model = ""; self._extra_headers = {}; self.provider = None
                logger.warning("LLM: 未知Provider: %s", provider)
                return
            self.api_key = api_key or os.getenv(cfg["env_key"], "")
            self.base_url = cfg["base_url"]
            self.default_model = cfg["default_model"]
            self._extra_headers = cfg["headers_extra"]
            self.provider = provider if self.api_key else None
            if self.api_key:
                logger.info(f"LLM使用指定Provider: {provider}")
            return

        # 自动检测: OpenRouter优先
        for name, cfg in PROVIDERS.items():
            key = api_key or os.getenv(cfg["env_key"], "")
            if key:
                self.api_key = key
                self.base_url = cfg["base_url"]
                self.default_model = cfg["default_model"]
                self._extra_headers = cfg["headers_extra"]
                self.provider = name
                logger.info(f"LLM自动选择Provider: {name} model={self.default_model}")
                return

        # 无可用Provider
        self.api_key = ""
        self.base_url = ""
        self.default_model = ""
        self._extra_headers = {}
        self.provider = None
        logger.warning("LLM: 未配置任何API Key (OPENROUTER_API_KEY / ZHIPU_API_KEY / DEEPSEEK_API_KEY)")

    def _check_available(self):
        """检查是否有可用Provider，无则抛清晰错误"""
        if not self.api_key:
            raise RuntimeError(
                "LLM服务不可用: 未配置API密钥。请设置环境变量 OPENROUTER_API_KEY、ZHIPU_API_KEY 或 DEEPSEEK_API_KEY"
            )

    def _call_api(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        """调用LLM API(自动适配Provider)"""
        self._check_available()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }

        trace_operation = str(kwargs.pop("trace_operation", "chat"))
        if "max_tokens" not in kwargs:
            kwargs["max_tokens"] = 8000

        data = {"model": model, "messages": messages, **kwargs}

        # 智谱API需要特殊处理stream
        if self.provider == "zhipu":
            data["stream"] = False

        started_at = time.perf_counter()
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=120,
            )

            if response.status_code == 401:
                raise RuntimeError(
                    f"LLM认证失败({self.provider}): API密钥无效或已过期"
                )
            if response.status_code == 429:
                raise RuntimeError(
                    f"LLM调用频率超限({self.provider}): 请稍后重试或切换模型"
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"LLM API调用失败({self.provider}): HTTP {response.status_code} — {response.text[:300]}"
                )

            result = response.json()
            record_llm_trace(
                provider=self.provider or "unknown", model=model,
                operation=trace_operation, status="success",
                latency_ms=(time.perf_counter() - started_at) * 1000,
                usage=result.get("usage") if isinstance(result, dict) else None,
            )
            return result

        except Exception as exc:
            if isinstance(exc, requests.exceptions.Timeout):
                error = RuntimeError(f"LLM调用超时({self.provider}): 请求超过120秒")
            elif isinstance(exc, requests.exceptions.ConnectionError):
                error = RuntimeError(
                    f"LLM连接失败({self.provider}): 无法连接到 {self.base_url}，请检查网络"
                )
            else:
                error = exc
            record_llm_trace(
                provider=self.provider or "unknown", model=model,
                operation=trace_operation, status="error",
                latency_ms=(time.perf_counter() - started_at) * 1000,
                error_message=str(error),
            )
            raise error

    def chat(self, prompt: str, model: Optional[str] = None, **kwargs) -> str:
        """简单的对话生成"""
        model = model or self.default_model
        messages = [{"role": "user", "content": prompt}]
        result = self._call_api(model, messages, **kwargs)
        return result["choices"][0]["message"]["content"]

    def generate_code(
        self, requirement: str, language: str = "python", **kwargs
    ) -> str:
        """根据需求生成代码"""
        prompt = f"""你是一个专业的程序员。请根据以下需求生成{language}代码：

需求描述：
{requirement}

请生成完整、可运行的代码。代码要求：
1. 遵循最佳实践
2. 包含必要的错误处理
3. 添加适当的注释
4. 代码结构清晰

请直接输出代码，不要包含其他说明。"""
        return self.chat(prompt, kwargs.get("model", self.default_model), trace_operation="generate_code")

    def optimize_code(
        self, code: str, language: str = "python", **kwargs
    ) -> Dict[str, str]:
        """优化代码"""
        prompt = f"""你是一个代码优化专家。请优化以下{language}代码：

原始代码：
```{language}
{code}
```

请提供：
1. 优化后的代码
2. 优化说明（简要）

请用以下JSON格式返回：
{{
  "optimized_code": "优化后的代码",
  "explanation": "优化说明"
}}

只返回JSON，不要包含其他内容。"""
        result = self.chat(prompt, kwargs.get("model", self.default_model), trace_operation="optimize_code")
        try:
            return json.loads(result)
        except Exception:
            return {"optimized_code": code, "explanation": "解析失败，原代码保持不变"}

    def generate_tests(self, code: str, language: str = "python", **kwargs) -> str:
        """生成测试代码"""
        prompt = f"""你是一个测试工程师。请为以下{language}代码生成单元测试：

原始代码：
```{language}
{code}
```

请生成完整的测试代码，要求：
1. 使用pytest框架
2. 覆盖主要功能
3. 包含边界情况测试
4. 测试代码要可运行

请直接输出测试代码，不要包含其他说明。"""
        return self.chat(prompt, kwargs.get("model", self.default_model), trace_operation="generate_tests")

    def explain_code(self, code: str, **kwargs) -> str:
        """解释代码"""
        prompt = f"""请解释以下代码的功能和逻辑：

```
{code}
```

请用清晰易懂的语言解释，包括：
1. 代码的主要功能
2. 关键逻辑说明
3. 使用的技术和模式"""
        return self.chat(prompt, kwargs.get("model", self.default_model), trace_operation="explain_code")


# 按提供方缓存实例，确保特定功能使用明确的模型凭证。
_llm_services: Dict[str, LLMService] = {}


def get_llm_service(api_key: Optional[str] = None, provider: Optional[str] = None) -> LLMService:
    """获取LLM服务实例；指定 provider 时不会回退到其他提供方。"""
    if api_key:
        return LLMService(api_key, provider)
    cache_key = provider or "auto"
    if cache_key not in _llm_services:
        _llm_services[cache_key] = LLMService(provider=provider)
    return _llm_services[cache_key]
