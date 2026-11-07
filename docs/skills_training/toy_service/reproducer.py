#!/usr/bin/env python3
"""Smallest safe reproducer for the pagination defect.

Walks pages 1..total_pages exactly as a well-behaved client would (it trusts
the advertised total_pages), and checks the invariant: every catalog item is
seen exactly once, in order.

Exit 0 = invariant holds (no defect).
Exit 1 = defect reproduced (items missing from the advertised page range).
"""
import sys

from toy_service import CATALOG, list_books


def main() -> int:
    per_page = 3
    resp = list_books(1, per_page)
    total_pages = resp["total_pages"]
    seen = []
    for p in range(1, total_pages + 1):
        seen.extend(b["id"] for b in list_books(p, per_page)["items"])
    expected = [b["id"] for b in CATALOG]
    if seen != expected:
        missing = [i for i in expected if i not in seen]
        print(
            f"REPRODUCED: walking pages 1..{total_pages} (per_page={per_page}) "
            f"yields {len(seen)}/{len(expected)} items; missing ids={missing}"
        )
        return 1
    print(f"OK: all {len(expected)} items reachable via pages 1..{total_pages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
