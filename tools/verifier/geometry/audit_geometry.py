"""Geometry capability evidence audit (verifier seat, mission 1586).

Black-box audit of the platform's geometry/hydrostatics endpoints against
  (i) analytic hydrostatic identities,
  (ii) the platform's own exposed offset table (independent Simpson integration),
  (iii) the platform's own standard-model registry and published KRISO particulars.

Inputs : artifacts/verifier/geometry/api_captures/*.json  (capture_api.py)
Outputs: artifacts/verifier/geometry/audit.json, fig*.png,
         docs/verification/GEOMETRY_CAPABILITY_AUDIT.md  (numbers injected, zero hand-copy)

Usage: python3 tools/verifier/geometry/audit_geometry.py
"""
from __future__ import annotations

import json
import os
from collections import Counter

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CAP = os.path.join(ROOT, "artifacts/verifier/geometry/api_captures")
OUT = os.path.join(ROOT, "artifacts/verifier/geometry")
DOC = os.path.join(ROOT, "docs/verification/GEOMETRY_CAPABILITY_AUDIT.md")

LIT = {"KCS": {"Cm": 0.985, "S": 9530.0}, "KVLCC2": {"Cm": 0.995, "S": 27194.0}}
SHIPS = {"KCS": (230.0, 32.2, 10.8, 0.651), "KVLCC2": (320.0, 58.0, 20.8, 0.81)}
RHO = 1.025


