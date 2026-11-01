# 船舶阻力基准验证链：完整性审计与真实物理复核

任务：`selfstart`（models 席，tp-mod-3）· 分支 `agent/m1530` · 目标仓 TensorPlatform
工件：`docs/verification/benchmark_audit.json`、`docs/verification/figures/fig_benchmark_audit.png`
复现：`cd backend && DATABASE_URL="sqlite:////tmp/tp.db" python3 ../docs/verification/gen_benchmark_audit.py`

---

## 1. 案例介绍（为什么做这一单）

平台对外声称 `calc.resistance` / `dev.gate_5415` 为 **REAL**（审计口径），其“真实”判定依赖
`POST /api/v1/calculations/benchmark_validate` 这条基准验证链：把平台算得的阻力系数与
`backend/data/benchmark_reference.json` 的实验值比对，误差 ≤15 % 记为通过。

本单起步时对这条链做只读复核，发现三个结构性问题（均可用代码/数据直接判定）：

| 编号 | 问题 | 判定方法 |
|---|---|---|
| D1 | 验证链**从不会真正跑物理**：`_call_resistance_api` 把条件 POST 到 `:8002` 的船舶平台，并读回 `ct` 键；而平台本地 `/calculations/resistance` 只返回 `total_resistance_n`（无 `ct`），且 `:8002` 不在部署栈内。取不到即 `data.get("ct", 0.0)` → **ct=0 → 误差恒 100 %**，且无人察觉。 | 读 `api/domain/ship_benchmark.py` + `api/calculations.py` 响应模型 |
| D2 | 验证**是循环论证**：`tests/feat/test_ship_benchmark.py` 用 `mock_resistance_api` 打桩，桩内写的是 `base_ct = 0.0025 + 0.003*Fn` 之类的手写公式，测试只证明“桩与自身一致”。 | 读测试 fixture |
| D3 | 参考数据集本身未经验收：12 条参考行的派生量（Froude 数、尺寸阻力）与自身系数互相矛盾。 | 见 §3.1 审计表 |

因此本单定位为**验证欠账清算**：把验证链改成真实在进程内计算、把参考数据集的完整性做成可机读审计、
并把“哪些数值不可用”如实上报（honest-FAIL 不粉饰）。

物理背景：阻力验证用三条标准船型——SUBOFF（DARPA 轴对称潜艇裸艇体，L=4.356 m/D=0.508 m）、
DTMB-5415（ITTC V&V 2010 水面舰，模型 L=5.72 m）、KVLCC2（SIMMAN 2008 油轮，L=320 m）。
平台阻力模型（`services/resistance_core.py`）：ITTC-1957 摩擦线 + `Cr = 0.002(1+2Cb)Fn^2.5`
残余阻力经验拟合 + 轴对称湿表面积估计 `S = π B L (0.5+0.25Cb)`。

## 2. 怎么做的（方法/工具链）

1. **物理内核单一来源**：新增 `backend/services/resistance_core.py`，把原先内联在
   `api/calculations.py` 的公式抽出为纯函数（含 `froude_number` / `ittc57_cf` /
   `axisym_wetted_surface` / `ct_from_rt`）。`POST /calculations/resistance` 改为调用它，并在响应中
   增补 `ct` 与 `wetted_surface_m2`（下游验证链正缺这两个量）。
   **重构不改数值**：对 5 组工况与旧内联公式逐项比对，残差 ≤2.3e-10 N（相对 1.5e-16，纯浮点舍入）。
2. **新增完整性审计** `backend/services/benchmark_audit.py`（纯 Python，无 DB/无网络）：
   - `FROUDE_INCONSISTENT`：声明 Fn 与 `V/√(gL)` 相对偏差 >5 % → 该行工况身份不可信（硬排除）；
   - `CT_RT_INCONSISTENT`：`Ct·½ρSV²` 与声明 `Rt` 相对偏差 >15 % → 尺寸列不可用（软告警，Ct 列仍保留）；
   - `WETTED_SURFACE_INVALID`：轴对称 S 估计与公开湿表面积偏差 >15 %；
   - 有效性包络：`Cb<0.60`（残余阻力拟合为潜艇标定）、`Fn>0.40`（超出标定域）、
     `Cb>0.65`（无形状因子 `1+k`、无兴波项）；
   - 趋势检查：深潜体 Ct 应随 Re 下降，数据集却单调上升 → 标记 `physically_suspect`。
