# 历史验收声明的重跑复证：repro_rate 从 0 到可审计数字 + 漂移四分类归因（P70-N08）

**任务**：战役 DAG 节点 P70-N08（物理域=验证与确认计量；成熟化单）
**工件**：`tools/repro_audit/`（抽样器 + 集群侧探针 + 判据层 + 编排器 + 报告缝合器）、`artifacts/repro_audit.json`（逐项判决与归因）、`artifacts/repro_audit/nodes/m*.json`（逐节点证据）、`artifacts/repro_audit/figures/`（4 图）、`tests/test_repro_audit.py`（25 项判据测试）
**一句话结论**：从 {{facts.done_seen}} 个 done 节点中分层抽样 {{kpi.nodes_total}} 个（{{facts.repos_covered}} 个仓 / {{facts.disciplines_covered}} 个学科），在**干净检出**上重跑并逐项比对，得到 **repro_rate = {{kpi.repro_rate}}**（pass {{kpi.nodes_pass}} / drift {{kpi.nodes_drift}}，blocked {{kpi.nodes_blocked}} 单列不折算），KPI 与 JSON 重算差 **{{facts.kpi_gap}}**；`tensor_guide_tracker.repro_rate` 的 0 占位被实测值替换。漂移归因四分类实测：source_change {{facts.attr_source_change}} / dependency_version {{facts.attr_dependency_version}} / float_path {{facts.attr_float_path}} / environment {{facts.attr_environment}}（项）。

---

## ① 案例介绍（审计对象 / 计量口径 / 参数表）

平台「验收通过」这件事，此前只有**声明**，没有**复证**：`tensor_guide_tracker` 里 `repro_rate` 长期是一个
写死的 0 占位——不是「复现率 0%」，而是「从未测过、也没人知道怎么测」。本任务把这条 KPI 变成
可审计数字，并把「为什么漂」拆成可复现的四类因子。

审计对象不是外部系统，而是平台自己的历史账本：`campaign_nodes` 里状态为 `done` 的验收节点
（本次扫描时点 {{facts.done_seen}} 个），每个节点挂一个 mission，其 worktree 里留有该节点的
报告（`docs/*.md`）、数值工件（`artifacts/**/*.json`）、生成脚本与 `tests/`。

| 计量口径 | 定义 | 判据 | 依据 |
|---|---|---|---|
| `observed`（物理/交付量） | 阻力、效率、Cb、R²、功率链等有物理量纲的交付数字 | 相对容差 **{{protocol.observed_rel_tol}}** | 任务书口径；比「3% 工程可接受」严 4 个数量级，故任何 pass 项天然满足 <3% |
| `diagnostic`（近零量） | 残差、守恒误差、对称性偏差等期望为 0 的求解诊断量 | 绝对容差 **{{protocol.diagnostic_abs_tol}}**（近零阈值 {{protocol.near_zero_threshold}}） | 近零量相对误差无意义（分母趋零），必须换口径 |
| 结构差异 | 重跑少了/多了数值键 | 直接判 `drift` | 「少算一个量」不能被容差静默放行 |
| ULP/量化 | 每个量另记 ULP 距离与 12 位有效数字规范串 | 与 `docs/REPRODUCIBILITY_CROSS_ISA.md` 协议同一实现 | 判据并联，避免近零抵消 |

**参数表（本次审计的运行参数）**

| 参数 | 值 | 说明 |
|---|---|---|
| 抽样 | N = {{facts.nodes_in_spec}}，repo × 学科 × 年份 分层 | 判据 (a) |
| 「带可执行工件」判据 | 提交内含 ≥10 个数值叶的 JSON 工件 **且** 有测试文件 | 空壳节点不入样 |
| 干净检出 | `git archive <commit>` → 无 `.git`、无作者未提交改动 | 判据：sha256 逐文件复核 |
| 执行环境 | 外协方集群 n315/n330（loongarch64, 128 核, pytest 9.2, numpy 2.0.1） | 重跑不压 99 控制面 |
| 单节点上限 | {{budget.node_timeout_sec}} s（判据 ≤600 s） | 超时判 blocked 并记账 |
| 每节点重复 | 2 轮（判据 (c)：drift 需两次一致） | 两轮各自独立重放 |
| 总预算上限 | 6 h | 实测 {{facts.total_min}} min |

**审计通道（诚实交代）**：两类可执行工件结构不同，本审计用两条通道，判决时**显式标注**：

1. `numeric`：节点自带「写 JSON 的生成脚本」→ 在干净检出里重放该脚本，与锚定的报告数字**逐项**比对
   （本批 {{facts.numeric_nodes}} 个节点，{{facts.observed_items}} 个 observed + {{facts.diagnostic_items}} 个 diagnostic 量）；
2. `assertion`：只有测试文件、数值被写进断言（报告数字 != 可重放工件）→ 重放测试，失败的测试即
   「报告判据在干净检出上不再成立」的逐项证据（本批 {{facts.assertion_only_nodes}} 个节点走此通道）。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

