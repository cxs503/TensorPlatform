"""
培养回路（Training Loop）

决策→后续结果回填→credit_ledger→playbooks 规划分册

权限阶梯：
1. advisory（纯建议）
2. shadow（并行对照跑分）
3. 低风险自动派（仅机械修+返工类）
4. 战役自动拆单

每级升权留 owner 批。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from enum import Enum


class PermissionLevel(str, Enum):
    """权限等级"""
    ADVISORY = "advisory"          # 纯建议
    SHADOW = "shadow"              # 并行对照跑分
    LOW_RISK_AUTO = "low_risk_auto"  # 低风险自动派（机械修+返工类）
    CAMPAIGN_AUTO = "campaign_auto"  # 战役自动拆单


# 权限升级条件
PERMISSION_REQUIREMENTS = {
    PermissionLevel.ADVISORY: {
        "description": "纯建议模式：规划师输出决策建议，不实际派单",
        "requirements": [],
        "owner_approval": False,  # 初始级别，无需审批
    },
    PermissionLevel.SHADOW: {
        "description": "影子模式：并行出决策，对照机器门+人工裁决",
        "requirements": {
            "min_decisions": 30,          # 至少 30 条影子决策
            "min_accuracy": 0.70,         # 准确率 ≥70%
            "min_held_out_accuracy": 0.65,  # held-out 准确率 ≥65%
        },
        "owner_approval": True,
    },
    PermissionLevel.LOW_RISK_AUTO: {
        "description": "低风险自动派：仅机械修+返工类可自动派单",
        "requirements": {
            "min_decisions": 100,
            "min_accuracy": 0.80,
            "min_held_out_accuracy": 0.75,
            "min_mechanical_accuracy": 0.90,  # 机械修类准确率 ≥90%
            "zero_false_dispatch": True,       # 零误派（dispatch 了不该派的）
        },
        "owner_approval": True,
    },
    PermissionLevel.CAMPAIGN_AUTO: {
        "description": "战役自动拆单：可自动拆解战役级任务",
        "requirements": {
            "min_decisions": 300,
            "min_accuracy": 0.85,
            "min_held_out_accuracy": 0.80,
            "min_mechanical_accuracy": 0.95,
            "zero_false_dispatch": True,
            "cost_calibration_error": 0.20,  # 成本预估误差 ≤20%
        },
        "owner_approval": True,
    },
}


class CreditLedger:
    """信用账本：记录规划师决策的信用分"""
    
    def __init__(self, store_path: str = "artifacts/planner/credit_ledger.jsonl"):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
    
    def record_decision(self, decision_id: str, verdict: str, confidence: float,
                         correct: Optional[bool] = None, outcome: Optional[dict] = None):
        """记录决策"""
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "decision_id": decision_id,
            "verdict": verdict,
            "confidence": confidence,
            "correct": correct,
            "outcome": outcome,
            "credit_delta": self._compute_credit_delta(correct, confidence),
        }
        
        with open(self.store_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def _compute_credit_delta(self, correct: Optional[bool], confidence: float) -> float:
        """计算信用变化"""
        if correct is None:
            return 0.0  # 未回填
        
        if correct:
            # 正确：置信度越高，加分越多
            return confidence * 10
        else:
            # 错误：置信度越高，扣分越多
            return -confidence * 20
    
    def get_credit_score(self) -> float:
        """获取当前信用分"""
        if not self.store_path.exists():
            return 100.0  # 初始分
        
        total = 100.0  # 初始分
        with open(self.store_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                total += entry.get("credit_delta", 0)
        
        return round(total, 2)
    
    def get_statistics(self) -> dict:
        """获取统计信息"""
        if not self.store_path.exists():
            return {"total_decisions": 0, "correct": 0, "incorrect": 0, "pending": 0}
        
        total = 0
        correct = 0
        incorrect = 0
        pending = 0
        
        with open(self.store_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                total += 1
                c = entry.get("correct")
                if c is True:
                    correct += 1
                elif c is False:
                    incorrect += 1
                else:
                    pending += 1
        
        return {
            "total_decisions": total,
            "correct": correct,
            "incorrect": incorrect,
            "pending": pending,
            "credit_score": self.get_credit_score(),
        }


class PermissionLadder:
    """权限阶梯管理"""
    
    def __init__(self, ledger: Optional[CreditLedger] = None):
        self.ledger = ledger or CreditLedger()
        self.current_level = PermissionLevel.ADVISORY
    
    def check_upgrade_eligibility(self, target_level: PermissionLevel) -> dict:
        """检查是否有资格升级"""
        requirements = PERMISSION_REQUIREMENTS[target_level]["requirements"]
        stats = self.ledger.get_statistics()
        
        checks = {}
        all_met = True
        
        # 检查决策数量
        if "min_decisions" in requirements:
            met = stats["total_decisions"] >= requirements["min_decisions"]
            checks["min_decisions"] = {
                "required": requirements["min_decisions"],
                "actual": stats["total_decisions"],
                "met": met,
            }
            all_met = all_met and met
        
        # 检查准确率
        if "min_accuracy" in requirements and stats["total_decisions"] > 0:
            accuracy = stats["correct"] / (stats["correct"] + stats["incorrect"]) if (stats["correct"] + stats["incorrect"]) > 0 else 0
            met = accuracy >= requirements["min_accuracy"]
            checks["min_accuracy"] = {
                "required": requirements["min_accuracy"],
                "actual": round(accuracy, 4),
                "met": met,
            }
            all_met = all_met and met
        
        return {
            "target_level": target_level.value,
            "eligible": all_met,
            "checks": checks,
            "owner_approval_required": PERMISSION_REQUIREMENTS[target_level]["owner_approval"],
        }
    
    def request_upgrade(self, target_level: PermissionLevel) -> dict:
        """请求升级（需 owner 审批）"""
        eligibility = self.check_upgrade_eligibility(target_level)
        
        return {
            "request": {
                "from_level": self.current_level.value,
                "to_level": target_level.value,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            "eligibility": eligibility,
            "status": "pending_owner_approval" if eligibility["eligible"] else "not_eligible",
            "message": self._generate_message(eligibility),
        }
    
    def _generate_message(self, eligibility: dict) -> str:
        """生成升级消息"""
        if not eligibility["eligible"]:
            failed = [k for k, v in eligibility["checks"].items() if not v["met"]]
            return f"不满足升级条件: {', '.join(failed)}"
        
        if eligibility["owner_approval_required"]:
            return "满足条件，等待 owner 审批"
        
        return "满足条件，可自动升级"


def generate_permission_ladder_doc() -> str:
    """生成权限阶梯文档"""
    doc = """# 规划师权限阶梯文档

