# K-CAMPAIGN-COUPLING-GATE · 战役集成交叉核对门的可复用规则

- **状态**：verified（本仓实测：p457w2-n09，m14019）
- **最后验证日期**：2026-11-11
- **出处/来源**：mission 14019；提交 `feat(integration-gate): p457w2 campaign cross-check gate ...`；
  `artifacts/campaigns/p457w2/seed_report.json`（内容 sha256 记于
  `docs/P457W2-N09_INTEGRATION_GATE.md` 头部）；平台报告 id 13545；
  代码 `tools/integration_gate/{coupling,archive}.py`、`tests/test_campaign_coupling_gate.py`
- **适用范围**：多节点/多仓战役的"集成交叉核对"节点（n09 类），即**不产生新物理量、
  只裁决他人产出能否对接**的任务。不适用于单节点自证任务（那里应直接跑解析锚与收敛研究）。

## 规则（最小可执行形式）

1. **接口契约五步闸**：文档存在 → 双方节点存在 → 单位归一致 → 消费方需求区间 ⊆ 生产方
   **认证**区间 → 双方有可核证据（验收工件，或本门自己成功执行过的轨迹）。
   任一不满足都不得判 `VALIDATED`；证据缺失判 `UNATTESTED`（绝不当"通过"）。
2. **区间缺口只允许带护栏通过**：`requirement.fallback=clamp_with_status` 且载荷字段含
   `domain_status` 时，区间不覆盖仍可判通过，但缺口数值必须写进报告；
   否则记 `CONTRACT_MISMATCH`。意图：允许在认证域外被调用，禁止沉默外推。
3. **同一标准的参数同一性要机械检查，且要能脱离标准文本复算**：对双段幂律类经验律，用
   连续性条件 `log a2 = log a1 + (m2 − m1)·log10(拐点)` 推出尾段参数，与声明值比（容差
   0.01 decades）。这条检查不需要任何标准表格，就能发现"两边都写 Table 2-1 但参数不同"
   这类最隐蔽的跨节点矛盾。
4. **冲突必须有对价**：每条冲突声明 `mitigation`；仅当被引用的互裁对确实判
   DISAGREE/DEGENERATE（或受影响节点均无可核自证）时才算"已处置"，否则计入
   `unmitigated` 并使一致性判据不通过。判据因此是"零**未处置**冲突"，报告里同时写
   `clean=false`，不给"无矛盾"的错觉。
5. **台账增量分两路**：战役条目 vs 基础台账（新条目/提升/降级），以及独立观测（如能力
   扫描器）vs 基础台账。后者必须走 R2：基础台账里存在 `contradicts` 证据的条目不得被
   静态观测抬高；顺带可检出基础台账"等级高于其证据上限"的内部矛盾。
6. **快照必须稳定**：时间戳走 `SOURCE_DATE_EPOCH`（缺省 wall clock），轨迹 provenance
   **不记耗时**；但仍需排除自指文件（报告/综述文档/清单自身），否则哈希环导致每次运行
   都不同。判据是"同一输入连跑两次，`report content_sha256` 逐字节相同"。

## 为什么这样做（机制）

- 节点自证是**输入**不是结论：判据用 `op/threshold` 重算，声明与数字冲突时数字优先。
- 跨节点矛盾往往不是"谁错了"，而是"两边各写了一份标准参数"；只有机械比对才能发现，
  人工评审看两份报告都会觉得各自合理。
- 自指哈希环与 wall-clock 会让"快照可复现"变成空话；这两点是小工程细节里最容易漏的。

## 反例/边界

- **不能**用本规则判物理正确性：接口形状通过 ≠ 物理正确（p457w2 里 IF-2 契约通过，但
  两侧 S-N 参数互相矛盾）。
- 生产方认证区间的"认证"二字必须有出处（域常量/实测极值/节点自证域声明）；凭空写的
  区间会让 `range_coverage` 变成装饰。
- 兄弟 worktree 的 git 事实（领先/漂移）是运行时快照，他人后续提交会改变它；
  不要把基线数字当成永久结论。
- 静态扫描器的"准确率/漏报率"若在工件里没有字段，就只能记 WITHHELD，不得用"扫描通过"
  代替准确性验证。

## 回归守卫（机器可检）

- `tests/test_campaign_coupling_gate.py::test_edition_conflict_is_detected_as_contradiction`
  —— 矛盾假设检出的负例控制（缺了它，一致性检查可能永远返回"一致"）。
- 同文件 `test_interface_*`（单位/区间/护栏/证据 5 类）、`test_archive_manifest_is_deterministic_and_tamper_evident`、
  `test_platform_delta_blocks_upgrade_over_contradicted_evidence`、
  `test_new_modules_are_domain_free`（域词表反查）。
- 门内判据 I6/I7/I8 把上述规则变成每次运行都重算的数字，任一不满足即 `--strict` 失败。