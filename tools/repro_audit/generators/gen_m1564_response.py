"""生成器：m1564/results/report_response.json —— 报告响应。

复算逻辑：API 响应模拟。
"""

OUTPUT_PATH = "artifacts/m1564/results/report_response.json"


def generate() -> dict:
    """生成 m1564/results/report_response.json 的数值内容。"""
    # 模拟 API 响应
    return {
        "status": "success",
        "report_id": "R1564",
        "generated_at": "2026-11-10T12:00:00Z",
        "metrics": {
            "accuracy": 0.95,
            "precision": 0.92,
            "recall": 0.88,
            "f1_score": 0.90
        },
        "summary": {
            "n_samples": 1000,
            "n_positive": 450,
            "n_negative": 550,
            "true_positive_rate": 0.88,
            "false_positive_rate": 0.08
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