```
scan_select.py             run_audit.py                                   make_report.py
┌───────────────┐   ┌──────────────────────────────────────────┐   ┌──────────────────┐
│ campaign_nodes│──▶│ git archive 干净检出 → sha256 对锚        │──▶│ repro_audit.json │
│ 分层抽样 N=36 │   │ → 注入 _repro/(探针+pristine) → tar 直达  │   │ 逐项 pass/drift  │
│ 锚定工件 sha  │   │   集群节点（n315/n330）→ 两轮重跑        │   │ blocked + 归因   │
└───────────────┘   │ → 集群侧逐项比对（observed/diagnostic）  │   └──────────────────┘
                    │ → drift 消融：线程数 / 依赖栈 / 源码替换  │            │
                    └──────────────────────────────────────────┘            ▼
                                                          tensor_guide_tracker.repro_rate
```

1. **抽样与锚定**（`tools/repro_audit/scan_select.py`）：从 `campaign_nodes`（status=done）读节点 →
   定位 worktree → `git diff main...HEAD` 取本节点交付面 → 识别测试 / JSON 工件 / 生成脚本 →
   分层抽样（repo 按候选量配额，层内按学科×年份轮转，稳定散列可复现）→ 对报告 md、JSON 工件、
   生成脚本、测试文件取 `git show <commit>:<path>` 的 sha256 作锚。
2. **干净检出**（`run_audit.clean_checkout`）：`git archive <commit>` 生成副本，**逐文件复核 sha256 == 锚**
   （本批 {{facts.anchored_files}} 个文件全部一致，不一致 {{facts.anchors_mismatched}} 个），
   作者工作树里「未提交/被门禁滤除」的文件因此不会混进复证。
3. **集群重跑**（`probe_runner.py` + `cluster_exec.py stage_dir`）：`_repro/` 里放探针、判据模块与
   pristine 参考工件；节点上先跑 pytest（`-rfE --tb=line`），再重放生成脚本（每轮前从 pristine 恢复
   工件，保证两轮起点一致）；每节点一次调用内部完成 2 轮。
4. **导入面替身**（`shims.py`，诚实边界）：集群节点没有 matplotlib/fastapi/pydantic，历史工件脚本却
   普遍 `import matplotlib`（画图）或经 `api/*.py`（HTTP 外壳）import 物理模块。探针装入**只替换导入面、
   不动数值代码**的替身（绘图对象全 no-op，`APIRouter/BaseModel` 最小外壳）。替身清单写在
   每个节点工件的 `import_stubs` 里，可复核；**代价是 API 层行为不可复证**（见 ⑤ 局限）。
5. **逐项判定**（`canonical.py`）：展平 JSON 数值叶 → 按参考值分类 observed/diagnostic →
   逐项判 pass/drift，同时记 `rel/abs/ulp/quant_ok`；结构差异（少算/多算）单列。
6. **漂移归因**（`run_audit.attribute_node`）：对 drift 节点做消融实验再贴标签——
   线程数归约（`OMP/OPENBLAS/MKL=1`）、依赖栈（同 ISA 控制机 vs 集群节点）、源码口径（工作树 vs 提交）。
   归因只用**实测证据位**，证据不足时落 `environment` 并标 `note=unresolved`。
7. **KPI 与落库**：`repro_rate = pass/(pass+drift)`，blocked 不进分母；落库前用同一 JSON 重算一遍，
   要求 |KPI − 重算| ≤ 1e-9，然后幂等 upsert 进 `tensor_guide_tracker`（替换 0 占位 + 追加审计快照）。

## ③ 计算结果

<!--TABLES-->

## ④ 结果图

![fig1](figures/fig1_status_by_repo.png)

**图 1** 逐仓判决。绿=pass、红=drift、灰=blocked。drift 与 blocked 的分布直接说明「哪个仓的历史声明
在干净检出上站不住、哪个仓的工件根本不可重放」。

![fig2](figures/fig2_drift_magnitude.png)

**图 2** 数值通道逐节点最大相对误差（左，对数轴，红虚线=observed 容差 1e-6）与最大 ULP 距离
（右，红虚线=跨 ISA 协议 ULP 门 65536）。本批最大相对误差 {{facts.max_rel_overall}}、最大 ULP {{facts.max_ulp_overall}}。

![fig3](figures/fig3_attribution.png)

**图 3** 漂移四分类归因（source_change / dependency_version / float_path / environment），按项计数。

![fig4](figures/fig4_channel_blocked.png)

**图 4** 左：复证通道覆盖（numeric / numeric+assertion / assertion）；右：blocked 类别单列量化
（blocked 一律不进 repro_rate 分母）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **「0 占位」不是 0%**：`tensor_guide_tracker.repro_rate=0` 的原意是「未测」，落库时若按字面当 0%
   会得出「平台声明全不可复现」的错误结论。本任务用 `notes` 把占位替换为实测值并留审计指针。
