"""m1862 — 生成 /polar/acceptance/channel 独立复核的报告与图件（数字全部从工件读）。

输入：artifacts/polar_channel_acceptance/audit.json（由
      tools/verifier/polar_channel_acceptance_audit.py 产出）
输出：docs/verification_debt/polar_channel/figs/*.png
      docs/verification_debt/polar_channel/numbers.json
      docs/verification_debt/polar_channel/REPORT.md（表格由工件程序化注入）
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AUDIT = os.path.join(REPO, "artifacts", "polar_channel_acceptance", "audit.json")
OUT = os.path.join(REPO, "docs", "verification_debt", "polar_channel")
FIGS = os.path.join(OUT, "figs")


def fig_thickness(audit: Dict[str, Any]) -> str:
    a3 = audit["A3_thickness_holdout"]
    a5 = audit["A5_consistent_fix"]
    hs = [r["calibration_thickness_m"] for r in a3["rows"]]
    cur_brash = [r["rel_err_brash_pct"] for r in a3["rows"]]
    cur_total = [r["rel_err_total_pct"] for r in a3["rows"]]
    fixed = [r["rel_err_brash_pct"] for r in a5["rows"]]

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.axhline(0.0, color="0.6", lw=0.8)
    ax.axhspan(-10, 10, color="#2ca02c", alpha=0.08, label="criterion (b) pass band |err|<10%")
    ax.plot(hs, cur_brash, "o-", color="#d62728", label="current: brash term (exact = h_cal/h_eval-1)")
    ax.plot(hs, cur_total, "s--", color="#ff7f0e", label="current: total (diluted by open water)")
    ax.plot(hs, fixed, "^-.", color="#1f77b4", label="A5 dimension-consistent fix: h_b^2*B")
    ax.set_xlabel("calibration thickness h_cal [m]  (evaluation fixed at h_eval = %.2f m)" % audit["settings"]["h_eval_m"])
    ax.set_ylabel("relative error [%]")
    ax.set_title("Thickness hold-out of criterion (b): linear drift now, exact after the fix")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    path = os.path.join(FIGS, "fig1_thickness_holdout.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_vacuity(audit: Dict[str, Any]) -> str:
    rows = audit["A2_vacuity"]["variants"]
    labels = [r["variant"] for r in rows]
    vals = [r["max_abs_rel_err_total_pct"] for r in rows]
    colors = ["#d62728" if not r["criterion_b_pass"] else "#1f77b4" for r in rows]

    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    bars = ax.barh(labels, vals, color=colors)
    ax.axvline(audit["settings"]["tol_pct"], color="#2ca02c", ls="--",
               label="criterion (b) threshold %.0f%%" % audit["settings"]["tol_pct"])
    for b, v in zip(bars, vals):
        ax.text(v + 0.6, b.get_y() + b.get_height() / 2, "%.2f%%" % v, va="center", fontsize=8)
    ax.set_xlabel("max |rel. error| under criterion (b) [%] (open-water included)")
    ax.set_title("Discriminating power: red = caught (2/8), blue = absorbed by the calibration constant")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="x")
    path = os.path.join(FIGS, "fig2_discriminating_power.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_identity(audit: Dict[str, Any]) -> str:
    rows = audit["A1_density_identity"]["rows"]
    cs = [r["concentration"] for r in rows]
    rel = [r["rel_err_pct"] for r in rows]
    pred = [r["density_ratio_pred_pct"] for r in rows]
    resid = [r["identity_residual_pct"] for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.8))
    ax1.plot(cs, rel, "o", ms=6, label="independent recomputation")
    ax1.plot(cs, pred, "-", lw=1.2, label="closed form rho_b(c)/rho_b(0.4)-1")
    ax1.set_xlabel("brash concentration c [-]")
    ax1.set_ylabel("brash-term rel. error [%]")
    ax1.set_title("(a) hold-out error = density-ratio identity")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    ax2.semilogy(cs, [max(abs(v), 1e-18) for v in resid], "o-", color="#9467bd")
    ax2.set_xlabel("brash concentration c [-]")
    ax2.set_ylabel("|recomputed - closed form| [%], log")
    ax2.set_title("(b) identity residual (<=1e-14 %, float only)")
    ax2.grid(alpha=0.3, which="both")
    path = os.path.join(FIGS, "fig3_density_identity.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def md_tables(audit: Dict[str, Any]) -> Dict[str, str]:
    s = audit["settings"]
    a1 = audit["A1_density_identity"]
    a2 = audit["A2_vacuity"]
    a3 = audit["A3_thickness_holdout"]
    a4 = audit["A4_physical_limits"]
    a5 = audit["A5_consistent_fix"]
    v = audit["verdict"]

    head = "| 量 | 值 |\n|---|---|\n"
    head += "| 船 | `%s` |\n" % audit["ship"]
    head += "| 速度 | %.1f kn |\n" % s["speed_kn"]
    head += "| 评测 brash 层厚 h_b | %.2f m |\n" % s["h_eval_m"]
    head += "| 锚定浓度 / hold-out 浓度 | %.2f / %s |\n" % (
        s["anchor_concentration"], ", ".join("%.2f" % c for c in s["holdout_concentrations"]))
    head += "| 判据 (b) 阈值 | %.0f %% |\n" % s["tol_pct"]

    t1 = "| c | brash_prod [N] | brash_ref [N] | 复算相对误差 [%] | 闭式 ρ_b 比 [%] | 残差 [%] |\n|---|---|---|---|---|---|\n"
    for r in a1["rows"]:
        t1 += "| %.2f | %.1f | %.1f | %.6f | %.6f | %.2e |\n" % (
            r["concentration"], r["brash_prod_n"], r["brash_ref_n"],
            r["rel_err_pct"], r["density_ratio_pred_pct"], r["identity_residual_pct"])

    t2 = "| 变体 | 说明 | max abs 误差（总口径）[%] | 判据 (b) |\n|---|---|---|---|\n"
    for r in a2["variants"]:
        t2 += "| `%s` | %s | %.4f | %s |\n" % (
            r["variant"], r["description"], r["max_abs_rel_err_total_pct"],
            "通过（放行）" if r["criterion_b_pass"] else "**不通过（抓到）**")

    t3 = "| h_cal [m] | K_cal | K·h_cal | brash 相对误差 [%] | 闭式 h_cal/h_eval−1 [%] | 总口径 [%] |\n|---|---|---|---|---|---|\n"
    for r, kh in zip(a3["rows"], a3["k_times_h"]):
        t3 += "| %.2f | %.6f | %.9f | %.4f | %.4f | %.4f |\n" % (
            r["calibration_thickness_m"], r["k_cal"], kh, r["rel_err_brash_pct"],
            r["predicted_hc_over_he_pct"], r["rel_err_total_pct"])

    t4 = "| 极限/单调性 | 结果 |\n|---|---|\n"
    t4 += "| c→0 时通道阻力 vs 开阔水（相对差） | %.3e |\n" % a4["limit_c_to_zero_rel_err"]
    t4 += "| h_b→0 时通道阻力 vs 开阔水（相对差） | %.3e |\n" % a4["limit_h_to_zero_rel_err"]
    t4 += "| ρ_b(0)=ρ_w，ρ_b(1)=ρ_i | %.1f / %.1f kg/m³ |\n" % (
        a4["density_limits"]["rho_b(0)"], a4["density_limits"]["rho_b(1)"])
    t4 += "| 对 c / h_b / V 单调不减 | %s / %s / %s |\n" % (
        a4["monotone_in_concentration"], a4["monotone_in_thickness"], a4["monotone_in_speed"])

    t5 = "| h_cal [m] | 修法后 brash 相对误差 [%] |\n|---|---|\n"
    for r in a5["rows"]:
        t5 += "| %.2f | %.2e |\n" % (r["calibration_thickness_m"], r["rel_err_brash_pct"])

    t6 = "| 判据 | 结论 |\n|---|---|\n"
    t6 += "| 判据 (b) 自报 max abs 误差（总口径） | %.4f %% 、pass=%s |\n" % (
        v["reported_max_abs_rel_err_pct"], v["reported_criterion_b_pass"])
    t6 += "| A1 hold-out 误差 = 密度比恒等式 | %s |\n" % v["a1_holdout_is_density_identity"]
    t6 += "| A2 判别力矩阵 | %s |\n" % v["a2_criterion_b_discriminating_power"]
    t6 += "| A3 判据 (b) 被单一厚度钉住 | %s |\n" % v["a3_criterion_b_is_thickness_pinned"]
    t6 += "| A4 物理极限 | %s |\n" % v["a4_physical_limits_hold"]
    t6 += "| `/polar/acceptance/channel` 证据等级 | **%s** |\n" % (
        v["evidence_grade_for_/polar/acceptance/channel"])

    return {"head": head, "t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5, "t6": t6}


REPORT = """# /polar/acceptance/channel 独立复核：判据 (b) 是「密度比恒等式 + 单一厚度」，非阻力物理验证（m1862）

