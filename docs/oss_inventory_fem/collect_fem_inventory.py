#!/usr/bin/env python3
"""Open-source FEM inventory collector (mission m8692, anchor FEAT-OSS-INVENTORY-FEM).

Zero-memory protocol: every field written by this script comes from a live GitHub
REST response captured in this run. Nothing is hand-typed repo metadata.

Stages
  1. search : >= 8 REST search queries (repositories) -> raw candidate pool
  2. detail : /repos/{full_name} for every candidate  -> license / topics / size
  3. commit : /repos/{full_name}/commits?per_page=1    -> true last commit date
  4. readme : first N chars of README (base64 decoded) -> tier evidence, quoted

Usage:
  python3 collect_fem_inventory.py --out raw            # full collection
  python3 collect_fem_inventory.py --out raw --stage search

Token: read from $GITHUB_TOKEN, else from ~/.gitcred (never printed, never stored).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = "tensorplatform-oss-inventory/1.0"


def _token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    for path in (os.path.expanduser("~/.gitcred"), os.path.expanduser("~/.git-credentials")):
        try:
            raw = open(path, encoding="utf-8").read().strip()
        except OSError:
            continue
        m = re.search(r"(github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+)", raw)
        if m:
            return m.group(1)
    return None


TOKEN = _token()
CALLS: list[dict] = []


def get(path: str, params: dict | None = None, tries: int = 3):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=40) as fh:
                body = json.load(fh)
            CALLS.append({"path": path, "params": params, "status": 200, "ts": _now()})
            return body
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            CALLS.append({"path": path, "params": params, "status": exc.code, "ts": _now()})
            if exc.code in (403, 429):
                time.sleep(20 * (attempt + 1))
                continue
            if exc.code in (404, 451):
                return None
            time.sleep(2)
        except Exception as exc:  # noqa: BLE001 - network flake
            last = f"{type(exc).__name__}"
            time.sleep(3)
    print(f"  ! give up {path} ({last})", file=sys.stderr)
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- search rounds
# 8 independent rounds; each is a real REST call whose hits are kept in raw/.
SEARCH_ROUNDS = [
    ("q1_core_solver_cpp", "finite element solver language:C++ stars:>200", 25),
    ("q2_fem_topic", "topic:finite-element stars:>300", 25),
    ("q3_fem_library_python", "finite element language:Python stars:>100", 25),
    ("q4_elasticity_multiphysics", "multiphysics finite element analysis in:description stars:>80", 25),
    ("q5_differentiable", "differentiable finite element in:name,description,readme stars:>10", 25),
    ("q6_jax_torch_fem", "jax OR pytorch finite element solver in:name,description stars:>20", 25),
    ("q7_iga_advanced", "isogeometric OR mixed finite element OR discontinuous galerkin in:name,description stars:>50", 25),
    ("q8_structural_engineering", "structural analysis finite element framework in:description stars:>150", 25),
    ("q9_gpu_hpc_fem", "GPU accelerated finite element in:description stars:>20", 25),
    ("q10_contact_fracture", "phase field fracture OR contact mechanics finite element in:description stars:>20", 25),
]


def stage_search() -> dict:
    out = {}
    for key, q, per_page in SEARCH_ROUNDS:
        print(f"[search] {key}: {q}")
        body = get("/search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": per_page})
        items = (body or {}).get("items", [])
        out[key] = {
            "query": q,
            "total_count": (body or {}).get("total_count"),
            "collected": len(items),
            "captured_at": _now(),
            "items": [
                {
                    "repo": it["full_name"],
                    "stars": it["stargazers_count"],
                    "lang": it.get("language"),
                    "license": ((it.get("license") or {}).get("spdx_id")),
                    "pushed_at": it.get("pushed_at"),
                    "archived": it.get("archived"),
                    "description": it.get("description"),
                    "topics": it.get("topics", []),
                }
                for it in items
            ],
        }
        time.sleep(3)  # search quota: 30 req/min authenticated
    return out


def stage_details(repos: list[str]) -> dict:
    out = {}
    for name in repos:
        print(f"[detail] {name}")
        d = get(f"/repos/{name}")
        if d is None:
            time.sleep(1)
            continue
        out[name] = {
            "full_name": d.get("full_name"),
            "description": d.get("description"),
            "homepage": d.get("homepage"),
            "stars": d.get("stargazers_count"),
            "forks": d.get("forks_count"),
            "open_issues": d.get("open_issues_count"),
            "watchers": d.get("subscribers_count"),
            "lang": d.get("language"),
            "license": ((d.get("license") or {}).get("spdx_id")),
            "license_name": ((d.get("license") or {}).get("name")),
            "topics": d.get("topics", []),
            "created_at": d.get("created_at"),
            "pushed_at": d.get("pushed_at"),
            "updated_at": d.get("updated_at"),
            "archived": d.get("archived"),
            "size_kb": d.get("size"),
            "default_branch": d.get("default_branch"),
            "html_url": d.get("html_url"),
            "fetched_at": _now(),
        }
    return out


def stage_commit(repos: list[str]) -> dict:
    out = {}
    for name in repos:
        c = get(f"/repos/{name}/commits", {"per_page": 1})
        if not c:
            out[name] = {"last_commit": None, "fetched_at": _now()}
            continue
        item = c[0]
        out[name] = {
            "last_commit": (item.get("commit", {}).get("committer", {}) or {}).get("date"),
            "last_commit_sha": (item.get("sha") or "")[:12],
            "last_commit_msg": ((item.get("commit", {}).get("message") or "").splitlines() or [""])[0][:120],
            "fetched_at": _now(),
        }
    return out


def stage_readme(repos: list[str], limit: int = 2600) -> dict:
    out = {}
    for name in repos:
        r = get(f"/repos/{name}/readme")
        if not r or r.get("encoding") != "base64":
            out[name] = {"readme_head": None, "bytes": None}
            continue
        try:
            txt = base64.b64decode(r["content"]).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            txt = ""
        txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", txt)  # strip badge images
        txt = re.sub(r"<[^>]+>", " ", txt)
        out[name] = {
            "path": r.get("path"),
            "bytes": r.get("size"),
            "readme_head": " ".join(txt.split())[:limit],
            "fetched_at": _now(),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="raw")
    ap.add_argument("--stage", default="all",
                    choices=["all", "search", "detail", "commit", "readme"])
    ap.add_argument("--repos", default="", help="comma separated list (detail/commit/readme stages)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    def dump(name: str, obj) -> None:
        p = os.path.join(args.out, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        print(f"  -> {p}")

    if args.stage in ("all", "search"):
        dump("search_rounds.json", {"generated_at": _now(), "authenticated": bool(TOKEN),
                                    "rounds": stage_search()})

    repos = [r for r in args.repos.split(",") if r]
    if args.stage in ("all", "detail") and repos:
        dump("repo_details.json", {"generated_at": _now(), "repos": stage_details(repos)})
    if args.stage in ("all", "commit") and repos:
        dump("repo_commits.json", {"generated_at": _now(), "repos": stage_commit(repos)})
    if args.stage in ("all", "readme") and repos:
        dump("repo_readme.json", {"generated_at": _now(), "repos": stage_readme(repos)})

    # append-only provenance log: never drop calls captured by an earlier stage run
    logp = os.path.join(args.out, "api_calls.json")
    prev = []
    if os.path.exists(logp):
        try:
            prev = json.load(open(logp, encoding="utf-8")).get("calls", [])
        except Exception:  # noqa: BLE001
            prev = []
    merged = prev + CALLS
    dump("api_calls.json", {"generated_at": _now(), "authenticated": bool(TOKEN),
                            "n_calls_total": len(merged), "n_calls_previous": len(prev),
                            "calls": merged})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())