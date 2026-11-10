# p262w1-N07 能力台账自动升账机制成熟化（证据链 + append-only sha256 审计）

- 战役节点：`p262w1-N07-capability-auto-audit`
- 落库：`TensorPlatform`（`tools/capability_auto_audit/`，HTTP 面挂在 `backend/main.py` → `POST /api/v1/capabilities/{capability_id}/auto-audit`）
- 扫描/运行时刻 commit：`1ee4017324d29e0ee9a49a3ebf3236c2f596350e`（本节点代码提交为其直接后继；工件内全部数字由该时刻的工作树跑出）
- 规格摘要：`spec_digest = 56a3cdc157b125b69a0eb2ac7405a0b8d2e7586210607d6b65eb77adb9a9cd5b`
- 工件：`artifacts/capability_auto_audit/acceptance_summary.json`（content_digest `343af7edaa21f7b237b0d40aeb9610e6cccf8189c842849200e5580231bc81e0`）+ `f4_audit_log.jsonl` + `fig_n07_overview.png`

## 1 案例介绍（这是什么问题）

平台的能力台账（`config/capabilities.json`）把每项能力标成 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED。
过去的等级是**人写的**：人会写 REAL，而仓里可能只有随机数、`NotImplementedError`，或者测试是红的。
上游两代机制已经把「等级怎么判」机制化（`tools/capability_ledger_auto` 扫描判级、`tools/metric_gate` 写入期数字重算）。
本节点补的是它们之间缺的一环：**当一个新的验证案例（verified benchmark）落地并通过机器门时，
等级必须自动升上去；当回归失败或证据链断裂时，等级必须自动降回去，而且全过程留可验证的审计链。**

本节点定义的证据链是五环，缺一环即「不完整」：

1. **artifact**：`verified/<capability_id>/artifact.json`，必须带输入内容摘要 `inputs_sha256`（证明这个数字是某次真实输入算出来的）
2. **pytest**：junit-xml 或 json 汇总，`passed ≥ 3`、`failed ≤ 0`
3. **report**：`report.json` 必须包含六要素 `case, method, results, figures, notes, error_judgement` 且内容非空
4. **error**：`metrics.json` 给原始 `measured` / `reference` 与**自称**误差；自称值必须由平台度量门（上游 `tools/metric_gate`）按 `abs(measured - reference) / abs(reference)` 重算后一致，且重算值 ≤ 0.03
5. **review**：`review.json`，独立复核人 + 通过词表

关键设置（全部由 `config/capability_chain_spec.json` 注入，代码里没有任何域词条）：

| 规格项 | 值 |
|---|---|
| 最小 pytest 通过数 | 3 |
| 允许失败/错误数 | 0 / 0 |
| 报告六要素 | case, method, results, figures, notes, error_judgement |
| 误差阈值 | 0.03 |
| 误差重算式 | `abs(measured - reference) / abs(reference)` |
| 声明一致性容差 | 1e-09 |
| 独立复核要求 | True |
| 建模默认等级 | WITHHELD |

## 2 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**四段流水线**（`tools/capability_auto_audit/`）：

| 模块 | 职责 |
|---|---|
| `evidence_chain.py` | 证据发现 + 五环校验 → `ChainVerdict`（完整/缺哪环/失败类型/64 位 chain_digest）。误差环委派 `tools.metric_gate.MetricGate` 重算，自称值与重算值不符即拒（防手抄数字） |
| `level_machine.py` | 等级状态机 `decide(current_level, verdict)`：证据链完整 → 升 REAL；REAL 且回归证据 → 降 MOCK；REAL 且证据缺失 → 降 WITHHELD；非 REAL 且链不完整 → 保持（不凭「没证据」继续往下踩） |
| `append_only_log.py` | append-only JSONL 审计日志：每条含 `seq / prev_sha256 / sha256`，哈希覆盖除自身 sha256 外全部字段；`verify()` 可检出改写、删条、乱序；损坏日志拒绝继续追加 |
| `auto_audit_engine.py` | 编排：`audit(cap)`（单次）+ `watch_once()`（按证据目录树签名变化触发）+ `run()/start_background()`（轮询监听器，默认 0.2 s）+ 原子替换的等级状态文件 |
| `api.py` | `POST /capabilities/{id}/auto-audit`、`GET /capabilities/{id}/level`、`GET /capabilities/levels`、`GET /capabilities/audit-log`、`GET /capabilities/audit-log/verify`；鉴权依赖可注入，`backend/main.py` 挂的是平台 `require_auth` |
| `n07_acceptance.py` | 本报告全部数字的来源脚本（重跑一次即重算 F1–F6 与图） |

