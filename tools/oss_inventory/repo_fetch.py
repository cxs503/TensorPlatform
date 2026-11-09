#!/usr/bin/env python3
"""Fetch upstream repository files for OSS inspection with rate-limit fallback.

Motivation
----------
Inventory waves need `path:line` pointers into third-party sources.  The two
obvious routes both fail in practice on our fleet:

* the unauthenticated REST API allows only 60 requests/hour *per egress IP*,
  and several inventory agents share one IP, so ``contents`` calls die with
  HTTP 403 mid-audit;
* ``codeload.github.com`` archives support no ``Range`` header, so a large
  repository cannot be resumed after a shell timeout.

A blobless partial clone (``--filter=blob:none --depth 1 --no-checkout``)
turns out to be the cheapest reliable route: all trees come in one small pack,
and ``git checkout <ref> -- <pathspec>`` then batches the needed blobs into a
single promisor fetch.  This module wraps that route plus the two fallbacks
behind one domain-neutral interface so future waves do not re-derive it.

Strategies (per repository, first viable wins)
----------------------------------------------
``api``    REST ``/contents``: one call per file; only usable for a few files.
``clone``  blobless shallow clone + targeted ``git checkout``; preferred.
``tar``    single-shot archive stream; last resort (no resume support).

CLI
---
    python -m tools.oss_inventory.repo_fetch --repo owner/name --stage /tmp/x \
        --paths paths.txt --ref master --write-manifest /tmp/x/manifest.json
    python -m tools.oss_inventory.repo_fetch --repo owner/name --stage /tmp/x --list

Exit code is 1 on usage/transport failure.  Nothing here talks to a specific
project: repository names, refs and paths are always caller supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"
CLONE_ROOT = "https://github.com"
TAR_ROOT = "https://codeload.github.com"
USER_AGENT = "tensorplatform-oss-inventory"

REPO_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")


class RepoFetchError(RuntimeError):
    """Raised when a repository cannot be identified or transported."""


class RateLimitError(RepoFetchError):
    """HTTP 403 raised by the REST API; ``remaining`` mirrors the response."""

    def __init__(self, message: str, remaining: int = 0):
        super().__init__(message)
        self.remaining = remaining


def parse_repo(repo: str) -> tuple[str, str]:
    """Split ``owner/name`` and reject anything that is not a plain repo slug.

    Rejecting early matters: these values are interpolated into URLs and git
    arguments, so a stray ``--upload-pack=`` must never reach argv.
    """
    repo = (repo or "").strip().strip("/")
    if not REPO_RE.match(repo):
        raise RepoFetchError(f"invalid repository slug: {repo!r}")
    owner, name = repo.split("/")
    for part in (owner, name):
        if part.startswith("-") or ".." in part:
            raise RepoFetchError(f"suspicious repository component: {part!r}")
    return owner, name


def _request(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - transport path
        if exc.code == 403:
            raise RateLimitError(str(exc), remaining=0) from exc
        raise RepoFetchError(f"GET {url} failed: {exc}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - transport path
        raise RepoFetchError(f"GET {url} failed: {exc}") from exc


def api_repo_meta(repo: str) -> dict:
    """Repository metadata via REST (1 request)."""
    owner, name = parse_repo(repo)
    return json.loads(_request(f"{API_ROOT}/repos/{owner}/{name}"))


def clone_blobless(repo: str, stage: str | Path, ref: str = "") -> Path:
    """Create (or reuse) a blobless, no-checkout clone and return its path."""
    owner, name = parse_repo(repo)
    dest = Path(stage) / f"{name}.git"
    if (dest / ".git").exists() or (dest / "HEAD").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--filter=blob:none", "--no-checkout"]
    if ref:
        cmd += ["--depth", "1", "--branch", ref]
    cmd += [f"{CLONE_ROOT}/{owner}/{name}", str(dest)]
    _git(cmd, cwd=None)
    return dest


def _git(args: list[str], cwd: str | Path | None) -> str:
    env = {"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RepoFetchError(f"{' '.join(args)} -> {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def list_paths(clone_dir: str | Path, ref: str = "HEAD") -> list[str]:
    """All tracked paths at ``ref`` (works offline once the clone exists)."""
    out = _git(["git", "ls-tree", "-r", "--name-only", ref], cwd=str(clone_dir))
    return [line for line in out.splitlines() if line.strip()]


def checkout_paths(clone_dir: str | Path, paths: list[str], ref: str = "HEAD") -> list[str]:
    """Materialise ``paths`` (files or directories); return those now on disk.

    Missing pathspecs are skipped instead of aborting the batch, because
    upstream layouts move between waves.
    """
    clone = Path(clone_dir)
    tracked = list_paths(clone, ref)
    tracked_set = set(tracked)
    wanted = [p for p in paths
              if p and (p in tracked_set or any(t.startswith(p.rstrip("/") + "/") for t in tracked))]
    if not wanted:
        return []
    _git(["git", "checkout", ref, "--", *wanted], cwd=str(clone))
    return [p for p in wanted if (clone / p).exists()]


def fetch_via_api(repo: str, paths: list[str], stage: str | Path, ref: str = "") -> list[str]:
    """Fallback: one REST request per file; ``RateLimitError`` propagates."""
    owner, name = parse_repo(repo)
    suffix = f"?ref={ref}" if ref else ""
    written: list[str] = []
    for path in paths:
        import base64

        payload = json.loads(_request(f"{API_ROOT}/repos/{owner}/{name}/contents/{path}{suffix}"))
        if payload.get("type") != "file":
            continue
        target = Path(stage) / "api" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(payload["content"]))
        written.append(str(target))
    return written


def manifest(root: str | Path, members: list[str]) -> list[dict]:
    """Size + sha256 for fetched files (provenance for the audit report)."""
    root = Path(root)
    rows: list[dict] = []
    for rel in members:
        f = root / rel
        if not f.is_file():
            continue
        data = f.read_bytes()
        rows.append({"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--stage", required=True, help="staging directory")
    ap.add_argument("--ref", default="", help="branch/tag/sha (default: default branch)")
    ap.add_argument("--paths", help="file with one path per line")
    ap.add_argument("--list", action="store_true", help="print tracked paths and exit")
    ap.add_argument("--strategy", choices=("auto", "clone", "api"), default="auto")
    ap.add_argument("--write-manifest", help="write sha256 manifest JSON here")
    args = ap.parse_args(argv)

    try:
        repo_meta = None
        try:
            repo_meta = api_repo_meta(args.repo)
        except RateLimitError:
            repo_meta = {"note": "REST rate limited; metadata withheld"}
        if args.list:
            clone = clone_blobless(args.repo, args.stage, args.ref or repo_meta.get("default_branch", ""))
            for path in list_paths(clone, args.ref or "HEAD"):
                print(path)
            return 0
        if not args.paths:
            ap.error("--paths is required unless --list is given")
        wanted = [line.strip() for line in Path(args.paths).read_text().splitlines() if line.strip()]
        if args.strategy in ("auto", "clone"):
            try:
                clone = clone_blobless(args.repo, args.stage, args.ref or repo_meta.get("default_branch", ""))
                got = checkout_paths(clone, wanted, args.ref or "HEAD")
            except RepoFetchError as exc:
                if args.strategy == "clone":
                    raise
                print(f"clone strategy failed ({exc}); falling back to REST", file=sys.stderr)
                got = fetch_via_api(args.repo, wanted, args.stage, args.ref)
        else:
            got = fetch_via_api(args.repo, wanted, args.stage, args.ref)
        if args.write_manifest:
            root = Path(args.stage)
            rows = manifest(root, got)
            Path(args.write_manifest).write_text(json.dumps(
                {"repo": args.repo, "ref": args.ref, "files": rows}, indent=1))
        print(json.dumps({"repo": args.repo, "resolved": len(got), "requested": len(wanted)}))
        return 0
    except RepoFetchError as exc:
        print(f"repo_fetch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())