"""Engine tests: automatic promotion / demotion (p262w1-N07, F2/F3/F4/F5)."""
from __future__ import annotations

import ast
import json
import re
import shutil
import time
from pathlib import Path

import pytest

from chain_fixtures import write_case

from tools.capability_auto_audit.auto_audit_engine import AutoAuditEngine
from tools.capability_auto_audit.evidence_chain import ChainSpec

ROOT = Path(__file__).resolve().parents[2]

# the modules delivered by this node -- the zero-domain-semantics scan target
NEW_MODULES = (
    "tools/capability_auto_audit/evidence_chain.py",
    "tools/capability_auto_audit/level_machine.py",
    "tools/capability_auto_audit/append_only_log.py",
    "tools/capability_auto_audit/auto_audit_engine.py",
    "tools/capability_auto_audit/api.py",
)

DEADLINE_S = 5.0  # acceptance criterion: promote/demote within 5 s


# --------------------------------------------------------------------------- #
# F2 -- promotion
# --------------------------------------------------------------------------- #
def test_complete_chain_promotes_within_five_seconds(engine, make_case):
    start = time.perf_counter()
    make_case("syn.promo", pytest_kind="junit", passed=6, measured=1.01)
    engine.watch_once()
    elapsed = time.perf_counter() - start

    assert engine.level_of("syn.promo") == "REAL"
    assert engine.outcomes[-1].action == "UPGRADE"
    assert elapsed < DEADLINE_S, f"promotion took {elapsed:.3f}s"

    entry = engine.log.entries[-1]
    assert entry.from_level == "WITHHELD" and entry.to_level == "REAL"
    assert entry.reason_code == "CHAIN_COMPLETE"


def test_second_audit_of_a_complete_chain_is_idempotent(engine, make_case):
    make_case("syn.idem")
    engine.audit("syn.idem")
    second = engine.audit("syn.idem")
    assert second.action == "HOLD"
    assert second.to_level == "REAL"
    assert engine.level_of("syn.idem") == "REAL"
    assert len(engine.log) == 2                      # both operations are audited
    assert engine.log.verify()["ok"] is True


def test_unknown_capability_is_withheld_not_invented(engine):
    outcome = engine.audit("syn.unknown")
    assert outcome.from_level == "WITHHELD"
    assert outcome.action == "HOLD"
    assert outcome.complete is False


# --------------------------------------------------------------------------- #
# F3 -- demotion
# --------------------------------------------------------------------------- #
def test_deleting_the_verified_artifact_demotes_within_five_seconds(engine, make_case):
    case_dir = make_case("syn.demo")
    engine.watch_once()
    assert engine.level_of("syn.demo") == "REAL"

    start = time.perf_counter()
    shutil.rmtree(case_dir)                          # the verified/ artifact is gone
    engine.watch_once()
    elapsed = time.perf_counter() - start

    assert engine.level_of("syn.demo") == "WITHHELD"
    assert engine.outcomes[-1].action == "DOWNGRADE"
    assert "EVIDENCE_LOSS" in engine.outcomes[-1].reason_code
    assert elapsed < DEADLINE_S, f"demotion took {elapsed:.3f}s"


def test_regression_demotes_to_mock_not_withheld(engine, make_case):
    make_case("syn.reg")
    engine.watch_once()
    assert engine.level_of("syn.reg") == "REAL"

    make_case("syn.reg", passed=4, failed=1)          # the suite now fails
    engine.watch_once()
    assert engine.level_of("syn.reg") == "MOCK"
    assert engine.outcomes[-1].failure_kind == "regression"


def test_demoted_capability_can_be_promoted_again(engine, make_case):
    case_dir = make_case("syn.cycle")
    engine.watch_once()
    shutil.rmtree(case_dir)
    engine.watch_once()
    assert engine.level_of("syn.cycle") == "WITHHELD"

    write_case(case_dir.parent, "syn.cycle", passed=7)
    engine.watch_once()
    assert engine.level_of("syn.cycle") == "REAL"
    assert [o.action for o in engine.outcomes] == ["UPGRADE", "DOWNGRADE", "UPGRADE"]


# --------------------------------------------------------------------------- #
# watcher thread -- the same guarantees without manual polling
# --------------------------------------------------------------------------- #
def _wait_for(predicate, timeout: float = DEADLINE_S, interval: float = 0.02) -> float:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if predicate():
            return time.perf_counter() - start
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def test_background_watcher_promotes_and_demotes_within_five_seconds(engine, make_case):
    case_dir = make_case("syn.watch")
    thread, stop = engine.start_background(interval=0.05)
    try:
        waited = _wait_for(lambda: engine.level_of("syn.watch") == "REAL")
        assert waited < DEADLINE_S

        shutil.rmtree(case_dir)
        waited = _wait_for(lambda: engine.level_of("syn.watch") == "WITHHELD")
        assert waited < DEADLINE_S
    finally:
        stop.set()
        thread.join(timeout=5)
    assert engine.log.verify()["ok"] is True


