#!/usr/bin/env python3
"""m15219 落表：把交差核验结论写入 tensor_maturity_verdicts（board-audit 席位）。

evidence 全部指针来自本单实测（verify_output.json / 文件 sha256 / SQL 行 / 命令），不含推断值。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "verify_output.json").read_text(encoding="utf-8"))
CH = {c["check"]: c for c in D["checks"]}
W15050 = Path("/root/tp-agent/work/m15050")
LEVEL = D["proposed_level"]


def sha(rel: str) -> str:
    p = W15050 / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "MISSING"


a5, a6, a10 = CH["A5_negative_controls"], CH["A6_benchmark_judge"], CH["A10_claim_vs_artifact"]
m = a6["measured"]
reg = a6["registered_cases"][0] if a6["registered_cases"] else {}

evidence = {
    "impl_files": [
        "tools/maturity_gate_h6/core.py:1-616（run_gate:519 / verify_artifact:604 / cross_source:244 / "
        "quote_binding:318 / recount_markdown:361 / evidence_support:408 / build_increment_ledger:435 / maturity_profile:474）",
        "tools/maturity_gate_h6/report.py:1-322（报告与 3 张图渲染）",
        "tools/maturity_gate_h6/cli.py:215 cmd_run / :270 cmd_verify / :283 cmd_mutate / :295 main",
        "tools/maturity_gate_h6/__main__.py:1-8（python3 -m 入口，main 上存在）",
        "data/maturity_gate_h6/h6.spec.json:1-35（源路径/锚点外置，代码零域词条）",
        f"tests/fixtures/capability_ledger/capabilities_snapshot.json sha256={sha('tests/fixtures/capability_ledger/capabilities_snapshot.json')}",
        f"artifacts/capability_ledger/capability_ledger_resolved.json sha256={sha('artifacts/capability_ledger/capability_ledger_resolved.json')}",
    ],
    "entry": [
        "python3 -m tools.maturity_gate_h6 {run|verify|mutate}（本单 cwd=/root/tp-agent/work/m15050 实跑 rc=0）",
        "docs/board_audit/m15219/verify_output.json:A2_repro_determinism（cli_rc=0, sha 三方相等）",
    ],
    "run_artifacts": [
        f"artifacts/maturity_gate_h6/gate.json {len(open(W15050 / 'artifacts/maturity_gate_h6/gate.json','rb').read())} B sha256={sha('artifacts/maturity_gate_h6/gate.json')}",
        f"artifacts/maturity_gate_h6/increment_ledger.json sha256={sha('artifacts/maturity_gate_h6/increment_ledger.json')}",
        f"artifacts/maturity_gate_h6/consistency.json sha256={sha('artifacts/maturity_gate_h6/consistency.json')}",
        f"artifacts/maturity_gate_h6/mutation_benchmark.json sha256={sha('artifacts/maturity_gate_h6/mutation_benchmark.json')}",
        "artifacts/maturity_gate_h6/fig1_maturity_profile.png / fig2_axes_health.png / fig3_increment_flow.png（34424/32015/48155 B，本单核对存在）",
        "注：上述 artifacts/* 与 runs/m15050/* 只在被审工作树 /root/tp-agent/work/m15050（head=6a1a550f3），"
        "生态基线 main=1649792e5 上 git cat-file -e HEAD:artifacts/maturity_gate_h6/gate.json → ABSENT（写出侧门禁滤除 artifacts/runs），main 仅有代码+docs",
    ],
    "tests": [
        "tools/maturity_gate_h6/tests/test_maturity_gate_h6.py（271 行）：本单复跑 `python3 -m pytest tools/maturity_gate_h6/tests -q` → 24 passed"
        f"（rc={CH['A9_tests']['rc']}）",
    ],
    "integration_callers": "none（decision path 0 命中：backend/ api/ ops/ harness_gateway/ .github/ deploy/ 均无 maturity_gate_h6 引用；"
                           "仅 scripts/post_h6_maturity_report.py:31,32,40 读产物 PNG 发报告，属下游读者）→ L3 不达",
    "benchmark": {
        "registered_case": f"tensor_benchmark_cases id={reg.get('id')} anchor={reg.get('anchor')} "
                           f"case_name='{reg.get('case_name')}' metric='{reg.get('metric')}' "
                           f"tolerance='{reg.get('tolerance')}' status={reg.get('status')}（从未 passed）",
        "got_vs_ref": {"ref": a6["tolerance"], "got_strict_pct": m["got_strict"], "got_structural_pct": m["got_structural"],
                       "within_tolerance": a6["in_tolerance"],
                       "method": "KPI-GATE-100 口径 100×|G1∧G2∧G3_prod|/5 条产出线，本单逐线独立探针（SQL+grep+settlement 复跑）"},
        "line_probes": {ln: {k: v for k, v in a6["measured"]["probes"][ln].items()
                             if k in ("gate_file", "G1_gate_exists", "G2_can_fail_struct",
                                      "G3_reject_prod", "G3_neg_control_test")} for ln in a6["measured"]["lines"]},
        "cross_check_existing": "docs/board_audit/m15168/evidence/artifacts/result.json current=60.0 / current_structural=80.0 → 与本单独立重算 60/80 一致",
        "self_mutation_benchmark": {"recall": a5["mutation_benchmark_reproduced"]["recall"],
                                    "false_positives": a5["mutation_benchmark_reproduced"]["false_positives"],
                                    "reproduced_by_audit": a5["mutation_benchmark_reproduced"]["match"],
                                    "note": "自指基准（门测门），5 例变异均自造 → 不构成 L4；M1-M5 召回可复现（本单复跑 mutate 一致）"},
    },
    "repro_cmd": "bash runs/m15050/repro_cmd.sh（本单实跑 rc=0）+ python3 -m tools.maturity_gate_h6 run --no-mutate "
                 "--work-root /root/tp-agent/work/m15050 --spec data/maturity_gate_h6/h6.spec.json → 重算 gate.json "
                 f"sha256={sha('artifacts/maturity_gate_h6/gate.json')[:32]}… 与 runs/m15050/gate_sha256.txt 完全一致（gate.json 无时间戳，确定性可复现）",
    "external_anchor": "docs/H6_MATURITY_GATE_G1.md §9 列 4 个仓内锚点（tools/capability_ledger_maturity/adjudication_engine.py、"
                       "tools/audit/capability_ledger.py、tools/maturity_gate/spec.py、docs/contracts/maturity-integration-v1.md）；"
                       "外部基准唯一裁判 tensor_benchmark_cases id=12 已注册但未达标（60/80% vs =100%）",
    "audit_report": "docs/board_audit/m15219/VERDICT_m15050.md + docs/board_audit/m15219/verify_output.json",
    "findings": {
        "F1_verify_blind_spots": f"报告 §⑤『篡改 gate.json 会被 --verify 检出』过宽：verify_artifact(core.py:604-620) 只比对 "
                                 f"verdict/criteria_pass_rate/thresholds_sha256/criteria/increments.chain_head；本单自造 3 变异"
                                 f"{a5['verify_blind_spots']}（maturity_score 0.75→0.99、账本条目等级翻转、引文 line_sha256 末位翻转）"
                                 f"全部 verified=true（正控 P1 verdict / P2 criteria 均 rc=1 检出，说明 verify 非空转）",
        "F2_source_gap_not_gated": f"报告 §⑦.6 称源缺失→判据 null→不通过；实际 sources.available=false 时 "
                                   f"criteria_with_null_value={a10['criteria_with_null_value']}、verdict={a10['gate_verdict']}、"
                                   f"criteria_pass_rate={a10['criteria_pass_rate']} → 缺口未进入判据面",
        "F3_no_integration": "无决策路径调用方 → L3 不达（m15032 前作同样结论，本轮返修未改）",
        "F4_benchmark_unmet": f"注册基准 case 容差 {a6['tolerance']}，got_strict={m['got_strict']}% / got_structural={m['got_structural']}% → 不在容差内；"
                              "H6 门不在 5 条产出线内，被审工件亦未计量该指标 → L4 不达",
        "F5_artifacts_not_on_main": "artifacts/maturity_gate_h6/* 与 runs/m15050/* 未进 main（门禁滤除），复现须回工作树重跑",
        "F6_score_decrease_honest": f"run_metadata 记 maturity_score 0.869048→0.75（-0.119048）为对账后下降，本单独立重算复现一致（非虚标）",
    },
    "level_rationale": f"L1✅（24 passed）/L2✅（真代码 616 行 + 入口 + 7 工件 + sha 恒定复现）/L3❌（无调用方）/L4❌（注册基准 60/80% vs =100%，"
                       f"自造变异基准属自指）→ 裁定 L{LEVEL}；与返修前 m14961 的 L1 相比确有 L2 实质补齐",
    "checks_not_run": ["集群 loong64 复跑（单机 0.3 s 级，且集群无本机工作树路径）",
                       "CI x86 侧复现（本机 loongarch64）",
                       "引文与线上 tensor_reports 正文逐字复核（仅核工件声明 match_rate=1.0/10 条）",
                       "doc 人读表数字篡改的独立变异（改动会写回被审工作树；仅复跑其 mutate M5）"],
}

payload = json.dumps(evidence, ensure_ascii=False)
sql = ("INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by) VALUES "
       "('TensorPlatform', 'm15050 H6-MATURITY-GATE', 15219, {lvl}, $json${ev}$json$::jsonb, 'board-audit') "
       "ON CONFLICT (repo, scope, verdict_by) DO UPDATE SET level=EXCLUDED.level, evidence=EXCLUDED.evidence, "
       "mission_id=EXCLUDED.mission_id RETURNING id, level, gate_state").format(lvl=LEVEL, ev=payload)
import tempfile, os
fd, path = tempfile.mkstemp(suffix=".sql", dir="/tmp")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(sql + ";\n")
os.chmod(path, 0o644)
r = subprocess.run(["su", "postgres", "-c",
                    f"psql -d tensorplatform -t -A -F '|' -f {path}"],
                   capture_output=True, text=True)
print("rc", r.returncode)
print(r.stdout.strip())
print(r.stderr.strip()[:500])