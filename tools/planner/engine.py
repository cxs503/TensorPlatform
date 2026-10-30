"""
Planner Agent - 决策引擎

基于规则 + 模式的决策逻辑（Phase 1）
后续可接入 LLM 做更复杂的意图理解
"""

from typing import Optional
from datetime import datetime
import uuid
import re

from . import (
    PlannerDecision, CandidateTask, ContextPackage, TaskSpecification,
    Verdict, DecisionRecord, CONFIDENCE_THRESHOLD_DISPATCH, CONFIDENCE_THRESHOLD_REJECT
)


# 僵尸单模式（来自 2026-10-31 全案标注）
ZOMBIE_PATTERNS = [
    r"openhands",           # openhands 僵尸返工
    r"返工.*已被覆盖",
    r"重复.*报告",
    r"MD5.*typo",
]

# 机械修复模式（不需 LLM）
MECHANICAL_PATTERNS = [
    r"报告.*\d+/\d+/\d+.*全同重复",  # 报告 34/35/36 三份全同重复
    r"MD5.*typo",                     # MD5 typo
    r"typo.*fix",
    r"format.*correction",
]

# 好单模式（EX-1 m541-550 系）
GOOD_TASK_PATTERNS = [
    r"EX-\d+",
    r"m5[4-5]\d",
]

# 高风险模式（m533 教训：判据必须配复算）
HIGH_RISK_PATTERNS = [
    r"m533",
    r"任务书.*判据",
    r"交付.*造假",
]


