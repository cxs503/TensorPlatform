#!/bin/bash
# TensorPlatform agent 舰队守护（v2）
# 修正1: 用 argv 里的 --agent-id 定位进程（env VAR=... 不会出现在 cmdline，导致重复拉起）
# 修正2: 逐个错峰启动（间隔 1s），避免瞬时打满限流
# 修正3: 轮询交给 worker 自己做抖动，守护只保证"活着"
set -u
FLEET=/root/tp-agent/fleet.json
LOGDIR=/var/log/tp-fleet
SUPLOG=/var/log/tp-fleet-supervisor.log
WOLLOG=/var/log/tp-agent-fleet.log
mkdir -p "$LOGDIR"; touch "$SUPLOG"

echo "=== $(date '+%F %T') fleet supervisor v2 start (pid $$) ===" >>"$SUPLOG"


MODELS_CACHE=/var/log/tp-fleet/relay_models.txt
refresh_models() {
  local base key
  base=$(grep -E "^LLM_BASE_URL=" /root/tp-agent/agent.env | cut -d= -f2-)
  key=$(grep -E "^LLM_API_KEY=" /root/tp-agent/agent.env | cut -d= -f2-)
  curl -s -m 8 -H "Authorization: Bearer $key" "$base/models" 2>/dev/null \
    | python3 -c "import json,sys;print('\n'.join(m['id'] for m in json.load(sys.stdin).get('data',[])))" >"$MODELS_CACHE.tmp" 2>/dev/null
  if [ -s "$MODELS_CACHE.tmp" ]; then mv "$MODELS_CACHE.tmp" "$MODELS_CACHE"
  else rm -f "$MODELS_CACHE.tmp"; fi
}

while true; do
  refresh_models
  /usr/bin/python3 /root/tp-agent/quota_fallback.py >>"$SUPLOG" 2>&1
  alive=0; launched=0
  while IFS=$'\t' read -r id role model sdaa; do
    [ -z "$id" ] && continue
    if pgrep -f "tp_worker.py --agent-id $id" >/dev/null 2>&1; then
      alive=$((alive+1)); continue
    fi
    if [ -s "$MODELS_CACHE" ] && ! grep -qx "$model" "$MODELS_CACHE"; then
      echo "$(date '+%F %T') [模型门] 拒绝拉起 $id：$model 不在中继清单（glm-5.3 事件 2026-10-31）" >>"$SUPLOG"
      continue
    fi
    echo "$(date '+%F %T') 拉起 $id (role=$role model=$model)" >>"$SUPLOG"
    if [ -n "$sdaa" ]; then export SDAA_VISIBLE_DEVICES="$sdaa"; else unset SDAA_VISIBLE_DEVICES; fi
    setsid --fork nohup python3 /root/tp-agent/tp_worker.py \
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