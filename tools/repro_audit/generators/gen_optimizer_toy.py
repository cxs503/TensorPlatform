"""生成器：optimizer_toy.json —— 玩具优化器测试。

复算逻辑：简单函数优化（Rosenbrock 函数）。
"""
import math

OUTPUT_PATH = "artifacts/optimizer_toy.json"


def rosenbrock(x: float, y: float) -> float:
    """Rosenbrock 函数：f(x,y) = (a-x)^2 + b*(y-x^2)^2，a=1, b=100。"""
    a, b = 1.0, 100.0
    return (a - x) ** 2 + b * (y - x ** 2) ** 2


def generate() -> dict:
    """生成 optimizer_toy.json 的数值内容。"""
    # 模拟优化轨迹
    x, y = -1.0, -1.0
    lr = 0.001
    trajectory = []
    
    for i in range(50):
        f = rosenbrock(x, y)
        trajectory.append({
            "iteration": i,
            "x": x,
            "y": y,
            "f": f
        })
        
        # 简单梯度下降（数值梯度）
        eps = 1e-6
        df_dx = (rosenbrock(x + eps, y) - f) / eps
        df_dy = (rosenbrock(x, y + eps) - f) / eps
        
        x -= lr * df_dx
        y -= lr * df_dy
    
    final = trajectory[-1]
    
    return {
        "optimizer": "gradient_descent",
        "function": "rosenbrock",
        "n_iterations": len(trajectory),
        "initial": {"x": -1.0, "y": -1.0, "f": rosenbrock(-1.0, -1.0)},
        "final": {"x": final["x"], "y": final["y"], "f": final["f"]},
        "trajectory": trajectory
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
