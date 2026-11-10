#!/usr/bin/env python3
"""Placeholder rendering + render manifest (P251-W2-N04).

Publishing is a *substitution*, not a transcription: the template handed to the
renderer contains ``{{prov:key}}`` placeholders and no result numbers, and the
renderer writes the value it reads from the sidecar.  Because the author never
types the number, the "number typed by hand and later drifted" failure mode is
removed at the source rather than detected afterwards.

Two outputs are produced per render:

* the rendered markdown (the published body);
* a **render manifest** recording, for every substitution, the key and the byte
  span ``[start, end)`` the value occupies, plus the SHA-256 of the rendered
  bytes.  The manifest is what makes the gate cheap and exact: if the published
  document's digest still matches the manifest, the spans are guaranteed valid,
  so "is this number bound?" is a span lookup instead of a heuristic.

Usage
-----
    python3 -m tools.prov_gate.render --template t.md --sidecar sidecar.json \
        --out published.md --manifest manifest.json

Exit code 0 = rendered, 1 = strict render refused (missing key), 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Sequence

from .sidecar import (
    PLACEHOLDER_RE,
    SCHEMA_VERSION as SIDECAR_SCHEMA_VERSION,
    Sidecar,
    render_record_text,
    sha256_text,
    utc_now,
)

MANIFEST_SCHEMA_VERSION = "report-prov-manifest/1"


class MissingKeyError(RuntimeError):
    """Raised by a strict render when the template references keys the sidecar
    does not define.  A strict render never publishes a partial document."""

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = list(missing)
        super().__init__(f"sidecar missing key(s): {', '.join(self.missing)}")


class AmbiguousJoinError(RuntimeError):
    """Raised when a substitution is glued to an adjacent digit, which would
    create a numeric literal attributable to no single key."""

    def __init__(self, keys: Sequence[str]) -> None:
        self.keys = list(keys)
        super().__init__(f"placeholder(s) glued to an adjacent digit: {', '.join(self.keys)}")


@dataclass
class RenderEntry:
    key: str
    start: int
    end: int
    text: str
    kind: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RenderResult:
    markdown: str
    entries: list[RenderEntry]
    missing: list[str]
    rendered_sha256: str
    template_sha256: str
    generated_at: str
    sidecar_schema_version: str = SIDECAR_SCHEMA_VERSION
    ambiguous_joins: list[str] = field(default_factory=list)

    def to_manifest(self) -> dict:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "sidecar_schema_version": self.sidecar_schema_version,
            "template_sha256": self.template_sha256,
            "rendered_sha256": self.rendered_sha256,
            "entry_count": len(self.entries),
            "ambiguous_joins": self.ambiguous_joins,
            "entries": [e.to_dict() for e in self.entries],
        }


def render(
    template: str,
    sidecar: Sidecar,
    strict: bool = True,
    generated_at: str | None = None,
) -> RenderResult:
    """Substitute every ``{{prov:key}}`` in ``template`` with the sidecar value.

    ``strict=True`` (default) refuses to produce a document when any key is
    missing; ``strict=False`` leaves the placeholder in place and records the
    key under ``missing`` so the gate can report it (never silently blank).
    """
    out: list[str] = []
    entries: list[RenderEntry] = []
    missing: list[str] = []
    cursor = 0
    length = 0
    for m in PLACEHOLDER_RE.finditer(template):
        head = template[cursor:m.start()]
        out.append(head)
        length += len(head)
        key = m.group(1)
        rec = sidecar.get(key)
        if rec is None:
            # never blank a value: the placeholder survives verbatim so the
            # gate can name the missing key instead of showing an empty cell
            if key not in missing:
                missing.append(key)
            out.append(m.group(0))
            length += len(m.group(0))
        else:
            text = render_record_text(rec)
            entries.append(RenderEntry(key=key, start=length, end=length + len(text),
                                       text=text, kind=rec.kind))
            out.append(text)
            length += len(text)
        cursor = m.end()
    tail = template[cursor:]
    out.append(tail)
    markdown = "".join(out)
    if strict and missing:
        raise MissingKeyError(missing)
    # a substitution that glues a digit onto a neighbouring digit produces a
    # literal nobody can attribute to a single key ("1.5" + "2.5" -> "1.52.5")
    ambiguous: list[str] = []
    for e in entries:
        before = markdown[e.start - 1] if e.start > 0 else ""
        after = markdown[e.end] if e.end < len(markdown) else ""
        if before.isdigit() or after.isdigit():
            ambiguous.append(e.key)
    if ambiguous and strict:
        raise AmbiguousJoinError(ambiguous)
    return RenderResult(
        markdown=markdown,
        entries=entries,
        missing=missing,
        rendered_sha256=sha256_text(markdown),
        template_sha256=sha256_text(template),
        generated_at=generated_at or utc_now(),
        ambiguous_joins=ambiguous,
    )


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def manifest_entries(manifest: dict | None) -> list[dict]:
    if not manifest:
        return []
    return list(manifest.get("entries", []))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="render a report template from its provenance sidecar")
    ap.add_argument("--template", required=True)
    ap.add_argument("--sidecar", required=True)
    ap.add_argument("--out", default=None, help="rendered markdown path (default: stdout)")
    ap.add_argument("--manifest", default=None, help="render manifest output path")
    ap.add_argument("--lenient", action="store_true",
                    help="render even when keys are missing (placeholders survive)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    template = Path(args.template).read_text(encoding="utf-8")
    sidecar = Sidecar.load(args.sidecar)
    try:
        res = render(template, sidecar, strict=not args.lenient)
    except MissingKeyError as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(res.markdown, encoding="utf-8")
    else:
        sys.stdout.write(res.markdown)
    if args.manifest:
        Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest).write_text(
            json.dumps(res.to_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())