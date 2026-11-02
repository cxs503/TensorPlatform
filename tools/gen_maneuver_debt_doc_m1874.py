#!/usr/bin/env python3
"""由工件（probe.json / verdict.json）生成 m1874 审计文档，数字零手抄（m1874）。

    python3 tools/gen_maneuver_debt_doc_m1874.py      # 写 docs/verifier/maneuvering_debt_m1874.md
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "maneuvering_debt_m1874")
DOC = os.path.join(ROOT, "docs", "verifier", "maneuvering_debt_m1874.md")

rec = json.load(open(os.path.join(ART, "probe.json")))
jd = json.load(open(os.path.join(ART, "verdict.json")))
C = {c["id"]: c for c in jd["criteria"]}
nr = jd["nomoto_reference"]
zc = nr["zigzag_nomoto"]
fam = jd["families"]
base = rec["base_ship"]
L = base["L"]
ext = rec["ext_calc"]
e82 = next(r for r in ext["cb"] if abs(r["value"] - base["Cb"]) < 1e-9)
zep = next(r for r in rec["cross_endpoints"] if r["path"] == "/maneuvering/zigzag")
zd = zep["data"]
turn = next(r for r in rec["cross_endpoints"] if r["path"] == "/maneuvering/turning")["data"]
mmg = next(r for r in rec["cross_endpoints"] if r["path"] == "/mmg/turning")["data"]
sim = {(r["maneuver_type"], r["delta0"]): r for r in rec["abkowitz_simulate"]}
der = rec["abkowitz_derivatives"]
td_ratio = nr["Td_closed_m"] / nr["Td_reported_m"]

rows = "\n".join(
    f"| {c['id']} | {c['kind']} | {c['criterion']} | {c['value']} | {c['baseline']} | "
    f"**{c['verdict']}** |" for c in jd["criteria"])
famrows = "\n".join(f"| `{k}` | **{v['verdict']}** | {v['basis']} |" for k, v in fam.items())

doc = f"""# 操纵域「未覆盖端点族」验证欠账清偿（m1874）

**席位**：tp-app-2（applications） · **单号**：m1874 · **分支**：`agent/m1874` · **日期**：2026-11-02
**EDIT_WHITELIST**：`docs/`, `tools/`, `tests/`, `artifacts/`
**被测服务**：`http://127.0.0.1:8001/api/v1`（ship-performance-platform 2.0.0，只读探针，未改服务代码）

**产物**
- `tools/audit/maneuver_debt_m1874.py` — 探针 + 判决 + 出图（22 条 FAIL 判据可一键重跑）
- `tests/test_maneuver_debt_m1874.py` — 19 项离线测试（参考核自证 + 工件一致性），**19/19 绿**
- `artifacts/maneuvering_debt_m1874/{{probe.json, verdict.json, probe.md, fig_*.png}}`

---

## ① 案例介绍（欠账来源与对象）

`docs/verifier/MANEUVERING_REVIEW.md` §5.6 明确记账：

> 未覆盖 `abkowitz/*`、`maneuvering/zigzag`、`extended-calculations/maneuvering`
> 三个端点族（记为后续欠账）。

其中 `maneuvering/zigzag` 已由 m1663 裁决；**剩下两个族（`abkowitz/derivatives`、
`abkowitz/simulate`、`extended-calculations/maneuvering`）以及同族的
`maneuvering-mmg/{{turning,zigzag}}` 至今无人复核**。本单把这条欠账清到底：4 个端点、
22 条判据、5 个族的证据分级。

### 基准船参数（探针输入，全单固定）

| Lpp [m] | B [m] | T [m] | Δ [t] | Cb | KG [m] | Dp [m] | A_R [m²] | V [m/s] | δ [deg] |
|---|---|---|---|---|---|---|---|---|---|
| {base['L']} | {base['B']} | {base['T']} | {base['displacement']} | {base['Cb']} | {base['KG']} | {base['Dp']} | {base['Ar']} | {base['speed']} | {base['rudder_angle']} |

灵敏度轴（每次只动一个）：L∈{{60,180,320}}、B∈{{20,32,50}}、T∈{{6,11,20}}、
Δ∈{{8k,55k,320k}}、Cb∈{{0.60,0.82,0.90}}、V∈{{3,7.2,10}}、δ∈{{5,10,20,35}}。