3. **修复验证链** `backend/api/domain/ship_benchmark.py`：默认**进程内**调用 `resistance_core`
   （仅当显式设置 `SHIP_PLATFORM_URL` 才走远端）；远端若只回尺寸力则用 `ct_from_rt` 反推 Ct（修 D1 的字段错配）；
   按审计结果硬排除坏行、软告警保留、输出 `envelope_flags`；
   新增 `GET /calculations/benchmark_audit` 暴露数据集审计。
   几何预设修正：`SUBOFF.cb` 原为 `0.508`（= 直径，占位符而非方形系数）→ 改为与
   `POST /calculations/resistance` 标定值一致的 `0.542`；三船型补齐 `wetted_surface_m2`
   （SUBOFF 5.42 / 5415 4.70 / KVLCC2 27000 m²）。
4. **可信判定口径**：只有“误差 ≤15 % **且** 有效性包络无旗标”才算 `verified_passes_in_envelope`；
   包络外的偶然小误差不得计为验证通过。

## 3. 计算结果

### 3.1 参考数据集完整性审计（12/12 行异常）

| case | Fn 一致性偏差 | \|Rt(声明) − Ct·½ρSV²\| / Rt | 状态 |
|---|---|---|---|
| SUBOFF-01…04 | 0.02 / 0.03 / 0.42 / 0.02 % | 640 / 724 / 802 / 852 % | REFERENCE_WARNING |
| DTMB5415-01…04 | **462.87 / 462.87 / 462.93 / 462.91 %** | 751 / 727 / … / 1739 % | **INCONSISTENT（硬排除）** |
| KVLCC2-01…04 | 0.36 / 0.19 / 0.48 / 0.06 % | 440 / 462 / 476 / 483 % | REFERENCE_WARNING |

`status_counts = {REFERENCE_WARNING: 8, INCONSISTENT: 4}`，`verdict = REFERENCE_DATASET_UNUSABLE`。
DTMB5415 的 Fn 偏差 463 % 的成因可判定：声明 Fn 对应约 181 m 的参考长度（全尺度 L），
而速度列是 5.72 m 模型的模型尺度节数——**同一行内混用了两个尺度**。
SUBOFF 的 Ct 由 0.0038 单调升到 0.0044，而 ITTC-57 的 Cf 由 0.002867 降到 0.002558，
即 `Ct/Cf` 由 1.33 涨到 1.72；深潜体无兴波、形状因子不随速度变，该趋势物理上不成立。

### 3.2 真实物理复核（进程内，无人为桩）

| case | $C_T$ 平台 | $C_T$ 参考 | 误差 | ≤15 % ? | 是否计入验证 |
|---|---|---|---|---|---|
| SUBOFF-01 | 0.003462 | 0.00380 | 8.90 % | 是 | 否（包络外：Cb<0.6、Fn>0.4） |
| SUBOFF-02 | 0.003953 | 0.00400 | 1.18 % | 是 | 否（同上） |
| SUBOFF-03 | 0.004766 | 0.00420 | 13.48 % | 是 | 否（同上） |
| SUBOFF-04 | 0.005921 | 0.00440 | 34.58 % | **否** | 否（honest-FAIL） |
| KVLCC2-01 | 0.001415 | 0.00280 | 49.46 % | **否** | 否（无 1+k、无兴波项） |
| KVLCC2-02 | 0.001410 | 0.00300 | 53.00 % | **否** | 否 |
| KVLCC2-03 | 0.001408 | 0.00310 | 54.59 % | **否** | 否 |
| KVLCC2-04 | 0.001406 | 0.00320 | 56.06 % | **否** | 否 |
| DTMB5415-01…04 | — | — | — | — | 硬排除（Fn 身份破碎） |

汇总（`benchmark_audit.json → validation.*.summary`）：SUBOFF 平均误差 14.54 %、
`verified_passes_in_envelope = 0`；KVLCC2 平均误差 53.28 %、同样 0；DTMB-5415 无可用行。
**结论：平台阻力模型目前没有任何一条“包络内 ≤15 %”的验证证据，`calc.resistance` 的 REAL 评级应降级为
REAL-with-honest-FAIL（算法是真的，但对标证据缺失）。**

### 3.3 湿表面积估计的独立缺陷（只污染尺寸量）

