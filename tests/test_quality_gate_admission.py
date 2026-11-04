"""PR-4 red-team: server-side quality-gate admission must be able to fail.

Audit R-B found that a missing ``summary.quality_gate`` key silently bypassed
every gate check in ``complete`` (the tp_worker happy path omits it). PR-4 adds
``_server_quality_audit`` + ``_quality_gate_admission``; these tests are the
mutation probes required by QUALITY_BAR R1 — each asserts the gate *fails*
when it should, not only that it passes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from api.tensor_missions import CompleteRequest, _server_quality_audit, _quality_gate_admission


def _body(**summary) -> CompleteRequest:
    return CompleteRequest(claim_token="token-1234", status="succeeded",
                           acceptance=True, summary=dict(summary))


class TestServerQualityAudit:
    def test_missing_key_is_reported_not_silent(self):
        audit = _server_quality_audit(_body(headline="x"))
        assert audit["key_present"] is False
        assert audit["gate_valid"] is False

    def test_wrong_typed_value_counts_as_present_but_invalid(self):
        audit = _server_quality_audit(_body(quality_gate="passed"))
        assert audit["key_present"] is True
        assert audit["gate_valid"] is False

    def test_valid_gate_shape(self):
        audit = _server_quality_audit(_body(quality_gate={"passed": True}))
        assert audit["key_present"] is True
        assert audit["gate_valid"] is True
        assert "artifacts_ok" not in audit  # nothing declared -> nothing claimed

    def test_artifact_exists_only_with_configured_root(self, tmp_path, monkeypatch):
        (tmp_path / "ev.json").write_text("{}")
        body = _body(quality_gate={"passed": True, "artifacts": ["ev.json"]})
        # no root configured -> unverifiable, never silently passed
        monkeypatch.delenv("TP_EVIDENCE_ROOT", raising=False)
        audit = _server_quality_audit(body)
        assert audit["artifacts"][0]["exists"] is None
        assert audit["artifacts_ok"] is False
        # root configured + file present -> verified
        monkeypatch.setenv("TP_EVIDENCE_ROOT", str(tmp_path))
        audit = _server_quality_audit(body)
        assert audit["artifacts_ok"] is True

    def test_path_escape_artifact_is_unsafe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TP_EVIDENCE_ROOT", str(tmp_path))
        body = _body(quality_gate={"passed": True,
                                   "artifacts": ["../../etc/passwd"]})
        audit = _server_quality_audit(body)
        assert audit["artifacts"][0]["safe"] is False
        assert audit["artifacts_ok"] is False

    def test_head_commit_shape_check(self):
        body = CompleteRequest(claim_token="token-1234", status="succeeded",
                               acceptance=True, summary={"quality_gate": {"passed": True}},
                               branch="agent/m1", head_commit="not-a-sha")
        assert _server_quality_audit(body)["head_commit_sha_like"] is False
        body.head_commit = "a" * 40
        assert _server_quality_audit(body)["head_commit_sha_like"] is True


class TestAdmissionPolicy:
    """The gate itself must fail: each gap blocks under enforce, warns otherwise."""

    def test_missing_key_blocks_when_enforced(self):
        audit = _server_quality_audit(_body(headline="x"))
        ok, detail = _quality_gate_admission(audit, acceptance=True, enforce=True)
        assert ok is False and "missing" in detail

    def test_missing_key_warns_by_default(self):
        audit = _server_quality_audit(_body(headline="x"))
        ok, _ = _quality_gate_admission(audit, acceptance=True, enforce=False)
        assert ok is True  # warn-mode proceeds, warning lands in approval evidence

    def test_invalid_shape_blocks_when_enforced(self):
        audit = _server_quality_audit(_body(quality_gate="passed"))
        ok, detail = _quality_gate_admission(audit, acceptance=True, enforce=True)
        assert ok is False and "invalid" in detail

    def test_unverifiable_artifacts_block_when_enforced(self):
        audit = _server_quality_audit(_body(quality_gate={"passed": True,
                                                          "artifacts": ["x.json"]}))
        ok, detail = _quality_gate_admission(audit, acceptance=True, enforce=True)
        assert ok is False and "artifacts" in detail

    def test_valid_gate_admits_under_enforce(self, tmp_path, monkeypatch):
        (tmp_path / "x.json").write_text("{}")
        monkeypatch.setenv("TP_EVIDENCE_ROOT", str(tmp_path))
        audit = _server_quality_audit(_body(quality_gate={"passed": True,
                                                          "artifacts": ["x.json"]}))
        ok, detail = _quality_gate_admission(audit, acceptance=True, enforce=True)
        assert ok is True and detail is None

    def test_failed_missions_are_not_gated(self):
        # acceptance=False path must never be blocked by the gate
        audit = _server_quality_audit(_body(headline="x"))
        ok, _ = _quality_gate_admission(audit, acceptance=False, enforce=True)
        assert ok is True
