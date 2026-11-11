"""运行时探针 — pytest 套件（含零领域语义自检）。

每个探针都要证明三件事：正例通过、反例被拒、环境缺失时如实降级。
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 - 夹具要造一个真 git 仓库
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.domain_leak_scan import compile_patterns, load_denylist, scan_text
from tools.evidence_automation.probes import (compose_service_inventory,
                                             git_plumbing, hull_geometry_identity,
                                             nginx_config_inventory, run_probe)

NEW_CODE_FILES = [
    "tools/evidence_automation/__init__.py",
    "tools/evidence_automation/mapping.py",
    "tools/evidence_automation/probes.py",
    "tools/evidence_automation/runner.py",
    "tools/evidence_automation/grader.py",
    "tools/evidence_automation/acceptance.py",
    "tools/evidence_automation/consistency.py",
    "tools/evidence_automation/collect.py",
    "tools/evidence_automation/cli.py",
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # nosec B603,B607
        ["git", "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", *args],
        cwd=str(repo), check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def test_git_plumbing_ok_on_real_repository(git_repo: Path):
    result = git_plumbing(git_repo)
    assert result.available and result.ok
    assert result.measured["head_sha_len"] == 40
    assert result.measured["head_matches_show"] is True


def test_git_plumbing_degrades_outside_repository(tmp_path: Path):
    result = git_plumbing(tmp_path)
    assert result.available is False
    assert result.ok is False
    assert "git_unavailable" in result.detail


def test_compose_inventory_accepts_valid_topology(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: example/api:1\n  worker:\n    build: .\n",
        encoding="utf-8")
    result = compose_service_inventory(tmp_path)
    assert result.ok, result.measured
    assert result.measured["services_with_image_or_build"] == 2


def test_compose_inventory_rejects_malformed_service_spec(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: example/api:1\n  broken: 42\n", encoding="utf-8")
    result = compose_service_inventory(tmp_path)
    assert result.available is True
    assert result.ok is False
    assert any("spec_not_mapping" in p for p in result.measured["problems"])


def test_nginx_inventory_accepts_structural_config(tmp_path: Path):
    conf = tmp_path / "site.conf"
    conf.write_text(
        "server {\n  listen 80;\n  location /api/ { proxy_pass http://api:8000; }\n}\n",
        encoding="utf-8")
    result = nginx_config_inventory(tmp_path, files=("site.conf",))
    assert result.ok, result.measured
    assert result.measured["per_file"]["site.conf"]["server_blocks"] == 1


def test_nginx_inventory_flags_unbalanced_braces(tmp_path: Path):
    (tmp_path / "bad.conf").write_text("server {\n  listen 80;\n", encoding="utf-8")
    result = nginx_config_inventory(tmp_path, files=("bad.conf",))
    assert result.ok is False
    assert any("brace" in p for p in result.measured["problems"])


def test_hull_geometry_probe_reports_analytic_identity_and_negative_control():
    """解析恒等式≈机器精度、扰动输入被拒（防"永远返回 True"的空壳）。"""
    result = hull_geometry_identity(ROOT)
    assert result.available and result.ok, result.detail
    assert result.criterion_kind == "analytic_identity"
    assert result.measured["gz_rel_error_max"] <= 2e-2
    assert max(result.measured["cb_rel_error"].values()) <= 1e-6
    assert result.measured["perturbed_cb_rejected"] is True


def test_unknown_probe_name_fails_fast():
    with pytest.raises(KeyError):
        run_probe("no_such_probe", ROOT)


def test_new_modules_have_zero_domain_semantics():
    """C9：新代码里不得出现域词（域词表当数据，扫描器复用平台既有实现）。"""
    patterns = compile_patterns(load_denylist())
    offenders = {}
    for rel in NEW_CODE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        hits = scan_text(text, patterns)
        if hits:
            offenders[rel] = hits[:3]
    assert offenders == {}, f"域词渗漏: {json.dumps(offenders, ensure_ascii=False)}"