"""Report fingerprint gate: four-way binding, duplicates, fail-closed defects."""
from __future__ import annotations

from tools.platform_governance_gates.report_fingerprint import (
    ReportEnvelope,
    body_sha256,
    gate,
    metadata_sha256,
    normalize_body,
)


def envelope(report_id=1, mission_id=10, body="# demo\n", *, meta_ids=True):
    meta = {"report_id": report_id if meta_ids else 999,
            "mission_id": mission_id if meta_ids else 999,
            "title": "demo"}
    return ReportEnvelope(
        report_id=report_id, mission_id=mission_id, body=body, metadata=meta,
        declared_body_sha256=body_sha256(body),
        declared_metadata_sha256=metadata_sha256(meta),
    )


def codes(result):
    return {d.code for d in result.defects}


def test_clean_reports_pass():
    result = gate([envelope(1, 10, "# a\n"), envelope(2, 20, "# b\n")])
    assert result.ok is True
    assert result.defects == []
    assert len(result.receipts) == 2
    assert all(r.fingerprint_ok and r.identity_ok for r in result.receipts)


def test_duplicate_body_under_new_id_is_closed():
    result = gate([envelope(1, 10, "# same\n"), envelope(2, 20, "# same\n")])
    assert result.ok is False
    assert "DUPLICATE_BODY" in codes(result)


def test_duplicate_report_id_with_new_body_is_closed():
    result = gate([envelope(1, 10, "# first\n"), envelope(1, 10, "# second\n")])
    assert result.ok is False
    assert "DUPLICATE_REPORT_ID" in codes(result)


def test_declared_body_fingerprint_mismatch_is_closed():
    bad = envelope(1, 10, "# body\n")
    bad.declared_body_sha256 = "0" * 64
    result = gate([bad])
    assert result.ok is False
    assert "DECLARED_BODY_MISMATCH" in codes(result)


def test_metadata_identity_mismatch_is_closed():
    result = gate([envelope(1, 10, "# body\n", meta_ids=False)])
    assert result.ok is False
    assert "ID_MISMATCH" in codes(result)


def test_missing_fingerprint_is_closed():
    bad = envelope(1, 10, "# body\n")
    bad.declared_metadata_sha256 = ""
    result = gate([bad])
    assert result.ok is False
    assert "MISSING_FINGERPRINT" in codes(result)


def test_empty_body_is_closed():
    result = gate([envelope(1, 10, "   \n\n")])
    assert result.ok is False
    assert "EMPTY_BODY" in codes(result)


def test_body_normalisation_is_idempotent_but_content_sensitive():
    text = "# title\r\n\r\ncontent  \r\n\r\n"
    assert body_sha256(text) == body_sha256(normalize_body(text))
    assert body_sha256("# a\n") != body_sha256("# b\n")


def test_defect_digest_changes_with_the_defect():
    a = gate([envelope(1, 10, "# a\n")])
    b = gate([envelope(1, 10, "# a\n"), envelope(2, 20, "# a\n")])
    assert a.digest != b.digest