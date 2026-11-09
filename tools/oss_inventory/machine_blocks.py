#!/usr/bin/env python3
"""Parse and validate ``[OSS-INV]`` / ``[OSS-SOL]`` machine blocks.

Background
----------
The OSS inventory campaign encodes every deep-dive result as two kinds of
machine-readable blocks embedded in markdown reports::

    [OSS-INV]
    repo: owner/name
    tier: 1
    ...
    [/OSS-INV]

    [OSS-SOL]
    repo: owner/name
    slug: GAP-XXX-YYY
    tag: short-method-tag
    method: one-paragraph method description
    files: path/To/File.c.Rt:580, path/To/File.c.Rt:586-599
    conventions: symbols -> meaning
    transfer: adapt | reference | fits | none
    [/OSS-SOL]

These blocks are the *ingestion contract* between an inventory report and the
``oss_inventory`` / ``oss_solutions`` tables.  Historically the blocks were
copied into Postgres by hand, which let two classes of defect through:

* vocabulary drift in ``transfer`` (``reject`` and ``reject-as-is`` appeared
  although only four values are defined), and
* anchor-namespace drift in ``slug`` (free-form area labels such as
  ``"分布式多卡"`` stored next to real ``GAP-...`` anchors, which silently
  breaks "consume knowledge by anchor" queries).

This module is deliberately domain-free: it knows the *shape* of a block, not
the vocabulary of any particular numerical method.  Anchor prefixes are passed
in by the caller.

CLI
---
    python -m tools.oss_inventory.machine_blocks docs/oss_inventory/*.md
    python -m tools.oss_inventory.machine_blocks --json --strict docs/**/*.md

Exit code is 1 when violations are found and ``--strict`` is given.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

BLOCK_RE = re.compile(r"^\[(OSS-INV|OSS-SOL)\]\s*$(.*?)^\[/\1\]\s*$", re.M | re.S)
KV_RE = re.compile(r"^([a-z_]+):\s*(.*)$")

INV_REQUIRED = ("repo", "lang", "license", "methods", "summary")
SOL_REQUIRED = ("repo", "slug", "tag", "method", "files", "conventions", "transfer")
INV_KEYS = ("repo", "lang", "license", "methods", "multiphase", "boundary",
            "gpu", "summary")
SOL_KEYS = ("repo", "slug", "tag", "method", "files", "conventions",
            "transfer", "evidence")

#: Transfer taxonomy, frozen with the campaign (see docs/oss-to-tk-playbook.md §3).
TRANSFER_VALUES = ("adapt", "reference", "fits", "none")

#: A file pointer must carry a line or line-range, e.g. ``a/b.c:580`` or
#: ``a/b.c:586-599``.  Bare file names are not reproducible pointers.
POINTER_RE = re.compile(r"[\w./\-]+\.[A-Za-z0-9_]+:\d+(?:-\d+)?")

SLUG_GAP_RE = re.compile(r"^GAP-[A-Z0-9][A-Z0-9-]*$")
SLUG_NEW_RE = re.compile(r"^NEW:[A-Z0-9][A-Z0-9-]*$")


@dataclass
class Block:
    kind: str                       # "OSS-INV" | "OSS-SOL"
    source: str                     # file:line of the opening marker
    fields: dict[str, str] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)

    @property
    def repo(self) -> str:
        return self.fields.get("repo", "")

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def parse_text(text: str, source: str = "<text>") -> list[Block]:
    """Extract all machine blocks from ``text`` in file order."""
    blocks: list[Block] = []
    for m in BLOCK_RE.finditer(text):
        kind, body = m.group(1), m.group(2)
        line_no = text[: m.start()].count("\n") + 1
        blk = Block(kind=kind, source=f"{source}:{line_no}")
        for raw in body.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            kv = KV_RE.match(line)
            if not kv:
                # continuation of the previous value (wrapped long sentence)
                for key, val in list(blk.fields.items()):
                    if val.endswith((",", "，")) or len(val) > 0:
                        continue
                continue
            key, val = kv.group(1), kv.group(2).strip()
            if key in blk.fields:
                blk.duplicates.append(key)
                blk.fields[key] = blk.fields[key] + ", " + val
            else:
                blk.fields[key] = val
        blocks.append(blk)
    return blocks


def parse_file(path: str | Path) -> list[Block]:
    p = Path(path)
    return parse_text(p.read_text(encoding="utf-8", errors="replace"), str(p))


def classify_slug(slug: str, anchor_prefix: str = "GAP-") -> str:
    """Return ``gap`` | ``namespaced`` | ``free-label`` for a slug value."""
    s = (slug or "").strip()
    if not s:
        return "missing"
    if anchor_prefix and s.startswith(anchor_prefix) and len(s) > len(anchor_prefix):
        return "gap"
    if SLUG_NEW_RE.match(s):
        return "namespaced"
    if s.isascii() and re.match(r"^[A-Za-z0-9][\w:.\-/]*$", s):
        return "namespaced"
    return "free-label"


def validate(blocks: list[Block], anchor_prefix: str = "GAP-") -> list[dict]:
    """Return a list of violation dicts (empty list == all blocks well formed)."""
    out: list[dict] = []

    def bad(blk: Block, code: str, detail: str) -> None:
        out.append({"source": blk.source, "kind": blk.kind, "repo": blk.repo,
                    "code": code, "detail": detail})

    for blk in blocks:
        required = INV_REQUIRED if blk.kind == "OSS-INV" else SOL_REQUIRED
        allowed = INV_KEYS if blk.kind == "OSS-INV" else SOL_KEYS

        for key in required:
            if not blk.fields.get(key, "").strip():
                bad(blk, "missing_field", key)
        for key in blk.fields:
            if key not in allowed:
                bad(blk, "unknown_field", key)
        if blk.duplicates:
            bad(blk, "duplicate_key", ",".join(sorted(set(blk.duplicates))))
        if blk.kind != "OSS-INV" and not re.match(r"^[\w.\-]+/[\w.\-]+$", blk.repo):
            bad(blk, "bad_repo", blk.repo)

        if blk.kind == "OSS-SOL":
            tr = blk.fields.get("transfer", "").strip()
            if tr and tr not in TRANSFER_VALUES:
                bad(blk, "bad_transfer", tr)
            files = blk.fields.get("files", "")
            if files and not POINTER_RE.search(files):
                bad(blk, "no_line_pointer", files[:80])
            slug = blk.fields.get("slug", "")
            if slug and classify_slug(slug, anchor_prefix) == "free-label":
                bad(blk, "slug_not_anchored", slug)
    return out


def summarise(blocks: list[Block], anchor_prefix: str = "GAP-") -> dict:
    inv = [b for b in blocks if b.kind == "OSS-INV"]
    sol = [b for b in blocks if b.kind == "OSS-SOL"]
    slug_class: dict[str, int] = {}
    for b in sol:
        k = classify_slug(b.fields.get("slug", ""), anchor_prefix)
        slug_class[k] = slug_class.get(k, 0) + 1
    transfer: dict[str, int] = {}
    for b in sol:
        t = b.fields.get("transfer", "").strip() or "missing"
        transfer[t] = transfer.get(t, 0) + 1
    return {
        "blocks": len(blocks),
        "oss_inv": len(inv),
        "oss_sol": len(sol),
        "repos": sorted({b.repo for b in blocks if b.repo}),
        "slug_class": slug_class,
        "transfer": transfer,
        "anchor_prefix": anchor_prefix,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--anchor-prefix", default="GAP-")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when violations are found")
    args = ap.parse_args(argv)

    blocks: list[Block] = []
    for pat in args.paths:
        p = Path(pat)
        if p.is_dir():
            files = sorted(p.rglob("*.md")) + sorted(p.rglob("*.txt"))
        else:
            files = [p]
        for f in files:
            blocks.extend(parse_file(f))

    violations = validate(blocks, args.anchor_prefix)
    report = {"summary": summarise(blocks, args.anchor_prefix),
              "violations": violations}
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        s = report["summary"]
        print(f"blocks={s['blocks']} OSS-INV={s['oss_inv']} OSS-SOL={s['oss_sol']} "
              f"repos={len(s['repos'])}")
        print(f"slug_class={s['slug_class']} transfer={s['transfer']}")
        for v in violations:
            print(f"VIOLATION {v['code']}: {v['source']} ({v['repo']}) {v['detail']}")
        print(f"violations={len(violations)}")
    return 1 if (violations and args.strict) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())