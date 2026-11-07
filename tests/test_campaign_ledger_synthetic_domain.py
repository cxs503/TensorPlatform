"""台账机制测试（合成域夹具）——判例 #608 清洗后的替代基线（IN17-B2 · 边界门B2）。

为什么这么写
------------
判例 #608：平台层测试曾把**具体领域语义**硬编码进断言——断言某个域的条目集合、
某个域的 REAL/WITHHELD 分布、某个域的运行 ID 与工件路径。后果有两个：
  * 该领域工件一被清理，机制测试立刻变红（红的原因却是"领域工件不在"，与机制无关）；
  * 测试实际验证的是领域结论，而领域结论应由承接该领域的验证单负责（本战役为 B4）。

本文件改为**合成域名夹具**（``test.domain/alpha``、``test.domain/beta``、
``test.domain/gamma``、``test.domain/delta``），断言只验台账机制**对任意域条目成立**：
  1. 登记路径：条目/证据/缺口/curation 的结构完整性 + 与快照逐项对齐（C1/C5）；
  2. 升账路径：``supports_real`` 证据把自述等级升到 REAL，且升账单调向上；
  3. WITHHELD 路径：无证据/证据不足 → reconciled 落 WITHHELD，claim 高于证据记
     UNVERIFIED / OVERCLAIM / REVERIFY；
  4. 反例（变异体判负）：逐项注入缺陷必须**各自**触发对应 C 编号，证明机制非恒真。

被测机制：``tools/audit/capability_ledger.py`` 的 ``reconcile_one`` / ``rank`` /
``resolve_quote`` / ``check``（C1~C10）。
夹具：``tests/fixtures/capability_ledger/synthetic_domain/``（纯机制文本，无领域结论）。

域词零命中由 ``tools/audit/domain_leak_scan.py`` + ``config/domain_word_denylist.json``
（词表当数据，不硬编码）在 TestDomainNeutralityGuard 里机器裁决。
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER_TOOL = ROOT / "tools" / "audit" / "capability_ledger.py"
LEAK_TOOL = ROOT / "tools" / "audit" / "domain_leak_scan.py"
DENYLIST = ROOT / "config" / "domain_word_denylist.json"
FIXTURES = ROOT / "tests" / "fixtures" / "capability_ledger" / "synthetic_domain"
LEDGER_FIXTURE = FIXTURES / "ledger.json"
CORPUS_FIXTURE = FIXTURES / "reports_corpus.json"
SNAPSHOT_FIXTURE = FIXTURES / "capabilities_snapshot.json"

LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
STANCES = {"supports_real", "contradicts", "conditional"}
REGISTERED_IDS = {
    "alpha.registration",
    "alpha.promotion",
    "alpha.withheld",
    "alpha.doc_evidence",
    "beta.no_evidence",
    "beta.overclaim",
    "beta.reverify",
    "gamma.gap_backfill",
}
SYNTHETIC_DOMAIN_NS = "test.domain/"
# 期望的对账结果（机制口径，不是任何领域结论）
EXPECTED_DIVERGENCE = {
    "alpha.registration": ("REAL", "OK"),
    "alpha.promotion": ("REAL", "OK"),
    "alpha.withheld": ("WITHHELD", "OK"),
    "alpha.doc_evidence": ("REAL", "OK"),
    "beta.no_evidence": ("WITHHELD", "UNVERIFIED"),
    "beta.overclaim": ("MOCK", "OVERCLAIM"),
    "beta.reverify": ("WITHHELD", "REVERIFY"),
    "gamma.gap_backfill": ("NOT_SUPPORTED", "OK"),
}


# ── 模块装载 / 夹具 ──────────────────────────────────────────────────────
def _load_module(path: Path, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_module(LEDGER_TOOL, "capability_ledger_tool")


@pytest.fixture(scope="module")
def leak_scan():
    return _load_module(LEAK_TOOL, "domain_leak_scan_tool")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def ledger():
    return copy.deepcopy(_load_json(LEDGER_FIXTURE))


class Sandbox:
    """把合成域夹具落到 tmp（文档正文 + 语料 sha256 + 快照 + 漂移闸快照）。"""

    def __init__(self, module, root: Path, corpus: dict, snapshot: dict):
        self.module = module
        self.root = root
        self.corpus = corpus
        self.snapshot = snapshot
        self.corpus_path = root / "reports_corpus.json"
        self.snapshot_path = root / "capabilities_snapshot.json"
        self.resolved_path = root / "capability_ledger_resolved.json"

    def sync_docs(self) -> "Sandbox":
        for rec in (self.corpus.get("docs") or {}).values():
            path = self.root / rec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rec["markdown"], encoding="utf-8")
            rec["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self.flush()

    def flush(self) -> "Sandbox":
        self.corpus_path.write_text(json.dumps(self.corpus, ensure_ascii=False), encoding="utf-8")
        self.snapshot_path.write_text(json.dumps(self.snapshot, ensure_ascii=False), encoding="utf-8")
        return self

    def doc_path(self, rel: str) -> Path:
        return self.root / rel

    def run(self, ledger_doc: dict):
        return self.module.check(ledger_doc)


@pytest.fixture()
def sandbox(tool, tmp_path, monkeypatch):
    corpus = copy.deepcopy(_load_json(CORPUS_FIXTURE))
    snapshot = copy.deepcopy(_load_json(SNAPSHOT_FIXTURE))
    box = Sandbox(tool, tmp_path, corpus, snapshot)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "CORPUS", box.corpus_path)
    monkeypatch.setattr(tool, "CAP_SNAPSHOT", box.snapshot_path)
    monkeypatch.setattr(tool, "RESOLVED", box.resolved_path)
    return box.sync_docs()


# ── 机制查询助手 ────────────────────────────────────────────────────────
def _cap(ledger_doc: dict, cap_id: str) -> dict:
    return next(c for c in ledger_doc["capabilities"] if c["id"] == cap_id)


def _row(tool, ledger_doc: dict, cap_id: str) -> dict:
    return tool.reconcile_one(_cap(ledger_doc, cap_id), tool._rank_map(ledger_doc))


def _rows(tool, ledger_doc: dict) -> dict:
    levels = tool._rank_map(ledger_doc)
    return {c["id"]: tool.reconcile_one(c, levels) for c in ledger_doc["capabilities"]}


def _codes(violations: list[str]) -> set[str]:
    return {v.split()[0] for v in violations}


# ════════════════════════════════════════════════════════════════════════
# 1. 登记路径：条目/证据/缺口/curation 的结构完整性
# ════════════════════════════════════════════════════════════════════════
class TestRegistrationPath:
    def test_schema_version_and_rank_ladder(self, ledger, tool):
        assert ledger["schema_version"] == "2.0"
        levels = tool._rank_map(ledger)
        assert list(levels) == list(LEVELS), "等级阶梯必须逐级声明"
        ranks = [levels[name] for name in LEVELS]
        assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
        assert tool.rank(levels, "REAL") > tool.rank(levels, "WITHHELD") > tool.rank(levels, "MOCK")
        with pytest.raises(KeyError):
            tool.rank(levels, "UNREGISTERED_LEVEL")

    def test_reconciliation_rules_declared(self, ledger):
        rules = ledger["reconciliation_rules"]
        assert isinstance(rules, list) and len(rules) >= 2
        assert all(isinstance(r, str) and len(r.strip()) >= 8 for r in rules)

    def test_capabilities_registered_exactly(self, ledger):
        ids = [c["id"] for c in ledger["capabilities"]]
        assert len(ids) == len(set(ids)), "capability id 不得重复"
        assert set(ids) == REGISTERED_IDS

    def test_every_capability_declares_domain_and_action(self, ledger):
        for cap in ledger["capabilities"]:
            assert cap["domain"].startswith(SYNTHETIC_DOMAIN_NS), f"{cap['id']} 域名字面非法"
            assert cap["claimed_level"] in LEVELS, f"{cap['id']} 自述等级不在阶梯内"
            assert cap.get("action", "").strip(), f"{cap['id']} 缺 action 句"

    def test_every_evidence_row_is_structurally_complete(self, ledger):
        for cap in ledger["capabilities"]:
            for ev in cap["evidence"]:
                assert set(("report_id", "as_of", "stance", "asserted_level", "quote_match")) <= set(ev)
                assert ev["stance"] in STANCES, f"{cap['id']} stance 非法"
                assert ev["asserted_level"] in LEVELS, f"{cap['id']} asserted_level 非法"
                assert isinstance(ev["as_of"], str) and ev["as_of"][:4].isdigit()

    def test_unregistered_domains_declared(self, ledger):
        gaps = ledger["unregistered_domains"]
        assert len(gaps) >= 2
        for gap in gaps:
            assert gap["domain"].startswith(SYNTHETIC_DOMAIN_NS)
            assert gap["gap"].strip() and gap["action"].strip()
            assert len(gap["evidence"]) >= 1, f"{gap['domain']} 缺口必须有逐字证据"

    def test_curation_declares_consumption_and_freshness(self, ledger):
        curation = ledger["curation"]
        assert curation["revision"].strip()
        assert isinstance(curation["max_evidence_age_days"], int) and curation["max_evidence_age_days"] > 0
        assert curation["as_of"][:4].isdigit()
        assert len(curation["must_consume"]) >= 1
        for item in curation["must_consume"]:
            assert item["path"].strip() and item["why"].strip()

    def test_fixture_snapshot_matches_claims(self, ledger, sandbox):
        snap = sandbox.snapshot
        assert snap["capability_count"] == len(snap["capabilities"]) == len(ledger["capabilities"])
        for cap in ledger["capabilities"]:
            assert snap["capabilities"][cap["id"]] == cap["claimed_level"], (
                f"{cap['id']} 台账自述与快照不一致（C5 会判负）"
            )


# ════════════════════════════════════════════════════════════════════════
# 2. 升账路径
# ════════════════════════════════════════════════════════════════════════
class TestPromotionPath:
    def test_supports_real_promotes_above_claim(self, tool, ledger):
        row = _row(tool, ledger, "alpha.promotion")
        assert _cap(ledger, "alpha.promotion")["claimed_level"] == "MOCK"
        assert row["reconciled_level"] == "REAL"
        assert row["divergence"] == "OK", "升账（claim < 证据）不是分歧项"

    def test_promotion_is_strictly_monotonic(self, tool, ledger):
        levels = tool._rank_map(ledger)
        row = _row(tool, ledger, "alpha.promotion")
        assert tool.rank(levels, row["reconciled_level"]) > tool.rank(levels, row["claimed_level"])
        assert row["latest_evidence_report"] == 9102

    def test_backed_claim_stays_real(self, tool, ledger):
        row = _row(tool, ledger, "alpha.registration")
        assert (row["reconciled_level"], row["divergence"]) == ("REAL", "OK")
        assert row["evidence_count"] == 1

    def test_doc_evidence_can_promote(self, tool, ledger):
        row = _row(tool, ledger, "alpha.doc_evidence")
        assert row["reconciled_level"] == "REAL"
        assert str(row["latest_evidence_report"]).startswith("doc:")

    def test_latest_evidence_wins_by_timestamp(self, tool, ledger):
        cap = copy.deepcopy(_cap(ledger, "alpha.registration"))
        cap["evidence"].append(
            {
                "report_id": 9104,
                "as_of": "2026-10-20T00:00:00+00:00",
                "stance": "contradicts",
                "asserted_level": "MOCK",
                "quote_match": "6/6 判据不通过",
            }
        )
        row = tool.reconcile_one(cap, tool._rank_map(ledger))
        assert row["reconciled_level"] == "MOCK", "较新证据必须压过较旧证据"
        assert row["divergence"] == "OVERCLAIM"
        assert row["evidence_count"] == 2

    def test_same_timestamp_prefers_online_report(self, tool, ledger):
        cap = copy.deepcopy(_cap(ledger, "alpha.registration"))
        cap["evidence"] = [
            {
                "report_id": "doc:docs/synthetic_domain/alpha_review.md",
                "as_of": "2026-10-05T00:00:00+00:00",
                "stance": "contradicts",
                "asserted_level": "MOCK",
                "quote_match": "文档正文一致性",
            },
            {
                "report_id": 9102,
                "as_of": "2026-10-05T00:00:00+00:00",
                "stance": "supports_real",
                "asserted_level": "REAL",
                "quote_match": "误差 0\\.8%",
            },
        ]
        row = tool.reconcile_one(cap, tool._rank_map(ledger))
        assert row["reconciled_level"] == "REAL"
        assert row["latest_evidence_report"] == 9102


# ════════════════════════════════════════════════════════════════════════
# 3. WITHHELD 全路径
# ════════════════════════════════════════════════════════════════════════
class TestWithheldPath:
    def test_withheld_claim_backed_by_withheld_evidence(self, tool, ledger):
        row = _row(tool, ledger, "alpha.withheld")
        assert row["reconciled_level"] == "WITHHELD"
        assert row["divergence"] == "OK"

    def test_no_evidence_falls_back_to_withheld(self, tool, ledger):
        row = _row(tool, ledger, "beta.no_evidence")
        assert row["reconciled_level"] == "WITHHELD", "无证据 → 未验证默认 WITHHELD"
        assert row["basis"] == "无证据"
        assert row["evidence_count"] == 0

    def test_no_evidence_above_withheld_is_unverified(self, tool, ledger):
        row = _row(tool, ledger, "beta.no_evidence")
        assert row["claimed_level"] == "REAL", "夹具：无证据条目自述 REAL（高于 WITHHELD）"
        assert row["divergence"] == "UNVERIFIED"
        # 反向：无证据但自述不高于 WITHHELD → 不是分歧项（机制边界）
        cap = copy.deepcopy(_cap(ledger, "beta.no_evidence"))
        cap["claimed_level"] = "MOCK"
        lower = tool.reconcile_one(cap, tool._rank_map(ledger))
        assert lower["reconciled_level"] == "WITHHELD"
        assert lower["divergence"] == "OK", "claim 不高于 WITHHELD 时无证据不算分歧"

    def test_conditional_evidence_downgrades_and_reverifies(self, tool, ledger):
        row = _row(tool, ledger, "beta.reverify")
        assert row["reconciled_level"] == "WITHHELD"
        assert row["divergence"] == "REVERIFY", "证据非 contradicts 但弱于自述 → REVERIFY"

    def test_contradicting_evidence_flags_overclaim(self, tool, ledger):
        row = _row(tool, ledger, "beta.overclaim")
        assert row["reconciled_level"] == "MOCK"
        assert row["divergence"] == "OVERCLAIM"

    def test_unsupported_claim_is_consistent(self, tool, ledger):
        row = _row(tool, ledger, "gamma.gap_backfill")
        assert row["reconciled_level"] == "NOT_SUPPORTED"
        assert row["divergence"] == "OK"

    def test_every_reconciled_level_is_in_ladder(self, tool, ledger):
        rows = _rows(tool, ledger)
        assert set(rows) == REGISTERED_IDS
        for cap_id, row in rows.items():
            assert row["reconciled_level"] in LEVELS, f"{cap_id} 对账结果不在阶梯内"
            assert row["divergence"] in {"OK", "OVERCLAIM", "REVERIFY", "UNVERIFIED"}


# ════════════════════════════════════════════════════════════════════════
# 4. 机制对任意域条目成立（整条 check 链路 C1~C10）
# ════════════════════════════════════════════════════════════════════════
class TestMechanismIsDomainAgnostic:
    def test_synthetic_ledger_passes_all_selfchecks(self, sandbox, ledger):
        result, violations = sandbox.run(ledger)
        assert violations == [], f"合成域台账自检不通过: {violations}"
        assert result["summary"]["violations"] == 0
        assert result["summary"]["capabilities"] == len(REGISTERED_IDS)
        assert result["summary"]["unregistered_domains"] == 2

    def test_divergence_tally_matches_mechanism(self, sandbox, ledger):
        result, _ = sandbox.run(ledger)
        assert result["summary"]["divergence_tally"] == {
            "OK": 5,
            "OVERCLAIM": 1,
            "REVERIFY": 1,
            "UNVERIFIED": 1,
        }
        assert result["summary"]["without_evidence"] == 1
        assert result["summary"]["with_evidence"] == len(REGISTERED_IDS) - 1

    def test_quote_hits_are_verbatim(self, sandbox, ledger):
        result, _ = sandbox.run(ledger)
        for cap in result["capabilities"]:
            for ref in cap["evidence_refs"]:
                assert ref["quote_hit"] is True, f"{cap['id']} 引文未逐字命中"
                assert len(ref["line_sha256"]) == 16, "行级 sha256 前缀必须落盘"

    def test_renaming_domain_and_ids_keeps_green(self, sandbox, ledger, tool):
        """域名/条目命名空间整体换名后机制结果不变 → 机制不耦合任何域名。"""
        renamed = json.loads(json.dumps(ledger, ensure_ascii=False))
        mapping = {"alpha.": "zeta.", "beta.": "eta.", "gamma.": "theta."}
        domain_map = {
            "test.domain/alpha": "probe.alt/one",
            "test.domain/beta": "probe.alt/two",
            "test.domain/gamma": "probe.alt/three",
            "test.domain/delta": "probe.alt/four",
        }

        def rename_id(cap_id: str) -> str:
            for old, new in mapping.items():
                if cap_id.startswith(old):
                    return new + cap_id[len(old):]
            return cap_id

        for cap in renamed["capabilities"]:
            cap["id"] = rename_id(cap["id"])
            cap["domain"] = domain_map[cap["domain"]]
        for gap in renamed["unregistered_domains"]:
            gap["domain"] = domain_map[gap["domain"]]

        sandbox.snapshot["capabilities"] = {
            rename_id(k): v for k, v in sandbox.snapshot["capabilities"].items()
        }
        sandbox.flush()

        result, violations = sandbox.run(renamed)
        assert violations == [], f"换名后机制失真: {violations}"
        got = {row["id"]: (row["reconciled_level"], row["divergence"]) for row in result["capabilities"]}
        assert got == {rename_id(k): v for k, v in EXPECTED_DIVERGENCE.items()}

    def test_check_is_deterministic(self, sandbox, ledger):
        first, _ = sandbox.run(ledger)
        second, _ = sandbox.run(ledger)
        canon = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True)
        assert canon(first) == canon(second)

    def test_resolve_quote_is_literal(self, tool, sandbox):
        md = sandbox.corpus["reports"]["9101"]["markdown"]
        hit = tool.resolve_quote(md, "12/12 通过")
        assert hit["hit"] is True and hit["line_sha256"]
        miss = tool.resolve_quote(md, "不存在的一句话")
        assert miss["hit"] is False and miss["line_sha256"] == ""


# ════════════════════════════════════════════════════════════════════════
# 5. 反例（变异体判负）：机制不得恒真
# ════════════════════════════════════════════════════════════════════════
class TestMutationGates:
    def test_mutant_missing_capability_trips_c1(self, sandbox, ledger):
        sandbox.snapshot["capabilities"].pop("beta.no_evidence")
        sandbox.flush()
        _, violations = sandbox.run(ledger)
        assert "C1" in _codes(violations), violations

    def test_mutant_unknown_report_trips_c2(self, sandbox, ledger):
        _cap(ledger, "alpha.registration")["evidence"].append(
            {
                "report_id": 9999,
                "as_of": "2026-10-02T00:00:00+00:00",
                "stance": "supports_real",
                "asserted_level": "REAL",
                "quote_match": "12/12 通过",
            }
        )
        _, violations = sandbox.run(ledger)
        assert "C2" in _codes(violations), violations

    def test_mutant_nonverbatim_quote_trips_c3(self, sandbox, ledger):
        _cap(ledger, "alpha.registration")["evidence"][0]["quote_match"] = "转述而非逐字"
        _, violations = sandbox.run(ledger)
        assert "C3" in _codes(violations), violations

    def test_mutant_divergence_without_action_trips_c4(self, sandbox, ledger):
        _cap(ledger, "beta.overclaim")["action"] = "   "
        _, violations = sandbox.run(ledger)
        assert "C4" in _codes(violations), violations

    def test_mutant_claim_flip_trips_c5(self, sandbox, ledger):
        _cap(ledger, "alpha.promotion")["claimed_level"] = "REAL"
        _, violations = sandbox.run(ledger)
        assert "C5" in _codes(violations), violations

    def test_mutant_gap_quote_trips_c6(self, sandbox, ledger):
        ledger["unregistered_domains"][0]["evidence"][0]["quote_match"] = "缺口没有这句话"
        _, violations = sandbox.run(ledger)
        assert "C6" in _codes(violations), violations

    def test_mutant_drift_trips_c7(self, sandbox, ledger):
        sandbox.resolved_path.write_text(
            json.dumps({"summary": {"capabilities": 0}, "capabilities": [], "unregistered_domains": []}),
            encoding="utf-8",
        )
        _, violations = sandbox.run(ledger)
        assert "C7" in _codes(violations), violations

    def test_mutant_stale_evidence_trips_c8(self, sandbox, ledger):
        ledger["curation"]["as_of"] = "2027-01-01T00:00:00+00:00"
        _, violations = sandbox.run(ledger)
        assert "C8" in _codes(violations), violations

    def test_mutant_unconsumed_doc_trips_c9(self, sandbox, ledger):
        ledger["curation"]["must_consume"].append(
            {"path": "docs/synthetic_domain/other_review.md", "why": "合成域另一份复核文档"}
        )
        _, violations = sandbox.run(ledger)
        assert "C9" in _codes(violations), violations

    def test_mutant_doc_body_change_trips_c10(self, sandbox, ledger):
        doc = next(iter(sandbox.corpus["docs"].values()))
        sandbox.doc_path(doc["path"]).write_text("# 合成域复核文档\n\n正文已被改写。\n", encoding="utf-8")
        _, violations = sandbox.run(ledger)
        assert "C10" in _codes(violations), violations


# ════════════════════════════════════════════════════════════════════════
# 6. 域词零命中（判例 #608 清洗判据；词表当数据）
# ════════════════════════════════════════════════════════════════════════
class TestDomainNeutralityGuard:
    def test_denylist_is_data_not_code(self, leak_scan):
        denylist = leak_scan.load_denylist(DENYLIST)
        assert len(denylist["ascii_words"]) + len(denylist["cjk_words"]) >= 20
        assert denylist["scan_paths"], "扫描范围必须显式声明"
        assert "config/domain_word_denylist.json" in denylist["exempt"], "词表自身须豁免"
        assert LEDGER_FIXTURE.name == "ledger.json"

    def test_owned_paths_have_zero_domain_words(self, leak_scan):
        result = leak_scan.scan(None, DENYLIST, root=ROOT)
        assert result["files_missing"] == [], f"扫描目标缺失: {result['files_missing']}"
        assert result["hit_count"] == 0, f"域词渗漏: {result['hits'][:5]}"
        assert len(result["files_scanned"]) >= 5

    def test_backend_runs_module_has_zero_domain_words(self, leak_scan):
        rel = "backend/api/tensorphysics_runs.py"
        result = leak_scan.scan([rel], DENYLIST, root=ROOT)
        assert result["files_scanned"] == [rel]
        assert result["hit_count"] == 0, f"{rel} 仍含域词: {result['hits'][:5]}"

    def test_campaign_ledger_test_names_carry_no_domain_word(self, leak_scan):
        denylist = leak_scan.load_denylist(DENYLIST)
        patterns = leak_scan.compile_patterns(denylist)
        names = sorted(p.name for p in (ROOT / "tests").glob("test_*campaign_ledger*"))
        assert names == [Path(__file__).name], f"台账机制测试应只留合成域版本: {names}"
        for name in names:
            assert leak_scan.scan_text(name, patterns) == [], f"测试文件名含域词: {name}"

    def test_guard_is_not_vacuous(self, leak_scan):
        denylist = leak_scan.load_denylist(DENYLIST)
        patterns = leak_scan.compile_patterns(denylist)
        probe = denylist["ascii_words"][0]
        planted = f"x = '{probe}'  # planted probe\n"
        assert leak_scan.scan_text(planted, patterns), "扫描器对植入域词失明"
        assert leak_scan.scan_text("x = 1  # 机制\n", patterns) == []