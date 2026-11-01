#!/usr/bin/env python3
"""
toy_optimize.py — 端到端 toy 优化

目标: 最小化 KVLCC2 阻力 (通过船舶平台 :8001 Holtrop API)
设计变量:
  - x1 = L/B (长宽比), 范围 [5.0, 7.5]
  - x2 = B/T (宽吃水比), 范围 [2.5, 4.5]

固定参数 (KVLCC2 基线):
  - L = 190 m (船长)
  - Cb = 0.71 (方形系数)
  - V = 14 kn (航速)
  - displacement = 80420 m³ (排水体积, 由 L,B,T,Cb 计算)

方法: differential_evolution (全局) + surrogate (DOE+RBF+SLSQP)
输出: artifacts/optimizer_toy.json
"""

from __future__ import annotations

import json
import sys
import os
import numpy as np

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.optimizer import OptimizerTool, DesignVariable, LatinHypercubeDOE, BayesianSurrogate

# ─────────────────────────────────────────────
# 船舶平台 API 调用
# ─────────────────────────────────────────────

import urllib.request
import urllib.error

SHIP_API_BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")

def call_holtrop(speed: float, length: float, beam: float, draft: float,
                 displacement: float, cb: float) -> dict:
    """调用船舶平台 Holtrop 阻力计算"""
    payload = {
        "speed": speed,
        "length": length,
        "beam": beam,
        "draft": draft,
        "displacement": displacement,
        "cb": cb
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SHIP_API_BASE}/calculations/resistance",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {e.code}: {body}")


# ─────────────────────────────────────────────
# 目标函数
# ─────────────────────────────────────────────

# KVLCC2 基线参数
L_BASE = 190.0   # m
CB = 0.71
V_SPEED = 14.0   # kn

