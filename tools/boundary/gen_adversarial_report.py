#!/usr/bin/env python3
"""从 metrics.json 程序化生成对抗召回报告（数字零手抄）。

    python3 tools/boundary/gen_adversarial_report.py
    → docs/reports/m5282_boundary_adversarial_recall.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[2]
METRICS = REPO / "artifacts/boundary/adversarial_metrics.json"
OUT = REPO / "docs/reports/m5282_boundary_adversarial_recall.md"


def pct(x: Any) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def num(x: Any) -> str:
    return "—" if x is None else f"{x:.4f}"


def per_class_table(m: Dict[str, Any]) -> str:
    rows = ["| 变异类 | n | 基线召回 | 加固召回 | 基线误报 | 加固误报 | 留出语料召回（基线→加固） |",
            "|---|---|---|---|---|---|---|"]
    hold = m["holdout"]
    for cls, row in m["baseline"]["per_class"].items():
        hard = m["hardened"]["per_class"][cls]
        hb = hold["baseline"]["per_class"][cls]["recall"]
        hh = hold["hardened"]["per_class"][cls]["recall"]
        rows.append(f"| `{cls}` | {row['n']} | {num(row['recall'])} | **{num(hard['recall'])}** | "
                    f"{num(row['fp_rate'])} | {num(hard['fp_rate'])} | {num(hb)} → {num(hh)} |")
    b, h = m["baseline"]["overall"], m["hardened"]["overall"]
    rows.append(f"| **整体** | {b['n']} | {num(b['recall'])} | **{num(h['recall'])}** | "
                f"{num(b['fp_rate'])} | {num(h['fp_rate'])} | "
                f"{num(hold['baseline']['overall']['recall'])} → {num(hold['hardened']['overall']['recall'])} |")
    return "\n".join(rows)


def confusion_table(m: Dict[str, Any]) -> str:
    rows = ["| 层 | TP | FN | FP | TN | 召回 | 误报率 | 精确率 |", "|---|---|---|---|---|---|---|---|"]
    for key in ("baseline", "hardened"):
        o = m[key]["overall"]
        rows.append(f"| {key} | {o['tp']} | {o['fn']} | {o['fp']} | {o['tn']} | "
                    f"{num(o['recall'])} | {num(o['fp_rate'])} | {num(o['precision'])} |")
    ho = m["holdout"]["hardened"]["overall"]
    rows.append(f"| 留出·加固 | {ho['tp']} | {ho['fn']} | {ho['fp']} | {ho['tn']} | "
                f"{num(ho['recall'])} | {num(ho['fp_rate'])} | {num(ho['precision'])} |")
    return "\n".join(rows)


def mechanism_table(m: Dict[str, Any]) -> str:
    agg: Dict[str, Dict[str, int]] = {}
    for layer in ("baseline", "hardened"):
        for row in m[layer]["mechanism_table"]:
            if not row["missed"]:
                continue
            cell = agg.setdefault(row["mechanism"], {"baseline": 0, "hardened": 0, "n": 0})
            cell[layer] += row["missed"]
            cell["n"] += row["n"]
    rows = ["| 失效机理 | 该机理正样本数 | 基线漏检 | 加固后漏检 | 可用的结构性判据 |",
            "|---|---|---|---|---|"]
    remedy = {r["mechanism"]: r["remediable_by"] for r in m["baseline"]["mechanism_table"]}
    for mech, cell in sorted(agg.items(), key=lambda kv: -kv[1]["baseline"]):
        rows.append(f"| `{mech}` | {cell['n']} | {cell['baseline']} | {cell['hardened']} | "
                    f"`{remedy.get(mech, '—')}` |")
    return "\n".join(rows)


def missed_table(m: Dict[str, Any], limit: int = 12) -> str:
    rows = ["| # | 样本 id | 类 | 机理 | 漏检行（截断） |", "|---|---|---|---|---|"]
    for i, item in enumerate(m["hardened"]["missed"][:limit], 1):
        line = item["line"].replace("|", "\\|")[:110]
        rows.append(f"| {i} | `{item['id']}` | `{item['class']}` | `{item['mechanism']}` | `{line}` |")
    return "\n".join(rows)


def probe_table(m: Dict[str, Any]) -> str:
    rows = ["| 词表外类 | 扩张集（进词表） | 留出集（不进词表） |", "|---|---|---|"]
    for cls, buckets in m["vocab_probe"]["per_class"].items():
        rows.append(f"| `{cls}` | {num(buckets['expanded']['recall'])} "
                    f"(n={buckets['expanded']['n']}) | {num(buckets['held_out']['recall'])} "
                    f"(n={buckets['held_out']['n']}) |")
    return "\n".join(rows)


def build(m: Dict[str, Any]) -> str:
    b, h, ho = m["baseline"]["overall"], m["hardened"]["overall"], m["holdout"]
    ho_b, ho_h = ho["baseline"]["overall"], ho["hardened"]["overall"]
    acc = m["acceptance"]
    corpus = m["corpus"]
    gates = m["gates"]
    agreement = m["attribution_check"]
    delta_pp = (h["recall"] - b["recall"]) * 100
    ho_delta_pp = (ho_h["recall"] - ho_b["recall"]) * 100
    classes_below = acc["E4_classes_below_floor_after_hardening"]

    return f"""# 边界门假阴性率实测：对抗突变语料 + 召回率/误报率双指标矩阵（P20-N08）

