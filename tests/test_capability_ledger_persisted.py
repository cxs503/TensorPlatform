#!/usr/bin/env python3
"""能力证据台账「入库防腐朽」闸门（mission 2031，platform 席）。

背景（真实事故，两次）
----------------------
`artifacts/capability_ledger/capability_ledger.json` 是平台能力分级的单一真值
（21 项自述等级 vs 对账等级的机器裁决）。它被**两次产出、两次未入库**：
m1567 与 m1811 的工作树里都有该文件，但 HEAD 上始终不存在 —— 于是
`tools/audit/capability_ledger.py --check` 退出 2、`tests/test_capability_ledger.py`
在干净工作树里全红（13 error + 2 failed），台帐对账仪处于「自述可用、实际死掉」状态，
且**没有任何判据能发现这件事**（缺文件时测试只是自己红，而不是闸门报警）。

本测试把「产出 ≠ 入库」这一失效模式变成机器判据：
  G1 台账/对账快照/表格三件套必须存在于工作树；
  G2 三件套必须被 git 跟踪（`git ls-files --error-unmatch`）—— 只存在于某人工作树
     的 artifacts（既不入库也不进 CI）是本事故的直接成因；
  G3 台账自身过 C1~C10 自检（退出码 0）；
  G4 curation 幂等（`curate_capability_ledger_m1811.py --check` 输出 SYNCED，
     否则说明台账被手改或未重新 curation）；
  G5（反例/阴性对照）把引文正则改成永不命中的样式，G3 必须翻为违规并点名 C3 ——
     证明 G3 不是恒真的摆设。

全程离线：只读工作树与本地 git，不访问 :8010/:8001。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = ROOT / "artifacts" / "capability_ledger"
LEDGER = LEDGER_DIR / "capability_ledger.json"
RESOLVED = LEDGER_DIR / "capability_ledger_resolved.json"
TABLE = LEDGER_DIR / "capability_ledger_table.md"
TOOL = ROOT / "tools" / "audit" / "capability_ledger.py"
CURATE = ROOT / "tools" / "audit" / "curate_capability_ledger_m1811.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("capability_ledger_m2031", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capability_ledger_m2031"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tracked(rel: str) -> bool:
    """该路径是否已被 git 跟踪（= 会随 merge 进入所有工作树/CI）。"""
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    return out.returncode == 0


# ── G1 存在性 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [LEDGER, RESOLVED, TABLE])
def test_g1_artifacts_exist(path: Path):
    assert path.is_file(), f"能力台账工件缺失: {path.relative_to(ROOT)}（欠账：产出未入库）"


# ── G2 入库（本事故的直接判据） ─────────────────────────────────────────
@pytest.mark.parametrize("path", [LEDGER, RESOLVED, TABLE])
def test_g2_artifacts_tracked_by_git(path: Path):
    rel = str(path.relative_to(ROOT))
    assert path.is_file(), f"无法判入库：{rel} 不存在"
    assert _tracked(rel), (
        f"{rel} 存在于工作树但**未被 git 跟踪** —— m1567/m1811 两次事故的复现形态："
        "台账只活在个人工作树里，干净工作树/CI 上没有它，对账仪静默失效。"
        "请 git add 后再提交。"
    )


def test_g2_gate_is_not_vacuous(tmp_path: Path):
    """阴性对照：未被跟踪的临时文件必须被判 False（否则 G2 恒真）。"""
    untracked = ROOT / "artifacts" / "capability_ledger" / "_untracked_probe_m2031.json"
    untracked.write_text("{}\n", encoding="utf-8")
    try:
        assert _tracked(str(untracked.relative_to(ROOT))) is False
    finally:
        untracked.unlink(missing_ok=True)


# ── G3 自检 C1~C10 ──────────────────────────────────────────────────────
def test_g3_ledger_passes_selfchecks_offline():
    out = subprocess.run([sys.executable, str(TOOL), "--check"],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, f"C1~C10 自检未通过（rc={out.returncode}）:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}"
    assert "PASS" in out.stdout


# ── G4 curation 幂等 ────────────────────────────────────────────────────
def test_g4_curation_is_idempotent():
    out = subprocess.run([sys.executable, str(CURATE), "--check"],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, f" curation 已漂移（台账被手改或未重跑）:\n{out.stdout}\n{out.stderr}"
    assert "SYNCED" in out.stdout


# ── G5 反例：闸门对引文失效必须报警 ─────────────────────────────────────
def test_g5_broken_quote_triggers_selfcheck(tmp_path: Path):
    tool = _load_tool()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    mutated = json.loads(json.dumps(ledger, ensure_ascii=False))
    # 把第一条证据的引文正则改成永不命中的样式（模拟「报告正文被改 / 引文是手抄的」）
    for cap in mutated["capabilities"]:
        if cap.get("evidence"):
            cap["evidence"][0]["quote_match"] = r"__m2031_quote_that_never_matches__"
            break
    else:  # pragma: no cover - 台账结构变了才会到这里
        pytest.fail("台账里找不到带证据的能力行，无法构造反例")
    bad = tmp_path / "capability_ledger_mutated.json"
    bad.write_text(json.dumps(mutated, ensure_ascii=False), encoding="utf-8")

    orig = tool.LEDGER
    tool.LEDGER = bad
    try:
        rc = tool.main(["--check"])
    finally:
        tool.LEDGER = orig
    assert rc == 1, "引文被破坏后台账仍判通过 —— C3（引文逐字命中）形同虚设"