#!/usr/bin/env python3
"""Pointer self-check for REPORT_walberla.md (FEAT-OSS-INVENTORY-LBM / mission 8551).

Every claim in the report carries a `path:line` pointer.  This script re-reads the
source tree and asserts that
  1. the file exists in the checkout,
  2. the line number exists,
and it prints the referenced source text so the pointer->claim link is auditable.

Two pointer styles are recognised:
  * explicit  `src/foo/Bar.h:58`            -> verified
  * shorthand `:80` inside a table row whose earlier column cited a file
                                            -> resolved against the file cited earlier
                                               IN THE SAME LINE (if any), otherwise counted
                                               as "bare" (not verified, reported as such)

Usage: python3 verify_walberla_pointers.py REPORT_walberla.md /tmp/walberla_src
Exit code 0 = all explicit pointers verified; 1 = any failure.
"""
import os
import re
import sys

report = sys.argv[1] if len(sys.argv) > 1 else "REPORT_walberla.md"
root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "/tmp/walberla_src")

FILE_RE = re.compile(r"((?:[\w.+-]+/)*[\w.+-]+\.(?:h|cpp|cu|py|prm|md|txt|yml|in)):(\d+)(?:[-–](\d+))?")
BARE_RE = re.compile(r"`?:(\d+)(?:[-–](\d+))?`?")


def read_line(path, n):
    with open(path, "r", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if i == n:
                return line.rstrip()
    return None


def file_len(path):
    with open(path, "r", errors="replace") as fh:
        return sum(1 for _ in fh)


# basename index so that shorthand citations such as `PdfRefillingModel.h:61-66` (path given
# earlier in the same paragraph) still resolve.
base_index = {}
for dirpath, _d, files in os.walk(root):
    if "/.git" in dirpath:
        continue
    for fn in files:
        base_index.setdefault(fn, []).append(os.path.join(dirpath, fn))


def resolve(rel):
    full = os.path.join(root, rel)
    if os.path.exists(full):
        return full, rel
    if "/" in rel:          # a path with directories must exist verbatim -> no fuzzy fallback
        return None, rel
    cand = base_index.get(os.path.basename(rel), [])
    if len(cand) == 1:
        return cand[0], os.path.relpath(cand[0], root)
    return None, rel


explicit_ok = explicit_bad = bare_ok = bare_orphan = 0
failures = []
samples = []

with open(report, "r", errors="replace") as fh:
    for lineno, line in enumerate(fh, 1):
        # skip the machine block section? no: it contains pointers too -> verify all
        cited = None
        last_pos = 0
        for m in FILE_RE.finditer(line):
            rel, start, end = m.group(1), int(m.group(2)), m.group(3)
            cited = rel
            last_pos = m.end()
            full, rel_res = resolve(rel)
            if full is None:
                explicit_bad += 1
                failures.append(f"L{lineno}: missing/ambiguous file {rel}")
                continue
            rel = rel_res
            n = file_len(full)
            hi = int(end) if end else start
            if start < 1 or hi > n:
                explicit_bad += 1
                failures.append(f"L{lineno}: {rel}:{start}-{hi} out of range (file has {n} lines)")
                continue
            explicit_ok += 1
            txt = read_line(full, start) or ""
            samples.append((lineno, f"{rel}:{start}", txt.strip()[:100]))
        if cited is None:
            continue
        # shorthand pointers after the first explicit citation in the same line
        for m in BARE_RE.finditer(line, last_pos):
            start = int(m.group(1))
            full, _ = resolve(cited)
            if full is None:
                continue
            n = file_len(full)
            if 1 <= start <= n:
                bare_ok += 1
                samples.append((lineno, f"{cited}:{start} (shorthand)", (read_line(full, start) or "").strip()[:80]))
            else:
                bare_orphan += 1

print(f"report            : {report}")
print(f"source tree       : {root}")
print(f"explicit pointers : {explicit_ok} verified, {explicit_bad} FAILED")
print(f"shorthand pointers: {bare_ok} resolved in-line, {bare_orphan} out of range")
print(f"total checkable   : {explicit_ok + bare_ok}")
print()
print("--- sample of resolved pointers (report line -> source line) ---")
for ln, ptr, txt in samples[:40]:
    print(f"  md:{ln:<4} {ptr:<52} | {txt}")
if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  !", f)
    sys.exit(1)
print("\nRESULT: OK")