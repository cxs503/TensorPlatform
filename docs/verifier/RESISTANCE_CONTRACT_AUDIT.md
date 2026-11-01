# 阻力端点契约与解析一致性复核（verifier 席 tp-ver-1 · mission 1582）

## ① 案例介绍

被复核对象：已上线端点 `POST /api/v1/calculations/resistance`（能力 `calc.resistance`，
台账证据等级 **REAL**，自述方法串 "ITTC-57摩擦阻力 + Holtrop剩余阻力"）。
之所以复核它：`artifacts/credit_ledger.jsonl` 里唯一的 resistance 裁决针对的是
`tools/verifier/pilots/resistance_calculator.py`（一个**独立玩具实现**），
**从未有任何裁决针对真正对外服务的端点**；而 1148 号对账报告已指出台账与自述等级存在
OVERCLAIM 风险。本单因此把"验证欠账"补在端点上：不读源码、不做数值 CFD，只用
闭式恒等式把端点自述的每一个物理量逐项钉住。

为什么能钉死：端点返回的 8 个量中，Re、Fr、Cf(ITTC-1957)、Rr、Pe 都有**定义级**闭式参考，
不需要实验值也不需要文献查表；CT/Cr 的经验部分则以端点的 `method` 自述为被检命题，
用恒等式反解其隐含常数（结果反解出 ITTC-1978 粗糙度补贴 0.400e-3，见 ③）。

算例（全部为黑盒 POST，载荷与应答逐字节存 `artifacts/verifier/resistance_contract/captures.json`）：

| 算例 | L×B×T [m] | ∇ 声称 [t] | Cb | Vs [kn] | HTTP | Rt [kN] | Pe [kW] |
|---|---|---|---|---|---|---|---|
| boxbarge_10kn | 100×20×8 | 16400 | 1 | 10 | 200 | 509.38 | 2620.23 |
| boxbarge_5kn | 100×20×8 | 16400 | 1 | 5 | 200 | 106.21 | 273.18 |
| boxbarge_20kn | 100×20×8 | 16400 | 1 | 20 | 200 | 4227.2 | 43489.45 |
| boxbarge_10kn_disp8000 | 100×20×8 | 8000 | 1 | 10 | 200 | 509.38 | 2620.23 |
| kcs_like_15p5kn | 230×32.2×10.8 | 52030 | 0.651 | 15.5 | 200 | 2035.13 | 16226.49 |
| invalid_cb_1p2 | 100×20×8 | 16400 | 1.2 | 10 | 200 | 950.84 | 4891.11 |
| zero_speed | 100×20×8 | 16400 | 1 | 0 | 500 | - | - |

参考链常数：1 kn = 0.5144444 m/s（平台实际用 0.5144，反解 0.514400，
偏差 -0.0086%）、g = 9.81、ρ = 1025 kg/m³、
ν = 1.19e-06 m²/s、dCf(ITTC-1978) = 4.0e-04。

## ② 怎么做的

1. **黑盒探针**：7 次 POST（5 个物理算例 + 2 个边界算例）。因平台 :8001 在本机
   load15≈126 时直连 HTTP 会挂起，全部经平台 API 工具调用，应答原样落盘（captures.json，
   含未成功的 500），可离线重放。
2. **单位契约反解**（C0-C2）：端点 schema 未声明 `speed` 单位。以 Fr = V/√(gL) 反解 V，
   得 V/Vs = 0.514400 m/s/knot，即 knots；同时用 Re = VL/ν 反解 ν，
   两者在 6 个算例上互相闭合 → 单位假设不是拟合出来的，是被两条独立定义同时确认的。
3. **闭式恒等式**（C3-C7）：Cf 对 ITTC-1957；Rr 对 ½ρV²S·Cr；Pe 对 Rt·V；
   再由 Rf/(½ρV²S) − cf 反解粗糙度补贴 dCf，并把 ct 用 (cf+dCf+cr) 重建做闭合校验。
4. **契约检查**（C8-C11）：返回的系数场能否自加（ct vs cf+cr）；`displacement` 是否起作用；
   物理不可能几何（Cb>1.2）是否被拒；V=0 是否 4xx。
5. **幂律与后果量化**（C12）：在 5/10/20 kn 上测 dlnRt/dlnV，把"单位误读"从"文档瑕疵"
   翻译成"Rt 低估多少 %"的可执行后果。
