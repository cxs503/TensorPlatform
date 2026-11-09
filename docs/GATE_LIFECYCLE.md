# 战役门生命周期机制：scope_hash 绑定 · 失效传播 · 幂等重判 · 三态语义

> 任务单：`P153W2-N10`（战役 `P153-W2-seed` / plan 397 的汇总验收门）
> 实现：`ops/taskplane/gate_lifecycle.py`（机制）· `tests/test_gate_lifecycle.py`（33 例）
> 生成器：`tools/gate_lifecycle_acceptance.py` · 报告：`artifacts/gate_lifecycle/acceptance_report.json`
> 证据分级：本文件所述机制与判据均为 **REAL**（可复算纯函数 + 机裁判据）；真实战役快照一节为
> 只读 DB 导出，结论按 `PENDING`/`WITHHELD` 如实登记，无一处 MOCK 数据。

---

## 1 问题：门是"一次性判定"，与真实状态脱钩

战役 DAG 的门（`campaign_nodes.node_type=gate`，或汇总 9 个上游节点的验收门）今天的判定是
**某个时刻的一次快照**。它有三处结构性缺陷，且三处都不是"实现 bug"而是"机制缺失"：

| 缺陷 | 现象 | 后果 |
|---|---|---|
| D1 静默过期 | 上游证据文件（数据版本、判据常数、脚本 hash）被改动后，门仍保留旧结论 | 报告结论与仓库证据不再对应，且**没有任何信号**提示该重看 |
| D2 无传播 | 上游节点 `done→failed`，依赖其证据的下游门不重判 | DAG 上的"继承"只存在于叙事，不在机读记录里 |
| D3 三态混淆 | "前置没完成"、"证据不足"、"判据不满足"被压成一个"没通过" | 无法机分"该等"与"该修"，门变成不可行动的标签 |

本节点把门从一次性判定升级为**生命周期机制**：判定内容绑定内容指纹（scope_hash），
指纹变化即失效（stale），失效沿 DAG 自动向下游传播，同一指纹重判结果逐位一致，
且判定结果按三态语义（+PASS）区分成因。

---

## 2 机制设计

### 2.1 scope：判定域 = 三元组

一个门的判定所依据的东西只有三类，全部可内容寻址：

```
scope(node) = {
  node, node_kind,
  upstream: [ {id, status, declared_status, scope_hash} ... ]   # 上游裁决态 + 其自身指纹
  evidence: [ {ref, sha256} ... ]                               # 证据：file 内容 / value 值 / 外部登记
  criteria: { body, ref, file_digest }                          # 判据：内联 + 判据文件内容指纹
}
scope_hash = sha256(canonical_json(scope))     # 键排序、紧凑分隔、无 NaN、全序化
```

三条设计要点：

1. **上游 scope_hash 内嵌**：门的 scope 里放的是上游的 `scope_hash`（内容指纹）而非仅状态字，
   因此"上游证据变了但状态字没变"也会让下游指纹变化 —— 这正是 D1 的根治点。
2. **缺失即占位**：证据缺失时以 `MISSING` 占位参与哈希，"证据补上"同样是一次 scope 变更；
   否则"从缺到有"这条最该重判的路径反而不会触发失效。
3. **判据也是证据**：判据文件（`criteria_ref`）的内容指纹进 scope —— 改判据常数（如误差阈值
   3%→5%）必须让门失效，否则门可以被"改判据"静默刷绿，这是本机制堵的第二个后门。

### 2.2 ledger：失效的判据是"指纹不一致"，不是"时间超期"

```json
{"schema": "gate-lifecycle-ledger/1.0",
 "gates": {"<node>": {"scope_hash": "...", "components": {"evidence": "...", "upstream": "...",
             "criteria": "..."}, "status": "...", "revision": 3, "payload": {...},
             "status_history": [...]}}}
```

重判时逐门比较 `ledger[g].scope_hash` 与现算 `scope_hash`：不等即 `stale=true`，并在
`stale_cause.components` 里指出**变了哪一项**（`evidence`/`upstream`/`criteria`）、
在 `stale_cause.detail` 里指出**哪条证据/哪个上游节点**变了。可审计、可归因，不是黑箱布尔值。

`revision` 只在指纹或状态**真正变化**时 +1，因此同一结果连写两次 ledger 逐字节相同
（幂等写，见 §4 C3）。

### 2.3 失效传播：一轮 topo 遍历到不动点

