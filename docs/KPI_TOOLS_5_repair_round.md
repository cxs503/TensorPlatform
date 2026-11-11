# KPI-TOOLS-5 · 外部工具类接入（返修轮1：补齐 L2–L4 证据）

**锚点**：KPI-TOOLS-5 ——「≥5 类外部工具接入平台并可用」。
**本轮结论（机器可复算）**：REAL **10/5** 类，不变式 **27/27** 通过，ref-vs-got 相对误差上限 **2.827159716856e-16**（11 个可比案例，全部 <3%）。台账 `runs/KPI-TOOLS-5/20261111T203628Z`。

> 上一轮 m15147 的缺口：L2/L3 的 `entry`/`run_artifacts` 缺失、L4 无 ref-vs-got 基准。本轮把「一条命令 → 台账 + 基准表 + 图 + 集成调用 + manifest」做成入口 `scripts/run_kpi_tools_5.py`，本页由 `scripts/render_kpi_tools_5_doc.py` 从工件程序化生成（零手抄）。

---

## ① 问题设定与基准出处

**问题**：不是「某个库能不能 import」，而是「这一类外部工具在本环境里可用吗」——可用 = 探针命中 → 冒烟真调用 → 数值对得上一个**独立参考值**。任何一段缺失只能记 WITHHELD/FAIL，且**不给数字**。

**基准出处（ref_source 逐条来自规格，非本页自述）**：

| 指标 | ref | 出处 |
|---|---|---|
| `symbolic_cas.antiderivative_identity_residual` | 0 | fundamental theorem: integrate(diff(f)) - f simplifies to 0 |
| `symbolic_cas.fd_vs_symbolic_abs_err` | 0 | central finite difference vs sympy's exact derivative at x0=1.7 |
| `numerical_quadrature.quad_vs_pi_abs_err` | 0 | closed form ∫0^1 4/(1+t²)dt = π |
| `numerical_quadrature.simpson_vs_pi_abs_err` | 0 | closed form ∫0^1 4/(1+t²)dt = π (independent Simpson rule) |
| `numerical_quadrature.quad_value` | 3.14159265359 | math.pi (closed form; benchmark ref-vs-got) |
| `blackbox_optimization.best_objective` | 0 | analytic minimum of (x-0.3)²+(y+0.7)² is exactly 0 at (0.3,-0.7) |
| `blackbox_optimization.distance_to_optimum` | 0 | analytic minimiser (0.3,-0.7) |
| `automatic_differentiation.grad_vs_analytic_max_abs_err` | 0 | closed form d/dx Σx³ = 3x² (float64, 257 points) |
| `relational_store.arithmetic_answer` | 42 | integer identity 6*7 = 42 |
| `relational_store.series_sum` | 3.338335000000e+08 | Σi² for i=1..1000 = n(n+1)(2n+1)/6 = 333833500 (exact integer) |
| `version_control.head_sha_len` | 40 | git object-id width for sha1 repositories |
| `version_control.blob_id_reproduced` | 1 | re-derived sha1('blob <n>' + worktree bytes) must equal git rev-parse HEAD:<path> |
| `version_control.blob_catfile_roundtrip` | 1 | git cat-file -p <oid> must return the exact worktree bytes (object store integrity) |
| `native_build_toolchain.h10_abs_err_vs_python` | 0 | H_10 = Σ1/i (i=1..10) evaluated in CPython float64 |
| `native_build_toolchain.h10_value` | 2.92896825397 | sum(1/i for i in 1..10) in CPython float64 (cross-language ref-vs-got) |
| `hdf5_store.roundtrip_max_abs_diff` | 0 | in-memory array is the reference: write→read must be bit-identical |
| `hdf5_store.gzip_attr_preserved` | 1 | declared attribute string must survive a reopen |
| `hdf5_store.gzip_filter_active` | 1 | dataset.compression == 'gzip' after reopen (the filter, not just the bytes) |
| `plotting_engine.line_ordinate_abs_err` | 0 | axhline(y=0.25) on full-bleed axes: pixel row must decode back to y=0.25 |
| `json_contract_validation.invalid_inputs_rejected` | 3 | 3 invalid payloads are the reference count that must be rejected |
| `json_contract_validation.valid_roundtrip_ok` | 1 | valid payload round-trips through model_dump_json |

