# 能力证据等级的**可执行证伪**（W18-N09 / m5238）

> 一句话：把「证据等级」从**自称 + 文献对账**改造成**可被机器推翻的判据**——四把刀
> （扰动不变性 / **输入-输出确定性** / 解析解对拍 / 单调性）逐能力执行，输出
> 「声明等级 × 证伪结果」混淆矩阵，并用变异体自检证明刀具本身有检出能力。
>
> 本单实测（21 项能力，**18 项自述 REAL**）：三刀只**确证 2 项**，**主动推翻 3 项**
> （lifting 2 项声明 REAL 的能力被判 MOCK；另有 1 项声明 NOT_SUPPORTED 的能力实际存在但输出不可复现），
> 在线 404 判 NOT_SUPPORTED 2 项，其余 12 项**不可判定（WITHHELD）**。
> 未确证率 **88.9%**（16/18），主动推翻率 **11.1%**（2/18），分母均为「声明 REAL」的 18 项。

## 1. 为什么需要这台仪器

`artifacts/capability_ledger/`（m1567/m1811/m2031）已把「自述等级」与「报告证据」对账，
但判据是**引文正则匹配**：只要有一份报告声称 REAL，账上就能记 REAL。两个缺口：

1. **不可执行**：没有代码能「跑一遍能力」来推翻等级——等级取决于谁写报告，而非能力做什么；
2. **不可复算**：引文命中 ≠ 物理正确，证据老化后无人重跑。

W18-N09 把判据换成**调用能力本身**：输入扰动、解析解、单调性、重复性——任何一条走反，
等级必须被机器翻掉。

## 2. 单一真值台账 `artifacts/capability_falsification/capabilities.json`

21 行，每行含 `id / name / domain / entrypoint / evidence_grade / evidence_ref / callable_spec`，
另含工具兼容字段 `claimed_level / reconciled_level / divergence`。

| 项 | 内容 |
|---|---|
| 覆盖 | 21 项，与 `audit_capabilities` 的 id 集合**对称差为空**（双向校验：仓内 resolved 台账 + 工具快照 `tests/fixtures/capability_ledger/capabilities_snapshot.json`） |
| `evidence_grade` | 取自对账台账的 `claimed_level`（自述，不是裁决） |
| `evidence_ref` | 台账路径 + `claimed_level`/`reconciled_level`/`divergence`/`line_sha256`/证据报告 id |
| `callable_spec` | **已展平的调用样本**（不 eval、无闭包）：方法/路径、扰动主集与加宽确认集、解析对拍项（公式 + 容差 + 参数来源）、单调性序列、适用刀号 |

> 本会话里 `audit_capabilities` 工具本身返回「读取能力清单失败」（硬编码路径
> `/root/ship-performance-platform-incoming/.../capabilities.json` 在 incoming 树里只剩 `.pyc`）。
> 台账因此以该工具的**仓内首选真值来源**（`capability_ledger_resolved.json`）取 id 集合，
> 并与工具快照交叉校验；事实记录在 `report_data.json::criteria.C1_ledger_single_truth.source`。

## 3. 四把刀（`tools/audit/falsification/knives.py`）

| 刀 | 判据（任务口径） | 实现要点 |
|---|---|---|
| **A 扰动不变性** | 量级 ×1.0/×0.7/×1.3、Cb∈[0.35,0.90]、L/B∈[3,12]、B/T∈[1.5,6.5]；输出逐位不变或相对变化 < **1e-12** → MOCK | 每组扰动带**加宽确认集**（×0.5/×1.5）：主集不变而加宽集变化 → `INCONCLUSIVE_ROUNDING`（不诬告），见 §6 陷阱 1 |
| **A2 输入-输出确定性** | 同一请求重复 3 次，输出相对漂移 > **1e-12** → MOCK | 与刀 A 互补：刀 A 抓「输入变了输出不变」（硬编码），A2 抓「输入没变输出却变」（随机数/未固化模型）；重复调用绕过缓存，真发请求 |
| **B 解析解对拍** | 与解析值相对误差 > **15%** → MOCK | 恒等式（`eta0=J·KT/2πKQ`、`GM=KB+BM−KG`、`∇=L·B·T·Cb`、`Rf=½ρV²S·Cf_total`）用 **1e-3** 容差（仅吸收十进制舍入）；另含 ITTC-1957 Cf 闭式、Froude 相似的 Cr 不变性；**跨模型一致性（consistency）不算绝对锚**，不得据此宣布 REAL |
| **C 单调性** | 违反物理单调方向 → MOCK | 步长 < 1e-12 记 STALL（不变性归刀 A，不重复计罪）；方向反转 KILL |

