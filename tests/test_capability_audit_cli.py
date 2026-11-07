#!/usr/bin/env python3
"""``tools/audit/capability_audit_cli.py`` 的验收用例（IN21-A1）。

覆盖三块：
* 扫描/量化内核：相关性门、数值化判据、tier-A/B 分级、权重归一；
* 判级规则级联：R0/R1/R2/R3/R3B/R3C/R4/R4A/R4B/R5/R6 逐条；
* 端到端：在 tmp_path 造一个「真实现 + 数值断言测试 + 已验证工件」的最小仓，
  跑完整 audit，要求 (a) 量化为 REAL，(b) 生成的 O7 卡片 100% 通过
  ``capability_ledger_upgrade.check_evidence()``（0 违规），
  (c) 内容摘要可重放，(d) CLI 退出码语义正确。

所有用例离线、无网络、不 import 被测平台代码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE / "tools" / "audit"))

import capability_audit_cli as CLI  # noqa: E402
import capability_audit_scan as S  # noqa: E402


# ── 内核：数值化判据 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expect_pass,expect_err", [
    ("覆盖率 98%，误差 1.20%，全部通过。", True, 1.2),
    ("与基准对比：误差 22.5%，不可接受。", False, 22.5),
    ("误差 <3% 满足要求。", True, 3.0),          # 上界表述 → 取 3% 作上界值
    ("RT 误差 -0.0%，Cf 误差 0.4%。", True, 0.0),
    ("裁决为 MOCK：KQ 对 J 不变化。误差 0.0%", False, 0.0),  # 负判决词压制
])
def test_judge_pass_numeric(text, expect_pass, expect_err):
    v = S.judge_pass(text)
    assert v["pass"] is expect_pass
    assert v["min_error_pct"] == expect_err


def test_numeric_error_picks_minimum():
    low, n = S.numeric_error_pct("误差 3.5% ；Cf 误差 0.8%；error 12%")
    assert (low, n) == (0.8, 3)


# ── 内核：相关性门 ──────────────────────────────────────────────────────────
def test_relevance_gate_blocks_generic_keyword(tmp_path):
    """通用词（predict）不得让无关文档成为证据；判别词（surrogate）才行。"""
    prof = {"x.predict": {"domain": "AI", "keywords": ("predict",),
                          "reference_domain": None, "solver_libs": ()}}
    d = tmp_path / "docs"
    d.mkdir()
    (d / "unrelated.md").write_text("This text predicts weather. 误差 0.5%",
                                    encoding="utf-8")
    S.RARE_TERMS["x.predict"] = ("surrogate",)
    caps = S.scan_worktree(tmp_path, prof, subdirs=("docs",))
    assert caps["x.predict"]["doc"] == []  # 无判别词 → 不计证据
    (d / "surrogate_audit.md").write_text("surrogate model predicts 误差 0.5%",
                                       encoding="utf-8")
    caps = S.scan_worktree(tmp_path, prof, subdirs=("docs",))
    assert len(caps["x.predict"]["doc"]) == 1
    assert caps["x.predict"]["doc"][0]["subject_hit"] is True


def test_subject_gate_requires_title_or_heading(tmp_path):
    """正文深处顺带提一句不算：判别词必须出现在文件名或开头 400 字符。"""
    prof = {"c": {"domain": "阻力", "keywords": ("resistance",),
                  "reference_domain": None, "solver_libs": ()}}
    S.RARE_TERMS["c"] = ("holtrop",)
    d = tmp_path / "docs"
    d.mkdir()
    (d / "deep.md").write_text("x" * 600 + " holtrop resistance 误差 0.2%",
                               encoding="utf-8")
    caps = S.scan_worktree(tmp_path, prof, subdirs=("docs",))
    assert caps["c"]["doc"][0]["subject_hit"] is False
    tiers = S.verification_tiers(caps["c"], "阻力")
    assert tiers["tier_a"] == [] and tiers["tier_b"] == []


def test_self_outputs_excluded_prevents_feedback_loop(tmp_path):
    """工具自己的产物/报告不得充作证据，否则「报告含上轮分数」会自激。"""
    prof = {"c": {"domain": "阻力", "keywords": ("holtrop",),
                  "reference_domain": None, "solver_libs": ()}}
    S.RARE_TERMS["c"] = ("holtrop",)
    d = tmp_path / "docs" / "audit"
    d.mkdir(parents=True)
    (d / "IN21_A1_capability_audit.md").write_text("holtrop 误差 0.1%", encoding="utf-8")
    caps = S.scan_worktree(tmp_path, prof, subdirs=("docs",))
    assert caps["c"]["doc"] == [] and caps["c"]["verified"] == []


# ── 内核：tier 分级与权重 ──────────────────────────────────────────────────
def _ev(**kw):
    base = {"code": [], "test": [], "verified": [], "doc": [],
            "markers": {"mock": 0, "stub": 0}}
    base.update(kw)
    return base


def test_tier_physics_vs_nonphysics():
    doc = {"path": "docs/verification/a.md", "subject_hit": True, "pass": True,
           "fail_quote": False, "min_error_pct": 0.5, "doc_fields": ["benchmark"]}
    test = {"path": "tests/test_a.py", "numeric_asserts": 3}
    ev = _ev(doc=[doc], test=[test])
    assert len(S.verification_tiers(ev, "阻力")["tier_a"]) == 1      # 物理域：已验证工件
    assert len(S.verification_tiers(ev, "平台")["tier_a"]) == 2      # 非物理域：测试也算 A


def test_weights_renormalize_and_sum_to_one():
    comps = {
        "code": {"score": 0.9, "detail": {}},
        "test": {"score": 1.0, "detail": {}},
        "verify": {"score": 0.5, "detail": {}},
        "doc": {"score": 0.75, "detail": {}},
        "reference": {"score": None, "detail": {}},
        "solver": {"score": None, "detail": {}},
    }
    w = S.normalize_weights(comps)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert "reference" not in w and "solver" not in w
    total = S.total_score(comps, w)
    assert 0.0 <= total <= 1.0


# ── 判级规则级联（逐条）───────────────────────────────────────────────────
def _comps(code=None, test=None, verify=0.0, tier_a=0, tier_b=0, fail_docs=0,
           stub_files=0, files=1, strong_mock=0, doc=None, ref=None, solver=None,
           conflict=False):
    return {
        "code": {"score": code, "detail": {"files": files, "stub_files": stub_files,
                                           "strong_mock_files": strong_mock,
                                           "mock_files": 0, "mock_marker_hits": 0,
                                           "stub_marker_hits": 0}},
        "test": {"score": test, "detail": {"files": 1 if test else 0,
                                           "numeric_assert_files": 1 if test else 0}},
        "verify": {"score": verify,
                   "detail": {"tier_a": tier_a, "tier_b": tier_b,
                              "negative_verdict_docs": fail_docs,
                              "verification_conflict": conflict,
                              "tier_a_paths": [], "tier_b_paths": [],
                              "fail_docs": fail_docs, "physics_domain": True,
                              "best_error_pct": 0.5}},
        "doc": {"score": doc, "detail": {"files": 1 if doc else 0}},
        "reference": {"score": ref, "detail": {}},
        "solver": {"score": solver, "detail": {}},
    }


def _sig(contradict=None, support=None):
    return {"contradict": contradict, "support": support, "evidence_count": 0}


def test_rule_r0_absent():
    c = _comps()
    v = S.classify(c, 0.0, _sig())
    assert (v["level"], v["rule"]) == ("NOT_SUPPORTED", "R0_ABSENT")


def test_rule_r1_stub():
    c = _comps(code=0.5, files=4, stub_files=3, verify=0.0)
    v = S.classify(c, 0.4, _sig())
    assert (v["level"], v["rule"]) == ("STUB", "R1_STUB")


def test_rule_r2_contradiction_dominates_score():
    """分数再高，人工 curation 的最新判负也要压回 MOCK（报告 1128/1133 场景）。"""
    c = _comps(code=0.9, test=1.0, verify=1.0, tier_a=3, doc=1.0)
    sig = _sig(contradict={"level": "MOCK", "as_of": "2026-11-01T00:00:00",
                           "ref": 1128, "quote": "MOCK"})
    v = S.classify(c, 0.95, sig)
    assert (v["level"], v["rule"]) == ("MOCK", "R2_CONTRADICTED")


def test_rule_r2_newer_support_allows_real():
    """更新的受证支撑可解锁 REAL（稳性 m1564 场景）。"""
    c = _comps(code=0.9, test=1.0, verify=1.0, tier_a=3, doc=1.0, fail_docs=5)
    sig = _sig(contradict={"level": "MOCK", "as_of": "2026-11-01T00:00:00", "ref": 1136},
               support={"level": "REAL", "as_of": "2026-11-01T08:00:00", "ref": "doc:x"})
    v = S.classify(c, 0.9, sig)
    assert (v["level"], v["rule"]) == ("REAL", "R4_REAL")


def test_rule_r3_strong_mock_marker():
    c = _comps(code=0.9, test=1.0, verify=0.0, strong_mock=1)
    v = S.classify(c, 0.6, _sig())
    assert (v["level"], v["rule"]) == ("MOCK", "R3_MOCK_MARKER")


def test_rule_r3b_conflict_without_hard_artifact():
    c = _comps(code=0.9, test=1.0, verify=0.5, tier_a=0, tier_b=1, fail_docs=1,
               conflict=True)
    v = S.classify(c, 0.7, _sig())
    assert (v["level"], v["rule"]) == ("WITHHELD", "R3B_CONFLICT")


def test_rule_r3c_negative_only_is_withheld_not_mock():
    """只有别人判负、没有正面判据 → 证据不足（不能顺手判 MOCK）。"""
    c = _comps(code=0.8, test=1.0, verify=0.0, fail_docs=2)
    v = S.classify(c, 0.55, _sig())
    assert (v["level"], v["rule"]) == ("WITHHELD", "R3C_NEGATIVE_ONLY")


def test_rule_r4a_negative_verdict_blocks_real():
    c = _comps(code=0.9, test=1.0, verify=1.0, tier_a=3, doc=1.0, fail_docs=2)
    v = S.classify(c, 0.95, _sig())
    assert (v["level"], v["rule"]) == ("WITHHELD", "R4A_NEGATIVE_VERDICT")


def test_rule_r4b_no_hard_artifact_cannot_be_real():
    c = _comps(code=0.9, test=1.0, verify=1.0, tier_a=0, tier_b=3, doc=1.0)
    v = S.classify(c, 0.9, _sig())
    assert (v["level"], v["rule"]) == ("WITHHELD", "R4B_NO_HARD_ARTIFACT")


def test_rule_r5_low_score():
    c = _comps(code=0.5, doc=0.25, verify=0.0)
    v = S.classify(c, 0.2, _sig())
    assert (v["level"], v["rule"]) == ("NOT_SUPPORTED", "R5_LOW_SCORE")


def test_rule_r6_withheld_fallback():
    c = _comps(code=0.9, test=0.33, verify=0.33, tier_a=1, doc=0.5)
    v = S.classify(c, 0.6, _sig())
    assert (v["level"], v["rule"]) == ("WITHHELD", "R6_WITHHELD")


# ── 端到端：最小仓 → REAL + O7 卡片零违规 ─────────────────────────────────
def _make_repo(root: Path):
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs" / "verification").mkdir(parents=True)
    (root / "benchmarks" / "verified").mkdir(parents=True)
    (root / "src" / "resistance.py").write_text(
        'def ittc57_cf(reynolds):\n    """ITTC-1957 摩擦系数。"""\n'
        '    return 0.075 / ((__import__("math").log10(reynolds) - 2) ** 2)\n',
        encoding="utf-8")
    (root / "tests" / "test_resistance.py").write_text(
        "from src.resistance import ittc57_cf\n\n"
        "def test_cf_holtrop():\n"
        "    assert abs(ittc57_cf(1e7) - 0.0034) < 1e-6\n"
        "    assert abs(ittc57_cf(1e8) - 0.0028) < 1e-6\n",
        encoding="utf-8")
    (root / "docs" / "verification" / "holtrop_resistance_audit.md").write_text(
        "# Holtrop 阻力验证\n\n"
        "## 物理表述\nRt = 0.5 rho V^2 S Ct，ITTC-1957 摩擦线。\n\n"
        "## 参数表\n| --- | --- |\n| Fr=0.2 | Cb=0.6 |\n\n"
        "## 基准比较\n与 SUBOFF 基准对比：RT 误差 0.8%，Cf 误差 0.4%。\n\n"
        "## 局限\n仅适用 Fr<0.45。\n",
        encoding="utf-8")
    (root / "benchmarks" / "verified" / "suboff_resistance.json").write_text(
        json.dumps({"case": "SUBOFF-01", "resistance_rt_error": "0.35%",
                    "holtrop": True}),
        encoding="utf-8")
    (root / "benchmarks" / "verified" / "kvlcc2_resistance.json").write_text(
        json.dumps({"case": "KVLCC2", "resistance_rt_error": "0.62%",
                    "holtrop": True}),
        encoding="utf-8")
    (root / "docs" / "reports").mkdir(parents=True)
    (root / "docs" / "reports" / "holtrop_resistance_report.md").write_text(
        "# 阻力报告\n\n物理方程 Rt 见 ITTC-1957。\n\n参数表\n| --- | --- |\n"
        "| Fr=0.2 | Cb=0.6 |\n\n基准比较：与 SUBOFF 参考值对比误差 1.1%。\n\n"
        "局限：Fr<0.45。\n", encoding="utf-8")
    for i in range(2, 4):
        (root / "tests" / f"test_resistance_{i}.py").write_text(
            "def test_holtrop_cf():\n"
            "    assert abs(0.0034 - 0.0034) < 1e-9\n", encoding="utf-8")
    claims = {"capabilities": {"resistance.auto": "REAL"},
              "capability_count": 1, "sha256": "x", "path": "claims.json", "source": {}}
    ledger = {"capabilities": {}, "available": True, "sha256": None,
              "path": "ledger.json", "reconciled": {}}
    ref = {"cases": [{"case_id": "SUBOFF-01", "rt_status": "OK", "resistance": 92.52}],
           "version": "3.0", "available": True, "path": "ref.json"}
    prof = {"resistance.auto": {"domain": "阻力", "keywords": ("resistance", "Cf", "Rt="),
                                "reference_domain": "resistance", "solver_libs": ()}}
    S.RARE_TERMS["resistance.auto"] = ("holtrop", "ITTC")
    return claims, ledger, ref, prof


def test_end_to_end_real_and_o7_alignment(tmp_path):
    claims, ledger, ref, prof = _make_repo(tmp_path)
    res = CLI.audit(claims, ledger, ref, None, profiles=prof,
                    subdirs=("src", "tests", "docs", "benchmarks"), root=tmp_path)
    payload, cards, o7 = res["payload"], res["cards"], res["o7"]
    rec = payload["capabilities"][0]
    assert rec["graded_level"] == "REAL", rec["classification"]
    assert rec["divergence"] == "OK"
    assert rec["components"]["verify"]["detail"]["tier_a"] >= 1
    # O7 升账 CLI 机器对拍：卡片必须 0 违规，且至少一张是真 PASS
    assert o7["available"] is True and o7["violation_count"] == 0, o7["violations"]
    assert cards[0]["validation_evidence"]["verification_status"] == "PASS"
    # 自检全绿 + 摘要可重放
    checks = CLI.self_checks(payload, claims, o7, cards, root=tmp_path)
    assert checks["all_ok"] is True, [c for c in checks["checks"] if not c["ok"]]
    assert CLI.content_digest(payload["capabilities"]) == payload["content_digest"]


def test_end_to_end_absent_capability_is_not_supported(tmp_path):
    (tmp_path / "docs").mkdir()
    claims = {"capabilities": {"ghost.cap": "REAL"}, "capability_count": 1,
              "sha256": "x", "path": "claims.json", "source": {}}
    ledger = {"capabilities": {}, "available": True, "sha256": None,
              "path": "ledger.json", "reconciled": {}}
    ref = {"cases": [], "version": None, "available": False, "path": "ref.json"}
    prof = {"ghost.cap": {"domain": "开发", "keywords": ("ghost_cap",),
                          "reference_domain": None, "solver_libs": ()}}
    S.RARE_TERMS["ghost.cap"] = ("ghost_cap",)
    res = CLI.audit(claims, ledger, ref, None, profiles=prof, subdirs=("docs",),
                    root=tmp_path)
    rec = res["payload"]["capabilities"][0]
    assert rec["graded_level"] == "NOT_SUPPORTED"
    assert rec["divergence"] == "OVERCLAIM"
    assert rec["evidence_scope"]["no_local_code"] is True
    # 无证据 → 卡片用 anchor_type=none + gap_reason，仍须 0 违规
    assert res["o7"]["violation_count"] == 0


# ── CLI 退出码语义 ─────────────────────────────────────────────────────────
def test_cli_exit_codes(tmp_path, capsys):
    claims, ledger, ref, prof = _make_repo(tmp_path)
    (tmp_path / "claims.json").write_text(json.dumps({"capabilities": claims["capabilities"]}),
                                          encoding="utf-8")
    code = CLI.main(["--claims", str(tmp_path / "claims.json"),
                     "--ledger", str(tmp_path / "noledger.json"),
                     "--reference", str(tmp_path / "noref.json"),
                     "--no-solvers", "--dry-run", "--check"])
    # 真实仓的 21 项画像不会被 tmp 仓命中，但自检结构必须成立（0 或 1 都合法，
    # 不允许 2 = 输入缺失以外的崩溃）
    assert code in (0, 1)
    assert "capabilities=" in capsys.readouterr().out


def test_cli_missing_claims_returns_2(tmp_path):
    code = CLI.main(["--claims", str(tmp_path / "missing.json"), "--dry-run"])
    assert code == 2

# ── 负控：对拍器必须能抓出被篡改的卡片（证明 0 违规不是真空判据）─────────────
def test_o7_negative_control_detects_tampering(tmp_path):
    claims, ledger, ref, prof = _make_repo(tmp_path)
    res = CLI.audit(claims, ledger, ref, None, profiles=prof,
                    subdirs=("src", "tests", "docs", "benchmarks"), root=tmp_path)
    good = res["cards"][0]
    assert res["o7"]["violation_count"] == 0            # 正控：原始卡片 0 违规

    bad = json.loads(json.dumps(good))
    bad["validation_evidence"]["report_body_sha256"] = "0" * 64   # 篡改摘要
    v1 = CLI.verify_o7_cards([bad], tmp_path)
    assert v1["violation_count"] > 0 and any("E3" in s for s in v1["violations"])

    bad2 = json.loads(json.dumps(good))
    bad2["validation_evidence"]["quote_match"] = "这句话不在文件里-xyz"  # 篡改引文
    v2 = CLI.verify_o7_cards([bad2], tmp_path)
    assert v2["violation_count"] > 0 and any("E7" in s for s in v2["violations"])

    bad3 = json.loads(json.dumps(good))
    bad3["validation_evidence"]["verification_status"] = "PASS"
    bad3["validation_evidence"]["relative_error_pct"] = 42.0        # 过度声明
    v3 = CLI.verify_o7_cards([bad3], tmp_path)
    assert v3["violation_count"] > 0 and any("E6" in s for s in v3["violations"])
