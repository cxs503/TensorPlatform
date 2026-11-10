"""生成器：backfill_demo.json —— 回填演示。

复算逻辑：回填统计。
"""

OUTPUT_PATH = "artifacts/backfill_demo.json"


def generate() -> dict:
    """生成 backfill_demo.json 的数值内容。"""
    # 模拟回填统计
    stats = {
        "total_records": 1000,
        "backfilled_records": 850,
        "pending_records": 150,
        "success_rate": 0.85,
        "by_category": {
            "A": 300,
            "B": 250,
            "C": 200,
            "D": 100
        }
    }
    
    return {
        "backfill_id": "BF_DEMO",
        "stats": stats,
        "summary": {
            "completion_rate": stats["backfilled_records"] / stats["total_records"],
            "remaining": stats["pending_records"],
            "largest_category": max(stats["by_category"].items(), key=lambda x: x[1])[0]
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
