# 阻力端点契约缺陷清零（verifier 席 · mission 1611）

## ① 案例介绍

**被复核与修复的对象**：运行时仓 `/srv/tensor/ship-workspace/` 的服务端点
`POST /api/v1/calculations/resistance`（能力 `calc.resistance`，台账证据等级 REAL，
自述方法串此前为 "ITTC-57摩擦阻力 + Holtrop剩余阻力"）。

**为什么有这一单**：m1582 用纯闭式恒等式黑盒复核该端点，结论是"算法是对的，接口把对的算式包坏了"——
6 项定义级物理量最大偏差 <0.03%，但接口层 5 项契约缺陷（D1 knots 单位未声明；D2 `cf` 缺 ITTC-1978
粗糙度补贴 dCf=0.400e-3，使 cf+cr 恒比 ct 低 1.8–6.6%；D3 Cb=1.2 物理不可能几何仍返 200；D4
`displacement` 被静默忽略；D5 V=0 返 500）。本单在运行时仓落地修复，并用**同一套判据、同一套容差**
证明缺陷清零、MATH 不回归。

**几何与工况**（全部黑盒 POST，载荷与应答逐字节存
`artifacts/verifier/resistance_contract/captures.json`，10 个算例）：

| 算例 | L×B×T [m] | ∇ [m³] | Cb | Vs | HTTP | Rt [kN] | ct | 判读 |
|---|---|---|---|---|---|---|---|---|
| boxbarge_5kn | 100×20×8 | 16400 | 1 | 5 kn | 200 | 106.23 | 0.008969 | 闭合 |
| boxbarge_10kn | 100×20×8 | 16400 | 1 | 10 kn | 200 | 509.48 | 0.010754 | 闭合 |
| boxbarge_20kn | 100×20×8 | 16400 | 1 | 20 kn | 200 | 4228.18 | 0.022313 | 闭合 |
| midcb_10kn | 100×20×8 | 12800 | 0.8 | 10 kn | 200 | 299.25 | 0.007111 | 闭合 |
| kcs_like_15p5kn | 230×32.2×10.8 | 52030 | 0.651 | 15.5 kn | 200 | 2035.53 | 0.006473 | 闭合 |
| kcs_like_15p5kn_ms | 230×32.2×10.8 | 52030 | 0.651 | 7.97389 m/s | 200 | 2035.53 | 0.006473 | 闭合 |
| boxbarge_10kn_disp8000 | 100×20×8 | 8000 | 1 | 10 kn | 422 | — | — | 拒绝（契约门禁） |
| invalid_cb_1p2 | 100×20×8 | 16400 | 1.2 | 10 kn | 422 | — | — | 拒绝（契约门禁） |
| zero_speed | 100×20×8 | 16400 | 1 | 0 kn | 422 | — | — | 拒绝（契约门禁） |
| negative_speed | 100×20×8 | 16400 | 1 | -3 kn | 422 | — | — | 拒绝（契约门禁） |

参考链常数：1 kn = 0.5144444444 m/s（= 1852/3600，修复前实现用 0.5144）、
g = 9.81、ρ = 1025 kg/m³、ν = 1.19e-06 m²/s、
dCf(ITTC-1978) = 4.0e-04。
探针还抓取端点 `openapi.json` 片段，因为 D1 是**声明**缺陷：只有 schema 自己写明单位，修才可验证。

## ② 怎么做的

1. **保留判据、只改实现**：`tools/verifier/audit_resistance_contract.py` 的 MATH 容差
   （44 项检查，TOL 见下）与 3% 验收线一字未改；改的只是被检对象
   与 C8 的闭合字段（`cf_total`）。这是"翻案不靠放宽容差"的前提。
