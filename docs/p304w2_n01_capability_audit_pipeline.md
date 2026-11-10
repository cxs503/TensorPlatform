# P304W2-N01 能力台账自动审计管线（TensorPlatform）

> 节点：`p304w2-n01-capability-audit-automation`｜仓：TensorPlatform｜产物：`tools/capability_audit_gate/` + `config/capability_audit_gate/` + `artifacts/capability_audit_gate/`｜判据：C1–C6 全部机器可算

## 1. 案例介绍（这是什么、为什么需要）

能力台账（21 项平台能力）此前是**人工维护的 JSON**：等级（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）写着，但没有任何机器复算——删一个测试、把算法换成常量、把基准工件扔掉，台账不会变红。本节点把「等级」变成**从代码结构/测试存在性/基准数值自动推导**的可复算判定，并把「等级下降」变成 CI 红灯。

零领域语义：能力清单、证据路径绑定、基准口径全在配置/工件里（`config/capability_audit_gate/`），`tools/capability_audit_gate/` 只放机制；测试夹具使用合成能力名（`alpha.compute`…）。

参数表（判据与阈值，**先于观测声明**，随工件落盘）：

| 参数 | 值 | 来源 |
|---|---|---|
| 五级偏序 | NOT_SUPPORTED < STUB < MOCK < WITHHELD < REAL | `taxonomy.py` |
| REAL 数值容差 | 3.0 % | `spec.DEFAULT_THRESHOLDS` |
| 不可接受误差 | 15.0 % | 同上 |
| STUB 空壳占比 | ≥ 0.5 | 同上 |
| MOCK 独立信号族 | ≥ 2 | 同上 |
| 一致率门 | ≥ 90 % 且标注 ≥10 项 | CLI 默认 |
| 运行时限 | < 60 s | 战役判据 C5 |

## 2. 怎么做的（方法与工具链）

```
spec.json / curated.json ─► resolve（显式绑定优先，路径词元兜底）
                              │
                              ▼
                   scan（AST 信号 + 基准工件数值）──► cache（内容 sha256 增量）
                              │
                              ▼
                   rules（五级级联 cag-rules-v2，理由码 + 证据链）
                     │                    │
                     ▼                    ▼
             report（JSON+MD）      gate（冻结基线 / 降级 / ack / 规则版本）
                                          │
                                          ▼
                             drill（REAL→MOCK 注入演练，C3 的机器证据）
```

关键设计（为什么不用「文档里出现 PASS 就算通过」）：判定只吃**结构信号 + 落盘基准数值**，模式匹配解析到的证据默认不做正面反证（`pattern_can_refute=false`），未绑定证据一律 WITHHELD（拒绝下判决而不是判缺失）。

## 3. 计算结果（判据逐项，程序化注入）

| 判据 | 实测 | 阈值 | 结论 | 产物 |
|---|---|---|---|---|
| C1 覆盖率 | 100.0%（21/21） | 100% | PASS | `artifacts/capability_audit_gate/report.json` |
| C2 一致率（主：均衡金标准 10 项） | 100.0%（多数类基线 30.0%，增益 +70.0pp） | ≥90% | PASS | `artifacts/capability_audit_gate/golden/report.json` |
| C2 一致率（副：外部台账对照 21 项） | 95.2%（多数类基线 90.5%，增益 +4.8pp） | ≥90% | PASS | `artifacts/capability_audit_gate/criteria.json` |
| C3 降级检出（金标准） | 2/2 = 100% | 100% | PASS | `artifacts/capability_audit_gate/drill/drill.json` |
| C3 降级检出（真台账） | 1/1 = 100% | 100% | PASS | `artifacts/capability_audit_gate/drill_real/drill.json` |
| C3 端到端负控（CLI 退出码） | exit=1 | 1（CI 红） | PASS | `artifacts/capability_audit_gate/evidence/ci_red_proof.txt` |
| C4 证据链完整 | 21/21 | 100% | PASS | `report.md` §3 |
| C5 运行时限（冷跑） | 0.93s | <60s | PASS | `criteria.json` |
| C6 pytest 用例数 | 64 | ≥15 | PASS | `tests/capability_audit_gate/` |

**全部判据：PASS**

### 3b. 逐能力证据链（21 项，节选三面齐全者与唯二 REAL）

| 能力 | 等级 | 实现文件 | 测试名 | 基准工件 | 最差误差% | 容差% | 理由码 |
|---|---|---|---|---|---|---|---|
| `calc.resistance` | WITHHELD | `backend/api/calculations.py` | `test_benchmark_validate_no_mocks` | `artifacts/benchmark_validation.json` | 68.89 | 3.0 | BENCHMARK_OVER_TOLERANCE |
| `calc.stability` | REAL | `backend/api/calculations.py` | `test_api_curve_monotone_up_to_max` | `artifacts/m1564/results/comparison.json` | 0.000266 | 3.0 | NUMERIC_VERIFICATION_PASS |
| `propeller.open_water` | WITHHELD | `backend/api/domain/propeller_open_water.py` | `test_batch_executor_uses_bem_model` | — | — | — | NO_BENCHMARK |

## 4. 结果图

**图 1** 五级分布（真台账 21 项 vs 均衡金标准 10 项）

