# 基准参考值溯源审计与验证门失效修复（m1469 · TensorPlatform）

## 1. 案例介绍
平台阻力链路的唯一外部判据是 `backend/data/benchmark_reference.json`（12 算例：
DARPA SUBOFF ×4 模型、DTMB-5415 ×4 模型、KVLCC2 ×4 实船），被
`api/domain/ship_benchmark.py::benchmark_validate` 与 `services/ship_proxy.py` 直接当实验参考值。

| 船型 | L [m] | B [m] | T [m] | Cb | 尺度 | 算例 |
|---|---|---|---|---|---|---|
| SUBOFF | 4.356 | 0.508 (D) | 0.508 (D) | 数据集缺 | 模型（淡水 ρ=998.5） | 4 |
| DTMB-5415 | 5.720 | 0.759 | 0.246 | 0.508 | 模型 | 4 |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 0.8098 | 实船（海水 ρ=1025） | 4 |

**发现的缺陷（本单核心）**：验证门 `benchmark_validate` 把上游基址写死为
`http://localhost:8002`，该端口无服务监听（实测 connect refused）→ 每个算例都走
"Resistance API call failed" 分支并被赋 `error_pct=999.0`，即**验证门从未真正调用过求解器**，
却输出一个看起来像精度的误差数字。配套 374 行测试全部用 mock 替身喂入合成 Ct，
因此对死端口与缺字段零敏感（失效测试）。

## 2. 怎么做的
1. 新增 `backend/services/benchmark_ref_audit.py`（纯标准库）：把"参考值能不能当判据"变成三条
   可机读恒等式 —— I1 `Fn=V/√(gL)` (±2%)、I2 `Ct=Rt/(½ρV²S)` (±15%)、I3 `Δ=ρCbLBT` (±5%)。
   缺字段一律 `WITHHELD`（不猜）；任一 FAIL ⇒ 该算例 `INCONSISTENT`；全数据集无判废才
   `baseline_usable=True`。S 缺失时用 Denny–Mumford 回归 `S=1.7LT+∇/T` 估计并标 `estimated_input`。
2. 修复 `benchmark_validate`：上游基址取自 `SHIP_PLATFORM_URL`，默认改回已部署的 `:8001`；
   上游不可达 → `status=UPSTREAM_UNAVAILABLE` 且 `error_pct=None`（**废除伪 999%**）；
   上游只给 Rt 时按 I2 派生 Ct 并标 `ct_source=derived_from_rt`；参考值判废的算例
   `judgement=WITHHELD_REFS`，summary 带 `baseline_usable` 与 `baseline_note`。
3. 几何先验：`geometry_payload()` 用 `Δ=ρCbLBT` 复核预设排水量，偏差 >5% 即改用几何派生值并记 warning。
4. 新增 `GET /calculations/benchmark_audit` 暴露机读审计结论。

## 3. 计算结果（程序化注入）

**审计结论：`DATASET_REJECTED_AS_EXTERNAL_BASELINE`** —— 一致 0/12，判废 12/12，
`baseline_usable=False`。

