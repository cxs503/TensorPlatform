"""Fidelity-chain selection by cross-validation.

The autoregressive chain is a *model*, and like any model it can be worse than a simpler
alternative: if a low-fidelity level is strongly biased, its scaling factor ``rho``
amplifies that bias into the fused prediction.  This module picks the chain by
cross-validation on the **most expensive level's samples** — the only data that carry
top-fidelity information — so the choice never looks at any validation set that the
downstream study will be scored on.

Candidates are subsets of the ranks present in the data, e.g.

* ``(top,)``               single-fidelity GP on the top level only;
* ``(mid, top)``           two-level chain (drops the coarse, biased level);
* ``(0, mid, top)``        the full ladder.

Procedure: the top-level samples are split into ``folds`` groups; for each candidate the
chain is fitted on the remaining data and scored on the held-out top-level points; the
candidate with the lowest pooled RMSE wins and is refitted on all data.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

from .cokriging import MultiFidelitySurrogate

__all__ = ["cross_validated_choice", "candidate_subsets"]


def candidate_subsets(ranks: Sequence[int], top_only: bool = True) -> List[Tuple[int, ...]]:
    """All contiguous-from-the-top subsets of the ladder (plus the top level alone)."""
    ranks = sorted(int(r) for r in ranks)
    if not ranks:
        return []
    out: List[Tuple[int, ...]] = []
    top = ranks[-1]
    if top_only:
        out.append((top,))
    for i in range(len(ranks)):
        sub = tuple(ranks[i:])
        if sub and sub not in out:
            out.append(sub)
    return out


def cross_validated_choice(
    data: Mapping[int, Tuple[np.ndarray, np.ndarray]],
    subsets: Iterable[Sequence[int]] | None = None,
    folds: int = 4,
    seed: int = 0,
    **surrogate_kwargs,
) -> Dict[str, object]:
    """Select the fidelity chain by k-fold validation on the top-level samples.

    Returns ``{"surrogate", "subset", "cv_table", "rmse"}``.  ``data`` maps rank to
    ``(X, y)``; ranks absent from ``subsets`` are dropped from the fit.
    """
    ranks = sorted(data)
    if not ranks:
        raise ValueError("no data")
    top = ranks[-1]
    subsets = [tuple(s) for s in (subsets or candidate_subsets(ranks))]

    Xt, yt = data[top]
    n = len(yt)
    k = int(min(max(folds, 2), n))
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    groups = np.array_split(order, k)

    table = []
    best = None
    for subset in subsets:
        subset = tuple(r for r in subset if r in data)
        if not subset:
            continue
        errs = []
        for g in groups:
            hold = np.zeros(n, dtype=bool)
            hold[g] = True
            sub_data = {}
            for r in subset:
                X, y = data[r]
                if r == top:
                    sub_data[r] = (X[~hold], y[~hold])
                else:
                    sub_data[r] = (X, y)
            if sub_data[top][1].size < 2:
                continue
            try:
                model = MultiFidelitySurrogate(**surrogate_kwargs).fit(sub_data)
                pred, _ = model.predict(Xt[hold])
            except Exception:  # pragma: no cover - degenerate fit
                errs.append(float("inf"))
                continue
            errs.append(float(np.sqrt(np.mean((pred - yt[hold]) ** 2))))
        rmse = float(np.sqrt(np.mean(np.square(errs)))) if errs else float("inf")
        table.append({"subset": [int(r) for r in subset], "cv_rmse": rmse,
                      "n_folds": len(errs)})
        if best is None or rmse < best[1]:
            best = (subset, rmse)

    chosen = best[0]
    truncated = {r: data[r] for r in chosen if r in data}
    model = MultiFidelitySurrogate(**surrogate_kwargs).fit(truncated)
    return {"surrogate": model, "subset": [int(r) for r in chosen],
            "cv_rmse": float(best[1]), "cv_table": table}