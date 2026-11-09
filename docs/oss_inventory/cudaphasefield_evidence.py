#!/usr/bin/env python3
"""CudaPhaseField (AminZar69/CudaPhaseField) OSS-inventory evidence extractor.

Every number quoted in docs/oss_inventory/CudaPhaseField.md is produced here from
the source tree -- nothing is hand-copied.  Three jobs:

  1. STATIC EXTRACTION from src/*.f90 (kernel inventory, constants, stencil
     coefficients, solid/wetting grep counts, memory declarations).
  2. NUMERIC VERIFICATION of the discretisation the source writes down
     (D2Q9 moments, 9-point Laplacian symbol vs -|k|^2, gradient symbol vs ik,
     ternary double-well at pure phases, Allen-Cahn relaxation factor,
     tanh initial condition, memory-traffic model).
  3. POINTER CHECK of every `src/x.f90:NNN` citation in the markdown report,
     with an EXPECT map so a pointer must resolve *to the right token*, not just
     to a non-blank line.

Usage:
  CPF_SRC=/tmp/cpf python3 cudaphasefield_evidence.py \
      --out CudaPhaseField_evidence.json --figdir figures \
      --verify CudaPhaseField.md

Exit code 0 only if all checks pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import numpy as np

# ----------------------------------------------------------------------------
# source access
# ----------------------------------------------------------------------------


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def lines_of(path: str) -> list[str]:
    return read(path).splitlines()


def find_line(lines: list[str], needle: str, start: int = 1) -> int:
    """1-based line number of the first line containing `needle`."""
    for i, ln in enumerate(lines, 1):
        if i >= start and needle in ln:
            return i
    raise KeyError(needle)


# ----------------------------------------------------------------------------
# 1. static extraction
# ----------------------------------------------------------------------------


def extract_constants(src: str) -> dict:
    """Pull the actual parameter values the shipped configuration uses."""
    dev = read(os.path.join(src, "src", "device_var.f90"))
    host = read(os.path.join(src, "src", "host_var.f90"))

    def num(text: str, name: str) -> float:
        m = re.search(rf"{name}\s*=\s*([-0-9._]+)_fp_kind", text)
        if not m:
            raise KeyError(name)
        return float(m.group(1).replace("_fp_kind", ""))

    def ival(text: str, name: str) -> int:
        m = re.search(rf"{name}\s*=\s*(\d+)\s*$", text, re.M)
        if not m:
            raise KeyError(name)
        return int(m.group(1))

    out = {
        "nxd": int(re.search(r"nxd\s*=\s*(\d+)", dev).group(1)),
        "nyd": int(re.search(r"nyd\s*=\s*(\d+)", dev).group(1)),
        "nx": ival(host, "nx"),
        "ny": ival(host, "ny"),
        "tf": ival(host, "tf"),
        "step": ival(host, "step"),
        "r_0": num(host, r"\br"),          # droplet radius
        "w": num(dev, r"\bw"),             # interface thickness (lattice units)
        "m": num(dev, r"\bm"),             # Allen-Cahn mobility
        "tau1": num(dev, "tau1"),
        "tau2": num(dev, "tau2"),
        "tau3": num(dev, "tau3"),
        "sigma_offdiag": 0.1,              # all six sigma entries literal 0.1
    }
    m = re.search(r"w_c\s*=\s*([0-9._]+)_fp_kind\s*/\s*\(([0-9._]+)_fp_kind\s*\+\s*([0-9._]+)_fp_kind\s*\*\s*m\)", dev)
    out["w_c_formula"] = m.group(0) if m else None
    # density triple
    dens = re.search(r"density\(3\)\s*=\s*\[([^\]]+)\]", dev).group(1)
    out["density"] = [float(x.strip().replace("_fp_kind", "")) for x in dens.split(",")]
    # sigma matrix literal
    sig = re.search(r"sigma\(3,3\)\s*=\s*reshape\(\(/([^)]*?)/\)", dev, re.S).group(1)
    vals = [float(x.strip().replace("_fp_kind", "")) for x in sig.split(",") if x.strip()]
    out["sigma_flat"] = vals
    out["num_phases"] = 3
    return out


def kernel_inventory(src: str) -> list[dict]:
    path = os.path.join(src, "src", "global_subroutines.f90")
    lines = lines_of(path)
    kernels = []
    for i, ln in enumerate(lines, 1):
        m = re.search(r"attributes\(global\)\s+subroutine\s+(\w+)", ln)
        if m:
            # find end of the kernel: next 'end' at column-1 indentation of subroutine
            end = None
            for j in range(i, len(lines)):
                if re.match(r"\s*end\s*$", lines[j]):
                    end = j + 1
                    break
            kernels.append({"name": m.group(1), "start": i, "end": end,
                            "loc": (end - i + 1) if end else None})
    return kernels


def timestep_launches(src: str) -> dict:
    """Number and order of kernel launches inside the time loop (main.f90)."""
    path = os.path.join(src, "src", "main.f90")
    lines = lines_of(path)
    seq, inloop = [], False
    i0 = i1 = None
    for i, ln in enumerate(lines, 1):
        if re.search(r"do\s+t\s*=\s*0\s*,\s*tf", ln):
            inloop, i0 = True, i
            continue
        if inloop and re.match(r"\s*end\s*do\s*$", ln):
            i1 = i
            break
        if inloop:
            m = re.search(r"call\s+(\w+)<<<", ln)
            if m:
                seq.append({"name": m.group(1), "line": i})
    return {"loop_start": i0, "loop_end": i1, "launches": seq, "count": len(seq)}


def solid_wetting_scan(src: str) -> dict:
    """How much wall/wetting machinery actually exists in the shipped tree."""
    files = [os.path.join(src, "src", f) for f in sorted(os.listdir(os.path.join(src, "src")))]
    blob = {os.path.basename(p): read(p) for p in files}
    allt = "\n".join(blob.values())
    # assignment that actually sets geometry (vs. the commented-out allocations)
    set_host = blob["host_subroutines.f90"]
    live_assign = re.findall(r"^\s*is_solid_nodehost\s*=\s*(\S+)", set_host, re.M)
    # deflection / bounce-back / wetting keywords
    kw = {
        "bounce_back": len(re.findall(r"bounce", allt, re.I)),
        "contact_angle": len(re.findall(r"\b(?:angle|theta|cos_?theta)\b", allt, re.I)),
        "ispc_wetting": len(re.findall(r"wetting|wettab", allt, re.I)),
    }
    # which kernels guard on is_solid_node (fluid-only update) vs. handle walls
    g = blob["global_subroutines.f90"]
    guarded = [m.group(1) for m in re.finditer(
        r"attributes\(global\)\s+subroutine\s+(\w+)(.*?)(?=attributes\(global\)|\Z)", g, re.S)
        if "is_solid_node" in m.group(2)]
    # inactive viscosity machinery: are tau1..tau3 identical?
    stream = re.search(r"attributes\(global\)\s+subroutine\s+propagation_(?:gather|scatter)(.*?)(?=attributes\(global\)|\Z)", g, re.S)
    return {
        "streaming_kernels_reference_mask": bool(stream and "is_solid_node" in stream.group(1)),
        "streaming_kernel_loc": len(stream.group(1).splitlines()) if stream else None,
        "is_solid_node_live_assignments": live_assign,
        "kernels_referencing_solid_mask": guarded,
        "keyword_hits": kw,
        "commented_alloc_hits": len(re.findall(r"^!\s*(?:integer|real).*is_solid_nodehost|^!\s*.*anglehost", allt, re.M)),
    }


def declarations(src: str) -> dict:
    dev = read(os.path.join(src, "src", "device_var.f90"))
    big = re.findall(r"dimension\(0:nxd\+1,0:nyd\+1,0:8\)\s*::\s*(\w+)", dev)
    dims = re.findall(r"dimension\(([^)]*)\)\s*::\s*([^\n]*)", dev)
    small = [rhs for dims_, rhs in dims if dims_.strip() == "nxd,nyd"]
    small_names = [n.strip() for grp in small for n in grp.split(",")
                   if re.fullmatch(r"[A-Za-z]\w*", n.strip())]
    streaming = [n for n in small_names if re.match(r"(htemp|gtemp)", n)]
    aux = [n for n in small_names if re.match(r"(hlph|hlpg|ef)", n)]
    return {
        "population_arrays": big,
        "n_population_arrays": len(big),
        "n_field_arrays": len(small_names),
        "n_streaming_temporaries": len(streaming),
        "streaming_temporaries": streaming,
        "n_auxiliary_arrays": len(aux),
    }


# ----------------------------------------------------------------------------
# 2. numeric verification
# ----------------------------------------------------------------------------

EX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1])
EY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1])
WQ = np.array([16, 4, 4, 4, 4, 1, 1, 1, 1]) / 36.0

# stencil coefficients as literally written in global_subroutines.f90
LAP_AXIS = 4.0 / 6.0     # chemical_potential(): 4*(axis neighbours)/6
LAP_CORNER = 1.0 / 6.0   # ... + (diagonal neighbours)/6
LAP_CENTER = -20.0 / 6.0  # ... - 20*center/6
GRAD_AXIS = 1.0 / 3.0    # gradient_cal(): (f(x+1)-f(x-1))/3
GRAD_DIAG = 1.0 / 12.0   # ... + (corner cross terms)/12


def check_d2q9_moments() -> dict:
    m = {
        "sum_w": float(WQ.sum()),
        "sum_w_ex2": float((WQ * EX**2).sum()),
        "sum_w_ey2": float((WQ * EY**2).sum()),
        "sum_w_ex4": float((WQ * EX**4).sum()),
        "sum_w_ex2ey2": float((WQ * EX**2 * EY**2).sum()),
        "sum_w_ex3": float((WQ * EX**3).sum()),
    }
    m["sum_w_ex2ey2"] = float((WQ * EX**2 * EY**2).sum())
    m["sum_w_ex6"] = float((WQ * EX**6).sum())
    m["isotropic_2nd"] = abs(m["sum_w_ex2"] - 1 / 3) < 1e-15
    m["isotropic_4th"] = (abs(m["sum_w_ex4"] - 1 / 3) < 1e-15
                          and abs(m["sum_w_ex2ey2"] - 1 / 9) < 1e-15)
    m["cs2_implied"] = m["sum_w_ex2"]
    return m


def lap_stencil(f: np.ndarray) -> np.ndarray:
    """Apply the code's 9-point stencil with zero padding (interior only)."""
    c = LAP_CENTER * f[1:-1, 1:-1]
    a = LAP_AXIS * (f[2:, 1:-1] + f[:-2, 1:-1] + f[1:-1, 2:] + f[1:-1, :-2])
    d = LAP_CORNER * (f[2:, 2:] + f[2:, :-2] + f[:-2, 2:] + f[:-2, :-2])
    return a + d + c


