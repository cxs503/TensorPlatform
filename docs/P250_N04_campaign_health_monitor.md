# p250-n04 战役节点健康监控成熟化（TensorPlatform / ops.taskplane）

> 节点：`p250-n04-campaign-health-monitor`　域：平台共性机制（零领域语义）　
> 产物：`ops/taskplane/campaign_health_monitor.py`（能力）、`tools/campaign_health_scenario.py`（合成场景）、
> `tools/campaign_health_report.py`（报表）、`tests/test_campaign_health_monitor_p250_n04.py`（27 用例）、
> `artifacts/p250_n04/*`（判据/告警/恢复/2 图）、`docs/P250_N04_campaign_health_monitor.md`（本文）。

## ① 案例背景与判据

战役 DAG 的节点此前**没有任何单节点健康面**：`ops/taskplane/stall_detector.py` 只监控
平台级停摆（断粮 / 舰队心跳丢失 / 脉搏停滞），不看某节点是否烧穿预算、是否假活、是否在漏资源。
本单把"人发现"变成机制：三类检测锚 + 终止后 salvage 释放，全部落在纯函数层，可离线重放、
可 pytest 复现、不依赖任何人类会话。

**判据（先于实现，机器可裁）**

| 判据 | 结论 | 要求 |
|---|---|---|
| A1_timeout | PASS | 3/3 超时节点被正确识别并终止 |
| A2_idle | PASS | 3/3 空转节点被正确识别（N=10 心跳） |
| A3_leak | PASS | 2/2 资源泄漏场景被正确预警 |
| A4_negative_control | PASS | 5 个正常运行节点误报 0/5（误报率 <1%） |
| A5_auto_recovery | PASS | 终止后下游依赖正确释放（DAG 边状态更新） |
| A6_artifacts | PASS | pytest 全绿 + 2 图（健康监控仪表板、预警时间线） |

阈值（全部集中在 `Thresholds`，可配置、越界即 ValueError 拒绝启动）：
`{"idle_heartbeats": 10, "resource_pct": 80.0, "resource_sustain_min": 5.0, "timeout_kill_factor": 2.0, "timeout_warn_factor": 1.5}`

## ② 方法

1. **场景注入（零领域语义）**：19 节点合成场景（13 受监控 + 6 下游 DAG），每节点只暴露
   **一个**判据以避免归因混淆；节点名一律 `synthetic-*`，模块内不含任何领域词条
   （测试 `test_scenario_is_synthetic_and_domain_free` 守住）。线上数据由
   `scenario_from_rows(rows, heartbeats)` 适配：行字段与 `backend/api/campaign_plans.py` 的
   `campaign_nodes` 列同名（planned_start/planned_end/dependencies/status），planned 时长优先取
   显式 `planned_minutes`、否则由 planned 窗口推导；无心跳节点 `monitored=False` 但留在 DAG 里。
2. **A1 超时**：`elapsed/planned >= 1.5` → P2 `node_timeout`；`>= 2.0` → P1 `node_timeout_kill`
   （各只报首次跨越，含边界：恰好 1.5×/2.0× 即触发）。
3. **A2 空转**：工作区指纹 = `sha256(max_mtime, new_files, commit)`；连续 N=10 次心跳指纹不变
   **且** `progress` 计数未严格递增 → P1 `node_idle`。progress 活口让"只算不落盘"的算力型节点不被误杀。
4. **A3 资源泄漏**：`mem_pct`/`cpu_pct`/`disk_pct` 任一 `>= 80%` 且**持续** ≥ 5 min（窗口内 ≥2 采样）
   → P2 `resource_pressure`；回落即重新武装（有恢复可再报）。
5. **A4 自动恢复**：终止节点 → `failed`；其**直接下游**边 `waiting → released`，上游从下游
   未满足依赖集移除；下游无剩余依赖且原 blocked/pending → `runnable`，否则保留 `blocked`
   并记录剩余依赖。隔代节点不动（上游是残废而非完成）。仅预警（超时 P2 / 资源 P2）**不触发** salvage。
6. **审计与确定性**：告警日志 JSONL + 哈希链（逐行 `prev_hash`/`hash`，篡改可检出）；
   时间取值一律来自场景时间戳（内容派生时钟），同一输入两次运行逐字节相同。

## ③ 计算结果（程序化注入，零手抄）

