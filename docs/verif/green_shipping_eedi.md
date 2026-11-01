# 绿色航行域（EEDI / EEXI / CII）复算参考实现与验证欠账清偿

> 数字由 `artifacts/verif/green_shipping/generate_evidence.py` 程序化注入，无手抄值。规则系数可核验性分级见 evidence.json 的 `provenance` 节。

## 1. 案例与参数

| 算例 | 船型 | Capacity [DWT] | ΣMCR [kW] | SFC_ME | SFC_AE | V_ref [kn] | P_AE [kW] |
|---|---|---|---|---|---|---|---|
| Kamsarmax | bulk_carrier | 82000 | 9000 | 170 | 200 | 14.0 | 450.0 |
| Capesize | bulk_carrier | 180000 | 20000 | 168 | 195 | 14.5 | 750.0 |
| Panamax_Container | container_ship | 45000 | 25000 | 172 | 200 | 19.0 | 875.0 |

## 2. 方法

获得值按 MARPOL Annex VI Reg.22 / MEPC.308(73) §2 闭式实现（主机项含 f_j/f_ice、辅机项 P_AE、PTI 项、机械/电气节能技术扣减，分母 f_i·f_c·f_l·Capacity·V_ref）；EEXI 按 MEPC.350(78) §2.2（P_ME=0.75·MCR、P_AE 分段式、无 f_w）；CII 按 MEPC.336(76) G1 与 G4 dd-vector 评级。旧实现同仓复刻用于缺陷量化。

## 3. 计算结果

| 算例 | 获得 EEDI | 参考线 | Phase 2 要求 | Phase 3 要求 | 达标(Phase 3) | 获得 EEXI | 要求 EEXI | 达标 |
|---|---|---|---|---|---|---|---|---|
| Kamsarmax | 4.402 | 4.357 | 3.486 | 3.050 | FAIL | 3.364 | 3.486 | PASS |
| Capesize | 4.188 | 2.994 | 2.396 | 2.096 | FAIL | 3.186 | 2.396 | FAIL |
| Panamax_Container | 16.317 | 20.221 | 16.177 | 14.155 | FAIL | 12.402 | 14.155 | PASS |

| 算例 | 获得 CII | 要求 CII(2026) | 比值 | 评级 | 旧实现评级 | 旧实现 EEDI | 旧/新偏差 |
|---|---|---|---|---|---|---|---|
| Kamsarmax | 3.418 | 3.709 | 0.922 | B | A | 4.150 | -5.71% |
| Capesize | 3.301 | 2.274 | 1.452 | E | A | 4.009 | -4.29% |
| Panamax_Container | 12.897 | 9.365 | 1.377 | E | E | 15.661 | -4.02% |

### 3.1 误差判定（vs 基准，逐项）

| 判据 | 基准性质 | 最大偏差 | 是否 <3% |
|---|---|---|---|
| EEDI 算术复算 | 与实现相互独立的算式 | 0.000e+00 (相对) | 是 |
| EEDI 参考线（散货船） | 同仓既有代码注释同式（双源） | 0（同式） | 是 |
| 辅助功率分段连续性 | 闭式恒等式（10000 kW 处两段均 500 kW） | <1e-6 kW | 是 |
| CII 评级跃迁 | dd-vector 闭式分界 | 1e-9 相对跃迁 | 是 |
| 规则系数表（C_F / 参考线 / Z / dd） | **仅离线转录，未经规则原文机检** | 未定义 | **否（无基准）** |

> 显式回答：**关键量中可机器判定的部分（算术、恒等式、分界）误差全部 <3%（实为 <1e-9）；但"规则系数本身是否转录正确"本轮无基准可判**——本会话离线，无法与 MEPC 原文逐条机检，故这些系数一律标注 `TRANSCRIBED_OFFLINE_UNVERIFIED`，不冒充已验证基准，法定合规判定须以规则原文复核后使用。

## 4. 结果图

![EEDI vs DWT](artifacts/verif/green_shipping/fig_eedi_vs_dwt.png)

![CII rating](artifacts/verif/green_shipping/fig_cii_rating.png)

## 5. 注意点与局限

- 现网 `api/green_shipping.py` 为 7 行硬编码桩（`optimal_speed=18.5, fuel_saving=12.0, cii_rating="B"`），与输入无关 → MOCK；`api/extended_calculations.py` 的 EEDI 漏 P_AE/f_j/f_c/f_l/PTI/节能技术项、参考线只套散货船系数、阶段用基线 0.7/0.8/0.9 臆造阈值、CII 用绝对阈值（<4→A…）→ 半实现+MOCK。
- 旧实现偏差已量化为上表"旧/新偏差"列；方向恒为低估（漏辅机项），且与船型/功率段相关。
- 未实现项按 `RuleCoefficientMissing`（NOT_IMPLEMENTED）显式拒绝：CII 仅收录散货/油轮/集装箱三型，其余船型不臆造系数。
- EEXI 的 V_ref 若无试航/模型试验值，只能用立方律近似（`estimated_speed_from_power`），报告中必须标注为近似。
- 本模块为纯闭式算术，无重计算，可在高负载机器上运行。
