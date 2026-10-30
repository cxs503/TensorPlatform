# 值班长班次 #322 分析与调度报告

**班次号**: 322  
**日期**: 2026-10-30  
**值班员**: eco_admin (ADMIN)  
**Mission ID**: 322

---

## 1. 平台登录与数据拉取

- **登录**: POST /api/v1/auth/login (eco_admin) → access_token 获取 ✅
- **拉取 missions**: GET /api/v1/tensor/missions?limit=200 → 返回 200 单（总 322 单）
- **拉取 reports**: GET /api/v1/tensor/reports?limit=20 → 返回 20 篇报告

---

## 2. SDAA 扫荡线分析

### 2.1 总体统计

| 指标 | 数值 |
|------|------|
| SDAA 扫荡总尝试次数 | 70 |
| 独立案例数 | 35 |
| PASS 案例 | 30 (85.7%) |
| FAIL 案例 | 4 (11.4%) |
| RUNNING 案例 | 1 (2.9%) |

### 2.2 PASS 案例清单（30 例）

| 案例 | 最新 Mission ID | 尝试次数 | 判定 |
|------|----------------|---------|------|
| acoustics | 213 | 1 | PASS |
| aircraft_icing_naca0012_rime | 173 | 1 | PASS |
| cavity | 237 | 3 | PASS |
| cavity_3d_full | 216 | 1 | PASS |
| cavity_natural_convection | 208 | 5 | PASS |
| couette_2d | 220 | 1 | PASS |
| couette_3d | 268 | 1 | PASS |
| cylinder | 269 | 1 | PASS |
| cylinder_3d | 200 | 3 | PASS |
| droplet_oscillation | 238 | 1 | PASS |
| floating_body_free_decay | 239 | 1 | PASS |
| kovasznay_2d | 217 | 1 | PASS |
| laplace_cac_phasefield | 293 | 2 | PASS |
| laplace_droplet | 183 | 3 | PASS |
| laplace_pr_eos | 270 | 1 | PASS |
| maneuvering_steady_turn | 314 | 1 | PASS |
| permeability | 315 | 1 | PASS |
| poiseuille_2d | 184 | 1 | PASS |
| poiseuille_3d_annulus | 224 | 1 | PASS |
| poiseuille_3d_duct | 295 | 1 | PASS |
| poiseuille_3d_pipe | 321 | 2 | PASS |
| poiseuille_two_phase_cg | 252 | 1 | PASS |
| sphere_re100_d3q27 | 232 | 1 | PASS |
| sphere_re100_mem | 290 | 4 | PASS |
| sphere_re200 | 223 | 1 | PASS |
| stokes_sphere_dg | 234 | 1 | PASS |
| suboff_sail_resistance | 289 | 2 | PASS |
| taylor_green_3d | 312 | 1 | PASS |
| thermal_cavity_3d | 294 | 1 | PASS |
| womersley | 291 | 6 | PASS |

### 2.3 FAIL 案例清单（4 例）

| 案例 | 最新 Mission ID | 尝试次数 | 失败原因归类 |
|------|----------------|---------|-------------|
| permeability3d | 316 | 1 | 工作树无改动（agent 基础设施） |
| sphere_re100 | 320 | 8 | 工作树无改动（agent 基础设施） |
| stokes_second_problem | 233 | 1 | 工作树无改动（agent 基础设施） |
| thermal_cavity | 250 | 2 | agent 未调用 finish（基础设施） |

### 2.4 RUNNING 案例（1 例）

| 案例 | Mission ID | 尝试次数 | 备注 |
|------|-----------|---------|------|
| suboff_re1000 | 319 | 7 | SUBOFF 艇体 Re=1000，第 7 轮诊断+修复绕行 |

### 2.5 失败原因分类

| 失败原因 | 次数 | 占比 |
|----------|------|------|
| agent 未调用 finish | 20 | 71.4% |
| 工作树无改动 | 8 | 28.6% |
| honest-FAIL 判据红 | 0 | 0% |
| triton 绕行问题 | 0 | 0% |
| 环境/OOM | 0 | 0% |

**关键发现**：28 次失败中 100% 为 agent 基础设施问题，零例物理/算法判据失败。

---

## 3. 本班次派发清单

### 3.1 派发决策

当前 in-flight SDAA 扫荡任务仅 1 单（m319 suboff_re1000, running），低于阈值 4，按案例字母序派发下探案例。

### 3.2 派发结果

| Mission ID | 案例 | 类型 | 状态 | 派发依据 |
|-----------|------|------|------|---------|
| 323 | permeability3d | 诊断+修复绕行 | queued | 仅 1 次尝试，agent 基础设施失败 |
| 324 | stokes_second_problem | 诊断+修复绕行 | queued | 仅 1 次尝试，agent 基础设施失败 |
| 325 | thermal_cavity | 诊断+修复绕行 | queued | 2 次尝试均为 agent 未调用 finish |

**硬上限检查**：本班次派发 3 单（≤3 ✅），全部为 TensorLBM 扫荡类 goal ✅。

---

## 4. 合并报告发布

- **Report ID**: 238
- **标题**: 【SDAA 扫荡】值班长 m322 班次论文级进展报告 — 35 案例扫荡 / 70 单完工 / 30 PASS / 4 FAIL / 1 RUNNING
- **状态**: published
- **POST 时间**: 2026-10-30T03:24:30

---

## 5. 升级事项（留给 owner）

### 5.1 agent 基础设施问题（高优先级）

- **现象**：28 次 FAIL 中 20 次（71.4%）为 agent 未调用 finish，8 次（28.6%）为工作树无改动
- **影响**：大量案例被误判为 FAIL，实际物理/算法可能正确
- **建议**：
  1. 在 agent 框架中增加自动 finish 调用机制（超时/异常时自动触发）
  2. 增加工作树改动检测与告警
  3. 对长跑任务（如 thermal_cavity）强制中间落盘

### 5.2 sphere_re100 反复失败（中优先级）

- **现象**：8 次尝试均未成功，最新 m320 为"工作树无改动"
- **建议**：owner 介入排查是否为案例本身的环境依赖问题（如 triton 版本、GPU 驱动等）

### 5.3 suboff_re1000 长跑（中优先级）

- **现象**：7 次尝试仍在运行中
- **建议**：为该案例设置中间检查点，避免单次运行超时导致全量重算

---

## 6. 下一班建议

1. **跟踪 m323/m324/m325 执行结果**：3 单诊断+修复绕行已派发，需关注是否成功
2. **关注 m319 (suboff_re1000)**：第 7 轮运行中，若再次失败需 owner 介入
3. **考虑 sphere_re100 专项排查**：8 次失败已超出正常重试范围，建议单独派专项诊断单
4. **持续监控 agent 基础设施**：若 FAIL 率仍高，建议暂停新案例扫荡，优先修复基础设施

---

## 7. 数据溯源

所有数字均来自 API 返回，零手抄：
- Missions 数据：GET /api/v1/tensor/missions?limit=200
- Reports 数据：GET /api/v1/tensor/reports?limit=20
- 分析脚本：python3 程序化解析 JSON，提取 case name/status/headline
