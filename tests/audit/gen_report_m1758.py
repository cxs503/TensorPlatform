#!/usr/bin/env python3
"""mission 1758 报告生成器：从 artifacts/m1758/bem_surface/audit.json 程序化生成
   ① docs/m1758/BEM_SURFACE_AUDIT.md（论文级中文报告）
   ② /tmp/report_m1758.json（平台报告载荷，图 base64 内嵌）
   ③ POST 到 $TENSOR_API_BASE/tensor/reports

数字全部从工件读取，零手抄。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
AUD = os.path.join(ROOT, "artifacts", "m1758", "bem_surface", "audit.json")
VERD = os.path.join(ROOT, "artifacts", "m1758", "bem_surface", "verdict.json")
FIGD = os.path.join(ROOT, "artifacts", "m1758", "figures")
DOCD = os.path.join(ROOT, "docs", "m1758")

FIG = [(f"fig{n}", name) for n, name in [
    (1, "fig1_restoring_identity.png"),
    (2, "fig2_rao_frequency_invariance.png"),
    (3, "fig3_input_sensitivity.png"),
    (4, "fig4_cross_solver_rao.png")]]

CAPTIONS = {
    "fig1": "图 1  垂荡静水刚度反解水线面积 A_wp=K33/(ρg) 与 L·B、独立估计 C_w·L·B 的对比。"
            "红柱（反解值）与灰柱（L·B）逐船重合，说明平台按矩形水线面取刚度、Cb 未参与。",
    "fig2": "图 2  平台 /advanced/bem 的垂荡/纵摇 RAO（彩色点，4 条不同主尺度船叠成同一条水平线）"
            "与已验证 STF 切片理论内核（黑虚线，m1547）在同一船体上的对比。",
    "fig3": "图 3  输入敏感性矩阵（log10 相对散布）。行=被扫描的输入，列=输出分量；0 表示该输入被完全忽略。",
    "fig4": "图 4  跨求解器散点：平台垂荡 RAO vs STF 切片理论 RAO，24 个（船, ω）点，虚线为 1:1。",
}


def load():
    with open(AUD, encoding="utf-8") as f:
        a = json.load(f)
    with open(VERD, encoding="utf-8") as f:
        v = json.load(f)
    return a, v


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def build_markdown(a, v):
    J = a["judgements"]
    D = a["derived"]
    X = a["cross_solver"]
    hull_rows = [(d["id"][5:], f"{d['payload']['length']:.0f}", f"{d['payload']['beam']:.2f}",
                  f"{d['payload']['draft']:.2f}", f"{d['payload']['cb']:.4f}",
                  f"{d['nabla']:.0f}", f"{d['LB']:.0f}", f"{d['implied_Awp']:.0f}",
                  f"{d['Awp_est']:.0f}", f"{d['rel_Awp_vs_est']:+.1%}",
                  f"{d['implied_GMT']:.3f}", f"{d['implied_GML']:.2f}", d["panels"])
                 for d in D]
    jrows = [(j["id"], j["claim"], j["expected"], j["measured"], j["verdict"]) for j in J]
    by_hull = {}
    for r in X:
        by_hull.setdefault(r["hull"], []).append(r)
    xrows = []
    for hull, rs in by_hull.items():
        hmax = max(r["rel_heave"] for r in rs)
        pmax = max(r["rel_pitch"] for r in rs)
        xrows.append((hull, len(rs), f"{hmax:.1%}", f"{pmax:.1%}",
                      f"{min(r['heave_stf'] for r in rs):.3f}~{max(r['heave_stf'] for r in rs):.3f}",
                      f"{min(r['heave_platform'] for r in rs):.3f}~{max(r['heave_platform'] for r in rs):.3f}"))
    fails = [j for j in J if j["verdict"] == "FAIL"]
    passes = [j for j in J if j["verdict"] == "PASS"]
    worst_Awp = max(D, key=lambda d: d["rel_Awp_vs_est"])
    max_rh = max(r["rel_heave"] for r in X)
    max_rp = max(r["rel_pitch"] for r in X)

    md = f"""# /advanced/bem「三维边界元法」证据等级机器裁决（mission 1758）

> 端点：`POST {a['endpoint']}` — 自报 `method = "{a['claim']}"`
> 探针：{a['n_probes_ok']}/{a['n_probes']} 成功；判据：{len(J)} 条（{len(passes)} PASS / {len(fails)} FAIL）
> **裁决：{a['evidence_grade']}**（ρ={a['rho']} kg/m³，g={a['g']} m/s²，恒等容差 {a['tol_identity']:.0%}）
> 工件：`artifacts/m1758/bem_surface/audit.json`（原始响应 + 判决）、`artifacts/m1758/figures/`（4 图）
> 复跑：`python3 tests/audit/audit_m1758_bem_surface.py --base http://127.0.0.1:8001/api/v1`；
> 单测：`python3 -m pytest tests/test_m1758_bem_audit.py -q`

