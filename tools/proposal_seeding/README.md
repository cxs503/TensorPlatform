# proposal_seeding —— 提案聚合 → 仲裁 → 战役 DAG 播种 → 自回归增长

零领域语义的平台治理管线：只认识 `proposal / item / node / milestone / repo / criteria`，
物理域词、域判据、域算例一律经输入工件注入。全部可调参数（n-gram 阶数、合并阈值、
节点数上下界、里程碑数、成熟化单下限、仓白名单、成熟化标记）唯一来源
`config/proposal_seeding.json`；代码内无字面量阈值、无域词。

## 四段

| 段 | 模块 | 产出 |
|---|---|---|
| 聚合 | `aggregate.py` | 多提案者读入 → 异构 `goal` 归一 → 3-gram Jaccard → 并查集 → 合并吸收细节（含来源与仓冲突留证） |
| 播种 | `seed.py` | 聚合项 → 计划（ext_id 内容定址 / 里程碑分块 / DAG 依赖构造性无环 / 超容量项进 `overflow`） |
| 机器门 | `gate.py` | 8 项硬校验：节点数 / ext_id 计划内与全库唯一 / 仓白名单 / DAG 无环 / 依赖闭合 / 里程碑覆盖 / 成熟化单数 |
| 自回归 | `autoregress.py` | 遥测 → 缺口 → 平台自优化提案（幂等注入）；`overflow` 反哺下一波；不足下限发背压信号 |

配套：`match.py`（人工↔机器四通道匹配，审计用）、`cli.py`（命令行）、
`m8405_pipeline.py`（真实数据复现）、`m8405_acceptance.py`（判据自检）、
`m8405_report.py`（报告程序化生成）。

## 用法

```bash
# 全链（聚合 → 播种 → 自回归注入 → 机器门 → 幂等回放 → 下一波）
python3 -m tools.proposal_seeding.m8405_pipeline

# 验收判据逐条自检（含跑 pytest）
python3 -m tools.proposal_seeding.m8405_acceptance --run-tests

# 报告（markdown + 平台 payload，数字零手抄）
python3 -m tools.proposal_seeding.m8405_report

# 单步
python3 -m tools.proposal_seeding.cli aggregate --inputs a.json b.json --out agg.json
python3 -m tools.proposal_seeding.cli seed --agg agg.json --wave pXw1 --out plan.json
python3 -m tools.proposal_seeding.cli gate --plan plan.json --snapshot snap.json --out gate.json
python3 -m tools.proposal_seeding.cli replay --inputs a.json b.json --wave pXw1
python3 -m tools.proposal_seeding.cli advance --plan plan.json --wave-next pXw2
```

回归测试：`python3 -m pytest tests/test_proposal_seeding.py -q`（24 例，零领域语义夹具）。
