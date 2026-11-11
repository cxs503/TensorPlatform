# TensorPlatform 度量工具验证链：pulse / chronicle / portrait

- run_id: `mv-20261111T061931Z` · git_commit: `a63fb6a0fbf8847a505beb269913ddf5846a5c5a` · overall_pass: **True**

## ① 案例介绍（物理背景与参数表）

平台的三个度量工具此前只有「报告值」，没有「可证伪的验证面」：
`tools/pulse/saturation_pulse.py` 回答「舰队是否活着、心跳延迟多大」，
`tools/chronicle` 回答「事件流有没有丢/乱」，`tools/worker_portrait/portrait.py`
回答「席位画像与它的行为是否一致」。三者都没有被测过——本节点建立合成真值链，
把「工具能测出来」变成可机器裁决的判据，并给出工具错误检出的负控。

本节点不引入任何领域语义：合成实体为 `agent-NNN` / `sess-NN` / `worker-NN`
与合成能力域 `domain_alpha/beta/gamma`，全部经 API 参数注入，平台仓零领域硬编码。

**参数表（真值注入侧，全部来自 report.json 的 `parameters`）**

| 项 | 值 | 说明 |
|---|---|---|
| pulse 集群规模 | 100 agents × 1000 heartbeats | 共 100000 个延迟样本 |
| 注入延迟分布 | lognormal(median=0.08 s, σ=0.9) | 单向下行延迟，闭式分位数即参考值 |
| 心跳完整率 | 100.000 %（loss_rate=0.0） | 丢失注入仅用于判据 (b) 的流 |
| 心跳间隔 / 新鲜窗 | 1.0 s / 300 s | 与生产 `saturation_pulse.WINDOW_SECONDS_DEFAULT` 一致 |
| chronicle 事件流 | 10000 事件 / 5 会话 | 列形 `id, session_id, seq, kind, payload, created_at` |
| 乱序 / 丢失 / 重复注入 | 1 % / 1 % / 1 %（各自独立流） | 相邻同会话换位=一次乱序故障 |
| portrait 席位群 | 8 workers × 200 missions | 共 1600 条 mission 行 |
| 行为真值 | p_success∈[0.55,0.95]、wall~lognormal(σ=0.45)、能力域权重逐席位不同 | 生成器在序列化前累加真值 |

## ② 怎么做的（方法 / 流程 / 工具链）

验证链是「真值 → 可观测量 → 回收值」三段式，而不是把工具再跑一遍：

1. **生成真值**（`tools/metrics_verify/sim.py`）：合成心跳流（含延迟/丢失/停顿）、
   事件流（含乱序/丢失/重复注入）、席位行为（成功率/工时/能力域），并在生成时
   保留注入参数与逐实体真值。
2. **经过被测工具**：pulse 侧调用**生产代码** `saturation_pulse.worker_is_fresh()` 与
   `saturation_pulse.compute()`；portrait 侧调用**生产代码** `portrait.aggregate()` 与
   `portrait.verify_against_missions()`；chronicle 侧因平台无纯函数校验器，
   本节点交付参考实现 `validate_event_stream()`（按 `tensor_agent_session_events` 列契约）。
3. **回收与裁决**（`pulse_latency.py` / `chronicle_integrity.py` /
   `portrait_consistency.py` + `suite.py`）：把回收值与注入真值比较，逐条判据给数。
4. **负控**：每条判据都配一个「本该失败」的注入，证明工具是检出的而不是恒真的。
5. **机器可算产物**：`report.json`（判据数字 + 阈值 + 种子 + 提交号 + 被测模块 sha256）、
   `report.md`、四张图、三个 HTTP 端点（`POST /v1/metrics_verify/{pulse,chronicle,portrait}`）。

一键复现（确定性种子，无 DB、无网络）：`python3 -m tools.metrics_verify.suite --out artifacts/metrics_verify/p465w1_n01`。

## ③ 计算结果（程序化注入，零手抄）

### 判据总表

| 判据 | 目标 | 实测 | 阈值 | 裁决 |
|---|---|---|---|---|
| (a) pulse 心跳延迟 | 均值<1 s、P99<5 s | mean=0.1202 s，P99=0.6505 s（n=100000） | <1.0 s / <5.0 s | 是 |
| (b) 心跳丢失检出 | 5 % 丢失检出>95 % | 99.879 %（4954/4960，严格口径） | >95.0 % | 是 |
| (c) chronicle 完整性 | 10000 事件无丢失、顺序 100 % | completeness=1，order=1 | =1.0 / =1.0 | 是 |
| (d) 乱序检出 | 1 % 乱序检出>99 % | 100.000 %（50/50 次换位），误报 0 | >99.0 % | 是 |
| (e) portrait 一致性 | 成功率/工时/能力域误差<5 % | 0 / 5.658e-07 / 0 | <0.05 | 是 |
| (f) 画像漂移检出 | 行为突变检出延迟<10 事件 | mean=4.45 事件，max=9 事件（20 seed） | <10 事件 | 是 |
| (g) 工具面卫生 | pytest≥15、3 端点、非法输入 422 | 51 例测试、3 端点、422={'pulse': 422, 'chronicle': 422, 'portrait': 422} | ≥15 / =3 / 422 | 是 |

