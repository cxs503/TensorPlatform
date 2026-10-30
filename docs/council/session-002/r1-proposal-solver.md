author-model: qwen3.7-plus

# 议事会 session-002 · R1 提案 · 求解器深化视角

**提案人槽位**：`r1-proposal-solver`（mission 390）
**视角**：10 库哪个先上卡深化（3D / 多相 / 自由面 / DEM-FEM 耦合）＋ SDAA 性能与精度基线
**立场一句话**：**P2 波次的第一优先级不是"再跑一遍 3D"，而是把 43 个已验证案例的 88 份异构工件归一成"机器可裁的精度-性能台账"，同时用一次 3 小时的三库普查裁决"第二个上卡的库"——这两件事不解决，后面所有 3D/多相/自由面上卡的 PASS 都无法作为基线引用。**

---

## 0. 本提案引用的硬数据（全部程序算出，零手抄；来源标注）

> 采集脚本：`/srv/tensor/agent/work/m390` 内一次性 python 聚合（glob `m*/artifacts/sdaa_*.json` + 扫描 `/srv/tensor/workspace/*` + 读 `/srv/tensor/agent/sweep_state.json`）。采集时刻 2026-10-30 07:35。

| # | 指标 | 值 | 来源 |
|---|---|---|---|
| D1 | TensorLBM `benchmarks/verified` 案例数 | **43** | 目录扫描 |
| D2 | SDAA 扫荡工件总数（各 mission 工作树 `artifacts/sdaa_*.json`） | **88** | glob 计数 |
| D3 | `sweep_state.json` 已派发案例数 | **43 / 43**（无未派） | sweeper 状态文件 |
| D4 | 43 个 verified 案例中"有上卡工件且含 `/dev/tcaicard<N>` 设备证据行" | **43 / 43 = 100%** | 工件文本正则 |
| D5 | 出现过的 SDAA 卡号 | **32/32**（`tcaicard0`…`tcaicard31`） | 88 份工件聚合 |
| D6 | 工件墙钟样本数 / 中位数 / 最大 | **n=177 / 36.19 s / 7351.5 s** | 键名 `wall_clock_s|wall_sec|wall_time_s|elapsed_s` |
| D7 | 墙钟 > 5400 s（单 mission 预算帽）的样本 | **2 / 177（1.1%）** | 同上 |
| D8 | 88 份工件中含**严格布尔总判定字段**（`overall_pass`/`all_gates_pass`/`overall_PASS`/`pass_all`/`all_pass`）的 | **23 / 88 = 26.1%**（其中 TRUE 13 / FALSE 10） | 键名白名单提取 |
| D9 | 10 个库中源码/文档出现 `sdaa` 引用的仓库数 | **1 / 10**（仅 TensorLBM，410 处引用；其余 9 库 **0**） | `grep -rli sdaa` |
| D10 | 10 库规模/测试/benchmarks（只取与选型相关列） | 见 §1 表 | 仓库扫描 |
| D11 | 平台能力审计：`methods.computation` = **NOT_SUPPORTED**，`misc.stubs` = **STUB** | 2 项 | `audit_capabilities` |
| D12 | 上一波 KPI：成功率 71.0%（252/355）、对标算例 1 完成 + 2 在飞、静水阻力"未实测"、工具接入 4/5 | 直接引用 `00_context.md` | 上下文 |

**由 D3+D4 推出的关键态势判断（本提案的杠杆点）**：扫荡线 43/43 已派完且 43/43 已有上卡工件 → **TensorLBM 单库上卡扫荡已自然收尾，12 卡卡位将在本波次释放**。这正是 P2 深化波次可以进场的窗口，也意味着"第二个库"的选型不能再拖（否则 12 卡空转）。

**由 D8 推出的关键缺口**：74%（65/88）的 SDAA 工件**没有可机器读取的总判定字段**，`PASS` 只存在于 mission 终态或 md 文档里。→ 任何"SDAA 精度基线 ≤3%"的说法目前**不可机器复现、不可第三方重裁**。这是本提案方向 A 的全部理由。

