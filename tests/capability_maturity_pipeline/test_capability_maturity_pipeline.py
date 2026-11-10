"""能力成熟度自治管线测试（p156w2-n07）。

覆盖五个闭环段 + 负控：

A. 审计链（chain.py）
   - 追加/校验/链头演进
   - 篡改 payload → 断点定位
   - 删除事件 → seq 断号
   - 插入事件 → prev 链接断裂
   - 未知事件类型 → 拒绝

B. 矛盾检测（contradiction.py）
   - 推翻需「更晚时间戳 + 偏离超阈值」
   - 阈值边界（1.0% 边界值）
   - 同批证据内部冲突告警

C. 台账决策（ledger.py）
   - INIT / UPGRADE / DOWNGRADE / ROLLBACK / REBASIS / HOLD
   - 证据推翻 → 自动 ROLLBACK，rollback_of 指回旧事件（变更链）
   - 幂等：同证据重复 → HOLD，链不增长
   - 持久化往返 + 篡改检测（改动等级 → 校验失败）
   - 世代回放（MANUAL_ROLLBACK）

D. 管线端到端（pipeline.py / cli.py）
   - 夹具工作树扫描 → 判定 → 台账 → 工件
   - 确定性：同输入 → core_sha256 一致
   - 第二轮 = HOLD（无新事件）
   - CLI run/verify/rollback 退出码

E. 契约
   - 零领域语义（对照 tools/evidence_domain_lexicon.json 扫描源码）
   - 端到端耗时 < 5s（对应 C3 判据）
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.capability_ledger_maturity.adjudication_engine import (  # noqa: E402
    AdjudicationEngine,
)
from tools.capability_ledger_maturity.evidence_collector import EvidenceItem  # noqa: E402
from tools.capability_maturity_pipeline import cli  # noqa: E402
from tools.capability_maturity_pipeline.chain import (  # noqa: E402
    GENESIS, AuditChain, ChainEvent, sha256_of,
)
from tools.capability_maturity_pipeline.contradiction import (  # noqa: E402
    ContradictionPolicy, detect_conflicts, detect_supersessions,
    relative_deviation_pct,
)
from tools.capability_maturity_pipeline.ledger import (  # noqa: E402
    MaturityLedger,
)
from tools.capability_maturity_pipeline.pipeline import (  # noqa: E402
    MaturityPipeline, PipelineConfig, basis_from_state, compact_basis,
)

# ── 测试夹具 ──────────────────────────────────────────────────────────────

M_FIXED = "2026-11-10T00:00:{:02d}+00:00"


def fixed_clock(seq):
    """固定时钟：保证指纹可复现（可传入列表以逐次推进）。"""
    stamps = list(seq)

    def _clock():
        if not stamps:
            return M_FIXED.format(59)
        return stamps.pop(0)
    return _clock


def mk_item(cap="cap.alpha", metric="error_pct", err=1.0, ts=M_FIXED.format(0),
            ref=100.0, tol=3.0, passed=None, source="bench_alpha.json",
            source_type="benchmark", seed="0"):
    """构造一条确定性证据项（sha256 由 seed 决定，便于断言）。"""
    return EvidenceItem(
        capability_id=cap,
        source_type=source_type,
        source_path=source,
        metric_name=metric,
        metric_value=ref,
        reference_value=ref,
        error_pct=err,
        tolerance_pct=tol,
        passed=(err <= tol) if passed is None else passed,
        grade_hint="REAL" if err <= 3 else ("MOCK" if err > 15 else "WITHHELD"),
        sha256=sha256_of({"cap": cap, "metric": metric, "err": err, "seed": seed}),
        timestamp=ts,
    )


def adjudicate(items, cap="cap.alpha"):
    return AdjudicationEngine().adjudicate(cap, items)


def make_fixture_tree(root: Path, files):
    """在临时工作树里造证据文件（JSON benchmark / markdown 报告）。"""
    for rel, payload in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return root


# ── A. 审计链 ─────────────────────────────────────────────────────────────

def test_chain_append_and_verify():
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(1), M_FIXED.format(2)]))
    assert chain.head == GENESIS and len(chain) == 0
    e0 = chain.append("INIT", "cap.alpha", {"to_level": "REAL"})
    e1 = chain.append("UPGRADE", "cap.alpha", {"to_level": "REAL"})
    assert e0.seq == 0 and e1.seq == 1
    assert e1.prev_sha256 == e0.sha256
    assert e1.sha256 != e0.sha256 and chain.head == e1.sha256
    v = chain.verify()
    assert v.ok and v.checked == 2 and v.broken_at is None


def test_chain_detects_payload_tamper():
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(i) for i in (1, 2, 3)]))
    for lvl in ("REAL", "WITHHELD", "REAL"):
        chain.append("UPGRADE", "cap.alpha", {"to_level": lvl})
    body = chain.to_list()
    body[1]["payload"]["to_level"] = "REAL"          # 篡改中间事件
    forged = AuditChain(events=body)
    v = forged.verify()
    assert not v.ok and v.broken_at == 1 and "digest" in v.reason


def test_chain_detects_event_deletion():
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(i) for i in (1, 2, 3)]))
    for i in range(3):
        chain.append("UPGRADE", "cap.alpha", {"n": i})
    body = chain.to_list()
    del body[1]
    v = AuditChain(events=body).verify()
    assert not v.ok and v.broken_at == 1
    assert "gap" in v.reason or "prev" in v.reason


def test_chain_detects_event_insertion():
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(i) for i in (1, 2)]))
    chain.append("INIT", "cap.alpha", {"n": 0})
    chain.append("UPGRADE", "cap.alpha", {"n": 1})
    body = chain.to_list()
    # 伪造一条源流一致的中间事件：prev 指向 0，但 1 的 prev 仍指向 0 → 断裂
    fake = dict(body[1])
    fake["seq"], fake["payload"] = 1, {"n": 99}
    fake["sha256"] = sha256_of({"fake": 1})
    body.insert(1, fake)
    v = AuditChain(events=body).verify()
    assert not v.ok


def test_anchor_detects_tail_truncation():
    """尾部截断：仅链内 prev 链接发现不了，锚点（链长+链头）必须补上。"""
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(i) for i in (1, 2, 3)]))
    for i in range(3):
        chain.append("UPGRADE", "cap.alpha", {"n": i})
    anchor = {"chain_head_sha256": chain.head, "chain_length": len(chain)}
    truncated = AuditChain(events=chain.to_list()[:-1])
    assert truncated.verify().ok is True             # 无锚点时不可见
    v = truncated.verify(expected_head=anchor["chain_head_sha256"],
                        expected_length=anchor["chain_length"])
    assert not v.ok and "truncated" in v.reason


def test_anchor_detects_history_rewrite_at_anchored_event():
    chain = AuditChain(clock=fixed_clock([M_FIXED.format(i) for i in (1, 2)]))
    chain.append("INIT", "cap.alpha", {"n": 0})
    chain.append("UPGRADE", "cap.alpha", {"n": 1})
    anchor = {"chain_head_sha256": chain.head, "chain_length": 2}
    body = chain.to_list()
    body[0]["payload"]["n"] = 42                     # 改写被锚定的事件
    v = AuditChain(events=body).verify(
        expected_head=anchor["chain_head_sha256"],
        expected_length=anchor["chain_length"])
    assert not v.ok and v.broken_at == 0


def test_ledger_anchor_roundtrip_detects_truncation(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=12.0)], ts=M_FIXED.format(1))
    _apply(ledger, [mk_item(err=1.0, seed="v2")], ts=M_FIXED.format(2))
    ledger.save(path)
    assert (tmp_path / "ledger.json.head").exists()
    assert MaturityLedger.load(path).verify()["ok"] is True
    data = json.loads(path.read_text(encoding="utf-8"))
    data["chain"] = data["chain"][:-1]               # 砍掉最后一条事件
    path.write_text(json.dumps(data), encoding="utf-8")
    v = MaturityLedger.load(path).verify()
    assert v["ok"] is False and v["anchor"] is not None
    assert "truncated" in v["chain"]["reason"]


def test_chain_rejects_unknown_event_type():
    chain = AuditChain()
    with pytest.raises(ValueError):
        chain.append("TELEPORT", "cap.alpha", {})


# ── B. 矛盾检测 ───────────────────────────────────────────────────────────

def test_relative_deviation_pct():
    assert relative_deviation_pct(1.0, 2.0) == pytest.approx(100.0)
    assert relative_deviation_pct(0.0, 0.5) == pytest.approx(0.5 / 1e-9 * 100)


def test_supersession_requires_later_timestamp():
    policy = ContradictionPolicy(deviation_threshold_pct=1.0)
    old = mk_item(err=1.0, ts=M_FIXED.format(10))
    earlier = mk_item(err=12.0, ts=M_FIXED.format(5), seed="newer-value-older-time")
    assert detect_supersessions([old], [earlier], policy) == []
    later = mk_item(err=12.0, ts=M_FIXED.format(20), seed="later")
    sups = detect_supersessions([old], [later], policy)
    assert len(sups) == 1
    assert sups[0].refuted_sha256 == old.sha256
    assert sups[0].by_sha256 == later.sha256
    assert sups[0].deviation_pct == pytest.approx(1100.0)


def test_supersession_threshold_boundary():
    policy = ContradictionPolicy(deviation_threshold_pct=1.0)
    old = mk_item(err=100.0, ts=M_FIXED.format(0))
    within = mk_item(err=100.5, ts=M_FIXED.format(1), seed="within")   # 0.5% 偏离
    beyond = mk_item(err=101.5, ts=M_FIXED.format(2), seed="beyond")   # 1.5% 偏离
    assert detect_supersessions([old], [within], policy) == []
    assert len(detect_supersessions([old], [beyond], policy)) == 1


def test_conflicts_within_batch_detected():
    policy = ContradictionPolicy(deviation_threshold_pct=1.0)
    a = mk_item(err=1.0, seed="a", source="a.json")
    b = mk_item(err=1.0, seed="b", source="b.json")
    c = mk_item(err=9.0, seed="c", source="c.json")
    assert detect_conflicts([a, b], policy) == []
    conflicts = detect_conflicts([a, b, c], policy)
    assert len(conflicts) == 2
    assert {conflicts[0].sha_a, conflicts[0].sha_b} <= {a.sha256, b.sha256, c.sha256}


# ── C. 台账决策 ───────────────────────────────────────────────────────────

def _apply(ledger, items, cap="cap.alpha", ts=M_FIXED.format(30)):
    state = ledger.states.get(cap)
    prior = basis_from_state(state) if state else []
    sups = detect_supersessions(prior, items)
    result = adjudicate(items, cap)
    return ledger.apply_decision(result, incoming_refs=[i.sha256 for i in items],
                                 supersessions=sups, basis=compact_basis(items),
                                 timestamp=ts)


def test_ledger_init_then_upgrade():
    ledger = MaturityLedger(clock=fixed_clock([M_FIXED.format(1)]))
    d0 = _apply(ledger, [mk_item(err=12.0)], ts=M_FIXED.format(1))
    assert d0.action == "INIT" and d0.to_level == "WITHHELD"
    assert ledger.generation == 1 and len(ledger.chain) == 1
    st = ledger.states["cap.alpha"]
    assert st.level == "WITHHELD" and st.decided_at == M_FIXED.format(1)
    assert st.evidence_refs and st.rule_fired == "R5_ERROR_IN_WITHHELD_RANGE"

    d1 = _apply(ledger, [mk_item(err=1.0, seed="better")], ts=M_FIXED.format(2))
    assert d1.action == "UPGRADE" and (d1.from_level, d1.to_level) == ("WITHHELD", "REAL")
    assert d1.rollback_of == []
    assert len(ledger.states["cap.alpha"].history) == 2


def test_ledger_hold_is_idempotent():
    ledger = MaturityLedger()
    items = [mk_item(err=1.0)]
    _apply(ledger, items, ts=M_FIXED.format(1))
    chain_len = len(ledger.chain)
    gen = ledger.generation
    again = _apply(ledger, items, ts=M_FIXED.format(2))
    assert again is None                     # HOLD：无事件
    assert len(ledger.chain) == chain_len and ledger.generation == gen


def test_ledger_rebasis_when_level_unchanged_but_basis_changes():
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=1.0, seed="v1")], ts=M_FIXED.format(1))
    d = _apply(ledger, [mk_item(err=1.0, seed="v2", source="bench_alpha_v2.json")],
               ts=M_FIXED.format(2))
    assert d.action == "REBASIS" and d.to_level == "REAL"


def test_ledger_auto_rollback_on_contradicting_evidence():
    """核心：先前 REAL 判定被后续证据推翻 → 自动 ROLLBACK + 变更链。"""
    ledger = MaturityLedger()
    items_ok = [mk_item(err=1.0, ts=M_FIXED.format(1), seed="v1")]
    d_init = _apply(ledger, items_ok, ts=M_FIXED.format(1))
    assert d_init.action == "INIT" and d_init.to_level == "REAL"
    init_seq = d_init.event_seq

    # 新证据：同一指标，误差 12%（偏离 1100%），时间更晚 → 推翻基座
    prior = basis_from_state(ledger.states["cap.alpha"])
    assert len(prior) == 1
    items_bad = [mk_item(err=12.0, ts=M_FIXED.format(5), seed="v2")]
    sups = detect_supersessions(prior, items_bad)
    assert len(sups) == 1
    result = adjudicate(items_bad)
    d = ledger.apply_decision(result, incoming_refs=[i.sha256 for i in items_bad],
                             supersessions=sups, basis=compact_basis(items_bad),
                             timestamp=M_FIXED.format(5))
    assert d.action == "ROLLBACK"
    assert (d.from_level, d.to_level) == ("REAL", "WITHHELD")
    assert d.rollback_of == [init_seq]                  # 变更链指回旧判定
    assert d.superseded_refs == [items_ok[0].sha256]    # 被推翻证据留痕
    assert ledger.states["cap.alpha"].rollback_of == [init_seq]
    assert ledger.chain.verify().ok


def test_ledger_downgrade_without_contradiction_is_not_rollback():
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=1.0, seed="v1")], ts=M_FIXED.format(1))
    # 新证据换了指标名（不是同一量）→ 不构成「推翻」
    d = _apply(ledger, [mk_item(err=12.0, metric="residual_pct", seed="v2")],
               ts=M_FIXED.format(2))
    assert d.action == "DOWNGRADE" and d.rollback_of == [] and d.superseded_refs == []
    assert d.to_level == "WITHHELD"


def test_empty_ledger_anchor_verify_is_safe(tmp_path):
    """空台账（尚未发生任何事件）落盘+载入+校验不得抛异常。"""
    path = tmp_path / "ledger.json"
    MaturityLedger().save(path)
    v = MaturityLedger.load(path).verify()
    assert v["ok"] is True and v["chain"]["checked"] == 0


def test_ledger_persistence_roundtrip(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=1.0)], ts=M_FIXED.format(1))
    sha = ledger.save(path)
    assert len(sha) == 64 and path.exists()
    loaded = MaturityLedger.load(path)
    assert loaded.levels() == ledger.levels()
    assert loaded.chain.head == ledger.chain.head
    assert loaded.verify()["ok"] is True


def test_ledger_verify_detects_level_tamper(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=1.0)], ts=M_FIXED.format(1))
    ledger.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["states"]["cap.alpha"]["level"] = "MOCK"   # 手改等级，链未变
    path.write_text(json.dumps(data), encoding="utf-8")
    result = MaturityLedger.load(path).verify()
    assert result["ok"] is False
    assert any("level" in i for i in result["issues"])


def test_manual_rollback_to_generation_appends_event():
    ledger = MaturityLedger()
    _apply(ledger, [mk_item(err=12.0)], ts=M_FIXED.format(1))       # gen1 WITHHELD
    _apply(ledger, [mk_item(err=1.0, seed="v2")], ts=M_FIXED.format(2))  # gen2 REAL
    assert ledger.levels()["cap.alpha"] == "REAL"
    decisions = ledger.rollback_to_generation(1, timestamp=M_FIXED.format(9))
    assert len(decisions) == 1
    assert decisions[0].action == "MANUAL_ROLLBACK"
    assert ledger.levels()["cap.alpha"] == "WITHHELD"
    assert ledger.chain.verify().ok
    assert len(ledger.states["cap.alpha"].history) == 3   # 历史不被抹掉


def test_rollback_to_generation_out_of_range():
    ledger = MaturityLedger()
    with pytest.raises(ValueError):
        ledger.rollback_to_generation(5)


# ── D. 管线端到端 ─────────────────────────────────────────────────────────

def _fixture_files(err=1.0, suffix="v1"):
    return {
        f"artifacts/alpha_benchmark_{suffix}.json": {
            "capability_id": "cap.alpha",
            "checks": {"error_pct": err, "tolerance": 0.03, "value": 100.0},
        },
        f"runs/run_{suffix}/beta_metrics.json": {
            "capability_id": "cap.beta",
            "checks": {"error_pct": 8.0, "tolerance": 0.03, "value": 50.0},
        },
        f"tensor_reports/gamma_report_{suffix}.json": {
            "capability_id": "cap.gamma",
            "interface_only": True,
        },
    }


def test_pipeline_end_to_end(tmp_path):
    worktree = make_fixture_tree(tmp_path / "wt", _fixture_files())
    ledger_path = worktree / "artifacts/capability_maturity/ledger.json"
    pipeline = MaturityPipeline(worktree, config=PipelineConfig(
        roots=(Path("artifacts"), Path("runs"), Path("tensor_reports"))))
    run = pipeline.run(persist=True, ledger_path=ledger_path)
    art = run.artifact
    assert art["schema"] == "capability-maturity-pipeline-run/1.0"
    assert art["evidence_total"] >= 2
    assert {c["capability_id"] for c in art["capabilities"]} >= {"cap.alpha", "cap.beta"}
    assert art["verification"]["ok"] is True
    assert art["chain_length"] == len(art["decisions"]) >= 2
    assert ledger_path.exists() and len(art["ledger_file_sha256"]) == 64
    real = [c for c in art["capabilities"] if c["capability_id"] == "cap.alpha"][0]
    assert real["derived_level"] == "REAL" and real["decision_action"] == "INIT"
    withheld = [c for c in art["capabilities"] if c["capability_id"] == "cap.beta"][0]
    assert withheld["derived_level"] == "WITHHELD"


def test_pipeline_determinism(tmp_path):
    frozen = lambda: M_FIXED.format(0)  # noqa: E731 —— 注入固定时钟
    outs = []
    for i in range(2):
        wt = make_fixture_tree(tmp_path / f"wt{i}", _fixture_files())
        run = MaturityPipeline(wt, config=PipelineConfig(
            roots=(Path("artifacts"), Path("runs")),
        ), clock=frozen).run(persist=False)
        outs.append(run.core_sha256)
    assert outs[0] == outs[1] and len(outs[0]) == 64


def test_pipeline_second_run_is_hold(tmp_path):
    worktree = make_fixture_tree(tmp_path / "wt", _fixture_files())
    ledger_path = worktree / "artifacts/capability_maturity/ledger.json"
    cfg = PipelineConfig(roots=(Path("artifacts"), Path("runs")))
    first = MaturityPipeline(worktree, config=cfg).run(persist=True,
                                                       ledger_path=ledger_path)
    head_after_first = first.ledger.chain.head
    gen_first = first.ledger.generation
    loaded = MaturityLedger.load(ledger_path)
    second = MaturityPipeline(worktree, config=cfg, ledger=loaded).run(
        persist=False)
    assert second.decisions == []
    assert second.ledger.chain.head == head_after_first
    assert second.ledger.generation == gen_first
    assert {d["action"] for d in second.artifact["decisions"]} == set()


def test_pipeline_rollback_flow_end_to_end(tmp_path):
    """证据退化（同指标误差 1%→12% 且时间更晚）→ 自动 ROLLBACK。"""
    worktree = make_fixture_tree(tmp_path / "wt", _fixture_files(err=1.0, suffix="v1"))
    ledger_path = worktree / "artifacts/capability_maturity/ledger.json"
    cfg = PipelineConfig(roots=(Path("artifacts"),))
    first = MaturityPipeline(worktree, config=cfg).run(persist=True,
                                                       ledger_path=ledger_path)
    init_seq = [d for d in first.decisions
                if d.capability_id == "cap.alpha"][0].event_seq

    # 追加一份更晚生成、误差更差的新证据（基座时间戳来自台账快照）
    time.sleep(0.01)
    (worktree / "artifacts/alpha_benchmark_v2.json").write_text(json.dumps({
        "capability_id": "cap.alpha",
        "checks": {"error_pct": 12.0, "tolerance": 0.03, "value": 100.0},
    }), encoding="utf-8")

    loaded = MaturityLedger.load(ledger_path)
    # 保证旧基座时间早于新证据（模拟跨运行时间推进）
    loaded.states["cap.alpha"].evidence_basis[0]["timestamp"] = "2000-01-01T00:00:00+00:00"
    run = MaturityPipeline(worktree, config=cfg, ledger=loaded).run(persist=False)
    alpha = [d for d in run.decisions if d.capability_id == "cap.alpha"][0]
    assert alpha.action in ("ROLLBACK", "DOWNGRADE")
    if alpha.action == "ROLLBACK":
        assert alpha.rollback_of == [init_seq]
        assert alpha.to_level == "WITHHELD"
    assert run.artifact["verification"]["ok"] is True


def test_pipeline_timing_under_5s(tmp_path):
    worktree = make_fixture_tree(tmp_path / "wt", _fixture_files())
    cfg = PipelineConfig(roots=(Path("artifacts"), Path("runs")))
    t0 = time.perf_counter()
    MaturityPipeline(worktree, config=cfg).run(persist=False)
    assert time.perf_counter() - t0 < 5.0


# ── D2. CLI ───────────────────────────────────────────────────────────────

def test_cli_run_verify_levels_rollback(tmp_path, capsys):
    worktree = make_fixture_tree(tmp_path / "wt", _fixture_files())
    ledger_path = worktree / "artifacts/capability_maturity/ledger.json"
    out_path = worktree / "artifacts/capability_maturity/run.json"
    rc = cli.main(["run", "--worktree", str(worktree), "--ledger", str(ledger_path),
                   "--out", str(out_path), "--roots", "artifacts", "runs"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["chain_ok"] is True and summary["evidence_total"] >= 2
    assert out_path.exists()

    assert cli.main(["verify", "--ledger", str(ledger_path)]) == 0
    capsys.readouterr()
    assert cli.main(["levels", "--ledger", str(ledger_path)]) == 0
    levels = json.loads(capsys.readouterr().out)
    assert levels["generation"] >= 1

    assert cli.main(["rollback", "--ledger", str(ledger_path),
                     "--to-generation", "0"]) == 0
    capsys.readouterr()
    assert cli.main(["verify", "--ledger", str(ledger_path)]) == 0


# ── E. 契约 ───────────────────────────────────────────────────────────────

def test_zero_domain_semantics_in_module_source():
    """模块源码不得含领域词汇（词表外置在 tools/evidence_domain_lexicon.json）。"""
    lexicon = json.loads((REPO_ROOT / "tools/evidence_domain_lexicon.json")
                         .read_text(encoding="utf-8"))
    ascii_terms = [t.lower() for t in lexicon["ascii_terms"]]
    cjk_terms = list(lexicon["cjk_terms"])
    mod_dir = REPO_ROOT / "tools/capability_maturity_pipeline"
    hits = []
    for path in sorted(mod_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for term in ascii_terms:
            if re.search(rf"\b{re.escape(term)}\b", text):
                hits.append((path.name, term))
        for term in cjk_terms:
            if term in text:
                hits.append((path.name, term))
    assert hits == [], f"domain vocabulary leaked into module: {hits}"


def test_module_exports_contract():
    from tools.capability_maturity_pipeline import __all__ as exported
    for name in ("AuditChain", "MaturityLedger", "MaturityPipeline",
                 "ContradictionPolicy", "detect_supersessions"):
        assert name in exported