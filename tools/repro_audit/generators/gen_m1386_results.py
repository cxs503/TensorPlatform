"""生成器：m1386/results.json —— 任务结果。

复算逻辑：数值计算结果。
"""
import math

OUTPUT_PATH = "artifacts/m1386/results.json"


def generate() -> dict:
    """生成 m1386/results.json 的数值内容。"""
    # 模拟数值计算结果
    x_values = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results = []
    
    for x in x_values:
        # 模拟函数：sin(pi*x) * exp(-x)
        y = math.sin(math.pi * x) * math.exp(-x)
        
        results.append({
            "x": x,
            "y": y,
            "error_bound": 1e-6 * (1 + x)
        })
    
    return {
        "computation": "test_function",
        "n_points": len(results),
        "results": results,
        "summary": {
            "x_range": [min(x_values), max(x_values)],
            "y_range": [min(r["y"] for r in results), max(r["y"] for r in results)],
            "max_error": max(r["error_bound"] for r in results)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
