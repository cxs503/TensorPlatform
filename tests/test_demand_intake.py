#!/usr/bin/env python3
"""``tools/demand_intake`` 的合成夹具测试（FEAT-DEMAND-AUTOINTAKE）。

纪律：**零领域词条** —— 夹具 slug 一律 ``FEAT-SYNTH-*``，标题/验收是合成句子；
域内容只经 DB 注入（本模块压根不碰领域数据）。

覆盖六面：
1. slug 规范化/规范判据（含 ``A1-A2`` 步骤编号必须判死）；
2. parser 三条抽取路径（结构化字段 / 文本内 JSON 块 / 锚语境行兜底）+ 抽取前置条件；
3. 报告「局限/待办」节解析（bullet 与整段两种形态）；
4. 审计门三判据与原因码（合格/坏 slug/空验收/无数字验收/标题重复/撞 slug）；
5. store 层的纯函数与只读硬闸（``query`` 拒绝非 SELECT）；
6. **真 PG 全链路红队**（``cli redteam``：断链红队 + 垃圾锚拦截红队 + 双重复跑幂等），
   在临时 schema 内跑，结束 DROP；本机无 psql/postgres 时自动 skip。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.demand_intake import cli, gate as G, store as S  # noqa: E402
from tools.demand_intake.parser import (  # noqa: E402
    candidates_from_result, parse_report_sections, split_anchor_text,
)
from tools.demand_intake.slugs import (  # noqa: E402
    canonical_slug, is_valid_slug, slug_problem, title_similarity,
)

GOOD = "FEAT-SYNTH-ALPHA"


# ── 1. slug ─────────────────────────────────────────────────────────────────
def test_slug_canonicalisation_and_validity():
    assert canonical_slug(" `feat: synth alpha` ") == "FEAT-SYNTH-ALPHA"
    assert canonical_slug("FEAT--SYNTH__ALPHA") == "FEAT-SYNTH-ALPHA"
    assert is_valid_slug(GOOD)
    assert is_valid_slug("GAP-SYNTH-T2-COREPATH")
    # 步骤编号形状像 slug 却不是锚（红队第 2 条靶子）
    assert not is_valid_slug("A1-A2")
    assert slug_problem("A1-A2") == "slug_no_alpha_word_segment"
    assert not is_valid_slug("SYNTH")                  # 单段=词不是锚
    assert not is_valid_slug("UTF-8")                  # 形状词黑名单
    assert is_valid_slug(GOOD) and slug_problem(GOOD) == ""


def test_title_similarity_is_token_based():
    assert title_similarity("合成锚丙：既有已开锚", "合成锚丙：既有已开锚") == 1.0
    assert title_similarity("合成锚丙", "完全不同的句子") < 0.2


# ── 2. parser ───────────────────────────────────────────────────────────────
def test_parser_structured_field_wins_and_merges():
    result = {
        "headline": "合成任务完成；建议新锚：%s — 合成锚甲：验收判据偏差 <3%" % GOOD,
        "proposed_anchors": [{"slug": GOOD, "title": "合成锚甲",
                              "acceptance": "偏差 <3%", "domain": "platform"}],
    }
    cands = [c for c in candidates_from_result(result, mission_id=1) if c.slug == GOOD]
    assert len(cands) == 1                    # 同 slug 合并成一条（不重复入榜）
    assert cands[0].confidence == "structured"      # 结构化优先于文本兜底
    assert "偏差 <3%" in cands[0].acceptance        # 验收条款跟着结构化字段走


def test_parser_json_in_text_fenced_block():
    result = {"checks": '结论如下：\n```json\n{"proposed_anchors": '
                        '[{"slug": "FEAT-SYNTH-BETA", "title": "合成锚乙", '
                        '"acceptance": "覆盖 5 类用例且误差 <2%"}]}\n```\n'}
    cands = candidates_from_result(result, mission_id=2)
    assert [c.slug for c in cands] == ["FEAT-SYNTH-BETA"]
    assert cands[0].confidence == "json_in_text"


def test_parser_text_fallback_requires_context_and_keeps_quantified_acceptance():
    result = {"checks": "建议新锚：FEAT-SYNTH-GAMMA 合成锚丁，验收判据：偏差 <3%"}
    cands = candidates_from_result(result, mission_id=3)
    assert [c.slug for c in cands] == ["FEAT-SYNTH-GAMMA"]
    assert cands[0].confidence == "text"
    assert "偏差 <3%" in cands[0].acceptance
    # 无「锚」语境的旁白不该抽出任何候选
    assert candidates_from_result({"checks": "跑了 pytest，7 passed"}, mission_id=4) == []


def test_split_anchor_text():
    slug, title, acc = split_anchor_text("FEAT-SYNTH-DELTA — 合成标题：pytest 全绿且覆盖率 >90%")
    assert slug == "FEAT-SYNTH-DELTA" and title == "合成标题"
    assert ">90%" in acc


# ── 3. 报告待办 ─────────────────────────────────────────────────────────────
MD = """# 合成报告
## 二 方法
正文甲
## 五 局限与待办
- 合成局限一：偏差 >3% 未解释
- 合成待办二：补 A/B 对照
## 六 注意事项
这是一整段整体描述，没有 bullet。
## 七 结论
无关正文
"""


def test_report_backlog_sections():
    items = parse_report_sections(MD)
    sections = [i["section"] for i in items]
    assert "五 局限与待办" in sections
    assert sum(1 for i in items if i["section"] == "五 局限与待办") == 2
    assert any("整体描述" in i["item"] for i in items)      # 整段形态也能收
    assert all("无关正文" not in i["item"] for i in items)
    assert parse_report_sections("## 方法\n无待办\n") == []


# ── 4. 审计门 ───────────────────────────────────────────────────────────────
EXISTING = [{"slug": "FEAT-SYNTH-ZETA", "title": "合成锚丙：既有已开锚", "status": "open"}]


@pytest.mark.parametrize("cand,verdict,reason_prefix", [
    ({"slug": GOOD, "title": "合成锚甲", "acceptance": "偏差 <3%"}, "open", ""),
    ({"slug": "A1-A2", "title": "合成步骤编号", "acceptance": "误差 <3%"},
     "dropped", "slug_no_alpha_word_segment"),
    ({"slug": "FEAT-SYNTH-NOACC", "title": "合成无验收锚", "acceptance": ""},
     "dropped", "acceptance_empty"),
    ({"slug": "FEAT-SYNTH-VAGUE", "title": "合成模糊验收锚", "acceptance": "做得更好更稳定"},
     "dropped", "acceptance_no_number"),
    ({"slug": "FEAT-SYNTH-ZETA2", "title": "合成锚丙：既有已开锚", "acceptance": "偏差 <3%"},
     "dropped", "duplicate_title"),
])
def test_gate_verdicts(cand, verdict, reason_prefix):
    d = G.evaluate(cand, EXISTING)
    assert d.verdict == verdict
    if reason_prefix:
        assert any(r.startswith(reason_prefix) for r in d.reasons), d.reasons


def test_gate_duplicate_slug_is_not_rewritten():
    d = G.evaluate({"slug": "FEAT-SYNTH-ZETA", "title": "合成锚丙",
                    "acceptance": "偏差 <3%"}, EXISTING)
    assert d.verdict == "duplicate"
    assert any(r.startswith("duplicate_slug") for r in d.reasons)


def test_gate_reason_code_distribution():
    ds = [G.evaluate({"slug": GOOD, "title": "合成锚甲", "acceptance": "偏差 <3%"}, []),
          G.evaluate({"slug": "FEAT-SYNTH-NOACC", "title": "合成无验收", "acceptance": ""}, [])]
    dist = G.gate_reason_codes(ds)
    assert dist.get("PASS") == 1 and dist.get("acceptance_empty") == 1


# ── 5. store 纯函数与只读硬闸 ───────────────────────────────────────────────
def test_store_literals_and_cand_key():
    assert S.sql_literal("a'b") == "'a''b'"
    assert S.sql_json({"a": "b'c"}).endswith("::jsonb")
    k1 = S.cand_key("mission_finish", "mission:1", GOOD)
    assert k1 == S.cand_key("mission_finish", "mission:1", GOOD) and len(k1) == 40
    assert k1 != S.cand_key("mission_finish", "mission:2", GOOD)
    assert S.q("t", "s") == "s.t" and S.q("t") == "t"


def test_transport_query_rejects_writes_without_touching_db():
    t = S.PsqlTransport(db="synthetic")
    with pytest.raises(S.ReadOnlyViolation):
        t.query("UPDATE synthetic SET x = 1")


def test_ddl_is_idempotent_and_schema_qualified():
    stmts = S.ddl("scratch_x")
    assert all("IF NOT EXISTS" in s for s in stmts)
    assert all("scratch_x." in s for s in stmts)


# ── 6. 真 PG 全链路红队 ─────────────────────────────────────────────────────
def _pg_available() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        p = subprocess.run(["su", "postgres", "-c", "psql -d tensorplatform -tA -c 'select 1'"],
                           capture_output=True, timeout=30)
        return p.returncode == 0 and p.stdout.decode("utf-8", "replace").strip() == "1"
    except Exception:  # noqa: BLE001
        return False


PG = pytest.mark.skipif(not _pg_available(), reason="no local psql/postgres peer access")


@PG
def test_redteam_end_to_end_in_scratch_schema(tmp_path):
    out = tmp_path / "redteam.json"
    rc = cli.main(["redteam", "--scratch", "m17133_pytest_scratch", "--out", str(out)])
    assert rc == 0, out.read_text(encoding="utf-8") if out.exists() else "no output"
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["verdict"] == "PASS"
    # 断链红队：两条合格锚必须已在可领面（open + source=agent-proposed）
    assert rep["checks"]["broken_link_no_anchor_left_behind"]["pass"]
    # 垃圾锚拦截红队：一条都没漏成 open
    assert rep["checks"]["junk_intercepted"]["pass"]
    dropped = rep["checks"]["junk_intercepted"]["dropped"]
    assert dropped["A1-A2"].startswith("slug_no_alpha_word_segment")
    assert dropped["FEAT-SYNTH-NOACC"].startswith("acceptance_empty")
    assert dropped["FEAT-SYNTH-ZETA2"].startswith("duplicate_title")
    # 双重复跑幂等（ingest / backlog）
    assert rep["checks"]["reingest_idempotent"]["second_pass_inserted"] == 0
    assert rep["checks"]["backlog_idempotent"]["second"] == 0
    # 入榜率口径本链 100%（detected 全部落到日志/账本），可领率=2/7（垃圾按设计拦下）
    assert rep["measured"]["intake_rate"] == 1.0


@PG
def test_scratch_schema_dropped_after_redteam():
    t = S.PsqlTransport()
    rows = t.query("SELECT schema_name FROM information_schema.schemata "
                   "WHERE schema_name = 'm17133_pytest_scratch'")
    assert rows == [], "红队必须清理临时 schema，不留生产残留"