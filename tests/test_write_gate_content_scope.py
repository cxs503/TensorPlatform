"""写入边界门：凭证判据的**内容范围**（``secrets.content_scope``）测试。

背景（被修复的已登记局限）
--------------------------
原先 ``staged`` 模式把索引里的**整份 blob** 交给凭证扫描：只要文件里历史遗留一行
"像凭证"的内容，之后任何一次无关改动都会被拦（WRITE_GATE.md §5 第 3 条记为遗留局限）。
本模块验证新增的 ``added`` 范围：

* ``full``（缺省）：整份内容都在判据范围内，历史内容照样拦截（行为不变，向后兼容）；
* ``added``：只判本次暂存 diff **新增的行**；旧内容命中降级为 ``pre_existing_content``
  advisory（留痕、不静默隐藏），新增内容命中照常拦截（``FAIL``）。

覆盖矩阵
--------
1. hunk 解析：``git diff -U0`` → 新增行号（含 ``,d`` 缺省按 1、多 hunk 去重）
2. 纯函数层：``Change.added_lines`` 控制拦截/advisory 分流
3. 真实 git 夹具：full 拦历史 / added 放历史拦新增 / 新增文件整份在范围内
4. CLI：``--content-scope added`` 端到端 + 退出码 + JSON 判决里的 ``content_scope``
5. 确定性：同输入两次逐字节一致；两种范围摘要不同（范围进入判决，可审计）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.write_gate import cli, gate, policy as policy_mod  # noqa: E402
from tools.write_gate.model import Change  # noqa: E402

POLICY_PATH = REPO_ROOT / "data" / "write_gate" / "policy.json"

# 预注册的凭证形态：私钥块头（policy.json 的 private_key_block 规则）。
# 字面拼接、不写整串：本仓纪律是测试夹具自身也要过写入门（同 tests/test_write_gate.py）。
SECRET_LINE = "-----BEGIN RSA " + "PRIVATE KEY-----"

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME="/tmp",
               GIT_AUTHOR_NAME="gate", GIT_AUTHOR_EMAIL="gate@example.invalid",
               GIT_COMMITTER_NAME="gate", GIT_COMMITTER_EMAIL="gate@example.invalid")
GIT_ID = ["-c", "user.name=gate", "-c", "user.email=gate@example.invalid"]


# ─────────────────────────── 夹具 ───────────────────────────
def cfg(scope: str = "full"):
    return policy_mod.load_policy(POLICY_PATH,
                                  overrides={"whitelist": {"allow_globs": ["**"]},
                                             "secrets": {"content_scope": scope}})


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *GIT_ID, *args], capture_output=True,
                          text=True, env=GIT_ENV, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """真实 git 仓：``app/settings.py`` 首行已提交一行历史遗留凭证。"""
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "app" / "settings.py").write_text(
        f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _stage(root: Path, text: str, path: str = "app/settings.py") -> None:
    (root / path).write_text(text, encoding="utf-8")
    _git(root, "add", path)


# ─────────────────────── 1. hunk 解析（纯函数） ───────────────────────
def test_parse_added_lines_handles_default_count_and_multi_hunk():
    diff = (
        "diff --git a/app/settings.py b/app/settings.py\n"
        "--- a/app/settings.py\n"
        "+++ b/app/settings.py\n"
        "@@ -3,0 +4 @@ def f():\n"
        "+x = 1\n"
        "@@ -10,2 +12,3 @@\n"
        "+a\n"
        "+b\n"
        "+c\n"
        "@@ -20 +23,0 @@\n"
        "-gone\n"
    )
    # ``+4`` 缺省计 1 行；``+12,3`` = 12..14；纯删除 hunk 不贡献新增行
    assert gate.parse_added_lines(diff) == (4, 12, 13, 14)


def test_parse_added_lines_on_real_diff(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nEXTRA = 1\n")
    raw = _git(repo, "diff", "--cached", "-U0", "--no-color")
    assert gate.parse_added_lines(raw) == (4,)


# ─────────────────────── 2. 纯函数分流（无 git） ───────────────────────
def _verdict_for(text: str, added_lines):
    change = Change(path="app/settings.py", kind="MODIFY", content=text,
                    added_lines=added_lines, source="paths")
    return gate.evaluate_changes([change], cfg())


def test_added_lines_none_scans_whole_content():
    verdict = _verdict_for(f"a = 1\n{SECRET_LINE}\n", None)
    assert verdict.verdict == "FAIL"
    assert [v.line for v in verdict.violations] == [2]
    assert verdict.files[0]["content_scope"] == "full"


def test_added_lines_out_of_scope_becomes_advisory_not_block():
    verdict = _verdict_for(f"a = 1\n{SECRET_LINE}\n", (1,))
    assert verdict.verdict == "PASS"
    assert verdict.violations == []
    advisories = [a for a in verdict.advisories if a["reason"] == "pre_existing_content"]
    assert len(advisories) == 1
    assert advisories[0]["line"] == 2 and advisories[0]["rule_id"] == "private_key_block"
    # 脱敏：advisory 不回显原文
    assert "PRIVATE KEY" not in json.dumps(verdict.to_json(), ensure_ascii=False)
    assert verdict.files[0]["content_scope"] == "added"


def test_added_lines_in_scope_still_blocks():
    verdict = _verdict_for(f"a = 1\n{SECRET_LINE}\n", (1, 2))
    assert verdict.verdict == "FAIL"
    assert [v.line for v in verdict.violations] == [2]


def test_truncated_hits_are_reported_not_silent():
    """命中数触顶时显式留痕：范围外命中很多也不允许"静默没扫到"。"""
    limit = 50
    text = "\n".join(SECRET_LINE for _ in range(limit + 10))
    verdict = _verdict_for(text, ())
    assert [a["reason"] for a in verdict.advisories].count("content_hits_truncated") == 1
    assert len([a for a in verdict.advisories if a["reason"] == "pre_existing_content"]) == limit
    assert verdict.verdict == "PASS"


# ─────────────────────── 3. 真实索引夹具 ───────────────────────
def test_full_scope_blocks_unrelated_edit_of_file_with_legacy_secret(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nEXTRA = 1\n")
    verdict = gate.evaluate_staged(repo, cfg("full"))
    assert verdict.verdict == "FAIL"
    assert [v.line for v in verdict.violations] == [2]


def test_added_scope_lets_unrelated_edit_through_with_advisory(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nEXTRA = 1\n")
    verdict = gate.evaluate_staged(repo, cfg("added"))
    assert verdict.verdict == "PASS"
    advisories = [a for a in verdict.advisories if a["reason"] == "pre_existing_content"]
    assert [a["line"] for a in advisories] == [2]


def test_added_scope_blocks_newly_added_secret(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nTOKEN_HDR = '{SECRET_LINE}'\n")
    verdict = gate.evaluate_staged(repo, cfg("added"))
    assert verdict.verdict == "FAIL"
    assert [v.line for v in verdict.violations] == [4]


def test_added_scope_scans_whole_new_file(repo: Path):
    _stage(repo, f"# new\n{SECRET_LINE}\n", path="app/new_module.py")
    verdict = gate.evaluate_staged(repo, cfg("added"))
    assert verdict.verdict == "FAIL"
    assert sorted(v.line for v in verdict.violations) == [2]


def test_added_scope_ignores_deletion_only_edit(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\n")
    verdict = gate.evaluate_staged(repo, cfg("added"))
    assert verdict.verdict == "PASS"


# ─────────────────────── 4. CLI 端到端 ───────────────────────
def test_cli_content_scope_flag_end_to_end(repo: Path, tmp_path: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nEXTRA = 1\n")
    out = tmp_path / "verdict.json"
    blocked = cli.main(["--root", str(repo), "--policy", str(POLICY_PATH),
                        "--no-ledger", "--json", str(out), "--format", "json", "staged"])
    assert blocked == cli.EXIT_BLOCK
    payload_full = json.loads(out.read_text(encoding="utf-8"))
    assert payload_full["files"][0]["content_scope"] == "full"

    passed = cli.main(["--root", str(repo), "--policy", str(POLICY_PATH),
                       "--no-ledger", "--content-scope", "added",
                       "--json", str(out), "--format", "json", "staged"])
    assert passed == cli.EXIT_PASS
    payload_added = json.loads(out.read_text(encoding="utf-8"))
    assert payload_added["files"][0]["content_scope"] == "added"
    # 范围进入判决摘要 ⇒ 两种口径不会撞成同一个 digest（审计可分辨）
    assert payload_full["digest"] != payload_added["digest"]


def test_policy_rejects_unknown_content_scope():
    from tools.write_gate.model import WriteGateError
    with pytest.raises(WriteGateError):
        policy_mod.load_policy(POLICY_PATH, overrides={"secrets": {"content_scope": "diff"}})


# ─────────────────────── 5. 确定性 ───────────────────────
def test_added_scope_verdict_is_byte_stable(repo: Path):
    _stage(repo, f"# legacy\n{SECRET_LINE}\nMODE = 'dev'\nEXTRA = 1\n")
    first = gate.evaluate_staged(repo, cfg("added")).to_json()
    second = gate.evaluate_staged(repo, cfg("added")).to_json()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)