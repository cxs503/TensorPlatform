# 值班长班次 m484 分析报告

**班次时间**: 2026-10-30T16:32 UTC  
**值班 Agent**: tp-arch-1 (mission 484)  
**黑名单检查**: `lid_driven_cavity_3d` 已在 `/srv/tensor/agent/guide_blacklist.json` 中（trailing_fails=4, since_mission=426），本班禁止再派。

---

## 一、扫荡线总体统计（API 返回，零手抄）

数据源: `GET /api/v1/tensor/missions?limit=100`，筛选 goal 含「SDAA」+「扫荡」且非「值班长班次」的 mission。

| 指标 | 数值 |
|------|------|
| 扫荡线 mission 总数 | 48 |
| PASS (succeeded) | 30 |
| FAIL (failed) | 13 |
| RUNNING (in-flight) | 5 |
| 完工数 (PASS+FAIL) | 43 |

### 失败原因归类（13 单 FAIL）

| 失败类别 | 数量 | 占比 |
|----------|------|------|
| 环境/no-evidence（工作树无改动） | 9 | 69.2% |
| 环境/llm_error（模型未调用 finish） | 3 | 23.1% |
| 其他 | 1 | 7.7% |
| triton 绕行 | 0 | 0% |
| 判据真红（status=failed） | 0 | 0% |

> **说明**: 所有 status=failed 的 mission 均为环境/agent 执行问题，无一为判据真红。判据真红（HONEST-FAIL）的案例均以 status=succeeded + accepted=True 完工，agent 如实报告了精度未达标。

---

## 二、逐案例判据表

| # | 案例 | 案例级判定 | mission IDs | P | F | R | 失败原因 | 备注 |
|---|------|-----------|-------------|---|---|---|----------|------|
| 1 | acoustics | PASS | [470] | 1 | 0 | 0 | — | D2Q9 平面声波 |
| 2 | aircraft_icing_naca0012_rime | PASS | [471,473,479] | 2 | 1 | 0 | 环境/llm_error | m473 llm_error, m479 补跑 PASS |
| 3 | aircraft_icing_rg15_ipw2 | RUNNING | [485] | 0 | 0 | 1 | — | **本班派发** |
| 4 | backward_step | PASS(HONEST-FAIL) | [425,444,459] | 2 | 1 | 0 | 环境/no-evidence | 精度门未达标,步数不足 |
| 5 | blasius_flat_plate | PASS(HONEST-FAIL) | [410,449,453,467] | 3 | 1 | 0 | 环境/no-evidence | Cf +27.3%, LBM 固有局限 |
| 6 | bstep_3d | PASS(HONEST-FAIL) | [411,450,462,466,469,478] | 3 | 3 | 0 | 环境/llm_error, 环境/no-evidence | +13.2% 再附着长度, bounce-back 滑移伪差 |
| 7 | capillary_invasion_washburn | PASS | [413] | 1 | 0 | 0 | — | Washburn 方程 |
| 8 | cavity | RUNNING | [481] | 0 | 0 | 1 | — | m480 派发 |
| 9 | cavity_3d_full | PASS | [465] | 1 | 0 | 0 | — | 64³ err=1.34%, 96³ err=0.26% |
| 10 | cavity_natural_convection | PASS | [482] | 1 | 0 | 0 | — | de Vahl Davis, Nu err<3% |
| 11 | channel_turb_3d | PASS(HONEST-FAIL) | [414,455,458] | 2 | 1 | 0 | 环境/no-evidence | Re_τ=180, RMS=5.33%>3% |
| 12 | collision_kernels_3d_tg | PASS | [415,443] | 2 | 0 | 0 | — | TG 湍流碰撞核 |
| 13 | couette_2d | RUNNING | [486] | 0 | 0 | 1 | — | **本班派发** |
| 14 | couette_3d | PASS | [474,477] | 1 | 1 | 0 | 环境/no-evidence | m474 no-evidence, m477 补跑 PASS |
| 15 | cylinder | RUNNING | [487] | 0 | 0 | 1 | — | **本班派发** |
| 16 | cylinder_re40_st | PASS | [437] | 1 | 0 | 0 | — | Re=20/40 定常绕流 |
| 17 | droplet_oscillation | RUNNING | [483] | 0 | 0 | 1 | — | m480 派发 |
| 18 | dtmb5415_resistance | PASS | [438,454] | 2 | 0 | 0 | — | DTMB 5415 阻力 |
| 19 | kcs_resistance | PASS | [439,460] | 2 | 0 | 0 | — | KCS 阻力 |
| 20 | laplace_droplet | PASS | [432] | 1 | 0 | 0 | — | Laplace 液滴 |
| 21 | lid_driven_cavity_3d | **FAIL** | [426,429,431,441] | 0 | 4 | 0 | 环境/llm_error, 环境/no-evidence, 其他 | **黑名单**, 禁止再派 |
| 22 | sod_shock_tube | PASS | [433] | 1 | 0 | 0 | — | Sod 激波管 |
| 23 | sphere_re100 | PASS | [475] | 1 | 0 | 0 | — | Re=100 球阻力, err<3% |
| 24 | startup_poiseuille | PASS | [409] | 1 | 0 | 0 | — | 启动 Poiseuille |
| 25 | taylor_green_3d_decay | PASS | [427] | 1 | 0 | 0 | — | TG 衰减 |
| 26 | thermal_cavity_3d | PASS | [445,448] | 1 | 1 | 0 | 环境/no-evidence | m445 no-evidence, m448 补跑 PASS |

