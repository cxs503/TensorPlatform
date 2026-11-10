"""Regression suite: evidence-chain integrity audit (figures / numbers / criteria)."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from tools.sustainment import evidence_audit as ea

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload-0123456789" * 4
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"a-different-figure" * 20


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def workspace(tmp_path: Path) -> dict:
    fig = tmp_path / "figures" / "f1.png"
    fig.parent.mkdir(parents=True, exist_ok=True)
    fig.write_bytes(PNG_BYTES)
    manifest = {
        "figures": [
            {"name": "f1.png", "path": "figures/f1.png", "sha256": _sha(PNG_BYTES),
             "caption": "lineage DAG"},
        ]
    }
    (tmp_path / "figures.json").write_text(json.dumps(manifest))

    claims = {
        "claims": [
            {"id": "c_nodes", "value": 1247, "source": "sql:select count(*) from lineage_nodes"},
            {"id": "c_rto", "value": 3.42, "source": "artifact:backup_ledger.json#records[0].rto_s"},
            {"id": "c_cov", "value": 0.9971, "source": "sql:select coverage from audit_run"},
        ]
    }
    (tmp_path / "claims.json").write_text(json.dumps(claims))

    criteria = {
        "criteria": [
            {"id": "K1", "statement": "DAG acyclic", "op": "==", "actual": 0, "threshold": 0,
             "verdict": "PASS", "evidence": ["figures/f1.png"]},
            {"id": "K2", "statement": "sha256 coverage", "op": ">=", "actual": 0.9971,
             "threshold": 0.99, "verdict": "PASS", "evidence": ["claims.json"]},
            {"id": "K3", "statement": "RTO under budget", "op": "<=", "actual": 3.42,
             "threshold": 900, "verdict": "PASS", "evidence": ["figures.json"]},
        ]
    }
    (tmp_path / "criteria.json").write_text(json.dumps(criteria))

    body = """# report

