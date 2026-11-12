"""hook —— 与 tp_worker 注入面（PANORAMA / 开工三查 / 席位记忆 / 技能块）整合的挂钩。

**不另开通道**：干货包只是现有首条 user 消息里多出来的一段（与 ``_seat_memory()``、
``_skill_block()`` 同槽），沿用同一注入面；模式由 ``CONTEXT_PACKAGE_MODE`` 控制
（``off`` 不注入 / ``report`` 注入并落账 / ``enforce`` 注入+落账且必需槽缺失记红）。

集成点（tp_worker.run_mission 首条消息）::

    from tools.context_package.hook import inject as ctxpkg_inject
    _ctxp, _ctxp_info = ctxpkg_inject(mission.get("goal",""), mission.get("repo",""),
                                      mission_id=mission.get("id"), consumer=AGENT_ID)
    messages = [{"role": "system", ...},
                {"role": "user", "content": _seat_memory() + _skill_block(goal) +
                                           _ctxp + f"任务目标:\\n{goal}\\n..."}]

任何异常 → 返回空块 + ``degraded``，派单链路绝不因干货包失败而中断（fail-open）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from .assembler import build_package
from .ledger import record
from .loader import load_config
from .render import render_block

MODES = ("off", "report", "enforce")


def inject(goal: Any, repo: str = "", mission_id: Any = None, consumer: str = "",
           mode: str | None = None, cfg: Mapping[str, Any] | None = None,
           reader=None, extra_candidates: Mapping[str, list] | None = None,
           ledger_dir=None, manifest_text: str = "", db=None) -> tuple[str, dict]:
    """返回 ``(注入块文本, 审计摘要)``；块为空串表示本单不注入。永不抛异常。"""
    cfg = cfg or load_config()
    mode = (mode or os.environ.get((cfg.get("mode_env") or "CONTEXT_PACKAGE_MODE"), "report")).lower()
    info: dict[str, Any] = {"mode": mode, "injected": False}
    if mode not in MODES:
        info["degraded"] = f"UNKNOWN_MODE:{mode}"
        return "", info
    if mode == "off":
        info["degraded"] = "MODE_OFF"
        return "", info
    try:
        pkg = build_package(goal, repo=repo, mission_id=mission_id, cfg=cfg, reader=reader,
                            extra_candidates=extra_candidates)
        block = render_block(pkg, cfg.get("decisions") or {})
        if not block:
            info["degraded"] = "EMPTY_PACKAGE"
            return "", info
        missing = [s["slot"] for s in pkg.get("slots") or []
                   if s.get("required") and s.get("status") == "DEGRADED"]
        required_empty = [s["slot"] for s in pkg.get("slots") or []
                          if s.get("required") and s.get("status") in ("MISSING", "EMPTY")]
        audit = record(pkg, rendered=block, mode=mode, ledger_dir=ledger_dir,
                       consumer=consumer, manifest_text=manifest_text, db=db)
        info.update({
            "injected": True,
            "package_id": pkg.get("package_id"),
            "task_class": pkg.get("task_class"),
            "class_evidence": pkg.get("class_evidence"),
            "tokens": (pkg.get("budget") or {}).get("used_tokens"),
            "density_yield": (pkg.get("audit") or {}).get("density_yield"),
            "required_missing": missing,
            "required_empty": required_empty,
            "ledger": {k: audit.get(k) for k in ("package_sha256", "_ledger_path", "_ledger_error")},
            "enforce_violation": bool(missing) if mode == "enforce" else False,
        })
        if audit.get("_ledger_error"):
            info["degraded"] = "LEDGER_WRITE_FAILED"
        return block + "\n\n", info
    except Exception as exc:  # noqa: BLE001 —— fail-open
        info["degraded"] = f"HOOK_ERROR:{type(exc).__name__}"
        info["error"] = str(exc)[:200]
        return "", info


def inject_json(goal: Any, repo: str = "", mission_id: Any = None, **kw) -> str:
    """CLI/子进程友好的包装：输出 JSON（块 + 摘要）。"""
    block, info = inject(goal, repo, mission_id=mission_id, **kw)
    return json.dumps({"block": block, "info": info}, ensure_ascii=False)