#!/usr/bin/env python3
"""Build machine-verified evidence pointers for the SPlisHSPlasH deep-dive (m8650).

Every ``file:line`` claim made in the report is declared here as
``(path, line, must_contain)``.  The script re-fetches each file from the
GitHub contents API at the pinned commit, then checks that the line really
contains the expected token.  Output (``splishsplash_pointers.json``) records
path / line / exact source text / per-file sha256 / per-claim pass-fail, so a
reviewer can audit the report without trusting hand-copied snippets.

Usage::

    python docs/oss_inventory_sph/build_splishsplash_evidence.py

Requires GitHub credentials (``~/.git-credentials`` or ``GITHUB_TOKEN``); the
request path uses the contents API with ``Accept: application/vnd.github.raw``
because raw.githubusercontent.com is blocked from this sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "InteractiveComputerGraphics/SPlisHSPlasH"
REF = "master"  # resolved to a commit SHA at run time, see resolve_ref()
OUT = Path(__file__).resolve().parent / "splishsplash_pointers.json"

# (path, line, must_contain) -- line numbers are 1-based, as in GitHub's blob view.
CLAIMS: list[tuple[str, int, str]] = [
    ("SPlisHSPlasH/Simulation.h", 170, "enum class SimulationMethods"),
    ("SPlisHSPlasH/Simulation.h", 171, "enum class BoundaryHandlingMethods"),
    ("SPlisHSPlasH/Simulation.h", 173, "NoginaSellan2026"),
    ("SPlisHSPlasH/Simulation.h", 193, "KERNEL_METHOD"),
    ("SPlisHSPlasH/Simulation.cpp", 70, "m_cflMethod = 1"),
    ("SPlisHSPlasH/Simulation.cpp", 73, "m_cflMaxTimeStepSize"),
    ("SPlisHSPlasH/Simulation.cpp", 92, "BoundaryHandlingMethods::Bender2019"),
    ("SPlisHSPlasH/Simulation.cpp", 427, "updateTimeStepSizeCFL"),
    ("SPlisHSPlasH/Simulation.cpp", 550, "SimulationMethods::WCSPH"),
    ("SPlisHSPlasH/Simulation.cpp", 563, "new TimeStepWCSPH()"),
    ("SPlisHSPlasH/Simulation.cpp", 591, "new TimeStepDFSPH()"),
    ("SPlisHSPlasH/Simulation.cpp", 598, "new TimeStepPF()"),
    ("SPlisHSPlasH/Simulation.cpp", 606, "new TimeStepICSPH()"),
    ("SPlisHSPlasH/Simulation.cpp", 708, "addFluidModel"),
    ("SPlisHSPlasH/Simulation.cpp", 499, "m_cflFactor * static_cast<Real>(0.4)"),
    ("SPlisHSPlasH/Simulation.cpp", 502, "m_cflMinTimeStepSize"),
    ("SPlisHSPlasH/TimeStep.cpp", 181, "computeVolumeAndBoundaryX"),
    ("SPlisHSPlasH/TimeStep.cpp", 353, "computeDensityAndGradient"),
    ("SPlisHSPlasH/FluidModel.h", 84, "DENSITY0"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 68, "void TimeStepWCSPH::step"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 106, "pow(density / density0, m_exponent)"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 177, "fm_neighbor->getDensity0()"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 186, "Akinci2012"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 194, "usesDensityMaps"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 202, "Bender2019"),
    ("SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp", 99, "#pragma omp parallel"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 36, "m_maxIterations = 100"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 37, "m_maxError"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 41, "m_maxErrorV"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 167, "divergenceSolve()"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 214, "pressureSolve()"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 336, "maxError is given in percent"),
    ("SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp", 386, "divergenceSolve"),
    ("SPlisHSPlasH/IISPH/TimeStepIISPH.cpp", 170, "getDii"),
    ("SPlisHSPlasH/IISPH/TimeStepIISPH.cpp", 279, "aii"),
    ("SPlisHSPlasH/IISPH/TimeStepIISPH.cpp", 321, "m_maxIterations"),
    ("SPlisHSPlasH/PCISPH/TimeStepPCISPH.cpp", 169, "m_maxIterations"),
    ("SPlisHSPlasH/PBF/TimeStepPBF.cpp", 125, "Solve density constraint"),
    ("SPlisHSPlasH/PF/TimeStepPF.cpp", 246, "m_maxIterations"),
    ("SPlisHSPlasH/PF/TimeStepPF.cpp", 259, "setTolerance"),
    ("SPlisHSPlasH/ICSPH/TimeStepICSPH.cpp", 328, "m_lambda"),
    ("SPlisHSPlasH/ICSPH/TimeStepICSPH.cpp", 99, "void TimeStepICSPH::step"),
    ("SPlisHSPlasH/SPHKernels.h", 31, "m_k = static_cast<Real>(8.0) / (pi*h3)"),
    ("SPlisHSPlasH/SPHKernels.h", 209, "static_cast<Real>(15.0) / (pi*radius6)"),
    ("SPlisHSPlasH/SPHKernels.h", 290, "static_cast<Real>(21.0)"),
    ("SPlisHSPlasH/SPHKernels.h", 479, "static_cast<Real>(40.0)"),
    ("SPlisHSPlasH/SPHKernels.h", 562, "static_cast<Real>(7.0) / (pi*h2)"),
    ("SPlisHSPlasH/NeighborhoodSearch.h", 9, "cuNSearch::NeighborhoodSearch"),
    ("SPlisHSPlasH/NeighborhoodSearch.h", 12, "CompactNSearch::NeighborhoodSearch"),
    ("SPlisHSPlasH/NeighborhoodSearch.h", 14, "TreeNSearch"),
    ("SPlisHSPlasH/XSPH.cpp", 46, "void XSPH::step"),
    ("SPlisHSPlasH/NonPressureForceRegistration.cpp", 35, "registerNonpressureForces"),
    ("SPlisHSPlasH/NonPressureForceRegistration.cpp", 59, "addViscosityMethod"),
    ("CMake/Common.cmake", 75, "USE_OpenMP"),
    ("CMake/Common.cmake", 82, "OPENMP_FOUND"),
    ("CMake/NeighborhoodSearch.cmake", 11, "USE_cuNSearch"),
    ("CMake/NeighborhoodSearch.cmake", 17, "enable_language(CUDA)"),
    ("Tests/Kernel/KernelTests.cpp", 17, "3D Kernel is normalized"),
    ("Tests/Kernel/KernelTests.cpp", 44, "REQUIRE(fabs(sum - 1.0) < eps)"),
    ("Tests/CMakeLists.txt", 1, "add_subdirectory(Kernel)"),
    (".github/workflows/build-linux.yml", 23, "./KernelTests"),
    ("doc/replicability.md", 1, "Replicability"),
    ("Changelog.txt", 5, "Nogina and Sellan"),
]


def _token() -> str:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, check=False,
        ).stdout
    except Exception:  # pragma: no cover - no credential helper
        return ""
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    return ""


def _get(url: str, tok: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"Authorization": f"token {tok}",
                      "Accept": "application/vnd.github.raw",
                      "User-Agent": "eco-agent"})
    return urllib.request.urlopen(req, timeout=60).read()


def resolve_ref(tok: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/{REF}",
        headers={"Authorization": f"token {tok}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "eco-agent"})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return data["sha"]


def main() -> int:
    tok = _token()
    if not tok:
        print("no GitHub token available", file=sys.stderr)
        return 2
    sha = resolve_ref(tok)
    cache: dict[str, list[str]] = {}
    files: dict[str, str] = {}
    rows = []
    for path, line, needle in CLAIMS:
        if path not in cache:
            raw = _get(f"https://api.github.com/repos/{REPO}/contents/{path}?ref={sha}", tok)
            text = raw.decode("utf-8", errors="replace")
            files[path] = hashlib.sha256(raw).hexdigest()
            cache[path] = text.splitlines()
        lines = cache[path]
        ok = 1 <= line <= len(lines) and needle in lines[line - 1]
        rows.append({
            "path": path,
            "line": line,
            "must_contain": needle,
            "text": lines[line - 1].strip() if 1 <= line <= len(lines) else None,
            "ok": ok,
        })
    failed = [r for r in rows if not r["ok"]]
    payload = {
        "campaign": "osssph-w2",
        "repo": REPO,
        "ref_requested": REF,
        "commit_sha": sha,
        "n_claims": len(rows),
        "n_failed": len(failed),
        "file_sha256": files,
        "claims": rows,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"commit={sha} claims={len(rows)} failed={len(failed)} -> {OUT}")
    for r in failed:
        print("FAIL", r["path"], r["line"], "expected:", r["must_contain"], "got:", r["text"])
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())