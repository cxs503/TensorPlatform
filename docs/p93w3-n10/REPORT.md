# 终态语义分层与预算收口契约成熟化（P93W3-N10）— 论文级报告

- 节点：`P93W3-N10`｜使命：9177｜仓库：TensorPlatform｜分支：`agent/m9177`
- 契约：`backend/contracts/terminal_state_vocab.v1.json`（sha256 `3ba22462bd1a5b2c4137cb7f111a2fd42803bd1aa8e7e4b506b9f03f5022dd2f`）
- 标签 schema：`backend/contracts/delivery_label.v1.json`
- 核心模块：`backend/api/domain/terminal_state.py`（新模块：纯函数、零领域语义、无隐藏状态）
- 语料：`runs/p93w3-n10/corpus/missions_succeeded.json`，2000 行真实终态记录，sha256 `ad831b69921a5e3fa96523a61a331387d77b16f279f4d073d67e817afe34d04b`（只读 API 抓取）
- 全部数字由 `tools/p93w3_n10/*.py` 从工件程序化注入（零手抄）：
  `metrics.json`(sha256 `59c30dcbfc4f17fffad7552eea21781ac16400363de4226094ef140fa5ecac87`) / `figures.json` / `ACCEPTANCE.json` / `pytest_junit.xml`
- 复现：`python3 tools/p92w3_n10/fetch_missions.py --status succeeded --limit 2000 --out runs/p93w3-n10/corpus` →
  `python3 tools/p93w3_n10/evaluate.py` → `python3 tools/p93w3_n10/figures.py` →
  `cd backend && python3 -m pytest tests/test_p93w3_n10_terminal_states.py -q` → `python3 tools/p93w3_n10/acceptance.py`

---

## ① 案例介绍（要解决的问题 / 数据）

平台把「正常完成」和「预算耗尽被迫收尾」都写进同一个终态 `succeeded`。自主续航体系
（补给、合并、判级、KPI、成功率）全部读这一个标签，因此一次被迫收尾会被当成一次交付：
成功率可以被预算耗尽刷高，而「交付了多少」这个更强的语义根本不存在。

本节点把终态语义分成四态，并给一条可机检的交付标签：

| 语义终态 | 语义 | 判定依据（全部为存量事实） |
|---|---|---|
| `succeeded` | 正常完成 | 工件完整（completeness=1.0）+ 无被迫收口标记 + 预算未耗尽 |
| `forced_finish` | 被迫收尾（预算/墙钟/无进展） | 收口标记，或工具调用计数触顶，或预算耗尽证据 |
| `degraded` | 部分交付（缺口可点名） | 无被迫标记但工件不完整 |
| `failed` | 存储态失败 / 无法分类（fail-closed） | `failed/stopped/revoked/orphan`，或记录不可用 |

优先级（契约 `precedence`，首个命中生效）：`failed > forced_finish > degraded > succeeded`。

数据（真实记录，非构造夹具）：只读抓取 `succeeded` 终态 **2000** 行（分页 200，
落盘后核对 sha256）。这 2000 行在旧口径下成功率 = **100.00%**
（分母即自身，因为抓取时它们全被标成 succeeded）。

## ② 怎么做的（方法与工具链）

1. **派生层，而不是改存储枚举。** 存储 `status` 的 CHECK 约束与跨仓契约
   `mission_status_vocab.v1.json`（TensorPhysics 也在消费）保持不动；语义终态是存量事实的
   确定性函数，因此**天然向后兼容**，也不需要两侧迁移。这是本节点的关键设计取舍（偏差与理由见 ⑤）。
2. **判据单一来源。** `0.70` 只写在契约里，测试把 `terminal_state.budget_thresholds()["mvd_checkpoint"]`
   与 `mvd_checkpoint.BUDGET_THRESHOLD` 钉在一起（`True`），
   两个模块不可能漂移。
3. **只信事实，不信自报。** 判定只读：存储态、`fail_stage` 收口标记、预算计划 vs 实际
   （`tool_calls/max_tool_calls`、`created_at→finished_at` 与 `max_wall_seconds`）、
   工件事实（复用 P92W3-N10 的 `normalize_artifacts` / `assess_attested_delivery`）。
4. **completeness 连续化。** `0.4*实现 + 0.4*可机检验证 + 0.2*声明`（权重来自契约），
   验证维按「可机检 1.0 / 存在但空泛 0.5 / 缺失 0」计分 —— 缺口可以被排序，而不只是被点名。
5. **70% MVD 检查点。** `mvd_checkpoint_for_record()` 把存量计划/实际喂给既有
   `mvd_checkpoint.evaluate_mvd_checkpoint()`（生产代码路径）并计时，判据 (b) 因此测的是真路径。
