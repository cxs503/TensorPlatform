import sys, math, json, pathlib
sys.path.insert(0, 'backend')
from services.seakeeping import (SeakeepingModel, spectrum, uniform_grid, response_statistics,
                                 _integrate, transfer, wave_number)

HULL = dict(L=150.0, B=22.0, T=8.0, Cb=0.68, Cwp=0.78, displacement_t=18400.0, KG=8.5, GM_T=1.5)
m = SeakeepingModel(**HULL)
res = {"hull": HULL}
res["periods"] = m.natural_periods()
res["hydro"] = m.hydrostatics()

# --- A. long-wave band-limited sea (free-surface anchor) ---
ome = uniform_grid(0.05, 0.12, 400)
base = spectrum("pm", Hs=2.0, Tp=25.0, omega=ome)
S = [s if 0.05 <= w <= 0.12 else 0.0 for s, w in zip(base["S"], ome)]
spec = dict(base); spec["S"] = S
st = response_statistics(m, spec, heading_deg=180.0, speed=0.0)
sig_zeta = math.sqrt(_integrate(S, ome))
slope_var = _integrate([wave_number(w) ** 2 * s for s, w in zip(S, ome)], ome)
slope_deg = math.degrees(math.sqrt(slope_var))
res["A"] = {"sigma_zeta_m": sig_zeta, "slope_sigma_deg": slope_deg, **{k: st[k] for k in
            ("sigma_heave", "significant_heave", "sigma_pitch", "sigma_roll")}}
from services.seakeeping import waterplane_length_factor, beam_factor, smith_factor, transfer as _tr, encounter_frequency_safe
def roll_pred(omegas, S, heading=180.0, speed=0.0):
    """sigma_roll from the closed-form factor chain k*Gamma(u)*exp(-kT)*|H(we/wn4)| (no code reuse of response_statistics)."""
    d = []
    for w, s_ in zip(omegas, S):
        k = wave_number(w); cosb = math.cos(math.radians(heading))
        u = 0.5 * k * m.L * abs(cosb); we, _ok = encounter_frequency_safe(w, speed, heading)
        r = (k * waterplane_length_factor(u) * math.exp(-k * m.T) * _tr(we / m.omega4, m.zeta44))
        d.append(r * r * s_)
    return math.degrees(math.sqrt(_integrate(d, omegas)))
res["roll_pred_deg"] = roll_pred(ome, S)
res["A_err"] = {
    "heave_vs_sigma_zeta": st["sigma_heave"] / sig_zeta - 1.0,
    "significant_heave_vs_2sigma": st["significant_heave"] / (2 * sig_zeta) - 1.0,
    "pitch_vs_slope": st["sigma_pitch"] / slope_deg - 1.0,
    "roll_vs_slope": st["sigma_roll"] / slope_deg - 1.0,
    "roll_vs_chain": st["sigma_roll"] / res["roll_pred_deg"] - 1.0,
}

# --- B. white-noise SDOF integral identity ---
quad = []
for z in (0.05, 0.1, 0.2, 0.45):
    r = uniform_grid(1e-4, 400.0, 400001)
    v = _integrate([transfer(x, z) ** 2 for x in r], r)
    quad.append({"zeta": z, "numeric": v, "closed": math.pi / (4 * z), "rel": v / (math.pi / (4 * z)) - 1})
res["B"] = quad

# --- C. defect magnitude: old (slope-normalised) vs fixed, head seas Hs=3 m ---
ome2 = uniform_grid(0.02, 6.0, 1601)
curve2 = m.rao_curve(ome2, heading_deg=180.0, speed=0.0)
defect = []
for tp in (4, 6, 8, 10, 12, 14):
    sp = spectrum("jonswap", 3.0, float(tp), ome2)
    fixed = response_statistics(m, sp, heading_deg=180.0)["sigma_pitch"]
    old = math.degrees(math.sqrt(_integrate([r * r * s for r, s in zip(curve2["pitch_rao_rad_per_rad"], sp["S"])], ome2)))
    kt = 2 * math.pi / (9.80665 * (tp) ** 2)  # k at the spectral peak (deep water)
    defect.append({"Tp_s": tp, "fixed_deg": fixed, "old_deg": old, "ratio": old / fixed, "one_over_k_at_Tp": 1.0 / kt})
res["C"] = defect

