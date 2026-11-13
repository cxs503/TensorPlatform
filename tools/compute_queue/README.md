# compute_queue —— 统一计算调度系统 v1

owner 2026-11-13 计算调度令（架构级）：「应该建计算调度系统，agent 计算提交给
调度系统，跑在哪里是调度系统的事」。本包 = cluster_queue 的三层分离升级版
（提交/裁决/执行），账本沿用 `cluster_jobs`（扩展 4 列），节点执行复用
`/root/tp-agent/cluster/cluster_exec.py`（既有资产，不重造）。

## 三层架构

```
提交面  compute_queue.py CLI + httpd.py HTTP        提交者永不说跑哪（无 --node）
裁决面  scheduler.py decide_placement（纯函数）      统一负载表 + 容量感知放置
执行面  runners.py LocalRunner / NodeRunner         local-99 硬限 + 双节点 ssh
账本    ledger.py  cluster_jobs + placement/cpu_seconds/wait_s/sched
```

### ① 提交面（双入口）
作业规格 = `{cmd 或 pytest 目标, 工作树来源(repo+ref 或 workdir), cores, mem_g, timeout}`。
`JobSpec` 结构体**没有 node 字段**——结构性保证 HPC 语义；CLI 传 `--node` 或
HTTP body 带 `node` → 400/REFUSED。

```bash
# CLI
python3 tools/compute_queue/compute_queue.py submit \
    --cmd "pytest -q" --repo TensorBEM --ref main --cores 8 --mem-g 16 --timeout 3600
python3 tools/compute_queue/compute_queue.py submit \
    --pytest "tests/test_x.py::test_y" --workdir /root/tp-agent/work/m1234
python3 tools/compute_queue/compute_queue.py submit \
    --cmd-tpl "bench --i {i} --n {n}" --shards 16 --repo TensorLBM
python3 tools/compute_queue/compute_queue.py ps --jid cj12
python3 tools/compute_queue/compute_queue.py fetch --jid cj12

# HTTP（serve --port 8099）
curl -s -X POST 127.0.0.1:8099/compute/submit -d '{"cmd":"nproc","repo":"TensorBEM","cores":32}'
curl -s 127.0.0.1:8099/compute/jobs?jid=cj12
curl -s 127.0.0.1:8099/compute/nodes
```

### ② 裁决面（容量感知放置）
每周期构建统一负载表 `{n315, n330, local-99}`（ssh 探测 + /proc 探测 + 账本在跑核数）：

| 规则 | 默认值 | 说明 |
|---|---|---|
| local 单作业上限 | 8 核 | 本机 99 只给小作业 |
| local 总帽 | 24 核 | 全部 agent 计算合计；其余 104 核保留控制面 |
| 负载准入线 | load/cores < 0.85 | 三主机同线 |
| 内存下限 | 30 GB | 请求 mem_g + 30 |
| 每主机并发 | 4 作业 | |
| 选择序 | 节点优先（最低 loadr）→ local 兜底 | 702 双节点 72% 闲置，99 曾被孤儿进程吃满 |
| 无容量 | 排队（FIFO） | HPC 常态：不失败不丢弃 |

### ③ 执行面
- **local-99**：cgroup v2（cpu.max+memory.max）→ cgroup v1（cfs_quota+memory.limit）
  → `taskset -c 0..N-1 + nice -n 19`（CPU 亲和硬限核数；内存软限，如实披露）。
  99 当前为 cgroup v1 只读挂载 → 实际走 taskset+nice 降级。
- **双节点**：`cluster_exec.py run --node <裁决结果>`（git 检出/stage_dir tar 管道）。
- **cpu_seconds**：cgroup cpu.stat（精确）→ 进程组 /proc 采样（树级，best-effort）→ NULL。
- **wait_s**：派发时刻 − 提交时刻。

### ④ 账本扩展（向后兼容 ⑤）
```sql
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS placement text;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS cpu_seconds double precision;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS wait_s double precision;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS sched text NOT NULL DEFAULT 'cluster_queue';
```
只增不改：旧 `cluster_queue.py` 读写不受影响；新作业 `sched='compute_queue'`。

## 测试（机器门，红队可失败）
```bash
python3 -m pytest tools/compute_queue/tests/ -v     # 52 项
python3 runs/compute_queue_live_smoke.py            # 活体冒烟（独立测试库 cq_live_test）
```
红队 Gate（tests/test_redteam.py + 活体冒烟）：
- Gate 1：32 核作业必须落节点不落本机（单测 + 活体：落 n330）
- Gate 2：本机帽打满（24 核）→ 新作业排队/落节点，绝不落本机
- Gate 3：控制面保留属性（local 在跑核数永 ≤ 24，网格 25×5 全过）
- Gate 4：CLI `--node` 结构性拒绝
- Gate 5：端到端派发账本字段（placement/wait_s）

## 部署 / cutover（运维步骤，需 owner 批准）
1. `ALTER` 迁移对生产库幂等（`compute_queue.py --db tensorplatform dispatch-once` 首跑自动迁移）。
2. 停旧调度器：`kill $(pgrep -f 'cluster_queue.py --sched')`。
3. 起新调度器：`setsid nohup python3 tools/compute_queue/compute_queue.py --sched &`
   （单实例守卫同旧版）。
4. 旧 555 个 queued 作业由新调度器继续 FIFO 派发（超集语义：新调度器能派旧作业）。
5. 回滚：停新调度器、重启 `sched_launch.sh`（新列闲置无害）。

## 已知局限（v1 如实披露）
- 节点侧 cpu_seconds 未记账（NULL）——v2 在节点侧 cgroup 记账。
- local 内存硬限依赖 cgroup 可写；v1 只读挂载下为软限。
- 双调度器并存期（cutover 前）旧调度器可能先派新作业（仍不落本机，红队不变量保持）。
- 节点探测 120s 超时上限；探测失败=该节点本周期不可用（下周期重试）。