轴对称式对排水型船严重高估：KVLCC2 得 40 958 m² vs 公开 ~27 000 m²（**+52 %**），
DTMB-5415 得 8.552 m² vs ~4.70 m²（**+82 %**）。
同时可证明一个有用性质：`Ct = Cf + Cr`，面积在无量纲化中约掉，
所以 **Ct 误差与 S 误差可分离**——S 偏差只放大预报阻力/有效功率（KVLCC2 有效功率将偏高约 52 %），
不影响 Ct 比对。这两类误差必须分开记账，不能混为“模型不准”。

## 4. 结果图

`figures/fig_benchmark_audit.png`（左）参考 Ct（空心点）与平台 Ct（实线方点）随速度变化，
阴影为 ±15 % 验收带；（右）逐行一致性偏差（对数轴）：橙色为 `|Rt − Ct·½ρSV²|/Rt`、
紫色为 `|Fn − V/√(gL)|/Fn`，虚线为 15 % 容差。12 行全部越过容差线，
DTMB5415 的 Fn 偏差（4.6×10²%）为最严重项。

## 5. 注意点（坑与适用范围）

- **`--noconftest` 是必须的**：`backend/tests/conftest.py` 导入 `main`，本机缺 `cryptography`
  与 `psycopg2`，全套 fixture 无法加载；本轮用
  `DATABASE_URL="sqlite:////tmp/tp_test.db" python3 -m pytest --noconftest ...` 运行模块级测试。
  新测试不依赖 conftest。
- **不要用 mock 证明物理**：本轮把“端点循环”与离线 `validate_cases` 做了交叉一致性测试
  （`TestConsistencyBetweenPaths`），防止两条路径漂移；原有桩测试保留但不再承担物理结论。
- **未做（如实说明）**：没有新增 Holtrop 形状因子 `(1+k)` 与兴波项，也没有替换 `benchmark_reference.json`
  的数值（无联网条件核对原文，改写实验值等于伪造）。数据集修复需要人工/联网取 Roddy 1990、
  ITTC 2010、SIMMAN 2008 原表；本轮只把它们的矛盾做成机读证据与硬排除规则。
- 一处历史测试断言 `total_cases >= 10` 已随硬排除改为 `>= 8`（12 行 − 4 行被排除），
  并在测试注释中标明原因。

## 6. 误差判定（逐项，显式回答是否 <3 %）

| 关键量 | 基准 | 误差 | <3 % ? |
|---|---|---|---|
| 重构前后阻力结果（5 组工况） | 旧内联公式 | ≤2.3e-10 N（相对 1.5e-16） | **是** |
| SUBOFF-02 $C_T$ | 数据集 0.00400 | 1.18 % | **是**（但包络外，不计为验证） |
| SUBOFF-01/03/04 $C_T$ | 数据集 | 8.90 / 13.48 / 34.58 % | 否 |
| KVLCC2 $C_T$（4 工况） | 数据集 | 49.5–56.1 % | 否 |
| 轴对称湿表面积 KVLCC2 | 27 000 m² | +52 % | 否 |
| 轴对称湿表面积 5415 | 4.70 m² | +82 % | 否 |

**注意**：SUBOFF 的 1.18 % 这类“好看”的数字不可当作精度证明——其参考 Ct 列本身已被趋势检查判为
物理可疑，且模型处于包络外；这正是本单要防的“把巧合当验证”。

## 7. 交付与验收

- 代码：`backend/services/resistance_core.py`（新）、`backend/services/benchmark_audit.py`（新）、
  `backend/api/calculations.py`（改）、`backend/api/domain/ship_benchmark.py`（改）。
- 测试：`backend/tests/feat/test_benchmark_integrity_audit.py`（新，24 例）+
  `backend/tests/feat/test_ship_benchmark.py`（25 例，含 1 处断言按硬排除更新）→ **49 passed**。
- 工件：`docs/verification/benchmark_audit.json`、`figures/fig_benchmark_audit.png`、
  生成脚本 `gen_benchmark_audit.py`。
- 后续建议（按性价比排序）：①按原表修复参考数据集（当前 verdict 为 UNUSABLE，是验证链最大瓶颈）；
  ②给阻力模型加 `(1+k)Cf + Cw(Fn, 船型)`，把 KVLCC2 的 50 % 欠预报压回 15 % 内；
  ③把 `wetted_surface_m2` 作为 `/calculations/resistance` 的可选输入，避免轴对称式污染功率预报。