6. **工具链**：纯 python + matplotlib；离线 pytest 14 项（`tools/verifier/tests/
   test_resistance_contract.py`），其中 MATH 检查为不变量、5 条缺陷为"钉住现状"的回归锚，
   运行时修好后这些锚会主动失败，提示刷新 captures 并降级缺陷等级。

## ③ 计算结果

**MATH 检查：44/44 PASS，
最大 |相对误差| = 0.1725%（受端点打印精度 2–6 位小数限制）。**

| 检查 | 被检量 | 容差 [%] | max |误差| [%] | 判定 |
|---|---|---|---|---|
| C0 | knot->m/s conversion recovered from Fr | 0.05 | 0.0086 | PASS |
| C1 | reynolds_number (V in m/s from knots) | 0.1 | 0.0086 | PASS |
| C2 | froude_number | 0.1 | 0.0086 | PASS |
| C3 | cf vs ITTC-1957 | 0.1 | 0.0233 | PASS |
| C4 | rr vs 0.5*rho*V^2*S*cr | 0.1 | 0.0223 | PASS |
| C5 | pe vs rt*V | 0.1 | 0.0094 | PASS |
| C6 | implied dCf vs ITTC-1978 0.4e-3 | 1 | 0.1725 | PASS |
| C7 | ct vs (cf+dCf+cr) reconciled | 0.1 | 0.0101 | PASS |
| C12 | Rt monotone in V (power law dlnRt/dlnV) | 0 | 0.0000 | PASS |

说明：C6 的 6.7e-07 级离散度即 6 位小数舍入误差，
其均值 0.000400 与 ITTC-1978 常用粗糙度补贴 0.400e-3 在 0.2% 内一致
—— 端点内部确实含该补贴，**但只在力值里，没在 `cf` 字段里**：

| 算例 | ct (返回) | cf+cr | (ct-(cf+cr))/ct [%] |
|---|---|---|---|
| boxbarge_10kn | 0.010754 | 0.010354 | +3.86 |
| boxbarge_5kn | 0.008969 | 0.008569 | +4.67 |
| boxbarge_20kn | 0.022311 | 0.021911 | +1.83 |
| boxbarge_10kn_disp8000 | 0.010754 | 0.010354 | +3.86 |
| kcs_like_15p5kn | 0.006472 | 0.006072 | +6.59 |
| invalid_cb_1p2 | 0.018039 | 0.017638 | +2.27 |

**CONTRACT 检查：0/9 PASS。**

| 缺陷 | 等级 | 检查 | 描述 | 证据 | 修法 |
|---|---|---|---|---|---|
| D1 | medium | C0/C1/C2/C12 | undeclared knots unit contract | closes only when speed is read as knots (recovered 0.514400 m/s per knot); reading it as SI m/s underestimates Rt by 82.9% | declare units in the schema (or switch to m/s) + OpenAPI description |
| D2 | medium | C6/C7/C8 | cf field and rf/ct field are not the same quantity (dCf=0.400e-3 missing) | cf+cr is 1.8-6.6% below ct in all 6 cases; back-solving the force gives a constant dCf=3.99e-4 (C6 within 0.2% in all cases) | return cf_total=cf+dCf (or an explicit dcf field) so that cf+cr=ct |
| D3 | high | C10 | no geometry/consistency validation (Cb>1 accepted) | Cb=1.2 => nabla=L*B*T*Cb>L*B*T, physically impossible; endpoint returns 200 with a plausible-looking answer | validate 0.3<Cb<=1.0 and |nabla-L*B*T*Cb|/nabla<5%, else 422 |
| D4 | low | C9 | displacement silently ignored | 16400 t and 8000 t give bit-identical responses | derive Cb/Cm from displacement, or mark the field ignored |
| D5 | medium | C11 | V=0 raises an unhandled exception (HTTP 500) | speed=0 -> 500 Internal Server Error (expected 422) | validate speed>0, return 422 for V<=0 or Re<=1 |

后果量化（C12 实测幂律 dlnRt/dlnV = 2.66）：若调用方把
`speed` 当 SI m/s 传入，Rt 被低估 83%
（例：106.21 / 509.38 /
4227.20 kN @ 5/10/20 kn）。这不是 15% 线附近的误差，而是数量级级误导。