状态机（机器可读表也在工件的 `transition_table` 里）：

| from | 条件 | 动作 | 目标 |
|---|---|---|---|
| REAL | complete | HOLD | CHAIN_COMPLETE_ALREADY_REAL |
| REAL | broken/regression | DOWNGRADE | MOCK |
| REAL | broken/evidence | DOWNGRADE | WITHHELD |
| MOCK | complete | UPGRADE | REAL |
| STUB | complete | UPGRADE | REAL |
| WITHHELD | complete | UPGRADE | REAL |
| NOT_SUPPORTED | complete | UPGRADE | REAL |

**与上游的集成**：误差环不自己写公式，调 `tools.metric_gate`（p262w1-N06）注册 `abs(measured - reference) / abs(reference)` 并 `check()`；
可复算证据里记录 `gate_source`（本机运行为 `metric_gate`；若该模块不可导入会退化为 `inline` 并在证据中显式降级标注）。

**监听器语义**：`verified/<cap>/` 目录树的 (相对路径, 大小, mtime_ns) 列表做 sha256 签名，签名变化即触发重新裁决；
因此「新增工件」与「删除工件」同一路径覆盖，不需要额外的事件源。

## 3 计算结果（数字全部由 acceptance_summary.json 程序化注入）

### 3.1 F1 五个已知案例（3 完整 / 2 不完整）

| 能力（合成） | 期望 | 判定 | reason_code | 缺失环 | 正确 |
|---|---|---|---|---|---|
| demo.case_a | 期望完整 | 判为完整 | CHAIN_COMPLETE | — | ✅ |
| demo.case_b | 期望完整 | 判为完整 | CHAIN_COMPLETE | — | ✅ |
| demo.case_c | 期望完整 | 判为完整 | CHAIN_COMPLETE | — | ✅ |
| demo.case_d | 期望不完整 | 判为不完整 | PYTEST_BELOW_THRESHOLD | pytest | ✅ |
| demo.case_e | 期望不完整 | 判为不完整 | REPORT_INCOMPLETE | report | ✅ |

- 正确率 **5/5 = 100.0%**（完整 3 例、不完整 2 例）

### 3.2 F2 自动升级 / F3 自动降级（时限 5 s）

| 判据 | 场景 | 等级迁移 | 动作 | 耗时 [s] | 判定 |
|---|---|---|---|---|---|
| F2 升级 | demo.latency_up | WITHHELD → REAL | UPGRADE | 0.036906 | PASS |
| F3 降级 | demo.latency_down | REAL → WITHHELD | DOWNGRADE | 0.005094 | PASS |

F3 的触发动作是**物理删除** `verified/<cap>/` 目录（证据链断裂），reason_code = `MISSING_ARTIFACT_ON_EVIDENCE_LOSS`。

### 3.3 F4 十次操作的审计链

- 操作数 **10** → 日志条目 **10**
- 链校验 `verify().ok = True`；每条 sha256 复算一致 = True；prev 链完整 = True；seq 连续 = True
- head_sha256 = `a5d2cac8edcff0897cd5c1acbdfa9b9b3304e3fc4b763833fc916c458f9272c6`

| seq | 能力（合成） | 迁移 | 动作 | reason_code |
|---|---|---|---|---|
| 1 | demo.ops_a | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |
| 2 | demo.ops_b | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |
| 3 | demo.ops_a | REAL → WITHHELD | DOWNGRADE | MISSING_ARTIFACT_ON_EVIDENCE_LOSS |
| 4 | demo.ops_b | REAL → WITHHELD | DOWNGRADE | MISSING_ARTIFACT_ON_EVIDENCE_LOSS |
| 5 | demo.ops_a | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |
| 6 | demo.ops_b | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |
| 7 | demo.ops_a | REAL → WITHHELD | DOWNGRADE | MISSING_ARTIFACT_ON_EVIDENCE_LOSS |
| 8 | demo.ops_b | REAL → WITHHELD | DOWNGRADE | MISSING_ARTIFACT_ON_EVIDENCE_LOSS |
| 9 | demo.ops_a | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |
| 10 | demo.ops_b | WITHHELD → REAL | UPGRADE | CHAIN_COMPLETE |