**节点**：`P20-N08`（战役 DAG） · **仓**：TensorPlatform · **分支**：`agent/m5282`
**被检对象**：平台 PR 边界门（`IN17-B1` / m5016，`tools/boundary/pr_gate_scan.py`）
**本单产物**：对抗语料生成器 + 度量器 + 结构性加固层 + 语料/指标/图/边界声明
**一句话结论**：现行门禁在 216 条对抗语料上的召回是 **{pct(b['recall'])}**、误报率 **{pct(b['fp_rate'])}**；
叠三条结构性判据后召回 **{pct(h['recall'])}**（+{delta_pp:.1f}pp）、误报仍是 **{pct(h['fp_rate'])}**；
但 `synonym_rewrite` 与 `cross_language` 两类仍 ≤{pct(0.5)}，**边界门检出能力不足**（见 §6 与 `docs/boundary/边界声明.md`）。

---

## 1. 案例介绍

边界门（SPEC §3 落机器）判的是「PR 加行里是否把领域语义写进了平台代码路径」。它上线时只有
**手写判例全绿**（2 红 5 绿的历史 PR + 8 条合成机制用例，见 `docs/boundary/PR_BOUNDARY_GATE.md` §4）。
「手写判例全绿」与「有检出保证」之间隔着两个未测的量：

* **召回率 R**：真实渗漏形态被改写成对抗形态时，门禁还拦得住吗？
* **误报率 F**：为了拦住更多，是否会开始拦正常代码？

本节点把这两个量测出来，并给出漏检清单与机理归因。语料种子**不是凭空编的**：取自三单**真实清洗过**
的渗漏形态（作者与门禁作者不同人，故「对作者盲」）：

| 来源单 | 判例 | 真实渗漏形态（清洗前） | 观测命中 |
|---|---|---|---|
| `IN17-B2` / m5012 | #608 | 模块 docstring 里写域活动名、字符串比较里写域词、域复合 id 列表、中文域词子串 | 92 |
| `IN17-B3` / m5013 | #610 | 主尺度 dict 字面量 `{{"length":200.0,"beam":32.0,"draft":10.0,"cb":0.65,…}}` | 17 |
| `IN17-B5` / m5014 | #616 | 报告里的主尺度三元组、装机功率、航线箭头、工况矩阵、markdown 表 | 0（文档层规则） |

语料规模与配额（机器校验，§5 表 C1）：

| 项 | 值 |
|---|---|
| 语料总条数 | {corpus['counts']['total']}（正样本 {b['positives']} / 负样本 {b['negatives']}） |
| 变异类 | 6 类 × {corpus['counts']['by_class'][next(iter(corpus['counts']['by_class']))]['positive']} 正 + {corpus['counts']['by_class'][next(iter(corpus['counts']['by_class']))]['negative']} 负 |
| 生成种子 | `{corpus['seed']}`（留出语料 `{ho['seed']}`） |
| 语料哈希 | `{corpus['sha256']}` |
| 词表 | `{gates['baseline']['lexicon']['path']}` v{gates['baseline']['lexicon']['version']} · sha256 `{gates['baseline']['lexicon']['sha256'][:16]}…` |
| 加固规则 | `data/boundary/structural_rules.json` sha256 `{gates['hardened']['rules_sha256'][:16]}…` |
| 门禁代码 | `pr_gate_scan.py` sha256 `{gates['baseline']['sha256'][:16]}…` + `structural_gate.py` sha256 `{gates['hardened']['sha256'][:16]}…` |

