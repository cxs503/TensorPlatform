"""能力台账 pin 自愈闭环测试（m15074）。

覆盖验收判据：
a) pin 缺失 → ``--fix`` → gate 转 OK 且 pin sha256 == 台账 sha256；
b) 篡改台账后 ``--fix`` **不掩盖**（仍 LEDGER_DRIFT，退出码 1，pin 未被覆盖）；
c) 幂等（连续两次 ``--fix``，pin 内容不变）；
d) 已健康世界 → ``--fix`` no-op（不重写 pin）；
e) 无台账 → 退出码 1 且不写 pin；
f) 叠加其他缺陷（DERIVED_MISSING）→ 只报不修。

世界构造沿用 ``tests/test_ledger_pin_contract.py::_make_world`` 的思路：以真仓台账/
派生物为母本，在 ``tmp_path`` 里造自洽世界，再按需注入缺陷。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402
from tools.audit import ledger_selfheal as LH  # noqa: E402

LEDGER = ROOT / LP.LEDGER_REL
RESOLVED = ROOT / LP.RESOLVED_REL
PIN = ROOT / LP.PIN_REL


def _make_world(tmp: Path, *, pin_missing: bool = False, mutate_ledger: bool = False,
                pin_sha256: str | None = None, no_ledger: bool = False,
                drop_derived: bool = False) -> Path:
    """在 tmp 下造一份自洽的台账世界，可按需注入缺陷。"""
    d = tmp / "artifacts" / "capability_ledger"
    d.mkdir(parents=True, exist_ok=True)
    if no_ledger:
        return tmp

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if mutate_ledger:  # 篡改：加一条自述能力，台账 sha256 变、pin 仍旧
        ledger["capabilities"].append({"id": "_tampered", "claimed_level": "MOCK",
                                       "evidence": "none"})
    (d / "capability_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    if not drop_derived:
        shutil.copy2(RESOLVED, d / "capability_ledger_resolved.json")

    if not pin_missing:
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        if pin_sha256 is not None:
            pin["sha256"] = pin_sha256
        elif not mutate_ledger:
            pin["sha256"] = LP.sha256_file(d / "capability_ledger.json")
        (d / "ledger_contract.json").write_text(
            json.dumps(pin, ensure_ascii=False, indent=1), encoding="utf-8")
    return tmp


def _pin_file(world: Path) -> Path:
    return world / LP.PIN_REL


def _run_cli(argv, capsys):
    """跑 CLI，返回 (退出码, 解析后的 stdout JSON)。"""
    code = LH.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


# ── a) pin 缺失 → --fix 自愈，gate 转 OK ──────────────────────────────
def test_fix_pin_missing_restores_gate(tmp_path, capsys):
    world = _make_world(tmp_path, pin_missing=True)
    assert not _pin_file(world).exists()

    code, out = _run_cli(["--fix", "--json", "--root", str(world)], capsys)

    assert code == 0, out
    assert out["action_taken"] == LH.ACTION_WROTE_PIN
    assert out["before"]["reasons"] == ["PIN_MISSING"]
    assert out["before"]["gate"] == "FAIL"
    assert out["after"]["reasons"] == []
    assert out["status"] == LP.STATUS_OK
    assert out["after"]["gate"] == "PASS"

    pin = json.loads(_pin_file(world).read_text(encoding="utf-8"))
    ledger_sha = LP.sha256_file(world / LP.LEDGER_REL)
    assert pin["sha256"] == ledger_sha == out["sha256"]
    assert out["pin_matches_ledger"] is True
    # 台账本体未被改动（自愈只补签，不改真值）
    assert LP.sha256_file(world / LP.LEDGER_REL) == out["sha256"]


# ── b) 篡改台账 → --fix 不掩盖（红灯仍在，pin 未被覆盖） ──────────────
def test_fix_refuses_to_mask_tampered_ledger(tmp_path, capsys):
    world = _make_world(tmp_path, mutate_ledger=True)
    pin_before = _pin_file(world).read_text(encoding="utf-8")
    assert "LEDGER_DRIFT" in LP.resolve_ledger(root=world)["reasons"]

    code, out = _run_cli(["--fix", "--json", "--root", str(world)], capsys)

    assert code == 1, out
    assert out["action_taken"] == LH.ACTION_REFUSED
    assert "LEDGER_DRIFT" in out["reasons"]
    assert out["fixable"] == []
    assert out["before"]["reasons"] == out["after"]["reasons"]
    # pin 未被覆盖（否则等于给篡改盖章）
    assert _pin_file(world).read_text(encoding="utf-8") == pin_before


# ── c) 幂等：连续两次 --fix，pin 内容不变 ─────────────────────────────
def test_fix_is_idempotent(tmp_path, capsys):
    world = _make_world(tmp_path, pin_missing=True)

    code1, out1 = _run_cli(["--fix", "--json", "--root", str(world)], capsys)
    assert code1 == 0 and out1["action_taken"] == LH.ACTION_WROTE_PIN
    pin_after_first = _pin_file(world).read_text(encoding="utf-8")

    code2, out2 = _run_cli(["--fix", "--json", "--root", str(world)], capsys)
    assert code2 == 0, out2
    assert out2["action_taken"] == LH.ACTION_NONE
    assert out2["status"] == LP.STATUS_OK
    assert _pin_file(world).read_text(encoding="utf-8") == pin_after_first


# ── d) 已健康世界 → no-op（不重写 pin，保留 pinned_at 与字节） ────────
def test_fix_on_healthy_world_is_noop(tmp_path, capsys):
    world = _make_world(tmp_path)
    pin_before = _pin_file(world).read_text(encoding="utf-8")
    mtime_before = _pin_file(world).stat().st_mtime_ns

    code, out = _run_cli(["--fix", "--json", "--root", str(world)], capsys)

    assert code == 0, out
    assert out["action_taken"] == LH.ACTION_NONE
    assert out["status"] == LP.STATUS_OK and out["reasons"] == []
    assert _pin_file(world).read_text(encoding="utf-8") == pin_before
    assert _pin_file(world).stat().st_mtime_ns == mtime_before


# ── e) 无台账 → 退出码 1 且不写 pin ───────────────────────────────────
def test_fix_without_ledger_writes_nothing(tmp_path, capsys):
    world = _make_world(tmp_path, no_ledger=True)

    code, out = _run_cli(["--fix", "--json", "--root", str(world)], capsys)

    assert code == 1, out
    assert "NO_LEDGER" in out["reasons"]
    assert out["fixable"] == []
    assert out["action_taken"] in (LH.ACTION_REFUSED, LH.ACTION_FAILED)
    assert not _pin_file(world).exists()


# ── f) pin 缺失 + 派生物缺失 → 只报不修（不许用写章掩盖其他红灯） ──────
def test_pin_missing_with_other_reasons_is_not_fixed(tmp_path, capsys):
    world = _make_world(tmp_path, pin_missing=True, drop_derived=True)

    code, out = _run_cli(["--check", "--json", "--root", str(world)], capsys)
    assert code == 1, out
    assert "PIN_MISSING" in out["reasons"]
    assert "DERIVED_MISSING" in out["reasons"]
    assert out["fixable"] == []           # 可修子句被叠加缺陷否决
    assert "DERIVED_MISSING" in out["unfixable"]

    code, out = _run_cli(["--fix", "--json", "--root", str(world)], capsys)
    assert code == 1, out
    assert out["action_taken"] == LH.ACTION_REFUSED
    assert not _pin_file(world).exists()


# ── g) --check 对真仓只读：不落盘、不改状态 ──────────────────────────
def test_check_is_read_only_on_real_repo(capsys):
    pin_before = PIN.read_text(encoding="utf-8")
    code, out = _run_cli(["--check", "--json"], capsys)
    assert out["action_taken"] == LH.ACTION_NONE
    assert out["scope"] == LH.SCOPE_REPO
    assert out["reasons"] == [] and out["status"] == LP.STATUS_OK
    assert out["gate"]["verdict"] == "PASS"
    assert code == 0
    assert PIN.read_text(encoding="utf-8") == pin_before