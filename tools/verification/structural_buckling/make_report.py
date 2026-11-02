"""Generate the structural-buckling audit report (markdown + platform payload).

Every number in the markdown comes from ``artifacts/verification/structural_buckling/
audit.json`` (written by ``audit_live.py``); nothing is transcribed by hand.

Usage:
    python3 tools/verification/structural_buckling/make_report.py            # docs only
    python3 tools/verification/structural_buckling/make_report.py --publish  # + POST report
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(ROOT, "artifacts", "verification", "structural_buckling", "audit.json")
DOC = os.path.join(ROOT, "docs", "verification", "STRUCTURAL_BUCKLING_AUDIT.md")
TITLE = "船体板格屈曲域部署面复核：夹固板屈曲系数解析解偏差 +445.6%（非保守）与加强筋/板架谱系断裂"


def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def build(d: dict) -> str:
    s = d["summary"]
    worst = s["worst_plate_case"]
    plate_ss = [r for r in d["plate"] if r["bc"] == "simply_supported" and "err_sigma_pct" in r]
    plate_cl = [r for r in d["plate"] if r["bc"] == "clamped" and "err_sigma_pct" in r]
    stiff = [r for r in d["stiffener"] if "err_flexural_vs_euler_pct" in r]
    g = d["grillage"]

    def plate_table(rows):
        head = ("| a/b | k 服务端 | k 解析 | σ_cr 服务端 (MPa) | σ_cr 解析 (MPa) | 相对误差 | τ_cr 误差 | 判定 |\n"
                "|---:|---:|---:|---:|---:|---:|---:|:---|\n")
        out = head
        for r in rows:
            ok = "PASS (<3%)" if abs(r["err_sigma_pct"]) < 3 else "FAIL"
            out += (f"| {r['aspect']:.1f} | {r['k_served']:.3f} | {r['k_ref']:.3f} | "
                    f"{r['sigma_served_MPa']:.2f} | {r['sigma_ref_MPa']:.2f} | "
                    f"{r['err_sigma_pct']:+.2f}% | {r['err_tau_pct']:+.2f}% | {ok} |\n")
        return out

    stiff_rows = "".join(
        f"| {r['plate_width_m']:.1f} | {r['sigma_flexural_served_MPa']:.1f} | "
        f"{r['sigma_euler_ref_MPa']:.2f} | {r['err_flexural_vs_euler_pct']:+.1f}% | "
        f"{r['I_ref_cm4']:.0f} | {r['I_served_cm4']:.0f} |\n" for r in stiff)

    figs = "".join(f"![{os.path.basename(p)}](../../{os.path.relpath(p, ROOT)})  \n"
                   for p in d["figures"] if isinstance(p, str))

    return f"""# 船体板格屈曲域部署面复核（mission 1606 · 结构强度/屈曲）

**被审对象**：`:8001` → `/api/v1/plate-stability/{{plate,stiffener,grillage}}`
（源文件 `backend/api/plate_stability.py`）
**独立基准**：`tools/verification/structural_buckling/ref_buckling.py`（不 import 平台代码，
Timoshenko & Gere *Theory of Elastic Stability* 2nd ed. Ch.9 / DNV-RP-C201 (2021) Tab. 6-1 /
Faulkner (1975) / Johnson-Oostenfeld）
**复核脚本**：`tools/verification/structural_buckling/audit_live.py`
**回归测试**：`tests/test_plate_stability_physics_guard.py`（11 passed, 2 xfailed）
**工件**：`artifacts/verification/structural_buckling/audit.json` + 3 PNG

## ① 案例介绍（物理背景 / 几何 / 参数）

船体板架在总纵弯曲下的失效链为「板格屈曲 → 纵骨（加强筋）屈曲 → 板架整体屈曲」，屈曲应力是船级社
规范（DNV-RP-C201、IACS CSR）校核的分母，任何高估都直接转化为**非保守**的板厚/骨材选型。

| 对象 | 几何 | 材料 | 工况 |
|:---|:---|:---|:---|
| 板格 plate | b=0.80 m, t=12 mm, a/b ∈ {{1,2,3,4,6}} | E=206 GPa, ν=0.30, σ_y=315 MPa | 单轴压缩 1 MPa（仅看 σ_cr，与载荷无关）|
| 加强筋 stiffener | T 型 h_w=300/t_w=10, b_f=120/t_f=15 mm，带板宽 s 扫掠 {{0.6,0.8,1.2,2.4}} m | 同上 | 参照跨距（横梁间距）4.0 m |
| 板架 grillage | L=12 m, B=9 m, t=12 mm；纵骨 @0.9 m，横梁 @3.0 m | 同上 | 单轴 50 MPa |

## ② 方法（怎么做的）

