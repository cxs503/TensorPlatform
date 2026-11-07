#!/usr/bin/env python3
"""`audit_capabilities` 工具（ops/tp_worker.py 的 agent 侧仪器）回归测试（m2031）。

事故
----
`_tool_audit_caps()` 只认一条硬编码路径
`/root/ship-performance-platform-incoming/ship-performance-platform/backend/platform_harness/capabilities.json`。
该文件在当前 incoming 树里只剩 `__pycache__/capabilities.cpython-311.pyc`，于是工具对
**全部 agent 恒返回「读取能力清单失败: [Errno 2] ...」**：证据分级是平台最核心的判据
来源（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED），仪器一死，40 个席位的自评全部失去参照。
更糟的是失败形式是「静默降级为一句报错」——agent 拿不到任何能力等级，也不会报警。

修复后判据
----------
T1 正例：读到仓内单一真值（artifacts/capability_ledger/）并对 21 项能力给出
    「自述 vs 对账」双列；propeller.open_water 必须呈现 REAL→MOCK 的 OVERCLAIM；
T2 优先级：TP_CAP_LEDGER 显式指定时以其为准；
T3 回退：最高优先级候选是坏 JSON 时必须跳过并落到下一个可用候选（不许整体失败）；
T4 反例（阴性对照）：所有候选都不可用时，返回必须**明确报失败并列出尝试过的路径**，
    不得返回看似正常的能力表（否则仪器失灵会被误读为「能力都正常」）。

全程离线：只读工作树，不访问 :8010/:8001。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "ops" / "tp_worker.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("tp_worker_m2031", WORKER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_worker_m2031"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def worker():
    return _load_worker()


def test_t1_reports_claimed_vs_reconciled(worker, monkeypatch):
    monkeypatch.delenv("TP_CAP_LEDGER", raising=False)
    out = worker._tool_audit_caps()
    assert "读取能力清单失败" not in out, out
    assert "能力清单 (21 个" in out
    assert "自述" in out and "对账" in out
    # 三态分歧必须可见，否则等于把自述当事实
    assert "OVERCLAIM" in out
    # 关键分歧：螺旋桨敞水自述 REAL、独立复核判 MOCK
    row = [ln for ln in out.splitlines() if ln.startswith("propeller.open_water")]
    assert row, "缺少 propeller.open_water 行"
    assert "REAL" in row[0] and "MOCK" in row[0] and "OVERCLAIM" in row[0], row[0]
    assert "[真值来源]" in out
    assert len(out) < 8000  # 工具返回给 LLM 的预算


def test_t2_explicit_env_wins(worker, monkeypatch, tmp_path):
    src = json.loads((ROOT / "artifacts" / "capability_ledger" /
                     "capability_ledger_resolved.json").read_text(encoding="utf-8"))
    # 造一份只含 2 项能力的清单，验证「显式指定 > 内置默认」
    src["capabilities"] = src["capabilities"][:2]
    alt = tmp_path / "cap_small.json"
    alt.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("TP_CAP_LEDGER", str(alt))
    out = worker._tool_audit_caps()
    assert "能力清单 (2 个" in out, out
    assert str(alt) in out


def test_t3_falls_back_past_corrupt_candidate(worker, monkeypatch, tmp_path):
    corrupt = tmp_path / "cap_corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TP_CAP_LEDGER", str(corrupt))
    out = worker._tool_audit_caps()
    assert "读取能力清单失败" not in out, out
    assert "能力清单 (21 个" in out
    assert "capability_ledger_resolved.json" in out


def test_t4_all_sources_dead_reports_failure(worker, monkeypatch, tmp_path):
    corrupt = tmp_path / "cap_corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TP_CAP_LEDGER", str(corrupt))
    monkeypatch.setenv("TP_SHIP_HARNESS_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(worker, "CAP_SNAPSHOT_REL", pathlib.Path("artifacts/__none__/a.json"))
    monkeypatch.setattr(worker, "CAP_LEDGER_REL", pathlib.Path("artifacts/__none__/b.json"))
    monkeypatch.setattr(worker, "CAP_HARNESS_DEFAULT", tmp_path / "nope2.json")
    # m5238/W18-N09 新增的兜底候选（证伪台账）也必须一并打死，否则「全灭」不成立
    if hasattr(worker, "CAP_FALSIFICATION_REL"):
        monkeypatch.setattr(worker, "CAP_FALSIFICATION_REL",
                            pathlib.Path("artifacts/__none__/c.json"))
    out = worker._tool_audit_caps()
    assert "读取能力清单失败" in out
    assert "能力清单 (" not in out, "仪器全灭时必须报失败，不能吐出看似正常的能力表"
    assert str(corrupt) in out  # 尝试过的路径要留痕，便于下一班定位