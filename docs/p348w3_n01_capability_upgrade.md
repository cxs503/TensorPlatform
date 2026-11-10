# P348-W3-N01 能力台账自动升级管线（成熟化）：规则表 + 完整性检查器 + 状态机 + 审计轨迹

**仓库** TensorPlatform · **节点** P348-W3-N01 · **mission** 10689 · **生成时间** 2026-11-10T18:44:37.214888+00:00
**验收总判** all_passed = **True** · 判据文档 `runs/p348w3_n01_capability_upgrade/acceptance.json`

## ① 案例介绍

平台的 `tensor_capability_deltas` / 能力台账长期靠人工把证据等级从 `MOCK`/`STUB` 抬到 `REAL`。
上一代机制（P218-N01 `RejudgeEngine`）把通过判据的 criterion 直接映射到 `target_level`：一次判定、
一次跳级，既不能说清跳过了哪一级，也不能在证据撤回后自动降级（其源码明示 "downgrades are never
automatic here"）。本节点把"升账"做成可审计的状态机：

* **规则表**（`capability_upgrade_rules` + `capability_rollback_rules` 两张 SQLite 表，JSON 为源）
  定义每一级台阶需要的证据条件，阈值全部可配置；
* **完整性检查器**把测试报告/基准结果/复核记录/缺陷记录折算成 7 个事实，逐台逐级给出完整度；
* **状态机**逐级攀爬 `NOT_SUPPORTED → STUB → MOCK → WITHHELD → REAL`，每步一条审计记录；
* **审计轨迹**是哈希链（`prev_digest`/`record_digest`），改一行即可检出；
* **回滚**：注入缺陷 → 落回该缺陷能支撑的最高等级；证据自身退化 → 自动降级。

几何/工况：本节点为平台机制节点，不涉及任何几何体，故无几何校验项。全部算例为合成能力位
（`synth.*`）与通用数学证据记录，零领域语义（域词条扫描器 0 命中）。

规则表（随附配置 `config/capability_upgrade/upgrade_rules.json`，digest `d7f976f906d6f4512ff813d4f2297eabc8d5f32c387548dc504d612a87556bda`）：

| 台阶 | 必备证据（默认阈值） |
|---|---|
| NOT_SUPPORTED → STUB | implementation_present |
| STUB → MOCK | implementation_present ∧ smoke_test_passed ∧ input_response |
| MOCK → WITHHELD | 锚点 ≥ 1 ∧ max_error_pct ≤ 15% |
| WITHHELD → REAL | 锚点 ≥ 3 ∧ max_error_pct ≤ 15% ∧ 独立复核 ≥ 1 ∧ 未闭缺陷 = 0 |

（即题述 MOCK→REAL 门槛：≥3 个独立验证锚 + 误差 <15% + 独立复核；阈值由规则表持有，非代码常量。）

## ② 怎么做的

1. `tools/capability_upgrade/upgrade_rules.py` — 规则表对象、合法算子、单级台阶校验、SQLite 镜像与回读
   （回读后 digest 必须与配置一致，用于配置漂移检测）。
2. `tools/capability_upgrade/completeness.py` — 事实推导（锚点数按 `source.sha256` 去重、只统计带
   `error_pct` 的测量记录；自述 `MOCK/STUB/NOT_SUPPORTED` 或哈希非法的记录不可采信；缺失事实永不算通过），
   `assess()` 给出 satisfied + completeness(0-1)，`chain_report()`/`completeness_report()` 做全队扫描。
3. `tools/capability_upgrade/state_machine.py` — `apply()` 先回归校验当前台阶（注册级为先验，仅在有证据时
   才可被推翻），再逐级攀爬（`max_steps` 可限步）；`inject_defect()`/`resolve_defect()` 管理缺陷阻塞；
   状态 JSON 原子落盘、同证据重复提交为 noop（幂等）。
4. `tools/capability_upgrade/upgrade_audit.py` — 哈希链 JSONL 审计；每条含 audit_id、slug、方向、
   from/to、规则 id 与 digest、升级前后证据快照、完整度、operator、reason。
