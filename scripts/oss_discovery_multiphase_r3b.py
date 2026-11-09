#!/usr/bin/env python3
"""Round 3b (FEAT-OSS-INVENTORY-LBM): phrase-quoted multiphase queries + canonical
LBM code-list README (default branch auto-detected).

Round-3a multi-term AND queries returned 0 hits for 4/6 patterns because GitHub
flat queries require every token; phrase quoting widens recall.

Output: artifacts/m8307/discovery_raw3.json
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
LIST_REPO = "sthavishtha/list-lattice-Boltzmann-codes"

QUERIES = [
    ("openlb", "openlb"),
    ("free_energy_phrase", "%22free+energy%22+lattice+boltzmann"),
    ("wetting_phrase", "%22contact+angle%22+lattice+boltzmann"),
    ("density_ratio_phrase", "%22high+density+ratio%22+lattice+boltzmann"),
]


def _get(url: str, tries: int = 3, raw: bool = False) -> tuple[int, bytes]:
    last = (0, b"")
    hdr = {"User-Agent": UA["User-Agent"]} if raw else UA
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, e.read() or b"")
            if e.code in (403, 429):
                time.sleep(15 * (i + 1))
                continue
            return last
        except Exception as e:
            last = (0, str(e).encode())
            time.sleep(4)
    return last


def main() -> int:
    existing = {d["repo"].lower() for d in json.load(open("/tmp/existing_inv.json"))}
    found: dict[str, dict] = {}
    per_query: dict[str, int] = {}
    for label, q in QUERIES:
        st, body = _get(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=30")
        items = json.loads(body).get("items", []) if st == 200 else []
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
                "discovered_via": f"search3b:{label}",
            }
            cur = found.setdefault(s["repo"], s)
            if cur is not s:
                cur["stars"] = max(cur["stars"], s["stars"])
                cur["discovered_via"] += ";" + s["discovered_via"]
        print(f"  {label:22s} [{st}] -> {len(items):3d}", flush=True)
        time.sleep(5)

    st, body = _get(f"https://api.github.com/repos/{LIST_REPO}")
    branch = json.loads(body).get("default_branch", "master") if st == 200 else None
    listed: dict[str, int] = {}
    url = ""
    if branch:
        for fn in ("README.md", "readme.md"):
            u = f"https://raw.githubusercontent.com/{LIST_REPO}/{branch}/{fn}"
            st2, body2 = _get(u, raw=True)
            if st2 == 200:
                text = body2.decode("utf-8", "replace")
                url = u
                for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text):
                    owner, name = m.group(1), m.group(2)
                    if owner.lower() in {"sthavishtha", "github", "www"}:
                        continue
                    name = name.removesuffix(".git")
                    if name in {"tree", "blob", "issues", "wiki"}:
                        continue
                    key = f"{owner}/{name}"
                    listed[key] = listed.get(key, 0) + 1
                print(f"  list README {u} -> {len(text)}B, {len(listed)} links", flush=True)
                break
    if not url:
        print("  ! list README unreachable", flush=True)

    new = [(k, v) for k, v in sorted(found.items(), key=lambda kv: -kv[1]["stars"])
           if k.lower() not in existing]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": dict(QUERIES), "hits_per_query": per_query,
        "canonical_list_default_branch": branch, "canonical_list_url": url,
        "canonical_list_entries": listed,
        "n_unique_outside_inventory": len(new),
        "candidates_raw": [dict(v, in_canonical_list=listed.get(v["repo"], 0)) for _, v in new],
    }
    p = os.path.join(OUT, "discovery_raw3.json")
    json.dump(payload, open(p, "w"), indent=2, ensure_ascii=False)
    print(f"\n-> {p} ({len(new)} outside inventory)")
    for k, v in new[:20]:
        print(f"  {v['stars']:6d} {k:46s} {v['pushed_at'][:10]} L={listed.get(k,0)} {v['description'][:55]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())