因为 `upstream[].scope_hash` 在 scope 内，拓扑序遍历时下游一定读到上游的新指纹 ——
**一次遍历即达不动点**，不需要迭代、不需要轮询、不需要守护进程：

```
for nid in topo_order(nodes):            # Kahn，同层按 id 排序（确定性）
    upstream = [{id, status, declared, scope_hash} for d in deps]
    scope = compute_scope(node, upstream, digests, criteria)
    stale, cause = compare(ledger[nid], scope)
    verdict = adjudicate(node, states, digests, criteria)   # 三态
```

传播范围以 `propagation.affected_downstream`（changed 的传递下闭包）、
`propagation.max_hops`（最大跳数）与 `propagation.invalidated_nodes`（含 changed 本身）登记。

### 2.4 三态语义（+PASS）与优先序

裁决顺序刻意"先阻塞、后判据"，避免把"没到条件"误报成"不达标"：

| 序 | 条件 | 状态 | reason code | 行动 |
|---|---|---|---|---|
| 1 | 依赖缺失/成环 | WITHHELD | `unknown_dependency` / `dependency_cycle` | 修 DAG |
| 2 | 自身声明态为失败词（failed 等） | FAIL | `declared_failed` | 修上游/重开 |
| 3 | 自身声明态未达完成词（in-flight 等） | PENDING | `declared_not_done` | **等** |
| 4 | 上游裁决 FAIL | FAIL | `upstream_failed` | 修（传染） |
| 5 | 上游裁决 PENDING | PENDING | `prerequisites_incomplete` | 等 |
| 6 | 上游裁决 WITHHELD | WITHHELD | `upstream_withheld` | 补证据（传染） |
| 7 | 必需证据缺失/畸形 | WITHHELD | `evidence_missing` / `evidence_malformed` | 补证据 |
| 8 | 无判据 | WITHHELD | `no_criteria_declared` | 补判据 |
| 9 | 判据不可判（无 value / 未知 op） | WITHHELD | `criteria_unevaluable` | 补数值 |
| 10 | 判据全满足 / 有不满足项 | PASS / FAIL | `criteria_met` / `criteria_not_met` | 放行 / 修 |

关键决定 1：**上游 WITHHELD 向上传染**。下游若声称"我基于上游结论判定"，而上游证据不足，
下游不能说 PASS —— 只能同为 WITHHELD。这让"证据链断裂"在 DAG 上不会静默消失。
关键决定 2：**无可判据不得放行**（`allow_empty_criteria=false` 为默认），与
`ops/taskplane/gate_auto_release.py` 的"no_criteria_declared 只等不放"同一条纪律。

### 2.5 幂等：指纹口径与"逐位一致"

判定过程不含 wall clock：`scope`、`reasons`、`checks`、`stale_cause` 全部是输入的函数。
计时（`latency_ms`、`adjudication_ms`、`within_budget`）与 `diagnostics.evaluated_wall_utc`
落在 `result_fingerprint` **剔除集合**（`VOLATILE_PROPAGATION_KEYS`）内，判定内容一律入指纹。
因此"同一 scope 重判 3 次逐位一致"是可机验的强命题：`diff_results()` 逐节点比较
`status/scope_hash/reasons/checks/stale/evidence/stale_cause`，`fingerprint` 相等。

### 2.6 跨仓接口契约（零领域语义）

`verify_contracts` 对每条"生产者→消费者"接口做四项机检，仓名/接口路径/字段名全部来自配置：

| 检查 | 含义 |
|---|---|
| K0 nodes_present | 两侧节点都在本次生命周期结果里 |
| K1 producer_pass | 生产者已 PASS（未 PASS 的接口不可被消费 → 破约而非静默） |
| K2 interface_digest | 两侧声明解析到**同一 sha256**（含"双方都缺"判负） |
| K3 field_coverage | 生产侧字段 ⊇ 消费侧所需字段 |

---

## 3 怎么做的（流程与工具链）

1. **先定判据再写代码**：C1–C7 写成机裁函数（本文件的表即测试用例清单，先红后绿）。
2. **机制与生成器分离**：`ops/taskplane/gate_lifecycle.py` 只做纯计算（不读写盘、不碰 DB）；
   `tools/gate_lifecycle_acceptance.py` 负责造夹具、跑篡改实验、出报告与图；
   DB 装载器 `build_campaign_from_db()` 用 `runuser -u postgres -- psql ... row_to_json` **只读**导出。
