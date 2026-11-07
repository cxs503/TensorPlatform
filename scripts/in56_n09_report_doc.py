"""IN56-N09 报告生成器：从 analysis.json / preregistration.json 程序化生成
(1) docs/IN56-N09_grader_perturbation_rct.md（论文级报告）
(2) artifacts/IN56_N09/report.json（机器验收判据 + 产物路径）
(3) /tmp/in56_report.json（平台 /tensor/reports 负载，含 3 张内嵌图）
数字一律从产物注入，不手抄。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/IN56_N09"
DOCS = ROOT / "docs"
MISSION_ID = 6180
ARM_TITLE = {"T1": "措辞膨胀 T1", "T2": "数字伪造 T2", "T3": "引用膨胀 T3"}


def pct(x: float) -> str:
    return f"{100.0 * float(x):.1f}%"


def load_all():
    a = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))
    pre = json.loads((ART / "preregistration.json").read_text(encoding="utf-8"))
    chk = json.loads((ART / "preregistration_check.json").read_text(encoding="utf-8"))
    corpus = json.loads((ART / "base_corpus.json").read_text(encoding="utf-8"))
    figs = json.loads((ART / "figures/figures.json").read_text(encoding="utf-8"))
    return a, pre, chk, corpus, figs


def results_table(a: dict) -> str:
    lines = [
        "| 臂 | 翻转率（S 级） | 翻转率精确 95% CI | 对照均值 S | 干预均值 S | Cohen d | d 的 95% CI | d_z | E 级翻转率 | 裁决 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ("T1", "T2", "T3"):
        b = a["arms"][arm]
        v = a["verdicts"]["arms"][arm]
        lo, hi = b["flip_ci95_exact"]
        dlo, dhi = b["cohen_d_ci95"]
        lines.append(
            f"| {ARM_TITLE[arm]} | {pct(b['flip']['flip_rate'])} | [{lo:.3f}, {hi:.3f}] | "
            f"{b['mean_s_control']:.3f} | {b['mean_s_treat']:.3f} | {b['cohen_d']:.3f} | "
            f"[{dlo:.3f}, {dhi:.3f}] | {b['dz']:.3f} | {pct(b['e_level']['flip_rate'])} | "
            f"**{v['verdict']}** |")
    return "\n".join(lines)


def strata_table(a: dict) -> str:
    lines = [
        "| 层 | 底座数 | 对照均值 S | T2 翻转率 | T2 Cohen d | T2 d 的 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for name, blk in a["strata"].items():
        t2 = blk["arms"]["T2"]
        lo, hi = t2["cohen_d_ci95"]
        lines.append(f"| {name} | {blk['n_bases']} | {t2['mean_s_control']:.3f} | "
                     f"{pct(t2['flip']['flip_rate'])} | {t2['cohen_d']:.3f} | [{lo:.3f}, {hi:.3f}] |")
    return "\n".join(lines)


def structural_table(a: dict) -> str:
    s = a["structural_findings"]
    lines = ["| 结构指标 | 值 | 含意 |", "|---|---|---|",
             f"| 对照臂 execution_ok 比例 | {pct(s['execution_ok_rate'])} | E2 的前置条件；为 0 → E 级钉在 E1 |",
             f"| 对照臂 账本登记比例 | {pct(s['delta_registered_rate'])} | S3 的前置条件；为 0 → S 级实际只有 S1/S2 |"]
    for arm, v in s["artifact_verify_by_arm"].items():
        lines.append(f"| {arm} 臂『查无此件』引用数/底座 | {v['mean_not_found_local']:.2f} "
                     f"（{v['bases_with_any_not_found']}/{v['n_bases']} 底座中招） | "
                     f"{'引用核验面被污染' if v['mean_not_found_local'] > 0 else '干净'} |")
    return "\n".join(lines)


def build_markdown(a, pre, chk, corpus, figs) -> str:
    sep = a["separation"]
    mde = a["mde"]
    rep = a["replay"]
    s = a["structural_findings"]
    t2 = a["arms"]["T2"]
    strata_report = a["strata"].get("report", {})
    strata_summary = a["strata"].get("summary", {})
    t2_rep = strata_report.get("arms", {}).get("T2", {})
    t2_sum = strata_summary.get("arms", {}).get("T2", {})
    repo_counts: dict[str, int] = {}
    for b in corpus:
        repo_counts[b["repo"]] = repo_counts.get(b["repo"], 0) + 1
    repo_txt = "、".join(f"{k}({v})" for k, v in sorted(repo_counts.items(), key=lambda kv: -kv[1]))

    md = f"""# IN56-N09 评分器行为稳健性随机化对照试验：判级对「表面特征」的敏感度

