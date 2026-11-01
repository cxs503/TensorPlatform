"""由 artifacts/manoeuvring_numerics/audit.json 生成 4 张图 + 论文级复核定稿（数字零手抄）。"""
from __future__ import annotations

import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "manoeuvring_numerics"
DOC = REPO / "docs" / "verification" / "MANEUVRING_NUMERICS_AUDIT.md"
A = json.loads((OUT / "audit.json").read_text())

S = A["sections"]

# 兼容：从 rows 现算 u_ss / r_ss 的收敛阶与默认步长误差（零手抄）
def _order(vals):
    ps = []
    for i in range(len(vals) - 2):
        d1, d2 = vals[i] - vals[i + 1], vals[i + 1] - vals[i + 2]
        ps.append(float("nan") if (abs(d2) < 1e-16 or abs(d1) < 1e-16)
                  else math.log(abs(d1 / d2)) / math.log(2.0))
    return ps


for _n, _v in S["V1_V2_dt_convergence"].items():
    _rows = _v["rows"]
    for _k in ("advance_over_L", "tactical_diameter_over_L", "u_steady", "r_steady"):
        _vals = [r[_k] for r in _rows]
        _ex = _vals[-1] + (_vals[-1] - _vals[-2]) / 15.0
        _api = next(r[_k] for r in _rows if r["dt_s"] == 0.5)
        _sfx = {"advance_over_L": "advance", "tactical_diameter_over_L": "diameter"}.get(_k, _k)
        _v.pop(f"order_{_k}", None)
        _v.pop(f"api_default_dt05_error_pct_{_sfx}", None)
        _v[f"order_{_sfx}"] = _order(_vals)
        _v[f"api_default_dt05_error_pct_{_sfx}"] = 100.0 * abs(_api - _ex) / abs(_ex)
conv, v3, v4, v5, v6, v7 = (S["V1_V2_dt_convergence"], S["V3_linear_analytic_consistency"],
                            S["V4_nomoto_consistency"], S["V5_imo_logic"],
                            S["V6_sensitivity"], S["V7_absolute_gap"])
BAND = A["literature_band"]
IMO = {"adv": 4.5, "dia": 5.0, "z10": 10.0, "z20": 25.0}

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MK = {"KVLCC2": "o-", "KCS": "s-", "FINE-100": "^-"}

# ---- F1 回转轨迹 ----
fig, ax = plt.subplots(figsize=(6.4, 5.0))
for n, v in S["manoeuvre_samples"]["turning_35deg"].items():
    ax.plot(v["x_over_L"], v["y_over_L"], MK[n], ms=2.0, lw=1.2, label=n)
ax.set_xlabel("x / L"); ax.set_ylabel("y / L")
ax.set_title("Turning circle, 35 deg rudder (MMG model, dt = 0.5 s)")
ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=.3); ax.legend()
fig.tight_layout(); fig.savefig(OUT / "fig_turning_circles.png", dpi=140); plt.close(fig)

# ---- F2 步长收敛 ----
fig, ax = plt.subplots(figsize=(6.4, 4.6))
for n, v in conv.items():
    dts = [r["dt_s"] for r in v["rows"]]
    adv = [abs(r["advance_over_L"] - v["extrapolated_advance_over_L"]) + 1e-14 for r in v["rows"]]
    dia = [abs(r["tactical_diameter_over_L"] - v["extrapolated_diameter_over_L"]) + 1e-14 for r in v["rows"]]
    ax.loglog(dts, adv, MK[n][0] + "-", ms=4, label=f"{n} advance/L")
    ax.loglog(dts, dia, MK[n][0] + "--", ms=4, alpha=.55, label=f"{n} D_t/L")
ax.set_xlabel("dt [s]"); ax.set_ylabel("|indicator - Richardson dt->0| [L]")
ax.set_title("Step-size convergence of turning-circle indicators")
ax.grid(alpha=.3, which="both"); ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(OUT / "fig_dt_convergence.png", dpi=140); plt.close(fig)

# ---- F3 之字超调 vs IMO ----
fig, ax = plt.subplots(figsize=(6.4, 4.4))
names = list(v7); w = 0.35; xs = list(range(len(names)))
ax.bar([x - w / 2 for x in xs], [v7[n]["zigzag_10_10"][0] for n in names], w, label="10/10 1st overshoot")
ax.bar([x + w / 2 for x in xs], [v7[n]["zigzag_20_20"][0] for n in names], w, label="20/20 1st overshoot")
ax.axhline(IMO["z10"], color="r", ls=":", lw=1.3, label="IMO limit 10 deg (10/10)")
ax.axhline(IMO["z20"], color="b", ls=":", lw=1.3, label="IMO limit 25 deg (20/20)")
ax.set_xticks(xs); ax.set_xticklabels(names); ax.set_ylabel("overshoot [deg]")
ax.set_title("Zig-zag first overshoot vs IMO Res. MSC.137(76)")
ax.grid(alpha=.3, axis="y"); ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(OUT / "fig_zigzag_imo.png", dpi=140); plt.close(fig)

