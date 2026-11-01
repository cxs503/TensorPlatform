# 几何/静水力域验证欠账核销（geometry & hydrostatics verification audit）

**单号** m1583（tp-ver-2 自主工时）｜**分支** agent/m1583｜**证据等级判定**：`geometry.*` → **MOCK**（原自述 REAL）

---

## ① 案例背景与对象

平台规则第 2 条写明「几何必须先验证 Cb/L/B」，而能力台账（报告 1148）中 6 项 geometry 能力
（`geometry.generate / hydro_curves / list_models / verify_official / shiphull.generate / stdmodels.lookup`）
此前**全部 UNVERIFIED**，且台账判定「几何域有审计、无条目」是三大覆盖缺口之一。
本单不新增物理算法，只做**验证欠账核销**：把「几何自述 REAL」变成可机器复算的门禁判决。

被审对象（`:8001`，实时探活，非纸面）：

| 端点 | 平台自述 |
|---|---|
| `GET /api/v1/geometry/{name}` | 主尺度 L/B/T/Cb + 站面 profile |
| `GET /api/v1/geometry/offsets/{name}` | 21 站 × 11 水线型值表（半宽按 B/2 归一） |
| `GET /api/v1/geometry/hydro-at/{name}?draft=d` | 单吃水静水力（Cb/Awp/LCB/VCB/∇/TPC/MTC/湿表面积） |
| `GET /api/v1/geometry/verify/{name}` | 生成型线 vs CAD 参考的自检 |

基准船型（主尺度与 Cb 均取有出处的公开值）：

| 型 | L (m) | B (m) | T (m) | Cb 文献/解析 | 出处 |
|---|---|---|---|---|---|
| KCS | 230.0 | 32.2 | 10.8 | 0.6505 | KRISO KCS（SIMMAN/T2015 官方主尺度） |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 0.8098 | MOERI KVLCC2（SIMMAN 2008） |
| Wigley | 1.0 | 0.1 | 0.0625 | 4/9 = 0.4444 | 抛物型船解析：y=(B/2)(1−(2x/L)²)(1−(z/T)²) |

文献项**只登记 Cb**（L/B/T 与之同源）。Cm/Cp 公开分解值本单不臆造，相关判据改由**同一型值表上
独立积分出的 Cm/Cp 做恒等式自检**（Cb = Cm·Cp）。

---

## ② 怎么做的（方法链，先证工具再判平台）

1. **解析自检先行**：积分器（复合 Simpson 1/3，偶数点末段退化梯形）先对**闭式解**收敛——
   箱型驳 Cb=Cm=Cp=Cw=1、Wigley 抛物型 Cb=4/9、Cp=Cw=2/3（本例取型线 u=(1−(2ξ−1)²)(1−ζ²/4) 得
   Cb=(2/3)(11/12)=0.6111 的等价闭式）、棱柱驳 Cb=2/3、浮心 ξ̄=0.5。**8/8 通过（误差 <1e-9）**。
   若自检不过，工具拒绝出判决——避免「用坏尺子量船」。
2. **独立复算**：不采信平台的 Cb 汇总标量，直接取型值表 (21×11)，二维 Simpson 自行积分
   Cb=∫∫u dζdξ、Cw=∫u(ξ,1)dξ、Cm=∫u(½,ζ)dζ、Cp=Cb/Cm、ξ̄=∫∫ξu/∫∫u，以及各站剖面面积曲线 A(ξ)。
3. **多源对账**：四源并列——声明值（/geometry）· 文献/解析基准 · 服务端静水力（hydro-at）·
   独立积分（offsets）。同一几何的四个数字必须收敛，否则即「同一几何两说」。
4. **吃水参数活性**：按各型设计吃水的 0.2/0.4/0.6/0.8/1.0/1.1 倍共 6 档扫掠，检查 ∇(d) 单调且
   Cb(d) 随吃水变化（真实船体 Cb 必随吃水上升，恒定为假）。
5. **七道门禁**（`tools/verifier/geometry_audit.py`，任一核心门不过即 exit 1，非零退出码可挂 CI）：
   G1 Cb 物理可容许 0<Cb≤1｜G2 声明 Cb vs 文献 ≤15%｜G3 服务端 Cb vs 文献 ≤15%｜
   G4 吃水参数活性｜G5 恒等式 Cb=Cm·Cp ≤3%｜G6 自检端点非空转（stations>0 且 max_err≤15%）｜
   G7 型值表积分 vs 服务端 Cb ≤3%。

