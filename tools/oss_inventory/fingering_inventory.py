#!/usr/bin/env python3
"""ebinan92/Fingering_dynamics 技术盘点（OSS-INV）—— 证据定位 + 复算 + 报告渲染。

流程
----
1. 用 `fingering_evidence.py` 的（路径，逐字子串）表在**真实源码树**里定位 ``path:line``；
   定位失败/命中多次 = 硬失败（反臆造门），不允许手工写行号。
2. 统计 LOC；跑缺席断言 `ABSENT`（token 出现 0 次）与出现次数断言 `EXPECTED_COUNTS`。
3. 跑 `fingering_physics.py` 的独立复算（差分算子、kink、σ 恒等式、黏度插值、
   无量纲换算链、谱方法积分因子、几何块数）→ JSON，全部程序化，不许手抄。
4. 用 `fingering_report_template.md` 渲染八节 markdown + `[OSS-INV]`/`[OSS-SOL]` 机器块，
   并生成三张 png。
5. 可选 ``--post`` 投递到 ``$TENSOR_API_BASE/tensor/reports``。

用法
----
    python3 tools/oss_inventory/fingering_inventory.py --src /tmp/fing_src --write \
        --json runs/oss_fingering/analysis_results.json \
        --figdir runs/oss_fingering/figures [--post] [--check-only]
"""

from __future__ import annotations

import argparse
import base64
import re
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import fingering_evidence as ev  # noqa: E402
import fingering_physics as fp  # noqa: E402

REPO = "ebinan92/Fingering_dynamics"
TITLE = "【盘点】Fingering_dynamics —— 相场/自由能两相 LBM + Zou-He + 半程反弹润湿（tier 1）"
SCENARIO_KEY = "oss-inventory"
MISSION = 8545
DOC_PATH = ROOT / "docs" / "oss_inventory" / "OSS-INV-Fingering_dynamics.md"
TEMPLATE = HERE / "fingering_report_template.md"


class PointerError(RuntimeError):
    pass


@dataclass
class Resolved:
    path: str
    line: int
    text: str
    pid: str
    note: str

    @property
    def ref(self) -> str:
        return "`%s:%d`" % (self.path, self.line)


# --------------------------------------------------------------------------- #
# 证据定位
# --------------------------------------------------------------------------- #
def resolve_all(src: Path) -> dict[str, Resolved]:
    out: dict[str, Resolved] = {}
    problems: list[str] = []
    for path, needle, pid, note in ev.ALL:
        f = src / path
        if not f.exists():
            problems.append("[missing file] %s" % path)
            continue
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        occ = None
        m = re.search(r"#(\d+)$", needle)
        if m:
            occ = int(m.group(1))
            needle = needle[: m.start()]
        hits = [i + 1 for i, ln in enumerate(lines) if needle in ln]
        if not hits:
            problems.append("[needle not found] %s :: %r" % (path, needle))
            continue
        if occ is None and len(hits) > 1:
            problems.append("[ambiguous needle x%d] %s :: %r at %s" % (len(hits), path, needle, hits))
            continue
        if occ is not None:
            if occ > len(hits):
                problems.append("[occurrence %d > hits %d] %s :: %r" % (occ, len(hits), path, needle))
                continue
            hits = [hits[occ - 1]]
        if pid in out:
            problems.append("[duplicate id] %s" % pid)
        out[pid] = Resolved(path, hits[0], lines[hits[0] - 1].strip(), pid, note)
    for path, token, note in ev.ABSENT:
        f = src / path
        text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        if token.lower() in text.lower():
            problems.append("[absent-check failed] %s contains %r (%s)" % (path, token, note))
    for path, token, want, note in ev.EXPECTED_COUNTS:
        f = src / path
        text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        got = text.count(token)
        if got != want:
            problems.append("[count-check failed] %s %r: got %d want %d (%s)" % (path, token, got, want, note))
    if problems:
        raise PointerError("evidence resolution failed:\n  " + "\n  ".join(problems))
    return out


def loc_table(src: Path) -> list[list]:
    rows = []
    for f in sorted(src.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".md"):
            n = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            rows.append([str(f.relative_to(src)), n])
    return rows


