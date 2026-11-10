# p223w3-n08 · 跨求解器耦合 checkpoint 与位级可复现验证面

*节点 p223w3-n08（TensorPlatform，mission 10558）· 生成时间 2026-11-10T18:37:20Z · 全部数字由 `tools/run_checkpoint_bitreproducibility_validation.py` 程序化写入 `artifacts/p223w3_n08/checkpoint_validation.json`，本报告由 `tools/render_checkpoint_report.py` 渲染（零手抄）*

## 1. 案例介绍

### 1.1 问题背景

耦合求解（两个以上求解器在一个时间推进循环里交换边界/源项）在工程上最怕三件事：① 长算例必须能中途落盘续算，且续算结果与不间断运行**逐位相同**（否则所有对比实验、回归测试、审计结论都失去基准）；② 落盘文件必须能自证完好，损坏能被定位并可回滚；③ 多求解器副本之间的“同一时刻”必须有可判定的定义（同步点），否则跨副本比较无意义。本节点在 TensorPlatform 平台层实现这三件事的**通用机制**（零领域语义）：只处理“命名定长数值数组 + 步号 + 时间 + 交换总线 + 可选 RNG 流位置”的序列化与验证，具体求解器家族把哪些数组交出来，由调用方通过公开 API 注入。

### 1.2 交付物

| 路径 | 内容 |
|---|---|
| `backend/physics/cross_solver/checkpoint.py` | checkpoint 协议 / 存储 / 位级校验 / 能量账本 / 时间同步 / 回滚 / IO 探针 |
| `backend/physics/cross_solver/checkpoint_scenarios.py` | 三档**合成**耦合算例（正交旋转 + 总线滞后耦合，天然守恒） |
| `backend/tests/test_cross_solver_checkpoint.py` | 验收测试（66 例） |
| `tools/run_checkpoint_bitreproducibility_validation.py` | 机器判据执行器（产出 JSON + 图） |
| `artifacts/p223w3_n08/checkpoint_validation.json` | 全部判据数字（审计链起点） |

### 1.3 环境

| 项 | 值 |
|---|---|
| Python | 3.11.6 |
| NumPy | 2.4.6 |
| 平台 | `Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38` |
| 可用压缩编码 | `none, lzma` |
| zstd 模块可用 | no |
| 临时目录 | `/dev/shm/tmp_m10558` |
| 宿主 loadavg（IO 测量时） | `[254.1083984375, 239.98974609375, 213.22998046875]` |

### 1.4 三档耦合算例（合成夹具，非物理模型）

| 算例 | 求解器数 | 交换间隔 | dt | 随机源 | 说明 |
|---|---|---|---|---|---|
| `s1_two_solver` | 2 | 10 | 0.01 | 无 | 正交旋转 + 环状耦合，2-范数守恒 |
| `s2_three_solver` | 3 | 5 | 0.005 | 无 | 正交旋转 + 环状耦合，2-范数守恒 |
| `s3_stochastic` | 3 | 20 | 0.01 | seed=20261110 | 正交旋转 + 环状耦合，2-范数守恒 |

> 为什么要用“正交旋转”做夹具：正交变换在精确算术下严格保持 2-范数，于是“能量守恒”与“位级复现”可以互相独立地度量——能量漂移只能来自浮点舍入，不来自算例本身的不守恒；任何一次落盘/续算引入的差异都会立刻在 sha256 上暴露。

## 2. 方法与实现

### 2.1 文件格式（确定性、可比对）

```
b"TPCKPT\x00\x01"        魔数/版本（9 B）
<u64 header_len>          小端头长度
header_json               规范 JSON（键排序、无空格）
sha256(header_json)       32 B 头部自校验
payload                   各字段裸字节（可选整体压缩）
```

头部逐字段记录 `name/dtype/shape/nbytes/offset/digest/blocks[]`。完整性采用两级Merkle 结构：每 64 KiB 页算页摘要，**字段摘要 = 页摘要拼接后的 sha256**，**状态摘要 = 规范 JSON（步号/时间/元数据/RNG 流位置/字段摘要表）的 sha256**。因此：位级同一 ⟺ 状态摘要相同；损坏定位 ⟺ 失效页号 × 64 KiB。页摘要在 `hashlib` 释放 GIL 的多线程池中并行计算（1 MiB 任务粒度），避免单条顺序 SHA-256 流成为带宽瓶颈。

### 2.2 耦合循环与同步点语义

