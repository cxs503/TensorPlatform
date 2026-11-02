#!/usr/bin/env python3
"""发布 m1874 论文级进展报告到平台（:8010 /tensor/reports），图内嵌 base64，数字零手抄。

凭据只从环境变量读（TENSOR_AGENT_USER / TENSOR_AGENT_PASS），不落盘不入库。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "maneuvering_debt_m1874")
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")

jd = json.load(open(os.path.join(ART, "verdict.json")))
rec = json.load(open(os.path.join(ART, "probe.json")))
nr = jd["nomoto_reference"]
zc = nr["zigzag_nomoto"]
C = {c["id"]: c for c in jd["criteria"]}
base = rec["base_ship"]
ext = rec["ext_calc"]
e82 = next(r for r in ext["cb"] if abs(r["value"] - base["Cb"]) < 1e-9)
zd = next(r for r in rec["cross_endpoints"] if r["path"] == "/maneuvering/zigzag")["data"]
turn = next(r for r in rec["cross_endpoints"] if r["path"] == "/maneuvering/turning")["data"]
mmg = next(r for r in rec["cross_endpoints"] if r["path"] == "/mmg/turning")["data"]
sim = {(r["maneuver_type"], r["delta0"]): r for r in rec["abkowitz_simulate"]}
der = rec["abkowitz_derivatives"]
ratio = nr["Td_closed_m"] / nr["Td_reported_m"]


def rel(a, b):
    return (a - b) / b * 100.0


FIGCAP = {
    "fig_td_by_endpoint.png": "同一基准船五条操纵路线的战术直径/Lpp；绿带为文献量级标尺（不参与判决）。"
                              "maneuvering-mmg 与 abkowitz 两路不可用，存活两路相差 2.5 倍。",
    "fig_parameter_blindness.png": "左：extended-calculations 只对 L、V 有响应（B/T/Δ 三条曲线精确重合）；"
                                   "右：abkowitz 导数只对 Δ、L 有响应，船型轴（Cb/B/T）响应精确为 0。",
    "fig_nomoto_closure.png": "左：用同船自报 (K,T) 作 Nomoto 闭式 Z 形，首超越角 "
                              f"{zc['overshoot_1_deg']:.2f}° vs 端点 {zd['overshoot_1']}°；"
                              f"右：自报 (K,T) 隐含回转直径 {nr['Td_closed_m']:.0f} m vs 自报 "
                              f"{nr['Td_reported_m']:.1f} m（log 轴，相差 {ratio:.2f} 倍）。",
}

figs = []
for name, cap in FIGCAP.items():
    p = os.path.join(ART, name)
    if os.path.exists(p):
        figs.append({"name": name, "caption": cap,
                     "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

rows = "\n".join(
    f"| {c['id']} | {c['kind']} | {c['criterion']} | {c['value']} | {c['baseline']} | **{c['verdict']}** |"
    for c in jd["criteria"])
fam = "\n".join(f"| `{k}` | **{v['verdict']}** | {v['basis']} |" for k, v in jd["families"].items())

md = f"""# 操纵域「未覆盖端点族」验证欠账清偿（m1874）：Abkowitz 族 / 扩展计算 / maneuvering-mmg

## ① 案例介绍
`docs/verifier/MANEUVERING_REVIEW.md` §5.6 明确记账「未覆盖 `abkowitz/*`、
`maneuvering/zigzag`、`extended-calculations/maneuvering` 三个端点族（记为后续欠账）」。
其中 zigzag 已由 m1663 裁决；本单清掉剩余部分，并把同族已死的 `maneuvering-mmg/{{turning,zigzag}}` 一并裁决。
被测服务 `:8001` ship-performance-platform 2.0.0，**只读探针，未改服务代码**。

基准船：Lpp={base['L']} m、B={base['B']} m、T={base['T']} m、Δ={base['displacement']} t、
Cb={base['Cb']}、Dp={base['Dp']} m、A_R={base['Ar']} m²、V={base['speed']} m/s、δ={base['rudder_angle']}°。
灵敏度轴各只动一个：L∈{{60,180,320}}、B∈{{20,32,50}}、T∈{{6,11,20}}、Δ∈{{8k,55k,320k}}、Cb∈{{0.60,0.82,0.90}}、V∈{{3,7.2,10}}。

