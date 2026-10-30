# 值班长班次 m288 — 扫荡线完工结果分析 + 3 单派发

- **班次号**: m288
- **分支**: agent/m288（仓库 TensorPlatform）
- **角色**: 值班长（分析与调度，**不改业务代码**）
- **平台 API**: `$TENSOR_API_BASE` = `http://127.0.0.1:8010/api/v1`（登录账号 `eco_admin`）
- **论文级报告**: 已发布 → report **id=207**（`【SDAA 扫荡】值班长 m288 班次分析报告 — 窗口 m239–m288 逐单判读 + 3 单派发`，3 张图件已落盘且可经 `GET /reports/207/figures/<name>` 取回）
- **数据纪律**: 全部数字由脚本从 API JSON 抽取（`/tmp/all_merged.json` ← `?limit=200&offset=0` + `?limit=200&offset=200`），零手抄；判定一律引用各单 `result.checks/headline/declared_summary` 原文，**未修改任何判据口径**。

---

## 一、扫荡线累计状态（全量 288 单口径）

| 指标 | 数值 | 来源 |
|---|---|---|
| 平台 mission 总数 | 288 | `GET /tensor/missions?limit=200&offset=0|200`（`total=288`） |
| 扫荡线单数（含诊断单） | **110** | goal 含「SDAA 验证案例扫荡」+ 含 `benchmarks/verified/<case>` + 非补报单 |
| ├ sweep 类 | 83 | 同上 |
| └ diagnosis 类（【诊断+修复绕行】） | 27 | 同上 |
| succeeded | **77** | status 字段 |
| failed | **31** | status 字段 |
| running（在飞） | **2**（m250 thermal_cavity、m251 laplace_cac_phasefield） | status 字段 |
| 覆盖唯一案例数 | **43** | 从 goal 中案例路径去重 |

> 注：上一班次 m262 报告的「未覆盖案例 18 例」在全量扫描下不成立——该 18 例中 15 例分别已有扫荡单（m27/maneuvering_steady_turn、m28/permeability、m29/permeability3d、m33/poiseuille_3d_duct、m34/poiseuille_3d_pipe、m39/shear_wave_decay、m40/sod_shock_tube、m48/square_cylinder、m49/startup_poiseuille、m50/stokes_first_problem、m64/taylor_couette、m65/taylor_green_2d、m67/taylor_green_3d、m69/thermal_cavity_3d、m70/wigley_hydrostatics），另 3 例（couette_3d/cylinder/laplace_pr_eos）已由 m268/m269/m270 于本窗口补齐。**未覆盖案例池已耗尽**，见第五节。

## 二、分析窗口 m239–m288 逐单判读（10 单）

| Mission | 案例 | 类型 | status | 本班判读 | tool_calls | result 原文摘录（截断） |
|---|---|---|---|---|---|---|
| m239 | floating_body_free_decay | sweep | succeeded | PASS（显式） | 43 | 「verdict.pass=True, all 4 cases < 3%」 |
| m243 | sphere_re100_mem | sweep | succeeded | **误差达标·判定未明示** | 41 | 「D=40 (err=2.78%) 和 D=60 (err=2.54%) 均 <3%，单调收敛」——无 VERDICT 行、无 device 行 |
| m244 | suboff_sail_resistance | sweep | failed | **AGENT_FAIL** | 80 | declared_summary「完成 80 次工具调用（未调用 finish）」；headline「未完成：模型未调用 finish」 |
| m245 | thermal_cavity | sweep | failed | **AGENT_FAIL** | 1 | tool_calls=1 即终止，headline「未完成：模型未调用 finish」 |
| m250 | thermal_cavity | diagnosis | running | 在飞 | — | m249 派发，运行中 |
| m251 | laplace_cac_phasefield | diagnosis | running | 在飞 | — | m249 派发，运行中 |
| m252 | poiseuille_two_phase_cg | sweep | succeeded | PASS（显式） | 39 | 「VERDICT: PASS (max relL2=1.092%, all arms ≤ 3%) | SDAA device: /dev/tcaicard14」 |
| m268 | couette_3d | sweep | succeeded | PASS（显式） | 40 | 「SDAA fp64 四档…12 项关键量判据均 < 3%」 |
| m269 | cylinder | sweep | succeeded | PASS（显式） | 57 | 「All 10 key quantities (Cd/St) verified <3% error; smoke 27.5ms/step」 |
| m270 | laplace_pr_eos | sweep | succeeded | PASS（显式） | 51 | 「σ error 0.213% < 3%; all ΔP errors < 0.3%; device sdaa:0 /dev/tcaicard12」 |

