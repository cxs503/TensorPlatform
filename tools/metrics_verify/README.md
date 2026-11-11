# tools/metrics_verify — 度量工具验证链（pulse / chronicle / portrait）

平台三个度量工具此前只有「报告值」，没有「可证伪的验证面」。本包把「工具能测出来」
变成机器可裁决的判据：合成真值 → 被测工具 → 回收值比对，每条判据都配负控。

| 模块 | 作用 |
|---|---|
| `sim.py` | 真值生成器：心跳流（延迟/丢失/停顿）、事件流（乱序/丢失/重复）、席位行为（成功率/工时/能力域） |
| `pulse_latency.py` | 延迟分位数回收与判据、`seq` 间隙丢失检出、**生产代码** `worker_is_fresh`/`compute` 的逐 agent 交叉核验 |
| `chronicle_integrity.py` | 事件流契约校验器（`id, session_id, seq, kind, created_at`）、乱序/丢失/重复检出 |
| `portrait_consistency.py` | **生产代码** `portrait.aggregate`/`verify_against_missions` 与真值比对、CUSUM 画像漂移检出 |
| `api.py` | 三个验证端点 + 阈值端点，非法输入一律 422 |
| `suite.py` | 全链路验收运行 → `report.json` / `report.md` / `report_paper.md` + 四张图 |
| `figures.py` / `report_doc.py` | 由同一份 payload 生成图与论文级文档（零手抄） |

## 用法

```bash
python3 -m tools.metrics_verify.suite --out artifacts/metrics_verify/p465w1_n01
python3 -m pytest tests/metrics_verify -q          # 51 例
uvicorn tools.metrics_verify.api:app --port 8099   # HTTP 面
```

端点：

```
POST /v1/metrics_verify/pulse       {n_agents, n_heartbeats, seed, median_latency_s, sigma,
                                     stall_rate, loss_rate, enabled_fraction, mean_max_s, p99_max_s}
POST /v1/metrics_verify/chronicle   {n_events, sessions, seed, disorder_rate, disorder_detect_min}
POST /v1/metrics_verify/portrait    {n_agents, missions_per_agent, seed, tolerance,
                                     injected_status_flips, drift}
GET  /v1/metrics_verify/spec        验收阈值（单一真值来源）
GET  /health
```

## 判据（与战役节点 metadata 一致）

| id | 判据 | 阈值 |
|---|---|---|
| a | 心跳延迟均值 / P99（100 agents × 1000 heartbeats） | <1 s / <5 s |
| b | 5 % 心跳丢失检出率（严格口径） | >95 % |
| c | 10000 事件完整性 / 顺序正确率 | =1.0 / =1.0 |
| d | 1 % 乱序检出率（换位粒度，误报 0） | >99 % |
| e | 画像成功率 / 平均工时 / 能力域份额误差 | <5 % |
| f | 画像漂移检出延迟（CUSUM） | <10 事件 |
| g | pytest 例数 / 验证端点数 / 非法输入 | ≥15 / =3 / 422 |

## 边界（不可外推的部分）

* 验证的是**计算内核与契约**，不是 DB 取数在线行为：`psycopg`/`psql` 路径不在本链内，
  因此整套验证可在无数据库的 CI 环境运行。
* `seq` 间隙检测器看不到尾部丢失（严格口径已把不可检出样本计入分母）；生产侧需
  用 agent 自报计数对账。
* 漂移检测器以 ARL0≈1.7e4 事件标定：10 事件内的检出保证只覆盖严重突变
  （0.95→0.20~0.40）；对温和变化（0.95→0.50）延迟超出设计点，报告如实在功效曲线中列出。
* 合成延迟/工时取 lognormal（闭式真值可算）；真实平台的重尾与时钟偏斜需重新标定阈值。