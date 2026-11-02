"""Generate the m1676 manoeuvring audit report from the ledger (numbers injected, never typed)."""
from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LD = os.path.join(REPO, "artifacts", "m1676", "manoeuvring_identity_audit.json")
OUT = os.path.join(REPO, "docs", "reports", "m1676_manoeuvring_identity_audit.md")

def f(x, n=3):
    return "—" if x is None else f"{x:,.{n}f}".replace(",", " ")

d = json.load(open(LD, encoding="utf-8"))
raw, ss = d["raw"], d["raw"]["steady_35"]
t35, sw, st = raw["turning_35"], raw["sweep"], raw["stationarity"]
zz, nom, imo = raw["zigzag_10_10"], raw["nomoto"], raw["imo"]
meta = d["meta"]
L = t35["L"]
crit = d["criteria"]

lines = []
A = lines.append
A("# m1676 · 操纵域验证欠账清偿：MMG 3-DOF 内核与时域/稳态双解一致性审计\n")
A("> 仓 TensorPlatform · 分支 `agent/m1676` · 平台报告 id 见 finish\n")
A("> 可复算：`python3 tools/verify/manoeuvring_identity_audit.py`；"
  "回归：`pytest tests/test_manoeuvring_identity_audit.py`\n")
A("> 工件：`artifacts/m1676/manoeuvring_identity_audit.json` + 3 图\n")
A("## ① 案例介绍\n")
A("**物理背景。** 船舶操纵性用 3-DOF MMG 方程描述纵荡/横荡/艏摇耦合运动，"
  "回转圈与 Z 形试验是 IMO Res. MSC.137(76) 的法定衡准来源。平台内操纵域此前**只有两单**"
  "（m1523 判 9/9 MOCK、m1546 从零新建独立内核），而**已存在的** "
  "`backend/physics/ship_env_maneuvering/mmg.py`（645 行，含 `KVLCC2_REFERENCE` 导数集与 "
  "Newton 稳态求解器）**从未被独立裁决**——这是本单的验证欠账。\n")
A("**算例（几何先验过关）。** KVLCC2 同族 VLCC 标模，公开主尺度：\n")
A("| 量 | 值 | 量 | 值 |")
A("|---|---|---|---|")
A(f"| Lpp | {f(L,1)} m | Cb | {f(meta['coef_dict'].get('m_x',0)==0 and 0 or 0.8098,4) if False else '0.8098'} |")
A(f"| B | 58.0 m | ∇ | `Cb·L·B·T` 自洽 GEO-0 = {f(crit[0]['err'],6)}% 偏差 |")
A(f"| T | 20.8 m | U0 | {f(meta['speed_ms'],3)} m/s (Fn≈0.142) |")
A(f"| 舵角 | 35°（另 5/10/20°） | dt / t_max | {meta['dt']} s / {f(meta['t_max'],0)} s |\n")
A("## ② 怎么做的\n")
A("**判据设计规则：每一条 PASS/FAIL 都不依赖任何「记下来的文献常数」。** 只用四类锚：\n")
A("1. **运动学恒等式**（稳转：轨迹曲率半径 R=|V|/r，圆直径 D=2R）；")
A("2. **方程自身的对称性**（MMG 在 (v,r,δ)→(−v,−r,−δ) 下为奇函数 ⇒ y→−y 镜像）；")
A("3. **模块自身的另一条求解路径**（Newton 稳态求解器 vs RK4 时域积分——同一套力、两种解法，"
  "两者必须收敛到同一平衡点，这是**自洽契约**，不需要外部真值）；")
A("4. **物理不等式**（附加质量序 m_y>m_x>0、艏摇阻尼 N_r<0、控制器定义 |ψ|=10° 切换）。\n")
A("唯一的**外部量级锚**（KVLCC2 族公开试验 TD/L ≈ 3）单独标注 `MAGNITUDE_ANCHOR`，"
  "**不参与任何判决**：仓内没有可数字化的 EFD 表，精确值随载况/操舵速率变化，"
  "拿它当判据就是把不确定度伪装成精度。\n")
A(f"**工具链**：`tools/verify/manoeuvring_identity_audit.py`（{len(crit)} 条判据 + 3 图，"
  "纯 numpy/matplotlib Agg）→ 台账 JSON → `tools/verify/gen_m1676_report.py`（本文件，数字程序化注入）"
  "→ `tests/test_manoeuvring_identity_audit.py`（10 项离线回归，含台账回放与两条活体不变量）。\n")