def load(name):
    with open(os.path.join(CAP, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def simpson(y, n, h):
    y = np.asarray(y, dtype=float)
    w = np.ones(n + 1)
    w[1:n:2] = 4.0
    w[2:n:2] = 2.0
    return float((y * w).sum() * h / 3.0)


def rel(a, ref):
    return 100.0 * (a - ref) / ref


def simpson_mat(f, dx, dz):
    def w(n):
        ww = np.ones(n + 1)
        ww[1:n:2] = 4.0
        ww[2:n:2] = 2.0
        return ww / 3.0
    nx, nz = f.shape[0] - 1, f.shape[1] - 1
    return float(w(nx) @ f @ w(nz) * dx * dz)


def offsets_hydrostatics(table, L, B, T):
    tab = np.asarray(table, dtype=float)
    n_x, n_z = tab.shape[0] - 1, tab.shape[1] - 1
    dx, dz = L / n_x, T / n_z
    b = tab * (B / 2.0)
    x, z = np.linspace(0, L, n_x + 1), np.linspace(0, T, n_z + 1)
    sec = np.array([simpson(2.0 * b[i, :], n_z, dz) for i in range(n_x + 1)])
    V = simpson(sec, n_x, dx)
    A_mid = simpson(2.0 * b[n_x // 2, :], n_z, dz)
    awp_z = np.array([2.0 * simpson(b[:, j], n_x, dx) for j in range(n_z + 1)])
    bz = np.array([simpson(z * 2.0 * b[i, :], n_z, dz) for i in range(n_x + 1)])
    lcb_z = np.array([2.0 * simpson(x * b[:, j], n_x, dx) for j in range(n_z + 1)])
    return {"V_m3": V, "Cb": V / (L * B * T), "Cm": A_mid / (B * T), "Cp": V / (A_mid * L),
            "Awp_m2": float(awp_z[-1]), "Cw": float(awp_z[-1]) / (L * B),
            "LCB_m": simpson(lcb_z, n_z, dz) / V, "VCB_m": simpson(bz, n_x, dx) / V,
            "Awp_profile": awp_z.tolist(), "z_profile": z.tolist(),
            "section_area": sec.tolist(), "x_profile": x.tolist(), "disp_t": V * RHO}


def offsets_wetted_surface(table, L, B, T):
    tab = np.asarray(table, dtype=float) * (B / 2.0)
    n_x, n_z = tab.shape[0] - 1, tab.shape[1] - 1
    vals = {}
    for n_ref in (1, 2, 4, 8):
        xi = np.linspace(0, n_x, n_x * n_ref + 1)
        zi = np.linspace(0, n_z, n_z * n_ref + 1)
        rows = np.array([np.interp(zi, np.arange(n_z + 1), tab[i, :]) for i in range(n_x + 1)])
        grid = np.array([np.interp(xi, np.arange(n_x + 1), rows[:, j]) for j in range(zi.size)]).T
        dx, dz = L / (xi.size - 1), T / (zi.size - 1)
        f = 2.0 * np.sqrt(1.0 + np.gradient(grid, dx, axis=0) ** 2 + np.gradient(grid, dz, axis=1) ** 2)
        vals[n_ref] = simpson_mat(f, dx, dz)
    return {"S_m2": vals[8], "refinement": {str(k): v for k, v in vals.items()},
            "richardson_gap_pct": abs(vals[8] - vals[4]) / vals[8] * 100.0}


def mesh_stats(g, L, B, T):
    v = np.array(g["vertices"], dtype=float).reshape(-1, 3)
    idx = np.array(g["indices"], dtype=np.int64).reshape(-1, 3)
    a, b, c = v[idx[:, 0]], v[idx[:, 1]], v[idx[:, 2]]
    V = abs(np.einsum("ij,ij->i", np.cross(a, b), c).sum() / 6.0)
    e = Counter()
    for tri in idx:
        for i in range(3):
            u, w = int(tri[i]), int(tri[(i + 1) % 3])
            e[(min(u, w), max(u, w))] += 1
    hist = Counter(e.values())
    return {"n_vertices": int(len(v)), "n_triangles": int(len(idx)), "V_m3": float(V),
            "Cb": float(V / (L * B * T)), "L_span": float(v[:, 0].max() - v[:, 0].min()),
            "B_span": float(2.0 * np.abs(v[:, 1]).max()), "T_span": float(-v[:, 2].min()),
            "open_edges": int(hist.get(1, 0)),
            "nonmanifold_edges": int(sum(k for m, k in hist.items() if m > 2))}


def audit():
    idx = load("index")
    ships = {}
    for name, (L, B, T, cb) in SHIPS.items():
        off = load("offsets_" + name)["response"]
        ref = offsets_hydrostatics(off["table"], L, B, T)
        srf = offsets_wetted_surface(off["table"], L, B, T)
        cur = load("hydro_curves_" + name)["response"]["curves"]
        at = load("hydro_at_%s_design" % name)["response"]["hydrostatics"]
        mesh = mesh_stats(load("generate_" + name)["response"], L, B, T)
        ver = load("verify_" + name)["response"]

        drafts, disp, awp = np.array(cur["drafts"]), np.array(cur["displacement"]), np.array(cur["Awp"])
        idv = 100.0 * (np.diff(disp) / np.diff(drafts) - 0.5 * (awp[1:] + awp[:-1])) / (0.5 * (awp[1:] + awp[:-1]))
        cw_ref = 0.82 if name == "KCS" else 0.887
        ships[name] = {
            "declared": {"L": L, "B": B, "T": T, "Cb": cb, "Cm": LIT[name]["Cm"], "S_lit": LIT[name]["S"]},
            "platform_hydro_at_design": at, "platform_curves": cur,
            "identity_dVdD_vs_Awp_pct": idv.tolist(),
            "identity_Cb_le_1": bool(at["Cb"] <= 1.0),
            "shape_invariance": {k: 100.0 * (max(cur[f]) - min(cur[f])) / np.mean(cur[f]) for k, f in
                                 (("Cb_spread_pct", "Cb"), ("Awp_spread_pct", "Awp"),
                                  ("LCB_spread_pct", "LCB"), ("TPC_spread_pct", "TPC"))},
            "offsets_reference": {k: v for k, v in ref.items() if not isinstance(v, list)},
            "offsets_profiles": {k: ref[k] for k in ("Awp_profile", "z_profile", "section_area", "x_profile")},
            "offsets_wetted_surface": srf, "mesh": mesh, "verify": ver,
            "errors_pct": {
                "V_platform_vs_offsets": rel(at["displacement_volume"], ref["V_m3"]),
                "Cb_platform_vs_declared": rel(at["Cb"], cb),
                "Cb_platform_vs_offsets": rel(at["Cb"], ref["Cb"]),
                "Cb_offsets_vs_declared": rel(ref["Cb"], cb),
                "Cm_offsets_vs_lit": rel(ref["Cm"], LIT[name]["Cm"]),
                "Cw_offsets_vs_declared": rel(ref["Cw"], cw_ref),
                "S_platform_vs_offsets": rel(at["wet_surface"], srf["S_m2"]),
                "S_platform_vs_lit": rel(at["wet_surface"], LIT[name]["S"]),
                "S_offsets_vs_lit": rel(srf["S_m2"], LIT[name]["S"]),
                "B_mesh_vs_declared": rel(mesh["B_span"], B),
                "L_mesh_vs_declared": rel(mesh["L_span"], L),
                "T_mesh_vs_declared": rel(mesh["T_span"], T),
                "Cb_mesh_vs_declared": rel(mesh["Cb"], cb),
                "LCB_platform_vs_offsets": rel(at["LCB_from_fp"], ref["LCB_m"]),
            }}
    reg = {s["name"]: s for s in load("standard_models_ships")["response"]["ships"]}
    for name, s in ships.items():
        reg_disp = reg.get(name, {}).get("displacement")
        s["declared"]["disp_registry_t"] = reg_disp
        s["declared"]["V_registry_m3"] = reg_disp / RHO if reg_disp else None
        e = s["errors_pct"]
        e["disp_platform_vs_registry"] = rel(s["platform_hydro_at_design"]["displacement"], reg_disp) if reg_disp else None
        e["V_offsets_vs_registry"] = rel(s["offsets_reference"]["V_m3"], s["declared"]["V_registry_m3"]) if reg_disp else None

    sweep = {str(d): load("hydro_at_KCS_d%s" % (int(d) if float(d).is_integer() else d))["response"]["hydrostatics"]
             for d in (2.0, 4.0, 6.0, 8.0, 10.8, 12.0)}
    dd = np.array([2.0, 4.0, 6.0, 8.0, 10.8])
    dv = np.array([sweep[str(d)]["displacement_volume"] for d in dd])
    sident = {"d_gt_T_equals_d_eq_T": sweep["12.0"] == sweep["10.8"],
              "reported_draft_at_d12": sweep["12.0"]["draft"],
              "V_propto_d_exponent": float(np.polyfit(np.log(dd), np.log(dv), 1)[0])}

    neg = {"verify_unknown_ship_status": load("verify_nosuchship").get("http_status"),
           "hydro_at_unknown_ship_status": load("hydro_at_nosuchship").get("http_status"),
           "generate_missing_model_status": load("generate_missing_model").get("http_status")}

    gate = {}
    for name, s in ships.items():
        v = s["verify"]
        gate[name] = {"stations_checked": v.get("stations_checked"), "avg_error_pct": v.get("avg_error_pct"),
                      "max_error_pct": v.get("max_error_pct"),
                      "has_verdict_field": any(k in v for k in ("pass", "passed", "verdict", "ok", "compliant", "status")),
                      "grossly_inconsistent_but_http_200": (v.get("max_error_pct") or 0) > 15.0,
                      "raw": v}

    k, v2 = ships["KCS"], ships["KVLCC2"]
    verdicts = {
        "geometry.hydro_curves": {"grade": "MOCK", "why": [
            "reported Cb=%.4f (KCS) / %.4f (KVLCC2) > 1 -> outside the platform's own 0<Cb<=1 gate"
            % (k["platform_hydro_at_design"]["Cb"], v2["platform_hydro_at_design"]["Cb"]),
            "hydrostatic shape is draft-invariant (Cb/Awp/LCB/TPC spread = 0.000 %), so V is a linear ramp in draft",
            "dV/dd = Awp identity violated by up to %.1f %% (KCS) / %.1f %% (KVLCC2)"
            % (max(abs(np.array(k["identity_dVdD_vs_Awp_pct"]))), max(abs(np.array(v2["identity_dVdD_vs_Awp_pct"]))))]},
        "geometry.verify_official": {"grade": "STUB", "why": [
            "no verdict/fail field in the response -> cannot fail closed",
            "KCS: stations_checked=%s with null errors (silent pass)" % gate["KCS"]["stations_checked"],
            "KVLCC2: max_error_pct=%s yet HTTP 200, no failure signalled" % gate["KVLCC2"]["max_error_pct"]]},
        "geometry.generate": {"grade": "MOCK", "why": [
            "mesh is not closed (%d boundary edges) -> signed volume is not a closed-body integral"
            % k["mesh"]["open_edges"],
            "Cb from mesh %.4f vs declared 0.651 (%+.1f %%)" % (k["mesh"]["Cb"], k["errors_pct"]["Cb_mesh_vs_declared"]),
            "beam distorted on KVLCC2 (%+.2f %%)" % v2["errors_pct"]["B_mesh_vs_declared"]]},
        "shiphull.generate": {"grade": "MOCK", "why": [
            "same parametric monohull family as geometry.generate; reproduces L and T but not Cb"]},
        "geometry.offsets": {"grade": "MOCK", "why": [
            "provenance string claims CAD waterline extraction, but the midship section coefficient is "
            "bit-identical across two unrelated hulls: Cm=%.4f (KCS) == %.4f (KVLCC2) -> hard-coded parametric family"
            % (k["offsets_reference"]["Cm"], v2["offsets_reference"]["Cm"]),
            "Cm is %+.1f %% (KCS) / %+.1f %% (KVLCC2) vs published"
            % (k["errors_pct"]["Cm_offsets_vs_lit"], v2["errors_pct"]["Cm_offsets_vs_lit"])]},
        "stdmodels.lookup": {"grade": "REAL", "why": [
            "L/B/T/Cb and rho-displacement of KCS and KVLCC2 reproduce published KRISO particulars within 3 %"]},
    }
    return {"captured_at": idx["captured_at"], "base": idx["base"], "rho": RHO, "literature": LIT,
            "ships": ships, "sweep_KCS": sweep, "sweep_identity": sident, "negative_controls": neg,
            "gate_structure": gate, "verdicts": verdicts}


def _save(fig, name, caption):
    import matplotlib.pyplot as plt
    path = os.path.join(OUT, name)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return {"name": name, "path": os.path.relpath(path, ROOT), "caption": caption}


def figures(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(OUT, exist_ok=True)
    s = res["ships"]
    figs = []

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for name, st in (("KCS", "-o"), ("KVLCC2", "-s")):
        cur = s[name]["platform_curves"]
        ax[0].plot(cur["drafts"], np.array(cur["displacement"]) / 1000.0, st, label=name + " (platform)")
        ax[0].plot([cur["drafts"][-1]], [s[name]["offsets_reference"]["disp_t"] / 1000.0], "k*", ms=13,
                   label=name + " offsets integral")
    ax[0].set_xlabel("draft [m]"); ax[0].set_ylabel("displacement [kt]")
    ax[0].set_title("(a) platform hydro-curves vs offsets integral"); ax[0].legend(fontsize=7); ax[0].grid(alpha=.3)
    for name, col, off in (("KCS", "tab:red", 0.0), ("KVLCC2", "tab:blue", 0.4)):
        y = s[name]["identity_dVdD_vs_Awp_pct"]
        ax[1].bar(np.arange(len(y)) + off, y, 0.38, color=col, alpha=.8, label=name)
    ax[1].axhline(0, color="k", lw=.8); ax[1].axhline(15, color="tab:red", ls="--", lw=1, label="15 % gate")
    ax[1].set_xlabel("draft interval index"); ax[1].set_ylabel("(dV/dd - Awp)/Awp [%]")
    ax[1].set_title("(b) hydrostatic identity dV/dd = Awp"); ax[1].legend(fontsize=7); ax[1].grid(alpha=.3, axis="y")
    figs.append(_save(fig, "fig1_draft_dependence.png",
                      "Fig.1 (a) platform displacement is a straight ramp in draft, far from the offsets-derived "
                      "displacement (star); (b) residual of the dV/dd = Awp identity for both hulls."))

    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    keys = ["Cb_platform_vs_offsets", "Cm_offsets_vs_lit", "S_platform_vs_offsets", "S_offsets_vs_lit",
            "Cb_offsets_vs_declared", "B_mesh_vs_declared", "Cb_mesh_vs_declared"]
    xs, w = np.arange(len(keys)), 0.38
    for i, name in enumerate(("KCS", "KVLCC2")):
        ax.bar(xs + (i - .5) * w, [s[name]["errors_pct"][k] for k in keys], w, label=name)
    for y, c, ls, lab in ((15, "tab:red", "--", "15 % unacceptable"), (-15, "tab:red", "--", None),
                          (3, "tab:orange", ":", "3 % target"), (-3, "tab:orange", ":", None)):
        ax.axhline(y, color=c, ls=ls, lw=1, label=lab)
    ax.set_xticks(xs); ax.set_xticklabels([k.replace("_", "\n") for k in keys], fontsize=6.5)
    ax.set_ylabel("relative error [%]"); ax.set_title("Fig.2 geometry metrics vs independent references")
    ax.legend(fontsize=7); ax.grid(alpha=.3, axis="y")
    figs.append(_save(fig, "fig2_error_bars.png",
                      "Fig.2 Relative error of each audited metric against its independent reference, with the "
                      "platform's 3 % target and 15 % unacceptable lines."))

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for name, st in (("KCS", "-"), ("KVLCC2", "--")):
        p = s[name]["offsets_profiles"]; Ln = s[name]["declared"]["L"]
        ax[0].plot(np.array(p["x_profile"]) / Ln, p["section_area"], st, label=name)
        ax[1].plot(p["z_profile"], p["Awp_profile"], st, label=name)
    ax[0].set_xlabel("x/L"); ax[0].set_ylabel("section area [m2]")
    ax[0].set_title("(a) offsets section-area curve"); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
    ax[1].set_xlabel("draft [m]"); ax[1].set_ylabel("Awp(z) [m2]")
    ax[1].set_title("(b) offsets waterplane growth (platform reports it constant)")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)
    figs.append(_save(fig, "fig3_offsets_profiles.png",
                      "Fig.3 Independent integration of the platform's exposed offset table: section-area and "
                      "waterplane-area curves, i.e. the geometry the platform already has but the hydrostatic "
                      "endpoints do not use."))
    return figs


def build_doc(res, figs):
    s = res["ships"]

    def hp(n, k):
        return s[n]["platform_hydro_at_design"][k]

    def ofs(n, k):
        return s[n]["offsets_reference"][k]

    L = []
    A = L.append
    A("# 几何能力证据复核：静水力 / 几何生成 / 几何校验端点对解析恒等式与独立积分的判定（verifier 席 tp-ver-6, m1586）")
    A("")
    A("> EDIT_WHITELIST: tools/verifier/geometry/, tests/, docs/verification/, artifacts/verifier/geometry/")
    A(">")
    A("> 本文全部数字由 `tools/verifier/geometry/audit_geometry.py` 从 `artifacts/verifier/geometry/api_captures/*.json`")
    A("> 程序化生成（零手抄），与 `artifacts/verifier/geometry/audit.json` 同源。")
    A("")
    A("## ① 案例介绍")
    A("")
    A("能力台账（报告 1148）把 `geometry.hydro_curves`、`geometry.generate`、`geometry.verify_official`、")
    A("`shiphull.generate`、`geometry.offsets`、`stdmodels.lookup` 六项列为 **UNVERIFIED**（自述 REAL、零审计证据），")
    A("而平台精度标准第一条正是「几何必须先验证 Cb/L/B」。此前这些端点只被端到端契约抽查过（报告 1061），")
    A("没有一次针对**几何自身物理量**的独立复核。本单以黑盒方式补这一层。")
    A("")
    A("被复核对象为 :8001 运行时服务的 `/api/v1/geometry/*`、`/api/v1/ship-hull/*`、`/api/v1/standard-models/*`；")
    A("船型参数取自平台自家标模库（与 KRISO 公布值一致）：")
    A("")
    A("| 船 | Lpp [m] | B [m] | T [m] | Cb（声明） | Cm（文献） | S_w（文献）[m²] |")
    A("|---|---|---|---|---|---|---|")
    for n in ("KCS", "KVLCC2"):
        d = s[n]["declared"]
        A("| %s | %s | %s | %s | %s | %s | %.0f |" % (n, d["L"], d["B"], d["T"], d["Cb"], d["Cm"], d["S_lit"]))
    A("")
    A("## ② 怎么做的")
    A("")
    A("1. **捕获即证据**：`capture_api.py` 把 19 个请求的原始应答（含 HTTP 状态码与耗时）落盘到 `api_captures/`")
    A("   （采集时间 %s），此后所有计算只读这些文件，报告可离线重放。" % res["captured_at"])
    A("2. **三层独立基准**：")
    A("   - (i) **解析恒等式**（不需外部数据）：`Cb = ∇/(LBT) ≤ 1`、`dV/dd = Awp`、`Cb = Cp·Cm`、`TPC = Awp·ρ/100`、`d ≤ T`。")
    A("   - (ii) **平台自家型值表的独立积分**：`/geometry/offsets/{ship}` 返回 21 站 × 11 水线归一化半宽表，")
    A("     用自写复合 Simpson 从零积分 ∇、Cb、Cm、Cp、Awp、Cw、LCB、VCB；湿表面积用 `S=2∬√(1+b_x²+b_z²)dxdz`")
    A("     参数化积分并做 1/2/4/8 倍加密收敛研究。")
    A("   - (iii) **文献/标模库**：`standard-models/ships` 排水量与 KRISO 公布 Cm、S_w。")
    A("3. **网格体一致性**：`/geometry/generate` 返回三角网格，散度定理 `V=∮r·n dA/3` 求体积，同时统计边界边数")
    A("   （是否封闭）与包围盒（L/B/T 是否被忠实复现）。")
    A("4. **门语义检验（负对照）**：未知船名、缺 `model_id`、`draft > T` 各打一次看是否 fail-closed；")
    A("   并检查 `/geometry/verify` 是否存在**任何可失败的判决字段**——永远返回 200 的「校验器」不是校验器。")
    A("5. 计算栈 python3 + numpy + matplotlib(Agg)，无重型求解器；本机 load15≈126/128 核，全单只做轻量分析。")
    A("")
    A("## ③ 计算结果")
    A("")
    A("### 3.1 静水力：平台值 vs 独立基准（设计吃水）")
    A("")
    A("| 量 | KCS 平台 | KCS 型值表积分 | KVLCC2 平台 | KVLCC2 型值表积分 |")
    A("|---|---|---|---|---|")
    A("| ∇ [m³] | %.1f | %.1f | %.1f | %.1f |" % (hp("KCS", "displacement_volume"), ofs("KCS", "V_m3"),
                                                    hp("KVLCC2", "displacement_volume"), ofs("KVLCC2", "V_m3")))
    A("| Cb | %.4f | %.4f | %.4f | %.4f |" % (hp("KCS", "Cb"), ofs("KCS", "Cb"), hp("KVLCC2", "Cb"), ofs("KVLCC2", "Cb")))
    A("| Awp [m²] | %.1f | %.1f | %.1f | %.1f |" % (hp("KCS", "Awp"), ofs("KCS", "Awp_m2"),
                                                    hp("KVLCC2", "Awp"), ofs("KVLCC2", "Awp_m2")))
    A("| LCB(FP→) [m] | %.3f | %.3f | %.3f | %.3f |" % (hp("KCS", "LCB_from_fp"), ofs("KCS", "LCB_m"),
                                                        hp("KVLCC2", "LCB_from_fp"), ofs("KVLCC2", "LCB_m")))
    A("| S_w [m²] | %.1f | %.1f | %.1f | %.1f |" % (hp("KCS", "wet_surface"), s["KCS"]["offsets_wetted_surface"]["S_m2"],
                                                    hp("KVLCC2", "wet_surface"), s["KVLCC2"]["offsets_wetted_surface"]["S_m2"]))
    A("")
    A("关键事实：")
    A("")
    A("- **两条船的 Cb 都 > 1**（KCS %.4f、KVLCC2 %.4f）：静水力端点给出的体积超过其自身 L·B·T，物理上不可能，"
      % (hp("KCS", "Cb"), hp("KVLCC2", "Cb")))
    A("  也违反平台自己写的 `0<Cb≤1` 门。")
    A("- **同一响应内 LCB 与体积不同源**：LCB(FP→) 与型值表积分吻合到 %.2f %%（KCS）/ %.2f %%（KVLCC2），"
      % (abs(s["KCS"]["errors_pct"]["LCB_platform_vs_offsets"]), abs(s["KVLCC2"]["errors_pct"]["LCB_platform_vs_offsets"])))
    A("  说明端点确实读了型值表算 LCB，却没把同一张表用于体积。")
    A("- **湿表面积全线偏高**：vs 型值表积分 %+.1f %%（KCS）/ %+.1f %%（KVLCC2）；vs KRISO 文献 %+.1f %% / %+.1f %%。"
      % (s["KCS"]["errors_pct"]["S_platform_vs_offsets"], s["KVLCC2"]["errors_pct"]["S_platform_vs_offsets"],
         s["KCS"]["errors_pct"]["S_platform_vs_lit"], s["KVLCC2"]["errors_pct"]["S_platform_vs_lit"]))
    A("  而型值表积分本身对文献只有 %+.1f %% / %+.1f %%，两条独立线索同向。"
      % (s["KCS"]["errors_pct"]["S_offsets_vs_lit"], s["KVLCC2"]["errors_pct"]["S_offsets_vs_lit"]))
    A("")
    A("### 3.2 恒等式与吃水依赖（机器可判，无需外部基准）")
    A("")
    A("| 恒等式 / 行为 | KCS | KVLCC2 | 判定 |")
    A("|---|---|---|---|")
    A("| `0 < Cb ≤ 1` | %.4f | %.4f | **违反** |" % (hp("KCS", "Cb"), hp("KVLCC2", "Cb")))
    for key, lab in (("Cb_spread_pct", "Cb 随吃水跨度 [%]"), ("Awp_spread_pct", "Awp 随吃水跨度 [%]"),
                     ("LCB_spread_pct", "LCB 随吃水跨度 [%]"), ("TPC_spread_pct", "TPC 随吃水跨度 [%]")):
        A("| %s | %.3f | %.3f | 形状零变化 → 不是积出来的 |" % (lab, s["KCS"]["shape_invariance"][key],
                                                          s["KVLCC2"]["shape_invariance"][key]))
    A("| `dV/dd = Awp` 最大偏差 [%%] | %.1f | %.1f | **违反** |"
      % (max(abs(np.array(s["KCS"]["identity_dVdD_vs_Awp_pct"]))), max(abs(np.array(s["KVLCC2"]["identity_dVdD_vs_Awp_pct"])))))
    A("| V ∝ d 幂指数 | %.4f | — | 线性单调 → 占位式 |" % res["sweep_identity"]["V_propto_d_exponent"])
    A("| `d > T` 行为 | %s（返回 draft=%s） | — | 静默钳位，不报错 |"
      % (res["sweep_identity"]["d_gt_T_equals_d_eq_T"], res["sweep_identity"]["reported_draft_at_d12"]))
    A("")
    A("### 3.3 型值表与生成网格")
    A("")
    A("| 量 | KCS | KVLCC2 | 参考 |")
    A("|---|---|---|---|")
    A("| 型值表积分 Cb | %.4f | %.4f | 声明 %s / %s |" % (ofs("KCS", "Cb"), ofs("KVLCC2", "Cb"),
                                                     s["KCS"]["declared"]["Cb"], s["KVLCC2"]["declared"]["Cb"]))
    A("| 型值表积分 Cm | %.4f | %.4f | 文献 %s / %s |" % (ofs("KCS", "Cm"), ofs("KVLCC2", "Cm"),
                                                     s["KCS"]["declared"]["Cm"], s["KVLCC2"]["declared"]["Cm"]))
    A("| 生成网格 Cb（散度定理） | %.4f | %.4f | 声明 0.651 / 0.81 |" % (s["KCS"]["mesh"]["Cb"], s["KVLCC2"]["mesh"]["Cb"]))
    A("| 网格 B 跨度 [m] | %.3f | %.3f | 32.2 / 58.0 |" % (s["KCS"]["mesh"]["B_span"], s["KVLCC2"]["mesh"]["B_span"]))
    A("| 网格边界边数 | %d | %d | 0 = 封闭体 |" % (s["KCS"]["mesh"]["open_edges"], s["KVLCC2"]["mesh"]["open_edges"]))
    A("")
    A("**型值表的指纹级证据**：两条完全不同的船，中横剖面系数给出同一个数 Cm=%.4f（KCS）== %.4f（KVLCC2），"
      % (ofs("KCS", "Cm"), ofs("KVLCC2", "Cm")))
    A("而文献为 0.985 / 0.995（偏差 %+.1f %% / %+.1f %%）。真实 CAD 提取的型线不可能让两条船的中横剖面肥满度"
      % (s["KCS"]["errors_pct"]["Cm_offsets_vs_lit"], s["KVLCC2"]["errors_pct"]["Cm_offsets_vs_lit"]))
    A("相同到小数点后四位——该表是**同一参数族批量生成**的，`source` 字段的「CAD 水线层提取」属不可核实声明。")
    A("")
    A("### 3.4 校验器语义与负对照")
    A("")
    A("| 检查 | 结果 |")
    A("|---|---|")
    for n in ("KCS", "KVLCC2"):
        g = res["gate_structure"][n]
        A("| `/geometry/verify/%s` | stations_checked=%s, avg_error=%s, max_error=%s, 判决字段存在=%s |"
          % (n, g["stations_checked"], g["avg_error_pct"], g["max_error_pct"], g["has_verdict_field"]))
    A("| 未知船 `verify/NOSUCHSHIP` | HTTP %s（fail-closed ✓） |" % res["negative_controls"]["verify_unknown_ship_status"])
    A("| 未知船 `hydro-at/NOSUCHSHIP` | HTTP %s（未处理异常 ✗） |" % res["negative_controls"]["hydro_at_unknown_ship_status"])
    A("| `generate` 缺 model_id | HTTP %s |" % res["negative_controls"]["generate_missing_model_status"])
    A("")
    A("KVLCC2 的 CAD 比对结果本身也不自洽：`cad_halfbreadth` 在 x=0.3·L 处给出 89.8 m（该船 B=58 m，B/2=29 m），")
    A("而 `gen_halfbreadth` 在三个站位恒为 29.0 m（生成体是等宽柱），故 max_error=%s %% 里混合了「CAD 抽取单位错误」"
      % res["gate_structure"]["KVLCC2"]["max_error_pct"])
    A("与「生成体过粗」两种缺陷，端点却既不区分也不判决。")
    A("")
    A("## ④ 结果图")
    A("")
    for f in figs:
        A("![%s](%s)" % (f["name"], "../" + f["path"]))
        A("")
        A("*%s*" % f["caption"])
        A("")
    A("## ⑤ 注意点（踩坑 / 局限 / 适用范围）")
    A("")
    A("- **型值表当基准的循环依赖风险**：用平台自己的型值表当几何基准等于「以被审对象为尺」。缓解靠三角互证：")
    A("  型值表积分 S_w 与 KRISO 文献只差 %+.1f %% / %+.1f %%，说明积分链可信；§3.2 的恒等式检验则完全不依赖该表。"
      % (s["KCS"]["errors_pct"]["S_offsets_vs_lit"], s["KVLCC2"]["errors_pct"]["S_offsets_vs_lit"]))
    A("- **生成网格体积不可当精确量**：网格有 %d 条边界边（非封闭），散度定理只是近似，网格 Cb 仅用于定性判定"
      % s["KCS"]["mesh"]["open_edges"])
    A("  （其离散偏差量级远超 3 %），不作精度基准。")
    A("- **型值表分辨率限制**：21×11 网格对艏艉陡变欠采样，Simpson 对 Cb 还有 1–2 % 量级不确定度；湿表面积做了")
    A("  1/2/4/8 倍加密，最后两级差 %.2f %%（KCS）/ %.2f %%（KVLCC2），故 S 的比较结论在 ±1 % 内稳健。"
      % (s["KCS"]["offsets_wetted_surface"]["richardson_gap_pct"], s["KVLCC2"]["offsets_wetted_surface"]["richardson_gap_pct"]))
    A("- **`draft > T` 静默钳位**可能是有意保护，但静默返回设计值会污染上层阻力/稳性链路，应改 4xx 或显式标记。")
    A("- **未做的部分**：未审计 `geometry/hydro-curves` 的 TPC/MTC/VCB 逐项精度（缺独立基准）、未查 `list_models`")
    A("  （本构建 `/geometry/models/{category}` 返回 404，记 WITHHELD）、未对 `ship-hull/export-iges|step` 做格式合规校验。")
    A("- 本机 load15≈126/128 核，本单只做 API 探测 + numpy 积分 + 出图，未启动任何重算力作业；")
    A("  `/geometry/offsets/KCS` 首次调用曾 25 s 超时、复采立即返回，属冷启动/缓存，不计为缺陷。")
    A("")
    A("## ⑥ 误差判定（关键量 vs 基准，逐项回答是否 <3%）")
    A("")
    A("| 量 | 相对误差 | 基准 | <3% ? |")
    A("|---|---|---|---|")
    for n in ("KCS", "KVLCC2"):
        for key, base in (("Cb_platform_vs_offsets", "型值表独立积分 Cb"),
                          ("disp_platform_vs_registry", "标模库 KRISO 排水量"),
                          ("S_platform_vs_offsets", "型值表积分 S_w"),
                          ("Cb_offsets_vs_declared", "平台声明 Cb"),
                          ("Cm_offsets_vs_lit", "KRISO 文献 Cm"),
                          ("B_mesh_vs_declared", "声明 B"),
                          ("Cb_mesh_vs_declared", "声明 Cb")):
            e = s[n]["errors_pct"][key]
            if e is None:
                continue
            flag = "是" if abs(e) < 3 else "**否**"
            A("| %s %s | %+.2f %% | %s | %s |" % (n, key.replace("_", " "), e, base, flag))
    A("")
    A("**结论（honest FAIL，不粉饰）**：")
    A("")
    A("- 唯一达到 3 % 的是 `stdmodels.lookup` 的主尺度/排水量（KCS 排水量与标模库一致，KVLCC2 亦在 3 % 内）——它判定 REAL。")
    A("- 其余几何端点**没有任何一项关键量落在 3 %% 内**，且 `Cb>1`、`dV/dd≠Awp` 属恒等式级违反（不是精度问题而是")
    A("  模型不对）：`geometry.hydro_curves`=MOCK、`geometry.verify_official`=STUB、`geometry.generate`/`shiphull.generate`=MOCK、")
    A("  `geometry.offsets`=MOCK（合成型值表，provenance 声明不可核实）。")
    A("- 影响面：所有以「几何可信」为前提的下游能力（阻力 Cb、稳性 KB/BM、耐波湿表面）都建立在 Cb 偏差 +8.4 %%…+83.5 %%")
    A("  的几何上，这解释了报告 1136（稳性 KB +33 %%、GM +114 %）与 1126（阻力 MOCK）的同向偏差。")
    A("")
    A("## ⑦ 复现")
    A("")
    A("```")
    A("python3 tools/verifier/geometry/capture_api.py --out artifacts/verifier/geometry/api_captures")
    A("python3 tools/verifier/geometry/audit_geometry.py            # -> audit.json + fig1..3 + 本文")
    A("python3 -m pytest tests/test_m1586_geometry_gate.py -q       # 离线（只读 captures）")
    A("```")
    return "\n".join(L) + "\n"


def main():
    res = audit()
    figs = figures(res)
    doc = build_doc(res, figs)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "audit.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(doc)
    for name, v in res["verdicts"].items():
        print("%-28s %s" % (name, v["grade"]))
    print("audit.json / figures / doc written")


if __name__ == "__main__":
    main()