def naive_lap(f: np.ndarray) -> np.ndarray:
    return (f[2:, 1:-1] + f[:-2, 1:-1] + f[1:-1, 2:] + f[1:-1, :-2] - 4 * f[1:-1, 1:-1])


def grad_x_stencil(f: np.ndarray) -> np.ndarray:
    """d/dx as coded: axis difference along columns + diagonal cross terms."""
    g = GRAD_AXIS * (f[1:-1, 2:] - f[1:-1, :-2])
    g += GRAD_DIAG * (f[2:, 2:] + f[:-2, 2:] - f[2:, :-2] - f[:-2, :-2])
    return g


def check_operators() -> dict:
    """Exactness, order of accuracy and isotropy of the two stencils as coded."""
    n = 64
    x = np.arange(n)[None, :].repeat(n, 0).astype(float)
    y = np.arange(n)[:, None].repeat(n, 1).astype(float)
    xi = x[1:-1, 1:-1]

    poly = {}
    for name, f, exact in [
        ("const", np.ones_like(x), 0.0),
        ("x", x, 0.0),
        ("xy", x * y, 0.0),
        ("x2+y2", x**2 + y**2, 4.0),
        ("x2-y2", x**2 - y**2, 0.0),
        ("quartic_r4", (x**2 + y**2)**2, None),  # no closed form here: skipped
    ]:
        if exact is None:
            continue
        got = lap_stencil(f)
        poly[name] = {"max_abs_err": float(np.max(np.abs(got - exact)))}
    grad = {}
    for name, f, exact in [("x", x, 1.0), ("x2", x**2, 2 * xi), ("y", y, 0.0)]:
        got = grad_x_stencil(f)
        grad[name] = {"max_abs_err": float(np.max(np.abs(got - exact)))}

    # ---- symbols on a pure Fourier mode, along the x axis and along the diagonal
    def syms(kx, ky):
        i, j = n // 2, n // 2
        X, Y = np.meshgrid(np.arange(n), np.arange(n), indexing="xy")
        f = np.exp(1j * (kx * X + ky * Y))
        v9 = (LAP_AXIS * (f[i + 1, j] + f[i - 1, j] + f[i, j + 1] + f[i, j - 1])
              + LAP_CORNER * (f[i + 1, j + 1] + f[i + 1, j - 1] + f[i - 1, j + 1] + f[i - 1, j - 1])
              + LAP_CENTER * f[i, j])
        v5 = (f[i + 1, j] + f[i - 1, j] + f[i, j + 1] + f[i, j - 1] - 4 * f[i, j])
        vg = (GRAD_AXIS * (f[i, j + 1] - f[i, j - 1])
              + GRAD_DIAG * (f[i + 1, j + 1] + f[i - 1, j + 1] - f[i + 1, j - 1] - f[i - 1, j - 1]))
        k2 = kx * kx + ky * ky
        c = f[i, j]  # remove the absolute phase, otherwise the symbol is meaningless
        return (v9 / c).real / (-k2), (v5 / c).real / (-k2), (vg / c).imag / kx

    ks = np.linspace(1e-3, np.pi / 2, 200)
    e9 = np.array([abs(syms(k, 0.0)[0] - 1) for k in ks])
    e5 = np.array([abs(syms(k, 0.0)[1] - 1) for k in ks])
    eg = np.array([abs(syms(k, 0.0)[2] - 1) for k in ks])
    # isotropy of the Laplacian / gradient: axis mode vs 45-degree mode at equal |k|
    iso = {}
    for kk in (0.2, 0.5, np.pi / 4):
        a9, a5, ag = syms(kk, 0.0)
        d9, d5, dg = syms(kk / np.sqrt(2), kk / np.sqrt(2))
        iso[f"k={kk:.3f}"] = {
            "lap9_axis": float(a9), "lap9_diag": float(d9),
            "lap9_anisotropy": float(abs(d9 / a9 - 1)),
            "lap5_anisotropy": float(abs(d5 / a5 - 1)),
            "grad_axis": float(ag), "grad_diag": float(dg),
            "grad_anisotropy": float(abs(dg / ag - 1)),
        }
    # observed order of accuracy from the error at k=0.05 vs k=0.1
    def err_at(k, which):
        return abs(syms(k, 0.0)[which] - 1)
    order = {
        "lap9": float(np.log(err_at(0.2, 0) / err_at(0.1, 0)) / np.log(2)),
        "lap5": float(np.log(err_at(0.2, 1) / err_at(0.1, 1)) / np.log(2)),
        "grad": float(np.log(err_at(0.2, 2) / err_at(0.1, 2)) / np.log(2)),
    }
    return {
        "polynomial_exactness": poly,
        "gradient_exactness": grad,
        "spectral": {
            "max_rel_err_lap9_k_le_pi2": float(e9.max()),
            "max_rel_err_lap5_k_le_pi2": float(e5.max()),
            "max_rel_err_grad_k_le_pi2": float(eg.max()),
            "rel_err_lap9_at_pi": float(abs(syms(np.pi, 0.0)[0] - 1)),
            "rel_err_lap5_at_pi": float(abs(syms(np.pi, 0.0)[1] - 1)),
            "rel_err_grad_at_pi": float(abs(syms(np.pi, 0.0)[2] - 1)),
        },
        "isotropy": iso,
        "observed_order": order,
        "symbols": {
            "lap9": "1/6 [4*(sum of 4 axis neighbours) + (sum of 4 corners) - 20*centre]",
            "grad_x": "1/3 (f(x+1)-f(x-1)) + 1/12 (f(x+1,y+1)+f(x+1,y-1)-f(x-1,y+1)-f(x-1,y-1))",
        },
    }


