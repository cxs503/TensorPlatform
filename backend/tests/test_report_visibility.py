"""Acceptance tests — report write-read visibility contract & lost-write healing.

Node ``p285w2b-n01``.  Every criterion below is a *number* produced by the test
itself, not a claim:

* **C1 readback identity** — a written payload read back is 100 % identical on
  ``markdown`` / ``figures`` / ``mission_id``; any mutation (truncation, single
  char, missing figure, changed owner id, vanished row) is a violation with a
  machine code, never a pass.
* **C2 degradation honesty** — a readback that cannot run reports
  ``ok=None/degraded``; it is never silently "fine".
* **C3 detection rate** — with 10 of 40 submissions injected as lost, the probe
  finds 10/10 (recall 1.0) and raises **zero** false positives on the 30 that
  are readable (negative control).
* **C4 healer success** — re-send under transient failures (p=0.30, 3 attempts)
  reaches >= 95 % verified recovery, and a claimed heal is always backed by a
  verification read (no unverified "healed").
* **C5 honest escalation** — missing/tampered payload → ``unhealable`` + alert;
  permanent failure → ``heal_failed`` + alert; nothing is dropped silently.
* **C6 audit chain** — every submission event is hash-chained; editing or
  dropping a line breaks ``verify()``.
* **C7 zero domain semantics** — the deny-list scanner reports zero hits on the
  new platform modules (they must stay domain-neutral).

Payloads are synthetic and domain-neutral throughout.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.report_visibility import (  # noqa: E402
    ALERT_HEAL_EXHAUSTED,
    ALERT_UNHEALABLE,
    KIND_HEAL_FAILED,
    KIND_LOST,
    KIND_UNHEALABLE,
    KIND_VERIFIED,
    HealPolicy,
    LostReportHealer,
    LostReportProbe,
    PayloadStore,
    ReportVisibilityService,
    SubmissionLedger,
    assert_readback,
    figures_projection,
    first_divergence,
    normalize_markdown,
)

MODULE_DIR = BACKEND / "services" / "report_visibility"
NEW_SOURCES = sorted(MODULE_DIR.glob("*.py")) + [BACKEND / "api" / "report_visibility.py"]


# ───────────────────────── synthetic payloads ─────────────────────────

def payload(markdown: str | None = None, *, figures: list[dict] | None = None,
            mission_id: int | None = 4242) -> dict:
    """Domain-neutral submission payload shaped like the real POST body."""
    return {
        "title": "synthetic submission",
        "summary": "write-read visibility fixture",
        "scenario_key": "platform-visibility",
        "mission_id": mission_id,
        "stage": "S1",
        "tags": ["synthetic"],
        "markdown": markdown if markdown is not None else
        "## case\n\nstep 1 → step 2\nvalue = 3.14159 ± 0.00001\n",
        "figures": figures or [],
    }


def fig(name: str, caption: str = "evidence") -> dict:
    return {"name": name, "caption": caption}


# ───────────────────────── C1: write-read identity ─────────────────────────

def test_c1_readback_accepts_byte_identical_markdown():
    """Identity holds for unicode, accents, tabs and long bodies."""
    body = payload("## 摘要\n\n温度 25 °C，误差 ±0.3 %；\n\tindented line\n" + "x" * 5000)
    verdict = assert_readback(body, dict(body))
    assert verdict.ok and not verdict.record_missing
    assert [c.field for c in verdict.checks] == ["markdown", "figures", "mission_id"]
    assert verdict.codes == ()


def test_c1_readback_flags_truncated_markdown_with_offset():
    body = payload("## case\n\nfull body text that must survive the round trip\n")
    observed = dict(body, markdown=body["markdown"][:12])
    verdict = assert_readback(body, observed)
    assert not verdict.ok
    assert verdict.codes == ("markdown_truncated",)
    assert first_divergence(body["markdown"], observed["markdown"]) == 12


def test_c1_readback_flags_single_character_mutation():
    body = payload("value = 1.0")
    verdict = assert_readback(body, dict(body, markdown="value = 1.1"))
    assert verdict.codes == ("markdown_mismatch",)
    check = verdict.checks[0]
    assert check.expected_digest != check.observed_digest
    assert "diverges at char 10" in check.detail


def test_c1_readback_flags_missing_figure():
    body = payload(figures=[fig("a.png"), fig("b.png", "second")])
    observed = dict(body, figures=figures_projection([fig("a.png")]))
    verdict = assert_readback(body, observed)
    assert not verdict.ok and verdict.codes == ("figures_mismatch",)
    assert "b.png" in verdict.checks[1].detail


def test_c1_readback_flags_mission_id_drift():
    body = payload(mission_id=1001)
    verdict = assert_readback(body, dict(body, mission_id=1002))
    assert verdict.codes == ("mission_id_mismatch",)


def test_c1_readback_missing_record_is_a_violation_not_a_pass():
    verdict = assert_readback(payload(), None)
    assert not verdict.ok and verdict.record_missing
    assert verdict.codes == ("record_missing",)
    assert verdict.checks == ()


def test_c1_readback_tolerates_crlf_transport_but_not_content_edits():
    """Negative control #1: newline transport is the *only* tolerated change."""
    body = payload("line one\nline two\n")
    assert assert_readback(body, dict(body, markdown="line one\r\nline two\r\n")).ok
    # …but a trailing space / dropped newline is still a violation.
    assert not assert_readback(body, dict(body, markdown="line one\nline two\n ")).ok
    assert not assert_readback(body, dict(body, markdown="line one\nline two")).ok


