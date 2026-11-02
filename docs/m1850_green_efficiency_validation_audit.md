# m1850 · 绿色航行（EEDI/EEOI/CII）端点验证欠账独立审计：7/7 IMO 判据 FAIL ⇒ 证据级 MOCK

**任务** tp-impl-6 自主工时单（2026-11-01；load15 ≈ 122 ≥ 100 → 只做轻量「验证欠账」活：纯解析闭式求值 + HTTP 探针 + 测试 + 作图，未起 CFD/DEM 重算）。
**被审端点** `POST /api/v1/extended-calculations/energy`（:8001，tag「扩展计算」，文档串写「能效计算 - EEDI/EEOI/CII」）
**被审源码** `/root/ship-performance-platform-incoming/ship-performance-platform/backend/api/extended_calculations.py`（sha1 `71f8df69b94723bd027f42ad7cc060f7499103c7`）
**平台报告** id **1613**（published，本单发布）
**探针时刻** 2026-11-02T01:33:13.841721+00:00，端点可达 = True，4/4 案例 HTTP 200
**产物** `artifacts/m1850/{green_efficiency_audit.py, green_efficiency_reference_fix.py, audit_green_efficiency.json, probe_platform_energy.json, probe_sensitivity.json, acceptance_gate.json, fig_g1..g4.png}`、`tests/test_m1850_green_efficiency_audit.py`、本文档

---

## 1. 案例介绍（物理背景 / 对象 / 参数）

绿色航行域的三个法定指标全部是**闭式代数**，因此可以对标 IMO 决议逐位复核，不需要实验数据——这使它们成为最廉价、判别力最强的验证欠账靶点：

- **EEDI**（能效设计指数，MEPC.203(62) 强制）：达到值 = P_ME·C_F·SFC_ME/(capacity·V_ref)，单位 g CO2/(t·nm)；要求值 = 参考线 a·DWT^(-c) ×(1−RF)，RF 按建造年份取 0/10%/20%/30%（MEPC.324(75)：Phase 3 自 2025-01-01）。
- **EEOI**（营运能效，MEPC.1/Circ.684）：Σ(F_C·C_F)/(m_cargo·D)，必须由**实测航次油量与货量**驱动，是唯一能反映装载率/航程的指标。
- **CII/AER**（碳强度，MEPC.346(78) 自 2023 强制）：AER = 年 CO2[g]/(DWT·年距离[nm])，与 CII 参考线（MEPC.353(78)）× (1−Z 年折减) 比较；Z 2023..2026 = 5/7/9/11%。

审计案例取 4 个公开量级的船型（干散×2、油、箱），参数表如下（数字由工件注入）：

| 案例 | 船型 | DWT [t] | V [kn] | P_ME [kW] | SFC [g/kWh] | 年份 | 燃油 |
|---|---|---|---|---|---|---|---|
| KVLCC2_tanker | tanker | 300000 | 15.5 | 27800.0 | 180.0 | 2026 | HFO |
| Capesize_bulk | bulk_carrier | 180000 | 14.5 | 20000.0 | 175.0 | 2026 | HFO |
| PostPanamax_container | container | 140000 | 22.0 | 60000.0 | 190.0 | 2026 | HFO |
| Handysize_bulk | bulk_carrier | 35000 | 14.0 | 8000.0 | 180.0 | 2026 | HFO |

