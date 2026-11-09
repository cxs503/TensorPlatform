#!/usr/bin/env python3
"""Regression: the capability instrument must degrade to UNKNOWN, never to a lie (m7794).

Prior behaviour (m2031 regression test T4) required a loud failure string when no
ledger source is readable, because an empty table reads as "all capabilities are
fine". This node adds the missing machine-readable half: the degraded answer must
carry ``UNKNOWN`` so that a caller (and the grading engine) can propagate
"unproven" instead of "not applicable". No exception may escape.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "ops" / "tp_worker.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("tp_worker_m7794", WORKER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_worker_m7794"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_worker_tool_returns_unknown_when_no_source(monkeypatch):
    worker = _load_worker()
    import tools.audit.ledger_consumer_audit_tool as consumer

    def _boom():
        raise FileNotFoundError("no readable ledger source")

    monkeypatch.setattr(consumer, "audit_capabilities", _boom)
    monkeypatch.setattr(worker, "_cap_sources", lambda: [])

    out = worker._tool_audit_caps()  # must not raise
    assert "读取能力清单失败" in out          # the failure stays loud (m2031 T4)
    assert "[证据等级] UNKNOWN" in out       # ... and is machine-readable
    assert "NO_EVIDENCE_SOURCE" in out


def test_worker_tool_returns_unknown_when_candidates_are_broken(monkeypatch, tmp_path):
    worker = _load_worker()
    import tools.audit.ledger_consumer_audit_tool as consumer

    def _boom():
        raise FileNotFoundError("no readable ledger source")

    broken = tmp_path / "capabilities.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(consumer, "audit_capabilities", _boom)
    monkeypatch.setattr(worker, "_cap_sources", lambda: [broken])

    out = worker._tool_audit_caps()
    assert "[证据等级] UNKNOWN" in out
    assert str(broken) in out  # the attempted path is named, not swallowed


def test_consumer_render_degrades_to_unknown():
    import tools.audit.ledger_consumer_audit_tool as consumer

    text = consumer.render({"capabilities": None, "reasons": ["NO_EVIDENCE_SOURCE"],
                            "path_rel": "artifacts/capability_ledger/capability_ledger.json"})
    assert text.startswith("[证据等级] UNKNOWN")
    assert "NO_EVIDENCE_SOURCE" in text


def test_consumer_audit_capabilities_reports_unknown(monkeypatch, tmp_path):
    import tools.audit.ledger_consumer_audit_tool as consumer
    from tools.audit import ledger_paths as LP

    missing = tmp_path / "absent.json"
    monkeypatch.setattr(consumer, "ledger_ref", lambda: {"status": "DEGRADED"})
    monkeypatch.setattr(LP, "render_candidates", lambda: [{"path": missing}])

    out = consumer.audit_capabilities()  # must not raise
    assert "[证据等级] UNKNOWN" in out
    assert str(missing) in out