方法谱系（外部规范，不是本仓自创）：Model Context Protocol `initialize`→`tools/list`（先协商后宣告）、Python entry points 规范（按 id 查表挂载）、Kubernetes liveness/readiness probes（running ≠ ready）、ISO/IEC 25010 互操作性、Fowler Plugin+Registry。

## ② 方法与实现（方程/格式出处 + 关键代码指针）

| 文件 | 职责 | 关键指针 |
|---|---|---|
| `data/external_tools/classes.json` | 声明式规格：10 类工具、探针、不变式、`ref`/`ref_source` | `classes[].invariants[]` |
| `tools/external_tools/probe.py` | 可用性观测（module/executable/command） | `probe()` |
| `tools/external_tools/adapters.py` | 每类一次真实调用，返回可观测量 | `ADAPTERS`；`version_control()` 的 blob 复算；`plotting_engine()` 的坐标反解；`relational_store()` 的 Σi² 恒等式 |
| `tools/external_tools/spec.py` | 规格校验 + 参考值字段（`ref`/`ref_source`）+ 证据等级滚降 | `Invariant`、`weakest()` |
| `tools/external_tools/harness.py` | 逐类判定、覆盖度、双指纹；指标保留 16 位有效数字 | `run_suite()`、`verdict()`、`_round()` |
| `tools/external_tools/benchmark.py` | ref→got→误差 的机裁表（无 ref 即 `unscored`） | `build()`、`render_markdown()` |
| `scripts/run_kpi_tools_5.py` | **入口**：一次运行产出全部工件 | `main()`、`_figures()`、`_api_integration()` |
| `backend/services/external_tools_service.py` | 平台服务层（规格/台账路径可注入） | `catalog/coverage/benchmark/probe` |
| `backend/api/external_tools.py` | HTTP 面（classes/coverage/benchmark/probe） | `@router.get('/benchmark')` |

**三条载重规则**：① 失效不静默（缺工具→WITHHELD 且 `metrics={}`）；② 不变式裁决、适配器只观测（适配器自称 REAL 而不变式不过 → FAIL）；③ 无参考值不给百分比（`rel_err=None`，标 `unscored`）。

## ③ 参数表

| 参数 | 值 | 说明 |
|---|---|---|
| 目标类数（KPI 下界） | 5 | `target_classes` |
| 声明类数 | 10 | 10 个不同 category |
| 优化预算 | 200 trials, seed=7 | TPE 采样（敏感性见 ⑤） |
| 积分参考 | ∫₀¹ 4/(1+t²)dt = π | 自适应 quad + 固定 1000 面板 Simpson 两条独立规则 |
| 自微分 | Σx³, 257 点, float64 | 对比闭式 3x² |
| 数据库恒等式 | Σi² (i=1..1000) = 333833500 | 闭式 n(n+1)(2n+1)/6，整数精确 |
| VCS | sha1 blob 宽度 40 | 由工作树字节反算 `sha1('blob <n>\0'+bytes)` |
| 绘图 | 3in × 3in @100dpi, full-bleed axes | y=0.25 横线像素行反解回数据坐标 |
| 容差带 | 3% / 15% | 平台精度标准：>15% 不可接受 |

## ④ 对照表（逐项 ref vs got + 误差 + 判据）

