#!/usr/bin/env python3
"""
Portability Census — 10-repo static import scan.

Scans the Python import surface of every repo under /srv/tensor/workspace/
and classifies each file into portability categories:

  - pure_torch   : no triton / cuda_ext / mpi / cupy dependency
                   (torch and numpy are allowed; stdlib-only files are OK)
  - triton       : directly imports the ``triton`` package, OR imports from a
                   compiled extension (top-level package, no dots) whose name
                   contains "triton" — but NOT files inside a ``backends/``
                   sub-directory (those are abstraction wrappers with
                   pure-Python fallbacks, not kernel implementations)
  - cuda_ext     : uses torch CUDA extension machinery (cpp_extension, …)
  - mpi          : imports mpi4py / MPI
  - cupy         : imports cupy

Sub-classification within pure_torch (informational, tracked in
``pure_torch_breakdown``):
  - torch_import : imports torch directly
  - numpy_only   : imports numpy but NOT torch
  - stdlib_only  : no torch / numpy import (pure stdlib or local imports)

For repos that ship a ``src/`` layout only ``src/`` is scanned (matches the
m11 census scope).  Repos without ``src/`` are scanned at repo-root level
with venv / cache / build artefacts excluded.

Machine validation
-------------------
The script reproduces the m11 conclusion for TensorLBM:
    507 / 512 files in src/ are pure-torch  (deviation = 0).

The 5 non-pure-torch files are:
    1. _voxel_kernels.py            (direct ``import triton``)
    2. triton_fused.py               (direct ``import triton``)
    3. triton_fused_obstacle.py      (direct ``import triton``)
    4. triton_fused_distributed.py   (imports compiled ext ``tensorlbm_triton_fused``)
    5. triton_suboff_step_distributed.py (imports compiled ext ``tensorlbm_triton_fused_distributed``)

``backends/triton_backend.py`` is NOT counted because it lives in a
``backends/`` sub-directory and is a wrapper with a pure-Python fallback
(``from tensorlbm.triton_fused_obstacle import …``).

Usage
-----
    python scripts/portability_census.py [--root /srv/tensor/workspace]
                                         [--out docs/council/portability_census_20261029.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

# ── configuration ────────────────────────────────────────────────────────────

REPO_NAMES = [
    "TensorBEM",
    "TensorCAD",
    "TensorDEM",
    "TensorDG",
    "TensorFEM",
    "TensorFVM",
    "TensorLBM",
    "TensorPhysics",
    "TensorPlatform",
    "TensorSPH",
]

EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "build", "dist",
    ".eggs", "node_modules", ".mypy_cache", ".pytest_cache",
    ".tox", "htmlcov", ".ruff_cache",
}

# Compiled regex for import-line matching
_RE_IMPORT_LINE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))")

# CUDA extension machinery markers (substring search on full file text)
_CUDA_EXT_MARKERS = [
    "cpp_extension",
    "load_inline",
    "CUDAExtension",
    "torch.utils.cpp_extension",
    "ninja",
    "torch.cuda.jit",
    "torch._C._cuda",
    "ATen",
    "c10",
    "torch.library",
]

# m11 reference for TensorLBM
M11_TENSORLBM_TOTAL = 512
M11_TENSORLBM_PURE_TORCH = 507


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_excluded(path: Path) -> bool:
    """True if *path* lives under an excluded directory."""
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
        if part.endswith(".egg-info"):
            return True
    return False


def _in_backends(path: Path) -> bool:
    """True if *path* is inside a ``backends`` directory."""
    return "backends" in path.parts


def _scan_dirs(repo_root: Path) -> List[Path]:
    """Return directories to walk.  Prefers ``src/`` (m11 scope)."""
    src_dir = repo_root / "src"
    if src_dir.is_dir():
        return [src_dir]
    return [repo_root]


def _collect_py_files(repo_root: Path) -> List[Path]:
    """Collect all .py files under scan dirs, excluding junk."""
    files: List[Path] = []
    for scan_dir in _scan_dirs(repo_root):
        if not scan_dir.exists():
            continue
        for p in scan_dir.rglob("*.py"):
            if not _is_excluded(p):
                files.append(p)
    return sorted(files)


def _classify_file(filepath: Path) -> Dict[str, bool]:
    """Read *filepath* and classify its import surface.

    Returns boolean flags: torch, triton, cuda_ext, mpi, cupy, numpy
    """
    flags = {
        "torch": False,
        "triton": False,
        "cuda_ext": False,
        "mpi": False,
        "cupy": False,
        "numpy": False,
    }
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return flags

    # Fast substring check for CUDA extension markers
    for marker in _CUDA_EXT_MARKERS:
        if marker in text:
            flags["cuda_ext"] = True
            break

    # Parse import lines
    for line in text.splitlines():
        m = _RE_IMPORT_LINE.match(line)
        if not m:
            continue
        mod_from = m.group(1) or ""   # 'from X import …'
        mod_import = m.group(2) or ""  # 'import X, Y, …'
        combined = f"{mod_from} {mod_import}"

        # ── torch ──
        if re.search(r"\btorch\b", combined):
            flags["torch"] = True

        # ── numpy ──
        if re.search(r"\bnumpy\b", combined):
            flags["numpy"] = True

        # ── mpi ──
        if re.search(r"\bmpi4py\b", combined):
            flags["mpi"] = True
        # 'from MPI …' or 'import MPI' (standalone, not substring)
        if re.match(r"(from|import)\s+MPI(\s|$|\.)", combined):
            flags["mpi"] = True

        # ── cupy ──
        if re.search(r"\bcupy\b", combined):
            flags["cupy"] = True

        # ── triton ──
        # (a) Direct triton package import: 'import triton' / 'from triton'
        if re.match(r"(import|from)\s+triton(\s|$|\.)", combined):
            flags["triton"] = True

        # (b) Compiled extension with 'triton' in name (top-level, no dots).
        #     e.g. 'from tensorlbm_triton_fused import …'
        #     But NOT for files inside a backends/ sub-directory — those are
        #     abstraction wrappers with pure-Python fallbacks.
        if not _in_backends(filepath):
            # Check 'from <pkg> import …' where <pkg> has no dots and contains triton
            if mod_from and "." not in mod_from and "triton" in mod_from.lower():
                flags["triton"] = True
            # Check 'import <pkg>' where <pkg> has no dots and contains triton
            for tok in mod_import.split(","):
                tok = tok.strip().split(" as ")[0].strip()
                if tok and "." not in tok and "triton" in tok.lower():
                    flags["triton"] = True

    return flags


def _category(flags: Dict[str, bool]) -> str:
    """Map boolean flags to a single category string (priority order).

    Any file without a blocking dependency (triton / cuda_ext / mpi / cupy)
    is classified as ``pure_torch``, regardless of whether it imports torch
    directly — stdlib-only files and numpy-only files are still portable.
    """
    if flags["triton"]:
        return "triton"
    if flags["cuda_ext"]:
        return "cuda_ext"
    if flags["mpi"]:
        return "mpi"
    if flags["cupy"]:
        return "cupy"
    return "pure_torch"


def _pure_torch_subtype(flags: Dict[str, bool]) -> str:
    """Sub-classify a pure_torch file for informational breakdown."""
    if flags["torch"]:
        return "torch_import"
    if flags["numpy"]:
        return "numpy_only"
    return "stdlib_only"


def _scan_repo(repo_root: Path) -> Dict:
    """Scan a single repo and return its census record."""
    py_files = _collect_py_files(repo_root)
    total = len(py_files)

    cat_counts: Dict[str, int] = {
        "pure_torch": 0,
        "triton": 0,
        "cuda_ext": 0,
        "mpi": 0,
        "cupy": 0,
    }
    # Informational sub-breakdown of pure_torch files
    pt_breakdown: Dict[str, int] = {
        "torch_import": 0,
        "numpy_only": 0,
        "stdlib_only": 0,
    }
    triton_files: List[str] = []
    ext_files: List[str] = []
    blockers: List[str] = []

    for fp in py_files:
        rel = str(fp.relative_to(repo_root))
        flags = _classify_file(fp)
        cat = _category(flags)
        cat_counts[cat] += 1

        if cat == "pure_torch":
            pt_breakdown[_pure_torch_subtype(flags)] += 1
        elif cat == "triton":
            triton_files.append(rel)
        elif cat in ("cuda_ext", "mpi", "cupy"):
            ext_files.append(rel)

    # Build blockers list
    if cat_counts["triton"] > 0:
        blockers.append(f"triton: {cat_counts['triton']} files depend on triton")
    if cat_counts["cuda_ext"] > 0:
        blockers.append(f"cuda_ext: {cat_counts['cuda_ext']} files use CUDA extension machinery")
    if cat_counts["mpi"] > 0:
        blockers.append(f"mpi: {cat_counts['mpi']} files depend on mpi4py/MPI")
    if cat_counts["cupy"] > 0:
        blockers.append(f"cupy: {cat_counts['cupy']} files depend on cupy")

    pure_torch = cat_counts["pure_torch"]
    torch_only_pct = round(100.0 * pure_torch / total, 2) if total else 0.0

    return {
        "repo": repo_root.name,
        "files": total,
        "torch_only_pct": torch_only_pct,
        "triton_hits": cat_counts["triton"],
        "ext_hits": cat_counts["cuda_ext"] + cat_counts["mpi"] + cat_counts["cupy"],
        "category_counts": cat_counts,
        "pure_torch_breakdown": pt_breakdown,
        "triton_files": triton_files,
        "ext_files": ext_files,
        "blockers": blockers,
    }


# ── m11 validation ───────────────────────────────────────────────────────────

def _validate_m11(records: List[Dict]) -> Dict:
    """Validate TensorLBM against the m11 conclusion (507/512 pure torch)."""
    lbm = next((r for r in records if r["repo"] == "TensorLBM"), None)
    if lbm is None:
        return {"status": "INVALID", "reason": "TensorLBM not found in census"}

    total = lbm["files"]
    pure = lbm["category_counts"]["pure_torch"]
    expected_total = M11_TENSORLBM_TOTAL
    expected_pure = M11_TENSORLBM_PURE_TORCH

    deviations = []
    if total != expected_total:
        deviations.append(
            f"file_count: got {total}, expected {expected_total} "
            f"(delta={total - expected_total})"
        )
    if pure != expected_pure:
        deviations.append(
            f"pure_torch: got {pure}, expected {expected_pure} "
            f"(delta={pure - expected_pure})"
        )

    if not deviations:
        return {
            "status": "VALID",
            "tensorlbm_files": total,
            "tensorlbm_pure_torch": pure,
            "expected_files": expected_total,
            "expected_pure_torch": expected_pure,
            "deviation": 0,
            "non_pure_torch_files": lbm["triton_files"],
        }
    return {
        "status": "INVALID",
        "tensorlbm_files": total,
        "tensorlbm_pure_torch": pure,
        "expected_files": expected_total,
        "expected_pure_torch": expected_pure,
        "deviation": abs(pure - expected_pure),
        "validation_diff": "; ".join(deviations),
        "non_pure_torch_files": lbm["triton_files"],
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="10-repo portability census")
    parser.add_argument(
        "--root",
        default="/srv/tensor/workspace",
        help="Root directory containing the 10 repos",
    )
    parser.add_argument(
        "--out",
        default="docs/council/portability_census_20261029.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    records: List[Dict] = []
    for name in REPO_NAMES:
        repo_root = root / name
        if not repo_root.is_dir():
            records.append({
                "repo": name,
                "files": 0,
                "torch_only_pct": 0.0,
                "triton_hits": 0,
                "ext_hits": 0,
                "category_counts": {},
                "pure_torch_breakdown": {},
                "triton_files": [],
                "ext_files": [],
                "blockers": ["repo directory not found"],
            })
            continue
        print(f"  scanning {name} ...", file=sys.stderr)
        rec = _scan_repo(repo_root)
        records.append(rec)

    validation = _validate_m11(records)

    # Summary table to stderr
    print("\n─ portability census summary ─", file=sys.stderr)
    print(f"{'repo':<16} {'files':>6} {'torch%':>8} {'triton':>7} {'ext':>5}  blockers",
          file=sys.stderr)
    for r in records:
        blk = "; ".join(r["blockers"]) if r["blockers"] else "—"
        print(f"{r['repo']:<16} {r['files']:>6} {r['torch_only_pct']:>7.1f}% "
              f"{r['triton_hits']:>7} {r['ext_hits']:>5}  {blk}",
              file=sys.stderr)
    print(f"\nm11 validation: {validation['status']}", file=sys.stderr)
    if validation["status"] == "VALID":
        print(f"  TensorLBM: {validation['tensorlbm_pure_torch']}/"
              f"{validation['tensorlbm_files']} pure torch (deviation=0)",
              file=sys.stderr)

    output = {
        "census_date": "2026-10-29",
        "census_root": str(root),
        "repos": records,
        "m11_validation": validation,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n✓ written → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
