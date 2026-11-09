"""Repo-level pytest config: register the reproducible-digest fixture plugin.

The plugin exposes ``assert_reproducible`` / ``repro_gate_k`` / ``ulp_protocol``
so that any digest-producing benchmark in this repo can assert the cross-ISA
reproducibility protocol in one line (see docs/REPRODUCIBILITY_CROSS_ISA.md).
"""

pytest_plugins = ["tools.repro_digest.pytest_plugin"]

# 让 tests/ 下的共享夹具模块可按顶层模块名导入（避开环境里同名的 `tests` 包）
import os as _os  # noqa: E402
import sys as _sys  # noqa: E402

_TESTS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _TESTS_DIR not in _sys.path:
    _sys.path.insert(0, _TESTS_DIR)
