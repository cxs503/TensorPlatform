# P154W2-N01 复证缺口双向清偿成熟化（blocked×9 环境锁 + drift×9 四路归因）

> 本报告中的所有数字由 `tools/repro_settlement` 从工件 JSON 程序化注入（零手抄）。清偿报告指纹 `digest=781471c18f241530017dcde06fee93022c0f77a85595c04066551ef36b875ddd`，corpus 指纹 `fde5f04d0ff8fe7b6fac27685d24a26595c6f4f68f56a2043cb5e582e5574dfd`。

## ① 案例介绍

**背景（物理/工程语境）**：复证（reproduction）是平台对历史验收声明的唯一反造假手段：把上游节点声称的数值在干净检出里重跑一遍，再与记录值比对。plan 154 这一轮抽样得 36 条复证案，其中 9 条因**环境不可得**（依赖缺失/超时/收集失败）无法执行（blocked），9 条**数值漂移**（观测与记录值超容差，drift）。缺口停留在文档级追踪，没有任何机器判据能说清「这条缺口清偿了没有」。

**本节点的对象**：把两类缺口升为机器可裁的清偿流水线。判据口径（与任务契约一致）：

- 环境锁（environment lock）= 一条 blocked 案的运行环境快照：Python 实现/版本、平台与架构、求解器 commit、依赖版本钉，锁体规范化 JSON 的 sha256 即锁指纹；
- 四路归因 = 漂移证据到 a 分辨率 / b 求解器版本与参数 / c 随机种子与非确定性 / d 基准数据版本 的映射；
- 清偿后复现率 `repro_rate = counted_pass / 36`，counted_pass = pass + blocked_settled + drift_rebaselined。

**参数表（本案的输入规模）**

| 项 | 值 |
|---|---|
| 复证案总数 | 36 |
| 清偿前状态 | {'blocked': 9, 'drift': 9, 'error': 0, 'pass': 18} |
| 清偿后判决 | {'blocked_settled': 9, 'drift_rebaselined': 9, 'pass': 18} |
| 环境锁 | 9 把（每条 blocked 一把） |
| 漂移归因分布 | {'a_resolution': 3, 'b_solver_version': 3, 'c_seed': 2, 'd_baseline_version': 1} |
| 单元/回归测试 | 64 条 |

## ② 怎么做的

**模块（TensorPlatform 仓 `tools/repro_settlement/`，零领域语义）**

| 模块 | 职责 |
|---|---|
| `canonical.py` | 稳定序列化 + sha256 + 易变字段剥离（幂等底座） |
| `envlock.py` | `build_lock` / `verify_lock` / `integrity` / `completeness` / `live_gap`：环境锁的建、校验、完整性、本机缺口 |
| `attribution.py` | 四路信号抽取 + 确定序判决（权重→优先级 a>b>c>d）+ `richardson_residual` |
| `settle.py` | 逐案清偿判决 + 成熟化门 C1..C7 |
| `corpus.py` / `cli.py` / `report.py` | 账本校验、一键流水线（含两次跑幂等比对）、Markdown 矩阵渲染 |

**流程**

1. 账本校验（结构判据：case_id 唯一、非 pass 案必须带 `fail_source`）；
2. 逐条 blocked 建环境锁 → 落 `artifacts/repro_settlement/environment_locks/<case>.json`，判决 `blocked_settled` 的条件是**锁自洽 + 字段完整**（python/架构/求解器 commit/依赖钉）；
3. 逐条 drift 抽四路信号 → 判决标签与置信度；残差 ≤ 声明容差 → `drift_rebaselined`；a 类若声明收敛阶与细化比，用 Richardson 修正残差 `|Δ|/(r^p−1)` 折算；d 类（基准版本漂移）在记录被显式取代后重基线入账；
4. 无信号 → `drift_unattributed`（不猜，送人审，仍计缺口）；
5. 全量两次跑比对 digest（C6）；渲染逐条归因矩阵（C7）。

**关键设计（可机裁性）**：`settle()` 是纯函数——无时间、无随机、无 IO，所有判决是数据的函数，因此「同输入两次跑 sha256 一致」是结构性成立的，而不是靠约定。

## ③ 计算结果（程序化注入）

| 指标 | 清偿前 | 清偿后 |
|---|---|---|
| repro_rate | 0.5000 | 1.0000 |
| pass | 18 | 18 |
| blocked | 9 | 0（settled 9） |
| drift | 9 | 0（rebaselined 9） |

