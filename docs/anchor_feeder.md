# anchor_feeder —— 主线锚长设供给（FEAT-ANCHOR-FEEDER）

状态：**机制层落地**（不代跑任何主线任务本身）。代码 `ops/taskplane/anchor_feeder.py`，
仪表 `ops/taskplane/mainline_share.py`（已挂进 `ops/taskplane/compound_meter.py` 的 M9 指标），
守护登记 `ops/taskplane/tp_autoheal.py:REGISTRY["anchor_feeder"]`，
锚源表 DDL `ops/taskplane/sql/repro_pool.sql`。

## 1. 问题（为什么缺口不是"没活"，是"没长设供给"）

近 24h 任务类型结构实测：自驱 55.9% / 知识工厂 16.5% / 算例池 14.8% / 主线（REPRO-+XVAL-+JOUR-）**<3%**。
根因：`case_feeder` 只喂 `tensor_case_pool`（CASE-）；主线锚（论文复现 REPRO-、开源对标 XVAL-、
期刊成稿 JOUR-）没有长设供单器，只能靠人肉规划/提案波偶发投放 →
**自驱成了默认主粮，主线长期饥饿**。

## 2. 机制（照抄 case_feeder，区别只在锚源查询）

| 环节 | 规则 | 代码 |
|---|---|---|
| 闲位门 | `idle = 工位数 − 在飞单`，`idle < 8` 不供给（不空转刷单） | `Feeder.idle` / `IDLE_FLOOR` |
| 每轮帽 | `min(8, idle//4)` | `Feeder.run_cycle` |
| 主线保底 | 配额 ≥50% 给主线锚源；主线源枯竭 → 空位让给算例池 | `split_quota`（`MAINLINE_FLOOR=0.5`）|
| 六仓轮转 | 源间轮转 + 源内按仓游标轮转（防一仓独占） | `round_robin`（按仓游标，非恒取首仓）|
| 幂等 | `feature_ledger` 已有 slug 或已有挂该锚点的**在飞**单 → 跳过，只补账不重派 | `existing_slugs` / `in_flight_slugs` |
| 覆盖账回流 | 锚消账（ledger done/verified）→ 池行 `passed`；每轮 log 覆盖账 | `Feeder.backflow` / `coverage` |
| 兜底 | 主线 + 算例池都不足额 → 用任务榜既有开放锚（TIER_A 族、无在飞单），**不新造锚**；主线族优先 | `ledger_candidates` / `run_cycle` 尾部 |

锚源（优先级自上而下）：

| 源 | 查询 | 锚点形态 | 说明 |
|---|---|---|---|
| repro | `repro_pool`（新表） | `REPRO-*` | 由 `tensor_repro_papers`（论文清单）+ `tensor_benchmark_cases` 的 paper 类行（`^REPRO-` 或 ref 含年份）幂等播种 |
| xval | `oss_solutions × oss_inventory` | `XVAL-{领域}-{方法}` | 领域从仓名/feature_slug/method 识别（`pylbm`/`listLBM`/`openLBMPM` 这类嵌入写法可认）；去重 + 已存在锚跳过；"可对标证据"（valid/reference/ghia/convergence…）优先排序 |
| case | `tensor_case_pool`（既有） | `CASE-*` | 与 case_feeder 同池，`linked_anchor` 非空的例不重复供 |
| ledger | `feature_ledger` 开放锚 | 沿用原 slug | 兜底，只在主线/算例池不足额时填残位 |

单的构造与 case_feeder 同宗：`【锚点=…】【第N轮·任务榜·…】` + ref 出处 + 容差判据 + 开工三查 +
「**目标数值从 ref 原文/手册查得（零手抄）**」+ `cluster_queue`（禁指定节点）+ 报告六段 + honest-FAIL 留 `fail_stage`。

## 3. 主线类占比仪表（M9，进 compound_meter）

`mainline_share.py` 把 mission goal 按锚点族分类（`CAT_RULES` 规则表外置，可增改）：
mainline(REPRO/XVAL/JOUR) · case_pool(CASE) · knowledge(KBM/知识工厂) · selfdrive(【自找活) · other(返修/治理/战役)。
窗口 `24h / 7d / 前7d`（小时界显式，`WINDOWS=(名,起h,止h)`），输出占比与**周变化 pp**。
落表：`compound_metrics(metric=mainline_share_pct_24h|7d, state='meter')` + 自有历史表
`mainline_share_metrics(win, cat, n, share_pct)`；`--post` 把周报计量写 `chronicle_snapshots`（机制化周报，不等人工）。
`compound_meter.read_metrics()` 通过 `mainline_share.mainline_metrics(_MetricQ())` 取 M9；`evaluate()` 在
7d 主线占比 <3% 时**只加告警不改 verdict**（结构改善是长线，熔断只会制造噪声 —— 对齐 kpi_meter「只计量不改进」纪律）。

实测对表（2026-11-13 01:09 UTC，`artifacts/anchor_feeder/mainline_weekly_20261113.txt`）：

| 窗口 | 总单 | 主线 | 算例池 | 知识工厂 | 自驱 | 其它 |
|---|---|---|---|---|---|---|
| 24h | 1417 | 15 (1.1%) | 258 (18.2%) | 252 (17.8%) | 734 (51.8%) | 158 (11.2%) |
| 7d | 11643 | 74 (0.6%) | 397 (3.4%) | 307 (2.6%) | 1417 (12.2%) | 9448 |
| 前 7d | 3980 | 0 (0.0%) | 0 | 0 | 2043 (51.3%) | 1937 |

