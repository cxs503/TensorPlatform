#!/bin/bash
# rootless docker 看门狗：dockerd 随 LBM 会话陪葬事故（2026-10-19 12:35，
# Linger=no，平台全灭 18 分钟）的自愈。owner 账号无 sudo 无法 enable-linger，
# owner 2026-10-19 批准本看门狗替代。保守策略：仅当 socket 不通且无 dockerd
# 进程（干净死亡）才拉起；半死状态（进程在 socket 坏）留给 operator 班次按
# playbook 处置，不自动 kill。
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
LOG=/srv/tensor/shared/tensor_eco/agent_work/docker_watchdog.log
if docker info >/dev/null 2>&1; then exit 0; fi
if pgrep -u "$(id -u)" -x dockerd >/dev/null; then
  echo "$(date -u +%FT%TZ) socket dead but dockerd alive — leave to operator (playbook)" >> "$LOG"
  exit 0
fi
echo "$(date -u +%FT%TZ) dockerd dead — relaunching" >> "$LOG"
nohup setsid /srv/tensor/operator-c/rootless-deps/launch-rootless.sh >> "$LOG" 2>&1 &