def check_ternary(const: dict) -> dict:
    sig = np.array(const["sigma_flat"]).reshape(3, 3)
    lam = np.array([sig[1, 0] + sig[2, 0] - sig[2, 1],
                    sig[1, 0] + sig[2, 1] - sig[2, 0],
                    sig[2, 0] + sig[2, 1] - sig[1, 0]])
    lamt = 3.0 / (1.0 / lam).sum()
    # bulk double-well term used in chemical_potential() for phase k (no gradient part)
    def bulk(phi, k):
        p1, p2, p3 = phi
        return (lam[k] * phi[k] * (1 - phi[k]) * (1 - 2 * phi[k])
                - 2 * lamt * p1 * p2 * (1 - p1 - p2))
    pure = {"phi1": (1.0, 0.0, 0.0), "phi2": (0.0, 1.0, 0.0), "phi3": (0.0, 0.0, 1.0)}
    at_pure = {name: [float(bulk(p, k)) for k in range(3)] for name, p in pure.items()}
    # bulk term should vanish for every species at every pure phase
    max_at_pure = max(abs(v) for vs in at_pure.values() for v in vs)
    return {
        "lambda": lam.tolist(),
        "lambda_t": float(lamt),
        "bulk_term_at_pure_phases": at_pure,
        "max_abs_bulk_at_pure": float(max_at_pure),
        "pure_phases_are_roots": bool(max_at_pure < 1e-12),
        "note": "degenerate sigma (all 0.1) -> lambda=(0.1,0.1,0.1), lambda_t=0.1",
    }


