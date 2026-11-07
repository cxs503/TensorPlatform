#!/usr/bin/env bash
# 放门自动化 daemon 常驻壳（IN20-R2）
#
# 为什么要壳：`gate_auto_release.py --loop` 内部已吞单轮异常，但进程级崩溃
# （OOM/psql 连接池炸/宿主重启）仍需自愈；本壳只做「拉起—退避—重启」，
# 不参与任何判据逻辑，替身因此独立于人类会话与 AI 协调者会话。
#
# 用法（宿主）：
#   GATE_PG_USER=foundation GATE_PG_PASSWORD=*** ./gate_auto_release_loop.sh &
# 或交给 systemd：deploy/taskplane/gate-auto-release.service
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
INTERVAL="${GATE_RELEASE_INTERVAL:-300}"
STATE_DIR="${GATE_RELEASE_STATE_DIR:-/var/lib/gate-auto-release}"
LOG="${GATE_RELEASE_LOG:-/var/log/tensorplatform/gate-auto-release.log}"
ACTOR="${GATE_RELEASE_ACTOR:-gate-auto-release}"
ROOTS="${GATE_ARTIFACT_ROOTS:-/root/tp-agent/work/*}"
PG_HOST="${GATE_PG_HOST:-127.0.0.1}"
PG_USER="${GATE_PG_USER:-foundation}"
PG_DB="${GATE_PG_DB:-tensorplatform}"

mkdir -p "$(dirname "$LOG")" "$STATE_DIR"

log() { printf '[%s] %s\n' "$(date -Is)" "$*" >>"$LOG"; }

# 单实例锁：两个 daemon 同时扫会重复记账（虽有幂等 SQL 兜底，但刷噪声）
LOCK="$STATE_DIR/daemon.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another gate_auto_release daemon holds $LOCK — exit"
  exit 0
fi

log "start pid=$$ interval=${INTERVAL}s state=${STATE_DIR} roots=${ROOTS}"
backoff=5
while true; do
  "$PY" "$HERE/gate_auto_release.py" --loop --interval "$INTERVAL" \
      --actor "$ACTOR" --state-dir "$STATE_DIR" \
      --artifact-roots "$ROOTS" \
      --pg-host "$PG_HOST" --pg-user "$PG_USER" --pg-db "$PG_DB" >>"$LOG" 2>&1
  rc=$?
  log "daemon exited rc=${rc}; restart in ${backoff}s"
  sleep "$backoff"
  backoff=$(( backoff < 120 ? backoff * 2 : 120 ))
  # 超过 2min 正常运行的进程重新从 5s 退避起（防「一次坏数据把退避钉死」）
  if [ "$rc" -eq 0 ]; then backoff=5; fi
done