工具链：`tools/verifier/geometry_probe.py`（原样抓快照）→ `geometry_audit.py`（判决+JSON 工件）→
`geometry_figures.py`（出图）→ `pytest tests/test_geometry_audit.py`（11 项，含 6 个门禁变异体）。

---

## ③ 计算结果（全部由程序从工件读出，零手抄）

判决摘要：**解析自检 8/8 PASS；门禁 7 项中 5 项 FAIL**。

| 型 | 声明 Cb | 文献 Cb | 服务端 Cb (hydro-at) | 独立积分 Cb | 服务端 ∇ (m³) |
|---|---|---|---|---|---|
| KCS | 0.651 | 0.6505 | **1.1949** | 0.7059 | 95 574.7 |
| KVLCC2 | 0.81 | 0.8098 | **1.4441** | 0.7803 | 557 487.5 |
| Wigley | 0.444 | 0.4444 | **0.7274** | 型值表空 | **0.0** |

门禁表：

| 门 | 判据 | 结果 | 证据 |
|---|---|---|---|
| G1 Cb 物理可容许 | 0<Cb≤1 | **FAIL** | KCS 1.1949、KVLCC2 1.4441（Cb>1 意味着截面面积之和大于 L·B·d，几何或积分仿射错误） |
| G2 声明 Cb vs 文献 | ≤15% | PASS | KCS +0.08%、KVLCC2 +0.02%、Wigley +0.10% |
| G3 服务端 Cb vs 文献 | ≤15% | **FAIL** | KCS +83.7%、KVLCC2 +78.3%、Wigley +63.7% |
| G4 吃水参数活性 | ∇(d) 单调且 ≥3 个不同值 | **FAIL** | KCS ∇ 严格正比于 d（19 114.9@0.2T … 95 574.7@1.0T），Cb 恒为 1.1949；Wigley ∇≡0 |
| G5 恒等式 Cb=Cm·Cp | ≤3% | PASS | 两型 0.00%（型值表自洽） |
| G6 自检端点非空转 | stations>0 且 max_err≤15% | **FAIL** | KCS `stations_checked=0, results=[]`（空转）；KVLCC2 4 站 avg 23.3% **max 141.1%** |
| G7 型值表 vs 服务端 Cb | ≤3% | **FAIL** | KCS 69.3%、KVLCC2 85.1%（同一船型两条几何路径互不相认） |

**根因线索（只报观测，不越界改别人仓库）**

1. `/geometry/verify/KVLCC2` 自己给出的 `gen_halfbreadth` 在 ξ=0.3/0.5/0.7 三站**恒为 29.0 m = B/2**，
   而 CAD 参考为 89.8/12.0/23.9 m ⇒ 生成器在这几站退化为**等宽矩形截面**，与端部型线无关。
2. 静水力积分因子：`_section_props` 注释声明「返回 ×2 为全截面」，若 profile 已含双舷则面积翻倍；
   KCS 实测 ∇ 恰为正解 52 027 m³ 的 1.837 倍，Cb 被同比例放大。
3. 型值表 ζ=0 列（龙骨水线）**全站恒为 0**（`zeta0_all_zero=True`），物理上平底船不可能——该列退化，
   使 Cm 独立积分仅 0.8208，而 KCS 由公开 Cb/Cp 反算 Cm=0.9849（−16.7%）。
4. Wigley（L=1 m，缩尺型）∇≡0.0 而 Cb=0.7274 同时成立 ⇒ 体积与 Cb **内部不自洽**，且量级相关（小尺度丢量）。

---

## ④ 图

- `artifacts/geometry_audit/fig1_Cb_sources.png`：四源 Cb 对比柱图 + Cb=1 物理界。服务端两型柱越过物理界。
- `artifacts/geometry_audit/fig2_station_areas.png`：由型值表独立积分的各站剖面面积曲线 A(ξ)/(B·T)
  + Wigley 解析曲线 1−(2ξ−1)²，可见端部形状与中体丰满度。

---

## ⑤ 注意点（踩坑/局限/适用范围）

