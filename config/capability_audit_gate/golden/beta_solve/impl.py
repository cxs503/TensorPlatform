"""Synthetic fixture: bisection solver with genuine iteration."""


def bisect(fn, lo, hi, iters=80):
    flo = fn(lo)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = fn(mid)
        if (fm < 0) == (flo < 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def residual_report(fn, root):
    return {"root": root, "residual": fn(root)}