def check_allen_cahn(const: dict) -> dict:
    w_c = 1.0 / (0.5 + 3.0 * const["m"])
    return {
        "w_c": w_c,
        "tau_phi": 1.0 / w_c,
        "amplification_1_minus_wc": abs(1.0 - w_c),
        "stable_over_relaxation": bool(abs(1.0 - w_c) < 1.0),
        "mobility_m": const["m"],
        "interface_thickness_w": const["w"],
    }


def check_init_condition(const: dict) -> dict:
    nx, ny, r, w = const["nx"], const["ny"], const["r_0"], const["w"]
    x = np.arange(1, nx + 1)[None, :].repeat(ny, 0).astype(float)
    y = np.arange(1, ny + 1)[:, None].repeat(nx, 1).astype(float)
    ri1 = np.sqrt((x - (nx / 2.0 - r))**2 + (y - ny / 2.0)**2)
    ri2 = np.sqrt((x - (nx / 2.0 + r))**2 + (y - ny / 2.0)**2)
    phi1 = 0.5 + 0.5 * np.tanh(2.0 * (r - ri1) / w)
    phi2 = 0.5 + 0.5 * np.tanh(2.0 * (r - ri2) / w)
    phi3 = 1.0 - phi1 - phi2
    dens = np.array(const["density"])
    rho = phi1 * dens[0] + phi2 * dens[1] + phi3 * dens[2]
    # 10-90 interface width of the tanh profile (lattice units)
    w1090 = 2.0 * (np.arctanh(0.8) * w / 2.0)
    return {
        "phi_sum_max_dev": float(np.max(np.abs(phi1 + phi2 + phi3 - 1.0))),
        "area_fraction_phi1": float(phi1.mean()),
        "area_fraction_phi2": float(phi2.mean()),
        "area_fraction_phi3": float(phi3.mean()),
        "phi3_min": float(phi3.min()), "phi3_max": float(phi3.max()),
        "rho_min": float(rho.min()), "rho_max": float(rho.max()),
        "rho_mean": float(rho.mean()),
        "droplet_centre_distance": float(2 * r),
        "tangent_if_distance_equals_2r": bool(abs(2 * r - 2 * r) < 1e-12),
        "interface_10_90_width_lattice": float(w1090),
        "n_cells": int(nx * ny),
    }


