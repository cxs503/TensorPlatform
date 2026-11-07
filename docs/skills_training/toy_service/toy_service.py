#!/usr/bin/env python3
"""Toy library-catalog service (skills training exercise).

A minimal in-process paginated list endpoint over a fixed catalog of 10 books.
No network, no external state: the "service" is the function `list_books`
plus a thin CLI so probes can be run as plain shell commands.

CLI:
    python3 toy_service.py --page 1 --per-page 3
"""
import argparse
import json

CATALOG = [{"id": i, "title": f"Book {i:02d}"} for i in range(1, 11)]


def list_books(page: int, per_page: int = 3, catalog=None) -> dict:
    """Return one page of the catalog.

    Contract (invariant): every item in `catalog` must be reachable by
    exactly one page in 1..total_pages, in catalog order, no duplicates.
    """
    if catalog is None:
        catalog = CATALOG
    if page < 1:
        raise ValueError("page must be >= 1")
    if per_page < 1:
        raise ValueError("per_page must be >= 1")
    total = len(catalog)
    total_pages = (total + per_page - 1) // per_page  # ceil: last partial page must be advertised
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": list(catalog[start:end]),
        "page": page,
        "per_page": per_page,
        "total_items": total,
        "total_pages": total_pages,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="toy catalog pagination service")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--per-page", type=int, default=3)
    args = ap.parse_args()
    try:
        print(json.dumps(list_books(args.page, args.per_page), indent=2))
    except ValueError as e:
        print(f"error: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
