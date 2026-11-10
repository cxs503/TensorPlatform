#!/usr/bin/env python3
"""Snapshot and machine-extract sibling node artifacts for an interop gate.

Two jobs, both provenance-first:

1. **snapshot** -- copy each declared artifact out of its source worktree into
   ``data/campaigns/<campaign>/raw/``, recording the source worktree, the git
   commit it was read at, and the sha256 of both source and snapshot.  The gate
   then verifies those sha256 pins, so a later edit of a snapshot cannot pass
   silently.
2. **extract** -- pull numbers out of prose/table artifacts (markdown reports)
   into a small JSON document, recording the *pattern* used and the matched
   lines.  Nothing is re-typed by hand: if the upstream table changes shape the
   extraction fails loudly instead of silently keeping a stale number.

Usage::

    python3 -m tools.xsolver_interop.ingest --campaign p106w2 \
        --data data/campaigns/p106w2 --repo-root .
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git_commit(worktree: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def snapshot(sources: Mapping[str, Any], data_dir: Path, *, allow_missing: bool = False) -> Dict[str, Any]:
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    for node in sources.get("sources") or []:
        worktree = Path(node["worktree"])
        commit = git_commit(worktree) if worktree.exists() else None
        for artifact in node.get("artifacts") or []:
            src = worktree / artifact["src"]
            dest = raw_dir / artifact["dest"]
            record = {
                "node": node["node"],
                "mission_id": node.get("mission_id"),
                "repo": node.get("repo"),
                "worktree": str(worktree),
                "git_commit": commit,
                "source": artifact["src"],
                "snapshot": str(Path("raw") / artifact["dest"]),
            }
            if not src.exists():
                record["status"] = "missing"
                record["reason"] = "source artifact not found in worktree"
                errors.append(f"{node['node']}: {artifact['src']} missing")
                records.append(record)
                continue
            payload = src.read_bytes()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
            record.update({
                "status": "snapshotted",
                "source_sha256": sha256_bytes(payload),
                "snapshot_sha256": sha256_bytes(dest.read_bytes()),
                "bytes": len(payload),
            })
            records.append(record)
    for node in sources.get("absent") or []:
        records.append({
            "node": node["node"], "mission_id": node.get("mission_id"), "repo": node.get("repo"),
            "status": "absent", "reason": node.get("reason"),
            "expected_snapshot": str(Path("raw") / node["expected_raw"]) if node.get("expected_raw") else None,
        })
    provenance = {"campaign": sources.get("campaign"), "records": records}
    (data_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if errors and not allow_missing:
        raise SystemExit("ingest failed (fail-closed): " + "; ".join(errors))
    return provenance


def extract(spec: Mapping[str, Any], data_dir: Path) -> Dict[str, Any]:
    """Extract declared tables and prose numbers from a markdown snapshot."""
    produced: Dict[str, Any] = {}
    for name, recipe in (spec.get("documents") or {}).items():
        source = data_dir / "raw" / recipe["source"]
        if not source.exists():
            raise SystemExit(f"extract failed (fail-closed): {source} missing")
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        doc: Dict[str, Any] = {"_source": str(Path("raw") / recipe["source"]),
                               "_source_sha256": sha256_bytes(text.encode("utf-8")),
                               "_patterns": {}}
        for table in recipe.get("tables") or []:
            pattern = re.compile(table["row_pattern"])
            start = 0
            heading = table.get("require_heading")
            if heading:
                start = next((i for i, line in enumerate(lines) if heading in line), None)
                if start is None:
                    raise SystemExit(f"extract failed: heading {heading!r} absent in {source}")
            columns: Dict[str, List[float]] = {key: [] for key in table["columns"]}
            matched_lines: List[str] = []
            for line in lines[start:]:
                match = pattern.match(line.strip())
                if not match:
                    continue
                for key in table["columns"]:
                    columns[key].append(float(match.group(key)))
                matched_lines.append(line.strip())
            if not columns or any(not v for v in columns.values()):
                raise SystemExit(f"extract failed: table {table['name']!r} matched nothing in {source}")
            doc.update(columns)
            doc["_patterns"][table["name"]] = {"regex": table["row_pattern"],
                                               "heading": heading,
                                               "matched_lines": matched_lines}
        for prose in recipe.get("prose") or []:
            match = re.search(prose["pattern"], text)
            if not match:
                raise SystemExit(f"extract failed: prose {prose['name']!r} unmatched in {source}")
            for index, key in enumerate(prose["keys"], start=1):
                doc[key] = float(match.group(index))
            doc["_patterns"][prose["name"]] = {"regex": prose["pattern"],
                                               "matched_text": match.group(0)}
        out = data_dir / "raw" / recipe["out"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        produced[name] = {"path": str(out.relative_to(data_dir)), "sha256": sha256_bytes(out.read_bytes())}
    (data_dir / "extract_report.json").write_text(
        json.dumps(produced, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return produced


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="snapshot + extract sibling artifacts")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--data", required=True, help="data/campaigns/<campaign> directory")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)
    data_dir = Path(args.data)
    sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))
    provenance = snapshot(sources, data_dir, allow_missing=args.allow_missing)
    spec_path = data_dir / "extract_spec.json"
    produced = extract(json.loads(spec_path.read_text(encoding="utf-8")), data_dir) if spec_path.exists() else {}
    snaphotted = sum(1 for r in provenance["records"] if r.get("status") == "snapshotted")
    print(f"snapshot: {snaphotted} artifact(s); extracted: {len(produced)} document(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())