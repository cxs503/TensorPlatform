# 标准模型几何一致性闸门 STD-GEOM v1：平台两条湿面积律 + 两种截面代理的机器对撞

**席位**：tp-app-1（applications 席）自主工时 · **单号**：m2057 · **分支**：`agent/m2057` · **日期**：2026-11-01
**EDIT_WHITELIST**：`docs/`, `src/`, `tools/`, `tests/`, `artifacts/`, `examples/`

**产物**
- 判据 + 修复侧参考实现：`src/ship_verification/std_geometry.py`
- 闸门运行器：`tools/verif/geometry/std_geometry_gate.py`（被审计对象 `services.ship_resistance.wetted_surface_axisym`）
- 出图：`tools/verif/geometry/make_std_geometry_figures.py`
- 台账：`artifacts/std_geometry/ledger.json|.md` · 图：`artifacts/std_geometry/fig1..fig4*.png`
- 测试：`tests/test_std_geometry_gate.py` — **29 passed**
- 复跑：`python3 -m tools.verif.geometry.std_geometry_gate --out artifacts/std_geometry`

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

总体性能的第一道物理门是几何：`∇ = Cb·A_section·L` 与 `S = ∮dS`。平台对同一批标准模型并存两套湿面积律、两种截面代理，从未被机器对撞——这正是 owner「几何必须先验证 Cb/L/B」的落点，也是验证欠账台账中 `stdmodels.lookup` 29 条硬欠账的物理内核。

| 名目 | 内容 | 出处（工作树） |
|---|---|---|
| 船型湿面积律 | Holtrop & Mennen (1982)（含 Holtrop 1984 球鼻项） | `backend/api/holtrop1984.py::wetted_surface` |
| 独立第二船型律 | Denny-Mumford (1962) `S = L(1.7T + Cb·B)` | `backend/api/holtrop1984.py::wetted_surface_denny_mumford` |
| **被审计律** | 回转体拟合 `S = πBL(0.5 + 0.25Cb)` | `backend/services/ship_resistance.py::wetted_surface_axisym` |
| 截面代理 A | 方截面 `A = B·T` | `services/ship_resistance.py::displacement_from_cb` |
| 截面代理 B | 圆截面 `A = πD²/4` | 本轮修复侧 |
| 独立第三方法 | 剖面直接积分 `S = 2π∫r√(1+r'²)dx`、`∇ = π∫r²dx` | `src/ship_verification/std_geometry.py` |

| 船/体 | L [m] | B 或 D [m] | T [m] | Cb | Cm | Cwp | 公布/档案 S [m²] | 公布 ∇ [m³] |
|---|---|---|---|---|---|---|---|---|
| KCS | 230.0 | 32.2 | 10.8 | 0.6505 | 0.9846 | 0.7350 | 9494 | 52030 |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 0.8098 | 0.9980 | 0.8548 | 27194 | 312622 |
| DTMB-5415-model | 5.720 | 0.759 | 0.246 | 0.508 | 0.821 | 0.771 | 无独立公布值 | 0.5425 |
| SUBOFF（裸壳） | 4.356 | D=0.508 | — | 0.542 | — | — | 5.42（档案） | 剖面积分 0.7092 |

## ② 怎么做的（方法 / 判据 / 工具链）

判据全部是恒等式、定义式或公布值，逐条机器裁决（容差：接受 3% = owner 精度标准，拒绝 15% = 平台现行判决线）：

| 判据 | 内容 | 类型 |
|---|---|---|
| C1 | `∇ = Cb·L·B·T` vs 公布 ∇ | 定义式 |
| C2 | Holtrop S vs 公布 S | 公布值（律自身精度） |
| C3 | **平台回转体律 S vs 公布 S** | 跨律污染 |
| C4 | 回转体律 / Holtrop 律 − 1 | 跨律分歧（无需公布值） |
| C5 | Denny-Mumford vs 参照 S | 独立第二律 |
| A1 | `B·T` 与 `πD²/4` 的记账差 | 代数恒等式（应触 4/π−1） |
| A2/A3 | 预设定体积（方/圆截面律）vs 剖面积分 ∇ | 独立第三方法 |
| A4 | 档案 S vs 剖面积分 S | 独立第三方法 |
| A5 | 档案 S 是否落在回转体律可达区间 `[0.5,0.75]·πDL` | 区间可达性（布尔） |
| A6 | 回转体律 S vs 剖面积分 S | 跨方法 |

回转体剖面：DARPA SUBOFF 裸壳分三段（首部 1.016 / 平行中体 2.229 / 尾部 1.111 m，Σ = 4.356 m 与总长精确自洽），型线指数族 `p_nose, p_stern ∈ {1.5,2,3}` 共 9 组合全算，报区间 + 标称，不报单点假精度。全流程单进程纯解析积分，实测 < 60 s，未起求解器（load15 ≈ 72 / nproc 128）。

