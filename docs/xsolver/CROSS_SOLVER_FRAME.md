# 跨求解器验证口径统一框架（`services.xsolver`）

节点：`p83w1-n07-cross-solver-schema-maturity`（战役 DAG 成熟化单，mission 7119）
落库：`TensorPlatform/backend/services/xsolver/` + `schemas/xsolver/` + `config/xsolver/`
证据：`artifacts/xsolver/acceptance.json`（机器判据数字）、`artifacts/xsolver/adjudication.json`（互裁报告）、
`artifacts/xsolver/verdicts/*.json`（DAG 节点消费文档）、`artifacts/xsolver/probe/*.json`（守恒实测量）

---

## 1. 案例介绍（物理背景）

同一物理问题（同一几何、同一 Froude 数、同一无量纲化）在不同求解器里会给出不同的
总阻力系数 `C_T`，而"差多少算不一致"从来不是数值问题，先是**口径问题**：
摩擦阻力求解器只报 `C_F`，势流求解器只报 `C_W`，全场求解器报 `C_F+C_W+C_R`。
三者相较，`C_T` 可以差 99%，但这个 99% 里没有一分是数值分歧——它是定义分歧。
不把口径固定下来就做跨求解器比较，得到的"差异"衡量的是比较方法本身，而不是求解器。

本框架因此把四件事机制化：

| 部件 | 作用 | 代码 |
|---|---|---|
| 输入 schema | `geometry / physics / solver / condition / provenance` 五段固定 | `schemas/xsolver/input_schema.json` |
| 输出 schema | `primary_quantities / canonical_quantities / derived / conservation_residuals / convergence` | `schemas/xsolver/output_schema.json` |
| 守恒残差门禁 | 质量/动量/能量残差 > 家族容忍度 → `conservation_fail`，**禁止**进入比较集 | `conservation.py` |
| 自动互裁引擎 | 同一 `problem_id` 全部臂自动提取 `C_T/C_F/C_W`，差异逐条归因 | `adjudicate.py` |
| DAG 交付 | 判决文档 → `NodeVerdictUpdate` → `POST /tensor/campaigns/nodes/{id}/verdict` | `dag_consumer.py`、`dag_push.py` |

互裁引擎只承认五类**可举证**归因（`term_coverage` / `normalization` /
`discretisation` / `model_formulation` / `reference_anchor_agreement`），
其余一律落 `unattributed` 原样上报——绝不把归不了因的差异四舍五入成"一致"。

## 2. 怎么做的（方法/流程）

```
ecosystem 检出 ──glob──► 只收带显式 verdict 的产物 ──► 规范化（别名/量纲/百分比）
      │                                                     │
      │                                          input_digest + solver_version + git_commit
      ▼                                                     ▼
artifacts/xsolver/corpus/corpus.json  ──►  守恒门禁（family 容忍度）──► 排除 conservation_fail
                                                │
                                                ▼
                                       互裁（同 problem_id × 同 condition 配对）
                                                │
                                   归因（term coverage / normalization / discretisation）
                                                ▼
                                   verdict 文档 ──► NodeVerdictUpdate ──► DAG 节点
```

* 语料：150 个产物文件中 95 个带显式 verdict 被提升，加 9 个 Wigley III 矩阵展开
  + 3 个 linkage + 1 个实测探针 = **106 条规范化案例**。
* 配对铁律：只在**同一 condition**内配对（比对两个 Froude 数产生的是工况差，不是求解器差）。
* 守恒门禁先于任何系数运算：不守恒的臂参与比较，量到的是它的失效模式。

## 3. 计算结果（全部由 `artifacts/xsolver/acceptance.json` 程序化导出）

| 判据 | 门槛 | 实测 | 状态 |
|---|---|---|---|
| C1 几何解析率 | ≥ 0.90 | **0.9717**（103/106） | PASS |
| C1 主量解析率 | ≥ 0.80 | **0.9811**（104/106） | PASS |
| C1 语料规模 | ≥ 74 | **106** | PASS |
| C2 LB 门禁质量/动量 | 1e-6 / 1e-4 | 配置逐位一致 | PASS |
| C2 内核实测（horizon 100） | < 1e-6 / < 1e-4 | **7.105e-15 / 7.216e-15** | PASS |
| C2 内核漂移 | — | **6.66e-17 /步**，1e-6 要到 1.5e10 步 | — |
| C2 语料暴露（诚实披露） | — | 16 臂记质量，**12 臂超 1e-6**（最大 3.2e-2，Zou–He 开边界长跑） | 门禁全部拦下 |
| C3 三求解器互裁 | 自动提取+归因 | 3 臂、9 组 `C_T` 配对、15 条归因、`C_T` 最大相对差 1.00 | PASS |
| C4 负控 | 三项全触发 | 删 `primary_quantities`→schema 报错；质量残差置 1.0→`conservation_fail` 且被剥离；域外模块→`ProvenanceError` | PASS |
| C5 可追溯 | 100 % | **106/106** 带 `input_digest`+`solver_version`+`git_commit` | PASS |
| C6 DAG 消费 | schema 零错 + 载荷合法 | 判决文档过 `verdict_schema`，投影到真实 `NodeVerdictUpdate` | PASS |

