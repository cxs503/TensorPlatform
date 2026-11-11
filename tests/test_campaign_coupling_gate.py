"""跨节点耦合闸 (tools/integration_gate/coupling.py) 与工件快照 (archive.py) 的测试。

纪律：本文件**只用合成域名** (alpha/beta/acme/metric_*/std-0) —— 平台仓零领域语义，
域数据经 data/campaigns/<c>/*.json 注入；本文件不出现任何域词条（有 pytest 门反查）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.integration_gate import archive as archive_mod  # noqa: E402
from tools.integration_gate import coupling as cpl  # noqa: E402
from tools.integration_gate import ledger as ledger_mod  # noqa: E402
from tools.integration_gate.model import PairResult  # noqa: E402
from tools.integration_gate.report import evaluate_integration_criteria  # noqa: E402

SHA = "0" * 64


# ─────────────────────────── fixtures (合成域) ───────────────────────────

def iface(**over):
    base = {
        "id": "IF-A",
        "producer": "demo-alpha",
        "consumer": "demo-beta",
        "payload": {"id": "flux", "unit": "kg/s", "fields": ["flux", "domain_status"],
                    "coverage": {"min": 0.0, "max": 1.0}},
        "requirement": {"unit": "kg/s", "min": 0.0, "max": 0.5, "fallback": "reject"},
        "doc_ref": "docs/demo.md",
    }
    base.update(over)
    return base


def iface_doc(*edges):
    return {"schema": cpl.INTERFACES_SCHEMA, "interfaces": list(edges) or [iface()]}


def decl(node: str, value, source_ref: str = "src/x.py") -> dict:
    return {"node": node, "value": value,
            "source": {"ref": source_ref, "sha256": SHA}}


def law_decl(node: str, params: dict[str, float]) -> dict:
    return {"node": node, "params": params,
            "source": {"ref": "src/law.py", "sha256": SHA}}


def assume_doc(*keys) -> dict:
    return {"schema": cpl.ASSUMPTIONS_SCHEMA, "keys": list(keys) or []}


# ─────────────────────────── schema gates ───────────────────────────

def test_validate_interfaces_accepts_minimal_doc():
    doc = cpl.validate_interfaces(iface_doc())
    assert doc["interfaces"][0]["id"] == "IF-A"


def test_validate_interfaces_rejects_inverted_range():
    bad = iface(payload={"id": "flux", "unit": "kg/s",
                         "coverage": {"min": 1.0, "max": 0.0}})
    with pytest.raises(cpl.CouplingSchemaError):
        cpl.validate_interfaces(iface_doc(bad))


def test_validate_interfaces_rejects_duplicate_id():
    with pytest.raises(cpl.CouplingSchemaError):
        cpl.validate_interfaces(iface_doc(iface(), iface()))


def test_validate_assumptions_requires_source_sha256():
    bad = {"schema": cpl.ASSUMPTIONS_SCHEMA,
           "keys": [{"key": "k", "kind": "value",
                     "declarations": [{"node": "demo-alpha", "value": 1.0,
                                       "source": {"ref": "x", "sha256": "short"}}]}]}
    with pytest.raises(cpl.CouplingSchemaError):
        cpl.validate_assumptions(bad)


def test_validate_assumptions_rejects_implied_target_without_declaration():
    doc = assume_doc({"key": "law.k", "kind": "law",
                      "declarations": [law_decl("demo-alpha", {"a": 1.0, "b": 2.0}),
                                       law_decl("demo-beta", {"a": 1.0, "b": 2.0})],
                      "implied": [{"node": "demo-alpha", "expr": "a * 2",
                                   "target": "c", "tol": 0.01}]})
    cpl.validate_assumptions(doc)
    with pytest.raises(cpl.CouplingSchemaError):
        cpl.check_assumptions(doc)


# ─────────────────────────── interface contracts ───────────────────────────

def _check(doc, attested=None, documented=None):
    return cpl.check_interfaces(doc, attested=attested or {},
                                documented=documented or {})


def test_interface_validated_when_contract_and_both_sides_evidenced():
    res = _check(iface_doc(), {"demo-alpha": True, "demo-beta": True},
                 {"docs/demo.md": True})
    assert res["interfaces"][0]["status"] == "VALIDATED"
    assert res["summary"]["mismatch"] == 0


def test_interface_unit_mismatch_is_contract_mismatch():
    bad = iface(requirement={"unit": "g/s", "min": 0.0, "max": 0.5})
    res = _check(iface_doc(bad), {"demo-alpha": True, "demo-beta": True},
                 {"docs/demo.md": True})
    assert res["interfaces"][0]["status"] == "CONTRACT_MISMATCH"
    assert "IF-A" in res["summary"]["mismatch_ids"]


def test_interface_uncovered_range_without_guard_is_mismatch():
    wide = iface(requirement={"unit": "kg/s", "min": 0.0, "max": 2.0})
    res = _check(iface_doc(wide), {"demo-alpha": True, "demo-beta": True},
                 {"docs/demo.md": True})
    row = res["interfaces"][0]
    assert row["status"] == "CONTRACT_MISMATCH"
    cov = next(c for c in row["checks"] if c["check"] == "range_coverage")
    assert cov["detail"]["shortfall_high"] == pytest.approx(1.0)
    assert cov["detail"]["guarded"] is False


def test_interface_guarded_coverage_passes_with_recorded_shortfall():
    wide = iface(requirement={"unit": "kg/s", "min": 0.0, "max": 2.0,
                              "fallback": "clamp_with_status"})
    res = _check(iface_doc(wide), {"demo-alpha": True, "demo-beta": True},
                 {"docs/demo.md": True})
    row = res["interfaces"][0]
    assert row["status"] == "VALIDATED"
    cov = next(c for c in row["checks"] if c["check"] == "range_coverage")
    assert cov["detail"]["guarded"] is True
    assert cov["detail"]["shortfall_high"] == pytest.approx(1.0)


def test_interface_without_doc_ref_is_undocumented():
    res = _check(iface_doc(), {"demo-alpha": True, "demo-beta": True}, {})
    assert res["interfaces"][0]["status"] == "UNDOCUMENTED"


def test_interface_one_sided_evidence_stays_unattested_not_validated():
    res = _check(iface_doc(), {"demo-alpha": True, "demo-beta": False},
                 {"docs/demo.md": True})
    assert res["interfaces"][0]["status"] == "UNATTESTED"
    assert res["summary"]["validated"] == 0


def test_interface_unknown_consumer_is_mismatch():
    bad = iface(consumer="demo-ghost")
    res = _check(iface_doc(bad), {"demo-alpha": True}, {"docs/demo.md": True})
    assert res["interfaces"][0]["status"] == "CONTRACT_MISMATCH"


# ─────────────────── assumption consistency (含负例) ───────────────────

def test_edition_conflict_is_detected_as_contradiction():
    """负例控制：同一标准两节点声明不同版本，闸必须判冲突（不得静默一致）。"""
    doc = assume_doc({"key": "std.edition", "kind": "edition",
                      "standard": "STD-0",
                      "declarations": [decl("demo-alpha", "STD-0:2009"),
                                       decl("demo-beta", "STD-0:2021")]})
    res = cpl.check_assumptions(cpl.validate_assumptions(doc))
    row = res["keys"][0]
    assert row["status"] == "CONTRADICTED"
    assert res["summary"]["unmitigated"] == 1
    assert res["summary"]["clean"] is False


def test_edition_agreement_is_agreed():
    doc = assume_doc({"key": "std.edition", "kind": "edition",
                      "declarations": [decl("demo-alpha", "STD-0:2009"),
                                       decl("demo-beta", "STD-0:2009")]})
    res = cpl.check_assumptions(cpl.validate_assumptions(doc))
    assert res["keys"][0]["status"] == "AGREED"
    assert res["summary"]["clean"] is True


def test_value_tolerance_boundary():
    within = assume_doc({"key": "const.g", "kind": "value", "rel_tol": 1e-3,
                         "declarations": [decl("demo-alpha", 9.81),
                                          decl("demo-beta", 9.80665)]})
    outside = assume_doc({"key": "const.g", "kind": "value", "rel_tol": 1e-9,
                          "declarations": [decl("demo-alpha", 9.81),
                                           decl("demo-beta", 9.80665)]})
    assert cpl.check_assumptions(cpl.validate_assumptions(within))["keys"][0]["status"] == "AGREED"
    assert cpl.check_assumptions(cpl.validate_assumptions(outside))["keys"][0]["status"] == "CONTRADICTED"


def test_single_declaration_is_undetermined_not_agreed():
    doc = assume_doc({"key": "std.edition", "kind": "edition",
                      "declarations": [decl("demo-alpha", "STD-0:2009")]})
    res = cpl.check_assumptions(cpl.validate_assumptions(doc))
    assert res["keys"][0]["status"] == "UNDETERMINED"
    assert res["summary"]["undetermined"] == 1


def test_implied_law_continuity_mismatch_is_flagged():
    params = {"m1": 3.0, "m2": 5.0, "a1": 12.0, "a2": 15.9, "knee": 50.0}
    doc = assume_doc({"key": "law.dual", "kind": "law", "tol": 1e-9,
                      "declarations": [law_decl("demo-alpha", params),
                                       law_decl("demo-beta", params)],
                      "implied": [{"node": "demo-alpha",
                                   "expr": "a1 + (m2 - m1) * log10(knee)",
                                   "target": "a2", "tol": 0.01}]})
    res = cpl.check_assumptions(cpl.validate_assumptions(doc))
    row = res["keys"][0]
    assert row["status"] == "IMPLIED_MISMATCH"
    import math
    expected = 12.0 + (5.0 - 3.0) * math.log10(50.0)
    imp = row["detail"]["implied"][0]
    assert imp["expected"] == pytest.approx(expected, rel=1e-12)
    assert imp["abs_dev"] > 0.01


def test_mitigation_requires_pair_that_actually_downgrades():
    key = {"key": "law.dual", "kind": "law", "tol": 1e-9,
           "declarations": [law_decl("demo-alpha", {"a1": 12.0, "a2": 15.9,
                                                   "m1": 3.0, "m2": 5.0, "knee": 50.0}),
                            law_decl("demo-beta", {"a1": 12.0, "a2": 15.2,
                                                   "m1": 3.0, "m2": 5.0, "knee": 50.0})],
           "implied": [], "mitigation": {"kind": "pair_downgrade", "pair": "ADJ-X"}}
    doc = cpl.validate_assumptions(assume_doc(key))
    downgraded = cpl.check_assumptions(doc, pair_verdicts={"ADJ-X": "DISAGREE"})
    agreed = cpl.check_assumptions(doc, pair_verdicts={"ADJ-X": "AGREE"})
    missing = cpl.check_assumptions(doc, pair_verdicts={})
    assert downgraded["summary"]["unmitigated"] == 0
    assert agreed["summary"]["unmitigated"] == 1
    assert missing["summary"]["unmitigated"] == 1


def test_safe_eval_rejects_code_execution():
    for expr in ("__import__('os').system('echo hi')",
                 "(1).__class__.__bases__",
                 "open('/etc/passwd')",
                 "unknown_name + 1"):
        with pytest.raises(cpl.CouplingSchemaError):
            cpl._safe_eval(expr, {"a": 1.0})


# ─────────────────────────── archive snapshot ───────────────────────────

def test_archive_manifest_is_deterministic_and_tamper_evident(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    m1 = archive_mod.build_manifest("demo", [tmp_path], root=tmp_path)
    m2 = archive_mod.build_manifest("demo", [tmp_path], root=tmp_path)
    assert m1["content_sha256"] == m2["content_sha256"]
    assert m1["n_files"] == 2
    assert archive_mod.verify_manifest(m1, root=tmp_path)["ok"] is True
    (tmp_path / "b.txt").write_text("beta-changed", encoding="utf-8")
    ver = archive_mod.verify_manifest(m1, root=tmp_path)
    assert ver["ok"] is False
    assert ver["changed"] == ["b.txt"]
    (tmp_path / "b.txt").unlink()
    ver = archive_mod.verify_manifest(m1, root=tmp_path)
    assert ver["missing"] == ["b.txt"]


def test_archive_refuses_empty_snapshot(tmp_path: Path):
    with pytest.raises(ValueError):
        archive_mod.build_manifest("demo", [tmp_path / "nope"], root=tmp_path)


# ─────────────────── report criteria wiring (I6/I7) ───────────────────

def _pairs(verdict: str) -> list[PairResult]:
    p = PairResult(id="ADJ-X", title="demo", kind="consistency")
    p.checks = [__import__("tools.integration_gate.model", fromlist=["CheckResult"])
                .CheckResult("trace_agreement", verdict, "blocking", {}, "")]
    return [p]


def test_criteria_keep_i1_i5_when_no_coupling_declared():
    crit = evaluate_integration_criteria([], _pairs("AGREE"), {"counts": {}, "entries": []},
                                        [], {"passed": 5})
    assert "I6_interface_contracts" not in crit
    assert "I7_assumption_consistency" not in crit


def test_criteria_i6_i7_reflect_coupling_and_archive():
    coupling = {
        "interfaces": cpl.check_interfaces(
            cpl.validate_interfaces(iface_doc()),
            attested={"demo-alpha": True, "demo-beta": True},
            documented={"docs/demo.md": True}),
        "assumptions": cpl.check_assumptions(cpl.validate_assumptions(assume_doc(
            {"key": "std.edition", "kind": "edition",
             "declarations": [decl("demo-alpha", "STD-0:2009"),
                              decl("demo-beta", "STD-0:2021")]}))),
    }
    archive = {"n_files": 3, "n_bytes": 10, "content_sha256": SHA,
               "verification": {"ok": True, "checked": 3, "missing": [], "changed": []}}
    crit = evaluate_integration_criteria([], _pairs("AGREE"), {"counts": {}, "entries": []},
                                        [], {"passed": 5}, coupling=coupling, archive=archive)
    assert crit["I6_interface_contracts"]["passed"] is True
    assert crit["I7_assumption_consistency"]["passed"] is False  # 未处置冲突必须判不通过
    assert crit["I7_assumption_consistency"]["value"]["unmitigated"] == 1
    assert crit["I8_archive_snapshot"]["passed"] is True


# ─────────────────── platform ledger delta (R2 protection) ───────────────────

def test_platform_delta_blocks_upgrade_over_contradicted_evidence():
    base = {"capabilities": [
        {"id": "cap.one", "claimed_level": "MOCK",
         "evidence": [{"stance": "contradicts", "asserted_level": "MOCK"}]},
        {"id": "cap.two", "claimed_level": "WITHHELD", "evidence": []},
        {"id": "cap.three", "claimed_level": "REAL", "evidence": []},
    ]}
    obs = {"cap.one": "REAL", "cap.two": "REAL", "cap.three": "STUB", "cap.four": "REAL"}
    out = ledger_mod.platform_ledger_delta(obs, base)
    verdicts = {r["capability_id"]: r["verdict"] for r in out["rows"]}
    assert verdicts["cap.one"] == "blocked_upgrade"
    assert verdicts["cap.two"] == "upgrade_candidate"
    assert verdicts["cap.three"] == "downgrade_candidate"
    assert verdicts["cap.four"] == "unregistered"


def test_compute_delta_counts_new_real_and_downgrades():
    entries = [
        {"capability_id": "cap.new.real", "node_ext_id": "demo-alpha",
         "level": "REAL", "status": "PROVISIONAL"},
        {"capability_id": "cap.old", "node_ext_id": "demo-alpha",
         "level": "MOCK", "status": "PROVISIONAL"},
    ]
    base = {"entries": [{"capability_id": "cap.old", "level": "REAL"}]}
    delta = ledger_mod.compute_delta(entries, base)
    assert delta["n_new_real"] == 1
    assert delta["new_entries"] == 1
    assert delta["downgraded"] == [{"capability_id": "cap.old", "before": "REAL",
                                    "after": "MOCK"}]


# ─────────────────── zero domain semantics guard ───────────────────

def test_new_modules_are_domain_free():
    import re
    vocab_files = sorted(REPO.glob("data/campaigns/*/domain_vocab.json"))
    if not vocab_files:
        pytest.skip("no injected domain vocabulary")
    banned: list[str] = []
    for vf in vocab_files:
        voc = json.loads(vf.read_text(encoding="utf-8"))
        banned.extend(voc.get("words") or voc.get("forbidden_in_mechanism_code") or [])
    targets = [REPO / "tools" / "integration_gate" / "coupling.py",
               REPO / "tools" / "integration_gate" / "archive.py",
               Path(__file__)]
    for p in targets:
        text = p.read_text(encoding="utf-8")
        hits = [w for w in banned
                if re.search(rf"\b{re.escape(w)}\b", text, flags=re.IGNORECASE)]
        assert not hits, f"{p.name} contains domain words: {hits}"