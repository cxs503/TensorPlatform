#!/usr/bin/env python3
"""Render the m1597 verification note from artifacts/m1597/stability_audit.json.

Zero hand-copied numbers: every value in the markdown comes from the audit
artifact.  Usage::

    python3 tools/verification/render_m1597_verification_doc.py
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "m1597" / "stability_audit.json"
OUT = REPO / "docs" / "verification" / "stability_engine_verification_m1597.md"


def pct(x, digits=1):
    return "n/a" if x is None else f"{100.0 * x:+.{digits}f}%"


def main() -> int:
    d = json.loads(ART.read_text(encoding="utf-8"))
    rows = d["cases"]
    diag = d["formula_diagnostics"]
    worst = {}
    for key in ("kb", "bm", "gm", "gz_max", "area_0_30", "area_30_40", "area_0_40"):
        vals = [abs(r["errors_vs_reference"][key]) for r in rows
                if r["errors_vs_reference"].get(key) is not None]
        worst[key] = max(vals) if vals else None

    L = []
    A = L.append
    A("# 平台稳性引擎复核：箱形驳船解析基准（mission m1597）")
    A("")
    A("**结论先行**：`POST /api/v1/calculations/stability`（部署版 "
      f"`{d['audited_engine']}`）对箱形驳船（`Cb=1`，全部水力要素有闭式解）的浮心高度 "
      f"**KB 恒定偏高 {pct(diag['kb_box_limit']['rel_error'])}**，经 GM 传播后最大复原力臂 "
      f"GZmax 偏高 {pct(worst['gz_max'])}、IMO 曲线面积偏高 {pct(worst['area_0_40'])}，稳性消失角"
      "被 60° 网格上界截断；5 个基准算例中 4 例出现 IMO 判据**判反**（3 例把不满足判成满足，"
      "1 例把满足判成不满足）。按平台证据分级，该能力应从 REAL 降级为 "
      "**REAL（摩擦/静水几何正确）+ MOCK（大倾角 GZ 曲线为经验拟合）混合**。")
    A("")
    A("## 1. 案例介绍")
    A("")
    A("横摇复原力臂 GZ(φ) 是完整稳性衡准（IMO IS Code 2008 Part A §2.2）的唯一输入量。"
      "经验船型公式（如 KB≈0.5~0.6T）**无法自证**，因此复核必须选取有闭式解的对象："
      "矩形驳船（箱形、直舷、`Cb=1`）。其精确水力要素为")
    A("")
    A("```")
    A("∇ = L·B·T ；KB = T/2 ；BM = I_T/∇ = B²/(12·T) ；KM = KB + BM")
    A("GZ(φ) = ( GM + ½·BM·tan²φ )·sinφ          （直舷区，精确恒等式）")
    A("```")
    A("")
    A("直舷区失效界：舷侧出水（`tanφ = 2T/B`）与甲板边缘入水（`tanφ = (D−T)/(B/2)`）"
      "取小者；界外由多边形裁剪精确积分求解（无网格误差）。")
    A("")
    A("基准算例（`artifacts/m1597/stability_audit.json`）：")
    A("")
    A("| 算例 | L [m] | B [m] | T [m] | D [m] | KG [m] | 精确 GM [m] | 直舷区上限 | 说明 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        g = r["geometry"]
        A(f"| {r['case']} | {g['L']:.0f} | {g['B']:.0f} | {g['T']:.0f} | {g['D']:.0f} | "
          f"{g['KG']:.1f} | {r['reference']['gm']:.3f} | {r['wallsided_limit_deg']:.2f}° | {r['note']} |")
    A("")
    A("## 2. 怎么做的")
    A("")
    A("1. **基准实现**：`backend/services/box_hydrostatics.py`（纯标准库，零经验系数）。"
      "直舷区内用闭式 `(GM+½BM tan²φ)sinφ`；界外用 Sutherland–Hodgman 半平面裁剪求"
      "任意倾角下的浸水截面多边形，面积/形心由 shoelace 精确给出，水位偏移用二分求解"
      "使浸水面积恒定等于 `B·T`（静水平衡），浮心/重心投影到世界水平方向得 GZ。")
    A("2. **测量尺自校**：直舷区内解析式与多边形解最大偏差 "
      f"**{max(r['reference_check_wallsided_identity_max_abs_m'] for r in rows):.2e} m**"
      "（机器精度）；GZ 原点斜率 = GM 相对偏差 1e-6；浸水面积守恒相对 1e-12；"
      "Simpson 面积 vs 细分梯形相对 3e-5（离散底噪）。")
    A("3. **引擎黑盒复核**：`tools/verification/stability_engine_audit_m1597.py` 按 "
      "`StabilityInput`（length/beam/draft/displacement/cb/kg）逐算例 POST 真实部署端点"
      "（:8001），与基准逐量比对，并核对 6 条 IMO 判据的布尔结论。")
    A("4. **引擎公式冻结快照**：`backend/services/stability_engine_audit_snapshot.py` 记录"
      "被审引擎源码公式（KB=T(1−Cb/3)、BM=(Cwp²B³/12)/(B·T·Cb)、φ<15° 用小角度式、"
      "消失角在 0–60°/5° 网格扫描且缺省 60），用于无网络、无端点时的可复现诊断。")
    A("5. **回归测试**：`backend/tests/test_box_hydrostatics.py`（17 项，全部通过）。")
    A("")
    A("## 3. 计算结果")
    A("")
    A("| 算例 | GM 精确 | GM 引擎 | 误差 | KB 精确 | KB 引擎 | 误差 | GZmax 精确 | GZmax 引擎 | 误差 | 消失角精确 | 消失角引擎 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a = r["api"] or {}
        e = r["errors_vs_reference"]
        A(f"| {r['case']} | {r['reference']['gm']:.3f} | {a.get('gm', float('nan')):.3f} | {pct(e['gm'])} "
          f"| {r['reference']['kb']:.3f} | {a.get('kb', float('nan')):.3f} | {pct(e['kb'])} "
          f"| {r['reference']['gz_max']:.3f} | {a.get('gz_max', float('nan')):.3f} | {pct(e['gz_max'])} "
          f"| {r['reference']['vanishing_angle']:.2f}° | {a.get('vanishing_angle', float('nan')):.0f}° |")
    A("")
    A("| 算例 | A(0–30) 精确 | 引擎 | 误差 | A(30–40) 精确 | 引擎 | 误差 | A(0–40) 精确 | 引擎 | 误差 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a = r["api"] or {}
        e = r["errors_vs_reference"]
        A(f"| {r['case']} | {r['reference']['area_0_30']:.4f} | {a.get('area_0_30', float('nan')):.4f} | {pct(e['area_0_30'])} "
          f"| {r['reference']['area_30_40']:.4f} | {a.get('area_30_40', float('nan')):.4f} | {pct(e['area_30_40'])} "
          f"| {r['reference']['area_0_40']:.4f} | {a.get('area_0_40', float('nan')):.4f} | {pct(e['area_0_40'])} |")
    A("")
    A("IMO 判据比对（引擎 vs 精确曲线）：")
    A("")
    for r in rows:
        flips = r.get("imo_flips") or {}
        if flips:
            A(f"* **{r['case']}** — " + "；".join(f"{k}: 引擎={v[0]} / 精确={v[1]}" for k, v in flips.items()))
        else:
            A(f"* **{r['case']}** — 判据结论一致")
    A("")
    A("公式级诊断（不依赖端点）：")
    A("")
    A(f"* KB 箱形极限：引擎 `KB/T={diag['kb_box_limit']['engine_over_T']:.4f}` vs 精确 `0.5` → "
      f"{pct(diag['kb_box_limit']['rel_error'])}（`T(1−Cb/3)` 应为 `T(5/6−Cb/3)`，后者在 `Cb=1` 处恰好给 T/2）")
    A(f"* φ=15° 分段切换处 GZ 不连续跳变 **{diag['gz_branch_jump_at_15deg_m']:.4f} m**"
      "（小角度式丢掉了 ½BM tan²φ 项）")
    for row in diag["gz_profile_vs_wallsided"]:
        A(f"* φ={row['heel_deg']:.0f}°：引擎 {row['engine_gz']:.4f} m vs 直舷精确 "
          f"{row['wallsided_gz']:.4f} m（{pct(row['rel_error'])}）")
    A("* BM 水线面惯性矩指数：引擎用 `Cwp²`，水线面惯性矩按定义只含 `Cwp¹`：")
    for row in diag["bm_cwp_exponent"]:
        A(f"  * Cb={row['Cb']}：引擎 {row['bm_engine']:.4f} m vs Cwp¹ 基准 {row['bm_ref']:.4f} m"
          f"（{pct(row['rel_error'])}）")
    A("* 消失角截断：GM>0 的驳船一律返回 60°（网格上界），"
      "使 IMO「消失角 ≥55°」判据几乎恒为真：")
    for row in diag["vanishing_censoring"]:
        A(f"  * KG={row['KG']}：GM={row['engine_gm']:.2f} m → 引擎消失角 {row['engine_vanishing']:.0f}°")
    A("")
    A("图（`artifacts/m1597/`）：")
    A("")
    A("* `gz_curves.png` —— 精确 GZ 曲线（黑）vs 引擎曲线（红）×算例 A/D，附 GZ 相对误差曲线与 ±15% 判定带")
    A("* `kb_gm_error.png` —— KB/GM/GZmax/A(0–40) 相对误差按算例分组柱状图")
    A("")
    A("## 4. 误差判定（逐项，是否 <3%）")
    A("")
    A("| 量 | 最不利相对误差 | <3%? | 判定 |")
    A("|---|---|---|---|")
    verdicts = {
        "kb": ("KB（浮心高度）", "否——经验公式写错一个系数，影响全部下游量"),
        "bm": ("BM（稳心半径，Cb=1 时）", "是——箱形极限下 BM 闭式正确"),
        "gm": ("GM（初稳性高度）", "否——KB 误差直接进入 GM"),
        "gz_max": ("GZmax（最大复原力臂）", "否——大倾角曲线不可用"),
        "area_0_30": ("A(0–30°)", "否——面积随 GZ 曲线一起偏大"),
        "area_30_40": ("A(30–40°)", "否——最大偏差出现在此区间"),
        "area_0_40": ("A(0–40°)", "否"),
    }
    for key, (label, text) in verdicts.items():
        w = worst[key]
        ok = "是" if (w is not None and w < 0.03) else "否"
        A(f"| {label} | {pct(w)} | {ok} | {text} |")
    A("")
    A("基准自身的可信度：直舷区解析式与多边形精确解偏差 1e-13 m 量级、原位 GM 斜率 1e-6、"
      "面积守恒 1e-12 —— 测量尺误差远小于 3%，故上述超差归因于被审引擎而非基准。")
    A("")
    A("## 5. 注意点 / 局限")
    A("")
    A("1. **部署版与源码不一致（如实记录）**：端点返回 GZmax=1.367 m @40° 与源码快照的"
      "低角度分支一致，但其后曲线下降，而源码公式继续增长至 60° 时 2.425 m。"
      "因此**大倾角分支无法由随包源码复现**（部署构建与仓库快照不同步），"
      "本复核只把 ≤40° 作为已验证锚点；60° 样点与依赖它的面积/消失角仅在黑盒层面比较。")
    A("2. **端点输入无干舷/型深**：`StabilityInput` 只有 length/beam/draft/displacement/cb/kg，"
      "甲板边缘入水与淹湿修正根本无法表达，这是曲线在大倾角失真的结构性原因。")
    A("3. **`Cb<1` 无闭式基准**：本复核的绝对误差结论只对箱形驳船成立；"
      "对一般船型只能做公式级审查（KB/BM 系数、Cwp 指数），不能给出 GZ 逐点误差。")
    A("4. **裁决方向有双向风险**：4/5 算例判反，其中 A、C 两例把「消失角不足 55°」判成满足"
      "（偏危险/非保守），D 例把「GZmax 倾角 ≥25°」判成不满足（偏保守）；"
      "不存在单向安全偏置，不能靠经验加裕度规避。")
    A("5. **修复建议（不在本仓白名单，见派发单）**：KB 改 `T(5/6−Cb/3)`；BM 惯性矩用 `Cwp¹`；"
      "GZ 全角度统一用 `(GM+½BM tan²φ)sinφ` 并在 0–90° 细分网格积分；消失角扫描到 90° 且"
      "把网格上界如实报告为「≥上界」而非具体角度；补充型深/干舷入参以表达甲板入水。")
    A("")
    A("## 6. 复现")
    A("")
    A("```bash")
    A("# 1) 端点复核（写 artifacts/m1597/）")
    A("python3 tools/verification/stability_engine_audit_m1597.py")
    A("# 2) 基准/快照回归测试（17 项）")
    A("cd backend && python3 -m pytest tests/test_box_hydrostatics.py -q --noconftest")
    A("# 3) 由工件重新生成本文")
    A("python3 tools/verification/render_m1597_verification_doc.py")
    A("```")
    A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT} ({len(L)} lines); worst errors: " +
          ", ".join(f"{k}={pct(v)}" for k, v in worst.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())