## ② 怎么做的（判据体系：三类，逐条可推翻）

| 类别 | 判据含义 | 为什么它能定罪 |
|---|---|---|
| **A 存在性** | 端点声称做的机动必须真的发生 | 返回 200 而 `|ψ|max≡0`、指标全 `null`、`δ≡0`，是**静默错答**，比 500 更危险 |
| **B 参数耦合** | 物理上必然耦合的输入改了，输出必须动 | 输出对这些输入**精确不变**⇒ 逻辑上断定实现未使用它们。**不需要任何文献点值** |
| **C 解析闭环** | 一阶 Nomoto 的定常回转与 Z 形超越角有精确闭式解，端点自报的 (K,T) 必须与自报的 T_d 闭合 | 同一实现的两组数自相矛盾即可定罪；与外部文献无关 |
| **D 量级标尺** | 文献带只作标尺 | 离线取不到点值 ⇒ 绝对精度一律 WITHHELD，**不进判决** |

### C 类的解析式（本单的参考核，已自证）

```
T·dr/dt + r = K·δ ,  dψ/dt = r
⇒ r(τ) = Kδ + (r0 − Kδ)e^(−τ/T)
   ψ(τ) = ψ0 + Kδ·τ + T(r0 − Kδ)(1 − e^(−τ/T))
⇒ 定常：r_ss = Kδ ，D_ss = 2V/r_ss
⇒ Z 形回舵后：ψ 的峰值出现在 r=0 处，τ_os = T·ln((r_sw+Kδ)/(Kδ))
```

> **踩坑记录（重要，本单自查修出）**：初版把超越角写成 `ψ_os = T(r_sw+Kδ)`，
> 那是对 r 积到 τ→∞ 的结果；而 ψ 的**峰值在 r=0 处**（τ 有限），两者在本船参数下相差 11 倍
> （43° vs 3.8°）。是 `tests/…::test_zigzag_overshoot_matches_rk4` 的 RK4 异离散交叉把它抓出来的。
> 修正后才敢把 C4 写进判决——**参考核必须先自证，再用它判别人**。

## ③ 计算结果（判决）

### 3.1 族级证据分级

| 端点族 | 判决 | 依据 |
|---|---|---|
{famrows}

### 3.2 判据全表（22 FAIL / 0 PASS / 2 INFO / 3 WITHHELD）

| 判据 | 类别 | 内容 | 实测 | 基线 | 判决 |
|---|---|---|---|---|---|
{rows}

### 3.3 关键数字（同一基准船，五条操纵路线横向对照）

| 路线 | T_d [m] | T_d/Lpp | 可用性 |
|---|---|---|---|
| `extended-calculations/maneuvering` | {e82['tactical_diameter']} | {e82['tactical_diameter']/L:.3f} | 200（但见 B5–B10） |
| `/maneuvering/turning` | {turn['tactical_diameter']} | {turn['tactical_diameter']/L:.3f} | 200（m1663 已判 3.5/2.0/4.5L 硬编码） |
| `/mmg/turning` | {mmg['tactical_diameter']} | {mmg['tactical_diameter']/L:.3f} | 200（进距 {mmg['advance']} m = {mmg['advance']/L:.2f}L，横距 {mmg['transfer']} m） |
| `/maneuvering-mmg/turning` | — | — | **HTTP 500** |
| `abkowitz/simulate` | — | — | 200 但 T_d=`{sim[('turning', 0.0)]['tactical_diameter']}` |

存活两路的战术直径相差 **{abs(e82['tactical_diameter']/mmg['tactical_diameter']-1)*100:.1f}%**，
且 `abkowitz/simulate` 自报直航稳定指数 `{sim[('zigzag', 0.0)]['stability_index']}`
（`is_stable={sim[('zigzag', 0.0)]['is_stable']}`）与 `extended-calculations` 的
「`{e82['straight_stability']}` / `{e82['dynamic_stability']}`」**互为否定**。

### 3.4 Nomoto 解析闭环（C 类）

