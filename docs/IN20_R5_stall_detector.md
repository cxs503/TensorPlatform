# IN20-R5 · 停摆自检自愈（stall detector，daemon 级机制替身）

> 战役 DAG 节点 IN20-R5（续航军 R5）。判据数字全部由 `tools/stall_detector_report.py`
> 程序化注入（源：`artifacts/in20_r5/criteria.json`），本文无手抄数字。
> 机制基因=owner 渐退令：**替身必须独立于任何人类会话与 AI 协调者会话**，
> 因此本体是 systemd/容器 daemon（`deploy/taskplane/tp-stall-detector.service`），
> 不是「等某个人来看一眼」。

## ① 案例介绍（问题 / 数据 / 参数）

平台曾经会「安静地停住」。2026-11-07 00:52→03:18 就是一次：脉搏表最后一行
**2026-11-07T00:52:35**，下一行 **2026-11-07T03:20:08**，
中间 **147.55 min（8853 s）**
没有任何观测行；同窗口舰队在线 52 席而只有个位数在干活、可派 DAG 节点为 0。
发现它的不是机制，是人的注意力——这正是本军要消灭的单点。

| 项 | 值 |
|---|---|
| 回归样例 | 2026-11-07T00:35:00+00:00 → 2026-11-07T03:30:00+00:00（176 个分钟样本） |
| 实测断档 | 2026-11-07T00:52:35 → 2026-11-07T03:20:08（147.55 min） |
| 断档内证据等级 | WITHHELD（缺口内无任何观测行） |
| 规则 1 断粮 | pending=0 且 queued≤0 且 idle≥8 且 idle/online≥0.5，持续 900 s |
| 规则 2 舰队心跳丢失 | 失联比≥0.5 且席位≥5，持续 300 s |
| 规则 3 脉搏停滞 | `saturation_pulse.max(ts)` 老化 ≥1800 s |
| 冷却 | 1800 s（同一事件不重复轰击） |
| 心跳新鲜窗 | 300 s |

物理量类比（同一套「停摆」直觉）：脉搏=系统的自由表面高度信号，断流即失效；
在线/idle 席位=可用排水量中的储备浮力；pending 节点=待装货舱容。三者只有
**同时**满足「无货可拉 + 大量储备浮力 + 观测信号断流」才构成停摆判定，单看任一量
都会误判（这正是三条规则互不代偿的设计理由）。

## ② 怎么做的（方法 / 工具链 / 关键设置）

1. **取数（只读）**：`collect_from_db()` 用只读 SELECT 把四组量压成一个 `Sample`：
   campaign DAG 可派节点（状态白名单，默认 pending/queued/ready/todo/not_started）、
   排队任务、`factory_resident_workers` 心跳新鲜度、`saturation_pulse` 最后一行。
   `idle = online − busy`（与 `tools/pulse` 口径一致）；worker 表自述 `state='idle'`
   只进 `detail` 作对账——IN15-O1 已实证「满负荷时 54 行仍报 idle」，用它当判据必误报。
2. **判定（纯函数）**：`evaluate_sample(sample, thresholds)` 不读墙钟、不碰 I/O；
   持续窗/冷却/恢复在 `Detector`（状态可序列化，跨进程续命）。
3. **动作面（子进程 + 可 dry）**：`Ops` 三条动作——`refill`（确保补给器
   `selfstarter.py` 存活；已在跑则据实记 `already_running`）、`fleet_restart`
   （systemd unit 优先，否则 TERM + 分离重启，沿用 `tp_autoheal` 口径）、
   `selfproof`（写带 `selfproof` 标记的 beacon，把「脉搏生产者死」与「检测器死」分开）。
   `--action-mode dry` 只做只读探活，`live` 才落动作；**dry ≠ 不检查**。
4. **留痕**：每周期一条 `observation` 审计 + 每条事件/动作一条记录，JSONL 逐条
   hash 链（`prev_sha → sha`），`verify_chain()` 可机械验伪；`beacon.json` 每周期原子
   前进（外部判死：`--check-beacon --max-age`）。