def test_c1_negative_control_two_hundred_healthy_rows_zero_violations():
    """Negative control #2: the contract must not cry wolf on healthy writes."""
    rng = random.Random(20261110)
    store = {}
    for rid in range(1, 201):
        body = payload(f"body {rid} " + "π" * rng.randint(0, 50),
                       figures=[fig("f.png")] if rid % 3 else [],
                       mission_id=9000 + rid)
        store[rid] = json.loads(json.dumps(body))  # simulate DB jsonb round-trip
    violations = [rid for rid, body in store.items()
                  if not assert_readback(body, store[rid]).ok]
    assert violations == []


def test_c2_ledger_marks_verified_and_lost_verdicts():
    ledger = SubmissionLedger()
    service = ReportVisibilityService(ledger=ledger, fetch=lambda rid: None)
    good = payload()
    ledger_ok = service.after_write(good, 11, observed=dict(good))
    assert ledger_ok.ok and ledger_ok.verdict.ok
    assert ledger.state(ledger_ok.submission_id).status == KIND_VERIFIED

    bad = service.after_write(payload(), 12, observed=None)  # vanished row
    assert not bad.ok and bad.verdict.codes == ("record_missing",)
    assert ledger.state(bad.submission_id).status == KIND_LOST


def test_c2_degradation_is_reported_not_swallowed():
    """A broken ledger must surface as ledger_ok=False, never as a silent pass."""
    service = ReportVisibilityService(ledger=SubmissionLedger())
    good = payload()
    service.ledger.submit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))  # type: ignore[assignment]
    result = service.after_write(good, 13, observed=dict(good))
    assert result.verdict.ok is True       # the write IS visible…
    assert result.ledger_ok is False       # …but the audit trail is not usable
    assert "disk full" in (result.ledger_error or "")


# ───────────────────────── C3: lost-write detection ─────────────────────────

def _populate(ledger: SubmissionLedger, n: int = 40) -> list[int]:
    ids = []
    for i in range(n):
        rid = 5000 + i
        entry = ledger.submit(payload(f"body {i}", mission_id=7000 + i), report_id=rid)
        ledger.mark(entry.submission_id, KIND_VERIFIED, detail={"readback": "ok"})
        ids.append(rid)
    return ids


def test_c3_ten_injected_losses_detected_100_percent():
    ledger = SubmissionLedger()
    ids = _populate(ledger, 40)
    dropped = set(ids[::4][:10])              # 10 of 40 vanish from the read side
    assert len(dropped) == 10
    readable = set(ids) - dropped

    probe = LostReportProbe(readable, ledger=ledger)
    result = probe.scan(ground_truth_missing=dropped)

    assert result.checked == 40
    assert result.detection_rate == 1.0, result.as_dict()
    assert set(result.missing) == dropped
    assert result.false_positives == ()


def test_c3_negative_control_no_losses_reported_when_all_readable():
    ledger = SubmissionLedger()
    ids = _populate(ledger, 30)
    result = LostReportProbe(set(ids), ledger=ledger).scan(
        ground_truth_missing=())
    assert result.missing == ()
    assert result.detection_rate is None       # no ground truth → no recall claim
    assert result.checked == 30


