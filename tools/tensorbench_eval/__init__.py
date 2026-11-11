"""TensorBench 评价回路（tb-eval-loop）：触发器 → 集群递交 → 结果落库 → 真增量。

平台共性机制层，**零领域语义**：基准规格（benchmark/workload/参考值/容差）一律由
工件注入（``data/tensorbench/specs/*.json``），本包只提供「递交 → 取数 → 对照 → 记账」的
通用机器。与 TensorBench 的接口只有两样：

* 规格/结果 JSON 的**结构**（TensorBench ``schemas/benchmark-result.schema.json`` v1.0.0）；
* ``tensorbench`` 本地 runner 的 argv 语义（``python -m tensorbench.cli run --spec ...``）。

四段对应任务书 ①②③④：

① :func:`store.submit_evaluation` —— 递交（集群 ``cluster_jobs`` 队列语义，不指定节点）；
② :func:`store.ingest_result` —— 结果按 result-schema 落 ``tensorbench_results``，
   误差由**平台侧**从原始字节复算（不采信调用方自报数字）；
③ :func:`store.book_bench_verified_delta` —— 真增量写 ``tensor_capability_deltas``
   ``category='bench_verified'``；
④ :func:`maturity_gate.benchmark_source.verdict` —— 无实证行 = ``claimed_not_proven``
   （红队可失败：伪造 L4 声明判不下去）。
"""
from __future__ import annotations

from . import contract, store  # noqa: F401

__all__ = ["contract", "store"]
