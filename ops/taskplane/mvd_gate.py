#!/usr/bin/env python3
"""Harness-side 70% budget MVD (minimum viable delivery) checkpoint gate.

Why this module exists
----------------------
The platform already evaluates the MVD checkpoint server-side and attaches a
``closure_instruction`` to the heartbeat response, but the worker never read it:
the instruction was generated and dropped, so a mission could burn its whole
budget exploring and only hear "you must close out" once, at zero budget left
(the forced-finish round). This gate makes the checkpoint *arrive on time*
(70% of budget) with a templated, domain-free instruction.

Design rules
------------
* **Pure function.** ``evaluate_heartbeat`` takes the heartbeat response and the
  reported progress, returns a decision. No I/O, no clock, no globals.
* **Mode gated.** ``enforce`` / ``shadow`` inject the instruction; ``off`` is a
  strict no-op (byte-identical worker behaviour, verified by a golden test).
* **Injection is advisory, once.** The instruction is a user message; it never
  blocks a tool and it is never repeated for the same mission.
* **Zero domain semantics.** The template only speaks about files, checks and
  claims — never about a subject-matter discipline.
"""
from __future__ import annotations

import os

#: Fraction of the budget at which the checkpoint fires.
BUDGET_THRESHOLD = 0.70

#: Modes. ``off`` => no-op; ``shadow``/``enforce`` => inject once + record.
MODES = ("off", "shadow", "enforce")
DEFAULT_MODE = "shadow"
MODE_ENV = "TP_MVD_CHECKPOINT"

#: Templated instruction (domain-free, no subject-matter vocabulary).
CLOSURE_TEMPLATE = (
    "【预算收口检查点 · 已用预算 {pct:.0f}%】剩余预算不足 {rest:.0f}%，之后不再有新预算。\n"
    "现在起按最小可交付（MVD）收口，停止一切探索性读取：\n"
    "1) 先把已完成的改动落盘，确保交付物清单（changed_files）至少有一个文件；\n"
    "2) 至少跑一次可机检的验证，把命令与结果（退出码 / 通过数 / 明确写\"未运行\"）写进 checks；\n"
    "3) 在 finish 里逐项写清：已完成项、未完成项、产物路径（docs/ artifacts/ runs/）；\n"
    "4) 做不到的部分必须显式声明为部分交付，不得声称完成。\n"
    "当前缺失项：{missing}"
)


def mode_from_env(environ: dict[str, str] | None = None) -> str:
    """Read the gate mode from the environment (default ``shadow``)."""
    env = os.environ if environ is None else environ
    mode = str(env.get(MODE_ENV, DEFAULT_MODE)).strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def budget_fraction(
    tool_calls: int, max_tool_calls: int, wall_seconds: float, max_wall_seconds: float
) -> float:
    """Return the binding budget utilisation fraction in ``[0, 1]``.

    The binding resource is whichever is closer to its own ceiling, so a mission
    that is slow but cheap still gets the checkpoint.

    Examples
    --------
    >>> budget_fraction(70, 100, 10, 3600)
    0.7
    >>> budget_fraction(0, 100, 1800, 3600)
    0.5
    """
    tool = (tool_calls / max_tool_calls) if max_tool_calls > 0 else 0.0
    wall = (wall_seconds / max_wall_seconds) if max_wall_seconds > 0 else 0.0
    return max(0.0, min(1.0, max(tool, wall)))


def missing_mvd_items(
    changed_files: list[str] | None,
    checks: str | None,
    headline: str | None = None,
    headline_is_claim: bool = True,
) -> list[str]:
    """Local (harness-side) MVD fact check — mirrors the server-side names.

    Returns the missing item names in a stable order.

    Examples
    --------
    >>> missing_mvd_items([], "", None)
    ['implementation', 'checks', 'claim']
    >>> missing_mvd_items(["a.py"], "12 passed", "done")
    []
    """
    missing: list[str] = []
    if not [f for f in (changed_files or []) if str(f).strip()]:
        missing.append("implementation")
    if not str(checks or "").strip():
        missing.append("checks")
    if headline_is_claim and not str(headline or "").strip():
        missing.append("claim")
    return missing


