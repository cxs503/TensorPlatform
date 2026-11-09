"""三合一写入边界门（tools/write_gate）测试。

覆盖矩阵
--------
* 白名单越界：10 种绕过写法**全部**拦截（前缀同形 / 回溯 / 点段 / 反斜杠 / 全角 /
  单层编码 / 双层编码 / NUL 截断 / 重复分隔符 / 大小写变体）
* 跨仓污染：越根 / 绝对路径 / 外部仓根 / 嵌套仓标记 / ``.git`` 内部 / 符号链接逃逸 /
  仓不符 —— 真实文件系统夹具
* 凭证泄漏：私钥块 / 凭据赋值 / URL 内嵌口令 / JWT / 云访问键 / 高熵串；并验证
  脱敏（判决 JSON 里绝不出现原文）、熵闸与行级豁免不误伤摘要
* 误报：预注册正常写入语料（122 路径 + 39 内容行）零拦截
* fail-closed：策略非法 → 异常；仓根不可读 → ``invalid`` + 拦截；判决确定性/幂等
* 暂存区集成：``git add`` 后的真实索引判决；门不写索引/工作树
* 零领域语义：包源码 + 策略/语料数据不含预注册域词条
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.write_gate import cli, gate, policy as policy_mod, secrets as secrets_mod  # noqa: E402
from tools.write_gate.model import Change, WriteGateError  # noqa: E402
from tools.write_gate.pathnorm import canonicalize, glob_match  # noqa: E402
from tools.write_gate.whitelist import decide, whitelist_from_text  # noqa: E402

PKG_DIR = REPO_ROOT / "tools" / "write_gate"
POLICY_PATH = REPO_ROOT / "data" / "write_gate" / "policy.json"
CORPUS = json.loads((REPO_ROOT / "data" / "write_gate" / "fp_corpus.json").read_text(encoding="utf-8"))

EDIT_WHITELIST = [
    "tools/write_gate/**",
    "docs/write_gate/**",
    "tests/test_write_gate.py",
    "data/write_gate/**",
    "artifacts/write_gate/**",
]

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME="/tmp",
               GIT_AUTHOR_NAME="gate", GIT_AUTHOR_EMAIL="gate@example.invalid",
               GIT_COMMITTER_NAME="gate", GIT_COMMITTER_EMAIL="gate@example.invalid")
GIT_ID = ["-c", "user.name=gate", "-c", "user.email=gate@example.invalid"]


# ─────────────────────────── 夹具 ───────────────────────────
def cfg_default():
    return policy_mod.load_policy(POLICY_PATH)


def cfg_whitelisted():
    return policy_mod.load_policy(POLICY_PATH, overrides={"whitelist": {"allow_globs": EDIT_WHITELIST}})


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    """模拟一个工作树：根带 ``.git`` 文件、内含嵌套仓、外部目录、符号链接。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    (root / "tools" / "write_gate").mkdir(parents=True)
    (root / "backend" / "api").mkdir(parents=True)
    (root / "vendor" / "subrepo" / ".git").mkdir(parents=True)
    (root / "vendor" / "subrepo" / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
    os.symlink(outside, root / "link")
    return root


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], capture_output=True,
                          text=True, env=GIT_ENV, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path


def _codes(verdict) -> list:
    return [v.code for v in verdict.violations]


def _categories(verdict) -> set:
    return {v.category for v in verdict.violations}


# ─────────────────── 1. 白名单越界（10 种写法） ───────────────────
WHITELIST_ESCAPES = [
    ("prefix_sibling", "tools/write_gate_evil/mod.py"),
    ("parent_traversal", "tools/write_gate/../../backend/api/leak.py"),
    ("dot_segments", "./tools/./../backend/leak.py"),
    ("backslash", "tools\\write_gate\\..\\..\\backend\\leak.py"),
    ("unicode_fullwidth", "tools／write_gate／．．／backend／leak.py"),
    ("percent_encoded", "tools/write_gate/%2e%2e/%2e%2e/backend/leak.py"),
    ("double_encoded", "tools/write_gate/%252e%252e/backend/leak.py"),
    ("nul_truncation", "tools/write_gate/ok.py\x00/../../../backend/leak.py"),
    ("doubled_separator", "tools//write_gate//..//..//backend/leak.py"),
    ("case_variant", "Tools/Write_Gate/../Backend/leak.py"),
]