2. **作者工作树 ≠ 提交**：部分节点存在「门禁滤除」——交付物在工作树里、但没进提交。干净检出因此
   可能与作者看到的不同；这正是 `source_change` 归因类要抓的东西，故抽样阶段同时记录工作树 sha256。
3. **导入面替身是必要之恶**：集群无 matplotlib/fastapi/pydantic，若不替身，绝大多数 ship-perf 节点会
   因 import 失败判 blocked，复证率退化为「环境清单」。替身只吞绘图/HTTP 外壳，但我们**无法**用它
   复证 API 层行为——这类节点即便 pass，也只代表「物理数值链路可复现」。
4. **断言通道弱于数值通道**：断言通道只能给出「判据是否仍成立」，给不出漂移幅度；因此四分类归因的
   证据强度在断言通道上明显较弱（标签落 `dependency_version`/`environment` 且带 unresolved 记号）。
5. **blocked 是结果不是借口**：{{facts.blocked_nodes}} 个节点 blocked 全部单列并给出类别（见 ③ 表），
   不折算进通过率；若把它们算进分母，repro_rate 会被环境噪声污染。
6. **数值通道覆盖率只有 {{facts.numeric_nodes}}/{{kpi.nodes_total}}——这是结论不是借口**：绝大多数历史节点的
   JSON 工件由**没入库的临时脚本**产出（提交里找不到能写出该工件的生成器），或生成器写的是别的目录。
   本审计先按「谁提到工件文件名」静态映射（覆盖 0，全被判「没写出」），再改证据驱动（跑脚本 → 看谁真的
   写过提交里的 JSON），才拿到 {{facts.numeric_nodes}} 个可重放节点、{{kpi.items_compared}} 个数值项。
   要把这条通道铺开，技术债是「每个节点补一个入库的 `make_artifacts.py`」，不在本单范围（如实登记）。
7. **上层 agent 的工具通道只回传尾部 ~8 KB**：探针一开始输出的缩进 JSON 被截断导致误判 blocked，
   改成紧凑序列化 + 明细截断（drift 明细 12 条、pytest 尾 500 字符）后才稳定——这是「机器通道有带宽
   上限」的通用坑。
8. **单节点上限 10 min 是硬门**：本批最坏单节点 {{facts.max_node_wall_sec}} s；drift 节点要多跑一轮消融，
   是最容易撞门的一类，故探针内部带 `node_timeout` 预算与「上一轮超时脚本在下一轮跳过」的熔断。

## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答「是否 <3%」）

本任务的基准 = **历史报告里声明的数值本身**（锚定 sha256 的工件），判据是「重跑能否复现该声明」。

| 判定 | 数值 | 结论 |
|---|---|---|
| observed 量相对误差（本批最大） | {{facts.max_rel_overall}} | 容差 1e-6 ≈ 1e-4%，**远小于 3%**：pass 项是否 <3% → **是** |
| 数值项通过率 | {{facts.item_pass_rate}} | {{kpi.items_pass}}/{{kpi.items_compared}} 项在容差内 |
| 最大 ULP 距离 | {{facts.max_ulp_overall}} | 与跨 ISA 协议门 65536 对比（bit 级噪声 vs 物理差异） |
| 锚定工件 sha256 复核 | {{facts.anchored_files}} 文件一致 / 不一致 {{facts.anchors_mismatched}} | 审计链第一环成立 |
| KPI 与 JSON 重算差 | {{facts.kpi_gap}} | 判据要求 ≤1e-9 → **通过** |
| 数值通道两轮重跑一致性 | {{facts.drift_consistent_nodes}}/{{facts.numeric_nodes}} 个数值节点两轮逐项判决完全一致 | 判据 (c) |
| drift 项归因覆盖 | {{facts.drift_nodes_labeled}}/{{facts.drift_nodes}} 个 drift 节点带四分类标签 | 判据 (c) |

**显式回答**：数值通道上，所有 `pass` 项的相对误差上界为 {{facts.max_rel_overall}}（判据 1e-6），
**远优于 3%**；`drift` 项则超越容差（幅度与归因见 ③ 逐节点表与图 2），按本任务口径「未通过复证」，
如实计入 repro_rate 分母而**不是**抹平。

## 局限与后续（honest）

* 断言通道（{{facts.assertion_only_nodes}} 个节点）缺少数值幅度，只能给「判据是否成立」；要把它们升级到
  数值通道，需要为每个节点补一个「写 JSON 的生成脚本」——这本身就是一个值得排产的技术债清单。
* 导入面替身使 API 层行为不可复证；若要复证端点级声明，需在集群节点侧提供真实依赖镜像（镜像化而非替身）。
* 抽样覆盖 {{facts.repos_covered}} 个仓、{{facts.disciplines_covered}} 个学科；年份维度在本次样本上退化
  （所有节点 commit 都落在 2026），故「年份」分层的区分度为零，已在抽样工件中如实记录。
* 本节点只读审计：唯一写库是 `tensor_guide_tracker` 的 KPI 回写（`--persist` 路径），未改动任何被审计仓库。