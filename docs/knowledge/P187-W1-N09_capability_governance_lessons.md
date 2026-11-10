# 知识蒸馏 — 能力治理三机制的六条可复用规则（P187-W1-N09）

- **来源**：mission 9811 / 节点 P187-W1-N09，仓库 TensorPlatform，分支 `agent/m9811`，
  提交 `b057bdb87`（主体）+ `982eb0096`（工件刷新）；证据 `artifacts/capability_governance/`。
- **复现**：`python -m services.capability_governance.cli run --out-dir artifacts/capability_governance`
  → `python3 tools/capability_governance/gen_report.py` → `python -m pytest tests/capability_governance -q`（38 passed）。
- **最后复核日期**：2026-11-10（本次运行全判据 C1–C7 PASS，报告 id 11683）。
- **评审状态**：自证（作者即实现者），待独立席位按 S×E 复判。

## R1 — 「自动升账」必须先量自己的假阳性率，且必须有单门基线对照

**规则**：任何把自述等级写成 REAL 的自动化，交付时必须附（i）带标签的冻结集、（ii）准确率/假阳性率、（iii）一个「只做一次数值检查」的单门基线。

**依据（本节点实测）**：同一 63 端点冻结集，四门机制 acc=1.0000 / FPR=0.0000；单门基线（设计点 + 1% 宽松容差）acc=0.6349 / FPR=0.3704（20 例假阳性）。没有这个对照，"准确率 100%" 只是"没测过"。

**反例/局限**：负例是构造缺陷模式（常数、等比常数、偏置、抖动、单位错、截断、量化格、调用序漂移），比生产流量容易 → 报出的 FPR 是**下界**，不是现场估计。若只跑单门基线，常数端点（设计点误差恰为 0）会被判 REAL。

**回归保护**：`tests/capability_governance/test_backtest.py::test_four_gate_mechanism_beats_single_gate_ablation`
（断言基线 FPR 必须严格高于机制 FPR；一旦哪天两者相等，说明闸门退化成单门，测试变红）。

## R2 — 证据链里的 sha256 字段是**声明**，只有对着文件核过才算证据

**规则**：`test_report.sha256` 这类字段必须在落账时校验：报告文件存在且哈希一致，否则判 `rejected`。

**依据**：负控里「把 test_report.sha256 改成 64 个 0 但重算 chain_digest」的伪造链，在加入文件哈希核对之前能顺利通过结构校验（chain_digest 只是内容寻址，不保证内容真实）；加入核对后 4/4 负控全部拒绝。

**反例**：只校验 `chain_digest` 自洽 → 任何改过内容再重算摘要的链都"合法"。这是最常见的假证据形态。

**回归保护**：`test_ledger_upgrade.py::test_report_file_mismatch_is_rejected`、
`::test_missing_report_file_is_rejected`、`::test_forged_pass_flag_is_rejected`。

## R3 — 台账只能"只升不静默降"：回归必须留痕并可查询

**规则**：REAL→WITHHELD 的每一次下降都要写 `regression` 事件（含 level_before、链摘要、原因）与计数，不允许静默覆盖；同一链重复提交必须 noop。

**依据**：`test_regression_is_recorded_not_swallowed`（counters.regression=1，entry.regressions[0].level_before=REAL）、`test_reapply_is_idempotent`（created=1、updated=0、applied_chains=1）。

**反例**：幂等性靠"跑得慢"实现（每次全量重写台账），会导致升级计数虚高、无法判断"这次升级是不是新证据带来的"。

## R4 — 多 arm 偏差分解：每一级纠正都要作用在**同一个坐标系**上

**规则**：对 arm 做逐级纠正（格式→输出定义→输入定义→离散化）时，网格序列/中间量必须与当前坐标一致：每级纠正必须同样作用到下一级要用到的所有数值上。

**依据（踩坑）**：第一版把 arm 的 `grid_levels` 当成物理坐标，而 `value` 已被单位缩放——结果离散化通道把格式差异"解释"成 −1000 倍份额（残差 1000×D0），格式维份额虽对但归因链荒谬。

**依据（第二坑）**：输入维纠正只作用于报告值、不作用于网格序列，于是网格外推又把输入偏移带回来（实测：输入维份额 1.0 同时残差 1.0，伸缩恒等式虽成立但归因无效）。修正为「每级数值都过同一套纠正」后，单维用例残差 = 0（<1e-9），伸缩恒等式份额+残差=1 精确成立。

**回归保护**：`test_attribution.py::test_telescoping_identity_holds_for_every_arm`、
`::test_single_dimension_is_recovered_exactly`（四维各一例，断言主因维正确且该维份额=1）。

## R5 — 归因链必须有"我说不"的用例

**规则**：残差门只有能拒绝才可信。交付里必须包含一条**故意不可归因**的用例（未建模的 3% 差异 + 单一网格层），并断言其 `attributed=False`、残差份额=1。

**依据**：`case_unexplained`；批级归因完备率因此为 0.8333（5/6），而不是被粉饰成 1.0。

**反例**：若该用例也带两级网格，Richardson 外推会把 3% 差异"吸收"成网格维份额、残差=0 —— 归因链看起来完美，实际在替未建模误差背书。

## R6 — 「一万条样本」的证明力来自去重键 + 溯源块 + 库上审计，不来自行数

**规则**：结构化样本表必须加内容寻址去重键（sha256(规范化输入)）+ 溯源块（规格/模型/生成器摘要、时间、run id），加载幂等，审计数字从数据库读回（不是生成器自报）。

**依据**：10800 行 → 唯一率 1.0000、溯源完整率 1.0000、output 非空率 1.0000、二次加载插入 0 行、库文件 sha256 记录在审计 JSON。

**局限**：这批行的数值来自规格声明的通用 IVP 模型（`spec.model.kind=ivp_rk4`），**没有任何外部参考对标**，因此只能作为"机制产出 + 结构完整性"证据，不能当领域精度证据。换真实求解器只换 `spec.model`，代码不动。

**回归保护**：`test_samples.py::test_load_is_idempotent`、
`::test_audit_numbers_come_from_the_database`、`::test_provenance_block_has_every_required_field`。

## 适用范围与不适用

- 适用：任何"端点/模块自述能力等级"的自动判级；任何多求解器/多实现结果比对；任何需要"可追溯样本库"的场景。
- 不适用：① 没有独立参考解、也没有输入敏感性可言的纯黑箱服务（五门里的数值门与响应门都失效，只能退化为 WITHHELD）；
  ② 强非线性输入响应场景下，R4 的输入维用一阶对数-线性灵敏度，误差会漏进残差（可见，不隐藏）；
  ③ 若被测能力的"正确值"本身就是随机量，需先定义统计判据，再谈升账。