"""Regenerate the sha256 digest table of the GCI anchor library.

Usage
-----
    python -m tools.gci.gen_anchor_digests --check    # verify only (default)
    python -m tools.gci.gen_anchor_digests --write    # rewrite the table

``--write`` rewrites only the ``_DIGESTS`` literal block in
``tools/gci/anchors.py``.  It is meant to be used when an anchor's *numeric
claim* is deliberately revised (which requires a documented review, see
``docs/gci_ittc_7503_absorption.md``); routine operation never needs it.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
from typing import Dict

from tools.gci import anchors as A
from tools.gci.core import gci

_TABLE_RE = re.compile(r"_DIGESTS: Dict\[str, str\] = \{.*?\n\}", re.S)


def compute_digests() -> Dict[str, str]:
    """Digest every anchor spec exactly as ``anchors.anchor_payload`` would."""
    out: Dict[str, str] = {}
    for spec in A._specs():
        spec = dict(spec)
        ident = str(spec["id"])
        spec.pop("sha256", None)
        anchor = A.Anchor(sha256="", **spec)
        out[ident] = hashlib.sha256(A.anchor_payload(anchor)).hexdigest()
    return out


def render_table(digests: Dict[str, str]) -> str:
    lines = ["_DIGESTS: Dict[str, str] = {"]
    for key, val in digests.items():
        lines.append(f'    "{key}": "{val}",')
    lines.append("}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="rewrite the digest table in anchors.py")
    args = ap.parse_args(argv)

    digests = compute_digests()
    path = pathlib.Path(A.__file__)
    if args.write:
        src = path.read_text(encoding="utf-8")
        new_table = render_table(digests)
        if not _TABLE_RE.search(src):
            print("ERROR: could not locate the _DIGESTS block", file=sys.stderr)
            return 2
        path.write_text(_TABLE_RE.sub(new_table, src, count=1), encoding="utf-8")
        print(f"rewrote {len(digests)} digests into {path}")
        return 0

    ok = True
    for ident, digest in digests.items():
        current = A._DIGESTS.get(ident)
        status = "OK " if current == digest else "MISMATCH"
        if current != digest:
            ok = False
        print(f"{status} {ident:32s} {digest}")
    # also report pinned-vs-recomputed numbers, ignoring the digest gate
    print()
    for spec in A._specs():
        spec = dict(spec)
        spec.pop("sha256", None)
        res = gci(spec["f1"], spec["f2"], spec["f3"], spec["r21"], spec["r32"], fs=spec["fs"])
        checks = [
            ("p", res.p_used, spec["p_expected"]),
            ("gci21", res.gci21, spec["gci21_expected"]),
            ("gci32", res.gci32, spec["gci32_expected"]),
            ("u_v", res.u_v, spec["u_v_expected"]),
        ]
        bad = [
            f"{name}: tool={val!r} pinned={exp!r} rel={abs(val - exp) / abs(exp) if exp else abs(val):.2e}"
            for name, val, exp in checks
            if abs(val - exp) > spec["rel_tol"] * max(abs(exp), 1e-300)
        ]
        flag = "OK " if not bad else "BAD"
        if bad:
            ok = False
        print(f"{flag} {spec['id']:32s} " + ("; ".join(bad) if bad else "all pinned values reproduced"))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())