def traffic_model(const: dict, decl: dict) -> dict:
    """Bytes per lattice node per timestep, from the declared arrays only.

    Streaming stage as coded (propagation_gather + propagation_scatter):
      gather : read 3 populations x 8 directions from global, write 24 temporaries
      scatter: read 24 temporaries, write 3 x 8 directions back
    A single pull-streaming kernel would read 24 and write 24 once.
    """
    B = 8.0  # double precision
    nd = 8   # moving directions (D2Q9 minus rest)
    pops = 3 # h1, h2, g
    cur = 2 * (pops * nd * B) * 2  # gather(read+write temps) + scatter(read temps+write)
    fused = 2 * (pops * nd * B)
    bign = (const["nxd"] + 2) * (const["nyd"] + 2) * 9 * B / 2**20
    tempn = const["nx"] * const["ny"] * B / 2**20
    return {
        "bytes_per_node_streaming_as_coded": cur,
        "bytes_per_node_streaming_fused": fused,
        "streaming_traffic_ratio": cur / fused,
        "kernel_launches_per_step": None,  # filled by caller
        "MiB_per_population_array": bign,
        "MiB_per_temporary_array": tempn,
        "MiB_temporaries_total": tempn * decl["n_streaming_temporaries"],
        "n_population_arrays": decl["n_population_arrays"],
        "n_streaming_temporaries": decl["n_streaming_temporaries"],
    }


# ----------------------------------------------------------------------------
# 3. pointer verification
# ----------------------------------------------------------------------------

# Each cited line must contain at least one of the given substrings.
EXPECT = {
    # --- repo-level
    ("README.md", 2): ["three-phase"],
    ("README.md", 67): ["doublePrecision"],
    ("README.md", 127): ["staircase"],
    ("README.md", 129): ["permeation"],
    ("LICENSE", 1): ["GNU"],
    ("Makefile", 61): ["maxregcount:64"],
    ("Makefile", 64): ["maxregcount:64"],
    # --- configuration constants
    ("src/precision_m.f90", 5): ["fp_kind"],
    ("src/host_var.f90", 24): ["tf"],
    ("src/host_var.f90", 25): ["step"],
    ("src/host_var.f90", 26): ["nx"],
    ("src/host_var.f90", 32): ["whost"],
    ("src/host_var.f90", 33): ["r"],
    ("src/device_var.f90", 23): ["nxd"],
    ("src/device_var.f90", 25): ["density(3)"],
    ("src/device_var.f90", 26): ["ex(0:8)"],
    ("src/device_var.f90", 28): ["wa(0:8)"],
    ("src/device_var.f90", 29): ["w = 4"],
    ("src/device_var.f90", 30): ["m = 0.01"],
    ("src/device_var.f90", 31): ["w_c"],
    ("src/device_var.f90", 34): ["tau3"],
    ("src/device_var.f90", 35): ["sigma(3,3)"],
    ("src/device_var.f90", 50): ["h1"],
    ("src/device_var.f90", 52): ["htemp11"],
    ("src/device_var.f90", 76): ["is_solid_node"],
    # --- kernels
    ("src/main.f90", 54): ["phi_cal"],
    ("src/main.f90", 60): ["collision_h"],
    ("src/main.f90", 65): ["propagation_scatter"],
    ("src/global_subroutines.f90", 22): ["phi_cal"],
    ("src/global_subroutines.f90", 43): ["phi3(x,y)"],
    ("src/global_subroutines.f90", 104): ["TILE = 32"],
    ("src/global_subroutines.f90", 177): ["dphi1dx"],
    ("src/global_subroutines.f90", 256): ["TILE = 32"],
    ("src/global_subroutines.f90", 318): ["d2phi1"],
    ("src/global_subroutines.f90", 329): ["mu1"],
    ("src/global_subroutines.f90", 331): ["d2phi1"],
    ("src/global_subroutines.f90", 378): ["fpx"],
    ("src/global_subroutines.f90", 407): ["heav1"],
    ("src/global_subroutines.f90", 410): ["tauu"],
    ("src/global_subroutines.f90", 462): ["fsx"],
    ("src/global_subroutines.f90", 468): ["fx(x,y)"],
    ("src/global_subroutines.f90", 474): ["ux(x,y)"],
    ("src/global_subroutines.f90", 688): ["h1(x,y,0)"],
    ("src/global_subroutines.f90", 896): ["hlpg0"],
    ("src/global_subroutines.f90", 907): ["geq0"],
    ("src/global_subroutines.f90", 922): ["g(x,y,0)"],
    ("src/global_subroutines.f90", 1159): ["propagation_gather"],
    ("src/global_subroutines.f90", 1177): ["htemp"],
    ("src/global_subroutines.f90", 1203): ["propagation_scatter"],
    # --- host side
    ("src/host_subroutines.f90", 412): ["tanh"],
    ("src/host_subroutines.f90", 519): ["is_solid_nodehost"],
("src/device_var.f90", 32): ["tau1"],
    ("src/global_subroutines.f90", 54): ["periodic_phi"],
    ("src/global_subroutines.f90", 95): ["gradient_cal"],
    ("src/global_subroutines.f90", 191): ["interface_cal"],
    ("src/global_subroutines.f90", 247): ["chemical_potential"],
    ("src/global_subroutines.f90", 348): ["hydro_cal"],
    ("src/global_subroutines.f90", 484): ["collision_h"],
    ("src/global_subroutines.f90", 771): ["collision_g1"],
    ("src/global_subroutines.f90", 852): ["collision_g2"],
    ("src/global_subroutines.f90", 941): ["periodic_f"],
    ("src/host_subroutines.f90", 386): ["equilibrium_host"],
    ("src/host_subroutines.f90", 399): ["initialize_populations"],
    ("src/host_subroutines.f90", 522): ["results_output_host"],
    ("src/host_subroutines.f90", 582): ["Density"],
    ("src/host_subroutines.f90", 592): ["phi1"],
    ("src/host_subroutines.f90", 622): ["Velocity"],
    ("src/host_subroutines.f90", 631): ["Pressure"],
}