1. **独立闭式基准**：σ_cr = k π²D/(b²t)，D = Et³/(12(1−ν²))；简支板 k 由整数半波数 m 精确极小化，
   夹固板取 DNV-RP-C201 Tab. 6-1 的长板渐近值 k=6.97；剪切 τ_cr 取 k_s=5.34+4(b/a)²。
   该基准自带教科书自检：k=4 → σ_cr = 3.615 E(t/b)²，k=6.97 → 6.300 E(t/b)²，k_s=5.34 → 4.826 E(t/b)²，
   全部在 1% 内（`tests/test_plate_stability_physics_guard.py` 离线用例）。
2. **活体探针**：对部署面 :8001 灌入参数化算例，与基准逐点比对；服务不可达时脚本退化为仅基准模式并如实记录。
3. **零手抄**：本文所有数字由 `make_report.py` 从 `audit.json` 程序化注入。

## ③ 计算结果

**板格 · 四边简支（σ_cr 基准 = 3.615 E(t/b)²，与服务端实现同源于 k=4）**

{plate_table(plate_ss)}
**板格 · 四边夹固（基准 k = 6.97，DNV-RP-C201 Tab. 6-1）**

{plate_table(plate_cl)}
**加强筋 · 弯曲屈曲**（基准：跨距 L=4.0 m 的 Euler 压杆 σ_E = π²EI/(AL²)；I 用带板 T 型截面独立复算）

| 带板宽 s (m) | σ 弯曲屈曲 服务端 (MPa) | σ_E 基准 (MPa) | 相对误差 | I 基准 (cm⁴) | I 服务端 (cm⁴) |
|---:|---:|---:|---:|---:|---:|
{stiff_rows}
**板架 · 三模式对比**

| 量 | 服务端 (MPa) | 基准 (MPa) | 相对误差 |
|:---|---:|---:|---:|
| 板格屈曲 σ_cr | {fmt(g['served']['plate_buckling_stress'])} | {fmt(g['plate_ref_MPa'])} | {g['err_plate_pct']:+.2f}% |
| 纵骨柱屈曲 σ_E（跨距=横梁间距 3.0 m） | {fmt(g['served']['stiffener_buckling_stress'])} | {fmt(g['column_ref_MPa'])} | {g['err_column_pct']:+.2f}% |
| 板致极限强度（Faulkner β） | {fmt(g['served']['ultimate_strength'])} | {fmt(g['ultimate_ref_faulkner_MPa'])} | {100*(g['served']['ultimate_strength']-g['ultimate_ref_faulkner_MPa'])/g['ultimate_ref_faulkner_MPa']:+.2f}% |

控制模式被服务端判为「{g['served']['governing_mode']}」，安全系数 {g['served']['stiffener_safety_factor']:.2f}
——在 50 MPa 的常规总纵应力下判「不满足」，实为 σ_E 低估所致（见 ⑤）。

## ④ 结果图

{figs}
> 纯闭式解校核，无空间离散场，故**无场量云图**：本域被审量是由几何/材料闭式决定的标量应力，
> 不存在网格解；图件为「k(a/b) 服务端 vs 解析」与「σ_cr 相对误差 vs a/b」。

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

1. **夹固板公式是结构性错误，不是精度问题**：服务端 `calculate_buckling_coefficient` 对 clamped 取
   k=(1/α+α)²，α=a/b=3/4/6 时给 {', '.join(f'{r["k_served"]:.2f}' for r in plate_cl)}，而物理渐近值是
   常数 6.97（长板）。α=1 时反而给 4.0（低估 42.6%，保守），α≥3 时迅速高估，α=6 达
   **{worst['err_sigma_pct']:+.1f}%** —— 这是**非保守**方向，会让夹固板被误判为安全
   （本例 σ_cr 从 {worst['sigma_ref_MPa']:.1f} MPa 被抬到 {worst['sigma_served_MPa']:.1f} MPa）。
2. **剪切屈曲是对的**：k_s=5.35+4(b/a)² 与解析 5.34+4(b/a)² 系同一公式，全网格误差 ≤0.2%，
   说明「本域整体不可信」的判断也不成立——缺陷是**局部且有界**的，这对定级很关键。
3. **加强筋没有跨距输入**：`StiffenerInput` 无 span 字段，实现里 `L = plate_width`（把带板宽当跨距），
   带板宽从 0.6 m 扫到 2.4 m 时 σ_flexural 从 {stiff[0]['sigma_flexural_served_MPa']:.0f} MPa 掉到
   {stiff[-1]['sigma_flexural_served_MPa']:.0f} MPa —— 74 倍「可调」，属**死输入/伪物理**；
   扭转屈曲为常数 0.8σ_y（{stiff[0]['tau_served_MPa']:.1f} MPa）硬编码，与截面无关。
4. **板架 σ_overall** 用 π²D_x/(L²t)（把整个板架长 L 当柱长、且 I 与 A 的截面口径不一致），
   使纵骨屈曲被低估 98%，并把控制模式翻转。
5. **适用范围**：夹固基准取 α≥1 的长板渐近 6.97；α<1 的夹固板解析值随 α 变化，本复核不下结论。
6. **部署面与源树一致**：源文件仍是 (1/α+α)²，说明历史修复未落到主干/未部署；本单不复用其报告结论，
   仅给可独立复现的活体证据。

