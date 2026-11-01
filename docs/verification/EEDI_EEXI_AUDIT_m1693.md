# EEDI / EEXI 验证欠账审计与修复（m1693 · tp-ver-7 verifier 席）

**EDIT_WHITELIST: backend/services/, backend/tests/, docs/, artifacts/, tools/, agent_work/**

证据分级：`backend/services/propulsion.py:attained_eedi` 修复前 = **REAL 但含公式错位缺陷**；
`backend/services/polar/cii.py` 的 EEXI 侧修复前 = **REAL 不完整（缺 required/合规判定）**；
绝对量级对标实船 EEDI（船队/试航数据）在本仓不可得 = **WITHHELD**；
EEXI 船型折减系数表（MEPC.328(76)）未硬编码（无法离线复核）= **WITHHELD**（见 §5）。

## 1. 案例与对象

绿色航行能效框架源码审计，不涉几何。基准算例（case ship）为 80 000 DWT 散货船：

| 参数 | 值 | 来源 |
|---|---|---|
| capacity (DWT) | 80000 t | 本文算例 |
| V_ref | 14.5 kn | 本文算例 |
| P_ME | 9000 kW @ SFC 170 g/kWh | 本文算例 |
| P_AE / SFC_AE | 600 kW / 190 g/kWh | 本文算例 |
| C_F (HFO) | 3.114 | MEPC.364(79)（`polar/constants.py`） |
| C_F legacy | 3.1144 | 部分调用方仍在传 |

## 2. 方法与发现的三个缺陷

对照 `attained_eedi` 自身 docstring 与 MEPC.308(73) 式

`EEDI = [(∏f_j)(Σ P_ME C_F SFC_ME) + P_AE C_F SFC_AE − Σ f_eff P_eff C_F SFC_ME] / (f_i f_c f_l Capacity f_w V_ref)`

逐项手写闭式复算（不经库函数），发现：

- **D1（公式错位）**：`numerator = f_i*(me+ae) - eff`，而 `f_i` 只应出现在分母。自身 docstring 与代码矛盾。
- **D2（默认值张冠李戴）**：`f_w` 缺省被写成 `f_i`（冰级容量修正），而 `f_w` 是 MEPC.1/Circ.850 的失速/天气系数，缺省应为 1.0。
- **D3（能力缺口）**：EEXI 只有 attained，没有 required 与合规判定，API 层亦未暴露 —— 无法回答「该船 EEXI 是否合规」。
- 附带观察：`propulsion` 与 `polar.cii` 两路 attained EEDI 的 C_F 来源不同（3.1144 vs 3.114），跨实现比对时必须统一传入，否则存在 1.28e-2 % 的假分歧。

D1 与 D2 **互相掩盖**：缺省时 `f_i` 在分子分母各出现一次而抵消，看似「f_i 生效」；只有显式给 `f_w` 或有创新能效项时才暴露。

## 3. 缺陷量级（程序化读自 `artifacts/m1693/eedi_eexi_audit.json`）

两种独立暴露方式下的修复前相对误差（正=偏高）：

| f_i | (a) 显式 f_w=1.10、无 E_eff | (b) 缺省 f_w + 20% 创新能效 |
|---|---|---|
| 1.0 | +0.000 % | +0.000 % |
| 1.1 | +10.000 % | +2.079 % |
| 1.2 | +20.000 % | +3.812 % |
| 1.3 | +30.000 % | +5.278 % |
| 1.4 | +40.000 % | +6.534 % |
| 1.5 | +50.000 % | +7.623 % |

判据（>15% 不可接受，本单按 >3% 从严）：方式 (a) 误差 = (f_i − 1)，f_i=1.3 时 **+30.0%**，超 15% 判据；
方式 (b) f_i=1.3 时 **+5.278%**，超 3% 误差门。

## 4. 修复与验证

修复（`backend/services/propulsion.py`）：`numerator = me + ae - eff`；`f_w` 缺省 = 1.0 并补文档。
补齐（`backend/services/polar/cii.py`）：新增 `required_eexi(category, dwt, reduction_pct)` 与
`eexi_status(...)`（attained/required 比、余量 %、compliant 布尔），折减系数由调用方显式给定。

验证：`backend/tests/test_eedi_eexi_verification.py`（23 用例，全部绿）——
手写闭式对齐（rel 1e-12）、`f_i` 仅进分母、`f_w` 缺省独立性、创新能效项只被除一次、
缺陷量级可复现、双实现一致性、EEXI 75% 载荷与合规代数。
**回归证明**：把修复回退后同一套测试 **5 条 FAIL**，恢复后全绿 —— 测试确实抓得住该缺陷。

基线数值：attained EEDI = 4.413290 g CO2/(t·nm)（C_F=3.114）；
legacy C_F=3.1144 时 4.413857；两路实现相对差
0.000e+00%（C_F 统一）/ 0.012845%（C_F 不统一）。

EEXI（同算例）：attained = 3.386475 g CO2/(t·nm)，与仅主机 attained EEDI 之比
= 0.750000000000（应为 0.75，闭式核对通过）。
合规边界随折减系数变化：

| 折减 r [%] | required EEXI | 余量 [%] | 合规 |
|---|---|---|---|
| 0 | 4.4087 | +23.186 | 是 |
| 10 | 3.9678 | +14.651 | 是 |
| 15 | 3.7474 | +9.630 | 是 |
| 20 | 3.5269 | +3.982 | 是 |
| 25 | 3.3065 | -2.419 | 否 |
| 30 | 3.0861 | -9.735 | 否 |
| 35 | 2.8656 | -18.176 | 否 |
| 40 | 2.6452 | -28.024 | 否 |

## 5. 注意点与局限

1. **不可对标的部分**：本仓无船队/试航数据，attained EEDI 的**绝对量级**只能对齐公式闭式，
   不能声明「与实船一致」→ 该维度 WITHHELD，未计入 PASS。
2. **EEXI 折减系数表未硬编码**：MEPC.328(76) 的船型折减系数无法在本仓离线复核，
   故 `required_eexi` 要求调用方显式传入并文档标注，避免把记忆值伪装成平台常数。
3. EEXI 的 `P_AE` 采用调用方给定值（本模块是简化式，未实现 MEPC.333(76) 的分船型 P_AE 定义）。
4. 参考线系数（MEPC.203(62)）只做了量级括号与幂律维度检验（DWT 加倍 ⇒ 缩放 2^-c），系数本身未重推。
5. 修复会改变任何传入 `f_i`/`f_w` 的调用方结果（缺省 f_i=f_w=1 的调用不变），属行为变更，已在 docstring 注明。

## 6. 工件

- 审计数据：`artifacts/m1693/eedi_eexi_audit.json`（报告与本文所有数字均由此读出）
- 图：`fig1_defect_materiality.png`（缺陷量级 vs f_i）、`fig2_eedi_reference_lines.png`（参考线+三阶段+算例船）、`fig3_eexi_compliance.png`（EEXI 合规边界）
- 复算脚本：`tools/audit_eedi_eexi_m1693.py`；回归测试：`backend/tests/test_eedi_eexi_verification.py`
