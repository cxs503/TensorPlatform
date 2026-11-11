"""漂移四路归因：把一条数值漂移的观测证据映射到 a/b/c/d 四类之一。

四类（与任务判据同构，均为通用故障机制、不含领域语义）：
  a_resolution        网格/离散分辨率差异（水平、阶次、单元数、时间步）
  b_solver_version    求解器版本或求解参数变更（版本号、容差、松弛因子、迭代上限）
  c_seed              随机种子/非确定性（种子缺失或不同、同种子重跑离散度超容差）
  d_baseline_version  基准数据版本漂移（基准工件 sha256 变化）

判决规则（可机裁，确定序）：
  1) 每个证据维度独立给出信号（命中=true）与权重（证据量：受影响参数个数）；
  2) 命中信号中权重最大者胜；权重并列按固定优先级 a>b>c>d（先结构后数值）；
  3) 无任何命中 → unattributed（不猜，作为 FAIL 送人审）；
  4) 置信度 = 胜者权重 / 全部命中权重之和（并列时为 1/k，如实反映不可分）。
"""
from __future__ import annotations

from typing import Any, Mapping

A = "a_resolution"
B = "b_solver_version"
C = "c_seed"
D = "d_baseline_version"
UNATTRIBUTED = "unattributed"

LABELS = (A, B, C, D)
PRECEDENCE = {A: 0, B: 1, C: 2, D: 3}

#: 每类的通用修复建议（领域无关）
FIXES = {
    A: "对齐两次复证的离散参数（网格/阶次/时间步）后重跑；若已对齐仍漂移，按分辨率收敛性做外推再比。",
    B: "固定求解器 commit 与求解参数（容差/迭代上限/松弛）到环境锁；对版本差异做一次等价性回归。",
    C: "在环境锁中记录种子与非确定性开关（并行归约顺序/线程数）；同种子重复 N 次取离散度作为容差下限。",
    D: "把基准工件按 sha256 版本化，比对时显式声明基准版本；基准升级须伴随重基线记录。",
    UNATTRIBUTED: "证据不足：需补充重跑离散度、求解参数差异、基准指纹三者中的至少一项，再归因。",
}


def _diff_keys(rec: Mapping[str, Any] | None, obs: Mapping[str, Any] | None) -> list[str]:
    rec = dict(rec or {})
    obs = dict(obs or {})
    return sorted(k for k in set(rec) | set(obs) if rec.get(k) != obs.get(k))


def signals(evidence: Mapping[str, Any] | None, tol: float = 1e-9) -> list[dict[str, Any]]:
    """从漂移证据里抽取四路信号（每路含 label / weight / details）。"""
    ev = dict(evidence or {})
    out: list[dict[str, Any]] = []

    res_keys = _diff_keys(ev.get("resolution", {}).get("recorded"),
                          ev.get("resolution", {}).get("observed"))
    if res_keys:
        out.append({"label": A, "weight": len(res_keys), "details": res_keys})

    sol_keys = _diff_keys(ev.get("solver", {}).get("recorded"), ev.get("solver", {}).get("observed"))
    if sol_keys:
        out.append({"label": B, "weight": len(sol_keys), "details": sol_keys})

    rng = dict(ev.get("rng") or {}) if "rng" in ev else None
    rng_details: list[str] = []
    if rng is not None:
        rs, os_ = rng.get("recorded_seed"), rng.get("observed_seed")
        spread = rng.get("rerun_spread")
        if rs is None or os_ is None or rs != os_:
            rng_details.append("seed_mismatch")
        if isinstance(spread, (int, float)) and spread > tol:
            rng_details.append(f"rerun_spread>{tol:g}")
    if rng_details:
        out.append({"label": C, "weight": 0.5 * len(rng_details), "details": rng_details})

    base = dict(ev.get("baseline") or {}) if "baseline" in ev else {}
    if base and base.get("recorded_sha256") != base.get("observed_sha256"):
        out.append({"label": D, "weight": 1, "details": ["baseline_sha256_changed"]})
    return out


def classify(evidence: Mapping[str, Any] | None, tol: float = 1e-9) -> dict[str, Any]:
    """归因判决：返回 {label, confidence, signals, fix, tied}。"""
    sig = signals(evidence, tol=tol)
    if not sig:
        return {"label": UNATTRIBUTED, "confidence": 0.0, "signals": [],
                "fix": FIXES[UNATTRIBUTED], "tied": []}
    best = max(s["weight"] for s in sig)
    winners = [s for s in sig if s["weight"] == best]
    winners.sort(key=lambda s: PRECEDENCE[s["label"]])
    total = sum(s["weight"] for s in sig)
    label = winners[0]["label"]
    return {
        "label": label,
        "confidence": round(best / total, 6) if total else 0.0,
        "signals": sig,
        "fix": FIXES[label],
        "tied": [s["label"] for s in winners[1:]],
    }


def richardson_residual(delta_abs: float, ratio: float, order: float) -> float:
    """离散误差估计的 Richardson 修正残差：|fine−coarse| / (r^p − 1)。

    这是离散收敛阶的通用数值工具（无领域语义）：把「分辨率差异造成的差」折算成
    「细网格解自身相对真解的误差上界估计」，用于判定归因后的残差是否仍在容差内。
    """
    if ratio is None or order is None or ratio <= 1 or order <= 0:
        raise ValueError("ratio>1 and order>0 required")
    return abs(float(delta_abs)) / (float(ratio) ** float(order) - 1.0)


def is_label(value: Any) -> bool:
    return value in LABELS
