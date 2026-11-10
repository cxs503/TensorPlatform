"""Zero-domain-semantics scan (thin adapter over the platform leak scanner).

The platform rule is that mechanism code, tests and fixtures must not contain
domain lexicon: domain words live in a data file and are injected.  This
adapter points the existing scanner at the new governance-suite paths and
returns a compact verdict, so a maturation report can state "0 hits" with the
exact scanned file list and scanner revision instead of a hand claim.

The scanner itself (:mod:`tools.audit.domain_leak_scan`) is upstream code; if
its API drifts only this adapter changes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from tools.audit import domain_leak_scan

_REPO_ROOT = Path(domain_leak_scan.__file__).resolve().parents[2]
_DEFAULT_DENYLIST = _REPO_ROOT / "config" / "domain_word_denylist.json"


def scan_paths(paths: Sequence[str], repo_root: str | Path, denylist: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    denylist_path = Path(denylist) if denylist else _DEFAULT_DENYLIST
    result = domain_leak_scan.scan(paths=list(paths), denylist_path=denylist_path, root=root)
    scanner_path = Path(domain_leak_scan.__file__)
    return {
        "schema": "domain-semantics-scan/1.0",
        "scanner": "tools.audit.domain_leak_scan",
        "scanner_sha256": "sha256:" + hashlib.sha256(scanner_path.read_bytes()).hexdigest(),
        "denylist": str(denylist_path.relative_to(root)) if denylist_path.is_relative_to(root) else str(denylist_path),
        "paths": list(paths),
        "files_scanned": result.get("files_scanned", []),
        "hits": result.get("hits", []),
        "n_hits": len(result.get("hits", [])),
        "verdict": "PASS" if not result.get("hits") else "FAIL",
    }


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--paths", nargs="+", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    out = scan_paths(args.paths, args.root)
    path = Path(args.json_out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n_hits", "verdict", "files_scanned")}, indent=2)[:2000])
    return 0 if out["n_hits"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())