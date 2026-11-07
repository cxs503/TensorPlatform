#!/usr/bin/env python3
"""coordinator_shadow v1 —— 常驻循环（IN27-H1）。

在岗纪律
--------
- **30 min 对齐**：下一拍落在 ``epoch % 1800 == 0`` 的槽上（而不是 ``sleep(1800)`` 之后
  的任意时刻），所以长期漂移不会累积；槽 = ``beat_id``，天然幂等。
- **单实例**：``flock`` 独占锁文件，第二个实例直接退出（避免一拍两次）。
- **优雅退出**：SIGTERM/SIGINT → 置停止标志，当前拍跑完再走，账本不留半条。
- **失败不自杀**：单拍异常由 ``run_beat_safe`` 收成 error 拍；循环继续。连续失败
  也只是继续（错拍本身就是证据），但会在心跳文件里写明 ``status``。

运行
----
    python3 -m tools.coordinator_shadow.daemon --once            # 跑一拍就退（巡检）
    python3 -m tools.coordinator_shadow.daemon --interval 1800   # 常驻
    python3 -m tools.coordinator_shadow.daemon --dry-run --once  # 只读跑一拍，不立单
"""
from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import diagnose as dg
from . import filing as fl
from . import judge as jd
from . import ledger as lg
from .pulse import PgRunner, ReadOnlyRunner, iso, utc_now
from .runtime import LivePulseSource, run_beat_safe

DEFAULT_STATE_DIR = "/var/lib/tp-coordinator-shadow"
DEFAULT_LOG = "/var/log/tp-fleet/coordinator_shadow.log"
DEFAULT_WATCH_LOGS = (
    "/var/log/tp-fleet/campaign_dispatcher.log",
    "/var/log/tp-fleet/coordinator_shadow.log",
)


@dataclass
class DaemonConfig:
    dsn: str | None = None
    interval_seconds: int = 1800
    state_dir: str = DEFAULT_STATE_DIR
    log_path: str = DEFAULT_LOG
    watch_logs: Sequence[str] = DEFAULT_WATCH_LOGS
    judge_mode: str = "auto"        # auto | llm | rule
    dry_run: bool = False
    max_beats: int = 0              # 0 = 无限
    once: bool = False
    policy: dg.Policy = field(default_factory=dg.Policy)


def log_line(path: str, message: str) -> None:
    line = f"{iso(utc_now())} {message}"
    print(line, flush=True)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def align_next(now: datetime | None = None, interval_seconds: int = 1800) -> float:
    """返回距下一个槽对齐时刻的秒数（严格 >0，避免同槽空转）。"""
    now = now or utc_now()
    epoch = now.timestamp()
    nxt = (int(epoch) // interval_seconds + 1) * interval_seconds
    return max(1.0, nxt - epoch)


class SingleInstance:
    """flock 独占；拿不到锁说明已有实例在岗。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fh = None

    def __enter__(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"影子协调官已在岗（锁 {self.path}）")
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()


def build_judge(mode: str) -> jd.Judge:
    if mode == "rule":
        return jd.RuleJudge()
    llm = jd.LLMJudge.from_env()
    if llm is not None:
        return llm
    if mode == "llm":
        raise RuntimeError("judge_mode=llm 但 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 不齐")
    return jd.RuleJudge()


def run_once(cfg: DaemonConfig, *, as_of: datetime | None = None) -> dict[str, Any]:
    """跑一拍（可被 CLI / 测试直接调用）。"""
    policy = cfg.policy
    policy.interval_seconds = cfg.interval_seconds
    runner = ReadOnlyRunner(PgRunner(cfg.dsn))
    source = LivePulseSource(runner, cfg.watch_logs, policy)
    ledger = lg.PgLedger(cfg.dsn)
    ledger.migrate()
    state_path = Path(cfg.state_dir) / "state.json"
    heartbeat = Path(cfg.state_dir) / "heartbeat.json"
    state = lg.RuntimeState.load(state_path)
    intake: fl.Intake = fl.DryRunIntake() if cfg.dry_run else fl.PgIntake(cfg.dsn)
    judge = build_judge(cfg.judge_mode)
    result = run_beat_safe(
        as_of=as_of or utc_now(), source=source, judge=judge, intake=intake, ledger=ledger,
        state=state, policy=policy, mode="dry_run" if cfg.dry_run else "live",
        allow_filing=not cfg.dry_run, fetchall=runner.fetchall,
        heartbeat_path=str(heartbeat), state_path=str(state_path),
    )
    log_line(cfg.log_path,
             f"拍 {result.beat.beat_id} mode={result.beat.mode} "
             f"judge={result.beat.provenance.get('judge')} "
             f"findings={len(result.beat.verdict.get('findings') or [])} "
             f"filings={len(result.filings)} suppressed={len(result.suppressed)} "
             f"error={'yes' if result.beat.error else 'no'} "
             f"ms={result.beat.duration_ms}")
    return result.to_dict()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="coordinator_shadow v1 常驻循环（只读+只立单）")
    ap.add_argument("--once", action="store_true", help="只跑一拍就退出")
    ap.add_argument("--dry-run", action="store_true", help="只读跑一拍，不立单、不写库")
    ap.add_argument("--interval", type=int, default=1800, help="拍间隔秒（默认 1800=30min）")
    ap.add_argument("--max-beats", type=int, default=0, help="最多跑多少拍（0=无限）")
    ap.add_argument("--judge", choices=("auto", "llm", "rule"), default="auto")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--watch-log", action="append", default=None,
                    help="要读尾部的 daemon 日志（可重复）")
    args = ap.parse_args(argv)

    cfg = DaemonConfig(
        dsn=args.dsn, interval_seconds=args.interval, state_dir=args.state_dir,
        log_path=args.log, watch_logs=tuple(args.watch_log) if args.watch_log else DEFAULT_WATCH_LOGS,
        judge_mode=args.judge, dry_run=args.dry_run, max_beats=args.max_beats, once=args.once,
        policy=dg.Policy(interval_seconds=args.interval),
    )
    if cfg.once or cfg.dry_run:
        run_once(cfg)
        return 0

    stop = {"flag": False}

    def _handle(signum, _frame):  # pragma: no cover - 信号
        stop["flag"] = True
        log_line(cfg.log_path, f"收到信号 {signum}：本拍结束后退出")

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    with SingleInstance(Path(cfg.state_dir) / "daemon.lock"):
        log_line(cfg.log_path, "=== coordinator_shadow v1 在岗（30min/拍；只读+只立单）===")
        beats = 0
        while not stop["flag"]:
            try:
                run_once(cfg)
            except Exception as exc:  # run_beat_safe 已兜单拍异常；这里兜「建连/迁移」级失败
                log_line(cfg.log_path, f"ERROR 拍外失败 {type(exc).__name__}: {str(exc)[:200]}")
            beats += 1
            if cfg.max_beats and beats >= cfg.max_beats:
                break
            sleep_for = align_next(interval_seconds=cfg.interval_seconds)
            deadline = time.time() + sleep_for
            while time.time() < deadline and not stop["flag"]:
                time.sleep(min(5.0, deadline - time.time()))
    log_line(cfg.log_path, "=== coordinator_shadow v1 退出 ===")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())