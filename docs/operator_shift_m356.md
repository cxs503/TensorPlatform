# 值班长班次 m356 — SDAA 验证案例扫荡线分析与调度

- **班次**：m356 ｜ **mission_id**：356 ｜ **仓库**：TensorPlatform ｜ **分支**：agent/m356
- **性质**：分析与调度（**不改业务代码**）
- **数据来源**：`POST /api/v1/auth/login`(eco_admin) → `GET /api/v1/tensor/missions?limit=50`（平台总单数 356）→ `GET /api/v1/tensor/reports/269`（覆盖基线表）。所有数字程序化提取，零手抄。
- **发布报告**：TensorPlatform report **id=273**（`【SDAA 扫荡】值班长 m356 班次合并报告`，含 2 张内嵌 png）

---

## 一、本班分析表

### 1.1 判定构成（窗口 goal 含「SDAA 验证案例扫荡」且 repo=TensorLBM）

| 指标 | 数值 |
|---|---|
| 扫荡线 mission 数（窗口） | 34 |
| succeeded | 20 |
| failed | 13 |
| running（在飞） | 1 |
| 涉及案例名 | 17 |

### 1.2 失败原因归类（13 单 failed，程序化按 gate 文案）

| 类别 | 单数 |
|---|---|
| 其他 → agent 基础设施：工作树空改动（gate=no-evidence，reason「工作树无任何改动，不能报成功」） | 11 |
| 其他 → agent 基础设施：模型未调用 finish | 2 |
| 环境（无卡 / OOM / 超预算） | 0 |
| triton 绕行导致失败 | 0 |
| 判据真红导致 gate 红 | 0 |

> **口径**：本窗口 gate 层失败的 13 单**没有一例是判据真红**，全部倒在流程门（空改动 / 未 finish）。判据真红只出现在 succeeded 但 honest-FAIL 的单里（见 1.4），二者不可混算。

### 1.3 逐案例最新状态（窗口内 latest-by-id）

| 案例 | 最新 mission | 状态 | 类型 | 失败根因 |
|---|---|---|---|---|
| acoustics | 355 | succeeded | SWEEP | — |
| aircraft_icing_naca0012_rime | 347 | succeeded | SWEEP | — |
| aircraft_icing_rg15_ipw2 | 354 | failed | SWEEP | agent基础设施/空改动(no-evidence门) |
| cavity | 351 | succeeded | SWEEP | — |
| laplace_cac_phasefield | 345 | succeeded | DIAG | — |
| maneuvering_steady_turn | 314 | succeeded | SWEEP | — |
| permeability | 315 | succeeded | SWEEP | — |
| permeability3d | 327 | succeeded | DIAG | — |
| poiseuille_3d_pipe | 321 | succeeded | DIAG | — |
| sphere_re100 | 329 | succeeded | DIAG | — |
| startup_poiseuille | 353 | running | DIAG | — |
| stokes_second_problem | 324 | succeeded | DIAG | — |
| suboff_re1000 | 337 | succeeded | DIAG | — |
| taylor_green_3d | 312 | succeeded | DIAG | — |
| thermal_cavity | 325 | succeeded | DIAG | — |
| thermal_cavity_3d | 341 | succeeded | DIAG | — |
| womersley | 346 | succeeded | DIAG | — |

### 1.4 honest-FAIL 如实记录（判据红，口径未改）

| mission | 案例 |
|---|---|
| 346 | womersley |
| 337 | suboff_re1000 |
| 331 | womersley |
| 319 | suboff_re1000 |

- **suboff_re1000（m319/m337）**：Cd_tot 在 48 分辨率 / 600 步档 = **+46.43%**；根因是派发达标档 L=80（13.7M cells，3.39 h）在本机预算内跑不完，且 Torch-SDAA 3.1.1a3 `teco_inductor` codegen 缺件（`InductorError@teco.py:2073`）强制 `compile_status=eager_fallback`。
- **womersley（m331/m346）**：H=29, α=8 复振幅误差 **4.890%**；δ=H/(α√2)≈2.56 lu、边界层内仅 2–3 格点，属 LBM 二阶精度的分辨率极限；加密到 H=59/119 即 2.900%/0.450% 通过。

