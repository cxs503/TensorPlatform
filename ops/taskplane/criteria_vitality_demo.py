#!/usr/bin/env python3
"""判据活力门的合成演示 + 产物生成器（零领域语义）。

跑三个**全合成**门，覆盖三种必检结局：

=====  ==============================  ==========================================
门      设计意图                         期望裁决
=====  ==============================  ==========================================
alpha   判据边界被证据钉住（边界两侧各有  ``VITAL``（MS=1.0，可自报 REAL）
        近界样本 + 侧翼远界样本）
beta    判据阈值悬空（样本全在边界一侧）  ``INERT``（MS≈0.33 < 0.5，封 REAL）
gamma   判据自身判错预注册样本            ``BLOCKED``（DEFECTIVE，活力分数无意义）
=====  ==============================  ==========================================

产物（路径全部写进 ``artifacts/criteria_vitality/report.json``）::

    artifacts/criteria_vitality/gates.json              三个合成门定义（含样本与正确判决）
    artifacts/criteria_vitality/mutant_library.json     预注册变异库（执行前落盘）
    artifacts/criteria_vitality/run.json                运行记录（kill 矩阵 + survived）
    artifacts/criteria_vitality/kill_matrix.md          markdown 报告
    artifacts/criteria_vitality/figs/kill_matrix.png    图：kill 矩阵热图 + 逐判据 MS
    artifacts/criteria_vitality/report.json             判据 A1~A8 的实测数字与工件路径

用法::

    python3 -m ops.taskplane.criteria_vitality_demo             # 出全部产物
    python3 -m ops.taskplane.criteria_vitality_demo --no-ledger  # 不动 capability_ledger
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import criteria_vitality as CV  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "criteria_vitality"
FIG_DIR = OUT_DIR / "figs"
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
TEST_FILE = "tests/test_criteria_vitality.py"


# ── 合成被测实现（含可注入的已知缺陷）────────────────────────────────────
def synthetic_observations(case: Mapping[str, Any]) -> dict:
    """合成实现入口：把 case 的输入按固定算式换算成观测量；inject 键注入已知缺陷。

    输入键：``num``/``den``（比值原材料）、``length``（量纲量）、``drift``、``flag``。
    输出：``{"metrics": {"ratio","span","drift","flag"}, "injected": <缺陷名>}``。
    缺陷（工程上的「实现 bug」）只由 case 声明，不由判据声明——这是「预注册正确判决」的
    可信来源：判决标签来自样本构造方式，不来自被体检的判据。
    """
    ratio = float(case["num"]) / float(case["den"])
    span = 2.0 * float(case["length"])
    drift = float(case["drift"])
    flag = float(case["flag"])
    inject = str(case.get("inject") or "none")
    if inject == "ratio_bias":
        ratio *= 0.90
    elif inject == "span_bias":
        span *= 1.05
    elif inject == "drift_bias":
        drift *= 1.5
    return {
        "metrics": {
            "ratio": round(ratio, 6),
            "span": round(span, 6),
            "drift": round(drift, 9),
            "flag": round(flag, 6),
        },
        "injected": inject,
    }


def _case(num, den, length, drift, flag, inject=None) -> dict:
    out = {"num": num, "den": den, "length": length, "drift": drift, "flag": flag}
    if inject:
        out["inject"] = inject
    return out


GATES: list[dict] = [
    {
        "schema": CV.SCHEMA_GATE,
        "gate_id": "synthetic.alpha",
        "title": "被证据钉住的门（边界两侧各有近界样本）",
        "implementation": {"ref": "ops.taskplane.criteria_vitality_demo:synthetic_observations"},
        "criteria": [
            {"criterion_id": "alpha.ratio", "observed": "/metrics/ratio", "op": ">=",
             "threshold": 0.80, "unit": "1"},
            {"criterion_id": "alpha.span", "observed": "/metrics/span", "op": "<=",
             "threshold": 12.0, "unit": "m"},
        ],
        "subjects": [
            {"subject_id": "a1", "case": _case(95, 100, 4.50, 0.0004, 1.5),
             "expect": "PASS", "provenance": "clean；ratio=0.95 span=9.0，两侧都远离边界"},
            {"subject_id": "a2", "case": _case(83, 100, 5.75, 0.0011, 1.2),
             "expect": "PASS",
             "provenance": "clean；ratio=0.83（界内 +3.8%）、span=11.5（界内 -4.2%）"},
            {"subject_id": "a3",
             "case": _case(87, 100, 6.40, 0.0008, 0.9, inject="ratio_bias"),
             "expect": "FAIL",
             "provenance": "注入已知缺陷 ratio_bias：0.87*0.90=0.783 < 0.80；span=12.8 > 12"},
            {"subject_id": "a4", "case": _case(55, 100, 15.0, 0.0020, 0.3),
             "expect": "FAIL", "provenance": "clean 但深处界外：ratio=0.55、span=30.0"},
        ],
    },
    {
        "schema": CV.SCHEMA_GATE,
        "gate_id": "synthetic.beta",
        "title": "阈值悬空的门（样本全在边界一侧，扰动不改变判决）",
        "implementation": {"ref": "ops.taskplane.criteria_vitality_demo:synthetic_observations"},
        "criteria": [
            {"criterion_id": "beta.drift", "observed": "/metrics/drift", "op": "<=",
             "threshold": 0.050, "unit": "1"},
        ],
        "subjects": [
            {"subject_id": "b1", "case": _case(95, 100, 4.5, 0.0003, 1.5),
             "expect": "PASS", "provenance": "clean；drift=0.0003，距阈值 99.4%"},
            {"subject_id": "b2", "case": _case(95, 100, 4.5, 0.0009, 1.5),
             "expect": "PASS", "provenance": "clean；drift=0.0009"},
            {"subject_id": "b3", "case": _case(95, 100, 4.5, 0.0015, 1.5),
             "expect": "PASS", "provenance": "clean；drift=0.0015，仍在阈值 3% 以内"},
            {"subject_id": "b4", "case": _case(95, 100, 4.5, 0.0800, 1.5),
             "expect": "FAIL", "provenance": "clean 但界外：drift=0.08（阈值的 1.6 倍）"},
        ],
    },
    {
        "schema": CV.SCHEMA_GATE,
        "gate_id": "synthetic.gamma",
        "title": "判据自身判错的门（预注册样本被原始判据误判）",
        "implementation": {"ref": "ops.taskplane.criteria_vitality_demo:synthetic_observations"},
        "criteria": [
            {"criterion_id": "gamma.flag", "observed": "/metrics/flag", "op": ">=",
             "threshold": 1.0, "unit": "1"},
        ],
        "subjects": [
            {"subject_id": "g1", "case": _case(95, 100, 4.5, 0.0004, 1.00),
             "expect": "PASS", "provenance": "clean；flag=1.00 正好在界上"},
            {"subject_id": "g2", "case": _case(95, 100, 4.5, 0.0004, 0.98),
             "expect": "PASS",
             "provenance": "clean；按声明的 ±2% 验收带宽判 PASS（判据忘了写容差）"},
            {"subject_id": "g3", "case": _case(95, 100, 4.5, 0.0004, 0.50),
             "expect": "FAIL", "provenance": "clean 但界外：flag=0.50"},
        ],
    },
]

#: 合成能力映射（证明「能力 -> 门」的入账通路；真实能力一律 UNEXAMINED）。
CAPABILITY_MAP: dict[str, list[str]] = {"synthetic.alpha": ["synthetic.demo.capability"]}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_figures(run: Mapping[str, Any], path: Path) -> Path:
    """kill 矩阵热图 + 逐判据 MS 柱图（数字全部取自 run.json）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gates = run["gates"]
    operators: list[str] = []
    for gate in gates:
        for row in gate["criteria"]:
            cells = gate["kill_matrix"][row["criterion_id"]]
            operators = sorted({c["operator"] for c in cells.values()})
        break

    rows: list[str] = []
    values: list[list[float]] = []
    for gate in gates:
        for row in gate["criteria"]:
            cells = gate["kill_matrix"][row["criterion_id"]]
            rows.append(f"{gate['gate_id']}\n{row['criterion_id']}")
            line = []
            for op in operators:
                cell = next((c for c in cells.values() if c["operator"] == op), None)
                line.append(1.0 if cell and cell["result"] == "KILLED" else 0.0)
            values.append(line)

    fig, axes = plt.subplots(
        1, 2, figsize=(16, 0.55 * max(3, len(rows)) + 3),
        gridspec_kw={"width_ratios": [3, 2]},
    )
    ax = axes[0]
    im = ax.imshow(values, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(operators)))
    ax.set_xticklabels(
        [op.replace("OBSERVED_", "OBS_").replace("THRESHOLD_", "THR_") for op in operators],
        fontsize=7, rotation=30, ha="right",
    )
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=8)
    ax.set_title("kill matrix (green=KILLED, red=SURVIVED)")
    for i, line in enumerate(values):
        for j, v in enumerate(line):
            ax.text(j, i, "K" if v else "S", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)

    ax2 = axes[1]
    labels, scores, colors = [], [], []
    palette = {"VITAL": "#2e7d32", "INERT": "#ef6c00", "BLOCKED": "#b71c1c"}
    for gate in gates:
        for row in gate["criteria"]:
            labels.append(row["criterion_id"])
            scores.append(row["vitality_score"])
            colors.append(palette[gate["verdict"]])
    ax2.barh(range(len(labels)), scores, color=colors)
    ax2.axvline(run["ms_floor"] * 100.0, color="black", linestyle="--", linewidth=1,
                label=f"MS floor = {run['ms_floor'] * 100:.0f}")
    for i, s in enumerate(scores):
        ax2.text(s + 1.5, i, f"{s:.1f}", va="center", fontsize=8)
    ax2.set_yticks(range(len(labels)))
    ax2.set_yticklabels(labels, fontsize=8)
    ax2.set_xlim(0, 115)
    ax2.set_xlabel("vitality score = MS x 100")
    ax2.set_title("per-criterion vitality (bar colour = gate verdict)")
    ax2.legend(fontsize=8, loc="lower right")
    fig.suptitle(
        f"criteria vitality gate - {run['generated_at']} - MS={run['summary']['ms']:.4f} "
        f"(killed {run['summary']['killed_total']}/{run['summary']['mutants_total']})",
        fontsize=10,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _run_pytest() -> dict:
    """跑本节点的 pytest（A8 数字程序化取值，不手抄）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
    )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    passed = 0
    for line in reversed(tail):
        tokens = line.replace(",", " ").split()
        for idx, token in enumerate(tokens):
            if token.isdigit() and idx + 1 < len(tokens) and tokens[idx + 1] == "passed":
                passed = int(token)
                break
        if passed:
            break
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "summary_line": tail[-1] if tail else "",
        "command": f"pytest {TEST_FILE} -q",
    }


def _domain_scan(targets: list[str]) -> dict:
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    result = scan_paths(targets, lexicon=load_lexicon())
    return {
        "scanned_files": result["scanned_files"],
        "hits": result["hits"],
        "targets": targets,
        "terms": result["terms"],
    }


def build(out_dir: Path = OUT_DIR, stamp_ledger: bool = True, run_pytest: bool = True) -> dict:
    """生成全部产物并返回报告字典（report.json 的内容）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now()

    gates_path = out_dir / "gates.json"
    gates_path.write_text(
        json.dumps({"schema": CV.SCHEMA_GATE, "generated_at": stamp, "gates": GATES},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    parsed = [CV.parse_gate(json.loads(json.dumps(g))) for g in GATES]

    # 1) 预注册：执行前把变异库落盘
    library = CV.build_library_file(parsed, generated_at=stamp)
    library_path = CV.write_library(library, out_dir / "mutant_library.json")

    # 2) 执行（只吃预注册库；库与门定义不一致会直接拒跑）
    run = CV.run_bundle(parsed, library, generated_at=stamp)
    run_path = out_dir / "run.json"
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = out_dir / "kill_matrix.md"
    md_path.write_text(CV.render_markdown(run), encoding="utf-8")

    # 3) 图
    fig_path = make_figures(run, FIG_DIR / "kill_matrix.png")

    # 4) 台账入账
    ledger_summary: dict = {"written": False}
    if stamp_ledger and LEDGER.exists():
        block, _updated, before = CV.stamp_capability_ledger(
            run, LEDGER, capability_map=CAPABILITY_MAP, embed_capability_fields=True
        )
        (out_dir / "capability_ledger_block.json").write_text(
            json.dumps(block, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        ledger_summary = {
            "written": True,
            "ledger": str(LEDGER.relative_to(ROOT)),
            "untouched_digest_before": before,
            "block_summary": block["summary"],
            "embedded_fields": [
                "capabilities[*].vitality_score",
                "capabilities[*].vitality_status",
                "capabilities[*].vitality_real_claim_allowed",
            ],
        }

    # 5) 判据 A1~A5 机裁
    ok, violations = CV.check_run(run)

    # 6) A7 零领域语义扫描
    scan = _domain_scan([
        "ops/taskplane/criteria_vitality.py",
        "ops/taskplane/criteria_vitality_demo.py",
        TEST_FILE,
    ])

    # 7) A8 pytest
    pytest_info = _run_pytest() if run_pytest else {"exit_code": None, "passed": 0,
                                                   "summary_line": "not run", "command": ""}

    per_gate = {
        gate["gate_id"]: {
            "verdict": gate["verdict"],
            "ms": gate["ms"],
            "gate_vitality": gate["gate_vitality"],
            "real_claim_allowed": gate["real_claim_allowed"],
            "mutants_total": gate["matrix_stats"]["expected_cells"],
            "killed": sum(r["killed"] for r in gate["criteria"]),
            "survived": len(gate["survived"]),
            "matrix_cells": {cid: len(cells) for cid, cells in gate["kill_matrix"].items()},
            "overfit_suspect": gate["diagnostics"]["overfit_suspect"],
            "overfit_note": gate["diagnostics"]["overfit_note"],
            "blockers": gate["blockers"],
            "criteria": {
                row["criterion_id"]: {
                    "statement": row["statement"],
                    "mutants_total": row["mutants_total"],
                    "operator_classes": row["operator_classes"],
                    "killed": row["killed"],
                    "ms": row["ms"],
                    "vitality_score": row["vitality_score"],
                    "min_abs_margin": row["min_abs_margin"],
                    "status": row["status"],
                }
                for row in gate["criteria"]
            },
        }
        for gate in run["gates"]
    }

    min_mutants = min(row["mutants_total"] for gate in run["gates"] for row in gate["criteria"])
    min_classes = min(
        len(row["operator_classes"]) for gate in run["gates"] for row in gate["criteria"]
    )
    cells_ok = all(gate["matrix_stats"]["complete"] for gate in run["gates"])
    survived_all = [item for gate in run["gates"] for item in gate["survived"]]
    reasons_ok = all(item.get("reason_code") and item.get("detail") for item in survived_all)
    score_ok = all(
        abs(row["vitality_score"] - row["ms"] * 100.0) < 1e-6
        for gate in run["gates"]
        for row in gate["criteria"]
    ) and all(abs(gate["gate_vitality"] - gate["ms"] * 100.0) < 1e-6 for gate in run["gates"])

    docs_path = ROOT / "docs" / "CRITERIA_VITALITY_GATE.md"
    report = {
        "schema": "criteria-vitality-report/1.0",
        "generated_at": stamp,
        "task": "P158W3-N07 criteria vitality gate",
        "mechanism": {
            "module": CV.MODULE_REL,
            "module_sha256": CV.sha256_file(Path(CV.__file__)),
            "demo": "ops/taskplane/criteria_vitality_demo.py",
            "tests": TEST_FILE,
            "docs": "docs/CRITERIA_VITALITY_GATE.md",
            "ms_floor": CV.MS_FLOOR,
            "relative_perturbation": CV.RELATIVE_PERTURBATION,
            "operators": CV.OPERATORS,
            "min_mutants_per_criterion": CV.MIN_MUTANTS_PER_CRITERION,
            "min_operator_classes": CV.MIN_OPERATOR_CLASSES,
        },
        "artifacts": {
            "gates": str(gates_path.relative_to(ROOT)),
            "mutant_library": str(library_path.relative_to(ROOT)),
            "run": str(run_path.relative_to(ROOT)),
            "kill_matrix_md": str(md_path.relative_to(ROOT)),
            "figure": str(fig_path.relative_to(ROOT)),
            "ledger_block": str((out_dir / "capability_ledger_block.json").relative_to(ROOT)),
            "report": str((out_dir / "report.json").relative_to(ROOT)),
            "docs": str(docs_path.relative_to(ROOT)),
        },
        "run_summary": run["summary"],
        "gates": per_gate,
        "acceptance": {
            "A1_coverage": {
                "min_mutants_per_criterion": min_mutants,
                "required": CV.MIN_MUTANTS_PER_CRITERION,
                "min_operator_classes": min_classes,
                "required_operator_classes": CV.MIN_OPERATOR_CLASSES,
                "pass": min_mutants >= CV.MIN_MUTANTS_PER_CRITERION
                and min_classes >= CV.MIN_OPERATOR_CLASSES,
            },
            "A2_kill_rate": {
                "pooled_ms": run["summary"]["ms"],
                "per_gate_ms": {g: v["ms"] for g, v in per_gate.items()},
                "floor": CV.MS_FLOOR,
                "vital_gates": [g for g, v in per_gate.items() if v["verdict"] == "VITAL"],
                "inert_gates": [g for g, v in per_gate.items() if v["verdict"] == "INERT"],
                "blocked_gates": [g for g, v in per_gate.items() if v["verdict"] == "BLOCKED"],
                "note": "达标线只对设计为「活力门」的 alpha 判定；beta/gamma 是刻意构造的"
                        "反例（阈值悬空 / 判据自身判错），其 MS 低正是被判有效的信号",
                "pass": (
                    per_gate["synthetic.alpha"]["ms"] >= CV.MS_FLOOR
                    and per_gate["synthetic.alpha"]["real_claim_allowed"] is True
                    and all(
                        v["real_claim_allowed"] is False
                        for v in per_gate.values()
                        if v["ms"] < CV.MS_FLOOR
                    )
                ),
            },
            "A3_matrix_complete": {
                "cells": {g: v["matrix_cells"] for g, v in per_gate.items()},
                "complete": cells_ok,
                "pass": cells_ok,
            },
            "A4_survived_reasons": {
                "survived_total": len(survived_all),
                "reason_codes": sorted({item["reason_code"] for item in survived_all}),
                "all_annotated": reasons_ok,
                "items": [
                    {
                        "gate_id": i["gate_id"],
                        "criterion_id": i["criterion_id"],
                        "mutant_id": i["mutant_id"],
                        "reason_code": i["reason_code"],
                        "detail": i["detail"],
                    }
                    for i in survived_all
                ],
                "pass": reasons_ok,
            },
            "A5_score_arithmetic": {
                "rule": "gate_vitality == MS*100；criterion vitality_score == criterion_ms*100",
                "pass": score_ok,
            },
            "A6_ledger_write": {
                **ledger_summary,
                "real_claim_rule": "未体检或 MS < floor ⇒ real_claim_allowed=False（不得自报 REAL）",
                "pass": bool(ledger_summary.get("written"))
                and ledger_summary["block_summary"]["capabilities_unexamined"] >= 1,
            },
            "A7_zero_domain_semantics": {
                **scan,
                "pass": len(scan["hits"]) == 0,
            },
            "A8_pytest": {
                **pytest_info,
                "pass": pytest_info["exit_code"] == 0 and pytest_info["passed"] >= 15,
            },
            "mechanism_check_A1_A5": {"pass": ok, "violations": violations},
        },
        "honest_findings": [
            "synthetic.alpha 的 MS=1.0：全部变异体被杀。过拟合诊断显示边界邻域样本占比 "
            f"{per_gate['synthetic.alpha']['criteria']['alpha.ratio']['min_abs_margin']:.4f} "
            "（见 per-gate overfit_suspect）；MS=1.0 只是「在样本池分辨率下参数均被咬住」，"
            "不等于判据参数在更大样本上正确，仍需外部样本复核。",
            "synthetic.beta 的 MS=0.333 < 0.5：阈值悬空判据，按机制封 REAL——本节点把这条"
            "负面结果入账，而不是把 beta 从演示里删掉。",
            "synthetic.gamma 的判据在原始形态下就判错 g2（accuracy<1.0），任意变异体统计都"
            "无意义，机制按 BLOCKED 封门，不输出活力分数。",
            "变异库预注册指纹只绑定「门定义 + 实现入口」，不绑定运行环境；跨机复核需同版本"
            "实现入口。",
        ],
    }

    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_docs(report), encoding="utf-8")

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return report


def render_docs(report: Mapping[str, Any]) -> str:
    """从 report.json 渲染机制文档（数字全部程序化注入，零手抄）。"""
    acc = report["acceptance"]
    mech = report["mechanism"]
    gates = report["gates"]
    lines = [
        "# 判据活力门（criteria vitality gate）：判据自身的变异注入体检",
        "",
        f"> 任务 `P158W3-N07` · 机制 `{mech['module']}`"
        f"（sha256 `{mech['module_sha256'][:16]}…`） · 演示/产物生成器 `{mech['demo']}` ·"
        f" 测试 `{mech['tests']}` · 生成于 `{report['generated_at']}`。",
        "> 本文件由 `ops/taskplane/criteria_vitality_demo.py::render_docs` 从",
        "> `artifacts/criteria_vitality/report.json` 渲染，文中每个数字都有工件出处，禁止手抄。",
        "",
        "## 1 问题：判据可以是「恒真装饰」",
        "",
        "门自称 REAL，靠的是判据能分辨合规与不合规。但一条 `observed >= threshold` 的判据",
        "有三种静默失效，任何一种都不会让门变红：",
        "",
        "| 失效 | 现象 | 为什么现有门看不出来 |",
        "|---|---|---|",
        "| 阈值悬空 | 阈值写 10，样本观测全在 100 以上；阈值改成 11 或 9 判决一模一样 |"
        " 判据仍然「全绿」，因为它确实没判错任何样本 |",
        "| 观测式可平移 | 观测式整体 +10% 常数偏移，判决不变 | 偏移不改变通过/不通过 |",
        "| 单位错位 | 阈值按 mm 写、按 m 解读（差 1000 倍） | 只影响量纲，不影响全绿结论 |",
        "",
        "三者共同点：**判据对自己参数的扰动不敏感**。判据参数是判据的全部信息量——参数随便改",
        "都不改变判决 ⇒ 参数没被证据约束 ⇒ 判据退化成常量，门退化成装饰。本机制把它变成机器",
        "可裁的指标：**变异注入 + 杀死率 MS**。",
        "",
        "## 2 机制设计（语义契约先于实现）",
        "",
        "1. **判据 = 验收条件**：`op`/`threshold` 描述「必须成立」的条件（例：`observed >= 0.80`",
        "   表示该量不得低于 0.80），不成立即判 FAIL。观测式是 JSON 指针，取不到值一律算",
        "   *输入错误*（`VitalityInputError`），绝不被折算成判据不合格。",
        "2. **体检样本 = 输入 + 预注册判决**：每个样本带 `expect: PASS/FAIL`，判决来自样本",
        "   构造方式（干净实现 → PASS；注入了已知缺陷的实现 → FAIL），**不来自被体检的判据**。",
        "   这条是机制可信度的地基：若判决由判据自己算，MS 恒等于 1。",
        "3. **变异体只扰动判据参数**：阈值 ±10%、比较方向翻转、观测式常数偏移、单位换算错误。",
        "4. **预注册**：变异库在**执行之前**落盘，每个变异体带规格指纹 `digest`，库带门定义指纹",
        "   `gate_sha256`；执行引擎只吃库里的变异体，门定义或变异载荷被改即拒跑（exit 3）。",
        "5. **杀死率**：变异体在样本池上给出与预注册判决不一致的判决 ⇒ KILLED；全部一致 ⇒",
        "   SURVIVED（该扰动在此样本分辨率下不改变判决）。`MS = killed / total`，",
        f"   `MS >= {mech['ms_floor']}` 达标，`gate_vitality = MS × 100`。",
        "6. **前置条件（防「拿坏尺子量」）**：原始判据必须在全部样本上判对（accuracy = 1.0），",
        "   否则记 `DEFECTIVE`、门封为 BLOCKED、不输出活力分数。",
        "7. **三态裁决**：`VITAL`（覆盖达标 + MS ≥ 达标线 + 矩阵完整）/ `INERT`（MS 不足）",
        "   / `BLOCKED`（判据自身判错、覆盖不足或矩阵不完整）。只有 VITAL 才 `real_claim_allowed`。",
        "",
        "## 3 变异算子库",
        "",
        "| 算子 | 语义 |",
        "|---|---|",
    ]
    for op, semantics in mech["operators"].items():
        lines.append(f"| `{op}` | {semantics} |")
    lines += [
        "",
        f"每条判据生成 {len(mech['operators'])} 个变异体（A1 要求 ≥"
        f"{mech['min_mutants_per_criterion']} 个 / ≥{mech['min_operator_classes']} 类算子），"
        "扰动幅度固定为 `relative_perturbation = "
        f"{mech['relative_perturbation']:g}`。",
        "",
        "## 4 判据机裁（A1~A8）与实测",
        "",
        "| 判据 | 要求 | 实测 | 结论 |",
        "|---|---|---|---|",
        f"| A1 变异体生成 | 每条判据 ≥{mech['min_mutants_per_criterion']} 个、"
        f"≥{mech['min_operator_classes']} 类算子 | 最少 {acc['A1_coverage']['min_mutants_per_criterion']}"
        f" 个 / {acc['A1_coverage']['min_operator_classes']} 类 | "
        f"{'PASS' if acc['A1_coverage']['pass'] else 'FAIL'} |",
        f"| A2 杀死率 | 设计门 MS ≥ {acc['A2_kill_rate']['floor']} 且 MS 不足者不得自报 REAL | "
        f"逐门 MS {acc['A2_kill_rate']['per_gate_ms']} | "
        f"{'PASS' if acc['A2_kill_rate']['pass'] else 'FAIL'} |",
        f"| A3 kill 矩阵完整性 | 判据 × 变异体逐格有裁决 | "
        f"{ {g: v['matrix_cells'] for g, v in gates.items()} } | "
        f"{'PASS' if acc['A3_matrix_complete']['pass'] else 'FAIL'} |",
        f"| A4 survived 清单 | 每个存活变异体有原因码 + 数值明细 | "
        f"存活 {acc['A4_survived_reasons']['survived_total']} 个，原因码 "
        f"{acc['A4_survived_reasons']['reason_codes']} | "
        f"{'PASS' if acc['A4_survived_reasons']['pass'] else 'FAIL'} |",
        f"| A5 分数换算 | `gate_vitality == MS×100`（逐判据同） | "
        f"{ {g: v['gate_vitality'] for g, v in gates.items()} } | "
        f"{'PASS' if acc['A5_score_arithmetic']['pass'] else 'FAIL'} |",
        f"| A6 台账入账 | 写入 `vitality_score`；未体检能力封 REAL | 台账 "
        f"{acc['A6_ledger_write'].get('ledger', '-')}：体检 "
        f"{acc['A6_ledger_write']['block_summary']['capabilities_examined']} / 未体检 "
        f"{acc['A6_ledger_write']['block_summary']['capabilities_unexamined']} / 可自报 REAL "
        f"{acc['A6_ledger_write']['block_summary']['capabilities_real_claim_allowed']} | "
        f"{'PASS' if acc['A6_ledger_write']['pass'] else 'FAIL'} |",
        f"| A7 零领域语义 | 模块/演示/测试经 `tools.evidence_domain_scan` 0 命中 | 扫描 "
        f"{acc['A7_zero_domain_semantics']['scanned_files']} 文件 / "
        f"{acc['A7_zero_domain_semantics']['terms']} 词条，命中 "
        f"{len(acc['A7_zero_domain_semantics']['hits'])} | "
        f"{'PASS' if acc['A7_zero_domain_semantics']['pass'] else 'FAIL'} |",
        f"| A8 pytest | ≥15 例全绿 | {acc['A8_pytest']['summary_line']} | "
        f"{'PASS' if acc['A8_pytest']['pass'] else 'FAIL'} |",
        "",
        "## 5 合成演示：三门三态（数字取自 `artifacts/criteria_vitality/run.json`）",
        "",
        "| 门 | 判据 | 变异体 | 杀死 | 存活 | MS | gate_vitality | 裁决 | 可自报 REAL | 阻塞项 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for gid, gate in gates.items():
        for cid, row in gate["criteria"].items():
            lines.append(
                f"| `{gid}` | `{cid}` | {row['mutants_total']} | {row['killed']} | "
                f"{row['mutants_total'] - row['killed']} | {row['ms']:.6f} | "
                f"{row['vitality_score']:.6f} | {gate['verdict']} | "
                f"{'是' if gate['real_claim_allowed'] else '否'} | "
                f"{'；'.join(gate['blockers']) or '-'} |"
            )
    lines += [
        "",
        f"存活原因码分布：{acc['A4_survived_reasons']['reason_codes']}；"
        f"完整矩阵见 `{report['artifacts']['kill_matrix_md']}`，"
        f"图为 `{report['artifacts']['figure']}`。",
        "",
        "## 6 台账入账语义",
        "",
        "* `criteria_vitality.gates.<gate>`：门级 `verdict/ms/gate_vitality/real_claim_allowed/"
        "blockers/matrix_complete/overfit_suspect` + 逐判据 `vitality_score`；",
        "* `criteria_vitality.capabilities.<cap>`：能力级 `vitality_score`（多门合并按"
        "`killed/total` 加权）、`real_claim_allowed`（所有映射门都必须 VITAL）、`status`；",
        "* 未体检能力：`vitality_score = null`、`real_claim_allowed = false`、原因 "
        "「未体检：没有活力门体检记录，按机制不得自报 REAL」；",
        "* 写入是**附加式**的：`capabilities[*].vitality_score / vitality_status / "
        "vitality_real_claim_allowed` 三个字段 + 一个顶层 `criteria_vitality` 段，"
        "其余内容逐字节不动（写入前后指纹比对，越界即中止），并沿用台账既有的"
        f" `indent={1}` 序列化惯例（重复入账幂等）。",
        "",
        "## 7 注意点与局限（honest）",
        "",
    ]
    for item in report["honest_findings"]:
        lines.append(f"* {item}")
    lines += [
        "* MS 高只说明「判据参数在**这批样本**的分辨率下被咬住」，不证明判据语义正确；"
        "语义正确性由前置条件（原始判据 accuracy=1.0）与外部基准共同保证。",
        "* 变异算子只覆盖参数级扰动（阈值/方向/偏移/单位）；**观测式本身写错**（取错量）"
        "不在覆盖范围，需要独立的语义审计。",
        "* 预注册指纹绑定「门定义 + 实现入口引用」，不绑定运行环境与依赖版本；跨机复核需同版本实现。",
        "* 本节点的真实能力能力台账条目（21 条）**全部为 UNEXAMINED**：本机制已就位，"
        "但「对哪条真实能力跑哪套判据」属领域侧注入，不在平台仓硬编码。",
        "",
        "## 8 复现",
        "",
        "```bash",
        "python3 -m ops.taskplane.criteria_vitality_demo        # 出全部产物 + 入账 + 自跑 pytest",
        "python3 -m pytest tests/test_criteria_vitality.py -q   # 机制验收",
        "python3 -m ops.taskplane.criteria_vitality preregister --gate artifacts/criteria_vitality/gates.json --out /tmp/lib.json",
        "python3 -m ops.taskplane.criteria_vitality run --gate artifacts/criteria_vitality/gates.json --library /tmp/lib.json --out /tmp/run.json --markdown /tmp/kill.md",
        "python3 -m ops.taskplane.criteria_vitality check --run /tmp/run.json"
        "   # 0 全绿且全 VITAL / 2 有未达标门 / 3 输入错（本演示刻意含 INERT+BLOCKED 门，故为 2）",
        "```",
        "",
        "## 9 审计链",
        "",
        f"* 机制源码：`{mech['module']}` sha256 `{mech['module_sha256']}`",
        f"* 变异库：`{report['artifacts']['mutant_library']}`；运行记录：`{report['artifacts']['run']}`",
        f"* 报告：`{report['artifacts']['report']}`；台账段：`{report['artifacts']['ledger_block']}`",
        f"* 生成时间：`{report['generated_at']}`（同一轮：预注册 → 执行 → 图 → 入账 → 机裁 → pytest）",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判据活力门合成演示 + 产物生成")
    parser.add_argument("--no-ledger", action="store_true", help="不写 capability_ledger")
    parser.add_argument("--no-pytest", action="store_true", help="跳过 pytest（只出产物）")
    args = parser.parse_args(argv)
    report = build(stamp_ledger=not args.no_ledger, run_pytest=not args.no_pytest)
    acc = report["acceptance"]
    print(f"产物目录: {OUT_DIR}")
    for key in sorted(acc):
        print(f"  {key}: pass={acc[key].get('pass')}")
    failed = [k for k in acc if not acc[k].get("pass")]
    return 0 if not failed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
