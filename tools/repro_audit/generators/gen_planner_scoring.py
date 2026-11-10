"""生成器：planner/scoring_report.json —— 规划器评分报告。

复算逻辑：多准则决策分析。
"""

OUTPUT_PATH = "artifacts/planner/scoring_report.json"


def generate() -> dict:
    """生成 planner/scoring_report.json 的数值内容。"""
    # 模拟多准则评分
    criteria = ["cost", "time", "quality", "risk"]
    alternatives = [
        {"id": "A1", "scores": {"cost": 0.8, "time": 0.7, "quality": 0.9, "risk": 0.6}},
        {"id": "A2", "scores": {"cost": 0.6, "time": 0.9, "quality": 0.7, "risk": 0.8}},
        {"id": "A3", "scores": {"cost": 0.9, "time": 0.6, "quality": 0.8, "risk": 0.7}},
    ]
    
    # 权重
    weights = {"cost": 0.3, "time": 0.25, "quality": 0.3, "risk": 0.15}
    
    # 计算加权总分
    for alt in alternatives:
        total = sum(alt["scores"][c] * weights[c] for c in criteria)
        alt["weighted_score"] = total
    
    # 排序
    alternatives.sort(key=lambda x: x["weighted_score"], reverse=True)
    
    return {
        "method": "weighted_sum",
        "criteria": criteria,
        "weights": weights,
        "alternatives": alternatives,
        "best_alternative": alternatives[0]["id"],
        "summary": {
            "n_alternatives": len(alternatives),
            "n_criteria": len(criteria),
            "score_range": [alternatives[-1]["weighted_score"], alternatives[0]["weighted_score"]]
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