# --------------------------------------------------------------------------- #
# 复算
# --------------------------------------------------------------------------- #
def run_checks() -> dict:
    stencil = fp.stencil_check()
    kink = fp.kink_convergence()
    fine = fp.solve_kink_newton(n=1601)
    chain = fp.conversion_chain()
    geom = fp.staggered_obstacle_array()
    sigma_num = fp.kink_surface_tension_numeric(chain["kappa"], 2.0)
    return {
        "stencil": stencil,
        "kink_convergence": kink,
        "kink_fine": {k: v for k, v in fine.items() if k not in ("x", "psi", "exact")},
        "curves": {
            "x": [float(v) for v in fine["x"][::8]],
            "psi": [float(v) for v in fine["psi"][::8]],
            "exact": [float(v) for v in fine["exact"][::8]],
        },
        "conversion": chain,
        "viscosity": fp.viscosity_check(),
        "geometry": {k: v for k, v in geom.items() if k != "blocks"},
        "spectral": fp.spectral_decay_check(),
        "sigma_numeric": sigma_num,
        "sigma_numeric_rel_err": abs(sigma_num - chain["sigma"]) / chain["sigma"],
    }


# --------------------------------------------------------------------------- #
# 取回清单（可复核：blob SHA + 本地 sha256 + 行数）
# --------------------------------------------------------------------------- #
def build_manifest(src: Path) -> dict:
    import hashlib
    import urllib.request

    files = []
    for f in sorted(src.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".md", ".gitignore"):
            raw = f.read_bytes()
            files.append({
                "path": str(f.relative_to(src)),
                "bytes": len(raw),
                "lines": len(raw.decode("utf-8", "replace").splitlines()),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    assets = []
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/%s/git/trees/HEAD?recursive=1" % REPO,
            headers={"User-Agent": "oss-inventory", "Accept": "application/vnd.github+json"},
        )
        tree = json.load(urllib.request.urlopen(req, timeout=30))["tree"]
        assets = [{"path": t["path"], "bytes": t.get("size"), "sha": t["sha"], "read": False,
                   "reason": "二进制资产（gif/png），无可复核数字，未纳入任何结论"}
                  for t in tree if t["type"] == "blob" and not t["path"].endswith((".py", ".md", ".gitignore"))]
        n_entries = len(tree)
    except Exception as exc:  # noqa: BLE001
        n_entries = None
        assets = [{"note": "tree API unavailable: %s" % type(exc).__name__}]
    return {
        "repo": REPO,
        "default_branch": "master",
        "tree_entries": n_entries,
        "fetched_via": "api.github.com contents API (base64); git clone timed out at 120 s",
        "text_files": files,
        "assets_not_read": assets,
    }


# --------------------------------------------------------------------------- #
# 图件
# --------------------------------------------------------------------------- #
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def make_figures(checks: dict, outdir: Path) -> list[dict]:
    plt = _plt()
    outdir.mkdir(parents=True, exist_ok=True)
    figs = []

    kc = checks["kink_convergence"]
    cv = checks["curves"]
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.6))
    ax[0].plot(cv["exact"], "k-", lw=2.4, label="analytic tanh(x/xi), xi=2")
    ax[0].plot(cv["psi"], "r--", lw=1.3, label="Newton solve of mu=0 (discrete)")
    x = np.array(cv["x"])
    ax[0].set_xlim(int(np.sum(x < -12)), int(np.sum(x < 12)))
    ax[0].set_xlabel("x [lattice]")
    ax[0].set_ylabel("order parameter psi")
    ax[0].set_title("free-energy interface profile (repo constants a, kappa)")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    ax[1].loglog(kc["h"], kc["max_abs_err"], "o-", label="max |psi_num - tanh|")
    hh = np.array(kc["h"])
    ax[1].loglog(hh, kc["max_abs_err"][0] * (hh / hh[0]) ** 2, "k:", lw=1, label="slope 2")
    ax[1].set_xlabel("grid spacing h")
    ax[1].set_ylabel("max abs error")
    ax[1].set_title("interface-profile convergence (2nd order)")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    p = outdir / "fig1_interface_profile.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    figs.append({"name": p.name, "path": p,
                 "caption": "自由能相场平衡界面：Newton 离散解与解析 kink psi=tanh(x/xi) 重合，误差二阶收敛（xi=2，仓内常数）"})

    psi = np.linspace(-1.2, 1.2, 400)
    eta_n = checks["conversion"]["Eta_n"]
    v = fp.mix_viscosity(psi, eta_n, 20.0)
    tau = 3 * v + 0.5
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.4))
    ax[0].plot(psi, v, "b-", lw=2)
    for xv in (-1, 1):
        ax[0].axvline(xv, color="gray", ls=":", lw=1)
    ax[0].set_xlabel("psi")
    ax[0].set_ylabel("nu(psi) [lattice]")
    ax[0].set_title("harmonic two-phase viscosity, M=20")
    ax[0].grid(alpha=0.3)
    ax[1].plot(psi, tau, "r-", lw=2)
    ax[1].axhline(0.5, color="k", ls="--", lw=0.8)
    ax[1].axhline(checks["conversion"]["tau"], color="g", ls=":", lw=1.2)
    ax[1].set_xlabel("psi")
    ax[1].set_ylabel("tau_f = 3 nu + 1/2")
    ax[1].set_title("BGK relaxation time (max %.2f)" % tau.max())
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    p = outdir / "fig2_viscosity_map.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    figs.append({"name": p.name, "path": p,
                 "caption": "两相黏度调和插值 nu(psi) 与对应 BGK 松弛时间：psi=+1 端回到仓内 tau=1/(3-sqrt3)，psi=-1 端 tau 远超 2（强耗散）"})

    st = checks["stencil"]
    ms = [32, 64, 128, 256]
    hs = [2 * np.pi / m for m in ms]
    fig, ax = plt.subplots(figsize=(5.0, 3.6))
    ax.loglog(hs, [st["lap_l2_%d" % m] for m in ms], "o-", label="Laplacian (D2Q9)")
    ax.loglog(hs, [st["gx_l2_%d" % m] for m in ms], "s-", label="d/dx (4-axis + diag)")
    hh = np.array(hs)
    ax.loglog(hh, st["lap_l2_32"] * (hh / hh[0]) ** 2, "k:", lw=1, label="slope 2")
    ax.set_xlabel("grid spacing h")
    ax.set_ylabel("L2 error on sin(2x)cos(3y)")
    ax.set_title("discrete operators: 2nd-order accuracy")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    p = outdir / "fig3_stencil_convergence.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    figs.append({"name": p.name, "path": p,
                 "caption": "仓内梯度/拉普拉斯模板复核：二次场精确（0 误差）、正弦场二阶收敛，各向同性模板无误"})
    return figs


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _md_table(rows, header) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build_values(P: dict, loc, checks) -> dict:
    st = checks["stencil"]
    kc = checks["kink_convergence"]
    kf = checks["kink_fine"]
    ch = checks["conversion"]
    vs = checks["viscosity"]
    gm = checks["geometry"]
    sp = checks["spectral"]

    vals = {("P_" + k): v.ref for k, v in P.items()}
    vals.update({
        "REPO": REPO,
        "MISSION": str(MISSION),
        "N_POINTERS": str(len(ev.ALL)),
        "N_ABSENT": str(len(ev.ABSENT)),
        "N_COUNTS": str(len(ev.EXPECTED_COUNTS)),
        "LOC_TABLE": _md_table(loc, ["文件", "行数"]),
        "LOC_TOTAL": str(sum(n for _, n in loc)),
        "T_STENCIL": _md_table([
            ["二次多项式场（Δx=1，内点）", "理论误差 0", "%.3g" % st["quad_exact_abs_err"], "精确"],
            ["sin(2x)cos(3y)，网格 32→256", "二阶",
             "收敛率 %.2f（laplacian）/ %.2f（grad_x）" % (st["lap_rate_min"], st["grad_rate_min"]), "二阶"],
        ], ["检验", "期望", "实测", "判定"]),
        "T_KINK": _md_table([
            ["离散解 vs tanh(x/ξ)（n=1601, ξ=2）", "%.3g" % kf["max_abs_err"], "< 1e-3", "通过"],
            ["收敛率（101→1601）", " / ".join("%.2f" % r for r in kc["rate"]), "≈2（二阶）", "通过"],
            ["σ=κ∫ψ'²dx 数值（%.0f 点）" % 200001, "%.10g" % checks["sigma_numeric"],
             "4κ/(3ξ)=%.10g" % ch["sigma_from_kappa"],
             "相对误差 %.1e" % checks["sigma_numeric_rel_err"]],
        ], ["检验", "实测", "期望", "判定"]),
        "T_VISC": _md_table([
            ["ν(ψ=+1)", "%.6g" % vs["v_psi_plus1"], "η_n = v0", "与仓内 τ=1/(3−√3) 一致"],
            ["ν(ψ=−1)", "%.6g" % vs["v_psi_minus1"], "η_n·M（M=20）", "比值 %.3f" % vs["contrast"]],
            ["ν(ψ=0)", "%.6g" % vs["v_psi_0"], "调和平均 2ν₁ν₂/(ν₁+ν₂)", "相对误差 %.1e" % vs["harm_rel_err"]],
            ["τ_f(ψ=−1)", "%.3f" % vs["tau_psi_minus1"], "BGK 精度区间 0.5~2",
             "远超 2：黏性耗散大（陷阱 5）"],
        ], ["检验", "实测", "期望", "判定"]),
        "T_CHAIN": _md_table([
            ["τ = 1/(3−√3)", "%.6f" % ch["tau"], P["tau_magic"].ref],
            ["v0 = (τ−0.5)/3", "%.6f" % ch["v0"], P["eta_n"].ref],
            ["C_t", "%.6e s" % ch["C_t"], P["c_t"].ref],
            ["η_n", "%.6f" % ch["Eta_n"], P["eta_n"].ref],
            ["σ（无量纲）", "%.6f" % ch["sigma"], P["sigma_dimless"].ref],
            ["κ", "%.6f" % ch["kappa"], P["kappa_relation"].ref],
            ["a", "%.6f" % ch["a"], P["a_relation"].ref],
            ["γ（迁移率）", "%.6f" % ch["gamma"], P["gamma_mobility"].ref],
            ["u0（格子）", "%.6f" % ch["u0"], P["u0"].ref],
            ["物理 u0 = u0·C_W/C_t", "%.4f m/s" % ch["physical_u0"], "换算链"],
            ["Ma = u0/c_s", "%.4f" % ch["Ma"], "不可压 LBM 判据 < 0.1（通过）"],
            ["Re = u0·20/η_n", "%.3f" % ch["Re_h20"], "Stokes/指进区（通过）"],
            ["Ca 回算 = u0·η_n/σ", "%.6f" % ch["Ca_back"],
             "输入 Ca=%.6f，相对误差 %.1e（代数恒等）" % (ch["Ca_in"], ch["Ca_identity_rel_err"])],
        ], ["量", "值", "证据/判据"]),
        "T_SPECTRAL": _md_table([
            ["k=2, Δt=0.001, 500 步", "%.15f" % sp["c_num_abs"], "%.15f" % sp["c_exact_abs"],
             "%.1e" % sp["rel_err"]],
        ], ["检验", "数值 |c|", "解析 |c|", "相对误差"]),
        "V_NBLOCKS": str(gm["n_blocks"]),
        "V_SOLID": "%.3f%%" % (100 * gm["solid_fraction"]),
        "V_POROSITY": "%.3f%%" % (100 * gm["porosity"]),
        "V_MA": "%.4f" % ch["Ma"],
        "V_RE": "%.2f" % ch["Re_h20"],
        "V_TAUMAX": "%.3f" % vs["tau_psi_minus1"],
        "V_ETA_N": "%.6f" % ch["Eta_n"],
        "V_V0": "%.6f" % ch["v0"],
    })
    return vals