5. `tools/capability_upgrade/upgrade_acceptance.py`（本文件）— 一次性跑出 C1–C5 的数字、3 张图与本报告。

工具链：CPython 3.11 · pytest（junit 报告哈希入证）· matplotlib(Agg) · sqlite3（stdlib）。

## ③ 计算结果

### C1 状态机迁移正确性
| 项 | 值 |
|---|---|
| 场景数 | 12 |
| 与预声明期望表一致 | 12 |
| **迁移准确率** | **1.0** |
| 覆盖初始等级 | ['NOT_SUPPORTED', 'STUB', 'MOCK', 'WITHHELD', 'REAL'] |

完整证据下 5 种初始状态全部到达 REAL；7 个部分证据场景分别停在 MOCK / WITHHELD / NOT_SUPPORTED 等
诚实台阶（若只测"都能升到 REAL"，无法排除实现恒返回 REAL 的退化，故并入部分证据对照）。

### 迁移网格（初始等级 × 证据完整度 → 到达等级）

| 初始等级 \ 证据 | absent | partial | complete |
|---|---|---|---|
| NOT_SUPPORTED | NOT_SUPPORTED | WITHHELD | REAL |
| STUB | STUB | WITHHELD | REAL |
| MOCK | MOCK | WITHHELD | REAL |
| WITHHELD | WITHHELD | WITHHELD | REAL |
| REAL | REAL | WITHHELD | REAL |


### C2 证据完整性阈值（可配置）
| 项 | 值 |
|---|---|
| MOCK→REAL 门槛（规则表读出） | {'min_anchors': 3, 'max_error_pct': 15.0, 'independent_reviews': 1, 'open_defects': 0} |
| 阈值扫描点 | 12 |
| 仅改配置可达的等级 | ['MOCK', 'REAL', 'WITHHELD'] |
| 配置与出厂表一致 | True |

### C3 审计轨迹
| 项 | 值 |
|---|---|
| 审计记录数 | 17 |
| 真实迁移数 | 11 |
| 含 before/after 证据快照的迁移记录 | 11 |
| 哈希链校验 | True |
| 篡改（改动一条 upgrade 的 to_level）检出 | True（首个问题行 1） |
| 轨迹文件 / sha256 | `runs/p348w3_n01_capability_upgrade/audit_trail.jsonl` / `67be22c5f8223a225c99b385145cc6064e64c1bdd96be2e25edd2d00d42c02f6` |

### C4 回滚
| 项 | 值 |
|---|---|
| 配置缺陷种类 | 9 |
| 落点与配置一致 | 9 |
| 记录原因非空 | 9 |
| 证据自身退化回滚 | REAL → WITHHELD（缺失项 ['min_anchors', 'independent_reviews']，blocked_by=evidence_regression） |

各缺陷落点：implementation_absent→NOT_SUPPORTED；smoke_test_failed→STUB；no_response_to_input→MOCK；error_above_threshold→WITHHELD；insufficient_anchors→WITHHELD；provenance_broken→WITHHELD；review_revoked→WITHHELD；evidence_regression→WITHHELD；unspecified→WITHHELD

### C5 用例
| 项 | 值 |
|---|---|
| 状态机/检查器/审计/回滚用例数 | 43（要求 ≥ 15） |
| 失败/跳过 | 0 / 0 |
| junit | `runs/p348w3_n01_capability_upgrade/pytest_state_machine_junit.xml` sha256 `7db32d5d82a81db017c71aaaa4532daf1dc94685ac2b2d3d02ccedeb5e0f8e6f` |

### 与基线（P218-N01 一次性判定）对比
| 指标 | 基线 RejudgeEngine | 本节点状态机 |
|---|---|---|
| C1 场景准确率 | 1.0（5/5） | 1.0（12/12） |
| 同证据下记录的中间台阶数 | 0 | 11 条迁移记录（逐级） |
| 证据快照 before/after | 无 | 每条迁移均有 |
| 自动回滚 | 无（源码明示不做） | 9 种缺陷落点 + 证据退化回滚 |