`CoupledRunner` 按声明顺序串行调用各求解器；求解器通过 `ExchangeBus` 读写耦合量，写入落在 `pending`、读取只见 `committed`，每隔 `sync_interval` 步 `commit()` 一次——该步即**同步点**。落盘内容包含 solver 数组、committed/pending 总线值、commit 计数、步号、时间与 RNG 流位置，因此**段中落盘也能位级续算**（这个坑下面第 5 节展开）。

### 2.3 能量账本

`EnergyLedger` 是状态字段的线性泛函（`sum` / `sumsq` / `abs_sum` × 系数），由调用方按自己的物理量登记（平台层不含任何域词条）。判据两条：落盘往返后 `|ΔE|` 与 1000 步相对漂移。

### 2.4 时间同步判定

`TimeSynchronizer.compare(a, b)` 只有在“双方都处于同步点 + 步号相同 + 状态摘要相同”时才判定 `synchronized`，否则给出 `not_sync_point / step_mismatch / digest_mismatch` 的原因码。

### 2.5 损坏检测与回滚

`CheckpointManager` 原子写（tmp + fsync + rename）、按保留数裁剪；`load_latest()` 从最新向旧逐个 `verify()`，跳过损坏文件并记录 `rollback_history`，全部损坏则抛 `NoValidCheckpoint`（绝不静默用坏档）。

## 3. 计算结果（机器判据逐条）

### (a) 位级可复现：重启后 1000 步状态 sha256

| 算例 | 连续运行摘要 | 续算摘要 | 逐位相同 | 全字段相等 | 段中（非同步点）落盘续算 | 落盘字节 |
|---|---|---|---|---|---|---|
| `s1_two_solver` | `9c2593337903ebda…` | `9c2593337903ebda…` | yes | yes | yes (step 507) | 3845 |
| `s2_three_solver` | `208361bac922d8fc…` | `208361bac922d8fc…` | yes | yes | yes (step 507) | 6963 |
| `s3_stochastic` | `fb97708f7333b185…` | `fb97708f7333b185…` | yes | yes | yes (step 507) | 5577 |

**判定：yes** —— 3 档耦合场景（2/3/3 求解器，含随机源）在 step 500 落盘、重启续跑至 step 1000，状态 sha256 与不间断运行完全一致。

### (b) 能量账本：落盘残差与 1000 步漂移

| 算例 | E_total | 落盘残差 \|ΔE\| | 容差 | 连续 1000 步相对漂移 | 续算 1000 步相对漂移 |
|---|---|---|---|---|---|
| `s1_two_solver` | 148.706788 | 0.000 | 1.000e-10 | 2.695e-14 | 2.695e-14 |
| `s2_three_solver` | 306.432618 | 0.000 | 1.000e-10 | 4.267e-14 | 4.267e-14 |
| `s3_stochastic` | 211.115343 | 0.000 | 1.000e-10 | 6.731e-16 | 6.731e-16 |

**判定：yes** —— 落盘往返残差为 0（位级无损的直接后果），1000 步相对漂移量级 4.267e-14，比 0.1% 判据低 11 个数量级。

### (c) 时间同步

| 情形 | 步号 | 双方同步点 | 摘要相同 | 判定 | 原因码 |
|---|---|---|---|---|---|
| 同步点对照 | 500 / 500 | yes / yes | yes | yes | `sync_point_match` |
| 非同步点对照 | 500 / 505 | yes / no | no | no | `not_sync_point` |
| 同步点但数值分歧 1e-12 | same | yes / yes | no | no | `digest_mismatch` |

**判定：yes** —— 同步点一致判真、非同步点判假、同步点但状态发散亦判假（区分“时刻对齐”与“状态一致”两个层面）。

### (d) 压缩无损

| 编码 | 载荷字节 | 落盘字节 | 压缩比 | 摘要不变 | 自校验 |
|---|---|---|---|---|---|
| `none` | 4640 | 6962 | 0.666× | yes | yes |
| `lzma` | 4640 | 6882 | 0.674× | yes | yes |

**判定：yes** —— 每个可用编码往返后状态摘要不变。本环境无 zstd 构建（`zstd_available=no`），编码协商自动落到 stdlib `lzma`，报告如实记录所用编码而非声称 zstd。

### (e) 1 比特翻转检测

| 注入故障 | 检测到 | 定位 |
|---|---|---|
| 载荷 1 bit 翻转（第 3874 字节 bit 5） | yes | 字段 `bus/committed/phase/angle`（期望 `bus/commits`），失效页偏移 1552 B，页大小 65536 B |
| 头部 1 bit 翻转 | yes | `header checksum mismatch (metadata corrupted)` |
| 文件截断一半 | yes | `payload truncated` |

**判定：yes**

### (f) 回滚