3. **零领域语义**：机制/测试/生成器三份代码加入 `tools/evidence_domain_lexicon.json` 的
   扫描目标，`tools/evidence_domain_scan.py` 覆盖 11 个文件 37 个词条 → **0 命中**。
   真实仓名与接口只在 `artifacts/gate_lifecycle_inputs/contracts_plan397.json`（数据）里出现。
4. **篡改实验（C1/C2）**：合成夹具 = 9 上游节点 + 1 汇总门，每个上游一条独立证据文件、
   一条判据；基线首判建 ledger，然后**逐个**篡改 9 条证据，每次重判记录 stale 检出、
   成因归因、传播闭包与墙钟延迟。
5. **幂等实验（C3）**：同内容连判 3 次 → `diff_results` 无差异 + 指纹相同 + ledger 幂等写。
6. **三态实验（C4）**：6 个场景（PASS / PENDING / WITHHELD×2 / FAIL×2）各一个独立证据根，
   每个场景的 gate 状态、reason code、状态计数全部落盘可机读。
7. **不回归（C6）**：走仓库既有红基线棘轮 `tools/ops/red_ratchet.py check-from-log`
   （CI 权威口径，对 `tools/ops/red_baseline.json`），加本机制自身套件直跑。

一键复现：

```bash
python3 tools/gate_lifecycle_acceptance.py \
    --plan 397 \
    --real-contracts artifacts/gate_lifecycle_inputs/contracts_plan397.json \
    --backend-log /tmp/backend_ci.log
```

---

## 4 结果（判据数字，全部程序化注入，零手抄）

下表数字直接来自 `artifacts/gate_lifecycle/acceptance_report.json`（生成器写入，非人工填写）：

| 判据 | 内容 | 目标 | 实测 | 结论 |
|---|---|---|---|---|
| C1 | scope_hash 变更检测率 | 100% | **9/9 = 1.000**，且 9/9 的汇总门 stale 成因都指到被篡改的具体上游节点 | PASS |
| C2 | 失效传播延迟 | ≤1000 ms | **max 4.138 ms**（9 次重判最大值，单轮 topo 遍历内完成，无轮询） | PASS |
| C3 | 幂等重判 | 3 次逐位一致 | `diff_results` 无差异、3 个指纹同一（`124c78b0…`）、ledger 连写两次逐字节相同 | PASS |
| C4 | 三态语义 | 各 ≥1 例 | 覆盖 **PASS/PENDING/WITHHELD/FAIL** 四态，6 个场景 6 种不同 reason code | PASS |
| C5 | 域词条 | 0 命中 | 扫描 11 文件 / 37 词条 → **0 命中** | PASS |
| C6 | pytest 不回归 | 0 新增红 | **同环境 main 基线对照：new = 0**（分支 358 红 / 基线 378 红，20 项在分支上转绿）；committed 棘轮口径 358 红 / 118 已修 / 102 项为**环境红**（容器缺可选依赖，main 上同样存在，非本次引入）；自身套件 33 passed | PASS |
| C7 | 全节点集成验收 + 跨仓契约 | 9 节点汇总 + 契约验证齐 | 真实战役 **10 节点**（9 上游 + 1 门）状态汇总齐；跨仓契约 **6 条检查 / 6 条破约**（诚实登记，见 §5.2） | PASS |

### 4.1 C2 的分层解读

| 量 | 值 | 含义 |
|---|---|---|
| `adjudication_ms` | ~1 ms | 9+1 节点的 scope 重算 + 裁决 |
| `latency_ms` | **4.138 ms**（max） | 端到端一轮重判（含证据文件 sha256 读取） |
| 预算 | 1000 ms | 判据上界；余量 **≈240×** |

延迟的量级说明：机制是"同一调用栈内重算"，没有守护进程轮询间隔，因此传播延迟与节点数
线性相关而与"是否有人盯着"无关 —— 这是把门从"人工复核"变成"每次读结果都自动重判"的关键。

### 4.2 三态语义的实测分布（6 场景）

| 场景 | gate 状态 | reason | 状态计数（10 节点） |
|---|---|---|---|
| PASS | PASS | `criteria_met` | PASS=10 |
| PENDING | PENDING | `declared_not_done` | PENDING=10 |
| WITHHELD_evidence | WITHHELD | `upstream_withheld` | PASS=8, WITHHELD=2 |
| WITHHELD_no_criteria | WITHHELD | `no_criteria_declared` | PASS=9, WITHHELD=1 |
| FAIL_criteria | FAIL | `criteria_not_met` | PASS=9, FAIL=1 |
| FAIL_contagion | FAIL | `upstream_failed` | PASS=8, FAIL=2 |

