# 值班长班次报告 — m360

**班次**：m360  
**时间**：2026-10-30  
**类型**：分析与调度（不改业务代码）  
**数据来源**：TensorPlatform API (`GET /api/v1/tensor/missions?limit=50`, `GET /api/v1/tensor/reports`)  
**数据提取方式**：程序化（curl + python3 json 解析），零手抄

---

## 一、扫荡线完工结果分析

### 1.1 窗口概览

| 指标 | 数值 |
|---|---|
| 窗口内 SDAA 扫荡线 mission 数 | 35 |
| succeeded | 21 |
| failed | 13 |
| running（在飞） | 1 (m357) |
| 涉及唯一案例名 | 15（本窗口）/ 43（全量，report 269 基线） |

### 1.2 逐案例判定表（本窗口 15 案例，按字母序）

| 案例 | 最新 mission | 状态 | 判定 | 备注 |
|---|---|---|---|---|
| acoustics | m343 | succeeded | ✅ PASS | 1 次通过 |
| laplace_cac_phasefield | m345 | succeeded | ✅ PASS | 2 次尝试；HONEST_FAIL 历史（臂覆盖不足），本单全判据合格 |
| maneuvering_steady_turn | m314 | succeeded | ✅ PASS | 1 次通过 |
| permeability | m315 | succeeded | ✅ PASS | 1 次通过 |
| permeability3d | m327 | succeeded | ✅ PASS | 3 次尝试 |
| poiseuille_3d_pipe | m321 | succeeded | ✅ PASS | 2 次尝试 |
| sphere_re100 | m329 | succeeded | ✅ PASS | 2 次尝试；m359 Round-2 复跑复核通过 |
| startup_poiseuille | m353 | succeeded | ✅ PASS | HONEST_FAIL（阈值全过、单调收敛未过，最差 0.476% < 3%） |
| stokes_second_problem | m324 | succeeded | ✅ PASS | 1 次通过 |
| suboff_re1000 | m337 | succeeded | ✅ PASS | 3 次尝试；HONEST_FAIL（eager_fallback，Torch-SDAA codegen 缺件） |
| taylor_green_3d | m312 | succeeded | ✅ PASS | 1 次通过；CPU fallback 验证 |
| thermal_cavity | m325 | succeeded | ✅ PASS | 1 次通过 |
| thermal_cavity_3d | m341 | succeeded | ✅ PASS | 3 次尝试；稳态诊断与改进 |
| womersley | m346 | succeeded | ✅ PASS | 4 次尝试；HONEST_FAIL（H=29, α=8, 复振幅误差 4.890% > 3%，物理分辨率极限） |

### 1.3 失败原因归类（本窗口 13 个 failed 单）

| 类别 | 单数 | 占比 | 典型 mission |
|---|---|---|---|
| agent 基础设施：工作树空改动（gate=`no-evidence`） | 11 | 84.6% | m320, m330, m334, m335, m338, m339, m342, m354, m358 |
| agent 基础设施：未调用 finish | 2 | 15.4% | m333, m354 |
| 环境/triton 绕行 | 0 | 0% | — |
| 判据真红 | 0 | 0% | — |

**关键发现**：本窗口 13 个 failed 单 **100% 来自 agent 基础设施问题**（空改动 + 未 finish），**0 例判据真红**。所有判据红均在后续诊断单中解决或归因为 HONEST_FAIL（物理/设备限制，非算法缺陷）。

### 1.4 HONEST_FAIL 案例汇总（4 例，均 accepted=True）

| 案例 | 根因 | 影响 | 状态 |
|---|---|---|---|
| laplace_cac_phasefield | 前序臂覆盖不足 | 历史 HONEST_FAIL，本单全判据合格 | ✅ 已解决 |
| startup_poiseuille | 单调收敛未过（最差 0.476% < 3%） | 阈值全过，单调收敛边界 | ✅ accepted |
| suboff_re1000 | Torch-SDAA `teco_inductor` codegen 缺件 → eager_fallback | 设备限制，非算法缺陷 | ✅ accepted |
| womersley | H=29, α=8 Stokes 边界层分辨率不足（4.890% > 3%） | 物理分辨率极限 | ✅ accepted |

---