### 1.5 收敛阶梯（数字正则抽取自 API result 文本，用于下一班复核基线）

- womersley α=8：[[29, 4.89], [59, 2.9], [119, 0.45]]
- permeability3d φ=0.343：[[64, 4.63], [96, 3.1], [128, 2.05], [160, 1.45]]
- cavity Re=100：[[32, 2.25], [48, 1.9], [128, 0.75], [192, 0.73]]

---

## 二、派发清单（硬上限 3 单，全部 TensorLBM 扫荡类 / runner=native）

| Mission | 类型 | 目标 | 派发理由（API 证据） | 派发后状态 |
|---|---|---|---|---|
| **357** | 诊断+修复绕行 | `aircraft_icing_rg15_ipw2`（benchmarks/**pending**） | m354 failed，`evidence.checks.gate=no-evidence`「工作树无任何改动」；agent 自述已产 run_sdaa.py/artifacts_sdaa.json 但未落盘受检树；案例位于 pending，须先判「是否具备可验证判据」，禁止自造阈值 | running |
| **358** | 下探（字母序 A） | verified 目录下一个未覆盖案例 | 覆盖基线 report 269 = 43 案例 / 193 单；派发时 in-flight 仅 1（本班次自身）→ 队列将空；跳过 357/353 已占用案例 | running |
| **359** | 下探（字母序 B） | 与 358 不重复的下一个未覆盖案例 | 同上；含去重规则（同批冲突取次一个）与退化规则（无未覆盖 → 高尝试案例 round-2 复核） | running |

- 未派其他类型、未改审批、未触碰 GitHub。
- 里程碑（task 4）判定：平台**已存在**多份「SDAA 扫荡」合并报告（id 269 / 248 / 245 / 242 / 238 / 235 / 234 / 220 …），「尚无合并报告」条件不成立 → 本班不重复声称里程碑，改为发布本班次进展报告 **273**（按 owner 常设的报告纪律执行）。

---

## 三、下一班建议

1. **优先回收 357 的三态结论**：`aircraft_icing_rg15_ipw2` 若在 pending 无基准，应立即定级 NOT_VERIFIABLE 并把它从"待验证"清单移出，避免同案例第 N 次空转（m354 已是本案例首次尝试）。
2. **把「工作树空改动」前置自检写进下探单模板**：窗口 13 单失败全是这一类；建议在 goal 第 1 步即要求 `git status --porcelain` 非空证据，finish 前再验一次。
3. **建立案例尝试次数上限**：sphere_re100 13 次、womersley 13 次、suboff_re1000 11 次。建议对"物理分辨率极限"型失败（womersley H=29）在 README 里就地标注 `MIN_BL_ROWS>=5`，后续扫荡直接按合规档位跑，不再重复诊断。
4. **区分证据等级**：`TRITON_INTERPRET=1` / CPU path / `eager_fallback` 均属合法绕行但**非卡上证据**；建议下一班统计 PASS 时把「卡上证据 PASS」与「绕行 PASS」分列，避免扫荡 PASS 率虚高。
5. **下一个里程碑**：若 358/359 完成且案例覆盖数超过 43，可重算合并报告（届时累计完工早已 ≥10 单），并补齐 pending 案例的判据表。

---

## 四、升级事项（留给 owner 的系统性问题）

1. **no-evidence 门是最大失败源**（13/13 failed 单）：产物未落在受检 worktree。建议平台在 finish 时强制校验 `git status --porcelain` 并把结果写入 `evidence.checks`。
2. **teco_inductor codegen 缺件**（Torch-SDAA 3.1.1a3）：每单重复诊断同一根因，建议提为环境缺陷单跟踪。
3. **declared PASS vs gate PASS 双轨**：本窗口多单 agent 自述 PASS 而 gate 判红，建议 result 显式拆 `verdict_declared` / `verdict_gate`。
4. **成本分级缺省**：suboff_re1000 的达标档 3.39 h 超出单班次预算，建议派单模板按 cells×steps 预估工时，超预算自动降档并标注 honest-FAIL。

---

**诚实声明**：本班次只做分析与调度，未修改业务代码；未改写任何判据门限；FAIL / honest-FAIL 如实列出；未合并、未 push、未触碰 GitHub。