6. **向后兼容迁移。** `migrate_legacy_succeeded()` 逐行 `deepcopy`，新增
   `terminal_state / delivery_label / migration{legacy_status,…}` 三键，原键值全部逐字保留；
   「零丢失」是**校验出来的**（键值逐个比对 + 计数对账 + 无漏洞清单），不是声明的。
7. **20 场景自测。** 场景表冻结在测试模块里，评估工具直接 import 同一份表（单一来源），
   分类混淆矩阵、准确率和异常路径一起输出。
8. **零领域语义。** 新模块与新测试都用既有 `scan_domain_semantics()` 扫过，命中数 0。

## ③ 计算结果（全部为工件注入的数字）

判据汇总（`ACCEPTANCE.json`，`all_criteria_met = True`）：

| 判据 | 要求 | 实测 | 通过 |
|---|---|---|---|
| (a) 四态分类准确率 | 20 场景 100% | 20/20 = **100.00%** | True |
| (b) 70% MVD 检查点触发延迟 | ≤ 1 s | p100 = **4.003 ms**（p50 0.055 / p95 0.145 / p99 0.280，n=6000） | True |
| (c) 向后兼容迁移零丢失 | 零数据丢失 | in 2000 → out 2000，violations 0，状态计数和 2000 | True |
| (d) 交付标签 schema 校验 | 100% 通过 | 2000/2000 = **100.00%**（负向控件：非法枚举/越界/缺键/多键全部被拒，见 pytest） | True |
| 支撑 | pytest 全绿 | 38 项，failures 0，errors 0 | True |

**四态分布（真实 2000 行，旧口径它们全是 succeeded）：**

| 语义终态 | 行数 | 占比 |
|---|---|---|
| succeeded | 1476 | 73.80% |
| forced_finish | 143 | 7.15% |
| degraded | 381 | 19.05% |
| failed | 0 | 0.00% |

- 旧口径成功率 `100.00%` → 真实交付率 **73.80%**，
  **虚高 26.20 个百分点**；524 行的语义标签与存储态不同。
- 质量闸门通过率 73.80%，completeness 均值 0.9186，
  工件完整率 77.90%；预算分档 normal/warning/exhausted =
  485/1411/104。

**被迫收尾的构成（143 行 = 四态 forced_finish 计数，一致性 True）：**

| 证据 | 行数 |
|---|---|
| budget_marker | 45 |
| wall_marker | 34 |
| progress_marker | 39 |
| tool_cap_only | 25 |
| total | 143 |

**预算证据的效度（本节点最重要的实测发现）：**

| 量 | 值 |
|---|---|
| 收口标记（budget+wall）行数 | 79 |
| 工具调用计数触顶行数 | 69 |
| 墙钟比 ≥1.0 行数 | 1169 |
| 墙钟比 ≥1.0 且带 wall 标记 | 40 |
| 墙钟代理 recall / precision | 1.000 / 0.034 |
| 仅按比例规则会误判的被迫收尾行 | 1127 |

结论：墙钟比 `finished_at-created_at` 会把 **1169** 行判成「预算耗尽」，
而真正带 `wall_exhausted` 标记的只有 34 行 —— recall 1.00、
precision 仅 0.034（排队等待与软墙钟预算都在这个跨度里）。
因此契约把墙钟比降到**只影响 warning 档**，`exhausted` 必须由证据（收口标记、硬性工具计数触顶）判定。

## ④ 结果图

![fig1_terminal_layering.png](../../runs/p93w3-n10/fig1_terminal_layering.png)
*图 fig1_terminal_layering.png：Left: the single stored status (all rows succeeded). Right: the four semantic states derived from stored facts; forced_finish/degraded were previously counted as success.* (sha256 `fa374c9b762bd453…`)

![fig2_mvd_latency.png](../../runs/p93w3-n10/fig2_mvd_latency.png)
*图 fig2_mvd_latency.png：Left: percentile latency of one checkpoint evaluation on the production code path. Right: the remaining margin against the 1 s criterion.* (sha256 `b9d38c0f02d2d1fc…`)

![fig3_budget_evidence.png](../../runs/p93w3-n10/fig3_budget_evidence.png)
*图 fig3_budget_evidence.png：Left: composition of the forced-closure population. Right: the rejected ratio-only rule would have declared a large majority of rows exhausted; only the harness marker (and the hard-enforced tool counter) are accepted as evidence.* (sha256 `b85f72a18d8f675b…`)

![fig4_confusion.png](../../runs/p93w3-n10/fig4_confusion.png)
*图 fig4_confusion.png：Confusion matrix of the 20-scenario acceptance matrix (19 classifiable scenarios plus one exception-path scenario); only the diagonal is populated.* (sha256 `79439030cdf3f768…`)


图注（左→右、上→下）：终态分层前后对比；70% 检查点触发延迟与判据余量；被迫收尾构成 +
比例规则的误判规模；20 场景混淆矩阵。