| case | ref | got | abs_err | rel_err | tol | 判据 | ref_source |
|---|---|---|---|---|---|---|---|
| `symbolic_cas.antiderivative_identity_residual` | 0 | 0 | 0 | — | 0 | ✓ | fundamental theorem: integrate(diff(f)) - f simplifies to 0 |
| `symbolic_cas.fd_vs_symbolic_abs_err` | 0 | 1.119678572081e-09 | 1.119678572081e-09 | — | 1.000000000000e-04 | ✓ | central finite difference vs sympy's exact derivative at x0=1.7 |
| `numerical_quadrature.quad_vs_pi_abs_err` | 0 | 4.440892098501e-16 | 4.440892098501e-16 | — | 1.000000000000e-10 | ✓ | closed form ∫0^1 4/(1+t²)dt = π |
| `numerical_quadrature.simpson_vs_pi_abs_err` | 0 | 2.664535259100e-15 | 2.664535259100e-15 | — | 1.000000000000e-06 | ✓ | closed form ∫0^1 4/(1+t²)dt = π (independent Simpson rule) |
| `numerical_quadrature.quad_value` | 3.14159265359 | 3.14159265359 | 8.881784197001e-16 | 2.827159716856e-16 | 1.000000000000e-10 | ✓ | math.pi (closed form; benchmark ref-vs-got) |
| `blackbox_optimization.best_objective` | 0 | 2.336827309380e-04 | 2.336827309380e-04 | — | 0.001 | ✓ | analytic minimum of (x-0.3)²+(y+0.7)² is exactly 0 at (0.3,-0.7) |
| `blackbox_optimization.distance_to_optimum` | 0 | 0.0152866847596 | 0.0152866847596 | — | 0.05 | ✓ | analytic minimiser (0.3,-0.7) |
| `automatic_differentiation.grad_vs_analytic_max_abs_err` | 0 | 0 | 0 | — | 1.000000000000e-10 | ✓ | closed form d/dx Σx³ = 3x² (float64, 257 points) |
| `relational_store.arithmetic_answer` | 42 | 42 | 0 | 0 | 0 | ✓ | integer identity 6*7 = 42 |
| `relational_store.series_sum` | 3.338335000000e+08 | 3.338335000000e+08 | 0 | 0 | 0 | ✓ | Σi² for i=1..1000 = n(n+1)(2n+1)/6 = 333833500 (exact integer) |
| `relational_store.server_version_len` | — | 119 | — | — | 20 | ✓ | — |
| `relational_store.public_table_count` | — | 190 | — | — | 1 | ✓ | information_schema.tables: a reachable DB with an empty catalog is not the platform DB |
| `version_control.head_sha_len` | 40 | 40 | 0 | 0 | 0 | ✓ | git object-id width for sha1 repositories |
| `version_control.commit_count` | — | 2136 | — | — | 1 | ✓ | — |
| `version_control.blob_id_reproduced` | 1 | 1 | 0 | 0 | 1 | ✓ | re-derived sha1('blob <n>' + worktree bytes) must equal git rev-parse HEAD:<path> |
| `version_control.blob_catfile_roundtrip` | 1 | 1 | 0 | 0 | 1 | ✓ | git cat-file -p <oid> must return the exact worktree bytes (object store integrity) |
| `native_build_toolchain.h10_abs_err_vs_python` | 0 | 0 | 0 | — | 1.000000000000e-12 | ✓ | H_10 = Σ1/i (i=1..10) evaluated in CPython float64 |
| `native_build_toolchain.h10_value` | 2.92896825397 | 2.92896825397 | 4.440892098501e-16 | 1.516196733264e-16 | 1.000000000000e-15 | ✓ | sum(1/i for i in 1..10) in CPython float64 (cross-language ref-vs-got) |
| `hdf5_store.roundtrip_max_abs_diff` | 0 | 0 | 0 | — | 0 | ✓ | in-memory array is the reference: write→read must be bit-identical |
| `hdf5_store.gzip_attr_preserved` | 1 | 1 | 0 | 0 | 1 | ✓ | declared attribute string must survive a reopen |
| `hdf5_store.gzip_filter_active` | 1 | 1 | 0 | 0 | 1 | ✓ | dataset.compression == 'gzip' after reopen (the filter, not just the bytes) |
| `plotting_engine.png_magic_ok` | — | 1 | — | — | 1 | ✓ | — |
| `plotting_engine.png_bytes` | — | 1176 | — | — | 1000 | ✓ | — |
| `plotting_engine.line_ordinate_abs_err` | 0 | 0.00166666666667 | 0.00166666666667 | — | 0.01 | ✓ | axhline(y=0.25) on full-bleed axes: pixel row must decode back to y=0.25 |
| `plotting_engine.ink_pixel_fraction` | — | 0.00333333333333 | — | — | 0.002 | ✓ | a rendered line covers more than 0.2% of the canvas |
| `json_contract_validation.invalid_inputs_rejected` | 3 | 3 | 0 | 0 | 3 | ✓ | 3 invalid payloads are the reference count that must be rejected |
| `json_contract_validation.valid_roundtrip_ok` | 1 | 1 | 0 | 0 | 1 | ✓ | valid payload round-trips through model_dump_json |

