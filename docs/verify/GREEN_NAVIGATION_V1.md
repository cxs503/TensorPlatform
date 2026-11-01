# 绿色航行域能力补全与证据分级（EEDI / EEXI / CII）v1

**任务**：selfstart / mission-1525（tp-ver-8 verifier 席自主工时）
**分支**：`agent/m1525` · **EDIT_WHITELIST**: `backend/services/`, `backend/api/domain/`, `tests/`, `tools/verify/`, `docs/verify/`, `artifacts/green_nav/`
**一句话**：平台在"绿色航行"这一学科上此前**零实现**；本单交付首个实现，并把可推导层（REAL）与抄规则表层（WITHHELD）、未取得原文层（NOT_SUPPORTED）**在 API 响应里逐层标注**，避免又一个"看起来能算"的假能力。

---

## 1 案例介绍（物理背景 / 几何 / 参数）

绿色航行是船舶总体性能九大学科之一，其定量核心是三个能效指数：

| 指数 | 物理含义 | 计算结构 |
|---|---|---|
| EEDI | 新造船设计能效 | 单位运输功的 CO₂ 排放 gCO2/(t·nm) |
| EEXI | 现有船能效（2023 起强制） | 同上，用 75% MCR 参考功率与参考航速 |
| CII | 营运碳强度（逐年收紧） | 年 CO₂ /(Capacity × 年航程) |

三者的**分子**都是同一条物理链：`机械功率 [kW] × 燃油消耗率 [g/kWh] × 碳因子 [gCO2/gFuel]`，再除以运输能力项。分子是纯物理，可独立复算；**分母中的"要求值"是规则基准线 `a·Capacity^(−c)` 乘削减率**，其系数来自规则文本，离线不可复核 —— 这正是本域必须分层判决的原因。

**算例船**：82 000 DWT Kamsarmax 散货船（bulk carrier）

| 参数 | 值 | 说明 |
|---|---|---|
| Capacity | 82 000 t | DWT（散货船按 DWT） |
| V_ref | 14.0 kn | 参考航速 |
| P_ME | 8 800 kW | 主机功率（<10 000 kW → 进入 P_AE 的 0.05·ΣMCR 分支） |
| SFOC_ME / SFOC_AE | 168 / 215 g/kWh | 主机 / 辅机燃油消耗率 |
| 燃料 | HFO（w_C = 0.849） | 碳因子导出 3.1108 |
| P_AE | 440.0 kW | 由 `0.05·ΣMCR` 折算（模块内算，非手填） |

## 2 怎么做的（方法 / 流程 / 工具链）

1. **扫描定位（全只读）**：提案池 `proposals/p1…p8`（17 条）、近 18 个分支的代码检索、`audit_capabilities` 能力清单。检索证据：`seakeeping`、`eedi/eexi/carbon_intensity` 在**全部近期分支**中均无实现（命中项都是无关文件），确认绿色航行是真空缺；而稳性/阻力/推进/操纵/振噪五域近 24 h 已分别由 m1390/m1392/m1472/c95782f/b47de8b/12185e9/2bf9b1c 清偿，主动避让以杜绝重复劳动。
2. **物理层重建（不抄因子）**：碳因子不用"抄表"，而用碳氧化计量导出 `C_F = w_C × 44.009/12.011`。纯物质燃料（CH₄/C₃H₈/C₄H₁₀/CH₃OH/C₂H₅OH）的 w_C 由**分子式原子量**算出；石油馏分用标准典型含碳量。公布 C_F 只作为**比对对象**，不参与计算（测试里篡改公布值后导出值不变，专门证明这一点）。
3. **规则层隔离**：基准线系数（a, c）、阶段削减率、CII Z 系数集中在一张表里并标注 `WITHHELD` 与转录来源；未知船型/阶段/燃料**一律报错**，绝不静默套默认值（历史陷阱：门禁字段名漂移导致"静默按 1 m/s"）。
4. **CII 等级不下结论**：G4（MEPC.354(78)）的 dd 向量与指数边界函数未取得原文，本版**不实现 A–E 等级**，`cii_rating_status()` 返回 `NOT_SUPPORTED` 并写明补齐路径。
5. **工具链**：`backend/services/green_navigation.py`（纯函数，无 FastAPI 依赖）→ `backend/api/domain/ship_green.py`（6 端点，挂 `/calculations/green/*`）→ `tests/test_green_navigation.py`（34 项）→ `tools/verify/green_nav_audit.py`（程序化产出证据 + 3 图）→ 本文。

## 3 计算结果

全部数字由 `python3 tools/verify/green_nav_audit.py` 生成，落在 `artifacts/green_nav/numbers.md` 与 `evidence.json`，**本文件不手抄任何计算值**。

### 3.1 碳因子：化学计量导出 vs 规则公布

| 燃料 | w_C | C_F 导出 | C_F 公布 | 偏差 |
|---|---|---|---|---|
| hfo | 0.84900 | 3.1108 | 3.114 | 0.103% |
| lfo | 0.85900 | 3.1474 | 3.151 | 0.113% |
| mdo | 0.87400 | 3.2024 | 3.206 | 0.113% |
| lpg_propane | 0.81713 | 2.9940 | 3.000 | 0.200% |
| lpg_butane | 0.82658 | 3.0286 | 3.030 | 0.045% |
| lng | 0.74868 | 2.7432 | 2.750 | 0.248% |
| methanol | 0.37485 | 1.3735 | 1.375 | 0.111% |
| ethanol | 0.52144 | 1.9106 | 1.913 | 0.127% |

**最大偏差 0.248%** —— 8 项全部远低于 0.5%，说明公布 C_F 表确实是碳氧化的化学计量结果，导出路径正确。

### 3.2 达到值（REAL）与要求值（WITHHELD）判决