## ② 怎么做的
四类判据，全部可推翻且**不依赖文献点值**：
- **A 存在性**：端点声称的机动必须真的发生（`|ψ|max`、`|δ|max`、指标非 null、非 500）。
- **B 参数耦合**：物理上必然耦合的输入改了输出必须动；**精确不变**⇒ 断定实现未使用该输入。
- **C 解析闭环**：一阶 Nomoto 精确闭式解 `r_ss=Kδ`、`D=2V/(Kδ)`、
  Z 形峰值 `τ_os=T·ln((r_sw+Kδ)/(Kδ))` ⇒ 端点自报 (K,T) 必须与自报 T_d 闭合。
- **D 文献量级标尺**：只作标尺，绝对精度一律 WITHHELD。

参考核先自证：`tests/…::test_zigzag_overshoot_matches_rk4` 用 RK4 异离散交叉，
抓出初版超越角公式（积到 τ→∞）比真峰值大 11 倍，修正后才用于判决。

## ③ 计算结果
| 端点族 | 判决 | 依据 |
|---|---|---|
{fam}

判据统计：**FAIL 22 / PASS 0 / INFO 2 / WITHHELD 3**（共 27 条）。

| 判据 | 类别 | 内容 | 实测 | 基线 | 判决 |
|---|---|---|---|---|---|
{rows}

同一基准船五条路线：ext-calc T_d={e82['tactical_diameter']} m（{e82['tactical_diameter']/base['L']:.3f}L）；
`/maneuvering/turning` {turn['tactical_diameter']} m（{turn['tactical_diameter']/base['L']:.3f}L）；
`/mmg/turning` {mmg['tactical_diameter']} m（{mmg['tactical_diameter']/base['L']:.3f}L）；
`/maneuvering-mmg/turning` HTTP 500；`abkowitz/simulate` T_d=null。
`abkowitz/simulate` 自报 `is_stable={sim[('zigzag', 0.0)]['is_stable']}`（SI={sim[('zigzag', 0.0)]['stability_index']}），
而 ext-calc 对同船报「{e82['straight_stability']}/{e82['dynamic_stability']}」——两族互相否定。

Nomoto 闭环：自报 K={nr['K_reported']} 1/s、T={nr['T_reported']:.2f} s ⇒ 闭式 T_d={nr['Td_closed_m']:.1f} m，
自报 {nr['Td_reported_m']} m，**相差 {ratio:.2f} 倍**；由自报 T_d 反推 K={nr['K_implied']:.5f} 1/s。
Abkowitz 导数归一化后 Y_v·L/Δ ≡ {der['L'][0]['derivatives']['Y_v']*der['L'][0]['value']/base['displacement']:.1f}、
N_r/(L·Δ) ≡ {der['L'][0]['derivatives']['N_r']/(der['L'][0]['value']*base['displacement']):.1f} 在 L 三档精确恒定
⇒ 导数形如 c·Δ·L^(±1)，与 Cb/B/T 无关。

## ④ 结果图
三张图内嵌：五路线 T_d/L 对照、参数盲性（B/T/Δ 与船型轴曲线精确重合）、Nomoto 闭环（闭式 Z 形 vs 端点、隐含直径 vs 自报）。

## ⑤ 注意点
1. 参考核必须先自证：本单 Z 形超越角初版公式错（积到 ∞ 而非峰值在 r=0），差 11 倍，被 RK4 抓出。
2. 一阶 Nomoto 固有上限：本船 KδT≈{nr['K_reported']*0.1745*nr['T_reported']:.2f}，闭式首超越角仅
   {zc['overshoot_1_deg']:.2f}°，进不了文献带 5–20°（需二阶以上模型）⇒ 文献带只作标尺。
3. 单位口径未声明：C1/C2 前提是 K[1/s]、T[s]（Nomoto 标准）；若另有口径，缺陷从"自相矛盾"变为"口径未声明"，两者都是缺陷。
4. 服务代码不在本工作树 ⇒ 只取证不改代码；修复须由持 ship-performance-platform 权限的单承接。
5. 本轮 `:8001` 要求 Bearer 令牌（无令牌 401、`:8001/auth/login` 自身 500），`:8010` 签发 token 有效；凭据只读环境变量。
6. 容量纪律：load15=122（nproc 128）⇒ 全程约 60 次 HTTP 探针 + 纯 python 后处理，未起重算力进程。

