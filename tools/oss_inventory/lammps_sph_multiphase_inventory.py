#!/usr/bin/env python3
"""Deep dive for ``slitvinov/lammps-sph-multiphase`` (campaign ``osssph-w2``, tier 3).

This module owns everything that must be *computed* rather than typed:

* GitHub API access (pinned commit, metadata, blob download into a local cache);
* kernel parsing: the quintic-spline constants and the piecewise polynomials are
  extracted out of ``MULTIPHASE/sph_multiphase.h`` by regex, translated to
  Python and re-used for the numerical checks;
* independent re-computation of every relation the source text fully determines
  (kernel normalisation in 2D/3D, the analytic derivative table, the Tait EOS
  slope implied by ``B = c^2 rho0 / gamma``, the latent-heat energy bookkeeping
  of ``fix phase_change``);
* the evidence table: for each capability claim a regex is resolved against the
  pinned sources, so the ``path:line`` pointer and the quoted source line are
  *found*, never remembered;
* absence evidence for the negative claims (no GPU / OpenMP / delta-SPH / CI ...).

``lammps_sph_multiphase_report.py`` consumes the results and renders the report.
No LAMMPS build happens here, therefore no physical simulation is reproduced.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
REPO = "slitvinov/lammps-sph-multiphase"
PIN = "7d420ddc8bf4bcf46664e9d7dcf2943d7dbffaca"
CACHE = (Path(os.environ.get("OSS_INV_CACHE", "~/.cache/oss_inv")).expanduser()
         / REPO.replace("/", "_") / PIN)
ART = ROOT / "artifacts" / "oss-sph" / "lammps-sph-multiphase"
DOC = ROOT / "docs" / "oss_inventory" / "OSS-INV-lammps-sph-multiphase.md"
MACH = ROOT / "docs" / "oss_inventory" / "OSS-INV-lammps-sph-multiphase.machine.txt"
TEMPLATE = ROOT / "tools" / "oss_inventory" / "lammps_sph_multiphase_report_template.md"
KERNEL_H = "MULTIPHASE/sph_multiphase.h"


# --------------------------------------------------------------------------
# GitHub access
# --------------------------------------------------------------------------
def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok:
        return tok
    cred = Path("~/.git-credentials").expanduser()
    if cred.exists():
        m = re.search(r"https://([^:]+):([^@]+)@github\.com", cred.read_text())
        if m:
            return m.group(2)
    raise RuntimeError("no GitHub token available (set GITHUB_TOKEN)")


def gh_api(path: str, raw: bool = False):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "User-Agent": "tensorplatform-oss-inventory",
            "Authorization": "Bearer " + _token(),
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        },
    )
    data = urllib.request.urlopen(req, timeout=60).read()
    return data.decode("utf-8") if raw else json.loads(data)


def repo_meta() -> dict:
    d = gh_api(f"/repos/{REPO}")
    keep = ("full_name", "description", "language", "stargazers_count", "forks_count",
            "subscribers_count", "size", "created_at", "pushed_at", "default_branch",
            "archived", "open_issues_count", "topics")
    meta = {k: d.get(k) for k in keep}
    meta["license"] = (d.get("license") or {}).get("spdx_id")
    meta["license_name"] = (d.get("license") or {}).get("name")
    meta["pinned_commit"] = PIN
    meta["languages"] = gh_api(f"/repos/{REPO}/languages")
    meta["branches"] = [b["name"] for b in gh_api(f"/repos/{REPO}/branches")]
    meta["commits"] = [
        {"sha": c["sha"][:12], "date": c["commit"]["author"]["date"],
         "message": c["commit"]["message"].splitlines()[0]}
        for c in gh_api(f"/repos/{REPO}/commits?per_page=15")
    ]
    return meta


def source_tree() -> dict:
    """Download every code/input blob of the pinned commit into a local cache."""
    index_path = CACHE / "blob_index.json"
    if index_path.exists():
        return json.loads(index_path.read_text())
    tree = gh_api(f"/repos/{REPO}/git/trees/{PIN}?recursive=1")["tree"]
    blobs = [e for e in tree if e["type"] == "blob"
             and not e["path"].startswith("img/")
             and not e["path"].lower().endswith((".png", ".gif", ".mp4", ".svg"))
             and e.get("size", 0) <= 400_000]
    index = {}
    for e in blobs:
        dest = CACHE / e["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            gh_api(f"/repos/{REPO}/contents/{urllib.parse.quote(e['path'])}?ref={PIN}",
                   raw=True),
            encoding="utf-8")
        index[e["path"]] = e["sha"]
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    return index


def src(rel: str) -> str:
    return (CACHE / rel).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Kernel parsing: constants and piecewise polynomials come out of the C++
# --------------------------------------------------------------------------
_POW_RE = re.compile(r"pow\(([^,]+),\s*([^)]+)\)")


def cpp_expr_to_py(expr: str) -> str:
    return _POW_RE.sub(r"((\1)**(\2))", expr).replace("^", "**")


def _branches(fn_body: str) -> list[tuple[float, str]]:
    return [(float(lim), expr) for lim, expr in
            re.findall(r"if \(s < ([\d.]+)\) return (.+?);", fn_body)]


def parse_kernel(header_text: str) -> dict:
    """Extract normalisation constants plus the piecewise W and dW tables."""
    norm3 = float(re.search(r"norm3d = ([0-9.eE+-]+);", header_text).group(1))
    norm2 = float(re.search(r"norm2d = ([0-9.eE+-]+);", header_text).group(1))
    kbody = header_text.split("inline double kernel_poly")[1].split("inline double dkernel_poly")[0]
    dbody = header_text.split("inline double dkernel_poly")[1].split("inline double w(")[0]
    return {"norm3d": norm3, "norm2d": norm2,
            "w_branches": _branches(kbody), "dw_branches": _branches(dbody)}


def eval_branches(branches: list[tuple[float, str]], s):
    s = np.asarray(s, dtype=float)
    out = np.zeros_like(s)
    done = np.zeros_like(s, dtype=bool)
    for limit, expr in branches:
        mask = (~done) & (s < limit)
        out[mask] = eval(cpp_expr_to_py(expr), {"s": s[mask]})  # nosec: source-derived expr
        done |= mask
    return out


def kernel_w(spec: dict, dim: int, r, cut: float = 1.0):
    """``SPHMultiphase::w`` including the ``ih**dim`` scaling used by the pair styles."""
    ih = 1.0 / cut
    norm = spec["norm3d"] if dim == 3 else spec["norm2d"]
    return norm * eval_branches(spec["w_branches"], 3.0 * np.asarray(r) * ih) * ih ** dim


def kernel_dw(spec: dict, dim: int, r, cut: float = 1.0):
    """``SPHMultiphase::dw``: radial derivative with ``ih**(dim+1)`` scaling."""
    ih = 1.0 / cut
    norm = spec["norm3d"] if dim == 3 else spec["norm2d"]
    return 3.0 * norm * eval_branches(spec["dw_branches"], 3.0 * np.asarray(r) * ih) * ih ** (dim + 1)


# --------------------------------------------------------------------------
# Independent re-computation of every source-determined relation
# --------------------------------------------------------------------------
def verify_kernel_normalisation(spec: dict, cut: float = 1.0) -> dict:
    """``int W dV == 1`` (3D) and ``int W dA == 1`` (2D) for the parsed kernel."""
    from scipy.integrate import quad
    pts = [cut / 3.0, 2.0 * cut / 3.0]
    i3 = quad(lambda r: float(kernel_w(spec, 3, r, cut)) * 4.0 * np.pi * r * r,
              0.0, cut, points=pts, limit=400)[0]
    i2 = quad(lambda r: float(kernel_w(spec, 2, r, cut)) * 2.0 * np.pi * r,
              0.0, cut, points=pts, limit=400)[0]
    return {"int_W_dV_3d": i3, "rel_err_3d": abs(i3 - 1.0),
            "int_W_dA_2d": i2, "rel_err_2d": abs(i2 - 1.0),
            "cut_over_h": 3.0,
            "analytic_3d": 9.0 / (40.0 * np.pi),
            "analytic_2d": 63.0 / (478.0 * np.pi)}


def verify_derivative_table(spec: dict) -> dict:
    """The hand-coded ``dkernel_poly`` must equal d/ds of ``kernel_poly`` (sympy)."""
    import sympy as sp

    s = sp.Symbol("s")

    def pw(branches):
        expr = 0
        for limit, e in reversed(branches):
            expr = sp.Piecewise((sp.sympify(cpp_expr_to_py(e)), s < limit), (expr, True))
        return expr

    w_sym, dw_sym = pw(spec["w_branches"]), pw(spec["dw_branches"])
    resid = sp.simplify(dw_sym - sp.diff(w_sym, s))
    grid = np.concatenate([np.linspace(0.02, 0.98, 200), np.linspace(1.02, 1.98, 200),
                           np.linspace(2.02, 2.98, 200)])
    f_dw = sp.lambdify(s, dw_sym, "numpy")
    f_d = sp.lambdify(s, sp.diff(w_sym, s), "numpy")
    with np.errstate(all="ignore"):
        err = np.abs(np.asarray(f_dw(grid), float) - np.asarray(f_d(grid), float))
    return {"symbolic_residual": str(resid), "symbolic_zero": bool(resid == 0),
            "max_abs_pointwise_err": float(np.nanmax(err)), "n_sample_points": int(grid.size)}


def verify_eos_slope(soundspeed: float = 1.5, rho0: float = 0.01, gamma: float = 1.4,
                     rbackground: float = 0.0) -> dict:
    """``B_one = c^2 rho0 / gamma`` must give ``dp/drho|rho0 == c^2``.

    The slope is taken analytically (``dp/drho = B gamma rho^(gamma-1) / rho0^gamma``)
    and cross-checked with a central difference, so a float-cancellation artefact
    cannot masquerade as an error.
    """
    B = soundspeed ** 2 * rho0 / gamma                        # pair_sph_taitwater_multiphase.cpp:141
    p = lambda rho: B * ((rho / rho0) ** gamma - rbackground)  # sph_multiphase.h:47
    dp = lambda rho: B * gamma * rho ** (gamma - 1.0) / rho0 ** gamma
    slope_an = float(dp(rho0))
    h = 1e-6 * rho0
    slope_fd = (p(rho0 + h) - p(rho0 - h)) / (2 * h)
    return {"B": float(B), "dp_drho": slope_an, "dp_drho_fd": float(slope_fd),
            "c_squared": float(soundspeed ** 2),
            "rel_err": float(abs(slope_an - soundspeed ** 2) / soundspeed ** 2),
            "rel_err_fd_vs_analytic": float(abs(slope_fd - slope_an) / slope_an),
            "p_at_rho0": float(p(rho0)), "rho0_ref": rho0, "gamma": gamma}


def energy_identity_check(seed: int = 7) -> dict:
    """Check the ``fix phase_change`` energy bookkeeping (fix_phase_change.cpp:206).

    One evaporation event must drop the total energy by exactly the latent heat
    ``to_mass * Hwv``; donor neighbours keep their *specific* energy.
    """
    rng = np.random.default_rng(seed)
    mi, ei = float(rng.uniform(1, 2)), float(rng.uniform(1, 3))
    mj, ej, wj = rng.uniform(1, 2, 6), rng.uniform(1, 3, 6), rng.uniform(0.1, 1.0, 6)
    to_mass, Hwv = 0.3, 32.0
    dm = to_mass * wj / wj.sum()                                 # fix_phase_change.cpp:197
    denergy = float(np.sum(ej * dm))                             # fix_phase_change.cpp:199
    enew = (mi * ei + denergy - to_mass * Hwv) / (mi + to_mass)   # fix_phase_change.cpp:206
    before = mi * ei + float(np.sum(mj * ej))
    after = (mi + to_mass) * enew + float(np.sum((mj - dm) * ej))
    return {"delta_E": float(after - before), "expected_delta_E": -to_mass * Hwv,
            "rel_err": float(abs((after - before) + to_mass * Hwv) / (to_mass * Hwv))}


def neighbour_support(dx: float = 1.0, h: float = 1.0) -> dict:
    """Neighbour counts on a simple-cubic lattice; h == dx in this package."""
    r = 2 * h / dx
    n = int(np.ceil(r)) + 1
    g = np.stack(np.meshgrid(*[np.arange(-n, n + 1) * dx] * 3, indexing="ij"), -1).reshape(-1, 3)
    d = np.linalg.norm(g, axis=1)
    return {"dx": dx, "h": h,
            "n_within_2h": int(np.sum((d > 0) & (d <= 2 * h))),
            "n_within_cut3h": int(np.sum((d > 0) & (d < 3 * h)))}


# --------------------------------------------------------------------------
# Evidence pointers: claim -> path:line, resolved out of the pinned tree
# --------------------------------------------------------------------------
EVIDENCE_SPECS: list[tuple[str, str, str, str]] = [
    ("kernel_quintic", KERNEL_H, r"pow\(3 - s, 5\) - 6",
     "Quintic-spline kernel W(q), support q<3, three piecewise branches"),
    ("kernel_dtable", KERNEL_H, r"-50 \* pow\(s, 4\) \+ 120",
     "Analytic radial derivative table dW/dq for the quintic spline"),
    ("kernel_norm3d", KERNEL_H, r"norm3d = 0\.0716",
     "3D normalisation constant norm3d = 9/(40 pi)"),
    ("kernel_norm2d", KERNEL_H, r"norm2d = 0\.0419",
     "2D normalisation constant norm2d = 63/(478 pi)"),
    ("kernel_scaling", KERNEL_H, r"3\.0 \* \(dim == 3 \? norm3d",
     "dw() evaluates d/ds at s = 3r/cut, i.e. cut = 3h and smoothing length h = cut/3"),
    ("eos_tait", KERNEL_H, r"B \* \(pow\(rho / rho0, gamma\) - rbackground\)",
     "Tait-type EOS p = B[(rho/rho0)^gamma - rbackground] (weakly compressible)"),
    ("cg_storage", KERNEL_H, r"fix property/atom d2_cg 3 ghost yes",
     "Colour function kept as a 3-component atom property with ghost exchange"),
    ("rmass_requirement", KERNEL_H, r"SPH multiphase requires atom_style sph",
     "All multiphase pair styles require atom_style sph (rho / esph / rmass)"),
    ("pressure_force", "MULTIPHASE/pair_sph_taitwater_multiphase.cpp",
     r"fpair = -\(Vi2 \+ Vj2\) \* pij \* wfd",
     "Pressure-gradient force f = -(V_i^2+V_j^2) p_ij gradW (volume-symmetric)"),
    ("viscous_force", "MULTIPHASE/pair_sph_taitwater_multiphase.cpp",
     r"fvisc = \(Vi2 \+ Vj2\) \* viscosity",
     "Viscosity term (V_i^2+V_j^2) eta (v_i-v_j) dW/dr, Morris-1997 form (first derivative only)"),
    ("mixed_pressure", "MULTIPHASE/pair_sph_taitwater_multiphase.cpp",
     r"double pij = \(rho\[j\] \* pi \+ rho\[i\] \* pj\)",
     "Multiphase pressure average p_ij = (rho_j p_i + rho_i p_j)/(rho_i+rho_j)"),
    ("B_from_soundspeed", "MULTIPHASE/pair_sph_taitwater_multiphase.cpp",
     r"B_one = soundspeed_one \* soundspeed_one \* rho0_one / gamma_one",
     "B derived from the input sound speed so that dp/drho|rho0 = c^2"),
    ("rhosum_self", "MULTIPHASE/pair_sph_rhosum_multiphase.cpp",
     r"rho\[i\] = w\(dim, 0\.0\)",
     "Density by summation, initialised with the self term W(0)/h^dim"),
    ("rhosum_sum", "MULTIPHASE/pair_sph_rhosum_multiphase.cpp",
     r"rho\[i\] \+= w\(dim, sqrt\(rsq\) \* ih\)",
     "Density summation over kernel-weighted neighbours, then rho_i *= m_i"),
    ("cg_gradient", "MULTIPHASE/pair_sph_colorgradient.cpp",
     r"double dphi = -wfd \* alpha",
     "Colour-function gradient sum_j alpha_ij/(n_j^2) n_i gradW"),
    ("cg_comm", "MULTIPHASE/pair_sph_colorgradient.cpp",
     r"int PairSPHColorGradient::pack_forward_comm",
     "Colour-gradient ghosts refreshed every step through a custom forward comm"),
    ("st_stress", "MULTIPHASE/pair_sph_surfacetension.cpp",
     r"static void stress\(int dim",
     "Surface-tension stress as the projection of the colour gradient (CSF form, no curvature fit)"),
    ("st_force", "MULTIPHASE/pair_sph_surfacetension.cpp",
     r"\(si\[d\] \* Vi \* Vi \+ sj\[d\] \* Vj \* Vj\) \* wfd",
     "Pairwise surface-tension force from the colour-gradient stress pair"),
    ("hc_flux", "MULTIPHASE/pair_sph_heatconduction_phasechange.cpp",
     r"double deltaE = 2\.0 \* alpha",
     "Heat conduction 2 alpha (T_i-T_j) gradW/(rho_i rho_j), symmetric energy exchange"),
    ("hc_dirichlet", "MULTIPHASE/pair_sph_heatconduction_phasechange.cpp",
     r"if \(fixflag\[itype\]\[jtype\] == itype && Ti < Tj\) Ti = tc",
     "Optional one-sided Dirichlet temperature on a type pair (NULL keyword)"),
    ("pc_fix_mask", "MULTIPHASE/fix_phase_change.cpp", r"return PRE_EXCHANGE;",
     "Phase change runs at pre-exchange: insertion happens before migration/comm"),
    ("pc_threshold", "MULTIPHASE/fix_phase_change.cpp",
     r"double threshold = \(e\[i\] - t2energy\(Tc, cv\[i\]\)\)",
     "Energy-based evaporation probability (e_i - cv Tc)/Hwv * dt * rate"),
    ("pc_gate", "MULTIPHASE/fix_phase_change.cpp",
     r"random->uniform\(\) < threshold && isfromphasearound\(i\)",
     "Evaporation only at an interface: a from-type particle must sit inside the cut-off"),
    ("pc_kernel_weights", "MULTIPHASE/fix_phase_change.cpp",
     r"wj\[jj\] = w\(dim, sqrt\(delx \* delx",
     "Vapour mass is donated by liquid neighbours weighted by the kernel"),
    ("pc_mass_split", "MULTIPHASE/fix_phase_change.cpp",
     r"double dm = to_mass \* wj\[jj\] / wtotal",
     "Kernel-weighted mass split; neighbour mass removed through a reverse comm"),
    ("pc_energy_update", "MULTIPHASE/fix_phase_change.cpp",
     r"double enew = \(rmass\[i\] \* e\[i\] \+ denergy - to_mass \* Hwv\)",
     "Energy update subtracts the latent heat Hwv from the inserted mass"),
    ("pc_convert", "MULTIPHASE/fix_phase_change.cpp",
     r"if \(convert_flag && type\[i\] == from_type",
     "Drained liquid particle can be converted into a vapour particle (convert CV)"),
    ("pc_owns", "MULTIPHASE/fix_phase_change.cpp", r"bool FixPhaseChange::owns",
     "Insertion ownership test incl. periodic images of the box faces (MPI-safe insertion)"),
    ("pc_insert", "MULTIPHASE/fix_phase_change.cpp", r"bool FixPhaseChange::insert_one_atom",
     "New particles created locally; natoms / atom map updated in pre_exchange"),
    ("pc_restart", "MULTIPHASE/fix_phase_change.cpp",
     r"double list\[3\] = \{\(double\) random->state\(\)",
     "Restart-safe RNG state plus next reneighbour step written to the restart file"),
    ("pc_reverse_comm", "MULTIPHASE/fix_phase_change.cpp",
     r"int FixPhaseChange::pack_reverse_comm",
     "Donated mass accumulated across ranks with a reverse comm on atom->drho"),
    ("dt_viscous", "examples/bubble_on_wall/settimestep.lmp", r"variable dt_eta_d equal",
     "Viscous time-step limit dt = dx^2 rho/(8 eta)"),
    ("dt_thermal", "examples/bubble_on_wall/settimestep.lmp", r"variable dt_alpha equal 1/4\.0",
     "Thermal-diffusion limit dt = (1/4) sqrt(rho dx^3/(6 alpha))"),
    ("dt_acoustic", "examples/bubble_on_wall/settimestep.lmp", r"variable dt_c_d equal",
     "Acoustic CFL limit dt = (1/4) dx/c"),
    ("dt_min", "examples/bubble_on_wall/settimestep.lmp", r"shell \./min\.sh dt",
     "Three limits reduced to one dt by a shell min() and injected as a LAMMPS variable"),
    ("integrator", "examples/bubble_on_wall/bubble.lmp", r"fix +integrate_fix_full all sph",
     "Time integration delegated to the LAMMPS core fix sph (explicit)"),
    ("boundary_fixed_wall", "examples/bubble_on_wall/bubble.lmp", r"^boundary +p f p",
     "Walls via boundary f plus frozen wall particles"),
    ("wall_frozen", "examples/bubble_on_wall/bubble.lmp", r"setforce 0 0 0",
     "Wall particles frozen with fix setforce (frozen boundary particles, no mirror particles)"),
    ("wall_rho_dirichlet", "examples/bubble_on_wall/bubble.lmp",
     r"fix bm +all set 1 0 group boundary sph/rho",
     "Wall density and energy prescribed (Dirichlet) instead of being integrated"),
    ("full_neigh", "MULTIPHASE/pair_sph_colorgradient.cpp",
     r"neighbor->add_request\(this, NeighConst::REQ_FULL\)",
     "Full neighbour list requested for colour gradient / phase change (ghost access)"),
    ("val_poiseuille_analytic", "examples/poiseuille/poiseuille.awk",
     r"analytical solution for gravity driven Poiseulle flow",
     "Reverse-Poiseuille validation against the transient analytic series (Morris 1997)"),
    ("val_poiseuille_series", "examples/poiseuille/poiseuille.awk", r"return 4\*F\*L\^2",
     "Analytic series term used as the reference solution"),
    ("val_cylinder_ref", "examples/flow_around_cylinder/flow.gp", r"morris-fig6-1",
     "Cylinder at Re=0.03 compared with digitised Morris-1997 FEM profiles"),
    ("val_slab_analytic", "examples/slub/infslab.mac", r"eq_r: \(t-t_l\)/\(t_r-t_l\)",
     "Two-material slab conduction against the VanSant closed form"),
    ("val_slab_source", "examples/slub/infslab.mac", r"VanSant, James H\. Conduction heat transfer",
     "The analytic reference is generated by a CAS script, not typed by hand"),
    ("kernel_lib", "examples/scripts/sph-kernel.mac", r"norm\[3\]: 9/\(40\*%pi\)",
     "Symbolic kernel library with exact normalisations (quintic/bspline/Wendland/Lucy)"),
    ("kernel_lib_family", "examples/scripts/sph-kernel.mac", r"elseif ktype='wendland4 then",
     "Alternative kernels available symbolically (wendland4/6, lucy, cubic, bspline)"),
    ("params_table", "examples/bubble_on_wall/vars.lmp", r"variable          sph_rho_g equal",
     "Boiling-case parameters live in a plain input-script variable table"),
    ("two_atom_case", "examples/two_atoms/phase_change.mac", r"define_kernel\(ndim, cutoff",
     "Two-particle cases generated from the symbolic kernel library (semi-analytic checks)"),
]


def evidence_table() -> tuple[list[dict], dict]:
    """Resolve every claim to ``path:line``; fail loudly on a missing pattern."""
    rows, by_key = [], {}
    for key, path, pattern, claim in EVIDENCE_SPECS:
        rx = re.compile(pattern)
        hit = next(((i, ln) for i, ln in enumerate(src(path).splitlines(), 1) if rx.search(ln)),
                   None)
        if hit is None:
            raise RuntimeError(f"evidence pattern not found: {key} -> {path}:{pattern}")
        row = {"key": key, "claim": claim, "path": path, "line": hit[0],
               "pointer": f"{path}:{hit[0]}", "source_line": hit[1].strip()}
        rows.append(row)
        by_key[key] = row
    return rows, by_key


def absence_evidence() -> dict:
    """Negative claims carry the same proof burden as positive ones."""
    meta = json.loads((CACHE / "blob_index.json").read_text())
    code = {p: src(p) for p in meta if p.startswith("MULTIPHASE/")}
    probes = {
        "cuda_or_kokkos_or_gpu": r"CUDA|cuda|Kokkos|KOKKOS|gpu|GPU",
        "openmp": r"_OPENMP|#pragma omp",
        "delta_sph_density_diffusion": r"delta_sph|delta-sph|density diff|diffusion term",
        "isph_or_projection": r"Poisson|projection|ISPH",
        "in_kernel_adaptive_dt": r"fix dt/reset|dt_reset|CFL",
        "tensile_or_artificial_viscosity": r"artificial viscosity|tensile",
        "curvature_surface_tension": r"curvature|kappa",
        "wet_dry_scheme": r"wet ?dry|wetting",
        "overlap_check_on_insert": r"hmin|overlap|exclusion",
    }
    out = {}
    for name, pat in probes.items():
        rx = re.compile(pat)
        hits = [f"{k}:{i}" for k, v in code.items()
                for i, ln in enumerate(v.splitlines(), 1) if rx.search(ln)]
        out[name] = {"n_hits": len(hits), "hits": hits[:5]}
    out["_scope"] = {"files_scanned": sorted(code), "n_files": len(code)}
    out["ci_workflows"] = {"n_files": sum(1 for p in meta if p.startswith(".github/")),
                           "n_files_total": len(meta)}
    out["test_files"] = {"matches": sorted(p for p in meta
                                           if re.search(r"(^|/)(test|tests|spec)", p))}
    out["license_files"] = {"matches": sorted(p for p in meta
                                              if re.match(r"(?i)^(LICENSE|COPYING)", p))}
    return out


# --------------------------------------------------------------------------
# Figure
# --------------------------------------------------------------------------
def figure(spec: dict, ver: dict, eos: dict, out_png: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 3, figsize=(13.5, 3.9))
    q = np.linspace(0.001, 2.999, 1200)
    ax[0].plot(q, eval_branches(spec["w_branches"], q), lw=2, label=r"$f(q)$, $q=3r/r_{cut}$")
    ax[0].plot(q, eval_branches(spec["dw_branches"], q), lw=2, ls="--", label=r"$f'(q)$")
    for b in (1.0, 2.0, 3.0):
        ax[0].axvline(b, color="0.85", lw=0.8, zorder=0)
    ax[0].set_xlabel(r"$q = 3r/r_{cut}$")
    ax[0].set_ylabel("value")
    ax[0].set_title("quintic kernel parsed from sph_multiphase.h:15-39", fontsize=9)
    ax[0].legend(fontsize=8)

    labels = ["3D $\\int W dV$", "2D $\\int W dA$", "dW/dq table",
              "EOS $dp/d\\rho=c^2$", "$\\Delta E=-mH_{wv}$"]
    vals = [max(ver["kernel"]["rel_err_3d"], 1e-17), max(ver["kernel"]["rel_err_2d"], 1e-17),
            max(ver["derivative"]["max_abs_pointwise_err"], 1e-17),
            max(ver["eos"]["rel_err"], 1e-17), max(ver["energy"]["rel_err"], 1e-17)]
    ax[1].bar(range(len(vals)), vals, color="#3b6ea5")
    ax[1].set_yscale("log")
    ax[1].set_xticks(range(len(vals)))
    ax[1].set_xticklabels(labels, rotation=18, ha="right", fontsize=8)
    ax[1].axhline(1e-12, color="crimson", ls=":", label="1e-12 tolerance")
    ax[1].set_ylabel("relative error")
    ax[1].set_title("independent re-computation residuals", fontsize=9)
    ax[1].legend(fontsize=8)

    rho = np.linspace(0.0, 2.0, 400) * eos["rho0_ref"]
    ax[2].plot(rho / eos["rho0_ref"], eos["B"] * (rho / eos["rho0_ref"]) ** eos["gamma"],
               lw=2, label="p(rho), Tait")
    ax[2].plot(rho / eos["rho0_ref"], eos["dp_drho"] * (rho - eos["rho0_ref"]), "--", lw=1.5,
               label="tangent at rho0, slope c^2 = %.3g" % eos["c_squared"])
    ax[2].set_xlabel(r"$\rho/\rho_0$")
    ax[2].set_ylabel("p")
    ax[2].set_title("EOS slope check (pair .cpp:141 + .h:47)", fontsize=9)
    ax[2].legend(fontsize=8)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return {"png": str(out_png.relative_to(ROOT)),
            "panels": ["kernel f(q) and f'(q)", "identity residuals",
                       "Tait EOS and tangent at rho0"]}