attained EEDI = **4.262426** gCO2/(t·nm)（独立逐项复算相对差 **0.00e+00 %**）。

| 阶段 | 参考值 | 削减 | 要求值 | 达到/要求 | 合规 |
|---|---|---|---|---|---|
| phase0 | 4.357 | 0% | 4.357 | 0.978 | PASS |
| phase1 | 4.357 | 10% | 3.921 | 1.087 | FAIL |
| phase2 | 4.357 | 20% | 3.486 | 1.223 | FAIL |
| phase3 | 4.357 | 30% | 3.050 | 1.398 | FAIL |

EEXI：达到 4.262 / 要求 3.486 → FAIL（比值 1.223）。该结论对一条无 ESD/EET 的经典 Kamsarmax 是工程上合理的（现有散货船普遍需要节能装置才能达标）。

### 3.3 CII（达到值 REAL）

年油耗 7 865.0 t → 年 CO₂ 24 466.3 t，年航程 60 000 nm → 达到 CII = **4.9728** gCO2/(t·nm)；

| 年份 | Z | 要求值 | 达到/要求 | 合规 |
|---|---|---|---|---|
| 2023 | 5% | 3.9588 | 1.256 | FAIL |
| 2024 | 7% | 3.8755 | 1.283 | FAIL |
| 2025 | 9% | 3.7921 | 1.311 | FAIL |
| 2026 | 11% | 3.7088 | 1.341 | FAIL |
| 2030 | 11% | 3.7088 | 1.341 | FAIL |

## 4 结果图

| 图 | 文件 | 内容 |
|---|---|---|
| 图 1 | `artifacts/green_nav/fig1_cf_chemistry.png` | 左：8 种燃料导出 vs 公布 C_F 分组柱；右：逐项相对偏差（对数轴）与 3% 不可接受阈、0.5% 自设阈 |
| 图 2 | `artifacts/green_nav/fig2_eedi_vs_capacity.png` | 散货船参考线四个阶段要求值随 Capacity 变化 + 本船达到值水平线（对数横轴） |
| 图 3 | `artifacts/green_nav/fig3_cii_timeline.png` | CII 达到值 vs 2019→2030 逐年收紧要求，超出区间阴影 |

## 5 注意点（踩过的坑 / 局限 / 适用范围）

1. **要求值层是 WITHHELD，不是 REAL**。a/c、削减率、Z 系数是从规则文本转录的；离线环境无法回读 MEPC 原文，所以本域整体合规判决继承 WITHHELD，**不得单独作为规范符合性结论**（API 字段 `required_evidence_grade` 会如实返回）。
2. **不实现 CII A–E 等级**：G4 边界函数未取得原文，宁可 `NOT_SUPPORTED` 也不给假能力。
3. **P_AE 双分支易错**：<10 000 kW 用 `0.05·ΣMCR`，≥10 000 kW 用 `0.025·ΣMCR+250`；单测首版就是因为把 8 800 kW 的船按后者算而失败，后被测试纠正（这是判据生效的证据，不是被掩盖的错误）。
4. **范围界定**：只覆盖 CO₂ 与能效指数，不含 NOx/SOx/CH₄ 逃逸与 LCA 燃料全生命周期（Well-to-Wake）口径；EEDI 的 `f_j / f_i / f_c / f_l` 修正系数已留接口，但本算例取 1.0。
5. **未接入线上服务**：`/calculations/green/*` 已挂到 `api.domain.misc` 聚合路由，但部署态（:8001）需要重新构建才会出现这些端点；本单验证走的是 `TestClient` 与进程内调用。此外本机缺少 `sklearn`，`import api.domain.misc` 会在**与本域无关**的既有依赖处失败，故"整包挂载"未能端到端跑通——这一点如实记下，等完整环境重建时复核（`ship_green.router` 自身 6 条路由已在 TestClient 下验证）。

## 6 误差判定（是否 <3%）

| 关键量 | 基准 | 实测相对误差 | <3% |
|---|---|---|---|
| 8 项 C_F（导出 vs 公布） | MARPOL Annex VI Reg.2 公布值 | 0.045% – 0.248%（最大 0.248%） | **是** |
| attained EEDI（模块 vs 测试内独立逐项复算） | 独立标量表达式 | 0.00e+00 % | **是** |
| 碳质量守恒（m_fuel·w_C·44/12 vs 模块 CO₂） | 计量恒等式 | 机器精度（1e-9 绝对） | **是** |
| 功率链 CO₂ vs 加油量×C_F（跨路径） | 内部一致性 | 1e-12 相对 | **是** |
| 要求值 / CII 等级 | 规则原文 | **无基准（离线不可得）** → 判 WITHHELD / NOT_SUPPORTED | 不适用（已显式声明，未粉饰） |

**pytest**：`python3 -m pytest tests/test_green_navigation.py -q` → **34 passed**。
**审计脚本**：`python3 tools/verify/green_nav_audit.py` → 5/5 判据 PASS（`evidence.json: E_all_pass = true`）。

---

### 能力判决（D5）

| 层 | 等级 | 依据 |
|---|---|---|
| L1 碳因子化学计量 | **REAL** | 分子式/含碳量导出，公布值比对 <0.25% |
| L2 达到值能效指数（分子） | **REAL** | 能量→燃油→CO₂ 链，独立复算 0 误差 + 三条标度/守恒判据 |
| L3 要求值（参考线×削减率） | **WITHHELD** | 规则系数转录，离线不可复核；响应显式标注 |
| L4 CII A–E 等级 | **NOT_SUPPORTED** | G4 原文未取得，不实现 |

域整体判决：**由 NOT_SUPPORTED 补全为 REAL（分子层）+ WITHHELD（要求值层）双标注**，不虚报全域 REAL。