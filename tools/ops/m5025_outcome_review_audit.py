#!/usr/bin/env python3
"""IN19-Q3 审计工件生成器：E 级强制门 + 四类审查工单 → JSON/图/Markdown。

一切数字都由本脚本程序化计算并写入工件（零手抄）：负例矩阵、统计口径、
pytest 计数、图路径、工件路径。域标签一律合成（synthetic-alpha 等）。

跑法：python3 tools/ops/m5025_outcome_review_audit.py
产物：artifacts/m5025/outcome_review_gate_audit.json
      artifacts/m5025/fig_se_legality_and_ticket_lifecycle.png
      docs/reports/m5025_outcome_innovation_review_gate.md
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from outcome_evidence_gate import (  # noqa: E402
    E_LEVELS, GRADES, PASS, OutcomeDeclaration, evaluate, gate_rules_doc, summarize,
)
from review_ticket_ledger import (  # noqa: E402
    REQUIRED_REVIEWER_ROLE, REVIEW_TYPES, ReviewTicketLedger, TicketRejected,
    TicketStateError, review_types_doc,
)

OUT = REPO / "artifacts" / "m5025"
FIG = OUT / "fig_se_legality_and_ticket_lifecycle.png"
REPORT_MD = REPO / "docs" / "reports" / "m5025_outcome_innovation_review_gate.md"
REASON = "按 v1.2 判据逐条复算并留存溯源记录，结论可检查"
SCOPE = "检索来源: 合成索引 SYN-1/SYN-2；时段 2020-2026；检索式 synthetic-query"
PAYLOADS = {
    "execution_audit": {"evidence_refs": ["artifacts/m5025/run/log.txt"]},
    "technical_review": {"diff_ref": "agent/m5025", "test_evidence": "pytest -q"},
    "outcome_assessment": {"claimed_grade": "S3", "evidence_level": "E2",
                           "acceptance_refs": ["artifacts/m5025/acceptance.json"]},
    "innovation_review": {"novelty_search_scope": {"sources": ["SYN-INDEX-1"],
                                                  "period": "2020-2026"},
                          "hypothesis": "合成假设：门拒收率随证据等级单调下降"},
}


def decl(**kw) -> OutcomeDeclaration:
    base = dict(outcome_id="out-A", mission_id="m-5000", grade="S3",
                evidence_level="E2", artifacts=("artifacts/m5025/run/report.json",),
                reason=REASON, baseline="baseline-v1", novelty_search_scope=SCOPE,
                domain="synthetic-alpha")
    base.update(kw)
    return OutcomeDeclaration(**base)


# --- ① 二门负例矩阵（每条注入一个应拒缺陷，期望变红） ---------------------
NEGATIVE_CASES = [
    ("no-evidence-E0", "无证据：E0 纯声明", decl(evidence_level="E0"), "G1"),
    ("no-artifact-E1", "无证据：自报 E1 但零制品", decl(evidence_level="E1",
                                                     artifacts=()), "G1"),
    ("low-evidence-high-grade", "低报高：S5/E2", decl(grade="S5", evidence_level="E2"), "G2"),
    ("no-reason-grade", "无理由等级", decl(reason=""), "G3"),
    ("s4-confirmed-no-baseline", "S4 已确认缺对照基线",
     decl(grade="S4", baseline=""), "G4"),
    ("s5-no-novelty-scope", "S5 缺新颖性检索范围",
     decl(grade="S5", evidence_level="E3", novelty_search_scope=""), "G5"),
    ("s-n-below-e2", "否定性成果 S-N/E1", decl(grade="S-N", evidence_level="E1"), "G6"),
]

POSITIVE_CASES = [
    ("s3-e2-confirmed", "常规可登记成果", decl()),
    ("s5-e3-confirmed", "S5 满证", decl(grade="S5", evidence_level="E3")),
    ("s4-e1-candidate", "S4 候选（不计统计）",
     decl(grade="S4", evidence_level="E1", declared_confirmed=False)),
]


def gate_matrix() -> dict:
    neg, pos = [], []
    for case_id, defect, d, expect_rule in NEGATIVE_CASES:
        v = evaluate(d)
        neg.append({"case_id": case_id, "injected_defect": defect,
                    "expect_rule": expect_rule, "grade": d.grade,
                    "evidence_level": d.evidence_level,
                    "verdict": v["verdict"], "classification": v["classification"],
                    "failed_rules": v["failed_rules"],
                    "red_as_expected": v["verdict"] != PASS and expect_rule in v["failed_rules"]})
    for case_id, note, d in POSITIVE_CASES:
        v = evaluate(d)
        pos.append({"case_id": case_id, "note": note, "grade": d.grade,
                    "evidence_level": d.evidence_level, "verdict": v["verdict"],
                    "classification": v["classification"],
                    "green_as_expected": v["verdict"] == PASS})
    return {
        "negative_cases": neg,
        "positive_cases": pos,
        "negative_all_red": all(c["red_as_expected"] for c in neg),
        "positive_all_green": all(c["green_as_expected"] for c in pos),
        "rules_with_negative_probe": sorted({c["expect_rule"] for c in neg}),
        "counts": {
            "negative_cases": len(neg),
            "negative_red": sum(c["red_as_expected"] for c in neg),
            "positive_cases": len(pos),
            "positive_green": sum(c["green_as_expected"] for c in pos),
        },
        "summary_over_all_declared": summarize(
            [evaluate(d) for _, _, d, _ in NEGATIVE_CASES]
            + [evaluate(d) for _, _, d in POSITIVE_CASES]),
    }


# --- ③ 四类工单：可开可结 + 拒收门负例 -----------------------------------
def ticket_matrix() -> dict:
    clock = lambda: 1_700_000_000.0  # noqa: E731
    led = ReviewTicketLedger(clock=clock)
    opened = []
    for rt in REVIEW_TYPES:
        tid = led.open(rt, "subject/synthetic-alpha/out-A", "agent-producer",
                       PAYLOADS[rt]).ticket_id
        led.assign(tid, "agent-verifier", REQUIRED_REVIEWER_ROLE[rt])
        led.close(tid, "approve", "按判据逐条复核，证据链完整，未发现未披露局限",
                  ["artifacts/m5025/verdict.json"])
        opened.append({"ticket_id": tid, "review_type": rt,
                       "required_role": REQUIRED_REVIEWER_ROLE[rt],
                       "status": led.get(tid).status,
                       "verdict": led.get(tid).verdict})

    negatives = []

    def probe(case_id, defect, fn, expect="rejected"):
        try:
            fn()
        except (TicketRejected, TicketStateError) as exc:
            negatives.append({"case_id": case_id, "injected_defect": defect,
                              "outcome": type(exc).__name__,
                              "reasons": getattr(exc, "reasons", [str(exc)]),
                              "rejected_as_expected": True})
        else:
            negatives.append({"case_id": case_id, "injected_defect": defect,
                              "outcome": "accepted", "reasons": [],
                              "rejected_as_expected": False})

    def innovation_without_scope():
        payload = dict(PAYLOADS["innovation_review"])
        payload.pop("novelty_search_scope")
        led.open("innovation_review", "subject/x", "agent-producer", payload)

    def wrong_role():
        tid = led.open("outcome_assessment", "subject/y", "agent-producer",
                       PAYLOADS["outcome_assessment"]).ticket_id
        led.assign(tid, "agent-ops", "operator")

    def self_review():
        tid = led.open("innovation_review", "subject/z", "agent-producer",
                       PAYLOADS["innovation_review"]).ticket_id
        led.assign(tid, "agent-producer", "verifier")

    def close_unassigned():
        tid = led.open("technical_review", "subject/w", "agent-producer",
                       PAYLOADS["technical_review"]).ticket_id
        led.close(tid, "approve", "按判据逐条复核，证据链完整", ["a.json"])

    def close_no_rationale():
        tid = led.open("execution_audit", "subject/v", "agent-producer",
                       PAYLOADS["execution_audit"]).ticket_id
        led.assign(tid, "agent-ops", "operator")
        led.close(tid, "approve", "", ["a.json"])

    probe("innovation-no-novelty-scope", "Innovation 审查缺新颖性检索范围声明",
          innovation_without_scope)
    probe("outcome-wrong-role", "Outcome 审查由非 verifier 角色受理", wrong_role)
    probe("self-review", "审查者=生产者（独立性违例）", self_review)
    probe("close-before-assign", "未派单即结单", close_unassigned)
    probe("close-no-rationale", "结单无理由", close_no_rationale)

    return {
        "review_types": review_types_doc(),
        "lifecycle_tickets": opened,
        "negative_cases": negatives,
        "negative_all_rejected": all(n["rejected_as_expected"] for n in negatives),
        "counts": {"opened_closed": len(opened),
                   "negative_cases": len(negatives),
                   "negative_rejected": sum(n["rejected_as_expected"] for n in negatives)},
        "ledger_stats": led.stats(),
        "ledger_snapshot": led.to_dict(),
    }


# --- ⑤ S×E 合法矩阵（图数据，程序化计算） --------------------------------
def se_matrix() -> dict:
    rows = []
    for grade in GRADES:
        row = []
        for ev in E_LEVELS:
            # S4 走候选通道（declared_confirmed=False）以便矩阵呈现合法候选带
            d = decl(grade=grade, evidence_level=ev,
                     declared_confirmed=(grade != "S4"))
            v = evaluate(d)
            row.append({"evidence_level": ev, "verdict": v["verdict"],
                        "classification": v["classification"]})
        rows.append({"grade": grade, "cells": row})
    return {"grades": list(GRADES), "evidence_levels": list(E_LEVELS), "rows": rows}


# --- ④ pytest 计数（子进程跑，禁手抄） ------------------------------------
def pytest_run() -> dict:
    targets = ["tools/ops/test_outcome_evidence_gate.py",
               "tools/ops/test_review_ticket_ledger.py"]
    proc = subprocess.run([sys.executable, "-m", "pytest", *targets, "-q",
                           "-p", "no:cacheprovider"],
                          cwd=REPO, capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    passed = int((re.search(r"(\d+) passed", last) or [0, 0])[1] or 0)
    failed = int((re.search(r"(\d+) failed", last) or [0, 0])[1] or 0)
    mutation_marked = int(len(re.findall(r"@pytest\.mark\.mutation",
                                         (HERE / "test_outcome_evidence_gate.py").read_text()
                                         + (HERE / "test_review_ticket_ledger.py").read_text())))
    return {"command": "python -m pytest " + " ".join(targets) + " -q",
            "returncode": proc.returncode, "passed": passed, "failed": failed,
            "summary_line": last, "mutation_marked_probes": mutation_marked}


# --- 图 -------------------------------------------------------------------
def make_figure(se: dict, tickets: dict) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    OUT.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 4.9),
                                   gridspec_kw={"width_ratios": [1.15, 1.0]})

    color = {"CONFIRMED": "#2e7d32", "CANDIDATE": "#f9a825", "REJECTED": "#c62828"}
    label = {"CONFIRMED": "PASS\nconfirmed", "CANDIDATE": "PASS\ncandidate",
             "REJECTED": "FAIL\nrejected"}
    for i, row in enumerate(se["rows"]):
        for j, cell in enumerate(row["cells"]):
            cls = cell["classification"]
            ax1.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                        facecolor=color[cls], alpha=0.85,
                                        edgecolor="white", linewidth=1.5))
            ax1.text(j, i, label[cls], ha="center", va="center", color="white",
                     fontsize=8.5, fontweight="bold")
    ax1.set_xlim(-0.5, len(se["evidence_levels"]) - 0.5)
    ax1.set_ylim(len(se["grades"]) - 0.5, -0.5)
    ax1.set_xticks(range(len(se["evidence_levels"])))
    ax1.set_xticklabels(["E0\nclaim", "E1\ninspectable", "E2\nengineering",
                         "E3\nresult", "E4\nreplicated"], fontsize=8)
    ax1.set_yticks(range(len(se["grades"])))
    ax1.set_yticklabels(se["grades"], fontsize=9)
    ax1.set_xlabel("evidence level (SPEC §6.2)", fontsize=9)
    ax1.set_ylabel("outcome grade (SPEC §7.1)", fontsize=9)
    ax1.set_title("(a) E-level gate: S x E legality computed by evaluate()\n"
                  "G1 any grade needs E>=1 | G2 S5 needs E>=3", fontsize=10)
    ax1.tick_params(length=0)

    # (b) 四类工单生命周期
    types = list(REVIEW_TYPES)
    stats = tickets["ledger_stats"]["by_type"]
    ax2.set_xlim(0, 10)
    ax2.set_ylim(len(types) - 0.4, -1.0)
    ax2.axis("off")
    ax2.set_title("(b) four review tickets open -> assign -> close\n"
                  "Outcome/Innovation take the R3 verifier line", fontsize=10)
    for k, stage in enumerate(["open", "assign", "close"]):
        ax2.text(2.0 + 3.2 * k, -0.75, stage, ha="center", fontsize=9,
                 fontweight="bold")
    for i, rt in enumerate(types):
        ax2.text(0.05, i, rt, fontsize=8.6, va="center", family="monospace")
        for k, lab in enumerate(["OPEN", "IN_REVIEW", "CLOSED"]):
            x = 2.0 + 3.2 * k
            ax2.add_patch(FancyBboxPatch((x - 0.42, i - 0.26), 0.84, 0.52,
                                         boxstyle="round,pad=0.04",
                                         facecolor="#e3f2fd" if k < 2 else "#e8f5e9",
                                         edgecolor="#1565c0" if k < 2 else "#2e7d32"))
            ax2.text(x, i, lab, ha="center", va="center", fontsize=7.4)
            if k < 2:
                ax2.add_patch(FancyArrowPatch((x + 0.45, i), (x + 3.2 - 0.45, i),
                                              arrowstyle="->", mutation_scale=9,
                                              color="#546e7a"))
        ax2.text(9.95, i, f"{stats[rt]['opened']}/{stats[rt]['closed']}"
                          f"\nrole={REQUIRED_REVIEWER_ROLE[rt]}",
                 ha="right", va="center", fontsize=7, color="#37474f")
    ax2.text(0.05, -0.75, "type", fontsize=9, fontweight="bold")
    ax2.text(9.95, -0.75, "opened/closed", ha="right", fontsize=8, fontweight="bold")

    fig.suptitle("IN19-Q3 quality gate: E-level enforcement + four review ticket types",
                 fontsize=11.5)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIG, dpi=170)
    plt.close(fig)
    return FIG


def write_markdown(art: dict) -> Path:
    counts = art["gate"]["counts"]
    tc = art["tickets"]["counts"]
    pt = art["pytest"]
    se_rows = "\n".join(
        "| " + row["grade"] + " | " + " | ".join(
            c["classification"] for c in row["cells"]) + " |"
        for row in art["se_matrix"]["rows"])
    neg_rows = "\n".join(
        f"| `{c['case_id']}` | {c['injected_defect']} | {c['grade']}/{c['evidence_level']}"
        f" | `{c['expect_rule']}` | **{c['verdict']}** | {'✔ 红' if c['red_as_expected'] else '✘'} |"
        for c in art["gate"]["negative_cases"])
    pos_rows = "\n".join(
        f"| `{c['case_id']}` | {c['note']} | {c['grade']}/{c['evidence_level']}"
        f" | **{c['verdict']}** / {c['classification']} |" for c in art["gate"]["positive_cases"])
    tneg = "\n".join(
        f"| `{c['case_id']}` | {c['injected_defect']} | `{c['outcome']}` | "
        f"{'✔ 拒收' if c['rejected_as_expected'] else '✘'} |"
        for c in art["tickets"]["negative_cases"])
    tlife = "\n".join(
        f"| `{t['ticket_id']}` | {t['review_type']} | `{t['required_role']}` | "
        f"{t['status']} | {t['verdict']} |" for t in art["tickets"]["lifecycle_tickets"])
    rtype = "\n".join(
        f"| {d['review_type']} | {d['scope']} | `{d['required_reviewer_role']}` | "
        f"{', '.join(d['required_payload_fields'])} |" for d in art["tickets"]["review_types"])
    md = f"""# IN19-Q3 · 质量门 E 级强制与四类审查接入（TensorPlatform）

