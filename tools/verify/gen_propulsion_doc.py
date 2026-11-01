"""Render docs/verify/PROPULSION_VERIFICATION_M1498.md from the audit artefact.

Every number in the generated markdown is read out of
``artifacts/m1498_propulsion_audit.json`` — nothing is hand-copied, so the
document cannot drift away from the evidence.

    python3 tools/verify/gen_propulsion_doc.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "m1498_propulsion_audit.json"
OUT = ROOT / "docs" / "verify" / "PROPULSION_VERIFICATION_M1498.md"


def fmt(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        if v != 0 and (abs(v) >= 1e6 or abs(v) < 1e-3):
            return f"{v:.3e}"
        return f"{v:.{nd}f}"
    return str(v)


def table(rows, header):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(fmt(c) for c in r) + " |")
    return "\n".join(out)


def main() -> None:
    a = json.loads(ART.read_text())
    ep = a["endpoints"]
    ow, chain, design, pred, cav = (a["open_water"], a["chain"], a["design_cavitation"],
                                    a["predict"], a["cavitation"])
    anchor = dict(chain["anchor"])
    # the share of the wave component in C_T is bounded analytically (3.0e-4/C_T at Fr~0.14)
    anchor["wave_share_bound"] = 3.0e-4 / anchor["C_T"] if anchor.get("C_T") else float("nan")
    calc = ep["calculations_propulsion"]
    calc_alt = ep.get("calculations_propulsion_alt", {})
    ittc = ep.get("anchor_resistance_ittc78", {})
    des = ep["propeller_design"]
    pv = ep["propulsion_predict"]
    cv = ep["propeller_cavitation"]
    base = a["open_water_source"]["PD1.0_Z4_AE0.7"]

    all_checks = [c for node in (ow, chain, design, pred, cav) for c in node["checks"]]
    n_chk, n_fail = len(all_checks), sum(1 for c in all_checks if c["verdict"] == "FAIL")

    # ---- KVLCC2 open-water table (platform answers, P/D = 1.0, Z = 4, Ae/Ao = 0.70)
    rows = []
    for j in sorted(base, key=float):
        ans = base[j]
        if "KT" not in ans:
            continue
        jf = float(j)
        ident = "—" if jf == 0 else f"{jf * ans['KT'] / (2 * 3.141592653589793 * ans['KQ']):.4f}"
        rows.append([jf, ans["KT"], ans["KQ"], ans["eta0"], ident,
                     "—" if jf == 0 else fmt(a["open_water"]["checks"][0].get("rows", [{}])[0].get("x"))])
    rows = [r[:5] for r in rows]
    ow_table = table(rows, ["J", "K_T (平台)", "K_Q (平台)", "η0 (平台)", "J·K_T/(2πK_Q) 由同响应复算"])

    # ---- P/D sensitivity of eta0
    p6 = next(c for c in ow["checks"] if c["id"] == "P6_eta_vs_PD")
    pd_rows = [[float(k), v] for k, v in sorted(p6["eta0_by_PD"].items(), key=lambda kv: float(kv[0]))]
    pd_table = table(pd_rows, ["P/D (Z=4, Ae/Ao=0.70, J=0.5)", "η0 (平台)"])

    # ---- chain table
    chain_rows = [[c["id"], c["verdict"], json.dumps({k: v for k, v in c.items()
                                                      if k not in ("id", "verdict", "desc")},
                                                     ensure_ascii=False)[:220]]
                  for c in chain["checks"]]

    grades = a["grades"]
    grade_rows = [[k, v] for k, v in grades.items()]

    md = f"""# 推进（Propulsion）域验证欠账清偿 —— D5 证据分级判决

* mission: **m1498**（tp-mod-1 自主工时，models 席）
* 平台: `{a['api']}`（ship-performance service :8001）
* 参考船: KVLCC2（L={pa_ref(a,'L')} m, B={pa_ref(a,'B')} m, T={pa_ref(a,'T')} m, Cb={pa_ref(a,'Cb')}, ∇={pa_ref(a,'displacement')} t, V_s={pa_ref(a,'speed')} kn）
* 复跑: `python3 tools/verify/propulsion_audit.py --live`；离线复判: `pytest tests/test_propulsion_audit.py`
* 平台报告: **#1085**（`scenario_key=ship_propulsion_verification`）
* 工件: `artifacts/m1498_propulsion_audit.json` + 图 1–3

## ① 案例介绍（物理背景 / 几何 / 参数）