2. **运行时修复**（`backend/api/calculations.py`，229 行 diff，存档
   `artifacts/verifier/resistance_contract/runtime_patch.diff`）：
   - D1：新增 `speed_unit: Literal["kn","m/s"]`（默认 `kn`），`speed` 与单位均在 pydantic
     `Field(description=...)` 与路由 OpenAPI description 中声明；换算常数改为 **1852/3600**
     （精确值），并回显 `speed_ms`。
   - D2：响应新增 `dcf` 与 `cf_total = cf + dcf`，且 `ct` 由取整后的字段求和生成，
     使返回的系数场恒等闭合。
   - D3/D4：`model_validator` 校验 `0.3 < Cb <= 1.0` 且 `|∇ − L·B·T·Cb|/∇ < 5%`，违者 422；
     `displacement` 因此真正进入判定，并在 200 响应里回显 `cb_from_dims`、
     `displacement_consistency_pct`。
   - D5：`speed: Field(gt=0)` + 显式 V≤0 守卫 → 422。
3. **新探针**：`tools/verifier/probe_resistance_contract.py` 起 10 次 POST（含 m/s 声明、
   不一致 ∇、Cb>1、V=0、V<0），刷新 captures.json 并抓 OpenAPI 片段。
4. **门禁**：`audit_resistance_contract.py` 在原 C0–C12 之外新增三项契约检查——
   **C13** 声明（`speed_unit` 枚举 + 描述 + 响应字段）、**C14** `m/s` 数值等价、
   **C11b** V<0；C8 改为对 `cf_total+cr`，C9 改为"随 displacement 变化**或**拒绝不自洽对"。
5. **测试翻转**：`tools/verifier/tests/test_resistance_contract.py` 的 5 条 PINS 断言由
   "钉住缺陷"改写为"证明已修"（每条 docstring 保留 `Was:` 原锚点）；verifier 套件
   `43 passed`（本文件 16 项）。
6. **工具链**：python + urllib（探针）+ matplotlib（图）+ pytest；服务以
   `/root/ship-venv` uvicorn 重启于 127.0.0.1:8001。全部离线可重放。

## ③ 计算结果

**MATH：44/44 PASS，最大 |相对误差| =
0.0933%（受端点打印精度限制）。**

| 检查 | 被检量 | 容差 [%] | max |误差| [%] | 判定 |
|---|---|---|---|---|
| C0 | knot→m/s 换算反解（Fr 逆算） | 0.05 | 0.0000 | PASS |
| C1 | Re = V·L/ν（单位由请求声明） | 0.1 | 0.0000 | PASS |
| C2 | Fr = V/√(gL) | 0.1 | 0.0000 | PASS |
| C3 | cf 对 ITTC-1957 | 0.1 | 0.0244 | PASS |
| C4 | rr 对 ½ρV²S·cr | 0.1 | 0.0104 | PASS |
| C5 | pe 对 rt·V | 0.1 | 0.0010 | PASS |
| C6 | Rf 反解的 dCf 对 ITTC-1978 0.400e-3 | 1 | 0.0933 | PASS |
| C7 | ct 对 (cf+dCf+cr) 重建 | 0.1 | 0.0051 | PASS |
| C12 | Rt 随 V 单调（幂律 dlnRt/dlnV） | 0 | 0.0000 | PASS |

**CONTRACT：12/12 PASS。**

