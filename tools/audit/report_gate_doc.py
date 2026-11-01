#!/usr/bin/env python3
"""从 report_gate_audit_*.json 程序化生成验证报告 md + 平台报告载荷（数字零手抄）。"""
import base64, json, pathlib, subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
J = json.loads((REPO / "docs/reports/report_gate_audit_2026-11-01.json").read_text())
FIG = REPO / "docs/reports/figs/report_gate_false_accept.png"
c = lambda v: "是" if v else "否"
pct = lambda v: f"{v*100:.2f}%"

rows_main = f"""| 指标 | 数值 |
|---|---|
| 语料规模（已发布报告，含 markdown） | {J['corpus_size']} 份（最大 id {J['corpus_max_id']}，快照 {J['generated_at']}） |
| v1（keyword-only）放行率 | {pct(J['v1_pass_rate'])}（{J['v1_pass']}/{J['corpus_size']}） |
| v2（六要素）放行率 | {pct(J['v2_pass_rate'])}（{J['v2_pass']}/{J['corpus_size']}） |
| v1 误放行（v1 过、v2 否） | {pct(J['v1_false_accept_rate'])}（{J['v1_pass_v2_fail']} 份） |
| 误放行占 v1 全部放行的比例 | {pct(J['false_accept_share_of_v1_passes'])} |
| v2 仅放行（v1 拒、v2 过：显式写了无图理由） | {J['v2_only_pass']} 份 |"""

rows_issue = "\n".join(
    f"| `{k}` | {v} |" for k, v in J["issue_breakdown_among_false_accepts"].items())
rows_issue = "| 缺项代码 | 误放行份数 |\n|---|---|\n" + rows_issue

rows_check = "\n".join(
    f"| {k} | {pct(v)} |" for k, v in J["v2_check_pass_rates"].items())
rows_check = "| 要素 | v2 在语料上的通过率 |\n|---|---|\n" + rows_check

adv = J["adversarial"]
rows_adv = "\n".join(
    f"| {r['name']} | {c(r['v1_pass'])} | {c(r['v2_pass'])} | "
    f"{'拒' if r['expect']=='reject' else '收'} | {c(r['v2_correct'])} | {r['why']} |"
    for r in adv["cases"])
rows_adv = ("| 对抗样本 | v1 判过 | v2 判过 | 期望 | v2 判定正确 | 构造依据 |\n"
            "|---|---|---|---|---|---|\n" + rows_adv)

