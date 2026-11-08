#!/usr/bin/env python3
"""Regression tests for the toy catalog pagination service.

Primary target: the violated invariant
    "every catalog item is reachable by exactly one page in 1..total_pages,
     in catalog order, no duplicates"
plus boundary values and adjacent behaviors that must remain covered.
"""
import math

import pytest

from toy_service import CATALOG, list_books


# --- the violated invariant -------------------------------------------------

def test_all_items_reachable_via_advertised_pages():
    per_page = 3
    total_pages = list_books(1, per_page)["total_pages"]
    seen = []
    for p in range(1, total_pages + 1):
        seen.extend(b["id"] for b in list_books(p, per_page)["items"])
    expected = [b["id"] for b in CATALOG]
    assert seen == expected, (
        f"pages 1..{total_pages} (per_page={per_page}) yielded {seen}; "
        f"expected {expected}"
    )


# --- boundary values ---------------------------------------------------------

@pytest.mark.parametrize(
    "total,per_page",
    [(10, 3), (9, 3), (1, 1), (0, 3), (10, 10), (10, 11), (10, 1), (7, 4)],
)
def test_total_pages_is_ceil(total, per_page):
    catalog = [{"id": i} for i in range(1, total + 1)]
    got = list_books(1, per_page, catalog=catalog)["total_pages"]
    want = math.ceil(total / per_page)
    assert got == want, f"total={total}, per_page={per_page}: got {got}, want {want}"


def test_last_page_returns_tail():
    per_page = 3
    total_pages = list_books(1, per_page)["total_pages"]
    tail = list_books(total_pages, per_page)["items"]
    expected_tail = [b["id"] for b in CATALOG[-(len(CATALOG) % per_page or per_page):]]
    assert [b["id"] for b in tail] == expected_tail


# --- adjacent behaviors that must stay green ---------------------------------

def test_page_one_returns_first_items():
    items = list_books(1, 3)["items"]
    assert [b["id"] for b in items] == [1, 2, 3]


def test_page_beyond_range_returns_empty_items():
    resp = list_books(99, 3)
    assert resp["items"] == []
    assert resp["total_items"] == 10


def test_empty_catalog():
    resp = list_books(1, 3, catalog=[])
    assert resp["items"] == []
    assert resp["total_pages"] == 0


def test_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        list_books(0, 3)
    with pytest.raises(ValueError):
        list_books(1, 0)