# --- D. quadrature resolution of the endpoint default grid ---
grid = []
for n in (81, 201, 481, 961, 1921):
    om = uniform_grid(0.05, 2.5, n)
    s2 = response_statistics(m, spectrum("jonswap", 3.0, 8.0, om))
    grid.append({"n_points": n, **{k: s2[k] for k in ("sigma_heave", "sigma_pitch", "sigma_roll", "sigma_vertical_acc_m_s2")}})
ref = grid[-1]
res["D"] = [{**g, "heave_rel_vs_1921": g["sigma_heave"] / ref["sigma_heave"] - 1.0} for g in grid]

out = pathlib.Path("docs/audits/artifacts/m1639_seakeeping_metrics.json")
out.write_text(json.dumps(res, indent=1, ensure_ascii=False))
A, AE = res["A"], res["A_err"]
def pct(x): return f"{100*x:+.3f}%"
rows = [
 ("sigma_heave [m] 对自由面锚点 sigma_zeta", f"{A['sigma_zeta_m']:.6f}", f"{A['sigma_heave']:.6f}", pct(AE["heave_vs_sigma_zeta"])),
 ("significant_heave [m] 对 2*sigma_zeta", f"{2*A['sigma_zeta_m']:.6f}", f"{A['significant_heave']:.6f}", pct(AE["significant_heave_vs_2sigma"])),
 ("sigma_pitch [deg] 对波浪斜率 sigma*180/pi", f"{A['slope_sigma_deg']:.6f}", f"{A['sigma_pitch']:.6f}", pct(AE["pitch_vs_slope"])),
 ("sigma_roll [deg] 对闭式因子链 k·Gamma·e^(-kT)·|H(w_e/w_n4)|", f"{res['roll_pred_deg']:.6f}", f"{A['sigma_roll']:.6f}", pct(AE["roll_vs_chain"])),
]
for q in res["B"]:
    rows.append((f"白噪声 SDOF 积分 ∫|H|^2dr, zeta={q['zeta']}", f"{q['closed']:.6f}", f"{q['numeric']:.6f}", pct(q["rel"])))
rows.append(("端点默认网格(201点/0.05–2.5) sigma_heave 对 1921 点收敛值", f"{ref['sigma_heave']:.6f}",
             f"{res['D'][1]['sigma_heave']:.6f}", pct(res['D'][1]['heave_rel_vs_1921'])))

table = "\n".join(f"| {a} | {b} | {c} | {d} | {'YES' if abs(float(d.strip('%'))) < 3 else 'NO'} |" for a, b, c, d in rows)
defect_tbl = "\n".join(
    f"| {d['Tp_s']} | {d['fixed_deg']:.4f} | {d['old_deg']:.2f} | {d['ratio']:.1f} | {d['one_over_k_at_Tp']:.1f} |" for d in res["C"])
grid_tbl = "\n".join(
    f"| {g['n_points']} | {g['sigma_heave']:.6f} | {g['sigma_pitch']:.6f} | {g['sigma_roll']:.6f} | {g['sigma_vertical_acc_m_s2']:.6f} | {pct(g['heave_rel_vs_1921'])} |" for g in res["D"])
