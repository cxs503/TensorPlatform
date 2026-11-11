# H6 成熟度门（能力增量账本 + 五级成熟度 + 交叉一致性）

平台共性机制，**零领域语义**：源路径 / 能力名 / 锚点全部由 `data/maturity_gate_h6/h6.spec.json`
注入；代码内不含任何业务域词条。

## 一句话

把「某能力有多成熟」的**自述**与**独立证据**放在同一把五级尺子上量，输出可机器复核的
等级、增量与矛盾；四轴交叉一致性各自独立重算，任何数字都能被 `--verify` 逐字节复算。

## 五级阶梯（唯一权威定义）

| 等级 | 秩 | 判据 |
|---|---|---|
| NOT_SUPPORTED | 0 | 能力/端点不存在 |
| STUB | 1 | 仅接口存在，无算法内核 |
| MOCK | 2 | 有实现但与独立基准背离（超容差）或非物理 |
| WITHHELD | 3 | 有实现但证据不足（无基准/基准不可信/引文失效） |
| REAL | 4 | 独立基准 + 误差达容差 + 可复现（逐字可查引文） |

## 四轴交叉一致性

| 轴 | 核对什么 | 失败意味着 |
|---|---|---|
| A 自述↔对账 | 逐能力秩比较 + 分歧归类（OK/OVERCLAIM/REVERIFY/UNVERIFIED），并用规则复算台账存储标签 | 标签不可复算 = 有人手改过账面 |
| B 引文绑定 | 从**原始来源**重算 `line_sha256` vs 台账记录值 | 引文漂移 = 文档被改/手抄/转述 |
| C 表↔台账重算 | 人读对账表里已发布的数字 vs 从台账重算 | 生成物与台账脱节 |
| D 等级↔证据同向 | 非 WITHHELD 必须至少一条同向证据 | 等级无证据支撑 = 孤儿等级 |

## 用法

```bash
python3 -m tools.maturity_gate_h6.cli run      # 跑门 + 变异基准 + 三张图 + 报告
python3 -m tools.maturity_gate_h6.cli verify   # 只读重算，防手改/防阈值暗改
python3 -m tools.maturity_gate_h6.cli mutate   # 只跑变异基准（ref vs got 召回）
python3 -m pytest tools/maturity_gate_h6/tests -q
```

产物：`artifacts/maturity_gate_h6/{gate,consistency,increment_ledger,mutation_benchmark}.json`、
`fig1_maturity_profile.png` / `fig2_axes_health.png` / `fig3_increment_flow.png`、
`docs/H6_MATURITY_GATE_G1.md`（论文级六要素报告）。

## 判据（阈值运行前声明，写入 `thresholds_sha256`）

C1 覆盖率≥1.0 · C2 阶梯严格单调且观测等级合法 · C3 无未解释分歧 · C4 分歧标签可复算 ·
C5 引文零漂移 · C6 无悬空引文 · C7 人读表零偏差 · C8 无孤儿等级。

**缺失源一律降级为 `null`（不通过）**——宁拒不猜，绝不静默成绿。