def test_c3_grace_window_defers_fresh_submissions():
    """Negative control #3: a just-written row must not be called lost."""
    now = {"t": 1000.0}
    ledger = SubmissionLedger(clock=lambda: now["t"])
    entry = ledger.submit(payload(), report_id=88)
    ledger.mark(entry.submission_id, KIND_VERIFIED)
    probe = LostReportProbe(set(), ledger=ledger, grace_s=30.0,
                            clock=lambda: now["t"])
    fresh = probe.scan()
    assert fresh.missing == () and fresh.deferred == (88,)

    now["t"] += 31.0                            # beyond the grace window → lost
    stale = probe.scan()
    assert stale.missing == (88,) and stale.deferred == ()


def test_c3_read_error_is_counted_as_missing_not_as_present():
    ledger = SubmissionLedger()
    ledger.submit(payload(), report_id=99)

    def broken(rid: int):
        raise TimeoutError("read timeout")

    result = LostReportProbe(broken, ledger=ledger).scan()
    assert result.missing == (99,)
    assert result.errors and "TimeoutError" in result.errors[0]


# ───────────────────────── C4: self-healing ─────────────────────────

def _lost_submission(ledger: SubmissionLedger, rid: int) -> str:
    entry = ledger.submit(payload(f"body {rid}", mission_id=rid), report_id=rid)
    ledger.mark(entry.submission_id, KIND_LOST, detail={"codes": ["record_missing"]})
    return entry.submission_id


def test_c4_heal_success_rate_above_95_percent_under_transient_failures():
    """Resend fails independently with p=0.30; 3 attempts ⇒ theoretical 97.3 %."""
    rng = random.Random(20261110)
    trials, recovered = 200, 0

    for t in range(trials):
        ledger = SubmissionLedger()
        sid = _lost_submission(ledger, 20000 + t)
        readable: set[int] = set()
        counter = {"n": 0}

        def resend(p: dict) -> int | None:
            if rng.random() < 0.30:
                return None                      # transient write failure
            counter["n"] += 1
            rid = 30000 + t * 10 + counter["n"]
            readable.add(rid)                    # the row is now readable
            return rid

        healer = LostReportHealer(ledger=ledger, resend=resend,
                                  verify=lambda r: r in readable,
                                  policy=HealPolicy(max_attempts=3),
                                  clock=lambda: 1.0)
        report = healer.heal()
        if report.healed:
            recovered += 1
            assert ledger.state(sid).status == "healed"

    rate = recovered / trials
    assert rate >= 0.95, f"verified heal rate {rate:.3f} < 0.95"


def test_c4_heal_is_never_claimed_without_a_verification_read():
    """Negative control #4: a re-send that does not become readable is a failure."""
    ledger = SubmissionLedger()
    sid = _lost_submission(ledger, 401)
    healer = LostReportHealer(
        ledger=ledger,
        resend=lambda p: 402,                    # POST returns an id…
        verify=lambda rid: False,                # …but the row is still unreadable
        policy=HealPolicy(max_attempts=2),
        clock=lambda: 1.0,
    )
    report = healer.heal()
    assert report.healed == 0 and report.success_rate == 0.0
    assert ledger.state(sid).status == KIND_HEAL_FAILED
    codes = [a["code"] for a in healer.alerts]
    assert ALERT_HEAL_EXHAUSTED in codes
    assert any(a["detail"].get("reason") == "resend_not_readable"
               for a in healer.alerts if a["code"] == ALERT_HEAL_EXHAUSTED)


def test_c4_missing_payload_is_escalated_as_unhealable():
    ledger = SubmissionLedger()
    sid = _lost_submission(ledger, 501)
    ledger._mem_payloads.clear()                 # snapshot lost
    healer = LostReportHealer(ledger=ledger, resend=lambda p: 502,
                              clock=lambda: 1.0)
    report = healer.heal()
    assert report.healed == 0
    assert ledger.state(sid).status == KIND_UNHEALABLE
    assert healer.alerts[0]["code"] == ALERT_UNHEALABLE
    assert report.outcomes[0].reason == "payload_missing_or_tampered"


def test_c4_tampered_payload_snapshot_is_refused(tmp_path):
    store = PayloadStore(tmp_path)
    saved = store.save("sid1", payload())
    path = tmp_path / "sid1.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["payload"]["markdown"] = "rewritten body"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert store.load("sid1") is None
    assert saved != ""