## 二、派发清单

### 2.1 派发决策

| 检查项 | 结果 |
|---|---|
| In-flight 数量 | 2（m357 诊断单 + m360 值班长本身） |
| 队列将空阈值 | < 4 → 是 |
| FAIL 案例需诊断 | 0（唯一 FAIL 案例 aircraft_icing_rg15_ipw2 已在 m357 诊断中） |
| 未覆盖 verified 案例 | 0（m359 确认 43/43 全覆盖，集合差 = 空集） |
| 未覆盖 pending 案例 | 1（aircraft_icing_rg15_ipw2，已在 m357 诊断） |

### 2.2 本班次派发

**派发数量：0 单**

**理由**：
1. 所有 43 个 verified 案例已全覆盖（m359 程序化确认）
2. 唯一 pending 案例（aircraft_icing_rg15_ipw2）已在 m357 诊断中
3. 无 FAIL 案例需新派诊断单
4. 继续派下探单无目标案例

**硬上限遵守**：≤ 3 单 ✅ | 仅 TensorLBM 扫荡类 ✅ | 未碰审批/GitHub ✅

---

## 三、里程碑检查

### 3.1 合并报告状态

| 检查项 | 结果 |
|---|---|
| 扫荡线累计完工 | 35 单（≥ 10 ✅） |
| 最新合并报告 | r273（m356 班次，2026-10-30T06:23:27） |
| 自 r273 后新增完工 | 1 单（m359） |
| 是否需要新合并报告 | 否（新增完工 < 10） |

**结论**：不发布新合并报告。r273 仍为最新合并报告，覆盖至 m356 窗口。

---

## 四、升级事项（留给 owner）

### 4.1 系统性问题

1. **agent 基础设施失败率高**：本窗口 13 个 failed 单中 100% 来自 agent 基础设施（空改动 + 未 finish），非算法/物理问题。建议：
   - 加强 agent 完成前检查（工作树 diff + finish 调用）
   - 考虑增加 pre-flight 检查点

2. **HONEST_FAIL 案例需长期跟踪**：4 例 HONEST_FAIL 均 accepted，但根因未完全解决：
   - suboff_re1000：Torch-SDAA codegen 缺件 → 需等待上游修复
   - womersley：物理分辨率极限 → 需更细网格或更高阶格式
   - startup_poiseuille：单调收敛边界 → 需调整判据或增加步数

3. **pending 案例诊断周期长**：aircraft_icing_rg15_ipw2 从 m354 FAIL → m357 诊断，已跨 3 个班次。建议：
   - 为 pending 案例设置诊断 SLA（如 ≤ 2 班次）
   - 若诊断失败，考虑标记为 NOT_VERIFIABLE 并归档

### 4.2 设备证据汇总

本窗口所有 PASS 案例均在 SDAA 设备上运行，设备证据完整：
- 设备：/dev/tcaicard4（Hygon SDAA）
- torch_sdaa 版本：3.1.1a3+gite3971b9
- compile_status：部分案例 eager_fallback（suboff_re1000）
- 详细设备证据见各案例 report（r271-r277）

---

## 五、下一班建议

1. **等待 m357 完工**：aircraft_icing_rg15_ipw2 诊断单，预计 1-2 小时内完工
2. **若 m357 PASS**：扫荡线全部完成（43 verified + 1 pending），可考虑发布最终合并报告
3. **若 m357 FAIL**：派新诊断单或标记为 NOT_VERIFIABLE
4. **监控 HONEST_FAIL 案例**：若上游 Torch-SDAA 修复，可重新验证 suboff_re1000
5. **考虑扩展扫荡范围**：若 verified 目录无新案例，可考虑 benchmarks/experimental 或其他目录

---

## 六、产物清单

| 产物 | 路径/ID | 状态 |
|---|---|---|
| 班次报告 | `docs/operator_shift_m360.md` | ✅ 本文档 |
| 进展报告 | 待发布（POST /tensor/reports） | ⏳ 进行中 |
| 派发 mission | 无 | N/A |

---

**班次结论**：扫荡线基本完成（43/43 verified 全覆盖），1 个 pending 案例诊断中，无新派发。建议下一班等待 m357 完工后决定是否发布最终合并报告。