## 概述

规划师权限分为 4 级，每级升权需 owner 审批。

## 权限等级

"""
    
    for level in PermissionLevel:
        info = PERMISSION_REQUIREMENTS[level]
        doc += f"### {level.value}\n\n"
        doc += f"**描述**: {info['description']}\n\n"
        
        if info["requirements"]:
            doc += "**升级条件**:\n\n"
            for key, val in info["requirements"].items():
                doc += f"- `{key}`: {val}\n"
            doc += "\n"
        
        doc += f"**需 owner 审批**: {'是' if info['owner_approval'] else '否'}\n\n"
        doc += "---\n\n"
    
    doc += """## 培养回路

1. **决策记录**：每次决策入 credit_ledger
2. **结果回填**：后续结果（succeeded/revoked/返工）回填
3. **信用评分**：基于正确性和置信度计算
4. **错判案例**：写入 playbooks 规划分册
5. **升权评估**：定期检查是否满足升级条件

## 当前状态

- 当前权限等级: advisory
- 信用分: 100.0（初始）
- 决策总数: 0
- 正确数: 0
- 错误数: 0

## 升权流程

1. 规划师达到目标等级的所有条件
2. 提交升权请求
3. Owner 审核跑分报告和典型案例
4. Owner 批准后升级

## 降级机制

- 连续 3 次误判 → 降一级
- 严重误判（如 false dispatch 导致资源浪费）→ 降两级
- 信用分 < 50 → 回到 advisory
"""
    
    return doc