### 3.4 F5 零领域语义 / F6 测试

- 扫描本节点交付的 5 个模块、531 个字符串常量，对照平台域词表 21 个 ASCII 词 + 16 个 CJK 词：命中 **0** 处
- `pytest tests/capability_auto_audit`：**60 passed**（阈值 ≥ 15），returncode = 0

### 3.5 图

`artifacts/capability_auto_audit/fig_n07_overview.png`（sha256 `e8e7cd12e1b5a530…`）：左=五案例×五环证据矩阵；中=F2/F3 实测耗时（对数轴，含 5 s 验收线）；右=等级状态机与 F4 审计链结论。

## 4 结果图

见图 `fig_n07_overview.png`（内嵌于平台报告）。三面板对应「证据链是否完整」、判据时延、状态迁移与审计链。

## 5 注意点（踩过的坑 / 局限 / 适用范围）

1. **机制只保证「证据链自洽」，不保证物理正确**：一个伪造的 `metrics.json`（measured/reference 都编）只要自称误差=重算误差就能升 REAL。
   本节点挡住的是「手抄数字 / 缺报告要素 / 删工件不降级」这一类，不是「物理造假」。物理真实性由上层的 verified 案例评审与误差阈值负责。
2. **mtime 是变化信号**：目录树签名用 mtime_ns，所以「同内容重写」也会触发一次重裁决（幂等，结果为 HOLD，但会多一条日志）。
   反过来说，如果文件系统时间戳被回拨，监听器可能漏一次事件——这不是安全边界，是轮询式监听器的固有限制。
3. **append-only 是「可检出」不是「不可写」**：日志文件对进程仍是可写的，`verify()` 只能发现篡改，不能阻止；真正的不可变性要靠外部对象存储/WORM 介质。
4. **非 REAL 不会因为「没证据」继续降级**：状态机只降 REAL，避免「证据目录搬走就把 MOCK 打成 WITHHELD」的语义污染。
5. **F1–F4 用的是合成能力（`demo.case_*` / `demo.ops_*`）**：它们是机制夹具，不是任何物理能力的验收声明；真实能力接入时只需把 verified 工件落到同名目录。
6. **inline 退化**：`tools/metric_gate` 不可导入时误差环比对退化为内置表达式，并在证据里记 `gate_source="inline"`（可见的降级，不是静默放过）。

## 6 误差判定（逐项，显式回答是否 <3%）

本节点是**后端机制**，没有物理场量，因此不存在「仿真 vs 实验」的误差可比。可比的基准是工件的自洽性与时延余量，逐项如下：

| 关键量 | 基准 | 实测 | 误差/余量 | 是否 <3% |
|---|---|---|---|---|
| F1 证据链裁决正确率 | 人写定的 5 例期望标签 | 5/5 | 0.0% 错分 | **不适用**（离散裁决，要求 100%，实测 100%） |
| F2 升级时延 | 验收线 5 s | 0.036906 s | 余量 135× | 是（两个数量级以上） |
| F3 降级时延 | 验收线 5 s | 0.005094 s | 余量 982× | 是（两个数量级以上） |
| F4 审计链完整性 | 每条 sha256 复算 + prev 链 + seq 连续 | 全一致 = True | 0 缺陷 | 是（0%） |
| F5 域词条泄漏 | 域词表命中 0 | 0 | — | 是（0%） |
| F6 测试 | 全绿且 ≥15 | 60 passed | — | 是 |

**「是否 <3%」的直接回答**：时延余量与审计链缺陷率均满足 <3%（后者为 0 缺陷）；F1 是离散裁决而非连续量，
3% 误差线不适用，改以 100% 正确性为判据并通过。**本节点未做任何物理精度声明**，故不给出「<3% 物理误差」的结论。

## 7 honesty 声明

- 全部数字来自 `tools/capability_auto_audit/n07_acceptance.py` 一次运行，报告由 `n07_report.py` 从 `acceptance_summary.json` 注入，无手抄。
- 本节点**未**改动 `config/capabilities.json`（注册表真值不动）；等级状态落在 `artifacts/capability_auto_audit/ledger_state.json`。
- 未完成/未覆盖：真实 21 项能力的批量接入（需要真实 verified 工件落盘）不在本节点范围；日志不可变性依赖外部存储（见注意点 3）。