class PlannerEngine:
    """规划师决策引擎"""
    
    def __init__(self, context: Optional[ContextPackage] = None):
        self.context = context or ContextPackage()
        self.decision_history: list[DecisionRecord] = []
    
    def decide(self, candidate: CandidateTask) -> PlannerDecision:
        """
        对候选任务做决策
        
        决策流程：
        1. 模式匹配（快速路径）
        2. 上下文分析（在飞单去重、KPI 挂靠）
        3. 成本收益评估
        4. 生成任务书或拒绝理由
        """
        decision_id = f"pd-{uuid.uuid4().hex[:8]}"
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # Step 1: 模式匹配
        verdict, confidence, reason = self._pattern_match(candidate)
        
        # Step 2: 上下文增强
        if verdict is None:
            verdict, confidence, reason = self._context_analysis(candidate)
        
        # Step 3: 构建决策
        decision = PlannerDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            candidate=candidate,
            context=self.context,
            verdict=verdict,
            confidence=confidence,
            reasoning_trace=reason
        )
        
        # Step 4: 根据 verdict 填充分支字段
        if verdict == Verdict.DISPATCH:
            decision.task_spec = self._build_task_spec(candidate)
            decision.cost_benefit_analysis = self._estimate_cost_benefit(candidate)
            decision.target_objective = self._find_target_objective(candidate)
        elif verdict == Verdict.REJECT:
            decision.reject_reason = reason
        elif verdict == Verdict.MECHANICAL:
            decision.mechanical_fix = self._suggest_mechanical_fix(candidate)
        elif verdict == Verdict.MERGE:
            decision.merge_target = self._find_merge_target(candidate)
        
        # 记录决策
        record = DecisionRecord(decision=decision)
        self.decision_history.append(record)
        
        return decision
    
    def _pattern_match(self, candidate: CandidateTask) -> tuple[Optional[Verdict], float, str]:
        """模式匹配快速路径"""
        text = f"{candidate.title} {candidate.description}".lower()
        
        # 检查僵尸单模式
        for pattern in ZOMBIE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return Verdict.REJECT, 0.95, f"匹配僵尸单模式: {pattern}"
        
        # 检查机械修复模式
        for pattern in MECHANICAL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return Verdict.MECHANICAL, 0.90, f"匹配机械修复模式: {pattern}"
        
        # 检查好单模式（高置信度派单）
        for pattern in GOOD_TASK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return Verdict.DISPATCH, 0.85, f"匹配好单模式: {pattern}"
        
        return None, 0.0, ""
    
    def _context_analysis(self, candidate: CandidateTask) -> tuple[Verdict, float, str]:
        """上下文分析"""
        # 检查在飞单去重
        for mission in self.context.active_missions:
            if self._is_duplicate(candidate, mission):
                return Verdict.MERGE, 0.80, f"与在飞单 {mission.get('id')} 重复"
        
        # 检查近期失败谱（避免重复失败）
        for failure in self.context.recent_7d_failure:
            if self._is_similar(candidate, failure):
                return Verdict.REJECT, 0.75, f"与近期失败单 {failure.get('id')} 相似，风险高"
        
        # 默认：根据复杂度判断
        complexity = candidate.estimated_complexity or "medium"
        if complexity == "low":
            return Verdict.DISPATCH, 0.65, "低复杂度任务，默认派单"
        elif complexity == "high":
            return Verdict.DISPATCH, 0.55, "高复杂度任务，需人工复核"
        else:
            return Verdict.DISPATCH, 0.60, "中等复杂度，默认派单"
    
    def _is_duplicate(self, candidate: CandidateTask, mission: dict) -> bool:
        """判断是否重复"""
        # 简单关键词匹配
        cand_words = set(candidate.title.lower().split())
        miss_words = set(mission.get("title", "").lower().split())
        overlap = len(cand_words & miss_words)
        return overlap > min(3, len(cand_words) * 0.5)
    
    def _is_similar(self, candidate: CandidateTask, failure: dict) -> bool:
        """判断是否与失败单相似"""
        cand_words = set(candidate.description.lower().split())
        fail_words = set(failure.get("description", "").lower().split())
        overlap = len(cand_words & fail_words)
        return overlap > min(5, len(cand_words) * 0.3)
    
    def _build_task_spec(self, candidate: CandidateTask) -> TaskSpecification:
        """构建任务书"""
        return TaskSpecification(
            goal=candidate.description,
            machine_criteria=[
                "代码变更可 diff 验证",
                "测试通过率 100%",
                "无新增 lint 错误"
            ],
            budget_suggestion="160,14400,6000000",  # calls,tokens,max_time
            forbidden_actions=[
                "禁止修改 .git 配置",
                "禁止 force push",
                "禁止修改热区文件（见约束）"
            ],
            acceptance_criteria=[
                "所有测试通过",
                "代码审查通过",
                "文档更新完整"
            ]
        )
    
    def _estimate_cost_benefit(self, candidate: CandidateTask) -> dict:
        """估算成本收益"""
        complexity = candidate.estimated_complexity or "medium"
        
        cost_map = {
            "low": {"calls": 50, "tokens": 5000, "time_hours": 1},
            "medium": {"calls": 160, "tokens": 14400, "time_hours": 4},
            "high": {"calls": 400, "tokens": 40000, "time_hours": 12},
        }
        
        cost = cost_map.get(complexity, cost_map["medium"])
        pricing = self.context.budget_pricing
        
        estimated_cost = (
            cost["calls"] / 100 * pricing["per_100_tool_calls"] +
            cost["tokens"] / 1000 * pricing["per_1000_tokens"]
        )
        
        # 价值估算（简化：基于复杂度）
        value_map = {"low": 50, "medium": 150, "high": 400}
        expected_value = value_map.get(complexity, 150)
        
        roi = expected_value / max(estimated_cost, 0.01)
        
        return {
            "estimated_cost": round(estimated_cost, 2),
            "expected_value": expected_value,
            "roi": round(roi, 2),
            "breakdown": cost
        }
    
    def _find_target_objective(self, candidate: CandidateTask) -> Optional[str]:
        """查找挂靠目标（指令/KPI/战役线）"""
        # 简化实现：基于标签匹配
        for label in candidate.labels:
            if label.startswith("kpi-"):
                return label
            if label.startswith("campaign-"):
                return label
        return "general-improvement"
    
    def _suggest_mechanical_fix(self, candidate: CandidateTask) -> str:
        """建议机械修复方案"""
        text = f"{candidate.title} {candidate.description}".lower()
        
        if "报告" in text and "重复" in text:
            return "脚本去重：检测相同 MD5 的报告文件，保留最早版本，删除重复"
        if "md5" in text and "typo" in text:
            return "sed 批量替换：修正 MD5 拼写错误"
        if "typo" in text:
            return "拼写检查工具自动修正"
        
        return "需人工定义机械修复脚本"
    
    def _find_merge_target(self, candidate: CandidateTask) -> Optional[str]:
        """查找合并目标"""
        for mission in self.context.active_missions:
            if self._is_duplicate(candidate, mission):
                return mission.get("id")
        return None
    
    def get_decision_history(self) -> list[dict]:
        """获取决策历史"""
        return [
            {
                "decision_id": r.decision.decision_id,
                "timestamp": r.decision.timestamp,
                "verdict": r.decision.verdict.value,
                "confidence": r.decision.confidence,
                "candidate_id": r.decision.candidate.task_id,
                "machine_gate": r.machine_gate_result,
                "human_verdict": r.human_verdict,
                "actual_outcome": r.actual_outcome,
                "credit_score": r.credit_score
            }
            for r in self.decision_history
        ]
