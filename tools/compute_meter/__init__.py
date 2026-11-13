"""计算调度与计量（owner 2026-11-13 计算调度令）。

policy   → 放置决策（local/cluster/queue）+ 节点权重 + 优先级 + 违例判定
meter    → 账本：cpu_seconds × placement（SQL / JSONL 双实现，source_ref 幂等）
sampler  → 控制节点本机 CPU 实测（/proc 采样，可注入假 /proc 做单测）
dashboard→ 队列深度 / 放置分布 / 等待分位 / 违例数 + 机器门
panorama → 看板块幂等注入 PANORAMA.md
cli      → compute-meter 命令行（submit / run-local / ingest / report / gate / inject）
"""
__all__ = ["policy", "meter", "sampler", "dashboard", "panorama"]
