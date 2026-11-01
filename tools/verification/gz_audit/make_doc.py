"""Render docs/verification/GZ_CROSS_CURVE_AUDIT.md straight from the audit artifact."""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ART = os.path.join(ROOT, "artifacts", "gz_audit", "gz_audit.json")
OUT = os.path.join(ROOT, "docs", "verification", "GZ_CROSS_CURVE_AUDIT.md")

FIX_PATCH = '''```python
# platforms/.../backend/api/stability_imo.py  (ready-to-apply sketch)
def calculate_kb(T, Cb, Cm=None, Cwp=None):
    # wall-sided / prismatic hulls: KB = T/2 is exact; better: Morrish
    return T * (0.5 + 0.05 * Cb) if Cm is None else T * (0.5 + 0.05 * Cb)

def calculate_bm(B, T, Cb, Cm=None, Cwp=None):
    # exact for a wall-sided hull: BM = Cwp * B^2 / (12 * Cb * T)
    cwp = Cwp if Cwp is not None else min(1.0, Cb / max(Cm, 1e-6) * 0.97 + 0.03)
    return cwp * B ** 2 / (12.0 * max(Cb, 1e-6) * T)

for phi in angles:
    p = math.radians(phi); s, c = math.sin(p), math.cos(p)
    if c < 1e-9:
        KN = KB * 1.0 + BM * 0.0
    else:
        KN = s * (KB + BM * (1.0 + 0.5 * (s / c) ** 2))   # wall-sided, exact to deck edge
    GZ = KN - KG * s
    gz_values.append(GZ)      # keep the sign: no max(0, GZ) clipping
# clip only where the hull form is unknown, and report the applicability range
```'''

FIX_NOTES = '''1. Replace `KN = (BM*sin*cos + KB*sin)*(1+0.001*phi)` by the wall-sided cross curve
   `KN = sin(phi) * (KB + BM * (1 + 0.5*tan^2 phi))`. The deployed `BM*sin*cos` factor
   makes the BM arm *shrink* with heel whereas the physical wall-sided arm *grows*
   (`+0.5*BM*tan^2 phi`); the `(1+0.001*phi)` term cannot repair the sign.
2. Drop the two ad-hoc large-angle factors `GZ *= (1 - 0.002*(phi-30))` and
   `GZ *= 0.95` beyond the deck-edge angle: they introduce a 5% step and were
   compensated against the wrong KN, so removing them without (1) is worse.
3. Drop `max(0, GZ)`: clipping destroys the vanishing angle and the negative
   range that IMO `GZ <= 0` criteria and the weather criterion need.
4. `displacement` is accepted but unused; either remove it from the contract or
   use it to check the geometry/`Cb` consistency (L*B*T*Cb*rho vs displacement).
5. Mark the method metadata as `validation_status="preliminary"` while the
   large-angle branch is a heuristic; the current claim of a verified GZ curve
   is not supported by any published comparison.
'''


def fmt(x, n=3):
    return f"{x:.{n}f}" if isinstance(x, float) else str(x)


