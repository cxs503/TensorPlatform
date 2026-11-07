#!/usr/bin/env python3
"""backlog_refiller daemon 入口。

用法:
  python -m backlog_refiller.daemon_entry [--api-base URL] [--loop-interval SEC] [--max-cycles N]

环境变量:
  DATABASE_URL: 数据库连接字符串
  API_BASE: 平台 API 基础 URL（默认 http://localhost:8001）
  LOOP_INTERVAL: 循环间隔秒数（默认 60）
  MAX_CYCLES: 最大循环次数（默认无限）
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backlog_refiller.daemon import BacklogRefillerDaemon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("backlog_refiller.entry")


def get_db_session():
    """获取数据库会话工厂。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = os.environ.get("DATABASE_URL", "postgresql://foundation:foundation@localhost:5432/open_digital_foundation")
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine)
    return session_factory


def main():
    parser = argparse.ArgumentParser(description="常设补给器 daemon")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://localhost:8001"),
                        help="平台 API 基础 URL")
    parser.add_argument("--loop-interval", type=int, default=int(os.environ.get("LOOP_INTERVAL", "60")),
                        help="循环间隔（秒）")
    parser.add_argument("--max-cycles", type=int, default=int(os.environ.get("MAX_CYCLES", "0")),
                        help="最大循环次数（0=无限）")
    parser.add_argument("--pending-threshold", type=int, default=0,
                        help="pending 任务数阈值")
    parser.add_argument("--idle-worker-threshold", type=int, default=6,
                        help="idle worker 数阈值")
    args = parser.parse_args()

    db_factory = get_db_session()
    daemon = BacklogRefillerDaemon(
        db_session_factory=db_factory,
        api_base=args.api_base,
        loop_interval=args.loop_interval,
        pending_threshold=args.pending_threshold,
        idle_worker_threshold=args.idle_worker_threshold,
    )

    # 信号处理
    def handle_signal(signum, frame):
        logger.info("收到信号 %d，停止 daemon", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    max_cycles = args.max_cycles if args.max_cycles > 0 else None
    logger.info("daemon 启动 api_base=%s loop_interval=%d max_cycles=%s",
                args.api_base, args.loop_interval, max_cycles)
    daemon.run_loop(max_cycles=max_cycles)
    logger.info("daemon 退出")


if __name__ == "__main__":
    main()
