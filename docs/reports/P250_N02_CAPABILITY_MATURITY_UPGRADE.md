# P250-N02 平台能力成熟化升级框架（MOCK/STUB → REAL）验收报告

- 节点：`p250-n02-capability-maturity-upgrade`（mission 9224，仓 `TensorPlatform`）
- 生成时点：2026-11-10T09:11:18+00:00；生成方式：`python -m tools.capability_maturity.run_acceptance`
- 判据指纹 sha256：`6cf3b5f47ce13e9a169202d6858a16610e6f937c44663c457a878d6f1c97625e`（**预声明**，先于任何基准结果落盘；
  运行期若改动容差/用例下界，指纹必变，报告即失效——见 `artifacts/capability_maturity/criteria_predeclaration.json`）
- 判据数值：逐例相对误差 ≤ **15%**；用例数 ≥ 3；
  必需证据链 ['test_report', 'benchmark_data', 'error_analysis']；参考值须来自**不同提供方**；旧实现须超阈（否则不算升级）
- 环境：Python 3.11.6 / Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38；numpy 2.4.6、matplotlib 3.10.8、mpmath 1.3.0；
  基准用例种子 `20261110`（固定，可复算）

## ① 案例介绍（审计对象与参数表）

审计对象 = 平台能力台账的 **21 项能力**（自述等级 declared 与对账等级 reconciled 两列）。
审计锚不另读台账，而是解析 `audit_capabilities` 工具的**同一份渲染正文**
（`tools/audit/ledger_consumer_audit_tool.audit_capabilities()`），正文 sha256 =
`44035b5e5e14fd2f`，真值来源 `/root/tp-agent/work/m9224/artifacts/capability_ledger/capability_ledger_resolved.json`，台账契约 `{'status': 'DEGRADED', 'path': 'artifacts/capability_ledger/capability_ledger.json', 'sha256': '618d37ff38a1', 'schema': '2.0', '未证实': '17', 'OVERCLAIM': '2', 'reasons': 'PIN_MISSING'}`。

对账等级分布：{'NOT_SUPPORTED': 0, 'STUB': 0, 'MOCK': 1, 'WITHHELD': 19, 'REAL': 1}。
未达 REAL 需升级的能力 20 项；已是 REAL 的能力用于负控。

| 参数 | 值 |
|---|---|
| 能力总数 | 21 |
| 渲染器自报条数 | 21 |
| 需升级能力数 | 20 |
| 容差（预声明） | 15% |
| 基准用例种子 | 20261110 |

## ② 怎么做的（方法/流程/工具链）

五锚流水线（全部域无关；能力 id/等级/基准数值均来自台账或 JSON 工件注入）：

1. **审计锚** `tools/capability_maturity/register.py`：解析 `audit_capabilities` 正文 →
   `AuditRow(cap_id, declared, reconciled, divergence, domain)`。审计表与工具输出**同源**，
   因此 A1 是结构性成立而非人工抄录。
2. **升级路径锚** `paths.py`：对每项未达 REAL 的能力生成固定四阶路径
   `algorithm → unit_test → benchmark → evidence_register`，每阶带「产出物 + 机器可判门」。
   实现位于领域仓的能力标记 `blocked`（**保留完整路径**，不隐藏、不假装已覆盖）。
3. **验收判据锚** `criteria.py`：`AcceptanceCriteria` 冻结容差、用例下界、必需证据链、
   独立性要求、旧实现改进要求，并给出 sha256 指纹（预声明工件）。
4. **基准执行器** `executor.py` + `validators.py`：实现/旧实现/参考三方并行跑同一批用例，
   逐例算误差；参考提供方必须与实现**不同**（LAPACK / mpmath / Fraction 对纯 Python 实现），
   同源即判 `rejected_not_independent`。
5. **证据链与裁定** `chain.py` + `engine.py`：每件工件按内容 sha256 入账；链条缺件 →
   `rejected_chain_incomplete`；已是 REAL → `no_op_already_real`（负控）；
   域边界外 → `blocked_domain_boundary`。引擎**只升不降**（降级必须走台账 OVERCLAIM 对账）。

