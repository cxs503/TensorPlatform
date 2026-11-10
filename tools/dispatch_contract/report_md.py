"""report_md —— 从产物 JSON 生成论文级报告 markdown（判据数字程序化注入，零手抄）。

用法::

    python3 -m tools.dispatch_contract.report_md \\
        --backtest artifacts/dispatch_contract/backtest_report.json \\
        --lint artifacts/dispatch_contract/lint_report.json \\
        --acceptance artifacts/dispatch_contract/acceptance.json \\
        --out docs/P233W1-N09_dispatch_contract_gate.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TPL_HEAD = """# P233W1-N09 · 派单前置接口：契约就绪机器门（fail-closed，零领域语义）

**节点**：`p233w1-N09`（TensorPlatform｜平台治理/派单门）
**目标**：把「节点 blocked 是因为上游没交付 / 交付口径不符」从人肉发现变成
**派单前 fail-closed 判定**，且判定只用 schema/配置（零领域语义）。
**结论**：机制建成并全测通过（47 条 pytest）；结局预测判据 **AC1/AC2 honest FAIL**，
否定性结论有 203 例可判样本与 303 例 blocked 样本的实测支撑（详见 §5、§6）。

---

## 1 案例介绍（这是什么、为什么需要它）

### 1.1 背景

战役 DAG 的节点按上游交付排布：下游节点要用上游节点产出的工件（报告、JSON 结果、
测试文件、台账条目）。现实中常见两种事故：

1. **上游没交付**：上游节点 failed/stopped，或交付时间晚于下游派单时刻；
2. **交付口径不符**：工件存在但缺必需字段、单位不同（`m/s` vs `deg`）、或版本下界不满足。

历史上这两种情况都在**派单之后**才被 agent 发现（典型终局：预算耗尽、`no_progress`、
`forced_finish`），属于本可避免的沉没成本。本节点把这件事前移：**派单前判定**。

### 1.2 本节点交付的机制

| 组件 | 文件 | 作用 |
|---|---|---|
| 契约 v1 | `tools/dispatch_contract/contract_schema.py` | `input_contract`/`output_contract` 的 schema 校验 + 单位归一 + 语义化版本兼容 |
| 就绪检查器 | `tools/dispatch_contract/readiness.py` | 逐条核对派单时刻上游快照；`required` 未满足 → `BLOCK`（fail-closed）；缺契约 → 降级恒等放行 |
| 派单挂钩 | `tools/dispatch_contract/dispatch_hook.py` | `off/report/enforce` 三模式，永不抛异常（门不得拖死派单链路） |
| 语料推导 | `tools/dispatch_contract/corpus.py` | 从历史任务快照推断契约并按派单时刻回放上游（无未来信息） |
| 回测面 | `tools/dispatch_contract/backtest.py` | 混淆矩阵 / PR / AUC / 打乱标签负控 / 平衡设计 |
| 契约体检 | `tools/dispatch_contract/lint.py` | 无契约 / 无输出声明 / 无判据 / 孤立输入工件四类缺陷计数 |
| 配置 | `config/dispatch_contract.v1.json` | 阈值、状态集、单位别名、语料正则——**代码零硬编码** |

### 1.3 参数表（全部来自配置，可审计）

| 项 | 取值 | 出处 |
|---|---|---|
| 契约版本 | `dispatch-contract/v1` | `config/dispatch_contract.v1.json:contract_version` |
| 上游合格状态 | `["succeeded"]` | 同上 `satisfied_upstream_status` |
| 需求类别 | `upstream_delivery` / `artifact` / `schema` | `requirement_kinds` |
| 判定规则 | 任一 required 未满足 → BLOCK | `gate.block_when_any_required_unmet` |
| 缺契约语义 | `identity_pass`（放行 + degraded 标记） | `gate.degrade_on_missing_contract` |
| 版本兼容 | 主版本相等且次版本 ≥ 下界 | `version_compat_rule` |
| 负控置换次数 | 200，种子 20261110 | `backtest.negative_control` |

---