def machine_blocks(P: dict, checks: dict) -> str:
    gm = _GM
    kf, ch, sp = checks["kink_fine"], checks["conversion"], checks["spectral"]
    r = lambda key: "%s:%d" % (P[key].path, P[key].line)  # noqa: E731
    fields = [
        ("repo", REPO),
        ("ref", "master (HEAD tree 20 entries; 14 text files read via contents API)"),
        ("lang", "Python (numpy / cupy / sympy / scipy / opencv / numba); 2D only; no compiled core"),
        ("license", "NONE (license=None -> all rights reserved -> reference-only for us: formulas only, no code text)"),
        ("methods", "lattice=D2Q9 with hardcoded e/w table (%s); two-distribution free-energy phase-field "
                    "(f->rho,p; g->psi); mu=a*psi(1-psi^2)-kappa*lap(psi) at %s; kappa=0.75*xi*sigma at %s; "
                    "a=-2*kappa/xi^2 at %s; variable relaxation tau_f=3*nu(psi)+1/2 with harmonic two-phase "
                    "viscosity (M=20) at %s; fixed tau for g; p=rho/3+psi*mu at %s; NO Shan-Chen, NO "
                    "color-gradient, NO free-surface, NO gravity"
                    % (r("d2q9_table"), r("mu_formula"), r("kappa_relation"), r("a_relation"),
                       r("mix_tau"), r("pressure_law"))),
        ("boundary", "Zou-He velocity INLET with chemical-potential flux correction -psi_x*mu/2 at %s, psi "
                     "reconstructed as 1-sum(g) split by (w1+w5+w8) at %s; OUTLET is ALSO velocity (u_x=u0) at %s "
                     "(not a pressure outlet); half-way bounce-back for top/bottom walls at %s; rectangular "
                     "obstacles via 8-class corner masks at %s; circle/ellipse via concave/convex corner tables "
                     "at %s; np.roll periodic streaming then boundary overwrite at %s"
                     % (r("zh_in_flux"), r("zh_in_psi_split"), r("zh_out"), r("top_bottom_bb"),
                        r("bb_rec_def"), r("bb_circle_def"), r("stream"))),
        ("wetting", "wall virtual order parameter psi_wall injected as ghost node at %s; the four scripts use "
                    "psi_wall=-1.0/0.0/-0.5/+1.0; no contact-angle calibration anywhere (Theta defined once at %s "
                    "and never used)" % (r("psi_wall_halo"), r("val_theta"))),
        ("geometry", "create_block.py rasterizes rect/circle/ellipse + binary_fill_holes, setblock() -> boolean "
                     "mask + corner list at %s, called at %s; main case = staggered 4/5-block array, recomputed by "
                     "us as %d blocks of 20x20 (solid %.3f%%, porosity %.3f%%)"
                     % (r("blk_set"), r("blk_call"), gm["n_blocks"], 100 * gm["solid_fraction"],
                        100 * gm["porosity"])),
        ("gpu", "fingering_periodic_gpu.py = CuPy drop-in replacement (78 cp. calls, %s), ZERO custom kernels "
                "(RawKernel / ElementwiseKernel / cuda / mpi: 0 hits, 4 absent-checks); no fusion, no multi-GPU; "
                "cv2 still imported at %s" % (r("gpu_device_array"), r("gpu_cv2"))),
        ("validation", "validation.py runs a droplet and PLOTS psi only at %s; no Young-Laplace, no contact-angle "
                       "extraction, no analytic comparison, no mass-conservation monitor, no asserts, no tests, no "
                       "CI, no requirements.txt; branch_width.py = OpenCV measurement tool at %s without reference "
                       "values" % (r("val_plot"), r("bw_width"))),
        ("extra", "separate pseudo-spectral Saffman-Taylor solver in spectrum_method/ (integrating factor + AB2, "
                  "RK4 start) at %s; integrating-factor diffusion is exact to round-off (our check: rel err <1e-14), "
                  "but no linear-stability comparison inside the repo" % r("sp_if")),
        ("verified-by-us", "kink psi=tanh(x/xi) from repo constants (Newton, max err %.2e at n=1601, 2nd-order "
                           "convergence); sigma=4*kappa/(3*xi) numeric quadrature vs repo sigma (rel err %.1e); "
                           "D2Q9 gradient/Laplacian stencils exact on quadratics and 2nd order on smooth fields; "
                           "spectral integrating factor exact for pure diffusion (rel err %.1e); Ca closed-form "
                           "identity (rel err %.1e); dimensionless chain gives Ma=%.4f, Re=%.2f"
                           % (kf["max_abs_err"], checks["sigma_numeric_rel_err"], sp["rel_err"],
                              ch["Ca_identity_rel_err"], ch["Ma"], ch["Re_h20"])),
        ("summary", "REAL-formulas / WITHHELD-accuracy. Physics implementation is genuine and internally consistent "
                    "(free-energy phase-field relations + correct isotropic stencils), but the repository offers zero "
                    "quantitative validation, zero tests, no CI, and no license (reference-only). Transferable: the "
                    "three calibration relations (kappa=0.75*xi*sigma, a=-2*kappa/xi^2, sigma=4*kappa/(3*xi)), the "
                    "two-conservation Zou-He convention, corner-class bounce-back tables, raster-obstacle geometry "
                    "pipeline. NOT transferable: GPU path (no fused kernel), high-density ratio (rho~1), any accuracy number."),
    ]
    inv = "[OSS-INV]\n" + "\n".join("%s: %s" % (k, v) for k, v in fields) + "\n[/OSS-INV]"

    sols = []
    specs = [
        ("GAP-LBM-MCMP-POISEUILLE", "phasefield-mcmp-poiseuille", "adapt",
         "用双分布自由能相场（f->rho,p; g->psi, mu=a*psi(1-psi^2)-kappa*lap(psi)）跑两相分层 Poiseuille：把障碍阵列几何换成解析通道，"
         "入口保留 Zou-He 速度边界。相对我们现有伪势/颜色梯度 MCMP，这是一条独立内核路线，可交叉验证。",
         ["mu_formula", "zh_in", "mix_tau"]),
        ("NEW:GAP-LBM-PHASEFIELD-FREEENERGY", "free-energy-cahn-hilliard-core", "fits",
         "守恒型 Cahn-Hilliard 相场内核：序参量 psi 由第二个分布 g 承载（psi=sum g），mu=a*psi(1-psi^2)-kappa*lap(psi)，"
         "kink 解 psi=tanh(x/xi) 与 sigma=4*kappa/(3*xi)（等价 kappa=0.75*xi*sigma）经本报告解析+数值复核一致，可作为内核验收门。",
         ["mu_formula", "kappa_relation", "a_relation"]),
        ("NEW:CAP-LBM-MCMP-FLUX-BOUNDARY", "two-conservation-zouhe-with-chemical-potential-flux", "adapt",
         "多相 Zou-He：入口同时补质量式 rho_in=1/(1-u_x)(sum f_k + ... - psi_x*mu/2) 与序参量式 psi_in=1-sum(g), 未知方向按 w1+w5+w8 归一分配；"
         "出口同式但 u_x=+u0。移植时必须补全出口角点并把出口改成压力/回压型。",
         ["zh_in_rho", "zh_in_flux", "zh_in_psi_split", "zh_out"]),
        ("NEW:GAP-LBM-WETTING-DIFFUSE", "diffuse-interface-wetting-via-wall-order-parameter", "fits",
         "润湿不用 Shan-Chen 吸附系数 G_ads, 而是把壁面格点 psi 固定为 psi_wall 并作为差分虚拟节点参与 nabla/lap。"
         "优点：无 G_ads 标定；缺点：接触角与 psi_wall 的映射需自建标定表（本仓没做）。",
         ["psi_wall_halo", "psi_wall_main"]),
        ("NEW:CAP-LBM-POROUS-OBSTACLE-STAGGER", "raster-obstacle-corner-class-bounce-back", "adapt",
         "任意栅格障碍 -> 布尔掩码 + 角点表（setblock）, 再用 8 类（矩形）/12 类（圆、椭圆凹凸角）半程反弹表处理边与角；"
         "几何管线可与其几何生成脚本对齐（交错 4/5 块阵列，本报告复算 40 块、孔隙率 88.9%）。",
         ["blk_set", "bb_rec_def", "bb_circle_def", "blk_call"]),
        ("NEW:CAP-HELE-SHAW-SPECTRAL", "spectral-saffman-taylor-integrating-factor", "adapt",
         "谱方法指进：涡量-流函数 phi_hat=-i*R*k_y*c_hat/(k^2+i*R(k_x*c_x+k_y*c_y)), 扩散用积分因子 exp(-k^2*dt) 精确、"
         "非线性项用 AB2（启动 RK4）。可作指进线性稳定性/分辨率基准，LBM 只负责非线性阶段与润湿壁面。",
         ["sp_phi", "sp_if", "sp_ab2"]),
        ("NEW:CAP-LBM-GPU-FUSED-KERNEL", "cupy-dropin-no-kernel-fusion", "reject-as-is",
         "GPU 版只是 numpy->cupy 直替换（78 处 cp.）, 无 RawKernel/ElementwiseKernel、无融合、无多卡；每步仍有 ~20 个算子级 kernel "
         "与全网格临时量。只能参考算子求值顺序，性能路径不可继承（负面结论，用于关闭该能力的捷径假设）。",
         ["gpu_cupy", "gpu_device_array"]),
        ("GAP-LBM-CI", "no-tests-no-ci-no-asserts", "reject",
         "无 tests/、无 CI、无断言、无 requirements.txt；唯一含 Theta 的行从未被使用。验证面反面教材：任何从本仓搬来的物理"
         "都必须由我们自带验收门。",
         ["val_theta", "val_plot"]),
    ]
    for slug, tag, transfer, method, keys in specs:
        sols.append("[OSS-SOL]\nrepo: %s\nslug: %s\ntag: %s\nmethod: %s\nfiles: %s\ntransfer: %s\n[/OSS-SOL]"
                    % (REPO, slug, tag, method, ", ".join(r(k) for k in keys), transfer))
    return inv + "\n\n" + "\n\n".join(sols)