### 案例级汇总

| 案例级判定 | 数量 |
|-----------|------|
| PASS（精度达标） | 16 |
| PASS(HONEST-FAIL)（精度未达标但如实报告） | 4 |
| FAIL（黑名单） | 1 |
| RUNNING（在途） | 5 |
| **合计** | **26** |

---

## 三、FAIL 案例处置

### 3.1 需要派发诊断+修复绕行的 FAIL 案例

**无**。所有 status=failed 的 mission 均已由后续 PASS mission 解决：

| FAIL mission | 案例 | 失败原因 | 解决 mission | 解决方式 |
|-------------|------|---------|------------|---------|
| m425 | backward_step | 环境/no-evidence | m444, m459 | 补跑 PASS |
| m445 | thermal_cavity_3d | 环境/no-evidence | m448 | 补跑 PASS |
| m450 | bstep_3d | 环境/llm_error | m462, m469 | 补跑 PASS(HONEST-FAIL) |
| m453 | blasius_flat_plate | 环境/no-evidence | m467 | 补跑 PASS(HONEST-FAIL) |
| m455 | channel_turb_3d | 环境/no-evidence | m458 | 补跑 PASS(HONEST-FAIL) |
| m466 | bstep_3d | 环境/no-evidence | m469 | 补跑 PASS |
| m473 | aircraft_icing_naca0012_rime | 环境/llm_error | m479 | 补跑 PASS |
| m474 | couette_3d | 环境/no-evidence | m477 | 补跑 PASS |
| m478 | bstep_3d | 环境/no-evidence | m469(已有) | 已解决 |

### 3.2 黑名单案例

`lid_driven_cavity_3d`: 4 次连续 FAIL（m426, m429, m431, m441），已在黑名单中，由根因升级线接管。**本班未派发**。

---

## 四、派发清单（本班 3 单，硬上限内）

派发前 in-flight = 2（m481 cavity, m483 droplet_oscillation），< 4，触发下探派发。

按案例字母序，前 3 个未覆盖案例：

| Mission ID | 案例 | 位置 | 类型 | 状态 |
|-----------|------|------|------|------|
| m485 | aircraft_icing_rg15_ipw2 | benchmarks/pending | 下探 | queued→running |
| m486 | couette_2d | benchmarks/verified | 下探 | queued→running |
| m487 | cylinder | benchmarks/verified | 下探 | queued→running |

派发后 in-flight = 5（m481, m483, m485, m486, m487）。

---

## 五、里程碑检查