---

## 1. 候选方向 A：SDAA 精度-性能基线台账 v1（Baseline Ledger）

### ① 目标（一句话）
把 88 份异构 SDAA 工件归一为 `sdaa_baseline_v1.json`（43 案例 × 判据/数值/墙钟/版本/设备），使"某案例是否 PASS、误差多少、耗时多少"可被一条命令重裁、且第三方可复跑比对。

### ② mission 粒度拆解（4 条单，全部 ≤140 调用 / 5400 s）

| ID | repo | 交付物 | 机器判据（可直接写成 assert） | 预算 |
|---|---|---|---|---|
| **A1** 工件归一抽取器 | TensorLBM | `tools/sdaa_baseline_extract.py` + `tests/test_sdaa_baseline_extract.py` + `artifacts/sdaa_baseline_extract.json` | ① 对 88 份工件 `parse_ok` 比例 ≥ 95%，`not-na` 的判定字段数 ≥ 1 的案例 ≥ 40/43；② 每个字段缺失必须写 `null` + `missing_reason`（非空字符串），`missing_reason` 空字符串计数 = 0；③ 抽取器对 3 类异构格式各 1 个 fixture 单测全绿（`pytest -k baseline`）；④ 抽取结果含 `torch_version`/`torch_sdaa_version`/`device_name`/`git_sha` 四元组，缺失则标 `INVALID`（口径沿用 session-001 质询 #19 的版本锁定要求） | 140 调用 / 5400 s（纯离线，实际预计 < 25 min） |
| **A2** 判据重裁（不采信历史终态） | TensorLBM | `docs/sdaa_baseline/CASE_VERDICTS.md` + `artifacts/sdaa_baseline_v1.json` | ① 43 案例每个都有脚本算出的 `gates[]`（每 gate 含 `name/expr/value/threshold/PASS`），手抄数字计数 = 0（用 `grep -c` 校验 md 内数字均由 json 渲染）；② 每案例明确 `evidence_level ∈ {REAL, MOCK, STUB, WITHHELD, NOT_SUPPORTED}`；③ 与 D8 严格字段子集（23 例）比对，凡本单判定与历史判定冲突的案例必须写 `conflict_reason`，冲突数可 ≠ 0 但每一条都要有理由 | 140 / 5400 s |
| **A3** 单卡性能基线（精度以外的第二个门） | TensorLBM | `artifacts/sdaa_perf_baseline.json` + `docs/sdaa_baseline/PERF.md` | ① 固定 1 个 case（建议 `cavity_3d_full`，已有 4283.9 s 实测记录）+ 固定网格/步数，重复 5 次，**墙钟 CV ≤ 2%**；② 输出 MLUPS（= 网格点×步数/墙钟/1e6）与显存峰值；③ 同 case 的 CPU 参考耗时至少记 1 次用于加速比；④ 记录 `nvidia-smi` 等价物不可用事实（SDAA 上无 nvidia-smi），co-tenancy 状态必须显式记 `exclusive|shared` | 140 / 5400 s（独占单卡窗口，墙钟 ≤ 3000 s + 5 次重复） |
| **A4** 台账看板/自动汇编 | TensorPlatform | `backend/.../sdaa_baseline_view`（只读端点）+ 页面 + 1 份自动生成报告 | ① 端点返回 schema 校验通过（`jsonschema` 对 `schemas/sdaa_baseline_v1.schema.json`）；② 页面上的每个数字都从 json 渲染，端点响应内数字与 json 逐字段 diff = 0；③ pytest 端到端 1 例（GET 200 + schema valid） | 140 / 5400 s |

### ③ 卡位分配
- A1/A2：**0 卡**（纯离线解析，可与任何线 co-tenancy 不冲突）。
- A3：**1 卡 × 1 个独占窗口（≤1 h）**，禁止 co-tenancy（否则 CV 判据失效）。
- A4：**0 卡**。
- 峰值：**1 卡**。这是本波次最便宜的、但收益最大的一单。