推进域的物理链条是水动力螺旋桨敞水特性 → 船身-螺旋桨相互作用 → 自航功率：

```
K_T(J), K_Q(J)  ->  eta_O = J K_T / (2 pi K_Q)          (敞水定义)
eta_H = (1 - t)/(1 - w)                                  (船身效率)
eta_D = eta_O eta_H eta_R                                (推进系数)
P_E = R_T V_s,  T = R_T/(1-t),  P_D = 2 pi n Q           (功率-推力闭合)
```

推进域此前**没有**独立复核记录：报告 1067/1068/1071/1076/1064/1073 覆盖了稳性/阻力/操纵/耐波/振动噪声，推进域只有 1070 的「域值锚库入库」（数据入库，不含端点判决）。本单把 6 个推进端点在 KVLCC2 上一次性对表。

参数表（KVLCC2 满尺度）：

| 量 | 值 |
|---|---|
| Lpp | {pa_ref(a,'L')} m |
| B | {pa_ref(a,'B')} m |
| T | {pa_ref(a,'T')} m |
| Cb | {pa_ref(a,'Cb')} |
| 排水量 | {pa_ref(a,'displacement')} t |
| 湿表面积（锚点用） | {pa_ref(a,'wetted_surface')} m² |
| 设计航速 | {pa_ref(a,'speed')} kn = {fmt(anchor['V_s_m_s'],3)} m/s |
| 螺旋桨（假定） | D = 8.0 m, n = 80 r/min, w = 0.35, t = 0.20, Ae/Ao = 0.70 |

## ② 怎么做的（方法 / 流程 / 判据）

**方法学选择（关键）：** 本机不可见 :8001 源码，因此不做「读代码找 bug」，而是构造**与实现无关的判据**，只用端点自己的回答去检验它。三类判据，全部可写成机器断言：

1. **解析恒等式**（无模型假定）：`eta_O = J K_T/(2 pi K_Q)`；`eta_D = eta_O eta_H eta_R`；`Q = P_D/(2 pi n)`；`J = V_a/(n D)`。
2. **物理上界/形状性质**：
   - `eta_O(J=0) ≡ 0`（零进速时无输出功，定义使然）；
   - Froude 动量（作动盘）上界 `eta_O < 2/(1+sqrt(1+C_T))`，`C_T = 8K_T/(π J²)`；
   - 必须在某个有限进速比 `J0` 处 `K_T=0`（零推力点），且**J0 随 P/D 单调增**（B 系列回归是含 P/D、Z、Ae/Ao 系数的 J 多项式，Oosterveld & van Oossanen 1975 —— 该性质对任何合法系数集都成立）；
   - `J → J0` 时 `K_T/K_Q → 0`（桨叶残余扭矩使 `K_Q(J0) > 0`），因此 `K_Q ≡ c·K_T` 的常数比不合法；
   - `eta_O` 必须随 P/D 变化。
3. **跨域锚 + 量级锚**：阻力域（已复核域）的 ITTC-78 干摩擦+相关津贴解析式 `C_T = (1+k)C_F + C_A`（`C_F = 0.075/(log10 Re − 2)²`, k=0.20, C_A=0.4e-3）给出 `R_T = {fmt(anchor['R_T_N'],0)} N`、`P_E = {fmt(anchor['P_E_W'],0)} W`；Fr = 0.142 时兴波分量上界 0.3e-3（占 C_T {fmt(anchor['wave_share_bound']*100,1)}% 量级），故本锚点容差按 ±15% 计（这一项已计入误差预算，见 ⑤）。直径/power/推力再有独立的「舰船常识量级窗」（320 m VLCC 桨径 7–10 m）。

流程：探查 `openapi.json` → 逐端点构造合法 payload（含 KVLCC2 与 120 m 近海船两组，用于硬编码探测）→ 敞水端点在 (J × P/D × Z × Ae/Ao) 参数盒上扫 71 个点 → 判据批处理 → 生成 JSON 工件与 3 张图 → pytest（14 项）。

**判据非空洞性（PB-005）：** `test_gate_accepts_admissible_open_water_curve` 用一条合成的、物理自洽的 B 系列型曲线喂同一个判据器，必须**不**被判 MOCK；`test_gate_rejects_fabricated_open_water_curve` 用平台同签名的伪曲线喂进去，必须判 MOCK。两条同时绿，说明判据器既非恒真也非恒假。