> 注：本节点是后端/契约任务，没有连续场量，故无「场量云图」；四张图均为判定结果的可视化，
> 每张图的 sha256 已登记在 `runs/p93w3-n10/figures.json`（图与数字同源，防止图文不一致）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **坑（已修）：墙钟比不能当耗尽证据。** 初版把 `max(工具比, 墙钟比) ≥ 1.0` 当耗尽，
   在真实语料上把 1231 行判成被迫收尾（61.6%），
   其中 1127 行没有收口标记。改成证据制后 forced_finish 回落到 143 行。
   这条改动写进了契约 `exhausted_evidence`，并用图 3 与 `budget_evidence` 数字留痕。
2. **取舍（偏差与理由）：派生语义层而非新存储枚举。** 任务书的「终态枚举扩展」若落成 DB 枚举，
   需要 `status` CHECK 约束迁移 + 跨仓契约版本双绿，且历史行仍要回填；派生层拿到同样的产品效果
   （成功率不再被被迫收尾刷高）且零兼容风险。代价：语义标签需要下游显式读取（本节点已提供
   纯函数与批量迁移工具），存储 `status` 仍是二元。**这是有意偏离任务书第 (1) 条形式的地方，
   语义与判据 (a)–(d) 全部满足。**
3. **局限：20 场景准确率是「规格一致性」而非统计准确率。** 场景由本节点按契约构造，100% 说明
   实现与契约一致，不说明「语义判定符合人类直觉」。真实语料上唯一可用的地面真值是
   harness 写的收口标记：标记的 118 行 recall
   1.00（全部被判 forced_finish），但「无标记却被迫」的
   25 行只能靠工具计数触顶这一硬事实，
   没有独立人工标注可校验（下一步应是抽 60 单人工标注，做法同 P92W3-N10）。
4. **局限：语料窗口与构成。** 只抓 `succeeded` 终态 2000 行（抓取瞬间），故
   四态里 `failed` 计数为 0（失败行不在本语料内，分类器对 failed 的证据来自 20 场景矩阵与负向控件）。
5. **局限：`created_at→finished_at` 含排队时间。** 这是墙钟比失效的根因；若未来平台记录
   `claimed_at`，墙钟维度可重新评估是否升级为证据。
6. **环境说明（如实）：** 本工作树未跟踪 `runs/p92w3-n10/*`（0 个文件），故上一节点的 4 个
   「冻结工件」测试在此处失败（`shadow_golden.json` 等不存在）。与本改动无关，也不计入本节点测试数；
   本节点 pytest 结果见支撑表。

## ⑥ 误差判定（逐项列出，显式回答「是否 < 3%」）

| 关键量 | 基准 | 实测误差 | 是否 < 3% |
|---|---|---|---|
| 四态分类准确率 | 契约 20 场景期望（100%） | 0.00% | **是（0.00%）** |
| 70% 检查点触发延迟 | 判据 1000 ms | p100 4.003 ms = 判据的 0.40% | **是（0.400%）** |
| 迁移数据丢失 | 零丢失 | violations 0 / 2000 行 | **是（0 行）** |
| 标签 schema 校验 | 100% 通过 | 失败 0 / 2000 | **是（0.00%）** |
| 收口标记 recall | harness 标记 118 行 | 0.00% | **是（0.00%）** |
| 预算阈值一致性 | 契约 0.70 == 检查点 0.70 | 差值 0.0000 | **是（0.0000）** |

- **有基准的量**（契约期望、1000 ms 判据、零丢失、schema 100%、标记 recall）全部 < 3%，逐项见上表。
- **无实验基准的量**：本节点不含物理场量或解析解，故不存在「与解析解/文献比较」的第三方基准；
  「语义判定是否符合人工直觉」没有独立标注，按 ⑤-3 如实声明为未验证项，不以自证数掩盖。
- **honest FAIL 声明**：无失败判据；但上述 ⑤-3/⑤-4 两项未验证范围如实保留，不粉饰为已验收。

---

### 审计链

- 契约 → 模块 → 测试 → 评估 → 图 → 验收 → 报告，全链路单一数据源：
  `terminal_state_vocab.v1.json` → `backend/api/domain/terminal_state.py` →
  `backend/tests/test_p93w3_n10_terminal_states.py` → `tools/p93w3_n10/evaluate.py` →
  `tools/p93w3_n10/figures.py` → `tools/p93w3_n10/acceptance.py` → 本报告（`tools/p93w3_n10/report.py` 渲染）。
- 图与数字同源：报告只读 `metrics.json`/`figures.json`/`ACCEPTANCE.json`；
  `figures.json` 内每图 sha256，`ACCEPTANCE.json` 内登记 `metrics_sha256` 与契约 sha256。
