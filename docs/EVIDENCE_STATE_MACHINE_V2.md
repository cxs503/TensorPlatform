# 证据等级引擎 v2：五态 + 不可判态（UNDECIDABLE）的 fail-closed 语义与机器裁决

> 战役节点 `p348w2-n04`｜仓库 TensorPlatform｜模块 `src/capability_ledger_auto`
> 全部数字由 `scripts/evidence_state_v2_acceptance.py` 程序化写入
> `artifacts/evidence_state_v2/metrics.json`，本报告由
> `scripts/evidence_state_v2_report.py` 从 JSON 注入（零手抄）。

## ① 案例介绍（问题背景与对象）

平台的能力台账引擎原先只有 **三态**（REAL / MOCK / STUB，`grader.py`），
对「验证锚存在但证据不闭合」的情形没有表达力：一个算例的相对误差落在判决线附近、
而参考值本身带不确定度（实验台测量、文献离散、缩放外推）时，旧引擎只能二选一
——按 `err < tol` 判 PASS（过度乐观，把统计上不可分的差异当通过），或判 WITHHELD
（语义错误：证据并非「没收集」，而是「收集了但不可判」）。

本节点把证据等级语义扩到 **五态 + 不可判态**，并为升级/降级建立机器可算规则：

| 状态 | 可采信秩 | 语义定义 | 本波场景数 |
|---|---|---|---|
| NOT_SUPPORTED | 0 | 无代码/测试/报告痕迹且无验证锚：能力明确不存在 | 1 |
| STUB | 1 | 仅接口或骨架，无实质实现（stub 标记命中） | 1 |
| MOCK | 2 | 有实现但被机器证据否证（决定性 FAIL 锚／反例／测试未通过） | 2 |
| WITHHELD | 3 | 有实现、无否证、但未收集到验证锚：证据不足，能力可能存在 | 5 |
| REAL | 4 | 机器判据闭合：≥N 条独立决定性 PASS 锚 + ≥K 类来源 + 测试通过率达标 | 2 |
| UNDECIDABLE | -1 | 有验证锚但机器无法裁决（不确定度带跨判决线／缺参考值）：fail-closed，不得采信 | 3 |

- **UNDECIDABLE ≠ WITHHELD**（C2 核心）：WITHHELD = 还没试过验证（无锚）；
  UNDECIDABLE = 试过但证据不闭合（有锚、不确定度带跨判决线、或缺参考值）。
  二者在门禁上分流：`WITHHELD→ALLOW(min_rank=WITHHELD)`、`UNDECIDABLE→BLOCK_UNDECIDABLE`，
  且 UNDECIDABLE 的可采信秩 **-1 < NOT_SUPPORTED 的 0**，
  保证「不确定即最不可信」的 fail-closed 偏序。
- 判据阈值（`EvidencePolicy`，全部可配）：容差 tol=0.15（平台精度标准 15%）、
  覆盖因子 z=2.0（≈95%）、
  REAL 需 ≥3 条**独立**决定性 PASS 锚 + ≥2 类来源、
  测试通过率 ≥0.8。

## ② 怎么做的（方法 / 工具链）

新增三个模块（纯函数、无 IO、零领域语义，能力名一律合成 `synth.*`）：

1. `anchors.py` —— 验证锚 `VerificationAnchor(rel_error, uncertainty, independence_key, has_reference_value)`
   与单锚判决 `judge_anchor`：
   ```
   band = z * uncertainty
   rel_error + band <  tol   → PASS        （决定性通过）
   rel_error - band >  tol   → FAIL        （决定性否证）
   否则（区间跨判决线）      → UNDECIDABLE （unadverse/adverse 由中心估计相对 tol 决定）
   rel_error is None / 缺参考值 → UNDECIDABLE（fail-closed，绝不当 PASS）
   ```
   同 `independence_key` 的锚视为同源，独立性计数只算一次（防同数据集重复刷数）。
2. `state_machine.py` —— 11 条机器规则的状态机（`R01…R10` + `R_MANUAL_OVERRIDE`），
   顺序即优先级：结构判据（无痕迹/stub）→ 决定性否证 → 测试失败 →
   偏坏侧不可判（拦截升级）→ REAL 门槛 → 不可判 → WITHHELD 不足 → 兜底。
3. `provenance.py` —— append-only 哈希链：`entry_hash = sha256(canonical(entry 除 entry_hash 外全字段 + prev_hash))`，
   `verify()` 从创世哈希逐条复算，定位首个坏条目；人工覆盖尝试也留 `outcome=REJECTED` 条目。

工具链：`python -m pytest tests/capability_ledger_auto`（用例 91 例，其中本节点新增 38 例，`artifacts/evidence_state_v2/tests.json`）+ 验收脚本（场景矩阵 / 判据数字 / 图 / 审计链）。

