# P188-N11 历史验收声明的重跑复证与漂移记账（m6864）

## ① 案例介绍

平台已积压 **1384** 个 `campaign_nodes.status='done'` 且 mission `succeeded` 的节点，
每份交付都带"验收声明"（报告里的数字、测试全绿）。这些声明此前从未被**独立重跑**核过，
仪表盘上的 `repro_rate` 长期是 0 / 1 这类无明细的值。本节点把"历史声明"当被测对象：
按 repo×复证层分层抽样 **N=40**（覆盖 **11** 个仓），
在 `git archive` 出的**干净检出**里重跑，逐项比对并记账。

参数表（协议，写死在 `tools/audit/repro_replay.py` 且落进 JSON）：

| 口径 | 值 |
|---|---|
| observed（物理/交付量）相对容差 | 1e-06 |
| diagnostic（近零量）绝对容差 | 1e-12 |
| 近零量判据 | \|值\| < 1e-06 |
| float_path 归因阈值 | 1e-09 |
| 单节点预算 | 600 s |
| 抽样种子 | `repro-replay`（sha256(seed\|node_id) 定序，幂等） |

## ② 怎么做的

1. **抽样框**：读 `campaign_nodes`/`tensor_dev_missions`（只读 SQL），要求节点有
   (a) `on_disk_changes` 里的 `artifacts/**.json` **或** 自带 `tests/**.py`；
   (b) mission→合并 commit 能在仓内解析（`git log --grep '(m<id>)'`）。见下表漏斗。
2. **干净检出**：`git archive <commit> | tar -x` 到 `/tmp`，不含任何未提交/未跟踪文件。
3. **重跑**：
   * 数值层——删掉检出里的工件文件，再执行其**生产者脚本**；只有"删得掉、跑得回"的脚本才算生产者
     （机械证伪 basename 撞名），随后把重算 JSON 与原 JSON 的数值叶子逐项比对；
   * 测试层——`python3 -m pytest -q <节点自带测试文件>`，解析 passed/failed。
4. **判决**：pass / drift / fail / blocked；drift 必须两次重跑一致才算成立，
   两次不一致降级 fail（非确定性），并按四类归因（source_change / dependency_version /
   float_path / environment）挂号。
5. **灵敏度负控**：对数值层节点注入已知偏移（相对 1e-3、相对 1e-9、绝对 1e-6），
   验证检测器是活的——历史样本 0 drift 时，没有负控就无法区分"没漂移"和"检测器瞎了"。
6. **落库**：`repro_rate` 与逐项加总一致（|KPI−JSON|≤1e-9）才允许写入 `tensor_guide_tracker`，
   blocked 单列不进分母。

抽样框漏斗（判据 a 的证据可得性约束）：

