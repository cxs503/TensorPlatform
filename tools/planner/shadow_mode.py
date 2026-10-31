"""
影子模式（Shadow Mode）

对进自动派单队列的候选并行出决策，不实际派单。
决策入 tensor_planner_decisions（append-only 触发器同 events 纪律）。

字段：
- input_snapshot: 输入快照
- output_decision: 输出决策
- machine_gate_verdict: 机器门裁决（对照）
- human_verdict: 人工裁决（回填）
- actual_outcome: 后续结果（回填）
"""

import json
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from . import PlannerDecision, CandidateTask, ContextPackage, DecisionRecord
from .engine import PlannerEngine


# Append-only 存储路径
SHADOW_STORE = Path(os.environ.get(
    "PLANNER_SHADOW_STORE",
    "/srv/tensor/agent/work/m604/artifacts/planner/shadow_decisions.jsonl"
))


class ShadowMode:
    """影子模式运行器"""
    
    def __init__(self, engine: Optional[PlannerEngine] = None):
        self.engine = engine or PlannerEngine()
        self._ensure_store()
    
    def _ensure_store(self):
        """确保存储文件存在"""
        SHADOW_STORE.parent.mkdir(parents=True, exist_ok=True)
        if not SHADOW_STORE.exists():
            SHADOW_STORE.touch()
    
    def shadow_decide(self, candidate: CandidateTask,
                      context: Optional[ContextPackage] = None,
                      machine_gate_result: Optional[dict] = None) -> DecisionRecord:
        """
        影子决策：并行出决策但不实际派单
        
        Args:
            candidate: 候选任务
            context: 上下文包（可选）
            machine_gate_result: 机器门结果（对照）
        
        Returns:
            DecisionRecord: 决策记录
        """
        if context:
            self.engine.context = context
        
        # 执行决策
        decision = self.engine.decide(candidate)
        
        # 构建记录
        record = DecisionRecord(
            decision=decision,
            machine_gate_result=machine_gate_result,
        )
        
        # Append-only 写入
        self._append_record(record)
        
        return record
    
    def _append_record(self, record: DecisionRecord):
        """Append-only 写入决策记录"""
        entry = {
            "decision_id": record.decision.decision_id,
            "timestamp": record.decision.timestamp,
            "input_snapshot": {
                "candidate": {
                    "task_id": record.decision.candidate.task_id,
                    "title": record.decision.candidate.title,
                    "description": record.decision.candidate.description,
                    "source": record.decision.candidate.source,
                    "labels": record.decision.candidate.labels,
                },
                "context_summary": {
                    "active_missions_count": len(record.decision.context.active_missions),
                    "recent_failures_count": len(record.decision.context.recent_7d_failure),
                }
            },
            "output_decision": {
                "verdict": record.decision.verdict.value,
                "confidence": record.decision.confidence,
                "target_objective": record.decision.target_objective,
                "reasoning_trace": record.decision.reasoning_trace,
            },
            "machine_gate_result": record.machine_gate_result,
            "human_verdict": record.human_verdict,  # 回填位
            "actual_outcome": record.actual_outcome,  # 回填位
            "credit_score": record.credit_score,  # 回填位
            "integrity_hash": self._compute_hash(record),
        }
        
        with open(SHADOW_STORE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def _compute_hash(self, record: DecisionRecord) -> str:
        """计算完整性哈希（防篡改）"""
        content = json.dumps({
            "decision_id": record.decision.decision_id,
            "timestamp": record.decision.timestamp,
            "verdict": record.decision.verdict.value,
            "candidate_id": record.decision.candidate.task_id,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def backfill_human_verdict(self, decision_id: str, human_verdict: str):
        """回填人工裁决（只更新回填位，不改已有字段）"""
        # 注意：append-only 纪律下，回填通过追加新记录实现
        entry = {
            "type": "backfill",
            "decision_id": decision_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "human_verdict": human_verdict,
        }
        with open(SHADOW_STORE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def backfill_outcome(self, decision_id: str, outcome: dict):
        """回填后续结果"""
        entry = {
            "type": "backfill_outcome",
            "decision_id": decision_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "actual_outcome": outcome,
        }
        with open(SHADOW_STORE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def load_all_decisions(self) -> List[dict]:
        """加载所有决策记录"""
        if not SHADOW_STORE.exists():
            return []
        
        decisions = []
        backfills = {}
        
        with open(SHADOW_STORE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                
                if entry.get("type") == "backfill":
                    did = entry["decision_id"]
                    if did not in backfills:
                        backfills[did] = {}
                    backfills[did]["human_verdict"] = entry.get("human_verdict")
                elif entry.get("type") == "backfill_outcome":
                    did = entry["decision_id"]
                    if did not in backfills:
                        backfills[did] = {}
                    backfills[did]["actual_outcome"] = entry.get("actual_outcome")
                else:
                    decisions.append(entry)
        
        # 合并回填
        for d in decisions:
            did = d["decision_id"]
            if did in backfills:
                d.update(backfills[did])
        
        return decisions
    
    def get_decision(self, decision_id: str) -> Optional[dict]:
        """获取单个决策"""
        for d in self.load_all_decisions():
            if d["decision_id"] == decision_id:
                return d
        return None


def demo_shadow_mode():
    """
    影子模式端到端演示（1 例）
    
    机器判据：端到端演示可复现
    """
    print("=" * 60)
    print("影子模式端到端演示")
    print("=" * 60)
    
    shadow = ShadowMode()
    
    # 候选任务：一个好单范本
    candidate = CandidateTask(
        task_id="demo-001",
        title="EX-1: Suboff 阻力验证",
        description="SUBOFF 裸艇体阻力验证，对标实验数据，目标误差 <3%",
        source="manual",
        labels=["EX-1", "validation", "resistance"],
        estimated_complexity="medium"
    )
    
    # 上下文
    context = ContextPackage(
        active_missions=[
            {"id": "m597", "title": "必要性三查"},
        ],
        recent_7d_success=[
            {"id": "m541", "description": "Suboff 阻力初步验证"},
        ],
        recent_7d_failure=[],
    )
    
    # 机器门结果（对照）
    machine_gate = {
        "verdict": "pass",
        "checks": {
            "already_covered": False,
            "mechanical_fix": False,
            "in_flight_dedup": False,
        }
    }
    
    # 执行影子决策
    record = shadow.shadow_decide(candidate, context, machine_gate)
    
    print(f"\n决策 ID: {record.decision.decision_id}")
    print(f"时间戳: {record.decision.timestamp}")
    print(f"候选: {candidate.title}")
    print(f"Verdict: {record.decision.verdict.value}")
    print(f"置信度: {record.decision.confidence}")
    print(f"挂靠目标: {record.decision.target_objective}")
    print(f"推理: {record.decision.reasoning_trace}")
    
    if record.decision.task_spec:
        print(f"\n任务书:")
        print(f"  目标: {record.decision.task_spec.goal}")
        print(f"  机器判据: {record.decision.task_spec.machine_criteria}")
        print(f"  预算: {record.decision.task_spec.budget_suggestion}")
        print(f"  禁止项: {record.decision.task_spec.forbidden_actions}")
    
    if record.decision.cost_benefit_analysis:
        cba = record.decision.cost_benefit_analysis
        print(f"\n成本收益:")
        print(f"  预估成本: {cba['estimated_cost']} 元")
        print(f"  预期价值: {cba['expected_value']}")
        print(f"  ROI: {cba['roi']}")
    
    print(f"\n机器门对照: {machine_gate}")
    print(f"完整性哈希: {record.decision.decision_id}")
    
    print(f"\n存储位置: {SHADOW_STORE}")
    print(f"记录数: {len(shadow.load_all_decisions())}")
    
    print("\n" + "=" * 60)
    print("演示完成 ✓")
    print("=" * 60)
    
    return record


if __name__ == "__main__":
    demo_shadow_mode()
