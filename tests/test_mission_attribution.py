"""Tests for tools/validation/mission_attribution.py (terminal-mission evidence attribution).

Two layers, deliberately separated:
  * reference layer  — the classifier/aggregator contract on synthetic fixtures (always valid);
  * pinned layer      — the numbers already published for this worktree must stay reproducible
                        from the committed snapshot, so a silent change to the rules fails CI.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import mission_attribution as ma  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1814")


def mission(status="failed", result=None, repo="TensorLBM", model=None, goal="普通任务"):
    return {"id": 1, "status": status, "repo": repo, "model": model,
            "goal": goal, "result": result if result is not None else {}}


def res(gate=None, reason=None, stage=None, stop=None, accepted=None, note=None,
        revoked=None, has_changes=True, model="m1"):
    out = {"model": model}
    if has_changes:
        out["on_disk_changes"] = ["docs/x.md"]
    else:
        out["has_changes"] = False
    if gate or reason or stage:
        out["evidence"] = {"checks": {k: v for k, v in
                                      (("gate", gate), ("reason", reason), ("fail_stage", stage))
                                      if v is not None}}
    if stop:
        out["stop_condition"] = stop
    if stage:
        out["fail_stage"] = stage
    if accepted is not None:
        out["accepted"] = accepted
    if note:
        out["decision_note"] = note
    if revoked:
        out["revoked"] = True
        out["revoked_reason"] = revoked
    return out


VIEW = lambda m: ma.classify(m)


def test_admin_dedupe_wins_over_everything():
    m = mission("revoked", res(gate="no-evidence", stop="agent_lost",
                               revoked="dup-dispatch-audit-20261031 (owner 批量停重复)"))
    c = VIEW(m)
    assert c["primary"] == "admin_dedupe"
    assert {"no_evidence", "agent_lost", "admin_dedupe"} <= set(c["flags"])


def test_worker_crash_is_infra():
    c = VIEW(mission("failed", res(gate="worker-crash", reason="仓库不存在或不是 git")))
    assert c["primary"] == "infra_crash" and "worker_crash" in c["flags"]


@pytest.mark.parametrize("stage", ["llm_error", "wall_exhausted", "budget_exhausted",
                                  "budget_exhausted+forced_finish", "no_progress"])
def test_runtime_stages_are_infra(stage):
    assert VIEW(mission("failed", res(stage=stage)))["primary"] == "infra_runtime"


def test_agent_lost_is_infra():
    assert VIEW(mission("failed", res(stop="agent_lost")))["primary"] == "infra_runtime"


def test_evidence_absent():
    assert VIEW(mission("failed", res(gate="no-evidence", reason="工作树无任何改动"),
                        ))["primary"] == "evidence_absent"


def test_claim_falsified_only_for_non_dedupe_revoke():
    c = VIEW(mission("revoked", res(revoked="『论文级补报』实为 17 篇空壳 stub")))
    assert c["primary"] == "claim_falsified"
    assert "claim_falsified" in c["flags"]


def test_acceptance_rejected_requires_decision():
    assert VIEW(mission("failed", res(accepted=False)))["primary"] == "acceptance_rejected"
    assert VIEW(mission("failed", res()))["primary"] == "failed_other"


def test_succeeded_needs_evidence_and_changes():
    assert VIEW(mission("succeeded", res()))["primary"] == "succeeded_verified"
    assert VIEW(mission("succeeded", res(has_changes=False)))["primary"] == "succeeded_unverified"
    assert VIEW(mission("succeeded", res(gate="no-evidence")))["primary"] == "succeeded_unverified"


def test_succeeded_forced_finish_still_counts_as_delivery():
    """A budget/LLM hiccup that still produced a verified artefact is a delivery, flagged."""
    c = VIEW(mission("succeeded", res(stage="budget_exhausted+forced_finish")))
    assert c["primary"] == "succeeded_verified" and "forced_finish" in c["flags"]


def test_non_terminal_refused():
    with pytest.raises(ValueError):
        ma.classify({"id": 9, "status": "running", "result": {}})


def test_free_text_self_report_is_never_used():
    """`result.checks`/`headline` are agent claims; they must not move the class."""
    claim = res(has_changes=False)
    claim["checks"] = "✅ 全部 PASS 已验收 已合并"
    claim["headline"] = "完美完成"
    assert VIEW(mission("succeeded", claim))["primary"] == "succeeded_unverified"


def test_case_key_from_goal():
    g = "SDAA 验证案例扫荡——案例 kelvin_helmholtz（TensorLBM benchmarks/verified）。"
    assert ma.case_key({"goal": g}) == "kelvin_helmholtz"
    assert ma.case_key({"goal": "普通任务"}) is None


def test_summary_rates_and_invariants():
    rows = [
        mission("succeeded", res()),
        mission("succeeded", res(has_changes=False)),
        mission("failed", res(gate="no-evidence")),
        mission("failed", res(accepted=False)),
        mission("revoked", res(revoked="dup-dispatch")),
        mission("failed", res(stop="agent_lost")),
    ]
    st = ma.summarize([ma.classify(r) for r in rows])
    assert st["total_terminal"] == 6 and st["unclassified"] == 0
    r = st["rates"]
    assert r["raw_status_success_rate"] == pytest.approx(2 / 6)
    assert r["raw_success_rate_excl_admin"] == pytest.approx(2 / 5)
    assert r["verified_delivery_rate"] == pytest.approx(1 / 6)
    assert r["technical_pass_rate"] == pytest.approx(1 / 2)
    assert r["infra_loss_share"] == pytest.approx(2 / 6)


def test_class_partition_is_exhaustive_and_disjoint():
    """Every (status, marker) combination maps to exactly one class that is a known class."""
    combos = []
    for status in ma.TERMINAL:
        for gate in (None, "no-evidence", "worker-crash", "passed"):
            for stop in (None, "agent_lost"):
                for acc in (None, True, False):
                    for rev in (None, "dup-dispatch", "claims disproved"):
                        combos.append(mission(status, res(gate=gate, stop=stop, accepted=acc,
                                                          revoked=rev, has_changes=bool(gate is None))))
    classes = [ma.classify(m) for m in combos]
    assert all(c["primary"] in ma.CLASSES for c in classes)
    st = ma.summarize(classes)
    assert st["unclassified"] == 0
    assert sum(st["by_class"].values()) == len(combos)


def test_snapshot_roundtrip_preserves_every_class():
    rows = [
        mission("succeeded", res(), goal="案例 dam_break_sc（TensorLBM）"),
        mission("succeeded", res(has_changes=False)),
        mission("failed", res(gate="no-evidence")),
        mission("failed", res(gate="worker-crash")),
        mission("failed", res(stop="agent_lost")),
        mission("revoked", res(revoked="dup-dispatch-audit")),
        mission("revoked", res(revoked="声明不实")),
        mission("failed", res(accepted=False, note="无交付物")),
        mission("failed", res()),
    ]
    before = [ma.classify(r)["primary"] for r in rows]
    snap = ma.snapshot(rows)
    after = [ma.classify({**s, "goal": s.get("case_key") and f"案例 {s['case_key']}（x）"})
             for s in snap]
    assert before == [c["primary"] for c in after]
    # snapshot must be free of agent prose and absolute worktree paths
    blob = json.dumps(snap, ensure_ascii=False)
    assert "/srv/tensor/agent/work" not in blob and "headline" not in blob


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "missions_snapshot.jsonl")),
                    reason="published artefacts absent")
def test_published_numbers_reproduce_from_committed_snapshot():
    """Pinned layer: rerunning on the committed snapshot must give the committed stats."""
    committed = json.load(open(os.path.join(ART, "mission_attribution.json"), encoding="utf-8"))
    rows = ma.load_jsonl(os.path.join(ART, "missions_snapshot.jsonl"))
    st = ma.summarize([ma.classify(r) for r in rows],
                      cases=[c for c in committed.get("cases", {})])
    assert st["unclassified"] == 0
    for key in ("total_terminal", "by_class", "rates", "flag_counts", "by_repo", "by_model"):
        assert st[key] == committed[key], f"{key} drifted from the published figure"


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "mission_attribution.md")),
                    reason="published artefacts absent")
def test_published_markdown_matches_rules():
    committed = json.load(open(os.path.join(ART, "mission_attribution.json"), encoding="utf-8"))
    md = open(os.path.join(ART, "mission_attribution.md"), encoding="utf-8").read()
    for cls in ma.CLASSES:
        assert f"`{cls}`" in md
    assert f"N = **{committed['total_terminal']}**" in md
    # every published rate string must be present verbatim (no hand-edited numbers)
    for key, val in committed["rates"].items():
        assert f"{100 * val:.2f}%" in md, f"{key} not rendered"