解析参照全部由审计侧**独立实现**（`knives.py` 末段 + `runner.py::REF_FNS`），**不 import 平台代码**，
避免同族自证；台账只存「公式 + 容差 + 参数来源」，参照数值运行时重算，**零手抄**。

## 4. 判决规则与混淆矩阵

规则（`grade.py::falsified_grade`，只看刀具证据，不看自述字段）：

| 条件 | 证伪等级 |
|---|---|
| 候选端点全部 404 | NOT_SUPPORTED |
| 数值能力但响应恒 0/空 | STUB |
| 任一刀具 KILL | **MOCK** |
| 无 KILL 且通过解析/恒等式**绝对锚** | **REAL** |
| 三刀存活但刀 B 只有跨模型一致性锚 | WITHHELD（不足以宣布 REAL） |
| 无「数值输入→数值输出」映射 / 入口未定位 | WITHHELD（刀具原理上不适用） |

**混淆矩阵**（行 = 声明，列 = 证伪；图 `fig_confusion_matrix.png`）：

| claimed ↓ / falsified → | REAL | WITHHELD | MOCK | STUB | NOT_SUPPORTED |
|---|---|---|---|---|---|
| **REAL (18)** | **2** | 12 | **2** | 0 | 2 |
| STUB (1) | 0 | 1 | 0 | 0 | 0 |
| NOT_SUPPORTED (2) | 0 | 1 | **1** | 0 | 0 |

- **未确证率（overclaim_rate）** = 16/18 = **88.9%**；**主动推翻率（refuted_rate）** = 2/18 = **11.1%**。
- 两率分开报：「不可判定」与「被推翻」不是一回事。7 项 `dev.*`（运维动作）、4 项查询类端点、`misc.stubs`
  根本没有「数值输入→数值输出」映射，刀具在原理上不适用——把它们记成「假阳性」会把**仪器适用范围问题**
  伪装成**造假问题**。
- 唯一一行 `NOT_SUPPORTED → MOCK` 是申报失真型：`ai.predict` 被注册表记为「不支持」，实测端点存在
  且**输出不可复现**（见下）。它不在「声明 REAL」的分母里，故不计入上述两率。

### 4.1 被推翻的三项（可复算证据）

| 能力 | 刀 | 证据 |
|---|---|---|
| `shiphull.generate` | A（Cb 驱动） | `hydrostatics.wetted_surface = 2920.0 m²` **与 Cb 完全无关**（Cb = 0.40/0.50/0.60/0.70/0.85 全同），`LCB = 52.0` 亦恒定。同一平台 `/calculations/resistance` 对同几何给 S = 2715.62 m² 且随 Cb 变化（2427.98 @0.45 → 3005.34 @0.75）→ 两个端点对同一船给出差 **7.0%** 的湿面积，其中一个是 Cb 盲 |
| `propeller.open_water` | C（KQ–J） | J = 0.1 → 0.2 时 KQ 由 0.070628 **升至** 0.072169（**+2.2%**），违反「KQ 随 J 单调降」；工作区 J∈[0.2,0.8] 单调（信息性序列 PASS）。eta0 恒等式误差 1.8e-5（恒等式本身没错） |
| `ai.predict` | **A2（重复性）** | 同一请求重复 3 次（本单 trace）：`predicted_rt` = **580.80 / 584.96 / 572.91**（漂移 **2.06%**），`predicted_cr` 漂移 **3.00%**，而 `predicted_cf` 恒为 0.001631。漂移幅度随调用随机（多次实测 1.9%~3.6%）。即摩擦项确定性、剩余阻力项每次调用重新抽随机数，同时响应自称 `accuracy = 0.95`、`training_samples = 5000`。另：与同输入物理链 R_t 差 3.4%（<15%）——**噪声不算「不准」，但不可复现的物理量不可用于工程决策** |