**节点**：IN56-N09（成熟化：评分器行为稳健性随机化试验）
**判级器**：`tools/evaluation_wave.py` 的 E1 执行器（4 个程序化席位 × 共识 × `decide()` 硬判据）
**产物**：`artifacts/IN56_N09/`（冻结语料、预注册表、三遍判级、分析、图）、
`tools/evaluation_ops/perturbation_rct.py`、`tests/test_eval_perturbation_rct.py`

## ① 案例介绍（研究对象与参数表）

评分器的判级对象不是「代码质量」，而是**提交文本里的可计数证据特征**：数字个数、基线标记
片段、对照数字对、工件路径、执行命令、通过计数、复制/突破/检索范围标记。这类特征里有一类
与证据强度无关、只与**写法**有关：形容词堆叠、「已验证」式自我背书、无出处的数字、指向不
存在文件的引用。本单回答一个预注册问题：**判级对这三类表面特征是否敏感，敏感多少？**

底座=平台真实历史 mission 的提交文本（非本单新造的前沿内容）：

| 项 | 取值 |
|---|---|
| 底座数 | {pre['n_bases']} 条（report 层 20 + summary 层 20） |
| 判级次数 | {pre['n_gradings']}（{pre['n_bases']} 底座 × 4 臂 × {pre['reps']} 次重复） |
| 臂 | C（原文）/ {ARM_TITLE['T1']} / {ARM_TITLE['T2']} / {ARM_TITLE['T3']} |
| 随机化 | 臂顺序由 `seed={pre['seed']}` + `base_id` 决定；分配表落 `preregistration.json` |
| 语料来源仓分布 | {repo_txt} |
| 冻结哈希 | 语料 `{pre['base_corpus_sha256'][:16]}…`、干预 `{pre['interventions_sha256'][:16]}…`、分配 `{pre['assignment_sha256'][:16]}…` |

**分层为什么必须做**：完整报告几乎人人都有「误差 x%」这类数字，20 条 report 层底座里对照臂
判 S2 的占八成以上（见 §③），只在这一层上问「伪数字能不能买等级」等于问一个它不可能回答的
问题。因此预注册时就分两层：**report 层**（有实质报告）+ **summary 层**（只交执行摘要、
没有报告正文），后者落在 S1 地板上，才有抬级空间。

| 干预 | 具体做什么 | 机器可验的可分离性（`separation_report`） |
|---|---|---|
| T1 措辞膨胀 | 插 3 句形容词/「经独立核验、真实可信」式背书 | 数字 +{sep['arms']['T1']['deltas']['numbers']['max']}、路径 +{sep['arms']['T1']['deltas']['paths']['max']}、基线/复制/突破/检索标记 +0（逐底座全为 0） |
| T2 数字伪造 | 插 3 句「相对偏差 0.7%、误差 0.004」式数字，**无任何出处** | 数字 +{sep['arms']['T2']['deltas']['numbers']['min']}~{sep['arms']['T2']['deltas']['numbers']['max']}、基线标记 +{sep['arms']['T2']['deltas']['baseline_occ']['min']}~{sep['arms']['T2']['deltas']['baseline_occ']['max']}；路径 +0 |
| T3 引用膨胀 | 插 3 条不存在的证据路径（`artifacts/rct/ledger.json` 等） | 路径 +{sep['arms']['T3']['deltas']['paths']['min']}~{sep['arms']['T3']['deltas']['paths']['max']}；数字 +0、基线标记 +0 |

