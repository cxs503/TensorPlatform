"""Resolve declaration sources into hash-pinned evidence locators.

The declaration *content* is domain data and lives in
``artifacts/evidence_second_impl/declaration_sources.json``.  This module only
resolves each declared needle to a 1-based line number, hashes that line and
writes the locator artifact that :mod:`independence` re-verifies offline.

Ambiguity is an error: a needle that does not match, or that matches more than
once, aborts the build rather than silently pinning the wrong line.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from . import independence
from .model import DeclarationRecord, EvidenceLocator, ReferenceSource, line_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCES_REL = "artifacts/evidence_second_impl/declaration_sources.json"
OUT_REL = independence.DEFAULT_DECLARATIONS_REL


class LocatorResolutionError(RuntimeError):
    pass


def resolve_locator(raw: Dict[str, Any], root: Path | str = ROOT) -> EvidenceLocator:
    root = Path(root)
    path = Path(raw["path"])
    full = path if path.is_absolute() else root / path
    if not full.is_file():
        raise LocatorResolutionError(f"source file not found: {raw['path']}")
    lines = full.read_text(encoding="utf-8").splitlines()
    needle = str(raw["needle"])
    hits = [i for i, text in enumerate(lines, start=1) if needle in text]
    if not hits:
        raise LocatorResolutionError(f"needle not found in {raw['path']}: {needle!r}")
    if len(hits) > 1:
        raise LocatorResolutionError(
            f"needle matches {len(hits)} lines in {raw['path']}: {needle!r}")
    line_no = hits[0]
    return EvidenceLocator(path=str(raw["path"]), line=line_no,
                           line_sha256=line_sha256(lines[line_no - 1]),
                           note=str(raw.get("note", "")))


def build(sources_path: Path | str = ROOT / SOURCES_REL,
          out_path: Path | str = ROOT / OUT_REL,
          root: Path | str = ROOT) -> Dict[str, Any]:
    raw = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    records: List[DeclarationRecord] = []
    for item in raw["sources"]:
        locs = tuple(resolve_locator(x, root) for x in item.get("locators", []))
        corr_raw = item.get("independent_corroboration")
        corr = resolve_locator(corr_raw, root) if corr_raw else None
        records.append(DeclarationRecord(
            capability_id=str(item["capability_id"]),
            graded_grade=str(item["graded_grade"]),
            reference_source=ReferenceSource(str(item["reference_source"])),
            locators=locs,
            independent_corroboration=corr,
            reason=str(item.get("reason", "")),
        ))
    digest = independence.save_declarations(
        out_path, records,
        extra={"audit_snapshot": raw.get("audit_snapshot", {}),
               "resolved_from": _rel(sources_path, root)})
    return {"n_declarations": len(records), "digest": digest,
            "out": _rel(out_path, root)}


def _rel(path: Path | str, root: Path | str) -> str:
    try:
        return str(Path(path).relative_to(Path(root)))
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build hash-pinned declaration locators")
    ap.add_argument("--sources", default=str(ROOT / SOURCES_REL))
    ap.add_argument("--out", default=str(ROOT / OUT_REL))
    args = ap.parse_args(argv)
    result = build(args.sources, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())