"""船舶总体性能 API 面证据普查闸门（m1708）。

验证目标（机器可裁，全部离线、无外部服务依赖）：
  C1 普查可复现：同一 worktree 连跑两次结果逐位相同（防随机/时间依赖混入证据链）；
  C2 记账闭合：每行端点必须带 端点/处理器/模块/证据等级/静态体事实 五个字段；
  C3 分类单调：ANCHORED 行必须有判据文件；MOCK_RISK 行必须被静态体事实支持；
  C4 欠账棘轮：所有 MOCK_RISK 端点必须被 docs/audit/ship_api_mock_allowlist.json 显式申报，
     且申报的 known_debt 端点若已消失（被修复/移除）必须同步清理——否则打红。
     这条闸门的作用：**新出现的未申报 MOCK 风险无法静默合入**。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "audit" / "ship_api_evidence_census.py"
ALLOWLIST = REPO_ROOT / "docs" / "audit" / "ship_api_mock_allowlist.json"


def _load_module():
    # 必须先注册到 sys.modules：dataclass 装饰器在解析字段类型时需要模块可回查
    name = "ship_api_evidence_census"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def ledger(mod):
    return mod.census(REPO_ROOT)


def test_census_covers_ship_surface(ledger):
    """C1a: 普查必须真的扫到船舶总体性能域端点（防空扫通过）。"""
    assert ledger["totals"]["ship_domain"] >= 30, ledger["totals"]
    assert ledger["totals"]["api_routes_all"] >= ledger["totals"]["ship_domain"]
    assert ledger["grades"]["ANCHORED"] > 0


def test_census_is_deterministic(mod):
    """C1: 连跑两次逐位相同。"""
    a = mod.census(REPO_ROOT)
    b = mod.census(REPO_ROOT)
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def test_every_row_is_fully_accounted(ledger):
    """C2: 记账闭合，五字段齐全且模块路径在仓内存在。"""
    rows = ledger["endpoints"]
    assert rows, "ledger 为空"
    for r in rows:
        for key in ("endpoint", "handler", "module", "grade", "static"):
            assert key in r, (key, r)
        assert r["grade"] in {"ANCHORED", "WITHHELD", "MOCK_RISK"}, r
        assert (REPO_ROOT / r["module"]).is_file(), r["module"]
        assert r["lineno"] > 0


def test_classification_is_monotone(ledger):
    """C3a: ANCHORED 必须给出判据文件；WITHHELD 不得声称有判据。"""
    for r in ledger["endpoints"]:
        if r["grade"] == "ANCHORED":
            assert r["evidence"] == "ANCHORED" and r["evidence_files"], r
        if r["grade"] == "WITHHELD":
            assert r["evidence"] in {"SMOKE", "UNCOVERED"}, r


def test_mock_risk_is_statically_supported(ledger):
    """C3b: MOCK_RISK 必须由静态体事实支持（常量返回或随机数），不是拍脑袋。"""
    for r in ledger["endpoints"]:
        if r["grade"] != "MOCK_RISK":
            continue
        assert r["static"]["constant_return"] or r["static"]["uses_random"], r
        assert r["evidence"] != "ANCHORED", r


def test_debt_ratchet_no_undeclared_mock(ledger):
    """C4a: 不允许出现未申报的 MOCK 风险端点。"""
    assert ledger["unacknowledged_mock_risk"] == [], (
        "发现未申报 MOCK 风险端点，请在 docs/audit/ship_api_mock_allowlist.json "
        f"申报或修复：{ledger['unacknowledged_mock_risk']}"
    )


def test_debt_ratchet_declared_entries_still_exist(ledger):
    """C4b: 申报表不得留僵尸条目（修复后必须同步清理，否则棘轮失效）。"""
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    live = {r["endpoint"] for r in ledger["endpoints"]}
    declared = set(data.get("acknowledged", [])) | set(data.get("known_debt", []))
    stale = sorted(declared - live)
    assert stale == [], f"申报表存在已不存在的端点，请清理：{stale}"


def test_allowlist_covers_all_mock_risk(ledger):
    """C4c: 申报表必须覆盖全部 MOCK_RISK 端点（双向闭合）。"""
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    declared = set(data.get("acknowledged", [])) | set(data.get("known_debt", []))
    mocked = {r["endpoint"] for r in ledger["endpoints"] if r["grade"] == "MOCK_RISK"}
    assert mocked <= declared, sorted(mocked - declared)


def test_markdown_render_contains_ledger_totals(ledger, mod):
    """C5: 报告渲染与 ledger 数字一致（防报告与数据漂移）。"""
    md = mod.render_markdown(ledger)
    assert str(ledger["totals"]["ship_domain"]) in md
    assert str(ledger["grades"]["ANCHORED"]) in md
    assert "MOCK 风险端点" in md


def test_ledger_file_matches_live_census(ledger):
    """C6: 落盘 ledger 必须与实时普查一致（防陈旧台账冒充当前证据）。"""
    path = REPO_ROOT / "docs" / "audit" / "ship_api_evidence_ledger.json"
    assert path.is_file(), "缺少 ledger，请运行 python3 tools/audit/ship_api_evidence_census.py --write"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["totals"] == ledger["totals"]
    assert on_disk["grades"] == ledger["grades"]
    assert on_disk["coverage_pct"] == ledger["coverage_pct"]