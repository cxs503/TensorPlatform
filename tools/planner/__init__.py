"""
Planner Agent - Decision Schema for Task Planning Layer

定位：机器门（m597 必要性三查）之上的判断层
- 机器查事实：已覆盖/机械可修/在途去重
- 规划师判意图与价值：挂靠谁/成本值不值/产出任务书

输入：候选任务 + 上下文包
输出：结构化决策 JSON
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Literal
from enum import Enum
import json
from datetime import datetime


class Verdict(str, Enum):
    """决策类型"""
    DISPATCH = "dispatch"      # 派单：有价值且未被覆盖
    REJECT = "reject"          # 拒绝：目标已被覆盖或无价值
    MECHANICAL = "mechanical"  # 机械修复：不需 LLM，脚本可修
    MERGE = "merge"            # 合并：与其他任务重复，应合并


@dataclass
class ContextPackage:
    """上下文包：决策所需的背景信息"""
    guide_tracker_kpi: dict = field(default_factory=dict)  # KPI 快照
    active_missions: list = field(default_factory=list)     # 在飞单清单
    recent_7d_success: list = field(default_factory=list)   # 近 7d 成功谱
    recent_7d_failure: list = field(default_factory=list)   # 近 7d 失败谱
    budget_pricing: dict = field(default_factory=lambda: {
        "per_100_tool_calls": 10.0,  # 每百次工具调用成本（元）
        "per_1000_tokens": 0.5       # 每千 token 成本（元）
    })


@dataclass
class CandidateTask:
    """候选任务"""
    task_id: str
    title: str
    description: str
    source: str = "manual"  # 来源：github_issue / auto_generated / manual
    created_at: str = ""    # 创建时间（可选）
    labels: list = field(default_factory=list)
    estimated_complexity: Optional[str] = None  # low/medium/high


@dataclass
class TaskSpecification:
    """任务书（当 verdict=dispatch 时）"""
    goal: str                          # 目标描述
    machine_criteria: list             # 机器判据（可程序验证）
    budget_suggestion: str             # 预算建议（calls,tokens,max_time）
    forbidden_actions: list            # 禁止项
    acceptance_criteria: list          # 验收标准
    dependencies: list = field(default_factory=list)  # 依赖的其他任务


@dataclass
class PlannerDecision:
    """规划师决策"""
    # 基础信息
    decision_id: str
    timestamp: str
    candidate: CandidateTask
    context: ContextPackage
    
    # 决策核心
    verdict: Verdict
    confidence: float  # 0.0-1.0
    
    # 挂靠目标
    target_objective: Optional[str] = None  # 指令/KPI/战役线 id
    target_rationale: Optional[str] = None  # 挂靠理由
    
    # 成本收益论证
    cost_benefit_analysis: Optional[dict] = None  # {estimated_cost, expected_value, roi}
    
    # 分支决策
    task_spec: Optional[TaskSpecification] = None  # verdict=dispatch
    reject_reason: Optional[str] = None            # verdict=reject
    mechanical_fix: Optional[str] = None           # verdict=mechanical
    merge_target: Optional[str] = None             # verdict=merge
    
    # 元数据
    reasoning_trace: str = ""  # 推理过程（用于审计）
    
    def to_dict(self) -> dict:
        """转为可序列化字典"""
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["candidate"] = asdict(self.candidate)
        d["context"] = asdict(self.context)
        if self.task_spec:
            d["task_spec"] = asdict(self.task_spec)
        return d
    
    def to_json(self, indent: int = 2) -> str:
        """转为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class DecisionRecord:
    """决策记录（用于 shadow mode 存储）"""
    decision: PlannerDecision
    machine_gate_result: Optional[dict] = None  # 机器门结果（对照）
    human_verdict: Optional[str] = None         # 人工裁决（回填）
    actual_outcome: Optional[dict] = None       # 后续结果（回填）
    credit_score: Optional[float] = None        # 信用分（回填）


# Schema 版本
SCHEMA_VERSION = "1.0.0"

# 决策置信度阈值
CONFIDENCE_THRESHOLD_DISPATCH = 0.7
CONFIDENCE_THRESHOLD_REJECT = 0.8  # 拒绝需要更高置信度