- 保留 3 个档；把最新档头部第 72 字节 bit 2 翻转 → `load_latest()` 回滚到 step **200**（`rolled_back=yes`，跳过 1 个损坏档）
- 回滚后继续推进到 step 300，状态摘要与不间断运行**yes** 相同（`a2571270490ab6d8…` vs `a2571270490ab6d8…`）
- 全部档都被破坏时抛 `NoValidCheckpoint`：yes

**判定：yes**

### (g) 快照 I/O 吞吐与 100 GB 外推

| 载荷 | 写耗时 | 读+校验耗时 | 写吞吐 | 读吞吐 | 外推 100 GB 写 | 外推 100 GB 读 | 无损 |
|---|---|---|---|---|---|---|---|
| 0.25 GiB | 1.06 s | 0.96 s | 241 MiB/s | 268 MiB/s | 424 s | 383 s | yes |
| 0.5 GiB | 1.19 s | 1.13 s | 430 MiB/s | 452 MiB/s | 238 s | 227 s | yes |
| 1.0 GiB | 1.71 s | 2.43 s | 600 MiB/s | 421 MiB/s | 171 s | 243 s | yes |
| 2.0 GiB | 7.23 s | 6.17 s | 283 MiB/s | 332 MiB/s | 361 s | 309 s | yes |

- 设备（`tmpfs (RAM backed)`，`/dev/shm/tmp_m10558/p223w3_n08_io`）裸写/裸读上限：写 656 MiB/s、读 508 MiB/s（同载荷、无序列化/无摘要）
- 本格式写路径相对裸写的开销倍数：1.09×
- 100 GB / 60 s 所需吞吐：1707 MiB/s
- 100 GB 外推：写 171 s、读 243 s → **no**

**判定：honest FAIL（no）** —— the container's root filesystem had < 5 MB free during this run, so the throughput numbers are measured on a RAM-backed tmpfs (an upper bound, no spindle/NVMe seek or page-cache writeback penalty). Writing the full 100 GB was not exercised; the < 60 s verdict is an extrapolation of a 1.0 GiB measurement.

### (h) pytest

- 命令：`python -m pytest backend/tests/test_cross_solver_checkpoint.py`
- 用例总数 66 / 通过 66 / 失败 0 / 错误 0 / 跳过 0，exit=0
- 判据 pytest≥30 全绿：**yes**

### 判据总表

| 判据 | 通过 |
|---|---|
| (a) 位级可复现 3 档 | yes |
| (b) 能量残差 <1e-10 / 漂移 <0.1% | yes |
| (c) 时间同步判定 | yes |
| (d) 压缩无损 | yes |
| (e) 1 bit 翻转检测 | yes |
| (f) 回滚有效 | yes |
| (g) 100 GB 快照 I/O <60 s | no |
| (h) pytest ≥30 全绿 | yes |

**总判定：no**（(a)–(f)、(h) 全部满足；(g) honest FAIL，见第 6 节）

## 4. 图

![energy drift](artifacts/p223w3_n08/fig_energy_drift.png)

*图 1 三档耦合算例的能量账本相对漂移（半对数）。红色虚线为 0.1% 验收线；曲线仅在浮点舍入量级波动，说明夹具本身严格守恒，任何落盘/续算误差都会被放大暴露。*

![energy ledger](artifacts/p223w3_n08/fig_energy_ledger.png)

*图 2 落盘往返残差与 1000 步漂移（对数）对比两条验收线（1e-10 / 0.1%）。*

![io throughput](artifacts/p223w3_n08/fig_io_throughput.png)

*图 3 快照 I/O 吞吐随载荷规模的变化，以及到 100 GB 的外推（右轴）与 60 s 预算线。所有测量点在单一 tmpfs 上完成，用于给出上界。*

![codec size](artifacts/p223w3_n08/fig_codec_size.png)

