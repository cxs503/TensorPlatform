"""采集闸门：**已提交**（git tracked）的仓库副本必须零 collection error。

为什么需要这条闸门
------------------
平台大量「pytest 全绿」声明的隐含前提是工作树里存在 ``artifacts/`` 下的探针产物。
而这些目录不在提交里（合并门禁会滤除），于是换一台机器 / 换一个分支后
``pytest`` 在 **collection 阶段**（而非执行阶段）就中断 —— 整套 2000+ 测试一条都不跑，
但报告里仍写着"测试绿"。2026-11-02 实测：干净副本 5 个 collection error，工作树内 0 个。

本闸门用 ``git ls-files`` 复制**全部 tracked 文件**（内容取工作树，集合取索引）到一个临时目录，
在那里跑一次 ``--collect-only``，断言：

* returncode == 0；
* 输出里没有 "errors during collection"；
* 收集到的用例数 >= ``MIN_COLLECTED``（防止"把测试全 skip 掉换取零错误"的假绿）。

它不是"更严的 CI"，而是**证明证据可复现**：只有提交进仓库的东西才算证据。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIN_COLLECTED = 2500
SELF = "tests/test_clean_checkout_collection.py"


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def _materialise_tracked_tree(dest: Path) -> int:
    """按 git 索引复制 tracked 文件到 dest；未跟踪文件（含 artifacts/ 探针产物）一律不带。"""
    files = _tracked_files()
    n = 0
    for rel in files:
        src = ROOT / rel
        if not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


@pytest.mark.skipif(
    shutil.which("git") is None or os.environ.get("TP_CLEAN_CHECKOUT_GATE", "1") == "0",
    reason="git 不可用或闸门被 TP_CLEAN_CHECKOUT_GATE=0 显式关闭",
)
def test_tracked_tree_collects_without_errors():
    with tempfile.TemporaryDirectory(prefix="tp-clean-") as td:
        tree = Path(td) / "repo"
        tree.mkdir()
        n_files = _materialise_tracked_tree(tree)
        assert n_files > 500, f"tracked 文件过少（{n_files}），复制步骤疑似失败"

        env = dict(os.environ)
        env.pop("DATABASE_URL", None)          # 不让外部 DB 配置左右采集结果
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
             "-p", "no:cacheprovider", "--ignore", SELF],
            cwd=tree, capture_output=True, text=True, env=env, timeout=1800,
        )
        tail = (proc.stdout + proc.stderr)[-4000:]

    assert "errors during collection" not in tail, (
        "已提交副本存在 collection error（工作树里的探针产物不构成证据）：\n" + tail
    )
    assert proc.returncode == 0, "已提交副本 pytest 采集返回非零：\n" + tail

    collected = 0
    for line in proc.stdout.splitlines():
        if " collected" in line:
            try:
                collected = int(line.split(" collected")[0].split()[-1])
            except (ValueError, IndexError):
                pass
    assert collected >= MIN_COLLECTED, f"仅收集到 {collected} 例（< {MIN_COLLECTED}）"