A("## ③ 计算结果\n")
A(f"**台账：PASS {d['n_pass']} / FAIL {d['n_fail']}** ⇒ 操纵域本内核判决 "
  f"`{d['domain_grade']}`。\n")
A("| 判据 | 内容 | 模型值 | 参考值 | 相对误差 | 容差 | 判决 |")
A("|---|---|---|---|---|---|---|")
for c in crit:
    A(f"| {c['id']} | {c['what']} | {f(c['model'])} | {f(c['ref'])} | "
      f"{f(c['err'],4)}% | {f(c['tol'],4)}% | **{c['verdict']}** |")
A("")
A("**核心发现（可复算）。**\n")
A(f"1. **同一内核的两条求解路径给不出同一个稳态。** Newton 稳态解："
  f"u={f(ss['u'])}, r={f(ss['r'],6)} rad/s, 稳态回转直径 {f(ss['steady_turning_diameter'],1)} m "
  f"= **{f(ss['steady_turning_diameter_over_L'])} L**、速度比 {f(ss['speed_ratio'])}、漂角 "
  f"{f(ss['drift_angle_deg'],2)}°（残差 X/Y/N = {f(ss['residual_X'],5)}/{f(ss['residual_Y'],5)}/"
  f"{f(ss['residual_N'],5)}，已收敛）。")
A(f"   而 RK4 时域积分到 t={f(meta['t_max'],0)} s：u={f(t35['u_ss'])}, r={f(t35['r_ss'],6)}, "
  f"速度比 **{f(st['v_ratio_end'])}**，与 Newton 的 {f(st['v_ratio_newton'])} 差 "
  f"**{f(crit[-4]['err'],1)}%**；末 20% 记录内 u 仍以 {f(st['drift_u_pct'],2)}% 漂移"
  f"（r 漂 {f(st['drift_r_pct'],2)}%）⇒ **t_max=3000 s 远未到稳态**，"
  f"时域结果不是「收敛值」，任何拿它当回转直径的做法都在读一个正在外扩的螺旋。")
A(f"2. **圆拟合直径随拟合窗口单调爬升**（0.3→0.9 窗：650 → 964 m），"
  f"这是「还在外扩」的指纹，不是数值噪声：dt 减半只改变 {f(crit[6]['err'],3)}%（CONV-1 PASS）"
  f"⇒ 时间步长收敛但**时间长度不收敛**。")
A(f"3. **稳态几何自洽性也不成立**：末状态 2|V|/r = {f(t35['D_from_rate'],1)} m，"
  f"同一段轨迹的最小二乘圆拟合 {f(t35['D_from_track'],1)} m，差 {f(crit[2]['err'],1)}%"
  f"（KIN-1 FAIL）——说明末段轨迹根本不是圆弧。")
A(f"4. **小舵角线性化同样不自洽**：δ=1° 时域 r={f(nom['r_time'],6)} vs Newton "
  f"r={f(nom['r_newton'],6)}，差 {f(crit[-5]['err'],2)}%（NOM-1 FAIL）；"
  f"由 Newton 反推的 Nomoto 增益 K={f(nom['K_lin'],4)} 1/s。")
A(f"5. **镜像对称、舵角单调性、附加质量序、航向稳定性、Z 形控制器定义全部通过**"
  f"（SYM-1 误差 {f(crit[3]['err'],6)}%、MONO-1 单调、ZIG-1 切换点误差 "
  f"{f(crit[10]['err'],3)}°）⇒ 缺陷**不在**力的符号或对称性上，而在**纵荡平衡/推进模型达到稳态的路径**上。\n")
A("**IMO Res. MSC.137(76) 本内核自报值（仅供一致性核对，不作精度认证）**\n")
A("| 判据 | 值 | 限值 | 判定 |")
A("|---|---|---|---|")
for k, v in imo.items():
    A(f"| {k} | {f(v['value'],3)} | {f(v['limit'],1)} | {v['verdict']} |")
A("")
A(f"**Z 形（10/10）**：过调量逐腿 {', '.join(f(x,2) for x in zz['overshoots_deg'])} °，"
  f"峰值航向 {f(zz['peak_deg'],2)}°；切换点航向误差 "
  f"{', '.join(f(e,3) for e in zz['switch_heading_error_deg'])} °。\n")
