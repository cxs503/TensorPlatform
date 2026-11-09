# OSS 深盘波2 · inductiva/inductiva 盘点报告（SPH 线）

- 任务：m8664 ｜ 战役：osssph-w2 ｜ tier 2（m8638 普查初判：仿真云编排层）
- 盘点人：TensorPlatform 建设公民 ｜ 日期：2026-11-09
- 盘点对象：`inductiva/inductiva` @ `development` 分支，HEAD `d3db1eca`（2025-12-19T12:27:41Z），版本 0.18.12
- 证据来源：GitHub REST API（repo/languages/commits，2026-11-09 实查）+ 该分支完整检出（521 文件全读关键路径）；**零凭记忆**

---

## 一、案例介绍（物理背景 / 对象 / 参数表）

**对象定性（源码确认，推翻"SPH 求解器"预期）**：inductiva/inductiva 是 **Inductiva 仿真云平台的 Python 客户端 SDK + CLI**（"Large scale simulations made simple."），不是任何求解器。它把 39 个开源物理模拟器（OpenFOAM、Elmer、WRF、GROMACS、CP2K、**DualSPHysics**、SplishSplash 等）包装成 Python 类，通过 HTTPS API 把任务提交到 GCP/AWS 云上的容器化求解器（镜像在 `inductiva/kutu` DockerHub 仓），管理机器组/存储/事件触发/性能基准。m8638 普查初判"仿真云编排层"**成立**。

**对 SPH 线的意义**：仓内**没有任何 SPH 数值代码**（全树 grep `sph|smoothed|particle` 仅命中 DualSPHysics 包装器与 splashsurf 后处理参数）。SPH 物理全部在外部容器镜像里（DualSPHysics 5.2.1/5.4.1，含 `_gpu` 变体）。本仓可迁移价值在**编排层模式**（基准扫描、事件链、案例包约定、GPU/抢占资源选择），不在数值内核。

**仓库参数表（GitHub API 实查，2026-11-09）**：

| 项 | 值 | 来源 |
|---|---|---|
| 默认分支 | development | API `/repos/inductiva/inductiva` |
| 语言构成 | Python 2,715,274 B / Shell 2,634 B / Jinja 124 B | API `/languages` |
| 星 / fork / open issues | 40 / 8 / 14 | API |
| 仓大小 | 1,249,515 KB（≈1.2 GB，含测试夹具） | API `size` |
| 创建 / 最近 push | 2023-01-26 / 2026-01-09T11:59:48Z | API |
| 最近提交 | d3db1eca 2025-12-19 "Merge PR #2836 …remove-logs-add-region" | API `/commits` |
| License | Apache-2.0 | API `license.spdx_id` + 本地 LICENSE 头 |
| topics | api, coastal-dynamics, dualsphysics, fluid-dynamics, hpc, molecular-dynamics, open-source, python, reef3d, simulation, splishsplash, synthetic-dataset-generation | API |
| 文件 / .py 文件 | 521 / 485 | 本地检出 |
| 模拟器包装器 / 示例 | 39 / 37 | 本地检出 |
| 测试文件 / 测试函数 | 19 / 120 | 本地检出 |
| CI workflow | 5 | 本地 `.github/workflows/` |

## 二、怎么做的（方法 / 流程 / 工具链）

1. **GitHub API 实查**（禁凭记忆）：`/repos/inductiva/inductiva`（元数据）、`/languages`、`/commits?per_page=8`。注：共享出口 IP 的匿名配额（60/h）被并发耗尽两次，元数据在重置窗口内取齐；目录结构/CI/测试布局改用**同分支完整检出**核对（比 API contents 更完整）。
2. **源码级盘点**：`git clone --depth 1 --branch development`（codeload 被网络代理拦截返回 404，git 协议可用）；逐文件读 `simulators/`、`api/methods.py`、`tasks/task.py`、`resources/machine_groups.py`、`benchmarks/benchmark.py`、`events/`、`commands/`、`templating/`、`storage/`、`_cli/`、`.github/workflows/`、`setup.cfg`。
3. **SPH 专项 grep**：`grep -rin "sph|smoothed|particle"` 全树 → 仅 `simulators/dualsphysics.py`、`examples/dualsphysics/`、`_cli/cmd_simulators/list.py:69`（版本表）命中；无 WCSPH/ISPH/PCISPH/DFSPH/IISPH/δ-SPH/PBD 任何内核、边界、时间步代码。
4. **数字注入**：报告数字表由脚本从 API JSON 与检出树直接计算（零手抄）。

