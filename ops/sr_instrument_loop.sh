#!/bin/sh
# sr_instrument_loop.sh -- IN27-H7 SR inheritance-readiness instrument loop.
#
# Appends one SR1..SR4 reading to the chronicle clock (slot s8) and refreshes the
# artifact/report every $SR_INTERVAL seconds.  Failures are logged and retried;
# the loop never exits on a transient database error.
#
# Usage:
#   ops/sr_instrument_loop.sh            # foreground, 1 h cadence
#   SR_INTERVAL=900 ops/sr_instrument_loop.sh
#   nohup ops/sr_instrument_loop.sh >/var/log/tp-fleet/sr_instrument.log 2>&1 &
#
# Env:
#   TP_DSN          psycopg connection string (default: local postgres socket)
#   SR_INTERVAL     seconds between readings (default 3600)
#   SR_WINDOW_DAYS  metric window (default 30)
#   TP_AGENT_ID     provenance stamp written into the artifact
set -u

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO" || exit 1

INTERVAL=${SR_INTERVAL:-3600}
WINDOW=${SR_WINDOW_DAYS:-30}
ART="artifacts/sr/sr_baseline.json"
MD="docs/reports/IN27_H7_sr_inheritance_readiness.md"
FIG="artifacts/sr/sr_baseline.png"

mkdir -p artifacts/sr docs/reports

log() { printf '%s sr_instrument %s\n' "$(date -u +%FT%TZ)" "$*"; }

log "start interval=${INTERVAL}s window=${WINDOW}d repo=$REPO"

while :; do
    if python3 -m tools.autonomy.sr_dashboard snapshot \
            --window-days "$WINDOW" \
            --sample-size 200 \
            --out "$ART" \
            --markdown "$MD" \
            --figure "$FIG" \
            --chronicle 2>&1 | sed 's/^/  /'
    then
        log "snapshot ok"
    else
        log "snapshot FAILED (kept looping; artifact left untouched)"
    fi
    sleep "$INTERVAL"
done