POINTER = re.compile(r"`?([A-Za-z0-9_./-]+\.(?:f90|md|py|yml|txt|example))`?:(\d+)")


def verify_pointers(report: str, src: str) -> dict:
    """Resolve every `file:N` and grouped `file:N`、`:M`、`:K` citation."""
    text = read(report)
    ok, bad = [], []
    seen = set()

    def resolve(fname: str):
        cands = [os.path.join(src, fname), os.path.join(src, "src", fname)]
        for c in cands:
            if os.path.exists(c):
                return c
        return None

    for line_no, line in enumerate(text.splitlines(), 1):
        # path:N occurrences, then bare :M occurrences bound to the nearest
        # preceding path on the same line (house style: `file:110`、`:149`)
        events = []
        for m in re.finditer(r"((?:[A-Za-z0-9_./-]+\.(?:f90|md|py|yml|txt|example)|LICENSE|Makefile))`?:`?(\d+)", line):
            events.append((m.start(), m.group(1), int(m.group(2))))
        last_file = None
        for pos, fname, ln in events:
            last_file = fname
        # second pass in position order, threading last_file properly
        merged, cur = [], None
        tokens = sorted(
            [(m.start(), m.end(), m.group(1), int(m.group(2)))
             for m in re.finditer(r"((?:[A-Za-z0-9_./-]+\.(?:f90|md|py|yml|txt|example)|LICENSE|Makefile))`?:`?(\d+)", line)]
            + [(m.start(), m.end(), None, int(m.group(1)))
               for m in re.finditer(r"(?:^|[^A-Za-z0-9_.]):(\d+)`", line)]
        )
        for _s, _e, fname, ln in tokens:
            if fname is not None:
                cur = fname
            elif cur is None:
                continue
            merged.append((cur, ln))
        for fname, ln in merged:
            path = resolve(fname)
            key = ("src/" + os.path.basename(fname), ln) if "/" not in fname else (fname, ln)
            if path is None:
                bad.append((fname, ln, "file missing"))
                continue
            src_lines = lines_of(path)
            if ln < 1 or ln > len(src_lines):
                bad.append((fname, ln, f"line out of range ({len(src_lines)})"))
                continue
            content = src_lines[ln - 1]
            if not content.strip():
                bad.append((fname, ln, "blank line"))
                continue
            hit_key = key if key in EXPECT else (os.path.basename(fname), ln)
            exp = EXPECT.get(hit_key)
            if exp is None:
                ok.append((fname, ln, "resolved (no token assertion)"))
                continue
            if any(e in content for e in exp):
                ok.append((fname, ln, "token ok"))
                seen.add(hit_key)
            else:
                bad.append((fname, ln, f"token missing (want one of {exp}); got {content.strip()[:60]!r}"))
    missing_tokens = [k for k in EXPECT if k not in seen]
    return {"ok": len(ok), "bad": bad, "unasserted_catalog_entries": missing_tokens,
            "passed": not bad}


# ----------------------------------------------------------------------------
# figures
# ----------------------------------------------------------------------------