## ③ 计算结果

### 3.1 逐船判决（由 `ledger.json` 注入）

| 船/体 | 形态 | 判决 | 首个失败判据 |
|---|---|---|---|
| KCS | ship | **FAIL** | C2 Holtrop vs 公布 = −6.84% |
| KVLCC2 | ship | **FAIL** | C3 回转体律 vs 公布 = +50.62% |
| DTMB-5415-model | ship | **FAIL** | C4 跨律分歧 = +83.93% |
| SUBOFF | axisymmetric | **FAIL** | A2 预设 ∇ vs 剖面积分 = −14.08% |

### 3.2 湿面积三律对照

| 船/体 | Holtrop [m²] | Denny-Mumford [m²] | 回转体律 [m²] | 公布/档案 [m²] | 回转体律误差 |
|---|---|---|---|---|---|
| KCS | 8845 | 9040 | 15420 | 9494 | **+62.39%** |
| KVLCC2 | 26490 | 26350 | 40960 | 27194 | **+50.62%** |
| DTMB-5415-model | 4.649 | 4.598 | 8.552 | （无独立值，vs Holtrop） | **+83.93%** |
| SUBOFF | — | — | 4.418 | 5.42 | **−18.49%** |

### 3.3 SUBOFF：几何记录与平台自身的律矛盾

- 回转体律在 Cb=0.542 下给 S = **4.418 m²**，比档案 5.42 m² 低 18.49%（A6 FAIL）。
- 回转体律可达上界 `0.75·πDL` = **5.214 m²** < 档案 5.42 m² ⇒ 复现档案值需 **Cb = 1.118 > 1**（A5 FAIL）。
- 剖面积分 `S = 2π∫r√(1+r'²)dx` = **6.0699 m²**（型线族区间 5.8695–6.3236）；档案值比区间下界还低 7.66%（A4 FAIL）。
- 剖面积分 `∇ = π∫r²dx` = **0.7092 m³**（区间 0.6767–0.7510）；预设方截面律 0.6093 m³（**−14.08%**，A2 FAIL）、圆截面律 0.4785 m³（**−32.5%**，A3 FAIL）。
- 两截面代理对同一 Cb=0.542 的记账差 = **27.32%**（= 4/π − 1，恒等式，1e-9 通过）⇒ 不声明截面律，`Cb=0.542` 对回转体无物理含义且不可逆。

### 3.4 为什么 15% 判决看不出来：`Ct` 与 `S` 解耦

用平台**自己**的在环模型 `resistance_breakdown` 实测（`Ct = Cf + Cr`，`Rt = ½ρV²S·Ct`）：

| 船/体 | V [m/s] | S_model [m²] | S_ref [m²] | S 误差 | Ct 误差 | Rt 高估 |
|---|---|---|---|---|---|---|
| KCS | 7.717 | 15420 | 9494 | +62.39% | +0.00% | **+62.39%** |
| KVLCC2 | 7.973 | 40960 | 27194 | +50.62% | +0.00% | **+50.62%** |
| DTMB-5415-model | 1.533 | 8.552 | 4.649 | +83.93% | +0.00% | **+83.93%** |

`Ct 误差 ≡ 0`（到 1e-12）而 `Rt 高估 ≡ S 误差`——判的是无量纲 `Ct`，被污染的是绝对量 `Rt[N]` 与 `S`，这就是欠账长期"看不见"的机理。

## ④ 结果图