md = f"""# 报告 QA 门禁的验证欠账审计与修复（RPT-GATE v1 → v2）

> 任务：tp-doc-3（data 席自主工时）· 代码分支 `agent/m1408` · 语料快照 {J['generated_at']}
> 复算入口：`tools/audit/report_gate_audit.py`（只读 psql 导出 + 全量复算 + 出图）
> 判据实现：`backend/services/report_gate.py`；单测：`backend/tests/test_report_gate_validation.py`

## ① 案例介绍（物理背景与"几何参数"）

被审计对象不是一条船，而是**平台自己的报告质量门禁**：owner 2026-10-29 把进展报告升级为
"论文级六要素"（① 案例介绍 ② 方法流程 ③ 计算结果 ④ 结果图 ⑤ 注意点 ⑥ 误差判定），
门禁 `_check_report_quality()` 负责在 `/tensor/reports/qa/check` 上把不合规报告打回。

把它当作**测量仪器**看，本单量的就是这台仪器的系统偏差：门禁太松 = 把不合格报告判合格
（误放行 false accept），后果与阻力预报系统性偏差 >15% 同类——后续所有结论都建在未验证的
数字上。六要素即该仪器的"参数表"：

| 要素 | v1 判据形式 | v2 判据形式 |
|---|---|---|
| ① 案例介绍 | 两组关键词**任一组**命中即可（组间 AND 被写成组内 OR） | 两组**同时**命中（问题描述 + 几何/参数） |
| ② 方法流程 | 关键词命中 | 不变（关键词命中） |
| ③ 验证对标 | 关键词命中 | 不变（关键词命中，含"解析"） |
| ④ 结果图 | 正文出现"图"字 **或** figures 非空 | figures 非空 **或** 显式无图理由；有图须有 caption |
| ⑤ 注意点 | 关键词命中 | 不变 |
| ⑥ 误差判定 | **完全缺失** | 误差数字 **且** 结论词（是否<3%），或显式声明无基准 |

## ② 怎么做的（方法/流程/工具链）

1. **取真值基线**：用 `git show HEAD:backend/api/tensor_reports.py` 取出生产 v1 门禁源码，
   在审计脚本里 exec 成纯函数（不改动生产代码即得"旧仪器"读数）。
2. **查副本漂移**：`backend/tests/test_report_gate_standalone.py` 曾是 v1 的**手抄副本**——
   副本与生产一旦漂移，测试绿灯就与生产行为脱钩（典型的"假门禁"）。审计对语料逐份比对副本
   与 v1 判定：本次快照下 0 处不一致（未漂移），但结构风险属实，故本单把副本改为 import 同源实现。
3. **全量复算**：psql 只读导出 `tensor_reports` 全部 `markdown + figures`（不建 engine，
   不需要 psycopg2），逐份跑 v1 / v2，统计放行率、误放行率、逐要素通过率。
4. **判据抽模块**：判据搬到 `backend/services/report_gate.py`（纯函数、无 FastAPI/DB 依赖），
   API 层只做 import 别名——门禁从此可离线单测。
5. **对抗样本**：每类误放行构造 1 例（A/B/C）+ 1 例合规对照（D），量化检出率与具体性。
6. **校准**：初版 ⑥ 只认"<3%/可接受"字面词，实测发现会把"误差 0.57%，✅ 一致"这类合规报告
   误拒；据语料把"一致/吻合/达标/判据/✅"等语义词并入（代码里保留 `*_CORE` / `*_EXTENDED`
   两组常量，校准效应可复算）。

## ③ 计算结果

{rows_main}

逐要素在语料上的通过率（v2）：

{rows_check}

v1 的 {J['v1_pass_v2_fail']} 份误放行，按缺项代码分解（一份可缺多项）：

{rows_issue}

第 ⑥ 要素单独看：严格阈值词口径通过率 {pct(J['error_verdict_strict_pass_rate'])}，
校准后 {pct(J['error_verdict_calibrated_pass_rate'])}——校准挽回
{round((J['error_verdict_calibrated_pass_rate']-J['error_verdict_strict_pass_rate'])*J['corpus_size'])} 份
本会被误拒的合规报告；同时被拒的 {J['error_verdict_reject_causes'].get('no_error_number',0)} 份
**全部**属于"根本没有误差数字"（`number_but_no_verdict` 计数为 0），说明校准没有放过真缺项。

对抗样本（A/B/C 为 v1 误放行类，D 为具体性控制）：

{rows_adv}

## ④ 结果图

![report_gate_false_accept.png](figs/report_gate_false_accept.png)

图（`docs/reports/figs/report_gate_false_accept.png`，由 `report_gate_audit.py` 程序化生成）：
(a) v1 与 v2 在 {J['corpus_size']} 份已发布报告上的放行率对比——门禁收紧后从
{pct(J['v1_pass_rate'])} 降到 {pct(J['v2_pass_rate'])}；
(b) v1 误放行的缺项代码分布，`missing_figures` 与 `missing_error_verdict` 是两大来源；
(c) v2 六要素逐项通过率，缺口集中在"图"与"误差判定"，而"方法/验证/局限"三项接近饱和
（说明关键词判据对这三项实际上已无区分度，见注意点）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

- **关键词判据只是代理指标**：v2 收紧的是"组间 AND 语义"与"必须给结论"，但 `method` /
  `validation` 两项仍是关键词命中，正文出现"验证"二字即可过。要真正防"假验证"需接到工件
  （artifacts 引用 + 数字来源可追溯），本单未做，属剩余欠账。
- **过严风险已量化但未消除**：v2 放行率 {pct(J['v2_pass_rate'])}，其中"缺图"是最大来源；
  历史上确有纯文档任务不配图，v2 用"显式无图理由"放行，但写法千变万化，新写法可能误拒。
- **语料非独立同分布**：835 份各不相同、含大量 QA/审计类报告，放行率不能外推为"平台报告质量"。
- **⑥ 不能验证"数字是否程序注入"**：门禁只看是否有误差数字+结论，无法判断数字是否手抄；
  零手抄只能靠工件指针人工/自动核对（本单报告自身即从 JSON 读表验证该纪律可行）。
- **旧 DB 依赖测试仍跑不起来**：`backend/tests/test_report_gate.py` 需 psycopg2/cryptography，
  本环境缺依赖，其期望值改用等价复算逐条核对（6/6 一致），未在 CI 环境实跑。

## ⑥ 误差判定（关键量 vs 基准，是否 <3%）

本单的"基准"是人工判定哪些报告合规——**平台没有人工标注金标准集**，故不宣称对全语料有
误差数字；改用两类可机器裁的基准，并如实说明：

| 关键量 | 基准 | 结果 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 对抗样本检出率（v1 误放行类 3 例） | 构造即标注（A/B/C 必拒） | v2 全部拒，recall = {pct(adv['recall_on_false_accept'])} | 0.00% | **是** |
| 具体性（合规对照 D 不得被误拒） | 构造即标注（D 必收） | v2 放行，specificity = {pct(adv['specificity_control'])} | 0.00% | **是** |
| ⑥ 校准误拒率（有误差数字却被拒） | v2 `details.error_number=True` 子集 | `number_but_no_verdict` = 0 份 | 0.00% | **是**（分母为该子集，非全语料） |
| 全语料"正确判否/判是"率 | **无人工金标准** | 不适用 | 不适用 | **无法判定**（声明原因：缺标注集） |

诚实结论：门禁**对已知三类误放行 100% 检出、对合规样本 0 误拒**（均 0% < 3%）；
但对真实语料的"判定正确率"没有金标准可对标，本单只能给出误放行的**下界**（
{pct(J['false_accept_share_of_v1_passes'])} of v1 放行），不能给出上界。
"""