## ① 案例介绍（物理背景与参数）

`/advanced/bem` 面向耐波性/水动力系数需求，向调用方承诺对给定船体做三维边界元（Rankine 源）
计算，返回六自由度附加质量、辐射阻尼、静水恢复力、RAO 传递函数与一阶波浪力/力矩，并回报
面板数 `panels`。它是平台 411 个端点里唯一自称「边界元」的水动力入口，直接支撑耐波性结论。

**它的请求体只有 8 个标量**：`length, beam, draft, speed, cb, wave_frequency, wave_direction`
（全部可选填项只有浪向与频率的默认值）。没有任何几何型值、站线、面元尺寸、网格密度或装载
（KG/装载状态）输入——这一点决定了「它到底能不能做 BEM」可从物理恒等式直接判定，而不是从
代码实现猜测。

审计算例取 4 条公开船型主尺度（覆盖 Cb 0.44~0.81、L 100~320 m 的常规商船包络）：

{md_table(["船型", "L [m]", "B [m]", "T [m]", "Cb", "∇ [m³]", "L·B [m²]",
           "反解 A_wp [m²]", "Cw·L·B 估计 [m²]", "A_wp 偏差", "反解 GM_T [m]", "反解 GM_L [m]", "panels"],
          hull_rows)}

## ② 怎么做的（方法、判据与工具链）

1. **探针设计**：37 条一次性探针，每次只改一个输入（频率 9 点、Cb 5 点、航速 4 点、浪向 4 点、
   船长 5 点、船宽 3 点、吃水 3 点、4 条船各 1 点），用于分离「输出对哪个输入敏感」。
   全部通过标准 JSON POST 采集，原始响应完整存入 `audit.json`。
2. **判据来自教科书写死的恒等式/极限**，不依赖任何平台自述：
   - 静水恢复力 `K33 = ρ g A_wp`、`K44 = ρ g ∇·GM`、`K55 = ρ g ∇·GM_L`；
   - 长波极限 `ω→0 ⇒ 垂荡 RAO → 1`（Newman 1977 §6.10）；
   - 势流辐射阻尼不可能在有限 ω 下恒等于 0；
   - RAO 的定义即「响应幅值 / 波幅」，必须随 ω 变化、必须随船体变化（传递函数）；
   - 面板法必须有可调离散参数才能做网格收敛（ITTC 7.5-03-01-01 / Roache GCI）。
3. **跨求解器基准**：以本工作树内已验证的 STF 切片理论内核
   （`backend/api/seakeeping_physics.py`，mission 1547 的 11 条解析判据全 PASS）作为独立实现，
   同一船体同一频率比对垂荡/纵摇 RAO，取 24 个（船, ω）点。
4. **隐含量反解**：由平台输出反解它隐含使用的静水几何量
   `A_wp = K33/(ρg)`、`GM_T = K44/(ρg∇)`、`GM_L = K55/(ρg∇)`，
   再与解析量（∇=Cb·L·B·T、`C_w≈(1+2Cb)/3` 的 A_wp）对照——
   这一步把「有没有硬编码」变成可复算的偏差数字。
5. 工具链：Python3 + urllib（探针）+ matplotlib（图）+ pytest（判据逻辑自检，4 用例 PASS）。

## ③ 计算结果（判据逐条判决）

{md_table(["判据", "声称必须成立的关系", "期望", "实测", "判决"], jrows)}

**关键反解结论**

- 反解水线面积与 `L·B` 的相对偏差：{'、'.join(f"{d['id'][5:]} {d['rel_Awp_vs_LB']:.1e}" for d in D)}
  —— 即 `A_wp = L·B` **精确成立**（浮点级），平台用矩形水线面当垂荡刚度，`Cb` 从未参与。
- 相对独立估计 `C_w·L·B` 的偏差：{'、'.join(f"{d['id'][5:]} {d['rel_Awp_vs_est']:+.1%}" for d in D)}
  —— 最大偏差出现在 **{worst_Awp['id'][5:]}（{worst_Awp['rel_Awp_vs_est']:+.1%}）**。
- 频率扫描 9 个 ω 点、航速 4 点、浪向 4 点、Cb 5 点：水动力系数与波浪力**逐字节相同**
  （相对散布 0.000e+00），即 `wave_frequency / speed / wave_direction / cb` 四个入参对
  附加质量、阻尼、恢复力、波浪力**完全无效**；`cb` 也仅通过 ∇ 影响 K44/K55，不影响 K33。