**几何前置校验说明（平台精度标准要求）**：本审计的被测量是能效代数，不涉及阻力/耐波流场，故 Cb/L/B 一致性校验不构成前提；反过来，审计恰好证明**几何入参在该端点里完全未参与运算**（见 §3 判据 G7：L/B/T/Δ/Cb 五个必填字段 +20% 后输出逐位不变）。

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **只读定位源码**：从 `pgrep -f uvicorn` → `/proc/<pid>/cwd` 找到 :8001 的实际进程目录（`ship-performance-platform-incoming/.../backend`），逐行读 `calculate_energy_efficiency`（L228–317），把每条分支抄成可判方程。
2. **独立参考核**（`green_efficiency_audit.py`）：按 IMO 决议闭式实现 EEDI 参考线族（8 船型）、阶段 RF、达到 EEDI（C_F 按燃油）、AER、CII 参考线（8 子类）与 Z 因子。零拟合、零平台依赖。
3. **端点行为重建**：把源码逐字复刻为 `platform_baseline/eedi/eeoi/cii/rating` 五个纯函数，再与实测 fixture 逐位对齐（`abs ≤ 0.01`）——既证明「我读到的是活进程跑的代码」，也让每条判据可离线复算。
4. **7 条机器判据**（G1–G7），每条自带负控制（篡改后必须翻面）：
   - G1 参考线随船型变化；G2 合规判定用当年 Phase RF；G3 EEOI 与 EEDI 不得代数同一；G4 CII 量纲与评级敏感性；G5 C_F 可按燃油声明；G6 知识库/代码/IMO 三方一致；G7 必填入参必须参与计算。
5. **实测探针**：4 案例主探针 + 9 次单入参 +20% 扰动 + 4 次几何整形变体（共 17 次 POST，纯公式求值，无重算力）。
6. **修复侧交付**：`green_efficiency_reference_fix.py` 给出 IMO 一致的 v2 实现（新增 ship_type/build_year/fuel_type/annual_hours/cargo_fraction/voyage_fuel_t/cargo_mass_t 七个可声明入参 + 诚实字段 `ship_type_assumed`/`eeoi_basis`/`limitations`），并落盘机器可读验收闸门 `acceptance_gate.json`（GATE_GREEN_M1850，相对容差 2%）。
7. **工具链**：Python 3.11 + NumPy/Matplotlib(Agg) + pytest；全部数字由脚本写 JSON，文档零手抄。

## 3. 计算结果

### 3.1 EEDI：参考线与合规判定

| 案例 | 平台 EEDI | IMO 达到 EEDI(HFO) | 平台 baseline | IMO 参考线 | baseline 误差 | IMO 2026 要求 | 平台判合规 | IMO 判合规 |
|---|---|---|---|---|---|---|---|---|
| KVLCC2_tanker | 3.450 | 3.351 | 2.35 | 2.589 | -9.2% | 1.812 | 不合规 | 不合规 |
| Capesize_bulk | 4.300 | 4.176 | 2.99 | 2.994 | -0.1% | 2.096 | 不合规 | 不合规 |
| PostPanamax_container | 11.870 | 11.526 | 3.38 | 16.096 | -79.0% | 11.267 | 不合规 | 不合规 |
| Handysize_bulk | 9.420 | 9.151 | 6.54 | 6.540 | +0.0% | 4.578 | 不合规 | 不合规 |

平台 `eedi_baseline` 与散货船线（961.79·DWT^-0.477）逐位吻合，与油船/箱船线误差 -9.2% / -79.0%；同时 `eedi_compliance` 与 RF=0 门槛比较，散货船门槛被低估 +42.6%。判决翻转扫描（4 案例 × 9 功率点 = 36 点）：**假放行 6 点、假拒收 6 点，翻转率 33%**。

### 3.2 CII：量纲缺陷与评级恒 A

| 案例 | 平台 CII（2dp 返回值） | 平台 CII（未舍入重建） | IMO AER | IMO 2026 要求 CII | 低估倍数 | 平台评级 | IMO 判定 |
|---|---|---|---|---|---|---|---|
| KVLCC2_tanker | 0.00 | 0.001863 | 3.351 | 2.129 | 1798.9× | A | NON-COMPLIANT |
| Capesize_bulk | 0.00 | 0.002321 | 4.176 | 2.274 | 1798.9× | A | NON-COMPLIANT |
| PostPanamax_container | 0.01 | 0.006407 | 11.526 | 5.376 | 1798.8× | A | NON-COMPLIANT |
| Handysize_bulk | 0.01 | 0.005087 | 9.151 | 6.298 | 1798.8× | A | NON-COMPLIANT |

