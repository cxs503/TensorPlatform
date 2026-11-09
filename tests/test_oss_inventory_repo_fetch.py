"""Tests for the domain-neutral OSS repository fetch helper.

All tests are offline: a throwaway local git repository stands in for an
upstream project, and REST behaviour is exercised through monkeypatching.
"""
import json
import subprocess

import pytest

from tools.oss_inventory import repo_fetch as rf


def _make_local_repo(tmp_path):
    """Create a tiny local repository with two dirs; no network involved."""
    root = tmp_path / "upstream"
    (root / "src" / "shared").mkdir(parents=True)
    (root / "src" / "shared" / "alpha.h").write_text("// alpha\nclass Alpha {};\n")
    (root / "src" / "shared" / "beta.hpp").write_text("// beta\n")
    (root / "tests").mkdir()
    (root / "tests" / "case.cpp").write_text("int main() { return 0; }\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                 "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    return root


def test_parse_repo_accepts_plain_slug():
    assert rf.parse_repo("acme/widget") == ("acme", "widget")
    assert rf.parse_repo("/acme/widget/") == ("acme", "widget")


@pytest.mark.parametrize("bad", ["", "widget", "acme/widget/extra", "--upload-pack=x/y", "acme/../widget"])
def test_parse_repo_rejects_injection_and_malformed(bad):
    with pytest.raises(rf.RepoFetchError):
        rf.parse_repo(bad)


def test_list_paths_is_offline_and_complete(tmp_path):
    repo = _make_local_repo(tmp_path)
    paths = rf.list_paths(repo, "HEAD")
    assert "src/shared/alpha.h" in paths
    assert "tests/case.cpp" in paths
    assert len(paths) == 3


def test_checkout_paths_materialises_requested_subset(tmp_path):
    repo = _make_local_repo(tmp_path)
    # Simulate a no-checkout clone: drop the working tree, keep the object db.
    subprocess.run(["git", "rm", "-rq", "--cached", "--ignore-unmatch", "."], cwd=repo,
                   check=True, capture_output=True)
    for f in ("src/shared/alpha.h", "src/shared/beta.hpp", "tests/case.cpp"):
        (repo / f).unlink()
    got = rf.checkout_paths(repo, ["src/shared", "tests/case.cpp", "does/not/exist"], "HEAD")
    assert (repo / "src/shared/alpha.h").is_file()
    assert (repo / "tests/case.cpp").is_file()
    assert "does/not/exist" not in got
    assert set(got) == {"src/shared", "tests/case.cpp"}


def test_manifest_hashes_and_skips_missing(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    rows = rf.manifest(tmp_path, ["a.txt", "missing.txt"])
    assert [r["path"] for r in rows] == ["a.txt"]
    assert rows[0]["bytes"] == 5
    assert rows[0]["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_api_403_becomes_rate_limit_error(monkeypatch):
    import urllib.error

    def boom(*_a, **_k):
        raise urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None)

    monkeypatch.setattr(rf.urllib.request, "urlopen", boom)
    with pytest.raises(rf.RateLimitError) as exc:
        rf.api_repo_meta("acme/widget")
    assert exc.value.remaining == 0


def test_auto_strategy_falls_back_to_api(tmp_path, monkeypatch, capsys):
    payload = {"type": "file", "content": "YWxwaGE="}  # base64 "alpha"

    monkeypatch.setattr(rf, "clone_blobless", lambda *a, **k: (_ for _ in ()).throw(rf.RepoFetchError("no git")))
    monkeypatch.setattr(rf, "api_repo_meta", lambda repo: {"default_branch": "main"})
    monkeypatch.setattr(rf, "_request", lambda url, accept=None: json.dumps(payload).encode())
    paths = tmp_path / "paths.txt"
    paths.write_text("src/shared/alpha.h\n")
    code = rf.main(["--repo", "acme/widget", "--stage", str(tmp_path / "stage"),
                    "--paths", str(paths)])
    assert code == 0
    assert (tmp_path / "stage" / "api" / "src/shared/alpha.h").read_bytes() == b"alpha"
    assert "falling back to REST" in capsys.readouterr().err


def test_cli_requires_paths_without_list(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "api_repo_meta", lambda repo: {"default_branch": "main"})
    with pytest.raises(SystemExit):
        rf.main(["--repo", "acme/widget", "--stage", str(tmp_path)])