### ④ 风险与依赖
- **依赖 D3 收尾**：A2 重裁时若扫荡线仍 in-flight，工件会被覆写 → 要求 A2 开始时对 88 份工件做 `sha256` 快照并写进产物（顺带解决"基线漂移"问题）。
- **风险 R-A1**：`parse_ok ≥ 95%` 可能不达标（88 份工件里键名至少 3 种写法，我已实测到 `wall_clock_s/wall_sec/wall_time_s/elapsed_s` 四种）。缓解：判据允许"标 `missing_reason` 视为解析成功"，但**不允许**用默认值填充。
- **风险 R-A2**：A2 重裁与历史终态冲突量大 → 这是**有价值的暴露**（等于把 71.0% 成功率的口径问题摊开），但要防止本单变成"重跑 43 案例"（超预算）。明确：**A2 只重裁，不重跑**，需重跑的一律登记为 A2 的 `needs_rerun[]` 交给下游。

### ⑤ 放弃条件
- A1：若 88 份工件 `parse_ok < 60%` → 放弃归一，产出一份《工件异构成因与重采必要性》结论书后关单（不再堆时间）。
- A3：若 3 次独立窗口的墙钟 CV > 5% → 该数据**降级为"仅报告，不作基线"**（沿用 session-001 质询 #1 的处置口径），并在 PERF.md 首行写 `BASELINE_INVALID`。
- A4：若 `:8002`/`backend` 端点无法在预算内打通 → 降级为静态报告页（JSON 直出），并如实记降级。

---

## 2. 候选方向 B：第二个库上卡选型裁决（普查 → 单点 PASS）

### ① 目标（一句话）
用一次 3 小时的三库普查（TensorDG / TensorFVM / TensorFEM）产出**可机器裁的选型裁决书**，把"第二个上卡的库是谁"从口头讨论变成 `artifacts/sdaa_lib_survey.json` 里的一行结论。

### ② 依据（D9/D10，弱证据要如实标注）
10 库 SDAA 就绪度现状：

| 库 | py 文件 | 代码行 | sdaa 引用 | benchmarks | tests | 上卡证据 |
|---|---|---|---|---|---|---|
| TensorLBM | 1965 | 595150 | **410** | 35（43 verified） | 483 | 43/43 案例 |
| TensorPlatform | 4469 | 1693833 | 0 | 0 | 9 | 无 |
| TensorFEM | 492 | 46448 | 0 | 3 | **171** | 无 |
| TensorFVM | 437 | 45576 | 0 | 0 | 103 | 无 |
| TensorDEM | 115 | 15893 | 0 | 4 | 54 | 无 |
| TensorBEM | 75 | 18219 | 0 | 0 | 35 | 无（m56 FAIL / m57 成功，但**资产未回灌主仓**） |
| TensorCAD | 58 | 20986 | 0 | 0 | 21 | 无 |
| TensorDG | 39 | 4684 | 0 | **18** | 9 | 无 |
| TensorPhysics | 102 | 19064 | 0 | 1 | 36 | 无 |
| TensorSPH | 23 | 5266 | 0 | 6 | 10 | 无 |

**诚实标注**：上表只统计"仓库里有没有 sdaa 字样 / 有多少 benchmark 与 test"，**我未逐个读 9 库源码判断其计算是否走 `torch` 张量**。所以这张表只能支持"谁最可能低风险上卡"，不能替代普查。**我的先验排序（待 B0 证伪）**：DG（纯 torch + 解析解 benchmark 密集，代码量最小 4.7k 行，最可能一次跑通）> FVM（45k 行 + 103 tests，Ghia 基准齐，但可能有 numba/CPU 专用路径）> FEM（46k 行 + 171 tests，模态解析解易取值，但矩阵装配可能依赖 scipy）> SPH / Physics > CAD（几乎纯 CPU 几何）> DEM（接触搜索用树结构常依赖 numba）。

### ③ mission 粒度拆解