解析化：源码 `annual_distance = V*1.852*h/1000` 把 km 当「千海里」，且年 CO2 用吨而非克，两处合并使 **CII_platform ≡ EEDI/1852**（本文用 4 案例逐位验证，残差 < 1e-12）。于是 CII 恒 < 4 → 评级恒 A；288 点（功率 0.5–3×、SFC 0.8–1.5×、DWT 0.5–2×）网格扫描**只出现 1 个等级（['A']）**，要触发 B 级需把 CII 放大 69 倍，物理不可达。四个案例 IMO 判定全部 NON-COMPLIANT（比值 1.45–2.14），平台却给「最环保 A 级」——**判决方向相反**。

### 3.3 EEOI：与 EEDI 代数同一 + 对航次状态零响应

源码 `fuel_used = P·SFC/1e6·(D/V)`、`eeoi = fuel_used·C_F·1e6/(DWT·D)`，航程 D 精确抵消 ⇒ EEOI ≡ EEDI（实测比值 1±3.0e-04）。再叠加输入侧缺口：schema 无货量/航程字段，故装载率 25% 时真实 EEOI 已是设计值的 4 倍，平台值**分毫不动**（最差偏差 +75%）。

### 3.4 判据总表

| 判据 | 内容 | 判决 | 关键量 |
|---|---|---|---|
| G1 | G1_EEDI参考线必须随船型变化 | **FAIL** | 船型盲：箱船 baseline 误差 -79.0%，最差 -79.0% |
| G2 | G2_合规判定必须用当年适用阶段（2026→RF=30%） | **FAIL** | 阶段缺失：门槛低估 +29.7%~+42.6%；36 点扫描翻转 6 假放行 / 6 假拒收 |
| G3 | G3_EEOI必须与EEDI独立 | **FAIL** | EEOI/EEDI 比值最大偏离 1 为 3.03e-04；装载率 25% 时真实 EEOI 偏差 -75% |
| G4 | G4_CII量级与评级必须对输入敏感 | **FAIL** | CII 低估 1799×；288 点网格评级恒 ['A'] |
| G5 | G5_C_F必须可按燃油种类声明 | **FAIL** | C_F 恒 MGO 3.206：HFO 船 EEDI 高估 2.95% |
| G6 | G6_知识库·代码·IMO 三方必须一致 | **FAIL** | 知识库/代码/IMO 三方最差分歧 83.0% |
| G7 | G7_必填入参必须参与计算（不得装饰） | **FAIL** | 必填入参 5/9 装饰（length, beam, draft, displacement, cb） |

### 3.5 知识库 · 代码 · IMO 三方分歧（G6）

| 船型 | DWT [t] | 知识库 part2 | 平台代码 | IMO | 知识库 vs IMO | 代码 vs IMO |
|---|---|---|---|---|---|---|
| bulk_carrier | 140000 | 2.963 | 3.376 | 3.376 | -12.2% | +0.0% |
| bulk_carrier | 180000 | 2.621 | 2.994 | 2.994 | -12.5% | +0.0% |
| tanker | 140000 | 3.755 | 3.376 | 3.755 | +0.0% | -10.1% |
| tanker | 180000 | 3.322 | 2.994 | 3.322 | +0.0% | -9.8% |
| container | 140000 | 3.950 | 3.376 | 16.096 | -75.5% | -79.0% |
| container | 180000 | 3.507 | 2.994 | 15.303 | -77.1% | -80.4% |
| general_cargo | 140000 | 3.480 | 3.376 | 8.313 | -58.1% | -59.4% |
| general_cargo | 180000 | 3.079 | 2.994 | 7.874 | -60.9% | -62.0% |
| lng_carrier | 140000 | 4.699 | 3.376 | 8.197 | -42.7% | -58.8% |
| lng_carrier | 180000 | 4.157 | 2.994 | 7.276 | -42.9% | -58.9% |

