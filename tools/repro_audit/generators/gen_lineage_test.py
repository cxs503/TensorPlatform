"""生成器：lineage_test.json —— 血统测试。

复算逻辑：数据血统追踪。
"""

OUTPUT_PATH = "artifacts/lineage_test.json"


def generate() -> dict:
    """生成 lineage_test.json 的数值内容。"""
    # 模拟数据血统链
    lineage = [
        {"step": 1, "operation": "input", "source": "raw_data.csv", "records": 1000},
        {"step": 2, "operation": "filter", "condition": "quality > 0.8", "records": 850},
        {"step": 3, "operation": "transform", "method": "normalize", "records": 850},
        {"step": 4, "operation": "aggregate", "group_by": "category", "records": 10},
        {"step": 5, "operation": "output", "target": "result.json", "records": 10},
    ]
    
    return {
        "pipeline": "data_processing",
        "n_steps": len(lineage),
        "lineage": lineage,
        "summary": {
            "input_records": lineage[0]["records"],
            "output_records": lineage[-1]["records"],
            "compression_ratio": lineage[-1]["records"] / lineage[0]["records"]
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