### 延迟分布细节（pulse，判据 a 的支撑数）

| 统计量 | 注入解析值 | 回收值 | 相对误差 |
|---|---|---|---|
| 中位数 p50 [s] | 0.08 | 0.080333 | 0.416 % |
| p90 [s] | 0.25352 | 0.25372 | 0.080 % |
| p99 [s] | 0.6492 | 0.65055 | 0.207 % |
| 均值 [s] | 0.11994（lognormal 闭式） | 0.12018 | 0.198 % |
| 方差 [s²] | 0.017953（闭式） | 0.018037 | 0.466 % |

### 生产内核交叉核验（pulse `compute()` / `worker_is_fresh()`）

- `worker_is_fresh` 逐 agent 判定：100 个 agent，错判 0 个；
- `compute()` 快照 vs 合成真值：online 100=100，busy 50=50，idle 50=50，stale_claims 12=12 → `counts_match=True`。

### 事件流细节（chronicle）

| 场景 | 注入 | 检出 |
|---|---|---|
| 诚实流 | 0 | 缺失 0、seq 回退 0、时间戳回退 0 |
| 乱序 1 % | 50 次换位（100 个事件位移） | 检出 50 次，误报 0 |
| 丢失 1 % | 100 事件 | completeness=0.99（<1 即检出） |
| 重复 1 % | 100 次重复 | 检出 100 |

### portrait 逐席位一致性（判据 e）

| 席位 | 成功率(真值/回收) | 平均工时[s](真值/回收) | 能力域份额最大偏差 |
|---|---|---|---|
| worker-00 | 0.535 / 0.535 | 715.776 / 715.776 | 0 |
| worker-01 | 0.83 / 0.83 | 798.626 / 798.626 | 0 |
| worker-02 | 0.7 / 0.7 | 917.491 / 917.491 | 0 |
| worker-03 | 0.63 / 0.63 | 1092.54 / 1092.54 | 0 |
| worker-04 | 0.89 / 0.89 | 1292.36 / 1292.36 | 0 |
| worker-05 | 0.73 / 0.73 | 1332.41 / 1332.41 | 0 |
| worker-06 | 0.595 / 0.595 | 1500.84 / 1500.84 | 0 |
| worker-07 | 0.93 / 0.93 | 1628.74 / 1628.74 | 0 |

### 漂移检出与功效曲线（判据 f）

- 注入突变：成功率 0.95 → 0.2 @ 第 150 事件；检测器参数 reference_n=50、p_alt=0.3、h=8.0 nats；
- 检出延迟：mean=4.45、max=9 事件（20 seeds，漏检 0）；受控误报：稳态流 0/20，实测受控平均运行长度 ARL0≈16783.2 事件（要求 ≥1000）。

| 突变后成功率 | 检出数 | 平均延迟 | 最大延迟 | 是否落在 10 事件内 |
|---|---|---|---|---|
| 0.6 | 10/10 | 10.3 | 17 | **否** |
| 0.5 | 10/10 | 7.5 | 14 | **否** |
| 0.4 | 10/10 | 6.2 | 9 | 是 |
| 0.3 | 10/10 | 5.2 | 9 | 是 |
| 0.2 | 10/10 | 4.2 | 9 | 是 |

- 能力域漂移（次要控制）：20/20 检出，平均延迟 5 事件（阈值 20）。

## ④ 结果图

**图 1 pulse：100 个 agent×1000 次心跳的延迟 ECDF 与直方图；实测均值/P99 与注入 lognormal 解析分布、判据线（1 s / 5 s）同图对比。**

`artifacts/metrics_verify/p465w1_n01/fig1_pulse_latency.png`（sha256 `820e4631122a3556…`）

**图 2 chronicle：完整性/顺序/乱序/丢失/重复五类检出率与注入-回收计数（左/右）。**

`artifacts/metrics_verify/p465w1_n01/fig2_chronicle_integrity.png`（sha256 `d90da14cf77d9490…`）

**图 3 portrait：逐席位成功率「回收 vs 真值」散点（±5 % 带）与三类误差预算。**

`artifacts/metrics_verify/p465w1_n01/fig3_portrait_consistency.png`（sha256 `4ddec1bdb726b078…`）

**图 4 portrait 漂移：CUSUM 统计量轨迹（真实突变点与告警点）与 20 seed 的检出延迟分布。**

`artifacts/metrics_verify/p465w1_n01/fig4_portrait_drift.png`（sha256 `9d8ff520aabf250f…`）