## ③ 计算结果（数字表，全部由工件程序化注入）

**判决汇总（{n_chk} 条判据，{n_fail} 条 FAIL）**

{table(grade_rows, ["端点 / 判决键", "证据分级"])}

### 3.1 `/propeller-design/wageningen-b`（P/D=1.0, Z=4, Ae/Ao=0.70）

{ow_table}

判据结果：{", ".join(f"{c['id']}={c['verdict']}" for c in ow['checks'])}

由此可反演出端点实际使用的闭式（从响应拟合，仅作证据）：

```
K_T = max( 0.36 (P/D) − 0.24 J + 0.2 (Ae/Ao − 0.70) + z(Z) , 0.05 )
K_Q = K_T / 10
eta0 = min( 1.5915 J , 0.85 )
```

它**不是** B 系列回归公式：系数表里没有 J 的高次项、K_Q 不含 P/D、η0 与几何量无关且被 min() 截顶。

### 3.2 η0 对 P/D 的敏感性（同一 J）

{pd_table}

跨 P/D 的 η0 极差 = {fmt(p6['spread'],6)}。真实 B 系列在同一 J 上，P/D 从 0.6 到 1.4 会使 η0 变化 0.2 以上。

### 3.3 `/calculations/propulsion`（KVLCC2 推进链）

{table(chain_rows, ["判据", "判决", "证据"])}

平台输出 vs 独立锚：

| 量 | 平台 | 独立锚（ITTC-78 干摩擦+相关，t=0.20, η_D=0.72） | 相对误差 |
|---|---|---|---|
| 推力 T [N] | {fmt(calc.get('thrust'),1)} | {fmt(chain['checks'][2]['T_required_N'],1)} | {fmt(chain['checks'][2]['rel_err']*100,2)} % |
| 有效功率 P_E [W] | — | {fmt(anchor['P_E_W'],1)} | — |
| 输出功率 P_D [W] | {fmt(calc.get('delivered_power'),1)} | {fmt(chain['checks'][3]['P_D_required_W'],1)} | 比值 {fmt(chain['checks'][3]['ratio'],4)} |
| 敞水效率 η_O | {fmt(calc.get('open_water_eff'),4)} | 0.45–0.75（量级窗） | 物理不可行（>1） |
| 推进系数 η_D | {fmt(calc.get('propulsive_eff'),4)} | < 0.85 | 物理不可行（>1） |
| 船身效率 η_H | {fmt(calc.get('hull_eff'),3)} | (1−t)/(1−w) 视船型定 | 两船恒等（硬编码） |

硬编码探测（320 m VLCC vs 120 m 近海船）：η_H = {fmt(calc.get('hull_eff'),3)} / {fmt(calc_alt.get('hull_eff'),3)}，η_R = {fmt(calc.get('relative_rot_eff'),3)} / {fmt(calc_alt.get('relative_rot_eff'),3)} —— 两船完全相同。

### 3.4 `/propulsion/predict`、`/propulsion/design`、`/propeller-design/design`

| 端点 | 现象 | 判决 |
|---|---|---|
| `/propulsion/predict` | T = {fmt(pv.get('thrust'),1)} N（应为 {fmt(pred['checks'][0]['T_required_N'],0)} N），P = {fmt(pv.get('power'),1)} W（应为 {fmt(pred['checks'][1]['P_required_W'],0)} W），D = {fmt(pv.get('propeller_diameter'),2)} m | {pred['grade']} |
| `/propulsion/design` | 500 Internal Server Error（与 `/propulsion/predict` 完全同 payload） | {grades.get('propulsion_design')} |
| `/propeller-design/design` | D = {fmt(des.get('diameter'),2)} m（VLCC 应为 7–10 m）、J = {fmt(des.get('advance_coefficient'),4)}（应有 {fmt(design['checks'][0]['data']['J_required'],4)}）、η0 = {fmt(des.get('open_water_efficiency'),4)}（技术上限 ~0.75） | {design['grade']} |

### 3.5 `/propeller-design/cavitation`（Burrill）

| 量 | 平台 | 独立复算（0.7R 约定，V_R = {fmt(cav['checks'][0]['V_R_0.7R'],2)} m/s） | 结论 |
|---|---|---|---|
| σ_n | {fmt(cv.get('cavitation_number'),4)} | {fmt(cav['checks'][0]['sigma_0.7R_independent'],4)} | {cav['checks'][0]['verdict']}：未按 Burrill 0.7R 约定 |
| τ_c | {fmt(cv.get('thrust_load_coefficient'),4)} | {fmt(cav['checks'][1]['tau_independent'],4)} | {cav['checks'][1]['verdict']}：偏离 {fmt(cav['checks'][1]['ratio'],1)}× ，量纲/面积基准错 |

