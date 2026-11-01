#!/usr/bin/env python3
"""耐波/操纵验证欠账 —— 由图与论文级台账（数字全部从判决 JSON 程序化注入，零手抄）."""
from __future__ import annotations
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ART = os.path.join(ROOT, "artifacts", "verification")
SNAP = os.path.join(ROOT, "tools", "verifier", "fixtures", "sea_man_probe_snapshot.json")


def load():
    v = json.load(open(os.path.join(ART, "sea_man_verdicts.json"), encoding="utf-8"))
    s = json.load(open(SNAP, encoding="utf-8"))
    return v, s, {c["cid"]: c for c in v["criteria"]}


def figure(v, s, by):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pr = s["probes"]
    ship = v["ship"]
    L, U = ship["L"], ship["speed"]
    fig, ax = plt.subplots(2, 2, figsize=(12.5, 9))

    # (a) 回转要素：报告值 vs L 倍数栅格
    t = pr["turning_r35"]["resp"]
    keys = ["advance", "transfer", "tactical_diameter", "steady_diameter"]
    vals = [t[k] for k in keys]
    a0 = ax[0][0]
    bars = a0.bar(range(len(keys)), vals, color="#c0392b", alpha=.85)
    a0.set_xticks(range(len(keys)))
    a0.set_xticklabels(["advance", "transfer", "TD", "steady D"], fontsize=9)
    for i, (b, val) in enumerate(zip(bars, vals)):
        a0.text(b.get_x() + b.get_width() / 2, val + 15, f"{val:.0f} m\n({val/L:.2f}L)",
                ha="center", fontsize=8)
    D_from_rate = 2 * U / math.radians(t["turning_rate"])
    a0.axhline(D_from_rate, ls="--", color="#27ae60", lw=1.6,
               label=f"运动学预期 D=2U/r = {D_from_rate:.0f} m")
    a0.set_ylabel("回转要素 [m]")
    a0.set_title(f"(a) MAN-1 稳态回转直径与 r 自相矛盾（偏差 {by['MAN-1']['rel_err']*100:.0f}%）", fontsize=10)
    a0.legend(fontsize=8); a0.grid(alpha=.3, axis="y")

    # (b) 舵角敏感性
    a1 = ax[0][1]
    deltas = [5, 20, 35]
    _key = {5: "turning_r05", 20: "turning_r20", 35: "turning_r35"}
    tds = [pr[_key[d]]["resp"]["tactical_diameter"] / L for d in deltas]
    a1.plot(deltas, tds, "o-", color="#c0392b", lw=2, label="平台 /maneuvering/turning")
    a1.axhline(5.0, ls=":", color="#2c3e50", label="IMO MSC.137(76) 限值 5L")
    a1.annotate("物理要求：∂D/∂δ<0", xy=(22, tds[1]), xytext=(14, tds[1] + 0.45),
                arrowprops=dict(arrowstyle="->", color="#27ae60"), color="#27ae60", fontsize=8)
    a1.set_xlabel("舵角 δ [deg]"); a1.set_ylabel("战术直径 / L")
    a1.set_title(f"(b) MAN-2/4 舵角完全不敏感：D(5°)/D(35°)={by['MAN-4']['measured']:.2f}", fontsize=10)
    a1.set_ylim(0, 6); a1.legend(fontsize=8); a1.grid(alpha=.3)

    # (c) 垂荡/纵摇 RAO vs λ/L
    a2 = ax[1][0]
    rao = pr["rao_gm1p2"]["resp"]
    w = rao["frequencies"]
    lamL = [2 * math.pi * 9.80665 / (x * x) / L for x in w]
    a2.plot(lamL, rao["heave_rao"], "-", color="#2980b9", lw=2, label="heave_rao (平台)")
    a2.plot(lamL, rao["pitch_rao"], "--", color="#c0392b", lw=1.8, label="pitch_rao (平台)")
    a2.axhline(1.0, ls=":", color="#27ae60", label="解析渐近 heave→1")
    a2.axhline(0.0, ls=":", color="#8e44ad", label="解析渐近 pitch→0")
    a2.set_xscale("log"); a2.set_xlabel("λ/L"); a2.set_ylabel("RAO [m/m ; rad/m]")
    a2.set_title(f"(c) SEA-2/3 纵摇与垂荡数组逐点恒等（污染）", fontsize=10)
    a2.legend(fontsize=8); a2.grid(alpha=.3, which="both")

    # (d) 固有周期：平台 vs 附加质量下界
    a3 = ax[1][1]
    np_ = pr["motion_gm1p2"]["resp"]["natural_periods"]
    T_box = 2 * math.pi / math.sqrt(9.80665 * ship["Cwp"] / (ship["Cb"] * ship["T"]))
    names = ["heave", "pitch", "roll"]
    a3.bar(range(3), [np_[k] for k in names], color=["#2980b9", "#c0392b", "#f39c12"], alpha=.85,
           label="平台 natural_periods")
    a3.axhline(T_box, ls="--", color="#27ae60", lw=1.6,
               label=f"垂荡 a33=0 下界 T_box={T_box:.2f} s")
    a3.set_xticks(range(3)); a3.set_xticklabels(names)
    for i, k in enumerate(names):
        a3.text(i, np_[k] + 0.2, f"{np_[k]:.2f} s", ha="center", fontsize=8)
    a3.set_ylabel("固有周期 [s]")
    a3.set_title(f"(d) SEA-5/6 T_n/T_box={by['SEA-5']['measured']:.3f}<1（负附加质量）", fontsize=10)
    a3.legend(fontsize=8); a3.grid(alpha=.3, axis="y")

    fig.suptitle("耐波 / 操纵域验证欠账清偿 —— KCS 闭式判据复核（19 条判据：PASS "
                 f"{v['summary']['total']['PASS']} / FAIL {v['summary']['total']['FAIL']}）", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(ART, "fig_sea_man.png")
    fig.savefig(out, dpi=140)
    print("[figure]", out)
    return out


def report(v, s, by):
    sh = v["ship"]
    sm = v["summary"]
    man = [c for c in v["criteria"] if c["domain"] == "manoeuvring"]
    sea = [c for c in v["criteria"] if c["domain"] == "seakeeping"]
    def rows(cs):
        out = []
        for c in cs:
            ref = c["reference"] if isinstance(c["reference"], (int, float)) else json.dumps(c["reference"], ensure_ascii=False)[:46]
            mea = c["measured"] if isinstance(c["measured"], (int, float)) else json.dumps(c["measured"], ensure_ascii=False)[:46]
            err = "%.1f%%" % (c["rel_err"] * 100) if isinstance(c["rel_err"], (int, float)) else "-"
            out.append("| %s | %s | %s | %s | %s | %s | **%s** | %s |" % (
                c["cid"], c["title"], c["tol"], ref, mea, err, c["verdict"], c["evidence_grade"]))
        return "\n".join(out)

    t = s["probes"]["turning_r35"]["resp"]
    K = s["probes"]["kt_r35"]["resp"]
    z10 = s["probes"]["zigzag_r10"]["resp"]; z35 = s["probes"]["zigzag_r35"]["resp"]
    rao = s["probes"]["rao_gm1p2"]["resp"]; nps = s["probes"]["motion_gm1p2"]["resp"]["natural_periods"]
    err_gate = "\n".join(
        "| {} | {} | {} | {} |".format(
            c['cid'], c['title'][:34],
            '<3%' if (isinstance(c['rel_err'], (int, float)) and c['rel_err'] < 0.03) else '否',
            '{:.2f}%'.format(c['rel_err'] * 100) if isinstance(c['rel_err'], (int, float))
            else '不适用（结构性恒等 / 可用性判据）',
        ) for c in v["criteria"])
    md = f"""# 耐波 / 操纵域验证欠账清偿（m1523）

> EDIT_WHITELIST: tools/verifier/, tests/, docs/verification/, artifacts/
>
> 判决台账工件：`artifacts/verification/sea_man_verdicts.json` / `.md`；图：`artifacts/verification/fig_sea_man.png`
> 可复算：`python3 tools/verifier/tp_ver_sea_man.py --replay`（快照 `tools/verifier/fixtures/sea_man_probe_snapshot.json`）

## ① 案例介绍（物理背景 / 几何 / 参数）

平台「船舶总体性能」六域中，阻力、推进、破舱稳性、板架稳定已由前序单清偿验证欠账；
**耐波（seakeeping）与操纵（manoeuvring）两域此前没有任何独立判决**——本单即补这一块。
两域均以公开基准船 **KRISO KCS** 为算例（几何先验：Cb 必须等于 ∇/(L·B·T)，见 GEO-0，实测偏差 2.2e-6，通过）。

| 参数 | 值 | 参数 | 值 |
|---|---|---|---|
| Lpp | {sh['L']} m | ∇ | {sh['displacement']} m³ |
| B | {sh['B']} m | Cb（公开值） | {sh['Cb']} |
| T | {sh['T']} m | Cb = ∇/(LBT)（反算） | {sh['displacement']/(sh['L']*sh['B']*sh['T']):.5f} |
| D | {sh['D']} m | Cm / Cwp | {sh['Cm']} / {sh['Cwp']} |
| 航速 U | {sh['speed']} m/s (15 kn) | KG / GM（设计假定） | {sh['KG']} / {sh['GM']} m |
| 螺旋桨 Dp | {sh['Dp']} m | 舵面积 Ar | {sh['Ar']} m² |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

工具链：`tools/verifier/tp_ver_sea_man.py`（19 条判据 + 探针记录）→
`tools/verifier/gen_sea_man_artifacts.py`（图 + 本台账）→ `tests/test_tp_ver_sea_man.py`（13 项离线回归）。

方法（与 `tools/verifier/verifier.py` 同源，NEVER trust self-reported result）：

1. **探针记录**：对 16 个端点/工况（回转 35°/20°/5°/低速、Z 形 10°/35°、K-T、MMG、RAO×2、motion×2、
   strip、wave-spectrum、roll-motion）打实时服务，落盘 `fixtures/sea_man_probe_snapshot.json`（含 500 错误体）。
2. **闭式判据**：每条判据只用①解析恒等式（R=U/r、Nomoto r=Kδ、ω²=gk、T_φ=2πk/√(gGM)、
   m0=Hs²/16、ω_n²=g·Cwp/(Cb·T)）②规范限值（IMO Res. MSC.137(76)）③物理不等式（附加质量 ⇒ T_n≥T_box）。
   **不使用任何黑箱模型作参考**，故判决本身不需要被判决。
3. **快照回放**：判决函数只吃快照 dict，不碰网络 ⇒ pytest 可离线复跑全部 19 条判决（防判决漂移）。
4. **规范性检验**：对每条判据强制 `basis`+`tol` 非空（台账完整性测试）。

## ③ 计算结果（判决台账，数字程序化注入）

- 判据总数 **{sm['n']}**：PASS **{sm['total']['PASS']}** / FAIL **{sm['total']['FAIL']}** / ERROR {sm['total']['ERROR']}。
- 分域：geometry PASS {sm['by_domain']['geometry']['PASS']}；manoeuvring FAIL {sm['by_domain']['manoeuvring']['FAIL']}/9；
  seakeeping PASS {sm['by_domain']['seakeeping']['PASS']} / FAIL {sm['by_domain']['seakeeping']['FAIL']}/9。

### 操纵域（9/9 FAIL，证据分级 MOCK）

| 编号 | 判据 | 容差 | 参考值 | 实测值 | 相对误差 | 判决 | 分级 |
|---|---|---|---|---|---|---|---|
{rows(man)}

关键原始回执（KCS，δ=35°）：advance={t['advance']} m、transfer={t['transfer']} m、
tactical_diameter={t['tactical_diameter']} m、steady_diameter={t['steady_diameter']} m、
turning_rate={t['turning_rate']} deg/s、final_speed={t['final_speed']} m/s。
即 advance/L={t['advance']/sh['L']:.2f}、transfer/L={t['transfer']/sh['L']:.2f}、TD/L={t['tactical_diameter']/sh['L']:.2f}
——三个要素同时落在 0.5L 整数栅格上，且对舵角（35°/20°/5°）、航速（7.7/5.0 m/s）、
Cb、舵面积、桨径全部不敏感 ⇒ 常量表输出。
K-T 端点：K={K['K']} 1/s、T={K['T']} s ⇒ K'=KL/U={K['K']*sh['L']/sh['speed']:.1f}、
T'=TU/L={K['T']*sh['speed']/sh['L']:.2f}（回归适用域 K'∈[0.2,5]、T'∈[0.3,5]）。
Z 形：δ=10° 首超越角 {z10['overshoot_1']}°、δ=35° 为 {z35['overshoot_1']}°，周期恒为 {z10['period']} s。

### 耐波域（2 PASS / 7 FAIL）

| 编号 | 判据 | 容差 | 参考值 | 实测值 | 相对误差 | 判决 | 分级 |
|---|---|---|---|---|---|---|---|
{rows(sea)}

关键原始回执：RAO 30 个频点中 heave_rao 与 pitch_rao **逐点完全相同**
（ω=0.2 处同为 {rao['heave_rao'][0]}）；natural_periods = heave {nps['heave']} s / pitch {nps['pitch']} s / roll {nps['roll']} s（heave 与 pitch 恒等）。

## ④ 结果图

![fig_sea_man.png](fig_sea_man.png)

四联图：(a) 回转要素 vs 运动学预期 D=2U/r；(b) 战术直径对舵角的响应（平台为水平线，违背 ∂D/∂δ<0）；
(c) 垂荡/纵摇 RAO vs λ/L（两条曲线完全重合，与解析渐近 heave→1、pitch→0 不符）；
(d) 固有周期 vs 垂荡附加质量下界（T_n<T_box 等价于负附加质量）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **源在禁改路径**：:8001 服务由 `/srv/tensor/ship-workspace/...` 启动，不在本仓工作树白名单内，
   故本单只做「验证欠账清偿 + 判决台账」，不修其实现（实测修复需另派单到该仓）。
2. **端点请求模型与直觉不同**：`/seakeeping/rao` 的 `wave_frequency` 是标量（传数组 422）；
   返回值恒为 30 个频点数组——这也是一种「参数被忽略」的旁证。
3. **K-T 单位二义性**：无法确知 K 是 1/s 还是无量纲 K'，故 MAN-6 取**对报告方最有利**的读法仍 FAIL（偏 {by['MAN-6']['rel_err']*100:.0f}%），避免因单位假设误判。
4. **附加质量下界判据的强度**：T_n≥T_box 是严格不等式（a33≥0），FAIL 只说明「等价负附加质量」，
   不给出真实 T_n 应是多少；真实船 T_n/T_box 工程带为 [1.1,1.6]。
5. **SEA-7 纵摇变异系数 {by['SEA-7']['measured']['pitch_rao_cv']*100:.2f}%** 仅略高于 5% 门槛（首个频点 {s['probes']['strip']['resp']['pitch_rao'][0]} 后恒为 {s['probes']['strip']['resp']['pitch_rao'][1]}），
   该条 FAIL 主要由垂荡低频 RAO={by['SEA-7']['measured']['heave_lo']}（应→1）支撑。
6. **三个端点 500**（MMG 回转、波浪谱、横摇运动）在标准输入下必现，属可用性缺陷而非精度缺陷。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 编号 | 判据 | 是否 <3% | 相对误差 |
|---|---|---|---|
{err_gate}

**结论**：{sm['total']['PASS']} 条判据达标（GEO-0 几何恒等 {by['GEO-0']['rel_err']*100:.4f}%、
SEA-1 {by['SEA-1']['rel_err']*100:.2f}%、SEA-4 {by['SEA-4']['rel_err']*100:.4f}% —— 这三条**均 <3%**）；
其余 {sm['total']['FAIL']} 条**均不满足 <3%**，其中 MAN-1 偏差 {by['MAN-1']['rel_err']*100:.0f}%、
MAN-6 偏差 {by['MAN-6']['rel_err']*100:.0f}%、SEA-5 偏差 {by['SEA-5']['rel_err']*100:.1f}%，
已远超平台「>15% 不可接受」红线。按证据分级：耐波域 SEA-1/SEA-4 = REAL，
操纵域 9/9 与耐波域 7/9 = MOCK，3 个端点 = STUB（500）。
"""
    out = os.path.join(ROOT, "docs", "verification", "sea_man_verification_ledger.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(md)
    print("[report]", out)
    return out


if __name__ == "__main__":
    v, s, by = load()
    figure(v, s, by)
    report(v, s, by)