图均由 suite 在验收运行时从同一份 report.json 数据生成，PNG 与 JSON 同批入库，
图像不能被单独替换而不破坏 sha256 链。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **尾部丢失不可见**：只靠 `seq` 间隙的检测器看不到「某 agent 最后若干次心跳丢失」，本次 5 % 丢失流中此类不可检出样本 4 个；严格口径（分母含它们）已如实计入判据 (b)，生产侧需用 agent 自报计数做对账补充。
2. **漂移检测器的功效有边界**：10 事件的设计点对应「严重突变」（0.95→0.20~0.40）；对温和变化（0.95→0.50）实测延迟均值远超 10 事件（见功效曲线），这是统计信息量的限制，不是实现缺陷——报告不据此宣称更强能力。
3. **特异性以 ARL0 而非「零误报」计**：CUSUM 在 ARL0≈1e4 量级下，250 事件窗口出现一次误报属预期；本报告同时给出稳态流实测误报数与 ARL0，不使用「零误报」措辞。
4. **DB 侧未被单元测试覆盖**：`pulse`/`portrait` 的取数（psycopg / psql）不在本链内；本链验证的是**计算内核与契约**（`compute`/`worker_is_fresh`/`aggregate`/`verify_against_missions`/事件校验器），不是数据库在线行为。这是刻意的边界：验证面必须能在 CI 与无 DB 环境跑。
5. **合成分布≠生产分布**：延迟取 lognormal、工时取 lognormal，真值可按闭式给出；真实平台存在时钟偏斜、批量提交、GC 停顿等重尾因素，判据阈值在真实数据上需重新标定（`worker_is_fresh` 对超前心跳按 skew 处理，已单独测到）。
6. **判据 (e) 的数值接近 0 是设计使然**：portrait 与真值来自同一批抽样、不同代码路径（真值在序列化前累加，画像从 ISO 时间戳重算），因此正确管道下误差应为舍入量级；该判据的**鉴别力**由负控证明（注入 15 % 状态翻转后最大成功率误差升至 0.24）。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否<3%」）

| 关键量 | 基准 | 误差 | 是否 <3 % |
|---|---|---|---|
| 延迟中位数 回收值 | 注入 lognormal 闭式分位数 | 0.416 % | 是 |
| 延迟 p90 回收值 | 注入 lognormal 闭式分位数 | 0.080 % | 是 |
| 延迟 p99 回收值 | 注入 lognormal 闭式分位数 | 0.207 % | 是 |
| 成功率（判据 e） | 生成器序列化前真值 | 0.000 %（绝对） | 是 |
| 平均工时（判据 e） | 生成器真值 | 0.000 % | 是 |
| 能力域份额（判据 e） | 生成器真值 | 0.000 %（绝对） | 是 |
| 事件完整性/顺序（判据 c） | 发射侧计数 | 缺失 0、回退 0 | 是 |
| 乱序检出（判据 d） | 注入换位集合 | 漏检 0 次 | 是 |

结论：延迟分位数回收误差最大 0.416 %（<3 % 达成）；portrait 三类误差最大 0.000 %（<3 % 达成）；事件流完整性为精确量（缺失=0），乱序检出为计数判据（漏检=0）。漂移延迟是**事件数**而非百分比误差，按判据 f 的 10 事件上限裁决。

## 审计链

| 项 | 值 |
|---|---|
| git commit | `a63fb6a0fbf8847a505beb269913ddf5846a5c5a` |
| 运行时间 | `2026-11-11T06:19:31.685125+00:00` |
| 确定性种子 | pulse/chronicle/portrait = 20261111 / 20261111 / 20261111 |
| 被测模块 `tools/pulse/saturation_pulse.py` | `2d30ede68e6b49017e58c5cbca4824d67c822d69ff4105c3edfe51d38eb90e51` |
| 被测模块 `tools/worker_portrait/portrait.py` | `aa9cab0da038aa49f6f176b2617d22baa54ca7467e04c2b550c8f73e0b1c644d` |
| 验证链模块 `tools/metrics_verify/stats.py` | `3ff1c87d0f8c8d80e1a43369999a2dc7217cc22f1903f1639f285b4b41b84d21` |
| 验证链模块 `tools/metrics_verify/sim.py` | `65618fa1540dcd87fc4f96ffef3ec01ff0f04cf19b83b2e2bdf283633923adbb` |
| 验证链模块 `tools/metrics_verify/pulse_latency.py` | `1c84b5f4e0524f68b39d472a7284d70692378d9a3be0cc83f2802a3bf58f8828` |
| 判据 g 探针 | 端点 3 个，非法输入状态 {'pulse': 422, 'chronicle': 422, 'portrait': 422} |

负控明细（每条都要求「注入 → 工具报错」）：

| 负控 | 观察量 | 结果 |
|---|---|---|
| pulse 8 % 停顿 | P99=13.01 s > 5 s 上限 | 是 |
| pulse 干净流 | seq 假间隙 0 | 是 |
| chronicle 1 % 丢失 | 对真值缺失 100 | 是 |
| chronicle 1 % 重复 | 重复 100 | 是 |
| portrait 15 % 状态翻转 | 成功率误差 0.24 > 5 % | 是 |

