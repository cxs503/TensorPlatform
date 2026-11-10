"""生成器：batch_sweep_demo.json —— 批量扫描演示。

复算逻辑：参数扫描 + 结果聚合。
"""
import math

OUTPUT_PATH = "artifacts/batch_sweep_demo.json"


def generate() -> dict:
    """生成 batch_sweep_demo.json 的数值内容。"""
    # 参数扫描：Reynolds 数 vs 摩擦系数
    re_range = [1e5, 5e5, 1e6, 5e6, 1e7, 5e7]
    results = []
    
    for Re in re_range:
        # ITTC-57
        log_re = math.log10(Re)
        Cf = 0.075 / (log_re - 2.0) ** 2
        
        results.append({
            "Reynolds": Re,
            "Cf_ittc57": Cf,
            "log_Re": log_re
        })
    
    return {
        "sweep_type": "reynolds_sweep",
        "n_points": len(results),
        "results": results,
        "summary": {
            "min_cf": min(r["Cf_ittc57"] for r in results),
            "max_cf": max(r["Cf_ittc57"] for r in results),
            "re_range": [min(re_range), max(re_range)]
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