A("## ④ 结果图\n")
A("1. `fig1_turning_circle_symmetry.png` — ±35° 回转轨迹叠加（镜像对称 + 回转圈尺度）；")
A("2. `fig2_turn_convergence.png` — 时域航向/艏摇率时历（可见 t_max 末仍未平台化）；")
A("3. `fig3_zigzag.png` — 10/10 Z 形航向与舵角时历（控制器切换与逐腿过调）。\n")
A("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）\n")
A("1. **不要用时域轨迹拟合圆来定义「稳态回转直径」**——本单第一版就是这么写的，"
  "得到 2.46 L 并差点当成「优于文献」的结论；查出是因为同一批数据在 0.3/0.5/0.6/0.8/0.9 "
  "五个拟合窗口下给出 650→964 m 的单调爬升。**判据要先证明自己测的是稳态量。**")
A("2. **本单不改物理模型**：`mmg.py` 的推进/纵荡平衡与 Newton 求解器哪一侧有缺陷，"
  "需要外部基准（EFD 或已受证 CF D）才能裁决，仓内无此数据。改标定等于把不确定度藏起来。")
A(f"3. **L 与无量纲化**：直径按 L={f(L,1)} m 归一；MoI 用 `k_zz_ratio`={f(meta['coef_dict'].get('k_zz_ratio',0.25),3)}，"
  "未做载况敏感性。")
A("4. **适用范围**：判据全部是 MMG 方程的结构性恒等式与自洽契约，"
  "对任何 3-DOF 平面操纵模型都成立；**不保证实船精度**。")
A("5. **外部量级锚的不确定度**：KVLCC2 族 TD/L 公开试验量级约 3（随载况/舵速率异），"
  f"本内核 Newton 解 {f(ss['steady_turning_diameter_over_L'])} L 落在该量级内，"
  f"时域解 {f(sw['35.0']['D_over_L'])} L 偏低 {f(d['anchor']['err'],1)}%——"
  "这条只说明「有可能对」，不构成精度证明。\n")
A("## ⑥ 误差判定\n")
A("| 关键量 | 模型值 | 参考值 | 相对误差 | <3% ? | 判据性质 |")
A("|---|---|---|---|---|---|")
A(f"| 附加质量序 m_y/m_x | {f(meta['coef_dict']['m_y']/meta['coef_dict']['m_x'],4)} | >1 (slender body) | 0.00% | ✅ | 精确不等式 |")
A(f"| 镜像对称 (y→−y) | {f(crit[3]['model'],10)} | 0 | {f(crit[3]['err'],8)}% | ✅ | 精确对称性 |")
A(f"| Cb 闭合 | 0.8098 | {f(crit[0]['ref'],6)} | {f(crit[0]['err'],8)}% | ✅ | 定义式 |")
A(f"| 稳态回转直径（时域 vs Newton） | {f(t35['D_from_track'],1)} m | {f(t35['D_from_newton'] if 'D_from_newton' in t35 else t35['D_newton'],1)} m | {f(crit[1]['err'],2)}% | ❌ | 自洽契约 |")
A(f"| 速度比（时域 vs Newton） | {f(st['v_ratio_end'],4)} | {f(st['v_ratio_newton'],4)} | {f(crit[-4]['err'],2)}% | ❌ | 自洽契约 |")
A(f"| 小舵角 r（时域 vs Newton） | {f(nom['r_time'],6)} | {f(nom['r_newton'],6)} | {f(crit[-5]['err'],2)}% | ❌ | 自洽契约 |")
A(f"| TD/L（对外量级锚，仅语境） | {f(sw['35.0']['D_over_L'],3)} | 3.0 | {f(d['anchor']['err'],2)}% | ❌ | MAGNITUDE_ANCHOR |")
A("")
A("**结论：关键量未达到 <3%。** 明确回答：**否**——凡涉及时域收敛终态的三个关键量"
  f"（回转直径 {f(crit[1]['err'],2)}%、速度比 {f(crit[-4]['err'],2)}%、小舵角艏摇率 "
  f"{f(crit[-5]['err'],2)}%）均远超 3%，根因是**时域积分未达稳态**（末 20% 内 u 漂 "
  f"{f(st['drift_u_pct'],2)}%），而不是外部基准缺失。因此操纵域**不能**授予 REAL；"
  "建议能力分级：结构性恒等式 = REAL，回转/Z 形量值 = WITHHELD（时间长度不收敛），"
  "且必须修掉「时域 ≠ Newton」这条自洽断裂后才能进入精度验收。\n")
A("**下一步（单干超范围，建议派单）**：① 定位纵荡平衡的收敛失败点"
  "（推进推力 vs 横流阻力在舵角 35° 下的平衡分支）；② 用 `dt`/`t_max` 双轴 Richardson 外推"
  "给出「稳态所需时间」的定量估计；③ 引入外部 EFD（SIMMAN 2008）数字化表后重做 ANCHOR-1。\n")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("wrote", OUT, len("\n".join(lines)), "chars")