## 三、计算结果（数字表，程序化注入）

| 量 | 值 | 溯源 |
|---|---|---|
| 星数 | 40 | repo.json `stargazers_count` |
| fork / open issues | 8 / 14 | repo.json |
| 最近提交 | d3db1eca（2025-12-19） | commits.json[0] |
| 文件总数 / .py | 521 / 485 | 检出树扫描 |
| 模拟器包装器 | 39（`inductiva/simulators/*.py` 除基类） | 检出树扫描 |
| 测试文件 / 测试函数 | 19 / 120 | 检出树扫描 |
| 示例目录 | 37 | 检出树扫描 |
| CI workflow | 5 | `.github/workflows/` |
| 包版本 | 0.18.12 | `inductiva/__init__.py:56` |
| 依赖 | jinja2, tabulate, tqdm, websocket-client, fsspec, requests, aiohttp, nest_asyncio, urllib3, python-dateutil, pydantic≥2, typing-extensions, pytimeparse2, docker | `setup.cfg [options] install_requires` |

**源码级方法清单（逐条 file:line）**：

| 能力 | 结论 | 定位 |
|---|---|---|
| SPH 变体（WCSPH/ISPH/PCISPH/DFSPH/IISPH/δ-SPH/PBD） | **仓内零实现**（NOT_SUPPORTED）；唯一 SPH 面 = DualSPHysics 外部求解器包装器（ISPH 族，容器内运行） | `inductiva/simulators/dualsphysics.py:8`（类）、`:32`（run）、`inductiva/_cli/cmd_simulators/list.py:69`（版本 5.2.1/5.4.1 + `_gpu`） |
| 自由面后处理 | `splashsurf reconstruct` marching-cubes：`-r` 粒子半径、`-l` 光滑长度（默认 2.0×粒子半径，核支撑=2h）、`-c` 背景网格（默认 1.0×粒子半径）、`-t` 密度等值面阈值（默认 0.6×静息密度）、网格清理/法向平滑 25/10 次 | `inductiva/simulators/dualsphysics.py:43-50`（参数默认值）、`:140-151`（命令拼装） |
| 边界处理 | **无**（无求解器）；边界条件在外部求解器容器内 | 全树 |
| 时间步控制 | **无**（无求解器）；唯一"时间"控制 = 任务墙钟上限 `time_to_live`（pytimeparse2 解析 "10m"/"2 hours"） | `inductiva/api/methods.py:336-380`（submit_task，`time_to_live_seconds`） |
| 多相/自由面 | 仅经外部 DualSPHysics；客户端只暴露自由面提取参数（见上） | `inductiva/simulators/dualsphysics.py:43-50` |
| GPU 策略 | 无 GPU 内核；GPU 仅作为**机型选择**：`has_gpu`/`gpu_count`、`_gpu` 镜像后缀、机型过滤 `gpus_range/gpu_names` | `inductiva/resources/machine_groups.py:150-162`、`inductiva/simulators/simulator.py:218`（`_append_simulator_image_suffix_based_on_resource`）、`inductiva/resources/utils.py:48-49` |
| MPI 策略 | `MPIConfig`（OpenMPI 1.10.7/4.1.6，`np=`、`use_hwthread_cpus` 等选项，`_`→`-` 转换）；`@mpi_enabled` 装饰器把 `MPICluster` 加入支持资源集；`MPICluster` 资源类 | `inductiva/commands/mpiconfig.py:5-27`、`inductiva/simulators/simulator.py:14-21`、`inductiva/resources/machine_groups.py:1025` |
| 任务生命周期 | submit（pending-input→zip 上传）→ 排队（`get_position_in_queue`）→ wait（tqdm+线程池）→ tail/kill/download_outputs | `inductiva/api/methods.py:336-441`、`inductiva/tasks/task.py:485,712,910,984,1243` |
| 事件/触发（任务链） | 触发器：`TaskOutputUploaded`、`ObserverFileExists`、`ObserverFileRegex`；动作：Email/Webhook 通知 | `inductiva/events/triggers.py:14,31,50`、`inductiva/events/actions.py:13,26` |
| 性能基准 | `Benchmark` 类：`set_default`/`add_run`（simulator×input_dir×machine_type×kwargs）、`run(num_repeats=2)` 自动起机器组+`resubmit_on_preemption=True`、`wait` 并行等待、`export` JSON/CSV（computation_time、estimated_computation_cost）、`terminate` | `inductiva/benchmarks/benchmark.py:33,65,100,136,179,226,283` |
| 输入模板 | Jinja2 `TemplateManager.render_dir`（整目录渲染，非模板文件原样拷贝） | `inductiva/templating/manager.py:10,17` |
| 任意容器 | `CustomImage`：任意 docker 镜像跑任意命令（`arbitrary_commands`） | `inductiva/simulators/custom_image.py:8,26` |
| 抢占/清理 | `resubmit_on_preemption`（spot 资源被抢占自动重投）、`on_finish_cleanup`（脚本或命令列表，任务结束后执行） | `inductiva/simulators/simulator.py:238-300`（run 签名与文档） |
| 命令安全 | `Command` 拒绝 shell 元字符 `[|><&;?~$]`，交互提示词序列 | `inductiva/commands/commands.py:10-40` |

