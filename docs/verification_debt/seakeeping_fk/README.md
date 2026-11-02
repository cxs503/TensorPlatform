# 耐波域验证欠账清偿：解析锚审计 + 线上 `/seakeeping/rao` 退化实锤

使命 **1810**（`tp-mod-2`，models 席）。审计器 `audit_seakeeping.py` + 独立参考 `references_seakeeping.py`，
回归测试 `tests/test_seakeeping_verification_anchors.py`，原始数字 `artifacts/audit_seakeeping.json`，
图 `figs/`。复现：`python3 audit_seakeeping.py`（仅 V1 段需要 :8001 在线）。

## 1. 审什么

| 项 | 值 |
|---|---|
| 参考船 | L = 100.0 m，B = 10.0 m，T = 6.25 m，Cb = 0.6，KG = 7.0 m，GM = 1.5 m |
| 排水量（几何闸门后） | 3843.75 t（= Cb·L·B·T·ρ，偏差 < 1e-3） |
| 频率栅格 | 40 点对数分布，0.05 … 3.0 rad/s |
| 端点 | `http://127.0.0.1:8001/api/v1` |
| 内核重力常数 | 9.80665 m/s²（审计参考同取 ISO 值；容差 0.1%） |

本审计三条独立参考腿：①闭式解（深水色散、FK 长波极限、自然周期构造式、SDOF 峰值放大率）；
②与内核自身自报量的闭合（附加质量→自然周期、双端点一致性）；③输入扰动响应（退化检测）。

## 2. V1 线上端点：退化曲线（缺陷坐实）

| 检查 | 结果 |
|---|---|
| 实返字段 | `['frequencies', 'heave_rao', 'pitch_rao', 'roll_rao']` — **缺** `heave_phase_deg`/`pitch_phase_deg`/`roll_phase_deg`/`encounter_frequencies`/`method`，说明在服务的不是仓内 `seakeeping_physical_api.RAOResult` 那条路由 |
| 垂荡 vs 纵摇 最大相对差 | 0.000e+00（30 个频点，相异点 0 个） |
| 垂荡 vs 横摇 最大相对差 | 9.636e-01 |
| 输入敏感性（垂/纵/横） | 仅对 `['Cb_0p80']` 敏感；对 `['heading_90', 'speed_10kn', 'omega_1p2']` **完全不变** |

**判读**：线上端点把同一条曲线同时填进垂荡（m/m）与纵摇（rad/波陡）两个数组——量纲不同，物理上不可能相等；
且对浪向、航速、请求频率毫无响应。这正是 `backend/services/seakeeping.py:7` 与
`backend/api/domain/ship_seakeeping.py:97` 已文字描述的 legacy 失效模式，本次补上**机器可裁的量化证据**。

## 3. V2 仓内核：9/9 解析锚 PASS

| 锚 | 参考量 | 实测偏差 | 判定 |
|---|---|---|---|
| A1_dispersion | identity | 0.00e+00 | PASS |
| A5_encounter_head_seas | identity | 0.00e+00 | PASS |
| A2_longwave_limit | limit vs 1.0 | 0.0028 | PASS |
| A4_heading_structure |  | 0.00e+00 | PASS |
| A6_natural_period_closure | T3/T4 closure | 0.00e+00 | PASS |
| A7_cross_endpoint |  | 0.00e+00 | PASS |
| A8_geometry_gate | vs 1/(2 zeta) | 0.00e+00 | PASS |
| A9_roll_peak | peak vs omega4 | 0.0144 | PASS |
| A10_roll_peak_magnification | vs 1/(2 zeta) | 0.0017 | PASS |

