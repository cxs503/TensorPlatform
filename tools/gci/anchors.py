"""Sha256-locked anchor library for the ITTC GCI chain.

Why this module exists
----------------------
A numerical-uncertainty tool is only trustworthy if its *numbers* are pinned
to something outside itself.  Each anchor below is a three-grid dataset with
the order of accuracy ``p`` and the grid-convergence indices that the ITTC
7.5-03-02-01 equations must return for it.  The expected values are closed
form (hand-derivable, see ``docs/gci_ittc_7503_absorption.md``), so the check
"tool output == anchor" is a genuine check of the implementation rather than a
regression against a previous run of the same code.

Each anchor is additionally protected by a sha256 digest over its canonical
payload.  Changing a single digit anywhere in an anchor record breaks the
digest and :func:`load_anchors` refuses to serve the library
(:class:`AnchorTamperError`), so a silently "improved" reference value cannot
enter an acceptance decision.

Provenance discipline
---------------------
``provenance = "clause"``      value derivable in closed form from the ITTC
                               equations (7.5-03-02-01 Eqs. 1-15) for an
                               explicitly constructed grid family.
``provenance = "internal"``    value produced by this repository's own
                               solver (``tools.gci.cases``) and pinned here as
                               a regression anchor with a documented tolerance.

No published measured triplet was copied from memory: the ITTC / Roache
example tables are behind a network boundary that this build environment
cannot reach (403), and inventing plausible-looking "published" numbers would
be worse than declaring the gap.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from tools.gci.core import GCIResult, gci

__all__ = [
    "Anchor",
    "AnchorTamperError",
    "ANCHORS",
    "load_anchors",
    "anchor_payload",
    "verify_anchor",
    "reproduction_table",
]

_REL_TOL_ANALYTIC = 1e-12
_REL_TOL_INTERNAL = 1e-3

#: field subset that is hashed -- the full numeric claim of an anchor
_HASHED_FIELDS = (
    "id",
    "provenance",
    "r21",
    "r32",
    "fs",
    "f1",
    "f2",
    "f3",
    "p_expected",
    "gci21_expected",
    "gci32_expected",
    "u_v_expected",
    "convergence_expected",
    "p_source_expected",
)


class AnchorTamperError(RuntimeError):
    """Raised when an anchor no longer matches its sha256 digest."""


@dataclass(frozen=True)
class Anchor:
    id: str
    provenance: str
    clause: str
    r21: float
    r32: float
    fs: float
    f1: float
    f2: float
    f3: float
    p_expected: float
    gci21_expected: float
    gci32_expected: float
    u_v_expected: float
    convergence_expected: str
    p_source_expected: str
    rel_tol: float
    sha256: str
    note: str = ""

    def as_payload(self) -> Dict[str, object]:
        return {k: getattr(self, k) for k in _HASHED_FIELDS if k != "id"} | {"id": self.id}


def anchor_payload(anchor: Anchor) -> bytes:
    """Canonical, order-independent byte string used for the sha256 digest."""
    data = {k: getattr(anchor, k) for k in _HASHED_FIELDS}
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(anchor: Anchor) -> str:
    return hashlib.sha256(anchor_payload(anchor)).hexdigest()


def _mk(**kwargs) -> Anchor:
    """Build an Anchor, taking the digest from the locked table below."""
    sha = kwargs.pop("sha256")
    anchor = Anchor(sha256=sha, **kwargs)
    return anchor


# ---------------------------------------------------------------------------
# The locked digests.  Regenerate ONLY together with a documented review of
# the numeric claim; the generator is tools/gci/gen_anchor_digests.py.
# ---------------------------------------------------------------------------
_DIGESTS: Dict[str, str] = {
    "ITTC-P2-EXACT": "eda8dbd45ca5019884af1c7803fb80aaca2edb65d89a7ec46963916fb3bbd8f9",
    "ITTC-P1-EXACT": "5879925edc8f27943680b0565dbbe8c52b8e0696c4ee3f67372114bacfcdbf5a",
    "ITTC-Q2-RELATIVE-P": "c2dea1cc8cd4a7fa9df23d6dafd510c8b007cd442a41fc6429d6588e47ba84d7",
    "ITTC-OSCILLATORY-FALLBACK": "772ea3948c02b6a7f4f3da940b6701e5e30a98edea63b2e66441892f1dfab2fa",
    "ITTC-DIVERGENT-FALLBACK": "95d66c69e2b3a93cd6a2de5f265ffc4882d53474bf77550e01d664af5a2596fa",
    "ITTC-P-ABOVE-MAX-FALLBACK": "3a34ba02087643ce48c8043df199292dab37d0355a00c8ff55cb1d82bfc04f8a",
    "ITTC-TWO-GRID-FS3": "d4b1bdce18f052f72cce59fd04dee51e05dd6b69425fbad1f88b7b2272df0428",
    "REPO-CIRCLE-CA-E2E": "7d072b46e178eac61be73ac6a9fef40246d32381d20f3341a7940d530f3aa767",
}


def _specs() -> List[Dict[str, object]]:
    """Anchor specifications.  Expected values are closed form, not copied."""
    # closed-form values of the two exact anchors below (see the report):
    #   GCI = Fs*|eps21|/(r^p - 1) with Fs = 1.25, r = 2, p = 2 -> |eps21|/2.4
    c1 = 1.25 * 0.01 / 3.0        # = 0.00416666...  (p = 2 family, eps21 = -1e-2)
    c2 = 1.25 * 0.04 / 3.0        # = 0.01666666...  (eps32 = -4e-2)
    return [
        dict(
            id="ITTC-P2-EXACT",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 Eq.(1)-(7),(15): eps32/eps21 = 4, r = 2",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.0, f2=0.99, f3=0.9504,
            p_expected=2.0,
            gci21_expected=c1,
            gci32_expected=c2,
            u_v_expected=c1,
            convergence_expected="monotonic",
            p_source_expected="observed",
            rel_tol=_REL_TOL_ANALYTIC,
            note="relative-error family eps21 = -1e-2, eps32 = -4e-2 -> "
                 "p = ln(4)/ln(2) = 2 exactly; R_G = 0.25",
        ),
        dict(
            id="ITTC-P1-EXACT",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 Eq.(5)-(6): first-order family, R_G = 0.5",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.0, f2=0.99, f3=0.9702,
            p_expected=1.0,
            gci21_expected=1.25 * 0.01 / 1.0,
            gci32_expected=1.25 * 0.02 / 1.0,
            u_v_expected=1.25 * 0.01 / 1.0,
            convergence_expected="monotonic",
            p_source_expected="observed",
            rel_tol=_REL_TOL_ANALYTIC,
            note="eps21 = -1e-2, eps32 = -2e-2 -> p = ln(2)/ln(2) = 1; "
                 "GCI = Fs*|eps21|/(r-1) = 1.25e-2",
        ),
        dict(
            id="ITTC-Q2-RELATIVE-P",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 Eq.(6): relative eps with quadratic error -> p != 2",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.00625, f2=1.025, f3=1.1,
            p_expected=1.9733648734965503,
            gci21_expected=0.007958074534161289,
            gci32_expected=0.0312500000000000,  # 1.25*0.07317073170731725/3
            u_v_expected=0.007958074534161289,
            convergence_expected="monotonic",
            p_source_expected="observed",
            rel_tol=_REL_TOL_ANALYTIC,
            note="documented trap: exact O(h^2) error gives p_obs = 1.9734, not 2, "
                 "because ITTC normalises by S1 and S2",
        ),
        dict(
            id="ITTC-OSCILLATORY-FALLBACK",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 §3.3: R_G < 0 -> p_lim fallback",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.0, f2=1.01, f3=1.005,
            p_expected=0.5,
            gci21_expected=0.03017766952966371,
            gci32_expected=0.01493944036121999,
            u_v_expected=0.03017766952966371,
            convergence_expected="oscillatory",
            p_source_expected="fallback:non_monotonic",
            rel_tol=_REL_TOL_ANALYTIC,
            note="eps21 = +0.01, eps32 = -0.00495 -> R_G = -2.02",
        ),
        dict(
            id="ITTC-DIVERGENT-FALLBACK",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 §3.3: R_G > 1 -> p_obs < 0 -> p_lim fallback",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.0, f2=1.04, f3=1.0416,
            p_expected=0.5,
            gci21_expected=0.12071067811865484,
            gci32_expected=0.004642718389179161,
            u_v_expected=0.12071067811865484,
            convergence_expected="divergent",
            p_source_expected="fallback:p_obs_non_positive",
            rel_tol=_REL_TOL_ANALYTIC,
            note="R_G = 26, p_obs = -4.70",
        ),
        dict(
            id="ITTC-P-ABOVE-MAX-FALLBACK",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 §3.3 + platform p_max policy: p_obs > 4 -> fallback",
            r21=2.0, r32=2.0, fs=1.25,
            f1=1.0 + 0.1 / 1024.0, f2=1.0 + 0.1 / 32.0, f3=1.1,
            p_expected=0.5,
            gci21_expected=0.00913492584141798,
            gci32_expected=0.2914354378254126,
            u_v_expected=0.00913492584141798,
            convergence_expected="monotonic",
            p_source_expected="fallback:p_obs_above_max",
            rel_tol=_REL_TOL_ANALYTIC,
            note="fifth-order error family -> p_obs = 4.996 > p_max = 4",
        ),
        dict(
            id="ITTC-TWO-GRID-FS3",
            provenance="clause",
            clause="ITTC 7.5-03-02-01 §3.3: Fs = 3.0 two-grid / assumed-p evaluation",
            r21=2.0, r32=2.0, fs=3.0,
            f1=1.0, f2=0.99, f3=0.9504,
            p_expected=2.0,
            gci21_expected=3.0 * 0.01 / 3.0,
            gci32_expected=3.0 * 0.04 / 3.0,
            u_v_expected=3.0 * 0.01 / 3.0,
            convergence_expected="monotonic",
            p_source_expected="observed",
            rel_tol=_REL_TOL_ANALYTIC,
            note="same triplet as ITTC-P2-EXACT; Fs = 3.0 inflates GCI by 2.4x",
        ),
        dict(
            id="REPO-CIRCLE-CA-E2E",
            provenance="internal",
            clause="platform end-to-end run: tools/gci/cases.py circle section added mass",
            r21=2.0, r32=2.0, fs=1.25,
            f1=0.9998380299669203, f2=0.9995025002950253, f3=0.9981661197019740,
            p_expected=1.994302904724744,
            gci21_expected=0.00014056532763859894,
            gci32_expected=0.0005600453607021620,
            u_v_expected=0.00014056532763859894,
            convergence_expected="monotonic",
            p_source_expected="observed",
            rel_tol=_REL_TOL_INTERNAL,
            note="nr/ntheta = 160/256, 80/128, 40/64; pinned with 1e-3 tolerance",
        ),
    ]


def _build() -> List[Anchor]:
    out = []
    for spec in _specs():
        ident = str(spec["id"])
        out.append(_mk(sha256=_DIGESTS.get(ident, "__MISSING__"), **spec))
    return out


ANCHORS: List[Anchor] = _build()


def verify_anchor(anchor: Anchor) -> Dict[str, object]:
    """Digest check + independent recomputation of an anchor.

    Returns a dict with ``sha256_ok``, the recomputed GCI result and the
    relative deviations of every pinned quantity.  Raises
    :class:`AnchorTamperError` when the digest does not match.
    """
    actual = _digest(anchor)
    if actual != anchor.sha256:
        raise AnchorTamperError(
            f"anchor {anchor.id!r} failed sha256 verification: "
            f"stored {anchor.sha256!r} != recomputed {actual!r}"
        )
    res: GCIResult = gci(anchor.f1, anchor.f2, anchor.f3, anchor.r21, anchor.r32, fs=anchor.fs)

    def rel(a: float, b: float) -> float:
        if b == 0.0:
            return abs(a)
        return abs(a - b) / abs(b)

    devs = {
        "p": rel(res.p_used, anchor.p_expected),
        "gci21": rel(res.gci21, anchor.gci21_expected),
        "gci32": rel(res.gci32, anchor.gci32_expected),
        "u_v": rel(res.u_v, anchor.u_v_expected),
    }
    return {
        "id": anchor.id,
        "sha256_ok": True,
        "sha256": actual,
        "result": res,
        "rel_dev": devs,
        "all_within_tol": all(d <= anchor.rel_tol for d in devs.values()),
        "convergence_ok": res.convergence == anchor.convergence_expected,
        "p_source_ok": res.p_source == anchor.p_source_expected,
        "rel_tol": anchor.rel_tol,
    }


def load_anchors(strict: bool = True) -> List[Anchor]:
    """Return the anchor library, verifying every digest on the way.

    ``strict=True`` (default) also verifies that the recomputed numbers agree
    with the pinned values inside the per-anchor tolerance; on failure the
    library is refused rather than silently returned.
    """
    if not ANCHORS:
        raise AnchorTamperError("anchor library is empty")
    checked = []
    for anchor in ANCHORS:
        info = verify_anchor(anchor)  # raises on digest mismatch
        if strict and not (info["all_within_tol"] and info["convergence_ok"] and info["p_source_ok"]):
            raise AnchorTamperError(
                f"anchor {anchor.id!r} disagrees with its pinned values: {info['rel_dev']}"
            )
        checked.append(anchor)
    return checked


def reproduction_table() -> List[Dict[str, object]]:
    """Reproduction report (one row per anchor) used by tests and reports."""
    rows = []
    for anchor in load_anchors():
        info = verify_anchor(anchor)
        res: GCIResult = info["result"]  # type: ignore[assignment]
        rows.append(
            {
                "id": anchor.id,
                "provenance": anchor.provenance,
                "clause": anchor.clause,
                "p_published": anchor.p_expected,
                "p_tool": res.p_used,
                "p_rel_dev": info["rel_dev"]["p"],
                "gci21_published": anchor.gci21_expected,
                "gci21_tool": res.gci21,
                "gci21_rel_dev": info["rel_dev"]["gci21"],
                "gci32_rel_dev": info["rel_dev"]["gci32"],
                "u_v_rel_dev": info["rel_dev"]["u_v"],
                "convergence": res.convergence,
                "p_source": res.p_source,
                "sha256_ok": info["sha256_ok"],
                "within_1pct": all(
                    info["rel_dev"][k] < 1e-2 for k in ("p", "gci21", "gci32", "u_v")
                ),
            }
        )
    return rows