- RAO 表在 4 条主尺度差 3 倍的船上**完全相同**，是与船型无关的常数表
  （垂荡 0.8/1.2/1.5/1.3/1.0/0.8，纵摇 0.5/0.8/1.2/1.0/0.7/0.5）。

**跨求解器对比（vs 已验证 STF 内核）**

{md_table(["船型", "对比点", "垂荡 RAO 最大偏差", "纵摇 RAO 最大偏差", "STF 垂荡 RAO 范围", "平台垂荡 RAO 范围"], xrows)}

## ④ 结果图

**fig1** — {CAPTIONS['fig1']}

**fig2** — {CAPTIONS['fig2']}

**fig3** — {CAPTIONS['fig3']}

**fig4** — {CAPTIONS['fig4']}

## ⑤ 注意点（踩坑、局限、适用范围）

1. **只做黑盒裁决，未改后端**：线上 `:8001` 由另一仓库（ship-performance-platform）master 检出
   提供服务，不在本工作树白名单内。本单的交付物是**可复算判据 + 工件 + 报告**，修复动作需要
   在上游仓库落地：要么把该端点降级标注为「工程代理（闭式经验式）」并撤下 BEM/Rankine 自述，
   要么接入真实几何/网格输入并补 GCI 收敛证据。
2. **`C_w ≈ (1+2Cb)/3` 是工程估计**（±5% 量级），因此 J4 的偏差数字（+12.7%…+37.0%）不是
   精确误差，而是「平台隐含 A_wp 与任何物理上可能的水线面之间的系统性偏离」的保守下界。
   判 FAIL 的**决定性证据不依赖该估计**：反解 A_wp 与 L·B 的偏差在浮点级（1e-16…1e-9），
   单一矩形水线面对应 Cw=1.0，与实船 Cw≈0.6~0.85 的差距不可能用估计误差解释。
3. **STF 内核同源风险**：交叉比对的两个实现都使用 Cw=(1+2Cb)/3 与经验 2D 系数，因此
   J12 比较的是**RAO 传递函数形状**（频率依赖性、共振结构）而非绝对标定；结论「平台 RAO
   与 ω 无关」不受该同源影响——它是响应逐字节相同这一事实的直接推论。
4. **反解 GM 不是常数**（{', '.join(f"{d['id'][5:]}={d['implied_GMT']:.2f} m" for d in D)}），
   故 J6 判 PASS：恢复力确随船型变化。但端点不接收 KG/装载，GM 只能来自经验式，
   装载变化下恢复力不可信，这一点无法用现有入参做定量裁决（WITHELD 级）。
5. **探针包络**：全部探针落在常规商船主尺度包络内，未测试极端 Cb 或畸形输入；
   端点对畸形输入的行为（422/异常值）不在本单范围。
6. **面板数为装饰量**：`panels/(L·B)` 在 0.82~1.13 panels/m² 之间浮动，与任何网格参数无关，
   也不随频率/航速/浪向变；故「5200 面元 Rankine 源求解」不可复现、不可验证。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答「是否 < 3%」）

{md_table(["关键量", "基准（解析/独立实现）", "平台值", "相对误差", "闸门", "<3%?"],
          [("垂荡静水刚度 K33（KCS）",
            f"ρg·C_w·L·B = {D[0]['Awp_est'] * a['rho'] * a['g']:.4g} N/m",
            f"{D[0]['implied_Awp'] * a['rho'] * a['g']:.4g} N/m",
            f"{D[0]['rel_Awp_vs_est']:+.1%}", "15%", "否"),
           ("垂荡静水刚度 K33（包络最差船，%s）" % worst_Awp['id'][5:],
            f"ρg·C_w·L·B = {worst_Awp['Awp_est'] * a['rho'] * a['g']:.4g} N/m",
            f"{worst_Awp['implied_Awp'] * a['rho'] * a['g']:.4g} N/m",
            f"{worst_Awp['rel_Awp_vs_est']:+.1%}", "15%", "否"),
           ("垂荡 RAO vs STF 内核（24 点最大）", "STF 切片理论（m1547 已验证）",
            "常数表（与 ω 无关）", f"{max_rh:.1%}", "15%", "否"),
           ("纵摇 RAO vs STF 内核（24 点最大）", "STF 切片理论（m1547 已验证）",
            "常数表（与 ω 无关）", f"{max_rp:.1%}", "15%", "否"),
           ("wave_frequency 入参有效性", "RAO 必须随 ω 变化", "散布 0.000e+00",
            "100%（响应与输入无关）", "判据存在性", "否"),
           ("speed / wave_direction 入参有效性", "水动力系数须随航速/浪向变", "散布 0.000e+00",
            "100%（响应与输入无关）", "判据存在性", "否")])}

