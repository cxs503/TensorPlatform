#!/usr/bin/env python3
"""suite_check.py 回归测试（P2b 修复：聚焦清单存在性过滤，m116 假红教训）。

  S1 全部 4 文件在 → 清单不变（正常基全跑）
  S2 只 1 文件在（m116 基实况）→ 清单缩到存在者，绝不产生 no-tests-ran 假红
  S3 全缺（基先于 #371）→ 空清单（调用方披露跳过）

跑法：/srv/tensor/shared/hermes-agent/.venv/bin/python -m pytest test_suite_check.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "suite_check", HERE / "suite_check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def test_focused_full_when_all_exist(tmp_path):
    for f in sc.FOCUSED:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    assert sc.focused_for(tmp_path) == sc.FOCUSED


def test_focused_partial_m116_base(tmp_path):
    """m116 实况：基 c86554df 只有 test_triton_fused.py 在。"""
    p = tmp_path / sc.FOCUSED[0]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    got = sc.focused_for(tmp_path)
    assert got == [sc.FOCUSED[0]]


def test_focused_empty_when_none(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "other.py").write_text("")
    assert sc.focused_for(tmp_path) == []