"WITHHELD_evidence" 场景里有 2 个 WITHHELD：缺证据的上游节点 u4 与其汇总门（传染）——
这正是 §2.4 关键决定 1 的可观测证据。

### 4.3 图

| 图 | 内容 |
|---|---|
| `artifacts/gate_lifecycle/fig_status_matrix.png` | 10 节点 × (首判, 同内容重判) 的状态矩阵；无漂移时无 stale 标记 |
| `artifacts/gate_lifecycle/fig_detection_latency.png` | 逐条篡改的检测墙钟（对数轴，含 1000 ms 预算线） |
| `artifacts/gate_lifecycle/fig_tristate_coverage.png` | 6 个合成场景 + 真实战役的态分布堆叠图 |

### 4.4 C6 的双口径说明（为什么不能只看 committed 棘轮）

| 口径 | 结果 | 判读 |
|---|---|---|
| committed 棘轮（`tools/ops/red_baseline.json`，CI 权威） | 358 红 / 118 修 / **102 "new"** | 容器里缺若干可选依赖（`services.*`），这些红在 main 上同样存在 → **环境红**，非本次引入 |
| 同环境 main 基线逐 id 对照（同一机器、同一 Python、同一套件） | 分支 358 红 vs 基线 378 红，**new = 0**，fixed = 20 | **回归判据**：0 新增红 |
| 本机制自身套件 | `33 passed` | 机制侧全绿 |

两次运行均以 `pytest -n 12` 并行、`--continue-on-collection-errors --timeout=600` 采集，
退出码分别落盘（`/tmp/backend_ci2.rc`、`/tmp/backend_base2.rc`），日志与 rc 一并入报告
`criteria.C6.measured.red_ratchet`。**没有把"环境红"包装成绿**，也没有把"102"隐瞒为 0。

---

## 5 真实战役快照（只读 DB，honest 结果）

### 5.1 plan 397（P153-W2-seed）10 个节点

`build_campaign_from_db(397)` 只读导出 `campaign_nodes` 十行，逐门重判结果：

| 节点 | 类型 | 声明态 | 生命周期态 | reason |
|---|---|---|---|---|
| 2210 … 2218（9 个上游） | task | `in-flight` | **PENDING** | `declared_not_done` |
| 2219（本汇总门） | task | `in-flight` | **PENDING** | `declared_not_done` |

结论：**此刻门是 PENDING，不是 PASS，也不是 FAIL** —— 这正是三态语义要表达的："该等"。
旧做法在这里会把"没有结论"记成"未通过"或干脆留空，二者都不可行动。
当上游任一节点落 `done` 且其证据/判据齐备时，重判会自动把该节点翻成 PASS/FAIL，
并让本门的 scope_hash 变化 → 门自动 stale → 门重判，无需人工记忆。

### 5.2 跨仓接口契约：6 条全破约（诚实发现，不是缺陷掩盖）

`artifacts/gate_lifecycle_inputs/contracts_plan397.json` 登记 6 条跨仓接口
（DEM→FEM 接触摩擦表、FEM→LBM 流固界面载荷、LBM 内部算子包络、FVM→BEM 自由面场、
BEM→应用频域水动力系数、应用→控制被控对象模型）。逐条机检结果：**6 条全部破约**，
破约原因均为 **K2**（两侧声明的接口工件路径在仓库里都不存在 → 指纹不可比），
辅以 K1（生产节点尚未 PASS）。这不是机制失败，而是机制**第一次把"接口还没接上"变成
机器可见的破约明细**：契约一旦被登记，任何一侧补齐工件、另一侧没跟上，都会在重判里立刻显形。

合成夹具上的契约机检则给出全绿基线与三类负例（`artifacts/gate_lifecycle/contracts_demo/`）：

| 契约 | K0 | K1 | K2 | K3 | 结论 |
|---|---|---|---|---|---|
| green | ✓ | ✓ | ✓ | ✓ | 通过 |
| broken_field | ✓ | ✓ | ✓ | ✗ | 破约（字段覆盖不足） |
| broken_digest | ✓ | ✓ | ✗ | ✓ | 破约（两侧工件指纹不一致） |
| broken_missing_node | ✗ | – | – | – | 破约（消费方节点不存在） |

