#!/usr/bin/env python3
"""平台派单器 ↔ 调度器标准门的接线层（adapter/hook）。

平台侧只需要两行：派单前 :func:`pre_dispatch`（路径预留），收单/推送前
:func:`post_finish`（交集 + 合并预演）。本模块不含业务判定，全部委托
:class:`tools.tenancy_gate.scheduler_gate.SchedulerGate`。

三种运行模式（环境变量 ``TENANCY_GATE_MODE``）
----------------------------------------------
* ``off``     ：完全跳过（应急开关，不读不写状态）。
* ``report``  ：**默认**。判定并写状态/台账，但从不拦单 —— 先量测真实并发写面的
  冲突率，再决定是否升级为强制（避免机制上线当天把整个派单面卡死）。
* ``enforce`` ：拦截。``exit_code != 0`` 时调用方必须放弃本次派单/推送。

降级纪律：``enforce`` 模式下任何内部异常都按 ``rollback``（exit 12）处理，绝不
"出错了就放行"；``report`` 模式下异常只记录，不改变调用方行为。
"""
from __future__ import annotations

import os
from pathlib import Path

from .model import digest
from .scheduler_gate import EXIT_ROLLBACK, SchedulerGate, exit_code_of

__all__ = ["MODE_ENV", "gate_mode", "build_gate", "pre_dispatch", "post_finish",
           "adopt_mission", "release_mission"]

MODE_ENV = "TENANCY_GATE_MODE"
_VALID_MODES = ("off", "report", "enforce")


def gate_mode(env: dict | None = None) -> str:
    """读运行模式；非法值按 fail-closed 记 ``enforce`` 会误伤，故按 ``report`` 并留痕。"""
    env = os.environ if env is None else env
    raw = str(env.get(MODE_ENV, "report")).strip().lower() or "report"
    return raw if raw in _VALID_MODES else "report"


def build_gate(env: dict | None = None) -> SchedulerGate:
    """按环境变量装配门：``TENANCY_GATE_STATE`` / ``TENANCY_GATE_LEDGER``。"""
    env = os.environ if env is None else env
    return SchedulerGate(
        state_path=env.get("TENANCY_GATE_STATE") or None,
        ledger_path=env.get("TENANCY_GATE_LEDGER") or None,
    )


def pre_dispatch(repo: str, write_paths, mission_key: str,
                 env: dict | None = None, ts: str | None = None) -> dict:
    """派单前路径预留。返回 ``{mode, enforced, blocked, exit_code, gate}``。"""
    mode = gate_mode(env)
    paths = [str(p).strip() for p in (write_paths or []) if str(p).strip()]
    if mode == "off" or not paths:
        return {"schema": "tenancy_gate/dispatch_hook/v1", "mode": mode,
                "enforced": False, "blocked": False, "exit_code": 0,
                "skipped": "mode_off" if mode == "off" else "no_write_paths",
                "gate": None}
    try:
        gate = build_gate(env)
        decision = gate.reserve(mission_key, repo, paths, ts=ts)
    except Exception as exc:  # noqa: BLE001 —— 降级策略见模块 docstring
        degraded = {"schema": "tenancy_gate/dispatch_hook/v1", "mode": mode,
                    "enforced": mode == "enforce", "exit_code": EXIT_ROLLBACK,
                    "blocked": mode == "enforce", "error": f"{type(exc).__name__}:{exc}"[:300],
                    "gate": None}
        return degraded
    enforced = mode == "enforce" and decision["exit_code"] != 0
    return {
        "schema": "tenancy_gate/dispatch_hook/v1",
        "mode": mode,
        "enforced": enforced,
        "blocked": enforced,
        "exit_code": decision["exit_code"],
        "resolution": decision["resolution"],
        "decision_digest": decision["digest"],
        "gate": decision,
    }


def adopt_mission(pending_key: str, mission_id, repo: str, env: dict | None = None) -> dict:
    """派单成功后把占位 id 的预留改挂到真实 mission id。"""
    try:
        return build_gate(env).adopt(str(pending_key), str(mission_id), repo=repo)
    except Exception as exc:  # noqa: BLE001
        return {"schema": "tenancy_gate/dispatch_hook/v1", "moved": 0,
                "error": f"{type(exc).__name__}:{exc}"[:300]}


def release_mission(mission_id, repo: str | None = None, env: dict | None = None) -> dict:
    """任务终态释放预留（幂等）。"""
    try:
        return build_gate(env).release(str(mission_id), repo=repo)
    except Exception as exc:  # noqa: BLE001
        return {"schema": "tenancy_gate/dispatch_hook/v1", "dropped": 0,
                "error": f"{type(exc).__name__}:{exc}"[:300]}


def post_finish(repo: str, mission_id, changed_files, repo_path: str | Path | None = None,
                ours: str | None = None, theirs: str | None = None, base: str | None = None,
                env: dict | None = None, ts: str | None = None) -> dict:
    """收单/推送前：changed_files 交集 + 三方合并 dry-run。"""
    mode = gate_mode(env)
    if mode == "off":
        return {"schema": "tenancy_gate/dispatch_hook/v1", "mode": mode,
                "enforced": False, "blocked": False, "exit_code": 0, "skipped": "mode_off",
                "gate": None}
    try:
        gate = build_gate(env)
        decision = gate.decide(mission_id, repo, changed_files=list(changed_files or []),
                               repo_path=repo_path, ours=ours, theirs=theirs, base=base, ts=ts)
    except Exception as exc:  # noqa: BLE001
        return {"schema": "tenancy_gate/dispatch_hook/v1", "mode": mode,
                "enforced": mode == "enforce", "exit_code": EXIT_ROLLBACK,
                "blocked": mode == "enforce", "error": f"{type(exc).__name__}:{exc}"[:300],
                "gate": None}
    enforced = mode == "enforce" and decision["exit_code"] != 0
    return {
        "schema": "tenancy_gate/dispatch_hook/v1",
        "mode": mode,
        "enforced": enforced,
        "blocked": enforced,
        "exit_code": decision["exit_code"],
        "resolution": decision["resolution"],
        "decision_digest": decision["digest"],
        "gate": decision,
    }


def summarize_hook(hook: dict) -> str:
    """单行摘要（写进派单日志/报告，便于事后追问"当时为什么放行"）。"""
    if not hook:
        return "gate:none"
    return (f"gate mode={hook.get('mode')} exit={hook.get('exit_code')} "
            f"digest={(hook.get('decision_digest') or digest(hook))[:12]}")


def exit_code_name(code: int) -> str:
    """退出码可读名（报告/日志用）。"""
    names = {0: "pass", 10: "queued", 11: "manual_arbitration", 12: "rollback", 13: "refused"}
    return names.get(int(code), "unknown")


def _unused() -> None:  # pragma: no cover - 保留 exit_code_of 的可导入性演示
    assert exit_code_of("pass") == 0