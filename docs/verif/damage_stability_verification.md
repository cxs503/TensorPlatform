# 破舱稳性域验证欠账清偿 v1：D5 证据分级（MOCK）

**对象**：`:8001` ship-performance-platform，`backend/api/damage_stability.py`，三个端点
`POST /api/v1/damage-stability/analyze`、`POST /api/v1/damage-stability/probabilistic`、
`GET /api/v1/damage-stability/imo-criteria`。

**结论**：破舱稳性域此前**从未被分级**（8 篇域级验证报告覆盖阻力/推进/耐波/操纵/稳性/结构/
振动噪声/绿色航行，破舱稳性为空白）。本轮以 19 个只读探针 + 8 条可纸面复算判据定级 **MOCK**
（8 项判据 2 通过 6 失败）。

## 1. 判据与结果

| 判据 | 量 | 平台 | 参考（解析/规范） | 偏差 | 判定 |
|---|---|---|---|---|---|
| J1 | 破损终吃水（盒式驳船 L100/B20/T6，舱 V=2000 m³、H=6、μ=0.95、TCG=+8） | 6.301 m | 7.1287 m（损失浮力 t'=∇0/(Aw−μA_c)） | **−11.6 %** | FAIL（>3 %） |
| J2 | 同工况横倾角 | 19.33° | ≥6.749°（tanθ≥M_T/(Δ_t·GM_max) 下界） | 在界内 | PASS |
| J3 | 破损范围 ×10（10×5×6 → 100×20×60 m）后 7 项输出 | **逐项完全不变** | 几何必须决定损失浮力 | 定性 | FAIL |
| J4 | μ 0.95→0.50 对终吃水 | 6.301→6.162 m | 7.1287→6.5455 m | 趋势一致、量值偏高 | PASS（仅趋势） |
| J5 | GZ 峰值角随 ship KG 变化 | 45.0°（GM>0）/ 0.0°（GM<0） | 由 GM 与干舷决定，因船而异 | 形状签名固定 | FAIL |
| J6 | 要求分舱指数 R(Ls)，Ls=100/150/200/250 | 恒 **0.9** | 0.4921 / 0.5762 / 0.6364 / 0.6816（SOLAS 2009 Reg.6，R=1−128/(Ls+152)） | **+82.9 %（Ls=100）**，最轻 +32.0 %（Ls=250） | FAIL（>3 %） |
| J7 | 达到分舱指数 A=Σp·s 的 s 来源 | A ≡ p·s（用户直接给 s），且换 KG=4.0 与 5.5 两条船 A 完全相同 | s 须由 GZ 曲线按 Reg.7.2 计算 | 纯回声、与稳性无关 | FAIL |
| J8 | `survival_probability` 取值 | 四组工况全部 0.5（代码 `1.0 if imo_compliant else 0.5`） | Reg.7.2 的 s∈[0,1] 连续函数 | 二值伪造 | FAIL |

**误差判定（显式回答）**：关键量 J1 偏差 −11.6 %、J6 最坏 +82.9 %，**均不满足 <3 %**；
J7/J3/J5 属「无物理计算」类缺陷，偏差不可用百分比表达（参考量根本未参与计算）。

## 2. 失败机理（读码 + 探针互证）

1. **破损 GZ 为经验式而非几何积分**：`calculate_damage_GZ` 返回
   `GM·sinθ·(1−0.5θ²)`（θ 弧度），与舱室几何、破损位置、进水分布全无关；其数学峰值角在
   ~45°（对 GM 归一后为常数），故 J5 的「峰值角与船无关」是必然签名。
2. **破损几何未进入计算**：`damage_extent`（length/width/height）只被 `location` 字符串之外
   的字段读取一次也未影响输出——J3 中范围放大 10 倍，`final_draft/final_trim/final_heel/
   flooded_volume/GM_damage/GZ_max/area_GZ` 7 项全等。
3. **R 公式写了另一套规范**：源码 `R = min(0.5·Ls/100+0.5, 0.9)`，非 SOLAS 2009 Reg.6；
   对货船 Ls≥80 m 该式恒 >1 被截断成常数 0.9，等效于对 100–250 m 船一律要求 A≥0.9。
4. **s 未计算**：`ProbabilisticInput.damage_zones` 由调用方直接给 `survival`，
   `A = Σ p·s` 为纯乘积（J7 回声检验通过，`p=0.3,s∈{0,0.5,1}` → A∈{0,0.15,0.30} 精确线性）。
5. **生存概率二值化**：`survival_probability = 1.0 if imo_compliant else 0.5`，与 s 无关。
6. **衡准集与 SOLAS 2009 不同源**：`freeboard > 0.3 m` 不是 Reg.7 的判据（Reg.7 判的是
   开口浸没/残存干舷与 s 因子阈 GZmax≥0.1 m、Range≥20°、面积项），属规范错配（记录在案，
   不作定量误差）。

## 3. 工件与复现

```
artifacts/verif/damage_stability/
  solas_reference.py           独立参考实现（R1 SOLAS Reg.6 / R2 Reg.7.2 s / R3 损失浮力解析）
  probe_damage_stability.py    19 个只读探针 → evidence.json（原始请求/响应）+ verdict.json + 3 图
  evidence.json                逐条 HTTP 留痕（path/body/http/resp）
  verdict.json                 8 条判据 + 相对误差 + 等级 MOCK
  fig_R_vs_solas.png           R(L_s) 平台 vs SOLAS + 偏差曲线
  fig_gz_heuristic.png         平台破损 GZ 经验式族 + 峰值角恒 45° 的船无关性
  fig_deviation.png            逐项相对误差 vs ±3 % 验收带
tests/verif/test_damage_stability_verdict.py   16 项测试（参考数学 5 + 工件完整性 3 + 缺陷锁 7 + 图 1）
```

复现：`python3 artifacts/verif/damage_stability/probe_damage_stability.py` 后
`python3 -m pytest tests/verif/test_damage_stability_verdict.py -q`（16 passed）。
缺陷锁测试从**落盘证据**复算（不手抄数字），平台一旦修复即失败，形成「修复即需复核」闸门。

## 4. 局限与适用范围

- 参考实现 R3 为**盒式驳船、矩形舱、自由注水、小角**解析，不含纵倾耦合与舱室非矩形几何；
  因此 J1 只作量级判据（−11.6 % 已远超 3 % 阈值，不依赖高阶修正）。
- J2 只判「下界」，平台 19.33° 大于下界 6.749°，故该项 PASS——横倾量值本身未被证实正确
  （其来源是体积矩的粗估，未与损失浮力平衡解对拍）。
- R2 的 s 公式取单区、K=1、无交叉注水、无开口浸没项（Reg.7.2 完整式含 K 与开口项），
  本域尚未实现 s，故只作「应有形式」的参照，不构成对平台数值的逐点对拍。
- 未改动 ship-performance-platform 源码（超出本单仓库白名单）；建议另派修复单：
  F1 换 Reg.6 的 R 公式；F2 由 GZ 曲线按 Reg.7.2 计算 s（删除用户直传）；F3 用损失浮力法
  真实几何求 draft/trim/heel/GZ；F4 删除二值 `survival_probability`；F5 衡准集对齐 Reg.7。
---
**发布**：进展报告 id=1093（figures: 3）；修复单 **mission 1507**（FIX-DAMAGE F1~F5，runner=native）已派。