场景共 19 节点 / 13 受监控；告警 11 条
（P1 6 + P2 5）；终止 6 个；释放 DAG 边
5 条；下游转 runnable 3 个；
健康节点 5 个、其中 0 告警 5 个。

| 节点 | 分组 | 计划(min) | 已用(min) | 预算比 | 峰值 mem/cpu/disk(%) | 结论 | 告警数 |
|---|---|---|---|---|---|---|---|
| H-1 | healthy | 60.0 | 50.0 | 0.8333 | 45.0/40.0/50.0 | healthy | 0 |
| H-2 | healthy | 120.0 | 20.0 | 0.1667 | 72.0/78.0/60.0 | healthy | 0 |
| H-3 | healthy | 90.0 | 30.0 | 0.3333 | 45.0/92.0/50.0 | healthy | 0 |
| H-4 | healthy | 60.0 | 40.0 | 0.6667 | 79.5/60.0/70.0 | healthy | 0 |
| H-5 | healthy | 20.0 | 27.0 | 1.35 | 45.0/40.0/50.0 | healthy | 0 |
| ID-1 | idle | 240.0 | 12.0 | 0.05 | 55.0/40.0/50.0 | idle_kill | 1 |
| ID-2 | idle | 120.0 | 20.0 | 0.1667 | 52.0/40.0/50.0 | idle_kill | 1 |
| ID-3 | idle | 180.0 | 25.0 | 0.1389 | 58.0/40.0/50.0 | idle_kill | 1 |
| RL-1 | leak | 200.0 | 24.0 | 0.12 | 83.0/60.0/55.0 | resource_warn | 1 |
| RL-2 | leak | 150.0 | 20.0 | 0.1333 | 50.0/45.0/86.0 | resource_warn | 1 |
| TO-1 | timeout | 60.0 | 130.0 | 2.1667 | 45.0/40.0/50.0 | timeout_kill | 2 |
| TO-2 | timeout | 30.0 | 65.0 | 2.1667 | 68.0/70.0/50.0 | timeout_kill | 2 |
| TO-3 | timeout | 10.0 | 22.0 | 2.2 | 45.0/40.0/50.0 | timeout_kill | 2 |

> 「已用」是轨迹末端的已观测时长（不是判定时刻）；各锚的**判定时刻**见下方 A1/A2/A3 明细。

**超时锚细节（A1）**：{"TO-1": {"budget_ratio": 2.1667, "elapsed_minutes": 130.0, "kill_at_min": 120.0, "planned_minutes": 60.0, "state": "timeout_kill", "terminated": true, "warn_at_min": 90.0}, "TO-2": {"budget_ratio": 2.1667, "elapsed_minutes": 65.0, "kill_at_min": 60.0, "planned_minutes": 30.0, "state": "timeout_kill", "terminated": true, "warn_at_min": 45.0}, "TO-3": {"budget_ratio": 2.2, "elapsed_minutes": 22.0, "kill_at_min": 20.0, "planned_minutes": 10.0, "state": "timeout_kill", "terminated": true, "warn_at_min": 15.0}}

**空转锚细节（A2）**：{"ID-1": {"detect_at_min": 4.5, "idle_run": 10, "state": "idle_kill", "terminated": true}, "ID-2": {"detect_at_min": 12.0, "idle_run": 10, "state": "idle_kill", "terminated": true}, "ID-3": {"detect_at_min": 15.0, "idle_run": 10, "state": "idle_kill", "terminated": true}}

**资源锚细节（A3）**：{"RL-1": {"held_minutes": 5.0, "metric": "mem_pct", "peak": 83.0, "state": "resource_warn", "warn_at_min": 15.0}, "RL-2": {"held_minutes": 5.0, "metric": "disk_pct", "peak": 86.0, "state": "resource_warn", "warn_at_min": 7.0}}

**负控细节（A4）**：误报 0/
5，误报率
0.0（<1%）。
其中 H-2 工作区全程不变但 progress 每拍 +1、H-3 CPU 92% 仅持续 2min、H-4 mem 79.5% 贴线不越线、
H-5 预算比 1.35（<1.5）——四类典型误报源都被正确压住。

**自动恢复细节（A5）**

