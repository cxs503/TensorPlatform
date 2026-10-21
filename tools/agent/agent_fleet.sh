#!/bin/bash
# agent_fleet.sh — worker 池巡检：按 workers.json 给每模型增补 slot >=2 的 worker。
#
# 设计（owner 2026-10-21 指令：worker 由 session 定，非 API 定）：
#   - slot 1 仍由既有 per-model cron 行保活（本脚本不碰）；
#   - 本脚本幂等：slot N 的锁被活 worker 持有时 flock -n 直接退出；
#     worker 死亡 → 锁释放 → 下轮巡检自动补位（*/5 keep-alive 语义不变）；
#   - workers.json owner 可编辑（同 roles.json 惯例），改完 5 分钟内生效。
# crontab: */5 * * * * bash /srv/tensor/shared/tensor_eco/TensorPlatform/tools/agent/agent_fleet.sh >> /srv/tensor/shared/tensor_eco/agent_work/fleet.log 2>&1
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
WJ="$D/workers.json"
LOG_TAG() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
[ -f "$WJ" ] || { echo "$(LOG_TAG) no workers.json — fleet idle"; exit 0; }
SPAWNED=0
while read -r MODEL N; do
  [ -z "$MODEL" ] && continue
  case "$N" in ''|*[!0-9]*|0|1) continue ;; esac
  for slot in $(seq 2 "$N"); do
    LOCK="/srv/tensor/shared/tensor_eco/tensor-agent-${MODEL}-w${slot}.lock"
    # 已有活 worker 持锁 → 跳过（flock 文件路径形式探测，不长期占用；
    # 探测释放与新 worker 拿锁间的微窗口由 wrapper 自身 flock 幂等兜底）
    if flock -n "$LOCK" true 2>/dev/null; then
      # 探测成功 = 无人持锁 → 启动 worker（它自己会拿同一把锁）
      nohup bash "$D/run_agent_multi.sh" "$MODEL" --slot "$slot" \
        >/dev/null 2>&1 &
      SPAWNED=$((SPAWNED+1))
      echo "$(LOG_TAG) spawned ${MODEL} slot ${slot}"
    fi
  done
done < <(python3 -c '
import json
try:
    cfg = json.load(open("'"$WJ"'"))
except Exception:
    raise SystemExit(0)
for m, n in cfg.items():
    if isinstance(n, int) and n >= 1:
        print(m, n)
')
echo "$(LOG_TAG) fleet pass done, spawned=$SPAWNED"