| 检查 | 算例 | 被检量 | 观测 | 误差 [%] | 判定 |
|---|---|---|---|---|---|
| C8 | boxbarge_5kn | ct vs (cf_total+cr) as returned | 0.008969 | -1.93e-14 | PASS |
| C8 | boxbarge_10kn | ct vs (cf_total+cr) as returned | 0.010754 | +0.00e+00 | PASS |
| C8 | boxbarge_20kn | ct vs (cf_total+cr) as returned | 0.022313 | +0.00e+00 | PASS |
| C8 | midcb_10kn | ct vs (cf_total+cr) as returned | 0.007111 | +0.00e+00 | PASS |
| C8 | kcs_like_15p5kn | ct vs (cf_total+cr) as returned | 0.006473 | +0.00e+00 | PASS |
| C8 | kcs_like_15p5kn_ms | ct vs (cf_total+cr) as returned | 0.006473 | +0.00e+00 | PASS |
| C9 | boxbarge_10kn_disp8000 | displacement 16400 m3 vs 8000 m3 (nabla = L*B*T*Cb = 16000 m3) | HTTP 422 (geometry gate) | — | PASS |
| C10 | invalid_cb_1p2 | Cb = 1.2 acceptance | HTTP 422 | — | PASS |
| C11 | zero_speed | V = 0 handling | HTTP 422 | — | PASS |
| C11b | negative_speed | V < 0 handling | HTTP 422 | — | PASS |
| C13 | openapi.json | units + self-consistent coefficient field declared in the schema | {"speed_unit_field": true, "speed_unit_enum": ["kn", "m/s"], "speed_description_mentions_unit":  | — | PASS |
| C14 | kcs_like_15p5kn_ms | same physical speed declared as m/s vs knots | 2035.53 | +0.00e+00 | PASS |

**缺陷台账（was → now）**：

| 缺陷 | 等级 | 状态 | 原状（m1582 钉住） | 修法（m1611 落地） |
|---|---|---|---|---|
| D1 | medium | FIXED | schema had only `speed`; responses closed only when read as knots (recovered 0.5144 m/s per knot), and Rt moves with dlnRt/dlnV=2.66, so a SI-m/s caller was off by 83% | `speed_unit` enum {kn, m/s} (default kn) + Field/OpenAPI descriptions; knot converted with the exact 1852/3600 instead of 0.5144 (removes a +0.023% Rt bias); `speed_ms` echoed back |
| D2 | medium | FIXED | cf+cr sat 1.8-6.6% below ct in every case; back-solving the force gave a constant dCf=3.998e-04 | response now returns `dcf` and `cf_total = cf + dcf`; ct is formed from the rounded fields, so (cf_total+cr-ct)/ct = 0.0000% <= 0.1% |
| D3 | high | FIXED | Cb=1.2 (nabla > L*B*T) answered with HTTP 200 and a plausible-looking Rt | model validator: 0.3 < Cb <= 1.0 and abs(nabla - L*B*T*Cb)/nabla < 5% -> 422 (geometry reply echoed as cb_from_dims / displacement_consistency_pct) |
| D4 | low | FIXED | 16400 m3 and 8000 m3 returned bit-identical responses | displacement now enters the returned geometry verdict: an inconsistent nabla is rejected with 422 naming both volumes; consistent cases echo cb_from_dims / displacement_consistency_pct |
| D5 | medium | FIXED | speed=0 -> 500 Internal Server Error | Field(gt=0) on `speed` plus an explicit V<=0 guard -> 422 |

**系数场闭合（D2 的机器证据）**：`cf_total+cr−ct` 逐算例为：

| 算例 | cf | dcf | cf_total | cr | ct | cf_total+cr−ct |
|---|---|---|---|---|---|---|
| boxbarge_5kn | 0.001869 | 0.000400 | 0.002269 | 0.006700 | 0.008969 | +1.7e-18 |
| boxbarge_10kn | 0.001703 | 0.000400 | 0.002103 | 0.008651 | 0.010754 | +0.0e+00 |
| boxbarge_20kn | 0.001559 | 0.000400 | 0.001959 | 0.020354 | 0.022313 | +0.0e+00 |
| midcb_10kn | 0.001703 | 0.000400 | 0.002103 | 0.005008 | 0.007111 | +0.0e+00 |
| kcs_like_15p5kn | 0.001452 | 0.000400 | 0.001852 | 0.004621 | 0.006473 | +0.0e+00 |
| kcs_like_15p5kn_ms | 0.001452 | 0.000400 | 0.001852 | 0.004621 | 0.006473 | +0.0e+00 |

D2 反解出的隐含补贴均值 = 0.000400（与 0.400e-3 相差
0.039%，
即 C6 仍以力值反解确认补贴未被偷偷改掉）。

**单位契约（D1）的两项独立证据**：
- 换算常数反解：Fr 逆解得 0.5144444444444445 m/s/knot，与 1852/3600 的相对偏差
  +0.0e+00%（修复前同一反解给出 0.514423）。换常数顺带清掉一个
  +0.0230% 的 Rt 系统偏置（经 dlnRt/dlnV =
  2.66 幂律放大）。
- `m/s` 声明路径与 knots 路径数值等价：Rt 2035.53 kN（kn）
  vs 2035.53 kN（m/s，同一物理航速），相对差
  +0.00e+00%；`speed_ms` 回显相对差
  +1.39e-06%。
- 单位误读的**后果仍然量化**：若调用方把 speed 当 SI m/s 传入，Rt 低估
  83%（5/10/20 kn 下 Rt =
  106.23 / 509.48 / 4228.18 kN）。这正是必须显式声明单位、
  而不能靠"读中文方法串猜"的理由。

## ④ 结果图

- 图1 `fig1_cf_vs_ittc1957.png`：6 个算例的 cf 点全部落在 ITTC-1957 曲线上（log-log），
  最大 MATH 偏差 0.093%——"REAL 而非 MOCK"的直观证据。
- 图2 `fig2_error_bars.png`：逐项 |相对误差| 条形（对数轴），蓝 = 闭式恒等式，红 = C8 系数闭合；
  0.1%（打印精度下限）/3%（目标）/15%（不可接受）三线并列，可见全部落在 0.1% 量级。
- 图3 `fig3_rt_powerlaw_and_coef_gap.png`：(a) Rt–V 幂律（斜率 2.66）
  与单位误读后果；(b) 修复后各算例 (ct−(cf_total+cr))/ct 全部为 0（±3% 带内贴零）。

## ⑤ 注意点（踩过的坑与局限）

- **接口层缺陷比算法缺陷更贵**：D2 只差一个返回字段，却让"系数场"无法自加；D1 没有单位声明，
  正确性由调用方猜。两者都不影响端点内部算式，因此任何只测"数值是否合理"的验收都会漏掉。
- **服务重启**：运行时服务（:8001，uvicorn，无 --reload）必须重启才生效；本单以原环境变量
  重拉起并核对 `/health`，期间该端口短时不可用。
- **∇ 单位歧义**：字段注释过去写"排水量 Δ (t)"，而几何一致性判据用的是体积 ∇/(L·B·T)。
  本单把契约明确为 **∇ [m³]**（质量 t 请除以 ρ≈1.025），并把 5% 门限设计成能吸收
  ρ≈1.025 的 t↔m³ 混淆（2.5%）；同时修正了 D3 判据。**残留风险**：调用方若按 t 传入且船型
  系数与 ρ 偏差叠加超过 5%，会被 422 拒绝而不是静默算错——这是有意的 fail-safe 取舍。
- **C6 边际**：dCf 由 6 位小数的 cf 与 2 位小数的 Rf 反解，量化误差最大
  0.093%（容差 1%）。
  若将来提高打印精度，该值会更小；不作为模型精度证据。
- **不做（WITHHELD）**：不重算 Holtrop 的 Cr / 湿表面积 / (1+k) 经验回归——缺 CM、CWP、
  Cstern、ABT 等入参，端点未暴露，标 **WITHHELD**；本单的 REAL 判定只覆盖定义级闭式量。
- **Rt 绝对值的内部诊断（C15，非门禁）**：所有闭式恒等式成立 ≠ 阻力量级正确。逐算例的
  剩余/摩擦比见下表；其中 2（kcs_like_15p5kn, kcs_like_15p5kn_ms）为
  `HIGH_RESIDUAL`——细长低 Fn 船型的剩余阻力反而超过摩擦阻力，与 Holtrop 的 (1+k) 形式
  （Fn≲0.2 时 Cr/Cf ~ 0.1–0.3）相差一个量级以上。结论：**端点的 Cr 子模型是简化占位式，
  Rt 绝对值不可用于工程判定**；这正是本单标 WITHHELD、只对定义级闭式量判 REAL 的原因。
  该诊断刻意不设 PASS/FAIL（缺外部基准曲线），避免用自造阈值制造"已标定"的假象。

| 算例 | Cb | Fn | cf | cr | cr/cf | 判读 |
|---|---|---|---|---|---|---|
| boxbarge_5kn | 1 | 0.082 | 0.001869 | 0.006700 | 3.58 | PLAUSIBLE |
| boxbarge_10kn | 1 | 0.164 | 0.001703 | 0.008651 | 5.08 | PLAUSIBLE |
| boxbarge_20kn | 1 | 0.328 | 0.001559 | 0.020354 | 13.06 | PLAUSIBLE |
| midcb_10kn | 0.8 | 0.164 | 0.001703 | 0.005008 | 2.94 | PLAUSIBLE |
| kcs_like_15p5kn | 0.651 | 0.168 | 0.001452 | 0.004621 | 3.18 | HIGH_RESIDUAL |
| kcs_like_15p5kn_ms | 0.651 | 0.168 | 0.001452 | 0.004621 | 3.18 | HIGH_RESIDUAL |

- **不覆盖实验标定**：`calc.resistance` 没有公开实验/模型试验基准算例（KCS 型算例只用于闭合
  恒等式），因此本单**不声称** Rt 绝对值满足 15% 工程精度线；要做这一步需要 KCS/KVLCC 的
  试验阻力曲线作为外部基准，属后续单。
- **越界说明**：运行时仓不在 TensorPlatform 工作树内，其改动以 `runtime_patch.diff` 存档并入
  本仓 artifacts，保证可复现、可审计。

## ⑥ 误差判定（是否 <3%）

逐项（全部由 gate_report.json 程序化读出）：

  - C0 knot→m/s 换算反解（Fr 逆算）: 0.0000% → 是
  - C1 Re = V·L/ν（单位由请求声明）: 0.0000% → 是
  - C12 Rt 随 V 单调（幂律 dlnRt/dlnV）: 0.0000% → 是
  - C2 Fr = V/√(gL): 0.0000% → 是
  - C3 cf 对 ITTC-1957: 0.0244% → 是
  - C4 rr 对 ½ρV²S·cr: 0.0104% → 是
  - C5 pe 对 rt·V: 0.0010% → 是
  - C6 Rf 反解的 dCf 对 ITTC-1978 0.400e-3: 0.0933% → 是
  - C7 ct 对 (cf+dCf+cr) 重建: 0.0051% → 是
  - C8 ct 对 (cf_total+cr)：1.93e-14% → 是
  - C14 同一物理航速以 m/s 声明：Rt 相对差 +0.00e+00% → 是
  - C15 剩余/摩擦比内部诊断（不参与 PASS/FAIL）：boxbarge_5kn cr/cf=3.58（PLAUSIBLE）；boxbarge_10kn cr/cf=5.08（PLAUSIBLE）；boxbarge_20kn cr/cf=13.06（PLAUSIBLE）；midcb_10kn cr/cf=2.94（PLAUSIBLE）；kcs_like_15p5kn cr/cf=3.18（HIGH_RESIDUAL）；kcs_like_15p5kn_ms cr/cf=3.18（HIGH_RESIDUAL）
  - Rt 绝对值对实验/模型试验：本端点无公开基准算例（浮箱 KCS 型算例仅用于恒等式闭合），**无基准即不判误差**，不得声称已做实验标定

**结论**：端点自述的 44 项闭式检查 **全部 <3%（实测最大
0.0933%）→ MATH PASS**；返回字段层 5 项契约缺陷
**5/5 FIXED，CONTRACT
12/12 PASS**，
其中 C8 系数闭合残差 1.93e-14%、`m/s` 与 kn 路径差
+0.00e+00%，均远优于 3% 线。整体判定：**PASS（契约缺陷清零、MATH 不回归）**；
未做实验基准标定一项如实标 WITHHELD。

## 产物

- `backend/api/calculations.py`（运行时修复；diff 存档 `artifacts/verifier/resistance_contract/runtime_patch.diff`）
- `tools/verifier/probe_resistance_contract.py`（新探针，含 OpenAPI 抓取）
- `tools/verifier/audit_resistance_contract.py`（C0–C14 门 + 3 图）
- `tools/verifier/tests/test_resistance_contract.py`（16 项 pytest，PINS 全部翻转）
- `artifacts/verifier/resistance_contract/{captures.json, gate_report.json, runtime_patch.diff, fig1..fig3}`
- 本文件由 `tools/verifier/render_resistance_contract_report.py` 程序化渲染，数字零手抄。