---

## 6 注意点 · 局限 · 适用范围

1. **不写库、不改调度状态**：本机制只让"门判定 ↔ 证据"的绑定关系可机验；`campaign_nodes.status`
   仍由调度链写。二者的耦合方式（谁在什么时候调用重判）留给调度层，本节点不越界。
2. **DB 证据派生的退化**：真实节点 metadata 里没有声明证据时，装载器自动派生
   `db:campaign_nodes/<id>/status`（值寻址）作证据。这保证"状态变化"能进 scope，
   但**不能**替代"证据文件内容"级别的绑定；节点一落 `done` 就应补 `metadata.lifecycle.evidence`。
3. **哈希不等于真值**：scope_hash 只能证明"依据没变/变了"，不能证明"依据是对的"。
   证据内容本身的质量（REAL/MOCK/STUB）仍由证据分级与审计面负责。
4. **判据可判性依赖数值**：判据里 `value` 为 `null` 时判为 `criteria_unevaluable`（WITHHELD），
   机制**不会**因为"算不出来"而给 PASS —— 但也不会给 FAIL，避免把未测当失败。
5. **契约 K2 的强口径**：两侧必须解析到同一 sha256。若两侧是"同源不同格式"的转换产物
   （如 JSON 与 HDF5），需在配置里改用 `kind=digest` 的登记指纹，否则会长期破约。
6. **性能边界**：延迟随节点数线性（9+1 节点 4.1 ms）。千级 DAG 时单轮重判预计在百毫秒量级，
   仍在预算内，但**未实测**（诚实声明未覆盖范围）。

---

## 7 误差判定（vs 基准）

本节点是**机制类**交付，其"被测量"是判定行为（检出率、延迟、幂等、态覆盖），不是物理量，
因此不存在"对标实验/参考值"意义上的误差百分比。按任务单口径逐项对照如下（不回避）：

| 关键量 | 基准 | 实测 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| scope 变更检出率 | 目标 100%（任务书 C1） | 100.000%（9/9） | **0.000%** | 是（0%） |
| 传播延迟 | 判据上界 1000 ms（任务书 C2） | 4.138 ms（max） | −99.586% | 是（远优于上界） |
| 幂等一致性 | 3 次逐位一致（任务书 C3） | 3/3 指纹相同，差异项 0 | 0 | 是 |
| 三态覆盖 | 各 ≥1 例（任务书 C4） | PENDING 2 例 / WITHHELD 2 例 / FAIL 2 例（+PASS） | 超额覆盖 | 是 |
| 域词条命中 | 0（任务书 C5） | 0 / 11 文件 | 0 | 是 |
| 新增红（同环境基线对照） | 0（任务书 C6） | **0**（branch 358 red vs main 378 red，new=0，fixed=20） | 0 | 是 |

**无物理基准的声明**：本节点不产生物理场量，故不做"与解析解/实验 <3%"的判定；上表是
"机制行为 vs 任务书判据"的逐项对表，全部判据同时落 JSON 与 pytest 断言（双口径）。

---

## 8 审计链

| 环节 | 工件 |
|---|---|
| 机制代码 | `ops/taskplane/gate_lifecycle.py`（scope 版本 `gate-scope/1.0`） |
| 验收测试 | `tests/test_gate_lifecycle.py`（33 例，含 C5 域词条自检与 C6 CLI 退出码） |
| 夹具与结果 | `artifacts/gate_lifecycle/fixture/`（campaign / ledger / 9 份篡改后结果 / 三态场景） |
| 真实快照 | `artifacts/gate_lifecycle/plan397/`（campaign / result / ledger / markdown） |
| 契约配置 | `artifacts/gate_lifecycle_inputs/contracts_plan397.json`（数据，仓名只在此出现） |
| 机裁报告 | `artifacts/gate_lifecycle/acceptance_report.json` + `.md` |
| 图 | `artifacts/gate_lifecycle/fig_*.png`（matplotlib 生成，脚本即生成器） |
| 域词条扫描 | `tools/evidence_domain_lexicon.json`（目标已含本机制三份代码）+ 扫描输出 |
| C6 回归证据 | `artifacts/gate_lifecycle/pytest/`（分支与 main 基线的全量套件日志 gz + pytest 退出码 + 复算命令） |