def test_c4_heal_report_metrics_are_machine_readable():
    ledger = SubmissionLedger()
    for rid in (601, 602, 603):
        _lost_submission(ledger, rid)
    readable: set[int] = set()

    def resend(p: dict) -> int | None:
        rid = 700 + len(readable)
        readable.add(rid)
        return rid

    report = LostReportHealer(ledger=ledger, resend=resend,
                              verify=lambda r: r in readable,
                              clock=lambda: 1.0).heal()
    doc = report.as_dict()
    assert doc["attempted"] == 3 and doc["healed"] == 3
    assert doc["success_rate"] == 1.0
    assert all({"submission_id", "healed", "attempts"} <= set(o) for o in doc["outcomes"])


# ───────────────────────── C6: audit chain ─────────────────────────

def test_c6_ledger_chain_detects_edited_and_dropped_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = SubmissionLedger(path)
    for rid in (801, 802, 803):
        entry = ledger.submit(payload(f"body {rid}"), report_id=rid)
        ledger.mark(entry.submission_id, KIND_VERIFIED)
    assert SubmissionLedger(path).verify()["ok"] is True

    lines = path.read_text(encoding="utf-8").splitlines()
    edited = [json.loads(line) for line in lines]
    edited[1]["kind"] = KIND_LOST                      # tamper
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in edited) + "\n",
                    encoding="utf-8")
    verdict = SubmissionLedger(path).verify()
    assert verdict["ok"] is False and verdict["broken_seq"] == 1

    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")   # drop a line
    assert SubmissionLedger(path).verify()["ok"] is False


def test_c6_probe_writes_detections_back_into_the_ledger():
    ledger = SubmissionLedger()
    ids = _populate(ledger, 6)
    dropped = {ids[1], ids[4]}
    service = ReportVisibilityService(ledger=ledger,
                                      fetch=lambda rid: None if rid in dropped else {"id": rid})
    result = service.scan()
    assert set(result.missing) == dropped
    statuses = [s.status for s in ledger.states() if s.report_id in dropped]
    assert statuses == [KIND_LOST, KIND_LOST]
    assert ledger.verify()["ok"] is True


# ───────────────────────── C7: zero domain semantics ─────────────────────────

def _load_domain_scanner():
    """Load ``tools/audit/domain_leak_scan.py`` by path (repo root is not a package)."""
    import importlib.util

    path = REPO / "tools" / "audit" / "domain_leak_scan.py"
    spec = importlib.util.spec_from_file_location("domain_leak_scan_m12439", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_c7_platform_modules_carry_no_domain_words():
    """The new platform code must stay domain-neutral (deny-list is data)."""
    scan = _load_domain_scanner().scan

    result = scan([str(p) for p in NEW_SOURCES])
    assert result["hit_count"] == 0, result["hits"][:5]
    assert result["files_scanned"], "scanner found no files — the guard is void"


def test_c7_markdown_normalisation_is_transport_only():
    assert normalize_markdown("a\r\nb\rc") == "a\nb\nc"
    assert normalize_markdown("a\n\nb") == "a\n\nb"

# ───────────────────────── C7b: structural guards (no second implementation) ─────────────────────────

def test_api_module_consumes_the_shared_contract_instead_of_forking_it():
    """The API layer must import the contract, never re-implement the comparison.

    The repo has already paid for a forked gate once (``_check_report_quality``
    was inlined in the API and drifted from ``services/report_gate.py``): a
    duplicated judgement goes green in tests while production silently relaxes.
    """
    source = (BACKEND / "api" / "report_visibility.py").read_text(encoding="utf-8")
    assert "from services.report_visibility import" in source
    assert "def assert_readback" not in source
    assert "def normalize_markdown" not in source
    assert "sha256" not in source  # digests live in exactly one place


def test_report_write_path_uses_one_insert_implementation():
    """Normal POST and self-heal must share the INSERT (no drifting second copy)."""
    reports_api = (BACKEND / "api" / "tensor_reports.py").read_text(encoding="utf-8")
    visibility_api = (BACKEND / "api" / "report_visibility.py").read_text(encoding="utf-8")
    assert "def _insert_report_row" in reports_api
    assert reports_api.count("INSERT INTO tensor_reports") == 1
    assert "INSERT INTO tensor_reports" not in visibility_api
    assert "_insert_report_row" in visibility_api
    # the write path must assert visibility right after committing
    assert 'out["readback"] = _readback_after_write(' in reports_api
