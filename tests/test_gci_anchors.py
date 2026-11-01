"""Tests for the sha256-locked GCI anchor library (tools.gci.anchors).

Acceptance criteria covered
---------------------------
(b) the tool reproduces every pinned ``p`` / ``GCI`` / ``U_V`` inside 1%;
(d) the sha256 self-check passes and a *single digit* change anywhere in an
    anchor record is refused (negative test).
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gci import anchors as A  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# (d) digest self-check
# ---------------------------------------------------------------------------
def test_anchor_library_verifies():
    lib = A.load_anchors()  # raises AnchorTamperError on mismatch
    assert len(lib) >= 6
    assert {a.provenance for a in lib} == {"clause", "internal"}


def test_every_digest_is_a_locked_literal():
    for anchor in A.ANCHORS:
        assert HEX64.match(anchor.sha256), f"{anchor.id} has no locked digest"
        assert A.anchor_payload(anchor)
        assert hashlib.sha256(A.anchor_payload(anchor)).hexdigest() == anchor.sha256


def test_digest_table_covers_every_anchor():
    ids = {a.id for a in A.ANCHORS}
    assert ids == set(A._DIGESTS), "digest table and anchor list disagree"


# ---------------------------------------------------------------------------
# (d) negative tests: one digit of tampering must be refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["f1", "f2", "f3", "r21", "fs", "p_expected", "gci21_expected"])
def test_single_field_tamper_is_rejected(field):
    anchor = A.ANCHORS[0]
    value = getattr(anchor, field)
    if isinstance(value, float):
        # flip one digit: the smallest change that still alters the decimal text
        tampered = dataclasses.replace(anchor, **{field: value * (1.0 + 1e-9) + 1e-12})
    else:  # pragma: no cover - all hashed numeric fields are floats
        tampered = dataclasses.replace(anchor, **{field: value})
    with pytest.raises(A.AnchorTamperError):
        A.verify_anchor(tampered)


def test_tamper_in_every_anchor_is_detected():
    for anchor in A.ANCHORS:
        tampered = dataclasses.replace(anchor, f3=anchor.f3 + 1e-9)
        with pytest.raises(A.AnchorTamperError):
            A.verify_anchor(tampered)


def test_expected_value_tamper_is_rejected_even_if_within_tolerance():
    """A pinned value cannot be nudged: the digest, not the tolerance, decides."""
    anchor = A.ANCHORS[0]
    tampered = dataclasses.replace(anchor, gci21_expected=anchor.gci21_expected * (1.0 + 1e-13))
    with pytest.raises(A.AnchorTamperError):
        A.verify_anchor(tampered)


def test_blank_digest_is_rejected():
    tampered = dataclasses.replace(A.ANCHORS[0], sha256="0" * 64)
    with pytest.raises(A.AnchorTamperError):
        A.verify_anchor(tampered)


# ---------------------------------------------------------------------------
# (b) reproduction of the pinned numbers
# ---------------------------------------------------------------------------
def test_reproduction_within_one_percent():
    rows = A.reproduction_table()
    assert len(rows) == len(A.ANCHORS)
    for row in rows:
        assert row["sha256_ok"] is True
        assert row["within_1pct"] is True, row
        for key in ("p_rel_dev", "gci21_rel_dev", "gci32_rel_dev", "u_v_rel_dev"):
            assert row[key] < 1e-2, (row["id"], key, row[key])


def test_closed_form_anchors_are_reproduced_to_machine_precision():
    for anchor in A.load_anchors():
        if anchor.provenance != "clause":
            continue
        info = A.verify_anchor(anchor)
        assert info["all_within_tol"] is True, (anchor.id, info["rel_dev"])
        assert max(info["rel_dev"].values()) < 1e-12, (anchor.id, info["rel_dev"])


def test_anchor_convergence_classes_and_fallbacks_are_pinned():
    classes = {a.id: a.convergence_expected for a in A.ANCHORS}
    assert classes["ITTC-P2-EXACT"] == "monotonic"
    assert classes["ITTC-OSCILLATORY-FALLBACK"] == "oscillatory"
    assert classes["ITTC-DIVERGENT-FALLBACK"] == "divergent"
    fallbacks = {a.id: a.p_source_expected for a in A.ANCHORS}
    assert fallbacks["ITTC-P2-EXACT"] == "observed"
    assert fallbacks["ITTC-OSCILLATORY-FALLBACK"].startswith("fallback:")


def test_reproduction_table_row_schema():
    row = A.reproduction_table()[0]
    for key in (
        "id",
        "provenance",
        "clause",
        "p_published",
        "p_tool",
        "gci21_published",
        "gci21_tool",
        "convergence",
        "p_source",
        "sha256_ok",
        "within_1pct",
    ):
        assert key in row


def test_anchor_payload_is_order_independent_and_stable():
    anchor = A.ANCHORS[0]
    assert A.anchor_payload(anchor) == A.anchor_payload(dataclasses.replace(anchor, note="ignored"))
    assert b'""' not in A.anchor_payload(anchor)