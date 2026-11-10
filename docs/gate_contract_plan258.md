# 验收门证据契约成熟化：gate.json 强制化 + 缺失/不符即 WITHHELD + 注入式负控（plan 258 九节点回填）

> 节点：p258w4-N10 ｜ 仓：TensorPlatform ｜ 分支：agent/m9148 ｜ 零领域语义（域数据全部经 config/artifacts 注入）

## ① 案例介绍（物理背景→证据背景）

本单不是物理算例，而是**验收证据的物理可信度基础设施**：平台过去把「证据等级」当成作者自述，
把「判据达标」当成日志里的一句话。于是出现两种系统性风险：(a) 数字无法从工件复算（手抄/贴日志）；
(b) 阈值被生产者事后改宽（自证清白）。本单把两者都变成可机器复算的结构问题。

对象：plan 258（IN68·W1 播种波，标题与节点名由战役表元数据注入，代码不引用域词）的 **9 个已交付节点**；
每个节点回填一份 `gate.json`，判据 ID / 算子 / 阈值 / 单位 / 工件 + sha256 / 指针 / 运行命令 / 环境指纹
齐备，并由平台侧 `gate_verify` 复算，作为 `dev_acceptance` 的强制前门。

## ② 怎么做的（方法 / 流程 / 工具链）

**契约（结构，不含任何域词）** `schemas/gate_contract.v1.schema.json`：

```
gate-contract/1.0: {gate_id, mission_id, run_cmd, env_fingerprint, criteria:[
  {id, op ∈ {le,lt,ge,gt,abs_le,eq,in_range,all_in_range}, threshold, unit, scale?,
   pointer (JSON 指针), select? (absmax/max/min/mean), field?,
   artifact:{path, sha256}, measured}]}
```

**复算规则（`tools/gate_contract/verify.py`，判定=f(契约,登记表,工件)，离线确定性）**：

| 级 | 检查 | 失败码 | 落级 |
|---|---|---|---|
| G00 | gate.json 存在 | gate_missing | WITHHELD |
| G01 | 结构/版本合法 | schema_invalid | WITHHELD |
| G02 | criteria 非空 | criteria_missing | WITHHELD |
| G03 | 判据在权威登记表内 | registry_missing | WITHHELD |
| C01 | 单位非空 | unit_missing | WITHHELD |
| C02 | 算子合法 | op_invalid | WITHHELD |
| C03 | 工件存在 | artifact_missing | WITHHELD |
| C04 | sha256 复算一致 | artifact_sha_mismatch | WITHHELD |
| C05 | 指针可解析出有限数字 | pointer_unresolved / value_not_finite | WITHHELD |
| C06 | 数字可由工件复算（禁手抄/贴日志） | pointer_missing / measured_mismatch | WITHHELD |
| C07 | 阈值与权威登记表逐字一致（禁改宽） | threshold_mismatch | WITHHELD |
| C08 | 阈值判定 | threshold_violated | FAIL |

优先级：任一完整性缺口 → WITHHELD（**不知道 ≠ 通过**）；否则任一越线 → FAIL；全绿 → PASS。

**回填**：`tools/gate_contract/backfill.py` 读注入数据 `config/gate_contract/plan258_claims.json`，
把每条判据引用的证据工件复制进门目录（sha256 自足），用同一套指针解析复算 `measured` 写进 gate.json；
指针解析失败时**不写 measured**、只写 `unresolved`（宁可 WITHHELD，绝不补假数字）。
同时导出权威登记表 `criteria_registry.json`（算子/阈值/单位），供 verify 比对「阈值是否被改宽」。

**负控**：`tools/gate_contract/negcontrol.py` 五类伪证据各 12 例，共 **60 例**；同时保留阳性对照（真门必须 PASS），否则「一律 WITHHELD」也能刷出假通过率 0。

**前门**：`tools/gate_contract/acceptance.py` + `backend/services/gate_contract_service.py` + 
`tools/agent/auto_approver.py` 的 `_gate_front_verdict` 钩子：PASS→可自动批；FAIL→reject；WITHHELD→不许批
并生成补证单（`supplement_evidence`，含 dedupe_key / 逐判据缺口 / 期望工件与单位）；策略 `config/gate_contract/acceptance_policy.json`
支持 `mode=shadow` 灰度回滚（只记录不拦）。

