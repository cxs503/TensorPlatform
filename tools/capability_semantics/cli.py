"""命令行：``python3 -m tools.capability_semantics``（或 ``.cli``）。

用法::

    # 全流程 + 图 + 报告文档（默认路径）
    python3 -m tools.capability_semantics --figures --doc

    # 只算不落工件（干跑；测试/CI 用）
    python3 -m tools.capability_semantics --dry-run --json

    # 注入测试覆盖率与测试文件清单（判据 e 需要）
    python3 -m tools.capability_semantics --coverage-pct 91.3 \
        --test-file tools/capability_semantics/tests/test_evidence.py --doc

退出码：0=全部判据通过；1=有判据未过；2=输入缺失/致命错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .figures import make_figures
from .pipeline import (DEFAULT_PATHS, PipelineResult, finalize_acceptance_after_docs,
                       rebuild_audit_log, run_pipeline)


def render_markdown(result: PipelineResult, figures: Sequence[Mapping[str, str]] = (),
                    coverage_pct: float | None = None) -> str:
    """把流水线结果渲染成六要素报告骨架（数字全部程序化注入，零手抄）。"""
    acc = result.acceptance
    crit = acc.get("criteria", {})
    stats = result.layers.get("stats", {})
    ev = result.evidence
    ev_after = result.verification.get("evidence_after", {})
    mis = result.misuse
    ver = result.verification
    gsum = result.graph.get("summary", {})
    a = crit.get("a_semantic_layers", {})
    b = crit.get("b_misuse_governance", {})
    c = crit.get("c_evidence_audit", {})
    d = crit.get("d_dependency_graph", {})
    e = crit.get("e_maturity_delivery", {})
    f = crit.get("f_safety", {})
    debt = result.debt or {}
    debt_summary = debt.get("summary", {})
    chain_names = sorted(set(result.artifacts) | {"audit_log.json"})
    _sens = c.get("sensitivity") or {}
    sens_all = (_sens.get("all_graded") or {}).get("ratio", 0.0)
    sens_ledger = (_sens.get("ledger_only") or {}).get("ratio", 0.0)
    sens_ex = (_sens.get("excluding_out_of_repo") or {}).get("ratio", 0.0)

    def _tbl(rows: Sequence[Sequence[Any]], header: Sequence[str]) -> str:
        out = ["| " + " | ".join(str(h) for h in header) + " |",
               "|" + "|".join(["---"] * len(header)) + "|"]
        for r in rows:
            out.append("| " + " | ".join(str(x) for x in r) + " |")
        return "\n".join(out)

    lvl_rows = [[k, int(v)] for k, v in (c.get("level_distribution_after") or {}).items()]
    mis_rows = [[f_["kind"], f_["capability_id"], f_["declared_level"],
                 f_["suggested_level"], ",".join(f_["actions"])]
                for f_ in (mis.get("findings") or [])[:20]]
    fig_lines = []
    for fig in figures:
        fig_lines.append(f"![{fig.get('caption', '')}](figures/{fig.get('name')})")
        fig_lines.append("")
        fig_lines.append(f"*{fig.get('caption', '')}*")
        fig_lines.append("")

    lines = [
        "# P266W2-N10 能力台账语义三元分层成熟化（capability / delivery / evidence）",
        "",
        "> 本文件由 `tools/capability_semantics` 程序化生成（零手抄）：所有数字来自",
        f"> `artifacts/capability_semantics/acceptance.json`（sha256 链见 `audit_log.json`）。",
        "",
        "## 1. 案例介绍（问题背景与输入）",
        "",
        "能力台账此前是**扁平结构**：一条能力一行，只有 `id / domain / 声明等级 / 自由文本证据`。",
        "扁平结构无法回答三个治理问题：①「能做什么」与「交付了什么」混为一谈；",
        "②「有实现代码」被当成「有证据」；③ 对外报 `NOT_SUPPORTED` 的能力可能早已有在线端点。",
        "本节点把它重构成语义三元分层，并给误用与证据建立机器判据。",
        "",
        _tbl([
            ["台账条目（capability 层）", stats.get("ledger_entries", 0)],
            ["分层后能力条目", stats.get("capability_entries", 0)],
            ["交付物节点（delivery 层）", stats.get("tier_node_counts", {}).get("delivery", 0)],
            ["证据节点（evidence 层）", stats.get("tier_node_counts", {}).get("evidence", 0)],
            ["端点模块扫描数（delivery 粒度）", stats.get("endpoint_module_count", 0)],
            ["其中孤立（账上无条目）", stats.get("orphan_delivery_count", 0)],
            ["配置文件", acc.get("inputs", {}).get("config", "")],
            ["配置 sha256", acc.get("inputs", {}).get("config_sha256", "")],
            ["台账 sha256", acc.get("inputs", {}).get("ledger_sha256", "")],
        ], ["输入项", "值"]),
        "",
        "## 2. 怎么做的（方法 / 流程 / 工具链）",
        "",
        "流水线：**声明源归一 → 交付物扫描 → 三层装配 → 证据等级级联判定 → 误用扫描 →",
        "补丁改判 → 复扫验证 → 依赖图 → 验收判据落盘**。",
        "",
        "证据等级判定级联（顺序即优先级，见 `tools/capability_semantics/evidence.py`）：",
        "",
        _tbl([
            ["1", "有 `stance=contradicts` 的独立审计裁决", "取最弱裁决等级（CONTRADICTED_BY_AUDIT）"],
            ["2", "无代码交付、无证据", "NOT_SUPPORTED（NO_DELIVERY_NO_EVIDENCE）"],
            ["3", "无代码交付、有证据/验证工件", "WITHHELD（NO_CODE_DELIVERY_WITH_EVIDENCE）"],
            ["4", "有代码 + 数值通过工件（误差 ≤ 3%）", "REAL（NUMERIC_PASS_VERIFIED）"],
            ["5", "有代码 + 含数值断言的测试", "WITHHELD（UNVERIFIED_IMPLEMENTATION）"],
            ["6", "有代码 + 无数值断言的测试", "WITHHELD（TEST_WITHOUT_NUMERIC_ASSERT）"],
            ["7", "只有代码", "STUB（CODE_ONLY）"],
        ], ["序", "条件", "派生等级"]),
        "",
        "误用治理三分类（`misuse.py`）：`UNDERCLAIM_NOT_SUPPORTED`（声明不支持但仓内有交付）、",
        "`UNREGISTERED_DELIVERY`（端点模块在飞而账上无条目）、`CLAIM_WITHOUT_DELIVERY`（声明有等级但扫描零交付，软信号）。",
        "改判只写成**补丁工件**并复扫验证，不改写规范台账本体。",
        "",
        "顺带修复（接线缺陷）：仓内 `backend/api/capability_audit.py` 一直存在但 **main.py 从未",
        "include**，线上 `/api/v1/tensor/capabilities/audit` 恒 404（探针实测：带 token 打 :8010",
        "返回 404；本节点已接线并在单测里做源码级接线回归）。",
        "",
        "### 2.1 attempt-2 修正（本版相对上一版的实质变更）",
        "",
        f"1. **真缺陷修复：工件未随分支提交。** 上一版只提交了代码与报告，"
        f"`artifacts/capability_semantics/` 未入分支 ⇒ 服务侧 `_load` 恒 503，"
        f"「成熟化 API」形同虚设。本版把 {len(chain_names)} 个工件（含 sha256 审计链）"
        "随分支提交，并加机器证明测试 `test_committed_artifacts_serve_all_endpoints`："
        f"用仓内**真实**工件打 {len((e.get('api_endpoints') or []))} 个只读端点，全 200 才算过。",
        "2. **判据 (c) 改为诚实口径。** 上一版当 WITHHELD 比例超标时按"
        "「已给逐因分析」判 `ok=True`（`waived_by_analysis=True`）——这等于让解释替代达标。"
        "本版删除豁免：超标即 `ok=False`，并额外给**三口径敏感性**"
        f"（all_graded {sens_all:.2%} / ledger_only {sens_ledger:.2%} / "
        f"excluding_out_of_repo {sens_ex:.2%}），防止挑分母宣布通过。",
        f"3. **新增验证欠账台账**（`verification_debt.json`，"
        f"{debt_summary.get('debt_entries', 0)} 条）：把比例拆成「优先级 + 提升条件 + "
        "当前量测缺口」，并**真跑**配置声明的独立验证脚本"
        f"（{debt_summary.get('verifiers_executed', 0)} 个执行成功），"
        "记录仓内命令/sha256/退出码/输出 sha256——量测值不是手抄，重跑即得。",
        "4. **新增只读端点** `/api/v1/tensor/capabilities/semantics/debt`"
        "（支持 priority/status 过滤），供审计脚本机器复核。",
        f"5. **覆盖率口径如实披露**：判据 (e) 的 {e.get('coverage_pct')}% 是 "
        "`tools/capability_semantics` 的分支覆盖（pytest --cov-branch，见第 6 节复现命令）；"
        "API 模块因 coverage 纯 Python tracer 与 anyio/TestClient 线程冲突（`pop from empty list`）"
        "无法同进程测量，按「端点级黑盒断言」覆盖（12 例），不冒充行覆盖率。",
        "",
        "## 3. 计算结果（判据 (a)–(f)）",
        "",
        _tbl([
            ["(a) 三层分层", a.get("capability_entries", 0),
             f"归类 {a.get('class_assignment_ratio', 0):.0%} / 实质覆盖 {a.get('tier_populated_ratio', 0):.2%}",
             f"丢失 {a.get('lost_entries', 0)} 条（{a.get('entry_loss_ratio', 0):.2%}）", a.get("ok")],
            ["(b) 误用治理", f"修复前 {b.get('misuse_found_before', 0)} → 后 {b.get('misuse_found_after', 0)}",
             f"fixed={b.get('fixed_count', 0)}", f"阈值 ≥{b.get('min_misuse_fixes', 0)}", b.get("ok")],
            ["(c) 证据审计", c.get("graded_entries", 0),
             f"WITHHELD {c.get('withheld_ratio', 0):.2%}"
             + ("" if c.get("withheld_ok") else "（honest-FAIL：附逐因分解 + 欠账台账）"),
             f"阈值 <{c.get('withheld_ratio_max', 0):.0%}", c.get("ok")],
            ["(d) 依赖图", gsum.get("node_count", 0),
             f"环 {d.get('cycle_count', 0)} / 孤立 {d.get('orphan_ratio_domain', 0):.2%}",
             f"阈值 <{d.get('orphan_ratio_max', 0):.0%}", d.get("ok")],
            ["(e) 成熟化交付", len(e.get("api_endpoints", [])),
             f"覆盖率 {coverage_pct if coverage_pct is not None else e.get('coverage_pct')}",
             f"阈值 ≥{e.get('coverage_threshold', 80)}%", e.get("ok")],
            ["(f) NaN/None 安全", len(f.get("degenerate_probes", [])), "—", "—", f.get("ok")],
        ], ["判据", "规模", "指标", "阈值", "结论"]),
        "",
        "证据等级分布（补丁后，共 %d 条）：" % ev_after.get("graded_entries", 0),
        "",
        _tbl(lvl_rows or [["—", 0]], ["等级", "条目数"]),
        "",
        "WITHHELD 逐因分解（判据 c 要求 >30% 时给原因；"
        f"本次 withheld_ok={c.get('withheld_ok')}, waived_by_analysis="
        f"{c.get('waived_by_analysis')}, ok_basis={c.get('ok_basis')}）：",
        "",
        _tbl([[k, v.get("count"), f"{v.get('share_of_withheld', 0):.1%}", v.get("text", "")]
              for k, v in (c.get("withheld_breakdown") or {}).items()] or [["—", 0, "—", "—"]],
             ["理由码", "条目数", "占 WITHHELD", "释义"]),
        "",
        "逐因受影响的条目（根因分析）：",
        "",
        _tbl([[k, len(v), "、".join(v), (c.get("withheld_root_causes") or {}).get(k, "")]
              for k, v in (c.get("withheld_groups") or {}).items()] or [["—", 0, "—", "—"]],
             ["理由码", "条目数", "受影响能力", "根因"]),
        "",
        "误用扫描明细（前 20 条）：",
        "",
        _tbl(mis_rows or [["—", "—", "—", "—", "—"]],
             ["kind", "capability / module", "declared", "suggested", "修复动作"]),
        "",
        "REAL 评定归因可靠性（subject_backed = 验证工件自身主题命中该能力；"
        "keyword_only = 仅靠关键词共现，需人工复核）：",
        "",
        _tbl([["REAL 条目", "、".join(c.get("real_entries") or []) or "—"],
              ["subject_backed", "、".join(c.get("real_subject_backed") or []) or "—"],
              ["keyword_only（需复核）", "、".join(c.get("real_keyword_only") or []) or "—"]],
             ["口径", "能力"]),
        "",
        "补丁改判与独立评定冲突（取保守值对外声明，冲突单列）：",
        "",
        _tbl([[x.get("capability_id"), x.get("evidence_level_uncapped"),
               x.get("applied_level")]
              for x in (c.get("patch_capped") or [])] or [["—", "—", "—"]],
             ["能力", "独立评定等级", "对外声明等级（保守）"]),
        "",
        "精度维（与等级维正交，`accuracy_flag`）：",
        "",
        _tbl([[k, v] for k, v in (c.get("accuracy_histogram") or {}).items()],
             ["accuracy_flag", "条目数"]),
        "",
        "## 4. 结果图",
        "",
        ("\n".join(fig_lines) if fig_lines else
         "（本次运行未生成图：`--figures` 未开启或 matplotlib 不可用，见 `warnings`）"),
        "",
        "## 5. 注意点（坑 / 局限 / 适用范围）",
        "",
        "1. **代码存在 ≠ 证据存在**：级联第 4 条要求独立验证工件 + 数值判据，否则 WITHHELD；",
        "   这把「实现完整」与「已证实」拆开，避免再用交付物冒充证据。",
        "2. **改判只出补丁**：`registry_patch.json` 带 `applies_to_ledger_sha256` 与逐条",
        "   `basis_sha256`；规范台账本体本节点不改写（需复核），避免自证式修复。",
        "3. **扫描关键词表是假阴性来源**：`CLAIM_WITHOUT_DELIVERY` 类信号既可能是过度声明，",
        "   也可能是关键词未覆盖；因此定级 low 并给 `REVIEW_SCAN_KEYWORDS` 人工动作，不自动降级。",
        "4. **孤立率分母口径**：判据在 `graph_role=domain` 子图上判，全体（含工具类节点）比例",
        f"   单列披露为 {gsum.get('orphan_ratio_all_disclosed', 0):.2%}；两口径都写进 `acceptance.json`。",
        "5. **自指证据陷阱（本单踩过）**：第一版把本节点自己生成的报告/工件当成扫描输入，"
        "报告里写着「误差 ≤3%」→ 扫描器判 pass → 变成该能力的验证证据（循环论证）。"
        "现已用 `delivery_discovery.exclude_glob_patterns` 排除本节点产物与模块自身，"
        f"排除项入账 warnings：{'; '.join(w for w in result.warnings if 'excluded_globs' in w) or '（本次无）'}。",
        "6. **REAL 的归因可靠性单列披露**：`subject_backed` 表示验证工件自身主题命中该能力"
        "（路径词干/文件首段），`keyword_only` 仅靠关键词共现——后者不是判据，只是风险披露，"
        "但必须在报告里点名，避免「扫描扫出 REAL」被当成独立复核。",
        "7. **零领域语义**：能力 id / 标签 / 学科 / 依赖边全部来自 `config/capability_semantics.json`，",
        "   代码与单测夹具只用合成域名（`synth.*`）。",
        "",
        "## 6. 误差判定（关键量 vs 基准）",
        "",
        _tbl([
            ["条目丢失率", f"{a.get('entry_loss_ratio', 0):.2%}", "0（预注册停止规则 5%）",
             "是" if a.get("entry_loss_ratio", 1) == 0 else "否"],
            ["归类率（结构）", f"{a.get('class_assignment_ratio', 0):.2%}", "100%",
             "是" if a.get("class_assignment_ratio", 0) == 1.0 else "否"],
            ["实质覆盖（交付或证据非空）", f"{a.get('tier_populated_ratio', 0):.2%}",
             "披露项（无阈值）", "—"],
            ["WITHHELD 比例", f"{c.get('withheld_ratio', 0):.2%}",
             f"<{c.get('withheld_ratio_max', 0):.0%}",
             "是" if c.get("criterion_withheld_ok") else "否（原因见第 3 节逐因分解）"],
            ["循环依赖", str(d.get("cycle_count", 0)), "0",
             "是" if d.get("cycle_count", 1) == 0 else "否"],
            ["孤立节点率（domain）", f"{d.get('orphan_ratio_domain', 0):.2%}",
             f"<{d.get('orphan_ratio_max', 0):.0%}",
             "是" if d.get("orphan_ratio_domain", 1) < d.get("orphan_ratio_max", 0.05) else "否"],
            ["误用修复数", str(b.get("fixed_count", 0)), f"≥{b.get('min_misuse_fixes', 0)}",
             "是" if b.get("fixed_count", 0) >= b.get("min_misuse_fixes", 0) else "否"],
        ], ["关键量", "实测", "基准/阈值", "是否达标"]),
        "",
        (f"注：判据 (c) 的 WITHHELD 比例 {c.get('withheld_ratio', 0):.2%} "
         f"{'低于' if c.get('withheld_ok') else '不低于'}阈值 "
         f"{c.get('withheld_ratio_max', 0):.0%}；ok_basis={c.get('ok_basis')}。"
         + ("" if c.get("withheld_ok") else
            f" 本条按诚实口径判 **FAIL**（原因分析不替代达标）：需消解 "
            f"{sum(len(v) for v in (c.get('withheld_groups') or {}).values())} 条 WITHHELD，"
            "逐条整改条件见第 3.1 节欠账台账。")),
        "",
        "口径敏感性（同一审计在不同总体定义下的比例；任一口径达标不得单独宣布通过）：",
        "",
        _tbl([[k, v.get("graded"), v.get("withheld"), f"{v.get('ratio', 0):.2%}",
               "是" if v.get("below_max") else "否"]
              for k, v in (c.get("sensitivity") or {}).items()
              if isinstance(v, Mapping)] or [["—", 0, 0, "—", "—"]],
             ["口径", "评定条目", "WITHHELD", "比例", "低于阈值"]),
        "",
        "### 3.1 验证欠账台账（比例→可执行整改项，`verification_debt.json`）",
        "",
        _tbl([[r.get("priority"), r.get("capability_id"), r.get("declared_level"),
               r.get("derived_level"), r.get("status"),
               ", ".join(f"{k}={v:.4g}" for k, v in (r.get("measurements") or {}).items()) or "—",
               r.get("lift_condition")]
              for r in (debt.get("records") or [])[:20]] or [["—"] * 7],
             ["优先级", "能力", "声明", "实得", "欠账状态", "独立验证量测", "提升条件"]),
        "",
        "验证脚本执行记录（真跑，命令/脚本 sha256/退出码/输出 sha256 可复核）：",
        "",
        _tbl([[r.get("capability_id"), r.get("label"), r.get("status"),
               r.get("verdict"), r.get("exit_code"),
               f"{r.get('script_sha256', '')[:12]}…" if r.get("script_sha256") else "—",
               (r.get("reason") or "")[:60]]
              for r in (debt.get("runs") or [])] or [["—"] * 7],
             ["能力", "验证脚本", "状态", "判决", "退出码", "脚本 sha256", "备注"]),
        "",
        (f"欠账汇总：条目 {debt_summary.get('debt_entries')}（P0 {debt_summary.get('by_priority', {}).get('P0', 0)} / "
         f"P1 {debt_summary.get('by_priority', {}).get('P1', 0)} / "
         f"P2 {debt_summary.get('by_priority', {}).get('P2', 0)}），"
         f"已有独立量测占比 {debt_summary.get('measured_ratio', 0):.2%}，"
         f"外仓实现 {debt_summary.get('out_of_repo_count', 0)} 条。"),
        "",
        "判据口径说明：本节点判的是**治理口径的误差（条目丢失率/覆盖率/比例）**，",
        "不是物理量精度——物理精度由各领域仓的验证工件承担，本流水线只按 3%/15% 两档引用其数值判据。",
        "",
        f"**总判定：{'全部判据通过' if acc.get('all_ok') else '存在未通过判据'}**"
        f"（all_ok={acc.get('all_ok')}）；warnings={len(result.warnings)} 条。",
        "",
    ]
    return "\n".join(lines) + "\n"


def _summary_text(result: PipelineResult) -> str:
    crit = result.acceptance.get("criteria", {})
    flags = ", ".join(f"{k}={'OK' if v.get('ok') else 'FAIL'}" for k, v in crit.items())
    return (f"entries={result.layers.get('stats', {}).get('capability_entries', 0)} "
            f"misuse {result.verification.get('before', {}).get('not_supported_misuse_count')}"
            f"→{result.verification.get('after', {}).get('not_supported_misuse_count')} "
            f"withheld={result.verification.get('evidence_after', {}).get('withheld_ratio')} "
            f"all_ok={result.acceptance.get('all_ok')} [{flags}]")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.capability_semantics",
                                description="能力台账语义三元分层 + NOT_SUPPORTED 误用治理")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--out", default=None, help=f"默认 {DEFAULT_PATHS['out']}")
    ap.add_argument("--config", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--patterns", default=None)
    ap.add_argument("--extra-source", action="append", default=[],
                    metavar="NAME=PATH", help="额外声明源（如 agent 工具快照）")
    ap.add_argument("--coverage-pct", type=float, default=None)
    ap.add_argument("--test-file", action="append", default=[])
    ap.add_argument("--doc", default=None, help="Markdown 报告输出路径（- 表示 stdout）")
    ap.add_argument("--figures", action="store_true", help="生成报告图（默认 docs/reports/capability_semantics）")
    ap.add_argument("--figure-dir", default=None)
    ap.add_argument("--run-verifiers", action="store_true",
                    help="执行配置声明的独立验证脚本，把量测缺口写入欠账台账（默认关闭）")
    ap.add_argument("--dry-run", action="store_true", help="只计算，不落工件")
    ap.add_argument("--json", action="store_true", help="stdout 打 JSON 摘要")
    args = ap.parse_args(list(argv) if argv is not None else None)

    extra: list[tuple[str, str]] = []
    for item in args.extra_source:
        if "=" not in item:
            print(f"bad --extra-source: {item}", file=sys.stderr)
            return 2
        name, path = item.split("=", 1)
        extra.append((name, path))

    root = Path(args.root).resolve()
    out = Path(args.out) if args.out else root / DEFAULT_PATHS["out"]
    try:
        result = run_pipeline(
            root=root, out_dir=out, config_path=args.config, ledger_path=args.ledger,
            patterns_path=args.patterns, extra_declaration_sources=extra,
            coverage_pct=args.coverage_pct, test_files=args.test_file,
            run_verifiers=args.run_verifiers)
    except Exception as exc:  # noqa: BLE001 - CLI 边界：致命错误如实返回码
        print(f"pipeline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    figures: list[dict[str, str]] = []
    if args.figures and not args.dry_run:
        fig_dir = Path(args.figure_dir) if args.figure_dir else root / "docs/reports/capability_semantics"
        figures = make_figures(result.to_dict(), fig_dir)
        fig_manifest = [{"name": f["name"], "caption": f["caption"]} for f in figures]
        (out / "figures.json").write_text(
            json.dumps(fig_manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if args.doc:
        md = render_markdown(result, figures, coverage_pct=args.coverage_pct)
        if args.doc == "-":
            print(md)
        else:
            doc_path = Path(args.doc)
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(md, encoding="utf-8")

    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        if args.doc and args.doc != "-":
            # 文档已真实落盘 → 回填判据 (e) 并重建 sha256 链（只回填事实，不改判定逻辑）
            finalize_acceptance_after_docs(root, out, args.doc)
        rebuild_audit_log(out)
    text = _summary_text(result)
    if args.json:
        print(json.dumps({
            "summary": text,
            "acceptance": result.acceptance,
            "artifacts": result.artifacts,
            "warnings": result.warnings,
        }, ensure_ascii=False, indent=1))
    else:
        print(text)
        for name, sha in sorted(result.artifacts.items()):
            if sha and len(sha) == 64:
                print(f"  {name}: {sha[:16]}…")
    return 0 if result.acceptance.get("all_ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())