| ID | repo | 交付物 | 机器判据 | 预算 |
|---|---|---|---|---|
| **B0** 三库上卡兼容性普查 | TensorDG, TensorFVM, TensorFEM | 各 1 份 `artifacts/sdaa_compat_<lib>.json` + `docs/sdaa_compat/SURVEY.md` | ① 每库必须给出**张量设备探针**证据（构 1 个 tensor，打印 `.device` 为 `sdaa:0`，并跑 ≥1 次完整 step/装配）；② 每库记录 `blocked_at`（失败点函数名 + 异常类型）或 `ok`；③ 每库给"sdaa 与 CPU 同一最小算例的逐值差"（若跑到）或 `NOT_AVAILABLE` + 原因；④ 三库任一出现 `triton`/`numba`/`cupy`/`import cuda` 硬依赖必须显式列出 | 140 / 5400 s（3 卡 × 40 min 错峰） |
| **B1** DG 单点 PASS（首选） | TensorDG | `artifacts/sdaa_dg_pass.json` + `docs/sdaa_compat/DG.md` | ① 取 1 个有解析解的 case（线性对流/扩散），相对 L2 误差 ≤ **3%**；② sdaa vs CPU 同算例 L2 差 **< 1e-6**（浮点等价级）；③ 设备证据行存在；④ 若 case 需 `float64`，明确记录 SDAA 是否支持 double（这是常见雷） | 140 / 5400 s |
| **B2** FVM 单点 PASS（次选） | TensorFVM | `artifacts/sdaa_fvm_lidcavity.json` + `docs/sdaa_compat/FVM.md` | ① lid-driven cavity Re=1000，与 Ghia et al. 1982：`u` 在 x=0.5 竖线最小值、`v` 在 y=0.5 横线最小值，**误差 ≤ 3%**；② 每 100 步残差单调下降到 `1e-6`；③ sdaa vs CPU L2 < 1e-6 | 140 / 5400 s |
| **B3** FEM 单点 PASS（第三选） | TensorFEM | `artifacts/sdaa_fem_cantilever.json` + `docs/sdaa_compat/FEM.md` | ① 悬臂梁一阶固有频率 vs Euler-Bernoulli 解析解误差 **≤ 3%**；② 装配矩阵与 CPU 端 `nnz` 与 Frobenius 范数差 < 1e-8；③ 设备证据行 | 140 / 5400 s |
| **B4** 资产回灌（可选，P2 尾） | TensorBEM | m56/m57 的 sdaa 资产合并路径清单 + 复跑 1 例 | ① TensorBEM 主仓出现 ≥1 个 `sdaa` 引用；② 复跑的球体声散射算例相对解析解 ≤ 5%（沿用 session-001 质询 #8 的 benchmark 口径） | 140 / 5400 s |

### ④ 卡位分配
- B0：**3 卡 × 40 min**（DG/FVM/FEM 并行，各自独立卡，避免互相 OOM 干扰）。
- B1/B2/B3：**按普查胜者串行放行**，每单 1–2 卡，同一时刻最多 **2 卡**。
- B4：**1 卡**。
- 峰值：**3 卡**（普查窗口），其后 2 卡。

### ⑤ 风险与依赖
- **依赖**：B1/B2/B3 的判据"≤3%"需要 A 的台账提供**同口径 CPU 参考值**，否则 CPU 基线也无版本锁定 → 若 A 未完成，B 线判据降级为"仅 sdaa vs CPU 相对差 <1e-6"（不宣称对文献精度）。
- **风险 R-B1（最高）**：`torch.compile` 在 SDAA 上不可用（m71 工件已记录：`teco_inductor incomplete`，自动回退 eager；`TRITON_INTERPRET=1` **不能**修复 inductor 缺口）。→ 本提案要求所有 B 线 mission 显式记录 `compile_route`，**禁止把 CPU/eager 结果冒充加速结果**（沿用上下文的 triton 绕行纪律）。
- **风险 R-B2**：TensorFEM/FVM 可能大量依赖 `scipy.sparse`（CPU-only）→ 普查要区分"用的是 scipy 还是 torch 稀疏"，若为 scipy，则**结论应写成"该库计算核心不在张量后端，上卡只是外壳"**，这本身是重要结论。
- **风险 R-B3**：`float64` 在 SDAA 上可能不支持 → 解析解验证常需 double，若冲突则改用 float32 并**如实记录精度天花板**。