| 边 | 前 | 后 | 原因 | 剩余依赖 |
|---|---|---|---|---|
| ID-1 → DS-A4 | waiting | released | upstream_idle_no_progress | — |
| TO-1 → DS-A1 | waiting | released | upstream_timeout_budget_exceeded | — |
| TO-1 → DS-A2 | waiting | released | upstream_timeout_budget_exceeded | H-1 |
| TO-2 → DS-A3 | waiting | released | upstream_timeout_budget_exceeded | TO-3 |
| TO-3 → DS-A3 | waiting | released | upstream_timeout_budget_exceeded | — |

未被终止触达（显式登记，证明"没动"而非"没看"）：

| 边 | 后 | 原因 |
|---|---|---|
| DS-A1 → DS-A6 | waiting | upstream_not_terminated |
| H-1 → DS-A2 | waiting | upstream_not_terminated |
| RL-1 → DS-A5 | waiting | upstream_not_terminated |

## ④ 图

* `artifacts/p250_n04/fig_health_dashboard.png`：左=各节点预算消耗（1.5×/2.0× 判据线，颜色即结论），
  右=各节点 mem/cpu/disk 峰值与 80% 阈值线。
* `artifacts/p250_n04/fig_alert_timeline.png`：左=告警时间线（按种类/严重度打点），
  右=三个空转节点的"连续无进展心跳数"曲线与 N=10 判据线（ID-3 因 progress 递增而延后到 15min）。

## ⑤ 注意点与局限

* **数据面是合成场景**：本单验收的是**判据实现**。采集适配层 `scenario_from_rows` 已就位
  （台账行 + 心跳 → 场景，列名同名、只读、幂等），但**尚未接线上库跑真实轨迹长测**，
  也**没有线上误报率样本**：A4 的 0/5 只在本合成场景成立（局限，如实登记）。
* **空转判据依赖心跳保真**：若心跳者自身死掉（而非节点空转），本模块看到的是"最后一拍之后
  没有采样"，不会误报空转（缺采样≠静止），但也**不会**报节点死亡——那是 `stall_detector`
  的 R2/R3 职责，两者互补而非替代。
* **progress 是可选字段**：不提供 progress 的节点只有工作区指纹一条证据，长算力无落盘会被判空转。
  这是有意的保守取舍（宁可误杀可疑节点也不养僵尸），接线上库应让 worker 上报迭代计数。
* **资源阈值为瞬时平台口径**：容器 cgroup 内存使用率与宿主机 load 的语义不同，接线上前需在
  采集层统一口径（当前只吃百分比数字）。
* **salvage 只动直接下游**：不做传递式释放、不自动重建被终止节点的任务单（重建属调度器职责）。
* **误报率口径**：0/5 是**本场景**的样本口径（5 个负控节点 × 平均约 51 拍），不是大样本统计
  置信区间；<1% 的推广需要有线上长跑样本才能下断言。

## ⑥ 误差判定

本单是机制类交付，判据是**离散命中数**而非连续物理量，故按命中率逐项对照：

| 判据 | 基准（场景声明） | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| A1 超时识别+终止 | 3 | 3 | 0 | 是（0%） |
| A2 空转识别 | 3 | 3 | 0 | 是（0%） |
| A3 泄漏预警 | 2 | 2 | 0 | 是（0%） |
| A4 误报 | 0/5 | 0/5 | 0 | 是（误报率 0.0%） |
| A5 释放边 | 5 | 5 | 0 | 是（0%） |
| A5 下游 runnable | 3 | 3 | 0 | 是（0%） |
| A5 仍阻塞 | 1 | 1 | 0 | 是（0%） |
| 判定时刻（8 个检测点） | 场景声明 offset | 实测一致（见 ③ 细节） | 0 min | 是（0%） |
| A6 pytest | 全绿 | 27 passed, 1 warning | — | 是 |

无物理量基准（本单无连续场量），故不套用 "vs 解析解/文献" 的误差口径；离散判据的"误差"
即命中数与基准之差，逐项为 0。诚实边界：以上全部在**合成场景**上成立，未在线上战役库验证。

## 附录：复现命令

```bash
python3 tools/campaign_health_scenario.py                 # 生成合成场景（确定性）
python3 ops/taskplane/campaign_health_monitor.py \
    --scenario tests/fixtures/campaign_health_p250_n04_scenario.json --out-dir artifacts/p250_n04
python3 -m pytest tests/test_campaign_health_monitor_p250_n04.py -q
python3 tools/campaign_health_report.py                   # 判据 + 2 图 + 本文
```