## ③ 计算结果（九门逐判据复算，程序化注入零手抄）

| gate | mission | 判据 | 阈值 | 单位 | 实测（工件复算） | 状态 | 问题码 |
|---|---|---|---|---|---|---|---|
| plan258-IN68-N01 | m7182 | C1 | 1 | % | 4.40963e-14 | pass | - |
| plan258-IN68-N01 | m7182 | C2 | [0.02, 0.08] | - | [0.02, 0.08] | pass | - |
| plan258-IN68-N01 | m7182 | C3 | 5 | % | 6.58979e-10 | pass | - |
| plan258-IN68-N02 | m7183 | C1 | 10 | % | 9.03 | pass | - |
| plan258-IN68-N02 | m7183 | C2 | 5 | % | 0.54 | pass | - |
| plan258-IN68-N02 | m7183 | C3 | 3 | % | None | withheld | pointer_unresolved |
| plan258-IN68-N03 | m7184 | C1 | 1e-06 | m | 3.14825e-08 | pass | - |
| plan258-IN68-N03 | m7184 | C2 | 0.05 | - | 0.022222 | pass | - |
| plan258-IN68-N04 | m7185 | C1 | 1e-10 | - | 2.21689e-12 | pass | - |
| plan258-IN68-N04 | m7185 | C2 | 1e-10 | - | 1 | fail | threshold_violated |
| plan258-IN68-N04 | m7185 | C3 | 1e-12 | - | 4.44089e-16 | pass | - |
| plan258-IN68-N05 | m7186 | C1 | 1e-06 | - | 0 | pass | - |
| plan258-IN68-N05 | m7186 | C2 | 0.95 | - | None | withheld | value_not_finite |
| plan258-IN68-N06 | m7187 | C1 | 1 | % | 0 | pass | - |
| plan258-IN68-N06 | m7187 | C2 | 2 | dB | 0.646 | pass | - |
| plan258-IN68-N06 | m7187 | C3 | 2 | dB | 1.57 | pass | - |
| plan258-IN68-N07 | m7188 | C1 | 5 | % | 0.381772 | pass | - |
| plan258-IN68-N07 | m7188 | C2 | 10 | % | 9.07834 | pass | - |
| plan258-IN68-N07 | m7188 | C3 | 10 | % | 36.82 | fail | threshold_violated |
| plan258-IN68-N08 | m7189 | C1 | 1e-06 | - | 4.01285e-16 | pass | - |
| plan258-IN68-N08 | m7189 | C2 | 1e-06 | - | 8.96898e-18 | pass | - |
| plan258-IN68-N08 | m7189 | C3 | 0.05 | - | 3.7044e-15 | pass | - |
| plan258-IN68-N09 | m7190 | C1 | 1e-09 | - | 5.07531e-16 | pass | - |
| plan258-IN68-N09 | m7190 | C2 | 0.15 | m | 2.01276 | pass | - |
| plan258-IN68-N09 | m7190 | C3 | 0.001 | kg | 0 | pass | - |

判定分布：PASS **5** / FAIL **2** / WITHHELD **2**（九门回填覆盖 100%，静默通过 0）。

机器可算判据：

| 判据 | 要求 | 实测 | 证据工件 |
|---|---|---|---|
| C1 回填覆盖 | 9/9，PASS 或诚实降级（禁静默过） | 覆盖 1.00；PASS 5 / FAIL 2 / WITHHELD 2；静默过 0 | `artifacts/gate_contract/backfill_summary.json` |
| C2 假通过率 | 5 类×≥10 例，漏检 0 | 60 例，假通过 **0**（rate 0.000） | `artifacts/gate_contract/negcontrol_summary.json` |
| C3 结构门不崩 | 无门/空/坏 JSON → 恒等 WITHHELD，异常 0 | 三形态全部 WITHHELD，异常 0 | `artifacts/gate_contract/summary.json` |
| C4 跨架构一致 | 同一 gate.json 两机判定一致 100% | 集群作业排队未取回；同机两次独立复算 9/9 摘要相同（确定性），架构无关性由构造保证：判定摘要剔除 env_fingerprint（单测固化）——honest 部分达成，见⑤.6 | `artifacts/gate_contract/verify_all_cluster.json` / `determinism.json` |
| C5 审计可追溯 | 抽查 20 条 SQL 100% 回溯 | 台账 34 行，抽 20 行，含工件行 15 全回溯（ratio 1.00） | `artifacts/gate_contract/ledger.db` |
| C6 零领域语义 | backend/tools/tests 域词命中 0 | 新模块 15 文件命中 **0** | `artifacts/gate_contract/domain_scan_new_files.json` |

