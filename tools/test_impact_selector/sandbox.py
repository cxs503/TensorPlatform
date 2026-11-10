"""Synthetic package tree used by the fail-closed matrix and the unit tests.

Zero domain semantics: every module here is a neutral placeholder (``alpha``,
``core``, ``util`` ...).  The tree is generated on the fly in ``tmp_path`` so the
tests never depend on repository contents that could change under them.

The tree deliberately contains the four shapes that break naive scanners:
relative imports, ``__init__`` re-export, a conditional (``try/except ImportError``)
import and a ``python -m`` style literal reference from a test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

FILES: Dict[str, str] = {
    "alpha/__init__.py": '"""Synthetic package root (re-exports)."""\n\nfrom .core import Engine\n\n__all__ = ["Engine"]\n',
    "alpha/core.py": '"""Core placeholder module."""\n\n\nclass Engine:\n    """Tiny deterministic engine used by the synthetic tests."""\n\n    def run(self, x: int = 1) -> int:\n        return x + 1\n',
    "alpha/util.py": '"""Utility placeholder module."""\n\n\ndef double(x: int) -> int:\n    return x * 2\n',
    "alpha/extra.py": '"""Optional dependency placeholder (conditional import)."""\n\ntry:  # optional acceleration path\n    from .util import double as _double  # noqa: F401\nexcept ImportError:  # pragma: no cover\n    _double = None\n\n\ndef quadruple(x: int) -> int:\n    return x * 4\n',
    "alpha/tests/__init__.py": "",
    "alpha/tests/test_core.py": 'from alpha.core import Engine\n\n\ndef test_engine_runs():\n    assert Engine().run(1) == 2\n',
    "alpha/tests/test_util.py": 'from alpha.util import double\n\n\ndef test_double():\n    assert double(3) == 6\n',
    "alpha/tests/test_extra.py": 'from alpha.extra import quadruple\n\n\ndef test_quadruple():\n    assert quadruple(2) == 8\n',
    "alpha/tests/test_cli_ref.py": (
        "import subprocess\nimport sys\n\n\n"
        "def test_module_is_importable():\n"
        '    out = subprocess.run([sys.executable, "-c", "import alpha.util"], capture_output=True)\n'
        "    assert out.returncode == 0\n"
    ),
    "tests/__init__.py": "",
    "tests/test_smoke_all.py": (
        "from alpha.core import Engine\nfrom alpha.extra import quadruple\nfrom alpha.util import double\n\n\n"
        "def test_smoke():\n    assert Engine().run(0) == 1 and double(1) == 2 and quadruple(1) == 4\n"
    ),
    "beta/__init__.py": "",
    "beta/calc.py": '"""Second placeholder package (keeps the tree wide)."""\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n',
    "beta/tests/__init__.py": "",
    "docs/guide.md": "# synthetic guide\n\nNothing to see here.\n",
}



FILES["gamma/__init__.py"] = ""
FILES["gamma/ops.py"] = '"""Third placeholder package."""\n\n\ndef tri(x: int) -> int:\n    return x * 3\n'
FILES["gamma/tests/__init__.py"] = ""

for _i in range(10):
    FILES[f"gamma/tests/test_ops_{_i}.py"] = (
        "from gamma.ops import tri\n\n\n"
        f"def test_tri_{_i}():\n    assert tri({_i}) == {_i} * 3\n"
    )

for _i in range(8):
    FILES[f"beta/tests/test_calc_{_i}.py"] = (
        "from beta.calc import add\n\n\n"
        f"def test_add_{_i}():\n    assert add({_i}, 1) == {_i} + 1\n"
    )

def make_clean_tree(root: Path | str) -> Path:
    """Materialise the synthetic tree (no fail-closed triggers)."""
    root = Path(root)
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def copy_tree(src: Path | str, dst: Path | str) -> Path:
    """Cheap deterministic copy of a small synthetic tree."""
    import shutil

    dst = Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(Path(src), dst)
    return dst


def touch(root: Path | str, rel: str, content: str = '"""Placeholder."""\n') -> Path:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def git_env() -> dict:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "tis",
        "GIT_AUTHOR_EMAIL": "tis@example.invalid",
        "GIT_COMMITTER_NAME": "tis",
        "GIT_COMMITTER_EMAIL": "tis@example.invalid",
    }


def git_commit(root: Path | str, message: str) -> None:
    """Commit everything in a throwaway repo (deterministic identity)."""
    import subprocess

    root = Path(root)
    env = git_env()
    subprocess.run(["git", "add", "-A"], cwd=str(root), env=env, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", message, "--no-verify"], cwd=str(root), env=env, check=True, capture_output=True
    )


def git_init(root: Path | str) -> None:
    """Initialise a throwaway git repo (for history dependent tests)."""
    import subprocess

    root = Path(root)
    env = git_env()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "init", "--no-verify"],
    ):
        subprocess.run(cmd, cwd=str(root), env=env, check=True, capture_output=True)