"""Integrity gates for the OSS-LBM discovery note (FEAT-OSS-INVENTORY-LBM, m8307).

These tests guard the *deliverable* rather than the GitHub API: they can run
offline because every input is a persisted artifact under artifacts/m8307/.
They assert what the note claims, so a silent drift (candidate added without
evidence, tier typo, repo leaking in from the existing inventory, machine block
count mismatch) fails here instead of in the report.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "m8307"
DOC = ROOT / "docs" / "oss_inventory_discovery_multiphase.md"

VALID_TIERS = {1, 2, 3}


def _data():
    return json.loads((ART / "shortlist.json").read_text())


def test_artifact_and_doc_exist():
    assert (ART / "shortlist.json").exists()
    assert (ART / "fig_discovery_overview.png").exists()
    assert (ART / "fig_discovery_overview.png").stat().st_size > 10_000
    assert DOC.exists()


def test_candidate_count_within_budget():
    d = _data()
    assert 1 <= len(d["candidates"]) <= 8, "发现单每单最多提名 8 个候选"
    assert len(d["excluded"]) >= 1, "必须给出排除项及理由"


def test_every_candidate_has_existence_evidence():
    for c in _data()["candidates"]:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", c["repo"]), c["repo"]
        # existence evidence = a live API payload field joined at render time
        assert isinstance(c["stars"], int) and c["stars"] >= 0, c["repo"]
        assert c["description"], f"{c['repo']} 缺 API description（存在性证据）"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["pushed_at"] or ""), c["repo"]
        assert c["evidence_source"], c["repo"]
        assert c["status"] == "suggested", "发现单只发现不深盘 -> 一律 suggested"


def test_tiers_and_focus_are_wellformed():
    for c in _data()["candidates"]:
        assert c["tier"] in VALID_TIERS, (c["repo"], c["tier"])
        assert 10 <= len(c["focus"]) <= 200, (c["repo"], len(c["focus"]))
        assert c["gap"], f"{c['repo']} 未说明填补的空缺"


def test_no_candidate_already_in_inventory():
    d = _data()
    inv = {r.lower() for r in d["inventory_snapshot"]}
    assert len(inv) == 24, "oss_inventory 快照应为 24 仓"
    dup = [c["repo"] for c in d["candidates"] if c["repo"].lower() in inv]
    assert not dup, f"候选与在盘仓重复: {dup}"


def test_excluded_items_carry_reasons():
    for x in _data()["excluded"] + _data()["watch"]:
        assert x["reason"] and len(x["reason"]) >= 8, x
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", x["repo"]), x["repo"]


def test_machine_blocks_match_candidates():
    md = DOC.read_text()
    blocks = re.findall(r"\[OSS-INV\]\s*repo: (\S+)\s*tier: (\d)\s*focus: (.+?)\s*status: (\w+)\s*\[/OSS-INV]",
                        md)
    cands = _data()["candidates"]
    assert len(blocks) == len(cands)
    assert {b[0] for b in blocks} == {c["repo"] for c in cands}
    for repo, tier, focus, status in blocks:
        assert int(tier) in VALID_TIERS
        assert status == "suggested"
        assert focus.strip()


def test_report_sections_present():
    md = DOC.read_text()
    for marker in ("① 案例介绍", "② 怎么做的", "③ 结果", "④ 明确排除项",
                   "⑤ 注意点", "⑥ 误差判定", "⑦ 机器块", "⑧ 溯源"):
        assert marker in md, f"报告缺六要素段落: {marker}"
    # honest-limits statement must be present (no code-level audit performed)
    assert "未 clone" in md or "未clone" in md


def test_recall_selfcheck_is_reported():
    s = _data()["stats"]
    assert s["recall_inventory_total"] == 24
    assert 0 < s["recall_inventory_hit_by_queries"] <= 24
    assert abs(s["recall_rate"] - s["recall_inventory_hit_by_queries"] / 24) < 1e-3  # 快照保留 3 位小数
    # honest reporting: recall below 100% has to be visible in the doc
    assert "召回自检" in DOC.read_text()