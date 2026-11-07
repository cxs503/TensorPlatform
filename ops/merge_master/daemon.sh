#!/usr/bin/env bash
# merge_master 常驻守护（机制替身：不依赖任何人类会话，也不依赖任何 AI 协调者会话）
#
# 三种跑法都在本脚本里：
#   ./daemon.sh plan       只判不并（巡检/证据固化）
#   ./daemon.sh execute    真的合并 + 回写（治理动作，需 TP_MM_ALLOW_EXECUTE=1）
#   ./daemon.sh loop       常驻：每 interval 秒一轮，心跳落 heartbeat.json
#
# 为什么是容器/setsid 而不是「某个 agent 会话里的一个进程」：
#   owner 渐退令要求机制在「明天没有人类来」时继续跑。会话会被回收、agent 会
#   换模型，只有脱离会话的 daemon（restart=always + 心跳 + 审计链）才谈得上
#   independent。审计链与回滚规格是本脚本产出的两部分证据。
set -uo pipefail

ROOT="${TP_REPO_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
OUT="${TP_MM_OUTDIR:-$ROOT/artifacts/merge_master}"
INTERVAL="${TP_MM_INTERVAL:-300}"
REPOS="${TP_MM_REPOS:-TensorPlatform}"
MODE="${1:-plan}"

cd "$ROOT" || exit 14
mkdir -p "$OUT"

common=(--repos $REPOS --outdir "$OUT" --audit "$OUT/audit.jsonl"
        --max-merges "${TP_MM_MAX_MERGES:-3}"
        --max-mutations "${TP_MM_MAX_MUTATIONS:-8}")

case "$MODE" in
  plan)
    exec python3 -m tools.merge_master.cli plan "${common[@]}" \
         --include-recent "${TP_MM_RECENT:-10}" --out plan.json
    ;;
  execute)
    if [ "${TP_MM_ALLOW_EXECUTE:-0}" != "1" ]; then
      echo "refused: 执行合并需显式 TP_MM_ALLOW_EXECUTE=1（治理动作阈值）" >&2
      exit 2
    fi
    exec python3 -m tools.merge_master.cli cycle "${common[@]}" --execute
    ;;
  loop)
    exec python3 -m tools.merge_master.cli daemon "${common[@]}" \
         --interval "$INTERVAL" ${TP_MM_ALLOW_EXECUTE:+--execute}
    ;;
  selfcheck)
    exec python3 -m tools.merge_master.cli selfcheck --repos $REPOS \
         --audit "$OUT/audit.jsonl"
    ;;
  verify)
    exec python3 -m tools.merge_master.cli verify-audit --audit "$OUT/audit.jsonl"
    ;;
  *)
    echo "usage: $0 {plan|execute|loop|selfcheck|verify}" >&2
    exit 14
    ;;
esac