5. **回滚路径**：`--rollback` 清全部闸闩（since/fired/cooldown）并把旧 state 归档成
   `state.rolled_back.<epoch>.json`，审计只 append（回滚自身也是一条记录）；
   unit 侧 `systemctl disable --now tp-stall-detector` 即彻底停用，台账零写入。
6. **回归样例**：`tests/fixtures/stall_20261107_0052_0318.json` 由
   `tools/stall_fixture_20261107.py` 生成——锚点=实测脉搏行，断档内按显式重建律插值，
   重建律写在 fixture 的 `provenance` 里（任何读者可重跑脚本逐字节复核）。

## ③ 计算结果（数字表）

### 3.1 阳性回放：历史停摆必须报警并触发

| 规则 | fire？ | 首次 fire | 动作 | 动作回执 |
|---|---|---|---|---|
| R1 断粮 | True | 2026-11-07T01:10:00+00:00 | `refill` | `already_running` |
| R2 舰队心跳丢失 | False | — | — | 本案例应沉默（负控） |
| R3 脉搏停滞 | True | 2026-11-07T01:23:00+00:00 | `selfproof` | `beacon_written` |

回放观测 176 个样本、事件 {'onset': 2, 'fire': 2, 'persist': 25, 'suppressed': 0, 'recovered': 2}、
fire 2 次。两条 P1 都落在停摆窗口内部（00:52→03:18），
03:19 补给回流后各出一条 `recovered`——**报警与恢复都不需要人。**

### 3.2 检出延迟（本交付的核心精度量）

| 量 | 值 |
|---|---|
| 断粮条件首次成立 | 2026-11-07T00:55:00+00:00 |
| R1 fire | 2026-11-07T01:10:00+00:00（持续门 900 s） |
| R1 检出延迟 | 0 s = 阈值 0.00% · 停摆开始后 1045 s |
| R3 老化门到达 | 2026-11-07T01:22:35+00:00（老化 1800 s） |
| R3 fire | 2026-11-07T01:23:00+00:00 |
| R3 检出延迟 | 25 s = 阈值 1.39% · 停摆开始后 1825 s |
| 采样分辨率 | 60 s |

延迟上限由采样周期决定（回放 60 s/样本）：两规则延迟均 < 1 个采样周期，
相对阈值误差最大 1.39%。

### 3.3 阴性控制

| 场景 | 结果 |
|---|---|
| 健康态 2 h（120 样本，有活/心跳新鲜/脉搏新鲜） | fire=0，`observations=120`（「不告警」可数） |
| 真实台账单次体检（2026-11-07T03:36:35+00:00） | alarm=False，fires=0 |
| 10 min 断粮即恢复 | 只有 onset+recovered，无 fire |
| 脉搏行缺失（观测盲区） | 只出 1 条 P2 `unknown`，不 fire |

真实台账快照（`artifacts/in20_r5/live/alerts.json`）：

| 量 | 值 |
|---|---|
| pending DAG 节点 | 0 |
| 排队任务 queued | 6 |
| 在线席位 online | 40 |
| 空闲席位 idle | 24 |
| idle/online | 0.6 |
| 心跳新鲜/登记 | 40/53 |
| 失联比 | 0.2453 |
| 脉搏老化 (s) | 111.9 |

注意 live 的 `idle/online=0.6` 已越过断粮门，但 `queued=6`
→ R1 保持沉默：判定要求「一个可派对象都没有」，排队任务存在时不许喊断粮
（宁可漏报不误报）。`lost_ratio=0.2453 < 0.5` → R2 沉默。

### 3.4 审计链与判据数字

| 项 | 值 |
|---|---|
| replay 审计记录 | 213 条，链验证 ok=True |
| live 审计记录 | 1 条，链验证 ok=True |
| pytest | 16 passed（16 passed, 1 warning in 6.27s） |
| 判据 JSON | `artifacts/in20_r5/criteria.json` |

![stop timeline](fig_stall_timeline.png)

图：上=脉搏老化（红）与 30 min 门；下=idle/online（蓝，左轴）与在线/忙碌/可派节点（右轴）。
红色阴影为实测断档（无观测），空心标记为两条规则的 fire 时刻。

## ④ 结果图说明

