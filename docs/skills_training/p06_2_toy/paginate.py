"""Core pagination logic for the P06-2 toy service.

NOTE (training exercise): the PLANTED off-by-one defect (floor instead of
ceil in ``total_pages``) has been diagnosed and fixed. See
docs/skills_training/P06-2.md for the full diagnosis.
"""


class PageOutOfRange(ValueError):
    """Raised when the requested page is outside the valid range."""


def paginate(items, page, per_page):
    """Return ``(rows, total, total_pages)`` for the requested page.

    Pages are 1-based. ``total_pages`` is the number of pages needed to
    cover every item in ``items``.
    """
    if per_page < 1:
        raise ValueError("per_page must be >= 1")
    total = len(items)
    total_pages = (total + per_page - 1) // per_page  # FIX: ceil division
    if page < 1 or page > total_pages:
        raise PageOutOfRange(f"page {page} out of range 1..{total_pages}")
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], total, total_pages
