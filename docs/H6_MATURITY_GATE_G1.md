# H6-MATURITY-GATE：能力增量账本 + 五级成熟度 + 交叉一致性（G1 验收门报告）

> 门禁裁定：**PASS**（判据通过率 1.000） · 阈值指纹 `96a4208e1fc3cad5` · 增量链头 `843c3f4a4097d077`

## ① 案例介绍（物理/工程背景与对象）

本门不针对某个算例，而针对**能力台账本身**：平台把「某个能力有多成熟」的自述与独立证据对账，输出可机器复核的等级。对象是平台能力清单，量纲为「等级秩」（L0..L4）。

| 源 | 角色 | 路径 | as_of | 能力数 | sha256(前16) |
|---|---|---|---|---|---|
| self_claimed_2026-11-01 | baseline | `tests/fixtures/capability_ledger/capabilities_snapshot.json` | - | - | - |
| evidence_ledger_resolved | current | `artifacts/capability_ledger/capability_ledger_resolved.json` | - | - | - |
| live_probe_2026-11-11 | extra | `artifacts/maturity_gate_h6/sources/live_probe_2026-11-11.json` | - | - | - |

五级成熟度阶梯（本门唯一权威定义，秩严格单调）：

| 等级 | 秩 | 判据 |
|---|---|---|
| NOT_SUPPORTED | 0 | 能力/端点不存在 |
| STUB | 1 | 仅接口存在，无算法内核 |
| MOCK | 2 | 有实现但与独立基准背离（超容差）或非物理 |
| WITHHELD | 3 | 有实现但证据不足（无基准/基准不可信/引文失效） |
| REAL | 4 | 独立基准 + 误差达容差 + 可复现（逐字可查引文） |

## ② 怎么做的（方法 / 流程 / 关键设置）

```
自述快照(baseline) ─┐
                     ├─► 轴A 逐能力秩比较 → 分歧归类(OK/OVERCLAIM/REVERIFY/UNVERIFIED)
证据台账(current) ───┘        │
        │                     ├─► 轴B 引文逐字绑定：重算 line_sha256 vs 记录值
        ├─ 原始来源语料 ──────┤
        │                     ├─► 轴C 人读表数字 vs 台账重算（防手改/生成漂移）
        │                     └─► 轴D 等级↔证据同向（非 WITHHELD 必有同向证据）
        ▼
增量账本（逐能力 direction + 前驱哈希链）→ 五级画像 → 判据 C1..C8 → 裁定
```

关键设置（**运行前声明**，不得事后调参）：

| 阈值键 | 值 | 含义 |
|---|---|---|
| `coverage_min` | 1.0 |  |
| `unexplained_divergence_max` | 0 |  |
| `recompute_mismatch_max` | 0 |  |
| `quote_drift_max` | 0 |  |
| `quote_source_unresolved_max` | 0 |  |
| `markdown_delta_max` | 0 |  |
| `orphan_evidence_max` | 0 |  |
| `ladder_sound` | True |  |

阈值集合指纹 `thresholds_sha256 = 96a4208e1fc3cad52596eacf30eef45f991e231c2d5465ac282c21e8e49e6b0c`，写入判定体，事后改阈值必然导致 `--verify` 重算不符。

## ③ 计算结果（数字表，程序化注入）

### 3.1 五级成熟度画像

| 等级 | 秩 | 基线(自述) | 当前(对账) | 变化 |
|---|---|---|---|---|
| NOT_SUPPORTED | 0 | 2 | 0 | -2 |
| STUB | 1 | 1 | 0 | -1 |
| MOCK | 2 | 0 | 1 | +1 |
| WITHHELD | 3 | 0 | 19 | +19 |
| REAL | 4 | 18 | 1 | -17 |

- 平均秩：基线 3.4762 → 当前 3.0
- 归一化成熟度得分（均值秩 / 4）：**0.869 → 0.75**
- N = 21 个能力

### 3.2 增量账本（基线 → 当前）

| 方向 | 数量 |
|---|---|
| PROMOTE | 3 |
| DEMOTE | 17 |
| HOLD | 1 |
| NEW | 0 |
| DROPPED | 0 |

逐能力明细（含证据引用数）：

| # | 能力 | 基线 | 当前 | 方向 | Δ秩 | 证据数 |
|---|---|---|---|---|---|---|
| 0 | `ai.predict` | NOT_SUPPORTED | WITHHELD | PROMOTE | +3 | 0 |
| 1 | `calc.resistance` | REAL | WITHHELD | DEMOTE | -1 | 4 |
| 2 | `calc.stability` | REAL | REAL | HOLD | +0 | 3 |
| 3 | `dev.gate_5415` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 4 | `dev.git` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 5 | `dev.logs` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 6 | `dev.nginx_audit` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 7 | `dev.probe` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 8 | `dev.restart` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 9 | `dev.services` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 10 | `geometry.generate` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 11 | `geometry.hydro_curves` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 12 | `geometry.list_models` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 13 | `geometry.verify_official` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 14 | `gov.stats` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 15 | `knowledge.docs` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 16 | `methods.computation` | NOT_SUPPORTED | WITHHELD | PROMOTE | +3 | 0 |
| 17 | `misc.stubs` | STUB | WITHHELD | PROMOTE | +2 | 0 |
| 18 | `propeller.open_water` | REAL | MOCK | DEMOTE | -2 | 3 |
| 19 | `shiphull.generate` | REAL | WITHHELD | DEMOTE | -1 | 0 |
| 20 | `stdmodels.lookup` | REAL | WITHHELD | DEMOTE | -1 | 0 |

