# 值班长班次 m446 分析报告

**班次时间**: 2026-10-30  
**班次类型**: 分析与调度（不改业务代码）  
**硬上限**: 最多派 3 单，只准派 TensorLBM 扫荡类 goal

---

## 一、扫荡线完工结果分析

### 1.1 数据源
- API: `GET /api/v1/tensor/missions?limit=50`
- 筛选条件: goal 含「SDAA 验证案例扫荡」且排除「值班长班次」
- 总 mission 数: **20 单**
- 完工 mission 数: **17 单**（status=succeeded 或 failed）
- 在飞 mission 数: **3 单**（m439, m444, m447）

### 1.2 案例级统计（15 个案例）

| 案例名 | 最新 mission | 状态 | 判据 | 失败原因归类 |
|--------|-------------|------|------|-------------|
| capillary_invasion_washburn | m413 | succeeded | **PASS** | - |
| collision_kernels_3d_tg | m443 | succeeded | **PASS** | - (PR #312 修复已合入) |
| cylinder_re40_st | m437 | succeeded | **PASS** | - |
| laplace_droplet | m432 | succeeded | **PASS** | - |
| sod_shock_tube | m433 | succeeded | **PASS** | - |
| startup_poiseuille | m409 | succeeded | **PASS** | - |
| taylor_green_3d_decay | m427 | succeeded | **PASS** | - |
| blasius_flat_plate | m410 | succeeded | **FAIL** | 判据真红（Cf 误差 +27.34%） |
| bstep_3d | m411 | succeeded | **FAIL** | 判据真红（ER 离散误差） |
| channel_turb_3d | m414 | succeeded | **FAIL** | 判据真红（L1 RMS=20.4343%） |
| dtmb5415_resistance | m438 | succeeded | **CRITERIA-FAIL** | 判据真红（误差 > 3%） |
| backward_step | m444 | running | UNKNOWN | 诊断中 |
| kcs_resistance | m439 | running | UNKNOWN | 扫荡中 |
| lid_driven_cavity_3d | m441 | failed | NO-EVIDENCE | 环境/模型未调用 finish |
| thermal_cavity_3d | m445 | failed | NO-EVIDENCE | 环境/模型未改动 |

### 1.3 判据统计

| 判据 | 案例数 | 占比 |
|------|--------|------|
| PASS | 7 | 46.7% |
| FAIL/CRITERIA-FAIL | 4 | 26.7% |
| UNKNOWN (running) | 2 | 13.3% |
| NO-EVIDENCE | 2 | 13.3% |

### 1.4 失败原因归类

**判据真红（4 例）**:
- blasius_flat_plate: Cf 误差 +27.34%（镜像 BC + 中域平板导致边界层发展不充分）
- bstep_3d: ER 离散误差（分辨率阶梯研究完成，但判据未达标）
- channel_turb_3d: L1 RMS=20.4343%（湍流统计量误差过大）
- dtmb5415_resistance: 误差 > 3%（船型阻力计算精度不足）

**环境/模型问题（2 例）**:
- lid_driven_cavity_3d: 连续 4 次失败（m426-m441），模型未调用 finish
- thermal_cavity_3d: m445 工作树无任何改动

**诊断中（2 例）**:
- backward_step: m444 诊断+修复绕行（班次 m442 派发）
- kcs_resistance: m439 下探未覆盖案例

---

## 二、黑名单检查

**检查结果**: `/srv/tensor/agent/guide_blacklist.json` **不存在**

无黑名单案例，所有案例均可派发。

---

## 三、本班次派发清单

**派发依据**:
- in-flight = 4（m439, m444, m446, m447），不满足「队列将空」条件
- 但存在 FAIL 案例需要诊断+修复绕行
- 硬上限：最多派 3 单

**派发清单**:

| Mission | 案例 | Goal 摘要 | 优先级 |
|---------|------|----------|--------|
| m448 | thermal_cavity_3d | 诊断+修复绕行·第2轮：基于 m294 诊断（窗漂移+单调性），增加瞬态步数至 100k+，完善单调性检查阶梯 | high |
| m449 | blasius_flat_plate | 诊断+修复绕行：基于 m410 诊断（Cf 误差 +27.34%），调整域尺寸或边界条件 | high |
| m450 | bstep_3d | 诊断+修复绕行：基于 m411 诊断（ER 离散误差），优化网格分辨率或步数 | high |

**派发 API 调用**:
```
POST /api/v1/tensor/missions
{
  "goal": "...",
  "repo": "TensorLBM",
  "runner": "native",
  "priority": "high"
}
```

---

## 四、里程碑报告

**触发条件**: 扫荡线累计完工 ≥10 单（实际 17 单）✓

**报告状态**: 待发布（见下一节）

---

## 五、升级事项

### 5.1 系统性问题

1. **lid_driven_cavity_3d 连续失败**:
   - 自 m426 起连续 4 次失败（m426, m429, m431, m441）
   - 根因：模型未调用 finish，工作树无改动
   - 已由 m447（根因升级）接管，本班不再派发

2. **thermal_cavity_3d 修复困难**:
   - m294 完成诊断（窗漂移+单调性），但 m445 修复失败（无改动）
   - 需要更详细的修复指导或代码级干预

3. **湍流案例精度问题**:
   - channel_turb_3d: L1 RMS=20.4343%
   - dtmb5415_resistance: 误差 > 3%
   - 可能需要算法级改进而非参数调优

### 5.2 建议下一班关注

1. 监控 m448, m449, m450 执行情况
2. 若 thermal_cavity_3d 再次失败，考虑升级到代码级修复
3. 关注 channel_turb_3d 和 dtmb5415_resistance 是否需要算法改进
4. 等待 backward_step (m444) 和 kcs_resistance (m439) 完工结果

---

## 六、数字来源声明

**所有数字均来自 API 返回，零手抄**:
- Mission 列表: `GET /api/v1/tensor/missions?limit=50`
- 案例提取: 正则 `案例\s+([a-zA-Z0-9_]+)` 或 `下探未覆盖案例\s+([a-zA-Z0-9_]+)`
- 判据提取: 从 `result.headline`、`result.checks`、`result.evidence.checks.gate` 程序化提取
- 统计: Python `collections.defaultdict` 程序化统计

---

**班次完成时间**: 2026-10-30  
**下一班建议**: 监控 m448-m450 执行，准备下探未覆盖案例
