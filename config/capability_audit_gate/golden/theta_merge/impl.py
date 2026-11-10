"""Synthetic fixture: genuine implementation, tests are placeholders."""


def merge(left, right):
    out = list(left)
    for item in right:
        out.append(item)
    return sorted(out)


def dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