def objective(x: np.ndarray) -> float:
    """
    目标: 最小化总阻力 Rt (kN)
    x[0] = L/B ratio → B = L_BASE / x[0]
    x[1] = B/T ratio → T = B / x[1]
    displacement = L * B * T * Cb
    """
    lb_ratio = x[0]  # L/B
    bt_ratio = x[1]  # B/T

    B = L_BASE / lb_ratio
    T = B / bt_ratio
    disp = L_BASE * B * T * CB

    # 物理约束: T > 0, B > 0, disp > 0
    if T <= 0 or B <= 0 or disp <= 0:
        return 1e6

    result = call_holtrop(
        speed=V_SPEED,
        length=L_BASE,
        beam=B,
        draft=T,
        displacement=disp,
        cb=CB
    )
    return result["rt"]  # 总阻力 kN


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Optimizer Tool — KVLCC2 阻力优化 Toy")
    print("=" * 60)

    # 定义设计变量
    variables = [
        DesignVariable(name="L_over_B", lower=5.0, upper=7.5,
                       description="长宽比 L/B"),
        DesignVariable(name="B_over_T", lower=2.5, upper=4.5,
                       description="宽吃水比 B/T"),
    ]

    # 1) 基线评估
    print("\n[1] 基线评估 (KVLCC2 原始参数)")
    B_base = 32.0
    T_base = 10.0
    disp_base = L_BASE * B_base * T_base * CB
    baseline = call_holtrop(V_SPEED, L_BASE, B_base, T_base, disp_base, CB)
    print(f"  L/B = {L_BASE/B_base:.2f}, B/T = {B_base/T_base:.2f}")
    print(f"  Rt = {baseline['rt']:.2f} kN, Pe = {baseline['pe']:.2f} kW")

    # 2) DOE 采样
    print("\n[2] 拉丁超立方 DOE (10 样本)")
    doe = LatinHypercubeDOE(variables)
    samples = doe.sample_dict(10)
    doe_results = []
    for s in samples:
        x = np.array([s["L_over_B"], s["B_over_T"]])
        rt = objective(x)
        doe_results.append({"x": s, "Rt": rt})
        print(f"  L/B={s['L_over_B']:.3f}, B/T={s['B_over_T']:.3f} → Rt={rt:.2f} kN")

    # 3) 差分进化全局优化
    print("\n[3] 差分进化 (DE) 全局优化")
    opt_de = OptimizerTool(variables)
    result_de = opt_de.optimize(objective, method="DE", max_iter=50, pop_size=10)
    print(f"  成功: {result_de.success}")
    print(f"  最优 L/B = {result_de.best_x['L_over_B']:.4f}")
    print(f"  最优 B/T = {result_de.best_x['B_over_T']:.4f}")
    B_opt = L_BASE / result_de.best_x['L_over_B']
    T_opt = B_opt / result_de.best_x['B_over_T']
    print(f"  对应 B = {B_opt:.2f} m, T = {T_opt:.2f} m")
    print(f"  最优 Rt = {result_de.best_fun:.2f} kN")
    print(f"  评估次数: {result_de.n_eval}, 耗时: {result_de.elapsed_s:.2f}s")
    print(f"  收敛史长度: {len(result_de.convergence_history)}")

    # 4) 代理辅助优化
    print("\n[4] 代理辅助优化 (DOE→RBF→SLSQP)")
    opt_sur = OptimizerTool(variables)
    result_sur = opt_sur.optimize(objective, method="surrogate",
                                   max_iter=50, surrogate_doe_size=20)
    print(f"  成功: {result_sur.success}")
    print(f"  最优 L/B = {result_sur.best_x['L_over_B']:.4f}")
    print(f"  最优 B/T = {result_sur.best_x['B_over_T']:.4f}")
    print(f"  最优 Rt = {result_sur.best_fun:.2f} kN")
    print(f"  评估次数: {result_sur.n_eval}, 耗时: {result_sur.elapsed_s:.2f}s")

    # 4.5) 验证代理最优点的真实目标值
    print("\n[4.5] 验证代理最优点")
    x_sur = np.array([result_sur.best_x['L_over_B'], result_sur.best_x['B_over_T']])
    rt_sur_true = objective(x_sur)
    print(f"  代理预测 Rt = {result_sur.best_fun:.2f} kN")
    print(f"  真实 Rt = {rt_sur_true:.2f} kN")
    print(f"  代理误差 = {abs(rt_sur_true - result_sur.best_fun):.2f} kN "
          f"({abs(rt_sur_true - result_sur.best_fun)/rt_sur_true*100:.1f}%)")

    # 5) 保存结果
    output = {
        "mission": "m377_optimizer_toy",
        "description": "KVLCC2 阻力最小化 — 2变量(L/B, B/T)优化",
        "baseline": {
            "L_m": L_BASE, "B_m": B_base, "T_m": T_base,
            "Cb": CB, "V_kn": V_SPEED,
            "L_over_B": L_BASE / B_base,
            "B_over_T": B_base / T_base,
            "Rt_kN": baseline["rt"],
            "Pe_kW": baseline["pe"],
        },
        "design_variables": [v.to_dict() for v in variables],
        "doe_samples": doe_results,
        "optimization_DE": result_de.to_dict(),
        "optimization_surrogate": result_sur.to_dict(),
        "surrogate_verification": {
            "predicted_Rt_kN": result_sur.best_fun,
            "true_Rt_kN": rt_sur_true,
            "error_kN": abs(rt_sur_true - result_sur.best_fun),
            "error_pct": abs(rt_sur_true - result_sur.best_fun) / rt_sur_true * 100,
        },
        "comparison": {
            "baseline_Rt_kN": baseline["rt"],
            "DE_best_Rt_kN": result_de.best_fun,
            "surrogate_predicted_Rt_kN": result_sur.best_fun,
            "surrogate_true_Rt_kN": rt_sur_true,
            "DE_improvement_pct": (baseline["rt"] - result_de.best_fun) / baseline["rt"] * 100,
        }
    }

    out_path = "/srv/tensor/agent/work/m377/artifacts/optimizer_toy.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n[5] 结果已保存: {out_path}")
    print(f"\n{'='*60}")
    print(f"优化总结:")
    print(f"  基线 Rt = {baseline['rt']:.2f} kN")
    print(f"  DE 最优 Rt = {result_de.best_fun:.2f} kN "
          f"(改善 {(baseline['rt']-result_de.best_fun)/baseline['rt']*100:.1f}%)")
    print(f"  Surrogate 最优 Rt = {result_sur.best_fun:.2f} kN "
          f"(改善 {(baseline['rt']-result_sur.best_fun)/baseline['rt']*100:.1f}%)")
    print(f"{'='*60}")

    return output


if __name__ == "__main__":
    main()
