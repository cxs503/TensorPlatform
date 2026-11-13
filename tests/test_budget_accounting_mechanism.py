"""FP15-BUDGET-ACCOUNTING v2 —— 机制三件套的回归守卫（合成夹具，零领域语义）。

守的是什么
----------
1. **写点审计器**（``tools/audit_terminal_budget_writes.py``）：仓里任何
   `UPDATE tensor_dev_missions ... SET status='<终态词>'` 都必须「自带记账」或
   「落在 DB 触发器兜底面」。首版扫描器把 `WHERE status='succeeded'` 也当成写点，
   12 条全是假阳性——用例 `test_scanner_ignores_where_clause_filters` 把这条错钉住。
2. **覆盖率闸**（``tools/budget_accounting_coverage.py``）：判据写死在代码里——
   样本不足 = 退出码 2（未跑 ≠ 绿），率不达标 = 1，达标 = 0。
3. **补录器**（``tools/backfill_budget_used.py:plan_rows``）：纯函数，墙钟必须用
   ``finished_at - created_at``；已记账行不许被改；脏 result 降级不崩。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for path in (REPO, REPO / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load("audit_terminal_budget_writes", "tools/audit_terminal_budget_writes.py")
coverage = _load("budget_accounting_coverage", "tools/budget_accounting_coverage.py")
backfill = _load("backfill_budget_used", "tools/backfill_budget_used.py")


# ── 1. 写点扫描器 ────────────────────────────────────────────────────────────

def test_scanner_ignores_where_clause_filters(tmp_path):
    """`WHERE status='succeeded'` 是查询过滤，不是写点（首版 12 条假阳性来源）。"""
    src = '''
def report(db):
    db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE status='succeeded'"))
    db.execute(text("SELECT id FROM tensor_dev_missions WHERE status IN ('failed','succeeded')"))
'''
    hits = list(audit._update_sites(src))
    assert hits == []


def test_scanner_finds_terminal_write_and_ledger_token():
    src = '''
def finish(db, mid):
    record_budget_used(db, mid)
    db.execute(text("""
        UPDATE tensor_dev_missions
           SET status = 'failed', finished_at = now()
         WHERE id = :mid
    """), {"mid": mid})
'''
    hits = list(audit._update_sites(src))
    assert len(hits) == 1 and hits[0][1] == "failed"
    assert "record_budget_used" in src


def test_scanner_flags_naked_terminal_write():
    src = '''
def finish(db, mid):
    db.execute(text("UPDATE tensor_dev_missions SET status = 'revoked' WHERE id = :mid"))
'''
    hits = list(audit._update_sites(src))
    assert len(hits) == 1 and hits[0][1] == "revoked"
    lines = src.splitlines()
    assert not audit._window_has_ledger(lines, 2)


def test_scanner_accepts_parameterised_terminal_write():
    src = '''
db.execute(text("UPDATE tensor_dev_missions SET status = :st WHERE id = :mid"),
           {"st": body.status, "mid": mid})
'''
    hits = list(audit._update_sites(src))
    assert len(hits) == 1
    assert hits[0][1] is None and hits[0][2] == "st"      # 参数化 → 由调用方/触发器兜底


def test_repo_audit_has_no_naked_write_site():
    """仓内现状：裸写点必须为 0（否则机器门红）。"""
    report = audit.scan()
    assert report["naked"] == [], report["naked"]
    assert report["summary"]["sites"] >= 5


# ── 2. 覆盖率闸 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,non_empty,expect_code,expect_word", [
    (10, 10, 0, "pass"),
    (10, 9, 0, "pass"),        # 90% 正好达标
    (10, 8, 1, "fail"),        # 80% 不达标
    (4, 4, 2, "insufficient"),  # 样本不足 ≠ 通过
    (0, 0, 2, "insufficient"),
])
def test_coverage_verdict_is_machine_decidable(n, non_empty, expect_code, expect_word):
    rate = None if n == 0 else round(100.0 * non_empty / n, 2)
    report = {"n": n, "non_empty": non_empty, "rate": rate}
    text, code = coverage.verdict(report, min_n=5, threshold=0.90)
    assert code == expect_code
    assert expect_word in text


def test_coverage_query_counts_only_four_key_records():
    """分子口径：四键齐全才算记账（历史形状 {} / 只有 legacy 键都不算）。"""
    assert "?&" in coverage.SQL and "ARRAY['tool_calls','tokens','wall_seconds','source']" in coverage.SQL


# ── 3. 补录器（纯函数） ──────────────────────────────────────────────────────

def _row(mid, result, created, finished, used=None):
    return (mid, result, created, finished, used or {})


def test_backfill_uses_finished_at_not_now():
    row = _row(1, {"tool_calls": 7},
               datetime(2026, 1, 1, tzinfo=timezone.utc),
               datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc))
    [item] = backfill.plan_rows([row])
    assert item["new"]["wall_seconds"] == 600        # finished - created，不是 weeks
    assert item["new"]["tool_calls"] == 7
    assert item["new"]["recorded_by"] == "backfill_20261113"


def test_backfill_skips_already_recorded_rows():
    used = {"tool_calls": 1, "tokens": 0, "wall_seconds": 5, "source": "x"}
    row = _row(2, {"tool_calls": 9}, datetime(2026, 1, 1, tzinfo=timezone.utc),
               datetime(2026, 1, 1, tzinfo=timezone.utc), used)
    assert backfill.plan_rows([row]) == []


def test_backfill_degrades_on_dirty_result():
    row = _row(3, "not-a-mapping", datetime(2026, 1, 1, tzinfo=timezone.utc), None)
    [item] = backfill.plan_rows([row])
    for key in ("tool_calls", "tokens", "wall_seconds", "source"):
        assert key in item["new"]
    assert item["new"]["sources"]["tool_calls"] == "unavailable"