@pytest.mark.parametrize("name,path", WHITELIST_ESCAPES, ids=[n for n, _ in WHITELIST_ESCAPES])
def test_whitelist_escape_is_blocked(name, path, worktree):
    verdict = gate.evaluate_changes([Change(path)], cfg_whitelisted(), root=worktree,
                                    active_repo="TensorPlatform")
    assert verdict.verdict == "FAIL", name
    assert _categories(verdict) <= {"whitelist", "invalid", "cross_repo"}, name
    assert verdict.violations, name


def test_whitelist_escape_lands_outside_whitelist(worktree):
    """每条绕过写法规范化后都必须落在白名单之外（拦截原因可解释，不只是"碰巧拒绝"）。"""
    cfg = cfg_whitelisted()
    for name, path in WHITELIST_ESCAPES:
        if "\x00" in path:
            continue  # NUL 走 invalid 分支，规范化即可判非法
        canon = canonicalize(path)
        assert not decide(canon.text, cfg).allowed, (name, canon.text)


def test_whitelist_inside_paths_allowed(worktree):
    paths = ["tools/write_gate/gate.py", "tools/write_gate/sub/deep/module.py",
             "docs/write_gate/WRITE_GATE.md", "tests/test_write_gate.py",
             "data/write_gate/policy.json", "artifacts/write_gate/verdict.json"]
    verdict = gate.evaluate_changes([Change(p) for p in paths], cfg_whitelisted(), root=worktree)
    assert verdict.verdict == "PASS", verdict.to_json()["violations"]
    assert verdict.blocked == 0


def test_glob_semantics_match_before_deciding():
    assert glob_match("tools/a/b.py", "tools/**")
    assert glob_match("a.py", "**")
    assert not glob_match("tools/write_gate", "tools/write_gate/**")
    assert not glob_match("tools/write_gate_evil/x.py", "tools/write_gate/**")
    assert glob_match("tools/wg/x.py", "tools/w?/x.py")
    assert glob_match("tests/test_a.py", "tests/test_[ab].py")


def test_deny_and_read_only_take_precedence():
    cfg = policy_mod.load_policy(POLICY_PATH, overrides={
        "whitelist": {"allow_globs": ["**"], "deny_globs": ["secret_area/**"],
                      "read_only_globs": ["frozen/**"], "treat_deny_as": "cross_repo"}})
    assert decide("secret_area/x.py", cfg).code == "deny_glob"
    assert decide("frozen/x.py", cfg).code == "read_only_glob"
    assert decide("ok/x.py", cfg).allowed


def test_edit_whitelist_env_parsing():
    parsed = whitelist_from_text("tools/write_gate/**, docs/x.md;\ntests/*.py")
    assert parsed["allow_globs"] == ["tools/write_gate/**", "docs/x.md", "tests/*.py"]


# ─────────────────── 2. 跨仓污染（5 种 + 2 扩展） ───────────────────
def test_c1_root_escape(worktree):
    verdict = gate.evaluate_changes([Change("../../etc/hosts")], cfg_default(), root=worktree)
    assert "root_escape" in _codes(verdict) and "cross_repo" in _categories(verdict)


def test_c2_absolute_foreign_path(worktree, tmp_path):
    foreign = tmp_path / "other_repo" / "backend" / "x.py"
    verdict = gate.evaluate_changes([Change(str(foreign))], cfg_default(), root=worktree)
    assert "absolute_path" in _codes(verdict)