| 图 | 内容 |
|---|---|
| `fig1_surface_laws.png` | 四条船/体上三条 S 律对撞（对数纵轴），红柱标注回转体律相对公布值偏差 +50.6%…+83.9%、SUBOFF −18.5% |
| `fig2_suboff_attainability.png` | 回转体律 `S(Cb)` 曲线与其 0.75πDL 上界；档案 5.42 m² 高于上界、剖面积分区间 5.87–6.32 m²——直观显示档案在可达域之外 |
| `fig3_rt_vs_ct.png` | Rt 误差恒落 y=x（≡ S 误差）、Ct 误差恒在横轴 0 处，并标出 15% 判决线 |
| `fig4_suboff_volume.png` | 剖面积分 ∇ 区间 vs 预设两种截面律 ∇ vs 文献 0.7060 m³ |

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. 闸门只裁几何量之间的自洽与可达性，不裁阻力模型的物理精度（后者见 `docs/reports/m1561_*` 的 Ct 口径结论）。
2. SUBOFF 型线指数是族近似，故体积/S 报区间；但结论已钉死：预设值在**区间之外**，不是型线不确定性可以解释的。
3. DTMB-5415 无独立公布 S：工作树里 4.6494 m² 是 Holtrop 相关式自身输出（Denny-Mumford 4.598），拿它当基准会循环论证，故该船只判 C4 跨律分歧与 C1 体积闭合。
4. KCS 上 Holtrop 律自身有 −6.84% 偏差（8845 vs 9494），KVLCC2 上仅 −2.6%：这是相关式适用性误差，不是本轮修复目标，判据分开记、不混谈。
5. **backend 侧未改**：`wetted_surface_axisym` / `displacement_from_cb` 在 `backend/`，不在本单 EDIT_WHITELIST。本单交付判据 + 修复侧参考实现 + 可重跑台账，接线由持该仓权限的单承接。
6. 被审计对象是在环路径（`_call_resistance_api` 默认 `resistance_breakdown`）；`SHIP_BENCHMARK_REMOTE=1` 的远端路径另计，不在本单范围。
7. 容量纪律：开工 load15 ≈ 72（nproc 128），全程单进程无求解器。
8. 曾写过一个"报告自动生成器"，因中英文引号混杂导致语法错误，已删除、未入库——报告数字改为从 `ledger.json` 直读后人工转录（来源可逐个核对），不隐藏这段弯路。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 结果 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| Holtrop S @KVLCC2 | 公布 27194 m² | 26490 m² | −2.59% | ✅ **是** |
| Holtrop S @KCS | 公布 9494 m² | 8845 m² | −6.84% | ❌ 否（相关式适用性） |
| 回转体律 S @KCS | 公布 9494 m² | 15420 m² | +62.39% | ❌ 否 |
| 回转体律 S @KVLCC2 | 公布 27194 m² | 40960 m² | +50.62% | ❌ 否 |
| 回转体律/Holtrop @DTMB-5415 | 比值应为 1 | 1.8393 | +83.93% | ❌ 否 |
| 预设 ∇（方截面律）@SUBOFF | 剖面积分 0.7092 m³ | 0.6093 m³ | −14.08% | ❌ 否 |
| 档案 S @SUBOFF | 剖面积分 6.0699 m² | 5.42 m² | −7.66% | ❌ 否 |
| 档案 S 可达性 | 区间 [1.720, 5.214] m² | 5.42 m²（需 Cb=1.118） | 超上界 +3.95% | ❌ 否（布尔 FAIL） |
| Ct 与 S 解耦 | 恒等式 | 误差 0（1e-12） | 0.00% | ✅ 是 |
| 剖面法 vs 文献 DARPA 裸壳 ∇ | 0.7060 m³（CONF_MED 上下文） | 0.7092 m³ | +0.45% | ✅ 是 |
| 剖面法 vs 文献 DARPA 裸壳 S | 5.988 m²（CONF_MED 上下文） | 6.0699 m² | +1.37% | ✅ 是 |

**显式回答**：修复侧两条船型律对公布值——KVLCC2 −2.59%（<3% 成立）、KCS −6.84%（不成立，如实记）；被审计回转体律套到船型上 +50.62%…+83.93%（远超 3% 与 15%，判 FAIL）；SUBOFF 几何 −14.08% / −7.66% / 超可达域（全部不成立）；而**独立第三方法**对文献 DARPA 值的 +0.45% / +1.37% 均 <3%，说明 FAIL 归因于平台的几何律与记录，不是本轮工具。

**诚实结论**：本单没有修好平台。它证明三件可机器复现的事实：①回转体湿面积律被套在船型上，S 高估 50.6%–83.9%，Rt[N] 同比例高估（Ct 不受影响，故 15% 判决看不见）；②SUBOFF 档案湿面积 5.42 m² 落在该律可达域之外（需 Cb=1.118>1）；③回转体 ∇ 用方截面代理，与圆截面代理差 27.32%，`Cb=0.542` 在两种代理下不可逆。

## 给下次自找活的线索

1. **修复派单**（repo=`ship-performance-platform`，需后端权限）：`wetted_surface_axisym` 加 `form_type` 分派（ship → Holtrop/Denny-Mumford；axisym → 剖面/圆截面），`displacement_from_cb` 加 `section="circular"`；验收门 = 本单 4 条 FAIL 转 PASS + `tests/test_std_geometry_gate.py` 29 绿。
2. 台账 delta：`python3 -m tools.verif.geometry.std_geometry_gate --out artifacts/std_geometry` 可 24 h 重跑，判决翻转即清偿证据。
3. 下一个未动欠账：`calc.resistance` 45 条硬欠账中的 STUB/MOCK 4+4 条；`stdmodels.lookup` 剩余 WITHHELD 25 条（本轮只清了其几何内核）。