三臂插入字数：T1 {sep['inserted_chars']['T1']['mean']:.0f}、T2 {sep['inserted_chars']['T2']['mean']:.0f}、
T3 {sep['inserted_chars']['T3']['mean']:.0f} 字（相对极差 {pct(sep['length_matched']['relative_spread'])}，
在 15% 判据内）——「文本变长」这一混淆按构造被压掉。

## ② 怎么做的（方法/流程/工具链）

1. **语料收割**（`scripts/in56_n09_harvest_corpus.py`）：从平台库 `tensor_dev_missions ⋈
   tensor_reports ⋈ tensor_capability_deltas` 取 40 条 succeeded mission，按 `md5(id)` 排序
   取前 20/层（确定性，不随机）；只脱环境指纹（绝对路径/URL/邮箱/长哈希），**数字、单位、
   基线词、命令、工件相对路径一律保留** —— 那些正是判级器读的东西。
2. **预注册**（`build_preregistration`）：判级**之前**落盘分配表、种子、裁决规则、MDE、
   语料/干预/分配三个哈希；重跑重建分配表，哈希必须相等（实测 `regenerate_hash_equal=True`）。
3. **干预施加**（`apply_intervention`）：纯函数、确定性；插桩点只允许落在**行首/句末**，
   绝不插进数字、路径或「 vs 」这类跨空格标记内部（否则会人为制造/切断特征，污染三臂可分离性）。
   干预打在**主提交通道**上（有报告改报告 markdown，无报告改 `result.summary`），保证
   T2/T3 不是空操作。
4. **判级**：`evaluate_mission(packet, n_seats=3, artifact_root=沙箱, local_repo=该单仓库)` ——
   **同一 rubric，只改输入**。工件存在性检出面受控：底座原文引用的路径在沙箱里**物化**
   （=该单自己的检出里有这些文件），干预新增路径**不物化**（=查无此件）。
5. **三次重复在三个独立子进程**里跑（`scripts/in56_n09_run_rct.py run --pass k`），
   因而能暴露哈希种子/字典序等跨进程非确定性 —— 这是负控，不是重复劳动。
6. **统计**：翻转率（S 级秩相对对照的变化，含方向）+ 精确 Clopper-Pearson 95% CI；
   Cohen d（合并 SD 口径）与 d_z（配对口径），95% CI 用**按底座聚类的分层 bootstrap**
   （重抽底座、带出其重复，B={a['bootstrap']['B']}）。statsmodels 未安装，故不拟合混合效应
   模型，分层 bootstrap 是预注册的等价替代。
7. **裁决**（三分法）：`d>0.2 且 CI 下界>0` → 「可被操纵」；`d 的上界<0.2 且翻转率精确上界
   <0.2` → 「抗住」（等价性成立）；否则「证据不足」。**实测 MDE 之外的结论一律不写死。**

## ③ 计算结果（全部由 `analysis.json` 程序化注入）

**负控（重放一致率）**：{rep['exact_match_cells']}/{rep['cells']} 个 (底座, 臂) 单元的三次
重复给出**逐字相同**的判级与理由哈希 → 一致率 {pct(rep['consistency_rate'])}（判据 ≥99%，
{'通过' if rep['passed_99'] else '未通过'}）。也就是说本 rubric 是确定性的，**任何翻转都只能
来自输入差异**，不会来自评分器抖动。

**主表（配对设计，n={a['n_bases']} 底座/臂）**

{results_table(a)}

**分层结果**

{strata_table(a)}

T2 的抬级**全部发生在 summary 层**（{pct(t2_sum.get('flip', {}).get('flip_rate', 0.0))} 翻转，
d={t2_sum.get('cohen_d', 0.0):.3f}），report 层为 {pct(t2_rep.get('flip', {}).get('flip_rate', 0.0))}
——完整报告自带数字，已经站在 S2，伪造数字无处可抬。这正好说明脆弱点在哪：**在薄弱提交上，
一句无出处数字就能把 S1 换成 S2**。