知识库文档 `ship_performance_platform_technical_docs_part2_energy_efficiency.md`（sha1 `1e5891793d28d2926cd51005967cd2ca2e800382`）把散货船参考线指数写成 0.488、箱船写成 1086.20·DWT^-0.474；代码用 961.79·DWT^-0.477；IMO 为箱船 174.22·DWT^-0.201。同一个量有三种答案，最差分歧 83.0%——knowledge↔code↔规范三者没有任何一致性闸门。

### 3.6 修复侧验收闸门（供 ship-performance-platform 单直接采用）

| 案例 | EEDI | IMO 要求 | 合规 | EEOI(设计等效) | AER(CII) | 要求 CII | 比值 | 状态 |
|---|---|---|---|---|---|---|---|---|
| KVLCC2_tanker | 3.351 | 1.812 | N | 3.351 | 3.351 | 2.129 | 1.574 | NON-COMPLIANT |
| Capesize_bulk | 4.176 | 2.096 | N | 4.176 | 4.176 | 2.274 | 1.836 | NON-COMPLIANT |
| PostPanamax_container | 11.526 | 11.267 | N | 11.526 | 11.526 | 5.376 | 2.144 | NON-COMPLIANT |
| Handysize_bulk | 9.151 | 4.578 | N | 9.151 | 9.151 | 6.298 | 1.453 | NON-COMPLIANT |

闸门文件 `acceptance_gate.json`（GATE_GREEN_M1850，容差 2%）由参考实现自洽生成并由 pytest 复核；修复后端点在这些案例上的返回值必须逐字段落带，且诚实字段 `ship_type_assumed/eeoi_basis/limitations` 必须回传。

## 4. 结果图

**图 1** `fig_g1_eedi_reference_lines.png` — G1：IMO EEDI 参考线族（8 船型，MEPC.203(62)/MEPC.364(79)）与平台单线（961.79·DWT^-0.477，无 ship_type）对照；★=平台返回值，○=IMO 值，箭头标注相对误差。箱船误差 −79%，油船 −9.2%，散货船恰好 0（因平台硬编码的正是散货船线）。
![fig_g1_eedi_reference_lines.png](artifacts/m1850/fig_g1_eedi_reference_lines.png)

**图 2** `fig_g2_eedi_phase_gate.png` — G1/G2：门槛三重对照（IMO 参考线 RF=0、IMO 2026 要求 RF=30%、平台门槛 RF=0）与阶段折减时间表；平台对一切建造年份都用 RF=0 门槛，右侧阶跃曲线本应为门槛随年份下降。
![fig_g2_eedi_phase_gate.png](artifacts/m1850/fig_g2_eedi_phase_gate.png)

**图 3** `fig_g3_cii_scale.png` — G4：CII 量级对照（对数纵轴）。IMO AER 与实际要求 CII 同量级（2.1–11.5 g CO2/(DWT·nm)），平台 CII 为 1.9e-3–6.4e-3，低估约 1799×（量纲缺陷 1852× 叠加 C_F 差），恒小于评级阈值 4 ⇒ 评级恒 A。
![fig_g3_cii_scale.png](artifacts/m1850/fig_g3_cii_scale.png)

**图 4** `fig_g4_verdict_flip.png` — G2：四个案例在 P_ME 扫描（0.3–1.2 倍设计功率）下的达到 EEDI、平台门槛与 IMO 2026 门槛；橙色带=判决翻转区（平台放行而 IMO 拒收，反之亦然）。
![fig_g4_verdict_flip.png](artifacts/m1850/fig_g4_verdict_flip.png)

## 5. 注意点（踩过的坑 / 局限 / 适用边界）