## ④ 结果图

- 图1 `fig1_cf_vs_ittc1957.png`：端点 6 个 cf 点全部落在 ITTC-1957 曲线上（log-log），
  最大偏差 0.023%，是"REAL 而非 MOCK"的直观证据。
- 图2 `fig2_error_bars.png`：逐项 |相对误差| 条形（对数轴），蓝=闭式恒等式（全部 <0.03%），
  红=契约检查 C8（1.8–6.6%），并画出 0.1%（舍入下限）/3%（目标）/15%（不可接受）三线。
- 图3 `fig3_rt_powerlaw_and_coef_gap.png`：(a) Rt–V 幂律与拟合；(b) 各算例
  (ct−(cf+cr))/ct 缺口，显示缺的正是同一个 0.400e-3 补贴。

## ⑤ 注意点

- **未声明单位是本单最"轻"却最致命的发现**：端点 schema 只有 `speed`，无 units 字段；
  正确性完全依赖调用方读中文方法串猜单位。已在 D1 给出最小修复（schema 声明/SI 化）。
- **本单不做**：(a) 不读平台运行时源码（:8001 服务位于白名单外的
  `/srv/tensor/ship-workspace/`），因此 D2-D5 只交证据与修法，不改实现；
  (b) 不重算 Holtrop 的 Cr / 湿表面积 / (1+k) 经验回归——这三项无可复现的公开系数录入，
  标为 **WITHHELD**（需要 CM、CWP、Cstern、ABT 等入参，端点未暴露），不能声称已复核。
  因此本单的"REAL"判定**只覆盖定义级闭式量**，不覆盖经验阻力分量。
- 浮箱算例 Cb=1.0 只用来让 L·B·T·Cb 与 ∇ 对得上；c5 的 KCS 型算例用于验证常规船型下
  同一套闭式关系依然闭合（不是只在退化几何上成立）。
- 承载力纪律：本机 load15≈126（nproc=128），全程纯公式+matplotlib，未起重算力进程。

## ⑥ 误差判定（是否 <3%）

逐项（详见 ③ 表）：
  - C0 knot->m/s conversion recovered from Fr: 0.0086% → 是
  - C1 reynolds_number (V in m/s from knots): 0.0086% → 是
  - C2 froude_number: 0.0086% → 是
  - C3 cf vs ITTC-1957: 0.0233% → 是
  - C4 rr vs 0.5*rho*V^2*S*cr: 0.0223% → 是
  - C5 pe vs rt*V: 0.0094% → 是
  - C6 implied dCf vs ITTC-1978 0.4e-3: 0.1725% → 是
  - C7 ct vs (cf+dCf+cr) reconciled: 0.0101% → 是
  - C12 Rt monotone in V (power law dlnRt/dlnV): 0.0000% → 是
  - C8 ct vs (cf+cr): 6.59% → 否
  - C9 displacement 16400 t vs 8000 t sensitivity: 契约违规（非数值误差）→ 否
  - C10 Cb = 1.2 acceptance: 契约违规（非数值误差）→ 否
  - C11 V = 0 handling: 契约违规（非数值误差）→ 否

**结论**：端点自述的 6 项闭式物理量 **全部 <3%（实测 <0.03%）→ MATH 判定 PASS**；
但**返回字段层的 cf/ct 不自洽最大 6.59% > 3% → CONTRACT 判定 FAIL**，
且 D1/D3/D5 属可导致误用的契约缺陷。整体判定：**部分 PASS / 契约 FAIL（honest）**——
即"算法是对的，接口把对的算式包坏了"。修 D2 只需在响应里多返回一个 dcf 或把 cf 换成
cf_total，修 D3/D5 只需两处入参校验，均为分钟级改动；本单不越界改运行时仓。

## 产物

- `tools/verifier/audit_resistance_contract.py`（门：12 类检查 + 3 图）
- `tools/verifier/tests/test_resistance_contract.py`（14 项 pytest，全绿）
- `artifacts/verifier/resistance_contract/{captures.json, gate_report.json, fig1..fig3}`
- 本文件由 `tools/verifier/render_resistance_contract_report.py` 程序化渲染，数字零手抄。
