#!/usr/bin/env python3
"""Numeric sanity checks + figures for the SPlisHSPlasH deep-dive (m8650).

Three independent products, all computed at run time (no hand-copied numbers):

1. ``kernel_constants`` -- the kernels are transcribed verbatim from
   ``SPlisHSPlasH/SPHKernels.h`` (cited lines), then integrated numerically
   over their support: a constant is right iff ``int W dV == 1``.  The
   support-radius identity ``R = 2h`` used by SPlisHSPlasH is checked by
   comparing ``m_k R^q`` with the textbook ``sigma(h) * (R/2)^q``.

2. ``lattice_consistency`` -- the summation form actually used by the solvers
   (``rho_i = rho0 * sum_j V_j W_ij``, see ``TimeStep::computeDensities``)
   is evaluated on a regular lattice for decreasing particle spacing dx.
   This measures the *kernel smoothing* error of the discretisation (and its
   observed order) plus the *support deficiency* next to a wall, which is
   exactly the error the boundary models must compensate.

3. Figures: ``splishsplash_fig1_kernels.png`` (profiles + convergence of the
   density summation), ``splishsplash_fig2_evidence.png`` (repository
   evidence composition / shipped-scene inventory).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
OUT_JSON = HERE / "splishsplash_kernel_check.json"
FIG1 = HERE / "splishsplash_fig1_kernels.png"
FIG2 = HERE / "splishsplash_fig2_evidence.png"
SHA = "dba6b26b9387ab6bdd187d6df0a7c9d490d03500"

# ---------------------------------------------------------------------------
# Kernel forms transcribed from SPlisHSPlasH/SPHKernels.h at commit dba6b26
#   CubicKernel::setRadius   -> SPHKernels.h:31   m_k = 8/(pi R^3)
#   SpikyKernel::setRadius   -> SPHKernels.h:209  m_k = 15/(pi R^6)
#   WendlandQuinticC2 3D     -> SPHKernels.h:290  m_k = 21/(2 pi R^3)
#   CubicKernel2D            -> SPHKernels.h:479  m_k = 40/(7 pi R^2)
#   WendlandQuinticC2 2D     -> SPHKernels.h:562  m_k = 7/(pi R^2)
# ---------------------------------------------------------------------------


def cubic_poly(q: np.ndarray) -> np.ndarray:
    """Cubic spline shape, SPHKernels.h:44-52 (identical 2D form at :492-500)."""
    q = np.asarray(q, dtype=float)
    out = np.zeros_like(q)
    lo = q <= 0.5
    hi = (q > 0.5) & (q <= 1.0)
    out[lo] = 6.0 * q[lo] ** 3 - 6.0 * q[lo] ** 2 + 1.0
    out[hi] = 2.0 * (1.0 - q[hi]) ** 3
    return out


def wendland_poly(q: np.ndarray) -> np.ndarray:
    """Wendland C2 shape, SPHKernels.h:299-302 (2D form at :571-574)."""
    q = np.asarray(q, dtype=float)
    out = np.zeros_like(q)
    m = q <= 1.0
    out[m] = (1.0 - q[m]) ** 4 * (4.0 * q[m] + 1.0)
    return out


def spiky_poly(r: np.ndarray, R: float) -> np.ndarray:
    """Spiky shape, SPHKernels.h:361-375 (3D only)."""
    r = np.asarray(r, dtype=float)
    out = np.zeros_like(r)
    m = r <= R
    out[m] = (R - r[m]) ** 3
    return out


# name: (dim, m_k * R**q as printed in source, shape(r,R), source line, q)
KERNELS = {
    "Cubic 3D":       (3, 8.0 / math.pi,          lambda r, R: cubic_poly(r / R),   "SPHKernels.h:31",  3),
    "Wendland C2 3D": (3, 21.0 / (2.0 * math.pi), lambda r, R: wendland_poly(r / R), "SPHKernels.h:290", 3),
    "Spiky 3D":       (3, 15.0 / math.pi,         lambda r, R: spiky_poly(r, R),     "SPHKernels.h:209", 6),
    "Cubic 2D":       (2, 40.0 / (7.0 * math.pi), lambda r, R: cubic_poly(r / R),   "SPHKernels.h:479", 2),
    "Wendland C2 2D": (2, 7.0 / math.pi,          lambda r, R: wendland_poly(r / R), "SPHKernels.h:562", 2),
}

# textbook sigma * h**q (Monaghan 1992 cubic spline; Wendland 1995 C2;
# spiky normalisation as used since Becker & Teschner 2007)
SIGMA_HDIM = {
    "Cubic 3D": 1.0 / math.pi,
    "Wendland C2 3D": 21.0 / (16.0 * math.pi),
    "Spiky 3D": 15.0 / (64.0 * math.pi),
    "Cubic 2D": 10.0 / (7.0 * math.pi),
    "Wendland C2 2D": 7.0 / (4.0 * math.pi),
}


def support_integral(name: str, n_shells: int, R: float = 1.0) -> float:
    """Composite midpoint rule for int_0^R W(r) * surface(r) dr."""
    dim, mk_Rdim, shape, _src, qpow = KERNELS[name]
    mk = mk_Rdim / R ** qpow
    r = (np.arange(n_shells) + 0.5) * (R / n_shells)
    w = shape(r, R)
    integrand = (w * 4.0 * math.pi * r ** 2) if dim == 3 else (w * 2.0 * math.pi * r)
    return float(np.sum(integrand) * (R / n_shells) * mk)


def kernel_w(name: str, r: np.ndarray, R: float = 1.0) -> np.ndarray:
    dim, mk_Rdim, shape, _src, qpow = KERNELS[name]
    return (mk_Rdim / R ** qpow) * shape(np.asarray(r, dtype=float), R)


def density_sum_3d(dx: float, R: float, offsets: tuple[float, float, float],
                   z_wall: float | None = None) -> float:
    """Dimensionless density summation rho/rho0 = sum_j V_j W(|x_i - x_j|).

    Lattice sites with spacing dx; particle volume V = dx**3.  A wall at
    z = 0 is emulated by dropping every site with z < 0 (semi-infinite
    support deficiency).  Returns the average over the tested offsets.
    """
    off = np.max(np.abs(np.asarray(offsets).reshape(-1, 3)), axis=0)
    n = int(math.ceil((R + float(np.max(off))) / dx)) + 1
    g = np.arange(-n, n + 1) * dx
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    if z_wall is not None:
        keep = Z >= 0.0
        X, Y, Z = X[keep], Y[keep], Z[keep]
    tot = []
    for ox, oy, oz in np.asarray(offsets, dtype=float).reshape(-1, 3) * dx:
        r = np.sqrt((X - ox) ** 2 + (Y - oy) ** 2 + (Z - oz) ** 2)
        tot.append(float(np.sum(kernel_w("Cubic 3D", r, R)) * dx ** 3))
    return float(np.mean(tot))


def order(e_coarse: float, e_fine: float, ratio: float) -> float:
    return math.log(e_coarse / e_fine) / math.log(ratio)


def main() -> int:
    # ---- 1. kernel constant check --------------------------------------
    shell_counts = [100, 1000, 10000, 100000]
    kc = {}
    for name in KERNELS:
        vals = [support_integral(name, n) for n in shell_counts]
        dim, mk_Rdim, _shape, src, qpow = KERNELS[name]
        kc[name] = {
            "dim": dim,
            "source": src,
            "m_k_power_of_R": qpow,
            "m_k_times_R_pow_q": mk_Rdim,
            "textbook_sigma_times_2_pow_q": SIGMA_HDIM[name] * 2.0 ** qpow,
            "integrals": vals,
            "max_abs_error": max(abs(v - 1.0) for v in vals),
            "constant_ratio_vs_textbook": mk_Rdim / (SIGMA_HDIM[name] * 2.0 ** qpow),
        }

    # ---- 2. lattice consistency of the density summation ----------------
    R = 1.0
    lattice_offsets = ((0.37, 0.61, 0.29), (0.11, 0.83, 0.52), (0.73, 0.19, 0.41))
    spacings = [R / k for k in (6, 8, 12, 16, 24, 32)]
    interior = [density_sum_3d(dx, R, lattice_offsets) for dx in spacings]
    wall = [density_sum_3d(dx, R, ((0.37, 0.61, 0.5),), z_wall=0.0) for dx in spacings]
    err_int = [abs(v - 1.0) for v in interior]
    lattice = {
        "smoothing": "regular cubic lattice, particle volume dx^3, R = 1, cubic spline kernel",
        "offsets": lattice_offsets,
        "dx_per_support_radius": [R / dx for dx in spacings],
        "interior_rho_over_rho0": interior,
        "interior_abs_error": err_int,
        "observed_orders_interior": [
            order(err_int[i], err_int[i + 1], spacings[i] / spacings[i + 1])
            for i in range(len(spacings) - 1)],
        "observed_orders_interior_note": "pairs whose both ends are above the "
            "float64 summation floor (~3e-10) are the meaningful ones",
        "wall_point_height": 0.5 * R,
        "wall_rho_over_rho0": wall,
        "wall_deficiency": [1.0 - v for v in wall],
        "note": "wall row = semi-infinite half space, i.e. the error a boundary "
                "model (Akinci2012 / Koschier2017 density maps / Bender2019 volume "
                "maps) has to compensate",
    }

    payload = {
        "campaign": "osssph-w2",
        "repo": "InteractiveComputerGraphics/SPlisHSPlasH",
        "commit_sha": SHA,
        "shell_counts": shell_counts,
        "kernel_constants": kc,
        "lattice_consistency": lattice,
        "all_constants_integrate_to_one": all(v["max_abs_error"] < 1e-9 for v in kc.values()),
        "all_constants_match_textbook_for_R_equals_2h":
            all(abs(v["constant_ratio_vs_textbook"] - 1.0) < 1e-12 for v in kc.values()),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({
        "kernel_max_abs_integral_error": {k: v["max_abs_error"] for k, v in kc.items()},
        "constant_ratio": {k: v["constant_ratio_vs_textbook"] for k, v in kc.items()},
        "interior_rho_over_rho0": interior,
        "interior_abs_error": err_int,
        "orders_interior": [round(o, 2) for o in lattice["observed_orders_interior"]],
        "wall_rho_over_rho0": wall,
    }, indent=1))

    # ---- Figure 1 -------------------------------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(10.5, 4.0))
    q = np.linspace(0, 1, 400)
    for name, ls in (("Cubic 3D", "-"), ("Wendland C2 3D", "--"), ("Spiky 3D", ":")):
        ax[0].plot(q, KERNELS[name][1] * KERNELS[name][2](q, 1.0), ls, label=name)
    ax[0].set_xlabel("q = r / R")
    ax[0].set_ylabel(r"$R^{q}W$  (dimensionless)")
    ax[0].set_title("(a) kernel profiles, constants as in SPHKernels.h")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)

    ax[1].loglog(spacings, err_int, "o-", label="interior $r = 0.5R$ lattice sum")
    ax[1].loglog(spacings, [1.0 - v for v in wall], "s--", label="0.5$R$ from a wall")
    ax[1].loglog(spacings, 3e-3 * (np.array(spacings) / spacings[0]) ** 4, "k:",
                 label=r"slope-4 reference")
    ax[1].set_xlabel("particle spacing dx (R = 1)")
    ax[1].set_ylabel(r"$|\rho/\rho_0 - 1|$")
    ax[1].set_title("(b) density-summation error vs resolution")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(FIG1, dpi=150)
    plt.close(fig)

    # ---- Figure 2: repository evidence composition ----------------------
    tree_path = HERE.parents[1] / "artifacts" / "oss-sph" / "splishsplash" / "tree.json"
    if not tree_path.exists():
        print(f"tree.json missing at {tree_path}; fig2 skipped")
        return 0
    tree = json.loads(tree_path.read_text())["data"]["tree"]
    paths = [t["path"] for t in tree if t["type"] == "blob"]
    groups = {
        "SPlisHSPlasH/ (solver core)": lambda p: p.startswith("SPlisHSPlasH/"),
        "extern/ (vendored deps)": lambda p: p.startswith("extern/"),
        "data/Scenes (.json)": lambda p: p.startswith("data/Scenes/") and p.endswith(".json"),
        "Simulator+Tools+GUI": lambda p: p.split("/")[0] in ("Simulator", "Tools", "GUI"),
        "pySPlisHSPlasH/": lambda p: p.startswith("pySPlisHSPlasH/"),
        "Tests/": lambda p: p.startswith("Tests/"),
        "doc/ + .github/ + Scripts/": lambda p: p.split("/")[0] in ("doc", ".github", "Scripts"),
    }
    counts = {k: sum(1 for p in paths if f(p)) for k, f in groups.items()}
    scenes = [p for p in paths if p.startswith("data/Scenes/") and p.endswith(".json")]
    cats = {
        "dam-break / free surface": r"Dam|Break|Wave",
        "surface tension (paper scenes)": r"SurfaceTension",
        "deformable / elastic coupling": r"Beam|Buckling|Coiling|Deformable|Elasticity",
        "boundary models / drag": r"GridModel|Obstacle|Drag",
        "other (emitter, script, sampling)": r"",
    }
    seen: set[str] = set()
    scene_counts: dict[str, int] = {}
    for k, pat in cats.items():
        hit = [s for s in scenes if s not in seen and (pat == "" or re.search(pat, s))]
        seen.update(hit)
        scene_counts[k] = len(hit)
    fig, ax = plt.subplots(1, 2, figsize=(11.0, 4.2))
    ks = list(counts)
    ax[0].barh(ks, [counts[k] for k in ks], color="#4c78a8")
    ax[0].set_xlabel("files at commit " + SHA[:7])
    ax[0].set_title(f"(a) repository composition ({len(paths)} blobs)")
    for i, k in enumerate(ks):
        ax[0].text(counts[k] + 3, i, str(counts[k]), va="center", fontsize=8)
    ax[0].invert_yaxis()
    ks2 = list(scene_counts)
    ax[1].barh(ks2, [scene_counts[k] for k in ks2], color="#f58518")
    ax[1].set_xlabel(f"scene files (n={len(scenes)})")
    ax[1].set_title("(b) shipped scenes by validation topic")
    for i, k in enumerate(ks2):
        ax[1].text(scene_counts[k] + 0.3, i, str(scene_counts[k]), va="center", fontsize=8)
    ax[1].invert_yaxis()
    fig.tight_layout()
    fig.savefig(FIG2, dpi=150)
    plt.close(fig)
    (HERE / "splishsplash_repo_composition.json").write_text(
        json.dumps({"n_blobs": len(paths), "groups": counts,
                    "n_scenes": len(scenes), "scene_categories": scene_counts}, indent=1) + "\n")
    print(f"wrote {OUT_JSON.name}, {FIG1.name}, {FIG2.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())