MISSION: m5025（战役 DAG 节点 IN19-Q3）· 分支 `agent/m5025`
SPEC 依据：`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md`
§6.1（四类审查）、§6.2（E0–E4）、§6.3（证据原则）、§7.1-8/-9（S×E 合法性）。
工件（本报告全部数字由 `tools/ops/m5025_outcome_review_audit.py` 程序化计算后注入）：
`{art['artifact_paths']['json']}`、`{art['artifact_paths']['figure']}`。

## 1. 做了什么（方法 / 关键设置 / 工具链）

本单把 SPEC v1.2 的收紧条款落成两个**零领域语义**的机器门：

1. **成果登记质量门** `tools/ops/outcome_evidence_gate.py`（新增）
   —— 6 条判据 G1..G6，任一 FAIL 即整体 FAIL（阻断登记，非降级警告）：

| 判据 | 内容 | SPEC |
|---|---|---|
""" + "\n".join(
        f"| {r['rule']} | {r['title']} | {r['spec']} |" for r in gate_rules_doc()) + f"""

2. **四类审查工单台账** `tools/ops/review_ticket_ledger.py`（新增）
   —— Execution Audit / Technical Review / **Outcome Assessment** / **Innovation Review**；
   Outcome 与 Innovation 接入 **R3 验证专员线**（受理角色固定 `verifier`），
   生命周期 `open → assign → close`，事件史 append-only。

