#!/bin/bash
# TensorPlatform 常驻 agent worker 循环（单实例，自动轮询任务队列）
# 用法: bash worker_loop.sh [--worker-id NAME]
set -u
AGENT_DIR=/srv/tensor/agent
LOG=/var/log/tp-agent.log
LOCK=/tmp/tp-agent.lock
mkdir -p "$(dirname "$LOG")"; touch "$LOG"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 已有 worker 循环在跑，退出" >>"$LOG"
  exit 0
fi

echo "=== $(date '+%F %T') worker loop start (pid $$) ===" >>"$LOG"
while true; do
  python3 "$AGENT_DIR/tp_worker.py" --once >>"$LOG" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "[$(date '+%F %T')] worker 异常退出 rc=$rc，30s 后重试" >>"$LOG"
    sleep 30
  else
    sleep 15
  fi
done