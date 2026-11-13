#!/usr/bin/env bash
# FP15-BUDGET-ACCOUNTING 一键复现（只读取证 + 帽曲线 + 记账用例）
set -euo pipefail
cd "$(dirname "$0")/.."
export FP15_DSN="${FP15_DSN:-postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql}"
python docs/fp15/fp15_intel.py --hours 24
python scripts/budget_cap_curve.py --hours 24
python -m pytest tests/test_budget_accounting.py -q
if [ -n "${TENSOR_TEST_PG_DSN:-}" ]; then python -m pytest backend/tests/test_budget_used_endpoint.py -q; else echo "SKIP endpoint suite: export TENSOR_TEST_PG_DSN"; fi