## ③ 计算结果（程序化注入）

### 场景矩阵（n=14，全部命中预期：14/14）

| 场景 | 独立 PASS 锚 | 来源类别 | 不可判锚 | 反例 | 判定 | 规则 | 门禁 |
|---|---|---|---|---|---|---|---|
| S01_no_trace | 0 | 0 | 0 | 0 | NOT_SUPPORTED | R01_NO_TRACE | BLOCK |
| S02_stub_marker | 0 | 0 | 0 | 0 | STUB | R02_STUB_MARKER | BLOCK |
| S03_no_anchor | 0 | 0 | 0 | 0 | WITHHELD | R09_WITHHELD_NO_ANCHOR | ALLOW |
| S04_one_anchor | 1 | 1 | 0 | 0 | WITHHELD | R08_WITHHELD_INSUFFICIENT_ANCHORS | ALLOW |
| S05_two_anchors | 2 | 2 | 0 | 0 | WITHHELD | R08_WITHHELD_INSUFFICIENT_ANCHORS | ALLOW |
| S06_three_independent | 3 | 3 | 0 | 0 | REAL | R06_REAL_THRESHOLD_MET | ALLOW |
| S07_dup_source | 1 | 1 | 0 | 0 | WITHHELD | R08_WITHHELD_INSUFFICIENT_ANCHORS | ALLOW |
| S08_single_kind | 3 | 1 | 0 | 0 | WITHHELD | R08_WITHHELD_INSUFFICIENT_ANCHORS | ALLOW |
| S09_test_failure | 0 | 0 | 0 | 0 | MOCK | R04_TEST_FAILURE | BLOCK |
| S10_counterexample | 3 | 3 | 0 | 1 | MOCK | R03_COUNTEREXAMPLE | BLOCK |
| S11_undecidable_band | 0 | 0 | 1 | 0 | UNDECIDABLE | R07_UNDECIDABLE_INCONCLUSIVE | BLOCK_UNDECIDABLE |
| S12_undecidable_no_ref | 0 | 0 | 1 | 0 | UNDECIDABLE | R07_UNDECIDABLE_INCONCLUSIVE | BLOCK_UNDECIDABLE |
| S13_adverse_undecidable | 3 | 3 | 1 | 0 | UNDECIDABLE | R05_UNDECIDABLE_ADVERSE | BLOCK_UNDECIDABLE |
| S14_partial_undecidable_supportive | 3 | 3 | 1 | 0 | REAL | R06_REAL_THRESHOLD_MET | ALLOW |

### 生命周期序列（同一能力：发现 → 锚积累 → 反例 → 再验证 → 不可判锁定）

| # | prev | new | 变更方向 | 规则 | 首条理由 |
|---|---|---|---|---|---|
| 0 | None | WITHHELD | INITIAL | R09_WITHHELD_NO_ANCHOR | 有实现但未收集到任何验证锚：证据不足，能力可能存在 |
| 1 | WITHHELD | WITHHELD | HOLD | R08_WITHHELD_INSUFFICIENT_ANCHORS | 独立决定性 PASS 锚 1 < 3 |
| 2 | WITHHELD | WITHHELD | HOLD | R08_WITHHELD_INSUFFICIENT_ANCHORS | 独立决定性 PASS 锚 2 < 3 |
| 3 | WITHHELD | REAL | UPGRADE | R06_REAL_THRESHOLD_MET | 独立决定性 PASS 锚 3 ≥ 3 |
| 4 | REAL | MOCK | DOWNGRADE | R03_COUNTEREXAMPLE | 决定性反例触发否证 |
| 5 | MOCK | REAL | UPGRADE | R06_REAL_THRESHOLD_MET | 独立决定性 PASS 锚 3 ≥ 3 |
| 6 | REAL | UNDECIDABLE | UNDECIDABLE_LOCK | R05_UNDECIDABLE_ADVERSE | 偏坏侧不可判锚 1 条：证据不闭合 |

转换统计：{"INITIAL": 1, "UPGRADE": 2, "DOWNGRADE": 1, "HOLD": 2, "UNDECIDABLE_LOCK": 1, "REJECTED": 0}；
降级事件 1 次（规则 R03_COUNTEREXAMPLE，理由已落链：True）；
零人工覆盖：3/3 次人工指定等级被拒（码 MANUAL_OVERRIDE_FORBIDDEN），
状态保持不变=True，拒绝尝试全部留痕=3 条；
无触发证据的降级被拒 = True（守卫台账状态保持 REAL）。

### 不可判态量化（判决分区扫描）

网格 81×25=2025 格
（rel_error∈[0,0.4]，uncertainty∈[0,0.12]，步长 0.005）：

