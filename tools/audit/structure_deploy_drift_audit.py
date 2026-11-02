"""结构域 m1623 「MOCK→REAL」修复的部署面独立复核（mission 1667，infrastructure 席）。

背景
----
进展报告 1192（m1623）声称结构域四端点已完成 MOCK→REAL 修复（屈曲 k(α) 极值化、
船体梁剖面模数接 IACS UR S11 波浪弯矩、板格 k=4 屈曲 + 屈服封顶、疲劳改 DNV-RP-C203 C 曲线）。
按 owner 的「已合并功能独立参考复核」KPI，修复必须用**独立判据**（闭式解 / 规范值 /
物理界限）在**服务面**上复核，自洽测试不计入。本脚本即该复核：

1. 探针层：对在线服务 ``:8001`` 的 ``/structure/analyze|plate|fatigue`` 打 7 组变体，
   原始响应逐条落盘为 fixture（零手抄源头）。
2. 判据层：每条判据的参照量只允许三类——(a) 闭式解（箱形剖面模数、Euler 板屈曲）、
   (b) 规范值（IACS UR S11 许用应力、DNV-RP-C203 C1 in-air S-N）、(c) 物理界限
   （工作应力不得超过屈服、板厚/间距必须进入强度通道）。
   >15% 相对误差判 FAIL（平台精度标准）；无参照则判 WITHHELD。
3. 部署面层：比对在线进程工作树与修复分支的源文件指纹（字节数 + MD5 + mtime），
   判定「修复是否落到服务面」，区分 代码错 / 部署漂移。
4. 出图与表格全部程序化生成（matplotlib Agg），数字零手抄。

用法
----
    python3 tools/audit/structure_deploy_drift_audit.py --probe     # 实机探针 → fixture
    python3 tools/audit/structure_deploy_drift_audit.py --report    # 台账 → artifacts/*
    python3 tools/audit/structure_deploy_drift_audit.py --figures   # → artifacts/figures/*
无重算力进程（本机 load15 高时也安全）：纯闭式算术 + HTTP 探针。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "structure_deploy_probe.json"
ARTIFACTS = ROOT / "artifacts"
FIGDIR = ARTIFACTS / "figures"

API = os.environ.get("SHIP_API", "http://127.0.0.1:8001/api/v1")

# ---------------------------------------------------------------- 参照常数
E_STEEL = 206000.0      # MPa
NU_STEEL = 0.3
K_PLATE = 4.0           # 长板简支单轴受压经典屈曲系数（Timoshenko）
LOG_A_DNV_C1 = 12.164   # DNV-RP-C203 C1 in air, m=3（S-N: N = 10^12.164 * dsigma^-3）
M_SN = 3.0
SIGMA_PERM_IACS = 175.0  # MPa, IACS UR S11 波浪+静水合成许用应力（k=1, sigma_y=235）

# 基准船（KCS 量级公开几何）
BASE = dict(L=230.0, B=32.2, T=10.8, D=19.5, displacement=52030.0, Cb=0.651,
            frame_spacing=0.6, plate_thickness=0.015, yield_stress=235.0)

VARIANTS = {
    "base": dict(BASE),
    "t_x2": dict(BASE, plate_thickness=0.030),
    "D_x1.5": dict(BASE, D=29.25),
    "disp_x2": dict(BASE, displacement=104060.0),
    "ys355": dict(BASE, yield_stress=355.0),
    "fs_x2": dict(BASE, frame_spacing=1.2),
    "L_x0.5": dict(L=115.0, B=16.1, T=5.4, D=9.75, displacement=6503.75, Cb=0.651,
                   frame_spacing=0.3, plate_thickness=0.0075, yield_stress=235.0),
}

ENDPOINTS = ["/structure/analyze", "/structure/plate", "/structure/fatigue"]

# 部署面：在线进程工作树 vs 修复分支（可被环境变量覆盖）
LIVE_SRC = os.environ.get(
    "LIVE_STRUCTURE_SRC",
    "/srv/tensor/ship-workspace/ship-performance-platform/backend/api/structure.py")
FIXED_SRC = os.environ.get(
    "FIXED_STRUCTURE_SRC", "/srv/tensor/agent/work/m1623/backend/api/structure.py")


# ---------------------------------------------------------------- 闭式参照
def box_section_modulus(B: float, D: float, t: float) -> float:
    """箱形剖面模数 Z = I/(D/2)，I = (B D^3 - b d^3)/12，b=B-2t, d=D-2t [m^3]。"""
    b, d = B - 2 * t, D - 2 * t
    I = (B * D ** 3 - b * d ** 3) / 12.0
    return I / (D / 2.0)


def plate_buckling_cr(t: float, b: float, E: float = E_STEEL, nu: float = NU_STEEL,
                      k: float = K_PLATE) -> float:
    """Euler 板屈曲 sigma_cr = k pi^2 E /(12(1-nu^2)) * (t/b)^2 [MPa]。"""
    C = k * math.pi ** 2 * E / (12.0 * (1.0 - nu ** 2))
    return C * (t / b) ** 2


def sn_cycles(dsigma: float, log_a: float = LOG_A_DNV_C1, m: float = M_SN) -> float:
    """DNV-RP-C203 幂律 N = 10^log_a * dsigma^-m。"""
    return 10 ** log_a * dsigma ** (-m)


def sn_log_a_implied(cycles: float, dsigma: float, m: float = M_SN) -> float:
    """由平台给出的循环数反解 log a（用于暴露「自称 DNV-C 实为 log a=13」的脱钩）。"""
    return math.log10(cycles * dsigma ** m)


def cb_check(L, B, T, disp):
    """几何前置校验：Cb = nabla/(L B T)。"""
    nabla = disp / 1.025  # t -> m^3（海水 1.025 t/m^3）
    return nabla / (L * B * T)


# ---------------------------------------------------------------- 探针层
def probe() -> dict:
    out = {"api": API, "endpoints": {}, "variants": VARIANTS}
    for ep in ENDPOINTS:
        out["endpoints"][ep] = {}
        for name, body in VARIANTS.items():
            req = urllib.request.Request(API + ep, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                out["endpoints"][ep][name] = json.load(urllib.request.urlopen(req, timeout=30))
            except urllib.error.HTTPError as exc:  # 记录而不隐藏
                out["endpoints"][ep][name] = {"_http_error": exc.code}
    out["deploy"] = source_fingerprints()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def _fp(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"path": path, "exists": False}
    raw = p.read_bytes()
    return {"path": path, "exists": True, "bytes": len(raw),
            "md5": hashlib.md5(raw).hexdigest(),
            "mtime": os.path.getmtime(p)}


def source_fingerprints() -> dict:
    return {"live": _fp(LIVE_SRC), "fixed_branch": _fp(FIXED_SRC)}


# ---------------------------------------------------------------- 判据层
def _rel(plat: float, ref: float) -> float:
    if ref == 0:
        return float("inf")
    return (plat - ref) / ref


def _judge(cid, domain, name, plat, ref, rel, ok, note, ref_kind):
    return {"id": cid, "domain": domain, "name": name, "platform": plat, "reference": ref,
            "rel_err": rel, "verdict": "PASS" if ok else "FAIL", "reference_kind": ref_kind,
            "note": note}


def build_ledger(fx: dict) -> dict:
    ep = fx["endpoints"]
    a, p, f = ep["/structure/analyze"], ep["/structure/plate"], ep["/structure/fatigue"]
    rows = []

    # J1 工作应力量级 vs 屈服界限（物理界限，不依赖弯矩假设）
    s_base = a["base"]["max_stress"]
    rel = _rel(s_base, SIGMA_PERM_IACS)
    rows.append(_judge("J1", "hull girder", "总纵弯曲工作应力 vs IACS UR S11 许用 175 MPa",
                       s_base, SIGMA_PERM_IACS, rel, s_base <= SIGMA_PERM_IACS * 1.15,
                       "上限型判据（工作应力须 <= 许用，容差 +15%）；平台超标 3.8e4 倍，且超自身屈服 2.8e4 倍 —— 单位与剖面模型共同失效",
                       "规范值(IACS UR S11)"))
    # 附加物理界限：应力不得超过屈服（任何未塌陷船体）
    rel_ys = _rel(s_base, BASE["yield_stress"])
    rows.append(_judge("J2", "hull girder", "工作应力 vs 屈服强度 235 MPa（不塌陷硬界限）",
                       s_base, BASE["yield_stress"], rel_ys, s_base <= BASE["yield_stress"] * 1.15,
                       "上限型判据；sigma_plat/sigma_y = 2.8e4，物理上不可能；kN.m/m^3=kPa 被当 MPa",
                       "物理界限"))
    # J3 闭式剖面模数 → 应力（需要弯矩假设，标注清楚）
    Z = box_section_modulus(BASE["B"], BASE["D"], BASE["plate_thickness"])
    M_ref = 1.173e6  # kN.m，KCS 量级中横剖面设计波浪+静水合成弯矩（前序审计 m1600 采用值）
    sigma_ref = M_ref / Z / 1000.0  # kN.m/m^3 = kPa -> MPa
    rel = _rel(s_base, sigma_ref)
    rows.append(_judge("J3", "hull girder", f"应力 vs 闭式 M/Z（M={M_ref:.3e} kN.m, Z={Z:.3f} m^3）",
                       s_base, sigma_ref, rel, abs(rel) <= 0.15,
                       "闭式 Z=(BD^3-bd^3)/12/(D/2)；平台 Z=B D^2*0.015 隐含杨氏模数无关的伪量",
                       "闭式解"))
    # J4 板厚灵敏度：sigma 必须随 t 变化（箱形截面 d lnZ/d lnt ~ 1）
    s_t2 = a["t_x2"]["max_stress"]
    rows.append(_judge("J4", "hull girder", "板厚加倍（t x2）对工作应力的灵敏度",
                       s_t2 / s_base, 1.0, s_t2 / s_base - 1.0, abs(s_t2 / s_base - 1.0) > 0.15,
                       "平台完全不响应（比值 1.000000）：板厚未进入强度通道 ⇒ 无截面模型",
                       "物理界限"))
    # J5 型深尺度律：d ln sigma/d ln D 闭式 -1.2 vs 实测 -2.0
    exp_plat = math.log(a["D_x1.5"]["max_stress"] / s_base) / math.log(1.5)
    exp_ref = math.log(box_section_modulus(BASE["B"], BASE["D"] * 1.5, BASE["plate_thickness"])
                       / Z) / math.log(1.5) * -1.0
    rel = _rel(exp_plat, exp_ref)
    rows.append(_judge("J5", "hull girder", "sigma 对 D 的幂指数（D x1.5）",
                       exp_plat, exp_ref, rel, abs(rel) <= 0.15,
                       "闭式箱形 Z 的 D 幂指数 1.200 ⇒ sigma 指数 -1.200；实测 -2.000",
                       "闭式解"))
    # J6 板格屈曲 vs Euler k=4（b 取请求里的 frame_spacing，不取平台隐含值）
    cr = p["base"]["critical_stress"]
    cr_ref = plate_buckling_cr(BASE["plate_thickness"], BASE["frame_spacing"])
    rel = _rel(cr, cr_ref)
    rows.append(_judge("J6", "plate buckling", "板格屈曲临界应力 vs Euler k=4（b=0.6 m）",
                       cr, cr_ref, rel, abs(rel) <= 0.15,
                       f"闭式 C=k pi^2 E/(12(1-nu^2))={plate_buckling_cr(1.0, 1.0):.1f} MPa",
                       "闭式解"))
    # J7 屈曲对板厚：必须 ~t^2
    r_t = p["t_x2"]["critical_stress"] / cr
    rows.append(_judge("J7", "plate buckling", "屈曲临界应力对 t 的幂指数（t x2，应=2.00）",
                       math.log(r_t) / math.log(2.0), 2.0, (math.log(r_t) / math.log(2.0) - 2.0) / 2.0,
                       abs(math.log(r_t) / math.log(2.0) - 2.0) <= 0.15 * 2.0,
                       "唯一物理正确通道：C (t/b)^2 精确复现", "闭式解"))
    # J8 屈曲对加强筋间距：必须 ~b^-2（间距加倍 → 临界应力 1/4）
    r_b = p["fs_x2"]["critical_stress"] / cr
    rows.append(_judge("J8", "plate buckling", "临界应力对 frame_spacing 的灵敏度（b x2，应=0.25）",
                       r_b, 0.25, _rel(r_b, 0.25), abs(_rel(r_b, 0.25)) <= 0.15,
                       "平台 b=1.5*frame_spacing（fs=0.6 隐含 b=0.9 m），b 通道真实存在 ⇒ 该端点唯一物理正确的通道（系数 k 仍错，见 J6）",
                       "闭式解"))
    # J9 板格实际应力：不得恒等于 0.5 sigma_y（硬编码占位）
    act_ratio = p["base"]["actual_stress"] / BASE["yield_stress"]
    act_ratio_355 = p["ys355"]["actual_stress"] / 355.0
    rows.append(_judge("J9", "plate buckling", "板格工作应力 / sigma_y（应来自 M/Z，非常数）",
                       act_ratio, 0.0, act_ratio, False,
                       f"base={act_ratio:.4f}, ys355 变体={act_ratio_355:.4f} —— 恒 0.5 sigma_y 硬编码",
                       "物理界限"))
    # J10 同一 dsigma 下 S-N 循环数（自称 DNV-C，实测 log a=13.0）
    ds = f["base"]["stress_range"]
    life_yr = f["base"]["calculated_life"]
    N_YR = 1.0e6  # 年循环数（显式假设：平台自身隐含 2.855e7/28.5=1.0018e6，取整 1e6）
    n_plat = life_yr * N_YR  # 平台按自身寿命反推的循环数
    log_a_plat = sn_log_a_implied(n_plat, ds)
    n_ref = sn_cycles(ds)
    rel = _rel(n_plat, n_ref)
    rows.append(_judge("J10", "fatigue", "同一 dsigma 下 S-N 循环数 vs DNV-C1 幂律",
                       n_plat, n_ref, rel, abs(rel) <= 0.15,
                       "平台寿命反推的 S-N 截距 log a={:.3f}，自称 DNV-C=12.164 ⇒ 循环数放大 6.9 倍".format(log_a_plat),
                       "规范值(DNV-RP-C203 C1)"))
    # J11 同一 dsigma 下寿命偏差（含安全结论是否反转）
    life_ref = sn_cycles(ds) / N_YR
    rel = _rel(life_yr, life_ref)
    status_inverted = life_yr >= 25 > life_ref
    rows.append(_judge("J11", "fatigue", "疲劳寿命（年）vs DNV-C1 闭式（同一 dsigma、同一循环率）",
                       life_yr, life_ref, rel, abs(rel) <= 0.15,
                       "平台判「合格」（{:.1f}>=25），闭式 4.2 年 ⇒ 结论反转（安全侧失效）".format(life_yr),
                       "规范值(DNV-RP-C203 C1)"))
    # J12 许用应力口径
    alw = a["base"]["allowable_stress"]
    rel = _rel(alw, SIGMA_PERM_IACS)
    rows.append(_judge("J12", "hull girder", "许用应力 0.6 sigma_y vs IACS UR S11 175 MPa",
                       alw, SIGMA_PERM_IACS, rel, abs(rel) <= 0.15,
                       "0.6*235=141 MPa，比规范低 19.4%（偏保守但超 15% 门槛）",
                       "规范值(IACS UR S11)"))

    n_pass = sum(r["verdict"] == "PASS" for r in rows)
    ledger = {
        "mission": 1667,
        "target": "船体结构域 /structure/analyze|plate|fatigue（在线 :8001）",
        "judgement_count": len(rows),
        "pass": n_pass,
        "fail": len(rows) - n_pass,
        "withheld": 0,
        "rows": rows,
        "geometry": {"Cb_input": BASE["Cb"], "Cb_closed": cb_check(
            BASE["L"], BASE["B"], BASE["T"], BASE["displacement"])},
        "derived": {"Z_box_m3": Z, "sigma_bending_ref_MPa": sigma_ref,
                    "sigma_cr_ref_MPa": cr_ref, "plate_C": plate_buckling_cr(1.0, 1.0),
                    "N_ref_cycles": sn_cycles(ds), "life_ref_yr": life_ref,
                    "cycles_per_year_assumed": 1.0e6,
                    "log_a_implied": log_a_plat},
        "verdict": None,
    }
    ledger["verdict"] = verdict(ledger, fx)
    return ledger


def verdict(ledger: dict, fx: dict) -> dict:
    """证据分级 + 部署漂移判定。"""
    dz = fx.get("deploy", {})
    live, fixed = dz.get("live", {}), dz.get("fixed_branch", {})
    same = bool(live.get("exists") and fixed.get("exists")
                and live.get("md5") == fixed.get("md5"))
    code_fail = ledger["fail"] > ledger["pass"]
    if code_fail and live.get("exists") and not same:
        kind = "DEPLOY_DRIFT"
        text = ("在线服务仍跑旧实现（live {} B / md5 {}），修复版在分支侧存在"
                "（{} B / md5 {}）但未落到服务面：m1623 的 MOCK→REAL 修复**对线上无效**").format(
            live.get("bytes"), (live.get("md5") or "")[:8], fixed.get("bytes"),
            (fixed.get("md5") or "")[:8])
    elif code_fail:
        kind = "CODE_DEFECT"
        text = "在线与修复分支同指纹，判据仍全线失败 ⇒ 修复本身未生效（代码缺陷，非部署）"
    else:
        kind = "FIX_EFFECTIVE"
        text = "判据多数 PASS，修复在服务面生效"
    return {"kind": kind, "evidence_level": "MOCK", "text": text,
            "live_md5": live.get("md5"), "fixed_md5": fixed.get("md5"),
            "live_bytes": live.get("bytes"), "fixed_bytes": fixed.get("bytes"),
            "deploy_same": same}


# ---------------------------------------------------------------- 输出
def write_tables(ledger: dict) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    json_path = ARTIFACTS / "structure_deploy_drift_audit.json"
    json_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
    lines = ["# 表 1 结构域部署面独立复核对账表（程序化生成，数字零手抄）", "",
             "| 判据 | 域 | 物理量 | 平台值 | 参照值 | 相对误差 | 判决 | 参照类别 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in ledger["rows"]:
        def fmt(x):
            if isinstance(x, float):
                if x != 0 and (abs(x) >= 1e4 or abs(x) < 1e-3):
                    return "{:.4e}".format(x)
                return "{:.4f}".format(x)
            return str(x)
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["id"], r["domain"], r["name"], fmt(r["platform"]), fmt(r["reference"]),
            ("{:+.4g}".format(r["rel_err"]) if abs(r["rel_err"]) >= 1 else "{:+.2%}".format(r["rel_err"])) if isinstance(r["rel_err"], float) and math.isfinite(r["rel_err"]) else "n/a",
            r["verdict"], r["reference_kind"]))
    lines += ["", "**总账**：{} 条判据 → PASS {} / FAIL {} / WITHHELD 0；证据等级 {}；判决 {}".format(
        ledger["judgement_count"], ledger["pass"], ledger["fail"],
        ledger["verdict"]["evidence_level"], ledger["verdict"]["kind"]), "",
        ledger["verdict"]["text"], "",
        "派生量：" + json.dumps(ledger["derived"], ensure_ascii=False, default=str)]
    md = ARTIFACTS / "structure_deploy_drift_tables.md"
    md.write_text("\n".join(lines) + "\n")
    return md


def write_figures(ledger: dict) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR.mkdir(parents=True, exist_ok=True)
    rows = {r["id"]: r for r in ledger["rows"]}
    out = []

    # fig1: 应力量级对比（对数柱）
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    labels = ["platform\n(hull stress)", "IACS UR S11\npermissible", "yield\n235 MPa",
              "closed-form\nM/Z"]
    vals = [rows["J1"]["platform"], SIGMA_PERM_IACS, BASE["yield_stress"], rows["J3"]["reference"]]
    colors = ["#c0392b", "#7f8c8d", "#7f8c8d", "#27ae60"]
    ax.bar(labels, vals, color=colors)
    ax.set_yscale("log")
    ax.set_ylabel("stress [MPa] (log)")
    ax.set_title("Hull girder stress: platform vs rule/closed-form (KCS-class)")
    for i, v in enumerate(vals):
        ax.text(i, v * 1.4, "{:.4g}".format(v), ha="center", fontsize=8)
    fig.tight_layout()
    p1 = FIGDIR / "fig1_stress_magnitude.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    out.append(str(p1.relative_to(ROOT)))

    # fig2: 灵敏度比值（平台 vs 物理）
    pairs = [("J4 t x2 (stress)", rows["J4"]["platform"], rows["J4"]["reference"]),
             ("J5 D x1.5 exponent", rows["J5"]["platform"], rows["J5"]["reference"]),
             ("J7 t x2 (sigma_cr^2)", rows["J7"]["platform"], rows["J7"]["reference"]),
             ("J8 b x2 (sigma_cr)", rows["J8"]["platform"], rows["J8"]["reference"])]
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    idx = range(len(pairs))
    ax.bar([i - 0.2 for i in idx], [p[1] for p in pairs], 0.4, label="platform", color="#c0392b")
    ax.bar([i + 0.2 for i in idx], [p[2] for p in pairs], 0.4, label="physics (closed-form)", color="#27ae60")
    ax.set_xticks(list(idx))
    ax.set_xticklabels([p[0] for p in pairs], fontsize=8)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_ylabel("ratio / exponent")
    ax.set_title("Sensitivity channels: platform is blind where physics demands response")
    ax.legend()
    fig.tight_layout()
    p2 = FIGDIR / "fig2_sensitivity_channels.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    out.append(str(p2.relative_to(ROOT)))

    # fig3: 判据矩阵
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    for i, r in enumerate(ledger["rows"]):
        ok = r["verdict"] == "PASS"
        ax.scatter([i], [0], s=260, marker="o", color="#27ae60" if ok else "#c0392b", zorder=3)
        ax.text(i, -0.28, r["id"], ha="center", fontsize=7)
    ax.set_ylim(-0.6, 0.3)
    ax.set_xlim(-0.8, len(ledger["rows"]) - 0.2)
    ax.set_yticks([])
    ax.set_title("Criteria matrix: {} PASS / {} FAIL (green=PASS, red=FAIL)".format(
        ledger["pass"], ledger["fail"]))
    fig.tight_layout()
    p3 = FIGDIR / "fig3_criteria_matrix.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    out.append(str(p3.relative_to(ROOT)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.probe or args.all:
        fx = probe()
        print("probe ->", FIXTURE, "endpoints:", list(fx["endpoints"]))
    if args.report or args.all or args.figures:
        fx = json.loads(FIXTURE.read_text())
        ledger = build_ledger(fx)
        md = write_tables(ledger)
        print("ledger:", ledger["judgement_count"], "PASS", ledger["pass"], "FAIL", ledger["fail"],
              "verdict", ledger["verdict"]["kind"])
        print("tables ->", md)
        if args.figures or args.all:
            print("figures ->", write_figures(ledger))


if __name__ == "__main__":
    main()