| case | I1 Fn | I2 Ct↔Rt | Ct(声明)/Ct(Rt) | I3 Δ | 结论 |
|---|---|---|---|---|---|
| SUBOFF-01 | 0.0% (PASS) | 640.3% (FAIL) | 7.40× | WITHHELD | INCONSISTENT |
| SUBOFF-02 | 0.0% (PASS) | 724.7% (FAIL) | 8.25× | WITHHELD | INCONSISTENT |
| SUBOFF-03 | 0.0% (PASS) | 802.0% (FAIL) | 9.02× | WITHHELD | INCONSISTENT |
| SUBOFF-04 | 0.0% (PASS) | 852.5% (FAIL) | 9.52× | WITHHELD | INCONSISTENT |
| DTMB5415-01 | 82.2% (FAIL) | 733.3% (FAIL) | 8.33× | 13.9% (FAIL) | INCONSISTENT |
| DTMB5415-02 | 82.2% (FAIL) | 709.8% (FAIL) | 8.10× | 13.9% (FAIL) | INCONSISTENT |
| DTMB5415-03 | 82.2% (FAIL) | 795.2% (FAIL) | 8.95× | 13.9% (FAIL) | INCONSISTENT |
| DTMB5415-04 | 82.2% (FAIL) | 889.3% (FAIL) | 9.89× | 13.9% (FAIL) | INCONSISTENT |
| KVLCC2-01 | 0.3% (PASS) | 256.5% (FAIL) | 3.57× | 4.2% (PASS) | INCONSISTENT |
| KVLCC2-02 | 0.2% (PASS) | 271.1% (FAIL) | 3.71× | 4.2% (PASS) | INCONSISTENT |
| KVLCC2-03 | 0.5% (PASS) | 280.2% (FAIL) | 3.80× | 4.2% (PASS) | INCONSISTENT |
| KVLCC2-04 | 0.0% (PASS) | 285.2% (FAIL) | 3.85× | 4.2% (PASS) | INCONSISTENT |

- 最大 Ct 自相矛盾：`DTMB5415-04` 声明 Ct 与由同算例 Rt 反推的 Ct 相差 **9.89 倍**；
- DTMB-5415 四个算例声明 Fn 与定义式相差约 5.6 倍（末例 Fn 定义值 2.06，物理不可能）；
- SUBOFF 无 Cb/排水量字段 → I3 `WITHHELD`，只能由 I2 判废。

## 4. 结果图
图1：12 算例 × 3 恒等式的相对偏差（对数轴，黑虚线=15% 精度红线，灰柱=字段缺失 WITHHELD）。
图2：同算例内"声明 Ct"与"由声明 Rt 反推 Ct"两条曲线分离 3.6–9.5 倍——分离说明基准自相矛盾，
而非求解器不准。

![图1](fig_benchmark_identity.png)

![图2](fig_benchmark_ct_vs_rt.png)

## 5. 注意点 / 局限
- 本单只修"验证门"与"参考值溯源"，不改任何文献值：数据集里矛盾的数值原样保留并加审计标注，
  因为修复需外部实验报告原文（Roddy 1990 / ITTC V&V / SIMMAN 2008），非本环境可得。
- Denny–Mumford 的 S 估计本身有 ±5–10% 不确定度，故 I2 用 15% 容差而非 3%；对 SUBOFF 这类
  有列值 S 的算例 I2 是硬判据。
- `services/ship_proxy.py::BENCHMARK_REFERENCE` 另有内联参考值（SUBOFF 容差 300%），本单只加
  了"容差 >15% 不得视为合格"的纪律函数 `tolerance_is_acceptable()`，未改其数值（另一条欠账）。
- 上游实测 `ct` 与 `rt` 量级可疑（KVLCC2 V=7.974 m/s 时返回 rt≈1368 N，且返回 Fr=0.073 与定义值
  0.142 不符），属船体平台侧算法缺陷，已被本单的契约测试捕获记录，不在本仓修复范围。

## 6. 误差判定
| 判据 | 基准 | 结果 |
|---|---|---|
| 恒等式门对已知伪造算例的判废率 | 12/12（外部独立复核结论一致） | **12/12 = 100%**，是否<3%：不适用（分类判据，须 ≥100%） |
| 恒等式门对自洽合成算例的误判率 | 0/1 | **0%**，<3% ✅ |
| Ct 派生式 `Ct=Rt/(½ρV²S)` 逆运算残差 | 解析恒等 | 相对残差 <1e-12，<3% ✅ |
| 死端口缺陷复现 | 修复前 `error_pct=999`（不可达也报误差） | 修复后 `error_pct=None` + `UPSTREAM_UNAVAILABLE`，缺陷已消除 ✅ |
| 参考值本身能否当外部判据 | 需数据集自洽 | **不能**（12/12 判废）= honest-FAIL，非本单可修复项 |