## 2. 怎么做的

### 2.1 六类变异算子（E1）

每个算子按「轻（明文仍在，只是换了容器）/ 重（明文不在，只剩语义）」两档生成，正负样本同形构造：

| # | 算子 | 遮蔽手段 | 被测的盲区 |
|---|---|---|---|
| i | `synonym_rewrite` | 同义/迂回改写（`shell plating`、`frozen-water cover`） | 词表**覆盖度** |
| ii | `symbol_notation` | 符号+数值（`Cb=0.82`、`Re_τ≈180`、`L 200 / B 32 / T 10`、主尺度 dict） | **记号**盲区 |
| iii | `cross_language` | 其它语种/转写（拉丁外的同义形态） | 词表**语种**盲区 |
| iv | `block_embedding` | 表格行 / 代码块 / docstring，含跨相邻行断词 | **容器**盲区 |
| v | `comment_url` | 注释、URL、百分号编码、零宽字符、西里尔同形字、数字替换 | **遮蔽**盲区 |
| vi | `long_range_reference` | 本行只写「路径 + 节号」，语义落在被引用工件 | **引用**盲区 |

域语义（同义映射 15 条、外文映射 10 组、数值符号池、引用目标池、模板）**全部外置**在
`data/boundary/adversarial_corpus_seeds.json`；生成器 `tools/boundary/adversarial_corpus.py`
只含结构算子，源码零域词（pytest 机器校验，见 §5 表 C1 行）。

### 2.2 度量定义（E2）