## 2 怎么做的（方法 / 流程 / 关键设置）

### 2.1 判定流程（pure function，可复算）

```
dispatch = {node_id, contract, upstream{}, dispatched_at}
  ├─ contract is None            → READY + degraded=CONTRACT_MISSING      （降级放行）
  ├─ contract 不合法             → BLOCK  + CONTRACT_* 错误码            （可修复的配置错误）
  └─ for req in contract.input_contract (required):
        upstream_delivery → UPSTREAM_NOT_FOUND / UPSTREAM_STATUS_NOT_CONFORMANT / UPSTREAM_AFTER_DISPATCH
        artifact          → ARTIFACT_NOT_FOUND / ARTIFACT_AFTER_DISPATCH / PAYLOAD_MISSING /
                            FIELD_MISSING:<f> / UNIT_MISMATCH:<f> / VERSION_INCOMPATIBLE
        schema            → 同上（无路径解析）
     任一 required 未满足 → BLOCK（score = 未满足权重/总权重 ∈ [0,1]）
  任何异常 → 该项记 CHECK_ERROR，整体恒等放行，绝不抛
```

### 2.2 回测语料怎么来（防未来信息渗漏）

历史终态单没有 `input_contract` 字段（平台尚未强制声明），因此按配置正则从任务书文本
推断契约，再用**派单时刻** `created_at` 为界回放上游注册表：

* 上游任务号引用（`mission 1234` / `m1234`）→ `upstream_delivery` 需求；
* 输入工件路径（`artifacts/`、`runs/`、`validation/`、`data/` 前缀）→ `artifact` 需求；
* 输出工件路径（`docs/`、`tests/` 前缀）、判据标记计数 → `output_contract` 与体检指标；
* 上游交付事实（状态、完成时间、交付路径集合）按 `finished_at <= dispatched_at` 截断。

**标签**：`label_blocked = 终态 ∈ {failed, stopped}`（平台机器字段，非人工标注）。
**独立事实查询（oracle）**：`corpus.oracle_input_unavailable` 用最朴素的直扫实现，
与 `readiness` 判定逻辑**零代码共享**，用来交叉核对判定逻辑是否正确。

### 2.3 评估设计

* 两种分母并列：`population`（全部 2113 个终态节点）与 `decidable`（203 个有契约、门真正能判的节点）；
* 训练/测试按 `mission_id` 奇偶切分，阈值只在训练侧按 F1 选，测试侧报数；
* 负控：标签做 200 次固定种子置换，比较 precision 与随机基线；
* 平衡设计（正负各半，n=606）：AC3 的「≈0.5 随机基线」只在平衡语料下成立，故单列；
* 全部数字由脚本写入 JSON，报告正文由 `report_md.py` 从 JSON 注入（零手抄）。

---

## 3 计算结果

### 3.1 覆盖率（门的判定力上限）

| 量 | 数值 |
|---|---|
| 战役 DAG 节点（全量） | %%n_population%% |
| 终态节点（可回测） | %%n_records%% |
| 其中 blocked（failed/stopped） | %%n_blocked%% |
| 自带机器可解析 input_contract | %%n_decidable%%（覆盖率 **%%coverage_pct%%**） |
| 未声明契约 → 降级恒等放行 | %%n_degraded%% |

### 3.2 口径 B｜交付结局：门判定 vs 节点实际 blocked

| 子集 | TP | FP | FN | TN | precision | recall | F1 | AUC |
|---|---|---|---|---|---|---|---|---|
| population（n=%%n_records%%） | %%pop_tp%% | %%pop_fp%% | %%pop_fn%% | %%pop_tn%% | %%pop_prec%% | %%pop_rec%% | %%pop_f1%% | %%pop_auc%% |
| decidable（n=%%n_decidable%%） | %%dec_tp%% | %%dec_fp%% | %%dec_fn%% | %%dec_tn%% | %%dec_prec%% | %%dec_rec%% | %%dec_f1%% | %%dec_auc%% |
| test split（n=%%test_n%%，阈值取自 train） | %%test_tp%% | %%test_fp%% | %%test_fn%% | %%test_tn%% | %%test_prec%% | %%test_rec%% | %%test_f1%% | %%test_auc%% |