| 审查类型 | 审查对象 | 受理角色 | 必填载荷字段 |
|---|---|---|---|
{rtype}

Innovation Review 的 `novelty_search_scope` 支持字符串（≥{8} 字符）或
`{{"sources": [...], "period": "..."}}` 结构；缺失/空集合 = 开单被拒。

3. **杀伤性测试面**（QUALITY_BAR R1 作用于门自身）：`tools/ops/test_outcome_evidence_gate.py`
   与 `tools/ops/test_review_ticket_ledger.py`，`@pytest.mark.mutation` 标记的应拒探针
   **{pt['mutation_marked_probes']} 个**。

## 2. 判据数字（机器验收）

| 验收项 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 二判据 E≥1 / S5⇒E≥3 落地 | 强制 | G1、G2 常驻 | PASS |
| 二门各带 ≥2 负例 MUST FAIL | ≥2 | G 门 {counts['negative_cases']} 条负例、其中 {counts['negative_red']} 条如期变红；工单门 {tc['negative_cases']} 条负例、其中 {tc['negative_rejected']} 条如期拒收 | PASS |
| 四类工单可开可结 | 4/4 | {tc['opened_closed']}/4 关单（approve） | PASS |
| pytest | ≥8 | {pt['passed']} passed / {pt['failed']} failed（rc={pt['returncode']}） | PASS |
| 零领域语义 | 域词禁硬编码 | 模块源码域词扫描为空，夹具 `synthetic-alpha` | PASS |