- 朴素规则（只看 `err < tol`）判 PASS 的格数：**750**
- 加不确定度带（fail-closed）后判 PASS 的格数：**240**
- 判 UNDECIDABLE 的格数：**1125**
- 朴素 PASS → 不可判（被 fail-closed 拦下）的格数：**510**
  （占朴素 PASS 的 **68.0%**）

即：在参考值不确定度 0–0.12 的现实区间，朴素阈值规则
有 68% 的「通过」在统计上站不住。

### 能力发现联动（C6）

| 能力 | 痕迹数 | 测试通过率 | 初始等级 | 规则 |
|---|---|---|---|---|
| synth.disc_good | 4 | 0.96 | REAL | R06_REAL_THRESHOLD_MET |
| synth.disc_bad | 2 | 0.25 | MOCK | R04_TEST_FAILURE |
| synth.disc_empty | 0 | 0.00 | NOT_SUPPORTED | R01_NO_TRACE |

重复登记被拒 = True；初始等级由扫描证据决定（非默认 WITHHELD、不可由调用方指定）。

### 审计链（C7）

条目 4，链校验 True（首个坏条目 None），
head_hash=`114e4712d95ce980…`；伪造 `entries[2].new_state REAL→MOCK` 后
篡改检测 False（首个坏条目 seq=2）。
全实验 provenance 条目合计 28。

## ④ 图

1. `artifacts/evidence_state_v2/figures/decision_map.png`
   —— 单锚判决分区（rel_error × 参考值 1σ 不确定度 → PASS/FAIL/UNDECIDABLE），叠加 14 个场景的验证锚；
2. `artifacts/evidence_state_v2/figures/state_delta.png`
   —— 场景矩阵上「朴素规则 vs fail-closed v2」的等级分布对照
   （UNDECIDABLE 1 → 3，
   MOCK 3 → 2）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **陷阱：不可判态被当成 WITHHELD 回收**。这是本波最主要的设计风险——若把 UNDECIDABLE
   归一到 WITHHELD，门禁会放过一批统计上不可分的证据。本实现用独立秩 -1
   与独立门禁码 `BLOCK_UNDECIDABLE` 双保险，并有 `test_undecidable_distinct_from_withheld_semantics` 守门。
2. **陷阱：同源重复刷锚**。三条锚共用同一数据集时独立锚数仍为 1（场景 S07：collapsed=2），
   否则「≥3 独立锚」条款等于虚设。
3. **陷阱：偏坏侧不可判反被升级**。只要存在一条中心估计落在坏侧且带内跨线的锚（S13），
   即便已有 3 条决定性 PASS，也锁 UNDECIDABLE（fail-closed 优先于升级）。
4. **局限 1**：不确定度按 1σ 相对量线性传递（z=2），未做非对称/分布形状建模；对强非线性误差传播应改用区间或 MC。
5. **局限 2**：`anchors` 的输入仍由调用方提供，引擎只保证「给定锚则判决可复算」，
   不验证锚本身来源的真实性（该职责在原 `scanner.py` 的哈希证据链）。
6. **局限 3**：本波未改 `backend/api/capability_audit.py` 的对外矩阵接口（保持最小改动），
   状态机目前是库级能力；对外暴露与台账 JSON 合流留作后续节点。
7. **适用范围**：任何「有参考值 + 有误差 + 有不确定度」的能力验收；无参考值的场景应显式落在
   UNDECIDABLE 而不是被硬编码 PASS。

## ⑥ 误差判定（关键量 vs 基准）

| 量 | 基准 | 本实现 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 场景判定命中率 | 场景预期状态（14 例） | 14/14 | 0 | 是（0%） |
| 单锚判决边界 | 解析分区（err+band<tol / err-band>tol / 其余） | 网格 2025 格逐格与解析式一致 | 0 格偏差 | 是（0%） |
| 独立性计数 | 同源锚应折叠为 1 | S07：3 锚 → 1 独立锚 | 0 | 是（0%） |
| 哈希链完整性 | 复算 sha256 链 | verify=True，篡改定位 seq=2 | 0 | 是（0%） |
| C1–C7 验收 | 战役判据 | 7/7 PASS | 0 | 是（0%） |

判决分区与解析式逐格一致是**构造性验证**（同一公式实现），真正的物理精度验证需在
领域仓的真实算例锚点上做；本节点只保证等级语义与裁决机制的正确性。

### 验收判据汇总

| 判据 | 结论 |
|---|---|
| C1_semantics | PASS |
| C2_undecidable_fail_closed | PASS |
| C3_upgrade_rules | PASS |
| C4_downgrade_rules | PASS |
| C5_no_manual_override | PASS |
| C6_discovery_integration | PASS |
| C7_audit_provenance | PASS |

## 复现

```bash
PYTHONPATH=src python -m pytest tests/capability_ledger_auto -q
PYTHONPATH=src python scripts/evidence_state_v2_acceptance.py
PYTHONPATH=src python scripts/evidence_state_v2_report.py
```
