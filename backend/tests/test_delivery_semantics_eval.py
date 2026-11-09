"""Real-corpus evaluation of the delivery-state classifier (node p92w2b-n08).

Unlike the synthetic fixtures in ``test_delivery_state.py``, these tests grade the
classifier against a *measured* corpus: 60 real ``succeeded`` missions sampled
from the platform API, hand-labelled against a frozen rubric, with all artifacts
committed under ``runs/p92w2b-n08/``.

Acceptance criteria (machine-checked here):
  a) macro-F1 >= 0.90 against the 60-unit manual annotation
  b) weak-label recall >= 0.85 and precision >= 0.90
  c) fail-closed: never ``delivered_complete`` when an artifact fact is missing
  d) shadow mode: pure classification, zero side effects, bitwise-stable output
  e) zero domain semantics: domain-term scan hits = 0

The corpus/labels/metrics files are data artifacts, not test fixtures: if they
are absent (e.g. a shallow checkout) the corpus tests skip instead of silently
passing.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

from api.domain.delivery_state import (
    MACHINE_CHECKABLE_PATTERNS,
    assess_delivery,
    normalize_artifacts,
    scan_domain_semantics,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN = REPO_ROOT / "runs" / "p92w2b-n08"
CORPUS = RUN / "corpus_eval_raw.json"
LABELS = RUN / "labels_human.json"
METRICS = RUN / "metrics.json"

STATES = ("delivered_complete", "delivered_partial", "suspect_undeclared")


# ───────────────────────── helpers ─────────────────────────

def _load():
    if not (CORPUS.exists() and LABELS.exists()):
        pytest.skip("real-corpus artifacts not present in this checkout")
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    labels = {u["mission_id"]: u["label"]
              for u in json.loads(LABELS.read_text(encoding="utf-8"))["units"]}
    return raw, labels


def _metrics(y_true, y_pred):
    def one(lab):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        return prec, rec, f1
    per = {lab: one(lab) for lab in STATES}
    macro = sum(per[lab][2] for lab in STATES) / len(STATES)
    return per, macro


# ───────────────────────── criteria a + b ─────────────────────────

class TestRealCorpusCriteria:
    def test_corpus_is_real_and_pinned(self):
        """60 units, labels cover exactly the corpus, provenance recorded."""
        raw, labels = _load()
        assert len(raw) == 60
        assert set(labels) == {int(k) for k in raw}
        corpus_meta = json.loads((RUN / "corpus_population.json").read_text(encoding="utf-8"))
        assert corpus_meta["status_filter"] == "succeeded"
        assert corpus_meta["population_usable"] >= 60
        assert corpus_meta["seed"] == 20261109

    def test_macro_f1_meets_threshold(self):
        """Criterion (a): macro-F1 >= 0.90 on the hand-labelled real units."""
        raw, labels = _load()
        ids = sorted(labels)
        y_true = [labels[i] for i in ids]
        y_pred = [assess_delivery((raw[str(i)] or {}).get("result")).state for i in ids]
        per, macro = _metrics(y_true, y_pred)
        assert macro >= 0.90, f"macro-F1 {macro:.4f} < 0.90 (per-class {per})"
        # every state must be reachable: no dead class on real data
        for lab in STATES:
            assert per[lab][1] > 0.0, f"recall of {lab} is 0 — state unreachable on real data"

    def test_weak_label_recall_and_precision(self):
        """Criterion (b): weak-label recall >= 0.85, precision >= 0.90."""
        raw, labels = _load()
        ids = sorted(labels)
        pred = [assess_delivery((raw[str(i)] or {}).get("result")).state for i in ids]
        weak = []
        for i in ids:
            facts = normalize_artifacts((raw[str(i)] or {}).get("result"))
            weak.append(facts.implementation_present and len(facts.verification_text.strip()) >= 20)
        pos = [k for k, w in enumerate(weak) if w]
        hits = [k for k in pos if pred[k] == "delivered_complete"]
        recall = len(hits) / len(pos) if pos else 0.0
        precision = (len(hits) / sum(1 for p in pred if p == "delivered_complete")
                     if any(p == "delivered_complete" for p in pred) else 0.0)
        assert recall >= 0.85, f"weak-label recall {recall:.4f} < 0.85"
        assert precision >= 0.90, f"weak-label precision {precision:.4f} < 0.90"

    def test_metrics_artifact_matches_recomputation(self):
        """Anti-hand-transcription: committed metrics.json equals live recomputation."""
        raw, labels = _load()
        recorded = json.loads(METRICS.read_text(encoding="utf-8"))
        ids = sorted(labels)
        y_true = [labels[i] for i in ids]
        y_pred = [assess_delivery((raw[str(i)] or {}).get("result")).state for i in ids]
        _, macro = _metrics(y_true, y_pred)
        assert round(macro, 4) == recorded["primary"]["macro_f1"]
        assert recorded["primary"]["criterion_a_met"] is (macro >= 0.90)


# ───────────────────────── criterion c ─────────────────────────

class TestFailClosed:
    @pytest.mark.parametrize("result", [
        {"checks": "12 passed", "headline": "done"},
        {"changed_files": ["a.py"], "headline": "done"},
        {"changed_files": [], "checks": "12 passed", "headline": "done"},
        {"changed_files": ["a.py"], "checks": None, "headline": "done"},
        {"changed_files": ["a.py"], "checks": "   ", "headline": "done"},
        {"headline": "all tests passed and shipped"},   # free text claim only
        {},
        None,
    ])
    def test_never_complete_without_artifact_facts(self, result):
        assert assess_delivery(result).state != "delivered_complete"

    def test_on_disk_changes_counts_as_implementation(self):
        """Production payload: implementation often arrives as on_disk_changes."""
        assessment = assess_delivery(
            {"on_disk_changes": ["src/a.py"], "checks": "12 passed", "headline": "x"})
        assert assessment.state == "delivered_complete"
        assert assessment.facts["implementation_count"] == 1

    def test_checks_as_string_is_accepted(self):
        """Production payload: checks is plain text, not a dict."""
        assessment = assess_delivery(
            {"changed_files": ["a.py"], "checks": "pytest -q -> 40 passed", "headline": "x"})
        assert assessment.state == "delivered_complete"


# ───────────────────────── criterion d ─────────────────────────

class TestShadowModePurity:
    def test_classifier_has_no_io_or_db_side_effects(self):
        src = inspect.getsource(sys.modules[assess_delivery.__module__])
        for token in ("db.execute", "db.commit", "SessionLocal", "urlopen", "requests.",
                      "open(", "subprocess"):
            assert token not in src, f"side-effect token {token!r} found in classifier"

    def test_classification_is_deterministic(self):
        raw, labels = _load()
        ids = sorted(labels)
        once = [assess_delivery((raw[str(i)] or {}).get("result")).to_dict() for i in ids]
        twice = [assess_delivery((raw[str(i)] or {}).get("result")).to_dict() for i in ids]
        sha = lambda rows: hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        assert sha(once) == sha(twice)

    def test_heartbeat_checkpoint_is_shadow_and_off_switchable(self, monkeypatch):
        """70% checkpoint: no DB writes, off-switch is a no-op, injection at 70%."""
        from api import tensor_missions
        from datetime import datetime, timedelta, timezone

        row = {"id": 1, "budget": {"max_tool_calls": 100, "max_wall_seconds": 1000},
               "created_at": datetime.now(timezone.utc) - timedelta(seconds=750)}

        monkeypatch.setenv("TP_MVD_CHECKPOINT", "off")
        assert tensor_missions._mvd_heartbeat_checkpoint(row, {"tool_calls": 99}) is None

        monkeypatch.setenv("TP_MVD_CHECKPOINT", "shadow")
        out = tensor_missions._mvd_heartbeat_checkpoint(row, {"tool_calls": 5})
        assert out["triggered"] is True
        assert out["budget_pct"] >= 70.0
        assert out["mode"] == "shadow"
        assert "closure_instruction" in out      # unfinished → closure instruction injected
        assert out["shadow_mode"] is True

    def test_heartbeat_checkpoint_below_threshold_has_no_instruction(self, monkeypatch):
        from api import tensor_missions
        from datetime import datetime, timedelta, timezone

        monkeypatch.setenv("TP_MVD_CHECKPOINT", "shadow")
        row = {"id": 2, "budget": {"max_tool_calls": 100, "max_wall_seconds": 1000},
               "created_at": datetime.now(timezone.utc) - timedelta(seconds=10)}
        out = tensor_missions._mvd_heartbeat_checkpoint(row, {"tool_calls": 3})
        assert out["triggered"] is False
        assert "closure_instruction" not in out


# ───────────────────────── criterion e ─────────────────────────

class TestZeroDomainSemantics:
    @pytest.mark.parametrize("rel", [
        "backend/api/domain/delivery_state.py",
        "backend/api/domain/mvd_checkpoint.py",
        "backend/api/kpi_delivery.py",
        "backend/tests/test_delivery_semantics_eval.py",
    ])
    def test_no_domain_terms(self, rel):
        path = REPO_ROOT / rel
        assert path.exists(), rel
        hits = scan_domain_semantics(path.read_text(encoding="utf-8"))
        assert hits == [], f"{rel}: domain terms {hits}"

    def test_scanner_is_not_vacuous(self):
        """The scanner must actually fire on a term from its own list (control).

        The term is taken from the scanner's list programmatically so this test
        file itself carries no domain vocabulary (criterion e).
        """
        from api.domain.delivery_state import _DOMAIN_TERMS_FOR_SCAN

        term = _DOMAIN_TERMS_FOR_SCAN[0]
        assert scan_domain_semantics(f"def {term}_resistance(): pass")


# ───────────────────────── layered KPI denominator ─────────────────────────

class _Row:
    def __init__(self, status, result):
        self._mapping = {"status": status, "result": result}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return _FakeResult(self._rows)


class TestLayeredKpi:
    def test_failed_missions_stay_out_of_delivery_denominator(self):
        """A failed mission with an empty result must not inflate undeclared rate."""
        from api.kpi_delivery import compute_delivery_kpis

        rows = [
            _Row("succeeded", {"changed_files": ["a.py"], "checks": "9 passed", "headline": "x"}),
            _Row("succeeded", {}),
            _Row("failed", {}),
            _Row("stopped", {}),
        ]
        out = compute_delivery_kpis(_FakeSession(rows))
        assert out["denominator_key"] == "succeeded"
        assert out["denominators"] == {"terminal": 4, "succeeded": 2,
                                       "succeeded_with_payload": 1, "not_succeeded": 2}
        assert out["layers"]["succeeded"]["denominator"] == 2
        assert out["rates"]["complete_rate"] == 50.0
        assert out["rates"]["forced_finish_rate"] == 50.0

    def test_definitions_carry_denominator(self):
        from api.kpi_delivery import get_delivery_kpi_definitions

        defs = {d["key"]: d for d in get_delivery_kpi_definitions()}
        # P92W3-N10: the binary success rate keeps its own (terminal) denominator;
        # every delivery-rate KPI stays on the ``succeeded`` layer.
        assert defs["success_rate"]["denominator"] == "terminal"
        for key in ("complete_rate", "partial_rate", "forced_finish_rate", "forced_closure_rate"):
            assert defs[key]["denominator"] == "succeeded"
            assert defs[key]["description"].endswith("/ succeeded * 100")