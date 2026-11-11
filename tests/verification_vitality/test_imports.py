"""AST 静态导入解析的单元测试：三类符号迁移 + 三类不应误报的写法。"""
from __future__ import annotations

from pathlib import Path

from verification_vitality.imports import local_roots, scan_file, scan_tree

from conftest import make_repo


def _findings(repo: Path, rel: str):
    roots = local_roots(repo)
    return scan_file(repo / rel, roots, rel_to=repo)


def test_module_rename_is_dead(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/geo.py": "def f():\n    return 1\n",
                         "tests/test_a.py": "from pkg.old_geo import f\n\n\ndef test_a():\n    assert f() == 1\n"})
    findings = _findings(repo, "tests/test_a.py")
    assert [f.kind for f in findings] == ["module_missing"]
    assert findings[0].lethal is True
    assert "old_geo" in findings[0].module


def test_symbol_rename_is_dead(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/geo.py": "def new_f():\n    return 1\n",
                         "tests/test_a.py": "from pkg.geo import old_f\n\n\ndef test_a():\n    assert old_f() == 1\n"})
    findings = _findings(repo, "tests/test_a.py")
    assert [f.kind for f in findings] == ["symbol_missing"]
    assert findings[0].name == "old_f"
    assert findings[0].lethal is True


def test_reexport_removed_is_dead(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/shape.py": "class Volume:\n    pass\n",
                         "pkg/geo.py": "from pkg.shape import Volume as _V\n",
                         "tests/test_a.py": "from pkg.geo import Volume\n\n\ndef test_a():\n    assert Volume\n"})
    findings = _findings(repo, "tests/test_a.py")
    assert [f.kind for f in findings] == ["symbol_missing"]
    assert findings[0].name == "Volume"


def test_all_contract_makes_symbol_importable(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/geo.py": "from pkg.shape import Volume\n\n__all__ = ['Volume']\n",
                         "pkg/shape.py": "class Volume:\n    pass\n",
                         "tests/test_a.py": "from pkg.geo import Volume\n\n\ndef test_a():\n    assert Volume\n"})
    assert _findings(repo, "tests/test_a.py") == []


def test_external_dependency_not_judged(repo_factory):
    repo = repo_factory({"tests/test_a.py": "import numpy\nfrom numpy.linalg import norm\n"
                                          "from totally_installed_pkg import thing\n\n\ndef test_a():\n    assert 1\n"})
    assert _findings(repo, "tests/test_a.py") == []


def test_optional_import_is_not_lethal(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "tests/test_a.py": ("try:\n    from pkg.optional_backend import fast\n"
                                             "except ImportError:\n    fast = None\n\n\n"
                                             "def test_a():\n    assert True\n")})
    findings = _findings(repo, "tests/test_a.py")
    assert [f.kind for f in findings] == ["module_missing"]
    assert findings[0].guarded is True
    assert findings[0].lethal is False, "可选依赖缺失不得判死验证面"


def test_type_checking_import_is_not_lethal(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "tests/test_a.py": ("from typing import TYPE_CHECKING\n"
                                             "if TYPE_CHECKING:\n"
                                             "    from pkg.types_only import Proto\n\n\n"
                                             "def test_a():\n    assert True\n")})
    findings = _findings(repo, "tests/test_a.py")
    assert findings and findings[0].type_checking is True
    assert findings[0].lethal is False


def test_star_import_skips_symbol_check(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/geo.py": "from pkg.shape import *\n",
                         "pkg/shape.py": "class A:\n    pass\n",
                         "tests/test_a.py": "from pkg.geo import NotDefinedAnywhere\n\n\ndef test_a():\n    assert True\n"})
    assert _findings(repo, "tests/test_a.py") == []


def test_relative_import_resolution(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/sub/__init__.py": "",
                         "pkg/sub/helper.py": "def h():\n    return 1\n",
                         "pkg/sub/use.py": "from .helper import h\n\n\ndef run():\n    return h()\n"})
    assert scan_file(repo / "pkg/sub/use.py", local_roots(repo), rel_to=repo) == []
    repo2 = repo_factory({"pkg/__init__.py": "",
                          "pkg/sub/__init__.py": "",
                          "pkg/sub/use2.py": "from .renamed_helper import h\n"},
                         name="repo2")
    findings = scan_file(repo2 / "pkg/sub/use2.py", local_roots(repo2), rel_to=repo2)
    assert [f.kind for f in findings] == ["module_missing"]


def test_scan_tree_returns_relative_keys(repo_factory):
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/geo.py": "def g():\n    return 1\n",
                         "tests/test_a.py": "from pkg.geo import gone\n"})
    out = scan_tree(repo, ["tests/test_a.py"], rel_to=repo)
    assert list(out) == ["tests/test_a.py"]
    assert out["tests/test_a.py"][0]["kind"] == "symbol_missing"

def test_from_pkg_import_submodule_is_not_dead(repo_factory):
    """``from pkg import submod`` 是合法的子模块导入——真仓库里踩到过的误报。"""
    body = "def run():\n    return True\n"
    repo = repo_factory({"pkg/__init__.py": "",
                         "pkg/ac_verify.py": body,
                         "pkg/sub/__init__.py": "",
                         "tests/test_a.py": ("from pkg import ac_verify\n"
                                             "from pkg import sub\n\n\n"
                                             "def test_a():\n    assert ac_verify.run()\n")})
    assert _findings(repo, "tests/test_a.py") == []


def test_sibling_conftest_import_is_not_dead(repo_factory):
    """pytest prepend 模式会把测试文件自身目录放进 sys.path：
    ``from conftest import helper`` 是合法的兄弟模块导入（真仓库踩到过的误报）。"""
    repo = repo_factory({"pkg/__init__.py": "",
                         "tests/conftest.py": "def helper():\n    return 1\n",
                         "tests/test_a.py": ("from conftest import helper\n\n\n"
                                             "def test_a():\n    assert helper() == 1\n")})
    assert _findings(repo, "tests/test_a.py") == []