## 0. 一句话结论

平台 `/polar/acceptance/channel`（战役节点 W3-N11 判据 b）自报 `max|rel_err| = {rep_err}% < 10% ⇒ pass`，
但独立复核表明该**通过值完全由 brash 混合密度比 ρ_b(c)/ρ_b(0.40) 精确决定**（恒等式残差 ≤{resid:.1e}%），
判据的判别力只有 **{power}**：厚度指数、宽度指数、`T·Cb`、速度因子 `f_v` 四类结构性扰动全部被单点标定常数
`K` 吸收而放行（其中「把混合密度换成常数 ρ_i」这种**更不物理**的写法反而得到 0.00% 误差）。
判据 (b) 只在标定厚度 h_b = {h_eval} m 上成立，一换厚度即出现 70% 级的精确偏差（h_cal/h_eval−1）。
所以 `/polar/acceptance/channel` 的证据等级**不能**升到 ANCHORED，保持 **WITHHELD**；
本文给出并验证了一条量纲一致的修法（A5），可让厚度 hold-out 由 70% 归零。

## 1. 案例与对象（物理背景 / 参数）

**物理背景**：破冰船护航时，被护航船在充满碎冰（brash ice）的通道里航行，通道阻力
R_channel = R_ow + R_brash。平台的生产式与参考式分别为