**统计**：cases=27，value=21，相对误差可比 11，仅绝对误差 10，unscored 6；通过 27/27；<3% 11 项，<15% 11 项。

仅绝对误差的行（参考值为精确 0，相对误差无定义——除以零只会编出百分比）：

- `symbolic_cas.antiderivative_identity_residual`: |got − 0| = 0，容差 0
- `symbolic_cas.fd_vs_symbolic_abs_err`: |got − 0| = 1.119678572081e-09，容差 1.000000000000e-04
- `numerical_quadrature.quad_vs_pi_abs_err`: |got − 0| = 4.440892098501e-16，容差 1.000000000000e-10
- `numerical_quadrature.simpson_vs_pi_abs_err`: |got − 0| = 2.664535259100e-15，容差 1.000000000000e-06
- `blackbox_optimization.best_objective`: |got − 0| = 2.336827309380e-04，容差 0.001
- `blackbox_optimization.distance_to_optimum`: |got − 0| = 0.0152866847596，容差 0.05
- `automatic_differentiation.grad_vs_analytic_max_abs_err`: |got − 0| = 0，容差 1.000000000000e-10
- `native_build_toolchain.h10_abs_err_vs_python`: |got − 0| = 0，容差 1.000000000000e-12
- `hdf5_store.roundtrip_max_abs_diff`: |got − 0| = 0，容差 0
- `plotting_engine.line_ordinate_abs_err`: |got − 0| = 0.00166666666667，容差 0.01

## ⑤ 收敛性 / 参数敏感性

### 5.1 优化预算（TPE trials）——三个参数点

| trials | best_objective | distance_to_optimum | 该类状态 | 来源 |
|---|---|---|---|---|
| 200 | 2.336827309380e-04 | 0.0152866847596 | REAL | `runs/KPI-TOOLS-5/20261111T203628Z` |
| 80 | 0.00503341050447 | 0.0709465327163 | FAIL | `runs/KPI-TOOLS-5/sensitivity_trials80/20261111T203641Z` |
| 40 | 0.00928929947852 | 0.0963810120227 | FAIL | `runs/KPI-TOOLS-5/sensitivity_trials40/20261111T203650Z` |

判读：容差 1e-3 是 **200-trial** 的 claim；80/40 trials 时目标值超限 → 该类诚实判 FAIL，KPI 覆盖仍 ≥5 类（不受影响）。这是把「预算不足」变成可观测事实，而不是调低阈值。

### 5.2 两条独立积分规则（同一被积函数的两种格式）

- `numerical_quadrature.quad_vs_pi_abs_err`: got=4.440892098501e-16, abs_err=4.440892098501e-16
- `numerical_quadrature.simpson_vs_pi_abs_err`: got=2.664535259100e-15, abs_err=2.664535259100e-15
- `numerical_quadrature.quad_value`: got=3.14159265359, abs_err=8.881784197001e-16

## ⑥ 失败模式与工件

**失败模式（已实测，不是设想）**

1. 凭证缺失 → `relational_store` 曾整类 WITHHELD。本轮加「本地 unix socket + peer 认证」候选；且表计数改用 `pg_catalog.pg_class`（`information_schema` 会按权限过滤，受限角色会读到 0 张表）。
2. 常量级判据不可证伪：`head_sha_len=40`、`png_bytes≥1000` 这类判据只证明「跑过」。本轮补了非恒真锚：git blob id 由工作树字节反算、matplotlib 像素行反解回数据坐标、SQL 做整数恒等式。
3. 台账把指标四舍五入到 12 位有效数字 → 基准表把量化误差误当物理误差。改为 16 位（判据仍在未舍入值上裁决）。
4. 优化类随预算失败（见 ⑤），不得用调低阈值掩盖。

**集成调用（平台侧真调用）**：

| method | path | status | response digest |
|---|---|---|---|
| GET | `/api/v1/external-tools/classes` | 200 | `b106a72f9d6a6152` |
| GET | `/api/v1/external-tools/coverage` | 200 | `3cac377c7c60c794` |
| GET | `/api/v1/external-tools/benchmark` | 200 | `7bc1640e5b562976` |
| POST | `/api/v1/external-tools/probe` | 200 | `394008c103625122` |