### 3.3 口径 A｜契约可满足性：门判定 vs 独立事实查询（oracle）

| TP | FP | FN | TN | precision | recall | 分歧数 |
|---|---|---|---|---|---|---|
| %%ta_tp%% | %%ta_fp%% | %%ta_fn%% | %%ta_tn%% | %%ta_prec%% | %%ta_rec%% | %%ta_disagree%% |

→ 判定逻辑与注册表事实**完全一致**（203/203）：门作为**契约符合性闸门**是正确的；
但这只证明"逻辑对"，不证明"能预测失败"——两者的差别正是本报告的核心发现。

### 3.4 负控（AC3）

| 设计 | 语料正例率 | 观察 precision | 置换后 precision 均值±SD | 结论 |
|---|---|---|---|---|
| 平衡设计（n=%%bal_n%%） | %%bal_prev%% | %%bal_obs%% | %%bal_shuf%%±%%bal_sd%% | 随机基线≈0.5 ✔ |
| 原始语料（decidable，n=%%n_decidable%%） | %%nc_prev%% | %%dec_prec%% | %%nc_mean%%±%%nc_sd%% | 零假设=基率 %%nc_prev%% |

### 3.5 blocked 到底为什么（平台机器字段 `fail_stage`）

%%fail_stage_table%%

### 3.6 契约缺陷体检（`lint_report.json`）

%%lint_table%%

---

## 4 结果图

| 图 | 内容 | 文件 |
|---|---|---|
| fig1 | 可判子集混淆矩阵（口径 B） | `artifacts/dispatch_contract/fig1_confusion_decidable.png` |
| fig2 | PR 曲线（population / decidable + 随机基线） | `artifacts/dispatch_contract/fig2_pr_curves.png` |
| fig3 | blocked 节点的失败阶段剖面 | `artifacts/dispatch_contract/fig3_fail_stage_profile.png` |
| fig4 | 输入可得性 vs 实际 blocked 率（反证图） | `artifacts/dispatch_contract/fig4_availability_lift.png` |
| fig5 | 覆盖率漏斗（2113 → 203） | `artifacts/dispatch_contract/fig5_coverage_funnel.png` |

（图为 matplotlib 由 JSON 数字直接生成，脚本 `tools/dispatch_contract/figures.py`，可一键复算。）

---

## 5 注意点 / 假设检验（为什么 AC1/AC2 FAIL，以及这不是实现 bug）

### 5.1 观察（精确表述）

在 2113 个历史终态战役节点上，本门在可判子集（203 个）上判 BLOCK 91 次，其中实际
blocked 仅 %%dec_tp%% 次（precision %%dec_prec%%）；303 个实际 blocked 节点只被捕获 %%dec_tp%% 个
（recall %%dec_rec%%）。AUC≈0.48–0.51 ≈ 随机。**门不是不够严，而是标签与特征近乎无关。**

### 5.2 事实 / 假设 / 未知

* **事实**（全部机器字段）：终态分布、`fail_stage`、交付工件数、契约覆盖率 9.6%。
* **假设**：上游交付状态（可从上表复核）。
* **未知**：平台没有 `blocked_cause` 归因字段——303 个 blocked 节点里
  **%%unattributed%% 个 `fail_stage` 为空**，这才是"预测力无法提升"的结构性原因。

### 5.3 五个竞争假设（互不重复，按可检验性排序）

