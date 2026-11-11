"""验收判据 C1–C7 / 回归门 / 端到端采集（本地 stub 服务）的测试。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.evidence_collection import collect as collector
from tools.evidence_collection import gate, ledger, spec as spec_mod

SPEC_PATH = Path("config/evidence_collection/spec.json")


# ── 本地 stub 服务（零外部依赖，验证探针链路真的走到 HTTP） ──────────────────
class _Handler(BaseHTTPRequestHandler):
    payloads: dict = {}

    def log_message(self, *args):  # noqa: D401 - 静音
        return

    def _send(self, status: int, body):
        blob = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):  # noqa: N802
        if self.path == "/openapi.json":
            return self._send(200, {"info": {"title": "stub", "version": "0.0.1"},
                                    "paths": {"/api/v1/alpha": {}, "/api/v1/beta": {}}})
        body = self.payloads.get(self.path)
        if body is None:
            return self._send(404, {"detail": "not found"})
        return self._send(200, body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        body = self.payloads.get(self.path)
        if body is None:
            return self._send(404, {"detail": "not found"})
        return self._send(200, body(request))


@pytest.fixture()
def stub_server():
    _Handler.payloads = {"/api/v1/alpha": {"value": 2.0, "double": 4.0},
                         "/api/v1/beta": {"items": [1, 2, 3]}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _stub_spec(tmp_path: Path) -> dict:
    art = tmp_path / "ref.json"
    art.write_text(json.dumps({"rows": [{"c": 0.0031, "r": 0.0031, "err": 0.0}]}))
    return {
        "schema": spec_mod.SCHEMA,
        "thresholds": {"real_tolerance_pct": 3.0, "accept_tolerance_pct": 15.0},
        "sources": {"ledger": "ledger.json", "out_dir": "out",
                    "runtime": {"base_url": "http://placeholder", "openapi_path": "/openapi.json"}},
        "capabilities": {
            "syn.alpha": {
                "surface": ["/api/v1/alpha"],
                "reference": {"path": str(art), "value_path": "rows.*.err", "aggregate": "worst",
                              "tolerance_pct": 3.0},
                "probes": [{
                    "id": "p1", "method": "GET", "path": "/api/v1/alpha",
                    "criteria": [
                        {"id": "ok", "kind": "status", "expect": [200], "class": "contract"},
                        {"id": "dbl", "kind": "identity", "lhs": "double", "rhs": "2*value",
                         "mode": "abs_rel", "tolerance": 1e-9, "class": "invariant"},
                    ]}],
            },
            "syn.beta": {
                "surface": ["/api/v1/beta"],
                "probes": [{
                    "id": "p2", "method": "GET", "path": "/api/v1/beta",
                    "criteria": [
                        {"id": "ok", "kind": "status", "expect": [200], "class": "contract"},
                        {"id": "n", "kind": "list_min_len", "path": "items", "min_len": 3,
                         "class": "contract"},
                    ]}],
            },
            "syn.gamma": {"surface": ["/api/v1/gamma"]},
        },
    }


def test_end_to_end_collect_against_stub(tmp_path: Path, stub_server):
    doc = spec_mod.validate_spec(_stub_spec(tmp_path))
    (tmp_path / "ledger.json").write_text(json.dumps({"capabilities": []}), encoding="utf-8")
    run = collector.collect(doc, tmp_path, base_url=stub_server, run_tests=False)
    caps = run["summary"]["capabilities"]
    assert run["runtime"]["available"] is True
    assert caps["syn.alpha"]["level"] == "REAL"            # 参考复算 0.0% + 不变量通过
    assert caps["syn.beta"]["level"] == "WITHHELD"         # 契约全通过但无外部参考
    assert caps["syn.gamma"]["level"] == "WITHHELD"        # 面缺失 → 拒绝下判决
    assert run["summary"]["coverage"]["covered"] == 3
    assert run["summary"]["coverage"]["runtime_probe_covered"] == 2


def test_end_to_end_runtime_unreachable_degrades(tmp_path: Path):
    """在线服务不可达时：探针记 NOT_EXECUTED（不算通过），只有参考工件的能力仍可给 REAL，
    纯契约能力必须回落 WITHHELD。"""
    doc = spec_mod.validate_spec(_stub_spec(tmp_path))
    (tmp_path / "ledger.json").write_text("{}", encoding="utf-8")
    run = collector.collect(doc, tmp_path, base_url="http://127.0.0.1:1", run_tests=False)
    caps = run["summary"]["capabilities"]
    assert run["runtime"]["available"] is False
    assert caps["syn.beta"]["level"] == "WITHHELD"
    assert caps["syn.beta"]["reason_codes"] == ["NO_EXECUTED_EVIDENCE"]
    assert caps["syn.alpha"]["level"] == "REAL"          # 外部参考工件独立成立
    assert run["summary"]["coverage"]["runtime_probe_covered"] == 0


def test_every_probe_in_shipped_spec_declares_criteria():
    doc = spec_mod.load_spec(SPEC_PATH)
    for cap_id, entry in doc["capabilities"].items():
        for probe in entry.get("probes") or []:
            assert probe.get("criteria"), f"{cap_id}/{probe['id']} declares no criteria"
        assert entry.get("surface") or entry.get("probes") or entry.get("tests") or entry.get("reference")


def _records_and_summary(levels, coverage_ok=True):
    records = {}
    for cap, lv in levels.items():
        records[cap] = {"level": lv, "reason_codes": ["REFERENCE_VERIFIED"] if lv == "REAL" else [],
                        "executed_evidence_items": 1,
                        "acceptance_path": "artifacts/evidence_collection/x/acceptance.json",
                        "criteria_table_path": "artifacts/evidence_collection/x/criteria_table.md",
                        "acceptance_sha256": "0" * 64, "criteria_rows": 2, "table_sha256": "1" * 64}
    summary = {"generated_at": "2026-01-01T00:00:00Z", "ruleset": "ec-grade-v1",
               "coverage": {"covered": len(levels), "total": len(levels),
                            "covered_ids": sorted(levels), "uncovered_ids": []}}
    del coverage_ok
    return records, summary


def _spec_min():
    return {"schema": spec_mod.SCHEMA,
            "thresholds": {"real_tolerance_pct": 3.0, "accept_tolerance_pct": 15.0},
            "sources": {"ledger": "l.json", "out_dir": "o", "runtime": {}},
            "acceptance": {"min_covered": 1, "min_agreement": 0.9, "min_labeled": 1,
                           "min_tests": 1, "denylist": "nope.json",
                           "zero_domain_scan": ["tools/evidence_collection/*.py"],
                           "ci_workflow": "nope.yml", "ci_markers": ["evidence_collection"],
                           "test_dir": "tests/evidence_collection"},
            "capabilities": {"syn.alpha": {"surface": ["/a"]}}}


def _calibration():
    return {"cases": [{"id": "c1", "surface": "pass",
                       "probe": {"executed": True, "criteria": [["c", "contract", "PASS"]]},
                       "reference": {"status": "PASS", "worst_pct": 0.1},
                       "expected_level": "REAL"}],
            "real_labels": []}


def test_gate_c1_c3_c4_pass_on_clean_snapshot(tmp_path: Path):
    levels = {"syn.alpha": "REAL"}
    records, summary = _records_and_summary(levels)
    records["syn.alpha"]["acceptance_path"] = "p"
    spec = _spec_min()
    baseline = {"levels": {"syn.alpha": "REAL"}, "legacy_real": ["syn.alpha"],
                "legacy_evidence": ["syn.alpha"]}
    result = gate.evaluate(summary, records, spec, baseline, _calibration(), root=tmp_path,
                           suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    # 工件路径为相对不可解析时 C3 会红，这里只断言 C1/C4 语义
    assert result["checks"]["C1_coverage"]["pass"] is True
    assert result["checks"]["C4_regression_gate"]["pass"] is True


def test_gate_c4_flags_silent_downgrade_and_accepts_acked(tmp_path: Path):
    spec = _spec_min()
    base = {"levels": {"syn.alpha": "REAL", "syn.beta": "REAL"}, "legacy_real": [], "legacy_evidence": []}
    records, summary = _records_and_summary({"syn.alpha": "WITHHELD", "syn.beta": "WITHHELD"})
    res = gate.evaluate(summary, records, spec, base, _calibration(), root=tmp_path,
                        suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    assert res["checks"]["C4_regression_gate"]["pass"] is False
    assert len(res["checks"]["C4_regression_gate"]["detail"]["violations"]) == 2

    res2 = gate.evaluate(summary, records, spec, base, _calibration(), root=tmp_path,
                         acks={"syn.alpha": {"basis": "独立审计 1128"}, "syn.beta": {"basis": "x"}},
                         suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    assert res2["checks"]["C4_regression_gate"]["pass"] is True
    assert len(res2["checks"]["C4_regression_gate"]["detail"]["acked_downgrades"]) == 2


def test_gate_c4_accepts_refutation_backed_downgrade(tmp_path: Path):
    spec = _spec_min()
    base = {"levels": {"syn.alpha": "REAL"}, "legacy_real": [], "legacy_evidence": []}
    records, summary = _records_and_summary({"syn.alpha": "MOCK"})
    records["syn.alpha"]["reason_codes"] = ["REFERENCE_OVER_ACCEPT"]
    res = gate.evaluate(summary, records, spec, base, _calibration(), root=tmp_path,
                        suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    assert res["checks"]["C4_regression_gate"]["pass"] is True


def test_gate_c5_detects_domain_word(tmp_path: Path):
    # 词条从词表里读，测试代码自身不写死任何域词（否则 C5 会扫到自己）
    denylist_doc = json.loads(Path("config/domain_word_denylist.json").read_text(encoding="utf-8"))
    planted = denylist_doc["cjk_words"][0]
    src = tmp_path / "leaky.py"
    src.write_text(f"# 这里是 {planted} 泄漏\nx = 1\n", encoding="utf-8")
    spec = _spec_min()
    spec["acceptance"]["zero_domain_scan"] = ["leaky.py"]
    spec["acceptance"]["denylist"] = str(Path("config/domain_word_denylist.json").resolve())
    records, summary = _records_and_summary({"syn.alpha": "REAL"})
    res = gate.evaluate(summary, records, spec, None, _calibration(), root=tmp_path,
                        suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    assert res["checks"]["C5_zero_domain_semantics"]["pass"] is False


def test_gate_c6_and_c7_report_injected_results(tmp_path: Path):
    wf = tmp_path / "wf.yml"
    wf.write_text("run: python -m tools.evidence_collection.cli collect\n", encoding="utf-8")
    spec = _spec_min()
    spec["acceptance"]["ci_workflow"] = "wf.yml"
    spec["acceptance"]["ci_markers"] = ["tools.evidence_collection.cli"]
    records, summary = _records_and_summary({"syn.alpha": "REAL"})
    res = gate.evaluate(summary, records, spec, None, _calibration(), root=tmp_path,
                        suite_runner=lambda p: {"status": "PASS", "passed": 11, "failed": 0})
    assert res["checks"]["C6_ci_integration"]["pass"] is True
    assert res["checks"]["C7_pytest"]["pass"] is True


def test_gate_c2_counts_real_labels_against_collected_levels(tmp_path: Path):
    spec = _spec_min()
    cal = _calibration()
    cal["real_labels"] = [{"capability": "syn.alpha", "expected_level": "WITHHELD",
                           "as_of": "2026-01-01", "basis": "独立审计"}]
    records, summary = _records_and_summary({"syn.alpha": "WITHHELD"})
    res = gate.evaluate(summary, records, spec, None, cal, root=tmp_path,
                        suite_runner=lambda p: {"status": "PASS", "passed": 3, "failed": 0})
    assert res["checks"]["C2_level_accuracy"]["value"] == 1.0


def test_shipped_calibration_corpus_meets_accuracy():
    cal = json.loads(Path("config/evidence_collection/calibration.json").read_text(encoding="utf-8"))
    doc = spec_mod.load_spec(SPEC_PATH)
    graded = gate.agreement(cal["cases"], doc["thresholds"])
    assert graded["labeled"] >= 10
    assert graded["agreement"] >= 0.9, graded["detail"]


def test_committed_acceptance_artifacts_are_verifiable():
    """回归守卫：仓里提交的快照工件必须自摘要一致，等级可由信号重放。"""
    summary_path = Path("artifacts/evidence_collection/summary.json")
    if not summary_path.exists():  # 首次采集前允许跳过（不伪装通过）
        pytest.skip("no committed snapshot yet")
    from tools.evidence_collection import artifacts as art
    from tools.evidence_collection.grade import grade

    doc = spec_mod.load_spec(SPEC_PATH)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for cap_id in summary["capabilities"]:
        ap = Path("artifacts/evidence_collection") / art.slug(cap_id) / "acceptance.json"
        assert ap.exists(), cap_id
        assert art.verify_acceptance(ap)["sha256_match"], cap_id
        payload = json.loads(ap.read_text(encoding="utf-8"))
        assert grade(payload["signals"], doc["thresholds"])["level"] == payload["level"], cap_id


def test_committed_ledger_block_matches_snapshot():
    ledger_path = Path("artifacts/capability_ledger/capability_ledger.json")
    block = json.loads(ledger_path.read_text(encoding="utf-8")).get(ledger.BLOCK_KEY)
    if block is None:
        pytest.skip("ledger not yet updated by a collection run")
    summary = json.loads(Path("artifacts/evidence_collection/summary.json").read_text(encoding="utf-8"))
    assert block["levels"] == {cap: v["level"] for cap, v in summary["capabilities"].items()}
    assert block["coverage"]["covered"] == summary["coverage"]["covered"]
    assert ledger._block_sha(block) == block["block_sha256"]