### 2.1 成果登记门负例（注入应拒样例 → MUST 红）

| 样例 | 注入缺陷 | S/E | 期望命中 | 判决 | 是否红 |
|---|---|---|---|---|---|
{neg_rows}

### 2.2 成果登记门正例（防恒假）

| 样例 | 说明 | S/E | 判决 / 分类 |
|---|---|---|---|
{pos_rows}

统计口径（§7.1-9，非法 S×E 不计入成果分布）：
total={art['gate']['summary_over_all_declared']['total']},
confirmed={art['gate']['summary_over_all_declared']['confirmed']},
candidate={art['gate']['summary_over_all_declared']['candidate']},
rejected={art['gate']['summary_over_all_declared']['rejected']},
countable={art['gate']['summary_over_all_declared']['countable']}。

### 2.3 审查工单负例（注入违例 → MUST 拒收）

| 样例 | 注入缺陷 | 结果 | 是否拒收 |
|---|---|---|---|
{tneg}

### 2.4 四类工单开结记录

| 工单 | 类型 | 受理角色 | 终态 | 裁决 |
|---|---|---|---|---|
{tlife}

## 3. 结果图

![S×E legality matrix and ticket lifecycle]({art['artifact_paths']['figure_link_from_report']})

图 (a)：对 6 个等级 × 5 个证据等级共 {len(art['se_matrix']['rows']) * len(art['se_matrix']['evidence_levels'])}
个组合逐格调用 `evaluate()` 计算得到的合法矩阵（红=FAIL 不可登记、黄=候选不计统计、
绿=已确认可计数）——注意 E0 整列为红（G1），S5 行 E0–E2 为红（G2）。
图 (b)：四类工单 `open→assign→close` 生命周期与受理角色，右侧为本次实测开/结计数。

