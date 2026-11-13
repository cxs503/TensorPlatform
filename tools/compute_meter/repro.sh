#!/bin/sh
# 一键复现计算调度策略+计量（全部产物落 artifacts/compute_meter/，退出码即门判）
# 用法: sh tools/compute_meter/repro.sh
set -eu
cd "$(dirname "$0")/../.."
A=artifacts/compute_meter
mkdir -p "$A"
echo "== 单测（策略/计量/看板/门/注入）"
python3 -m pytest tests/compute_meter -q
echo "== 集群账本回填（cluster_jobs → compute_ledger，幂等）"
python3 -m tools.compute_meter.cli ingest
echo "== 看板/报表（SQL 账本 + 实验账本叠加）"
python3 -m tools.compute_meter.cli --store "sql,jsonl:$A/ab_treatment.jsonl" report \
    --days 7 --json "$A/report_metrics.json" --markdown "$A/panorama_block.md" --scope share
echo "== 门：本机占比（scope=share，退出码 0=过 / 3=失败 / 4=证据不足）"
set +e
python3 -m tools.compute_meter.cli --store "sql,jsonl:$A/ab_treatment.jsonl" gate \
    --days 7 --scope share > "$A/gate_share_union.json"; echo "share gate rc=$?"
python3 -m tools.compute_meter.cli --store "sql,jsonl:$A/ab_treatment.jsonl" gate \
    --days 7 > "$A/gate_all_union.json"; echo "all gate rc=$?"
python3 -m tools.compute_meter.cli --store "jsonl:$A/redteam_local_heavy.jsonl" gate \
    --scope share --min-cpu-seconds 60 > "$A/gate_redteam.json"; echo "redteam rc=$?（应=3）"
set -e
echo "== 对照实验（A 本机强制 vs B 策略路由，每次 30 CPU 秒）"
python3 -m tools.compute_meter.ab_experiment --burn-seconds 30 --repeats 3 \
    --out "$A/ab_result.json" > "$A/ab_stdout.json" || echo "ab rc=$?"
echo "== 图"
python3 -m tools.compute_meter.make_figures
echo "== PANORAMA 注入（幂等：先 --dry-run 看差异）"
python3 -m tools.compute_meter.cli --store "sql,jsonl:$A/ab_treatment.jsonl" \
    inject-panorama --days 7 --scope share --dry-run
ls -la "$A"
