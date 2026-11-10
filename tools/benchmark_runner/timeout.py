"""墙钟超时护栏（wall-clock guard）——无人值守执行**不允许被挂死的 SUT 拖住**。

背景（本模块存在的理由）
------------------------
``runner.run_case`` 把 SUT 抛出的任何异常都转成 ``status="error"``（fail-closed）。
但"挂住"（死循环、等锁、等 IO、被 C 扩展卡住）**不是异常**：进程会一直等下去。
对一个无人值守的 CI/夜间 benchmark 引擎，"永远不返回"比"失败"更糟——失败有裁决，
挂死只会让整条门链悄悄停在半路（既无 pass 也无 fail，无法机器裁）。

本模块给出两条护栏，都是 **fail-closed**：超时 → 抛 :class:`CaseTimeout` →
runner 记为 ``status="error"`` / ``passed=False``，绝不当"通过"。

1. 逐算例 ``timeout_s``：单次 SUT/参考解调用超时；
2. suite ``budget_s``（在 :mod:`runner` 内实施）：整批墙钟预算用尽 → 剩余算例一律
   记 error，不假装它们"跑过了"。

实现取向
--------
* 主线程 + POSIX 有 ``setitimer`` → 用 ``SIGALRM``（能真正打断纯 Python 死循环，
  且不产生线程泄漏）；调用结束后立即撤销定时器并还原旧 handler（不污染宿主进程）；
* 非主线程 / 无 ``setitimer`` → 守护线程兜底：``join(timeout)`` 仍存活即判超时。
  线程无法强杀，故设为 daemon，保证进程可以退出（"不挂死"优先于"无泄漏"）。

已知局限（诚实声明，不粉饰）：若 SUT 卡在**不可中断**的 C 扩展内部，``SIGALRM``
的异常要等其返回才生效；此时守护线程路径同样只能放弃等待。所以本护栏保证的是
"引擎不挂死、裁决按时给出"，不是"任意 SUT 都能被强行终止"。
"""
from __future__ import annotations

import signal
import threading
from typing import Any, Callable, Mapping, Optional


class CaseTimeout(RuntimeError):
    """一次调用超过预声明墙钟预算（fail-closed 信号，不是"通过"）。"""

    def __init__(self, seconds: float, label: str = "") -> None:
        self.seconds = float(seconds)
        self.label = label or "call"
        super().__init__(f"{self.label} exceeded wall-clock timeout of {self.seconds:g}s "
                         f"(unattended run must return a verdict, not hang)")


def _signal_path_available() -> bool:
    return (hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread())


def call_with_timeout(fn: Callable[..., Any],
                      kwargs: Optional[Mapping[str, Any]] = None,
                      timeout_s: Optional[float] = None,
                      label: str = "") -> Any:
    """调用 ``fn(**kwargs)``；超过 ``timeout_s`` 秒抛 :class:`CaseTimeout`。

    ``timeout_s`` 为 ``None``/``<=0`` 时不设限（配置层默认总是给一个有限值，
    这里保留逃逸阀只用于单元测试与交互式调试）。
    """
    kwargs = dict(kwargs or {})
    if not timeout_s or float(timeout_s) <= 0:
        return fn(**kwargs)
    timeout_s = float(timeout_s)

    if _signal_path_available():
        prev_handler = signal.getsignal(signal.SIGALRM)
        prev_timer = signal.setitimer(signal.ITIMER_REAL, 0.0)

        def _on_alarm(signum, frame):          # noqa: ANN001 - signal handler 签名固定
            raise CaseTimeout(timeout_s, label)

        try:
            signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, timeout_s)
            return fn(**kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, prev_handler)
            if prev_timer and prev_timer[0] > 0:   # 还原宿主原有的定时器（不吞别人的闹钟）
                signal.setitimer(signal.ITIMER_REAL, *prev_timer)
    # 兜底：守护线程（不能强杀，但保证"引擎按时裁决并且能退出"）
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["result"] = fn(**kwargs)
        except BaseException as exc:            # noqa: BLE001 - 原样回抛给调用方
            box["error"] = exc

    thread = threading.Thread(target=_run, name=f"benchmark-case-{label or 'call'}", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise CaseTimeout(timeout_s, label)
    if "error" in box:
        raise box["error"]
    return box.get("result")