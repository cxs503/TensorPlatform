# 知识蒸馏：让「成熟度」机器可判（P315W1-N11）

- **最后校验日期**：2026-11-10（跑批 `artifacts/maturity_inference/acceptance.json`，sha 见附录）
- **节点/任务**：`p315w1-n11-maturity-inference-maturity`，mission 10861，仓 TensorPlatform
- **产出代码**：`tools/maturity_inference/`（README 为接口说明）；用例 `tests/test_maturity_inference_{core,pipeline}.py`（65 passed）
- **报告**：`docs/reports/p315w1_n11_maturity_inference/REPORT.md`
- **复核状态**：待独立席位按 S×E 复判（本文件由实施者撰写，属"待复核"状态，不宣称已复核）

## 规则（最小可执行形式）

> 能力的成熟度标签**只能由证据图推出**：`level = 逐环累积支撑得起的最高环`；
> 任一环证据缺件即在该环截断（fail-closed）；自述等级不参与抬级，只用于判高报；
> 矛盾证据按断言档位封顶。`node.*` 一类记账行不是能力，一律拒收并迁出能力台账。

## 适用边界

**适用**：任何「能力/成熟度/证据等级」台账（平台能力、求解器内核能力、审计结论）。
**不适用**：
- 数值物理量本身的精度判定（本规则判"证据形态"，不判"物理对错"；物理对错由判据数值 vs 基线承担）；
- 需要"部分证据也承认一点"的场景（本规则是硬的，不做加权）。

## 证据/出处（provenance）

| 断言 | 数字 | 来源工件 |
|---|---|---|
| 黄金集 40 行（合成键）宏 F1 | 1.0000（acc 1.0000） | `artifacts/maturity_inference/golden_report.json`，冻结 sha256 `3575873…1ed439e` |
| naive「取最高环」基线 | acc 0.5000（40 行里 20 行判反） | 同上 `naive_baseline` |
| 真库台账自述 REAL → 保留 REAL | 18 → 0 | `artifacts/maturity_inference/real_ledger_levels.json` |
| 等级分布 | not_supported 7 / implemented 6 / tested 8 / bench 0 / coupled 0 / engineering 0 | 同上 |
| 仓储测试实测 | 执行 19 个文件：15 passed / 4 failed / 4 not_run | `artifacts/maturity_inference/evidence_links.json` `test_run` |
| node.* 记账行 | 12 行快照中 3 行被拒收 → 迁移后残留 0 | `node_migration_plan.json` / `migrated_ledger.json` |
| 用例 | 65 passed | `acceptance.json` `criteria.e_pytest_and_zero_domain.pytest` |

## 机制（为什么这条规则能成立）

1. **证据形态可枚举**：五环各自的必交字段（sha256 / 重跑命令 / 判据数值+容差 / 组件+接口摘要 /
   独立复核+复跑互异 sha）都是**结构性判据**，不需要人工裁量。
2. **fail-closed 比"取最高证据"更接近真相**：实测同 40 行黄金集上，naive 取最高环 acc 0.5，
   fail-closed 1.0；差异全在对抗行（判据越界、缺容差、复跑同 sha、单组件、夹具作用域、矛盾档位）。
3. **自述不抬级**是唯一能自动否掉"口头 REAL"的机制：本轮真库 18 条自述 REAL 全部无法站上
   `benchmark_verified`，若允许自述参与抬级，这 18 条会全部记成 REAL。

## 推荐流程（复用到别处时）

1. 先定阶梯与每环必交字段（**先写判据再写引擎**）；
2. 冻结一份带人工标签的黄金集（含对抗原型），并把文件 sha256 写进冻结清单；
3. 用「naive 最大环」做基线对照——没有基线的一致性 1.0 不构成证据；
4. 真库落地时只接**适配层**（台账字段漂移只改 adapter）；
5. 迁移类动作必须幂等且带 before/after sha（可回滚、可审计）。

## 反例/局限（诚实登记）

- **黄金集标签与引擎同源**：F1=1.0 度量的是「实现-规格一致」，不是泛化能力。缓解：naive 基线 +
  16 类对抗原型；但样本仍由本节点作者构造，独立复标注尚未做。
- **coupled / engineering 两环不自动装配**：本轮没有一行站上 `benchmark_verified`，
  `unlinked REAL = 0` 是**空真值**（保留 REAL 的行数为 0），不能读成"所有 REAL 都挂上了证据"。
- **路径级证据 ≠ 语义等价证据**：`code_patterns` 命中的测试通过，只证明"该测试通过"，
  不证明"被测实现就是该能力的正确实现"——这正是必须引入 L3 数值判据的原因。
- **4 个测试文件失败 / 4 个未跑**（`test_propeller_open_water.py`、`test_gate_auto_release.py` 等）：
  失败即不能支撑 `tested`，未跑即按 `not_run` 处理（不猜测结果）。

## 回归保护（机器可判）

1. `python3 -m tools.maturity_inference.cli golden` —— 宏 F1 < 0.9 直接非零退出（CI 可挂）；
2. `tests/test_maturity_inference_*` —— 65 条用例覆盖：各环正/反例、缺件截断、矛盾封顶、
   自述不抬级、夹具封顶、命名空间拒收、迁移幂等与残留 0、装配器三态（未跑/通过/失败）、黄金集冻结 sha；
3. 黄金集内容与 `fixtures/golden_set.sha256` 绑定：改题必须显式重建并提交新 sha。

## 被后续推翻时的处理

本文件不删不改历史：若将来出现"部分证据可加权"或被证伪的判据，**追加**新条目并标注
`supersedes: <本文件日期>`，保留原结论与反例证据。