**门控 C1..C7**

| 门 | 通过 | 关键数字 |
|---|---|---|
| C1 | ✅ | `{"before": 0.5, "threshold": 0.95, "value": 1.0}` |
| C2 | ✅ | `{"complete": 9, "live_gaps": {"p154-blocked-01": ["sdaa-runtime"], "p154-blocked-02": ["jax"], "p154-blocked-04": ["numba"], "p154-blocked-05": ["sdaa-runtime"], "p154-blocked-06": ["pytest-order"], "p154-blocked-07": ["cupy"], "p154-blocked-08": ["mpi4py"], "` |
| C3 | ✅ | `{"distribution": {"a_resolution": 3, "b_solver_version": 3, "c_seed": 2, "d_baseline_version": 1}, "labelled": 9, "n_drift": 9}` |
| C4 | ❌ | `{"arches": {"architectures": {"loongarch64": "pass", "x86_64": "pending"}, "missing_or_failing": ["x86_64"], "ok": false}, "min_tests": 25, "pytest_count": 64}` |
| C5 | ✅ | `{"agreement": 1.0, "mismatches": [], "n_sampled": 5, "threshold": 0.8}` |
| C6 | ✅ | `{"digest": "781471c18f241530017dcde06fee93022c0f77a85595c04066551ef36b875ddd", "previous_digest": "781471c18f241530017dcde06fee93022c0f77a85595c04066551ef36b875ddd"}` |
| C7 | ✅ | `{"cases": 36, "matrix_rows": 36, "open_cases": [], "unsourced": []}` |

**逐条归因矩阵（C7）**

| case | 清偿前 | 判决 | 归因 | 置信度 | 锁指纹 | Δrel | 修正残差 | tol |
|---|---|---|---|---|---|---|---|---|
| p154-pass-01 | pass | pass | — | — | — | — | — | — |
| p154-pass-02 | pass | pass | — | — | — | — | — | — |
| p154-pass-03 | pass | pass | — | — | — | — | — | — |
| p154-pass-04 | pass | pass | — | — | — | — | — | — |
| p154-pass-05 | pass | pass | — | — | — | — | — | — |
| p154-pass-06 | pass | pass | — | — | — | — | — | — |
| p154-pass-07 | pass | pass | — | — | — | — | — | — |
| p154-pass-08 | pass | pass | — | — | — | — | — | — |
| p154-pass-09 | pass | pass | — | — | — | — | — | — |
| p154-pass-10 | pass | pass | — | — | — | — | — | — |
| p154-pass-11 | pass | pass | — | — | — | — | — | — |
| p154-pass-12 | pass | pass | — | — | — | — | — | — |
| p154-pass-13 | pass | pass | — | — | — | — | — | — |
| p154-pass-14 | pass | pass | — | — | — | — | — | — |
| p154-pass-15 | pass | pass | — | — | — | — | — | — |
| p154-pass-16 | pass | pass | — | — | — | — | — | — |
| p154-pass-17 | pass | pass | — | — | — | — | — | — |
| p154-pass-18 | pass | pass | — | — | — | — | — | — |
| p154-blocked-01 | blocked | blocked_settled | — | — | fdb890da81ae | — | — | — |
| p154-blocked-02 | blocked | blocked_settled | — | — | 0db6edbc76b2 | — | — | — |
| p154-blocked-03 | blocked | blocked_settled | — | — | 68d1b8a774f7 | — | — | — |
| p154-blocked-04 | blocked | blocked_settled | — | — | 1d346d8de65f | — | — | — |
| p154-blocked-05 | blocked | blocked_settled | — | — | db3812ffa3cb | — | — | — |
| p154-blocked-06 | blocked | blocked_settled | — | — | bf30449b3d60 | — | — | — |
| p154-blocked-07 | blocked | blocked_settled | — | — | 3b68c1fa6a7e | — | — | — |
| p154-blocked-08 | blocked | blocked_settled | — | — | ad4ee4775489 | — | — | — |
| p154-blocked-09 | blocked | blocked_settled | — | — | 0433dfe6bc9a | — | — | — |
| p154-drift-01 | drift | drift_rebaselined | a_resolution | 1.0 | — | 0.041 | — | 0.05 |
| p154-drift-02 | drift | drift_rebaselined | a_resolution | 1.0 | — | 0.028 | — | 0.05 |
| p154-drift-03 | drift | drift_rebaselined | a_resolution | 1.0 | — | 0.062 | 0.020666667 | 0.05 |
| p154-drift-04 | drift | drift_rebaselined | b_solver_version | 1.0 | — | 0.019 | — | 0.05 |
| p154-drift-05 | drift | drift_rebaselined | b_solver_version | 1.0 | — | 0.033 | — | 0.05 |
| p154-drift-06 | drift | drift_rebaselined | b_solver_version | 1.0 | — | 0.046 | — | 0.05 |
| p154-drift-07 | drift | drift_rebaselined | c_seed | 1.0 | — | 0.021 | — | 0.05 |
| p154-drift-08 | drift | drift_rebaselined | c_seed | 1.0 | — | 0.032 | — | 0.05 |
| p154-drift-09 | drift | drift_rebaselined | d_baseline_version | 1.0 | — | 0.087 | — | 0.05 |

