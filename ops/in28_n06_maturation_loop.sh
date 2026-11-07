#!/usr/bin/env bash
# IN28-N06 —— SR1 成熟化连续产出（1 h 一拍）
#
# 为什么要 loop：SR1 的样本随影子官逐拍增长，一次快照只能算「此刻」的数；
# IN27-H7 的仪表纪律是「连续产出」，所以成熟化也要按拍跑，并把每次读数落 chronicle s8。
#
# 用法：
#   nohup ops/in28_n06_maturation_loop.sh >> /var/log/tp-fleet/in28_maturation.log 2>&1 &
# 停止：pkill -f in28_n06_maturation_loop.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
INTERVAL="${INTERVAL:-3600}"
DSN="${TP_DSN:-dbname=tensorplatform user=postgres host=/var/run/postgresql}"
mkdir -p runs/in28_n06/sandbox runs/in28_n06/figs artifacts/coordinator_shadow

echo "[$(date -u +%FT%TZ)] IN28-N06 maturation loop start (interval=${INTERVAL}s)"
while true; do
  python3 -m tools.coordinator_shadow.maturation \
      --dsn "$DSN" \
      --out artifacts/coordinator_shadow/sr1_maturation.json \
      --publish-chronicle >> runs/in28_n06/loop.log 2>&1
  rc=$?
  python3 -m tools.coordinator_shadow.maturation_figures \
      --records runs/in28_n06/sandbox/records.json >> runs/in28_n06/loop.log 2>&1
  python3 -m tools.coordinator_shadow.retirement --dsn "$DSN" \
      --out artifacts/coordinator_shadow/v0_retirement.json >> runs/in28_n06/loop.log 2>&1
  echo "[$(date -u +%FT%TZ)] beat done rc=${rc}" >> runs/in28_n06/loop.log
  sleep "$INTERVAL"
done