结论：**准确率不是增益所在**（两者都 100%），增益在可解释性与可回滚性——中间台阶 0 → 逐级留痕、
0 → 11 条带前后快照的迁移、回滚从"无此能力"到"按规则表落点并记录原因"。

## ④ 结果图

![transition_grid.png](figures/transition_grid.png)

*图：5 initial levels x 3 evidence grades -> reached level (annotated); diagonal-free rows show the ladder walking.*

![rung_completeness.png](figures/rung_completeness.png)

*图：Fraction of each rung's requirements met by an absent / partial / complete evidence bundle.*

![audit_timeline.png](figures/audit_timeline.png)

*图：Evidence level vs audit record sequence for the five C3 scenarios; the final drop is the injected defect.*

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. `UpgradeAudit` 定义了 `__len__`：空审计对象是 falsy，`self.audit = audit or UpgradeAudit(None)`
   会静默替换成内存审计——落盘轨迹消失而一切"看似正常"（已改显式 `is not None`）。
2. 锚点数初版把所有可采信记录都计入，实现记录（impl/smoke）自带 source sha256 也被算成 1 个锚点，
   使"2 个锚点"达到 3 → 误升 REAL。修正：锚点只统计带数值 `error_pct` 的测量记录。
3. SQLite 回读时按 `rule_id` 字典序排列会改变表顺序 → digest 变化（误判配置漂移）。修正：按阶梯 rank 排序。
4. 注册等级是先验而非证据主张：空证据包不得降级（"没测"不等于"有缺陷"）；一旦有可采信证据与注册等级
   矛盾，首次判定即可把等级纠正下来。
5. 证据退化回滚落点 = 新证据实际能支撑的台阶（而非固定 WITHHELD），回滚原因写明缺失项。
6. 局限：证据为合成记录，尚未接入真实 benchmark/报告流水线（规则表与事实键已是数据驱动，接线属下一节点）；
   回滚只看证据，不看"证据之间的时间序"；证据撤销若表现为长时间沉默（无新证据包）不会自动降级，
   需由定期 `recheck()` 或缺陷注入触发。
7. 事实不可由调用方声明：初版留有 `overrides` 逃生口（调用方可自称"3 个锚点"），与"独立锚点数出来而非声称"
   相矛盾，已删除并由 `test_bundle_rejects_caller_supplied_facts` 锁定。
8. 审计链只保证内部自洽：整段尾部截断（删掉最后 N 条）`verify()` 无法察觉，因为它没有外部锚。缓解：本次
   轨迹的 sha256 已钉进 `acceptance.json`（本报告 ③ 表），生产接线时应把 `head_digest` 登记到 DB 行或
   git commit 注释里。

## ⑥ 误差判定

本节点的关键量是**离散状态迁移正确性**，基准=动手前预声明的期望表（C1_EXPECTED_FULL / C1_EXPECTED_PARTIAL，
写在 acceptance 模块常量中，先于任何数字）。逐项：

| 关键量 | 基准（期望） | 实测 | 误差 |
|---|---|---|---|
| C1 迁移准确率 | 1.0 | 1.0 | **0.0%**（< 3% ✔） |
| C2 MOCK→REAL 门槛 | 锚点 3 / 误差 15% / 复核 1 | {'min_anchors': 3, 'max_error_pct': 15.0, 'independent_reviews': 1, 'open_defects': 0} | **0%** ✔ |
| C3 审计快照覆盖率 | 100% 迁移记录含 before/after | 100.0 | **0.0%** ✔ |
| C4 回滚落点一致率 | 9/9 按配置 | 9/9 | **0.0%** ✔ |
| C5 用例数 | ≥ 15 | 43 | 超额 ✔ |

baseline 对照说明：基线与本节点准确率同为 1.0（误差 0%），因此本节点**不宣称精度提升**；声明的是审计粒度
（中间台阶记录 0 → 11）、证据快照（无 → 全）与回滚（无 → 有）三项结构性能力。诚实 FAIL 声明：无。