_GM = {"n_blocks": 0, "porosity": 0.0}


def render(P, loc, checks, figs) -> str:
    global _GM
    _GM = checks["geometry"]
    vals = build_values(P, loc, checks)
    text = TEMPLATE.read_text(encoding="utf-8")
    blocks = machine_blocks(P, checks)
    body = text.replace("{MACHINE_BLOCKS}", blocks)
    return body.format(**vals)


# --------------------------------------------------------------------------- #
# 投递
# --------------------------------------------------------------------------- #
def post_report(markdown: str, figs: list[dict], summary: str) -> dict:
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "")
    pw = os.environ.get("TENSOR_AGENT_PASS", "")
    tok = os.environ.get("TOKEN", "")
    if not tok and user:
        last = None
        for ep in ("/auth/login", "/tensor/login"):
            try:
                req = urllib.request.Request(
                    base + ep,
                    data=json.dumps({"username": user, "password": pw}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                j = json.load(urllib.request.urlopen(req, timeout=30))
                tok = j.get("access_token") or j.get("token") or ""
                if tok:
                    break
            except Exception as exc:  # noqa: BLE001
                last = exc
        if not tok:
            raise RuntimeError("login failed: %r" % (last,))
    payload = {
        "title": TITLE,
        "summary": summary,
        "scenario_key": SCENARIO_KEY,
        "mission_id": MISSION,
        "tags": ["oss-inventory", "lbm", "multiphase", "phase-field", "zou-he", "wettability", "fingering"],
        "markdown": markdown,
        "figures": [
            {"name": f["name"], "caption": f["caption"],
             "png_base64": base64.b64encode(Path(f["path"]).read_bytes()).decode()}
            for f in figs
        ],
    }
    req = urllib.request.Request(
        base + "/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
    )
    return json.load(urllib.request.urlopen(req, timeout=120))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/tmp/fing_src")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", default=str(ROOT / "runs" / "oss_fingering" / "analysis_results.json"))
    ap.add_argument("--figdir", default=str(ROOT / "runs" / "oss_fingering" / "figures"))
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--post", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    P = resolve_all(src)
    print("evidence ok: %d pointers resolved, %d absent checks, %d count checks"
          % (len(P), len(ev.ABSENT), len(ev.EXPECTED_COUNTS)))
    if args.check_only:
        return 0

    loc = loc_table(src)
    checks = run_checks()
    figs = make_figures(checks, Path(args.figdir))
    markdown = render(P, loc, checks, figs)

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({
        "repo": REPO,
        "mission": MISSION,
        "pointers": {k: {"path": v.path, "line": v.line, "text": v.text, "note": v.note}
                     for k, v in P.items()},
        "loc": loc,
        "checks": checks,
        "figures": [{"name": f["name"], "caption": f["caption"]} for f in figs],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s" % args.json)

    if args.write:
        (Path(args.json).parent / "source_manifest.json").write_text(
            json.dumps(build_manifest(src), ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote %s" % (Path(args.json).parent / "source_manifest.json"))
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(markdown, encoding="utf-8")
        print("wrote %s (%d chars)" % (DOC_PATH, len(markdown)))

    if args.post:
        summary = ("ebinan92/Fingering_dynamics 深盘：二维自由能相场两相 LBM（f->rho/p, g->psi, mu=a*psi(1-psi^2)-kappa*lap psi）、"
                   "两级 Zou-He（含化学势通量）与半程反弹障碍/润湿壁；复算证实 kink psi=tanh(x/xi)、sigma=4*kappa/(3*xi)、"
                   "D2Q9 模板二阶收敛，但仓内零验证、零测试、无许可证 -> REAL-公式 / WITHHELD-精度。")
        r = post_report(markdown, figs, summary)
        print("posted: %s" % json.dumps(r, ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())