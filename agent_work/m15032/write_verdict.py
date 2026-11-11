#!/usr/bin/env python3
"""m15032：把交差核验判级写入 tensor_maturity_verdicts（repo=TensorPlatform, verdict_by=board-audit）。"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ev = json.loads((ROOT / "runs" / "m15032_audit" / "evidence_verification.json").read_text())
tamper = json.loads((ROOT / "runs" / "m15032_audit" / "tamper_negative_control.json").read_text())

evidence = {
    "os": "board-audit m15032 of m14961 (H6-MATURITY-GATE), repo TensorPlatform, merged PR #1211 (commit a801c35be tree)",
    "impl_files": [
        "tools/maturity_gate_h6/core.py:1-720 (29,465 B；LEVELS/LEVEL_RANK 阶梯、cross_source:244、quote_binding:318、recount_markdown:361、evidence_support:408、build_increment_ledger:435、maturity_profile:474、run_gate:519、verify_artifact:604)",
        "tools/maturity_gate_h6/cli.py:215 cmd_run / :285 cmd_mutate / :295 main（run|verify|mutate 三子命令）",
        "tools/maturity_gate_h6/report.py（1-440：报告/图渲染）",
        "data/maturity_gate_h6/h6.spec.json（源路径/锚点全部外置，代码零域词条）",
    ],
    "entry": "python3 -m tools.maturity_gate_h6.cli run --work-root <root> --spec data/maturity_gate_h6/h6.spec.json --out artifacts/maturity_gate_h6",
    "run_artifacts": [
        "/root/tp-agent/work/m14961/artifacts/maturity_gate_h6/gate.json (含 verdict=PASS, criteria 8/8, axes 4 轴, increments 21 条哈希链)",
        "/root/tp-agent/work/m14961/artifacts/maturity_gate_h6/{consistency.json,increment_ledger.json,mutation_benchmark.json}",
        "/root/tp-agent/work/m14961/artifacts/maturity_gate_h6/{fig1_maturity_profile,fig2_axes_health,fig3_increment_flow}.png",
        "runs/m15032_audit/repro_artifacts/*（本单 m15032 独立复跑同一批工件；数值逐字段一致）",
    ],
    "tests": [
        "tools/maturity_gate_h6/tests/test_maturity_gate_h6.py（本单复跑：24 passed, 0.29 s）",
        "runs/m15032_audit/tamper_negative_control.json（本单独立负控：注入 9 例篡改，verify 仅检出 3 例，recall=0.333，fp=0）",
    ],
    "integration_callers": "none：grep -rn 'maturity_gate_h6' --include=*.py 仅 scripts/post_h6_maturity_report.py:40 读产物 PNG 发报告；无 api/backend/CI/战役门调用方 → 不达 L3",
    "benchmark": {
        "mutation_benchmark": {"ref": 5, "got": 5, "recall": 1.0, "false_positives": 0,
                               "error": "0/5 未检出", "note": "注入缺陷由本仓作者自造，非外部真值"},
        "target_axis_hit": {"ref": "5/5 逐轴命中", "got": "3/5", "error": "M1/M3 由非目标轴触发；axis_perfect 实为『全部检出』"},
        "cross_worktree_recompute": {"fields": ["inputs", "criteria", "thresholds_sha256", "axes", "maturity", "verdict", "increments.chain_head"], "mismatch": 0, "error": "0%"},
        "independent_verify_negcontrol": {"injected": tamper["injected"], "detected": tamper["detected"],
                                          "recall": tamper["recall"], "false_positive": tamper["false_positive"],
                                          "undetected": [c["case"] for c in tamper["cases"] if not c["detected"]]},
    },
    "repro_cmd": "python3 -m tools.maturity_gate_h6.cli run --work-root . --spec data/maturity_gate_h6/h6.spec.json --out artifacts/maturity_gate_h6 && python3 -m tools.maturity_gate_h6.cli verify --out artifacts/maturity_gate_h6  # m15032 实测 verdict=PASS, verified=true, problems=[]",
    "external_anchor": "none：h6.spec.json anchors 全为仓内定义文件（adjudication_engine.py / capability_ledger.py / maturity_gate/spec.py），无文献/解析解/独立基准 → 不达 L4",
    "evidence_mirror": [
        "docs/reports/m15032_evidence/evidence_verification.json (三件套逐项核验矩阵)",
        "docs/reports/m15032_evidence/tamper_negative_control.json (独立负控 9 例)",
        "docs/reports/m15032_evidence/gate.json + mutation_benchmark.json + consistency.json (m15032 复跑工件)",
        "docs/reports/m15032_evidence/pytest_tools_maturity_gate_h6.log (24 passed) / verify_output.json",
        "docs/reports/m15032_audit_m14961.md (审计报告，六要素) / fig1_triad_verification.png / fig2_negcontrol_recall.png",
        "agent_work/m15032/tamper_negcontrol.py (负控可复跑脚本)",
    ],
    "report": "tensor_reports id=14035 (m14961 原报告, mission 14961) / id=14130 (本单审计报告, mission 15032)",
    "level_rationale": "L2：真代码（输入=3 源台账→计算=四轴交叉+阶梯+增量链→输出=gate/ledger/报告），有工程入口、可复跑工件、PR #1211 合并；L3 缺集成调用方，L4 缺外部基准。",
    "findings": {
        "F1_antitamper_gap": "verify_artifact(core.py:604) 只比对 verdict/criteria_pass_rate/thresholds_sha256/criteria/increments.chain_head 五键；axes、maturity、increments.entries（含条目 sha256/prev_sha256）手改全部漏检（独立负控 9 例仅检出 3 例）→ README『任何数字都能被 --verify 逐字节复算』与 docs/H6_MATURITY_GATE_G1.md 注意点 5『单条事件含 prev_sha256 可发现中间被改』为过度声明。",
        "F2_narrow_test": "唯一 verify 负控测试 test_verify_detects_tampered_gate_artifact（tests:242）只翻 criteria[0].pass，恰好命中三键之一 → 测试覆盖窄于声称覆盖面。",
        "F3_axis_naming": "cli.py:209 axis_perfect = all(detected)，名不副实（应为 all_detected）；target_axis_hit 自报 3/5 已诚实但不显眼。",
        "F4_artifacts_not_committed": "artifacts/maturity_gate_h6/ 未入 git（artifacts/ 默认忽略），合并树内工件需复跑生成；本单已在 m15032 复跑得到同值工件（runs/m15032_audit/repro_artifacts/）。",
        "F5_doc_path_leak": "docs/H6_MATURITY_GATE_G1.md 内嵌绝对 work-root 路径，跨工作树复跑必然产生 diff（数值不变）；建议改为相对路径。",
        "F6_live_probe_gap": "第三源 live_probe 显式 available=false（harness 能力清单未随工作树同步），判据取 null 不通过 —— 诚实缺口、非造假，但说明复核只覆盖仓内两源。",
    },
}

payload = {
    "repo": "TensorPlatform",
    "scope": "m14961 H6-MATURITY-GATE",
    "mission_id": 15032,
    "level": int(ev["verdict_level"]),
    "evidence": evidence,
    "verdict_by": "board-audit",
}

sql = "INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by) VALUES ('TensorPlatform', 'm14961 H6-MATURITY-GATE', %d, %d, $json$%s$json$::jsonb, 'board-audit') ON CONFLICT (repo, scope, verdict_by) DO UPDATE SET level=EXCLUDED.level, evidence=EXCLUDED.evidence, mission_id=EXCLUDED.mission_id RETURNING id, gate_state;" % (
    payload["mission_id"], payload["level"], json.dumps(evidence, ensure_ascii=False))

sqlfile = ROOT / "runs" / "m15032_audit" / "insert_verdict.sql"
sqlfile.write_text(sql + "\n")
p = subprocess.run(["psql", "-U", "postgres", "-d", "tensorplatform", "-v", "ON_ERROR_STOP=1", "-f", str(sqlfile)],
                   capture_output=True, text=True)
print(p.stdout, p.stderr)
(ROOT / "runs" / "m15032_audit" / "verdict_payload.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=1) + "\n")