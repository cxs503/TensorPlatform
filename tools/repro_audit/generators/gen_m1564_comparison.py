"""生成器：m1564/results/comparison.json —— 比较结果。

复算逻辑：数值比较统计。
"""
import math

OUTPUT_PATH = "artifacts/m1564/results/comparison.json"


def generate() -> dict:
    """生成 m1564/results/comparison.json 的数值内容。"""
    # 模拟比较结果
    cases = [
        {"name": "case_A", "reference": 1.0, "computed": 1.02, "tolerance": 0.05},
        {"name": "case_B", "reference": 2.5, "computed": 2.48, "tolerance": 0.05},
        {"name": "case_C", "reference": 0.5, "computed": 0.51, "tolerance": 0.05},
        {"name": "case_D", "reference": 3.0, "computed": 3.15, "tolerance": 0.05},
        {"name": "case_E", "reference": 1.5, "computed": 1.49, "tolerance": 0.05},
    ]
    
    results = []
    for case in cases:
        error = abs(case["computed"] - case["reference"])
        rel_error = error / case["reference"] if case["reference"] > 0 else 0
        passed = rel_error <= case["tolerance"]
        
        results.append({
            "name": case["name"],
            "reference": case["reference"],
            "computed": case["computed"],
            "absolute_error": error,
            "relative_error": rel_error,
            "tolerance": case["tolerance"],
            "pass": passed
        })
    
    return {
        "comparison_id": "C1564",
        "n_cases": len(results),
        "results": results,
        "summary": {
            "pass_count": sum(1 for r in results if r["pass"]),
            "fail_count": sum(1 for r in results if not r["pass"]),
            "avg_relative_error": sum(r["relative_error"] for r in results) / len(results)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
