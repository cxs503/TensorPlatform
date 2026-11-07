"""Regression tests for the pagination off-by-one (P06-2).

Run:  python3 -m pytest test_pagination.py -v
Before the fix: 3 failed, 2 passed.  After the fix: 5 passed.
"""
import pytest

from paginate import PageOutOfRange, paginate

ITEMS = list(range(1, 11))  # 10 items, per_page=3 -> 4 pages (3+3+3+1)


def test_all_items_reachable_across_pages():
    """Every item must appear exactly once across pages 1..total_pages."""
    seen, page = [], 1
    while True:
        rows, _total, total_pages = paginate(ITEMS, page, 3)
        seen.extend(rows)
        if page >= total_pages:
            break
        page += 1
        assert page <= 100, "pagination never terminates"
    assert seen == ITEMS, f"missing/duplicated items: {seen}"


def test_total_pages_is_ceil():
    """10 items / per_page=3 needs 4 pages, not 3."""
    _rows, _total, total_pages = paginate(ITEMS, 1, 3)
    assert total_pages == 4


def test_single_item():
    """A 1-item collection still has 1 page (floor(1/3)=0 is the trap)."""
    _rows, _total, total_pages = paginate([1], 1, 3)
    assert total_pages == 1
    rows, _t, _tp = paginate([1], 1, 3)
    assert rows == [1]


def test_exact_multiple_no_extra_page():
    """12 items / per_page=3 -> exactly 4 pages; page 5 must be rejected.

    Guards against over-fixing (e.g. ceil+1).
    """
    items = list(range(12))
    _rows, _total, total_pages = paginate(items, 1, 3)
    assert total_pages == 4
    with pytest.raises(PageOutOfRange):
        paginate(items, 5, 3)


def test_empty_collection():
    with pytest.raises(PageOutOfRange):
        paginate([], 1, 3)