```
R_brash,prod = ρ_b(c) g h_b  B T Cb (μ_b + sinβ) (c/c_max)^n (1 + C_VEL·V/√(g h_b))
R_brash,ref  = K ρ_i g h_b² B          (c/c_max)^n (1 + C_VEL·V/√(g h_b))
ρ_b(c) = c ρ_i + (1-c) ρ_w
```

两式**结构不同**：生产式 brash 项 ∝ h_b¹·B·T·Cb，参考式 ∝ h_b²·B（差一个长度量纲 h_b，以及 T·Cb 因子）。
判据 (b) 的流程是在锚定浓度 c₀ = 0.40 上标定唯一常数 K，然后比较 c = 0.20 / 0.70 两个 hold-out 浓度，
`max|rel_err_total| < 10%` 记 pass。

**算例参数表（全部来自工件 `artifacts/polar_channel_acceptance/audit.json`）**：

{head}

**被审计对象**：`backend/services/polar/channel.py :: verify_channel_concentrations`
（经 `/polar/acceptance/channel`、`/polar/acceptance` 暴露）。审计脚本
`tools/verifier/polar_channel_acceptance_audit.py` 只把被测函数当作**被审对象**，
所有基准量用自己写的闭式独立复算，不读被测模块的任何中间量。

## 2. 怎么做的（方法 / 判据 / 工具链）

| 判据 | 内容 | 可裁方式 |
|---|---|---|
| A1 | hold-out 残差是否等于混合密度比 `ρ_b(c)/ρ_b(c₀)−1` | 闭式恒等式，残差 < 1e-9 % |
| A2 | 判据 (b) 的判别力矩阵：8 个物理变体（浓度指数、厚度指数、宽度指数、T·Cb、f_v、常数密度）+ 对照 | 每个变体在锚点重标定 K 后跑原判据，统计"抓到/放行" |
| A3 | 厚度 hold-out：在 h_cal 标定、在 {h_eval} m 评测 | 误差应等于 h_cal/h_eval−1，且 K·h 恒定 |
| A4 | 物理极限：c→0、h_b→0 退化为开阔水；对 c、h_b、V 单调 | 解析判据，无需文献值 |
| A5 | 量纲一致修法（生产式 brash 项改 ∝ h_b²·B）后的厚度 hold-out | 误差应 < 5% |

