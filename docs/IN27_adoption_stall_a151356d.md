# IN27 影子诊断单 a151356df79cb336 · 器官 `adoption_stall` 越界根因与修复

**诊断对象**：`coordinator_shadow v1`（只读影子协调官，IN27-H1）在 beat 996693
（2026-11-07T10:30:47Z）读到 `oldest_queued_minutes = 8820 min ≥ 240 min`，连续 2 拍同签。
**器官 SQL（原文复算）**：`SELECT min(created_at) FROM tensor_dev_missions WHERE status = 'queued'`。
**执行者**：本单（m5322）在 TensorPlatform 工作树内定位根因、修复队列、并给出机器可裁判据。

## ① 案例介绍（物理背景=平台收养流）

「收养停滞」是**运维流**器官，不是物理场量：任务入池（`status='queued'`）后必须被
某个驻留 worker 领走（claim）。指标问的是「池子里最老的等领单有多老」。2026-11-07
10:30 的读数 8820 min ≈ 147 h ≈ 6.1 天，远超 240 min 阈值。

复算现场（`artifacts/adoption_stall_a151356d/audit_before.json`，as_of 2026-11-07 10:42:26.112763+00）：

| 量 | 值 |
| --- | --- |
| status='queued' 行数 | 26 |
| 其中 suspended_at 非空（停挂却停在 queued） | 1 |
| 其中可收养(adoptable) | 25 |
| 最老行年龄 min（器官原口径） | 8831.8 |
| 最老**可收养**年龄 min | 607.0 |
| 孤儿引脚 | glm-5.1 |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **复算**：以 `tools/queue_adoption_audit.py` 只读通道（与影子同一条 runuser→psql）
   取 `tensor_dev_missions(status='queued')` + `factory_resident_workers` + 近 7 天认领
   记录，把 147 h 拆成可归因的两块。
2. **根因定位**（两条独立缺陷叠加，缺一不可）：
   * **L1 口径缺陷（占 147 h 的全部）**：`suspended_at IS NOT NULL` 的停挂单仍停在
     `status='queued'`。这类单**按设计不参与收养**（owner 2026-11-02 控仓令停挂），
     却因为状态没有回到 `suspended`：（a）被器官当成「入池无人领」；（b）`/resume`
     要求 `status='suspended'` 而拒绝它（不可逆）；（c）claim 查询不过滤 `suspended_at`，
     native worker 可以领走冻结单——**停挂单自己写下的 `suspend_reason` 就点名了这个
     “冻结洞”**。
   * **L2 引脚缺陷（占 10 h，修复后仍会独立触发越界）**：19 张单被钉在 `model='glm-5.1'`，
     而该车道没有可工作的认领者。注册表 `factory_resident_workers` 里 `tp-gen-4` 注册
     model=`glm-5.1`、`state=idle`、心跳新鲜，但**该 model 最近一次被领到
     是 2026-11-01T04:36:13.755891+00:00**（≈6 天前），它最近领到的单 model 是 `deepseek-v4-flash`。
     真相：进程以 `--model deepseek-v4-flash` 运行，与注册 model 漂移，会话级
     claim 每次被 409 挡回后**静默退化**到公共池车道——心跳新鲜恰恰是掩盖该洞的信号，
     "存活"不等于"可认领"。
3. **修复**（都不动阈值，都可逆）：
   * L1：停挂单恢复 `status='suspended'`（保留 `suspended_at`/`suspend_reason`，`/resume` 重新可用）；
   * L2：孤儿引脚解钉 `model=NULL`，原 model 写入 `result.model_unpinned_from` 可回滚；
     每步落 `mission_lifecycle_log` 审计（`action=orphan_detect, detail.op=unpin_orphan_model`）。
4. **平台护栏（代码，pytest 锁定）**：
   * `backend/api/tensor_missions.py::claim_mission` 的 claim 查询加 `AND suspended_at IS NULL`
     ——停挂单永不被收养（关掉冻结洞）；
   * 会话配置漂移的 409 改为**机器可读**：`code=session_config_drift` + `registered_model` /
     `presented_model` + 修复建议，让"车道已死"不再表现为普通 409 被吞掉。