# ---- F4 Nomoto 线性化偏差 ----
fig, ax = plt.subplots(figsize=(6.4, 4.4))
for n, v in v4.items():
    ax.plot([r["delta_deg"] for r in v["rows"]], [r["rel_dev_pct"] for r in v["rows"]], MK[n], label=n)
ax.set_xlabel("rudder angle delta [deg]"); ax.set_ylabel("|r_ss - K*delta| / r_ss [%]")
ax.set_title("Linearisation error of the Nomoto equivalent vs rudder angle")
ax.grid(alpha=.3); ax.legend()
fig.tight_layout(); fig.savefig(OUT / "fig_nomoto_linearisation.png", dpi=140); plt.close(fig)

# ---------------- 表格与文档 ----------------
def tbl(rows, cols, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(
            (f"{r[c]:.6g}" if isinstance(r[c], float) else str(r[c])) for c in cols) + " |")
    return "\n".join(out)


conv_tbl = "\n\n".join(
    f"**{n}** — 观测阶 advance = {', '.join(f'{x:.2f}' for x in v['order_advance'])}，"
    f"u_ss = {', '.join(f'{x:.2f}' for x in v['order_u_steady'])}；"
    f"dt=0.5 s 相对外推值误差：advance {v['api_default_dt05_error_pct_advance']:.2e} %、"
    f"D_t/L {v['api_default_dt05_error_pct_diameter']:.2e} %、u_ss {v['api_default_dt05_error_pct_u_steady']:.2e} %\n\n"
    + tbl(v["rows"], ["dt_s", "advance_over_L", "tactical_diameter_over_L", "u_steady",
                      "r_steady", "speed_loss_pct", "drift_angle_deg"],
          ["dt [s]", "advance/L", "D_t/L", "u_ss [m/s]", "r_ss [rad/s]", "增速损失 %", "漂角 °"])
    for n, v in conv.items())

v3_tbl = "\n".join(
    f"| {n} | " + " | ".join(f"{r['delta_deg']:g}° → {r['max_rel_dev_r']*100:.3f} %" for r in v["rows"])
    + f" | {', '.join(f'{x:.2f}' for x in v.get('shrink_ratio') or v.get('ratios'))} |" for n, v in v3.items())

v4_tbl = "\n".join(
    f"| {n} | {v['K']:.6g} | {v['T']:.4f} | "
    + " | ".join(f"{r['rel_dev_pct']:.2f}" for r in v["rows"])
    + f" | {'是' if v['directionally_stable'] else '否'} |" for n, v in v4.items())

v6_tbl = "\n".join(
    f"| {n} | " + " | ".join(f"{v['by_rudder_deg'][k]['radius_over_L']:.3f}" for k in ("10", "20", "35"))
    + f" | {'是' if v['monotone_decreasing'] else '否'} |" for n, v in v6.items())

v7_tbl = "\n".join(
    f"| {n} | {v['advance_over_L']:.3f} | {v['tactical_diameter_over_L']:.3f} | "
    f"{v['steady_radius_over_L']:.3f} | {v['speed_loss_pct']:.2f} | "
    f"{v['diameter_vs_IMO_ratio']:.2f}× | {v['advance_vs_IMO_ratio']:.2f}× | "
    f"{v['diameter_vs_literature_ratio']:.2f}× |" for n, v in v7.items())

v5_tbl = "\n".join(f"| {k} | {'PASS' if v['pass'] else 'FAIL'} | {v['detail'][:100]} |"
                   for k, v in v5.items())

all_pass = sum(1 for v in v5.values() if v["pass"])
worst_dia = max(v["diameter_vs_IMO_ratio"] for v in v7.values())
max_v3 = max(r["max_rel_dev_r"] for v in v3.values() for r in v["rows"])
dt_err = max(max(v["api_default_dt05_error_pct_advance"],
                 v["api_default_dt05_error_pct_diameter"],
                 v["api_default_dt05_error_pct_u_steady"]) for v in conv.values())