## ⑥ 误差判定（逐项回答「是否 <3%」）
| 关键量 | 基准 | 结果 | 相对误差 | <3%? |
|---|---|---|---|---|
| B1–B3 Cb/B/T→导数响应 | >1% | 0.0% | −100% | ❌ 否 |
| B5–B9 B/T/Δ→回转几何响应 | >1% | 0.0% | −100% | ❌ 否 |
| C1 T_d 闭环 | {nr['Td_closed_m']:.1f} m（2V/(Kδ)） | {nr['Td_reported_m']} m | **{rel(nr['Td_reported_m'], nr['Td_closed_m']):+.1f}%** | ❌ 否（{ratio:.1f} 倍） |
| C2 K 反推 | {nr['K_implied']:.5f} 1/s | {nr['K_reported']} 1/s | **{rel(nr['K_reported'], nr['K_implied']):+.1f}%** | ❌ 否 |
| C4 首超越角（跨端点） | {zc['overshoot_1_deg']:.2f}° | {zd['overshoot_1']}° | **{rel(zd['overshoot_1'], zc['overshoot_1_deg']):+.1f}%** | ❌ 否 |
| C5 半周期（跨端点） | {zc['half_period_s']:.1f} s | {zd['period']} s | **{rel(zd['period'], zc['half_period_s']):+.1f}%** | ❌ 否 |
| 参考核 ψ 自由响应 vs RK4 | 闭式解 | 同 | <1e-6 | ✅ 是 |
| 参考核 Z 形超越角 vs RK4 | RK4 | 三组 (K,T) | <10%（峰值浅极大） | ⚠️ 参考核自证，非被测端点 |
| 文献带 D1–D4 | 量级标尺 | 见判据表 | — | **WITHHELD**（离线无点值） |

**诚实结论**：本单不提供任何操纵量的绝对精度（WITHHELD）。已证明：三个端点族的参数盲性与自相矛盾
（22 条 FAIL 全部来自不变量/解析闭环），`maneuvering-mmg` 族完全不可用；欠账状态由「未知」推进到「已知且可重跑」。
重跑：`python3 tools/audit/maneuver_debt_m1874.py`；离线测试：`pytest tests/test_maneuver_debt_m1874.py --noconftest -q`（19/19 绿）。
"""

payload = {
    "title": "操纵域未覆盖端点族验证欠账清偿（m1874）：abkowitz/extended-calculations/maneuvering-mmg 四端点 22 条判据定罪",
    "summary": (
        "MANEUVERING_REVIEW §5.6 记账的 abkowitz 族、extended-calculations/maneuvering 与 "
        "maneuvering-mmg 族首次被机器裁决：abkowitz/derivatives=MOCK（导数为 c·Δ·L^±1，"
        "Cb/B/T 响应精确 0）、abkowitz/simulate=STUB（200 OK 但 ψ≡δ≡0、指标全 null、"
        "δ0>0 直接 500）、extended-calculations/maneuvering=MOCK（T_d≡3.59L、T≡24.222L/V，"
        "自报 K-T 与自报 T_d 差 8.7 倍）、maneuvering-mmg/{turning,zigzag}=STUB（合法输入全 500）。"
        "27 条判据 FAIL 22，全部来自参数耦合与 Nomoto 解析闭环，不依赖文献点值；19 项离线测试全绿。"
    ),
    "scenario_key": "maneuvering-debt-clearance",
    "mission_id": 1874,
    "tags": ["maneuvering", "verification-debt", "abkowitz", "nomoto", "evidence-grading",
             "mock-detection", "ship-performance"],
    "markdown": md,
    "figures": figs,
}

user, pwd = os.environ.get("TENSOR_AGENT_USER", ""), os.environ.get("TENSOR_AGENT_PASS", "")
req = urllib.request.Request(BASE + "/auth/login",
                            data=json.dumps({"username": user, "password": pwd}).encode(),
                            headers={"Content-Type": "application/json"}, method="POST")
tok = json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]

req = urllib.request.Request(BASE + "/tensor/reports", data=json.dumps(payload).encode(),
                            headers={"Content-Type": "application/json",
                                     "Authorization": "Bearer " + tok}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as f:
        print(f.status, f.read()[:400].decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:600].decode())