| 抽样框环节 | 节点数 |
|---|---|
| campaign_nodes.status='done' ∩ mission succeeded | 1384 |
| on_disk_changes 含 artifacts/**.json | 937 |
| └ 其中 mission→合并 commit 不可定位 | 395 |
| **数值工件随合并 commit 真正落地** | **43** |
| 有可执行 spec（数值层） | 37 |
| 仅测试层可复证 | 658 |
| 抽样 N | 40 |

## ③ 计算结果

| 判决 | 节点数 | 说明 |
|---|---|---|
| pass | 33 | 干净检出重跑后逐项一致 / 自带测试全绿 |
| drift | 0 | 数值不一致但两次重跑可复现（4 类归因） |
| fail | 4 | 自带测试在干净检出下失败且两次一致 |
| blocked | 3 | 工件未落地/无生产者/超时/依赖不可得（**不进分母**） |
| **分母 pass+drift+fail** | **37** | **repro_rate = 0.891892**（blocked_share=0.075000） |

分层明细：

| 层 | 节点数 | 判据载体 | 比对项 |
|---|---|---|---|
| 数值层 | 5 | 合并 commit 里的 artifacts/**.json + 生产者脚本 | 976 项逐值比对 |
| 测试层 | 35 | 节点自带的 tests/**.py（干净检出内 pytest） | 通过/失败计数 |

失败/阻塞逐节点证据：

| node | repo | 判决 | 失败/阻塞证据 |
|---|---|---|---|
| 196 | TensorSPH | fail | tests/test_threed_artifacts.py::test_shear_report_meets_its_criteria; tests/test_threed_artifacts.py::test_hydrostatic_r |
| 469 | TensorBEM | fail | tests/test_m5455_verification.py::TestTandemInterference::test_interference_at_zero_spacing; tests/test_m5455_verificati |
| 503 | TensorLBM | fail | tests/test_cht_validation.py::TestC2_ErfPenetration::test_c2_vs_analytical; tests/test_cht_validation.py::TestC6_Negativ |
| 1843 | ship-performance-platform | fail | tests/test_bwm_d1_operation_window.py::test_artifacts_exist_and_all_criteria_pass; tests/test_bwm_d1_operation_window.py |
| 513 | ship-performance-platform | blocked | not_a_producer: backend/api/manoeuvring_benchmark.py 未重写 artifacts/manoeuvring_kvlcc2/benchmark.json |
| 698 | TensorFVM | blocked | collection_error rc=1 |
| 1921 | TensorPlatform | blocked | script_failed rc=1 on tools/audit/m1868_render.py: udit/m1868_render.py", line 10, in <module>
    from tools.audit.resi |

归因分布：`{"dependency_version": 4}`；
抽样框统计：`{"artifact_not_durable": 745, "counted": 695, "layer_numeric": 37, "layer_test": 658, "no_commit": 539, "no_executable_spec": 150, "no_numeric_artifact": 447, "no_on_disk_changes": 0, "nodes_with_artifact_ref": 937, "nodes_with_artifact_ref_no_commit": 395, "nodes_with_durable_artifact": 43, "queried": 1384}`；
注入式负控：9 个已知偏移场景中检出 9 个（detection_rate=1.0），两次重复一致 9 个。

机器自检（`verify` 子命令输出）：`{"all_criteria_pass": true, "anchors_complete": true, "blocked_not_in_denominator": true, "counts_match": true, "drift_not_two_run_verified": [], "drift_without_attribution": [], "kpi_consistent": true, "n_beyond_30": true, "n_repos": 11, "over_budget_nodes": []}`

## ④ 结果图

![fig1](artifacts/repro_replay/fig1_repro_rate.png) · ![fig2](artifacts/repro_replay/fig2_by_repo.png)
![fig3](artifacts/repro_replay/fig3_budget.png) · ![fig4](artifacts/repro_replay/fig4_durability_funnel.png)

## ⑤ 注意点（坑与局限）

* **最大约束是证据耐久性**：937 个节点在 `on_disk_changes` 里点名了
  数值工件，其中 395 个连 mission→合并 commit 都定位不到，
  可定位的 542 个里又只有
  **43** 个的工件真的随 commit 落地（其余被推送门禁滤除）——
  "历史数字"因此**无法纯靠 git 复原**，这是数值层样本只有
  5 个的根因，也是本审计发现的**结构性证据缺陷**（建议门禁对
  `artifacts/**.json` 白名单放行或改为内容寻址归档）。
* **可编辑安装污染**：本机 `tensorbem`/`tensordem` 等包解析到**别的 agent 工作树**
  （`/root/tp-agent/work/mXXXX/src/...`）。重跑已用 `PYTHONPATH` 前置检出路径压制，
  并把解析到检出之外的本地包显式记账（`shadowed_local_modules`），否则测的根本不是该 commit 的代码。
* **测试层 ≠ 数值层**：测试层只证明"该 commit 的测试在干净检出里过/不过"，
  不等价于报告数字被复算；两层在 JSON 里单列，不混算。
* **本机环境坑**：`TORCH_DEVICE_BACKEND_AUTOLOAD=0` 必须设置，否则任何 import torch 的测试
  在收集期即因缺 `libsddev.so` 报 ImportError（会把整批节点误判 blocked）。
* **归因是分类器不是因果证明**：四类归因按可机器复核的优先级路由（不稳定→environment；
  末位→float_path；版本自报不符→dependency_version；本地包外解析→source_change），
  不改代码做控制变量实验就无法给出唯一因果。

## ⑥ 误差判定

| 关键量 | 基准 | 本次实测 | 是否满足判据 |
|---|---|---|---|
| repro_rate 与逐项加总一致性 | \|KPI−JSON\|≤1e-9 | 0.8918918918918919 vs 0.8918918918918919 | 一致（≤1e-9） |
| 抽样规模 | N≥30 | 40 | 满足 |
| 仓覆盖 | ≥3 repo | 11 | 满足 |
| 单节点成本 | ≤600 s | max 585.73 s | 满足 |
| 总预算 | ≤6 h | 586.7 s | 满足 |
| 检测器灵敏度（负控） | 注入偏移必检出 | 1.0 | 满足 |

**是否 <3% / 是否达标**：本节点是**计量审计**任务，主判据是"KPI 与明细可加总一致"（实测偏差
0.0e+00，阈值 1e-9）与判据 (a)–(f) 的逐条满足；
不存在与实验基准的物理量对比，故不适用 3% 判据（此处显式声明原因，不含糊）。
历史声明侧的真实误差由 fail/drift 项逐条列出（上表 ③ 的失败证据清单）。