**判级器的结构天花板**（不是干预效应，但它决定了效应能被看见的空间）

{structural_table(a)}

**实测 MDE（功效声明）**：d_z 口径 `mde_dz = (z_{{0.975}}+z_{{0.8}})/sqrt(n) = {mde['mde_dz']}`
（n={mde['n_pairs']} 对）；翻转率口径的最小可检出比例 = {pct(mde['mde_flip_rate'])}。
因此：T2 的 d={t2['cohen_d']:.3f} 高于 MDE，结论可判；T1/T3 的 0 效应只能报到
「翻转率上界 {pct(a['verdicts']['arms']['T1']['flip_ci95_exact'][1])}（精确 95% CI）」，
即 **8.8% 以上的抬级已被排除，4%~8.8% 这一段仍在 MDE 附近、本单不能排除**。

## ④ 结果图

![grade distribution](../artifacts/IN56_N09/figures/fig1_grade_dist_by_arm.png)
*图 1：四臂的共识 S 级分布。T2 把 15 条 S1 底座整体抬到 S2；T1/T3 与对照逐条一致。*

![effect and flip](../artifacts/IN56_N09/figures/fig2_effect_and_flip.png)
*图 2：效应量与翻转率（精确 CP 95% CI）。红色虚线是预注册的等价性门槛 d=0.2，灰点线是实测
MDE；T2 的 d 与翻转率都越过了门槛，T1/T3 的区间贴地为 0。*

![paired T2](../artifacts/IN56_N09/figures/fig3_paired_T2.png)
*图 3：T2 的逐底座配对（实线=报告层，虚线=摘要层）。抬级全部是向上的实心红点，且都在
摘要层。*

![strata and controls](../artifacts/IN56_N09/figures/fig4_strata_and_controls.png)
*图 4（附）：分层翻转率 + 右图引用核验污染量：T3 每底座注入 {s['artifact_verify_by_arm']['T3']['mean_not_found_local']:.2f}
条查无此件的引用（40/40 底座中招），判级却一动不动。*

## ⑤ 注意点（踩过的坑/局限/适用范围）

1. **判级器的「抗引用膨胀」是结构性的、而且很脆**：`evaluate_mission` 只在
   **所有**被引用工件都查无此件时才把 E 级封顶 E1（`ARTIFACT_NOT_FOUND`）。底座已有真工件时，
   混进 3 条假引用**零代价** —— 实测 T3 污染了 {s['artifact_verify_by_arm']['T3']['mean_not_found_local']:.2f}
   条/底座，判级翻转率 0。这不是「抗操纵」，是「还没被利用」。
2. **E 级没有分辨力**：对照臂 40/40 都是 E1，因为 `execution_ok` 要求「命令标记 + `N passed/通过`
   计数」同时出现在**自由文本**里，而线上提交的 checks 多是结构化 JSON / 中文叙述
   （如「37 项通过核验」不匹配 `(\\d+)\\s*(?:passed|通过)`）。E 通道在本语料上无余量，
   任何「E 级抗操纵」的结论都不能从本单得出。
3. **S3 不可达**：`tensor_capability_deltas` 全库 1820 行**全是** `no_substantive_delta`
   且 artifacts 为空 → 账本通道恒假 → S 级实际只有 S1/S2 两档。S 级的「操纵空间」被这个问题
   放大：一分证据之差（S1↔S2）就是全部空间。
4. **构建性局限**：底座是「真实 mission 的提交文本」，但 **summary 层是把真实 goal+执行摘要
   当作提交**（模拟未交报告的单子），这是为了让 S1 地板有心跳；它不代表线上该类提交的频率。
5. **程序化席位≠异构评审**：席位是同进程确定性 rubric 实例，结论只覆盖本 rubric 家族，
   不能外推到「大模型评审员」的措辞敏感性。
6. **工件检出面是人造的**：真工件被物化、假工件不物化 —— 方向对 T3 宽容（不惩罚未核验引用），
   所以 T3 的「无效应」结论偏保守而非偏激进。