### 4.2 被确证的两项（解析锚全通过）

| 能力 | 锚 | 误差 |
|---|---|---|
| `calc.resistance` | ITTC-1957 Cf 闭式 | **2.25e-4** |
| | `Rf = ½ρV²S·Cf_total`（kN，含 ITTC-1978 粗糙度补贴 dcf） | 1.64e-4 |
| | Froude 相似（几何放大 1.2、同 Fr）Cr 不变 | **0.0** |
| | R_t 随 Fr 单调增（7→21 kn，4 步） | 全 PASS |
| `calc.stability` | `GM = KB + BM − KG` 恒等式 | 5.6e-16 |
| | `GZ_max` 直壁式解析极值（0.005° 网格，域取平台自报 `heel_validity_limit_deg`） | **2.91e-4** |
| | GM 随 KG 单调减（5→11 m） | 全 PASS |

### 4.3 数值能力的全貌（5 项有「输入→输出」映射）

| 能力 | A 最大相对变化 | A2 漂移 | B | C | 证伪 |
|---|---|---|---|---|---|
| `calc.resistance` | 0.839（speed）/0.315（B/T） | 0.0 | cf 2.3e-4 / rf 1.6e-4 / Cr 0.0 | PASS | **REAL** |
| `calc.stability` | 1.268（kg）/0.770（beam） | 0.0 | GM 5.6e-16 / GZ 2.9e-4 | PASS | **REAL** |
| `shiphull.generate` | 体积 responds，**湿面积 0.0** | 0.0 | ∇ 恒等式 0.0 | 体积 PASS / 湿面积 STALL | **MOCK** |
| `propeller.open_water` | 0.675（J）/0.338（P_D）/0.467（Ae/Ao） | 0.0 | eta0 恒等 1.8e-5 | **KQ_vs_J KILL** | **MOCK** |
| `ai.predict` | 0.715（speed）/0.116（beam） | **0.0206** | 一致性 <15%（非绝对锚） | PASS | **MOCK** |

## 5. 仪器自检（`mutant_selftest.json` + `fig_mutant_selftest.png`）

刀具必须证明**有检出能力**且**不误报**，否则「证伪」只是又一层自述。变异体真值先于判决写死：

| 变异体 | 预登记 | 判决 | 击杀刀 |
|---|---|---|---|
| mock_constant（恒 500 kN） | MOCK | MOCK | A, B |
| mock_speed_blind（速度钉死 14 kn） | MOCK | MOCK | A |
| analytic_violator（Cf 硬编码 0.0045） | MOCK | MOCK | B |
| non_monotone（14 kn 挖坑） | MOCK | MOCK | C |
| **nondeterministic（±2% 随机抖动）** | MOCK | MOCK | **A2** |
| stub_zeros（全 0） | STUB | STUB | A, B |
| correct_physics（阴性对照） | REAL | REAL | — |
| rounded_physics（真实现 + 6 位舍入） | REAL | REAL | — |

**检出率 6/6 = 100%，误报率 0/2 = 0%**；四把刀各自都独立击杀过至少一个变异体（非恒真装置）。
刀 B 在真实平台上 5 项检查全 PASS、0 击杀——正是自检给出的「它有击杀能力」使这一结果可解释为
「平台这几条解析关系确实对」，而不是「刀 B 是摆设」。

## 6. 注意点（踩过的坑）

1. **舍入假阳性**：平台响应常见 3~6 位小数，弱依赖驱动量（Ae/Ao、Cb→湿面积）在 ±30% 扰动下可能被
   舍入吃掉。只看主扰动集会误杀真实现 → 刀 A 一律带加宽确认集；
   `test_knife_a_does_not_kill_when_widened_sweep_moves` 钉住它。
2. **恒等式的单位/口径**：`Rf` 恒等式第一次跑出 100% 误差——不是平台错，而是 (a) 阻力字段单位是 **kN**，
   (b) 平台把 ITTC-1978 粗糙度补贴 `dcf` **并进摩擦项**（响应有 `cf_total`）。改用 `cf_total/1000` 后
   误差落到 1.6e-4。**口径不清就判 MOCK，是仪器自己的假阳性来源**（现在有 `test_resistance_identity_units_are_kn` 守门）。
