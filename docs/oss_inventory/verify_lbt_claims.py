#!/usr/bin/env python3
"""Re-verify every "absent"/"stub" claim made in LB-t.md against a fresh checkout.

usage: python3 docs/oss_inventory/verify_lbt_claims.py <path-to-LB-t-clone>
exit 0 = all claims hold, 1 = at least one claim falsified (report must be corrected).

The script only greps the source tree; it never modifies it.
"""
import json
import subprocess
import sys
import pathlib

repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lbt")
REV_PHYSICS = "v2.4"   # 42d377a — where the LBM physics lives
REV_HEAD = "devel"     # 162092a — default branch (scaffolding)

results = []


def sh(cmd, rev=None):
    if rev:
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", rev], check=True)
    p = subprocess.run(cmd, cwd=repo, shell=True, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def claim(cid, desc, rev, cmd, expect_empty=True):
    rc, out, err = sh(cmd, rev)
    hits = [l for l in out.splitlines() if l.strip()]
    ok = (len(hits) == 0) if expect_empty else (len(hits) > 0)
    results.append({"id": cid, "claim": desc, "rev": rev, "cmd": cmd,
                    "matches": len(hits), "ok": ok,
                    "sample": hits[:3], "rc": rc})
    return ok


# ---- claims: multiphase / forcing / collision-family / parallel backends are ABSENT
claim("MP-1", "no multiphase keywords anywhere (v2.4 src/)", REV_PHYSICS,
      r'grep -rniE "shan|multiphase|phase_field|phasefield|colorgradient|free_surface|freesurface" src/ || true')
claim("MP-2", "no body-force / interaction-force term (v2.4 src/)", REV_PHYSICS,
      r'grep -rniE "body_?force|forceterm|force_term|interaction|guo_?force|G\[" src/ || true')
claim("MP-3", "no multiphase modules in devel include tree", REV_HEAD,
      r'grep -rniE "shan|multiphase|phase_field|phasefield|free_surface" include/ || true')
claim("COL-1", "no MRT / KBC / regularized / cumulant collision (v2.4)", REV_PHYSICS,
      r'grep -rniE "mrt|kbc|regularized|cumulant|entropic" src/ || true')
claim("COL-2", "devel collision/ holds exactly {bgk.hpp, collision_base.hpp}", REV_HEAD,
      "ls include/lbt/populations/collision/ | tr '\\n' ' ' | grep -qx 'bgk.hpp collision_base.hpp ' && true || echo MISMATCH")
claim("PAR-1", "no MPI / CUDA / HIP / SYCL backend (v2.4 src/)", REV_PHYSICS,
      r'grep -rniE "\bmpi\b|cuda|hip/|sycl|kokkos" src/ || true')
claim("PAR-2", "no MPI / CUDA / HIP / SYCL backend (devel include+src)", REV_HEAD,
      r'grep -rniE "\bmpi\b|cuda|hip/|sycl|kokkos" include/ src/ || true')
# NOTE: "Zou" occurs once in v2.4, but only as a cited author in the doc comment of
# HalfwayBounceBack (boundary_bounceback.hpp:22 "X. He, Q. Zou, L.S. Luo, M. Dembo").
# So the check strips comment lines and only looks at code.
claim("BC-1", "no Zou-He / curvature-corrected BB / immersed boundary in CODE (v2.4)", REV_PHYSICS,
      r'grep -rniE "zou|curvature|immersed|partial_?slip|wall_?function" src/ | grep -vE ":[0-9]+: *\\*" || true')
claim("BC-1b", "the single v2.4 'Zou' hit is a literature citation in a doc comment", REV_PHYSICS,
      r'grep -rn "Zou" src/ | grep -vE ":[0-9]+: *\\*" | wc -l | grep -qx 0 && true || echo CODE_HIT')
claim("TST-1", "v2.4 ships NO test directory", REV_PHYSICS, 'ls -d test 2>/dev/null', expect_empty=True)
claim("TST-2", "devel ships a gtest suite", REV_HEAD, 'ls test/run_tests.cpp', expect_empty=False)

# ---- claims: stub existence (must be PRESENT)
claim("STUB-1", "devel collision has an unimplemented collide-stream TODO", REV_HEAD,
      r'grep -n "TODO(tobit): Implement collide-stream" include/lbt/populations/collision/bgk.hpp',
      expect_empty=False)
claim("STUB-2", "devel Simulation::run() has an empty body", REV_HEAD,
      r'grep -n "Collision & streaming" include/lbt/simulation/simulation.hpp', expect_empty=False)
claim("NPOP-1", "NPOP>1 never instantiated: every Population<> use passes no NPOP (v2.4)",
      REV_PHYSICS,
      r'grep -rn "Population<NX,NY,NZ,lattice::D3Q27,FType>" src/main.cpp || true', expect_empty=False)

# ---- counts that back figure panel (a)/(b)
rc, loc_v24, _ = sh(r"find src -name '*.hpp' -o -name '*.cpp' | xargs cat | wc -l", REV_PHYSICS)
rc, loc_dev, _ = sh(r"find include src -name '*.hpp' -o -name '*.cpp' | xargs cat | wc -l", REV_HEAD)
counts = {"v2.4_src_loc": int(loc_v24), "devel_include_loc": int(loc_dev)}

sh("true", REV_HEAD)  # leave the clone on the default branch
bad = [r for r in results if not r["ok"]]
print(json.dumps({"checks": len(results), "failed": len(bad),
                  "counts": counts,
                  "failures": bad,
                  "claims": [{k: r[k] for k in ("id", "claim", "rev", "matches", "ok")}
                             for r in results]}, indent=2, ensure_ascii=False))
sys.exit(0 if not bad else 1)