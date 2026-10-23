"""Stateless inference backend; resident tool and memory loops live in the harness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import tomllib

from fastapi import HTTPException

_CALL_LOCK = threading.Lock()
PROBE_SCHEMA = {"type": "object", "properties": {"marker": {"type": "string"}},
                "required": ["marker"], "additionalProperties": False}
PLAN_SCHEMA = {"type": "object", "properties": {
    "goal": {"type": "string"},
    "steps": {"type": "array", "items": {"type": "string"}},
    "acceptance_criteria": {"type": "array", "items": {"type": "string"}}},
    "required": ["goal", "steps", "acceptance_criteria"], "additionalProperties": False}


def configuration() -> dict:
    enabled = os.getenv("FACTORY_MODEL_BACKEND") == "codex"
    home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        config = tomllib.loads((home / "config.toml").read_text())
    except (OSError, ValueError):
        config = {}
    provider_id = config.get("model_provider", "openai")
    provider = config.get("model_providers", {}).get(provider_id, {})
    model = os.getenv("FACTORY_CODEX_MODEL") or config.get("model", "")
    binary = shutil.which("codex")
    # Presence only: the CLI owns credential parsing, refresh and use.
    auth_present = (home / "auth.json").is_file() or bool(os.getenv("CODEX_API_KEY"))
    unsupported = set(provider) - {"name", "base_url", "wire_api", "env_key",
                                  "requires_openai_auth", "supports_websockets"}
    missing = []
    if not enabled:
        missing.append("FACTORY_MODEL_BACKEND=codex")
    if not binary:
        missing.append("codex CLI")
    if not model:
        missing.append("Codex model")
    if not auth_present and not os.getenv(provider.get("env_key", "")):
        missing.append("Codex login or provider credential")
    if unsupported:
        missing.append("provider settings require explicit adapter support")
    if provider.get("wire_api", "responses") != "responses":
        missing.append("Responses API provider")
    return {"backend": "codex", "configured": not missing, "model": model,
            "provider": provider.get("name", provider_id), "wire_api": "responses",
            "authentication": "service-user-codex", "missing": missing,
            "capabilities": ["connection-test", "task-draft"],
            "autonomous_worker_verified": False,
            "_binary": binary, "_provider_id": provider_id, "_provider": provider}


def status() -> dict:
    return {key: value for key, value in configuration().items() if not key.startswith("_")}


def generate(prompt: str, schema: dict, *, model: str | None = None, timeout_seconds: float = 85) -> dict:
    config = configuration()
    if model is not None:
        if model not in allowed_models():
            raise HTTPException(422, "模型未列入平台允许列表")
        config["model"] = model
    if not config["configured"]:
        raise HTTPException(503, "GPT 调用配置未就绪，请检查模型接入状态")
    if not _CALL_LOCK.acquire(blocking=False):
        raise HTTPException(429, "模型正在处理另一个请求，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="tensorplatform-model-") as directory:
            work = Path(directory)
            schema_file, output = work / "schema.json", work / "result.json"
            schema_file.write_text(json.dumps(schema))
            command = [config["_binary"], "exec", "--ignore-user-config", "--ignore-rules",
                       "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                       "--json", "--color", "never", "--model", config["model"],
                       "--output-schema", str(schema_file), "--output-last-message", str(output),
                       "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                       "-c", 'features.shell_tool=false', "-c", 'features.unified_exec=false',
                       "-c", 'features.multi_agent=false', "-c", 'features.enable_mcp_apps=false',
                       "-c", 'model_reasoning_effort='+json.dumps(os.getenv('FACTORY_REASONING_EFFORT','high')),
                       "-c", "model_provider=" + json.dumps(config["_provider_id"])]
            for key, value in config["_provider"].items():
                command += ["-c", f'model_providers.{config["_provider_id"]}.{key}=' + json.dumps(value)]
            command.append("-")
            with (work / "events.jsonl").open("w+") as events:
                # Never inherit the current interactive turn's prompts or task context.
                process = subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE,
                                           stdout=events, stderr=subprocess.DEVNULL,
                                           text=True, start_new_session=True)
                try:
                    process.communicate(input=prompt, timeout=max(1, min(600, timeout_seconds)))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise HTTPException(504, "模型调用超时，请检查 Codex 登录和网关状态") from None
                if process.returncode or not output.is_file():
                    raise HTTPException(502, "Codex 调用失败，请在服务器检查登录、网关与模型权限")
                if output.stat().st_size > 64 * 1024 or events.tell() > 2 * 1024 * 1024:
                    raise HTTPException(502, "模型返回内容超过允许大小")
                events.seek(0)
                usage = {}
                completed = False
                for line in events:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    item_type = (event.get("item") or {}).get("type")
                    if item_type and item_type not in {"agent_message", "reasoning"}:
                        raise HTTPException(502, "文本模型调用出现工具活动，结果已拒绝")
                    if event.get("type") in {"error", "turn.failed"}:
                        raise HTTPException(502, "Codex 模型推理未正常完成")
                    if event.get("type") == "turn.completed":
                        completed = True
                        usage = event.get("usage") or {}
                if not completed:
                    raise HTTPException(502, "缺少模型调用完成记录")
                try:
                    result = json.loads(output.read_text())
                except (OSError, ValueError):
                    raise HTTPException(502, "模型未返回有效的结构化结果") from None
                return {"model": config["model"], "reasoning_effort":os.getenv("FACTORY_REASONING_EFFORT","high"), "data": result,
                        "usage": {k: v for k, v in usage.items()
                                  if k in {"input_tokens", "cached_input_tokens", "output_tokens"}
                                  and isinstance(v, int)}}
    except OSError:
        raise HTTPException(502, "无法启动 Codex，请检查服务账号的执行环境") from None
    finally:
        _CALL_LOCK.release()


def allowed_models() -> list[str]:
    """Explicit operator allowlist; availability is established by real calls."""
    return list(dict.fromkeys(filter(None, os.getenv("FACTORY_ALLOWED_MODELS", "gpt-6.1-sol,gpt-6-sol").split(","))))