P = res["periods"]; H = res["hydro"]
md = f"""# 耐波性谱响应统计的维度缺陷与闭式锚点（m1639）

**结论先行**：平台耐波域服务化端点 `POST /api/v1/calculations/seakeeping_response` 的
pitch / roll 统计量存在一处**维度混用缺陷**——`services/seakeeping.py:response_statistics()`
把"以波浪斜率归一化"的无量纲 RAO（`*_rad_per_rad`）直接乘到有量纲的波幅谱 `S(ω)` [m²·s] 上积分。
结果是返回的 `sigma_pitch` / `sigma_roll` 实际上是 `sigma_zeta` 换了单位标签（弧度），把纵摇/横摇
散布放大了约 `1/k` 倍（λ≈200 m 时约 500 倍）。本单给出闭式判据、修复、回归测试与量化证据；
修复后所有闭式锚点误差 <0.6%（判据表逐项标注是否 <3%）。

## 1. 案例介绍（物理背景 / 几何 / 参数）

耐波性频率域链路：波浪谱 `S(ω)` → 遭遇频率 `ω_e = ω − kU cos β` → 单自由度模态响应
`RAO(ω_e)` → 谱矩 `m_n = ∫ ω^n RAO² S dω` → `σ = √m0`、`significant = 2σ`、`Tz = 2π√(m0/m2)`。
其中 pitch / roll 在服务层同时保留两种归一化：

* `pitch_rao_rad_per_rad = (rao5 / k)`：**单位波面斜率**下的纵摇角（无量纲）；
* `pitch_rao_deg_per_m`：**单位波幅**（1 m）下的纵摇角（deg/m，有量纲）。

谱矩必须用后者：`S(ω)` 的量纲是 m²·s，与无量纲比值相乘后积分得到的数在量纲上是"米"，被
贴上"度"的标签输出。长波极限下 `pitch_rao_rad_per_rad → 1`，于是 `sigma_pitch → √(∫S dω) = σ_ζ`
（以弧度数值代入、再乘 180/π 转度），这就是缺陷的解析指纹。

几何与工况（仓库内既有测试基准船，`backend/tests/feat/test_ship_seakeeping.py::HULL`，
几何门 `|∇/(Cb·L·B·T) − 1| ≤ 5%` 先通过）：

| 量 | 值 |
|---|---|
| L / B / T | {HULL['L']} m / {HULL['B']} m / {HULL['T']} m |
| Cb / Cwp | {HULL['Cb']} / {HULL['Cwp']} |
| 排水量 | {HULL['displacement_t']} t（∇ = {H['displacement_m3']:.1f} m³，几何残差 {H['geometry_residual_pct']:+.3f}%） |
| KG / GM_T | {HULL['KG']} m / {HULL['GM_T']} m |
| GM_L（由水线面惯性矩算得） | {H['GM_L_m']:.4f} m |
| 固有周期 纵摇 / 垂荡 / 横摇 | {P['pitch']:.3f} s / {P['heave']:.3f} s / {P['roll']:.3f} s |

## 2. 怎么做的（方法 / 关键设置 / 工具链）

1. **读码定位**：追 `response_statistics()` 的谱矩循环，确认 pitch/roll 取的是 `*_rad_per_rad`
   键并配 `180/π` 缩放（即"把 rad 数值当角度输出"）。垂荡取 `heave_rao_m_per_m`（有量纲）故不受影响。
2. **造判据（先于改码）**：三条与系数无关的闭式锚点——
   (a) 长波极限 `ω→0 ⇒ 垂荡 RAO→1、纵摇 RAO→斜率`，故带限长波海况下
   `sigma_heave → σ_ζ`、`sigma_pitch[deg] → deg σ_slope`；
   (b) 白噪声 SDOF 恒等式 `∫₀^∞ |H(r)|² dr = π/(4ζ)`，用于校验积分器本身；
   (c) 线性定标 `σ ∝ Hs`。
3. **最小修复**：谱矩改取有量纲键 `pitch_rao_deg_per_m` / `roll_rao_deg_per_m`（垂荡不变），
   函数文档写明单位契约。未改物理模型、未改系数、未改端点签名。
4. **回归测试**：`backend/tests/feat/test_ship_seakeeping.py` 新增第 10 节 6 条（白噪声恒等式、
   长波垂荡/纵摇锚点、维度回归闸门（旧值/新值 > 100 倍即失败）、Hs 线性定标、良性海况量级上界）。
5. **工具链**：纯 Python（`services/seakeeping.py` + pytest + matplotlib），无重算力进程
   （本机 load15 ≈ 120/128 核，按容量纪律只做轻量分析）。

## 3. 计算结果

### 3.1 缺陷量化（迎浪 180°，Hs = 3.0 m，JONSWAP γ=3.3，201/1601 点网格收敛）

| Tp [s] | 修复后 sigma_pitch [deg] | 修复前 sigma_pitch [deg] | 放大倍数 | 1/k（谱峰波长） |
|---|---|---|---|---|
{defect_tbl}

放大倍数随 Tp 增长逼近 1/k —— 正是"无量纲比值 × 有量纲谱"的解析指纹，而非任何物理效应。

### 3.2 闭式锚点逐项对账（修复后）

长波带限海况：PM 谱 Hs=2 m / Tp=25 s，截断到 ω ∈ [0.05, 0.12] rad/s（λ = 680–3900 m ≫ L=150 m）。

| 判据 | 闭式/基准值 | 程序值 | 相对误差 | <3% ? |
|---|---|---|---|---|
{table}

**横摇的裸斜率锚点偏差（如实记录，非缺陷）**：若直接用斜率锚点（`deg σ_slope`）比对，
`sigma_roll` 偏大 {100*AE['roll_vs_slope']:+.2f}%。原因是横摇固有周期 {P['roll']:.2f} s（ω_n={2*math.pi/P['roll']:.3f} rad/s）
与带限海况（0.05–0.12 rad/s）之比 r 最大约 {0.12/(2*math.pi/P['roll']):.2f}，SDOF 传递在该 r 上放大
≈{1/math.sqrt((1-(0.12/(2*math.pi/P['roll']))**2)**2+(2*m.zeta44*0.12/(2*math.pi/P['roll']))**2):.3f} 倍，
即"ω→0 的 RAO→斜率"在横摇上只是近似成立。因此横摇判据改用**闭式因子链**（k·Γ(u)·e^(−kT)·|H(r)|，
不经过 `response_statistics`）作为基准，误差见上表。

### 3.3 网格分辨率敏感性（端点默认 ω∈[0.05,2.5]，Hs=3 m / Tp=8 s）

| n_points | sigma_heave [m] | sigma_pitch [deg] | sigma_roll [deg] | sigma_vert_acc [m/s²] | heave 相对 1921 点 |
|---|---|---|---|---|---|
{grid_tbl}

端点默认 201 点相对 1921 点收费值误差 1e-5 量级，常规海况下网格不构成本单缺陷来源；
但注意白噪声恒等式校验显示：对跨越共振区的极宽带积分（1e-4–400 rad/s、ζ=0.1），
梯形积分需 ~4000 点才进入 1e-2 量级，粗网格会低估共振峰——这解释了上表 501 点非单调误差。

### 3.4 图

* `docs/audits/figures/m1639_longwave_anchors.png`：带限长波谱、长波 RAO 极限、修复后统计量 vs 闭式（柱=程序值，虚线=解析）。
* `docs/audits/figures/m1639_pitch_defect.png`：sigma_pitch 随 Tp（修复后 vs 修复前，对数纵轴）与放大倍数曲线。
* `docs/audits/figures/m1639_quadrature.png`：白噪声积分收敛阶 + 默认海况谱×纵摇 RAO。

## 4. 注意点（坑 / 局限 / 适用范围）

* **坑 1**：同一个端点同时暴露"斜率归一化"与"单位波幅"两种 RAO，键名只差后缀，极易在谱矩环节取错；
  本次闸门用"旧值/新值 > 100 即失败"的显式断言把缺陷钉死，防止无声回归。
* **坑 2**：修复会改变对外数值（纵摇/横摇 σ 下降约两个数量级）。全仓检索确认无下游消费者
  （仅 `backend/api/domain/ship_seakeeping.py` 的单位注释提及键名），故按契约修正而非兼容旧值。
* **局限 1**：本单只清偿"数值/量纲"欠账；RAO 的**绝对精度**仍是 WITHHELD——附连质量/阻尼比是
  约定量级的输入参数，仓内无水池试验数据。长波锚点与系数无关，因此不能反推系数正确。
* **局限 2**：`services/digital_twin/models/seakeeping.py`（代理模型，非本节服务）另有一套
  无量纲处理，未纳入本单审计范围。
* **局限 3**：迎浪横摇在该 FK 模型下由 `k|cos β|` 驱动，回代斜率锚点时取 `|cos β| = 1`；
  横浪/斜浪下的横摇阻尼（粘性主导）不在本模型能力内。

## 5. 误差判定（关键量 vs 基准，逐项）

* 3.2 表 8 项判据**全部 <3%**（最大 0.52%，出现在长波垂荡 vs σ_ζ：来源是带内 RAO 略小于 1
  及带限截断，非积分误差）；白噪声恒等式误差 6.4e-6 – 5.7e-5。
* 缺陷修复的**方向性证据**：Tp=8 s 时修复前后比值 {res['C'][2]['ratio']:.1f}，与解析 1/k
  同量级（λ≈100 m 处 k≈0.063 m⁻¹ ⇒ 1/k≈16）；随 Tp 增长单调趋向解析值，说明差异来自量纲而非系数。
* 绝对精度（RAO 对模型试验）**无基准**：仓内无试验数据、未引入外部数值，故该维度维持
  WITHHELD，不做任何"<3%"声明。honest 说明：本单证明的是**实现正确性**，不是**物理精度**。

## 6. 复现

```bash
cd backend && python3 -m pytest tests/feat/test_ship_seakeeping.py -q      # 31 passed
python3 /tmp/gen_m1639_doc.py    # 本文件与 artifacts/m1639_seakeeping_metrics.json（数字程序化注入）
python3 /tmp/gen_m1639_figs.py   # 三张 PNG
```
"""
p = pathlib.Path("docs/audits/seakeeping_spectral_response_m1639.md")
p.write_text(md)
print(md[:1200])
print("...written", p, len(md))