工具链：pytest（19 passed, 1 warning in 2.19s）+ matplotlib（3 图）+
numpy/mpmath（独立参考）+ JSON/MD 工件（含 sha256）。

## ③ 计算结果（数字表，程序化注入）

### 3.1 验收判据 A1–A6

| 判据 | 结果 |
|---|---|
| A1_audit_table_matches_renderer | PASS |
| A2_three_or_more_upgraded_to_real | PASS |
| A3_path_coverage | PASS |
| A4_evidence_chain_complete | PASS |
| A5_negative_control_real_unchanged | PASS |
| A6_pytest_and_figures | PASS |

- A1 与渲染器逐行一致性：对称差 0，
  等级不一致 0 项。
- A3 覆盖率：20/20 = 100.0%，
  四阶齐全的路径 20 条，域边界外 20 项。

### 3.2 三项能力升级（MOCK/STUB → REAL）

| 能力 | 升级前 | 升级后 | 升级后最大误差(%) | 容差(%) | <15% |
|---|---|---|---|---|---|
| `synth.linalg.cholesky_solve` | MOCK | REAL | 2.073e-14 | 15 | 是 |
| `synth.stats.normal_ppf` | STUB | REAL | 1.041e-11 | 15 | 是 |
| `synth.integrate.adaptive_simpson` | MOCK | REAL | 4.163e-13 | 15 | 是 |

### 3.3 逐能力基准试验（实现 vs 独立参考，含旧实现基线）

| 能力 | 用例数 | 升级后最大误差(%) | 旧实现最大误差(%) | 实现探针 | 参考探针 | 独立性 |
|---|---:|---:|---:|---|---|---|
| `synth.linalg.cholesky_solve` | 5 | 2.073e-14 | 2.960e+01 | `synth.linear.upgraded` | `lapack.linear.solve` | 独立 |
| `synth.stats.normal_ppf` | 6 | 1.041e-11 | 1.960e+05 | `synth.normal.upgraded` | `mpmath.normal.ppf` | 独立 |
| `synth.integrate.adaptive_simpson` | 6 | 4.163e-13 | 6.500e+02 | `synth.simpson.upgraded` | `mpmath.simpson.quad` | 独立 |
| `synth.stats.mean_reference` | 3 | 0.000e+00 | — | `synth.mean.kahan` | `fractions.mean.exact` | 独立 |

### 3.4 证据链（每项升级能力：测试报告 + 基准数据 + 误差分析）

| 能力 | 链条构件 | 完整 | 链条摘要 sha256 |
|---|---|---|---|
| `synth.linalg.cholesky_solve` | benchmark_data, error_analysis, test_report | 是 | `105079d2f1d9b970` |
| `synth.stats.normal_ppf` | benchmark_data, error_analysis, test_report | 是 | `f873661a60974489` |
| `synth.integrate.adaptive_simpson` | benchmark_data, error_analysis, test_report | 是 | `ed9f1b8dbbc4ed9a` |

### 3.5 负控（已是 REAL 的能力，升级后等级不变）

| 能力 | 升级前 | 升级后 | 不变 |
|---|---|---|---|
| `calc.stability` | REAL | REAL | 是 |
| `synth.stats.mean_reference` | REAL | REAL | 是 |

负控汇总：`pass = True`（台账侧 REAL 能力与合成域负控对象均保持 REAL）。

## ④ 结果图

| 图 | 路径 | sha256 |
|---|---|---|
| `fig1_capability_audit_matrix.png` | `docs/reports/capability_maturity/fig1_capability_audit_matrix.png` | `fee8578ba7debc87` |
| `fig2_upgrade_path_graph.png` | `docs/reports/capability_maturity/fig2_upgrade_path_graph.png` | `f94b47a2958f5caf` |
| `fig3_error_improvement.png` | `docs/reports/capability_maturity/fig3_error_improvement.png` | `a5d41fbd5dc22734` |