def test_c3_nested_repo_marker(worktree):
    verdict = gate.evaluate_changes([Change("vendor/subrepo/src/x.py")], cfg_default(), root=worktree)
    assert "nested_repo_marker" in _codes(verdict)


def test_c4_git_internals(worktree):
    verdict = gate.evaluate_changes([Change(".git/config")], cfg_default(), root=worktree)
    assert "git_internals" in _codes(verdict)
    assert gate.evaluate_changes([Change(".git/hooks/pre-commit")], cfg_default(),
                                 root=worktree).verdict == "FAIL"


def test_c5_symlink_escape(worktree):
    verdict = gate.evaluate_changes([Change("link/secret.txt")], cfg_default(), root=worktree)
    assert "symlink_escape" in _codes(verdict)


def test_c6_repo_mismatch(worktree):
    verdict = gate.evaluate_changes([Change("tools/a.py", repo="TensorFEM")], cfg_default(),
                                    root=worktree, active_repo="TensorPlatform")
    assert "repo_mismatch" in _codes(verdict)
    ok = gate.evaluate_changes([Change("tools/a.py", repo="TensorPlatform")], cfg_default(),
                               root=worktree, active_repo="TensorPlatform")
    assert ok.verdict == "PASS"


def test_c7_foreign_root_glob(worktree):
    verdict = gate.evaluate_changes([Change("../m8000/backend/x.py")], cfg_default(), root=worktree)
    assert "foreign_root" in _codes(verdict)


def test_normal_in_repo_paths_pass_crossrepo(worktree):
    paths = ["tools/a.py", "backend/api/b.py", "docs/x/y.md", "vendor/other/file.txt"]
    verdict = gate.evaluate_changes([Change(p) for p in paths], cfg_default(), root=worktree,
                                    active_repo="TensorPlatform")
    assert verdict.verdict == "PASS", verdict.to_json()["violations"]


def test_boundary_violation_is_not_double_counted(worktree):
    """归因优先边界类：越界路径不因"白名单未命中"被重复计一次（审计口径）。"""
    verdict = gate.evaluate_changes([Change(".git/config")], cfg_whitelisted(), root=worktree)
    categories = [v.category for v in verdict.violations]
    assert "cross_repo" in categories and "whitelist" not in categories


# ─────────────────── 3. 凭证泄漏 ───────────────────
def _pkcs8_header() -> str:
    return "-----BEGIN " + "PRIVATE KEY" + "-----"


def _assigned_token() -> str:
    return 'API_KEY = "' + ("A1b2C3d4" * 3) + '"'


def _url_with_password() -> str:
    return "https://ci-runner:" + ("Zx9" * 4) + "@ci.example.org/run"


def _jwt() -> str:
    return "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkw" + "." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def _cloud_key() -> str:
    return "AKIA" + "Q7RTY2PLMNBVCXZ3"


def _entropy_blob() -> str:
    return "Xk9Qm2Lp7Rw4Tz8Vb3Nc6Hd1Jf5Gs0YaE3Ut5Ip1Ov7Bn9"


SECRET_SAMPLES = [
    ("private_key_block", _pkcs8_header()),
    ("credential_assignment", _assigned_token()),
    ("url_credentials", _url_with_password()),
    ("jwt_bearer", _jwt()),
    ("cloud_access_key_id", _cloud_key()),
    ("high_entropy_blob", _entropy_blob()),
]


@pytest.mark.parametrize("rule_id,text", SECRET_SAMPLES, ids=[r for r, _ in SECRET_SAMPLES])
def test_secret_patterns_are_caught(rule_id, text):
    verdict = gate.evaluate_changes([Change("tools/write_gate/sample.py", content=text)],
                                    cfg_default(), root=REPO_ROOT)
    hit_rules = [v.rule_id for v in verdict.violations if v.category == "secret"]
    assert rule_id in hit_rules, (rule_id, hit_rules)