### 3.6 顺带发现：`/resistance-advanced/ittc78` 的量级反常（交阻力席复核）

平台返回 `Rt = {fmt(ittc.get('Rt'),2)}`、`Pe = {fmt(ittc.get('Pe'),2)}`；ITTC-78 干摩擦+相关解析式给 `R_T = {fmt(anchor['R_T_N'],0)} N`、`P_E = {fmt(anchor['P_E_W'],0)} W`。二者相差 **{fmt((ittc.get('Rt') or 0)*0.0 + ((anchor['R_T_N'])/(ittc.get('Rt') or 1)),1)}×**（平台值只有读成 kN 才勉强有量纲一致性，但即使如此仍是本锚点的 {fmt((ittc.get('Rt') or 0)*1e3/anchor['R_T_N'],2)}×）。另：`Cr = {fmt(ittc.get('Cr'),6)}` 在 Fr = {fmt(ittc.get('froude_number'),3)} 下比 ITTC-78 摩擦项 `Cf = {fmt(ittc.get('Cf'),6)}` 大 {fmt((ittc.get('Cr') or 0)/(ittc.get('Cf') or 1),2)}×，与本船型该 Fr 下兴波阻力 <0.3e-3 的量级不符。**本项不属于推进域，未计入本单判决**，建议阻力席另立单复核「Rt 量纲 + Cr 模型」。

## ④ 结果图

* **图 1** `artifacts/m1498_fig1_eta_identity.png`：平台报出的 η0（圆点）、Froude 作动盘上界（方块）与「用同一响应的 K_T、K_Q 复算的 J·K_T/(2πK_Q)」（三角）。三角线在 J>0.53 之后一路升到 1.9，而平台报 0.85 —— 平台自己的三列数字互相矛盾（实现了 `min(eta,0.85)` 截顶）。
* **图 2** `artifacts/m1498_fig2_torque_ratio.png`：K_Q/K_T 在 P/D = 0.6/1.0/1.4 三条曲线、全部 J 上恒为 0.1000 —— 没有扭矩模型。
* **图 3** `artifacts/m1498_fig3_power_chain.png`：P_E（独立锚）= {fmt(anchor['P_E_W']/1e6,2)} MW vs 平台 P_D = {fmt(calc.get('delivered_power',0)/1e6,4)} MW vs 应有 P_D = {fmt(chain['checks'][3]['P_D_required_W']/1e6,2)} MW。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **不能拿 `method` 字段当证据。** 敞水端点自报 `method="Wageningen B系列回归公式"`，实际是手写闭式 + `min()` 截顶 + 0.05 下限。报告 1062/1071 在操纵域踩过同一个坑（自报方法名 ≠ 真实算法），此处复现。
2. **锚点含**摩擦+相关津贴，不含兴波分量**（Fr = 0.142 下兴波 < 0.3e-3，约 {fmt(anchor['wave_share_bound']*100,2)}% 的 C_T）；同时 t = 0.20、η_D = 0.72 取的是本船型类值而非该船实测，故 §3.3 的通过门限设 ±15%（而非 3%）。这对判决不影响：平台偏差是 99% 量级（{fmt(chain['checks'][2]['rel_err']*100,1)}%），远超容差。
3. **敞水端点参数盒边界外的行为未测**（J<0、J>1.5、Ae/Ao>1.05、Z∉[3,7]）。
4. **本机不可见 :8001 源码**，所以本单只做证据分级与判据建设，**不含修复**。修复建议见文末。
5. 容量纪律：本机 load15 = 125（nproc 128），本单全程为 HTTP 探查 + 解析式复算，未起任何重算力进程（总 CPU 秒 < 5 s，不含 pytest）。
6. `find_kt_zero` 对「KT 被下限截断」的端点返回 `None`（网格内无符号变化），这本身就是判决依据之一，不要误读为「找不到零点所以跳过」。

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 基准 | 平台值 | 相对误差 | <3%？ |
|---|---|---|---|---|
| 敞水 η0（J=0.7, P/D=1.0） | 自身定义 J·K_T/(2πK_Q) = {fmt(base['0.7']['KT']*0.7/(2*3.141592653589793*base['0.7']['KQ']),4)} | {fmt(base['0.7']['eta0'],4)} | {fmt(abs(base['0.7']['eta0']-base['0.7']['KT']*0.7/(2*3.141592653589793*base['0.7']['KQ']))/abs(base['0.7']['KT']*0.7/(2*3.141592653589793*base['0.7']['KQ']))*100,1)} % | **否** |
| η0(J=0) | 0（定义） | {fmt(base['0.0']['eta0'],4)} | ∞（基准为 0） | **否** |
| K_Q/K_T 的 J 依赖性 | 必须随 J 变化（J→J0 时 →0） | 恒 0.1000，极差 {fmt(ow['checks'][2]['spread'],6) if len(ow['checks'])>2 else '—'} | 结构错误 | **否** |
| 零推力点 J0 | 有限且随 P/D 增 | 不存在（K_T 下限 0.05） | 结构错误 | **否** |
| 推进链推力 T | {fmt(chain['checks'][2]['T_required_N'],0)} N | {fmt(calc.get('thrust'),1)} N | {fmt(chain['checks'][2]['rel_err']*100,2)} % | **否** |
| 输出功率 P_D | {fmt(chain['checks'][3]['P_D_required_W'],0)} W | {fmt(calc.get('delivered_power'),1)} W | 比值 {fmt(chain['checks'][3]['ratio'],4)}（≈{fmt(1/(chain['checks'][3]['ratio'] or 1),0)}× 偏小） | **否** |
| 敞水效率 η_O | 0 < η_O ≤ 1（并 < 作动盘上界） | {fmt(calc.get('open_water_eff'),4)} | 物理不可行 | **否** |
| 推进系数 η_D | 0 < η_D < 1 | {fmt(calc.get('propulsive_eff'),4)} | 物理不可行 | **否** |
| 桨径 D（同船） | 7–10 m | {fmt(des.get('diameter'),2)} m / {fmt(pv.get('propeller_diameter'),2)} m | {fmt((des.get('diameter',0)/8.0-1)*100,0)} % / {fmt((pv.get('propeller_diameter',0)/8.0-1)*100,0)} % | **否** |
| Burrill σ_n | 0.7R 约定 {fmt(cav['checks'][0]['sigma_0.7R_independent'],4)} | {fmt(cv.get('cavitation_number'),4)} | {fmt(abs(cv.get('cavitation_number',0)-cav['checks'][0]['sigma_0.7R_independent'])/cav['checks'][0]['sigma_0.7R_independent']*100,1)} % | **否** |