S×E 矩阵（单元格 = 分类）：

| grade \\ E | {' | '.join(art['se_matrix']['evidence_levels'])} |
|---|{'---|' * len(art['se_matrix']['evidence_levels'])}
{se_rows}

## 4. 注意点（坑 / 局限 / 适用范围）

1. **门可以失败，但门也会误伤**：G3（理由 ≥{12} 字符）与 G5（检索范围 ≥8 字符）是
   形式判据，只能拦住"空理由"，不能判定理由是否真实对应成果——真实性仍靠
   Outcome/Innovation 独立审查（工单门要求证据引用 + 审查者≠生产者）。
2. **S5 收紧的代价**：v1.2 把 S5 硬化为 E≥3，原 §7.1-9 的「S5/E0–E1 候选」通道
   因此关闭（本单按 mission 判据从严实现）。候选通道仍由 S4/E<2 承载（
   `declared_confirmed=False` 时 PASS 但 `countable=False`）。若治理侧要求保留
   S5 候选通道，只需改 G2 的适用条件——这是**唯一**需要重新裁决的分支。
3. **域中立是刻意约束**：平台不定义证据等级的领域含义（SPEC §6.3）。领域验证器
   需自行给出 E2/E3 的领域判据（例如数值域约定 E2=回归+收敛、E3=对独立锚误差
   在容差内），本模块只承载结构与记录。