## 四、验证面盘点

1. **自带算例/基准**：**无物理基准**——无 dam-break / Poiseuille / Taylor-Green 等任何算例数据；`examples/` 37 个目录全是"如何提交某求解器"的 10 行用法示例（如 `examples/openfoam_foundation/openfoam_foundation.py`：MachineGroup→run→wait→download）。
2. **测试框架**：pytest + `unittest.mock`（120 个测试函数，mock 掉 API 客户端，不连真实后端；仅 1 个测试文件引用 `INDUCTIVA_API_KEY`）；`pytest-markdown-docs` 把文档当测试（文档示例可执行性）；CI 在 Linux ARM64 / Windows ARM64 / Windows x64 自托管 runner 上跑 `pytest --suppress-no-test-exit-code`（`.github/workflows/run_tests_on_different_operating_systems.yml`），另有 `python-package.yml`（pylint/yapf/mypy/coverage 徽章/publish-pypi）。
3. **量化验证数据**：**无**——`Benchmark` 类是**性能**基准（每 run 记 computation_time 与 estimated_computation_cost，按 machine_type 对比），不是物理验证；仓内无任何误差/收敛数据。

## 五、对 TensorPhysics SPH 线的可迁移方案（≥3，license 判定显式）

**License 合规判定**：Apache-2.0（API 与 LICENSE 头双重确认）→ 允许复制/修改/再分发，须保留版权与许可声明。**但**：代码与 Inductiva 专有云 API 强耦合（`inductiva/client/*` 为 OpenAPI 生成客户端，后端 `api.inductiva.ai` 私有），**客户端代码本身不可移植** → 本仓**无 adapt 级**条目；可迁移物是**模式/约定**（reference/fit 级），吸收时只搬模式不搬代码，无合规风险。

