#!/usr/bin/env python3
"""能力台账 pin 生产环境验收测试（m15402）。

验收判据（机器可算）：
- health_status == OK（无 PIN_MISSING）
- pin_file_exists == True
- pin_sha256_matches_ledger == True
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import ledger_paths as LP
from tools.audit import ledger_selfheal as LH


# ── 测试 1：台账解析状态为 OK（无 PIN_MISSING） ─────────────────────
def test_health_status_ok():
    """/health 的 capability_ledger.status 必须为 OK。"""
    ref = LP.resolve_ledger()
    assert ref["status"] == LP.STATUS_OK, (
        f"Expected status=OK, got {ref['status']} with reasons={ref['reasons']}"
    )
    assert "PIN_MISSING" not in ref["reasons"], (
        f"PIN_MISSING still present in reasons: {ref['reasons']}"
    )


# ── 测试 2：pin 文件存在 ────────────────────────────────────────────
def test_pin_file_exists():
    """pin 文件 artifacts/capability_ledger/ledger_contract.json 必须存在。"""
    pin_path = ROOT / LP.PIN_REL
    assert pin_path.is_file(), f"Pin file not found at {pin_path}"


# ── 测试 3：pin sha256 与台账 sha256 匹配 ──────────────────────────
def test_pin_sha256_matches_ledger():
    """pin 中记录的 sha256 必须与当前台账 sha256 一致。"""
    ref = LP.resolve_ledger()
    assert ref["sha256"], "Ledger sha256 is None"
    assert ref["pinned"], "Pin data is None"
    assert ref["pinned"]["sha256"] == ref["sha256"], (
        f"Pin sha256 mismatch: pin={ref['pinned']['sha256'][:16]}... "
        f"ledger={ref['sha256'][:16]}..."
    )


# ── 测试 4：自愈闭环可用（pin 缺失 → --fix → OK） ──────────────────
def test_selfheal_fixes_pin_missing(tmp_path):
    """模拟 pin 缺失场景，验证自愈能恢复 OK 状态。"""
    d = tmp_path / "artifacts" / "capability_ledger"
    d.mkdir(parents=True)
    ledger = {
        "schema_version": "2.0",
        "capabilities": [{"id": "test", "claimed_level": "REAL", "evidence": "synthetic"}]
    }
    (d / "capability_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    (d / "capability_ledger_resolved.json").write_text(json.dumps(ledger), encoding="utf-8")

    res = LH.fix(root=tmp_path)
    assert res["fixed"] is True, f"Self-heal failed: {res}"
    assert res["status"] == LP.STATUS_OK
    assert res["pin_matches_ledger"] is True


# ── 测试 5：幂等性（已健康时 --fix 不重写 pin） ─────────────────────
def test_selfheal_idempotent_on_healthy():
    """已健康状态下 fix 应为 no-op，不改变 pin 文件。"""
    pin_path = ROOT / LP.PIN_REL
    pin_before = pin_path.read_text(encoding="utf-8")
    mtime_before = pin_path.stat().st_mtime_ns

    res = LH.fix()
    assert res["action_taken"] == LH.ACTION_NONE, (
        f"Expected no-op on healthy world, got action={res['action_taken']}"
    )
    assert pin_path.read_text(encoding="utf-8") == pin_before
    assert pin_path.stat().st_mtime_ns == mtime_before


# ── 测试 6：契约门全绿 ──────────────────────────────────────────────
def test_contract_gate_passes():
    """契约门四消费者 sha256 一致、schema 注册、无漂移。"""
    from tools.audit import ledger_contract_gate as GATE
    rep = GATE.run(str(ROOT))
    assert rep["verdict"] == "PASS", f"Contract gate failed: {rep}"
    assert rep["violations"] == [], f"Contract violations: {rep['violations']}"
    assert rep["failed"] == [], f"Failed checks: {rep['failed']}"