① 案例介绍 core metadata drill, 5 tables, 2026-11-10 run.
② 怎么做的 lineage 1247 nodes [src: sql:select count(*) from lineage_nodes] and
   RTO 3.42 s [src: artifact:backup_ledger.json#records[0].rto_s].
③ 计算结果 coverage 0.9971 of nodes hashed [src: sql:select coverage from audit_run].
④ 结果图 ![lineage DAG](figures/f1.png)
⑤ 注意点 isolated namespace only; no live writes.
⑥ 误差判定 K1 K2 K3 all within budget.
"""
    md = tmp_path / "report.md"
    md.write_text(body)
    return {"tmp": tmp_path, "md": md, "figures": tmp_path / "figures.json",
            "claims": tmp_path / "claims.json", "criteria": tmp_path / "criteria.json"}


def test_clean_report_passes(workspace: dict):
    res = ea.audit(workspace["md"], workspace["figures"], workspace["claims"],
                   workspace["criteria"])
    assert res.ok, json.dumps(res.to_json(), indent=2)[:2000]
    assert res.checks["C0_sections"]["found"] == 6
    assert res.checks["C1_figures"]["problems"] == 0
    assert res.checks["C2_numbers"]["untraced"] == 0
    assert res.checks["C3_criteria"]["passed"] == 3


# -- C0 -------------------------------------------------------------------


def test_sections_detected_with_circled_digits():
    ok = ea.check_sections("① a ② b ③ c ④ d ⑤ e ⑥ f")
    assert ok["ok"] and ok["found"] == 6
    bad = ea.check_sections("① a ② b")
    assert bad["ok"] is False and bad["found"] == 2


def test_sections_required_flag(workspace: dict):
    md = workspace["tmp"] / "bare.md"
    md.write_text("no structure here at all\n")
    res = ea.audit(md, workspace["figures"], workspace["claims"], workspace["criteria"],
                   require_sections=True)
    assert res.checks["C0_sections"]["ok"] is False
    res2 = ea.audit(md, workspace["figures"], workspace["claims"], workspace["criteria"],
                    require_sections=False)
    assert res2.checks["C0_sections"]["ok"] is True


# -- C1 figures -----------------------------------------------------------


def test_figure_sha_mismatch_is_caught(workspace: dict):
    fig = workspace["tmp"] / "figures" / "f1.png"
    fig.write_bytes(OTHER_PNG)  # swapped on disk after the manifest was written
    res = ea.audit(workspace["md"], workspace["figures"], workspace["claims"],
                   workspace["criteria"])
    assert res.ok is False
    kinds = {p["kind"] for p in res.figures["problems"]}
    assert "figure_sha256_mismatch" in kinds
    assert res.figures["manifest_disk_mismatch"]


def test_embedded_figure_must_be_in_manifest(workspace: dict):
    b64 = base64.b64encode(PNG_BYTES).decode()
    good = workspace["tmp"] / "embedded_good.md"
    good.write_text(f"① ② ③ ④ ![fig](data:image/png;base64,{b64}) ⑤ ⑥\n")
    res = ea.audit(good, workspace["figures"], workspace["claims"], workspace["criteria"])
    assert res.figures["embedded_figures"] == 1
    assert res.figures["problems"] == []

    b64_bad = base64.b64encode(OTHER_PNG).decode()
    bad = workspace["tmp"] / "embedded_bad.md"
    bad.write_text(f"![fig](data:image/png;base64,{b64_bad})\n")
    res2 = ea.audit(bad, workspace["figures"], workspace["claims"], workspace["criteria"])
    kinds = {p["kind"] for p in res2.figures["problems"]}
    assert "embedded_figure_not_in_manifest" in kinds


# -- C2 numbers -----------------------------------------------------------


def test_untraced_number_lowers_coverage():
    # leading marker traces the next number; the invented one stays untraced
    body = ("[src: sql:select x] 7.5 measured. 12.345 is invented.\n")
    out = ea.check_numbers(body, None, min_coverage=0.99)
    assert out["untraced"] == 1
    assert out["coverage"] < 0.99 and out["ok"] is False
    assert out["untraced_samples"][0]["token"] == "12.345"


def test_trailing_marker_with_scheme_is_accepted():
    body = "RTO was 3.42 s [src: artifact:backup_ledger.json#records[0].rto_s] on the box\n"
    out = ea.check_numbers(body, None)
    assert out["untraced"] == 0 and out["traced"] == 1
    assert out["traced_samples"][0]["marker"].startswith("artifact:")


def test_trailing_marker_without_scheme_does_not_launder():
    body = "RTO was 3.42 s [see appendix]\n"
    out = ea.check_numbers(body, None)
    assert out["untraced"] == 1 and out["traced"] == 0


def test_claimed_numbers_count_as_traced():
    claims = {"claims": [{"id": "c1", "value": 0.9971, "source": "sql:select coverage"}]}
    out = ea.check_numbers("coverage was 0.9971 overall and 0.9971 again\n", claims)
    assert out["untraced"] == 0 and out["ok"] is True
    assert out["traced"] == 2


def test_allowlisted_and_date_tokens_are_exempt():
    body = "run on 2026-11-10 with 6 elements, version 1.2.3\n"
    out = ea.check_numbers(body, {"claims": [], "allowlist": ["6"]})
    assert out["untraced"] == 0


# -- C3 criteria ----------------------------------------------------------


def test_inconsistent_verdict_is_caught(workspace: dict):
    data = json.loads(workspace["criteria"].read_text())
    data["criteria"][0]["actual"] = 5   # op '==' against 0 -> recomputed FAIL
    data["criteria"][0]["verdict"] = "PASS"
    p = workspace["tmp"] / "criteria_bad.json"
    p.write_text(json.dumps(data))
    res = ea.audit(workspace["md"], workspace["figures"], workspace["claims"], p)
    assert res.ok is False
    assert res.criteria["verdict_inconsistent"][0]["id"] == "K1"
    assert res.criteria["verdict_inconsistent"][0]["recomputed"] == "FAIL"


def test_missing_evidence_path_is_unverified(workspace: dict):
    data = json.loads(workspace["criteria"].read_text())
    data["criteria"][1]["evidence"] = ["does/not/exist.json"]
    p = workspace["tmp"] / "criteria_missing.json"
    p.write_text(json.dumps(data))
    res = ea.audit(workspace["md"], workspace["figures"], workspace["claims"], p)
    assert res.ok is False
    assert res.criteria["unverified"][0]["reason"] == "evidence_missing"


def test_honest_fail_must_be_disclosed_in_prose(workspace: dict):
    data = json.loads(workspace["criteria"].read_text())
    data["criteria"].append({"id": "K9", "statement": "disk pressure under 85%",
                             "op": "<=", "actual": 99.0, "threshold": 85,
                             "verdict": "FAIL", "evidence": ["figures.json"]})
    p = workspace["tmp"] / "criteria_fail.json"
    p.write_text(json.dumps(data))
    res = ea.audit(workspace["md"], workspace["figures"], workspace["claims"], p)
    # verdict recomputed honestly as FAIL, but the prose never mentions K9
    assert res.criteria["failed"] == 1
    assert res.criteria["fail_not_disclosed"][0]["id"] == "K9"
    md2 = workspace["tmp"] / "report_disclosed.md"
    md2.write_text(workspace["md"].read_text() + "\nK9 FAILED honestly: 99% > 85%.\n")
    res2 = ea.audit(md2, workspace["figures"], workspace["claims"], p)
    assert res2.criteria["fail_not_disclosed"] == []
    assert res2.criteria["ok"] is True   # honest FAIL is allowed, hidden FAIL is not


def test_missing_input_file_raises(workspace: dict):
    with pytest.raises(FileNotFoundError):
        ea.audit(workspace["md"], workspace["tmp"] / "nope.json")