"""compute_queue —— 统一计算调度系统 v1（owner 2026-11-13 计算调度令）。

三层分离：
  提交面  compute_queue.py（CLI + HTTP 双入口；提交者永不说跑哪）
  裁决面  scheduler.py（n315/n330/local-99 统一负载表 + 容量感知放置）
  执行面  runners.py（local-99 cgroup/nice 硬限 + 双节点 ssh 复用 cluster_exec）
账本：cluster_jobs 扩展（placement / cpu_seconds / wait_s / sched）。
"""
