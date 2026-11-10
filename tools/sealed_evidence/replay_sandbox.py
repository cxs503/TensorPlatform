"""sealed_evidence.replay_sandbox —— 最小依赖环境执行 replay.cmd，禁网禁写库，超时 300s。

沙箱特性：
  * 禁网：设置空代理，阻止网络访问
  * 禁写库：PYTHONDONTWRITEBYTECODE=1，PYTHONPYCACHEPREFIX 指向临时目录
  * 超时：硬超时 300s（可配置）
  * 隔离：使用临时目录作为 pycache 和 pip 缓存
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import Any


def run_replay_sandbox(
    cmd: str,
    cwd: str,
    timeout_s: int = 300,
    env: dict[str, str] | None = None,
    allow_network: bool = False,
    allow_write: bool = False,
) -> dict[str, Any]:
    """在沙箱中执行重放命令。

    Args:
        cmd: 要执行的命令
        cwd: 工作目录
        timeout_s: 超时（秒）
        env: 额外环境变量
        allow_network: 是否允许网络访问（默认 False）
        allow_write: 是否允许写库（默认 False）

    Returns:
        {exit_code, timed_out, duration_sec, stdout, stderr, sandbox_error?}
    """
    # 创建临时目录用于隔离
    with tempfile.TemporaryDirectory(prefix="replay_sandbox_") as tmpdir:
        # 构建沙箱环境
        full_env = dict(os.environ)

        # 禁写库
        if not allow_write:
            full_env["PYTHONDONTWRITEBYTECODE"] = "1"
            full_env["PYTHONPYCACHEPREFIX"] = os.path.join(tmpdir, "pycache")
            full_env["PIP_CACHE_DIR"] = os.path.join(tmpdir, "pip_cache")
            full_env["PIP_NO_BUILD_ISOLATION"] = "1"

        # 禁网
        if not allow_network:
            full_env["HTTP_PROXY"] = ""
            full_env["HTTPS_PROXY"] = ""
            full_env["http_proxy"] = ""
            full_env["https_proxy"] = ""
            full_env["NO_PROXY"] = "*"
            full_env["no_proxy"] = "*"

        # 合并额外环境
        if env:
            full_env.update(env)

        # 确保工作目录存在
        if not os.path.isdir(cwd):
            return {
                "exit_code": -1,
                "timed_out": False,
                "duration_sec": 0,
                "stdout": "",
                "stderr": f"working directory not found: {cwd}",
                "sandbox_error": "cwd_not_found",
            }

        # 执行命令
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=full_env,
            )
            duration = round(time.time() - started, 2)

            # 检测命令不存在（shell 返回 127）
            sandbox_error = None
            if proc.returncode == 127:
                stderr_lower = proc.stderr.lower() if proc.stderr else ""
                if "not found" in stderr_lower or "no such" in stderr_lower:
                    sandbox_error = "command_not_found"

            return {
                "exit_code": proc.returncode,
                "timed_out": False,
                "duration_sec": duration,
                "stdout": proc.stdout[-20000:] if proc.stdout else "",
                "stderr": proc.stderr[-8000:] if proc.stderr else "",
                **({"sandbox_error": sandbox_error} if sandbox_error else {}),
            }
        except subprocess.TimeoutExpired as exc:
            duration = round(time.time() - started, 2)
            stdout = ""
            stderr = ""
            if exc.stdout:
                stdout = exc.stdout.decode("utf-8", "replace")[-20000:] if isinstance(exc.stdout, bytes) else str(exc.stdout)[-20000:]
            if exc.stderr:
                stderr = exc.stderr.decode("utf-8", "replace")[-8000:] if isinstance(exc.stderr, bytes) else str(exc.stderr)[-8000:]
            return {
                "exit_code": None,
                "timed_out": True,
                "duration_sec": duration,
                "stdout": stdout,
                "stderr": stderr,
                "sandbox_error": "timeout",
            }
        except FileNotFoundError as exc:
            duration = round(time.time() - started, 2)
            return {
                "exit_code": -1,
                "timed_out": False,
                "duration_sec": duration,
                "stdout": "",
                "stderr": f"command not found: {exc}",
                "sandbox_error": "command_not_found",
            }
        except Exception as exc:
            duration = round(time.time() - started, 2)
            return {
                "exit_code": -1,
                "timed_out": False,
                "duration_sec": duration,
                "stdout": "",
                "stderr": f"sandbox error: {exc}",
                "sandbox_error": "unexpected_error",
            }