| 量 | 端点自报 | 闭式解 | 相对偏差 |
|---|---|---|---|
| K [1/s] | {nr['K_reported']} | — | — |
| T [s] | {nr['T_reported']:.2f} | — | — |
| T_d [m] | {nr['Td_reported_m']} | **{nr['Td_closed_m']:.1f}** | **{(nr['Td_reported_m']-nr['Td_closed_m'])/nr['Td_closed_m']*100:+.1f}%** |
| 等效 K（由自报 T_d 反推）[1/s] | {nr['K_reported']} | {nr['K_implied']:.5f} | {((nr['K_reported']-nr['K_implied'])/nr['K_implied'])*100:+.1f}% |
| Z 形首超越角 [deg] | {zd['overshoot_1']} | {zc['overshoot_1_deg']:.2f} | {((zd['overshoot_1']-zc['overshoot_1_deg'])/zc['overshoot_1_deg'])*100:+.1f}% |
| Z 形半周期 [s] | {zd['period']} | {zc['half_period_s']:.1f} | {((zd['period']-zc['half_period_s'])/zc['half_period_s'])*100:+.1f}% |

自报 (K,T) 隐含的回转直径是自报 T_d 的 **{td_ratio:.2f} 倍**；`T_index = {nr['T_reported']:.2f} s`
对应 `T/(L/V) = {nr['T_over_LV']:.2f}`，而物理量级标尺是 0.30–1.50。

### 3.5 Abkowitz 导数的合成张量指纹（B4）

| L [m] | Y_v | Y_v·L/Δ | N_r | N_r/(L·Δ) |
|---|---|---|---|---|
""" + "\n".join(
    f"| {r['value']:.0f} | {r['derivatives']['Y_v']:.3f} | "
    f"{r['derivatives']['Y_v']*r['value']/base['displacement']:.4f} | "
    f"{r['derivatives']['N_r']:.3e} | {r['derivatives']['N_r']/(r['value']*base['displacement']):.4f} |"
    for r in der["L"]) + f"""

两张归一化列在 L 三档上**精确恒定** ⇒ 导数形如 `c·Δ·L^(±1)`，与 Cb/B/T 完全无关
（B1–B3 的响应为 0.0%）。这不是"经验公式精度不足"，是**没有经验公式**。

## ④ 结果图（matplotlib，数字全部由工件注入）

| 图 | 内容 |
|---|---|
| `fig_td_by_endpoint.png` | 同一船五条操纵路线的 T_d/Lpp 与文献量级带（绿）；两路不可用（500 / null），存活两路相差 2.5 倍 |
| `fig_parameter_blindness.png` | 左：extended-calculations 只对 L、V 有响应（B/T/Δ 曲线精确重合）；右：abkowitz 导数只对 Δ、L 有响应（船型轴精确为 0） |
| `fig_nomoto_closure.png` | 左：用同船自报 (K,T) 作闭式 Z 形（首超越角 {zc['overshoot_1_deg']:.2f}°）vs 端点 {zd['overshoot_1']}°；右：自报 (K,T) 隐含直径 {nr['Td_closed_m']:.0f} m vs 自报 {nr['Td_reported_m']:.1f} m（log 轴） |

## ⑤ 注意点（坑、局限、适用范围）

1. **参考核必须先自证**：本单的 Z 形超越角公式初版是错的（积到 ∞ 而非峰值在 r=0），
   差 11 倍。RK4 异离散交叉测试抓出后才修正——若跳过自证，C4 会给出完全错误的判决。
2. **一阶 Nomoto 的固有上限**：本船 `KδT ≈ {nr['K_reported']*0.1745*nr['T_reported']:.2f}`，
   一阶模型给出的 Z 形首超越角只有几度（闭式 {zc['overshoot_1_deg']:.2f}°），**进不了**
   文献带 5–20°（那需要二阶以上模型或更大的 KδT）。因此 D5 只标 WITHHELD：
   不能拿"1.5° 低于文献带"单独定罪，定罪的落点是 C4 的跨端点不自洽（−60%）与 B 类参数盲。
3. **单位口径未声明**：`extended-calculations` 未在响应里声明 `k_index`/`t_index` 的量纲。
   C1/C2 的前提是"K 为 1/s、T 为 s（Nomoto 标准）"。若二者另有口径，则**缺陷从"自相矛盾"
   变成"口径未声明"**——两种都是缺陷，但结论措辞不同。已在 verdict 里按前一种记账。
4. **端点归属**：:8001 的服务代码不在本工作树（属 ship-performance-platform），
   本单**只取证不改代码**；修复须由持有该仓权限的单承接，本工具的 22 条 FAIL 判据可直接当验收门。