调用链：backend/api/external_tools.py -> services/external_tools_service.py；整体 ok=是。（`probe` 子集探测按设计 `partial=True` → verdict=FAIL，属预期语义。）

**工件与复现**：

- repro_cmd：`python3 scripts/run_kpi_tools_5.py --out-dir /root/tp-agent/work/m15199/runs/KPI-TOOLS-5`
- run_dir：`runs/KPI-TOOLS-5/20261111T203628Z`；git HEAD `1649792e5475f16e4cd136f926cd2a0179688c74`
- 规格 sha256：`3eebbef6c723c3c469cdf07392474a7c8f6fa15f1fafab8f1111b270bee80706`
- manifest（每个工件的 sha256/字节数）：

| 工件 | bytes | sha256 |
|---|---|---|
| `benchmark.json` | 15759 | `1dddec763b4d35f3bfaf23d357f5da5e69e1e1f691ab9e272766fb8d6d7c18fe` |
| `benchmark.md` | 4403 | `53116000b30cdf858f657d80b78f25804b9ff1e45b2568bc58c9de02081fca74` |
| `env.json` | 686 | `92f7815d4e3734be0ef613360bba048e20df354f26468b12a6bf1026564581a2` |
| `figures/benchmark_summary.png` | 169658 | `48adce0c81d982b465961298210a4c44b369199efb705cbf060942827a1ceaa7` |
| `integration.json` | 1247 | `199ce4741bf82e2801a07f7be3f3d3972ba389caaf56ec63f8276a18eda51c50` |
| `latest.json` | 15373 | `a04c5b6e2c54ef435af6c4c6e04b686d6f2776713cc5f7f1ed57b23a81ea289b` |
| `ledger_20261111T203636.json` | 15373 | `a04c5b6e2c54ef435af6c4c6e04b686d6f2776713cc5f7f1ed57b23a81ea289b` |
| `ledger_20261111T203636.md` | 4895 | `6a1494eff02cea9cefc018b5b7933a6a9c39146a27dff2074b1f6d9833d48f08` |
| `plotting_engine_smoke.png` | 1176 | `4a7bd87ad926e8c25bf963d117cf968c215e75b2eede5fa2f6ac6ff557671c83` |
| `repro.json` | 324 | `8e1c1a2504855b21680c8702d6259404033b83e0bccff4065d382e7c562191b0` |

## ⑦ 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 关键量 | ref | got | 相对误差 | <3%？ |
|---|---|---|---|---|
| `numerical_quadrature.quad_value` | 3.14159265359 | 3.14159265359 | 2.827159716856e-16 | 是 |
| `relational_store.arithmetic_answer` | 42 | 42 | 0 | 是 |
| `relational_store.series_sum` | 3.338335000000e+08 | 3.338335000000e+08 | 0 | 是 |
| `version_control.head_sha_len` | 40 | 40 | 0 | 是 |
| `version_control.blob_id_reproduced` | 1 | 1 | 0 | 是 |
| `version_control.blob_catfile_roundtrip` | 1 | 1 | 0 | 是 |
| `native_build_toolchain.h10_value` | 2.92896825397 | 2.92896825397 | 1.516196733264e-16 | 是 |
| `hdf5_store.gzip_attr_preserved` | 1 | 1 | 0 | 是 |
| `hdf5_store.gzip_filter_active` | 1 | 1 | 0 | 是 |
| `json_contract_validation.invalid_inputs_rejected` | 3 | 3 | 0 | 是 |
| `json_contract_validation.valid_roundtrip_ok` | 1 | 1 | 0 | 是 |

结论：11 个可比关键量全部 <3%（最大 2.827159716856e-16）。仅绝对误差的恒等式残差项列在 ④，其参考值是精确 0，相对误差无定义，未做百分比粉饰。

**环境**：python=3.11.6 platform=Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38 packages={'exe:gcc': 'gcc (GCC) 12.3.0 20230508 (Anolis OS 12.3.0-9)', 'exe:git': 'git version 2.41.0', 'h5py': '3.15.1', 'matplotlib': '3.10.8', 'optuna': '5.0.0', 'pydantic': '2.13.3', 'scipy': '1.16.0', 'sympy': '1.14.0', 'torch': '2.7.1'}
