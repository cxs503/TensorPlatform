#!/bin/bash
# Per-model, per-slot keep-alive wrapper for the tensor-agent bridge daemon.
# Usage: run_agent_multi.sh <relay-model-id> [--claim-null] [--slot N] [--print-lock]
#   --claim-null:  this worker also picks up legacy NULL-model missions
#                  (designate exactly one worker, normally glm-5.2 slot 1)
#   --slot N:      worker slot (default 1). Worker 身份 = slot（会话 runner），
#                  非模型 API——同一 API 可挂 N 个 worker；claim 是平台侧
#                  FOR UPDATE SKIP LOCKED 原子分配，多 worker 无新竞态类。
#                  Slot 1 保持旧锁名（既有 per-model cron 行零改动）；
#                  slot >=2 锁 tensor-agent-<model>-w<N>.lock。
#   --print-lock:  只打印将用的锁路径并退出（ops/测试用，不启动守护）。
# crontab: @reboot + */5 per model (slot 1), agent_fleet.sh 增补 slot >=2。
set -a; source "$(dirname "$0")/agent.env"; set +a
MODEL="$1"
if [ -z "$MODEL" ]; then
  echo "usage: $0 <relay-model-id> [--claim-null] [--slot N] [--print-lock]" >&2
  exit 1
fi
shift || true
EXTRA=""
SLOT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --claim-null) EXTRA="--claim-null" ;;
    --slot) SLOT="$2"; shift ;;
    --print-lock) PRINT_LOCK=1 ;;
  esac
  shift
done
case "$SLOT" in ''|*[!0-9]*) SLOT=1 ;; esac
if [ "$SLOT" = "1" ]; then
  LOCK=/srv/tensor/shared/tensor_eco/tensor-agent-${MODEL}.lock
  LOG=/srv/tensor/shared/tensor_eco/agent_work/agent-${MODEL}.log
else
  LOCK=/srv/tensor/shared/tensor_eco/tensor-agent-${MODEL}-w${SLOT}.lock
  LOG=/srv/tensor/shared/tensor_eco/agent_work/agent-${MODEL}-w${SLOT}.log
fi
if [ -n "${PRINT_LOCK:-}" ]; then
  echo "$LOCK"
  exit 0
fi
export LLM_MODEL="openai/${MODEL}"
exec 9>"$LOCK"
flock -n 9 || exit 0
export HOME=/srv/tensor/shared/tensor_eco/agent_work/home
export TMPDIR=/srv/tensor/shared/tensor_eco/agent_work/tmp
mkdir -p "$HOME" "$TMPDIR"
exec /srv/tensor/shared/venvs/tensor_agent/bin/python "$(dirname "$0")/tensor_agent.py" \
  --model "$MODEL" --slot "$SLOT" $EXTRA \
  >> "$LOG" 2>&1
