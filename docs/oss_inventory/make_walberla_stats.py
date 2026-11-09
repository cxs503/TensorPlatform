#!/usr/bin/env python3
"""Programmatic statistics for the waLBerla OSS inventory (FEAT-OSS-INVENTORY-LBM / mission 8551).

All numbers quoted in REPORT_walberla.md that are countable are produced here and written to
walberla_stats.json -- nothing is hand-copied.  The script only *reads* a waLBerla checkout
(path given by argv[1]); it never writes into it.

Usage:  python3 make_walberla_stats.py /tmp/walberla_src [out.json]
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "/tmp/walberla_src")
OUT = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "walberla_stats.json")

CPP_EXT = (".h", ".hpp", ".cpp", ".cu", ".inl")
PY_EXT = (".py",)


def walk(rel_root, exts):
    out = []
    base = os.path.join(ROOT, rel_root)
    for dirpath, _dirnames, filenames in os.walk(base):
        for fn in filenames:
            if fn.endswith(exts):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def loc(path):
    with open(path, "r", errors="replace") as fh:
        return sum(1 for _ in fh)


def count(files):
    return {"n_files": len(files), "loc": sum(loc(f) for f in files)}


def sum_dicts(ds):
    return {"n_files": sum(d["n_files"] for d in ds), "loc": sum(d["loc"] for d in ds)}


def grep(pattern, rel_roots, exts=CPP_EXT + PY_EXT, flags="-rn"):
    """Return list of 'path:line: text' hits (case-insensitive extended regex)."""
    hits = []
    for rel in rel_roots:
        base = os.path.join(ROOT, rel)
        if not os.path.exists(base):
            continue
        cmd = ["grep", flags + "i", "-E", pattern, base]
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stdout.splitlines():
            hits.append(line.replace(ROOT + "/", ""))
    return hits


def defs(path_rel, pattern=r"^\s*(class|struct)\s+\w+"):
    path = os.path.join(ROOT, path_rel)
    out = []
    if not os.path.exists(path):
        return out
    rx = re.compile(pattern)
    with open(path, "r", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            m = rx.search(line)
            if m:
                out.append({"line": i, "text": line.strip()[:120]})
    return out


def enum_members(path_rel, enum_name):
    """Collect identifiers inside `enum class <enum_name> { ... }` (single-file, line-based)."""
    path = os.path.join(ROOT, path_rel)
    members, inside, line_start = [], False, None
    if not os.path.exists(path):
        return {"file": path_rel, "members": members}
    with open(path, "r", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if not inside and enum_name in line and "enum" in line:
                inside, line_start = True, i
            elif inside:
                if "}" in line:
                    inside = False
                    continue
                for tok in re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", line):
                    if tok not in (enum_name,):
                        members.append({"line": i, "name": tok})
    return {"file": path_rel, "enum_line": line_start,
            "members": members}


stats = {}
stats["repo"] = "lssfau/walberla"
stats["head"] = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
stats["head_date"] = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%cI"],
                                    capture_output=True, text=True).stdout.strip()

# ---------------------------------------------------------------- module size
mods = {}
src_dirs = [d for d in sorted(os.listdir(os.path.join(ROOT, "src")))
            if os.path.isdir(os.path.join(ROOT, "src", d))]
for d in src_dirs:
    c = count(walk("src/" + d, CPP_EXT))
    if c["n_files"]:
        mods[d] = c
stats["modules_cpp"] = dict(sorted(mods.items(), key=lambda kv: -kv[1]["loc"]))

stats["totals"] = {
    "cpp_src": count(walk("src", CPP_EXT)),
    "apps_cpp": count(walk("apps", CPP_EXT)),
    "tests_cpp": count(walk("tests", CPP_EXT)),
    "python": count(walk("python", PY_EXT)),
    "sweepgen_python": count(walk("sweepgen/src", PY_EXT)),
    "lbm_cpp": count(walk("src/lbm", CPP_EXT)),
    "free_surface_cpp": count(walk("src/lbm/free_surface", CPP_EXT)),
    "gpu_cpp": count(walk("src/gpu", CPP_EXT)),
    "boundary_cpp": sum_dicts([count(walk("src/boundary", CPP_EXT)), count(walk("src/lbm/boundary", CPP_EXT))]),
}

# ---------------------------------------------------------------- lattices
stats["lattice_model_files"] = sorted(os.path.basename(p) for p in walk("src/lbm/lattice_model", (".h",)))
stats["lattice_classes"] = {p: defs(p, r"^\s*class\s+(D2Q9|D3Q15|D3Q19|D3Q27)\b")
                            for p in ["src/lbm/lattice_model/D2Q9.h", "src/lbm/lattice_model/D3Q15.h",
                                      "src/lbm/lattice_model/D3Q19.h", "src/lbm/lattice_model/D3Q27.h"]}

# ---------------------------------------------------------------- collision models
stats["collision_model_classes"] = defs("src/lbm/lattice_model/CollisionModel.h",
                                        r"^class\s+\w+")
stats["collision_tags"] = defs("src/lbm/lattice_model/CollisionModel.h", r"^struct\s+\w+_tag")
stats["collision_variants_present"] = {
    "kbc": len(grep(r"\bkbc\b|\bcascade\b", ["src/lbm", "src/lbm_generated"])),
    "regularized": len(grep(r"regulari[sz]ed", ["src/lbm", "src/lbm_generated"])),
    "shan_chen": len(grep(r"shan.?chen|pseudo.?potential|interparticle", ["src", "apps"])),
}

# ---------------------------------------------------------------- force models
stats["force_model_classes"] = defs("src/lbm/lattice_model/ForceModel.h", r"^class\s+\w+")
stats["force_model_tags"] = defs("src/lbm/lattice_model/ForceModel.h", r"^struct\s+\w+_tag")

# ---------------------------------------------------------------- boundaries
stats["lbm_boundary_classes"] = {p: defs("src/lbm/boundary/" + p, r"^class\s+\w+")
                                 for p in sorted(os.listdir(os.path.join(ROOT, "src/lbm/boundary")))
                                 if p.endswith(".h")}
stats["generic_boundary_classes"] = defs("src/boundary/BoundaryHandling.h", r"^\s*class\s+\w+")

# ---------------------------------------------------------------- free surface
fs = {}
fs["reconstruction_models"] = enum_members(
    "src/lbm/free_surface/dynamics/PdfReconstructionModel.h", "ReconstructionModel")
fs["refilling_models"] = enum_members(
    "src/lbm/free_surface/dynamics/PdfRefillingModel.h", "RefillingModel")
fs["excess_mass_models"] = enum_members(
    "src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h", "ExcessMassModel")
fs["curvature_models"] = defs("src/lbm/free_surface/surface_geometry/CurvatureModel.h",
                              r"^class\s+\w+")
fs["sweep_order"] = []
with open(os.path.join(ROOT, "src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h"),
          errors="replace") as fh:
    for i, line in enumerate(fh, 1):
        m = re.search(r'Sweep\(\s*[\w\(\)\.\*>&:_ ]+,\s*"([^"]+)"', line)
        if m:
            fs["sweep_order"].append({"line": i, "sweep": m.group(1).strip()})
stats["free_surface"] = fs

# ---------------------------------------------------------------- multiphase (phase field)
mp = {}
mp["showcase_apps"] = sorted(d for d in os.listdir(os.path.join(ROOT, "apps/showcases"))
                             if os.path.isdir(os.path.join(ROOT, "apps/showcases", d)))
mp["phasefield_apps"] = [d for d in mp["showcase_apps"] if "Phase" in d or "Thermocapillary" in d]
mp["codegen_imports"] = grep(r"from lbmpy\.phasefield_allen_cahn|import AllenCahnParameters",
                             ["apps/showcases/PhaseFieldAllenCahn/CPU",
                              "apps/showcases/Thermocapillary"])
mp["sc_absent_hits"] = len(grep(r"shan.?chen|pseudo.?potential|color.?gradient|free.?energy|"
                                r"carnahan|phase.?field.*conserv.*allied", ["src", "apps"]))
stats["multiphase"] = mp

# ---------------------------------------------------------------- gpu / parallel
gp = {}
gp["gpu_files"] = [os.path.basename(p) for p in walk("src/gpu", (".h", ".cu"))]
gp["gpu_comm_files"] = [os.path.basename(p) for p in walk("src/gpu/communication", (".h",))]
gp["layouts"] = sorted(set(m.group(1) for m in
                           (re.search(r"\b(fzyx|zyxf)\b", h) for h in
                            grep(r"layout", ["src/field/GhostLayerField.h"])) if m))
gp["gpu_target_codegen"] = len(grep(r"target=Target\.GPU", ["apps"]))
gp["mpi_partition_files"] = [os.path.basename(p) for p in walk("src/domain_decomposition", (".h",))]
gp["load_balancing"] = defs("src/blockforest/GlobalLoadBalancing.h", r"^\s*class\s+\w+")
stats["parallel"] = gp

# ---------------------------------------------------------------- codegen
cg = {}
cg["sweepgen_files"] = [os.path.relpath(p, ROOT) for p in walk("sweepgen/src", (".py",))]
cg["pystencils_walberla"] = sorted(os.listdir(os.path.join(ROOT, "python/pystencils_walberla")))
cg["lbmpy_walberla"] = sorted(os.listdir(os.path.join(ROOT, "python/lbmpy_walberla")))
cg["lbm_generated"] = sorted(d for d in os.listdir(os.path.join(ROOT, "src/lbm_generated"))
                             if os.path.isdir(os.path.join(ROOT, "src/lbm_generated", d)))
cg["generated_sweeps"] = sorted(os.listdir(os.path.join(ROOT, "src/lbm_generated/sweep_collection")))
stats["codegen"] = cg

# ---------------------------------------------------------------- tests / CI / apps
ts = {}
ts["test_cpp"] = count(walk("tests", (".cpp",)))
ts["test_dirs"] = sorted(d for d in os.listdir(os.path.join(ROOT, "tests"))
                         if os.path.isdir(os.path.join(ROOT, "tests", d)))
ts["test_lbm_files"] = sorted(os.listdir(os.path.join(ROOT, "tests/lbm")))
ts["ci_files"] = [f for f in (".gitlab-ci.yml",) if os.path.exists(os.path.join(ROOT, f))]
ts["github_dir"] = sorted(os.listdir(os.path.join(ROOT, ".github"))) \
    if os.path.isdir(os.path.join(ROOT, ".github")) else []
ts["apps_toplevel"] = sorted(os.listdir(os.path.join(ROOT, "apps")))
for sub in ("examples", "showcases", "benchmarks", "tutorials"):
    p = os.path.join(ROOT, "apps", sub)
    if os.path.isdir(p):
        ts["apps_" + sub] = sorted(d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d)))
stats["tests_ci_apps"] = ts

# ---------------------------------------------------------------- license
lic = {}
with open(os.path.join(ROOT, "COPYING.txt"), errors="replace") as fh:
    first = [next(fh).strip() for _ in range(3)]
lic["copying_head"] = first
stats["license"] = lic

with open(OUT, "w") as fh:
    json.dump(stats, fh, indent=1, ensure_ascii=False)
print("wrote", OUT)
print(json.dumps({k: stats[k] for k in ("head", "head_date", "totals")}, indent=1))