@pytest.mark.parametrize("rule_id,text", SECRET_SAMPLES, ids=[r for r, _ in SECRET_SAMPLES])
def test_secret_values_are_redacted(rule_id, text):
    """门本身不得成为泄漏渠道：判决 JSON 里不能出现命中原文。"""
    verdict = gate.evaluate_changes([Change("tools/write_gate/sample.py", content=text)],
                                    cfg_default(), root=REPO_ROOT)
    blob = json.dumps(verdict.to_json(), ensure_ascii=False)
    core = text.split("=")[-1].strip().strip('"') if "=" in text else text
    assert core[:12] not in blob, f"{rule_id}: raw value leaked into verdict"
    for violation in verdict.violations:
        assert violation.excerpt and "*" in violation.excerpt


def test_secret_allow_and_entropy_gate_avoid_false_positives():
    cfg = cfg_default()
    safe = "\n".join(CORPUS["normal_lines"])
    hits, _ = secrets_mod.scan_text(safe, cfg)
    assert hits == [], [h.rule_id for h in hits]


def test_public_key_header_is_not_a_secret():
    verdict = gate.evaluate_changes(
        [Change("docs/write_gate/x.md", content="-----BEGIN PUBLIC KEY-----\nMIIBIjAN\n")],
        cfg_default(), root=REPO_ROOT)
    assert verdict.verdict == "PASS"


def test_binary_and_oversize_blobs_are_skipped_not_crashed(worktree):
    repo = make_repo(worktree / "binrepo")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    git(repo, "add", "blob.bin")
    verdict = gate.evaluate_staged(repo, cfg_default(), active_repo="TensorPlatform")
    assert verdict.verdict == "PASS"
    assert any(a["reason"] == "binary_skipped" for a in verdict.advisories)


# ─────────────────── 4. 误报率 ───────────────────
def test_false_positive_rate_within_budget():
    """预注册正常写入语料：122 路径 + 39 内容样本，拦截数必须为 0（≤1%）。"""
    cfg = cfg_default()
    samples = blocked = 0
    for path in CORPUS["normal_paths"]:
        samples += 1
        if gate.evaluate_changes([Change(path)], cfg, root=REPO_ROOT).verdict != "PASS":
            blocked += 1
    for line in CORPUS["normal_lines"]:
        samples += 1
        verdict = gate.evaluate_changes([Change("docs/write_gate/fp_sample.md", content=line)],
                                        cfg, root=REPO_ROOT)
        if verdict.verdict != "PASS":
            blocked += 1
    rate = blocked / samples
    assert samples >= 150
    assert rate <= 0.01, f"false positive rate {rate:.4f} ({blocked}/{samples})"


# ─────────────────── 5. fail-closed / 确定性 ───────────────────
def test_missing_root_is_fail_closed(tmp_path):
    verdict = gate.evaluate_changes([Change("tools/a.py")], cfg_default(), root=tmp_path / "nope")
    assert verdict.verdict == "FAIL" and verdict.decision == "unknown"
    assert _codes(verdict) == ["root_unreadable"]


def test_bad_policy_is_rejected():
    with pytest.raises(WriteGateError):
        policy_mod.load_policy(POLICY_PATH, overrides={"unknown_group": {}})
    with pytest.raises(WriteGateError):
        policy_mod.load_policy(POLICY_PATH, overrides={"whitelist": {"allow_globs": []}})
    with pytest.raises(WriteGateError):
        policy_mod.load_policy(REPO_ROOT / "data" / "write_gate" / "does_not_exist.json")


