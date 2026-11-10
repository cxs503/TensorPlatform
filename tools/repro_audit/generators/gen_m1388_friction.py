"""生成器：m1388/friction_crosscheck.json —— 摩擦交叉检查。

复算逻辑：多种摩擦公式对比。
"""
import math

OUTPUT_PATH = "artifacts/m1388/friction_crosscheck.json"


def ittc57_cf(re: float) -> float:
    """ITTC-57 摩擦系数。"""
    if re <= 0:
        return 0.0
    log_re = math.log10(re)
    return 0.075 / (log_re - 2.0) ** 2


def schoenherr_cf(re: float) -> float:
    """Schoenherr 摩擦系数（隐式公式近似）。"""
    if re <= 0:
        return 0.0
    # 近似：Cf ≈ 0.05 / (log10(Re))^2
    log_re = math.log10(re)
    return 0.05 / log_re**2


def generate() -> dict:
    """生成 m1388/friction_crosscheck.json 的数值内容。"""
    # Reynolds 数范围
    re_values = [1e5, 5e5, 1e6, 5e6, 1e7, 5e7]
    results = []
    
    for Re in re_values:
        cf_ittc = ittc57_cf(Re)
        cf_sch = schoenherr_cf(Re)
        diff_pct = abs(cf_ittc - cf_sch) / cf_ittc * 100 if cf_ittc > 0 else 0
        
        results.append({
            "Reynolds": Re,
            "Cf_ittc57": cf_ittc,
            "Cf_schoenherr": cf_sch,
            "difference_pct": diff_pct
        })
    
    return {
        "crosscheck_id": "F1388",
        "n_points": len(results),
        "results": results,
        "summary": {
            "avg_difference_pct": sum(r["difference_pct"] for r in results) / len(results),
            "max_difference_pct": max(r["difference_pct"] for r in results),
            "re_range": [min(re_values), max(re_values)]
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