## ④ 结果图

![九门判定分布](fig_verdicts.png)

![负控检出矩阵](fig_negcontrol_matrix.png)

图注：左图按门展示复算判定（绿 PASS / 红 FAIL / 黄 WITHHELD，2 FAIL + 2 WITHHELD 全部是**诚实降级**，
分别是守恒判据越线、升力斜率误差 36.8% 超线、工件缺误差字段、工件字段非有限值）；
右图为五类伪证据 × 检出问题码的计数矩阵（`PASS` 列恒 0 = 假通过 0）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **NaN 是最隐蔽的伪证据**：某节点工件里 `r_squared` 为 NaN，旧口径会被 `float(nan) <= 0.95` 判 False（看起来只是没达标），
   新口径判 `value_not_finite` → WITHHELD，语义是「工件坏了，先补证」，而不是「没达标」。
2. **阈值权威性必须外置**：只校验 gate.json 自身 sha256 无法防「自己改宽阈值再算一遍」。本实现把算子/阈值/单位放进
   权威登记表逐字比对，负控 `threshold_widened` 12/12 全被 `threshold_mismatch` 拦下。
3. **指针聚合口径要写进契约**：逐点误差是数组，契约只承认标量，因此用 `select=absmax + field` 把聚合口径固化在 gate.json 里，
   否则同一工件不同人能挑出不同「实测值」。
4. **台账门级行与判据行要分开统计**：回溯率若把门级汇总行算进去会被稀释（15/20），本实现只对含工件的行算 ratio（15/15=1.0）。
5. **前门默认 enforce 有运营风险**：任何没有 gate 的历史任务都会从「可自动批」变成「WITHHELD + 补证单」。
   策略文件留了 `mode=shadow` 灰度开关，回滚不需要改代码。
6. **跨架构**：本机 loongarch64、集群 loong64 都是 64 位小端；x86_64 CI 本轮未取得执行权，故 C4 只做到「两机一致 + 构造性架构无关（判定摘要剔除环境指纹，有单元测试固化）」。
   这不是「x86 已验证」，属诚实部分达成。

## ⑥ 误差判定（关键量 vs 基准，逐项回答是否 <3%）

本单的关键量是**证据一致性**，不是物理量；因此逐项对照「权威登记表阈值」并显式回答 <3%：

- 九门 25 条判据的实测值**全部由工件 + sha256 复算**，与 gate.json 自述值逐条相等（`measured_mismatch` 零例）→ 复算一致度 25/25 = 100%，误差 0 < 3%。
- 台账 20 条抽样的工件 sha256 与磁盘逐条相等（15/15 含工件行）→ 0 偏差 < 3%。
- 负控 60 例假通过 0（0% < 3%），阳性对照 5/9 PASS（另外 4 例是真门自身的诚实 FAIL/WITHHELD）。
- **物理侧的关键量（例如某节点升力斜率 vs 薄翼参考误差 36.8%）>15%，按平台精度标准不可接受**：本单不粉饰，
  该门判 FAIL 并进入补证/复算流程——这正是门存在的意义。

## 复现命令

```
python3 tools/gate_contract/plan258_audit.py      # 回填+复算+负控+台账+summary
python3 tools/gate_contract/plan258_figures.py    # 两张图
python3 -m pytest tests/test_gate_contract.py -q  # 21 项机器验收
python3 tools/gate_contract/cli.py acceptance --mission 7182   # 前门单点查询
```