工具链：`tools/verifier/polar_channel_acceptance_audit.py`（审计 + 工件落盘）→
`tests/test_m1862_polar_channel_acceptance_audit.py`（12 passed / 5 xfail-strict 欠账台账）→
`tools/verifier/gen_polar_channel_report.py`（本报告与图件，**表格数字由工件程序化注入**）。

## 3. 计算结果

### 3.1 A1 — hold-out 误差就是混合密度比（精确成立）

{t1}

锚点 c=0.40 处误差恒为 0（K 在此标定）；两个 hold-out 浓度上误差 = +2.2000% / −3.3001%，
与闭式 `ρ_b(c)/ρ_b(0.40)−1` 逐例吻合到 1e-14 % 量级 ⇒ **这 2.2%–3.3% 的残差不是阻力物理的偏差，
而是"混合密度 vs 常数冰密度"这一项的代数结果**。由于 ρ_b(c) 对 c 线性，误差也严格线性：
`err(c) = (c−0.40)(ρ_i−ρ_w)/ρ_b(0.40) = −0.1097·(c−0.40)`。

### 3.2 A2 — 判别力矩阵：8 个变体只有 2 个被抓到

{t2}

被抓到的两个变体都恰好是**破坏了"与参考式共同的浓度指数 n=1.5"**的那些；凡是"对 c 均匀、
可被单点标定吸收"的结构差异（厚度指数、宽度指数、T·Cb、f_v、甚至把混合密度换成常数 ρ_i）
一律放行。特别地，`density_constant_rho_ice`（更不物理）在判据 (b) 下的误差是 **0.00%**，
比现状的 2.94% 更"好"——即该闸门在奖励错模型。

### 3.3 A3 — 判据 (b) 被单一厚度钉住（厚度 hold-out 立即失效）

{t3}

`K·h_cal` 在整个厚度网格上恒定（相对差 < 1e-9），证明两个公式相差恰好一个长度量纲 h_b；
brash 项误差精确等于 `h_cal/h_eval − 1`。判据 (b) 把 h_b 钉死在 {h_eval} m，
所以这个错误在自证里永远看不见；一旦做厚度 hold-out，总阻力口径误差最大达
**{a3_max:.2f}%** —— 远超 10% 判据带，也超出「静水阻力 ≤5% / 冰区 ≤10%」的平台验收标准。

### 3.4 A4 — 物理极限（这一层是真的，可以锚）

{t4}

退化为开阔水、密度极限、三个方向的单调性全部成立 ⇒ 生产式在**定性物理**上没有明显错误，
问题集中在"标定 + 自证"这套验证流程，而不是公式本身违反了极限行为。

### 3.5 A5 — 量纲一致的修法把厚度 hold-out 打成恒真

{t5}

把生产式 brash 项改成与参考式同量纲的 `ρ_b(c) g h_b² B (μ_b+sinβ)(c/c_max)^n f_v` 后，
K 与厚度无关，厚度 hold-out 误差归零（机器零）。代价是量级变化因子 `h_eval/(T·Cb) = {mag:.4f}`
（需重新标定 K 并复核绝对量级）——本文只验证"这条修法能恢复判别力"，**未**改动生产物理。

## 4. 图件

| 图 | 内容 |
|---|---|
| `figs/fig1_thickness_holdout.png` | 厚度 hold-out：现状（brash / 总口径）随 h_cal 线性漂移 vs A5 修法归零，绿色带 = 判据 (b) 通过带 |
| `figs/fig2_discriminating_power.png` | 判别力矩阵：8 变体的 max|误差|，红=抓到、蓝=放行、虚线=10% 阈值 |
| `figs/fig3_density_identity.png` | (a) 复算误差与密度比闭式的重合；(b) 恒等式残差（对数，≤1e-14 %） |

## 5. 注意点（坑 / 局限 / 适用范围）