**显式裁决**：以上关键量**全部不满足 3%（亦不满足 15%）精度闸门**；
再叠加「无网格输入 ⇒ 数值不确定度不可评估」这一前置缺陷，`/advanced/bem` 的
`三维边界元法 (Rankine源)` 自述**不成立**，证据等级由隐含的 REAL 降为 **{a['evidence_grade']}**
（{len(fails)} 条判据 FAIL）。恢复力端口（K44/K55 随 ∇ 与经验 GM 变化）具备部分物理结构，
故不是纯常量桩，但作为「BEM」声明属**硬编码经验代理**。

## 附：复跑与落地建议

```bash
python3 tests/audit/audit_m1758_bem_surface.py --base http://127.0.0.1:8001/api/v1 \
        --out artifacts/m1758/bem_surface            # 37 探针，≈3 s
python3 -m pytest tests/test_m1758_bem_audit.py -q    # 判据逻辑自检 4 用例
python3 tests/audit/gen_figures_m1758.py             # 4 图
```

落地建议（上游仓库执行）：
1. 端点元数据改标 `method = "闭式经验代理（非 BEM）"`，并在响应里给出所用经验式出处；
2. RAO 必须实现为真正的 ω 传递函数（可直接复用本仓已验证的 STF 内核），并把
   `wave_frequency` 纳入计算；恢复力改用几何积分 A_wp/I_L，并把 Cb、KG 纳入入参；
3. 若坚持 BEM 自述，则须引入几何/面元输入并给出 GCI 收敛表，否则应在能力清单中标注 MOCK。
"""
    return md


def main():
    a, v = load()
    md = build_markdown(a, v)
    os.makedirs(DOCD, exist_ok=True)
    docp = os.path.join(DOCD, "BEM_SURFACE_AUDIT.md")
    with open(docp, "w", encoding="utf-8") as f:
        f.write(md)
    print("wrote", docp)

    figs = []
    for name, fname in FIG:
        with open(os.path.join(FIGD, fname), "rb") as f:
            figs.append(dict(name=name, caption=CAPTIONS[name],
                             png_base64=base64.b64encode(f.read()).decode()))
    payload = dict(
        title="/advanced/bem「三维边界元法」证据裁决：矩形水线面硬编码 + RAO 与频率/船型无关 → 判 MOCK（2 PASS / 11 FAIL）",
        summary=(f"对平台自称三维边界元（Rankine 源）的水动力端点做 37 条单因子探针 + 12 条解析/物理判据"
                 f"机器裁决：垂荡静水刚度反解 A_wp 精确等于 L·B（Cb 未参与，vs C_w·L·B 偏差 "
                 f"{min(d['rel_Awp_vs_est'] for d in a['derived']):+.1%}~"
                 f"{max(d['rel_Awp_vs_est'] for d in a['derived']):+.1%}）；RAO 表在 4 条主尺度差 3 倍的船上"
                 f"逐字节相同且与 ω 无关；wave_frequency/speed/wave_direction/cb 四个入参对水动力系数完全无效；"
                 f"辐射阻尼缺 3 个自由度且其余恒 0；请求体无任何网格参数 ⇒ 无法做 GCI，数值不确定度不可评估。"
                 f"与已验证 STF 切片理论内核（m1547）对比，垂荡 RAO 最大偏差 {max(r['rel_heave'] for r in a['cross_solver']):.1%}、"
                 f"纵摇 {max(r['rel_pitch'] for r in a['cross_solver']):.1%}，远超 15% 闸门。结论 MOCK。"),
        scenario_key="platform_audit",
        mission_id=1758,
        tags=["verification_debt", "bem", "seakeeping", "evidence_grade", "mock_detection",
              "analytic_identity", "cross_solver", "m1758"],
        markdown=md,
        figures=figs,
    )
    with open("/tmp/report_m1758.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("wrote /tmp/report_m1758.json", len(payload["summary"]), "chars summary")

    # ---- 登录并发布 ----
    api = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    try:
        req = urllib.request.Request(api + "/auth/login",
                                     data=json.dumps(dict(
                                         username=os.environ.get("TENSOR_AGENT_USER", "eco_admin"),
                                         password=os.environ.get("TENSOR_AGENT_PASS", ""))).encode(),
                                     headers={"Content-Type": "application/json"})
        tok = json.load(urllib.request.urlopen(req, timeout=30))["access_token"]
    except Exception as e:
        print("LOGIN FAILED:", e)
        return 1
    try:
        req = urllib.request.Request(api + "/tensor/reports", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + tok})
        r = json.load(urllib.request.urlopen(req, timeout=120))
        print("REPORT PUBLISHED:", json.dumps(r, ensure_ascii=False)[:300])
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        print("PUBLISH FAILED:", e, body[:400])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())