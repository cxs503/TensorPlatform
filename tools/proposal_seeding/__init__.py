"""提案聚合播种管线（proposal aggregation → arbitration → campaign DAG seeding）。

模块分工（每层都可脱库/脱网单测，零领域语义）：

* :mod:`tools.proposal_seeding.config`    —— 唯一参数来源（``config/proposal_seeding.json``）；
  阈值 / 仓白名单 / 成熟化标记全部外置，代码内无字面量阈值、无域词。
* :mod:`tools.proposal_seeding.model`     —— 提案 / 聚合项 / 计划三层的规范模型，
  异构 ``goal``（字符串 / 字典 / 列表）显式归一；内容寻址摘要保证幂等。
* :mod:`tools.proposal_seeding.aggregate` —— 多提案者读入 + 3-gram Jaccard 语义去重 +
  合并吸收各家可取细节（段/判据/依赖/仓冲突全部留证）。
* :mod:`tools.proposal_seeding.seed`      —— 聚合项 → campaign_plans/milestones/nodes，
  ``ext_id`` 内容定址、里程碑分配、DAG 依赖解析（构造性无环）。
* :mod:`tools.proposal_seeding.gate`      —— 机器门硬校验（节点数 / ext_id 唯一 / 仓白名单 /
  DAG 无环 / 依赖闭合 / 里程碑覆盖 / 成熟化单数）。
* :mod:`tools.proposal_seeding.autoregress` —— 战役 DAG 自回归增长：从管线自身遥测合成
  平台自优化提案，保证每一波必含平台节点，溢出项反哺下一波（背压信号）。
* :mod:`tools.proposal_seeding.match`     —— 人工结果 ↔ 机器结果的四通道匹配（全文字符 3-gram /
  仅中文 3-gram / ASCII 词集合 / 标题前缀）+ 平台角色通道，用于可复现性审计。
* :mod:`tools.proposal_seeding.cli` / ``m8405_pipeline`` / ``m8405_acceptance`` /
  ``m8405_report`` —— 命令行、真实数据复现、验收自检、报告生成（数字全部程序化注入）。

设计纪律
--------
1. **零领域语义**：本包只认识 "proposal / item / node / milestone / repo / criteria" 这些
   平台治理词汇；物理域词、域判据、域算例一律经输入工件注入。
2. **幂等**：所有 id 都是内容摘要或按内容排序后的序号，同一输入两次运行产生逐字节相同的
   ``ext_id`` 序列（``replay`` 命令给出证据）。
3. **fail-closed**：缺字段的提案不静默丢弃，计入 ``rejected`` 并给出原因；机器门任何一项
   不过即 ``all_passed=False``。
"""

from __future__ import annotations

from .aggregate import aggregate, load_proposals, pairwise_similarity
from .autoregress import advance_wave, ensure_platform_node, pipeline_telemetry, platform_proposal
from .config import SeedingConfig, load_config
from .gate import check_plan
from .match import best_match, is_platform_role, match_channels
from .model import AggregatedItem, PlanNode, Proposal, Quote, canonical_json, digest
from .seed import seed_plan, slugify

__all__ = [
    "SeedingConfig",
    "load_config",
    "Proposal",
    "AggregatedItem",
    "PlanNode",
    "Quote",
    "canonical_json",
    "digest",
    "load_proposals",
    "pairwise_similarity",
    "aggregate",
    "seed_plan",
    "slugify",
    "check_plan",
    "match_channels",
    "best_match",
    "is_platform_role",
    "pipeline_telemetry",
    "platform_proposal",
    "ensure_platform_node",
    "advance_wave",
]