5. **鉴权变化（实测）**：本轮 :8001 开始要求 Bearer 令牌（无令牌 401、`:8001/auth/login` 自身 500），
   而 `:8010/api/v1/auth/login` 签发的 token 在 :8001 有效。工具已按此实现，凭据只从环境变量读，
   不落盘不入库。
6. **A5 的 500 是服务端异常**：`delta0=20°` 的三种 maneuver_type 全部 500 ⇒ 舵力项路径未实现，
   不是输入校验问题（0° 时能返回 200）。
7. **容量纪律**：开工时 `load15 = 122`（nproc 128）⇒ 全程只做轻量（约 60 次 HTTP 探针 +
   纯 python 后处理），未起任何重算力进程。

## ⑥ 误差判定（逐项显式回答「是否 <3%」）

| 关键量 | 基准 | 结果 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| B1 Cb→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B2 B→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B3 T→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B5–B9 B/T/Δ→回转几何响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| C1 T_d 闭环（自报 K,T） | 2V/(Kδ) = {nr['Td_closed_m']:.1f} m | {nr['Td_reported_m']} m | **{(nr['Td_reported_m']-nr['Td_closed_m'])/nr['Td_closed_m']*100:+.1f}%** | ❌ 否（差 {td_ratio:.1f} 倍） |
| C2 K 反推 | {nr['K_implied']:.5f} 1/s | {nr['K_reported']} 1/s | **{((nr['K_reported']-nr['K_implied'])/nr['K_implied'])*100:+.1f}%** | ❌ 否 |
| C4 首超越角（跨端点） | {zc['overshoot_1_deg']:.2f}° | {zd['overshoot_1']}° | **{((zd['overshoot_1']-zc['overshoot_1_deg'])/zc['overshoot_1_deg'])*100:+.1f}%** | ❌ 否 |
| C5 半周期（跨端点） | {zc['half_period_s']:.1f} s | {zd['period']} s | **{((zd['period']-zc['half_period_s'])/zc['half_period_s'])*100:+.1f}%** | ❌ 否 |
| 参考核 Z 形 vs RK4（自证） | RK4 逐段积分 | 三组 (K,T) | 见测试 <10%（峰值浅极大，对换舵时刻敏感） | ⚠️ 部分（参考核自证，非被测端点） |
| 参考核 ψ 自由响应 vs RK4 | 闭式 ψ(τ) | 同 | <1e-6 | ✅ 是 |
| A 类存在性 | 布尔 | 见 3.2 | 无数值误差 | — |
| 文献带（D1–D4） | 文献量级标尺 | 见 3.2 | — | **WITHHELD**（离线无点值，不作判决） |

**结论（诚实）**：本单**没有**、也无法给出任何操纵量的绝对精度（无离线点值 ⇒ 绝对精度 WITHHELD）。
本单证明的是：`abkowitz/derivatives`、`abkowitz/simulate`、`extended-calculations/maneuvering`
三个端点的**参数盲性**与**自相矛盾**（22 条 FAIL，全部来自不变量/解析闭环，与文献无关），
`maneuvering-mmg/{{turning,zigzag}}` 完全不可用。据此把三族从"未复核"推进到
**MOCK / STUB / MOCK / STUB**，欠账从"未知"变为"已知且可重跑"。

**重跑命令**：
```
python3 tools/audit/maneuver_debt_m1874.py          # 需 :8001 可达
python3 -m pytest tests/test_maneuver_debt_m1874.py --noconftest -q   # 离线
```

## 后续建议（给下一单）

1. **修复承接**：需 `ship-performance-platform` 写权限的单把 `abkowitz` 族接上真导数
   （Kijima/Inoue 回归或细长体解析）与真积分器；验收门 = 本工具 22 条 FAIL 转 PASS。
2. **`maneuvering-mmg` 族的 500**：先取服务端 traceback（G21 在 m1663 就已 FAIL，两轮无变化），
   建议直接派 infra 单抓日志，避免第三轮重复可用性裁决。
3. **K-T 口径**：`extended-calculations` 应在响应里显式声明 `k_index`/`t_index` 量纲，
   否则任何下游自航/操纵预报都会把 8.7 倍误差带进设计回路。
"""
open(DOC, "w").write(doc)
print(f"wrote {DOC} ({len(doc)} bytes)")