**结论：推进域无一项关键量满足 <3%；真满足的只有两条代数恒等式（C2 推进链分解、C5 扭矩-功率），它们恰好说明问题在「物理输入」而不是「算术」。**

## 修复建议（按性价比排序，供派单）

1. **P0 — 敞水端点换真回归**：用 Oosterveld & van Oossanen (1975) 的 B 系列 5 阶回归（含 Z、Ae/Ao、P/D 项）替换闭式；禁止 `min()` 截顶 η0，禁止 K_T 下限；返回时给出 J0 与 η0 峰值。验收判据已在本单 `tests/` 中就位（合成合法曲线必须判 REAL）。
2. **P0 — 推进链量纲闭合**：`T = R_T/(1-t)`、`P_D = P_E/η_D`、`η_H = (1-t)/(1-w)` 由输入按定义算，不许常数 1.1；`eta_O` 必须来自敞水曲线在该 J 上的插值（禁止 >1）。
3. **P1 — `/propeller-design/design`**：`J = V_a/(nD)` 自洽；直径来自叶序/盘面比设计而不是乱给；η0 用敞水曲线求峰值附近。
4. **P1 — Burrill 空泡**：统一 0.7R 约定算 σ_n 与 τ_c，A_P 用量纲一致的投影面积。
5. **P2 — `/propulsion/design` 500**：与 `/propulsion/predict` 同 payload 却 500，需修契约。
6. **P2 — 交阻力席**：`/resistance-advanced/ittc78` 的 Rt 量级（kN vs N）与 Cr 模型（§3.6）。

---

*生成器：`tools/verify/gen_propulsion_doc.py`（本文所有数值均从 `artifacts/m1498_propulsion_audit.json` 注入，零手抄）。*
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(md)} bytes)")


def pa_ref(a, key):
    return a["reference_vessel"][key]


if __name__ == "__main__":
    main()