单幅双面板图 `fig_stall_timeline.png`（matplotlib，数据全部来自 fixture + 回放 trace，
无手工点）。为什么不做云图：本任务是**机制/时序**类，场量云图不适用；替代物是
「信号 + 门限 + 判定时刻」的时序判读图，它直接支撑 3.2 的延迟数字。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **断档内数值是重建的**：`provenance` 明确标注 gap 内 busy/pending 为
   `RECONSTRUCTED`；检测器判据只依赖「pending=0 + idle 高 ≥15 min」「脉搏停 ≥30 min」
   这两个由任务书与 gap 边界实测支撑的定性形状，不依赖 gap 内具体数值。
   若换一份真实逐分钟台账，回放结果可能位移，但定性判定不应翻转。
2. **queued 的语义坑**：`queued` 任务可能长期无人认领（本次 live 有
   6 条停摆期遗留），此时「有货」是假象。本机制按
   「宁可漏报不误报」保持沉默，代价是**这类死排队不会触发 R1**——需另配
   「queued 年龄」规则（记为后续项，不在本单范围）。
3. **status 白名单是最大误报面**：campaign 节点状态词若改名（如 `planned` 与
   `pending` 混用），`pending` 计数会失真。因此每条 digest 都打印
   `dag_status_hist` / `plan_status_hist`，把语义风险常驻在观测面上。
4. **动作面回执随宿主状态变化**（回放里 R1 记为 `already_running` 而非
   `would_launch`，因为本机补给器确实在跑）：这是特性不是缺陷——「补给器在跑仍断粮」
   意味着饥饿不是没人拉车而是没货可拉，digest 会保留 P1。
5. **两处未做**：(a) 未把 detector 的 P1 直接推进 obs 的 alerts 表（当前落
   `alerts.json` + digest.md，格式对齐 IN15-O8，接入由其侧消费）；
   (b) 未在真实 unit 下跑满 24 h（本单只做单次 live 体检 + 176 样本回放 + 16 项测试）。

## ⑥ 误差判定（对照基准逐项）

| 关键量 | 基准 | 本实现 | 误差 | 是否 <3% |
|---|---|---|---|---|
| R1 检出时刻 | 条件成立 + 900 s | 2026-11-07T01:10:00+00:00（延迟 0 s） | 0.00% | 是（≤1 采样周期） |
| R3 检出时刻 | 脉搏末行 + 1800 s | 2026-11-07T01:23:00+00:00（延迟 25 s） | 1.39% | 是（≤1 采样周期） |
| 断档时长 | 实测 147.55 min | 回放断言 147.55 min | 0（同源实测） | 是 |
| 阴性误报率 | 0 次/2 h 健康态 | 0 次（120 样本） | 0 | 是 |
| 审计链完整性 | 全链 hash 一致 | 213 条全通过 | 0 | 是 |

唯一**无基准**的量：真实停摆期的逐分钟 worker 状态（历史未落库）——故按 ⑤.1
声明证据等级，不做百分比误差承诺（honest WITHHELD，不粉饰）。

## 附：产物与复现命令

```
ops/taskplane/stall_detector.py                     daemon 本体（判定/动作/审计/回滚）
tools/stall_fixture_20261107.py                     回归 fixture 生成器（锚点可重算）
tests/fixtures/stall_20261107_0052_0318.json        176 样本回放时间线
tests/test_stall_detector_in20_r5.py                16 项验收测试
deploy/taskplane/tp-stall-detector.service          生产 daemon unit（独立于任何会话）
artifacts/in20_r5/{replay,live}/                    审计链 / beacon / digest / alerts
artifacts/in20_r5/criteria.json                     机器可裁判据数字
```

```bash
python3 ops/taskplane/stall_detector.py --fixture tests/fixtures/stall_20261107_0052_0318.json \
    --replay --out artifacts/in20_r5/replay --action-mode dry        # rc=2（有 P1）
python3 ops/taskplane/stall_detector.py --from-db --once \
    --out artifacts/in20_r5/live --action-mode dry                   # rc=0（健康态）
python3 -m pytest tests/test_stall_detector_in20_r5.py -q
python3 ops/taskplane/stall_detector.py --check-beacon artifacts/in20_r5/live/beacon.json
python3 ops/taskplane/stall_detector.py --rollback --out runs/stall_detector   # 回滚
```
