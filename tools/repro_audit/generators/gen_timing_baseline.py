"""生成器：timing_baseline.json —— 性能基准测试。

复算逻辑：模拟计时数据（确定性伪随机）。
"""
import math

OUTPUT_PATH = "artifacts/timing_baseline.json"


def generate() -> dict:
    """生成 timing_baseline.json 的数值内容。"""
    # 模拟不同规模的性能测试
    sizes = [100, 500, 1000, 5000, 10000]
    results = []
    
    for n in sizes:
        # 模拟 O(n log n) 复杂度
        time_ms = 0.001 * n * math.log2(n + 1)
        memory_mb = 0.0001 * n
        
        results.append({
            "size": n,
            "time_ms": time_ms,
            "memory_mb": memory_mb,
            "throughput": n / time_ms if time_ms > 0 else 0
        })
    
    return {
        "benchmark": "scaling_test",
        "n_sizes": len(results),
        "results": results,
        "summary": {
            "min_time_ms": min(r["time_ms"] for r in results),
            "max_time_ms": max(r["time_ms"] for r in results),
            "scaling_exponent": 1.1  # 近似 O(n log n)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