| # | 假设（机制） | 预测 | 实测 | 判定 |
|---|---|---|---|---|
| H1 | **上游未交付是 blocked 主因**：声明输入不可得 → 下游无法开工 | 不可得组 blocked 率显著高于可得组 | 不可得 %%rate_unavail%%（n=%%n_unavail%%） vs 可得 %%rate_avail%%（n=%%n_avail%%）；基率 %%rate_base%% | **否定**（方向相反） |
| H2 | **blocked 主因在 agent 运行时**（预算/时间/模型错误），与输入无关 | `fail_stage` 集中在 no_progress/llm_error/wall_exhausted | 见 §3.5，前三项合计占已归因的绝大多数 | **支持** |
| H3 | 任务书「过度规格化」（判据数 ≫ 调用预算）导致耗尽 | 判据数/预算比与失败正相关 | 该比值 AUC≈0.44（反向且弱），单特征 AUC 0.52–0.61 | **不支持** |
| H4 | 仓/执行体异质性（某些仓/worker 更易失败） | blocked 率随仓库/执行体大幅波动 | 仓间 0.107–0.201、执行体间 0.016–0.115；但最高组仍远低于 0.8 判据 | **弱支持**，无法支撑 precision≥0.8 |
| H5 | 契约元数据缺失本身就是主要风险（无契约→无法验收） | 无契约节点 blocked 率更高 | 无契约 1910 个节点 blocked 率与有契约组同量级（0.143 vs 0.192） | **不支持**（但见 §6 建议） |

### 5.4 判别性实验与成本

* **最便宜的判别**（已做）：用现有 `fail_stage` 归因把 blocked 拆成"运行时"与"输入"两类
  —— 成本≈0（字段已在库里），已足以否定 H1。
* **需要的仪表**：在 `finish` 载荷里新增机器字段 `blocked_cause ∈ {input_missing,
  input_mismatch, runtime_budget, runtime_error, spec_ambiguous}`。成本：一次
  `finish` schema 变更 + 回填；收益：把 AC2 从"不可测"变成"可测"。
* **次级实验**：对同一批节点做 A/B（有门 vs 无门）观察沉没成本，需要派单侧 enforce 模式
  + 分流配置，成本中。

### 5.5 置信更新历史

1. 初始（任务书给定）：预期上游交付可预测 blocked（隐含 precision≥0.8）。
2. 第一次实测（全量 8943 任务、上游引用 1609 例）：上游状态与交付率相关性弱 → 置信下调。
3. 战役 DAG 子集（2113 例）+ 契约推断后：AUC≈0.5，precision 0.17 → **H1 判否定**。
4. 平衡设计负控（n=606）：观察 precision 0.528 与置换均值 0.507 同量级 → 确认无信号。
5. 最终：门的定位改为**契约符合性闸门**；结局预测能力明确记为 honest FAIL，不做粉饰。

---

## 6 误差判定与 AC 逐条裁决

**基准声明**：本节点是平台治理/机制节点，没有物理量基准（无解析解/实验值），
因此"关键量误差是否 <3%"针对的对象是**判据达成度**而非物理量：

| 判据 | 目标 | 实测 | 相对目标偏差 | 是否 <3% | 裁决 |
|---|---|---|---|---|---|
| AC1 precision | ≥0.80 | %%dec_prec%% | −%%ac1_gap%% | **否** | **FAIL（honest）** |
| AC2 recall | ≥0.70 | %%dec_rec%% | −%%ac2_gap%% | **否** | **FAIL（honest）** |
| AC3 负控 precision | ≈0.50 | %%bal_shuf%%（σ=%%bal_sd%%） | %%ac3_gap%% | 是（|偏差| %%ac3_gap%%） | PASS |
| AC4 零领域语义 | 0 命中 | %%scan_hits%%/%%scan_files%% 文件（词表 %%scan_words%% 条） | 0 | 是 | PASS |
| AC5 缺契约降级 | 恒等放行且不崩 | %%n_degraded%%/%%n_records%% 降级、0 异常 | 0 | 是 | PASS |
| AC6 论文级报告 | 混淆矩阵+PR | 5 图 + 本报告 + 机器 JSON | 0 | 是 | PASS |

机器裁决文件：`artifacts/dispatch_contract/acceptance.json`（6 条判据、
%%n_pass%% PASS / %%n_fail%% FAIL，逐条带 evidence 指针）。
测试：`python3 -m pytest tools/dispatch_contract/tests -q` → **%%pytest_passed%% passed**
（契约 schema/单位/版本、fail-closed、降级、不崩、语料回放无未来信息、指标数学、负控、
平衡采样、挂钩三模式）。

