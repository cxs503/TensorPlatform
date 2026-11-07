# 注入后新单 goal 质量对比样本

> 机器生成：`python3 -m tools.precedent_library.goal_quality` + `make_artifacts`
> 样本源：`tools/precedent_library/data/goal_quality_samples.json`（真实 mission goal 原文节录，
> 带 mission id 与抓取时间戳；由 `build_goal_samples.py` 从控制面重取，不手抄）。

## 1. 打分口径（8 项可证伪属性，等权，代码即定义）

| # | 属性 | 机检正则（摘要） |
|---|---|---|
| 1 | 量化判据（带数字+单位/比例） | `\d+(?:\.\d+)?\s*(?:%|‰|s\b|ms\b|min\b|h\b|kN\b|N\b|kg\b|Hz\b|dB\b|KB\b|MB\b|GB\b|°|个|条|项|行|次|倍|台|核|层|门)` |
| 2 | 交付工件路径可定位 | `(?:artifacts|docs|tests|runs|src|tools|scripts)/[\w./\-]+` |
| 3 | 验证命令/测试可重跑 | `(?:pytest|python3\s|make\s|curl\s|npm\s|cargo\s|bash\s)` |
| 4 | 基线/参考值对照 | `(?:基线|baseline|参考值|对照|对比|基准|vs\.?\s)` |
| 5 | honest FAIL 纪律 | `(?:honest[-\s]?FAIL|如实|不可覆盖|partial[-\s]?FAIL)` |
| 6 | 分支/工作树纪律 | `(?:worktree|分支|branch|EDIT_WHITELIST)` |
| 7 | 报告交付要求 | `(?:报告|report|manuscript|论文)` |
| 8 | 不重复既定案例的前沿声明 | `(?:全新|前沿|禁止重复|不重复|未做过|never\s+done)` |

分档：`excellent ≥ 0.875`、`adequate ≥ 0.625`、`weak < 0.625`。
`after` = 同一段 goal 原文 + 注入块（`injection_suffix`，确定性拼接）。

## 2. 样本结果

| 样本 | 来源 | before | after | Δ | 新覆盖的属性 |
|---|---|---|---|---|---|
| `m5118` | tensor/missions/5118 | 0.750 (adequate) | 0.875 (excellent) | +0.125 | no_duplicate_frontier |
| `m5101` | tensor/missions/5101 | 1.000 (excellent) | 1.000 (excellent) | +0.000 | — |
| `m5121` | tensor/missions/5121 | 0.750 (adequate) | 1.000 (excellent) | +0.250 | baseline_ref, no_duplicate_frontier |
| `m5047` | tensor/missions/5047 | 0.625 (adequate) | 0.875 (excellent) | +0.250 | baseline_ref, quant_criteria |

## 3. 汇总

| 指标 | 值 |
|---|---|
| 样本数 | 4 |
| 平均 before | 0.7812 |
| 平均 after | 0.9375 |
| 平均 Δ | +0.1562 |
| Δ 区间 | +0.000 … +0.250 |
| 通道 | PLAYBOOK append-only (<=120 char/line) |

图：`artifacts/precedent_library/fig2_goal_quality.png`；原始 JSON：
`artifacts/precedent_library/goal_quality_comparison.json`。

## 4. 怎么读这张表（诚实边界）

1. **Δ=0 的样本是饱和证据**：`m5101` 的 goal 本就含数字判据/工件路径/重跑命令/基线对照，
   注入无从增益——说明评分器不是"给注入块放水"的橡皮图章。
2. **这不是行为改变实验**：`after` 是文本改写，测的是注入块对"可证伪属性"的覆盖能力；
   真实行为改变需要后续单的对照（n=4，不作统计推断）。
3. **正则只看表面特征**：它无法判断引用的基线是否**正确**——那仍是独立评审的职责
   （与 S4 空缺同一原因）。