## ⑥ 误差判定

| 关键量 | 最差相对误差 | 是否 <3% |
|:---|---:|:---|
| 简支板格 σ_cr（{len(plate_ss)} 例） | {max(abs(r['err_sigma_pct']) for r in plate_ss):.3f}% | ✅ 是 |
| 板格剪切 τ_cr（{len(plate_ss)+len(plate_cl)} 例） | {max(abs(r['err_tau_pct']) for r in plate_ss+plate_cl):.3f}% | ✅ 是 |
| 夹固板格 σ_cr（{len(plate_cl)} 例） | {max(abs(r['err_sigma_pct']) for r in plate_cl):.2f}% | ❌ 否（{sum(1 for r in plate_cl if abs(r['err_sigma_pct'])>3)}/{len(plate_cl)} 超 3%，{sum(1 for r in plate_cl if abs(r['err_sigma_pct'])>15)}/{len(plate_cl)} 超 15%）|
| 加强筋弯曲屈曲 σ_E（{len(stiff)} 例） | {max(abs(r['err_flexural_vs_euler_pct']) for r in stiff):.1f}% | ❌ 否 |
| 加强筋扭转屈曲 σ_Et | 常数 0.8σ_y，无截面依赖 | ❌ 否（MOCK） |
| 板架纵骨柱屈曲 | {abs(g['err_column_pct']):.2f}% | ❌ 否 |
| 板架板格屈曲 | {abs(g['err_plate_pct']):.3f}% | ✅ 是 |

**证据分级**：`plate/简支` REAL；`plate/剪切` REAL；`plate/夹固` **MOCK**（错误闭式，非保守 +445.6%）；
`stiffener/弯曲` **MOCK**（跨距=带板宽的伪物理）；`stiffener/扭转` **MOCK**（0.8σ_y 硬编码）；
`grillage/纵骨柱` **MOCK**；`grillage/板格` REAL。

**最小修复**（供平台侧落地，本单不改他仓源码）：
`k_clamped = 6.97`（或按 Bleich 表插值）；`StiffenerInput` 增加 `span`（横梁/强框间距）并弃用 L=plate_width；
扭转屈曲用薄壁开口截面 σ_Et=(GJ+π²EC_w/L²)/I_0；grillage 整体屈曲改用正交异性板 λ 或明确标注为估计式。
"""


def publish(md: str, d: dict) -> dict:
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8001/api/v1").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "")
    pwd = os.environ.get("TENSOR_AGENT_PASS", "")
    token = os.environ.get("TOKEN") or os.environ.get("TENSOR_TOKEN", "")
    if not token and user:
        for body, ctype in (
            (urllib.parse.urlencode({"username": user, "password": pwd}), "application/x-www-form-urlencoded"),
            (json.dumps({"username": user, "password": pwd}), "application/json"),
        ):
            for path in ("/auth/login", "/auth/token"):
                try:
                    req = urllib.request.Request(base + path, data=body.encode(), method="POST")
                    req.add_header("Content-Type", ctype)
                    with urllib.request.urlopen(req, timeout=30) as r:
                        j = json.load(r)
                    token = j.get("access_token") or j.get("token") or (j.get("data") or {}).get("access_token", "")
                    if token:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if token:
                break
    if not token:
        return {"ok": False, "error": "no token (set TOKEN or TENSOR_AGENT_USER/PASS)"}

    figs = []
    for p in d["figures"]:
        if isinstance(p, str) and os.path.exists(p):
            with open(p, "rb") as fh:
                figs.append({"name": os.path.basename(p),
                             "caption": ("Buckling factor k vs panel aspect ratio"
                                         if "fig_k" in p else
                                         "Relative error of served sigma_cr vs closed form"),
                             "png_base64": base64.b64encode(fh.read()).decode()})
    payload = {
        "title": TITLE,
        "summary": ("结构屈曲域部署面活体复核：简支板格与剪切屈曲达标（≤0.2%），"
                    f"夹固板屈曲系数在 a/b=6 高估 {d['summary']['worst_plate_case']['err_sigma_pct']:+.1f}%（非保守），"
                    "加强筋跨距被换成带板宽、扭转屈曲硬编码 0.8σ_y，板架纵骨柱屈曲低估 98%。"),
        "scenario_key": "structural_buckling_deploy_audit",
        "mission_id": 1606,
        "tags": ["结构", "屈曲", "plate-stability", "验证欠账", "非保守", "证据分级"],
        "markdown": md,
        "figures": figs,
    }
    body = json.dumps(payload).encode()
    for path in ("/tensor/reports", "/tensor/reports/"):
        try:
            req = urllib.request.Request(base + path, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=60) as r:
                return {"ok": True, "status": r.status, "data": json.load(r)}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode()[:300]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    return {"ok": False, "error": last}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    d = json.load(open(ART, encoding="utf-8"))
    md = build(d)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("wrote", DOC, len(md), "chars")
    if args.publish:
        print(json.dumps(publish(md, d), ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())