1. **SPHSOL-inductiva-01（reference → GAP-SPH-GPU-SCALING）**：性能基准扫描器模式——`set_default` 定公共参数、`add_run` 逐格点（simulator×machine_type×kwargs）、`run(num_repeats)` 自动起/停机器组、`resubmit_on_preemption=True` 抗抢占、`wait` 线程池并行等待、`export` 出 JSON/CSV（时间+成本双指标）。→ 移植为 SPH GPU 扩展性战役的扫描器：节点数/GPU 数×重复次数，自动产出 scaling 表（含成本列，供 S 级判级引用）。
2. **SPHSOL-inductiva-02（reference → GAP-SPH-WCSPH-VALIDATION）**：事件触发任务链——`ObserverFileExists`/`ObserverFileRegex`（监视任务输出文件出现/匹配正则）+ `TaskOutputUploaded` 触发器 + Webhook/Email 动作，实现"求解完成→自动起后处理/验证任务"的无轮询链。→ 移植为 WCSPH 验证管线：求解任务输出文件落盘即触发"对解析解算误差"任务，误差超阈自动告警。
3. **SPHSOL-inductiva-03（fit → GAP-SPH-FSI-BENCHMARK）**：案例包约定——`input_dir`（输入文件目录）+ `shell_script`（入口脚本）+ `remote_assets`（从另一任务输出/存储桶拉输入）+ `on_finish_cleanup`（结束后清理命令列表）+ 版本化容器镜像（`dualsphysics 5.2.1/5.4.1(_gpu)`）。→ 直接采纳为 SPH FSI 基准案例包标准：每案例一个目录（输入+run.sh+cleanup），后端无关（本地集群/云通用）。
4. **SPHSOL-inductiva-04（reference → GAP-SPH-ISPH-CONVERGENCE）**：SPH 粒子数据自由面提取约定——marching-cubes 参数组（粒子半径 r、光滑长度 l=2.0r、背景网格 c=1.0r、密度等值面 t=0.6ρ₀，网格清理+法向平滑 25/10 次）。→ ISPH 收敛研究中把粒子场转等值面网格与解析解对比的标准后处理步骤（参数默认值可作起点，需自校）。
5. **SPHSOL-inductiva-05（reference → GAP-SPH-GPU-SCALING）**：GPU/抢占资源选择 API——`has_gpu`/`gpu_count` 机型探测、`_gpu` 镜像后缀自动切换、spot 机型 + `resubmit_on_preemption` 自动重投、`time_to_live` 墙钟熔断。→ 移植到 run_cluster 之上的 SPH 大规模算例：GPU 机型筛选 + 抢占重投 + 超时熔断三件套。

## 六、注意点（坑 / 局限 / 适用范围）

1. **定性风险已排除**：若按"SPH 求解器"预期去挖内核会白做——本仓是客户端，物理在 `inductiva/kutu` 镜像里（本单未拉镜像，镜像内 DualSPHysics 源码未审计，属 unknown）。
2. **pushed_at（2026-01-09）晚于 development 最近提交（2025-12-19）**：差异来自其他分支 push，不影响本盘点（以 development 为准）。
3. **网络坑**：codeload.github.com 被代理拦截（对 torvalds/linux 也 404）；匿名 API 配额共享 IP 易耗尽——后续 OSS 盘点单建议先 `git clone` 再补 API 元数据。
4. **测试面局限**：120 个测试全是 mock 单测，不覆盖真实 API 行为；`pytest-markdown-docs` 只保证文档示例可执行，不保证物理正确性。
5. **适用范围**：本盘点结论仅对 `development@d3db1eca` 有效；Inductiva 平台后端能力（调度器/镜像构建）不在本仓，未审计。

## 七、误差判定

本单为**盘点/审计任务，无物理计算量**，无解析解/文献基准可比 → 误差判定 **N/A（声明原因：无计算量）**。盘点质量判据改为：① 全部元数据 API 实查（非记忆）✓；② 方法清单逐条 file:line 定位 ✓；③ "无 SPH 代码"结论有全树 grep 证据 ✓；④ 机器块齐备（OSS-INV×1、OSS-SOL×5）✓。

---

