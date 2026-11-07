#!/usr/bin/env bash
# IN27-H4 战略质询官 · 常驻调度壳（24h 一周期，与 ops/worker_loop.sh 同风格）
#
# 安装（二选一，二选一即可，勿同时跑）：
#   1) cron：  crontab -e  →  17 3 * * *  cd /root/tp-agent/work/TensorPlatform && \
#                TENSOR_API_BASE=$TENSOR_API_BASE TENSOR_AGENT_USER=... TENSOR_AGENT_PASS=... \
#                bash ops/interrogator_h4_loop.sh --once >> /var/log/interrogator_h4.log 2>&1
#   2) nohup： nohup bash ops/interrogator_h4_loop.sh >> /var/log/interrogator_h4.log 2>&1 &
#
# 默认行为：每 24h 跑一个周期并**真实立单**（--create-missions），周期结束后跑验收门。
# 门未 PASS 不阻塞循环：连续性只能靠时间累积，机制负责每天把账记上。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

INTERVAL="${INTERVAL:-86400}"          # 秒；24h
LEDGER="${LEDGER:-runs/IN27-H4/interrogator.db}"
OUT="${OUT:-runs/IN27-H4}"
API_BASE="${TENSOR_API_BASE:-http://127.0.0.1:8010/api/v1}"
LOCK="${LOCK:-$OUT/.interrogator.lock}"
ONCE=0
[[ "${1:-}" == "--once" ]] && ONCE=1

mkdir -p "$OUT"

# 单实例：上一周期没跑完时绝不叠跑（防重复立单/重复记账）
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -Is)] another cycle holds $LOCK, skip"
  exit 0
fi

run_cycle() {
  echo "[$(date -Is)] cycle start api=$API_BASE ledger=$LEDGER"
  python3 -m src.interrogator.cli --ledger "$LEDGER" --api-base "$API_BASE" --out "$OUT" \
      cycle --create-missions || echo "[$(date -Is)] cycle FAILED rc=$?"
  python3 -m src.interrogator.cli --ledger "$LEDGER" --out "$OUT" verify
  rc=$?
  # 退出码：0=PASS 3=PENDING 1=FAIL —— 都不影响下一周期（连续性靠时间累积）
  echo "[$(date -Is)] verify rc=$rc (0=PASS 3=PENDING 1=FAIL)"
}

while true; do
  run_cycle
  [[ $ONCE -eq 1 ]] && exit 0
  sleep "$INTERVAL"
done