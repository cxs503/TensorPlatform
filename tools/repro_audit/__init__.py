"""repro_audit —— 历史验收声明的抽样式重跑复证（reproducibility metrology）。

纯函数层在 :mod:`tools.repro_audit.core`，编排在 :mod:`tools.repro_audit.cli`，
KPI 落库在 :mod:`tools.repro_audit.persist`。

扩展模块（matured mission deliverables）：
  * :mod:`canonical` —— 任务交付物规范化表示
  * :mod:`scan_select` —— 分层扫描 + 多策略选择
  * :mod:`probe_runner` —— 轻量级探针执行器
  * :mod:`run_audit` —— 完整审计执行器
  * :mod:`make_report` —— 审计报告生成器
  * :mod:`launch` —— 高层启动入口
  * :mod:`shims` —— 兼容性垫片

设计约束（与平台铁律对齐）：
  * **零领域语义**：本包不认识任何学科词/船型/仓名；仓、学科、判据全部来自
    调用方传入的节点 JSON（``metadata.repo`` / ``acceptance_criteria``）。
  * **只读**：仅在临时目录里 ``git archive`` 出干净检出并执行，不写任何被审计仓。
  * **幂等**：输出 JSON ``sort_keys=True``，抽样用 (seed, node_id) 的 sha256 定序，
    不含 wall-clock 派生的排序键；KPI 用小时桶 upsert。
"""
from .core import (  # noqa: F401
    DIAG_ABS_TOL,
    DIAG_NEAR_ZERO,
    OBS_REL_TOL,
    classify_value,
    flatten_numbers,
    parse_tolerance,
    sample_stratified,
    verdict_of,
)
