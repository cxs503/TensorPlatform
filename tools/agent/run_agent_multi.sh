#!/bin/bash
# Per-model keep-alive wrapper for the tensor-agent bridge daemon.
# Usage: run_agent_multi.sh <relay-model-id> [--claim-null]
#   --claim-null: this daemon also picks up legacy NULL-model missions
#                 (designate exactly one daemon, normally glm-5.2)
# crontab: @reboot + */5 per model, same pattern as the old single daemon.
set -a; source "$(dirname "$0")/agent.env"; set +a
MODEL="$1"
if [ -z "$MODEL" ]; then
  echo "usage: $0 <relay-model-id> [--claim-null]" >&2
  exit 1
fi
export LLM_MODEL="openai/${MODEL}"
EXTRA=""
if [ "$2" = "--claim-null" ]; then EXTRA="--claim-null"; fi
LOCK=/srv/tensor/shared/tensor_eco/tensor-agent-${MODEL}.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
export HOME=/srv/tensor/shared/tensor_eco/agent_work/home
export TMPDIR=/srv/tensor/shared/tensor_eco/agent_work/tmp
mkdir -p "$HOME" "$TMPDIR"
exec /srv/tensor/shared/venvs/tensor_agent/bin/python "$(dirname "$0")/tensor_agent.py" \
  --model "$MODEL" $EXTRA \
  >> /srv/tensor/shared/tensor_eco/agent_work/agent-${MODEL}.log 2>&1
