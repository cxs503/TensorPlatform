# 值班长班次 m332 分析报告

**班次**: m332  
**时间**: 2026-01-XX  
**职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工结果分析

### 1.1 数据源
- API: `GET /api/v1/tensor/missions?limit=50`
- 筛选条件: goal 含「SDAA」且含「扫荡」，排除「值班长」与「补报单」
- 完工 mission 数: 22 单（succeeded 16 + failed 5 + running 1）

### 1.2 案例状态总览（15 案例）

| 案例 | 最新 mission | 状态 | 判据结果 |
|------|-------------|------|---------|
| laplace_cac_phasefield | m293 | succeeded | HONEST_FAIL（网格阶梯不足） |
| maneuvering_steady_turn | m314 | succeeded | PASS |
| permeability | m315 | succeeded | PASS |
| permeability3d | m327 | succeeded | PASS |
| poiseuille_3d_duct | m295 | succeeded | PASS |
| poiseuille_3d_pipe | m321 | succeeded | PASS |
| sphere_re100 | m329 | succeeded | PASS |
| sphere_re100_mem | m290 | succeeded | PASS |
| stokes_second_problem | m324 | succeeded | PASS |
| suboff_re1000 | m330 | running | 进行中 |
| suboff_sail_resistance | m289 | succeeded | HONEST_FAIL（内存受限） |
| taylor_green_3d | m312 | succeeded | PASS |
| thermal_cavity | m325 | succeeded | PASS |
| thermal_cavity_3d | m294 | succeeded | HONEST_FAIL（步数不足未达稳态） |
| womersley | m331 | succeeded | HONEST_FAIL（粗网格离散化误差） |

### 1.3 统计汇总

- **PASS**: 10 案例 (66.7%)
- **HONEST_FAIL**: 4 案例 (26.7%)
- **RUNNING**: 1 案例 (6.7%)

### 1.4 失败原因归类

| 失败类别 | 数量 | 案例 | 根因 |
|---------|------|------|------|
| 网格/分辨率不足 | 2 | laplace_cac_phasefield, womersley | 网格阶梯不够或粗网格离散化误差 |
| 步数不足 | 1 | thermal_cavity_3d | 未达稳态，需长瞬态期 |
| 资源受限 | 1 | suboff_sail_resistance | 内存受限，无法运行完整案例 |

**注**: 所有 HONEST_FAIL 均为判据真红（物理/资源限制），非算法 bug。诊断方向明确，需增加资源或调整配置。

---

## 二、本班次派发清单

### 2.1 派发依据
- 在飞 mission 数: 2 (m332 本班次 + m330 suboff_re1000)
- 阈值: in-flight < 4，需补充队列
- 策略: 针对 HONEST_FAIL 案例派发第 2 次诊断+修复绕行

### 2.2 派发 mission

| Mission ID | 案例 | Goal 摘要 | 预算 |
|-----------|------|----------|------|
| m333 | laplace_cac_phasefield | 第2次诊断：增加网格层级（至少3层），输出收敛阶梯 | 80 tool calls / 7200s |
| m334 | thermal_cavity_3d | 第2次诊断：增加计算步数至推荐值，监控稳态指标 | 80 tool calls / 7200s |
| m335 | womersley | 第3次诊断：尝试更细网格（H≥58），分析 Stokes 边界层分辨率 | 80 tool calls / 7200s |

**派发类型**: 全部为 TensorLBM 扫荡类 goal（诊断+修复绕行）  
**硬上限遵守**: 3 单 ≤ 3 单上限 ✓

---

## 三、下一班建议

### 3.1 待跟进事项
1. **m330 (suboff_re1000)**: 等待完工，检查是否 PASS 或 HONEST_FAIL
2. **m333/m334/m335**: 等待完工，检查诊断结果
3. **suboff_sail_resistance**: 内存受限问题，需评估是否调整案例规模或申请更多资源

### 3.2 升级事项（留给 owner）
1. **系统性问题**: 4 个 HONEST_FAIL 案例均与资源/配置相关，非算法 bug
   - 建议: 建立案例资源需求基线，派发前预检资源充足性
2. **合并报告**: m328 已发布合并报告（ID 245），当前状态无重大变化，暂不需新合并报告
3. **判据口径**: 所有 HONEST_FAIL 均如实报告，未改口径 ✓

### 3.3 下一班职责
1. 读取 m330/m333/m334/m335 完工结果
2. 若新增 PASS，更新案例状态表
3. 若新增 HONEST_FAIL，评估是否需要第 3 次诊断或升级
4. 若在飞 < 4，按字母序派发新案例（需先确认 TensorLBM benchmarks/verified 下是否有未扫荡案例）

---

## 四、纪律遵守声明

- ✓ 只分析不改业务代码
- ✓ 数字全部来自 API 返回，零手抄
- ✓ 派发硬上限: 3 单，只准派 TensorLBM 扫荡类 goal
- ✓ 未改审批、未碰 GitHub
- ✓ 判据红未改口径
- ✓ 系统性问题写入「升级事项」节

---

**班次完成**: m332  
**派发 mission**: m333, m334, m335  
**下一班入口**: 读取 m330/m333/m334/m335 完工结果
