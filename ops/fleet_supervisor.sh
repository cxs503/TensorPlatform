#!/bin/bash
# TensorPlatform agent 舰队守护（v2）
# 修正1: 用 argv 里的 --agent-id 定位进程（env VAR=... 不会出现在 cmdline，导致重复拉起）
# 修正2: 逐个错峰启动（间隔 1s），避免瞬时打满限流
# 修正3: 轮询交给 worker 自己做抖动，守护只保证"活着"
set -u
FLEET=/srv/tensor/agent/fleet.json
LOGDIR=/var/log/tp-fleet
SUPLOG=/var/log/tp-fleet-supervisor.log
WOLLOG=/var/log/tp-agent-fleet.log
mkdir -p "$LOGDIR"; touch "$SUPLOG"

echo "=== $(date '+%F %T') fleet supervisor v2 start (pid $$) ===" >>"$SUPLOG"

while true; do
  alive=0; launched=0
  while IFS=$'\t' read -r id role model sdaa; do
    [ -z "$id" ] && continue
    if pgrep -f "tp_worker.py --agent-id $id" >/dev/null 2>&1; then
      alive=$((alive+1)); continue
    fi
    echo "$(date '+%F %T') 拉起 $id (role=$role model=$model)" >>"$SUPLOG"
    if [ -n "$sdaa" ]; then export SDAA_VISIBLE_DEVICES="$sdaa"; else unset SDAA_VISIBLE_DEVICES; fi
    setsid --fork nohup python3 /srv/tensor/agent/tp_worker.py \
      --agent-id "$id" --role "$role" --model "$model" \
      >>"$LOGDIR/$id.log" 2>&1 </dev/null
    launched=$((launched+1))
    sleep 1
  done < <(python3 -c "
import json
for w in json.load(open('$FLEET')):
    print(w['id'], w['role'], w['model'], w.get('sdaa',''), sep='\t')
")
  echo "$(date '+%F %T') 巡检：存活 $alive，新拉起 $launched" >>"$SUPLOG"
  sleep 30
done