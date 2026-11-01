"""冰区航行域独立参考实现与闭式判据（验证欠账清偿 m1517）。

来源与角色
----------
本模块是与平台端点 POST /api/v1/extended-calculations/ice **互不共享代码**的
独立实现，用于对该端点逐条做机器判据：

* B 类（文献闭式）：Lindqvist (1989) 层冰阻力三分量（挤压 R_c / 破断 R_b /
  浸没 R_s）+ 速度放大因子；IACS UR I2 极地级壳板厚度关系式
  t = 667·s·sqrt(f1·P/σ_y)。系数取值区间化（σ_c, σ_f, μ, α 给定范围），
  因此判据以**区间**形式给出，结论不依赖单点取值。
* A 类（系数无关的响应律）：功率恒等式 P=R·V、dR/dV>0、dR/dT>0、
  厚度标度指数、挤压下界、等级饱和（continuous_speed ≤ 等级上限）、
  极地级壳板/骨材必须随等级变化（IACS UR I2 依等级设计冰压）。

这些 A 类判据不含任何需要"记忆"的系数，是本判决的硬核部分；B 类只做量级与
标度律校核，误差以区间给出（区间端点随参数上下界给出，见 LINDQVIST_BAND）。

单位约定：国际单位制（m, m/s, N, W, Pa）；输入速度为节，内部换算 m/s。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

G = 9.80665
KNOT = 0.514444
RHO_ICE = 900.0
RHO_WATER = 1025.0

# ── Lindqvist (1989) 参数区间（区间化，避免单点系数误差主导判决）───────────
LINDQVIST_BAND = {
    "sigma_c": (0.50e6, 1.50e6),   # 冰挤压强度 Pa（淡水/咸水冰 0.5–1.5 MPa）
    "sigma_f": (0.30e6, 0.70e6),   # 冰弯曲强度 Pa
    "mu": (0.05, 0.15),            # 冰-船摩擦系数
    "alpha_deg": (20.0, 35.0),     # 首部水线进流角
}

# ── IACS UR I2 极地级设计冰压（首部区，MPa）与壳板厚度系数 ─────────────────
IACS_PC_PRESSURE_MPA = {  # 等级 -> 首部设计冰压 P
    "PC1": 5.6, "PC2": 4.2, "PC3": 3.2, "PC4": 2.5,
    "PC5": 2.0, "PC6": 1.6, "PC7": 1.3,
}
F1_TRANSVERSE = 1.3
SIGMA_Y_STEEL = 315e6  # Pa（船用普通强度/高强钢下界）


# ──────────────────────────────────────────────────────────────
# Lindqvist 层冰阻力（独立实现）
# ──────────────────────────────────────────────────────────────
def lindqvist_resistance(L: float, B: float, T: float, h: float, v_ms: float,
                         sigma_c: float, sigma_f: float, mu: float,
                         alpha_deg: float, rho_i: float = RHO_ICE,
                         rho_w: float = RHO_WATER) -> Dict[str, float]:
    """Lindqvist (1989) 层冰阻力三分量（N）。

    R_c = 0.5·σ_c·h²·(tanα + μ)                       挤压（ψ≈α，cos 比取 1）
    R_b = 0.003·σ_f·B·h^1.5·(L/T)^0.6                 破断
    R_s = μ·(ρ_w-ρ_i)·g·h·B·(T + B·tanα/2)            浸没
    R_i = (R_c+R_b)(1 + 1.4·v/√(gh)) + R_s(1 + 9.4·v/√(gL))
    """
    a = math.radians(alpha_deg)
    R_c = 0.5 * sigma_c * h ** 2 * (math.tan(a) + mu)
    R_b = 0.003 * sigma_f * B * h ** 1.5 * (L / T) ** 0.6
    R_s = mu * (rho_w - rho_i) * G * h * B * (T + B * math.tan(a) / 2.0)
    f_v = 1.0 + 1.4 * v_ms / math.sqrt(G * h)
    f_L = 1.0 + 9.4 * v_ms / math.sqrt(G * L)
    R_i = (R_c + R_b) * f_v + R_s * f_L
    return {"R_c": R_c, "R_b": R_b, "R_s": R_s, "f_v": f_v, "f_L": f_L, "R_i": R_i}


def lindqvist_band(L, B, T, h, v_ms) -> Dict[str, float]:
    """参数区间四角组合，给出 Lindqvist 阻力上下界（N）。"""
    vals: List[float] = []
    sc = LINDQVIST_BAND["sigma_c"]
    sf = LINDQVIST_BAND["sigma_f"]
    mu = LINDQVIST_BAND["mu"]
    al = LINDQVIST_BAND["alpha_deg"]
    for s_c in sc:
        for s_f in sf:
            for m in mu:
                for a in al:
                    vals.append(lindqvist_resistance(L, B, T, h, v_ms, s_c, s_f, m, a)["R_i"])
    return {"R_lo": min(vals), "R_hi": max(vals)}


def iacs_plating_thickness_mm(ice_class: str, s_m: float) -> float:
    """IACS UR I2 极地级壳板厚度 t = 667·s·sqrt(f1·P/σ_y)（mm）。"""
    P = IACS_PC_PRESSURE_MPA[ice_class] * 1e6
    return 667.0 * s_m * math.sqrt(F1_TRANSVERSE * P / SIGMA_Y_STEEL)


# ──────────────────────────────────────────────────────────────
# 判据（对探针工件逐条判决）
# ──────────────────────────────────────────────────────────────
def _rows(probe: dict) -> Dict[str, dict]:
    """变体名 -> 端点响应。"""
    return {r["variant"]: r["response"] for r in probe["rows"]}


def _sel(rows: Dict[str, dict], prefix: str) -> List[Tuple[float, float]]:
    out = []
    for k, v in rows.items():
        if k.startswith(prefix + "=") or (prefix == "base" and k == "base"):
            x = float(k.split("=")[-1]) if "=" in k else 0.0
            out.append((x, v.get("ice_resistance")))
    return sorted(out)


def _rel(a: float, b: float) -> float:
    return (a - b) / b if b else float("inf")


def evaluate(probe: dict) -> dict:
    rows = _rows(probe)
    base_in = probe["base_case"]
    L, B, T = base_in["length"], base_in["beam"], base_in["draft"]
    h = base_in["ice_thickness"]
    v_ms = base_in["speed"] * KNOT
    R0 = rows["base"]["ice_resistance"]
    P0 = rows["base"]["ice_power"]
    crit: List[dict] = []

    def add(cid, desc, value, ref, verdict, cls):
        crit.append({"id": cid, "criterion": desc, "value": value,
                     "reference": ref, "verdict": verdict, "class": cls})

    # A1 功率恒等式 P = R·V
    err = _rel(P0, R0 * v_ms)
    add("A1", "功率恒等式 P = R·V(m/s)", f"{P0:.1f} W", f"{R0*v_ms:.1f} W",
        "PASS" if abs(err) < 0.01 else "FAIL", "A")

    # A2 速度响应 dR/dV > 0（Lindqvist 含 (1+1.4v/√(gh))）
    vs = _sel(rows, "V")
    dRdv = (vs[-1][1] - vs[0][1]) / (vs[-1][0] - vs[0][0])
    add("A2", "全速域 dR/dV > 0（Lindqvist 速度项）", f"{dRdv:.1f} N/kn",
        "> 0（1→5 kn 期望 R 增 50%+）", "PASS" if dRdv > 0 else "FAIL", "A")

    # A3 速度放大比 R(5kn)/R(1kn)，Lindqvist 区间 [1.4, 2.6]
    r_ratio = vs[-1][1] / vs[0][1] if vs[0][1] else float("inf")
    v1 = [c for c in vs if c[0] == 1.0][0][1]
    v5 = [c for c in vs if c[0] == 5.0][0][1]
    ratio15 = v5 / v1
    lq1 = lindqvist_band(L, B, T, h, 1.0 * KNOT)["R_lo"]
    lq5 = lindqvist_band(L, B, T, h, 5.0 * KNOT)["R_lo"]
    ref15 = lq5 / lq1
    add("A3", "速度放大比 R(5kn)/R(1kn)", f"{ratio15:.3f}",
        f"{ref15:.2f}（Lindqvist 同参数比）",
        "PASS" if abs(_rel(ratio15, ref15)) < 0.15 else "FAIL", "A")

    # A4 厚度标度指数 n: R ∝ h^n（Lindqvist 混合 1.5–2.0）
    hs = [(x, y) for x, y in _sel(rows, "h") if x > 0]
    n = None
    if len(hs) >= 2:
        (h1, r1), (h2, r2) = hs[0], hs[-1]
        n = math.log(r2 / r1) / math.log(h2 / h1)
    add("A4", "厚度标度指数 n（R ∝ h^n，h=0.3→2.0 m）",
        f"{n:.3f}" if n else "n/a", "1.5 ≤ n ≤ 2.0（Lindqvist 区间）",
        "PASS" if (n is not None and 1.45 <= n <= 2.05) else "FAIL", "A")

    # A5 吃水响应 dR/dT > 0（浸没项 ∝ T）
    ts = _sel(rows, "T")
    dRdt = (ts[-1][1] - ts[0][1])
    add("A5", "吃水响应 R(12m)-R(6m)（浸没项 ∝ T）", f"{dRdt:.1f} N", "> 0",
        "PASS" if dRdt > 0 else "FAIL", "A")

    # A6 挤压下界：R ≥ 0.5·σ_c,min·h²·tanα_min（任何 Lindqvist 实现的下界）
    import math as _m
    R_floor = 0.5 * LINDQVIST_BAND["sigma_c"][0] * h ** 2 * _m.tan(
        _m.radians(LINDQVIST_BAND["alpha_deg"][0]))
    add("A6", "挤压分量下界 R ≥ 0.5·σ_c,min·h²·tanα_min", f"{R0:.1f} N",
        f"≥ {R_floor:.1f} N", "PASS" if R0 >= R_floor else "FAIL", "A")

    # A7 等级饱和：continuous_speed ≤ 等级上限
    cap = {}
    for c in ("PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7"):
        cap[c] = rows[f"class={c}"]["continuous_speed"]
    v10 = [r for k, r in rows.items() if k == "V=10.0"][0]
    sat_ok = v10["continuous_speed"] <= cap["PC7"] + 1e-9
    add("A7", "连续航行速度不超等级上限（V=10kn, PC7）",
        f"{v10['continuous_speed']:.2f} kn", f"≤ {cap['PC7']:.2f} kn",
        "PASS" if sat_ok else "FAIL", "A")

    # A8 等级上限单调（PC1 ≥ PC2 ≥ … ≥ PC7）
    seq = [cap[c] for c in ("PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7")]
    add("A8", "等级能力单调 PC1≥…≥PC7（continuous_speed）", str(seq), "单调不增",
        "PASS" if all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1)) else "FAIL", "A")

    # A9 壳板厚度必须随极地级变化（IACS UR I2：P 依等级）
    plat = {c: rows[f"class={c}"]["shell_plating"] for c in cap}
    spread = max(plat.values()) - min(plat.values())
    t_pc1 = iacs_plating_thickness_mm("PC1", rows["class=PC1"]["frame_spacing"] / 1000.0)
    t_pc7 = iacs_plating_thickness_mm("PC7", rows["class=PC7"]["frame_spacing"] / 1000.0)
    add("A9", "壳板厚度随等级变化（IACS UR I2 P(PC1)≠P(PC7)）",
        f"PC1..PC7 = {sorted(set(plat.values()))} mm",
        f"IACS 估值 PC1≈{t_pc1:.1f} mm / PC7≈{t_pc7:.1f} mm",
        "PASS" if spread > 0.5 else "FAIL", "B")

    # A10 量级：平台 R vs Lindqvist 区间
    band = lindqvist_band(L, B, T, h, v_ms)
    dev_hi = _rel(R0, band["R_lo"])
    dev_lo = _rel(R0, band["R_hi"])
    add("A10", "层冰阻力量级 R_platform vs Lindqvist 区间",
        f"{R0/1e3:.2f} kN",
        f"[{band['R_lo']/1e3:.1f}, {band['R_hi']/1e3:.1f}] kN",
        "PASS" if band["R_lo"] <= R0 <= band["R_hi"] else "FAIL", "B")

    # A11 位移/吃水一致性：disp 变化但 T 不变时，R 不得依赖 disp（输入一致性）
    ds = _sel(rows, "disp")
    dspread = max(y for _, y in ds) - min(y for _, y in ds)
    add("A11", "位移不独立作用于 R（disp 10k→50k, T 固定）",
        f"ΔR = {dspread:.1f} N", "0（几何-水动力一致）",
        "PASS" if dspread == 0 else "FAIL", "A")

    n_fail = sum(1 for c in crit if c["verdict"] == "FAIL")
    verdict = "MOCK" if n_fail >= 4 else ("WITHHELD" if n_fail else "REAL")
    summary = {
        "endpoint": probe["endpoint"],
        "base_case": base_in,
        "platform": {"ice_resistance_N": R0, "ice_power_W": P0,
                     "continuous_speed_kn": rows["base"]["continuous_speed"],
                     "shell_plating_mm": rows["base"]["shell_plating"],
                     "frame_spacing_mm": rows["base"]["frame_spacing"]},
        "lindqvist_band_N": band,
        "underprediction_factor": {"lo": band["R_lo"] / R0, "hi": band["R_hi"] / R0},
        "iacs_plating_mm": {"PC1": t_pc1, "PC7": t_pc7},
        "criteria": crit,
        "n_criteria": len(crit),
        "n_fail": n_fail,
        "verdict": verdict,
    }
    return summary


def markdown_table(summary: dict) -> str:
    lines = ["| 判据 | 内容 | 平台实测 | 基准/参考 | 判决 |", "|---|---|---|---|---|"]
    for c in summary["criteria"]:
        lines.append(f"| {c['id']} | {c['criterion']} | {c['value']} | "
                     f"{c['reference']} | **{c['verdict']}** |")
    return "\n".join(lines)


if __name__ == "__main__":
    probe = json.load(open("artifacts/m1517/ice_probe.json"))
    s = evaluate(probe)
    json.dump(s, open("artifacts/m1517/ice_criteria.json", "w"),
              ensure_ascii=False, indent=1)
    open("artifacts/m1517/ice_criteria.md", "w").write(markdown_table(s) + "\n")
    print(markdown_table(s))
    print("\nverdict:", s["verdict"], f"({s['n_fail']}/{s['n_criteria']} FAIL)")
    print("Lindqvist band:", s["lindqvist_band_N"],
          "underprediction:", {k: round(v, 1) for k, v in s["underprediction_factor"].items()})