5. **工具链**：`backend/api/queue_adoption.py`（纯判定函数，无 I/O）+
   `tools/queue_adoption_audit.py`（体检/`--apply` 修复）+ `backend/tests/test_queue_adoption_in27.py`（11 例）。

## ③ 计算结果（程序化注入，零手抄）

| 指标 | 修复前 | 修复即刻 | 稳定后 |
| --- | --- | --- | --- |
| queued 总数 | 26 | 21 | 6 |
| 停挂(parked)条数 | 1 | 0 | 0 |
| 可收养(adoptable)条数 | 25 | 21 | 6 |
| 最老 queued 年龄 (min) · 器官原口径 | 8831.8 | 548.1 | 40.0 |
| 最老可收养年龄 (min) · 真实口径 | 607.0 | 548.1 | 40.0 |
| 越界? (>240 min) | True | True | False |
| 孤儿引脚 model 数 | 1 | 0 | 0 |

修复即刻一栏偏高是**预期**：解钉只是让单变得可收养，收养本身由 worker 在秒级完成，
所以年龄要等一轮认领才落。稳定后 19 张孤儿引脚单分别在数秒内被 `tp-impl-7 / tp-doc-4 /
tp-mod-1 / tp-mod-4 / tp-doc-1 / tp-ver-2 / tp-qw38-1 …` 领走（`adopted_sample.txt`，全为 `running`）。

![adoption_stall 修复前后](fig_adoption_stall.png)

左图：灰柱=器官原口径（含停挂单，147 h），红柱=真实可收养年龄；修复后两者同落到 0.67 h
（=10:04 那批新入池单的自然等待），远低于 240 min 阈值线。右图：队列构成，孤儿引脚 19 张清零。

## ④ 注意点（踩过的坑 / 局限 / 适用范围）

* **心跳不是可认领性**：`tp-gen-4` 心跳新鲜、`state=idle`，却 6 天没领到过它注册 model 的单。
  只按心跳算"在线"的监护面会把这个洞判成健康；判据必须是**近期认领证据**。
* **停挂语义必须落到 status**：`suspended_at` 与 `status` 双写不一致时，`/resume`、
  claim、器官指标三者会同时错，且互相掩盖。
* **局限性**：(a) `L2` 的修复选择"解钉"而非"给 glm-5.1 补一个真车道"——后者需要重启
  `tp-gen-4`（`--model glm-5.1`）或另起进程，属舰队运维，未在本单执行；若确需该 model 的
  多样性路由，运维重启后应把解钉改回（`result.model_unpinned_from` 可回滚）。
  (b) 首次 `--apply` 时 `1572` 的状态已改但审计行插入因 JSON 转义失败而回滚，
  工具已修（改为 `json.dumps` 统一转义）；该单状态变更在库中已生效并被复核。
  (c) 未做生产 DDL：审计动作复用词表内 `suspend`/`orphan_detect`，具体动作写在 `detail.op`。
  (d) 平台仓处于 owner 2026-11-02 冻结期，本单只做该缺陷的最小护栏，不扩功能面。
* **适用面**：任何"入池—收养"型流水线（campaign_nodes / paper 素材单 / 冻结停挂单）。

## ⑤ 误差判定（vs 基准，逐项）

* **器官读数复算**：诊断单 8820 min（as_of 10:30:47）↔ 本单 10:42:26 复算
  8831.8 min。两者相差 11.3 min，正是两次采样时差
  （147 h 里的一单，年龄按分钟线性增长），**相对偏差 0.13% < 3%** → 复算一致，越界为真。
* **修复效果判据**：目标 `oldest_any_minutes ≤ 240`；实测 40.0 min，
  相对阈值偏差 0.0%（在阈值内），**判定：已修复**。
* **无可比基准的量**：`lane_alive`、`drift` 是二值判据（有/无近期认领证据），不存在百分比误差；
  已用 11 例 pytest（含 4 条负控：仅停挂池、新鲜队列、活车道、阈值内引脚）锁死其行为。

## ⑥ 复现

```bash
python3 tools/queue_adoption_audit.py --json-out /tmp/adoption.json      # 只读体检
python3 tools/queue_adoption_audit.py --apply --json-out /tmp/healed.json # 可逆修复
python3 -m pytest backend/tests/test_queue_adoption_in27.py -q            # 判据回归
```
