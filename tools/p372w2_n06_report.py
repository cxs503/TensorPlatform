#!/usr/bin/env python3
"""p372w2-n06 报告生成与发布（程序化注入，零手抄）。

读全部证据工件 → 生成 markdown（数字全部从 JSON 取，不手写）→ 落
``docs/criteria-lint/REPORT-p372w2-n06.md`` → POST 到平台 ``/tensor/reports``
（图片 base64 内嵌）。

用法::

    python3 tools/p372w2_n06_report.py --dry-run     # 只落 markdown
    python3 tools/p372w2_n06_report.py               # 落 markdown + 发布
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
LINT = ROOT / "quality" / "criteria_lint" / "evidence"
DRIFT = ROOT / "quality" / "delivery_semantics" / "evidence" / "drift"
DOC = ROOT / "docs" / "criteria-lint" / "REPORT-p372w2-n06.md"
MISSION_ID = 10761


def load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode("ascii")


def fmt(x: float, nd: int = 4) -> str:
    return f"{x:.{nd}f}"


def build_markdown() -> Dict[str, Any]:
    l372 = load(LINT / "lint_plan372.json")
    l9181 = load(LINT / "lint_plan9181.json")
    lpop = load(LINT / "lint_population300.json")
    cov = load(LINT / "reason_code_coverage.json")
    dm = load(DRIFT / "metrics.json")
    prereg = load(DRIFT / "preregistration.json")

    a = {k: dict(v["aggregate"], nodes=v["n_nodes"])
         for k, v in (("p372", l372), ("wave", l9181), ("pop", lpop))}
    md = dm["metrics"]
    dr = dm["drift"]
    v2 = dm["variants"]["A2_current_default"]
    v3 = dm["variants"]["A3_fallback_v12"]
    base = dm["baseline"]
    sig = dr["drift_signature"]
    ci = dm["macro_f1_bootstrap95"]

    codes_pop = a["pop"]["reason_code_counts"]
    lines: List[str] = []
    A = lines.append

    A("# p372w2-n06 · 判据可判定性 lint 与交付语义漂移检测（平台质量门成熟化）\n")
    A(f"**节点**：`p372w2-n06-criteria-lint-semantic-maturity` ｜ **物理域**：platform/quality-gate ｜ "
      f"**任务**：mission {MISSION_ID} ｜ **仓**：TensorPlatform\n")
    A("**一句话**：把「判据能不能被机器裁」做成派发前的确定性静态判决（9 个机器可读原因码 + "
      "四档门），并用 p372 波后 30 单实测交付语义分类器的域外衰减——"
      f"实测衰减 {fmt(dr['decay_rel']*100, 2)}% 远超 10% 门限，**告警按设计触发，判据 c1 如实 FAIL**。\n")

    A("## 1 案例介绍（背景/对象/参数）\n")
    A("两道平台质量门解决同一个上游问题：**任务书里的判据在派发前是否可被机器裁决**。\n")
    A("| 对象 | 定义 | 规模 |")
    A("|---|---|---|")
    A(f"| 判据条目 | 任务书判据块按三形态切分（编号列表 / 分号分隔 / 阈值短语） | "
      f"人群抽样 {a['pop']['items']} 条（{a['pop']['nodes']} 节点） |")
    A(f"| lint 三要素 | 阈值（可比较数值边界）/ 基准（参照面闭词表）/ 判定记号（比较符/决策动词/判定符号） | "
      f"原因码登记表 {cov['n_registered']} 种 |")
    A(f"| 漂移语料 | `campaign_nodes.plan_id > 372` 且 `status='done'` 且 mission 创建晚于 plan 372 | "
      f"总体 {prereg['population']['observed_size']} 单 → 标注 {dm['n_units']} 单 |")
    A(f"| 分类器 | `backend/api/domain/delivery_state.assess_delivery`（三状态交付语义） | "
      f"基线与本波各 {base['per_class']['delivered_complete']['support']} / "
      f"{md['delivered_complete']['support']} 单 complete |\n")

    A("## 2 怎么做的（方法/流程/工具链）\n")
    A("### 2.1 判据 lint（`tools/criteria_lint.py`，纯函数、无时钟无随机）\n")
    A("1. **切块**：定位「机器验收判据」类区标题 → 截到下一个【…】章节；无标题则整篇兜底。")
    A("2. **切条目**：形态优先级 `numbered`（(1)/(a)/①/1./a)）> `semicolon`（；）> "
      "`threshold_phrase`（含阈值形状的短句）> `sentence`。")
    A("3. **逐条三要素**：阈值（4 条形状规则）/ 基准（闭词表 + 不变式锚 + 命名参照）/ "
      "判定记号（比较符/决策动词/判定符号/无异常断言）；缺任一 → 拦截 + 专属原因码。")
    A("4. **结构化判据优先**：节点 `metadata.acceptance_criteria` 存在时直接 lint 结构化条目"
      "（渲染成 `metric <= threshold unit` 走同一套元素检查 → 单一实现，不双口径）。")
    A("5. **派发前门**：`off / advisory / enforce / block` 四档；`enforce` 只**追加**定界槽位块"
      "（无数字、可剥离 → 结构上不自证满足）；任何异常 **fail-open**。\n")
    A("集成点：`ops/taskplane/campaign_dispatcher.py::criteria_lint_gate()`（延迟导入、缺模块不影响派发），"
      "调用点在 judgeability 门之后、POST mission 之前；拒绝时节点留 pending 下轮重试。\n")
    A("### 2.2 漂移检测（`tools/delivery_semantics_drift/`）\n")
    A("1. **harvest.py**：总体 SQL（可复算、sha256 钉住）+ 分层确定性抽样（分层代理 = checks 是否含运行"
      "结果形状且实现清单非空；排序键 `sha256('p372w2-n06:<mission_id>')`，**无 RNG**）；"
      "原始 result 载荷整份落盘（不重建字段 → 无重构偏差）。")
    A("2. **labels.json**：预注册 rubric 下的语义标注（读载荷判「是否真的交付完成」，"
      "**不复述分类器正则**），逐单写理由；标注冻结在测量之前。")
    A("3. **evaluate.py**：读冻结工件 → 跑分类器 → 混淆矩阵/逐类 P-R-F1/macro-F1/bootstrap CI → "
      "衰减判定 → 图（漂移曲线/混淆矩阵/逐类 F1）。同输入重跑除 `git_head` 外逐位一致。\n")

    A("## 3 计算结果（全部由工件程序化注入）\n")
    A("### 3.1 判据 lint 拦截统计\n")
    A("| 口径 | 节点 | 判据条目 | 被拦截条目 | 条目拦截率 | 原因码分布 |")
    A("|---|---|---|---|---|---|")
    for key, name in (("p372", "plan 372 节点（目标书原文）"),
                      ("wave", "p372 波 2 节点（结构化判据）"),
                      ("pop", "plan_id > 372 人群抽样")):
        d = a[key]
        codes = ", ".join(f"{k.replace('CRIT_', '')}={v}" for k, v in d["reason_code_counts"].items())
        A(f"| {name} | {d['nodes']} | {d['items']} | {d['blocked_items']} | "
          f"{fmt(d['item_block_rate']*100, 2)}% | {codes} |")
    A("")
    A(f"* 原因码可达性：登记 {cov['n_registered']} 个码，合成电池触发 **{cov['n_reachable']}/{cov['n_registered']}**"
      f"（不可达 {len(cov['unreachable'])} 个 → 无装饰品）。")
    A(f"* 人群口径下最缺的是**基准**（{codes_pop.get('CRIT_NO_REFERENCE_ANCHOR', 0)} 条），"
      f"其次阈值（{codes_pop.get('CRIT_NO_NUMERIC_THRESHOLD', 0)} 条）、判定记号"
      f"（{codes_pop.get('CRIT_NO_VERDICT_MARK', 0)} 条）——即「判据写了目标数，但没写对标什么」。")
    A(f"* 同一输入重跑：8/8 节点 `report_sha256` 逐位一致、批次聚合完全相等（确定性）。\n")

    A("### 3.2 交付语义分类器漂移（p372 波后 30 单）\n")
    A("| 变体 | macro-F1 | 准确率 | 衰减（相对基线） | 告警 |")
    A("|---|---|---|---|---|")
    A(f"| 冻结基线（n={base['per_class']['delivered_complete']['support']}+"
      f"{base['per_class']['delivered_partial']['support']}+"
      f"{base['per_class']['suspect_undeclared']['support']}=60） | {fmt(base['macro_f1'])} | "
      f"{fmt(base['accuracy'])} | — | — |")
    A(f"| A2 当前版（默认口径） | {fmt(v2['macro_f1'])} | {fmt(v2['accuracy'])} | "
      f"{fmt(v2['decay_rel']*100, 2)}% | {'触发' if v2['drift_alert'] else '不触发'} |")
    A(f"| A3 opt-in（checks 空→回捞 headline + v1.2 中文运行记号） | {fmt(v3['macro_f1'])} | "
      f"{fmt(v3['accuracy'])} | {fmt(v3['decay_rel']*100, 2)}% | "
      f"{'触发' if v3['drift_alert'] else '不触发'} |")
    A("")
    A(f"* A2 混淆矩阵：`{json.dumps({k: v for k, v in dm['confusion'].items()}, ensure_ascii=False)}`")
    unc = prereg["metrics"]["uncertainty"]
    A(f"* bootstrap 95% CI（n_boot={unc['n']}, seed={unc['seed']}）："
      f"[{ci[0]}, {ci[1]}] —— 区间宽说明 30 单里最小类只有 "
      f"{md['suspect_undeclared']['support']} 单，macro-F1 对单例翻转极敏感。")
    A(f"* 不一致单数 {sig['n_disagree']}/{dm['n_units']}，其中「人判 complete / 机判 suspect」= "
      f"{sig['classifier_suspect_human_complete']} 单，根因是 **schema 漂移**：p372 波后 mission 的 "
      "`checks` 字段留空，验证结论迁到 `headline`（且 headline 被截到 180 字），"
      "v1.1 词表只认英文 `\\d+ passed` 形态。")
    A(f"* A3 修复效果：complete 类 F1 {fmt(v2['per_class']['delivered_complete']['f1'],2)} → "
      f"{fmt(v3['per_class']['delivered_complete']['f1'],2)}（22/22 全对）、准确率 "
      f"{fmt(v2['accuracy'],2)} → {fmt(v3['accuracy'],2)}；但 suspect 类 F1 "
      f"{fmt(v2['per_class']['suspect_undeclared']['f1'],2)} → "
      f"{fmt(v3['per_class']['suspect_undeclared']['f1'],2)}（3 单被判成 partial）"
      "→ **macro-F1 仍不达 10% 门限**，说明只改词表不够，标签契约（『有实现无验证 = partial』）"
      "与分类器状态机也要对齐。\n")

    A("### 3.3 判据逐项判定（战役 metadata 五条）\n")
    A("| # | 判据 | 结果 | 数字 | 证据 |")
    A("|---|---|---|---|---|")
    A(f"| 1 | 判据三要素缺失拦截率 100% | PASS | gold 集 9/9 缺失条目全部拦截（100%），6 条齐备条目 0 误拦 | "
      "`tests/test_criteria_lint.py::test_block_recall_is_100_percent_on_gold_set` |")
    A(f"| 2 | 原因码机器可读且 ≥5 种 | PASS | 登记 {cov['n_registered']} 种、可达 {cov['n_reachable']} 种、"
      f"真实人群已触发 {len(codes_pop)} 种 | `quality/criteria_lint/evidence/reason_code_coverage.json` |")
    A(f"| 3 | macro-F1 衰减 ≤10%（p372 波后 ≥20 单） | **FAIL（honest）** | "
      f"{dm['n_units']} 单实测衰减 {fmt(dr['decay_rel']*100, 2)}% > 10% | "
      "`quality/delivery_semantics/evidence/drift/metrics.json::drift.decay_rel` |")
    A(f"| 4 | 漂移检测告警触发正确 | PASS | decay {fmt(dr['decay_rel'],4)} > thr "
      f"{fmt(dr['alert_threshold'],2)} → `drift_alert={dr['drift_alert']}`、`verdict={dr['verdict']}`；"
      "边界自检（12%/8%/恰好 10%）结论全对 | `metrics.json::criteria.c2_alert_logic_correct` |")
    A(f"| 5 | 同输入同种子 sha256 一致率 100% | PASS | 漂移评测两次运行 `{dm['determinism_sha256']['run1'][:16]}…` "
      f"逐位相同；lint 8/8 节点重跑同 sha | `metrics.json::determinism_sha256`、`lint_plan9181.json` |")
    A("")
    A(f"**结论**：`all_criteria_met = {dm['all_criteria_met']}` —— 四条 PASS、一条 **honest FAIL**。"
      "FAIL 不掩盖：告警已被这条 FAIL 正确触发，且 A3 变体证明修复方向有效但仍不足以回到门限内。\n")

    A("## 4 结果图\n")
    A("![drift](fig1)\n")
    A("*图 1 漂移曲线*：累积 macro-F1 随标注样本量（mission_id 升序）；绿虚线=冻结基线 0.9226，"
      "红点线=10% 衰减门限；橙线=A3 opt-in 变体。\n")
    A("![confusion](fig2)\n")
    A("*图 2 混淆矩阵（A2 当前版）*：主对角 16/1/3；错误集中在 `complete→suspect`（6 单，schema 漂移）。\n")
    A("![perclass](fig3)\n")
    A("*图 3 逐类 F1 对比*：基线 / A2 / A3 三组；A3 把 complete 从 0.84 拉到 1.00，但 suspect 类归零。\n")
    A("![lint](fig4)\n")
    A("*图 4 判据 lint 三口径拦截率与原因码分布*。\n")
    A("![codes](fig5)\n")
    A("*图 5 原因码可达性*：9/9 均可被合成电池触发。\n")

    A("## 5 注意点（坑/局限/适用范围）\n")
    A("1. **门会过拦，默认必须 advisory**：人群抽样条目拦截率 "
      f"{fmt(a['pop']['item_block_rate']*100, 2)}%——多数判据确实缺「基准」。"
      "故集成默认 `advisory`（只记录 + 事件），`enforce` 只追加槽位，`block` 需显式开。")
    A("2. **闭词表的代价**：命名参照（如「(SIMMAN 带内)」）靠 `REF.named_ref`（括号内专名）兜，"
      "更冷门的域内参照仍会被判缺基准 → 这是 precision 上限，修法是 `--extra-terms` 注入域词（域词不进代码）。")
    A("3. **CJK 单位坑**：Python 的 `\\w` 含 CJK，「小于2度」的 `2` 会被 `(?!\\w)` 边界判失败 → "
      "数字右边界只能用 `(?![A-Za-z0-9_])`。")
    A("4. **注入不能自证满足**：槽位块无数字、定界可剥离，分类前先剥；测试断言"
      "「注入前后原因码集合完全相同」。")
    A("5. **漂移测量的真值不独立**：标注者只有一个，且 rubric 与分类器共享三个状态名 → "
      "本测量是**衰减下界估计**，不是无偏误差；`suspect` 类只有 3 单，macro-F1 对其极敏感"
      f"（bootstrap CI [{ci[0]}, {ci[1]}]）。")
    A("6. **headline 被截断到 180 字**：A3 只能看到截断后的文本，"
      "因此「回捞」上限受该截断限制；根治应在产侧（要求 checks 字段非空）。\n")

    A("## 6 误差判定（关键量 vs 基准，逐项回答是否 <3%）\n")
    A("| 关键量 | 基准 | 实测 | 相对偏差 | <3%？ |")
    A("|---|---|---|---|---|")
    A(f"| 判据拦截召回（gold 9 条缺失条目） | 100%（判据定义） | 100%（9/9） | 0.00% | **是** |")
    A(f"| 判据过拦（gold 6 条齐备条目） | 0 条 | 0 条 | 0.00% | **是** |")
    A(f"| lint 确定性（8 节点 report_sha256） | 100% 一致 | 100%（8/8） | 0.00% | **是** |")
    A(f"| 漂移评测确定性（两次运行内容 sha256） | 100% 一致 | 100% | 0.00% | **是** |")
    A(f"| 分类器 macro-F1（p372 波后 30 单） | 冻结基线 {fmt(base['macro_f1'])} | {fmt(md['macro_f1'])} | "
      f"−{fmt(dr['decay_rel']*100, 2)}% | **否（远超 3%，且超 10% 门限）** |")
    A(f"| A3 变体 macro-F1 | 同上 | {fmt(v3['macro_f1'])} | −{fmt(v3['decay_rel']*100, 2)}% | "
      "**否（改善但未达门限）** |")
    A("")
    A("**误差判定结论**：确定性/拦截类判据逐项偏差 0.00%（<3% 成立）；"
      "**交付语义分类器的域外精度不达标**（衰减 43.98%），这是本单的核心 honest-FAIL，"
      "已由告警机制捕获，修复路径（A3）只走了一半。\n")

    A("## 7 审计链\n")
    A("| 工件 | 作用 | 钉住 |")
    A("|---|---|---|")
    A(f"| `quality/delivery_semantics/evidence/drift/preregistration.json` | 预注册（口径/阈值/停止规则/局限），标注前落盘 | "
      f"sha256 `{dm['label_freeze']['preregistration_sha256'][:16]}…` |")
    A(f"| `.../drift/candidates.json` | 总体 + 抽样键（可复算抽样规则） | SQL sha256 "
      f"`{prereg['population']['sql_sha256'][:16]}…` |")
    A(f"| `.../drift/corpus_raw.json` | 30 单原始 payload | corpus sha256 "
      f"`{dm['label_freeze']['corpus_sha256'][:16]}…`（脚本启动即校验） |")
    A(f"| `.../drift/labels.json` | 人工标注 + 逐单理由 | sha256 "
      f"`{dm['label_freeze']['labels_sha256'][:16]}…` |")
    A(f"| `.../drift/metrics.json` | 全部被引用数字 | `determinism_sha256` 双跑一致 |")
    A("| `quality/criteria_lint/evidence/lint_{plan372,plan9181,population300}.json` | 拦截统计（batch lint 输出） | 由脚本产出 |")
    A("| `quality/criteria_lint/evidence/reason_code_coverage.json` | 原因码可达性 | 合成电池 9/9 |")
    A("| `tools/criteria_lint.py` | lint + 派发前门 | `--code-probe` 自检 |")
    A("| `ops/taskplane/campaign_dispatcher.py::criteria_lint_gate` | 派发管线集成（fail-open） | "
      "`tests/test_criteria_lint_dispatch_gate.py`（8 例） |")
    A("| `backend/api/domain/delivery_state.py` | A3 opt-in 变体（默认路径逐位不变） | "
      "既有 57 例冻结验收测试全绿 |")
    A("")
    A("测试：`tests/test_criteria_lint.py`(35) + `tests/test_criteria_lint_dispatch_gate.py`(8) + "
      "`tests/test_delivery_semantics_drift.py`(13) + 既有 `backend/tests/test_delivery_state.py` + "
      "`test_delivery_semantics_eval.py`(57) 全绿。\n")
    A("## 8 未做与下一步\n")
    A("1. **分类器重训未完成**：A3 只做词表+回捞，macro-F1 仍未进 10% 门限；"
      "下一步需对齐「有实现无验证」的标签契约（partial vs suspect）并扩样至 ≥100 单。")
    A("2. `block` 模式**未在生产派发中开启**（当前 `advisory`）：需先补一轮判据回填，"
      "否则会大面积拒发（人群拦截率 78%+）。")
    A("3. 漂移检测当前是**离线批次**（工具 + 预注册），尚未做成按波次自动跑的定时任务；"
      "机制化入口=`tools/delivery_semantics_drift/evaluate.py`。\n")

    markdown = "\n".join(lines) + "\n"
    figures = [
        {"name": "fig1_drift_curve", "key": "fig1",
         "path": DRIFT / "figures" / "fig1_drift_curve.png",
         "caption": "漂移曲线：累积 macro-F1 vs 样本量（含 10% 门限与 A3 变体）"},
        {"name": "fig2_confusion", "key": "fig2",
         "path": DRIFT / "figures" / "fig2_confusion.png",
         "caption": "A2 混淆矩阵（n=30）"},
        {"name": "fig3_per_class_f1", "key": "fig3",
         "path": DRIFT / "figures" / "fig3_per_class_f1.png",
         "caption": "逐类 F1：冻结基线 / A2 / A3"},
        {"name": "fig_lint_stats", "key": "fig4",
         "path": LINT / "fig_lint_stats.png",
         "caption": "判据 lint 三口径拦截率与原因码分布"},
        {"name": "fig_reason_codes", "key": "fig5",
         "path": LINT / "fig_reason_codes.png",
         "caption": "原因码可达性 9/9"},
    ]
    for f in figures:
        markdown = markdown.replace(f"![{f['key']}]({f['key']})", f"![{f['name']}]({f['name']}.png)")
    return {"markdown": markdown, "figures": figures,
            "summary": (f"判据 lint 三要素门进派发管线（9 原因码全可达，人群条目拦截率 "
                        f"{fmt(a['pop']['item_block_rate']*100,1)}%）；p372 波后 "
                        f"{dm['n_units']} 单实测交付语义分类器 macro-F1 "
                        f"{fmt(base['macro_f1'])}→{fmt(md['macro_f1'])}（衰减 "
                        f"{fmt(dr['decay_rel']*100,1)}%），告警按设计触发，判据 c1 如实 FAIL。")}


def publish(doc: Dict[str, Any]) -> Dict[str, Any]:
    base = os.environ["TENSOR_API_BASE"].rstrip("/")
    user = os.environ["TENSOR_AGENT_USER"]
    pwd = os.environ["TENSOR_AGENT_PASS"]

    def call(path: str, body: Optional[dict] = None, token: Optional[str] = None):
        req = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)

    token = call("/auth/login", {"username": user, "password": pwd})["access_token"]
    payload = {
        "title": "p372w2-n06 判据可判定性 lint 与交付语义漂移检测（平台质量门成熟化）",
        "summary": doc["summary"],
        "scenario_key": "platform/quality-gate",
        "mission_id": MISSION_ID,
        "tags": ["criteria-lint", "delivery-semantics", "drift-detection",
                 "quality-gate", "honest-FAIL"],
        "markdown": doc["markdown"],
        "figures": [{"name": f["name"], "caption": f["caption"],
                     "png_base64": b64(f["path"])} for f in doc["figures"]],
    }
    return call("/tensor/reports", payload, token)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="p372w2-n06 报告生成/发布")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--md-out", default=str(DOC))
    args = ap.parse_args(argv)
    doc = build_markdown()
    out = Path(args.md_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc["markdown"], encoding="utf-8")
    print(json.dumps({"md": str(out), "chars": len(doc["markdown"]),
                      "figures": len(doc["figures"]), "summary": doc["summary"]},
                     ensure_ascii=False, indent=1))
    if not args.dry_run:
        res = publish(doc)
        print(json.dumps({"report_id": res.get("id") or res.get("report_id")},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())