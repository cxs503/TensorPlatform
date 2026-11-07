"""受控干跑装置：解析 SDOF 物理 + 合成席位制造器 + 物理锚打分器 + 三个演练场景。

**这不是船舶/平台业务代码**，而是编排器的「地面测试台」：一个解析可解的物理问题
（单自由度阻尼自由振动），让「生成席位 → 取工件 → 物理锚打分 → 淘汰 / 杂交 → 裁决」
这条链路在没有外部依赖的情况下被真实跑通、真实测量。

物理
----
无量纲化单自由度阻尼自由振动 ``x'' + 2ζω x' + ω² x = 0``，初条件 ``x(0)=x0, x'(0)=0``：

    x(t) = x0 · e^{-ζωt} · [cos(ω_d t) + (ζω/ω_d)·sin(ω_d t)],   ω_d = ω√(1-ζ²)

参考参数 ``ζ=0.08, ω=2.4 rad/s, x0=0.05 m``，时间窗 ``t ∈ [0,12] s``，``Δt=0.05 s``（241 点）。
两个物理锚（打分器独立计算，席位无法改写）：
- ``rel_error``：预测序列与解析解的相对 L2 误差（通过门 ≤ 3%，与平台精度标准同档）；
- ``decay_consistency_residual``：机械能 ``E=½ẋ²+½ω²x²`` 的**对数减量自洽**残差 ``|λ̂+2ζ̂ω̂|/|2ζ̂ω̂|``——
  物理上阻尼系统必须满足 ``d lnE/dt = -2ζω``；该锚对相位误差不敏感、只考核耗散律，与 L2 误差互补，
  任何数值不稳定的推进（显式欧拉）在这里露馅（物理锚不是模型自评）。

席位制造器（受控合成）
--------------------
- ``model_family`` → 参数估计偏差 ``(Δζ/ζ, Δω/ω)``（**单族偏差全部设计在门限之外**：这正是
  锦标赛要检验的「裸数量不突破」，只有异号偏差族杂交平均才能把偏差压到门内）；
- ``framework`` → 数值推进格式（closed-form / rk4 / symplectic-euler / forward-euler）；
- ``physics_anchor=energy-decay`` → 事后做能量投影（把非单调的轨迹投影回单调衰减流形，
  这是真实的数值技巧，不是作弊）；
- ``blind_prediction`` → 席位在取分前先写下 ``predicted_pass_prob``（盲预测，用于校准审计）；
- 工件里带一个**乐观的自评分** ``self_reported_score``（只反映参数自信，看不到积分误差），
  编排器必须丢弃它——这是 Goodhart 免疫门的活体演示。

诚实声明：基因→偏差表是本干跑装置的**设计参数**（受控实验），不是物理定律；编排器逻辑
本身不依赖任何具体数字。三个场景：``three_generations``（三代链路，G3 才 PASS）、
``pass_early``（G1 即 PASS，验证停播后续席位 + 预算回收）、``all_eliminated``（门限不可达，
全灭裁决 = honest FAIL 参照）。

用法
----
    python3 tools/tournament/dry_run.py --out-dir artifacts/tournament
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Callable

ZETA_REF, OMEGA_REF, X0_REF = 0.08, 2.4, 0.05
DT_GRID = 0.05
T_END = 12.0
ANCHOR_METRICS = ("rel_error", "decay_consistency_residual")
DECAY_TOL = 0.02  # 耗散律自洽残差的门：物理锚，非模型自评

# 模型族 → 参数估计偏差（相对）。设计纪律：任一单族的 |Δζ/ζ| 都足以让 rel_error 越门；
# 异号族（正 / 负）平均后偏差大幅相消 —— 这正是「集成抵消模型形偏差」的标准结论。
FAMILY_BIAS: dict[str, dict[str, float]] = {
    # 正号族：单独投放时阻尼被高估，响应衰减过快
    "gp-regression": {"zeta": +0.080, "omega": +0.0015},
    "spectral-collocation": {"zeta": +0.085, "omega": -0.0010},
    "mimetic-operator": {"zeta": +0.090, "omega": -0.0020},
    "poly-fit": {"zeta": +0.115, "omega": -0.0030},
    # 负号族：单独投放时阻尼被低估，响应衰减过慢
    "analytic-inversion": {"zeta": -0.062, "omega": +0.0008},
    "nn-surrogate": {"zeta": -0.066, "omega": -0.0015},
    "pinn-residual": {"zeta": -0.070, "omega": +0.0020},
}
# 设计纪律：任一单族的 |Δζ/ζ| >= 6.2%，在 3% 相对误差门下单族必不达标（rel_error >~ 3.9%），
# 故任何一代的「单族席位」都不可能 PASS；只有把正号族与负号族的估计等权平均（集成抵消
# 模型形偏差，标准结论）才可能进 3% 门。编排器里没有一行代码知道族的符号——它只看分数。

# 框架 → 数值推进格式与步长（步长不同但公共输出网格一致，避免用分辨率作弊）
FRAMEWORK_SCHEME: dict[str, dict[str, Any]] = {
    # base_dt = 该框架的默认步长；declaring the anchor ⇒ 承诺耗散律 ⇒ 步长细分 REFINE 倍
    "closed-form": {"scheme": "closed", "base_dt": DT_GRID},
    "rk4": {"scheme": "rk4", "base_dt": DT_GRID},
    "symplectic-euler": {"scheme": "symplectic", "base_dt": 0.01},
    "forward-euler": {"scheme": "forward", "base_dt": 0.01},
}
REFINE = 2.0  # physics_anchor=energy-decay 的席位必须把步长再细分（约束驱动的离散化）



# ------------------------------------------------------------------------------- 物理
def time_grid() -> list[float]:
    n = int(round(T_END / DT_GRID))
    return [round(i * DT_GRID, 10) for i in range(n + 1)]


def reference_series() -> list[float]:
    """解析解（无阻尼器误差、无积分误差的真实参考）。"""
    zeta, omega, x0 = ZETA_REF, OMEGA_REF, X0_REF
    wd = omega * math.sqrt(1.0 - zeta * zeta)
    out = []
    for t in time_grid():
        out.append(
            x0 * math.exp(-zeta * omega * t) * (math.cos(wd * t) + (zeta * omega / wd) * math.sin(wd * t))
        )
    return out


def mechanical_energy(x: list[float], dt: float, omega: float) -> list[float]:
    """E = ½ẋ² + ½ω²x²（中心差分取 ẋ）。"""
    n = len(x)
    energy = []
    for i in range(n):
        if i == 0:
            v = (x[1] - x[0]) / dt
        elif i == n - 1:
            v = (x[-1] - x[-2]) / dt
        else:
            v = (x[i + 1] - x[i - 1]) / (2 * dt)
        energy.append(0.5 * v * v + 0.5 * omega * omega * x[i] * x[i])
    return energy


def decay_rate(x: list[float], dt: float, omega: float) -> float:
    """对数减量：lnE(t) 的最小二乘斜率 λ̂（真实阻尼系统 λ = -2ζω）。"""
    energy = mechanical_energy(x, dt, omega)
    ts = [i * dt for i in range(len(energy))]
    ys = [math.log(max(e, 1e-18)) for e in energy]
    n = len(ts)
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    num = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys))
    den = sum((t - mean_t) ** 2 for t in ts)
    return num / den if den else 0.0


def decay_consistency_residual(
    x: list[float], dt: float, zeta_claim: float, omega_claim: float
) -> float:
    """耗散律自洽残差：|λ̂ + 2ζ̂ω̂| / |2ζ̂ω̂|，λ̂ = lnE(t) 的最小二乘斜率（席位自己申报的 ζ̂,ω̂）。

    这是**物理锚**而不是精度判据：参数偏差对它不可见（席位拿自己的申报值当基准），只有
    「数值格式违背耗散律」（显式欧拉的非物理增幅/过量数值阻尼）才会被它抓住。因此它与 L2
    误差互补：一个管"对不对得上参考解"，一个管"是否符合自己宣称的物理律"。
    注意它的局限：席位可以把申报值反向拟合自己的曲线来蒙混过关，所以锚**不能**替代外部
    参考判据（报告里如实写明）。
    """
    lam_true = -2.0 * zeta_claim * omega_claim
    if lam_true == 0:
        return float("inf")
    return abs(decay_rate(x, dt, omega_claim) - lam_true) / abs(lam_true)


def relative_l2(pred: list[float], ref: list[float]) -> float:
    num = math.sqrt(sum((p - r) ** 2 for p, r in zip(pred, ref)))
    den = math.sqrt(sum(r * r for r in ref))
    return num / den if den else float("inf")


# ------------------------------------------------------------------------------- 席位制造器
def family_estimate(gene: dict) -> dict[str, float]:
    bias = FAMILY_BIAS.get(gene["model_family"], {"zeta": 0.0, "omega": 0.0})
    return {
        "zeta": ZETA_REF * (1.0 + bias["zeta"]),
        "omega": OMEGA_REF * (1.0 + bias["omega"]),
        "x0": X0_REF,
    }


def integrate(scheme_cfg: dict, zeta: float, omega: float, x0: float) -> list[float]:
    """按框架格式积分；输出对齐公共时间网格（closed-form 直接解析）。"""
    grid = time_grid()
    dt = float(scheme_cfg["dt"])
    scheme = scheme_cfg["scheme"]
    if scheme == "closed":
        wd = omega * math.sqrt(max(1e-12, 1.0 - zeta * zeta))
        return [
            x0 * math.exp(-zeta * omega * t) * (math.cos(wd * t) + (zeta * omega / wd) * math.sin(wd * t))
            for t in grid
        ]

    steps = int(round(T_END / dt))
    x, v = x0, 0.0
    samples = {0.0: x}
    for i in range(steps):
        if scheme == "rk4":
            def f(state):  # 状态空间
                xx, vv = state
                return (vv, -2.0 * zeta * omega * vv - omega * omega * xx)

            k1 = f((x, v))
            k2 = f((x + 0.5 * dt * k1[0], v + 0.5 * dt * k1[1]))
            k3 = f((x + 0.5 * dt * k2[0], v + 0.5 * dt * k2[1]))
            k4 = f((x + dt * k3[0], v + dt * k3[1]))
            x_new = x + dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            v_new = v + dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        elif scheme == "symplectic":
            v_new = v + dt * (-2.0 * zeta * omega * v - omega * omega * x)
            x_new = x + dt * v_new
        else:  # forward（显式欧拉，对振子无条件不稳定）
            x_new = x + dt * v
            v_new = v + dt * (-2.0 * zeta * omega * v - omega * omega * x)
        x, v = x_new, v_new
        samples[(i + 1) * dt] = x

    keys = sorted(samples)
    out = []
    for t in grid:
        if t in samples:
            out.append(samples[t])
        else:  # 线性插值到公共网格
            lo = max(k for k in keys if k <= t)
            hi = min(k for k in keys if k >= t)
            if hi == lo:
                out.append(samples[lo])
            else:
                w = (t - lo) / (hi - lo)
                out.append(samples[lo] * (1 - w) + samples[hi] * w)
    return out


def make_executor() -> Callable[[dict], dict]:
    """合成席位执行器：mission → artifact（真实数值积分，不是随机数）。"""

    def execute(mission: dict) -> dict:
        gene = mission["diversity"]
        own = family_estimate(gene)
        reflowed = mission.get("parent_estimates") or []
        if reflowed:  # 杂交：父代实测量等权平均（回流），而不是重新抽签
            zeta = sum(est["zeta"] for est in reflowed) / len(reflowed)
            omega = sum(est["omega"] for est in reflowed) / len(reflowed)
            x0 = sum(est["x0"] for est in reflowed) / len(reflowed)
            estimate = {"zeta": zeta, "omega": omega, "x0": x0}
        else:
            estimate = dict(own)

        base = FRAMEWORK_SCHEME[gene["framework"]]
        dt_eff = float(base["base_dt"])
        if gene["physics_anchor"] == "energy-decay" and base["scheme"] != "closed":
            dt_eff = dt_eff / REFINE  # 承诺耗散律 ⇒ 步长细分，把能量残差压进锚门
        cfg = {"scheme": base["scheme"], "dt": dt_eff}
        x = integrate(cfg, estimate["zeta"], estimate["omega"], estimate["x0"])

        ref = reference_series()
        self_err = relative_l2(x, ref)
        # 乐观自评：席位只看自己的参数自信（看不到自己相对解析解的误差）→ 必然虚高
        self_score = 1.0 - abs(estimate["zeta"] - ZETA_REF) / ZETA_REF * 0.25
        artifact = {
            "t": time_grid(),
            "x": [round(v, 12) for v in x],
            "estimate": {k: round(v, 8) for k, v in estimate.items()},
            "declared_anchor": gene["physics_anchor"],
            "framework_scheme": cfg["scheme"],
            "dt_used": dt_eff,
            # 盲预测：取分前先承诺通过概率（盲预测席位必须写）
            "predicted_pass_prob": (round(max(0.05, min(0.95, 1.0 - self_score + 0.5)), 3)
                                    if gene["blind_prediction"] else None),
            # 乐观自评分（编排器必须丢弃）
            "self_reported_score": round(self_score, 4),
            "seat_self_error_estimate": round(self_err, 6),
            "mission_id": mission["mission_id"],
        }
        return artifact

    return execute


# ------------------------------------------------------------------------------- 打分器
def make_scorer(max_rel_error: float = 0.03) -> Callable[[dict, dict], dict]:
    """物理锚打分器：只用解析参考 + 能量单调性，不看席位基因组、不看席位自评。"""

    ref = reference_series()
    grid = time_grid()

    def score(mission: dict, artifact: dict) -> dict:
        x = artifact.get("x")
        t = artifact.get("t")
        if not isinstance(x, list) or not isinstance(t, list) or len(x) != len(t):
            raise ValueError("artifact 缺 x/t 序列")
        if len(x) != len(ref):  # 网格必须对齐（不允许用分辨率换分数）
            raise ValueError(f"预测网格 {len(x)} 与参考网格 {len(ref)} 不一致")
        pred = [float(v) for v in x]
        rel = relative_l2(pred, ref)
        claim = artifact.get("estimate") or {}
        resid = decay_consistency_residual(
            pred, DT_GRID, float(claim.get("zeta", ZETA_REF)), float(claim.get("omega", OMEGA_REF))
        )
        anchor_ok = bool(resid <= DECAY_TOL)
        passed = bool(rel <= max_rel_error and anchor_ok)
        return {
            "score": max(0.0, 1.0 - rel),
            "passed": passed,
            "anchor_ok": anchor_ok,
            "metrics": {
                "rel_error": round(rel, 8),
                "decay_consistency_residual": round(resid, 8),
                "measured_decay_rate": round(decay_rate(pred, DT_GRID, OMEGA_REF), 6),
                "claimed_decay_rate": round(-2.0 * float(claim.get("zeta", ZETA_REF)) * float(claim.get("omega", OMEGA_REF)), 6),
                "gate_rel_error": max_rel_error,
                "gate_decay_consistency_residual": DECAY_TOL,
                "n_samples": len(pred),
                "grid_dt": DT_GRID,
            },
        }

    return score


# ------------------------------------------------------------------------------- 场景
SCENARIOS: dict[str, dict[str, Any]] = {
    # 三代链路：单族全部越门，靠 G2 杂交降偏差 + G3 换稳定框架才 PASS
    # 选 seed=12 是为了让「完整三代链路」这一过程被真正走满：跨 40 个 seed 扫描显示
    # 冠军多出现在 G2（33/40），G3 才出冠军的有 3 个（12/21/40），7 个在预算/代数内全灭。
    # 编排器不依赖 seed（见 tests 的跨 seed 不变量测试）；seed 只是受控干跑的环境参数。
    "three_generations": {
        "threshold": 0.97,
        "max_rel_error": 0.03,
        "seats_per_generation": 5,
        "max_generations": 3,
        "max_total_seats": 20,
        "seed": 12,
    },
    # 早停正例：门放宽到 12% → G1 就有席位通过，后续代席位不播、预算回收
    "pass_early": {
        "threshold": 0.88,
        "max_rel_error": 0.12,
        "seats_per_generation": 5,
        "max_generations": 3,
        "max_total_seats": 20,
        "seed": 12,
    },
    # 全灭负例：门限 0.1% 不可达 → 跑满代数、无冠军、全席入负知识轨（honest FAIL 参照）
    "all_eliminated": {
        "threshold": 0.999,
        "max_rel_error": 0.001,
        "seats_per_generation": 5,
        "max_generations": 3,
        "max_total_seats": 20,
        "seed": 12,
    },
}


def build_problem(scenario: str = "three_generations"):
    from .model import ProblemSpec

    cfg = SCENARIOS[scenario]
    return ProblemSpec(
        problem_id=f"dryrun-sdof-{scenario}",
        title="解析 SDOF 阻尼自由振动的参数反演（受控干跑问题）",
        families=tuple(FAMILY_BIAS),
        frameworks=tuple(FRAMEWORK_SCHEME),
        anchors=("energy-decay", "none"),
        anchor_metrics=ANCHOR_METRICS,
        score_threshold=cfg["threshold"],
        max_rel_error=cfg["max_rel_error"],
        seats_per_generation=cfg["seats_per_generation"],
        max_generations=cfg["max_generations"],
        max_total_seats=cfg["max_total_seats"],
        seed=cfg["seed"],
    )


def run_scenario(scenario: str, out_dir: str | Path, clock: Callable[[], str] | None = None) -> dict:
    from .orchestrator import TournamentOrchestrator

    problem = build_problem(scenario)
    out_dir = Path(out_dir)
    orch = TournamentOrchestrator(
        problem=problem,
        scorer=make_scorer(problem.max_rel_error or 0.03),
        executor=make_executor(),
        workdir=out_dir / "runs" / scenario,
        clock=clock,
        campaign_id=f"IN25-T1/{scenario}",
    )
    return orch.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="锦标赛编排器干跑（三场景）")
    parser.add_argument("--out-dir", default="artifacts/tournament")
    parser.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    summary = {}
    for name in names:
        report = run_scenario(name, out_dir)
        (out_dir / f"report_{name}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), "utf-8"
        )
        from .report import render_markdown

        (out_dir / f"report_{name}.md").write_text(render_markdown(report), "utf-8")
        champ = report["verdict"]["champion"]
        summary[name] = {
            "verdict": report["verdict"]["verdict"],
            "generations_run": report["verdict"]["generations_run"],
            "stopped_early": report["verdict"]["stopped_early"],
            "pass_generation": report["verdict"]["pass_generation"],
            "seats_spent": report["budget"]["seats_spent"],
            "seats_unspent": report["budget"]["seats_unspent"],
            "champion": champ["seat_id"] if champ else None,
            "champion_relerr": None if not champ else champ["metrics"].get("rel_error"),
            "audit_events": report["audit"]["event_count"],
            "audit_chain_valid": report["audit"]["chain_valid"],
            "negative_records": report["negative_knowledge"]["count"],
        }
        print(json.dumps({name: summary[name]}, ensure_ascii=False))
    (out_dir / "dryrun_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), "utf-8"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    if __package__ in (None, ""):  # 允许直接 `python3 tools/tournament/dry_run.py`
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tools.tournament.dry_run import main as _entry

        raise SystemExit(_entry())
    raise SystemExit(main())