### ⑥ 放弃条件
- B0：若预算内某一库连"张量设备探针"都跑不出（例如 import 即崩）→ 该库标 `BLOCKED` + 异常堆栈，**不追**，转下一库。
- B1/B2/B3：任一库若出现 **CPU 无法回退的硬依赖**（例如只接受 CUDA kernel）→ 立即关单记 `BLOCKED`，把卡位让给下一候选。
- B 线整体：若 **3 库全部 BLOCKED** → 产出《上卡深化不可行清单（逐库阻塞点）》上报 owner，**不得**为凑数改走 CPU 结果冒充（这是本提案的诚实红线）。

---

## 3. 候选方向 C：LBM 历史 FAIL 的根因归零（不是再跑一遍）

### ① 目标（一句话）
把 10 个"有严格布尔判定且为 FALSE"的案例（D8 实测：`couette_2d`、`cylinder`、`cylinder_3d`、`permeability`、`permeability3d`、`sphere_re100_mem`、`laplace_droplet`、`womersley`、`poiseuille_2d`、`aircraft_icing_naca0012_rime`）逐条落到**根因类别**（判据错 / 配置错 / 实现错 / 设备或后端错 / 预算不足），使"再跑一遍"变成"按根因派修复单"。

### ② 为什么这比 M-S1/S2/S3 更该先做
session-001 P2 计划里的 M-S1/S2/S3（3D 单相 / 自由面 / 多相上卡）是"新能力上卡"；但现状是**已有的 10 个案例在卡上是 FALSE**。在根因未清的情况下再开 3D/多相新线，等于在同一批未诊断的坑上叠加风险。**先归零，再深化。**

### ③ mission 粒度拆解

| ID | repo | 交付物 | 机器判据 | 预算 |
|---|---|---|---|---|
| **C1** FAIL 根因分类 | TensorLBM | `docs/sdaa_triage/ROOT_CAUSE.md` + `artifacts/sdaa_triage.json` | ① 10/10 案例每个有 `root_cause_category ∈ {CRITERION, CONFIG, IMPL, BACKEND, BUDGET}` 且附**可复现证据**（日志行号 / 工件字段路径）；② 每案例给"最小修复动作 + 预计耗时"，`estimated_fix_hours` 非空；③ 凡分类为 `CRITERION`（判据本身错）的，必须给出"旧判据 vs 新判据"数值对照 | 140 / 5400 s（纯离线） |
| **C2** 3D 单相显存-精度包线（承接 M-S1） | TensorLBM | `artifacts/sdaa_sphere_re100_envelope.json` + `docs/sdaa_triage/ENVELOPE.md` | ① `sphere_re100` 在 D=10/20 上给出显存峰值与墙钟（已有 m*`_v5_d10_20/80/200` 的历史点可复用，本单只补 D20 收敛档）；② Cd vs 参考值误差 **≤ 3%**；③ 显存峰值 ≤ **14.94 GiB**（m75 实测单卡容量，写死为上界）；④ 若 OOM，必须给"OOM 发生在哪一行 tensor 操作"（m75 已给出范式：`bfl_mask[1:].sum()`） | 140 / 5400 s（2 卡 × 错峰） |
| **C3** 自由面 3D 质量守恒（承接 M-S2） | TensorLBM | `artifacts/sdaa_dambreak3d.json` + `docs/sdaa_triage/FREESURFACE.md` | ① 质量漂移 ≤ CPU 基线实测值（**先测后锁**，口径沿用 session-001 质询 #3/#7）；② sdaa vs CPU 液面高度场 L2 **≤ 1%**；③ 若 CPU 基线本身 > 0.1%/1e4 步，以 CPU 基线为准并显式写明 | 140 / 5400 s（2 卡） |
| **C4** 相场 Laplace 定律（历史 FAIL 复检） | TensorLBM | `artifacts/sdaa_laplace_recut.json` + `docs/sdaa_triage/LAPLACE.md` | ① `Δp·R/σ` vs 解析值 **≤ 3%**；② 复跑 2 个网格确认收敛阶（`p` 值记录）；③ 与 m24/m25/m52 历史数值逐项对照，说明是否被根因修复改变 | 140 / 5400 s（2 卡） |

