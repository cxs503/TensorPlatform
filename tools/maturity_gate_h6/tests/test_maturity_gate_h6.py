"""H6 成熟度门测试（含负控：篡改必须被检出，缺失源必须降级为不通过）。

合成域名词条（``cap.a``/``cap.b``）——测试夹具不含任何真实业务域词汇。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_gate_h6 import core  # noqa: E402

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
SYNTH_TABLE = """# 合成对账表（生成物，勿手改）

- 能力数：3（有证据 2 / 无证据 1）
- 分歧：OVERCLAIM 1 · REVERIFY 0 · UNVERIFIED 0 · OK 2
- 未注册域缺口：0；证据行：2；违规：0

| 能力 | 域 | 自述 | 对账 | 分歧 | 最新证据 | 依据 |
|---|---|---|---|---|---|---|
| `cap.a` | dom-x | REAL | **MOCK** | OVERCLAIM | r1 | x |
| `cap.b` | dom-x | STUB | **WITHHELD** | OK | - | 无证据 |
| `cap.c` | dom-y | REAL | **REAL** | OK | r1 | x |
"""

SYNTH_CORPUS = {
    "schema_version": "2.0",
    "reports": {"r1": {"markdown": "# t\n\n裁决：**MOCK** —— synthetic response\n第二行\n"}},
}


def _write(tmp_path: Path, name: str, payload) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture()
def snapshot(tmp_path):
    data = {"capabilities": {"cap.a": "REAL", "cap.b": "STUB", "cap.c": "REAL"},
            "source": {"observed_at": "2026-01-01T00:00:00Z"}}
    return core.load_flat_snapshot("baseline", _write(tmp_path, "snap.json", data))


@pytest.fixture()
def ledger(tmp_path):
    line = "裁决：**MOCK** —— synthetic response"
    data = {"capabilities": [{
        "id": "cap.a", "claimed_level": "REAL", "reconciled_level": "MOCK",
        "divergence": "OVERCLAIM", "latest_evidence_as_of": "2026-02-01T00:00:00+08:00",
        "evidence_refs": [{"report_id": "r1", "asserted_level": "MOCK",
                           "line": line, "line_sha256": core.line_sha256(line)}],
    }, {
        "id": "cap.b", "claimed_level": "STUB", "reconciled_level": "WITHHELD",
        "divergence": "OK", "evidence_refs": [],
    }, {
        "id": "cap.c", "claimed_level": "REAL", "reconciled_level": "REAL",
        "divergence": "OK",
        "latest_evidence_as_of": "2026-02-02T00:00:00+08:00",
        "evidence_refs": [{"report_id": "r1", "asserted_level": "REAL", "line": "第二行",
                           "line_sha256": core.line_sha256("第二行")}],
    }]}
    return core.load_ledger("current", _write(tmp_path, "ledger.json", data))


# ---------------------------------------------------------------------------
# 五级阶梯
# ---------------------------------------------------------------------------
def test_ladder_is_strictly_monotonic_and_complete():
    assert list(core.LEVELS) == ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
    ranks = [core.LEVEL_RANK[k] for k in core.LEVELS]
    assert ranks == sorted(ranks) and len(set(ranks)) == 5
    assert set(core.LEVEL_DEFINITIONS) == set(core.LEVELS)


def test_unknown_level_is_a_spec_error_not_a_silent_pass():
    with pytest.raises(core.GateError):
        core.rank("REALISH")


@pytest.mark.parametrize("claimed,reconciled,ev,expected", [
    ("REAL", "REAL", 0, "OK"),
    ("REAL", "MOCK", 2, "OVERCLAIM"),
    ("STUB", "REAL", 1, "REVERIFY"),
    ("REAL", "WITHHELD", 0, "UNVERIFIED"),
    ("NOT_SUPPORTED", "WITHHELD", 0, "OK"),   # 主张不高于未验证默认 → 不算分歧
])
def test_divergence_classification(claimed, reconciled, ev, expected):
    assert core.label_divergence(claimed, reconciled, ev) == expected


# ---------------------------------------------------------------------------
# 轴 A：自述 ↔ 对账
# ---------------------------------------------------------------------------
def test_cross_source_counts_and_coverage(snapshot, ledger):
    xs = core.cross_source(snapshot, ledger)
    assert xs["available"] and xs["total"] == 3
    assert xs["counts"] == {"OK": 2, "OVERCLAIM": 1, "REVERIFY": 0, "UNVERIFIED": 0}
    assert xs["coverage"] == 1.0
    assert xs["recomputed_mismatch"] == []


def test_cross_source_flags_stored_label_tamper(snapshot, ledger):
    ledger.divergence["cap.a"] = "OK"      # 篡改存储标签
    xs = core.cross_source(snapshot, ledger)
    assert [m["capability_id"] for m in xs["recomputed_mismatch"]] == ["cap.a"]


def test_cross_source_reports_orphan_capabilities(snapshot, ledger):
    ledger.levels.pop("cap.c")
    xs = core.cross_source(snapshot, ledger)
    assert xs["only_claimed"] == ["cap.c"] and xs["coverage"] < 1.0


# ---------------------------------------------------------------------------
# 轴 B：引文逐字绑定
# ---------------------------------------------------------------------------
def test_quote_binding_matches_and_detects_drift(ledger):
    ok = core.quote_binding(ledger, SYNTH_CORPUS, Path("/nonexistent"))
    assert ok["drift"] == 0 and ok["matched"] == 2 and ok["match_rate"] == 1.0

    ledger.evidence["cap.a"][0]["line_sha256"] = "0" * 16
    bad = core.quote_binding(ledger, SYNTH_CORPUS, Path("/nonexistent"))
    assert bad["drift"] == 1
    assert bad["findings"][0]["kind"] == "line_sha256_drift"


def test_quote_binding_detects_absent_quote_and_unresolved_source(ledger):
    ledger.evidence["cap.a"][0]["line"] = "这句不在正文里"
    out = core.quote_binding(ledger, SYNTH_CORPUS, Path("/nonexistent"))
    assert any(f["kind"] == "quote_absent" for f in out["findings"])

    ledger.evidence["cap.a"][0]["report_id"] = "missing-report"
    out2 = core.quote_binding(ledger, SYNTH_CORPUS, Path("/nonexistent"))
    assert any(f["kind"] == "source_unresolved" for f in out2["findings"])


# ---------------------------------------------------------------------------
# 轴 C：人读表 ↔ 台账重算
# ---------------------------------------------------------------------------
def test_markdown_recount_clean(ledger):
    rc = core.recount_markdown(core.parse_markdown_claims(SYNTH_TABLE), ledger)
    assert [m for m in rc["metrics"] if not m["ok"]] == []
    assert rc["row_findings"] == []


def test_markdown_recount_detects_number_and_row_drift(ledger):
    tampered = SYNTH_TABLE.replace("有证据 2", "有证据 3").replace(
        "| `cap.c` | dom-y | REAL | **REAL** | OK |", "| `cap.c` | dom-y | REAL | **MOCK** | OK |")
    rc = core.recount_markdown(core.parse_markdown_claims(tampered), ledger)
    assert any(m["metric"] == "with_evidence" and not m["ok"] for m in rc["metrics"])
    assert any(f["kind"] == "level_drift" for f in rc["row_findings"])


def test_markdown_claims_parse_requires_known_levels():
    with pytest.raises(core.GateError):
        core.parse_markdown_claims("| `cap.z` | d | VERY_REAL | **REAL** | OK |\n")


# ---------------------------------------------------------------------------
# 轴 D + 增量账本 + 画像
# ---------------------------------------------------------------------------
def test_evidence_support_flags_unsupported_level(ledger):
    ledger.levels["cap.a"] = "REAL"        # 证据只断言 MOCK
    out = core.evidence_support(ledger)
    assert out["orphan"] == 1
    assert out["findings"][0]["kind"] == "level_unsupported"


def test_increment_ledger_directions_and_chain(snapshot, ledger):
    inc = core.build_increment_ledger(snapshot, ledger)
    dirs = {e["capability_id"]: e["direction"] for e in inc["entries"]}
    assert dirs == {"cap.a": "DEMOTE", "cap.b": "PROMOTE", "cap.c": "HOLD"}
    assert inc["counts"]["DEMOTE"] == 1 and inc["counts"]["PROMOTE"] == 1
    assert inc["chain_len"] == 3
    # 确定性：同输入两次运行链头一致
    assert core.build_increment_ledger(snapshot, ledger)["chain_head"] == inc["chain_head"]
    # 篡改一条事件 → 链头必变
    ledger.levels["cap.c"] = "MOCK"
    assert core.build_increment_ledger(snapshot, ledger)["chain_head"] != inc["chain_head"]


def test_maturity_profile_math():
    prof = core.maturity_profile({"a": "REAL", "b": "WITHHELD", "c": "NOT_SUPPORTED"})
    assert prof["histogram"]["REAL"] == 1 and prof["n"] == 3
    assert prof["mean_rank"] == pytest.approx((4 + 3 + 0) / 3)
    assert prof["maturity_score"] == pytest.approx((4 + 3 + 0) / 3 / 4)


# ---------------------------------------------------------------------------
# 门禁裁定：缺失源必须降级为「不通过」，不得静默成绿
# ---------------------------------------------------------------------------
def _gate(snapshot, ledger, claims=None):
    return core.run_gate(claimed=snapshot, reconciled=ledger,
                         markdown_claims=claims if claims is not None
                         else core.parse_markdown_claims(SYNTH_TABLE),
                         corpus=SYNTH_CORPUS, work_root=Path("/nonexistent"))


def test_gate_passes_on_clean_synthetic_inputs(snapshot, ledger):
    gate = _gate(snapshot, ledger)
    assert gate["verdict"] == "PASS", gate["criteria"]
    assert gate["criteria_pass_rate"] == 1.0


def test_gate_fails_when_an_axis_is_broken(snapshot, ledger):
    ledger.evidence["cap.a"][0]["line_sha256"] = "f" * 16
    gate = _gate(snapshot, ledger)
    assert gate["verdict"] == "FAIL"
    c5 = next(c for c in gate["criteria"] if c["criterion_id"] == "C5")
    assert c5["pass"] is False and c5["value"] == 1


def test_gate_degrades_when_source_missing(snapshot, tmp_path):
    missing = core.load_ledger("current", tmp_path / "nope.json")
    assert missing.available is False
    gate = core.run_gate(claimed=snapshot, reconciled=missing,
                         markdown_claims={}, corpus={}, work_root=tmp_path)
    assert gate["verdict"] == "FAIL"           # 缺失源绝不静默成绿
    # C2 只校验阶梯定义本身（与源无关），其余数据类判据必须全部不通过
    assert all(c["pass"] is False or c["value"] is None
               for c in gate["criteria"] if c["criterion_id"] != "C2")


def test_unknown_threshold_key_is_rejected(snapshot, ledger):
    with pytest.raises(core.GateError):
        core.run_gate(claimed=snapshot, reconciled=ledger, markdown_claims={},
                      corpus={}, work_root=Path("/nonexistent"),
                      thresholds={"made_up_key": 1})


def test_verify_detects_tampered_gate_artifact(snapshot, ledger):
    gate = _gate(snapshot, ledger)
    assert core.verify_artifact(gate, claimed=snapshot, reconciled=ledger,
                                markdown_claims=core.parse_markdown_claims(SYNTH_TABLE),
                                corpus=SYNTH_CORPUS, work_root=Path("/nonexistent")) == []
    tampered = json.loads(json.dumps(gate))
    tampered["criteria"][0]["pass"] = not tampered["criteria"][0]["pass"]
    problems = core.verify_artifact(tampered, claimed=snapshot, reconciled=ledger,
                                    markdown_claims=core.parse_markdown_claims(SYNTH_TABLE),
                                    corpus=SYNTH_CORPUS, work_root=Path("/nonexistent"))
    assert problems


# ---------------------------------------------------------------------------
# 端到端（真实仓内工件；缺失则跳过，不制造假绿）
# ---------------------------------------------------------------------------
REAL_SPEC = REPO_ROOT / "data" / "maturity_gate_h6" / "h6.spec.json"


@pytest.mark.skipif(not REAL_SPEC.is_file(), reason="H6 spec 不在本工作树")
def test_end_to_end_on_repo_artifacts_is_deterministic():
    from tools.maturity_gate_h6 import cli
    inputs = cli.load_inputs(REPO_ROOT, REAL_SPEC)
    g1 = cli.run_gate_from(inputs, REPO_ROOT)
    g2 = cli.run_gate_from(inputs, REPO_ROOT)
    assert json.dumps(g1, sort_keys=True) == json.dumps(g2, sort_keys=True)
    # 第三源（live probe）应显式降级而不是伪造等级
    probe = [s for s in g1["sources"] if "live_probe" in s["source_id"]]
    assert probe and probe[0]["available"] is False
    bench = cli.build_mutation_benchmark(inputs, REPO_ROOT)
    assert bench["recall"] == 1.0 and bench["false_positives"] == 0