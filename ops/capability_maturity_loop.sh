#!/bin/sh
# capability_maturity_loop.sh — 能力成熟度自治管线的定期扫描壳（p156w2-n07）
#
# 只做三件事：跑 `tools.capability_maturity_pipeline.cli run`、按节拍重跑、失败不退出。
# 扫描/判定/回滚/审计的全部逻辑在 python 侧（工具层），壳子里不许有业务判断。
#
# 用法:
#   ops/capability_maturity_loop.sh                        # 前台，默认 1800s 节拍
#   CAP_MATURITY_INTERVAL=600 ops/capability_maturity_loop.sh
#   nohup ops/capability_maturity_loop.sh >> logs/daemon_capability_maturity.log 2>&1 &
#
# 环境变量（全部可选）:
#   CAP_MATURITY_INTERVAL  节拍秒数（默认 1800）
#   CAP_MATURITY_ROOTS     扫描根（空格分隔，默认 "artifacts runs tensor_reports"）
#   CAP_MATURITY_LEDGER    台账路径（默认 artifacts/capability_maturity/ledger.json）
#   CAP_MATURITY_ATTR      归因配置（默认 artifacts/capability_maturity/attribution.json，存在才传）
#   CAP_MATURITY_LOG       日志文件（默认 logs/daemon_capability_maturity.log）
set -u

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO" || exit 1

INTERVAL=${CAP_MATURITY_INTERVAL:-1800}
ROOTS=${CAP_MATURITY_ROOTS:-"artifacts runs tensor_reports"}
LEDGER=${CAP_MATURITY_LEDGER:-artifacts/capability_maturity/ledger.json}
ATTR=${CAP_MATURITY_ATTR:-artifacts/capability_maturity/attribution.json}
LOG=${CAP_MATURITY_LOG:-logs/daemon_capability_maturity.log}

mkdir -p "$(dirname "$LOG")" "$(dirname "$LEDGER")" runs/capability_maturity

log() {
    printf '{"ts":"%s","event":"%s","detail":"%s"}\n' \
        "$(date -u +%FT%TZ)" "$1" "$2" >>"$LOG"
}

while :; do
    TS=$(date -u +%Y%m%dT%H%M%SZ)
    OUT="runs/capability_maturity/run_${TS}.json"
    set -- --worktree . --ledger "$LEDGER" --out "$OUT" --roots $ROOTS
    if [ -f "$ATTR" ]; then
        set -- "$@" --attribution "$ATTR"
    fi
    # flock 防重入：上一轮没跑完就跳过本轮（单机多实例/慢盘都安全）
    if command -v flock >/dev/null 2>&1; then
        if ! flock -n "$LEDGER.lock" python3 -m tools.capability_maturity_pipeline.cli run "$@" >>"$LOG" 2>&1; then
            RC=$?
            case $RC in
                1) log "verify_failed" "chain or ledger verification failed (see $OUT)" ;;
                0) : ;;
                *) log "run_error" "exit=$RC (ledger locked or python error)" ;;
            esac
        else
            log "round_ok" "$OUT"
        fi
    else
        if python3 -m tools.capability_maturity_pipeline.cli run "$@" >>"$LOG" 2>&1; then
            log "round_ok" "$OUT"
        else
            log "run_error" "exit=$? (see $OUT)"
        fi
    fi
    sleep "$INTERVAL"
done