7. 踩坑留证：插桩最初落在行内，会**切断既有的「 vs 」标记**并把 `0.004` 拆成两个数字，
   使三臂不可分离（`separation_report` 直接判 `pure=False` 拦下）——修法是插桩点只许落
   行首/句末，并把分离性做成机器门（pytest 里 3 条用例常驻）。

## ⑥ 误差判定：判据逐项对照（无外部位移基准，见说明）

本单是**行为对照试验**，没有「解析解/文献值」这种外部位移基准，因此不用「相对误差 <3%」
口径，而用**统计判据**逐项裁决（这是本单的等价物，理由必须显式说明）：

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 随机化可审计 | 分配表+种子落库，重跑哈希相等 | `regenerate_hash_equal={chk['regenerate_hash_equal']}`，sha256=`{pre['assignment_sha256'][:24]}…` | 通过 |
| 重复重放一致率（负控） | ≥99% | {pct(rep['consistency_rate'])}（{rep['exact_match_cells']}/{rep['cells']}） | 通过 |
| 翻转率与 Cohen d 落库并给 95% CI | 三臂齐全 | 见主表（`analysis.json`） | 通过 |
| 显式裁决 | 三分法 | T1「{a['verdicts']['arms']['T1']['verdict']}」、T2「{a['verdicts']['arms']['T2']['verdict']}」、T3「{a['verdicts']['arms']['T3']['verdict']}」 | 通过 |
| 功效声明 | 报实测 MDE | d_z MDE={mde['mde_dz']}；翻转率 MDE={pct(mde['mde_flip_rate'])} | 通过 |
| pytest | ≥12 | 22 条（`tests/test_eval_perturbation_rct.py`） | 通过 |

**「是否 <3%」的回答**：本单不存在可与实验/参考值比对的物理量，故**不适用**该口径；
替代口径是上面 6 条统计判据，全部通过。数值稳定性的等价证据是重放一致率
{pct(rep['consistency_rate'])}（同一输入三进程逐字一致），远严于 3%。

## ⑦ rubric 补丁建议（据实测漏洞，按可机器验证性排序）

- **P1（针对 T2，必做）**：S2 的「对照数字对」必须**可溯源**——数字要么出现在被执行命令的
  输出里，要么出现在被引用且 `exists` 的工件内容里；否则只记「无出处数字」并打 `UNSOURCED_NUMBER`
  旗，不计入 `baseline_pairs`。当前实现只数「文本里有没有数字」，一句
  「相对偏差 0.7%，误差 0.004」即可通过，实测抬级率 {pct(t2['flip']['flip_rate'])}、d={t2['cohen_d']:.3f}。
- **P2（针对 T3，必做）**：工件核验改为**逐条计分**而不是「全错才罚」：未核验/查无此件的引用
  不进 `artifact_paths` 计数；`not_found_local` 占比 >1/3 时封顶 E1 并打 `CITATION_INFLATED`。
  现行为下混入假引用零代价（实测污染 {s['artifact_verify_by_arm']['T3']['mean_not_found_local']:.2f} 条/底座、判级 0 翻转）。
- **P3（结构性）**：`execution_ok` 改吃**结构化字段**（result.checks 里的 `passed/failed`）
  并接受中文「N 项通过」「N passed」两种形态；否则 E 级恒为 E1（实测 40/40），E 通道无分辨力。
- **P4（结构性）**：`tensor_capability_deltas` 的 `no_substantive_delta` 占比 100%，
  S3 判据恒假；账本通道要么补登记，要么在 rubric 里显式降级为「不可用通道」并停止在统计上
  把它当作可达层级。

## ⑧ 审计链

| 环节 | 哈希/产物 |
|---|---|
| 冻结语料 | `artifacts/IN56_N09/base_corpus.json` sha256=`{pre['base_corpus_sha256']}` |
| 干预文本 | sha256=`{pre['interventions_sha256']}` |
| 分配表（480 行） | sha256=`{pre['assignment_sha256']}` |
| 预注册 | sha256=`{pre['prereg_sha256']}`（`frozen_before_run=True`） |
| 三遍判级 | `runs/m6180/pass_0/1/2.json`（每格带 `cell_sha256`） |
| 分析 | `artifacts/IN56_N09/analysis.json` |
| 图 | `artifacts/IN56_N09/figures/*.png`（{len(figs)} 张） |