与任务书给的独立基线（自驱 55.9 / 知识工厂 16.5 / 算例池 14.8 / 主线 <3）在 24h 窗口同量级
（自驱 51.8 vs 55.9、知识工厂 17.8 vs 16.5、算例池 18.2 vs 14.8、主线 1.1 <3），
差异来自窗口滑动与分类口径（任务书"算例池"可能含 GAP- 族）——**方向一致，失衡成立**。

## 4. 验证（可复跑）

```bash
python3 -m pytest tests/test_anchor_feeder.py -q      # 19 passed
python3 ops/taskplane/anchor_feeder.py --once --dry-run   # 只读 DB 的计划审计（生产库跑过）
python3 ops/taskplane/mainline_share.py --weekly          # 周报计量文本
python3 ops/taskplane/mainline_share.py                   # 落表（compound_metrics + mainline_share_metrics）
```

生产库 dry-run（2026-11-13 01:09 UTC，`artifacts/anchor_feeder/dryrun_20261113.txt`）：

- 工位 94 / 在飞 32 / **闲 62** → 过闲位门，帽 = min(8, 62//4) = 8
- repro 播种 **28 行**（repro_pool 未建 → 池表缺失时用播种行预演，不中断供给）
- 候选：主线 **157**（xval 为主）/ 算例池 **0** / 兜底 38 → 本轮计划 **主线 8 / 算例池 0 / 兜底 0**
- 计划构成：`XVAL-*` 8 单，TensorLBM/FVM/FEM/DEM/SPH/DG 六仓各 1-2 单（仓间轮转生效）
- 算例池 0 候选是**真实的**：19 条 pending 全部 `linked_anchor` 非空（已被 GAP/REPRO/XVAL 锚覆盖，按设计跳过）

`compound_meter` 接线实测（`artifacts/anchor_feeder/compound_meter_wiring_20261113.txt`）：

```
M9: {'mainline_24h_pct': 1.1, 'mainline_7d_pct': 0.6, 'mainline_delta_pp': 0.6, 'mainline_7d_n': 74, 'missions_7d': 11640}
verdict/alerts: ('armed-green', ['M9 主线占比偏低: 7d 0.6%（<3.0%）——查 anchor_feeder 供给/主线锚是否断流'])
```

## 5. 注意点 / 踩过的坑

1. **psql 多行文本解析缺陷**（真坑）：mission goal/title 含换行，`psql -tA -F '\x01'` 按 `\n` 切记录会把
   一行切成几十行 → 行数虚增、字段错位。首版实测把 24h 自驱数算成 3（真值 734）。
   修法：记录分隔符改 `-R '\x02'`，按记录切、按 `\x01` 切字段。**case_feeder.py 生产副本有同一潜在缺陷**
   （覆盖账/in-flight 判读会错位），建议随部署以本模块的解析方式对齐。
2. **兜底 ≠ 抢占**：ledger 兜底只填残位，且排除在飞单；`covered` 参数对兜底源传"在飞集合"而不是
   "已存在集合"（首版误传导致兜底源 100% 自废，dry-run 抓到）。
3. **轮转要按仓游标**：首版 `round_robin` 恒取 `keys()[0]`，实际退化成"一仓打死"，单测抓出后改为游标。
4. **XVAL 候选有噪声**：oss_solutions 的 tag 有的是实现细节而非可对标目标 → 用 `_xval_score`
   把 valid/bench/reference/analytic/convergence 等证据行排前，但**未做人审过滤**：首批观察到明显非对标语时
   应在 `CAT_RULES`/`_xval_score` 层加负例词表（机制内改，不硬编码域判据）。
5. **与 taskboard AUTO_SUPPLY 的边界**：两者都可能派 GAP-/REPRO- 族。缓解=幂等（slug 级在飞检查）+
   兜底低优先 + 只在残位使用；仍存在 30min 窗口内双派的理论可能，需观察日志 `供给 …→ m…` 与 taskboard 派单是否撞 slug。

## 6. 部署（合并后）

```bash
cp ops/taskplane/anchor_feeder.py ops/taskplane/mainline_share.py ops/taskplane/compound_meter.py /root/tp-agent/
# autoheal 60s 内按 REGISTRY 自动拉起 anchor_feeder；日志 /var/log/tp-anchor-feeder.log
python3 /root/tp-agent/anchor_feeder.py --once --dry-run   # 上线前最后一眼
```
`repro_pool` 由守护首轮自建（`IF NOT EXISTS`）+ 幂等播种，无需人工 DDL；如需预建用 `ops/taskplane/sql/repro_pool.sql`。
## 7. 本单证据索引

- 报告：`/tensor/reports` **id 16108**（六要素齐全，图内嵌 `artifacts/anchor_feeder/mainline_share_fig.png`）
- 单测：`python3 -m pytest tests/test_anchor_feeder.py -q` → **19 passed**
- 生产库只读审计：`artifacts/anchor_feeder/dryrun_20261113.txt`
- 仪表实测：`artifacts/anchor_feeder/mainline_share_20261113.json`、`mainline_weekly_20261113.txt`、`mainline_record_20261113.txt`
- compound_meter 接线：`artifacts/anchor_feeder/compound_meter_wiring_20261113.txt`
- 误差诚实结论：三分类占比对基线偏差 7.9%–23%（>3%），主因口径/窗口差异；**口径对齐既有类型结构统计脚本**列为下一步必做项。