3. **公式适用范围**：直壁式 GZ 的 `tan²φ` 在近 90° 发散（数学必然、物理无效），解析求极值必须限制在
   平台自报的 `heel_validity_limit_deg` 内，否则「解析参照」会算出 8357 m 并误杀真实现。
4. **加宽确认集要 ≥2 点**：只有 1 个确认样本时无法判断「是否仍不变」，刀 A 退化为 INCONCLUSIVE。
5. **几何包络是多约束**：把 B/T 推到 6.5 的同时必须拉长 L，否则 L/B = 1.92 越出 [3,12]；
   `test_ledger_geometry_perturbations_stay_in_physics_envelope` 逐样本校验。
6. **缓存 vs 重复性**：探针按 `(method,path,body)` 去重缓存（本单 86 次调用 / 21 项能力），
   但刀 A2 必须**绕过缓存**真发请求，否则「确定性」被自己的缓存洗白。

## 7. 局限（honest）

- **结构刀不能证伪绝对量级**。四把刀检验的是「有响应 / 输出可复现 / 与解析式一致 / 方向正确」；
  湿表面积绝对值错 20%、垂荡 RAO 短波端错 311 倍这类问题，需要实验/CFD 基准才能推翻（本单未引入外部基准）。
  因此 `calc.resistance` 判 REAL 仅表示「其摩擦线、内部恒等式与 Froude 相似结构可信」，**不覆盖**
  湿面积与兴波阻力量级的绝对精度——与 m1811 对账台账把它记 WITHHELD（inline 湿表面积 −20.9%）不矛盾：
  两者测的是不同命题，本单在 `verdicts.json` 里并列保存两种口径，不做覆盖。
- **覆盖 ≠ 可证伪**：21 项里只有 5 项具备「数值输入→数值输出」映射；7 项 `dev.*` 是运维动作，
  4 项是查询类端点，`misc.stubs` 无入口。对这 12 项记 WITHHELD（不适用），**不是「通过」**。
- **推进域的 KILL 是局部异常**：KQ 违反只发生在 J<0.2（+2.2%），不是「全程硬编码」。判 MOCK 的语义是
  「其 REAL 声明被推翻」，不是「所有数值都是假的」。高置信度定位根因需要 Wageningen B 系列图谱或试验
  数据做绝对对拍（本会话无文献访问）。
- **A2 的 1e-12 判据偏严**：多线程/GPU 归约顺序导致的 1e-16 级抖动也可能被判 KILL。本单实测平台
  确定性能力全部给出**逐位相同**的输出（漂移 0.0），故未出现该假阳性；换到浮点并行后端时应放宽到
  1e-12 相对（当前）或显式记录「允许抖动」的配置项。
- 样本是**单点几何/单工况族**（L ≈ 100 m、Fr ≈ 0.23），不覆盖低速/高速/浅水等分支。

## 8. 复现

```bash
python3 tools/audit/falsification/run_audit.py      # 在线全量审计（需 :8001）
python3 tools/audit/falsification/run_audit.py --no-probe   # 复用已落盘 trace 重建判决/图
python3 -m pytest tests/test_capability_falsification.py -q # 离线回归（23 项）
```

产物（全部在 `artifacts/capability_falsification/`）：

```
capabilities.json        单一真值台账（21 项 + callable_spec）
probe_trace.json         探针原始证据（每样本请求体、状态码、抽取字段、响应摘要）
verdicts.json            逐能力 声明 vs 证伪 + 原因（含 whether analytic anchor）
confusion_matrix.json    混淆矩阵 + 未确证率/主动推翻率
mutant_selftest.json     刀具检出率/误报率
report_data.json         判据数字（C1~C5）+ 产物路径
fig_confusion_matrix.png / fig_knife_profiles.png / fig_mutant_selftest.png
```

工具链：`tools/audit/falsification/{spec,knives,runner,grade,selftest,figures,run_audit}.py`；
`ops/tp_worker.py::_cap_sources` 新增兜底候选 `artifacts/capability_falsification/capabilities.json`
（含 `claimed_level/reconciled_level/divergence/domain`，可被 `_render_caps` 直接渲染，
`test_ledger_renders_in_agent_tool` 守门）。