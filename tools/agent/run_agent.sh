#!/bin/bash
# tensor-agent launcher — sanitized env, log to agent_work/agent.log
set -a; source "$(dirname "$0")/agent.env"; set +a
export HOME=/srv/tensor/shared/tensor_eco/agent_work/home
export TMPDIR=/srv/tensor/shared/tensor_eco/agent_work/tmp
mkdir -p "$HOME" "$TMPDIR"
exec /srv/tensor/shared/venvs/tensor_agent/bin/python "$(dirname "$0")/tensor_agent.py"
