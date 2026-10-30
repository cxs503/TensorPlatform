# 值班长班次 m452 — SDAA 扫荡线分析与调度报告

**班次号**: m452  
**日期**: 2026-10-30  
**操作员**: eco_admin (TensorPlatform Agent)  
**类型**: 分析与调度（不改业务代码）

---

## 一、数据源与纪律声明

- 登录：POST /api/v1/auth/login (eco_admin) → JWT token ✅
- Mission 拉取：GET /api/v1/tensor/missions?limit=50 → 50 单（total=452）✅
- 黑名单检查：`/srv/tensor/agent/guide_blacklist.json` → `lid_driven_cavity_3d`（4 连败，自 m426 起）✅
- **所有数字来自 API 返回，零手抄**
- **黑名单案例 lid_driven_cavity_3d 本班禁止派发重试单**

---

## 二、SDAA 扫荡线完工分析

### 2.1 总体统计（程序化分析 43 单 SDAA 相关 mission）

| 指标 | 数值 |
|------|------|
| 唯一案例数 | 17 |
| PASS 案例数 | 12 |
| FAIL 案例数 | 12（含 honest-FAIL） |
| 进行中 | 1 (bstep_3d, M450) |
| 总完工单 | 24 |

### 2.2 逐案例判据表

| 案例 | PASS | FAIL | 状态 | 失败原因归类 |
|------|------|------|------|-------------|
| backward_step | 1 | 1 | ✅ 已修复 (M444) | 任务失败→已修复 |
| blasius_flat_plate | 0 | 2 | ❌ 待诊断 | 判据真红 (Cf 误差 +27%~+35%) |
| bstep_3d | 1 | 0 | 🔄 进行中 (M450) | — |
| capillary_invasion_washburn | 1 | 0 | ✅ | — |
| channel_turb_3d | 0 | 1 | ❌ 待诊断 | 其他 (L1 RMS=20.4%) |
| collision_kernels_3d_tg | 1 | 1 | ✅ 已修复 (M443) | 其他→已修复 |
| cylinder_re40_st | 1 | 0 | ✅ | — |
| dtmb5415_resistance | 0 | 1 | ❌ 待诊断 | 判据真红 (error > 3%) |
| kcs_resistance | 1 | 0 | ✅ | — |
| laplace_droplet | 1 | 0 | ✅ | — |
| lid_driven_cavity_3d | 0 | 4 | ⛔ **黑名单** | 任务失败×4（根因升级线接管） |
| propeller | 0 | 1 | ❌ 待诊断 | 其他 |
| sod_shock_tube | 1 | 0 | ✅ | — |
| sphere_re100 | 1 | 0 | ✅ | — |
| startup_poiseuille | 1 | 0 | ✅ | — |
| taylor_green_3d_decay | 1 | 0 | ✅ | — |
| thermal_cavity_3d | 1 | 1 | ✅ 已修复 (M448) | 任务失败→已修复 |

### 2.3 失败原因归类统计

| 失败类型 | 计数 | 说明 |
|----------|------|------|
| 判据真红 | 3 | blasius_flat_plate×2, dtmb5415_resistance×1 |
| 任务失败 | 6 | lid_driven_cavity_3d×4, backward_step×1, thermal_cavity_3d×1 |
| 其他 | 3 | channel_turb_3d×1, collision_kernels_3d_tg×1, propeller×1 |
| 环境/OOM | 0 | — |
| triton 绕行 | 0 | — |

---

## 三、本班派发清单（硬上限：3 单）

| 派发单号 | 案例 | 类型 | 派发依据 |
|----------|------|------|----------|
| M453 | blasius_flat_plate | 诊断+修复绕行 | 2 连 FAIL（判据真红），Cf 误差 +27%~+35%，边缘加速非根因 |
| M454 | dtmb5415_resistance | 诊断+修复绕行 | 1 FAIL（判据真红），Criteria validation error > 3% |
| M455 | channel_turb_3d | 诊断+修复绕行 | 1 FAIL（其他），L1 RMS=20.4% 远超 3% 阈值 |

**派发约束检查**：
- ✅ 全部为 TensorLBM 扫荡类 goal
- ✅ 未派黑名单案例 lid_driven_cavity_3d
- ✅ 未超 3 单上限
- ✅ runner=native, repo=TensorLBM

---

## 四、合并报告状态

- 上一份合并报告：Report #368（m446 班次，15 案例 / 17 完工单）
- 最新综合报告：Report #372（m451 班次，304 单 / 55 案例 / 36 PASS / 4 HONEST_FAIL）
- 本班新增完工：0 单（M452 为分析班次，无新完工）
- **结论**：累计新增完工 < 10 单，暂不发布新合并报告

---

## 五、升级事项（留给 owner）

1. **blasius_flat_plate 系统性精度问题**：2 轮诊断均 FAIL，Cf 误差 +27%~+35%。已排除边缘加速因素，需深入检查：
   - LBM 碰撞模型对边界层分辨率的适用性
   - 网格收敛性（当前 220×150/220×400 两档均不达标）
   - 建议 owner 评估是否需更换碰撞模型（BGK→MRT）或引入壁面函数

2. **lid_driven_cavity_3d 根因升级线**：4 连败已移交根因升级线（M447），本班遵守黑名单不重试。建议 owner 跟踪 M447 进展。

3. **channel_turb_3d 湍流精度**：L1 RMS=20.4% 表明当前设置远未收敛，需检查网格分辨率、壁面模型、统计平均时间。

4. **dtmb5415_resistance 船型阻力**：作为船舶总体性能核心案例，阻力误差 >3% 需优先关注。建议检查几何生成、Re 数设置、自由面处理。

---

## 六、下一班建议

1. **优先跟踪**：M450 (bstep_3d 诊断) 结果，决定是否需进一步修复
2. **待诊断队列**：propeller（1 FAIL，其他）尚未派发，建议下一班优先处理
3. **下探案例**：若诊断单减少，可按字母序下探未覆盖 pending 案例
4. **合并报告**：若累计新增完工 ≥10 单，发布新合并报告

---

## 七、产物清单

| 路径 | 说明 |
|------|------|
| `docs/operator_shift_m452.md` | 本班分析文档 |
| `artifacts/shift_m452_analysis.json` | 程序化分析原始数据 |

---

*文档生成时间：2026-10-30 | 数据来源：平台 API (GET /tensor/missions?limit=50) | 分析脚本：Python3 程序化解析*
