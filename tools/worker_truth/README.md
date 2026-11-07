# tools/worker_truth — worker 占用的外部真值面（IN23-N08）

平台的「worker 忙不忙」原本只有一条自述口径：*claim 持有 ∧ 心跳新鲜*（claim-window）。本模块给
worker 占用建立**不依赖被观测者配合**的外部真值（`/proc` 进程树 + cgroup 计量 + `/proc/<pid>/io`），
派生四态 `busy / suspect_hang / silent_dead / idle`，并与 claim-window 口径做时间加权对账。

**只读纪律**：不 DDL、不 DML、不写 cgroup 控制文件、不改平台 schema。唯一副作用是工作树内
`runs/` `artifacts/` 下的 JSONL/JSON/PNG。

## 快速开始

```bash
# 1) 采样（10 s 一 tick，只读；claims 走只读 SELECT）
python3 -m tools.worker_truth.sampler --duration 3900 --interval 10 \
    --claims db --workers-from-db --out runs/in23_n08/samples.jsonl \
    --scope-out runs/in23_n08/scope.json

# 2) 静默死回放（机制回放 + 历史批次回放）
python3 -m tools.worker_truth.replay --mode both --outdir runs/in23_n08/replay

# 3) 图 + 判据 + 报告（数字全部程序化注入）
python3 -m tools.worker_truth.figures
python3 -m tools.worker_truth.report
python3 -m tools.worker_truth.publish --dry-run     # 尺寸自检，再正式发布

# 4) 单测
python3 -m pytest tests/test_worker_truth_in23.py -q
```

## 四个口径（写清楚才能被审计）

| 口径 | 定义 | 用途 |
|---|---|---|
| `busy` | pids>0 且 60 s 窗口内 CPU 增量 > ε（可选 I/O 面：syscalls 增量 > ε_io） | 外部真值「在烧机器」 |
| `suspect_hang` | claim 持有 ∧ 存活 ∧ CPU 与 I/O 全平 | 账本说忙、机器不动 |
| `silent_dead` | claim 持有 ∧ 进程树 pids=0（连续 2 样本确认） | 静默死（本节点主目标） |
| `idle` | 无 claim 且无燃烧 | 空闲/未受领 |
| claim-window busy | claim 持有 ∧ `heartbeat_age ≤ 300 s` | 平台现状口径（被审计对象） |

`online` = `busy | suspect_hang | idle`（有身体或无需认领）；`dead` = `silent_dead`。

## 关键结论（详见 `docs/IN23_N08_worker_truth.md`）

1. 静默死用**进程缺席**口径检出只需一个采样周期（机制回放实测 ~2 s），而心跳陈旧口径的下界是
   120 s——后者是「策略阈值」，前者是「观测事实」。
2. claim-window 与 CPU 口径的时间加权一致率只有 ~50%，抬底噪/拉窗口都到不了 90%：两条口径测的
   不是同一件事（等模型 vs 挂死），**这是测量设计问题，不是实现 bug**。
3. 本宿主 cgroup 是 v1 且所有 worker 共用一个 cgroup → `cpu.stat`/`memory.current`/`pids.current`
   只能做容器级旁证；per-worker cgroup 真值要求**启动时按 worker 分 cgroup**。v2 代码路径有
   fixture 单测覆盖，缺真 v2 宿主实测（证据等级 WITHHELD）。

模块结构：`probes.py`（内核探针，纯函数解析）· `states.py`（四态判定表）· `sampler.py`（只读采样器 /
JSONL / 完整性审计）· `ledger.py`（时间加权对账 + 灵敏度）· `replay.py`（机制/历史回放）·
`fake_worker.py`（回放用带 claim 文件的真进程体）· `figures.py` · `report.py` · `publish.py`。