### ④ 卡位分配
C1 **0 卡**（离线）→ C2 **2 卡**、C3 **2 卡**、C4 **2 卡**，三者错峰，任一时刻 ≤ 2 卡在用（等 A3 的独占窗口结束后再启）。
峰值：**2 卡**。

### ⑤ 风险与依赖
- 依赖 A（台账口径）与 C1（根因分类）的顺序：C1 必须先于 C2/C3/C4，**否则修复方向是猜的**。
- 风险 R-C1：C1 纯分析，容易被当成"文档单"降低价值 → 判据里强制要求 `evidence` 可点回工件字段路径，不允许"推断式根因"。
- 风险 R-C2：D6 显示最大墙钟 7351.5 s > 5400 s 预算帽（D7：2/177 超帽）→ **C2~C4 必须按网格分档拆单**，单档墙钟门 ≤ 5400 s，超了就拆。

### ⑥ 放弃条件
- C1：若 10 案例中有 > 3 例的工件**不足以判定根因**（无日志/无中间量）→ 该批标 `WITHHELD`，并直接把"补诊断埋点"作为新的入池单（而不是硬凑分类）。
- C2：若 D20 仍 OOM 且分块方案在本波次预算内不可行 → 写"D≤16 为当前基线，3D 大网格需分块单独立项"，不做无限调参。
- C3：若 CPU 基线本身质量漂移 > 1%/1e4 步 → 判据失效，转"先修 CPU 侧守恒性"。

---

## 4. 候选方向 D：DEM–FEM 耦合最小闭环（先 CPU 后上卡）

### ① 目标（一句话）
在 **CPU** 上跑通一个双向耦合最小闭环（DEM 接触力 ↔ FEM 变形反力），用 1 个解析标杆（Hertz 接触力–位移关系，或球形颗粒撞击弹性梁的接触时长）判定物理正确性，**上卡作为第二步而非第一步**。

### ② mission 粒度拆解

| ID | repo | 交付物 | 机器判据 | 预算 |
|---|---|---|---|---|
| **D1** 耦合接口契约 | TensorDEM, TensorFEM | `docs/sdaa_coupling/IFACE.md` + `schemas/dem_fem_coupling_v1.json` | ① schema 通过 `jsonschema` 校验；② 字段含 `positions/velocities/contact_forces/dt/step` 且声明 dtype 与 device；③ 两库各有一个符合 schema 的 `dump()` 实现，单测对 schema 校验通过 | 140 / 5400 s（0 卡） |
| **D2** 耦合标杆（CPU） | TensorDEM, TensorFEM | `artifacts/dem_fem_hertz_cpu.json` + `docs/sdaa_coupling/HERTZ.md` | ① 单球压入弹性半空间，`F ∝ δ^1.5` 拟合指数 **1.5 ± 0.05**；② 接触刚度 vs Hertz 解析 `K = 4/3·E*·√R` 误差 **≤ 3%**；③ 能量守恒：总能量漂移 ≤ 1%（除接触耗散） | 140 / 5400 s（0 卡） |
| **D3** 上卡（仅在 D2 PASS 后） | TensorDEM, TensorFEM | `artifacts/sdaa_dem_fem.json` + `docs/sdaa_coupling/SDAA.md` | ① sdaa vs CPU 同算例 `δ(t)` 曲线 L2 **< 1e-6**；② 设备证据行；③ step 间数据搬迁耗时占比记录（这是耦合上卡的真实成本项） | 140 / 5400 s（2 卡 × 1 h） |
| **D4** 规模-精度扫描 | 同上 | `artifacts/sdaa_dem_fem_scan.json` | ① ≥3 个颗粒数档位，记录墙钟与内存；② 报"每步接触搜索占比"，若 > 70% 则明确结论"耦合瓶颈在 DEM 搜索而非 FEM 求解" | 140 / 5400 s（2 卡） |