- **A1_dispersion**: omega_e = omega - k U cos(beta) with k = omega^2/g, U = 0
- **A5_encounter_head_seas**: kernel uses abs(): the following-sea branch (omega_e <= 0) is folded, not flagged
- **A2_longwave_limit**: FK long-wave limit: heave -> 1 m/m, pitch -> 1 rad/(ka), roll -> sin(beta) rad/(ka); kL at the grid floor is finite, hence the 5 % band
- **A4_heading_structure**: f4 is proportional to sin(beta) => roll must vanish identically at 180 deg
- **A6_natural_period_closure**: T3 = 2pi sqrt((m+A33)/(rho g Aw)); T4 = 2pi k_xx / sqrt(g GM), k_xx in metres
- **A7_cross_endpoint**: /seakeeping/rao and /seakeeping/motion share solve_rao, so they must agree exactly
- **A8_geometry_gate**: platform rule: Cb/L/B/T must close before any resistance/RAO number is usable
- **A9_roll_peak**: peak located on the kernel grid (40 log points, 0.05-3.0 rad/s); tolerance = one grid box
- **A10_roll_peak_magnification**: closed form 1/(2 zeta) = 6.25 for zeta = 0.08; finite-grid peak is a slight underestimate

诚实 WITHHELD（不计入 pass/fail）：**A3 高频 ω⁻² 渐近** —— with the FK Smith factor exp(-kT) the excitation decays by exp(-kT) ~ exp(-5.7) at 3 rad/s and the coupled heave-pitch terms re-inject energy, so the omega^-2 reactive asymptote has no accessible window on the 0.05-3.0 rad/s grid；实测 1.5–3.0 rad/s 的
log-log 斜率 -0.566，栅格顶端 kT = 5.74。

## 4. 误差判定（平台标准：>15% 不可接受，<3% 为达标）

| 量 | 参考 | 偏差 | <3%？ |
|---|---|---|---|
| 遭遇频率 ω_e | ω − kU cos β，k = ω²/g | 0.00e+00 | **是（恒等）** |
| T3 垂荡自然周期 | 2π√((m+A33)/(ρgA_w)) | 0.00e+00 | **是（恒等）** |
| T4 横摇自然周期 | 2π k_xx/√(g GM) | 0.00e+00 | **是（恒等）** |
| 长波极限（垂/纵/横） | 1，1，1 | 0.28 % | **是** |
| 横摇固有频率 | √(g GM)/k_xx = 1.0654 rad/s | 1.44 %（一个栅格箱） | **是** |
| 横摇峰值放大率 | 1/(2ζ) = 6.250 | 0.17 % | **是** |
| 线上端点 vs 内核（垂荡） | 同船同频 | ≥100%（不同曲线） | **否** —— 即缺陷本体 |

## 5. 注意事项 / 局限

* 内核的 2D 垂荡系数**并非** Lewis/Tasai 剖面解：`_fa = 1 − 0.25ν²/(1+ν²)`、`_fb = 0.8·2ν/(1+ν²)` 是
  拟合频率因子，而 `_METHOD` 字符串宣称 “frequency-dependent Lewis-section 2D coefficients”。
  上述实现级锚全部通过，但**附加质量绝对量级未对 Lewis/Frank 参考验证**——记为文档过度宣称，严重度 MINOR。
* 因此 RAO 绝对幅值对模型试验仍为 **WITHHELD**：仓内无水池数据，本审计**不**声称对实验 <3%，只声称对闭式解达标。
* 内核 `encounter_frequency` 取 `abs(ω − kU cos β)`，把随浪分支（ω_e ≤ 0）折叠而非标记；
  同仓 `backend/services/seakeeping.py` 是带 valid 标记的，二者不一致。
* 只扫了一条船一个载况，未覆盖 T/L 与 GM 的敏感性。

## 6. 后续（超出单干范围：需动船仓 + 切服务）

把 :8001 切到物理路由属于 `ship-performance-platform` 仓的部署改动（本席不得在 TensorPlatform 改船码），
已另行派单。回归测试 `test_deployed_rao_endpoint_is_not_degenerate` 为 `xfail(strict=True)`：
缺陷一修好即转 XPASS→红灯，强制摘标记，不会悄悄留下假绿。