### 3.3 交叉一致性四轴

| 轴 | 核对量 | 通过 | 发现 | 比率 |
|---|---|---|---|---|
| A 自述↔对账 | 21 | 1 | 20 | 0.048 |
| B 引文逐字绑定 | 10 | 10 | 0 | 1.000 |
| C 人读表↔台账重算 | 7 | 7 | 0 | 1.000 |
| D 等级↔证据同向 | 2 | 2 | 0 | 1.000 |

轴 C 逐指标对照（ref = 已发布人读表数字，got = 从台账重算）：

| 子轴 | 指标 | ref | got | 偏差 | 一致 |
|---|---|---|---|---|---|
| counts | capabilities | 21 | 21 | +0 | ✅ |
| counts | with_evidence | 3 | 3 | +0 | ✅ |
| counts | without_evidence | 18 | 18 | +0 | ✅ |
| divergence | OK | 1 | 1 | +0 | ✅ |
| divergence | OVERCLAIM | 1 | 1 | +0 | ✅ |
| divergence | REVERIFY | 16 | 16 | +0 | ✅ |
| divergence | UNVERIFIED | 3 | 3 | +0 | ✅ |

### 3.4 判据明细（C1..C8）

| 判据 | 陈述 | 值 | 比较 | 阈值 | 结果 |
|---|---|---|---|---|---|
| C1 | 基线↔当前能力集合全覆盖（无孤儿能力） | 1.0000 | ge | 1.0 | PASS |
| C2 | 五级阶梯严格单调且全部观测等级落在阶梯内 | True | true | True | PASS |
| C3 | 分歧全部可归类（无未解释分歧） | 0 | le | 0 | PASS |
| C4 | 分歧标签可复算（规则 vs 台账存储标签一致） | 0 | le | 0 | PASS |
| C5 | 证据引文逐字可查（记录 sha256 == 从原始来源重算） | 0 | le | 0 | PASS |
| C6 | 证据来源可解析（无悬空引文） | 0 | le | 0 | PASS |
| C7 | 人读对账表数字 == 台账重算数字 | 0 | le | 0 | PASS |
| C8 | 等级与证据同向（非 WITHHELD 必有同向证据） | 0 | le | 0 | PASS |

## ④ 结果图

- 图 `fig1_maturity_profile.png`：图1 五级成熟度分布：基线(自述) vs 当前(证据对账)
- 图 `fig2_axes_health.png`：图2 四轴交叉一致性：核对量 vs 发现量
- 图 `fig3_increment_flow.png`：图3 增量账本逐能力秩变化

## ⑤ 注意点 / 局限 / 适用范围

1. **本门不生产领域证据**：它只做「账面一致性」。等级是否真的 REAL 由证据链决定。
2. **源缺失必须显式降级**：任何源不可用时判据值为 `null` 而非 0。
3. **分歧率高不是缺陷**：`OVERCLAIM` 大量出现说明自述系统性虚高。
4. **引文绑定依赖原始语料**：语料是快照；报告正文若在上游变更，本门会报 drift。
5. **增量链不是防篡改链**：单条事件含 `prev_sha256` 可发现中间被改。

## ⑥ 误差判定

本门的关键量全部是**计数/等级**，基准为「独立重算值」或「已发布数字」，故误差定义为`got - ref`（计数差）或等级秩差，判据容差为 0（不允许任何漂移）。

| 关键量 | 基准 ref | 本门 got | 误差 got-ref | 相对量级 | 是否<3% |
|---|---|---|---|---|---|
| 人读表↔台账重算最大计数偏差 | 0 | 0 | +0 | 0.0%（以 21 个能力为基数） | ✓ |
| 引文逐字绑定失败数（逐字可查） | 0 | 0 | +0 | 0.0%（以 21 个能力为基数） | ✓ |
| 分歧标签复算不一致数 | 0 | 0 | +0 | 0.0%（以 21 个能力为基数） | ✓ |
| 等级↔证据不同向数 | 0 | 0 | +0 | 0.0%（以 21 个能力为基数） | ✓ |
| 悬空引文（来源不可解析）数 | 0 | 0 | +0 | 0.0%（以 21 个能力为基数） | ✓ |

**门禁裁定：PASS**；判据通过率 1.000。

## ⑦ 复现

```bash
python3 -m tools.h6_maturity_gate.cli --spec data/maturity_gate_h6/h6.spec.json --out artifacts/maturity_gate_h6
```
