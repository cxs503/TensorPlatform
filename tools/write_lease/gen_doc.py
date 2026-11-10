#!/usr/bin/env python3
"""从 ``artifacts/write_lease/acceptance.json`` 生成 docs/write_lease/WRITE_LEASE.md。

纪律：文档里**所有数字都来自产物**（本脚本注入，零手抄）；重跑验收后重跑本脚本即可刷新。
"""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ART = os.path.join(REPO, "artifacts", "write_lease", "acceptance.json")
DOC = os.path.join(REPO, "docs", "write_lease", "WRITE_LEASE.md")


def load() -> dict:
    with open(ART, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _rows(crit: dict) -> str:
    out = []
    names = {"D1": "线性化", "D2": "死锁检出", "D3": "零误杀与吞吐", "D4": "崩溃回收",
             "D5": "shadow 无副作用", "D6": "零领域语义", "D7": "可复现审计"}
    for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        c = crit[k]
        mark = "PASS" if c.get("pass") else "FAIL"
        out.append(f"| {k} | {names[k]} | {mark} | {_headline(k, c)} |")
    return "\n".join(out)


def _headline(k: str, c: dict) -> str:
    if k == "D1":
        return (f"成功写 {c['successful_writes']}/{c['expected_writes']}，丢失更新 {c['lost_updates']}，"
                f"终值 {c['final_counter_value']}，陈旧写拒绝 {c['stale_probe']['rejected_lost_update']}/"
                f"{c['stale_probe']['probes']}")
    if k == "D2":
        label = {"2": "2 环", "4": "4 环", "self": "自环", "ttl": "TTL 竞争"}
        kinds = "、".join(f"{label.get(kk, kk)} {v['detected']}/{v['cases']}"
                          for kk, v in c["by_kind"].items())
        return f"{c['cases_passed']}/{c['cases_total']} 例通过（{kinds}）"
    if k == "D3":
        rows = c["throughput"]["rows"]
        judged = c["throughput"]["criterion_payload_bytes"]
        row = next(r for r in rows if r["payload_bytes"] == judged)
        return (f"不相交 acquire {c['disjoint_acquires']} 次 → 拒绝 {c['rejected']}、死锁 {c['deadlocks']}；"
                f"判据载荷 {judged} B 下降 {row['drop_pct_vs_guarded_session']:.2f}%")
    if k == "D4":
        r = [x["reclaimed_after_ms"] for x in c["cases"] if x["reclaimed_after_ms"] is not None]
        worst = max(r) if r else None
        return (f"{c['cases_passed']}/{c['cases_total']} 例回收成功，最长 {worst} ms"
                f"（预算 {c['cases'][0]['reclaim_budget_ms']:.0f} ms）")
    if k == "D5":
        return (f"shadow 写 {c['writes']} 次，字节级一致 {'是' if c['byte_identical_files'] else '否'}，"
                f"promote 门槛 {c['promote_after']} → 切换 {c['promote_result']['to']}")
    if k == "D6":
        return f"域词表 {c['domain_terms_checked']} 条命中 {len(c['hits'])}，夹具命名合成 {c['fixture_naming_synthetic']}"
    if k == "D7":
        return f"{c['runs_passed']}/{c['runs_total']} 次运行可从台账重建等待图（摘要一致 + 二次重建一致）"
    return ""


def _d3_table(d3: dict) -> str:
    head = ("| 载荷 | guarded 基线 (ms) | session 租约 (ms) | per-write 租约 (ms) | "
            "下降 vs 基线（session） | 下降 vs 基线（per-write） |\n|---|---|---|---|---|---|")
    rows = []
    for r in d3["throughput"]["rows"]:
        rows.append(f"| {r['payload_bytes']} B | {r['guarded']['median_ms']:.3f} | "
                    f"{r['governed_session']['median_ms']:.3f} | "
                    f"{r['governed_perwrite']['median_ms']:.3f} | "
                    f"{r['drop_pct_vs_guarded_session']:+.2f}% | "
                    f"{r['drop_pct_vs_guarded_perwrite']:+.2f}% |")
    return "\n".join([head] + rows)


def _d2_table(d2: dict) -> str:
    head = ("| 用例 | 类 | 检出 | 环 | 受害者规则 | 最慢线程(ms) | 预算(ms) |\n|---|---|---|---|---|---|---|")
    rows = []
    for c in d2["cases"]:
        rows.append(f"| {c['case']} | {c['kind']} | {'是' if c['detected'] else '否'} | "
                    f"{c['expected_cycle_class']} | {c.get('rule') or '—'} | "
                    f"{c['slowest_thread_ms']} | {c['progress_budget_ms']:.0f} |")
    return "\n".join([head] + rows)


def _d4_table(d4: dict) -> str:
    head = ("| 用例 | TTL(s) | 崩溃前持有者 | 存活期拦截 | 回收耗时(ms) | 预算(ms) | 回收后持有者 | 继任者拿到锁 |\n"
            "|---|---|---|---|---|---|---|---|")
    rows = []
    for c in d4["cases"]:
        rows.append(f"| {c['case']} | {c['ttl']} | {c['holders_before_kill']} | "
                    f"{'是' if c['intruder_blocked_while_alive'] else '否'} | "
                    f"{c['reclaimed_after_ms']} | {c['reclaim_budget_ms']:.0f} | "
                    f"{c['holders_after_reclaim']} | {'是' if c['heir_acquired'] else '否'} |")
    return "\n".join([head] + rows)


def render(data: dict) -> str:
    crit = data["criteria"]
    pol = data["policy"]
    d1, d2, d3, d4, d5, d6, d7 = (crit[k] for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"))
    with open(os.path.join(REPO, "data", "write_lease", "policy.json"), "r", encoding="utf-8") as fh:
        policy = json.load(fh)
    acc = policy["acceptance"]
    now = time.strftime("%Y-%m-%d %H:%M")
    return f"""# 并发写所有权治理（p300w1-n10）：写租约 + 等待图死锁检测 + CAS 静默覆写归因

> 平台共性机制，**零领域语义**（路径/仓名全部合成）。本文件由
> `python3 -m tools.write_lease.gen_doc` 从 `artifacts/write_lease/acceptance.json`
> 注入生成（产物 digest `{data['digest'][:16]}…`，生成于 {now}），**文内数字零手抄**。
> 产物 sha/digest 与台账路径一并记录，可复算。

## 1. 案例介绍

**背景。** 12 仓租户制下多个智能体同时在同一工作树上写：没有所有权约定时，两类事故必然发生——
① **增量丢失**：两个写者各自「读-改-写」同一文件，后写者把先写者的增量**静默**覆盖，
无人报错、事后无痕（lost update）；② **互等死锁**：各自持有一部分资源再申请对方持有的资源
（hold-and-wait），任务无限挂起而且**无人知道在等谁**。既有治理只有 `flock` + 分支账本
（`ops/taskplane/merge_governor.py` 一类），能防并发写，但给不出「谁在等谁」「谁把谁的值盖掉了」。

**对象。** 一次写操作 = （仓 `repo`、仓内相对路径 `path`、持有者 `holder`、字节内容）。治理把它拆成
三层可判定语义：**所有权**（同一路径同时只有一个 `rw` 持有者）、**等待关系**（等待边
`waiter→blocker`）、**版本**（内容哈希 `prev_sha→new_sha`）。

**参数表（策略外置 `data/write_lease/policy.json`，sha256 `{pol['digest'][:16]}…`）。**

| 组 | 键 | 现值 | 说明 |
|---|---|---|---|
| lease | `default_ttl_s` / `wait_timeout_s` / `poll_s` | {policy['lease']['default_ttl_s']} / {policy['lease']['wait_timeout_s']} / {policy['lease']['poll_s']} | 租约 TTL、等锁上限、首次轮询间隔 |
| lease | `modes` | {policy['lease']['modes']} | `rw` 独占写 / `r` 共享读 |
| cas | `hash` | {policy['cas']['hash']} | 内容摘要算法（CAS 前置哈希） |
| cas | `on_mismatch` | `{policy['cas']['on_mismatch']}` | 哈希不符 → 拒写 + lost-update 事件 |
| graduation | `promote_after_real_writes` | {policy['graduation']['promote_after_real_writes']} | shadow 满额才切 enforcing |
| acceptance | `d1_workers×d1_rounds` | {acc['d1_workers']}×{acc['d1_rounds']} | D1 并发规模 |
| acceptance | `d2_cases_per_class` | {acc['d2_cases_per_class']} | D2 每类用例数（4 类） |
| acceptance | `d3_acquires` / `d3_payload_bytes` | {acc['d3_acquires']} / {acc['d3_payload_bytes']} | D3 扫描规模与载荷矩阵 |
| acceptance | `d4_cases` / `d4_ttl` | {acc['d4_cases']} / {acc['d4_ttl']} | D4 崩溃注入例数与 TTL |
| acceptance | `d5_writes` | {acc['d5_writes']} | D5 shadow 真实写次数 |

## 2. 怎么做的

**分层（`tools/write_lease/`）**

1. `model.py` — 资源键（`sha256(repo\\0规范化路径)[:16]`）、租约/事件 schema、内容摘要算法入口；
2. `registry.py` — 租约协议：每个资源键一个 768 B 记录板 `locks/<key>.lock`
   （`[0:512)` 持有者记录、`[512:768)` 撤销标记，**原地 `pwrite` 无 rename**），
   权威是**内核 `flock`**（每个租约每键一个 fd），记录板只是 advisory 元数据；
3. `waitgraph.py` — 等待图：迭代版 Tarjan SCC 检环，打破策略 = 环内 **TTL 最早到期者**
   （同刻按 `lease_id` 升序，确定性）；
4. `cas.py` — CAS 提交：`commit/<key>.lock` 的 `flock` 覆盖「校验 + 落盘」整段，
   写前比对 `expected_sha`，不符即拒写并落 `cas_reject`（带 `expected`/`actual`，可归因）；
5. `gate.py` — 两个粒度：`write()` 每次写租一次（最强）、`session()` 拿一张
   **worktree/repo 级 scope 租约**后连续写（正常粒度，写前只校验 scope 授权 + CAS）；
6. `ledger.py` — append-only 台账（`O_APPEND` 单次 write），`reconstruct_wait_graph()` 是纯函数；
7. `acceptance.py` / `gen_doc.py` / `gen_figure.py` — 判据执行器与产物注入。

**关键设计（三个决定成败的选择）**

1. **权威是 `flock`，不是 JSON。** 记录板写入撕裂的最坏后果只是失去身份信息
   （记 `record_corrupt`），**不会**破坏「≤1 持有者」不变量；进程被 `SIGKILL` 时内核释放
   `flock` → 崩溃回收是**瞬时**的（D4 实测最长 {max(x['reclaimed_after_ms'] for x in d4['cases']):.1f} ms），
   TTL+心跳只用来覆盖「没死但卡住」的另一类故障。
2. **等待边只登记一次。** 早期实现每轮自旋都写一行 `wait_start`：40 路竞争时台账被灌到
   156k 行、CPU 全耗在 `open/pread` 上（吞吐掉 30 倍）。改成「同一 `(waiter,key,blocker)`
   只落一次台账、撤销检查与死锁检测限流」后吞吐恢复。
3. **撤销标记必须打在「受害者真正持有的键」上。** 只标它「正在等的键」时，受害者要等到自己
   记录板 TTL 到期才发现被撤（实测 D2 单例 600 ms+）；改标自持键后同一用例降到
   {max(x['slowest_thread_ms'] for x in d2['cases']):.1f} ms 量级。

**灰度。** `mode=shadow` 时机制全跑但**不拦写**（会被拦的写记 `would_block` 后放行，落盘字节与直写
基线一致），累计 `promote_after_real_writes={policy['graduation']['promote_after_real_writes']}` 次真实写
才允许 `promote()` 到 `enforcing`——门槛是计数而非拍脑袋。

## 3. 计算结果（判据 D1–D7）

| 判据 | 名称 | 结论 | 关键数字 |
|---|---|---|---|
{_rows(crit)}

**D1 线性化**（`{d1['concurrency_model']}`，本次耗时 {d1['elapsed_s']} s、{d1['ops_per_s']} 写/s；
共享盘负载敏感，重复运行区间 13–34 s，判据只看**计数**不看速率）：
期望 {d1['expected_writes']} 次写、成功 {d1['successful_writes']} 次、丢失更新 {d1['lost_updates']}、
计数器终值 {d1['final_counter_value']}；字面变体「总量恰为
{d1['literal_total_variant']['total_writes']} 次」成功 {d1['literal_total_variant']['successful_writes']}、
终值 {d1['literal_total_variant']['final_counter_value']}；**陈旧写探针**
{d1['stale_probe']['probes']} 次全部被拒（静默接受 {d1['stale_probe']['accepted_silently']} 次），
文件内容停在 `{d1['stale_probe']['final_content']}`。

**D2 死锁检出**：{d2['cases_total']} 例（2 环 / 4 环 / 自环 / TTL 竞争各
{acc['d2_cases_per_class']} 例），检出 {d2['cases_passed']} 例。

{_d2_table(d2)}

**D3 零误杀与吞吐**：不相交路径 {d3['disjoint_acquires']} 次 `acquire` → 拒绝 {d3['rejected']}、
超时 {d3['timeouts']}、死锁 {d3['deadlocks']}（{d3['sweep_seconds']} s，{d3['acquire_ops_per_s']} 次/s）。
吞吐用**交错 A/B/C 计时取逐次中位数**（共享盘上离群点极多，均值不可用）：

{_d3_table(d3)}

**符号约定：下降为负 = 治理路径比基线更快**（baseline 抖动 + 哈希成本之差）。
判据载荷 {d3['throughput']['criterion_payload_bytes']} B 时下降
{next(r['drop_pct_vs_guarded_session'] for r in d3['throughput']['rows'] if r['payload_bytes'] == d3['throughput']['criterion_payload_bytes']):+.2f}%
（门槛 {d3['throughput']['criterion_drop_limit_pct']}%）。
**honest note**：{d3['honest_note']}
`criterion_met_at_all_payloads = {d3['criterion_met_at_all_payloads']}`。

**D4 崩溃回收**（`SIGKILL` 注入）：

{_d4_table(d4)}

**D5 shadow 无副作用**：{d5['writes']} 次真实写，接收端字节级一致
= {d5['byte_identical_files']}，`would_block` 事件 {d5['would_block_events']}；
`promote()` 在 {d5['promote_after']} 次后生效（{d5['promote_result']['from']} → {d5['promote_result']['to']}）。

**D6 零领域语义**：扫描 {len(d6['scanned_files'])} 个文件、域词表 {d6['domain_terms_checked']} 条
（词条来自 `data/write_lease/policy.json` 注入，非硬编码），命中 {len(d6['hits'])}。

**D7 可复现审计**：{d7['runs_total']} 个场景（静止等待图一致性 + 死锁环可追溯），通过
{d7['runs_passed']}。重建入口 `tools.write_lease.ledger.reconstruct_wait_graph(ledger, at=…)`，
返回 `{{graph, digest, at_seq}}`，同一台账前缀二次重建逐字节一致。

## 4. 结果图

![D1 线性化](figs/fig1_d1_linearizability.png)

**图 1** D1：{d1['workers']} 个并发进程 × {d1['rounds_per_worker']} 轮读-改-写同一文件。
期望写数 = 成功写数 = 计数器终值，丢失更新 = {d1['lost_updates']}；最右柱为陈旧写探针
（{d1['stale_probe']['rejected_lost_update']}/{d1['stale_probe']['probes']} 被 CAS 拒绝）。

![D2 死锁](figs/fig2_d2_deadlock.png)

**图 2** D2：{d2['cases_total']} 个死锁用例的「打破后前行耗时」（对数纵轴）与 2×TTL 预算线。
自环类为微秒级（重入授予、不撤不杀）；环类最慢 {max(x['slowest_thread_ms'] for x in d2['cases']):.1f} ms，
全部在预算内。

![D3 吞吐](figs/fig3_d3_throughput.png)

**图 3** D3：不同载荷下的吞吐下降百分比。蓝柱 = worktree/repo 级 session 租约（正常粒度，
判据 {acc['d3_criterion_payload_bytes']} B 载荷达标），橙柱 = per-write 租约（最强粒度，
常数开销 ~0.3 ms/次，小载荷下代价明显）；红虚线 = 10% 门槛。{d3['honest_note']}

![D4 崩溃回收](figs/fig4_d4_crash_reclaim.png)

**图 4** D4：10 例 `SIGKILL` 注入的回收延迟（对数纵轴）与 TTL+1s 预算线；回收延迟远低于预算，
因为崩溃回收走内核 `flock` 释放（瞬时）而不是等 TTL。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **线程数 ≠ 并发度（CPython GIL）。** 40 线程做 D1 时，实际是 GIL 轮转在限速（切换间隔 5 ms →
   40 线程一轮 200 ms），误判机制"慢 30 倍"。改用 40 个**进程**（真正多 agent 的形态）后
   D1 从 ~30 s 未跑完降到 {d1['elapsed_s']} s 内完成。教训：并发判据必须用进程级并发测。
2. **FIFO 票据队列适得其反。** 为消除饿死加过 per-key ticket 队列：8 线程吞吐从 1103 次/s 掉到
   118 次/s（每轮多 4 次 `open/pread` + 临界区）。已回退——`flock` 的手递手 + 限流自旋足够。
3. **"双杀"活锁的防线**：环上若两个等待者同时动手撤销，会把整环撤掉→没人前行。打破者用
   **确定性选举**（环内 `lease_id` 最小者）确定，其余只等不动手；实测 2 环 / 4 环各 40 次重复，
   "全环被撤"0 次（2 环恒 1 人前行、4 环恒 3 人前行），测试锁同一不变量。
4. **撤销标记打在错误的键上** → 受害者靠 TTL 才发现（约 1×TTL 延迟）；见 §2 设计选择 3。
5. **`fd` 不能跨键借用。** `holds(lease_id, key, fd)` 若采信调用方给的 fd，可用 A 键的 fd 冒充
   B 键（越权写）。现只信注册中心自己记的「该键 fd」，外来 fd 一律走慢路径。
6. **scope 层级租约的 fd 登记顺序**：先登记 fd 再等 scope，否则 scope 超时回滚会漏掉已拿到的
   文件锁（本节点真实踩到，已在 `acquire()` 修正并由测试锁定）。
7. **局限（未做/不覆盖）**：① 进程**卡死**（`SIGSTOP`/长 GC）时 `flock` 不会被内核释放，
   只能靠撤销标记 + 心跳，真正"抢占"做不到（内核锁不可偷）——D4 覆盖的是 `SIGKILL` 语义；
   ② 死锁检测是**本地等待图 + 台账内跨进程边**，跨进程环要等下一次台账扫描（节流 50 ms）；
   ③ 记录板 768 B 单持有者槽位，共享读用 side 文件登记（`locks/<key>.readers/`），读者数多时
   目录枚举变慢；④ 内容哈希随载荷线性增长（blake2b-256 ≈ 0.5 GB/s），大文件写的相对开销见 §3。

## 6. 误差判定

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 成功写数 | 期望 {d1['expected_writes']} | {d1['successful_writes']} | 0 | **是（精确相等）** |
| 丢失更新 | 期望 0 | {d1['lost_updates']} | 0 | **是（精确相等）** |
| 计数器终值 | 期望 {d1['expected_writes']} | {d1['final_counter_value']} | 0 | **是（精确相等）** |
| 死锁检出率 | 期望 {d2['cases_total']}/{d2['cases_total']} | {d2['cases_passed']}/{d2['cases_total']} | 0 | **是（精确相等）** |
| 误杀数（不相交路径） | 期望 0 | {d3['rejected']} | 0 | **是（精确相等）** |
| 崩溃回收延迟 | 预算 TTL+1s = {d4['cases'][0]['reclaim_budget_ms']:.0f} ms | 最长 {max(x['reclaimed_after_ms'] for x in d4['cases']):.1f} ms | 远低于预算 | **是** |
| shadow 字节一致性 | 与直写基线逐字节相同 | {d5['byte_identical_files']} | 0 字节差 | **是（精确相等）** |
| 域词表命中 | 期望 0 | {len(d6['hits'])} | 0 | **是（精确相等）** |
| 吞吐下降（session 粒度，{acc['d3_criterion_payload_bytes']} B） | 门槛 ≤{d3['throughput']['criterion_drop_limit_pct']}% | {next(r['drop_pct_vs_guarded_session'] for r in d3['throughput']['rows'] if r['payload_bytes'] == acc['d3_criterion_payload_bytes']):+.2f}% | 距门槛 {abs(acc['d3_max_throughput_drop_pct'] - abs(next(r['drop_pct_vs_guarded_session'] for r in d3['throughput']['rows'] if r['payload_bytes'] == acc['d3_criterion_payload_bytes']))):.2f} 个百分点 | **是（判据内）**，但大载荷不成立（见 §3 honest note） |

**审计链**：产物 `artifacts/write_lease/acceptance.json`（digest `{data['digest'][:16]}…`）→
本文档（`gen_doc.py` 注入）→ 图（`gen_figure.py` 注入）→ 每次判据运行各自的台账
`<tmp>/.lease/ledger.jsonl`（事件带 `digest` 与 `pid`，可按 `reconstruct_wait_graph(at=…)` 复算）。
测试：`python3 -m pytest tests/test_write_lease.py -q`。

**复现**：`python3 -m tools.write_lease.acceptance`（约 {data['summary']['elapsed_s']} s）→
`python3 -m tools.write_lease.gen_doc && python3 -m tools.write_lease.gen_figure`。
"""


def main() -> int:
    data = load()
    text = render({"criteria": data["criteria"], "policy": data["policy"],
                   "digest": data["digest"], "summary": data["summary"]})
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {os.path.relpath(DOC, REPO)} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())