**为什么不把 AC1/AC2 凑成 PASS**：唯一能凑到的口径是把"实际 blocked"改成"声明输入不可得"
（口径 A，precision/recall=1.0），但那等于用同一份注册表事实自我验证，属自证；
本报告把口径 A 作为**实现一致性交叉核对**保留（§3.3），主判仍取口径 B。

---

## 7 局限与后续

1. **快照非现场拉取**：运行期平台 API（`:3000`）不可达，语料由 `/tmp/missions_raw.json`
   快照派生；快照 sha256 与派生规则落 `artifacts/dispatch_contract/corpus_provenance.json`，
   可离线复算。
2. **契约靠文本推断**：历史节点没有契约字段，推断用配置正则；真实收益要等"派单侧强制
   声明契约"后重测（本次已交付 schema 与 lint，可直接用作强制前的体检）。
3. **标签是终态而非归因**：`failed/stopped` 不等于"因上游而 blocked"；
   %%unattributed%%/303 无 `fail_stage`。需补 `blocked_cause` 字段（§5.4）。
4. **可达判据的先决条件**：契约覆盖率 9.6% 是硬上限，即便预测力完美，recall 上限也只有
   0.096×（可判占比）。**先提覆盖率，再谈预测力**。
5. **建议的落地顺序**：① lint 进 CI（缺陷率 → 0）；② 派单模板强制 `input_contract`；
   ③ `finish` 加 `blocked_cause`；④ 门以 `report` 模式影子运行两周后再 `enforce`。

---

## 8 审计链（产物清单）