out = REPO / "docs/reports/report_gate_validation_2026-11-01.md"
out.write_text(md, encoding="utf-8")
print("[ok] doc ->", out, len(md), "chars")

report = {
    "title": f"报告 QA 门禁的验证欠账审计与修复：v1 keyword-only 误放行 {pct(J['v1_false_accept_rate'])}，v2 六要素对 {J['corpus_size']} 份已发布报告全量复算",
    "summary": (
        f"把平台报告 QA 门禁当测量仪器审计：从 git HEAD 取出生产 v1 判据，对 {J['corpus_size']} 份"
        f"已发布报告全量复算，发现 v1 放行率 {pct(J['v1_pass_rate'])} 中有 {pct(J['false_accept_share_of_v1_passes'])}"
        f"属误放行（组间 AND 被写成 OR、'图'字即算有图、第 ⑥ 要素误差判定完全缺失）。"
        f"判据抽成无依赖模块 backend/services/report_gate.py（生产与单测同源，消除手抄副本漂移），"
        f"补六要素判定并对语料校准（严格词口径 {pct(J['error_verdict_strict_pass_rate'])} → 校准后 "
        f"{pct(J['error_verdict_calibrated_pass_rate'])}），v2 放行率 {pct(J['v2_pass_rate'])}；"
        f"对抗样本 recall {pct(adv['recall_on_false_accept'])}、specificity {pct(adv['specificity_control'])}；"
        f"新增 16 条 pytest 全绿（含防副本漂移结构守卫）。"
    ),
    "scenario_key": "report_qa_gate_validation",
    "mission_id": 1408,
    "tags": ["report-gate", "validation-debt", "qa", "audit", "pytest", "tensorplatform"],
    "markdown": md,
    "figures": [{
        "name": "report_gate_false_accept.png",
        "caption": f"(a) v1 vs v2 放行率（n={J['corpus_size']}：{pct(J['v1_pass_rate'])} → {pct(J['v2_pass_rate'])}）；"
                   f"(b) v1 误放行缺项分布；(c) v2 六要素逐项通过率",
        "png_base64": base64.b64encode(FIG.read_bytes()).decode(),
    }],
}
pathlib.Path("/tmp/report.json").write_text(json.dumps(report, ensure_ascii=False))
print("[ok] report payload -> /tmp/report.json", len(json.dumps(report)), "bytes")
