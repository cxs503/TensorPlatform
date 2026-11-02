"""验证欠账台账工具的机器判据测试（内联夹具，不依赖数据库/网络）。

被测对象：``tools/validation_debt_ledger.py``
测试原则：每条断言都对应一个可被推翻的命题（分节是否吃对 / 归税是否命中 /
续行是否误切 / 伪闭合是否被抓 / 聚类是否把同义欠账合并 / 输出是否确定）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import validation_debt_ledger as L  # noqa: E402


REPORT_A = """# 复核对标报告 A

## ① 案例介绍
KVLCC2 阻力复核。

## ⑤ 注意点（局限与待办）
- 冰区阻力链路仍是 STUB：`backend/services/ice_resistance.py` 仅返回占位值。
- KVLCC2 湿面积缺失，参考阻力量 WITHHELD，未实测。
* 本表未包含螺旋桨敞水特性，自航点无法闭合。
本段是续行说明：仅在 Fr<0.25 适用。

## ⑥ 误差判定
- 关键量 Ct 误差 2.1%，小于 3%。

## 结论
- 已关闭历史欠账三条。
"""

REPORT_B = """# 复核对标报告 B

## 局限
- 冰区阻力链路仍是 STUB：`backend/services/ice_resistance.py` 仅返回占位值。
- 稳性 GZ 壁式解仅在 tanφ ≤ 2T/B 内有效，外推非物理。