![fig1](artifacts/capability_audit_gate/figures/fig1_level_distribution.png)

**图 2** 一致率 vs 多数类基线（C2 的关键对照：高一致率不能只是类别不平衡）

![fig2](artifacts/capability_audit_gate/figures/fig2_agreement_vs_baseline.png)

**图 3** 回归演练：注入 REAL→MOCK 的检出数（漏检必须为 0）

![fig3](artifacts/capability_audit_gate/figures/fig3_regression_drill.png)

**图 4** 运行时限 vs 60s 预算（对数轴）

![fig4](artifacts/capability_audit_gate/figures/fig4_runtime_budget.png)

**图 5** 逐能力证据三面（实现/测试/基准）在场情况

![fig5](artifacts/capability_audit_gate/figures/fig5_evidence_facets.png)

## 5. 注意点（踩过的坑与适用范围）

1. **CI 回归门曾经是死的**：`capability-audit.yml` 第 4 步引用 `artifacts/capability_audit_gate/baseline.json`，但该文件从未入仓 → CLI 以退出码 4（配置错误）中止，PR 上看到的「红」是配置缺失而不是能力退化，C3 名存实亡。修复=把基线冻结成**版本化数据** `config/capability_audit_gate/baseline.json` + 新增基线在场门 + 新增 6 条测试（含「基线必须与当前树一致」），并把注入演练下发到 CI。
2. **一致率的类别不平衡陷阱**：真台账 21 项里 20 项为 WITHHELD，「全判 WITHHELD」的多数类基线就有 90.5%；因此 C2 的**主证据是均衡金标准语料**（10 项五级各 2 项，多数类基线 30%），外部台账对照只作为**副证据**并显式标注增益（见报告 §3 与图 2）。
3. **静态结构信号不执行代码**：运行期才暴露的缺陷（数值发散、随机种子未固定、并发竞态）不在覆盖范围，需配合在线探针（`tools/capability_audit`）。
4. **基准工件是历史快照**：REAL 只代表「该工件所测版本 + 该 sha256」，代码改动而工件未重跑时等级仍可能显示 REAL——CI 每晚随全量巡检复跑 + 增量缓存按内容 sha256 失效，缩小但不能消除该窗口。
5. **未绑定证据 ⇒ WITHHELD 不是「不支持」**：21 项里 9 项因无显式绑定而 WITHHELD，这是拒绝下判决，不能读成「实现不存在」（后者要求绑定路径存在但文件缺失）。
6. **`calc.resistance` 的 68.89% 需域侧复核**：该值来自工件 `artifacts/benchmark_validation.json` 内 12 条算例的最差值（aggregate=worst）；若该工件混入了对照模型口径、而非单一模型的验证算例，则 WITHHELD 属保守误判——审计器只按 spec 声明聚合，不替域做口径解释，这条留给域侧把工件口径写清。
7. **演练用子集工作树**：真台账树过大（>5000 文件）时 drill 只复制 spec 引用文件，非目标能力的等级可能漂移（`collateral_level_moves` 已记录，不参与判定）。

## 6. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 实测 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 构成判据的 REAL 判定（`calc.stability`） | GZ 曲线最大相对误差 2.66e-4（见证据链） | 闭式解（`artifacts/m1564/results/comparison.json`） | 2.66e-4 % | 是 |
| C2 一致率（金标准 10 项） | 100.0% | 预声明阈值 90% | 10.0 pp | 不适用（比例判据，超阈值即合格） |
| C3 检出率 | 100% / 100% | 100% | 0 pp | 不适用（计数判据） |
| C5 运行时间 | 0.93s | 60s | −59.1s | 不适用（时限判据） |

说明：本节点交付的是**审计机制**而非物理求解器，唯一参与数值判定的物理量是 `calc.stability` 的 GZ 对比（误差 2.66e-4 %，远小于 3%）；其余判据是覆盖/一致/检出/时限类**计数与比例判据**，其「误差」含义是相对预声明阈值的余量，已在表中列出，不冒充物理误差。`calc.resistance` 因基准最差误差 68.9% > 15% 被判 WITHHELD（诚实保留，不因历史声称 REAL 而放行）。

## 7. 复现命令

```bash
python3 -m pytest -q tests/capability_audit_gate
python3 -m tools.capability_audit_gate.cli audit --spec config/capability_audit_gate/golden/spec.json \
  --root config/capability_audit_gate/golden --out artifacts/capability_audit_gate/golden --enforce-acceptance
python3 -m tools.capability_audit_gate.cli drill --spec config/capability_audit_gate/golden/spec.json \
  --root config/capability_audit_gate/golden --out artifacts/capability_audit_gate/drill
python3 -m tools.capability_audit_gate.cli drill --spec config/capability_audit_gate/spec.json \
  --root . --out artifacts/capability_audit_gate/drill_real
python3 -m tools.capability_audit_gate.cli audit --spec config/capability_audit_gate/spec.json \
  --root . --out artifacts/capability_audit_gate --cache /tmp/cag_cache.json \
  --baseline config/capability_audit_gate/baseline.json \
  --contrast artifacts/capability_ledger/capability_ledger_resolved.json \
  --fail-on-gate --enforce-acceptance
python3 scripts/p304w2_n01_capability_audit_report.py   # 本报告 + 图 + criteria_report.json
```