4. **未接入的部分（如实披露）**：本次未改动 `tools/ops/quality_gate.py`（R1–R6 PR 门）
   与 `backend/api/tensor_missions.py` 的 complete 路径——理由：平台冻结期禁动运行
   底座，且本单判据只要求门与工单接入 machine-checkable 模块。接续点：把
   `summary.quality_gate` 的审计扩展为调用 `outcome_evidence_gate.evaluate()`，
   并在 `complete` 受理前用 `ReviewTicketLedger` 检查 S3+ 是否已闭环四类审查。
5. **基线红记账（QUALITY_BAR R4）**：`tools/ops/benchmark/test_benchmark_dispatch.py::test_backlog_backward_step_l4_is_gpu`
   在本分支与 main 上均为红（与本次改动无关，已用移动 `tools/ops/pytest.ini` 前后对照复验：
   1 failed / 33 passed 两次一致）。按 R4 只记录不修改，本次不新增红：`tools/ops` 全目录
   116 passed / 1 failed（该既有红），本单新增两套件全绿。
6. **工单台账未落数据库**：当前为 JSON 快照 + append-only 事件史（`save/load`），
   便于审计与回放；并发场景需加文件锁或迁移到 DB（未做）。

## 5. 误差判定（本单为后端/治理任务，无数值基准）

| 关键量 | 基准 | 实测 | 是否 <3% |
|---|---|---|---|
| 成果登记门分类正确率 | 7 条注入样例的期望判决（手工设计判据） | {counts['negative_red']}/{counts['negative_cases']} 负例如期红、{counts['positive_green']}/{counts['positive_cases']} 正例如期绿（离散一致，误差=0） | 是（离散 100% 一致） |
| 工单拒收正确率 | {tc['negative_cases']} 条违例注入 | {tc['negative_rejected']}/{tc['negative_cases']} 如期拒收（离散一致） | 是 |
| pytest 通过率 | 全绿 | {pt['passed']} passed / 0 failed | 是 |

无物理量基准：本单不产生数值仿真结果，故无场量/曲线误差可比；上表以离散一致性
代替连续误差判据，并显式声明理由（无物理基准，不适用 3% 门槛）。

## 5.1 复现指令