## 后续计划
- 补做 DTMB 5415 的 Fr 列复核。
"""


def _reports():
    return [
        {"id": 1, "title": "A", "markdown": REPORT_A, "mission_id": 11, "scenario_key": "s"},
        {"id": 2, "title": "B", "markdown": REPORT_B, "mission_id": 12, "scenario_key": "s"},
    ]


@pytest.fixture()
def ledger():
    return L.build_ledger(_reports(), repo_root="/nonexistent-root")


# ── 分节 ──────────────────────────────────────────────────────────────────────
def test_section_split_counts_headings():
    secs = L.split_sections(REPORT_A)
    titles = [t for t, _ in secs]
    assert "① 案例介绍" in titles
    assert "⑤ 注意点（局限与待办）" in titles


@pytest.mark.parametrize("title,expected", [
    ("⑤ 注意点（局限与待办）", True),
    ("局限", True),
    ("后续计划", False),
    ("Limitations and caveats", True),
    ("误差判定", False),
    ("结论", False),
    ("Open issues / TODO", True),
])
def test_is_debt_section(title, expected):
    assert L.is_debt_section(L._norm_title(title)) is expected


# ── 条目切分 ──────────────────────────────────────────────────────────────────
def test_extract_items_merges_continuation_and_drops_closed():
    body = L.split_sections(REPORT_A)[2][1]
    items = L.extract_items(body)
    joined = " ".join(items)
    # 续行必须并入上一条（不是独立条目）
    assert any("仅在 Fr<0.25 适用" in it and "自航点无法闭合" in it for it in items)
    # 太短的碎片被丢弃
    assert all(len(it) >= L.MIN_ITEM_CHARS for it in items)
    assert len(items) == 3


def test_extract_items_drops_closure_statements():
    assert L.extract_items("- 已关闭历史欠账三条。") == []
    assert L.extract_items("- closed the debt for good.") == []


# ── 归税 ──────────────────────────────────────────────────────────────────────
def test_capability_tagging_ice_and_resistance():
    caps = L.map_tags("KVLCC2 总阻力链路仍是 STUB，参考量 WITHHELD", L.CAPABILITY_RULES)
    assert "stdmodels.lookup" in caps  # KVLCC2 → 标准船型
    assert "calc.resistance" in caps


def test_ice_resistance_does_not_over_tag_calc_resistance():
    """防过标：冰区阻力属于冰力学模块，不应被记成 calc.resistance 的欠账。"""
    caps = L.map_tags("冰区阻力链路仍是 STUB，仅返回占位值", L.CAPABILITY_RULES)
    assert "calc.resistance" not in caps
    assert "misc.stubs" in caps


def test_domain_tagging_covers_three_disciplines():
    assert "推进" in L.map_tags("螺旋桨敞水特性无法闭合", L.DOMAIN_RULES)
    assert "稳性" in L.map_tags("稳性 GZ 壁式解", L.DOMAIN_RULES)
    assert "冰区航行" in L.map_tags("冰区阻力链路", L.DOMAIN_RULES)


@pytest.mark.parametrize("text,level", [
    ("该链路仍是 STUB，仅返回占位值", "STUB"),
    ("参考值是硬编码的伪数据", "MOCK"),
    ("湿面积缺失，参考量 WITHHELD", "WITHHELD"),
    ("采用 Holtrop 经验公式近似", "ASSUMPTION"),
    ("仅在 Fr<0.25 适用", "LIMIT"),
])
def test_severity_levels(text, level):
    got, _why = L.severity_of(text)
    assert got == level


def test_severity_takes_hardest_when_multiple_match():
    got, _ = L.severity_of("这是 STUB 且为硬编码伪数据，仅在小 Fr 适用")
    assert got == "STUB"


# ── 闭合交叉验证（伪闭合） ────────────────────────────────────────────────────
def test_artifact_refs_extracted_from_backticks_and_plain():
    refs = L.artifact_refs("见 `backend/services/ice_resistance.py` 与 docs/x.md")
    assert "backend/services/ice_resistance.py" in refs
    assert "docs/x.md" in refs


def test_missing_artifact_is_flagged_not_silently_closed(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "present.md").write_text("ok")
    rep = [{"id": 9, "markdown": "## 局限\n- 证据在 docs/present.md 与 docs/absent.md 两处，均需复核。\n"}]
    led = L.build_ledger(rep, repo_root=str(tmp_path))
    refs = led["items"][0]["artifact_exists"]
    assert refs["docs/present.md"]["exists"] is True
    assert refs["docs/absent.md"]["exists"] is False
    assert led["stats"]["artifact_refs_missing"] == ["docs/absent.md"]


def test_bare_filename_is_not_counted_as_false_closure(tmp_path):
    """裸文件名无法定位到任何仓 → 记「证据不足」，不得计入伪闭合罪证。"""
    rep = [{"id": 10, "markdown": "## 局限\n- 产物 acceptance.json 未入库，需补。\n"}]
    led = L.build_ledger(rep, repo_root=str(tmp_path))
    st = led["stats"]
    assert st["artifact_refs_path_like"] == 0
    assert st["artifact_refs_unlocatable_bare_name"] == 1
    assert st["artifact_refs_missing"] == []


def test_search_roots_resolve_cross_repo_artifacts(tmp_path):
    """多仓语料：产物在别的工作树里时不应被记成缺失。"""
    repo, other = tmp_path / "repo", tmp_path / "other"
    (other / "artifacts").mkdir(parents=True)
    (other / "artifacts" / "x.json").write_text("{}")
    repo.mkdir()
    rep = [{"id": 11, "markdown": "## 局限\n- 证据 artifacts/x.json 已产出但待复核。\n"}]
    led1 = L.build_ledger(rep, repo_root=str(repo))
    assert led1["stats"]["artifact_refs_missing"] == ["artifacts/x.json"]
    led2 = L.build_ledger(rep, repo_root=str(repo), search_roots=[str(other)])
    assert led2["stats"]["artifact_refs_missing"] == []


def test_meta_statements_land_in_platform_gov_bucket():
    """治理/元陈述必须落进「平台治理」，不得混进物理学科。"""
    doms = L.map_tags("本质检为 cross-family 报告质检，判定可终局", L.DOMAIN_RULES)
    assert doms == ["平台治理"]


# ── 聚类 ──────────────────────────────────────────────────────────────────────
def test_recurrent_debt_clusters_across_reports(ledger):
    clusters = ledger["clusters"]
    top = clusters[0]
    # A 与 B 都写了同一条冰区 STUB 欠账 → 该簇复发 2 篇
    assert top["n_reports"] == 2
    assert set(top["report_ids"]) == {1, 2}
    assert "STUB" in top["representative"] or top["worst_severity"] == "STUB"
    assert ledger["stats"]["recurrent_clusters_ge2"] >= 1


def test_cluster_ids_are_ranked_contiguous(ledger):
    ids = [c["cluster_id"] for c in ledger["clusters"]]
    assert ids == list(range(1, len(ids) + 1))
    counts = [c["n_reports"] for c in ledger["clusters"]]
    assert counts == sorted(counts, reverse=True)


def test_jaccard_bounds():
    a, b = L.tokens("冰区阻力链路 STUB 占位"), L.tokens("冰区阻力链路 STUB 占位")
    assert L.jaccard(a, b) == 1.0
    assert L.jaccard(a, set()) == 0.0


# ── 统计与确定性 ──────────────────────────────────────────────────────────────
def test_stats_accounting_is_internally_consistent(ledger):
    s = ledger["stats"]
    assert s["reports_scanned"] == 2 and s["reports_with_debt_section"] == 2
    assert sum(s["by_severity"].values()) == s["items_total"]
    assert sum(s["by_capability"].values()) >= sum(1 for it in ledger["items"] if it["capabilities"])
    assert s["items_total"] == len(ledger["items"])


def test_outputs_are_deterministic_modulo_timestamp():
    a = L.build_ledger(_reports(), repo_root="/nonexistent-root")
    b = L.build_ledger(_reports(), repo_root="/nonexistent-root")
    a.pop("generated_at"), b.pop("generated_at")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_write_outputs_roundtrip(tmp_path):
    led = L.build_ledger(_reports(), repo_root="/nonexistent-root")
    outs = L.write_outputs(led, str(tmp_path), top=5)
    back = json.load(open(outs["ledger_json"], encoding="utf-8"))
    assert back["stats"]["items_total"] == led["stats"]["items_total"]
    md = open(outs["ledger_md"], encoding="utf-8").read()
    assert "验证欠账台账" in md and "复发欠账簇" in md


def test_empty_corpus_does_not_crash(tmp_path):
    led = L.build_ledger([], repo_root=".")
    assert led["stats"]["items_total"] == 0 and led["clusters"] == []
    outs = L.write_outputs(led, str(tmp_path))
    assert os.path.exists(outs["ledger_md"])