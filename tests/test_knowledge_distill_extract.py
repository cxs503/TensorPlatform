"""knowledge_distill 报告解析测试（合成域夹具，零域词）。

被测：``tools/knowledge_distill/extract.py``
（结构化字段通道 / markdown 小节通道 / 占位符与表格残片过滤 / 覆盖率口径 / 全局去重）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.knowledge_distill.extract import (  # noqa: E402
    dedupe_items,
    redact,
    extract_report,
    parse_reports,
    report_sha256,
    text_digest,
)
from tools.knowledge_distill.params import DistillParams  # noqa: E402


def _write(tmp_path: Path, name: str, doc) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 结构化字段通道
# --------------------------------------------------------------------------- #
def test_structured_fields_and_aliases(tmp_path: Path) -> None:
    doc = {
        "node_key": "n01",
        "mission_id": 9001,
        "limitations": ["求解器缺预条件子，迭代次数偏高"],
        "known_limitations": {"text": "日志采样率不可按路由单独配置，只能全局设定"},
        "next_steps": [{"detail": "补齐检查点续跑入口，节点重启后不必整段重跑"}],
        "honest_fail": "断点续跑未实现，长作业重启后从零开始",
    }
    rec = extract_report(_write(tmp_path, "r.json", doc))
    assert rec["status"] == "ok", rec
    assert rec["node_key"] == "n01"
    assert rec["mission_id"] == 9001
    kinds = {i["kind"] for i in rec["items"]}
    assert kinds == {"limitation", "next_step", "honest_fail"}
    assert all(i["pointer"].startswith("$.") for i in rec["items"])
    assert rec["fields_present"] == rec["fields_extracted"]


def test_nested_and_wrapped_document(tmp_path: Path) -> None:
    doc = {"report": {"node_key": "n02", "review": {
        "limitation": [{"text": "表格分页控件在窄屏下错位，遮挡操作按钮"}]}}}
    rec = extract_report(_write(tmp_path, "r.json", doc))
    assert rec["wrapped_in"] == "report"
    assert rec["node_key"] == "n02"
    assert [i["text"] for i in rec["items"]] == ["表格分页控件在窄屏下错位，遮挡操作按钮"]
    assert rec["items"][0]["pointer"] == "$.review.limitation[0].text"


def test_placeholder_and_short_fragments_are_dropped(tmp_path: Path) -> None:
    doc = {"node_key": "n03", "limitations": ["-", "无", "n/a", "短"],
           "next_steps": ["补齐热加载入口，改阈值不再需要重启"]}
    rec = extract_report(_write(tmp_path, "r.json", doc))
    assert [i["text"] for i in rec["items"]] == ["补齐热加载入口，改阈值不再需要重启"]
    assert rec["fields_extracted"] == 1
    assert rec["fields_present"] == 2  # 占位符字段计入 present，不计入 extracted
    assert rec["warnings"]  # 软告警，而不是硬错误


# --------------------------------------------------------------------------- #
# markdown 小节通道
# --------------------------------------------------------------------------- #
def test_markdown_sections_and_fullwidth_numbering(tmp_path: Path) -> None:
    md = "\n".join([
        "## 1. 案例介绍", "正文。",
        "## 5. 注意点（踩过的坑/局限/适用范围）",
        "- 稀疏求解器只实现对角预条件，大问题收敛慢",
        "（2）报告模板缺审计字段，输入工件哈希无处记录",
        "## 6. 下一步",
        "* 队列补 checkpoint 续跑，节点重启后不必整段重跑",
        "## 7. 误差判定", "误差 < 1e-15。",
    ])
    rec = extract_report(_write(tmp_path, "r.json", {"node_key": "n04", "markdown": md}))
    texts = [i["text"] for i in rec["items"]]
    assert texts == [
        "稀疏求解器只实现对角预条件，大问题收敛慢",
        "报告模板缺审计字段，输入工件哈希无处记录",
        "队列补 checkpoint 续跑，节点重启后不必整段重跑",
    ]
    assert [i["kind"] for i in rec["items"]] == ["limitation", "limitation", "next_step"]


def test_markdown_table_and_colon_fragments_rejected(tmp_path: Path) -> None:
    md = "\n".join([
        "## 5. 注意点",
        "| repo | flag |",
        "| TensorX | true |",
        "**验证案例**：",
        "- 真正的一条局限：缓存目录在多进程下互相覆盖",
    ])
    rec = extract_report(_write(tmp_path, "r.json", {"markdown": md}))
    assert [i["text"] for i in rec["items"]] == ["真正的一条局限：缓存目录在多进程下互相覆盖"]


def test_same_text_in_two_channels_kept_once(tmp_path: Path) -> None:
    text = "批量导入遇到重复键时整批回滚，缺逐条重试"
    doc = {"limitations": [text], "markdown": f"## 5 注意点\n- {text}\n"}
    rec = extract_report(_write(tmp_path, "r.json", doc))
    assert len(rec["items"]) == 1
    assert rec["items"][0]["pointer"] == "$.limitations[0]"


# --------------------------------------------------------------------------- #
# 降级与覆盖率
# --------------------------------------------------------------------------- #
def test_unparsable_file_degrades_without_raising(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    good = _write(tmp_path, "good.json", {"limitations": ["条目内容足够长的一条局限描述"]})
    out = parse_reports(sorted([str(bad), str(good)]))
    cov = out["coverage"]
    assert cov["reports_total"] == 2
    assert cov["reports_unparsable"] == 1
    assert cov["parse_coverage"] == pytest.approx(0.5)
    assert cov["field_coverage"] == 1.0
    assert cov["errors"][0]["path"].endswith("bad.json")


def test_coverage_uses_hard_errors_only(tmp_path: Path) -> None:
    p1 = _write(tmp_path, "a.json", {"limitations": ["一条足够长的局限说明文本"]})
    p2 = _write(tmp_path, "b.json", {"limitations": ["-"], "next_steps": ["补齐灰度发布批次控制"]})
    cov = parse_reports([p1, p2])["coverage"]
    assert cov["parse_coverage"] == 1.0
    assert cov["reports_degraded"] == 1
    assert cov["field_coverage"] == pytest.approx(1 / 3, rel=1e-6)


def test_missing_file_is_recorded(tmp_path: Path) -> None:
    rec = extract_report(tmp_path / "nope.json")
    assert rec["status"] == "unparsable" and rec["errors"]


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
def test_redact_removes_credentials() -> None:
    text, hits = redact("数据库连接: 挖掘 PGPASSWORD（abcDEF123456ghi），password=hunter2x token: zzzz1111")
    assert hits >= 3
    assert "abcDEF123456ghi" not in text and "hunter2x" not in text and "zzzz1111" not in text
    assert text.count("<REDACTED>") >= 3


def test_redact_keeps_ordinary_text() -> None:
    text = "报告模板缺审计字段，工件 sha256 与参数指纹都没有落点"
    assert redact(text) == (text, 0)


def test_items_are_redacted_before_output(tmp_path: Path) -> None:
    doc = {"limitations": ["数据库连接需要明文口令 PGPASSWORD（abcDEF123456ghi），无环境变量"],
           "next_steps": ["把口令移入密钥管理并轮换"]}
    rec = extract_report(_write(tmp_path, "r.json", doc))
    joined = " ".join(i["text"] for i in rec["items"])
    assert "abcDEF123456ghi" not in joined
    assert any(i.get("redacted") for i in rec["items"])


# --------------------------------------------------------------------------- #
# 去重与溯源哈希
# --------------------------------------------------------------------------- #
def test_dedupe_keeps_all_sources(tmp_path: Path) -> None:
    text = "稀疏求解器缺预条件子，大问题迭代次数偏高"
    p1 = _write(tmp_path, "a.json", {"node_key": "n1", "limitations": [text]})
    p2 = _write(tmp_path, "b.json", {"node_key": "n2", "limitations": [text]})
    parsed = parse_reports([p1, p2])
    assert parsed["coverage"]["items"] == 2
    out = dedupe_items(parsed["items"])
    assert out["stats"] == {"items_total": 2, "items_distinct": 1,
                            "duplicate_items": 1, "duplicate_groups": 1}
    kept = out["items"][0]
    assert kept["occurrences"] == 2
    assert sorted(kept["source_paths"]) == sorted([str(p1), str(p2)])
    assert kept["text_sha256"] == text_digest(text)


def test_report_sha256_matches_file_bytes(tmp_path: Path) -> None:
    p = _write(tmp_path, "a.json", {"limitations": ["一条足够长的局限说明文本"]})
    assert report_sha256(p) == extract_report(p)["sha256"]
    assert len(report_sha256(p)) == 64


# --------------------------------------------------------------------------- #
# 参数与确定性
# --------------------------------------------------------------------------- #
def test_min_item_chars_threshold_enforced(tmp_path: Path) -> None:
    doc = {"limitations": ["十二个字符刚刚好的一条局限", "十个字符的局限说明"]}
    strict = extract_report(_write(tmp_path, "a.json", doc),
                            DistillParams(min_item_chars=13))
    assert len(strict["items"]) <= 1


def test_extraction_is_deterministic(tmp_path: Path) -> None:
    doc = {"node_key": "n05", "limitations": ["一条足够长的局限说明文本", "另一条同样足够长的局限"],
           "next_steps": ["补齐检查点续跑入口"]}
    path = _write(tmp_path, "a.json", doc)
    first = extract_report(path)
    second = extract_report(path)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_synthetic_fixture_reports_parse() -> None:
    base = ROOT / "tests" / "fixtures" / "knowledge_distill" / "reports"
    out = parse_reports(sorted(str(p) for p in base.glob("*.json")))
    cov = out["coverage"]
    assert cov["reports_total"] == 10
    assert cov["parse_coverage"] >= 0.9
    assert cov["items"] >= 18
    assert cov["items_by_kind"]["limitation"] > 0
    assert cov["items_by_kind"]["next_step"] > 0