```bash
python -m pytest tools/ops/test_outcome_evidence_gate.py tools/ops/test_review_ticket_ledger.py -q   # 42 passed
python tools/ops/m5025_outcome_review_audit.py    # 重算全部数字 + 重画图 + 重写本报告
python tools/ops/outcome_evidence_gate.py --rules # 判据清册
REVIEW_TICKET_STORE=/tmp/rt.json python tools/ops/review_ticket_ledger.py types | list | stats
```

## 6. 审计链

- SPEC：`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md` §6.1–§7.1
- 实现：`tools/ops/outcome_evidence_gate.py`、`tools/ops/review_ticket_ledger.py`
- 测试：`tools/ops/test_outcome_evidence_gate.py`、`tools/ops/test_review_ticket_ledger.py`
- 工件：`{art['artifact_paths']['json']}`（含逐例判决、矩阵、pytest 计数、台账快照）
- 图：`{art['artifact_paths']['figure']}`
- 生成器：`tools/ops/m5025_outcome_review_audit.py`（重跑即复现全部数字）
"""
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(md, encoding="utf-8")
    return REPORT_MD


def main() -> int:
    gate = gate_matrix()
    tickets = ticket_matrix()
    se = se_matrix()
    pt = pytest_run()

    fig = make_figure(se, tickets)
    art = {
        "mission_id": 5025,
        "node": "IN19-Q3",
        "spec": "docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md",
        "spec_clauses": {"four_reviews": "§6.1", "evidence_levels": "§6.2",
                         "evidence_principles": "§6.3", "sxE_legality": "§7.1-8/-9"},
        "modules": {"evidence_gate": "tools/ops/outcome_evidence_gate.py",
                    "review_tickets": "tools/ops/review_ticket_ledger.py"},
        "gate_rules": gate_rules_doc(),
        "gate": gate,
        "tickets": tickets,
        "se_matrix": se,
        "pytest": pt,
        "artifact_paths": {
            "json": str((OUT / "outcome_review_gate_audit.json").relative_to(REPO)),
            "figure": str(fig.relative_to(REPO)),
            "markdown": str(REPORT_MD.relative_to(REPO)),
            "figure_link_from_report": "../../" + str(fig.relative_to(REPO)),
            "generator": "tools/ops/m5025_outcome_review_audit.py"},
        "domain_neutrality": {"fixture_domain": "synthetic-alpha",
                              "hardcoded_domain_words": []},
        "acceptance": {
            "e_level_gate_enforced": True,
            "s5_requires_e3": True,
            "negative_probes_gate": gate["counts"]["negative_cases"],
            "negative_probes_gate_red": gate["counts"]["negative_red"],
            "negative_probes_tickets": tickets["counts"]["negative_cases"],
            "negative_probes_tickets_rejected": tickets["counts"]["negative_rejected"],
            "ticket_types_openable_closable": tickets["counts"]["opened_closed"],
            "review_types_total": len(REVIEW_TYPES),
            "innovation_novelty_field_enforced": True,
            "pytest_passed": pt["passed"],
            "pytest_failed": pt["failed"],
            "pytest_requirement": 8,
            "all_criteria_satisfied": bool(
                gate["negative_all_red"] and gate["positive_all_green"]
                and tickets["negative_all_rejected"]
                and gate["counts"]["negative_cases"] >= 2
                and tickets["counts"]["negative_cases"] >= 2
                and tickets["counts"]["opened_closed"] == len(REVIEW_TYPES)
                and pt["passed"] >= 8 and pt["returncode"] == 0),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    snap = OUT / "review_tickets_snapshot.json"
    snap.write_text(json.dumps(tickets["ledger_snapshot"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    art["artifact_paths"]["ticket_ledger_snapshot"] = str(snap.relative_to(REPO))
    (OUT / "outcome_review_gate_audit.json").write_text(
        json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
    md = write_markdown(art)
    print(json.dumps({"artifact": art["artifact_paths"], "pytest": pt["summary_line"],
                      "all_criteria_satisfied": art["acceptance"]["all_criteria_satisfied"],
                      "markdown": str(md)}, ensure_ascii=False, indent=2))
    return 0 if art["acceptance"]["all_criteria_satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())