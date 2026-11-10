"""生成器：planner/report.json —— 规划器报告。

复算逻辑：任务规划评分。
"""

OUTPUT_PATH = "artifacts/planner/report.json"


def generate() -> dict:
    """生成 planner/report.json 的数值内容。"""
    # 模拟任务规划评分
    tasks = [
        {"id": "T001", "priority": 1, "complexity": 0.8, "score": 0.95},
        {"id": "T002", "priority": 2, "complexity": 0.6, "score": 0.88},
        {"id": "T003", "priority": 3, "complexity": 0.9, "score": 0.75},
        {"id": "T004", "priority": 4, "complexity": 0.5, "score": 0.92},
        {"id": "T005", "priority": 5, "complexity": 0.7, "score": 0.85},
    ]
    
    return {
        "planner_version": "1.0.0",
        "n_tasks": len(tasks),
        "tasks": tasks,
        "summary": {
            "avg_score": sum(t["score"] for t in tasks) / len(tasks),
            "avg_complexity": sum(t["complexity"] for t in tasks) / len(tasks),
            "total_priority": sum(t["priority"] for t in tasks)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