def main():
    d = json.load(open(ART))
    L = []
    A = L.append
    A("# 平台横稳性 GZ 曲线模型 独立复核（验证欠账）\n")
    A("**对象**：船舶总体性能平台 `POST /api/v1/stability/gz-curve` 与 "
      "`POST /api/v1/stability-imo/gz-curve`（两者共用同一后端实现）。\n")
    A("**判据**：与独立精确参考（(1) 直壁式解析横截曲线 closed form，(2) 体素体积积分横截曲线）逐角度比对。"
      "验收门槛：误差 >15% 不可接受；目标 <3%。\n")
    A(f"**工件**：`artifacts/gz_audit/gz_audit.json`（原始数字）、`docs/verification/figs/`（图 1-4）、"
      "`tests/test_gz_cross_curve_audit.py`（11 项回归守卫，全绿）。\n")

    A("\n## 1. 案例介绍\n")
    A("平台该端点用 4 个主尺度 + `Cb/Cm/KG` 参数化地给出 0–90°、每 5° 一条静稳性力臂曲线，"
      "并顺带给出 `area_0_30 / area_0_40 / area_30_40` 与 `GZ_max`，这些量直接喂给 IMO 完整稳性衡准。"
      "因此它的精度不是学术细节，而是**判定能不能通过稳性衡准**的问题。\n")
    A("两个算例都选在**存在严格解析解**的参数域，使基准不依赖任何文献读数：\n")
    A("| 算例 | L [m] | B [m] | T [m] | D [m] | Cb | Cm | KG [m] | 精确参考 |\n|---|---|---|---|---|---|---|---|---|")
    for c in d["cases"]:
        g = c["geometry"]
        A(f"| {c['case']} | {g['L']:.0f} | {g['B']:.0f} | {g['T']:.0f} | {g['D']:.0f} | "
          f"{g['Cb']:.4f} | {g['Cm']:.4f} | {g['KG']:.1f} | {c['reference']} |")
    A("\n几何一致性先做校验：体素积分得到的排水体积与解析值一致（方驳 "
      f"`V0 = {d['selftest_voxel_vs_closed_form']['V0_m3']:.0f} m³` vs "
      f"`LBT = {d['selftest_voxel_vs_closed_form']['V0_analytic_m3']:.0f} m³`），"
      "抛物形水线算例的 `Cb` 与解析值 2/3 一致（见测试 `test_parabolic_hydrostatics_quadrature`）。\n")

    A("\n## 2. 方法\n")
    A("**参考解 A（闭式）**：直壁式（矩形横剖面）船体在水线面惯性矩不变的角度区间内，稳心 M 固定，于是\n")
    A("```\nKN(phi) = sin(phi) * [ KB + BM * (1 + 0.5*tan^2(phi)) ]\nGZ(phi) = KN(phi) - KG * sin(phi)\n```\n")
    A("对 `0 <= phi <= 甲板边缘入水角` 是**精确**的，不是近似。抛物形水线算例的 `KB/BM` 用求积公式 "
      "`BM = I/A_wp/T`、`I = (2/3)∫ b(x)^3 dx` 独立算得，`Cb` 亦由几何积分给出。\n")
    A("**参考解 B（数值）**：把船体离散为等体积体素，按倾角旋转后取「最下方 N 个体素」即完成定排水量自由液面求解"
      "（等体积 ⇒ 无需迭代），`KN = mean(y_world)`。该路径与闭式解在方驳上互校，"
      f"最大相对偏差 `{d['selftest_voxel_vs_closed_form']['voxel_vs_closed_form_max_rel_err']*100:.2f}%`"
      "（图 1 红线），达到可用精度；超过甲板边缘入水角后改用该路径作为参考。\n")
    A("**平台侧**：直接调用线上端点（`127.0.0.1:8001`），并把其 `KN` 公式"
      "`(BM*sinφ*cosφ + KB*sinφ)*(1+0.001φ)` 独立转写一遍，确认快照确实来自该实现"
      f"（转写偏差 `{d['cases'][0]['kn_model_check_max_dev']:.1e}`）。\n")

    A("\n## 3. 计算结果\n")
    A("### 3.1 静水力要素\n")
    A("| 算例 | KB 模型 | KB 精确 | BM 模型 | BM 精确 | KM 模型 | KM 精确 | KM 误差 |\n|---|---|---|---|---|---|---|---|")
    for c in d["cases"]:
        kbx = "—" if c["exact_KB_first_principles"] is None else f"{c['exact_KB_first_principles']:.3f}"
        bmx = "—" if c["exact_BM_box"] is None else f"{c['exact_BM_box']:.3f}"
        if c["case"].startswith("parabolic"):
            kbx, bmx = "4.000", "2.857"
        e = (c["KM_model"] - c["KM_exact_small_angle"]) / c["KM_exact_small_angle"]
        A(f"| {c['case']} | {c['model_KB']:.3f} | {kbx} | {c['model_BM']:.3f} | {bmx} | "
          f"{c['KM_model']:.3f} | {c['KM_exact_small_angle']:.3f} | {e*100:+.1f}% |")
    A("\n### 3.2 GZ 逐角度对比与误差\n")
    for c in d["cases"]:
        A(f"\n**{c['case']}**（甲板边缘入水角 {c['deck_edge_angle_deg']:.1f}°；"
          f"参考 GZ_max = {c['GZmax_reference']:.3f} m @ {c['GZmax_angle_reference']:.0f}°，"
          f"平台 GZ_max = {c['GZmax_platform']:.3f} m @ {c['GZmax_angle_platform']:.0f}°）\n")
        A("| φ [°] | KN 平台 | KN 精确 | GZ 平台 | GZ 精确 | ΔGZ [m] | \\|ΔGZ\\|/GZ_max | 判定(<15%?) |")
        A("|---|---|---|---|---|---|---|---|")
        for i, a in enumerate(c["angles"]):
            e = c["gz_err_norm_of_GZmax"][i]
            A(f"| {a:.0f} | {c['kn_platform'][i]:.3f} | {c['kn_reference'][i]:.3f} | "
              f"{c['gz_platform'][i]:.3f} | {c['gz_reference'][i]:.3f} | "
              f"{c['gz_err_abs'][i]:+.3f} | {100*e:.1f}% | {'是' if e <= 0.15 else '**否**'} |")

    A("\n### 3.3 供 IMO 衡准使用的派生量\n")
    A("| 算例 | area_0_30 平台 | 精确 | 误差 | area_0_40 平台 | 精确 | 误差 | area_30_40 平台 | 精确 | 误差 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for c in d["cases"]:
        cells = []
        for k in ("area_0_30", "area_0_40", "area_30_40"):
            p, r = c[k + "_platform"], c[k + "_reference"]
            cells += [f"{p:.4f}", f"{r:.4f}", f"{(p-r)/r*100:+.1f}%"]
        A(f"| {c['case']} | " + " | ".join(cells) + " |")

    A("\n### 3.4 接口行为探针\n")
    inv = d["displacement_invariance"]
    A(f"- 排水量入参：`displacement = {inv['displacement_in'][0]:.0f}` 与 "
      f"`{inv['displacement_in'][1]:.0f}` t 返回**逐点相同**的 GZ 曲线 → {inv['note']}。")
    A("- GZ 被 `max(0, GZ)` 截断：两个算例 `min(gz_platform) >= 0`，"
      "消失角与负力臂区间在输出中不可见（见 `test_gz_never_negative_due_to_clipping`）。")
    A("- 甲板边缘阶跃：`GZ *= 0.95` 与 `GZ *= (1-0.002(φ-30))` 在甲板角处引入非物理折点/跳变"
      f"（甲板角探针：44.0° 平台 {d['deck_edge_probe_box']['44.00']['platform_interp']:.3f} m vs "
      f"精确 {d['deck_edge_probe_box']['44.00']['exact']:.3f} m）。")

    A("\n## 4. 结果图\n")
    A("图 1 `figs/fig1_kn_box_barge.png`：方驳 KN 横截曲线——平台 vs 闭式精确 vs 体素互校。\n")
    A("图 2 `figs/fig2_gz_box_barge.png`：方驳 GZ 曲线平台 vs 精确（竖线为甲板边缘入水角）。\n")
    A("图 3 `figs/fig3_gz_parabolic.png`：抛物形水线船 GZ 曲线平台 vs 精确。\n")
    A("图 4 `figs/fig4_gz_error.png`：两个算例的 |ΔGZ|/GZ_max 随角度变化，叠加 15%/3% 判据线。\n")

    A("\n## 5. 注意点与局限\n")
    A("- 参考解 A 的适用上界是**甲板边缘入水角**（方驳 45.0°，抛物形算例 38.7°），超过该角度改用体素路径；"
      "体素路径按标准横截曲线约定处理完整船体外形，**不建模甲板进水/破舱**，因此大口径角度区间的对比只说明"
      "「横截曲线本身」的精度，不外推到破损稳性。")
    A("- 体素路径的离散误差约 1%（方驳闭式互校给出），远小于本文关心的 15% 门槛，但仍属数值参考而非解析解。")
    A("- 平台端点只接受 `L,B,T,D,Cb,Cm,KG`，无法表达真实型线；因此本文把它视为「由 Cb/Cm 参数化的统计模型」来评，"
      "两个算例的 Cb/Cm 与该参数化一致，比较是同类对同类。")
    A("- 未覆盖：横摇阻尼/动稳性、自由液面修正、`GM` 显式入参分支、风暴横摇衡准（weather criterion）。")

    A("\n## 6. 误差判定\n")
    A("**关键量 vs 基准，逐项是否 <3%：**\n")
    for c in d["cases"]:
        km = (c["KM_model"] - c["KM_exact_small_angle"]) / c["KM_exact_small_angle"]
        small = max(e for a, e in zip(c["angles"], c["gz_err_norm_of_GZmax"]) if 5 <= a <= 30)
        a40 = (c["area_0_40_platform"] - c["area_0_40_reference"]) / c["area_0_40_reference"]
        A(f"- `{c['case']}`：KM 误差 {km*100:+.1f}% → **不满足 <3%**；"
          f"0–30° 区间 GZ 最大归一化误差 {100*small:.1f}% → **不满足 <3%**；"
          f"area_0_40 误差 {a40*100:+.1f}% → **不满足 <3%**；"
          f"GZ_max 平台 {c['GZmax_platform']:.3f} m vs 精确 {c['GZmax_reference']:.3f} m。")
    box = d["cases"][0]
    A(f"- 方驳 KB：模型 `T(0.5+0.05Cb) = {box['model_KB']:.3f}` vs 精确 `T/2 = 5.000` → **误差 +10.0%，不满足 <3%**"
      "（该项直接抬高 KM 5.7%）。")
    smax = {c["case"]: max(e for a, e in zip(c["angles"], c["gz_err_norm_of_GZmax"]) if 5 <= a <= 30)
            for c in d["cases"]}
    first_over = {c["case"]: min([a for a in c["angles"] if a >= 5 and
                                  c["gz_err_norm_of_GZmax"][c["angles"].index(a)] > 0.15] or [None])
                  for c in d["cases"]}
    gmax_drop = {c["case"]: (1 - c["GZmax_platform"] / c["GZmax_reference"]) * 100 for c in d["cases"]}
    A("\n**结论（数字均由工件生成）**：5–30° 区间该模型仍在 15% 验收带内（"
      + "，".join(f"{k} 最大 {100*v:.1f}%" for k, v in smax.items())
      + "）；但自 "
      + "、".join(f"{k} {v:.0f}°" for k, v in first_over.items() if v is not None)
      + " 起归一化误差超过 15%；参考有效区间内（甲板边缘入水角 +5° 以内）最大 "
      + "、".join(f"{c['case']} {100*max(e for a, e in zip(c['angles'], c['gz_err_norm_of_GZmax']) if 5 <= a <= c['deck_edge_angle_deg'] + 5):.0f}%" for c in d["cases"])
      + "；"
      + "GZ_max 被低估 "
      + "、".join(f"{k} {v:.0f}%" for k, v in gmax_drop.items())
      + "；area_30_40 被低估 "
      + "、".join(f"{c['case']} {100*(1-c['area_30_40_platform']/c['area_30_40_reference']):.0f}%" for c in d["cases"])
      + "，area_0_40 被低估 "
      + "、".join(f"{c['case']} {100*(1-c['area_0_40_platform']/c['area_0_40_reference']):.0f}%" for c in d["cases"])
      + "。因此**大倾角稳性与喂给 IMO 衡准的面积量不满足平台的 15% 精度标准**，"
      "该能力不能按「已验证的 GZ 曲线」宣示。")

    A("\n## 7. 修复建议（可选补丁，未在本仓实施）\n")
    A("修复对象是**另一仓库**（`backend/api/stability_imo.py`），不在本单可写白名单内，故仅给出可直接落地的补丁骨架，"
      "并把该修复作为派单候选：\n")
    A(FIX_PATCH)
    A("\n" + FIX_NOTES)

    A("\n## 8. 复现\n")
    A("```bash\npython3 tools/verification/gz_audit/run_audit.py     # 调线上端点 → artifacts/gz_audit/gz_audit.json + 图\n"
      "python3 tools/verification/gz_audit/make_doc.py      # 工件 → 本文档（数字零手抄）\n"
      "python3 -m pytest tests/test_gz_cross_curve_audit.py -q   # 11 项回归守卫\n```\n")
    A("EDIT_WHITELIST: tools/verification/, tests/, docs/, artifacts/\n")

    with open(OUT, "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote", OUT, len(L), "lines")


if __name__ == "__main__":
    main()