def test_verdict_is_deterministic_and_content_addressed(worktree):
    changes = [Change("tools/write_gate/gate.py"),
               Change("tools/write_gate/sample.py", content=_assigned_token()),
               Change(".git/config")]
    cfg = cfg_whitelisted()
    runs = [json.dumps(gate.evaluate_changes(changes, cfg, root=worktree).to_json(),
                       ensure_ascii=False, sort_keys=True) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    other = json.dumps(gate.evaluate_changes([Change("tools/write_gate/other.py")], cfg,
                                             root=worktree).to_json(), ensure_ascii=False)
    assert json.loads(other)["digest"] != json.loads(runs[0])["digest"]


def test_policy_digest_changes_with_policy():
    a = gate.evaluate_changes([], cfg_default(), root=REPO_ROOT).to_json()["policy"]["sha256"]
    b = gate.evaluate_changes([], cfg_whitelisted(), root=REPO_ROOT).to_json()["policy"]["sha256"]
    assert a != b


# ─────────────────── 6. 暂存区集成 ───────────────────
def test_staged_gate_blocks_secret_and_keeps_index_intact(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "notes.md").write_text("normal line\n", encoding="utf-8")
    git(repo, "add", "notes.md")
    clean = gate.evaluate_staged(repo, cfg_default(), active_repo="TensorPlatform")
    assert clean.verdict == "PASS", clean.to_json()["violations"]

    (repo / "leaky.py").write_text(_assigned_token() + "\n", encoding="utf-8")
    git(repo, "add", "leaky.py")
    before = git(repo, "diff", "--cached", "--name-only")
    verdict = gate.evaluate_staged(repo, cfg_default(), active_repo="TensorPlatform")
    after = git(repo, "diff", "--cached", "--name-only")
    assert verdict.verdict == "FAIL"
    assert "credential_assignment" in [v.rule_id for v in verdict.violations]
    assert before == after, "write_gate must not mutate the index"


def test_staged_gate_blocks_out_of_whitelist_in_real_repo(tmp_path):
    repo = make_repo(tmp_path / "repo2")
    (repo / "backend").mkdir()
    (repo / "backend" / "leak.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "backend/leak.py")
    verdict = gate.evaluate_staged(repo, cfg_whitelisted(), active_repo="TensorPlatform")
    assert verdict.verdict == "FAIL"
    assert "whitelist" in _categories(verdict)


# ─────────── 6a. 可判性筛选：UTF-8 截断不得把文本判成二进制 ───────────
def test_looks_binary_tolerates_truncated_utf8_tail():
    """8KB 切片正好切在多字节字符中间时，不得判成二进制（否则凭证扫描静默跳过）。"""
    blob = "中".encode() * 3000            # 9000 字节：8192 落在第二个字节中间
    assert len(blob) > 8192
    assert secrets_mod.looks_binary(blob) is False


def test_looks_binary_still_catches_invalid_sequences():
    assert secrets_mod.looks_binary(b"\xff\xfe not utf-8") is True
    assert secrets_mod.looks_binary(b"ok\x00binary") is True


def test_cjk_blob_with_secret_beyond_8k_is_scanned(tmp_path):
    """中文文件 + 8KB 之后的凭证：修复前整份跳过（binary_skipped）⇒ 漏判。"""
    repo = make_repo(tmp_path / "cjk_repo")
    filler = "# " + "中文注释填充\n" * 900          # 远超 8192 字节，且切点落在字内
    (repo / "notes.py").write_text(filler + _assigned_token() + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    verdict = gate.evaluate_staged(repo, cfg_default(), active_repo="TensorPlatform")
    payload = verdict.to_json()
    assert payload["verdict"] == "FAIL", payload["advisories"]
    assert "credential_assignment" in [v["rule_id"] for v in payload["violations"]
                                       if v["category"] == "secret"]
    assert payload["advisories"] == [], "文本文件不得再出现 binary_skipped"


# ─────────── 6b. 破坏性写入：越界删除与改名源 ───────────
def _destructive_repo(tmp_path: Path, name: str = "delrepo") -> Path:
    """真 git 仓：白名单内一个文件、白名单外一个文件、只读区一个文件（均已提交）。"""
    repo = make_repo(tmp_path / name)
    (repo / "tools" / "write_gate").mkdir(parents=True)
    (repo / "backend").mkdir()
    (repo / "docs" / "spec").mkdir(parents=True)
    (repo / "tools" / "write_gate" / "mod.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "backend" / "api.py").write_text("B = 2\n", encoding="utf-8")
    (repo / "docs" / "spec" / "guide.md").write_text("# guide\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_parse_name_status_expands_rename_source_as_delete():
    """``R`` 是「目标新增 + 源删除」两件事：源路径不入列 ⇒ ``git mv`` 成了越界暗道。"""
    changes = gate.parse_name_status_z(b"R100\0backend/api.py\0tools/write_gate/api.py\0")
    assert [(c.path, c.kind) for c in changes] == [
        ("backend/api.py", "DELETE"), ("tools/write_gate/api.py", "MODIFY")]


def test_parse_name_status_copy_keeps_source_intact():
    changes = gate.parse_name_status_z(b"C100\0a.py\0b.py\0")
    assert [(c.path, c.kind) for c in changes] == [("b.py", "ADD")]


def test_staged_delete_outside_whitelist_is_blocked(tmp_path):
    repo = _destructive_repo(tmp_path)
    (repo / "backend" / "api.py").unlink()
    git(repo, "add", "-A")
    verdict = gate.evaluate_staged(repo, cfg_whitelisted(), active_repo="TensorPlatform")
    payload = verdict.to_json()
    assert payload["verdict"] == "FAIL", payload
    assert payload["decision"] == "block"
    assert payload["meta"]["changes"] == 1, "删除事件必须入列，不能是 changes=0"
    assert [v["path"] for v in payload["violations"]] == ["backend/api.py"]
    assert "whitelist" in _categories(verdict)


def test_staged_delete_inside_whitelist_allowed(tmp_path):
    repo = _destructive_repo(tmp_path, "delrepo2")
    (repo / "tools" / "write_gate" / "mod.py").unlink()
    git(repo, "add", "-A")
    verdict = gate.evaluate_staged(repo, cfg_whitelisted(), active_repo="TensorPlatform")
    assert verdict.verdict == "PASS", verdict.to_json()["violations"]
    assert [(f["path"], f["kind"]) for f in verdict.files] == [
        ("tools/write_gate/mod.py", "DELETE")]


def test_rename_source_outside_whitelist_is_blocked(tmp_path):
    repo = _destructive_repo(tmp_path, "delrepo3")
    git(repo, "mv", "backend/api.py", "tools/write_gate/api.py")
    verdict = gate.evaluate_staged(repo, cfg_whitelisted(), active_repo="TensorPlatform")
    payload = verdict.to_json()
    assert payload["verdict"] == "FAIL", payload
    assert [v["path"] for v in payload["violations"]] == ["backend/api.py"]
    statuses = {f["path"]: f["status"] for f in payload["files"]}
    assert statuses == {"backend/api.py": "block", "tools/write_gate/api.py": "allow"}


def test_rename_inside_whitelist_allowed(tmp_path):
    repo = _destructive_repo(tmp_path, "delrepo4")
    git(repo, "mv", "tools/write_gate/mod.py", "tools/write_gate/mod2.py")
    verdict = gate.evaluate_staged(repo, cfg_whitelisted(), active_repo="TensorPlatform")
    assert verdict.verdict == "PASS", verdict.to_json()["violations"]


def test_staged_delete_in_read_only_zone_is_blocked(tmp_path):
    repo = _destructive_repo(tmp_path, "delrepo5")
    (repo / "docs" / "spec" / "guide.md").unlink()
    git(repo, "add", "-A")
    cfg = policy_mod.load_policy(
        POLICY_PATH, overrides={"whitelist": {"allow_globs": EDIT_WHITELIST,
                                              "read_only_globs": ["docs/**"]}})
    verdict = gate.evaluate_staged(repo, cfg, active_repo="TensorPlatform")
    payload = verdict.to_json()
    assert payload["verdict"] == "FAIL"
    assert [v["code"] for v in payload["violations"]] == ["read_only_glob"]


def test_staged_delete_skips_content_scan(tmp_path):
    """删除事件无 blob 可扫：不得因「读不到索引 blob」升级为拦截或告警。"""
    repo = make_repo(tmp_path / "delrepo6")
    (repo / "leaky.py").write_text(_assigned_token() + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed secret")
    (repo / "leaky.py").unlink()
    git(repo, "add", "-A")
    verdict = gate.evaluate_staged(repo, cfg_default(), active_repo="TensorPlatform")
    payload = verdict.to_json()
    assert payload["verdict"] == "PASS", payload["violations"]
    assert payload["advisories"] == []


def test_staged_delete_does_not_mutate_index_and_is_deterministic(tmp_path):
    repo = _destructive_repo(tmp_path, "delrepo7")
    (repo / "backend" / "api.py").unlink()
    git(repo, "add", "-A")
    before = git(repo, "diff", "--cached", "--name-status")
    first = gate.evaluate_staged(repo, cfg_whitelisted()).to_json()
    second = gate.evaluate_staged(repo, cfg_whitelisted()).to_json()
    after = git(repo, "diff", "--cached", "--name-status")
    assert before == after, "write_gate must not mutate the index"
    assert first == second


def test_clean_verdict_reports_allow_decision(worktree):
    verdict = gate.evaluate_paths(["tools/write_gate/x.py"], cfg_whitelisted(), root=worktree)
    payload = verdict.to_json()
    assert payload["verdict"] == "PASS" and payload["decision"] == "allow"


# ─────────────────── 7. CLI ───────────────────
def _run_cli(args, cwd=REPO_ROOT):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run([sys.executable, "-m", "tools.write_gate.cli", *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)
    return proc


def test_cli_exit_codes(tmp_path):
    inside = _run_cli(["--root", str(REPO_ROOT), "--edit-whitelist", "tools/write_gate/**",
                       "--quiet", "paths", "tools/write_gate/gate.py"])
    assert inside.returncode == 0, inside.stderr

    outside = _run_cli(["--root", str(REPO_ROOT), "--edit-whitelist", "tools/write_gate/**",
                        "--quiet", "paths", "backend/api/x.py"])
    assert outside.returncode == 2

    json_out = tmp_path / "verdict.json"
    blocked = _run_cli(["--root", str(REPO_ROOT), "--edit-whitelist", "tools/write_gate/**",
                        "--json", str(json_out), "--quiet", "paths", "../escape.py"])
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert blocked.returncode == 2 and payload["verdict"] == "FAIL"
    assert payload["schema"] == "tp.write_gate.verdict/v2"
    assert payload["digest"]

    missing = _run_cli(["--policy", str(tmp_path / "nope.json"), "paths", "a.py"])
    assert missing.returncode == 3


def test_cli_json_mode_with_content(tmp_path):
    payload = {"changes": [{"path": "tools/write_gate/x.py", "content": _url_with_password()}]}
    src = tmp_path / "changes.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    proc = _run_cli(["--root", str(REPO_ROOT), "json", str(src)])
    assert proc.returncode == 2
    assert "url_credentials" in proc.stdout


# ─────────────────── 8. 零领域语义 ───────────────────
def _domain_terms() -> list:
    data = json.loads((REPO_ROOT / "tools" / "tenancy_gate" / "domain_denylist.json")
                      .read_text(encoding="utf-8"))
    return list(data["terms"])


@pytest.mark.parametrize("target", ["package", "data"])
def test_carries_no_domain_terms(target):
    """包源码与策略/语料数据不得出现预注册域词条（词表本身在别处，不在本节点）。"""
    terms = _domain_terms()
    if target == "package":
        files = sorted(PKG_DIR.glob("*.py"))
    else:
        files = sorted((REPO_ROOT / "data" / "write_gate").glob("*.json"))
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for term in terms:
            pattern = (r"(?<![0-9A-Za-z])" + re.escape(term) + r"(?![0-9A-Za-z])"
                       if term.isascii() else re.escape(term))
            if re.search(pattern, text, flags=re.IGNORECASE if term.isascii() else 0):
                hits.append(f"{path.name}:{term}")
    assert hits == [], hits