#!/usr/bin/env bash
# Reproduce the P478W1-N03 evidence chain (structural MOCK detector).
#
#   bash scripts/reproduce_p478w1_n03.sh [base_url]
#
# Needs the platform API (default :8002).  Steps are independent; each writes its
# artefact under artifacts/mock_detector/ so `report` can always be re-run alone.
set -eu
cd "$(dirname "$0")/.."
BASE="${1:-http://127.0.0.1:8002}"
OUT=artifacts/mock_detector
EXCL="simulat|train|upload|stream|websocket|batch|delete|/\{|export|download|login|token|optimiz|spectrum|rao|curve"

# 1 known-answer fixtures (criterion C1: the detector must not be vacuous)
python3 -m tools.mock_detector.cli fixtures --out "$OUT"

# 2 main sweep: schema-synthesised payloads, ledger-referenced endpoints first
python3 -m tools.mock_detector.cli scan --base-url "$BASE" --out "$OUT" \
        --max-targets 60 --concurrency 3 --timeout 12 --budget 26 --max-axes 4 \
        --deadline 40 --transport-retries 1 --prioritise-ledger --exclude "$EXCL"

# 3 supplementary sweep: payloads injected from the domain-owned audit registry
python3 -m tools.mock_detector.cli scan --base-url "$BASE" --out "$OUT/injected" \
        --from-audit-specs --max-targets 10 --concurrency 3 --timeout 20 \
        --budget 30 --max-axes 6 --deadline 45

# 4 reproducibility (criterion C4) + ledger reconciliation (criterion C5)
python3 -m tools.mock_detector.cli repeat --scan "$OUT/scan.json" --out "$OUT" --n 8
python3 -m tools.mock_detector.cli ledger --scan "$OUT/scan.json" --out "$OUT" \
        --apply-to "$OUT/capability_ledger_derived.json"

# 5 figures + criteria.json + markdown (criterion C2, C3, C6)
python3 -m tools.mock_detector.cli report --out "$OUT" \
        --docs docs/reports/p478w1_n03_mock_detector.md

# 6 pytest suite
python3 -m pytest tools/mock_detector/tests/test_mock_detector.py -q