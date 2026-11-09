#!/usr/bin/env python3
"""Metadata verification for candidates surfaced by
sthavishtha/list-lattice-Boltzmann-codes (canonical LBM code list) and by the
github search rounds (FEAT-OSS-INVENTORY-LBM, mission 8307).

Every row written here is a live api.github.com /repos/{owner}/{name} payload
excerpt -> existence evidence (stars / description / pushed_at / archived), so
the discovery note never relies on memory.

Output: artifacts/m8307/canonical_candidates.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "tensorplatform-oss-inventory", "Accept": "application/vnd.github+json"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "m8307")

# canonical-list entries not yet in oss_inventory + search surfacing worth verifying
TARGETS = [
    # --- from sthavishtha/list-lattice-Boltzmann-codes ---
    "lanl/MF-LBM",
    "je-santos/MultiphasePorousMediaPalabos",
    "apes-suite/musubi",
    "otbrown/LAMBReX",
    "blackoffee/3D-LBM-AMR",
    "copmat/LBsoft",
    "ecoon/Taxila-LBM",
    "mehdiataei/LBfoam",
    "UCL/LatBo.jl",
    "weierstrass/wlb",
    "jabhiji/lbmles",
    "cb-geo/2d-lbm-dem",
    "ParticulateFlow/LBDEMcoupling-public",
    "Clemson-MSE/hemelb-gpu",
    "XInhuan-imperial/Lattice-Boltzmann-Method-GPU",
    "fabioskomori/lbsim",
    "rlee32/lbm_matlab",
    "shurikkuzmin/LatticeBoltzmannMethod",
    "longmakesstuff/Lattice-Boltzmann",
    "ecalzavarini/ch4-project",
    # --- from search rounds ---
    "openLB/openLB",
    "JuliusWeinmiller/ThermalMultiphaseOpenLB",
    "geoelements/mcmp-lbm",
    "LBMFluids/sc_lbm",
    "ebinan92/Fingering_dynamics",
    "Zitzeronion/Swalbe.jl",
    "grasingerm/lbxflow",
    "NatLabRockies/marbles",
    "spoonacular/LBM_python",
    "lhufnagel/multi-lbm",
    "carlosrosales/mplabs",
    "EPyutao/MCMP_LBM",
    "Geoenergia-Lab/HermiteLBM",
    "zhishang72/OpenLBM",
    "wme7/openLBMflow",
    "pmocz/latticeboltzmann-python",
    "kobejean/tf-cfd",
    "kuldeep-tolia/LBM_Codes",
]


def _get(url: str, tries: int = 2) -> tuple[int, bytes]:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(20)
                continue
            return e.code, b""
        except Exception:
            time.sleep(3)
    return 0, b""


def main() -> int:
    existing = {d["repo"].lower() for d in json.load(open("/tmp/existing_inv.json"))}
    out: dict[str, dict] = {}
    for full in TARGETS:
        st, body = _get(f"https://api.github.com/repos/{full}")
        rec: dict = {"http": st}
        if st == 200:
            it = json.loads(body)
            rec.update({
                "repo": it["full_name"], "stars": it["stargazers_count"],
                "forks": it.get("forks_count"),
                "description": (it.get("description") or "").strip(),
                "language": it.get("language"), "topics": it.get("topics") or [],
                "pushed_at": it.get("pushed_at"), "created_at": it.get("created_at"),
                "archived": it.get("archived", False), "size_kb": it.get("size"),
                "license": (it.get("license") or {}).get("spdx_id"),
                "in_inventory": full.lower() in existing,
            })
        else:
            rec["in_inventory"] = full.lower() in existing
        out[full] = rec
        print(f"  {full:48s} http={st} " + (f"{rec.get('stars'):5d}* {rec.get('pushed_at','')[:10]} "
              f"{rec.get('description','')[:50]}" if st == 200 else "MISSING"), flush=True)
        time.sleep(1.2)

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "source": "api.github.com/repos/{owner}/{name}",
               "results": out}
    json.dump(payload, open(os.path.join(OUT, "canonical_candidates.json"), "w"),
              indent=2, ensure_ascii=False)
    print(f"\n-> artifacts/m8307/canonical_candidates.json ({len(out)} repos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())