- **不自欺的第一原则**：本单先写解析自检，第一版曾把 Wigley 的 Cw 期望值写成 2/3（错，应为 (2/3)(3/4)=0.5），
  是**自检抓住了自己的错**——这正是「先证尺子再量船」的价值，已固化为变异体测试。
- 文献 Cm/Cp 未登记，故「Cm 误差 −16.7%」一条以**公开 Cb/Cp 反算**为依据，标注为派生值，不作独立基准。
- `/geometry/offsets` 的 ξ 方向（0=AP 还是 0=FP）平台未声明，故 ξ̄ 只报相对位置，不与 LCB 直接比对；
  LCB 裁决以 magnitude 观察（KVLCC2 LCB_from_fp=166.3 m=51.96%L，即中前 1.96%L）记录，不下判决。
- DTMB5415 / Series60 / barge 在 `:8001` 上 404（`/geometry/{name}` 不可达但 `/offsets` 仍返回通用表），
  说明 offsets 端点对未知船名**静默回落通用型值表**——本条已记入报告，未纳入门禁（避免误判）。
- 本单仅做**只读审计 + 参考实现**；被审代码位于 `ship-performance-platform`（不在本仓白名单），
  修复需另派单，故不在此改一行别人的源码，也不声称已修复。

---

## ⑥ 误差判定（逐项对 3% 严档回答）

| 关键量 | 基准 | 相对误差 | <3%? |
|---|---|---|---|
| KCS 声明 Cb | 0.6505（KRISO） | +0.08% | **是** |
| KVLCC2 声明 Cb | 0.8098（MOERI） | +0.02% | **是** |
| Wigley 声明 Cb | 4/9（解析） | +0.10% | **是** |
| KCS 服务端 Cb | 0.6505 | **+83.7%** | 否（远超 15% 门） |
| KVLCC2 服务端 Cb | 0.8098 | **+78.3%** | 否 |
| Wigley 服务端 Cb | 0.4444 | **+63.7%** | 否 |
| KCS 型值表独立 Cb | 0.6505 | +8.5% | 否 |
| KVLCC2 型值表独立 Cb | 0.8098 | −3.6% | 否（刚越线） |
| KCS ∇（服务端） | 52 027 m³（=Cb·L·B·T） | **+83.7%** | 否 |
| KCS Cm（型值表） | 0.9849（公开 Cb/Cp 反算） | **−16.7%** | 否 |
| 解析自检 8 项 | 闭式解 | <1e-9 | **是** |

结论：几何域**主尺度与声明 Cb 可信**（<0.1%），但**由几何推导出来的静水力不可信**（Cb 越过物理界 +63.7%~+83.7%，
∇ 同比例放大，自检端点空转/报 141% 却不拦截）。按平台证据分级，`geometry.*` 由自述 REAL 降为 **MOCK**
（管线在跑、返回结构真实，但数值不满足物理可容许性），需按本单门禁修复后才能回升。

---

## 复现

```bash
python3 tools/verifier/geometry_probe.py                    # 抓快照 → artifacts/geometry_audit/raw_probe.json
python3 tools/verifier/geometry_audit.py --write            # 实时判决（实时探 :8001）
python3 tools/verifier/geometry_audit.py --offline --write  # 用快照复现判决（无网络）
python3 tools/verifier/geometry_figures.py                  # 出图
python3 -m pytest tests/test_geometry_audit.py -q           # 11 passed
```

工件：`artifacts/geometry_audit/{raw_probe.json, geometry_audit.json, fig1_Cb_sources.png, fig2_station_areas.png}`

## 后续（建议派单，本单不越界实现）

1. **修静水力积分**：`api/hydrostatics.py::_section_props` 的双舷因子与截面生成（`generate_hull_sections`
   在 ξ=0.3/0.5/0.7 返回 B/2 等宽）二选一为真凶；判据用本单 G1/G3/G7 三门（KCS Cb 目标 0.6505 ±3%）。
2. **型值表 ζ=0 列退化**：平底船龙骨水线宽度非零，需与 generate 的型线同源导出。
3. **自检端点接门**：`/geometry/verify` 的 `stations_checked=0` 必须报错而非 200；`max_error>15%` 必须非 200。
4. **量级无关性**：Wigley L=1 m 的 ∇≡0 说明积分存在单位/尺度假设，需加缩尺回归（L/2、2L 应同 Cb）。