**窗口汇总**：PASS（显式）**5** · 误差达标但判定未明示 **1** · AGENT_FAIL **2** · 在飞 **2**。

## 三、失败原因归类（窗口内 2 个 failed）

| 归类 | 单数 | 证据 |
|---|---|---|
| 环境（显存/driver/依赖） | 0 | 窗口内无环境类失败 |
| triton 绕行问题 | 0 | 窗口内无 triton 类失败收尾（相关入口已用 `TRITON_INTERPRETER=1` 绕行并跑通） |
| 判据真红（案例判据未过） | 0 | 无 |
| 其他（Agent 未调用 finish / 工具次数耗尽） | **2** | m244（80 次工具调用未 finish）、m245（1 次调用即终止） |

## 四、误差判定（关键量 vs 基准，逐项）

| 关键量（来源单） | 基准 | 相对误差 | <3%？ |
|---|---|---|---|
| sphere_re100_mem D=40 Cd（m243） | Re=100 球绕流参考 Cd | 2.78% | ✅ |
| sphere_re100_mem D=60 Cd（m243） | 同上 | 2.54% | ✅ |
| poiseuille_two_phase_cg relL2 max（m252） | 两层 Poiseuille 解析解 | 1.092% | ✅ |
| laplace_pr_eos σ（m270） | Laplace 定律 σ=ΔP·R/2 | 0.213% | ✅ |
| couette_3d 12 项（m268） | 线性剖面/解析 τ | 原文汇总「均 <3%」 | ✅（原文口径） |
| cylinder 10 项 Cd/St（m269） | 文献值 | 原文汇总「verified <3%」 | ✅（原文口径） |
| floating_body_free_decay 4 工况（m239） | 4 工况基准 | 原文汇总「all 4 cases <3%」 | ✅（原文口径） |
| suboff_sail_resistance Cf（m244） | Blasius 1.328/√Re | **无值**（AGENT_FAIL） | ❌ **未判定，不得称通过** |

**显式回答**：可抽取到具体数值的 4 项误差（最大 2.78%）**全部 <3%**，余量 0.22 个百分点；3 单仅有「<3%」汇总口径，按原文记录；suboff_sail_resistance 本窗口未产出判据值，不参与结论。

## 五、本班派发清单（3 单 = 硬上限；全部 TensorLBM / runner=native / 扫荡类 goal）

| 新单 | 案例 | 触发规则 | goal 内写明的失败证据与诊断方向 |
|---|---|---|---|
| **m289** | suboff_sail_resistance | FAIL 案例 → 诊断+修复绕行 | m244「80 次工具调用未 finish」+ m109 定位 SDAA allocator 预留导致 L≥64 首步 OOM + m63 18 项断言 PASS 但 L≥90 跑不到判据；方向：复用 m244 落盘的 `run_sdaa_chunked.py`，逐档测显存峰值判别 allocator 预留 vs 图捕获重复分配，禁止 CPU 冒充、禁止改判据 |
| **m290** | sphere_re100_mem | 判定未明示（NO_VERDICT）→ 补证收口 | m243 仅有 2.78%/2.54% 数值、无 VERDICT 行、无 device 行；方向：按案例原口径落盘机器可读 verdict（device 证据行 + 窗口长度 + 95%CI），并核对与历史 honest-FAIL（m46/m174/m202）的口径差异 |
| **m291** | womersley | 残留硬红点 → 收敛阶梯收口 | m71/m222 基线 H=29 α=8 = 4.890% FAIL；m222 给出 4.89%→2.90%→0.57% 单调收敛 + BFL q=0.45 得 2.67% PASS；方向：一次跑齐 H∈{29,58,116}×壁面口径×BFL 的逐档判据台账，H=29 基线如实保留 FAIL |

**未派「下探新案例」单的理由**：43 个案例目录均已至少被扫荡一次，未覆盖池为空（见第一节注），故 3 个名额全部用于 FAIL / 未明示案例的诊断收口；`in-flight<4` 的下探规则无可用新案例，已如实记录而非硬凑。

## 六、下一班建议

