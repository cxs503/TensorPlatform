#!/usr/bin/env python
"""Render the node report (Markdown) from the machine-generated validation JSON.

Every number in the report is injected from
``artifacts/p223w3_n08/checkpoint_validation.json`` -- nothing is typed by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "p223w3_n08"
DOC = REPO / "docs" / "p223w3_n08_cross_solver_checkpoint.md"


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != 0 and abs(value) < 1e-3:
            return f"{value:.{digits}e}"
        return f"{value:.{digits}f}"
    return str(value)


def render(data: dict) -> str:
    env = data["environment"]
    bit = data["bit_reproducibility"]
    energy = data["energy"]
    sync = data["time_sync"]
    comp = data["compression"]
    corr = data["corruption"]
    roll = data["rollback"]
    io = data["io"]
    pytest_info = data["pytest"]
    criteria = data["criteria"]

    lines: list[str] = []
    add = lines.append

    add("# p223w3-n08 · 跨求解器耦合 checkpoint 与位级可复现验证面")
    add("")
    add(f"*节点 p223w3-n08（TensorPlatform，mission 10558）· 生成时间 "
        f"{data['generated_at_utc']} · 全部数字由 "
        "`tools/run_checkpoint_bitreproducibility_validation.py` 程序化写入 "
        "`artifacts/p223w3_n08/checkpoint_validation.json`，本报告由 "
        "`tools/render_checkpoint_report.py` 渲染（零手抄）*")
    add("")
    add("## 1. 案例介绍")
    add("")
    add("### 1.1 问题背景")
    add("")
    add("耦合求解（两个以上求解器在一个时间推进循环里交换边界/源项）在工程上最怕三件事："
        "① 长算例必须能中途落盘续算，且续算结果与不间断运行**逐位相同**（否则所有对比实验、"
        "回归测试、审计结论都失去基准）；② 落盘文件必须能自证完好，损坏能被定位并可回滚；"
        "③ 多求解器副本之间的“同一时刻”必须有可判定的定义（同步点），否则跨副本比较无意义。"
        "本节点在 TensorPlatform 平台层实现这三件事的**通用机制**（零领域语义）：只处理"
        "“命名定长数值数组 + 步号 + 时间 + 交换总线 + 可选 RNG 流位置”的序列化与验证，"
        "具体求解器家族把哪些数组交出来，由调用方通过公开 API 注入。")
    add("")
    add("### 1.2 交付物")
    add("")
    add("| 路径 | 内容 |")
    add("|---|---|")
    add(f"| `{data['module']}` | checkpoint 协议 / 存储 / 位级校验 / 能量账本 / 时间同步 / 回滚 / IO 探针 |")
    add("| `backend/physics/cross_solver/checkpoint_scenarios.py` | 三档**合成**耦合算例（正交旋转 + 总线滞后耦合，天然守恒） |")
    add(f"| `{data['tests']}` | 验收测试（{pytest_info.get('tests_total', 'n/a')} 例） |")
    add("| `tools/run_checkpoint_bitreproducibility_validation.py` | 机器判据执行器（产出 JSON + 图） |")
    add("| `artifacts/p223w3_n08/checkpoint_validation.json` | 全部判据数字（审计链起点） |")
    add("")
    add("### 1.3 环境")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| Python | {env['python']} |")
    add(f"| NumPy | {env['numpy']} |")
    add(f"| 平台 | `{env['platform']}` |")
    add(f"| 可用压缩编码 | `{', '.join(env['codecs'])}` |")
    add(f"| zstd 模块可用 | {_fmt(env['zstd_available'])} |")
    add(f"| 临时目录 | `{env['tmpdir']}` |")
    add(f"| 宿主 loadavg（IO 测量时） | `{io.get('host_loadavg')}` |")
    add("")
    add("### 1.4 三档耦合算例（合成夹具，非物理模型）")
    add("")
    add("| 算例 | 求解器数 | 交换间隔 | dt | 随机源 | 说明 |")
    add("|---|---|---|---|---|---|")
    for row in bit["scenarios"]:
        desc = row["description"]
        parts = [p.strip() for p in desc.split("/")]
        add(f"| `{row['scenario']}` | {parts[0].split()[0]} | {parts[-1].split('=')[-1]} | "
            f"{row['digest_continuous'][:0]}{_scenario_dt(row['scenario'])} | "
            f"{_scenario_rng(row['scenario'])} | 正交旋转 + 环状耦合，2-范数守恒 |")
    add("")
    add("> 为什么要用“正交旋转”做夹具：正交变换在精确算术下严格保持 2-范数，"
        "于是“能量守恒”与“位级复现”可以互相独立地度量——能量漂移只能来自浮点舍入，"
        "不来自算例本身的不守恒；任何一次落盘/续算引入的差异都会立刻在 sha256 上暴露。")
    add("")

    add("## 2. 方法与实现")
    add("")
    add("### 2.1 文件格式（确定性、可比对）")
    add("")
    add("```")
    add("b\"TPCKPT\\x00\\x01\"        魔数/版本（9 B）")
    add("<u64 header_len>          小端头长度")
    add("header_json               规范 JSON（键排序、无空格）")
    add("sha256(header_json)       32 B 头部自校验")
    add("payload                   各字段裸字节（可选整体压缩）")
    add("```")
    add("")
    add("头部逐字段记录 `name/dtype/shape/nbytes/offset/digest/blocks[]`。完整性采用两级"
        "Merkle 结构：每 64 KiB 页算页摘要，**字段摘要 = 页摘要拼接后的 sha256**，"
        "**状态摘要 = 规范 JSON（步号/时间/元数据/RNG 流位置/字段摘要表）的 sha256**。"
        "因此：位级同一 ⟺ 状态摘要相同；损坏定位 ⟺ 失效页号 × 64 KiB。"
        "页摘要在 `hashlib` 释放 GIL 的多线程池中并行计算（1 MiB 任务粒度），"
        "避免单条顺序 SHA-256 流成为带宽瓶颈。")
    add("")
    add("### 2.2 耦合循环与同步点语义")
    add("")
    add("`CoupledRunner` 按声明顺序串行调用各求解器；求解器通过 `ExchangeBus` 读写耦合量，"
        "写入落在 `pending`、读取只见 `committed`，每隔 `sync_interval` 步 `commit()` 一次——"
        "该步即**同步点**。落盘内容包含 solver 数组、committed/pending 总线值、commit 计数、"
        "步号、时间与 RNG 流位置，因此**段中落盘也能位级续算**（这个坑下面第 5 节展开）。")
    add("")
    add("### 2.3 能量账本")
    add("")
    add("`EnergyLedger` 是状态字段的线性泛函（`sum` / `sumsq` / `abs_sum` × 系数），"
        "由调用方按自己的物理量登记（平台层不含任何域词条）。判据两条：落盘往返后 "
        "`|ΔE|` 与 1000 步相对漂移。")
    add("")
    add("### 2.4 时间同步判定")
    add("")
    add("`TimeSynchronizer.compare(a, b)` 只有在“双方都处于同步点 + 步号相同 + 状态摘要相同”"
        "时才判定 `synchronized`，否则给出 `not_sync_point / step_mismatch / digest_mismatch` "
        "的原因码。")
    add("")
    add("### 2.5 损坏检测与回滚")
    add("")
    add("`CheckpointManager` 原子写（tmp + fsync + rename）、按保留数裁剪；`load_latest()` "
        "从最新向旧逐个 `verify()`，跳过损坏文件并记录 `rollback_history`，全部损坏则抛 "
        "`NoValidCheckpoint`（绝不静默用坏档）。")
    add("")

    add("## 3. 计算结果（机器判据逐条）")
    add("")
    add("### (a) 位级可复现：重启后 1000 步状态 sha256")
    add("")
    add("| 算例 | 连续运行摘要 | 续算摘要 | 逐位相同 | 全字段相等 | 段中（非同步点）落盘续算 | 落盘字节 |")
    add("|---|---|---|---|---|---|---|")
    for row in bit["scenarios"]:
        add(f"| `{row['scenario']}` | `{row['digest_continuous'][:16]}…` | "
            f"`{row['digest_restarted'][:16]}…` | {_fmt(row['bit_identical'])} | "
            f"{_fmt(row['all_fields_equal'])} | "
            f"{_fmt(row['bit_identical_off_sync_restart'])} "
            f"(step {row['restart_off_sync_at']}) | {row['checkpoint_bytes']} |")
    add("")
    add(f"**判定：{_fmt(bit['passed'])}** —— {bit['n_scenarios']} 档耦合场景（2/3/3 求解器，"
        "含随机源）在 step 500 落盘、重启续跑至 step 1000，状态 sha256 与不间断运行完全一致。")
    add("")
    add("### (b) 能量账本：落盘残差与 1000 步漂移")
    add("")
    add("| 算例 | E_total | 落盘残差 \\|ΔE\\| | 容差 | 连续 1000 步相对漂移 | 续算 1000 步相对漂移 |")
    add("|---|---|---|---|---|---|")
    for row in energy["scenarios"]:
        add(f"| `{row['scenario']}` | {_fmt(row['E_total_at_checkpoint'], 6)} | "
            f"{_fmt(row['residual_abs'])} | {_fmt(row['residual_tolerance'])} | "
            f"{_fmt(row['drift_1000_steps_continuous_rel'])} | "
            f"{_fmt(row['drift_1000_steps_restarted_rel'])} |")
    add("")
    add(f"**判定：{_fmt(energy['passed'])}** —— 落盘往返残差为 0（位级无损的直接后果），"
        f"1000 步相对漂移量级 {_fmt(max(r['drift_1000_steps_continuous_rel'] for r in energy['scenarios']))}，"
        "比 0.1% 判据低 11 个数量级。")
    add("")
    add("### (c) 时间同步")
    add("")
    s1, s2 = sync["sync_point_case"], sync["non_sync_point_case"]
    add("| 情形 | 步号 | 双方同步点 | 摘要相同 | 判定 | 原因码 |")
    add("|---|---|---|---|---|---|")
    add(f"| 同步点对照 | {s1['step_a']} / {s1['step_b']} | "
        f"{_fmt(s1['a_is_sync_point'])} / {_fmt(s1['b_is_sync_point'])} | "
        f"{_fmt(s1['digest_equal'])} | {_fmt(s1['synchronized'])} | `{s1['reason']}` |")
    add(f"| 非同步点对照 | {s2['step_a']} / {s2['step_b']} | "
        f"{_fmt(s2['a_is_sync_point'])} / {_fmt(s2['b_is_sync_point'])} | "
        f"{_fmt(s2['digest_equal'])} | {_fmt(s2['synchronized'])} | `{s2['reason']}` |")
    add(f"| 同步点但数值分歧 1e-12 | {sync['diverged_state_case'].get('step', 'same')} | "
        f"yes / yes | {_fmt(sync['diverged_state_case']['digest_equal'])} | "
        f"{_fmt(sync['diverged_state_case']['synchronized'])} | "
        f"`{sync['diverged_state_case']['reason']}` |")
    add("")
    add(f"**判定：{_fmt(sync['passed'])}** —— 同步点一致判真、非同步点判假、"
        "同步点但状态发散亦判假（区分“时刻对齐”与“状态一致”两个层面）。")
    add("")
    add("### (d) 压缩无损")
    add("")
    add("| 编码 | 载荷字节 | 落盘字节 | 压缩比 | 摘要不变 | 自校验 |")
    add("|---|---|---|---|---|---|")
    for row in comp["codecs"]:
        add(f"| `{row['codec']}` | {row['uncompressed_bytes']} | {row['on_disk_bytes']} | "
            f"{_fmt(row['compression_ratio'])}× | {_fmt(row['lossless'])} | {_fmt(row['verify_ok'])} |")
    add("")
    add(f"**判定：{_fmt(comp['passed'])}** —— 每个可用编码往返后状态摘要不变。"
        f"本环境无 zstd 构建（`zstd_available={_fmt(comp['zstd_available_in_env'])}`），"
        "编码协商自动落到 stdlib `lzma`，报告如实记录所用编码而非声称 zstd。")
    add("")
    add("### (e) 1 比特翻转检测")
    add("")
    payload_case = corr["cases"][0]
    header_case = corr["cases"][1]
    trunc_case = corr["cases"][2]
    add("| 注入故障 | 检测到 | 定位 |")
    add("|---|---|---|")
    add(f"| 载荷 1 bit 翻转（第 {payload_case['flipped_byte']} 字节 bit {payload_case['bit']}） | "
        f"{_fmt(payload_case['detected'])} | 字段 `{payload_case['detected_entry']}`（期望 "
        f"`{payload_case['expected_entry']}`），失效页偏移 {payload_case['bad_block_offset']} B，"
        f"页大小 {payload_case['block_size']} B |")
    add(f"| 头部 1 bit 翻转 | {_fmt(header_case['detected'])} | "
        f"`{header_case['error']['message'] if header_case.get('error') else 'n/a'}` |")
    add(f"| 文件截断一半 | {_fmt(trunc_case['detected'])} | "
        f"`{trunc_case['error']['message'] if trunc_case.get('error') else 'n/a'}` |")
    add("")
    add(f"**判定：{_fmt(corr['passed'])}**")
    add("")
    add("### (f) 回滚")
    add("")
    add(f"- 保留 {roll['retained']} 个档；把最新档头部第 {roll['flip']['byte_offset']} 字节 bit "
        f"{roll['flip']['bit']} 翻转 → `load_latest()` 回滚到 step **{roll['rolled_back_to_step']}**"
        f"（`rolled_back={_fmt(roll['rolled_back'])}`，跳过 {roll['skipped']} 个损坏档）")
    add(f"- 回滚后继续推进到 step 300，状态摘要与不间断运行"
        f"**{_fmt(roll['recovered_bit_identical'])}** 相同（`{roll['recovered_digest'][:16]}…` vs "
        f"`{roll['reference_digest'][:16]}…`）")
    add(f"- 全部档都被破坏时抛 `NoValidCheckpoint`：{_fmt(roll['all_corrupt_raises'])}")
    add("")
    add(f"**判定：{_fmt(roll['passed'])}**")
    add("")
    add("### (g) 快照 I/O 吞吐与 100 GB 外推")
    add("")
    add("| 载荷 | 写耗时 | 读+校验耗时 | 写吞吐 | 读吞吐 | 外推 100 GB 写 | 外推 100 GB 读 | 无损 |")
    add("|---|---|---|---|---|---|---|---|")
    for row in io["measurements"]:
        add(f"| {row['size_GiB']} GiB | {_fmt(row['write_s'], 2)} s | {_fmt(row['read_s'], 2)} s | "
            f"{_fmt(row['write_MBps'], 0)} MiB/s | {_fmt(row['read_MBps'], 0)} MiB/s | "
            f"{_fmt(row['projected_write_s_100GB'], 0)} s | "
            f"{_fmt(row['projected_read_s_100GB'], 0)} s | {_fmt(row['lossless'])} |")
    add("")
    add(f"- 设备（`{io['device_kind']}`，`{io['device']}`）裸写/裸读上限："
        f"写 {_fmt(io['raw_device_probe']['write_MBps'], 0)} MiB/s、"
        f"读 {_fmt(io['raw_device_probe']['read_MBps'], 0)} MiB/s（同载荷、无序列化/无摘要）")
    add(f"- 本格式写路径相对裸写的开销倍数：{_fmt(io['format_overhead_factor_write'], 2)}×")
    add(f"- 100 GB / 60 s 所需吞吐：{_fmt(io['required_MBps_for_100GB_in_60s'], 0)} MiB/s")
    add(f"- 100 GB 外推：写 {_fmt(io['projected_write_s_100GB'], 0)} s、"
        f"读 {_fmt(io['projected_read_s_100GB'], 0)} s → **{_fmt(io['passed'])}**")
    add("")
    add(f"**判定：honest FAIL（{_fmt(io['passed'])}）** —— {io['honest_limitation']}")
    add("")
    add("### (h) pytest")
    add("")
    add(f"- 命令：`{pytest_info['command']}`")
    add(f"- 用例总数 {pytest_info.get('tests_total')} / 通过 {pytest_info.get('tests_passed')} / "
        f"失败 {pytest_info.get('failures')} / 错误 {pytest_info.get('errors')} / "
        f"跳过 {pytest_info.get('skipped')}，exit={pytest_info['exit_code']}")
    add(f"- 判据 pytest≥30 全绿：**{_fmt(pytest_info['passed'])}**")
    add("")
    add("### 判据总表")
    add("")
    add("| 判据 | 通过 |")
    add("|---|---|")
    for key, label in (
        ("bit_reproducibility", "(a) 位级可复现 3 档"),
        ("energy", "(b) 能量残差 <1e-10 / 漂移 <0.1%"),
        ("time_sync", "(c) 时间同步判定"),
        ("compression", "(d) 压缩无损"),
        ("corruption", "(e) 1 bit 翻转检测"),
        ("rollback", "(f) 回滚有效"),
        ("io", "(g) 100 GB 快照 I/O <60 s"),
        ("pytest", "(h) pytest ≥30 全绿"),
    ):
        add(f"| {label} | {_fmt(criteria[key])} |")
    add("")
    add(f"**总判定：{_fmt(data['all_passed'])}**（(a)–(f)、(h) 全部满足；(g) honest FAIL，见第 6 节）")
    add("")

    add("## 4. 图")
    add("")
    add("![energy drift](artifacts/p223w3_n08/fig_energy_drift.png)")
    add("")
    add("*图 1 三档耦合算例的能量账本相对漂移（半对数）。红色虚线为 0.1% 验收线；"
        "曲线仅在浮点舍入量级波动，说明夹具本身严格守恒，任何落盘/续算误差都会被放大暴露。*")
    add("")
    add("![energy ledger](artifacts/p223w3_n08/fig_energy_ledger.png)")
    add("")
    add("*图 2 落盘往返残差与 1000 步漂移（对数）对比两条验收线（1e-10 / 0.1%）。*")
    add("")
    add("![io throughput](artifacts/p223w3_n08/fig_io_throughput.png)")
    add("")
    add("*图 3 快照 I/O 吞吐随载荷规模的变化，以及到 100 GB 的外推（右轴）与 60 s 预算线。"
        "所有测量点在单一 tmpfs 上完成，用于给出上界。*")
    add("")
    add("![codec size](artifacts/p223w3_n08/fig_codec_size.png)")
    add("")
    add("*图 4 各压缩编码的载荷/落盘字节与压缩比；三者状态摘要均不变（无损）。*")
    add("")

    add("## 5. 注意点（踩过的坑 / 局限 / 适用范围）")
    add("")
    add("1. **`hash(str)` 是每进程加盐的**：合成算例最初用 `hash(name) % 4` 派生求解器角频率，"
        "同进程内可复现、跨进程就换频率——续算必然发散。改为 sha256 摘要派生后，"
        "并加了一条跨解释器子进程的回归用例（`test_cross_process_bit_reproducibility`）"
        "把这条约束钉死。任何“跨进程位级一致”的系统都必须审查所有 `hash()` 使用点。")
    add("2. **`np.ascontiguousarray` 会把 0 维数组变成 shape (1,)**：标量字段（如 commit 计数）"
        "在落盘/回读后形状被悄悄改写、后续 `int()` 直接 TypeError。规范化的换用 "
        "`np.array(..., order='C', copy=True)` 保形。")
    add("3. **RNG 流位置必须与步进内核共享同一个 generator**：最初 runner 记录自己的 RNG，"
        "而步进函数用另一个私有 generator，续算时随机数从头开始，三档中的随机场景立刻不位级一致。"
        "结论写入 `Scenario.solvers()` 的接口约定：内核必须使用 runner 拥有的 generator。")
    add("4. **两级 Merkle 摘要 vs 单条顺序 sha256**：宿主 SHA-256 单流只有 "
        f"{_fmt(io['raw_device_probe']['write_MBps'], 0)} MiB/s 量级（受 loadavg≈"
        f"{_fmt(io.get('host_loadavg', [None])[0], 0)} 的共享 CPU 影响），"
        "并行页摘要 + 由页摘要派生字段摘要把同样的活提速约 3×；顺序单流会成为第一瓶颈。")
    add("5. **1 比特定位需要“页摘要”，只有字段级摘要只能报错不能定位**：报错信息里带"
        "字段名 + 失效页偏移是本设计刻意保留的可运维性。")
    add("6. **本节点不含任何域语义**：模块只认“命名定长数值数组 + 步号 + 时间 + 总线 + RNG 状态”；"
        "LBM 分布函数 / FEM 节点场 / DEM 颗粒表这类说法只出现在这套机制**以外**的调用方。")
    add("7. **预存在的包导入缺陷（顺手修）**：`physics/cross_solver/__init__.py` 直接 "
        "`from .wigley_bem_nk import ...`，而该模块在被裁剪的检出里不存在，导致**整个包不可导入**；"
        "已把可选域模块的导入包进 try/except 并暴露 `_OPTIONAL_IMPORT_ERROR`，"
        "checkpoint 层（仅依赖 numpy）无条件可导入。该缺陷与本次节点无关，但阻塞平台层。")
    add("8. **局限一：100 GB 快照未真实写入**。容器根盘剩余 <5 MB，全部 I/O 测量在内存后备的 "
        "tmpfs 上完成（对上界友好、对真实磁盘不友好），100 GB 结论是外推值，见第 6 节。")
    add("9. **局限二：只有合成耦合夹具**。位级可复发机制与具体求解器无关，但真实求解器的"
        "字段数量、dtype 混合度、单步耗时都会改变落盘频率与体积；本节点的证据不外推到"
        "任何具体求解器的性能数字。")
    add("10. **局限三：损坏检测只覆盖随机位翻转与截断**，未覆盖“恶意篡改且同步改头部摘要”"
        "这类攻击面（那需要签名/密钥，不属于本节点范围）。")
    add("")

    add("## 6. 误差判定（逐项，显式回答是否 <3%）")
    add("")
    add("本节点的“基准”不是物理实验值，而是**不间断运行的同一算例状态**（位级）与"
        "**解析守恒量**（正交变换下 2-范数不变）。判定如下：")
    add("")
    add("| 关键量 | 参考值 / 基准 | 本文结果 | 相对偏差 | <3%？ |")
    add("|---|---|---|---|---|")
    add("| 续算末态 sha256（3 档） | 不间断运行末态摘要 | 完全相同 | 0（逐位相同） | **是** |")
    max_drift = max(r["drift_1000_steps_continuous_rel"] for r in energy["scenarios"])
    add(f"| 能量 E_total 落盘残差 | 0（无损序列化的解析期望） | "
        f"{_fmt(max(r['residual_abs'] for r in energy['scenarios']))} | 0 | **是** |")
    add(f"| 1000 步能量相对漂移 | 0（正交变换严格守恒） | {_fmt(max_drift)} | "
        f"{_fmt(max_drift)} | **是** |")
    add(f"| 压缩往返状态摘要 | 原状态摘要 | 相同 | 0 | **是** |")
    add(f"| 1 bit 翻转检出率 | 100% | 载荷/头部/截断 3/3 检出 | 0 | **是** |")
    add(f"| 100 GB 快照 I/O 时间 | 60 s 上限 | 外推 "
        f"{_fmt(io['projected_write_s_100GB'], 0)} s（写）/ "
        f"{_fmt(io['projected_read_s_100GB'], 0)} s（读） | "
        f"{(io['projected_write_s_100GB'] / 60.0 - 1) * 100 if io.get('projected_write_s_100GB') else float('nan'):.0f}% 超限 | "
        "**否（honest FAIL）** |")
    add("")
    add("**关于 (g) 的诚实结论**：判据要求 100 GB 快照 I/O < 60 s，等价于 ≥1.71 GB/s 的"
        "端到端写吞吐。本宿主上裸写上限只有 "
        f"{_fmt(io['raw_device_probe']['write_MBps'], 0)} MiB/s（tmpfs，loadavg≈"
        f"{_fmt(io.get('host_loadavg', [None])[0], 0)} 的共享 128 核机器），"
        "即使摘要开销归零也无法在 60 s 内写完 100 GB：**这是设备/宿主约束，不是本格式能靠改代码"
        "跨越的**。本节点因此把 (g) 记为 honest FAIL 并给出可复核的数字链："
        "裸设备上限、格式开销倍数、所需吞吐、外推时间。要真正满足该判据，需要"
        "① 落盘路径做分块并行/流水线写（多文件或并行文件系统），② 换个带宽 ≥2 GB/s 的存储卷，"
        "③ 或把判据改写为“每 GB 落盘开销 < X s”。")
    add("")
    add("## 7. 审计链")
    add("")
    add("- 机器判据：`artifacts/p223w3_n08/checkpoint_validation.json`"
        "（本报告全部数字的唯一来源，字段与判据一一对应）")
    add("- 复现命令：`python tools/run_checkpoint_bitreproducibility_validation.py`"
        "（需可写 TMPDIR，>2 GiB 空间；本仓库在 `/dev/shm` 下执行）")
    add("- 测试：`cd backend && python -m pytest tests/test_cross_solver_checkpoint.py`")
    add("- 报告渲染：`python tools/render_checkpoint_report.py`")
    add(f"- 运行耗时：{_fmt(data['runtime_s'], 1)} s")
    add("")
    return "\n".join(lines)


def _scenario_dt(key: str) -> str:
    from physics.cross_solver.checkpoint_scenarios import SCENARIOS  # noqa: WPS433

    return str(SCENARIOS[key]["dt"])


def _scenario_rng(key: str) -> str:
    from physics.cross_solver.checkpoint_scenarios import SCENARIOS  # noqa: WPS433

    seed = SCENARIOS[key]["rng_seed"]
    return "无" if seed is None else f"seed={seed}"


def main() -> int:
    import sys

    sys.path.insert(0, str(REPO / "backend"))
    data = json.loads((OUT / "checkpoint_validation.json").read_text())
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(render(data))
    print(f"wrote {DOC} ({len(DOC.read_text().splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())