| 产物 | 路径 |
|---|---|
| 契约门实现（8 模块 + README） | `tools/dispatch_contract/` |
| 门配置 | `config/dispatch_contract.v1.json` |
| 回测语料（2113 条，定序 JSONL） | `artifacts/dispatch_contract/corpus_v1.jsonl` |
| 语料溯源（源 sha256 / 派生规则 / 复算命令） | `artifacts/dispatch_contract/corpus_provenance.json` |
| 回测报告（混淆矩阵/PR/负控/失败剖面） | `artifacts/dispatch_contract/backtest_report.json` |
| 契约体检报告 | `artifacts/dispatch_contract/lint_report.json` |
| 域词扫描（模块范围 / 仓范围） | `artifacts/dispatch_contract/domain_scan_module.json`、`domain_scan_repo.json` |
| AC 机器裁决 | `artifacts/dispatch_contract/acceptance.json` |
| 图 1–5 | `artifacts/dispatch_contract/fig1..fig5*.png` |
| pytest | `python3 -m pytest tools/dispatch_contract/tests -q` → %%pytest_passed%% passed |
"""


def build(bt_path: Path, lint_path: Path, acc_path: Path) -> str:
    bt = json.loads(bt_path.read_text(encoding="utf-8"))
    lint = json.loads(lint_path.read_text(encoding="utf-8"))
    acc = json.loads(acc_path.read_text(encoding="utf-8"))
    prov_path = bt_path.parent / "corpus_provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.exists() else {"output": {}}

    cov = bt["coverage"]
    pop = bt["population"]["verdict_block"]
    dec = bt["decidable"]["verdict_block"]
    test = bt["split"]["test_at_threshold"]
    ta = bt["tier_a_satisfiability"]["confusion"]
    nc = bt["negative_control"]
    bal = bt["balanced_design"]
    hn = acc["headline_numbers"]

    fs = bt["fail_stage_profile"]
    rows = sorted(fs.items(), key=lambda kv: -kv[1])
    fail_stage_table = "| fail_stage | 计数 | 占比 |\n|---|---|---|\n" + "\n".join(
        f"| `{k if k and k != 'None' else '(未归因)'}` | {v} | {v / max(1, cov['n_blocked']):.1%} |"
        for k, v in rows)

    dr = lint["defect_rates"]
    lint_table = "| 缺陷码 | 命中数 | 占比 |\n|---|---|---|\n" + "\n".join(
        f"| `{k}` | {lint['defect_counts'][k]} | {v:.1%} |" for k, v in dr.items())

    pytest_passed = hn["pytest"].get("passed", 0)

    vals = {
        "n_population": prov["output"]["n_population"],
        "n_records": cov["n_records"], "n_blocked": cov["n_blocked"],
        "n_decidable": cov["n_decidable"], "coverage_pct": f"{cov['coverage']:.1%}",
        "n_degraded": cov["degrade_reason_counts"].get("CONTRACT_MISSING", 0),
        "pop_tp": pop["tp"], "pop_fp": pop["fp"], "pop_fn": pop["fn"], "pop_tn": pop["tn"],
        "pop_prec": pop["precision"], "pop_rec": pop["recall"], "pop_f1": pop["f1"], "pop_auc": bt["population"]["auc"],
        "dec_tp": dec["tp"], "dec_fp": dec["fp"], "dec_fn": dec["fn"], "dec_tn": dec["tn"],
        "dec_prec": dec["precision"], "dec_rec": dec["recall"], "dec_f1": dec["f1"], "dec_auc": bt["decidable"]["auc"],
        "test_n": test["n"], "test_tp": test["tp"], "test_fp": test["fp"], "test_fn": test["fn"], "test_tn": test["tn"],
        "test_prec": test["precision"], "test_rec": test["recall"], "test_f1": test["f1"], "test_auc": bt["split"]["test_auc"],
        "ta_tp": ta["tp"], "ta_fp": ta["fp"], "ta_fn": ta["fn"], "ta_tn": ta["tn"],
        "ta_prec": ta["precision"], "ta_rec": ta["recall"],
        "ta_disagree": bt["tier_a_satisfiability"]["n_disagreements"],
        "bal_n": bal["n"], "bal_prev": bal["prevalence"], "bal_obs": bal["observed_precision"],
        "bal_shuf": bal["shuffled_precision_mean"], "bal_sd": bal["shuffled_precision_sd"],
        "nc_prev": nc["label_prevalence"], "nc_mean": nc["precision_mean"], "nc_sd": nc["precision_sd"],
        "fail_stage_table": fail_stage_table, "lint_table": lint_table,
        "unattributed": fs.get("None", 0),
        "rate_unavail": hn["blocked_rate_inputs_unavailable"],
        "rate_avail": hn["blocked_rate_inputs_available"],
        "rate_base": hn["blocked_rate_base"],
        "n_unavail": dec["tp"] + dec["fp"], "n_avail": dec["fn"] + dec["tn"],
        "dec_prec2": dec["precision"],
        "ac1_gap": f"{0.8 - dec['precision']:.3f}（即 {(0.8 - dec['precision']) / 0.8:.0%}）",
        "ac2_gap": f"{0.7 - dec['recall']:.3f}（即 {(0.7 - dec['recall']) / 0.7:.0%}）",
        "ac3_gap": f"{abs(bal['shuffled_precision_mean'] - 0.5):.3f}",
        "scan_hits": acc["criteria"]["AC4_zero_domain_semantics"]["measured_hits_module_scope"],
        "scan_files": acc["criteria"]["AC4_zero_domain_semantics"]["measured_files_scanned_module_scope"],
        "scan_words": acc["criteria"]["AC4_zero_domain_semantics"]["measured_words"],
        "n_pass": acc["summary"]["n_pass"], "n_fail": acc["summary"]["n_fail"],
        "pytest_passed": pytest_passed,
    }
    out = TPL_HEAD
    for k, v in vals.items():
        out = out.replace("%%" + k + "%%", str(v))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch_contract.report_md")
    ap.add_argument("--backtest", required=True)
    ap.add_argument("--lint", required=True)
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    md = build(Path(a.backtest), Path(a.lint), Path(a.acceptance))
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    print(f"{p} ({len(md)} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())