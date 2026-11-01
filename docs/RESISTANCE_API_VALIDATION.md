# 船舶阻力计算 API 验证报告：Holtrop/ITTC-57 对标实验基准

## ① 案例介绍（物理背景/几何/参数表）

本报告验证 TensorPlatform 船舶阻力计算 API（`POST /api/v1/calculations/resistance`）对三类经典船型的计算精度：

| 船型 | 来源 | L (m) | B (m) | T (m) | Cb | 速度范围 (m/s) | Fn 范围 |
|------|------|-------|-------|-------|------|----------------|---------|
| SUBOFF | Roddy 1990 (DTRC) | 4.356 | 0.508 | 0.508 | 0.542 | 3–6 | 0.459–0.918 |
| DTMB-5415 | ITTC V&V 2010 | 5.720 | 0.759 | 0.246 | 0.508 | 2.57–15.43 | 0.061–0.366 |
| KVLCC2 | SIMMAN 2008 | 320.0 | 58.0 | 20.8 | 0.810 | 7.20–8.23 | 0.129–0.147 |

**计算方法**：ITTC-1957 摩擦阻力线 + Holtrop 简化剩余阻力模型。
湿表面积近似：S = π·B·L·(0.5 + 0.25·Cb)（适用于回转体）。

## ② 怎么做的（方法/流程/关键设置/工具链）

1. **修复 API 响应字段**：原 `calculations.py` 的 `ResistanceResponse` 缺少 `ct`、`cf`、`cr`、`method` 字段，导致 `ship_benchmark.py` 基准验证端点无法获取计算阻力系数（`calc_data.get("ct", 0.0)` 始终返回 0）。本次修复添加了这四个字段。

2. **自包含测试**：编写 `backend/tests/test_calculations.py`（25 项测试），不依赖完整后端栈，验证：
   - 响应字段完整性（ct/cf/cr/method）
   - ITTC-1957 摩擦系数公式正确性
   - Ct = Cf + Cr 恒等式
   - 几何参数合理性
   - Fn/Re 无量纲数正确性
   - 边界条件（Cb 超范围、负尺寸、低 Fn 时 Cr=0）
   - 基准验证兼容性

3. **批量验证**：运行 `tools/validate_resistance_api.py` 对 12 个基准算例逐一计算，对比实验 Ct 值，生成误差图表。

## ③ 计算结果

| 算例 | Ct_calc | Ct_ref | 误差(%) | 判定 |
|------|---------|--------|---------|------|
| SUBOFF-01 | 0.003462 | 0.00380 | 8.89 | ✅ PASS |
| SUBOFF-02 | 0.003953 | 0.00400 | 1.18 | ✅ PASS |
| SUBOFF-03 | 0.004766 | 0.00420 | 13.48 | ✅ PASS |
| SUBOFF-04 | 0.005921 | 0.00440 | 34.57 | ❌ FAIL |
| DTMB5415-01 | 0.003089 | 0.00450 | 31.36 | ❌ FAIL |
| DTMB5415-02 | 0.004085 | 0.00380 | 7.50 | ✅ PASS |
| DTMB5415-03 | 0.011169 | 0.00350 | 219.11 | ❌ FAIL |
| DTMB5415-04 | 0.026688 | 0.00380 | 602.32 | ❌ FAIL |
| KVLCC2-01 | 0.001415 | 0.00280 | 49.46 | ❌ FAIL |
| KVLCC2-02 | 0.001410 | 0.00300 | 53.00 | ❌ FAIL |
| KVLCC2-03 | 0.001408 | 0.00310 | 54.58 | ❌ FAIL |
| KVLCC2-04 | 0.001406 | 0.00320 | 56.06 | ❌ FAIL |

**汇总**：12 算例中 4 例通过（33.3%），8 例未通过。

## ④ 结果图

### 图1：Ct vs Fn 比较
![Ct Comparison](outputs/resistance_validation/ct_comparison.png)

### 图2：Rt vs Speed 比较
![Rt Comparison](outputs/resistance_validation/rt_comparison.png)

### 图3：误差柱状图（15% 阈值）
![Error Bar](outputs/resistance_validation/error_bar.png)

## ⑤ 注意点（踩过的坑/局限/适用范围）

### 已修复的 Bug
- **API 响应缺失 Ct 字段**：`ResistanceResponse` 原无 `ct`/`cf`/`cr`/`method` 字段，导致 `ship_benchmark.py` 的 `calc_data.get("ct", 0.0)` 始终返回 0，基准验证端点无法工作。修复后返回完整阻力系数。

### 方法局限
1. **湿表面积公式**：S = π·B·L·(0.5 + 0.25·Cb) 适用于回转体（SUBOFF），对 DTMB-5415（瘦削军舰）和 KVLCC2（肥大油轮）不适用，导致后两者的 Ct 系统偏差。
2. **剩余阻力模型**：Cr = 0.002·(1+2Cb)·Fn^2.5 为 SUBOFF 标定公式，对高 Fn（>0.3）船型严重高估兴波阻力。
3. **Holtrop 适用范围**：原始 Holtrop-Mennen 方法针对 Cb > 0.55 的商船标定，对 SUBOFF（Cb=0.542, Fn>0.4）和 DTMB-5415（Cb=0.508）属于外推。

### HONEST-FAIL 声明
- SUBOFF-04（Fn=0.918）：误差 34.57%，超出 15% 阈值。Holtrop 方法在超高 Fn 下不适用。
- DTMB-5415-03/04（Fn>0.24）：误差 219–602%，剩余阻力模型完全失效。
- KVLCC2 全部：误差 49–56%，湿表面积公式不适用于肥大船型。

## ⑥ 误差判定

| 船型 | 通过数/总数 | 最大误差 | 是否 <3% |
|------|------------|---------|---------|
| SUBOFF | 3/4 | 34.57% | ❌ 否（仅 SUBOFF-02 为 1.18% <3%） |
| DTMB-5415 | 1/4 | 602.32% | ❌ 否 |
| KVLCC2 | 0/4 | 56.06% | ❌ 否 |

**结论**：当前简化 Holtrop/ITTC-57 模型仅在 SUBOFF 中低 Fn 范围（Fn < 0.8）内达到 <15% 精度。对 DTMB-5415 高 Fn 和 KVLCC2 全范围均不满足精度要求。建议：
- 对 KVLCC2 等肥大船型：采用完整 Holtrop-Mennen 方法（含船型修正项）
- 对 SUBOFF 高 Fn：采用 slender-body theory 或 CFD (RANS)
- 对 DTMB-5415 高 Fn：需兴波阻力专用模型

---

**产物指针**：
- 代码修复：`backend/api/calculations.py`（添加 ct/cf/cr/method 字段）
- 测试：`backend/tests/test_calculations.py`（25/25 通过）
- 验证工具：`tools/validate_resistance_api.py`
- 输出数据：`outputs/resistance_validation/`（validation_results.json, report_data.json, 3 张 PNG）