### ③ 卡位分配
D1/D2：**0 卡**；D3：**2 卡 × 1 h**；D4：**2 卡**。峰值 2 卡。

### ④ 风险与依赖
- **依赖**：D 线依赖 B0 普查（DEM/FEM 是否有可用张量后端）→ 若 B0 判 DEM 为 `BLOCKED`，D3/D4 直接不启动，D1/D2 仍有独立价值（接口契约 + CPU 标杆是长期资产）。
- 风险 R-D1（高）：两库均 **0 处 sdaa 引用**（D9），且 TensorDEM 的邻域搜索常依赖 `scipy.spatial.cKDTree`（CPU-only）→ 上卡时搜索仍在 CPU，会得到"数据在卡上、搜索在 CPU"的假加速/真瓶颈。**必须在 D4 显式量化**。
- 风险 R-D2：Hertz 解析解对网格分辨率敏感（FEM 侧接触区需 ≥ 数个单元），容易把"网格不足"误判成"耦合错"。→ 判据加一条：单元尺寸减半时接触刚度变化 < 5% 才认 3% 误差。

### ⑤ 放弃条件
- 若 D1 在 1 单预算内无法闭环（两库数据结构差距过大）→ 降级为"文档 + 参考实现"，上卡推迟到 P3（与 session-001 分歧 2 的"耦合阶梯推迟"一致）。
- 若 D2 的 `F ∝ δ^1.5` 拟合不出 1.5（指数偏离 > 0.15）且网格加密 2 次仍不过 → 判 `IMPL` 缺陷，转修复单，**不在本波次继续投入卡时**。

---

## 5. 推荐入池顺序与 32 卡卡位总表

**推荐：A（必须先行）→ B0（3 小时普查，裁决第二个库）→ C1（离线根因）→ B1/B2/B3 按普查胜者 → C2/C3/C4 → D1/D2 → D3/D4。**

理由：A 是全部"≤3%"判据的**分母**（没有版本锁定的 CPU 参考值，3% 无从谈起，D8 已证 74% 工件当前不可裁）；B0 只用 3 卡 2 小时却回答 owner 最关心的"10 库哪个先上卡"；C1 零卡但能避免 C2–C4 盲目调参。

| 车道 | 卡数 | 时段 | 约束 |
|---|---|---|---|
| **A1/A2（离线归一 + 重裁）** | 0 | 第 1 日 | 开工即对 88 工件做 sha256 快照 |
| **A3（单卡性能基线）** | 1 | 第 1 日 1 h | **独占**，禁 co-tenancy，5 次重复 |
| **B0（三库普查）** | 3 | 第 1 日 2 h | 三卡并行，各卡独立 |
| **C1（FAIL 根因）** | 0 | 第 1–2 日 | 纯离线 |
| **B1/B2/B3（胜者单点 PASS）** | 2 | 第 2 日起 | 串行放行，单档墙钟 ≤ 5400 s |
| **C2/C3/C4（3D/自由面/相场）** | 2 | 第 2–3 日错峰 | 与 A3 独占窗口互斥 |
| **D1/D2（耦合 CPU 闭环）** | 0 | 第 2–3 日 | 无卡 |
| **D3/D4（耦合上卡）** | 2 | 第 3 日起 | 仅在 D2 PASS + B0 判 DEM 可行后 |
| **A4（台账看板）** | 0 | 第 3 日 | 与后端线共享 |
| **合计峰值** | **6 卡** | — | 远低于 12 卡待释放额度（D3/D4 证明扫荡线已 43/43 收官） |

**分配原则（写给值班长）**：本波次全部单的墙钟门 ≤ 5400 s（D7 已实测有 2 个历史样本 7351.5 s 级，必须拆单）；计时类单（A3）独占；精度类单（B1–B3/C2–C4）可与离线单 co-tenancy。

---

## 6. 与 session-001 P2 计划的关系（不重复造轮子）