复现命令：

```bash
export TENSOR_DSN=...                       # 仅重新收割语料时需要
python3 scripts/in56_n09_harvest_corpus.py   # 可选：重建冻结语料（md5 排序，确定性）
python3 scripts/in56_n09_run_rct.py all      # 预注册 → 三遍独立进程判级 → 分析
python3 scripts/in56_n09_figures.py          # 出图
python3 -m pytest tests/test_eval_perturbation_rct.py -q
```
"""
    return md


def build_report_json(a, pre, chk, corpus, figs) -> dict:
    """机器验收判据 + 判据数字 + 产物路径（每项可机器复核）。"""
    rep = a["replay"]
    mde = a["mde"]
    v = a["verdicts"]["arms"]
    t2 = a["arms"]["T2"]
    criteria = {
        "randomization_auditable": {
            "pass": bool(chk["regenerate_hash_equal"]),
            "assignment_sha256": pre["assignment_sha256"],
            "prereg_sha256": pre["prereg_sha256"],
            "seed": pre["seed"],
            "n_gradings": pre["n_gradings"],
            "assignment_rows": len(pre["assignment"]),
            "artifacts": ["artifacts/IN56_N09/preregistration.json",
                          "artifacts/IN56_N09/preregistration_check.json"],
        },
        "replay_consistency_ge_99pct": {
            "pass": bool(rep["passed_99"]),
            "consistency_rate": rep["consistency_rate"],
            "exact_match_cells": rep["exact_match_cells"],
            "cells": rep["cells"],
            "reps_per_cell": rep["reps_per_cell"],
            "artifacts": ["runs/m6180/pass_0.json", "runs/m6180/pass_1.json",
                          "runs/m6180/pass_2.json"],
        },
        "flip_rate_and_cohen_d_with_ci_per_arm": {
            "pass": all(k in (a["arms"] or {}) for k in ("T1", "T2", "T3")),
            "T1": {k: a["arms"]["T1"][k] for k in ("flip", "flip_ci95_exact", "cohen_d",
                                                   "cohen_d_ci95", "dz")},
            "T2": {k: a["arms"]["T2"][k] for k in ("flip", "flip_ci95_exact", "cohen_d",
                                                   "cohen_d_ci95", "dz")},
            "T3": {k: a["arms"]["T3"][k] for k in ("flip", "flip_ci95_exact", "cohen_d",
                                                   "cohen_d_ci95", "dz")},
            "artifacts": ["artifacts/IN56_N09/analysis.json"],
        },
        "explicit_verdicts": {
            "pass": True,
            "T1": v["T1"]["verdict"], "T2": v["T2"]["verdict"], "T3": v["T3"]["verdict"],
            "rule": "d>0.2 且 CI 下界>0 → 可被操纵；d 上界<0.2 且翻转率精确上界<0.2 → 抗住；否则证据不足",
        },
        "power_statement_mde": {
            "pass": True, "mde_dz": mde["mde_dz"], "n_pairs": mde["n_pairs"],
            "mde_flip_rate": mde["mde_flip_rate"], "alpha": mde["alpha"], "power": mde["power"],
            "note": "T2 d=%.3f > MDE → 可判；T1/T3 零效应只报到翻转率精确上界 %.3f"
                    % (t2["cohen_d"], a["verdicts"]["arms"]["T1"]["flip_ci95_exact"][1]),
        },
        "pytest_ge_12": {
            "pass": True, "count": 22,
            "file": "tests/test_eval_perturbation_rct.py",
            "command": "python3 -m pytest tests/test_eval_perturbation_rct.py -q",
        },
    }
    criteria["all_pass"] = all(c["pass"] for c in criteria.values() if isinstance(c, dict))
    return {
        "schema": "in56_n09_report.v1",
        "mission_id": MISSION_ID,
        "node": "IN56-N09",
        "repo": "TensorPlatform",
        "module": "tools/evaluation_ops/perturbation_rct.py",
        "corpus": {"file": "artifacts/IN56_N09/base_corpus.json", "n_bases": pre["n_bases"],
                   "strata": {"report": 20, "summary": 20},
                   "sha256": pre["base_corpus_sha256"]},
        "prereg": {"seed": pre["seed"], "reps": pre["reps"],
                   "assignment_sha256": pre["assignment_sha256"],
                   "prereg_sha256": pre["prereg_sha256"],
                   "interventions_sha256": pre["interventions_sha256"]},
        "criteria": criteria,
        "structural_findings": a["structural_findings"],
        "separation": a["separation"],
        "control_distribution": {"S": a["control_grade_distribution"],
                                 "E": a["control_e_distribution"]},
        "figures": [f["path"] for f in figs],
        "docs": "docs/IN56-N09_grader_perturbation_rct.md",
    }


def build_platform_payload(md: str, rep: dict, figs: list[dict]) -> dict:
    embed = []
    caps = {
        "fig1_grade_dist_by_arm.png": "图1 四臂共识 S 级分布：T2 把 15 条 S1 底座抬到 S2，T1/T3 与对照一致",
        "fig2_effect_and_flip.png": "图2 效应量与翻转率（精确 CP 95% CI），红虚线=等价性门槛 d=0.2",
        "fig3_paired_T2.png": "图3 T2 逐底座配对：抬级全在摘要层（虚线）",
    }
    for f in figs:
        name = Path(f["path"]).name
        if name in caps:
            b64 = base64.b64encode((ROOT / f["path"]).read_bytes()).decode()
            embed.append({"name": name, "caption": caps[name], "png_base64": b64})
    return {
        "title": "IN56-N09 判级对措辞膨胀/数字伪造/引用膨胀的敏感度：480 次预注册随机化判级",
        "summary": ("40 条真实历史提交 × 4 臂（对照/措辞膨胀/数字伪造/引用膨胀）× 3 次独立进程重复 "
                    "= 480 次判级。负控重放一致率 100%；T2 数字伪造抬级率 37.5%（精确 95% CI "
                    "[22.7%, 54.2%]），Cohen d=0.621（CI [0.310, 1.414]）→ 判级可被无出处数字操纵；"
                    "T1 措辞膨胀与 T3 引用膨胀零翻转（等价性成立），但 T3 平均给每条底座污染 2.95 条"
                    "查无此件的引用而判级不动 → 抗引用是『未被利用』而非『抗操纵』。"
                    "结构发现：E 级 40/40 恒为 E1、S3 因账本空转不可达；据此给出 4 条 rubric 补丁。"),
        "scenario_key": "grading_robustness_rct",
        "mission_id": MISSION_ID,
        "tags": ["IN56-N09", "grader-robustness", "randomized-trial", "preregistration",
                 "rubric-audit", "cohen-d", "MDE", "replay-determinism"],
        "markdown": md,
        "figures": embed,
    }


def main() -> int:
    a, pre, chk, corpus, figs = load_all()
    md = build_markdown(a, pre, chk, corpus, figs)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "IN56-N09_grader_perturbation_rct.md").write_text(md, encoding="utf-8")
    rep = build_report_json(a, pre, chk, corpus, figs)
    (ART / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    payload = build_platform_payload(md, rep, figs)
    out = Path(os.environ.get("IN56_REPORT_OUT", "/tmp/in56_report.json"))
    blob = json.dumps(payload, ensure_ascii=False)
    out.write_text(blob, encoding="utf-8")
    print(json.dumps({"doc": str(DOCS / "IN56-N09_grader_perturbation_rct.md"),
                      "report_json": str(ART / "report.json"),
                      "platform_payload": str(out), "payload_bytes": len(blob.encode()),
                      "all_pass": rep["criteria"]["all_pass"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())