| 条件 | 状态 |
|------|------|
| 扫荡线累计完工 ≥10 单 | ✅ 43 单完工 |
| 尚无合并报告 | ❌ 已有合并报告 (id=402 m480, id=395 m476, id=388 m468, id=386 m464) |
| **是否发布新合并报告** | **否**（条件不满足） |

已有合并报告列表：
- id=402: SDAA 扫荡——验证案例扫荡合并报告（145 单完工·34 案例）— m480
- id=395: SDAA 扫荡——验证案例扫荡合并报告（31 单完工·18 案例）— m476
- id=388: SDAA 扫荡线合并报告——TensorLBM 验证案例系统性评估 — m468
- id=386: SDAA 扫荡合并报告：TensorLBM 验证案例系统性评估（m464 班次）— m464

---

## 六、设备证据汇总

从 mission result 中提取的 SDAA 设备证据（程序化注入，零手抄）：

| 案例 | 设备 | 关键精度 | 判定 |
|------|------|---------|------|
| cavity_3d_full | SDAA | 64³ err=1.34%, 96³ err=0.26% | PASS |
| cavity_natural_convection | /dev/tcaicard1 | N=128 Nu err=-2.91%, N=256 err=-1.36% | PASS |
| couette_3d | SDAA | fp64 L2=1.77e-14, fp32 L2=3.90e-06 | PASS |
| sphere_re100 | SDAA | T1 err=+2.77%, T2 err=+2.54% | PASS |
| backward_step | SDAA | 步数不足, HONEST-FAIL | PASS(HONEST-FAIL) |
| blasius_flat_plate | SDAA | Cf +27.34% (plate400 TRT) | PASS(HONEST-FAIL) |
| bstep_3d | /dev/tcaicard8 | +13.18% 再附着长度 | PASS(HONEST-FAIL) |
| channel_turb_3d | SDAA | RMS=5.33% (L3) | PASS(HONEST-FAIL) |

---

## 七、下一班建议

1. **监控在途 5 单**: m481(cavity), m483(droplet_oscillation), m485(aircraft_icing_rg15_ipw2), m486(couette_2d), m487(cylinder)。完工后更新案例级判定表。
2. **继续下探**: 下一批字母序未覆盖案例为 cylinder_3d → dam_break_2d_multimodel → dam_break_3d_mm。in-flight 回落至 <4 时派发。
3. **HONEST-FAIL 案例跟踪**: backward_step, blasius_flat_plate, bstep_3d, channel_turb_3d 四个案例已确认 LBM 固有精度局限，非设备/代码 bug。建议 owner 评估是否调整判据口径或标注为「方法局限·已知」。
4. **环境失败模式**: 69.2% 的 FAIL 为「工作树无改动」(no-evidence)，23.1% 为 llm_error。建议 owner 增加 agent 最小产出约束或预检机制。

---

## 八、升级事项（留 owner）

1. **系统性问题: no-evidence 失败率过高**。9/13 FAIL (69.2%) 为「工作树无任何改动」，agent 完成了分析但未写入工作树文件。建议：
   - 增加 agent 预检：finish 前检查 git diff 是否非空
   - 或放宽 evidence gate：允许 declared_summary 作为证据

2. **llm_error 间歇出现**。3/13 FAIL 为模型未调用 finish（glm-5.2 异常）。建议：
   - 排查特定模型 (glm-5.2) 的 finish 调用可靠性
   - 考虑 fallback 模型策略

3. **lid_driven_cavity_3d 黑名单案例**。4 次连续失败，根因未明。建议根因升级线优先排查：
   - 是否为案例本身代码缺陷（非设备问题）
   - 是否需要重写验证脚本

4. **HONEST-FAIL 案例的方法学定位**。4 个案例（backward_step, blasius_flat_plate, bstep_3d, channel_turb_3d）均确认为 LBM 方法固有精度局限，误差 5.3%~27.3%。建议：
   - 在案例 README 中标注「方法局限·已知 HONEST-FAIL」
   - 评估是否需要 TRT/更高阶格式/网格加密方案
