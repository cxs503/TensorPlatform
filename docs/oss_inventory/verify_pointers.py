#!/usr/bin/env python3
"""Verify every `file:line` pointer in the inventory report against the real source tree.

Extracts tokens of the form `path:line` (and grouped forms like `file.py:110,:149`)
from the markdown, resolves them against the lbmpy checkout and checks:
  - the file exists somewhere under the checkout,
  - the referenced line number is within the file,
  - the referenced line is non-empty.

Exit code 0 only when all pointers resolve. Prints a per-pointer table.
Usage: LBMPY_SRC=/tmp/lbmpy_src python3 verify_pointers.py report.md
"""
import os
import re
import sys

SRC = os.environ.get("LBMPY_SRC", "/tmp/lbmpy_src")
REPORT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "REPORT_lbmpy.md")

# index of all files in the checkout (basename -> [abs paths])
INDEX = {}
for dirpath, _dirs, names in os.walk(SRC):
    if "/.git" in dirpath:
        continue
    for n in names:
        INDEX.setdefault(n, []).append(os.path.join(dirpath, n))


def resolve(fname):
    cands = INDEX.get(os.path.basename(fname), [])
    # prefer candidate whose path ends with the referenced suffix
    suff = fname.lstrip("./")
    for c in cands:
        if c.replace(os.sep, "/").endswith(suff):
            return c
    return cands[0] if len(cands) == 1 else None


TOKEN = re.compile(r"[`]?([A-Za-z0-9_./-]+\.(?:py|yml|toml|md|txt|ipynb)):(\d+)")
GROUPED = re.compile(r"[`]?([A-Za-z0-9_./-]+\.(?:py|yml|toml|md|txt|ipynb)):((?:\d+)(?:[-,:]\d+)*)")

ok, bad = [], []
text = open(REPORT, encoding="utf-8").read()
for line_no, line in enumerate(text.splitlines(), 1):
    for m in GROUPED.finditer(line):
        fname, spec = m.group(1), m.group(2)
        nums = [int(x) for x in re.findall(r"\d+", spec)]
        single = not any(ch in spec for ch in "-,")
        path = resolve(fname)
        if not path:
            bad.append((line_no, f"{fname}:{spec}", "file not found in checkout"))
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            src_lines = f.readlines()
        for n in nums:
            if n < 1 or n > len(src_lines):
                bad.append((line_no, f"{fname}:{n}", f"line out of range (file has {len(src_lines)})"))
            elif single and not src_lines[n - 1].strip():
                bad.append((line_no, f"{fname}:{n}", "line is blank"))
            else:
                ok.append((line_no, f"{fname}:{n}", src_lines[n - 1].strip()[:70]))

print(f"report : {REPORT}")
print(f"source : {SRC}")
print(f"pointers verified OK : {len(ok)}")
print(f"pointers FAILED      : {len(bad)}")
for b in bad:
    print("  FAIL md:%d %s -> %s" % b)
if ok:
    print("\nsample of verified pointers (md line -> source line content):")
    for o in ok[:12]:
        print("  md:%-4d %-46s | %s" % o)
sys.exit(0 if not bad else 1)