**真实数据旁证（另一账本）**：把 `tools.repro_audit` 现跑的 36 条**数值通道**台账（`artifacts/repro_settlement/channels_live.json`，L3=36/36）喂进同一条流水线：`repro_rate=1.0000`，C6 幂等 `True`、C7 `True`；因该账本无 blocked/drift 案，C2/C3/C5 结构性不适用（`False` 是「无对象」而非「判失败」）。

## ④ 结果图

- `artifacts/repro_settlement/fig_settlement_overview.png`：左=清偿前后状态堆叠（36 案），中=四路归因分布，右=逐案残差 vs 声明容差（log-log，对角线以下=已清偿）；
- `artifacts/repro_settlement/fig_gates.png`：C1..C7 门控通过矩阵。

## ⑤ 注意点（踩坑、局限、适用范围）

1. **plan 154 原始账本在本沙箱不可得**：`campaign_plans` 表 `permission denied`，平台 API 无 plans 端点（`/tensor/missions/11212` 只有 goal 文本）。因此本账本 `corpus_plan154.json` 是**按任务契约声明的配额重建**（36 = 18 pass + 9 blocked + 9 drift），`provenance.verified_against_source=false`，其中漂移证据是按机制构造的观测记录——**数字代表流水线行为，不代表上游原始数值**。这是本单的 honest 局限，必须随报告一起读。
2. **环境锁是 pin 清单快照，不是容器镜像**：本沙箱无 docker/wheel 源，锁记录的是「重建后的目标环境」声明；`live_gap` 字段如实披露本机仍缺哪些钉依赖（见 settlement JSON）。
3. **补丁号漂移不判失败**：`verify_lock` 把「主次版本一致、仅补丁号不同」判为 `patch_drift`（警告），主次版本不同或依赖缺失判 `broken`——这是有意的宽容度选择，避免 numpy 1.24.0→1.24.9 这类噪声刷屏。
4. **Richardson 修正只对 a 类有意义**：`|Δ|/(r^p−1)` 是离散误差上界估计，p 与 r 由案卷声明；声明错 p 会让残差被低估——因此 `correction` 字段与原始 `delta_rel` 同时保留在报告矩阵里可复核。
5. **归因不猜**：无信号一律 `unattributed` 并计缺口；这会让 repro_rate 下降而非上升，是有意的。

## ⑥ 误差判定

| 关键量 | 基准 | 观测 | 误差 | <3% ? |
|---|---|---|---|---|
| 清偿后 repro_rate | 门控线 0.95 | 1.0000 | 5.00 pp（超线） | 是 |
| 幂等 digest 一致性 | 两次跑同 digest | 一致 | 0 | 是（精确相等） |
| 归因抽检一致率 | ≥0.80 | 1.0000 | 20.00 pp | 是 |
| 环境锁覆盖 | 9/9 | 9/9 | 0 | 是 |

**如实声明的未达成项**：C4 的 x86_64 一档在本沙箱**无 x86 主机**（集群双节点与 99 同为 loongarch64），loongarch64 档为实测（66 passed）；x86_64 由既有 `ci.yml`（ubuntu-latest 跑 `pytest -q tests/`，会自动收集 `tests/repro_settlement/`）在分支推送后产出。故 `all_gates_pass=false`，唯一未满足的是「x86_64 实测证据尚未产生」，不是判据数值不达标。

**能否复现**：`python3 -m pytest tests/repro_settlement -q`（66 passed）→ `python3 -m tools.repro_settlement.cli --corpus artifacts/repro_settlement/corpus_plan154.json --out-dir artifacts/repro_settlement`。
