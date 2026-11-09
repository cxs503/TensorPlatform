"""repro_audit.launch —— 审计启动器（高层入口，零领域语义）。

提供一键式审计启动接口，封装完整的流水线：
  1. 加载节点数据（从 API 或快照文件）
  2. 构建审计计划
  3. 执行探针预检
  4. 执行完整复证
  5. 生成报告
  6. 持久化 KPI

用法::

    from tools.repro_audit.launch import launch_audit

    result = launch_audit(
        nodes_snapshot="artifacts/repro_audit/nodes_snapshot.json",
        n=32,
        strategy="stratified",
        output_dir="artifacts/repro_audit",
    )
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import make_report, persist, run_audit


DEFAULT_OUTPUT_DIR = "artifacts/repro_audit"


def load_nodes(source: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """加载节点数据：支持文件路径或列表。

    如果是文件路径，读取 JSON；如果是列表，直接返回。
    """
    if isinstance(source, list):
        return source
    if isinstance(source, str):
        if not os.path.exists(source):
            raise FileNotFoundError(f"nodes snapshot not found: {source}")
        with open(source, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        raise ValueError(f"unexpected nodes snapshot format: {type(data)}")
    raise TypeError(f"source must be str or list, got {type(source)}")


def launch_audit(nodes_source: str | list[dict[str, Any]],
                 n: int = 32,
                 strategy: str = "stratified",
                 seed: str = "repro-audit",
                 work_root: str | None = None,
                 output_dir: str = DEFAULT_OUTPUT_DIR,
                 run_probes: bool = True,
                 run_full: bool = True,
                 persist_kpi: bool = False,
                 run: int = 1) -> dict[str, Any]:
    """一键式审计启动。

    参数：
      - ``nodes_source``: 节点数据（文件路径或列表）
      - ``n``: 抽样数量
      - ``strategy``: 选择策略（stratified/priority/coverage/random）
      - ``seed``: 随机种子（保证幂等）
      - ``work_root``: 工作目录根（默认从环境变量 TP_WORK_ROOT）
      - ``output_dir``: 输出目录
      - ``run_probes``: 是否执行探针预检
      - ``run_full``: 是否执行完整复证
      - ``persist_kpi``: 是否持久化 KPI
      - ``run``: 运行编号（用于多次重跑）

    返回：
      ``{pipeline_result, report_path, kpi_persist_result}``
    """
    work_root = work_root or os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work")
    os.makedirs(output_dir, exist_ok=True)

    # 1. 加载节点
    nodes = load_nodes(nodes_source)

    # 2. 执行流水线
    pipeline_result = run_audit.run_audit_pipeline(
        nodes, n, strategy=strategy, seed=seed, work_root=work_root,
        run_probes=run_probes, run_full=run_full, run=run,
    )

    # 3. 保存中间结果
    checkpoint_path = os.path.join(output_dir, f"audit_run{run}.json")
    run_audit.save_checkpoint(pipeline_result, checkpoint_path)

    # 4. 生成报告
    report_path = os.path.join(output_dir, f"audit_report_run{run}.md")
    make_report.generate_and_save(pipeline_result, report_path)

    # 5. 持久化 KPI（可选）
    kpi_persist_result = None
    if persist_kpi and pipeline_result.get("audit_results"):
        summary = {"items": [{"mission_id": r.get("mission_id"), "verdict": r.get("verdict", "blocked")}
                              for r in pipeline_result["audit_results"]]}
        sql_path = os.path.join(output_dir, f"kpi_upsert_run{run}.sql")
        kpi_persist_result = persist.persist_kpi(summary, sql_out=sql_path)

    return {
        "pipeline_result": pipeline_result,
        "report_path": report_path,
        "checkpoint_path": checkpoint_path,
        "kpi_persist_result": kpi_persist_result,
    }


def launch_from_api(n: int = 32, strategy: str = "stratified", **kwargs) -> dict[str, Any]:
    """从平台 API 加载节点并启动审计。

    需要环境变量 TENSOR_AGENT_USER / TENSOR_AGENT_PASS。
    """
    from .cli import fetch_nodes_via_api

    nodes = fetch_nodes_via_api()
    return launch_audit(nodes, n=n, strategy=strategy, **kwargs)


def launch_from_snapshot(snapshot_path: str, n: int = 32, strategy: str = "stratified",
                         **kwargs) -> dict[str, Any]:
    """从快照文件加载节点并启动审计。"""
    return launch_audit(snapshot_path, n=n, strategy=strategy, **kwargs)
