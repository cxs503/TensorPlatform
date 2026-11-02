#!/usr/bin/env python3
"""验证欠账台账（tools/verification_debt/ledger.py）的判据测试。

两类用例：
* **合成夹具**（`tmp_path` 假仓）—— 逐个钉住判据分支，特别是 v1 标定中真实踩到的
  五个假阳/假阴陷阱：单位靠量级猜、绝对量混进百分口径、HTTP 200 当成 PASS、
  判据定义行当成结论、欠账目录相对路径当成悬空。
* **真仓回归** —— 对 `docs/verification_debt/` 现有 8 笔欠账的整体行为与两处已知缺陷。

真仓断言里带 `KNOWN_*` 常量：**欠账还清后请同步删掉对应条目**（测试失败即提醒更新台账），
不允许把断言改成「恒真」来消红。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.verification_debt import ledger as L  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 真仓当前已知缺陷（债还清即删）
KNOWN_DANGLING = {
    "chain_gate": {"artifacts/chain_gate/ledger.json", "artifacts/chain_gate/fig1_closure.png"},
    "seakeeping_following_sea": {"tests/feat/test_seakeeping_following_sea.py"},
    "propeller_endpoint": {
        "artifacts/mock_fingerprint/propeller_endpoint_mock_gate.json",
        "artifacts/mock_fingerprint/propeller_endpoint_mock_gate.png",
    },
}
KNOWN_STALE_ARTIFACT_PCT = 195.93   # stability_pontoon 修复前 GM_rel_err（m1842 报告自述 195.9%）


# ---------------------------------------------------------------- 夹具
def make_item(tmp_path, name, docs=None, scripts=None, artifacts=None, extra=None):
    """在假仓里造一笔欠账：docs/scripts 值为 {相对文件名: 内容}。"""
    root = tmp_path
    d = root / "docs" / "verification_debt" / name
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in (docs or {}).items():
        (d / fname).write_text(content, encoding="utf-8")
    for fname, content in (scripts or {}).items():
        (d / fname).write_text(content, encoding="utf-8")
    for fname, content in (artifacts or {}).items():
        p = d / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(content), encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(d)


def rank_of(tmp_path, name):
    led = L.build_ledger(str(tmp_path))
    item = next(i for i in led["items"] if i["name"] == name)
    return item


# ---------------------------------------------------------------- S2 单位口径
def test_fraction_named_err_is_multiplied_by_100():
    """rel_err=1.846 是 184.6%（分数），**不是** 1.85%——v1 用 |v|<=1 猜单位在此翻车。"""
    checks = L.extract_checks({"sweep": [{"GM_rel_err": 1.846}]}, "a.json")
    assert len(checks) == 1
    assert checks[0]["err_pct"] == pytest.approx(184.6)
    assert checks[0]["unit"] == "fraction"
    assert checks[0]["band"] == "RED"


def test_percent_named_err_is_taken_as_is():
    checks = L.extract_checks({"live_checks": {"P4": {"err_pct": 21.509}}}, "a.json")
    assert checks[0]["err_pct"] == pytest.approx(21.509)
    assert checks[0]["unit"] == "percent"
    assert checks[0]["band"] == "RED"


def test_band_thresholds_are_3_and_15_percent():
    for err, band in ((0.02, "PASS_STRONG"), (0.03, "PASS_STRONG"),
                      (0.031, "PASS_WEAK"), (0.15, "PASS_WEAK"), (0.151, "RED")):
        assert L.extract_checks({"x": {"rel_err": err}}, "a.json")[0]["band"] == band


def test_absolute_and_threshold_keys_are_excluded():
    """绝对量（abs_err/_m）与容差（err_threshold）不进百分口径——v1 曾读出 1e32%。"""
    payload = {
        "a": {"KN_identity_abs_err": 1.0, "max_abs_err_m": 1e32, "err_threshold": 0.03},
        "b": {"GM_abs_err_m": 0.25},
    }
    assert L.extract_checks(payload, "a.json") == []


# ---------------------------------------------------------------- 自报结论
def test_http_status_200_is_not_a_pass_claim():
    """`status: 200` 是 HTTP 码，不是 PASS——v1 据此造出 7 条假矛盾。"""
    checks = L.extract_checks({"sweep": [{"status": 200, "GM_rel_err": 0.27}]}, "a.json")
    assert checks[0]["self_verdict"] is None
    assert checks[0]["contradiction"] is False


def test_self_declared_pass_with_red_error_is_a_contradiction():
    checks = L.extract_checks({"gate": {"pass": True, "rel_err": 0.20}}, "a.json")
    assert checks[0]["self_verdict"] == "pass"
    assert checks[0]["contradiction"] is True


def test_self_declared_fail_is_not_a_contradiction():
    checks = L.extract_checks({"gate": {"verdict": "fail", "rel_err": 0.20}}, "a.json")
    assert checks[0]["contradiction"] is False


# ---------------------------------------------------------------- S1 引用落地
def test_item_relative_artifact_ref_resolves_and_missing_test_is_dangling(tmp_path):
    make_item(
        tmp_path, "k1",
        docs={"README.md": "# 清偿\n数据：`artifacts/gate.json`，测试：`tests/feat/ghost.py`\n"},
        artifacts={"artifacts/gate.json": {"c": {"rel_err": 0.001}}},
    )
    item = rank_of(tmp_path, "k1")
    assert "artifacts/gate.json" not in item["dangling_refs"]      # 欠账目录相对写法
    assert "tests/feat/ghost.py" in item["dangling_refs"]          # 真悬空
    assert item["rank"] == "UNBACKED"


# ---------------------------------------------------------------- S3 定级
def test_rank_backed(tmp_path):
    make_item(
        tmp_path, "ok",
        docs={"README.md": "# 验证欠账清偿：解析锚\n"},
        artifacts={"artifacts/gate.json": {"c": {"rel_err": 0.0003, "pass": True}}},
        extra={"tests/test_ok.py": ""},
    )
    item = rank_of(tmp_path, "ok")
    assert item["rank"] == "BACKED", item["reasons"]
    assert item["n_strong"] == 1


def test_rank_overclaim_when_closure_declared_with_red_numbers(tmp_path):
    make_item(
        tmp_path, "bad",
        docs={"README.md": "# 验证欠账清偿：某核\n"},
        artifacts={"artifacts/gate.json": {"c": {"rel_err": 0.20}}},
    )
    assert rank_of(tmp_path, "bad")["rank"] == "OVERCLAIM"


def test_rank_overclaim_on_self_contradiction(tmp_path):
    make_item(
        tmp_path, "bad2",
        docs={"README.md": "**结论**：欠账已清偿\n"},
        artifacts={"artifacts/gate.json": {"c": {"pass": True, "rel_err": 0.4}}},
    )
    assert rank_of(tmp_path, "bad2")["rank"] == "OVERCLAIM"


def test_rank_open_with_red_when_declared_withheld(tmp_path):
    make_item(
        tmp_path, "openred",
        docs={"README.md": "**结论 `WITHHELD`（部署缺口）**\n"},
        artifacts={"artifacts/gate.json": {"c": {"err_pct": 21.5}}},
    )
    assert rank_of(tmp_path, "openred")["rank"] == "OPEN_WITH_RED"


def test_rank_no_evidence(tmp_path):
    make_item(tmp_path, "empty", docs={"FINDINGS.md": "结论：待复核。\n"})
    assert rank_of(tmp_path, "empty")["rank"] == "NO_EVIDENCE"


def test_criteria_definition_line_is_not_a_conclusion(tmp_path):
    """「三态判据：PASS / FAIL / WITHHELD」是判据定义，不能读成「未清偿」。"""
    make_item(
        tmp_path, "defn",
        docs={"README.md": "三态判据：PASS / FAIL / WITHHELD。\n\n# 验证欠账清偿：期末\n"},
        artifacts={"artifacts/gate.json": {"c": {"rel_err": 0.001}}},
    )
    item = rank_of(tmp_path, "defn")
    assert item["open_claims"] == []
    assert item["rank"] == "BACKED"


def test_strong_withheld_declaration_is_detected(tmp_path):
    make_item(tmp_path, "wh", docs={"README.md": "**结论 `WITHHELD`（部署缺口）**\n"},
              artifacts={"artifacts/g.json": {"c": {"rel_err": 0.001}}})
    item = rank_of(tmp_path, "wh")
    assert "WITHHELD" in item["open_claims"]
    assert item["rank"] == "OPEN"


# ---------------------------------------------------------------- 渲染 / CLI
def test_markdown_and_cli_strict(tmp_path, capsys):
    make_item(tmp_path, "dangling",
              docs={"README.md": "# 清偿\n`tests/nope.py`\n"},
              artifacts={"a.json": {"c": {"rel_err": 0.001}}})
    led = L.build_ledger(str(tmp_path))
    md = L.render_markdown(led)
    assert "验证欠账台账" in md and "dangling" in md
    assert L.main(["--root", str(tmp_path), "--no-figure"]) == 0
    assert L.main(["--root", str(tmp_path), "--no-figure", "--strict"]) == 1
    out = os.path.join(str(tmp_path), "artifacts/verification_debt/ledger.json")
    assert json.load(open(out))["schema"] == L.LEDGER_SCHEMA


# ---------------------------------------------------------------- 真仓回归
@pytest.fixture(scope="module")
def real_ledger():
    return L.build_ledger(REPO_ROOT)


def test_real_repo_ledger_schema_and_coverage(real_ledger):
    assert real_ledger["schema"] == L.LEDGER_SCHEMA
    assert real_ledger["thresholds"] == {"strong_pct": 3.0, "red_pct": 15.0}
    names = {i["name"] for i in real_ledger["items"]}
    assert len(names) >= 8, names            # 欠账只增不减：新欠账进来应放宽这条
    assert real_ledger["summary"]["n_checks"] > 50


def test_real_repo_known_dangling_refs_are_detected(real_ledger):
    items = {i["name"]: i for i in real_ledger["items"]}
    for name, expected in KNOWN_DANGLING.items():
        assert name in items, f"欠账目录 {name} 不见了？"
        missing = expected - set(items[name]["dangling_refs"])
        assert not missing, f"{name} 的悬空引用未被检出：{missing}（若已补齐请更新 KNOWN_DANGLING）"


def test_real_repo_stability_artifact_is_pre_fix_snapshot(real_ledger):
    """stability_pontoon 的 artifact 仍是 KM 重复计入缺陷的**修复前**快照。"""
    item = next(i for i in real_ledger["items"] if i["name"] == "stability_pontoon")
    assert item["worst_err_pct"] == pytest.approx(KNOWN_STALE_ARTIFACT_PCT, abs=0.5)
    assert item["rank"] == "OPEN_WITH_RED"


def test_real_repo_seakeeping_fk_is_backed(real_ledger):
    item = next(i for i in real_ledger["items"] if i["name"] == "seakeeping_fk")
    assert item["rank"] == "BACKED", item["reasons"]
    assert item["worst_err_pct"] <= L.RED_PCT