def evaluate_heartbeat(
    heartbeat_response,
    *,
    mode: str = DEFAULT_MODE,
    already_injected: bool = False,
    tool_calls: int = 0,
    max_tool_calls: int = 0,
    wall_seconds: float = 0.0,
    max_wall_seconds: float = 0.0,
    changed_files: list[str] | None = None,
    checks: str | None = None,
    headline: str | None = None,
) -> dict:
    """Decide whether to inject the closure instruction after a heartbeat.

    Parameters
    ----------
    heartbeat_response : tuple | dict | None
        ``(status, payload)`` as returned by the platform ``api()`` helper, or the
        payload dict itself, or ``None`` when the heartbeat failed.
    mode : str
        One of ``off`` / ``shadow`` / ``enforce``.
    already_injected : bool
        True if the instruction was already handed to the model this mission.
    tool_calls, max_tool_calls, wall_seconds, max_wall_seconds : numbers
        Budget facts reported by the harness.
    changed_files, checks, headline : artifact facts known so far.
        Used only when the server did not send its own checkpoint verdict.

    Returns
    -------
    dict
        ``{"mode", "triggered", "budget_pct", "inject", "instruction",
        "event", "reason"}``; ``event`` is ``None`` unless the checkpoint fired.

    Examples
    --------
    >>> evaluate_heartbeat(None, mode="off")["inject"]
    False
    >>> r = evaluate_heartbeat(None, mode="shadow", tool_calls=71,
    ...                        max_tool_calls=100, changed_files=["a.py"],
    ...                        checks="3 passed", headline="done")
    >>> r["triggered"], r["inject"]
    (True, True)
    >>> evaluate_heartbeat(None, mode="shadow", tool_calls=10, max_tool_calls=100)["inject"]
    False
    """
    from_out = {"mode": mode, "triggered": False, "budget_pct": 0.0,
                "inject": False, "instruction": None, "event": None,
                "reason": "off"}
    if mode == "off":
        return from_out

    payload: dict = {}
    if isinstance(heartbeat_response, tuple) and len(heartbeat_response) == 2:
        status, body = heartbeat_response
        payload = body if isinstance(body, dict) else {}
        if status and int(status) >= 400:
            payload = {}
    elif isinstance(heartbeat_response, dict):
        payload = heartbeat_response

    pct = budget_fraction(tool_calls, max_tool_calls, wall_seconds, max_wall_seconds)
    server_cp = payload.get("mvd_checkpoint") if isinstance(payload, dict) else None
    server_cp = server_cp if isinstance(server_cp, dict) else {}
    if server_cp.get("budget_pct") is not None:
        # Trust the server verdict when present (it knows the authoritative budget).
        try:
            pct = max(pct, float(server_cp["budget_pct"]) / 100.0)
        except (TypeError, ValueError):
            pass

    triggered = bool(server_cp.get("triggered")) or pct >= BUDGET_THRESHOLD
    from_out["budget_pct"] = round(pct * 100, 2)
    if not triggered:
        from_out["reason"] = "below_threshold"
        return from_out
    from_out["triggered"] = True

    server_missing = [m for m in (server_cp.get("missing") or []) if isinstance(m, str)]
    missing = server_missing or missing_mvd_items(changed_files, checks, headline)
    passed = bool(server_cp.get("passed")) if "passed" in server_cp else (not missing)

    event = {
        "budget_pct": round(pct * 100, 2),
        "missing": missing,
        "passed": passed,
        "mode": mode,
        "source": "server" if server_cp else "harness",
        "injected": False,
    }

    if passed:
        from_out["event"] = event
        from_out["reason"] = "mvd_satisfied"
        return from_out

    if already_injected:
        event["injected"] = "already"
        from_out["event"] = event
        from_out["reason"] = "already_injected"
        return from_out

    instruction = server_cp.get("closure_instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        instruction = CLOSURE_TEMPLATE.format(
            pct=pct * 100,
            rest=(1.0 - pct) * 100,
            missing=", ".join(missing) if missing else "(none)",
        )
    event["injected"] = True
    from_out.update({"inject": True, "instruction": instruction,
                     "event": event, "reason": "mvd_unmet"})
    return from_out