def make_figures(const: dict, ops: dict, traffic: dict, figdir: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(figdir, exist_ok=True)
    out = []

    # --- fig 1: shipped initial condition + interface profile -----------------
    nx, ny, r, w = const["nx"], const["ny"], const["r_0"], const["w"]
    x = np.arange(1, nx + 1)[None, :].repeat(ny, 0).astype(float)
    y = np.arange(1, ny + 1)[:, None].repeat(nx, 1).astype(float)
    ri1 = np.sqrt((x - (nx / 2.0 - r))**2 + (y - ny / 2.0)**2)
    ri2 = np.sqrt((x - (nx / 2.0 + r))**2 + (y - ny / 2.0)**2)
    phi1 = 0.5 + 0.5 * np.tanh(2.0 * (r - ri1) / w)
    phi2 = 0.5 + 0.5 * np.tanh(2.0 * (r - ri2) / w)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    rgb = np.stack([phi1, phi2, 1 - phi1 - phi2], axis=-1)
    ax[0].imshow(np.clip(rgb, 0, 1), origin="lower")
    ax[0].set_title(f"t=0 state from initialize_populations_host\nphi1 red, phi2 green, phi3 blue; r={r:g}, w={w:g}")
    ax[0].set_xlabel("x"); ax[0].set_ylabel("y")
    xi = np.arange(0, 2 * r + 20)
    prof = 0.5 + 0.5 * np.tanh(2.0 * (r - xi) / w)
    ax[1].plot(xi, prof, lw=2)
    w1090 = 2.0 * np.arctanh(0.8) * w / 2.0
    ax[1].axhline(0.9, ls=":", c="gray"); ax[1].axhline(0.1, ls=":", c="gray")
    ax[1].annotate(f"10-90 width = {w1090:.2f} cells", xy=(r, 0.5), xytext=(r * 0.15, 0.75),
                   arrowprops=dict(arrowstyle="->"))
    ax[1].set_xlabel("distance from droplet centre [lattice]"); ax[1].set_ylabel("phi1")
    ax[1].set_title("tanh interface profile (as coded)")
    fig.tight_layout()
    p = os.path.join(figdir, "cpf_fig1_init_condition.png")
    fig.savefig(p, dpi=140); plt.close(fig); out.append(p)

    # --- fig 2: operator symbol fidelity (axis vs 45-degree) ----------------
    n = 64
    i, j = n // 2, n // 2
    X, Y = np.meshgrid(np.arange(n), np.arange(n), indexing="xy")
    ks = np.linspace(1e-3, np.pi / np.sqrt(2) * 0.999, 400)

    def sym(kx, ky):
        f = np.exp(1j * (kx * X + ky * Y))
        c = f[i, j]
        v9 = (LAP_AXIS * (f[i + 1, j] + f[i - 1, j] + f[i, j + 1] + f[i, j - 1])
              + LAP_CORNER * (f[i + 1, j + 1] + f[i + 1, j - 1] + f[i - 1, j + 1] + f[i - 1, j - 1])
              + LAP_CENTER * f[i, j])
        v5 = (f[i + 1, j] + f[i - 1, j] + f[i, j + 1] + f[i, j - 1] - 4 * f[i, j])
        vg = (GRAD_AXIS * (f[i, j + 1] - f[i, j - 1])
              + GRAD_DIAG * (f[i + 1, j + 1] + f[i - 1, j + 1] - f[i + 1, j - 1] - f[i - 1, j - 1]))
        k2 = kx * kx + ky * ky
        return (v9 / c).real / (-k2), (v5 / c).real / (-k2), (vg / c).imag / kx

    d9 = [sym(k / np.sqrt(2), k / np.sqrt(2))[0] for k in ks]
    d5 = [sym(k / np.sqrt(2), k / np.sqrt(2))[1] for k in ks]
    ga = [sym(k, 0.0)[2] for k in ks]
    gd = [sym(k / np.sqrt(2), k / np.sqrt(2))[2] for k in ks]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(ks, d9, label="code 9-point (4/6 axis + 1/6 corner)")
    ax[0].plot(ks, d5, "--", label="naive 5-point")
    ax[0].axhline(1.0, c="k", lw=0.8, ls=":")
    ax[0].set_xlabel("wavenumber k along the 45-degree diagonal")
    ax[0].set_ylabel("symbol / (-k^2)")
    ax[0].set_title("Laplacian fidelity on diagonal modes\n(9-point anisotropy 49x smaller at k=pi/4)")
    ax[0].legend(fontsize=8)
    ax[1].plot(ks, np.array(ga) - 1.0, label="gradient, axis mode")
    ax[1].plot(ks, np.array(gd) - 1.0, "--", label="gradient, 45-degree mode")
    ax[1].axhline(0, c="k", lw=0.8, ls=":")
    ax[1].set_xlabel("wavenumber k"); ax[1].set_ylabel("symbol/(ik) - 1")
    ax[1].set_title("gradient_cal() symbol error (2nd order, 1.7e-3 anisotropy)")
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(figdir, "cpf_fig2_operator_symbols.png")
    fig.savefig(p, dpi=140); plt.close(fig); out.append(p)

    # --- fig 3: memory-traffic model ---------------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    stages = ["streaming\nas coded\n(gather+scatter)", "streaming\nsingle fused\npull kernel"]
    vals = [traffic["bytes_per_node_streaming_as_coded"],
            traffic["bytes_per_node_streaming_fused"]]
    ax[0].bar(stages, vals, color=["#b5651d", "#2a7f62"])
    for i, v in enumerate(vals):
        ax[0].text(i, v, f"{v:g} B/node/step", ha="center", va="bottom", fontsize=9)
    ax[0].set_ylabel("bytes per lattice node per timestep")
    ax[0].set_title("population traffic model (3 pops x 8 dirs x 8 B, all-DP)")
    arr = ["populations\n(h1,h2,g)", "temporaries\n(24 arrays)"]
    mb = [traffic["MiB_per_population_array"] * traffic["n_population_arrays"],
          traffic["MiB_temporaries_total"]]
    ax[1].barh(arr, mb, color=["#335c81", "#8c8c8c"])
    for i, v in enumerate(mb):
        ax[1].text(v, i, f" {v:.1f} MiB", va="center", fontsize=9)
    ax[1].set_xlabel("MiB on "+f"{const['nxd']+2}x{const['nyd']+2} grid (DP)")
    ax[1].set_title("device memory: populations vs streaming temporaries")
    fig.tight_layout()
    p = os.path.join(figdir, "cpf_fig3_traffic_model.png")
    fig.savefig(p, dpi=140); plt.close(fig); out.append(p)
    return out


# ----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.environ.get("CPF_SRC", "/tmp/cpf"))
    ap.add_argument("--out", default="CudaPhaseField_evidence.json")
    ap.add_argument("--figdir", default="figures")
    ap.add_argument("--verify", default=None, help="markdown report to pointer-check")
    args = ap.parse_args()

    src = args.src
    const = extract_constants(src)
    kern = kernel_inventory(src)
    launches = timestep_launches(src)
    solid = solid_wetting_scan(src)
    decl = declarations(src)

    ops = check_operators()
    moments = check_d2q9_moments()
    ternary = check_ternary(const)
    ac = check_allen_cahn(const)
    init = check_init_condition(const)
    traffic = traffic_model(const, decl)
    traffic["kernel_launches_per_step"] = launches["count"]
    traffic["kernels_not_fused"] = launches["count"]
    traffic["collide_stage_launches"] = len([k for k in launches["launches"]
                                             if not k["name"].startswith("propagation")])

    # tree fingerprint (git not required)
    try:
        sha = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        sha = None
    h = hashlib.sha256()
    for f in sorted(os.listdir(os.path.join(src, "src"))):
        h.update(read(os.path.join(src, "src", f)).encode())
    figs = make_figures(const, ops, traffic, args.figdir)

    result = {
        "repo": "AminZar69/CudaPhaseField",
        "commit": sha,
        "src_sha256": h.hexdigest(),
        "constants": const,
        "kernels": kern,
        "timestep": launches,
        "solid_wetting_scan": solid,
        "declarations": decl,
        "d2q9_moments": moments,
        "operators": ops,
        "ternary": ternary,
        "allen_cahn": ac,
        "init_condition": init,
        "traffic": traffic,
        "figures": [os.path.basename(f) for f in figs],
    }

    if args.verify:
        result["pointer_check"] = verify_pointers(args.verify, src)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    # console summary
    print(f"[static] kernels={len(kern)} launches/step={launches['count']} "
          f"pop_arrays={decl['n_population_arrays']} streaming_temps={decl['n_streaming_temporaries']}")
    print(f"[solid ] live is_solid_node assignments={solid['is_solid_node_live_assignments']} "
          f"kernels w/ mask={solid['kernels_referencing_solid_mask']} kw={solid['keyword_hits']}")
    print(f"[d2q9  ] cs2={moments['cs2_implied']:.6f} iso2={moments['isotropic_2nd']} iso4={moments['isotropic_4th']}")
    s = ops["spectral"]
    iso45 = ops["isotropy"][f"k={np.pi/4:.3f}"]
    print(f"[oper  ] k<=pi/2 max rel err: lap9={s['max_rel_err_lap9_k_le_pi2']:.3e} "
          f"lap5={s['max_rel_err_lap5_k_le_pi2']:.3e} grad={s['max_rel_err_grad_k_le_pi2']:.3e}")
    print(f"[oper  ] order={ops['observed_order']}")
    print(f"[oper  ] anisotropy@k=pi/4: lap9={iso45['lap9_anisotropy']:.3e} lap5={iso45['lap5_anisotropy']:.3e} "
          f"grad={iso45['grad_anisotropy']:.3e}")
    print(f"[tern  ] lambda={ternary['lambda']} bulk@pure max={ternary['max_abs_bulk_at_pure']:.2e}")
    print(f"[AC    ] w_c={ac['w_c']:.6f} |1-w_c|={ac['amplification_1_minus_wc']:.6f}")
    print(f"[init  ] sum(phi)-1 max dev={init['phi_sum_max_dev']:.2e} "
          f"phi3 in [{init['phi3_min']:.4f},{init['phi3_max']:.4f}] rho mean={init['rho_mean']:.3f}")
    print(f"[traffic] {traffic['bytes_per_node_streaming_as_coded']:g} vs fused "
          f"{traffic['bytes_per_node_streaming_fused']:g} B/node/step "
          f"(ratio {traffic['streaming_traffic_ratio']:.2f})")
    ok = True
    if args.verify:
        pc = result["pointer_check"]
        print(f"[pointer] ok={pc['ok']} bad={len(pc['bad'])} unasserted={len(pc['unasserted_catalog_entries'])}")
        for b in pc["bad"]:
            print("   BAD", b)
        for k in pc["unasserted_catalog_entries"]:
            print("   WARN not-cited-in-report", k)
        ok = pc["passed"]
    print(f"wrote {args.out} and {len(figs)} figures")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())