1. **坑：`round(cii, 2)` 让 CII 显示成 0.0**。只看返回值会误判为「计算失败」，实际是量纲错后的舍入；必须按源码重建未舍入值（1.9e-3–6.4e-3）才能量出 1852× 缺陷。凡「返回 0 或接近 0」的字段，先算未舍入值再下判断。
2. **坑：EEOI 与 EEDI 的代数同一性**。肉眼看两个字段数值不同（3.4501 vs 3.45）是因为舍入位不同，符号推导能一步证死：航程 D 在分子分母抵消。对「两个指标」的审计必须做符号化，不能只做数值对比。
3. **坑：平台 :8001 的活代码不在 `/root/ship-performance-platform`**，而在 `-incoming/.../backend`。审计前必须由 `/proc/<pid>/cwd` 核准进程真实目录，否则会审到旧副本，结论全错。
4. **坑：知识库文档不能当基准**。part2 文档的系数表与 IMO 差 5–79%，若以文档为参考核，本审计会得出相反的结论；参考核必须直接取规范原文。
5. **局限：EEDI 用简化面**（P_ME·C_F·SFC/(capacity·V)，不含 P_AE、f_j、f_i、f_w、创新技术抵扣）。这是**与端点同形**的简化，目的是把缺陷隔离到参考线与阶段上；完整 MEPC.364(79) 式会改变绝对值（同量级），但不改变任何一条判据的方向。
6. **局限：CII 字母评级未判**。字母级需 MEPC.354(78) 的 dd 向量，本文未取得可核实原文，故只判「达到值 vs 要求值」的合规性与比值；平台的「恒 A」由敏感性指纹（288 点）证伪，不依赖 dd 向量。
7. **局限：只读审计，未改 ship 仓**。修复必须落 `ship-performance-platform` 私有仓；本单按仓位纪律只做审计 + 参考实现 + 验收闸门，并派 native 单承接修复（见 §7）。
8. **适用范围**：结论对该端点当前实现（sha1 见头部）成立；任何一次改动后重跑 `python3 artifacts/m1850/green_efficiency_audit.py` 即可重裁。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 相对基准误差 | 是否 <3% | 是否 ≤15%（可接受上限） |
|---|---|---|---|
| EEDI baseline：散货船（Capesize 180k DWT） | -0.15% | 是 | 是 |
| EEDI baseline：油船（KVLCC2 300k DWT） | -9.22% | **否** | 是 |
| EEDI baseline：箱船（140k DWT） | -79.00% | **否** | **否** |
| EEDI 达到值（平台 C_F=MGO vs HFO 基准） | +2.95% | 是 | 是 |
| EEDI 要求值 2026（平台 RF=0 vs IMO RF=30%，散货） | +42.65% | **否** | **否** |
| CII 量级（平台 CII vs IMO AER，箱船） | -99.94% | **否** | **否** |

**结论（不含糊）**：关键量相对 IMO 基准的误差**不满足 <3%**，且除「散货船 baseline」（因平台硬编码的恰是散货船线，巧合 0.15%）外，全部超过 15% 的不可接受线；CII 量级偏差达 −99.94%，EEOI 在装载率 50%/25% 工况偏差 −50%/−75%。因此该端点的证据级判定为 **MOCK**（HTTP 200、字段齐全、数值形状合理，但物理不成立），不得作为绿色航行（EEDI/EEOI/CII）决策依据。

## 7. 局限 / 待办 / 下一步

- **修复派单**：已向 `/tensor/missions` 派 native 单（runner=native），带 `artifacts/m1850/acceptance_gate.json` 作为验收闸门；改动落 `ship-performance-platform`。
- **知识库整改**：part2 文档系数表需按 MEPC.203(62)/MEPC.353(78) 重写，并加「文档系数 = 代码系数 = 规范系数」的一致性测试（G6 已给出判别式）。
- **CII 字母评级**：待取得 MEPC.354(78) dd 向量原文后补齐；补齐前必须返回 `N/A` 而非编造字母。
- **横向推广**：同一套「参考线闭式 + 闸门 + 负控制」打法可直接复用到 EEXI（MEPC.350(78)）、EU ETS/MRV 与 FuelEU 指标，均为纯代数，成本极低。
