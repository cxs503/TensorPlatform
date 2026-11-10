#!/bin/sh
# sediment_line_loop.sh — 被动进水口守护壳（owner 2026-11-10 定位令 / FEAT-TK-GOV-INFRA）
#
# 只做三件事：起 tools/sediment_line.py scan、按 300s 节拍重跑、失败不退出。
# 单轮逻辑（判定/派单/幂等/配额）全在 python 侧，壳子里不许有业务判断。
#
# 用法:
#   ops/sediment_line_loop.sh                       # 前台，5min 节拍
#   SEDIMENT_INTERVAL=300 ops/sediment_line_loop.sh  # 显式节拍
#   nohup ops/sediment_line_loop.sh >> logs/daemon_sediment.log 2>&1 &
#
# 环境变量（全部可选）:
#   TENSOR_API_BASE     平台 API 基址（默认 http://127.0.0.1:8010/api/v1）
#   TENSOR_AGENT_USER   平台登录账号（必填，缺失即快速失败）
#   TENSOR_AGENT_PASS   平台登录口令（必填）
#   SEDIMENT_INTERVAL   轮询秒数（默认 300）
#   SEDIMENT_DAYS       扫描窗口天数（默认 7，覆盖守护停机补扫）
#   SEDIMENT_MAX_RUN    单轮派单上限（默认 20）
#   SEDIMENT_DAILY_CAP  当日派单总上限（默认 40）
#   SEDIMENT_PER_REPO   单仓当日上限（默认 6）
#   SEDIMENT_LOG        日志文件（默认 logs/daemon_sediment.log）
set -u

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO" || exit 1

INTERVAL=${SEDIMENT_INTERVAL:-300}
DAYS=${SEDIMENT_DAYS:-7}
MAX_RUN=${SEDIMENT_MAX_RUN:-20}
DAILY_CAP=${SEDIMENT_DAILY_CAP:-40}
PER_REPO=${SEDIMENT_PER_REPO:-6}
LOG=${SEDIMENT_LOG:-logs/daemon_sediment.log}

mkdir -p "$(dirname "$LOG")" runs/sediment_line

if [ -z "${TENSOR_AGENT_USER:-}" ] || [ -z "${TENSOR_AGENT_PASS:-}" ]; then
    echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"level\":\"ERROR\",\"event\":\"config\",\"detail\":\"TENSOR_AGENT_USER/TENSOR_AGENT_PASS 未设置\"}" >>"$LOG"
    exit 78
fi

# 单实例锁：守护重复拉起时后到者直接退出（多开=重复派单风险，虽有 task_key 幂等兜底）
LOCK=${SEDIMENT_LOCK:-runs/sediment_line/loop.lock}
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"level\":\"WARN\",\"event\":\"lock\",\"detail\":\"already running ($LOCK)\"}" >>"$LOG"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"level\":\"INFO\",\"event\":\"start\",\"interval\":$INTERVAL,\"days\":$DAYS,\"max_per_run\":$MAX_RUN,\"daily_cap\":$DAILY_CAP}" >>"$LOG"

while :; do
    python3 tools/sediment_line.py scan \
        --days "$DAYS" --max-per-run "$MAX_RUN" \
        --daily-cap "$DAILY_CAP" --per-repo-daily "$PER_REPO" \
        --log-file "$LOG" >>"$LOG" 2>&1
    rc=$?
    # 非零只记账不退出：平台 API 抖/单轮派单部分失败不得中断守护（下轮补扫）
    [ "$rc" -ne 0 ] && echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"level\":\"WARN\",\"event\":\"round_nonzero\",\"rc\":$rc}" >>"$LOG"
    sleep "$INTERVAL"
done