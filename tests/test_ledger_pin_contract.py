"""能力台账 PIN 落盘契约测试（m14437）。

覆盖验收判据：
- pin 缺失必红（C3/C4 FAIL）
- pin sha256 漂移必红（C3 FAIL）
- pin schema 漂移必红（C3 FAIL）
- pin 正确必绿（C1-C4 全 PASS）
- 篡改-还原往返（正控反例）
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402
from tools.audit import ledger_contract_gate as GATE  # noqa: E402

LEDGER = ROOT / LP.LEDGER_REL
RESOLVED = ROOT / LP.RESOLVED_REL
PIN = ROOT / LP.PIN_REL


def _make_world(tmp: Path, *, pin_missing: bool = False,
                pin_sha256: str | None = None, pin_schema: str | None = None,
                mutate_ledger: bool = False) -> Path:
    """在 tmp 下造一份自洽的台账世界，可按需注入缺陷。"""
    d = tmp / "artifacts" / "capability_ledger"
    d.mkdir(parents=True, exist_ok=True)

    # 台账
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if mutate_ledger:
        ledger["capabilities"].append({"id": "_tampered", "claimed_level": "MOCK", "evidence": "none"})
    (d / "capability_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(RESOLVED, d / "capability_ledger_resolved.json")

    # pin
    if not pin_missing:
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        if pin_sha256 is not None:
            pin["sha256"] = pin_sha256
        if pin_schema is not None:
            pin["schema_version"] = pin_schema
        if pin_sha256 is None and not mutate_ledger:
            pin["sha256"] = LP.sha256_file(d / "capability_ledger.json")
        (d / "ledger_contract.json").write_text(json.dumps(pin, ensure_ascii=False, indent=1), encoding="utf-8")

    return tmp


# ── 用例 1：pin 缺失必红 ──────────────────────────────────────────────
def test_pin_missing_causes_fail(tmp_path):
    """pin 文件不存在 → resolve_ledger 报 PIN_MISSING。"""
    world = _make_world(tmp_path, pin_missing=True)
    ref = LP.resolve_ledger(root=world)
    assert ref["status"] == LP.STATUS_DEGRADED
    assert "PIN_MISSING" in ref["reasons"]


# ── 用例 2：pin sha256 漂移必红 ───────────────────────────────────────
def test_pin_sha256_drift_causes_fail(tmp_path):
    """pin 的 sha256 与台账不一致 → LEDGER_DRIFT。"""
    world = _make_world(tmp_path, pin_sha256="0" * 64)
    ref = LP.resolve_ledger(root=world)
    assert ref["status"] == LP.STATUS_DEGRADED
    assert "LEDGER_DRIFT" in ref["reasons"]
    assert any(d["against"] == "pin" for d in ref["drift"])


# ── 用例 3：pin schema 漂移必红 ───────────────────────────────────────
def test_pin_schema_drift_causes_fail(tmp_path):
    """pin 的 schema_version 与台账不一致 → LEDGER_DRIFT。"""
    world = _make_world(tmp_path, pin_schema="9.9")
    ref = LP.resolve_ledger(root=world)
    assert ref["status"] == LP.STATUS_DEGRADED
    assert "LEDGER_DRIFT" in ref["reasons"]


# ── 用例 4：pin 正确必绿（用真实仓，消费者链完整） ─────────────────────
def test_pin_correct_causes_pass():
    """pin 与台账 sha256/schema 一致 → C1-C4 全 PASS。"""
    ref = LP.resolve_ledger()
    assert ref["status"] == LP.STATUS_OK, ref["reasons"]
    assert "PIN_MISSING" not in ref["reasons"]
    assert ref["drift"] == []
    res = GATE.run()
    assert res["verdict"] == "PASS", f"failed={res['failed']}"
    assert res["failed"] == []
    assert all(res["checks"].values())


# ── 用例 5：篡改-还原往返（正控反例，用真实仓） ───────────────────────
def test_tamper_restore_roundtrip():
    """篡改台账 → gate 红；还原 → gate 绿。"""
    # 基线：绿
    res_before = GATE.run()
    assert res_before["verdict"] == "PASS", f"baseline failed={res_before['failed']}"

    # 篡改：改台账内容
    original = LEDGER.read_text(encoding="utf-8")
    ledger = json.loads(original)
    ledger["capabilities"].append({"id": "_tampered_test", "claimed_level": "MOCK", "evidence": "none"})
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        # 篡改后：红
        res_tampered = GATE.run()
        assert res_tampered["verdict"] == "FAIL", "tampered ledger should fail"
        assert "C3_no_ledger_drift" in res_tampered["failed"]
    finally:
        # 还原：恢复原始内容
        LEDGER.write_text(original, encoding="utf-8")

    # 还原后：绿
    res_restored = GATE.run()
    assert res_restored["verdict"] == "PASS", f"restored failed={res_restored['failed']}"
    assert res_restored["failed"] == []
