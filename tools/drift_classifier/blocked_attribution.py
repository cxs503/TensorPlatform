"""Blocked Attribution — distinguish environment-missing vs resource-timeout.

Sub-classes of blocked:
  * ``env_missing_dep``   — 环境缺件（ModuleNotFoundError / 依赖不可得）
  * ``env_collect_error`` — 收集阶段错误（conftest 加载失败等）
  * ``resource_timeout``  — 资源超时（允许降规模重跑）
  * ``resource_oom``      — 内存不足（允许降规模重跑）
  * ``infra_failure``     — 基础设施故障（节点宕机等，不可自动重跑）

Key functions:
  * ``attribute_blocked(run_result)`` → {blocked_class, evidence, can_rerun_scaled}
  * ``is_blocked(run_result)`` → bool
  * ``can_rerun_scaled(run_result)`` → bool (resource timeout/OOM → True)
"""
from __future__ import annotations

import re
from typing import Any

# ── Blocked sub-class constants ────────────────────────────────────────

BLOCKED_ENV_MISSING_DEP = "env_missing_dep"
BLOCKED_ENV_COLLECT_ERROR = "env_collect_error"
BLOCKED_RESOURCE_TIMEOUT = "resource_timeout"
BLOCKED_RESOURCE_OOM = "resource_oom"
BLOCKED_INFRA_FAILURE = "infra_failure"

BLOCKED_SUBCLASSES = (
    BLOCKED_ENV_MISSING_DEP,
    BLOCKED_ENV_COLLECT_ERROR,
    BLOCKED_RESOURCE_TIMEOUT,
    BLOCKED_RESOURCE_OOM,
    BLOCKED_INFRA_FAILURE,
)

# ── Regex patterns ─────────────────────────────────────────────────────

_MISSING_DEP_RE = re.compile(
    r"ModuleNotFoundError:\s*No\s+module\s+named\s+'([^']+)'"
    r"|ImportError:\s*cannot\s+import\s+name\s+'([^']+)'"
    r"|PackageNotFoundError:\s*No\s+package\s+named\s+'([^']+)'"
)

_COLLECT_ERR_RE = re.compile(
    r"error[s]?\s+during\s+collection"
    r"|ERROR\s+collecting\s+"
    r"|ImportError\s+while\s+loading\s+conftest"
    r"|conftest\.py.*import"
)

_TIMEOUT_RE = re.compile(
    r"TimeoutExpired"
    r"|timed?\s+out"
    r"|execution\s+timed\s+out"
    r"|SIGKILL.*timeout"
    r"|Killed\s+by\s+signal\s+9"
)

_OOM_RE = re.compile(
    r"MemoryError"
    r"|OutOfMemory"
    r"|Killed\s*\(signal\s+9\)"
    r"|OOM"
    r"|Cannot\s+allocate\s+memory"
    r"|cuda\s+out\s+of\s+memory"
)

# ── Attribution ────────────────────────────────────────────────────────

def attribute_blocked(run_result: dict[str, Any]) -> dict[str, Any]:
    """Attribute a blocked run to a specific sub-class.

    Args:
        run_result: dict with keys like ``timed_out``, ``exit_code``,
                    ``stdout``, ``stderr``, ``signal``.

    Returns:
        {
            "blocked_class": str,
            "evidence": dict,
            "can_rerun_scaled": bool,
            "rerun_recommendation": str | None,
        }
    """
    timed_out = run_result.get("timed_out", False)
    exit_code = run_result.get("exit_code")
    stdout = run_result.get("stdout", "") or ""
    stderr = run_result.get("stderr", "") or ""
    signal = run_result.get("signal")
    text = f"{stdout}\n{stderr}"

    evidence: dict[str, Any] = {
        "timed_out": timed_out,
        "exit_code": exit_code,
        "signal": signal,
    }

    # 1. Check for missing dependencies
    dep_match = _MISSING_DEP_RE.search(text)
    if dep_match:
        missing = dep_match.group(1) or dep_match.group(2) or dep_match.group(3)
        evidence["missing_module"] = missing
        return {
            "blocked_class": BLOCKED_ENV_MISSING_DEP,
            "evidence": evidence,
            "can_rerun_scaled": False,
            "rerun_recommendation": f"Install missing dependency: {missing}",
        }

    # 2. Check for collection errors
    if _COLLECT_ERR_RE.search(text):
        return {
            "blocked_class": BLOCKED_ENV_COLLECT_ERROR,
            "evidence": evidence,
            "can_rerun_scaled": False,
            "rerun_recommendation": "Fix conftest.py or test collection setup",
        }

    # 3. Check for OOM
    if _OOM_RE.search(text) or signal == 9:
        evidence["oom_detected"] = True
        return {
            "blocked_class": BLOCKED_RESOURCE_OOM,
            "evidence": evidence,
            "can_rerun_scaled": True,
            "rerun_recommendation": "Reduce problem size or increase memory allocation",
        }

    # 4. Check for timeout
    if timed_out or _TIMEOUT_RE.search(text):
        evidence["timeout_detected"] = True
        duration = run_result.get("duration_sec")
        if duration:
            evidence["duration_sec"] = duration
        return {
            "blocked_class": BLOCKED_RESOURCE_TIMEOUT,
            "evidence": evidence,
            "can_rerun_scaled": True,
            "rerun_recommendation": "Reduce problem size or increase timeout",
        }

    # 5. Default: infrastructure failure
    return {
        "blocked_class": BLOCKED_INFRA_FAILURE,
        "evidence": evidence,
        "can_rerun_scaled": False,
        "rerun_recommendation": None,
    }


def is_blocked(run_result: dict[str, Any]) -> bool:
    """Quick check: is this run result blocked?"""
    if run_result.get("timed_out"):
        return True
    exit_code = run_result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        stdout = run_result.get("stdout", "") or ""
        stderr = run_result.get("stderr", "") or ""
        text = f"{stdout}\n{stderr}"
        if _MISSING_DEP_RE.search(text) or _COLLECT_ERR_RE.search(text):
            return True
        if _OOM_RE.search(text):
            return True
    return False


def can_rerun_scaled(run_result: dict[str, Any]) -> bool:
    """Can this blocked run be retried with reduced scale?"""
    attr = attribute_blocked(run_result)
    return attr["can_rerun_scaled"]