`C_T` 提取值（Wigley III，Fn=0.3）：TensorBEM `5.064e-4`（仅 C_W）、TensorLBM
`1.500e-2`（仅 C_F）、TensorFVM `2.112e-2`（C_F+C_R+C_W）。三值互差 100 %，
归因判定为 `term_coverage`：**三臂报的不是同一个物理量**，因此该问题组状态是
`not_comparable`，DAG 判决为 `fail`——这是本框架的**结论**，不是框架的失败。

## 4. 图

| 图 | 内容 |
|---|---|
| `fig1_term_coverage.png` | 三臂在各 Froude 数下的 `C_T/C_F/C_W`：摩擦臂只有 `C_F`、势流臂只有 `C_W`、全场臂三项齐全 |
| `fig2_static_closure.png` | 各臂 `Cb` 相对闭式解的偏差（体素离散 ~1.3 %） |
| `fig3_schema_coverage.png` | 语料几何/输出量/规范量/溯源四类解析率 |
| `fig4_provenance_mass_defect.png` | 同一探针两种 import 来源的质量漂移曲线（见 §5.1） |

## 5. 注意点（踩过的坑 / 局限）

### 5.1 最重要的坑：数字测了，但测的不是那个 commit

原实现用 `importlib.import_module("tensorlbm")` 取后端，然后记录
`/root/tensor-ecosystem/TensorLBM` 的 `git_commit`。本机 `tensorlbm` 装了多份
**可编辑安装**（editable install 走 `sys.meta_path`，优先级高于 `sys.path`），
于是探针实际测的是 `/root/tp-agent/work/m6150/src` 的旧副本，却把残差写在
另一个 commit 名下：

| import 来源 | horizon=100 质量残差 | 漂移/步 | 1e-6 穿越步数 |
|---|---|---|---|
| 域外 worktree（m6150，旧副本） | **9.313e-07** | 9.31e-09 | 107 步 |
| 声明仓（421f043e，`W_EXACT` 权重） | **7.105e-15** | 6.66e-17 | 1.5e10 步 |

两者差 **8 个数量级**。更糟的是 9.3e-7 与 1e-6 只差 7 %，写报告时会得到一句
"内核勉强达标"——既可能把坏的内核certify成好的，也可能把好的内核判成坏的。
修复：按文件位置钉住仓内包（`_pin_to_repo`）、校验 `module_file` 在声明仓内、
把 `module_file/module_sha256/shadow_copies` 与残差一起存档，来源不可证即
`ProvenanceError`（→ C2 WITHHELD，绝不发布不可归因的数字）。
回归测试 `tests/xsolver/test_xsolver_probe_provenance.py` 同时复现旧行为
（裸 import 命中影子副本）与新行为（钉住后仍取声明仓）。

**一般化**：任何"跨仓测量"都要把被测代码的**物理位置**记进证据，而不是记它的 git 描述。

### 5.2 其他局限（明确声明）

* 能量守恒对等温 LBM **不成立**（无能量方程），框架报 `energy: null` 并说明，
  不参与门禁；因此等温 LB 案例的总状态是 `WARN` 而非 `PASS`。
* 语料 12 个 LB 臂质量残差超 1e-6（最多 3.2e-2，poiseuille 49400 步 Zou–He 开边界长跑）。
  这是**物理/边界格式的真实暴露**而非框架缺陷，门禁把它们全部剥离出比较集；
  但它们也说明"LBM 案例质量守恒 < 1e-6"作为**语料级**断言目前不成立，
  只有**内核级**（周期盒、无壁面）断言成立。本节点按后者验收，前者如实入账。
* `canonical_quantities` 解析率仅 0.2547：多数案例不算阻力系数，不是解析失败。
* 语料采集依赖 `/root/tensor-ecosystem` 实时状态；已通过 corpus 快照 + digest 固定，
  快照内 `adjudication` 完全确定（`report_digest` 不含墙钟，重跑逐位一致）。
* Wigley III 三臂问题**当前不可互裁**（项覆盖不相交），需要统一到"全场分解"口径
  才能做真正的三求解器 `C_T` 比较——这是后续节点，不是本单遗留 bug。

## 6. 误差判定（显式回答"是否 < 3%"）

| 关键量 | 基准 | 误差 | < 3 %？ |
|---|---|---|---|
| 内核质量守恒（horizon 100） | 解析值 0（周期盒 BGK） | 7.105e-15 | **是**（远优） |
| 内核动量守恒（horizon 100） | 解析值 0 | 7.216e-15 | **是** |
| `Cb`（体素离散，TensorFVM 臂） | 闭式 `Cb` | ~1.3 % | **是**（fig2） |
| 三臂 `C_T` 互差 | 无共同基准（项覆盖不相交） | 100 %（定义差） | **不适用**：不是误差，是不可比；框架判 `not_comparable` |

## 7. 复现

```bash
make xsolver-test          # 41 项 pytest，15 s（合成夹具 + 快照语料，无外部依赖）
make xsolver-acceptance    # 全量验收，~2 min，写 artifacts/xsolver/*
make xsolver-provenance    # 两种 import 来源的质量漂移对照（fig4）
```