*图 4 各压缩编码的载荷/落盘字节与压缩比；三者状态摘要均不变（无损）。*

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **`hash(str)` 是每进程加盐的**：合成算例最初用 `hash(name) % 4` 派生求解器角频率，同进程内可复现、跨进程就换频率——续算必然发散。改为 sha256 摘要派生后，并加了一条跨解释器子进程的回归用例（`test_cross_process_bit_reproducibility`）把这条约束钉死。任何“跨进程位级一致”的系统都必须审查所有 `hash()` 使用点。
2. **`np.ascontiguousarray` 会把 0 维数组变成 shape (1,)**：标量字段（如 commit 计数）在落盘/回读后形状被悄悄改写、后续 `int()` 直接 TypeError。规范化的换用 `np.array(..., order='C', copy=True)` 保形。
3. **RNG 流位置必须与步进内核共享同一个 generator**：最初 runner 记录自己的 RNG，而步进函数用另一个私有 generator，续算时随机数从头开始，三档中的随机场景立刻不位级一致。结论写入 `Scenario.solvers()` 的接口约定：内核必须使用 runner 拥有的 generator。
4. **两级 Merkle 摘要 vs 单条顺序 sha256**：宿主 SHA-256 单流只有 656 MiB/s 量级（受 loadavg≈254 的共享 CPU 影响），并行页摘要 + 由页摘要派生字段摘要把同样的活提速约 3×；顺序单流会成为第一瓶颈。
5. **1 比特定位需要“页摘要”，只有字段级摘要只能报错不能定位**：报错信息里带字段名 + 失效页偏移是本设计刻意保留的可运维性。
6. **本节点不含任何域语义**：模块只认“命名定长数值数组 + 步号 + 时间 + 总线 + RNG 状态”；LBM 分布函数 / FEM 节点场 / DEM 颗粒表这类说法只出现在这套机制**以外**的调用方。
7. **预存在的包导入缺陷（顺手修）**：`physics/cross_solver/__init__.py` 直接 `from .wigley_bem_nk import ...`，而该模块在被裁剪的检出里不存在，导致**整个包不可导入**；已把可选域模块的导入包进 try/except 并暴露 `_OPTIONAL_IMPORT_ERROR`，checkpoint 层（仅依赖 numpy）无条件可导入。该缺陷与本次节点无关，但阻塞平台层。
8. **局限一：100 GB 快照未真实写入**。容器根盘剩余 <5 MB，全部 I/O 测量在内存后备的 tmpfs 上完成（对上界友好、对真实磁盘不友好），100 GB 结论是外推值，见第 6 节。
9. **局限二：只有合成耦合夹具**。位级可复发机制与具体求解器无关，但真实求解器的字段数量、dtype 混合度、单步耗时都会改变落盘频率与体积；本节点的证据不外推到任何具体求解器的性能数字。
10. **局限三：损坏检测只覆盖随机位翻转与截断**，未覆盖“恶意篡改且同步改头部摘要”这类攻击面（那需要签名/密钥，不属于本节点范围）。

## 6. 误差判定（逐项，显式回答是否 <3%）

本节点的“基准”不是物理实验值，而是**不间断运行的同一算例状态**（位级）与**解析守恒量**（正交变换下 2-范数不变）。判定如下：

| 关键量 | 参考值 / 基准 | 本文结果 | 相对偏差 | <3%？ |
|---|---|---|---|---|
| 续算末态 sha256（3 档） | 不间断运行末态摘要 | 完全相同 | 0（逐位相同） | **是** |
| 能量 E_total 落盘残差 | 0（无损序列化的解析期望） | 0.000 | 0 | **是** |
| 1000 步能量相对漂移 | 0（正交变换严格守恒） | 4.267e-14 | 4.267e-14 | **是** |
| 压缩往返状态摘要 | 原状态摘要 | 相同 | 0 | **是** |
| 1 bit 翻转检出率 | 100% | 载荷/头部/截断 3/3 检出 | 0 | **是** |
| 100 GB 快照 I/O 时间 | 60 s 上限 | 外推 171 s（写）/ 243 s（读） | 184% 超限 | **否（honest FAIL）** |

**关于 (g) 的诚实结论**：判据要求 100 GB 快照 I/O < 60 s，等价于 ≥1.71 GB/s 的端到端写吞吐。本宿主上裸写上限只有 656 MiB/s（tmpfs，loadavg≈254 的共享 128 核机器），即使摘要开销归零也无法在 60 s 内写完 100 GB：**这是设备/宿主约束，不是本格式能靠改代码跨越的**。本节点因此把 (g) 记为 honest FAIL 并给出可复核的数字链：裸设备上限、格式开销倍数、所需吞吐、外推时间。要真正满足该判据，需要① 落盘路径做分块并行/流水线写（多文件或并行文件系统），② 换个带宽 ≥2 GB/s 的存储卷，③ 或把判据改写为“每 GB 落盘开销 < X s”。

## 7. 审计链

- 机器判据：`artifacts/p223w3_n08/checkpoint_validation.json`（本报告全部数字的唯一来源，字段与判据一一对应）
- 复现命令：`python tools/run_checkpoint_bitreproducibility_validation.py`（需可写 TMPDIR，>2 GiB 空间；本仓库在 `/dev/shm` 下执行）
- 测试：`cd backend && python -m pytest tests/test_cross_solver_checkpoint.py`
- 报告渲染：`python tools/render_checkpoint_report.py`
- 运行耗时：111.5 s