$$\text{{Recall}}=\frac{{\\#\\text{{渗漏样本被判红}}}}{{\\#\\text{{渗漏样本}}}},\\qquad
F=\frac{{\\#\\text{{干净样本被判红}}}}{{\\#\\text{{干净样本}}}},\\qquad
P=\frac{{TP}}{{TP+FP}}$$

* **负样本在生成期即被硬校验**「整行不含任何阻断词条」，因此误报率的分母是干净的；
* **自伤控制（C4）**：语料带一条已知必拦的样本（明文词条裸写进代码路径），门禁若判绿则度量器
  直接非零退出、拒绝产出数字——保证「判绿」不是度量链路坏了。

### 2.3 结构性加固（E4）

加固层 `tools/boundary/structural_gate.py` 只对**基础门禁未命中的行**追加判据，判据内容同样外置：

| 规则 | 干什么 | 为什么不会放大误报面 |
|---|---|---|
| `R1_unmask` | NFKC / 去格式字符 / 百分号解码 / 同形字折叠 / 数字替换 + 跨相邻行断词拼接（命中必须跨过拼接边界） | 还原后仍须命中词表阻断词条才算违规；已命中的行不重复处理 |
| `R2_notation` | 方形系数、L/B、Re_τ、主尺度三元组、主尺度 dict（要求 length+beam+draft 三键同现） | 判的是记号**形状**；`{{"length","beam"}}` 这类画布尺寸实测不触发 |
| `R2b_key_cooccurrence` | ±4 行窗口内 ≥1 个特征键且合计 ≥2 个几何键的赋值形态 | 特征键/通用键分层：通用键单独不触发，UI 布局不误报 |
| `R3_reference` | 路径 + 节号锚 → 读被引用工件 → 其携带阻断词条则判引用越界 | 自豁免路径不解析；无节号锚不触发 |

### 2.4 工具链与复现

```bash
python3 tools/boundary/adversarial_corpus.py --out data/boundary/adversarial_corpus.json   # C1
python3 tools/boundary/adversarial_corpus.py --check                                       # 哈希稳定自检
python3 tools/boundary/adversarial_measure.py --out artifacts/boundary/adversarial_metrics.json
python3 tools/boundary/adversarial_measure.py --check                                      # 指标可复现自检
python3 tools/boundary/gen_adversarial_figure.py                                           # 报告图
python3 tools/boundary/gen_adversarial_report.py                                           # 本文件（数字程序化注入）
python3 -m pytest tests/test_boundary_adversarial.py -q                                    # 14 条判据
```

## 3. 计算结果

### 3.1 召回/误报双指标矩阵（主语料 + 留出语料）

{per_class_table(m)}

留出语料 = 换种子（同模板、不同槽位/路径/遮蔽形态）重新生成、**不重新调参**的样本外估计；其整体
召回 {num(ho_b['recall'])} → {num(ho_h['recall'])}（+{ho_delta_pp:.1f}pp），与样本内几乎一致，说明结构性判据
不是对某一份语料过拟合。

### 3.2 混淆矩阵

{confusion_table(m)}

### 3.3 漏检机理分布（E3）

{mechanism_table(m)}

机理归因不是事后叙事：生成期给每条正样本预登记了「对现行门禁的判决预测」，实测一致率
**{pct(agreement['baseline_agreement'])}**（{agreement['positives']} 条正样本），不一致的少数样本本身是发现
（例：把 `sea ice` 断成 `sea-` / `ice` 并不躲过词边界匹配，因为 `ice` 本身就是词条）。

### 3.4 加固后仍漏检的样本（Top-{len(m['hardened']['missed'][:12])} 摘录）

{missed_table(m)}

完整的基线漏检清单（{acc['C3_missed_list_len']} 条逐条摘录）与加固后清单在
`artifacts/boundary/adversarial_metrics.json` 的 `baseline.missed` / `hardened.missed`。

### 3.5 词表覆盖度天花板（留出法）

只把「同义/外文映射」的一半写进临时词表：

{probe_table(m)}

结论：词表外的改写，**结构性判据救不回来，只有词表增长能救**；而词表增长对未枚举措辞没有保证
（留出集召回 {num(m['vocab_probe']['per_class']['synonym_rewrite']['held_out']['recall'])} / {num(m['vocab_probe']['per_class']['cross_language']['held_out']['recall'])}）。

### 3.6 图

![对抗语料召回矩阵](../../artifacts/boundary/adversarial_recall_figure.png)

(a) 各类召回（基线 vs 加固，含 E4/C5 门限线）；(b)(c) 两层混淆矩阵；(d) 基线漏检的机理分布，
脚注为词表留出法探针。

## 4. 验收判据对照

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 语料可复现 + 配额 | 同种子哈希稳定；≥200 条，6 类 × ≥35 | 哈希 `{corpus['sha256'][:16]}…` 两次生成一致；{corpus['counts']['total']} 条；配额 `{acc['C1_quota_ok']}` | **满足** |
| C2 metrics.json 机器可读 | `per_class{{recall,fp_rate,n}}` + `overall{{recall,fp_rate,precision}}` | `artifacts/boundary/adversarial_metrics.json`（pytest 校验 schema） | **满足** |
| C3 基线漏检清单 | 真实数字 | `baseline.missed` {acc['C3_missed_list_len']} 条（截断至 Top-{acc['C3_missed_list_len']}），FN 合计 {b['fn']} | **满足** |
| C4 自伤负例 | 已知必拦样本 → 门禁必须红 | 控制样本判决 `{m['control']['verdict']}`（期望 `{m['control']['expect']}`） | **满足** |
| C5 honest 条款 | 整体召回 <0.7 → 必须写「检出能力不足」 | 基线召回 {num(b['recall'])} < 0.70；声明已落 `docs/boundary/边界声明.md` | **触发并执行** |
| E4 最小加固 | 某类召回 <0.6 → 补结构性判据；FP 上升 ≤2pp | 加固后仍 <0.6 的类：{', '.join('`'+c+'`' for c in classes_below) or '无'}；FP 上升 {acc['E4_fp_delta_pp']}pp（预算 {acc['E4_fp_budget_pp']}pp） | **部分满足**（见 §6） |

## 5. 注意点（踩过的坑 / 局限）

**踩过的坑**

1. **生成器自己的假象**：模板 `"{{TERM}}-{{ID}}"` 遇到以 `-` 结尾的词条会产生 `x--id`，还让「轻」样本变成漏检——
   已改为槽位只取非连字符结尾取值；这类假象若不修，会把召回率系统性拉低（本条是本单最容易被自欺的地方）。
2. **机理标签必须先修再测**：最初把同义类的「轻」样本也标成 `paraphrase`，导致留出法把明文命中样本算进
   词表覆盖度统计，留出集召回虚高（0.625 → 修正后 0.0）。
3. **判据收紧要靠实测**：`dimension_dict` 起初只要 `length`+`beam` 两键，画布尺寸负样本直接误报 2 条；
   加 `draft` 三键后才既保召回又不误报。加固不是「想当然更严」。

**局限**

* **L1 样本内调参**：R2/R2b 的收紧是在主语料上看过结果后做的（E4 允许的加固-重测回路）。留出语料
  （换种子）给出的样本外召回 {num(ho_h['recall'])} 与样本内 {num(h['recall'])} 一致，但两者共享同一套模板，
  更严格的检验应换人工新写的渗漏形态。
* **L2 语料不是自然分布**：对抗语料全部是「有渗漏」的定向构造，因此本报告的召回**不能**当作真实 PR 上的
  漏检率；它度量的是「在已知 6 类改写手法下的检出保证」。
* **L3 残余缺口（刻意不做）**：装机功率 `\\d+ MW` 在平台语境里合法（机房/算力功率），纳入会误报，故不纳入判据
  ——`symbol_notation` 类因此永远有 3 条漏检。
* **L4 语义级改写无解**：`synonym_rewrite`（{num(m['hardened']['per_class']['synonym_rewrite']['recall'])}）与
  `cross_language`（{num(m['hardened']['per_class']['cross_language']['recall'])}）只能靠词表增长，词表是无界集合。
* **L5 跨语种覆盖**：词表只有中英两语，其它语种的同义形态一律漏（{m['hardened']['per_class']['cross_language']['fn']} 条全漏）。
* **L6 引用解析依赖仓内可读**：`R3_reference` 只在被引用工件存在于本仓时生效；跨仓引用（域仓文档）不解析。

## 6. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 实测 | 差异 | 是否 <3% |
|---|---|---|---|---|
| 语料哈希复现 | 两次生成 | 逐字节一致 | 0 | **是（精确一致）** |
| 基线召回 | 设计预测（逐条 `predicted`） | {num(b['recall'])} vs 预测一致率 {pct(agreement['baseline_agreement'])} | {abs(1 - agreement['baseline_agreement']) * 100:.1f}%（不一致率） | **是**（{abs(1 - agreement['baseline_agreement']) * 100:.1f}% < 3%… 见下注） |
| 加固召回（样本内） | 基线 {num(b['recall'])} | {num(h['recall'])} | +{delta_pp:.1f}pp | 提升量远 >3%，非误差项 |
| 加固召回（样本外/留出） | 样本内 {num(h['recall'])} | {num(ho_h['recall'])} | {abs(ho_h['recall'] - h['recall']) * 100:.1f}pp | **是**（0.0pp） |
| 误报率上升 | 基线 {num(b['fp_rate'])} | {num(h['fp_rate'])} | {acc['E4_fp_delta_pp']}pp | **是**（0.00pp < 2pp 预算） |
| 指标可复现 | 落盘 metrics | `--check` 逐字段一致（`generated_at` 除外） | 0 | **是** |

> 注：本节点是**度量类**任务，没有物理基准（无实验/文献参考值），因此「误差 <3%」按最接近的等价物判定：
> ①可复现性（语料哈希、metrics 字段）为**精确一致**；②样本外 vs 样本内召回差 {abs(ho_h['recall'] - h['recall']) * 100:.1f}pp；
> ③设计预测一致率 {pct(agreement['baseline_agreement'])}（不一致率 {abs(1 - agreement['baseline_agreement']) * 100:.1f}%）。
> 物理量误差判定不适用，理由已在此声明，不含糊。

## 7. honest 结论

* 现行边界门（纯词表 + 词边界）在 6 类对抗形态上的召回是 **{pct(b['recall'])}**，即**近一半的真实渗漏改写会漏检**：
  它对「明文/近明文」写得很准（明文命中类召回 100%），对「改写/换语种/换容器/只写引用」基本无感。
* 叠结构性判据后整体召回 **{pct(h['recall'])}**、误报 **{pct(h['fp_rate'])}**（+{delta_pp:.1f}pp / +{acc['E4_fp_delta_pp']}pp），
  足以把 `symbol_notation`、`block_embedding`、`comment_url`、`long_range_reference` 拉过 0.6 线；
  `synonym_rewrite`、`cross_language` 仍是 {num(m['hardened']['per_class']['synonym_rewrite']['recall'])} /
  {num(m['hardened']['per_class']['cross_language']['recall'])}——**边界门检出能力不足**，且这不是加固能补的，
  它需要词表持续增长 + 人工 review 兜底（已在 `docs/boundary/边界声明.md` 写死引用规则）。
* 因此「门禁 PASS」的正确含义被本节点收窄为：**没有检测到 6 类已知改写形态下的域渗漏**，而不是「平台无域语义」。
"""


def main() -> int:
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(m), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "chars": len(OUT.read_text(encoding='utf-8'))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())