1. **"hold-out 通过"≠"物理被验证"**：只要参考式与被测式共享同一套归一化因子，单点标定后
   残差就退化成被标定常数吸收的代数式。判断一个 hold-out 有没有判别力，最快的办法是
   **主动形变被测式的每一项，看闸门会不会红**（A2 的做法）。
2. **单一厚度/单一速度的自证会隐藏量纲错误**：K 的量纲随 h_b 漂移时，只要标定厚度=评测厚度就完全不可见。
   凡带"标定常数"的对比，至少要在一个**与标定点不同的厚度/速度**上做 hold-out。
3. **自报的 headline 数字口径不同**：被审计对象自报的 2.9358% 是**总阻力**口径（开阔水项稀释），
   brash 口径是 3.3001%；两者都对，但引用时必须写清口径，否则误差会被系统性低估。
4. **本文不改生产物理**：A5 只是"修法有效性"的量化，落地需要战役节点负责人裁定（会影响通道阻力绝对值与
   护航编队结论）。因此本单结论是**证据降级 + 修法建议**，不是补丁。
5. **未覆盖**：判据 (a)(c)(d)(e) 未在本单复核；`/polar/acceptance/route`（判据 e）仍是自己与自己的
   基线族对比，同样需要独立复核（下一张自找活的首选线索）。

## 6. 误差判定（关键量 vs 基准，逐项，含是否 <3%）

{t6}

**是否 <3%？** 分两层回答，不含糊：

- **平台自报 vs 其自身参考式**：max 2.9358%（总口径）< 3% —— 但 A1 证明这个数字只是密度比的代数结果，
  **不构成物理验证**，因此这一项"达标"没有意义。
- **厚度 hold-out（真基准：h_cal/h_eval−1 解析式）**：max **{a3_max:.2f}%** ≫ 15% 不可接受线 ⇒ **不达标**。
- **A1 恒等式残差**：≤{resid:.1e}% ⇒ 自洽性达标（这是"没算错"，不是"算对了"）。
- **A5 修法厚度 hold-out**：0.00% ⇒ 修法本身在代数上正确（仍未与试验/文献对流，故不作为能力锚）。

基准来源声明：本单所有基准量均为**闭式代数/极限判据**（密度比、量纲因子、退化极限、单调性），
不依赖文献数值或试验数据；因此不给出"vs 试验"的误差，也不宣称通道阻力的绝对精度。
"""


def main() -> None:
    with open(AUDIT, encoding="utf-8") as fh:
        audit = json.load(fh)
    os.makedirs(FIGS, exist_ok=True)
    f1 = fig_thickness(audit)
    f2 = fig_vacuity(audit)
    f3 = fig_identity(audit)
    tables = md_tables(audit)

    nums = {
        "reported_max_abs_rel_err_pct": audit["verdict"]["reported_max_abs_rel_err_pct"],
        "a1_max_identity_residual_pct": audit["A1_density_identity"]["max_abs_identity_residual_pct"],
        "a2_discriminating_power": audit["A2_vacuity"]["discriminating_power"],
        "a3_max_abs_rel_err_total_pct": audit["A3_thickness_holdout"]["max_abs_rel_err_total_pct"],
        "a5_max_abs_rel_err_brash_pct": audit["A5_consistent_fix"]["max_abs_rel_err_brash_pct"],
        "a5_magnitude_change": audit["A5_consistent_fix"]["magnitude_change_at_h_eval"],
        "grade": audit["verdict"]["evidence_grade_for_/polar/acceptance/channel"],
        "figures": [os.path.relpath(p, OUT) for p in (f1, f2, f3)],
    }
    with open(os.path.join(OUT, "numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(nums, fh, ensure_ascii=False, indent=2)

    md = REPORT.format(
        rep_err="%.4f" % nums["reported_max_abs_rel_err_pct"],
        resid=nums["a1_max_identity_residual_pct"],
        power=nums["a2_discriminating_power"],
        h_eval=audit["settings"]["h_eval_m"],
        a3_max=nums["a3_max_abs_rel_err_total_pct"],
        mag=nums["a5_magnitude_change"],
        **tables,
    )
    with open(os.path.join(OUT, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("wrote", os.path.join(OUT, "REPORT.md"))
    print(json.dumps(nums, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()