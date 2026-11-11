"""收集器测试：一次进程拿到全幅收集错误（而不是被首错中断）、错误归类、通过率。"""
from __future__ import annotations

from pathlib import Path

from verification_vitality.collector import (classify_error, run_pytest_collect,
                                             run_pytest_pass_rate)


def test_classify_error_kinds():
    assert classify_error("ModuleNotFoundError: No module named 'pkg'") == "import_error"
    assert classify_error("ImportError: cannot import name 'x' from 'pkg'") == "import_error"
    assert classify_error("FileNotFoundError: [Errno 2] No such file or directory") == "data_file_missing"
    assert classify_error("fixture 'tmp_home' not found") == "fixture_error"
    assert classify_error("SyntaxError: invalid syntax") == "syntax_error"
    assert classify_error("something else") == "collection_error"


def test_collect_reports_all_errors_not_just_first(repo_factory):
    """`--continue-on-collection-errors` 的语义保证：三个坏文件三个错误都要被看见。"""
    files = {
        "pkg/__init__.py": "",
        "pkg/geo.py": "def f():\n    return 1\n",
        "tests/test_ok.py": "def test_ok():\n    assert True\n",
        "tests/test_bad1.py": "from pkg.missing_one import f\n",
        "tests/test_bad2.py": "from pkg.missing_two import f\n",
        "tests/test_bad3.py": "from pkg.geo import absent\n",
    }
    repo = repo_factory(files)
    res = run_pytest_collect(repo, ["tests"], timeout=90)
    assert res.exit_code != 0
    assert not res.ok
    files_with_errors = {e.file for e in res.errors}
    assert {"tests/test_bad1.py", "tests/test_bad2.py", "tests/test_bad3.py"} <= files_with_errors
    assert res.collected.get("tests/test_ok.py", 0) == 1
    assert res.collected_total >= 1
    assert res.error_for("tests/test_bad3.py").error_kind == "import_error"


def test_collect_clean_repo(healthy_repo):
    res = run_pytest_collect(healthy_repo, ["tests"], timeout=90)
    assert res.ok and res.exit_code == 0
    assert res.collected_total == 3
    assert res.errors == []


def test_pass_rate_is_measured_not_inferred(repo_factory):
    files = {"tests/test_mix.py": ("def test_a():\n    assert True\n"
                                   "def test_b():\n    assert True\n"
                                   "def test_c():\n    assert False\n")}
    repo = repo_factory(files)
    res = run_pytest_pass_rate(repo, ["tests"], timeout=90)
    assert (res.passed, res.failed, res.executed) == (2, 1, 3)
    assert abs(res.pass_rate - 2 / 3) < 1e-9


def test_pass_rate_none_when_nothing_ran(repo_factory):
    repo = repo_factory({"tests/test_broken.py": "from does_not_exist import x\n"})
    res = run_pytest_pass_rate(repo, ["tests"], timeout=90)
    assert res.pass_rate is None, "没跑起来不得给出通过率（否则会冒充执行证据）"
    assert res.errors >= 1