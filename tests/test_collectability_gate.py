"""套件可采集性闸门（m2059）——防「整轮采集中止」类回归。

历史：``pytest tests`` 曾因 5 个采集错误整轮中止（退出码 2），同一批 2590 例一条都跑不到。
根因分五类（R1–R5），详见 ``docs/audit/m2059_test_collectability_audit.md``。
本文件把「哪一类复发」变成机器可判：
  G1 历史热点文件必须可采集（子进程 --collect-only，退出码 0 且无 "errors during collection"）；
  G2 测试模块禁止裸 import ``artifacts/``：凡把 artifacts 目录塞进 sys.path 的测试，
      必须经 ``tests/_evidence.py`` 安全加载（缺失 → 模块级 WITHHELD skip）；
  G3 工件登记表必须自证「来源 + 恢复指令」，否则 WITHHELD 会退化成不可复现的借口；
  G4 加载器本身会 skip 而不是抛 ImportError。
运行：python3 -m pytest tests/test_collectability_gate.py -q
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(TESTS))

import _evidence  # noqa: E402

HOTSPOTS = [
    "tests/factory",
    "tests/test_public_wall.py",
    "tests/test_vibration_analytic_bench.py",
    "tests/verif/test_seakeeping_section_analytic.py",
    "tests/verifier/test_maneuvering_reference_gate.py",
]


def test_g1_historical_hotspots_collect_cleanly():
    cmd = [sys.executable, "-m", "pytest", *HOTSPOTS, "--collect-only", "-q",
           "-p", "no:cacheprovider"]
    out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    blob = out.stdout + out.stderr
    assert "errors during collection" not in blob, blob[-2000:]
    assert out.returncode == 0, blob[-2000:]
    assert "collected" in blob


def test_g2_no_unguarded_artifact_imports():
    offenders = []
    for py in sorted(TESTS.rglob("test_*.py")):
        src = py.read_text(encoding="utf-8")
        if "artifacts" not in src:
            continue
        tree = ast.parse(src)
        inserts_artifacts = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "insert":
                if any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                       and "artifacts" in a.value for a in node.args):
                    inserts_artifacts = True
        if inserts_artifacts and "_evidence" not in src:
            offenders.append(str(py.relative_to(ROOT)))
    assert not offenders, f"裸 import artifacts/ 的测试（改用 tests/_evidence.py）：{offenders}"


def test_g3_registry_entries_are_recoverable():
    reg = _evidence.registry()
    assert reg, "tests/evidence_registry.json 不可为空"
    for art, meta in reg.items():
        assert meta.get("provenance"), art
        assert meta.get("recover"), art
        assert meta.get("consumers"), art


def test_g4_loader_skips_instead_of_erroring():
    assert _evidence.locate("artifacts/__definitely_absent__") is None
    with pytest.raises(pytest.skip.Exception):
        _evidence.require("artifacts/__definitely_absent__")
    reason = _evidence.skip_reason("artifacts/__definitely_absent__")
    assert reason.startswith("WITHHELD:") and "恢复" in reason


def test_g5_no_hardcoded_absolute_dates_in_seeded_tests():
    """时间炸弹：按「最近 24h」计数的端点，种子时间写死日期会在次日必红。"""
    bad = []
    for name in ("tests/test_public_wall.py",):
        src = (ROOT / name).read_text(encoding="utf-8")
        bad += [n for n in ("2026-10-22T01:00:00", "2026-10-22T03:00:00") if n in src]
    assert not bad, f"写死日期残留：{bad}"
