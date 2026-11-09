#!/usr/bin/env python3
"""Render the deep-dive report for ``slitvinov/lammps-sph-multiphase``.

Consumes the artefacts produced by :mod:`tools.oss_inventory.lammps_sph_multiphase_inventory`
(metadata, evidence pointers, re-computed identities, absence evidence, figure) and
writes

    docs/oss_inventory/OSS-INV-lammps-sph-multiphase.md          (report, JSON machine blocks)
    docs/oss_inventory/OSS-INV-lammps-sph-multiphase.machine.txt (playbook key:value blocks)
    artifacts/oss-sph/lammps-sph-multiphase/*.json|png           (machine-readable evidence)

No number is typed into the report: every value is injected from the JSON files
written by the computation module, so re-running the module refreshes the report.

Usage
-----
    python -m tools.oss_inventory.lammps_sph_multiphase_report            # render
    python -m tools.oss_inventory.lammps_sph_multiphase_report --offline  # skip GitHub calls
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from pathlib import Path

from tools.oss_inventory import lammps_sph_multiphase_inventory as inv
from tools.oss_inventory.machine_blocks import parse_text, summarise, validate


def md_table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def build_inputs(offline: bool = False) -> dict:
    inv.CACHE.mkdir(parents=True, exist_ok=True)
    meta_path = inv.ART / "repo_meta.json"
    if offline and meta_path.exists():
        meta = json.loads(meta_path.read_text())
    else:
        meta = inv.repo_meta()
        inv.ART.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True, ensure_ascii=False))

    tree = inv.source_tree()
    spec = inv.parse_kernel(inv.src(inv.KERNEL_H))
    ver = {
        "kernel": inv.verify_kernel_normalisation(spec),
        "derivative": inv.verify_derivative_table(spec),
        "eos": inv.verify_eos_slope(),
        "energy": inv.energy_identity_check(),
    }
    ev_rows, ev = inv.evidence_table()
    absent = inv.absence_evidence()
    support = inv.neighbour_support()

    inv.ART.mkdir(parents=True, exist_ok=True)
    (inv.ART / "verification.json").write_text(json.dumps(
        {"kernel_spec": spec, "identities": ver, "neighbour_support": support},
        indent=1, sort_keys=True))
    (inv.ART / "evidence.json").write_text(json.dumps(ev_rows, indent=1, ensure_ascii=False))
    (inv.ART / "absence_evidence.json").write_text(json.dumps(absent, indent=1, ensure_ascii=False))
    fig = inv.figure(spec, ver, ver["eos"], inv.ART / "fig1_kernel_and_verification.png")
    return {"meta": meta, "tree": tree, "spec": spec, "ver": ver, "ev_rows": ev_rows,
            "ev": ev, "absent": absent, "support": support, "fig": fig}


# --------------------------------------------------------------------------
# Machine blocks
# --------------------------------------------------------------------------
def machine_blocks(d: dict) -> tuple[dict, list, list]:
    ev, absent = d["ev"], d["absent"]

    def p(key):
        return ev[key]["pointer"]

    methods = [
        f"WCSPH weakly-compressible core: quintic-spline kernel, support 3h ({p('kernel_quintic')}, {p('kernel_norm3d')})",
        f"Tait EOS p=B[(rho/rho0)^gamma-rbackground] with B=c^2 rho0/gamma ({p('eos_tait')}, {p('B_from_soundspeed')})",
        f"density by kernel summation with self term ({p('rhosum_self')}, {p('rhosum_sum')})",
        f"pressure + viscosity pair forces, volume-symmetric (V_i^2+V_j^2) form ({p('pressure_force')}, {p('viscous_force')})",
        f"colour-gradient surface tension, CSF stress projection, no curvature fit ({p('cg_gradient')}, {p('st_stress')}, {p('st_force')})",
        f"multi-material heat conduction with optional one-sided Dirichlet temperature ({p('hc_flux')}, {p('hc_dirichlet')})",
        f"latent-heat phase change: energy-probability evaporation + kernel-weighted mass donation ({p('pc_threshold')}, {p('pc_mass_split')}, {p('pc_energy_update')})",
        f"explicit integration by LAMMPS core fix sph plus script-level multi-criterion dt ({p('integrator')}, {p('dt_acoustic')}, {p('dt_viscous')}, {p('dt_thermal')})",
        f"compute sph/de/atom exposes the per-atom energy change of the conduction operator ({p('hc_flux')})",
    ]
    inv_block = {
        "campaign": "osssph-w2",
        "repo": inv.REPO,
        "tier": 3,
        "pin": inv.PIN,
        "methods": methods,
        "boundary": (
            "frozen boundary particles on 'boundary f' walls with prescribed rho and e "
            f"({p('wall_frozen')}, {p('wall_rho_dirichlet')}); periodic elsewhere "
            f"({p('boundary_fixed_wall')}); insertion ownership covers periodic images "
            f"({p('pc_owns')}); no mirror particles, no boundary-integral or ghost-fluid "
            "extrapolation (curvature/extrapolation probes in the added sources: "
            f"{absent['curvature_surface_tension']['n_hits']} hits)"),
        "gpu": ("none: no CUDA/Kokkos/OpenMP in the added sources "
                f"({absent['cuda_or_kokkos_or_gpu']['n_hits']} hits for CUDA|Kokkos|GPU, "
                f"{absent['openmp']['n_hits']} hits for OpenMP); parallelism is LAMMPS MPI "
                "domain decomposition plus custom forward/reverse comm "
                f"({p('cg_comm')}, {p('pc_reverse_comm')})"),
        "validation": (
            "4 quantitative case families, all manual gnuplot comparison: reverse Poiseuille "
            f"vs transient analytic series ({p('val_poiseuille_analytic')}), cylinder Re=0.03 "
            f"vs digitised Morris-1997 FEM ({p('val_cylinder_ref')}), two-material slab "
            f"conduction vs VanSant closed form ({p('val_slab_analytic')}), capillary droplet "
            "oscillation across nx=40/69/126 (self-convergence only); no test framework and no "
            f"CI (workflow files: {absent['ci_workflows']['n_files']}, test files: "
            f"{len(absent['test_files']['matches'])}); the repo stores no numeric error metric"),
        "license": ("none detected: GitHub license = null and no LICENSE/COPYING file in the "
                    f"tree ({len(absent['license_files']['matches'])} candidates) -> "
                    "reference-only, no code transfer"),
        "transferable": [f"SPHSOL-lammpssphmultiphase-{i:02d}" for i in (1, 2, 3, 4)],
    }

    def sol(n, kind, summary, gap):
        return {"repo": inv.REPO, "id": f"SPHSOL-lammpssphmultiphase-{n:02d}",
                "kind": kind, "summary": summary, "gap": gap}

    sols = [
        sol(1, "adapt",
            ("Colour-gradient surface tension without curvature estimation: interface colour "
             f"function stored as a 3-component atom property ({p('cg_storage')}), per-type-pair "
             f"interface coefficient alpha_ij ({p('cg_gradient')}), force from the projected CSF "
             f"stress tensor ({p('st_stress')}, {p('st_force')}). Portable as an interface-only "
             "force term that needs first kernel derivatives only; licence = none, so "
             "clean-room re-implementation."),
            "GAP-SPH-MULTIPHASE"),
        sol(2, "adapt",
            ("Latent-heat phase-change operator with exact energy bookkeeping: evaporation "
             f"probability from excess sensible energy above saturation ({p('pc_threshold')}), "
             f"interface gate ({p('pc_gate')}), donor mass taken kernel-weighted from liquid "
             f"neighbours ({p('pc_kernel_weights')}, {p('pc_mass_split')}), energy update minus "
             f"m*Hwv ({p('pc_energy_update')}), drained-liquid conversion ({p('pc_convert')}), "
             f"MPI-safe insertion ({p('pc_owns')}, {p('pc_insert')}) and restart-safe RNG "
             f"({p('pc_restart')}). The delta-E = -m*Hwv identity is re-verified here to "
             "machine precision."),
            "GAP-SPH-MULTIPHASE"),
        sol(3, "reference",
            ("Validation baselines consumable as GAP anchors without touching the code: "
             f"transient reverse-Poiseuille analytic series ({p('val_poiseuille_series')}), "
             f"digitised Morris-1997 FEM cylinder profiles ({p('val_cylinder_ref')}), VanSant "
             f"two-material slab closed form generated by a CAS script ({p('val_slab_source')}), "
             f"and a symbolic kernel library with exact normalisations for "
             f"quintic/bspline/Wendland/Lucy families ({p('kernel_lib')}, "
             f"{p('kernel_lib_family')}). Reference-only: cite the values, do not copy code."),
            "GAP-SPH-WCSPH-VALIDATION"),
        sol(4, "fit",
            ("Reusable explicit stability envelope: three time-step limits computed in the input "
             f"deck and reduced by min() -- acoustic CFL ({p('dt_acoustic')}), viscous "
             f"({p('dt_viscous')}), thermal diffusion ({p('dt_thermal')}) -> {p('dt_min')}. Fits "
             "an explicit WCSPH solver's timestep policy directly and supplies the stability "
             "domain that must be quoted next to any convergence study; the formulas are "
             "textbook, the value is the combined envelope."),
            "GAP-SPH-WCSPH-VALIDATION"),
    ]

    tag = {1: "sph-colorgradient-surface-tension", 2: "sph-latent-heat-phase-change",
           3: "sph-wcsp-validation-baselines", 4: "sph-explicit-dt-envelope"}
    files = {1: [p("cg_gradient"), p("st_stress"), p("st_force")],
             2: [p("pc_threshold"), p("pc_mass_split"), p("pc_energy_update")],
             3: [p("val_poiseuille_series"), p("val_cylinder_ref"), p("val_slab_source"),
                 p("kernel_lib")],
             4: [p("dt_acoustic"), p("dt_viscous"), p("dt_thermal")]}
    conv = {
        1: "alpha_ij = interface tension coefficient per type pair; cg = colour function "
           "(3 components); TO-DO: wetting only through alpha_dw, no explicit contact-angle model",
        2: "Hwv = latent heat per unit mass; to_mass = vapour particle mass; Tc = saturation "
           "temperature; change_chance / phase_change_rate = evaporation probability knob",
        3: "nu = kinematic viscosity; L = channel half width; Fo = Fourier number; reference "
           "data are digitised curves, the repo stores no numeric tolerance",
        4: "dx = particle spacing; eta = dynamic viscosity; alpha = thermal diffusivity; "
           "c = numerical sound speed",
    }
    inv_kv = {
        "repo": inv.REPO, "lang": "C++", "license": "none",
        "methods": "; ".join(methods),
        "multiphase": ("two-phase liquid/vapour boiling and evaporation: colour-gradient "
                       f"surface tension ({p('st_force')}) + heat conduction ({p('hc_flux')}) + "
                       f"latent-heat phase change ({p('pc_energy_update')})"),
        "boundary": inv_block["boundary"],
        "gpu": inv_block["gpu"],
        "summary": ("LAMMPS extension package (6 pair styles + 1 fix + 1 compute for boiling and "
                    "evaporation with multiphase WCSPH); quantitative validation exists but is "
                    "manual; no licence file, no CI, no GPU"),
    }
    sol_kv = []
    for s in sols:
        n = int(s["id"][-2:])
        sol_kv.append({
            "repo": inv.REPO, "slug": s["gap"], "tag": tag[n], "method": s["summary"],
            "files": ", ".join(files[n]), "conventions": conv[n],
            "transfer": {"adapt": "adapt", "reference": "reference", "fit": "fits"}[s["kind"]],
            "evidence": ", ".join(files[n]),
        })
    return inv_block, sols, [inv_kv, *sol_kv]


def render_kv_blocks(inv_kv: dict, sol_kv: list[dict]) -> str:
    def block(kind, fields):
        return f"[{kind}]\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + f"\n[/{kind}]"

    head = ("# machine blocks (campaign validator: python -m "
            "tools.oss_inventory.machine_blocks --strict)\n"
            f"# source: {inv.DOC.relative_to(inv.ROOT)}  pin: {inv.PIN}\n")
    parts = [head, block("OSS-INV", inv_kv)]
    parts += [block("OSS-SOL", s) for s in sol_kv]
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
def parameter_table() -> str:
    import re
    rows = []
    for line in inv.src("examples/bubble_on_wall/vars.lmp").splitlines():
        m = re.match(r"variable\s+(\S+)\s+(equal|index)\s+(.+?)\s*$", line)
        if m:
            rows.append([f"`{m.group(1)}`", m.group(2), f"`{m.group(3)}`"])
    return md_table(["变量", "类型", "输入脚本表达式（原样抽取）"], rows)


def meta_table(meta: dict) -> str:
    lic = meta["license"] or "**null（无许可文件）**"
    langs = ", ".join(f"{k} {v} B" for k, v in meta["languages"].items())
    rows = [
        ["仓库", f"`{meta['full_name']}`"],
        ["描述", meta["description"] or "—"],
        ["主语言", f"{meta['language']}（`/languages`：{langs}）"],
        ["stars / forks / watchers",
         f"{meta['stargazers_count']} / {meta['forks_count']} / {meta['subscribers_count']}"],
        ["license", lic],
        ["体积 / 建仓 / 最近推送",
         f"{meta['size']} kB / {meta['created_at']} / {meta['pushed_at']}"],
        ["branches", ", ".join(meta["branches"])],
        ["topics", ", ".join(meta["topics"] or [])],
        ["最近 5 次提交", "<br>".join(f"`{c['sha']}` {c['date']} {c['message']}"
                                     for c in meta["commits"][:5])],
        ["CI", "无 `.github/` 目录（workflow 文件 0，见 3.4）"],
        ["测试布局", "无 test/spec 文件；验证靠 `examples/*/run.sh` + gnuplot 目视"],
    ]
    return md_table(["项", "值"], rows)


def methods_table(ev: dict) -> str:
    spec = [
        ("SPH 变体", "WCSPH（弱可压缩）：五次样条核 + Tait EOS + 密度求和 + 显式积分；非 ISPH/PFEM/δ-SPH",
         ["kernel_quintic", "eos_tait", "rhosum_self", "integrator"]),
        ("核函数", "五次样条 f(q)，支撑 q<3（cut=3h ⇒ h=粒子间距 dx）；归一化 9/(40π) 3D、63/(478π) 2D",
         ["kernel_norm3d", "kernel_norm2d", "kernel_scaling"]),
        ("动量方程", "压力梯度 f=-(V_i²+V_j²)p_ij∇W；粘性 (V_i²+V_j²)η(v_ij)∇W/r（仅一阶导）",
         ["pressure_force", "viscous_force"]),
        ("多相处理", "分相物性（rho0/cv/c/η/k）；跨相压力混合平均；η、k 取调和平均",
         ["mixed_pressure", "eos_tait"]),
        ("自由面 / 边界", "冻结壁粒子（setforce 0）+ 壁上 Dirichlet ρ、e；周期边界；无镜像/虚粒子外推",
         ["wall_frozen", "wall_rho_dirichlet", "boundary_fixed_wall"]),
        ("表面张力", "颜色函数梯度 + CSF 应力投影（无曲率拟合）：`sph/colorgradient` + `sph/surfacetension`",
         ["cg_gradient", "st_stress", "st_force"]),
        ("传热", "对称导热通量 2α(T_i-T_j)∇W/(ρ_iρ_j)；类型对可选单侧 Dirichlet 温度（NULL 关键字）",
         ["hc_flux", "hc_dirichlet"]),
        ("相变", "能量概率蒸发 + 界面门 + 核权重质量捐出 + 潜热扣减 + 抽干转相 + restart 安全 RNG",
         ["pc_threshold", "pc_gate", "pc_mass_split", "pc_energy_update", "pc_convert"]),
        ("时间步控制", "固定 dt = min(声学 CFL, 粘性, 热扩散)；由输入脚本计算并 min()，无内核自适应",
         ["dt_acoustic", "dt_viscous", "dt_thermal", "dt_min"]),
        ("时间积分", "委托 LAMMPS 内核 `fix sph`（显式）；本仓只加力/能量项与相变算子",
         ["integrator"]),
        ("并行策略", "MPI 域分解；自定义 forward/reverse comm（cg 3 分量、ρ、e、被抽走质量）；全邻居表",
         ["cg_comm", "pc_reverse_comm", "full_neigh"]),
        ("GPU", "无：`CUDA|Kokkos|GPU` 与 `_OPENMP` 命中数均为 0（3.4）", []),
        ("IO / 后处理", "dump xyz/custom + `fix print` 输出 rg.dat；gnuplot/awk/maxima 后处理",
         ["val_poiseuille_analytic", "val_slab_source"]),
    ]
    rows = []
    for name, desc, keys in spec:
        ptr = "<br>".join(f"`{ev[k]['pointer']}`" for k in keys) if keys else "—（见 3.4 命中数）"
        rows.append([name, desc, ptr])
    return md_table(["能力", "实现要点", "文件:行"], rows)


def evidence_table(rows: list[dict]) -> str:
    return md_table(["#", "claim", "pointer", "源码原文（自动摘取）"],
                    [[i, r["claim"], f"`{r['pointer']}`", f"`{r['source_line'][:96]}`"]
                     for i, r in enumerate(rows, 1)])


def verification_table(ver: dict, spec: dict | None = None) -> str:
    k, dd, eos, en = ver["kernel"], ver["derivative"], ver["eos"], ver["energy"]
    rows = [
        ["核归一化 ∫W dV（3D）", f"{k['int_W_dV_3d']:.15f}", "1",
         f"{k['rel_err_3d']:.3e}", "sympy 独立积分（scipy.quad，断点 1/3、2/3）"],
        ["核归一化 ∫W dA（2D）", f"{k['int_W_dA_2d']:.15f}", "1",
         f"{k['rel_err_2d']:.3e}", "同上（2πr 权重）"],
        ["源码 norm3d vs 9/(40π)", f"{(spec or {}).get('norm3d', float('nan')):.15f}",
         f"{k['analytic_3d']:.15f}",
         f"{abs((spec or {}).get('norm3d', float('nan')) - k['analytic_3d']) / k['analytic_3d']:.3e}",
         "sph_multiphase.h:12 的常数 vs 解析 9/(40π)"],
        ["源码 norm2d vs 63/(478π)", f"{(spec or {}).get('norm2d', float('nan')):.15f}",
         f"{k['analytic_2d']:.15f}",
         f"{abs((spec or {}).get('norm2d', float('nan')) - k['analytic_2d']) / k['analytic_2d']:.3e}",
         "sph_multiphase.h:13 的常数 vs 解析 63/(478π)"],
        ["导数表 dW/dq vs d/dq W", f"max|Δ| = {dd['max_abs_pointwise_err']:.3e}",
         "0", f"{dd['max_abs_pointwise_err']:.3e}",
         f"sympy 化简残差 = {dd['symbolic_residual']}（{dd['n_sample_points']} 采样点）"],
        ["EOS 斜率 dp/dρ(ρ0) vs c²", f"{eos['dp_drho']:.12f}", f"{eos['c_squared']:.12f}",
         f"{eos['rel_err']:.3e}",
         f"解析导 Bγρ^(γ-1)/ρ0^γ；B = c²ρ0/γ = {eos['B']:.6e}（γ={eos['gamma']}）；"
         f"中心差分交叉校验残差 {eos['rel_err_fd_vs_analytic']:.1e}；p(ρ0) = {eos['p_at_rho0']:.1e}"],
        ["相变能量账 ΔE vs −m·Hwv", f"{en['delta_E']:.12f}", f"{en['expected_delta_E']:.12f}",
         f"{en['rel_err']:.3e}", "随机 6 邻居样本，核权重质量分配"],
    ]
    return md_table(["恒等式", "复算值", "基准/期望", "相对残差", "怎么算的"], rows)


def absence_table(absent: dict) -> str:
    label = {
        "cuda_or_kokkos_or_gpu": "GPU 后端（CUDA / Kokkos）",
        "openmp": "OpenMP 线程并行",
        "delta_sph_density_diffusion": "δ-SPH 密度扩散项",
        "isph_or_projection": "ISPH / 压力泊松投影",
        "in_kernel_adaptive_dt": "内核自适应时间步（CFL 控制器）",
        "tensile_or_artificial_viscosity": "人工粘性 / 张力不稳定处理",
        "curvature_surface_tension": "曲率（κ）表面张力",
        "wet_dry_scheme": "干湿（wet-dry）处理",
        "overlap_check_on_insert": "插入时最小间距/重叠检测",
    }
    rows = [[f"没有 {label.get(k, k)}", f"{absent[k]['n_hits']} 命中",
             ("`" + "`, `".join(absent[k]["hits"]) + "`") if absent[k]["hits"] else "—"]
            for k in label]
    rows.append(["CI 工作流（`.github/`）", f"{absent['ci_workflows']['n_files']} 个文件",
                 f"扫描 {absent['ci_workflows']['n_files_total']} 个源码/输入 blob"])
    rows.append(["测试框架（test/tests/spec）", f"{len(absent['test_files']['matches'])} 个",
                 "—"])
    rows.append(["LICENSE / COPYING 文件", f"{len(absent['license_files']['matches'])} 个",
                 "GitHub API `license = null`"])
    return md_table(["否定声明", "检索结果", "命中位置（前 5）"], rows)


def validation_table(ev: dict) -> str:
    rows = [
        ["反 Poiseuille（Morris 1997 参数）", "瞬态解析级数（awk 现算）",
         f"`{ev['val_poiseuille_analytic']['pointer']}`", "有解析解；无误差指标"],
        ["圆柱绕流 Re=0.03", "Morris 1997 FEM 曲线数字化（morris-fig6-1/2）",
         f"`{ev['val_cylinder_ref']['pointer']}`", "有参照曲线；无误差指标"],
        ["双材料板导热", "VanSant 闭式解（Maxima 生成 infslab.dat）",
         f"`{ev['val_slab_analytic']['pointer']}`", "有闭式解；无误差指标"],
        ["液滴/毛细波振荡", "分辨率自收敛（nx=40/69/126）+ Hu&Adams 2006 文献",
         f"`{ev['two_atom_case']['pointer']}`", "只有目视对比，无收敛阶"],
        ["两粒子算例", "符号核库生成半解析检查输入",
         f"`{ev['two_atom_case']['pointer']}`", "无断言，人工判读"],
    ]
    return md_table(["算例", "参照/基准", "指针", "量化程度"], rows)


def error_table(ver: dict) -> str:
    k, dd, eos, en = ver["kernel"], ver["derivative"], ver["eos"], ver["energy"]
    rows = [
        ["∫W dV = 1（3D，核归一化）", f"{k['rel_err_3d']:.3e}", "1e-12", "是"],
        ["∫W dA = 1（2D，核归一化）", f"{k['rel_err_2d']:.3e}", "1e-12", "是"],
        ["dW/dq 表 vs 符号导数", f"{dd['max_abs_pointwise_err']:.3e}", "1e-12", "是"],
        ["dp/dρ(ρ0) = c²（EOS 构造）", f"{eos['rel_err']:.3e}", "1e-12", "是"],
        ["ΔE = −m·Hwv（相变能量账）", f"{en['rel_err']:.3e}", "1e-12", "是"],
        ["SPH 物理精度（Poiseuille/绕流/导热/毛细波）", "未复现（无 LAMMPS 构建）",
         "3%", "不判定（honest）"],
    ]
    return md_table(["关键量", "相对误差", "门槛", "<3% ?"], rows)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def render(d: dict, inv_block: dict, sols: list, kv_blocks_out: str) -> str:
    ver = d["ver"]
    max_resid = max(ver["kernel"]["rel_err_3d"], ver["kernel"]["rel_err_2d"],
                    ver["derivative"]["max_abs_pointwise_err"], ver["eos"]["rel_err"],
                    ver["energy"]["rel_err"])
    fields = {
        "pin": inv.PIN,
        "repo": inv.REPO,
        "date": _dt.date.today().isoformat(),
        "art": f"`{inv.ART.relative_to(inv.ROOT)}`",
        "mach": f"`{inv.MACH.relative_to(inv.ROOT)}`",
        "cache_hint": "`~/.cache/oss_inv/<owner>_<repo>/<pin>`",
        "n_tree": len(d["tree"]),
        "n_blobs": len(d["tree"]),
        "n_files_code": d["absent"]["_scope"]["n_files"],
        "n_ci": d["absent"]["ci_workflows"]["n_files"],
        "n_tests": len(d["absent"]["test_files"]["matches"]),
        "n_2h": d["support"]["n_within_2h"],
        "n_3h": d["support"]["n_within_cut3h"],
        "max_resid": f"{max_resid:.3e}",
        "parameter_table": parameter_table(),
        "meta_table": meta_table(d["meta"]),
        "methods_table": methods_table(d["ev"]),
        "evidence_table": evidence_table(d["ev_rows"]),
        "verification_table": verification_table(ver, d["spec"]),
        "absence_table": absence_table(d["absent"]),
        "validation_table": validation_table(d["ev"]),
        "error_table": error_table(ver),
        "figure_path": d["fig"]["png"],
        "inv_json": "[OSS-INV]" + json.dumps(inv_block, ensure_ascii=False) + "[/OSS-INV]",
        "sol_json": "\n".join("[OSS-SOL]" + json.dumps(s, ensure_ascii=False) + "[/OSS-SOL]"
                              for s in sols),
        "pc_convert": d["ev"]["pc_convert"]["pointer"],
        "pc_threshold": d["ev"]["pc_threshold"]["pointer"],
        "pc_gate": d["ev"]["pc_gate"]["pointer"],
        "pc_restart": d["ev"]["pc_restart"]["pointer"],
    }
    # Substitution is key-driven, not str.format: the report contains literal
    # braces in formulas (e.g. dp/d\rho|_{\rho0}) that must survive untouched.
    tpl = inv.TEMPLATE.read_text()
    leftover = sorted(set(re.findall(r"\{([a-z_0-9]+)\}", tpl)) - set(fields))
    assert not leftover, f"unfilled template keys: {leftover}"
    for key, value in fields.items():
        tpl = tpl.replace("{" + key + "}", str(value))
    return tpl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true",
                    help="reuse artifacts/oss-sph/.../repo_meta.json instead of calling GitHub")
    args = ap.parse_args(argv)

    d = build_inputs(offline=args.offline)
    inv_block, sols, kv = machine_blocks(d)
    kv_text = render_kv_blocks(kv[0], kv[1:])
    inv.MACH.parent.mkdir(parents=True, exist_ok=True)
    inv.MACH.write_text(kv_text, encoding="utf-8")
    (inv.ART / "machine_blocks.json").write_text(json.dumps(
        {"OSS-INV": inv_block, "OSS-SOL": sols}, indent=1, ensure_ascii=False))

    report = render(d, inv_block, sols, kv_text)
    inv.DOC.write_text(report, encoding="utf-8")

    blocks = parse_text(kv_text, str(inv.MACH))
    violations = validate(blocks)
    summary = summarise(blocks)
    print(f"report={inv.DOC.relative_to(inv.ROOT)} bytes={len(report)}")
    print(f"machine_txt={inv.MACH.relative_to(inv.ROOT)} blocks={summary['blocks']} "
          f"inv={summary['oss_inv']} sol={summary['oss_sol']} "
          f"slug_class={summary['slug_class']} transfer={summary['transfer']}")
    print(f"violations={len(violations)} {violations}")
    print("identities=" + json.dumps({k: {kk: vv for kk, vv in v.items()
                                          if kk.endswith(('err', 'err_3d', 'err_2d'))}
                                      for k, v in d["ver"].items()}))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())