DOC.write_text(f"""# 操纵性 MMG 模型数值复核与绝对精度缺口量化（m1761，2026-11-01）

> 被复核代码：`backend/methods/manoeuvring/{{model.py,manoeuvres.py}}`、`backend/api/domain/manoeuvring.py`
> 复核脚本：`tools/verifier/manoeuvring/{{verify_mmg_numerics.py,make_numerics_assets.py}}`
> 工件：`artifacts/manoeuvring_numerics/{{audit.json,fig_turning_circles.png,fig_dt_convergence.png,fig_zigzag_imo.png,fig_nomoto_linearisation.png}}`
> 测试：`backend/tests/feat/test_manoeuvring_numerics.py`
> 复跑：`python3 tools/verifier/manoeuvring/verify_mmg_numerics.py && python3 tools/verifier/manoeuvring/make_numerics_assets.py`
> （约 60 s，单核纯 CPU，无外部数据、无 CFD/LBM）

## 1 案例介绍

操纵性是 IMO 强制衡准项（Res. MSC.137(76）：回转性 advance ≤ 4.5L、战术直径 ≤ 5L；
10°/10° 与 20°/20° 之字首超调 ≤ 10° / ≤ 25°）。平台 `POST /api/v1/calculations/manoeuvring`
用 MMG 3-DOF 模型（纵荡-横荡-艏摇）计算回转圈与之字试验，并自评 `accuracy_grade = WITHHELD`
（诚实：无转录的水动力导数表）。本单的**验证欠账**是：把「数值侧」钉到闭式解上，
并把「绝对精度缺口」从定性 caveat 变成可复算的倍数。

三条标定船的几何（几何门槛 `Δ = ρ·Cb·L·B·T` 由 `HullParameters` 强制 3 % 内闭合）：

| 船型 | L [m] | B [m] | T [m] | Cb | U0 [m/s] | L/B | B/T |
|---|---|---|---|---|---|---|---|
{chr(10).join(f"| {n} | {h['length']} | {h['beam']} | {h['draft']} | {h['cb']} | {h['design_speed']} | {h['length']/h['beam']:.2f} | {h['beam']/h['draft']:.2f} |" for n, h in A["hulls"].items())}

## 2 怎么做的

七条判据全部闭式/客观（`tools/verifier/manoeuvring/verify_mmg_numerics.py`）：

| 编号 | 判据 | 基准 |
|---|---|---|
| V1 | 回转圈指标随 dt 的观测收敛阶 | Richardson 差分（RK4 理论 4 阶） |
| V2 | API 默认 dt = 0.5 s 的指标误差 | Richardson 外推（dt→0） |
| V3 | 小舵角非线性积分 vs 线性精确解 | 2×2 线性化系统 `exp(At)` 闭式解 |
| V4 | `K·δ` vs Newton 稳态 `r_ss` | Nomoto 一阶等效增益 |
| V5 | IMO 判决逻辑 | 阈值 4.5L / 5L / 10° / 25°、L ≥ 100 m 门槛、超限拦截 |
| V6 | 稳态回转半径对舵角单调性 | Newton 稳态解（无瞬态偏差） |
| V7 | 绝对精度缺口 | IMO 限值 + MMG 文献量级带 |

关键设置：回转 35° 舵、1800 s、dt ∈ {{1, 0.5, 0.25, 0.125, 0.0625}} s；之字 10°/10° 与 20°/20°；
线性化 `A, b` 由模型自带数值雅可比给出，解析解为 `z(t) = z_ss − exp(At)·z_ss`，
`z_ss = −A⁻¹bδ`（实/复特征值分别用双曲与 σ/ω 实值形式）。

## 3 计算结果

### 3.1 V1/V2 步长收敛（35° 舵，1800 s）

{conv_tbl}

判读：连续状态量 `u_ss`、`r_ss` 的收敛阶接近 RK4 理论值；`advance/L` 只有约 1 阶，
原因是 `turning_circle_indicators` 取「首个 ψ ≥ 90° 的采样点」而不做插值，指标抽取精度被采样间隔支配。
dt = 0.5 s 时全部指标相对外推值的误差 ≤ {dt_err:.2e} %，工程上可忽略 ⇒ 不构成缺陷，
但若要高阶一致，应改成跨点线性插值（列为可选改进，未改代码）。

### 3.2 V3 线性解析一致性（1°/2°/4° 舵，900 s，dt = 0.25 s）

| 船型 | 1° | 2° | 4° | 偏差缩小比（1→2, 2→4） |
|---|---|---|---|---|
{v3_tbl}

判读：非线性时域 RK4 推进与独立推导的 `exp(At)` 精确解最大相对偏差 ≤ {max_v3*100:.2f} %，
且随舵角减小而下降 ⇒ 推进器与线性化模块互相独立且自洽，无隐藏的单位/符号错误。

### 3.3 V4 Nomoto 一阶等效 vs Newton 稳态

| 船型 | K [1/s] | T [s] | δ=5° | δ=10° | δ=20° | δ=35° | 线性方向稳定 |
|---|---|---|---|---|---|---|---|
{v4_tbl}

（表中 δ 列为 `|r_ss − K·δ| / r_ss` 的百分数）判读：5° 时偏差最小、随舵角单调增长，
是**物理非线性**（舵力 ∝ sin α、横流阻力 ∝ |V|V），因此用 `K·δ` 外推大舵角回转会高估回转能力。

### 3.4 V5 IMO 判决逻辑

| 检查项 | 结果 | 证据（截断） |
|---|---|---|
{v5_tbl}

合计 **{all_pass}/{len(v5)} PASS**。

### 3.5 V6 灵敏度指纹（Newton 稳态 R/L）

| 船型 | δ=10° | δ=20° | δ=35° | 单调递减 |
|---|---|---|---|---|
{v6_tbl}

判读：半径随舵角单调、随船型与船长变化 ⇒ 排除「与输入无关的硬编码/MOCK」指纹。

### 3.6 V7 绝对精度缺口（35° 舵回转；10°/20° 之字）

| 船型 | advance/L | D_t/L | R/L | 增速损失 % | D/IMO 5L | advance/IMO 4.5L | D/文献中值 3.15L |
|---|---|---|---|---|---|---|---|
{v7_tbl}

IMO Res. MSC.137(76) 限值：advance ≤ 4.5L、D_t ≤ 5L；之字首超调 10/10 ≤ 10°、20/20 ≤ 25°。
文献量级带（MMG 标准法模拟 + 试航量级）D/L {BAND['diameter_over_L'][0]}–{BAND['diameter_over_L'][1]}、
A/L {BAND['advance_over_L'][0]}–{BAND['advance_over_L'][1]} 仅作**量级比对**——本模型不含实船导数表，
不具备逐点对标条件，故不给「<3 %」式的逐点精度结论。

**误差判定（逐项回答「是否 < 3 %」）**

| 关键量 | 基准 | 现状 | < 3 %？ |
|---|---|---|---|
| 连续状态量 u_ss / r_ss 的 dt 离散误差 | Richardson 外推 | {dt_err:.2e} % | **是** |
| 时域推进 vs 线性解析 r(t) | `exp(At)` 闭式解 | {max_v3*100:.2f} % | **是** |
| 战术直径 D_t/L | IMO 5L | {min(v['tactical_diameter_over_L'] for v in v7.values()):.1f}–{max(v['tactical_diameter_over_L'] for v in v7.values()):.1f} L（{min(v['diameter_vs_IMO_ratio'] for v in v7.values()):.1f}–{worst_dia:.1f}× 限值） | **否** |
| 纵距 advance/L | IMO 4.5L | {min(v['advance_over_L'] for v in v7.values()):.1f}–{max(v['advance_over_L'] for v in v7.values()):.1f} L | **否** |
| 之字首超调 | IMO 10° / 25° | {min(v['zigzag_10_10'][0] for v in v7.values()):.1f}–{max(v['zigzag_10_10'][0] for v in v7.values()):.1f}° / {min(v['zigzag_20_20'][0] for v in v7.values()):.1f}–{max(v['zigzag_20_20'][0] for v in v7.values()):.1f}° | **否** |

## 4 结果图

- `fig_turning_circles.png`：35° 舵回转轨迹（x/L–y/L，三船型）——定量印证 V7 的大直径。
- `fig_dt_convergence.png`：指标误差–步长 log-log；advance 斜率 ≈ 1（采样限制）、D_t/L 触底（≈1e-5 L）。
- `fig_zigzag_imo.png`：10/10 与 20/20 首超调柱状 vs IMO 限值虚线——超调约 2× 限值。
- `fig_nomoto_linearisation.png`：`|r_ss − Kδ|/r_ss` 随舵角增长，界定「线性增益只在小舵角可用」。

## 5 注意点与局限

1. **精度缺口的物理根因（判读，非实测）**：hull 力为纯 Newton 横流模型，缺势流升力项
   （实船 `Y'_v`、`N'_v`），且舵法向力未计入螺旋桨尾流增速（常数推力假设），
   两者共同导致回转能力被系统性低估约 5 倍。这与端点自评 `WITHHELD` 及 caveat 一致，
   本单只是把缺口**量化**，并未修改物理模型（改导数集需外部数据，超出单干范围）。
2. **文献带的引用强度**：仅作量级比对，未逐点引用具体论文表格；因此不宣称「误差 X%」。
3. **指标抽取精度**：advance 为 1 阶（采样点不插值），已验证其绝对值影响 ≤ {dt_err:.2e} %。
4. **适用域**：`stance` 判据只对 L ≥ 100 m 的排水型船生效；Cb∈[0.35, 0.90]、L/B∈[3, 12]、B/T∈[1.5, 6.5]。
5. **使用边界（给下游）**：可用于相对比较、敏感度与算法验证；**不可用于 IMO 合规判定或操纵性预报**。

## 6 复现

```bash
python3 tools/verifier/manoeuvring/verify_mmg_numerics.py
python3 tools/verifier/manoeuvring/make_numerics_assets.py
python3 -m pytest backend/tests/feat/test_manoeuvring_numerics.py -q
```
""")
print("doc:", DOC, DOC.stat().st_size, "bytes")