1. **先收 m289/m290/m291 三单**：m289（suboff_sail_resistance）在历史单 m109 已定位到 SDAA allocator 预留根因、m244 又因工具次数耗尽未收口，属「根因已知但从未出显式判定」的状态；若 m289 再次 AGENT_FAIL，建议按环境限制类 honest-FAIL 冻结并停止重复派单（需 owner 确认）。
2. **womersley 若 m291 拿到 H=58/116 的 <3% 证据**，可把案例判定升级为 PASS（口径不变），但 H=29 基线必须保留 FAIL 行——历史单 m71/m110/m222 已三次复现 4.890%，不得因高分辨率通过而抹掉基线红点。
3. **m243/m290 类「有数无判」是当前最容易被漏掉的形态**：下一班读 result 时请把「有数值但无 VERDICT 行」单列为需收口，不要并进 PASS。
4. **审计务必全量分页**（`limit=200` + offset），否则「未覆盖案例」「累计完工」会算错（本班已踩实，见第七节第 3 条）。
5. **m250/m251 若本班窗口内完成**：下一班优先核对其判定是否显式，thermal_cavity/laplace_cac_phasefield 属长期 HONEST_FAIL，需要逐档判据台账而非结论一句话。

## 七、升级事项（系统性，留给 owner，本班不处置）

1. **AGENT_FAIL 率系统性偏高**：110 单中 failed **31** 单（28.2%），窗口内 2/2 失败均为「未调用 finish / 工具次数耗尽」。已在本班 3 单 goal 中内置「先落盘中间 JSON → 再长跑 → 结束必发显式 VERDICT」的纪律条款，建议升级为派发模板硬约束，并评估提高 `max_tool_calls` 或对重案例拆分。
2. **案例级台账口径冲突**：report 158（m249：43 案例 PASS 32 / honest-FAIL 8 / WITHHELD 3）与 report 178（m262：PASS 28 / AGENT_FAIL 21 / 真红 0 / 未覆盖 18）互斥，且后者与全量扫描矛盾。建议 owner 授权一次性程序化台账重算并冻结为一等公民 API（如 `GET /tensor/sweep/ledger`），避免每班人工判读导致口径漂移。
3. **`/tensor/missions` 分页静默截断**：`limit=300` 返回 200 条但 `total` 仍为 288，无 `truncated` 标志，容易造成审计漏数。建议超限显式报错或返回 `truncated: true`。
4. **报告图件校验口径与存储设计不匹配（新发现，代码佐证）**：`backend/api/tensor_reports.py` 的 `create_report` 里 `figures_meta = [{"name","caption"} ...]` 只把元数据写 DB，PNG 落盘到 `FIGURE_DIR/<report_id>/<name>.png`，读取接口 `GET /reports/{id}` 因此**永远不含 `png_base64`**。本班已验证 report 207 的三张图经 `GET /reports/207/figures/<name>` 取回 200 + 合法 PNG。据此，返工单 m261（"3 张图无 png_base64 内嵌且文件不存在"）这类判据若只读 DB 字段，会**结构性误判**；建议 QA/守望线改为「先查 `GET /reports/{id}/figures/{name}` 是否 200」再判欠账。
5. **m287 班次空转**：m287（succeeded）result 原文称「API 访问受限（P0 blocker）… 端口 8004 可达但登录端点 405」，而本班用 `$TENSOR_API_BASE`（`:8010/api/v1`）一次登录成功——属 Agent 侧端口猜测导致的整班空转。建议在派发模板固定「必须使用 `$TENSOR_API_BASE`，禁止自行猜端口」，并让守望线对「结果声称环境不可用」的单做二次探测。
6. **补报单债务**：reports 列表中 m255/m266/m280/m281 等补报单 failed，说明「报告未发=未完成」的守望线仍在产生欠账，需 owner 关注偿还节奏。

---

### 本班验证方式

- 登录与全量拉取：`POST /auth/login` → `GET /tensor/missions?limit=200&offset=0` + `?limit=200&offset=200`（合并 288 单，无重复）。
- 逐单判读：`GET /tensor/missions/{239,243,244,245,250,251,252,268,269,270}` 读 `result.checks/headline/declared_summary/git_status`。
- 派发验证：`POST /tensor/missions` ×3 → 回读 m289/m290/m291 均为 `repo=TensorLBM, runner=native`，goal 含「SDAA 验证案例扫荡」。
- 报告验证：`POST /tensor/reports` → id=207 `status=published`；`GET /reports/207` 回读 markdown 8140 字符、3 图；`GET /reports/207/figures/fig1_...png` → 200 / 47 KB / PNG magic 正确。
- 未跑 pytest：本班次为分析+调度，不新增业务代码（工作树无代码改动），测试不适用。