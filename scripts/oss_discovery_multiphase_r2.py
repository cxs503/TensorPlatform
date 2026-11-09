#!/usr/bin/env python3
"""Round 2 of OSS LBM discovery (FEAT-OSS-INVENTORY-LBM, mission 8307).

Round 1 (scripts/oss_discovery_multiphase.py) covered multiphase / colour-gradient /
free-surface / phase-field / topics / wetting / thermal / shan-chen.
Round 2 broadens to pseudopotential (SCMP), multicomponent, droplet, porous-media
two-phase, conjugate-heat-transfer, and fixes the canonical-list README branch.
It also existence-checks a hand-curated list of well-known upstreams by name.

Output: artifacts/m8307/discovery_raw2.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "tensorplatform-oss-inventory", "Accept": "application/vnd.github+json"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "m8307")
SEARCH = "https://api.github.com/search/repositories"
REPO_API = "https://api.github.com/repos"

QUERIES = [
    ("pseudopotential", "lattice+boltzmann+pseudopotential"),
    ("multicomponent", "lattice+boltzmann+multicomponent"),
    ("two_phase", "lattice+boltzmann+two-phase"),
    ("droplet", "lattice+boltzmann+droplet"),
    ("porous_two_phase", "lattice+boltzmann+porous+media+two-phase"),
    ("conjugate_heat", "lattice+boltzmann+conjugate+heat+transfer"),
    ("topic_multiphase", "topic:multiphase-flow+lattice"),
    ("topic_gpu_lbm", "lattice+boltzmann+opencl+cuda+multiphase"),
]

# Known-by-name upstreams to existence-check (not trusted from memory: each hit
# is recorded with the live API payload so the claim is evidence-backed).
NAMED = [
    "openLB/openLB",
    "MF-LBM/MF-LBM",
    "priestc/MF-LBM",
    "Sihao-Liao/LBM",
    "gongzhengqi/LBM",
    "lanl/OpenLBM",
    "OpenLB/OpenLB",
    "CFD-GO/Palabos",
    "UniStuttgart-IHR/OpenLB",
]
LIST_CANDIDATES = [
    "https://raw.githubusercontent.com/sthavishtha/list-lattice-Boltzmann-codes/main/README.md",
    "https://raw.githubusercontent.com/sthavishtha/list-lattice-Boltzmann-codes/master/README.md",
]


def _get(url: str, tries: int = 3) -> tuple[int, bytes]:
    last = (0, b"")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, e.read() or b"")
            if e.code in (403, 429):
                time.sleep(25 * (i + 1))
                continue
            return last
        except Exception as e:
            last = (0, str(e).encode())
            time.sleep(5)
    return last


def search(query: str, per_page: int = 30) -> list[dict]:
    st, body = _get(f"{SEARCH}?q={query}&sort=stars&order=desc&per_page={per_page}")
    if st != 200:
        print(f"  ! {query} -> HTTP {st}", file=sys.stderr)
        return []
    return json.loads(body).get("items", [])


def list_entries() -> tuple[str, dict[str, int]]:
    for url in LIST_CANDIDATES:
        st, body = _get(url)
        if st == 200:
            text = body.decode("utf-8", "replace")
            hits: dict[str, int] = {}
            for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text):
                owner, name = m.group(1), m.group(2)
                if owner.lower() in {"sthavishtha", "github", "www"}:
                    continue
                name = name.removesuffix(".git")
                if name in {"tree", "blob", "issues", "wiki"}:
                    continue
                key = f"{owner}/{name}"
                hits[key] = hits.get(key, 0) + 1
            return url, hits
    return "", {}


def main() -> int:
    existing = {d["repo"].lower() for d in json.load(open("/tmp/existing_inv.json"))}
    found: dict[str, dict] = {}
    per_query: dict[str, int] = {}
    for label, q in QUERIES:
        items = search(q)
        per_query[label] = len(items)
        for it in items:
            if it.get("fork"):
                continue
            s = {
                "repo": it["full_name"], "stars": it["stargazers_count"],
                "description": (it.get("description") or "").strip(),
                "language": it.get("language"), "topics": it.get("topics") or [],
                "pushed_at": it.get("pushed_at"), "created_at": it.get("created_at"),
                "archived": it.get("archived", False), "size_kb": it.get("size"),
                "license": (it.get("license") or {}).get("spdx_id"),
                "discovered_via": f"search2:{label}",
            }
            cur = found.setdefault(s["repo"], s)
            if cur is not s:
                cur["stars"] = max(cur["stars"], s["stars"])
                cur["discovered_via"] += ";" + s["discovered_via"]
        print(f"  {label:20s} -> {len(items):3d}", flush=True)
        time.sleep(7)

    url, listed = list_entries()
    print(f"canonical list: {url or 'UNREACHABLE'} entries={len(listed)}", flush=True)

    named = {}
    for full in NAMED:
        st, body = _get(f"{REPO_API}/{full}")
        rec = {"http": st}
        if st == 200:
            it = json.loads(body)
            rec.update({"stars": it["stargazers_count"],
                        "description": (it.get("description") or "").strip(),
                        "pushed_at": it.get("pushed_at"), "archived": it.get("archived"),
                        "full_name": it["full_name"]})
        named[full] = rec
        print(f"  named {full:32s} http={st} " +
              (f"stars={rec.get('stars')}" if st == 200 else ""), flush=True)
        time.sleep(1.5)

    new = [(k, v) for k, v in sorted(found.items(), key=lambda kv: -kv[1]["stars"])
           if k.lower() not in existing]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": dict(QUERIES), "hits_per_query": per_query,
        "canonical_list_url": url, "canonical_list_entries": listed,
        "named_checks": named,
        "n_unique_outside_inventory": len(new),
        "candidates_raw": [dict(v, in_canonical_list=listed.get(v["repo"], 0)) for _, v in new],
    }
    p = os.path.join(OUT, "discovery_raw2.json")
    json.dump(payload, open(p, "w"), indent=2, ensure_ascii=False)
    print(f"\n-> {p} ({len(new)} outside inventory)")
    for k, v in new[:30]:
        print(f"  {v['stars']:6d} {k:48s} {v['pushed_at'][:10]} L={listed.get(k,0)} {v['description'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())