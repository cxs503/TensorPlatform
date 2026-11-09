#!/usr/bin/env python3
"""OSS LBM discovery — multiphase / multi-component / thermal-flow / wetting focus.

FEAT-OSS-INVENTORY-LBM (mission 8307).

Discovery only: collect existence evidence (stars / description / pushed_at /
topics / archived) for repos outside the current 24-repo oss_inventory, so a
human or downstream node can decide what to deep-audit. No code cloning, no
quality claims beyond what the GitHub metadata + list-repo mention support.

Sources (all whitelisted):
  1. api.github.com /search/repositories  (topic + keyword queries)
  2. api.github.com /repos/...            (per-candidate metadata refresh)
  3. raw.githubusercontent.com            (sthavishtha/list-lattice-Boltzmann-codes README)

Output: artifacts/m8307/discovery_raw.json  (raw, everything returned)
Write with: python3 scripts/oss_discovery_multiphase.py [--candidates a/b,c/d]
"""
from __future__ import annotations

import argparse
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
LIST_REPO = "https://raw.githubusercontent.com/sthavishtha/list-lattice-Boltzmann-codes/master/README.md"

# Domain-scoped queries: multiphase / multi-component / phase-field / color-gradient /
# free-surface / thermal-flow / wetting. Each entry is (label, query-string).
QUERIES = [
    ("multiphase", "lattice+boltzmann+multiphase"),
    ("color_gradient", "lattice+boltzmann+color+gradient"),
    ("free_surface", "lattice+boltzmann+free+surface"),
    ("phase_field", "lattice+boltzmann+phase+field"),
    ("topic_lbm_method", "topic:lattice-boltzmann-method"),
    ("topic_lbm", "topic:lattice-boltzmann"),
    ("topic_multiphase_lbm", "topic:multiphase-lbm"),
    ("wetting", "lattice+boltzmann+wetting+contact+angle"),
    ("thermal", "lattice+boltzmann+thermal+two-phase"),
    ("shan_chen", "lattice+boltzmann+shan+chen"),
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
            # 403/429 = rate limited -> back off and retry
            if e.code in (403, 429):
                time.sleep(20 * (i + 1))
                continue
            return last
        except Exception as e:  # network hiccup
            last = (0, str(e).encode())
            time.sleep(5 * (i + 1))
    return last


def search(query: str, per_page: int = 30) -> list[dict]:
    url = f"{SEARCH}?q={query}&sort=stars&order=desc&per_page={per_page}"
    st, body = _get(url)
    if st != 200:
        print(f"  ! search {query} -> HTTP {st}", file=sys.stderr)
        return []
    d = json.loads(body)
    return d.get("items", [])


def slim(it: dict, via: str) -> dict:
    return {
        "repo": it["full_name"],
        "stars": it["stargazers_count"],
        "forks": it.get("forks_count"),
        "description": (it.get("description") or "").strip(),
        "language": it.get("language"),
        "topics": it.get("topics") or [],
        "pushed_at": it.get("pushed_at"),
        "created_at": it.get("created_at"),
        "archived": it.get("archived", False),
        "size_kb": it.get("size"),
        "license": (it.get("license") or {}).get("spdx_id"),
        "homepage": it.get("homepage"),
        "html_url": it.get("html_url"),
        "discovered_via": via,
    }


def list_repo_entries() -> dict[str, int]:
    """Return {owner/name: occurrence count} from the canonical LBM code list README."""
    st, body = _get(LIST_REPO)
    if st != 200:
        print(f"  ! list repo README -> HTTP {st}", file=sys.stderr)
        return {}
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
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--existing", default="/tmp/existing_inv.json",
                    help="JSON list of {repo,...} already in oss_inventory")
    ap.add_argument("--sleep", type=float, default=7.0, help="seconds between search calls")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    existing = {d["repo"].lower() for d in json.load(open(args.existing))}
    print(f"existing inventory: {len(existing)} repos")

    found: dict[str, dict] = {}
    per_query: dict[str, list[str]] = {}
    for label, q in QUERIES:
        items = search(q)
        names = []
        for it in items:
            if it.get("fork"):
                continue  # forks inherit upstream; inventory tracks upstreams
            s = slim(it, f"search:{label}")
            names.append(s["repo"])
            cur = found.get(s["repo"])
            if cur is None:
                found[s["repo"]] = s
            else:
                # keep max stars, merge provenance
                cur["stars"] = max(cur["stars"], s["stars"])
                cur["discovered_via"] = cur["discovered_via"] + ";" + s["discovered_via"]
        per_query[label] = names
        print(f"  {label:22s} q={q:40s} -> {len(items):3d} hits")
        time.sleep(args.sleep)

    listed = list_repo_entries()
    print(f"list-lattice-Boltzmann-codes entries: {len(listed)}")

    ranked = sorted(found.items(), key=lambda kv: -kv[1]["stars"])
    new = [(k, v) for k, v in ranked if k.lower() not in existing]
    in_inv = [k for k, _ in ranked if k.lower() in existing]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": dict(QUERIES),
        "hits_per_query": per_query,
        "existing_inventory_size": len(existing),
        "existing_inventory_hit_in_search": in_inv,
        "n_unique_outside_inventory": len(new),
        "candidates_raw": [dict(v, in_canonical_list=listed.get(v["repo"], 0)) for _, v in new],
        "canonical_list_all": listed,
    }
    path = os.path.join(OUT, "discovery_raw.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n-> {path}  ({len(new)} repos outside inventory)")
    for k, v in new[:40]:
        print(f"  {v['stars']:6d}  {k:55s} {v['pushed_at'][:10]}  listed={listed.get(k,0)}  {v['description'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())