- 图1 审计矩阵：21 项能力的自述 vs 对账等级（颜色=等级，含等级序号 rank 0..4）。
- 图2 升级路径图：每项未达 REAL 能力的四阶状态（done / planned / blocked）。
- 图3 升级前后逐例误差对照（对数纵轴，含 15% 容差线）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **相对误差在参考值零点无定义**。标准正态分位数 p=0.5 的真值为 0，纯相对误差会
   爆到 1e47%。判据里预声明 `zero_reference_floor=0.001`（|ref| ≤ floor
   时以 floor 作分母），尺度先声明后使用，不是看完结果调阈值。
2. **"测试绿 ≠ 能力真"**：本框架强制「旧实现必须超阈」才算升级（`rejected_no_improvement`），
   并把旧实现误差一并入账——否则「把一个本来就在容差内的实现换一种写法」会被误记成升级。
3. **独立提供方是硬门**：实现与参考若同源（自己证自己）直接 `rejected_not_independent`。
4. **只升不降**：升级流水线不得降级；降级须走台账 OVERCLAIM 对账路径，避免"用升级掩盖退化"。
5. **域边界**：船舶域能力的实现在 ship-performance-platform / TensorFEM 等领域仓，
   TensorPlatform 只承载机制。因此本节点对 20 项未达 REAL 的真实能力给出**路径与判据**并
   显式裁定 `blocked_domain_boundary`，在本仓完成 3 项合成域能力升级作为框架验收——
   这 3 项是**框架验收对象**，不是船舶域能力声明。
6. **跨检出差异（KNOWN_DIVERGENCE）**：本次 agent 侧 `audit_capabilities` 工具正文来自另一个
   检出的台账（12 项域化能力，如
   `fem.*`/`ice.*`/`structure.*`），与本仓渲染器解析的 21 项集合不同。A1 按本仓同源口径裁决
   （对称差 0），跨检出拓扑问题如实登记在
   `artifacts/capability_maturity/audit_table.json::live_tool_observation`，不由本节点掩盖。
7. **适用范围**：框架裁决的是「证据等级」而非物理精度本身；能力真实精度取决于领域仓
   提交的基准数据质量（提供方、种子、条件数等必须随证据上传）。

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 能力 | 升级后最大误差 | 旧实现最大误差 | 是否 <15%（不可接受线） | 是否 <3% |
|---|---|---|---|---|
| `synth.linalg.cholesky_solve` | 2.073e-14% | 2.960e+01% | 是（<15%） | 是（<3%） |
| `synth.stats.normal_ppf` | 1.041e-11% | 1.960e+05% | 是（<15%） | 是（<3%） |
| `synth.integrate.adaptive_simpson` | 4.163e-13% | 6.500e+02% | 是（<15%） | 是（<3%） |

- 三项升级能力对独立参考的误差均在 1e-11% 量级以下 → **是（<3%）**。
- 旧实现误差 synth.linalg.cholesky_solve: 2.960e+01%, synth.stats.normal_ppf: 1.960e+05%, synth.integrate.adaptive_simpson: 6.500e+02%
  → **否（>15%，不可接受）**，正是升级的对象。
- 20 项真实台账能力：本次**不给出精度数字**（其实现不在本仓，未执行基准），
  等级维持 WITHHELD/MOCK —— 声明原因：域边界，禁止在本仓臆造数值。honest FAIL/未完成项如实登记。

## 工件清单

- `artifacts/capability_maturity/audit_capabilities_capture.txt`
- `artifacts/capability_maturity/audit_table.json`
- `artifacts/capability_maturity/audit_table.md`
- `artifacts/capability_maturity/criteria_predeclaration.json`
- `artifacts/capability_maturity/evidence_chain_report.json`
- `artifacts/capability_maturity/evidence_chain_report.md`
- `artifacts/capability_maturity/trial_results.json`
- `artifacts/capability_maturity/upgrade_paths.json`
- `artifacts/capability_maturity/upgrade_paths.md`
