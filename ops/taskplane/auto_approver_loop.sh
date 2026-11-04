#!/bin/bash
# 自动审批器常驻循环（flock 单实例）
set -u
LOG=/var/log/tp-auto-approver.log
LOCK=/tmp/tp-auto-approver.lock
mkdir -p "$(dirname "$LOG")"; touch "$LOG"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 已有审批循环在跑，退出" >>"$LOG"
  exit 0
fi

echo "=== $(date '+%F %T') auto-approver loop start (pid $$) ===" >>"$LOG"
while true; do
  python3 /root/tp-agent/auto_approver.py --once >>"$LOG" 2>&1
  sleep 5
done