## 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"inductiva/inductiva","tier":2,"methods":["none-in-repo: cloud simulation orchestration client (SDK+CLI), no SPH kernels","DualSPHysics wrapper (external ISPH solver in container, v5.2.1/5.4.1 + _gpu): inductiva/simulators/dualsphysics.py:8,32","splashsurf marching-cubes free-surface post-processing: inductiva/simulators/dualsphysics.py:43-50,140-151"],"boundary":"none (no solver in repo; BCs live in external solver containers)","gpu":"no GPU kernels; GPU only as machine-type selection (has_gpu/gpu_count resources/machine_groups.py:150-162; _gpu image suffix simulators/simulator.py:218)","validation":"no physics benchmarks (no dam-break/Poiseuille/Taylor-Green); Benchmark class = performance harness (time+cost per machine type, benchmarks/benchmark.py:33); 120 mock-based pytest tests + pytest-markdown-docs; CI on Linux ARM64/Windows ARM64/x64","license":"Apache-2.0 (code reuse allowed w/ attribution; client coupled to proprietary cloud API -> patterns transferable, code not)","transferable":["SPHSOL-inductiva-01 benchmark-sweep-harness (reference, GAP-SPH-GPU-SCALING)","SPHSOL-inductiva-02 event-trigger task chaining (reference, GAP-SPH-WCSPH-VALIDATION)","SPHSOL-inductiva-03 case-package convention (fit, GAP-SPH-FSI-BENCHMARK)","SPHSOL-inductiva-04 splashsurf free-surface extraction convention (reference, GAP-SPH-ISPH-CONVERGENCE)","SPHSOL-inductiva-05 gpu/spot resource selection + preemption resubmit (reference, GAP-SPH-GPU-SCALING)"]}[/OSS-INV]

[OSS-SOL]{"repo":"inductiva/inductiva","id":"SPHSOL-inductiva-01","kind":"reference","summary":"性能基准扫描器模式：set_default 定公共参数、add_run 逐格点(simulator×machine_type×kwargs)、run(num_repeats=2) 自动起停机器组且 resubmit_on_preemption=True 抗抢占、wait 线程池并行等待、export JSON/CSV 双指标(computation_time+estimated_computation_cost)。移植为 SPH GPU 扩展性战役扫描器：节点数/GPU 数×重复次数，自动产出 scaling 表(含成本列)。","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"inductiva/inductiva","id":"SPHSOL-inductiva-02","kind":"reference","summary":"事件触发任务链：ObserverFileExists/ObserverFileRegex 监视任务输出文件出现/正则命中 + TaskOutputUploaded 触发器 + Webhook/Email 动作(events/triggers.py:14,31,50; events/actions.py:13,26)，实现求解→后处理→验证的无轮询自动链。移植为 WCSPH 验证管线：结果文件落盘即触发对解析解的误差计算任务，超阈自动告警。","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"inductiva/inductiva","id":"SPHSOL-inductiva-03","kind":"fit","summary":"案例包约定：input_dir(输入目录)+shell_script(入口脚本)+remote_assets(跨任务/存储桶拉输入)+on_finish_cleanup(结束后清理命令列表)+版本化容器镜像(simulators/simulator.py:238-300, custom_image.py:8)。直接采纳为 SPH FSI 基准案例包标准：每案例一目录(输入+run.sh+cleanup)，后端无关(本地集群/云通用)。","gap":"GAP-SPH-FSI-BENCHMARK"}[/OSS-SOL]

[OSS-SOL]{"repo":"inductiva/inductiva","id":"SPHSOL-inductiva-04","kind":"reference","summary":"SPH 粒子场自由面提取约定：splashsurf reconstruct marching-cubes，参数组 r=粒子半径、l=2.0r(核支撑=2h)、c=1.0r(背景网格)、t=0.6ρ₀(密度等值面)，网格清理+法向平滑 25/10 次(simulators/dualsphysics.py:43-50,140-151)。作为 ISPH 收敛研究粒子场→等值面网格→对解析解对比的标准后处理步骤，默认参数作起点需自校。","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL]{"repo":"inductiva/inductiva","id":"SPHSOL-inductiva-05","kind":"reference","summary":"GPU/抢占资源选择三件套：has_gpu/gpu_count 机型探测(resources/machine_groups.py:150-162)+_gpu 镜像后缀自动切换(simulators/simulator.py:218)+spot 机型 resubmit_on_preemption 自动重投+time_to_live 墙钟熔断(api/methods.py:336-380)。移植到 run_cluster 之上的 SPH 大规模算例调度：GPU 机型筛选+抢占重投+超时熔断。","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]