| session-001 既有 | 本提案的增量 |
|---|---|
| M-P1/P2 性能基线（CV ≤2%） | A3 复用该口径，但**限定单一 case + 记录 MLUPS 与显存**，并新增"co-tenancy 状态"字段 |
| M-C3「第二个库」裁决书（三条客观判据） | **B0 提供 M-C3 所缺的普查数据**（3 卡 2 h 即可），并给出可机器裁的 `sdaa_lib_survey.json` |
| M-S1/S2/S3（3D/自由面/多相上卡） | C2/C3/C4 复用其判据（≤3% / L2 ≤1% / Δp·R/σ ≤3%），但**前置 C1 根因归零**，避免在已 FALSE 的坑上叠新线 |
| S1–S3 耦合阶梯（已推迟 P3） | D1/D2 把"接口契约 + CPU 标杆"提前做掉（**零卡**），使 P3 上卡时风险大幅下降 |
| M-T1 triton 审计（5 模块纯 torch 等价） | 本提案不重复；但 B 线所有单强制记 `compile_route`（m71 已证 `teco_inductor` 不完整） |

---

## 7. 我没做的事 / 不确定处（honest）

1. **我没有对 43 个案例做通过率重判**。理由：D8 显示仅 26.1% 工件有严格布尔总判定字段，其余 74% 的 PASS 只存在于 mission 终态或 md 文本，我若给出通过率必然是二手口径 → 故本提案**不报通过率**，把这件事作为 A1/A2 的交付。
2. **我不掌握 9 个非 LBM 库的源码实现细节**（是否走 torch 张量、是否依赖 scipy/numba/cupy）。§1 的选型表只用了"py 文件数 / 代码行 / sdaa 引用 / benchmarks / tests"这类弱证据，我把排序写成"先验排序（待 B0 证伪）"，不作裁决。
3. **TensorBEM 的 m57 虽 succeeded，但主仓 `grep sdaa` = 0**（D9）→ "资产未回灌"是我的推断，未逐文件核对 m57 工作树 diff；B4 就是为了核实这件事。
4. **D5 说 32/32 卡号都出现过，≠ 32 卡同时在用**。上下文给的"利用率 12/32"与"在飞 24"两个数并列，我无法从日志判定其口径，故卡位表按"峰值 6 卡、上限 12 卡"保守写。
5. **Hertz 标杆的适用性未验证**：D2 选 Hertz 是因为有闭式解，但 TensorFEM 是否支持接触单元我没查；若不支持，标杆需换成"梁受集中力挠度"（解析解同样存在）。
6. 本提案**不产出任何 move 派发**；卡位与排期仅供综合主席与 owner 裁决，我无派发权。

---

## 8. 对指南 KPI 的映射（每条都指到 §1 的数据）

| KPI（`00_context.md`） | 本提案贡献 |
|---|---|
| 静水阻力 ≤5% / 冰区 ≤10% **未实测** | A1–A3 给出"实测"所需的分母（版本锁定 CPU 参考值 + 计时基线），C1 清理 FALSE 案例 |
| 对标算例 ≥10（现 1 完成 + 2 在飞） | C2 把 `sphere_re100`/`cavity_3d_full` 变成可引用的 PASS 算例；B1–B3 在非 LBM 库上各加 1 个文献基准 |
| 工具接入 ≥5 类（现 4/5） | B0/B1–B3 是"第二个求解库"这条路径的机器证据 |
| 成功率 ≥90%（现 71.0%） | A2 把 74% 无判定工件纳入可裁范围；C1 直接降低重复 FAIL |
| 谱系追溯 100%（现 62%） | A1 输出的每条记录都带 `torch/torch_sdaa/git_sha/device_name` 四元组（session-001 质询 #19 口径） |
| 周期 −50% / 试验 −30%（无基线） | A3 产出第一个可引用的墙钟基线（含 median 36.19 s 这类现状分布，D6） |

---

*本文件为 session-002 提案人个人意见，不构成派发指令；终审归 owner。所有数字标注了来源与采集方式，不确定处已在 §7 显式列出。*