# --------------------------------------------------------------------------- #
# F4 -- ten operations, ten verifiable log entries
# --------------------------------------------------------------------------- #
def test_ten_audit_operations_produce_ten_chained_entries(engine, make_case):
    make_case("syn.ops_a")
    make_case("syn.ops_b")
    operations = []
    for round_index in range(5):
        for cap in ("syn.ops_a", "syn.ops_b"):
            case_dir = make_case.root / cap
            if not case_dir.is_dir():
                write_case(make_case.root, cap, measured=1.01)
            elif round_index % 2 == 1:
                shutil.rmtree(case_dir)
            operations.append(engine.audit(cap))

    assert len(operations) == 10
    verify = engine.log.verify()
    assert verify["ok"] is True
    assert verify["entries"] == 10 == len(engine.log)
    assert [e.seq for e in engine.log.entries] == list(range(1, 11))
    for entry in engine.log.entries:
        assert entry.recompute_sha256() == entry.sha256
        assert len(entry.sha256) == 64
    # the log records both directions
    actions = {e.payload["outcome"] for e in engine.log.entries}
    assert {"UPGRADE", "DOWNGRADE"} <= actions, actions
    assert verify["head_sha256"] == engine.log.head_sha256


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def test_state_survives_restart(tmp_path, make_case):
    paths = dict(verified_dir=make_case.root,
                 state_path=tmp_path / "state" / "ledger_state.json",
                 log_path=tmp_path / "state" / "audit_log.jsonl")
    first = AutoAuditEngine(**paths, spec=ChainSpec())
    make_case("syn.persist")
    first.watch_once()
    assert first.level_of("syn.persist") == "REAL"

    second = AutoAuditEngine(**paths, spec=ChainSpec())
    assert second.level_of("syn.persist") == "REAL"
    assert len(second.log) == 1
    state = json.loads(paths["state_path"].read_text(encoding="utf-8"))
    body = {k: v for k, v in state.items() if k != "digest"}
    from tools.capability_auto_audit.evidence_chain import sha256_json

    assert state["digest"] == sha256_json(body)


def test_summary_reports_levels_and_integrity(engine, make_case):
    make_case("syn.sum")
    engine.watch_once()
    summary = engine.summary()
    assert summary["capabilities"] == 1
    assert summary["levels"] == {"syn.sum": "REAL"}
    assert summary["distribution"]["REAL"] == 1
    assert summary["audit_integrity"]["ok"] is True


# --------------------------------------------------------------------------- #
# F5 -- zero domain semantics
# --------------------------------------------------------------------------- #
def _lexicon_terms() -> tuple[list[str], list[str]]:
    lexicon = ROOT / "tools" / "evidence_domain_lexicon.json"
    if not lexicon.is_file():  # pragma: no cover - lexicon is part of the repo
        return [], []
    data = json.loads(lexicon.read_text(encoding="utf-8"))
    return list(data.get("ascii_terms", [])), list(data.get("cjk_terms", []))


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def test_new_modules_carry_no_domain_vocabulary():
    ascii_terms, cjk_terms = _lexicon_terms()
    assert ascii_terms, "domain lexicon missing -- cannot assert zero-domain-semantics"
    hits = []
    for relative in NEW_MODULES:
        path = ROOT / relative
        assert path.is_file(), relative
        for text in _string_constants(path):
            lowered = text.lower()
            for term in ascii_terms:
                if re.search(rf"\b{re.escape(term)}\b", lowered):
                    hits.append((relative, term, text[:60]))
            for term in cjk_terms:
                if term in text:
                    hits.append((relative, term, text[:60]))
    assert hits == [], f"domain vocabulary leaked into platform code: {hits}"


def test_engine_behaves_identically_for_a_synthetic_capability_name(tmp_path):
    """A made-up capability id must be handled like any other -- no domain shortcuts."""
    verified = tmp_path / "verified"
    write_case(verified, "zz.frobnicate", passed=8, measured=0.998)
    engine = AutoAuditEngine(verified_dir=verified,
                             state_path=tmp_path / "state.json",
                             log_path=tmp_path / "log.jsonl",
                             spec=ChainSpec())
    outcome = engine.audit("zz.frobnicate")
    assert outcome.to_level == "REAL"
    verify = {c["name"]: c for c in outcome.checks}
    assert verify["error"]["numbers"]["gate_source"] == "metric_gate"