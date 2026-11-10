"""能力台账三源对账引擎单测（合成域，零真实领域词）。

本套件用**合成能力 / 合成代码**构造一个迷你工作树，逐分支验证判定规则、过度
声明检测、等级调整建议、sha256 审计链、schema 校验与零领域语义硬编码。

合成金标准（人工标注，逐条对应一条规则分支）：

=====================  ==============  =============  ====================  ==========================
能力                   声明等级        期望派生       期望对账              触发分支
=====================  ==============  =============  ====================  ==========================
alpha                   REAL           REAL           CONSISTENT            三源齐全 + 数值验证 1.2%
beta                    REAL           STUB           OVERCLAIM(HARD)       空壳函数体
gamma                   REAL           MOCK           OVERCLAIM(HARD)       未播种随机
delta                   REAL           NOT_SUPPORTED  OVERCLAIM(HARD)       无任何源文件
epsilon                 REAL           WITHHELD       OVERCLAIM(SOFT)       无测试
zeta                    REAL           WITHHELD       OVERCLAIM(SOFT)       占位断言 assert True
eta                     STUB           REAL           UNDERCLAIM            三源齐全 + 0.5%
theta                   NOT_SUPPORTED  NOT_SUPPORTED  CONSISTENT            无代码且声明即无
iota                    REAL           WITHHELD       OVERCLAIM(SOFT)       文档反述弱等级
kappa                   REAL           WITHHELD       OVERCLAIM(SOFT)       验证工件判失败
lambda                  REAL           WITHHELD       OVERCLAIM(SOFT)       文档无精度声明
nu                      REAL           WITHHELD       OVERCLAIM(SOFT)       junit 执行失败
=====================  ==============  =============  ====================  ==========================
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.capability_reconcile import core, engine  # noqa: E402
from tools.capability_reconcile.selfcheck import (  # noqa: E402
    DOMAINS,
    GOLD,
    GOLD_OVERCLAIMS,
    JUNIT,
    build_fixture_repo,
    write_ledger,
)
from tools.capability_reconcile.resolver import normalize_ledger, resolve_sources  # noqa: E402

@pytest.fixture()
def built(tmp_path):
    repo = build_fixture_repo(tmp_path)
    ledger = write_ledger(tmp_path)
    report = engine.build_report(
        ledger_path=ledger,
        root=repo,
        junit=JUNIT,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    return repo, ledger, report


# ── 1. 判定规则逐条对齐金标准 ──────────────────────────────────────────────
def test_derived_levels_match_gold(built):
    _, _, report = built
    got = {c["id"]: c["derived_level"] for c in report["capabilities"]}
    wrong = {cid: (got.get(cid), GOLD[cid][1]) for cid in GOLD if got.get(cid) != GOLD[cid][1]}
    assert not wrong, f"derived level mismatches: {wrong}"


def test_verdicts_and_overclaim_kind_match_gold(built):
    _, _, report = built
    got = {c["id"]: (c["verdict"], c["overclaim_kind"]) for c in report["capabilities"]}
    wrong = {cid: (got.get(cid), GOLD[cid][2:]) for cid in GOLD if got.get(cid) != GOLD[cid][2:]}
    assert not wrong, f"verdict mismatches: {wrong}"


def test_reason_codes_are_explicit(built):
    _, _, report = built
    by_id = {c["id"]: c for c in report["capabilities"]}
    assert "STUB_DOMINATED" in by_id["syn.beta_stub"]["reasons"]
    assert "UNSEEDED_RANDOM" in by_id["syn.gamma_mock"]["reasons"]
    assert "NO_SOURCE_BINDING" in by_id["syn.delta_absent"]["reasons"]
    assert "TESTS_MISSING" in by_id["syn.epsiloncase_notest"]["reasons"]
    assert "TESTS_SKELETON" in by_id["syn.zeta_trivial"]["reasons"]
    assert "VERIFICATION_CONTRADICTS" in by_id["syn.kappa_verifail"]["reasons"]
    assert "NO_NUMERIC_VERIFICATION" in by_id["syn.lambda_noacc"]["reasons"]
    assert "TESTS_FAILING" in by_id["syn.nucase_junitfail"]["reasons"]
    assert "NUMERIC_VERIFICATION_PASS" in by_id["syn.alpha_ok"]["reasons"]
    for cap in report["capabilities"]:
        assert cap["reasons"], cap["id"]


# ── 2. 机器可算判据：覆盖率 / 准确率 / 一致率 ───────────────────────────────
def test_scan_coverage_ge_95pct(built):
    _, _, report = built
    assert report["summary"]["capabilities"] == len(GOLD)
    assert report["summary"]["scan_coverage"] >= 0.95
    assert report["summary"]["source_binding_coverage"] > 0


def test_overclaim_detection_accuracy_ge_90pct(built):
    _, _, report = built
    pred = {c["id"] for c in report["capabilities"] if c["verdict"] == "OVERCLAIM"}
    tp = len(pred & GOLD_OVERCLAIMS)
    fp = len(pred - GOLD_OVERCLAIMS)
    fn = len(GOLD_OVERCLAIMS - pred)
    accuracy = tp / (tp + fp + fn) if (tp + fp + fn) else 1.0
    assert accuracy >= 0.90, f"overclaim detection accuracy={accuracy:.3f} (tp={tp}, fp={fp}, fn={fn})"


def test_grade_agreement_ge_85pct(built):
    _, _, report = built
    got = {c["id"]: c["derived_level"] for c in report["capabilities"]}
    agree = sum(1 for cid in GOLD if got.get(cid) == GOLD[cid][1])
    assert agree / len(GOLD) >= 0.85, f"grade agreement={agree}/{len(GOLD)}"


# ── 3. 等级调整建议 ────────────────────────────────────────────────────────
def test_suggestions_actionable(built):
    _, _, report = built
    by_id = {c["id"]: c for c in report["capabilities"]}
    assert by_id["syn.beta_stub"]["suggestion"]["direction"] == "DOWNGRADE"
    assert "REPLACE_STUB" in by_id["syn.beta_stub"]["suggestion"]["actions"]
    assert "REMOVE_UNSEEDED_RANDOM" in by_id["syn.gamma_mock"]["suggestion"]["actions"]
    assert "ADD_NUMERIC_TESTS" in by_id["syn.epsiloncase_notest"]["suggestion"]["actions"]
    assert "ADD_TEST_ASSERTIONS" in by_id["syn.zeta_trivial"]["suggestion"]["actions"]
    assert "FIX_FAILING_TESTS" in by_id["syn.nucase_junitfail"]["suggestion"]["actions"]
    assert by_id["syn.etacase_under"]["suggestion"]["direction"] == "UPGRADE"
    assert by_id["syn.alpha_ok"]["suggestion"]["direction"] == "HOLD"
    for cap in report["capabilities"]:
        sug = cap["suggestion"]
        assert sug["actions"] and all(a in sug["action_hints"] for a in sug["actions"])


# ── 4. sha256 审计链与可复现性 ─────────────────────────────────────────────
def test_audit_chain_integrity(built):
    _, _, report = built
    audit = report["audit_log"]
    assert audit["algorithm"] == "sha256"
    assert audit["chain_len"] == len(report["capabilities"])
    prev = "0" * 64
    for rec, cap in zip(audit["records"], report["capabilities"]):
        assert rec["prev_sha256"] == prev
        assert rec["capability_id"] == cap["id"]
        assert rec["payload_sha256"] == core.sha256_hex(cap)
        expected = core.sha256_hex({"prev": prev, "payload": rec["payload_sha256"], "seq": rec["seq"]})
        assert rec["record_sha256"] == expected
        prev = rec["record_sha256"]
    assert audit["head_sha256"] == prev


def test_anchor_reproducible_across_runs(built, tmp_path):
    repo, ledger, report = built
    second = engine.build_report(ledger_path=ledger, root=repo, junit=JUNIT, generated_at="2030-12-31T23:59:59+00:00")
    assert report["generated_at"] != second["generated_at"]
    assert report["audit_log"]["anchor"]["value"] == second["audit_log"]["anchor"]["value"]
    assert report["audit_log"]["head_sha256"] == second["audit_log"]["head_sha256"]
    assert report["reproducibility"]["match"] is True


def test_anchor_detects_tampering(built):
    _, _, report = built
    tampered = json.loads(json.dumps(report))
    tampered["capabilities"][0]["derived_level"] = "MOCK"
    assert core.anchor_of(tampered) != report["audit_log"]["anchor"]["value"]


def test_report_schema_valid(built):
    _, _, report = built
    assert report["validation"]["valid"] is True, report["validation"]["errors"]
    assert report["validation"]["engine"] == "jsonschema"


# ── 5. 分级口径（evidence vs presence） ────────────────────────────────────
def test_evidence_policy_requires_numeric_verification(tmp_path):
    repo = build_fixture_repo(tmp_path)
    ledger = write_ledger(tmp_path)
    evidence = engine.build_report(ledger_path=ledger, root=repo, policy="evidence")
    presence = engine.build_report(ledger_path=ledger, root=repo, policy="presence")
    ev = {c["id"]: c["derived_level"] for c in evidence["capabilities"]}
    pr = {c["id"]: c["derived_level"] for c in presence["capabilities"]}
    assert ev["syn.lambda_noacc"] == "WITHHELD"
    assert pr["syn.lambda_noacc"] == "REAL"
    assert ev["syn.alpha_ok"] == pr["syn.alpha_ok"] == "REAL"


# ── 6. 零领域语义硬编码（铁律） ────────────────────────────────────────────
def _package_sources(include_selfcheck: bool = False) -> str:
    pkg = ROOT / "tools" / "capability_reconcile"
    files = [p for p in sorted(pkg.glob("*.py")) if include_selfcheck or p.name != "selfcheck.py"]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_zero_domain_hardcoding_synthetic():
    # selfcheck.py 装的是**合成**夹具（syn.* / zone-*），不属于领域语义；
    # 对它的检查见下一个测试（真实台账 id / 域标签字面量不得出现）。
    src = _package_sources()
    for cid in GOLD:
        assert cid not in src, f"engine source embeds capability id {cid}"
    for domain in DOMAINS.values():
        assert domain not in src, f"engine source embeds domain label {domain}"


def test_zero_domain_hardcoding_real_ledger():
    ledger = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
    if not ledger.is_file():
        pytest.skip("real ledger not present in this worktree")
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    src = _package_sources(include_selfcheck=True)
    for row in payload.get("capabilities") or []:
        cid = str(row.get("id", ""))
        if cid:
            assert cid not in src, f"engine source embeds ledger capability id {cid}"
        domain = row.get("domain")
        if isinstance(domain, str) and len(domain) >= 2:
            # 域标签只查「作为字符串字面量」出现（散文里的同形通用词如「结构」
            # 不构成域语义硬编码；域数据若被写死必然以字面量形式存在）。
            assert f'"{domain}"' not in src and f"'{domain}'" not in src, (
                f"engine source embeds ledger domain label as literal: {domain}"
            )


# ── 7. 解析与 CLI ─────────────────────────────────────────────────────────
def test_resolver_buckets_sources(tmp_path):
    repo = build_fixture_repo(tmp_path)
    res = resolve_sources(Path(repo), ["syn.alpha_ok", "syn.delta_absent"])
    alpha = res["syn.alpha_ok"]
    assert "src/alpha_impl.py" in alpha["impl_files"]
    assert any(p.startswith("tests/") for p in alpha["test"])
    assert any(p.startswith("docs/") for p in alpha["doc"])
    assert alpha["verification_docs"] == ["docs/verification/alpha_verify.md"]
    assert res["syn.delta_absent"]["impl_files"] == []


def test_ledger_normalization_shapes():
    flat = normalize_ledger({"capabilities": {"a.b": "REAL", "c.d": {"claimed_level": "stub", "domain": "z"}}})
    assert [r["id"] for r in flat] == ["a.b", "c.d"]
    assert flat[1]["declared_level"] == "STUB"
    assert flat[1]["domain"] == "z"
    listed = normalize_ledger({"capabilities": [{"id": "x", "claimed_level": "MOCK"}]})
    assert listed[0]["declared_level"] == "MOCK"


def test_cli_exit_codes(tmp_path):
    repo = build_fixture_repo(tmp_path)
    ledger = write_ledger(tmp_path)
    out = tmp_path / "report.json"
    base = [sys.executable, "-m", "tools.capability_reconcile.cli", "--ledger", str(ledger), "--root", str(repo), "--out", str(out)]
    ok = subprocess.run(base, cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert out.is_file()
    gated = subprocess.run(base + ["--fail-on-overclaim"], cwd=ROOT, capture_output=True, text=True)
    assert gated.returncode == 2
    missing = subprocess.run(
        [sys.executable, "-m", "tools.capability_reconcile.cli", "--ledger", str(tmp_path / "nope.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 3


def test_junit_gate_changes_verdict(tmp_path):
    repo = build_fixture_repo(tmp_path)
    ledger = write_ledger(tmp_path)
    without = engine.build_report(ledger_path=ledger, root=repo)
    with_fail = engine.build_report(ledger_path=ledger, root=repo, junit={"syn.nucase_junitfail": {"tests": 3, "failures": 1}})
    a = {c["id"]: c["derived_level"] for c in without["capabilities"]}
    b = {c["id"]: c["derived_level"] for c in with_fail["capabilities"]}
    assert a["syn.nucase_junitfail"] == "REAL"
    assert b["syn.nucase_junitfail"] == "WITHHELD"