"""CLI 端到端：audit 落盘 + 验收判据 + verify 复现校验。"""
import json
from pathlib import Path

import pytest

from tools.capability_audit_v2 import cli

ROOT = Path(__file__).resolve().parents[2]
SPEC = "tests/capability_audit_v2/fixtures/spec_exec.json"
LABELS = "tests/capability_audit_v2/fixtures/labels.json"
PATHS = "tests/capability_audit_v2/fixtures/upgrade_paths.json"


@pytest.fixture(scope="module")
def audit_out(tmp_path_factory):
    out = tmp_path_factory.mktemp("cav2") / "out"
    code = cli.main(["audit", "--root", str(ROOT), "--spec", SPEC, "--labels", LABELS,
                     "--paths", PATHS, "--out", str(out), "--repro-runs", "2"])
    return code, out


def test_audit_writes_all_artifacts_and_passes(audit_out):
    code, out = audit_out
    assert code == 0
    for name in ("ledger.json", "roadmap.json", "evidence_index.json", "acceptance.json",
                 "reproducibility.json", "meta.json", "sha256_manifest.json", "report.md",
                 "domain_scan.json", "figures.json"):
        assert (out / name).is_file(), name
    acceptance = json.loads((out / "acceptance.json").read_text(encoding="utf-8"))
    assert acceptance["all_pass"] is True
    assert acceptance["checks"]["C1_coverage_100pct"]["value"] == 1.0
    assert acceptance["checks"]["C2_agreement_ge_95pct"]["value"] == 1.0
    assert acceptance["checks"]["C3_roadmap_complete"]["items"] == 2
    assert acceptance["checks"]["C5_reproducible_sha256"]["equal"] is True


def test_audit_report_mentions_every_capability(audit_out):
    _code, out = audit_out
    report = (out / "report.md").read_text(encoding="utf-8")
    for cap in ("cap.alpha", "cap.beta", "cap.gamma", "cap.delta", "cap.epsilon"):
        assert cap in report


def test_audit_figures_written(audit_out):
    _code, out = audit_out
    figures = json.loads((out / "figures.json").read_text(encoding="utf-8"))
    assert set(figures) >= {"level_tally", "agreement", "evidence_planes"}
    for path in (figures["level_tally"], figures["agreement"], figures["evidence_planes"]):
        assert Path(path).is_file() and Path(path).stat().st_size > 1000


def test_manifest_pins_ledger_digest(audit_out):
    _code, out = audit_out
    manifest = json.loads((out / "sha256_manifest.json").read_text(encoding="utf-8"))
    repro = json.loads((out / "reproducibility.json").read_text(encoding="utf-8"))
    assert manifest["ledger_canonical_sha256"] == repro["digest_run1"]
    assert len(manifest["ledger.json"]) == 64


def test_verify_exit_codes(monkeypatch):
    """verify 的判定逻辑：摘要一致 → 0；不一致 → 1（重计算代价由 audit 的 repro 覆盖）。"""
    monkeypatch.setattr(cli, "_run_once", lambda *a, **k: {"digest": "same"})
    assert cli.main(["verify", "--root", str(ROOT), "--spec", SPEC,
                     "--paths", PATHS, "--runs", "2"]) == 0
    calls = {"n": 0}

    def _flip(*_a, **_k):
        calls["n"] += 1
        return {"digest": "a" if calls["n"] == 1 else "b"}

    monkeypatch.setattr(cli, "_run_once", _flip)
    assert cli.main(["verify", "--root", str(ROOT), "--spec", SPEC,
                     "--paths", PATHS, "--runs", "2"]) == 1


def test_bad_spec_exit_code_is_four(tmp_path):
    bad = tmp_path / "bad_spec.json"
    bad.write_text(json.dumps({"schema_version": "2.0", "base_spec": "nope.json",
                               "capabilities": {}}), encoding="utf-8")
    code = cli.main(["audit", "--root", str(ROOT), "--spec", str(bad), "--paths", PATHS,
                     "--out", str(tmp_path / "o")])
    assert code == 4