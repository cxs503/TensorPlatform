"""Fixtures for the regression-guard self-tests.

Everything here is a *synthetic* platform: package names, capability ids and
task ids carry no domain vocabulary (TensorPlatform iron law).  The workspace
is a real directory tree with real pytest files so the guard can be exercised
end to end through an actual ``pytest`` subprocess.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.regression_guard.guard import GuardConfig, RegressionGuard  # noqa: E402
from tools.regression_guard.model import TaskLedger  # noqa: E402
from tools.regression_guard.scanner import RepoGraph  # noqa: E402

SOLVER_KERNEL = '''"""Synthetic solver kernel (guarded artifact: kind=solver_core)."""

SCHEME_VERSION = 2


def relax(state: int) -> int:
    return state * 2


def damping(rate: float) -> float:
    return 0.5 * rate
'''

PLATFORM_API = '''"""Synthetic platform API module (guarded artifact: kind=platform_api)."""

API_VERSION = 1


def endpoint_probe() -> dict:
    return {"status": "ok", "version": API_VERSION}


def endpoint_reject() -> dict:
    return {"status": "rejected"}
'''

ORPHAN = '''"""Nothing imports this module: negative control for the guard."""


def unused() -> int:
    return 42
'''

HELPER = '''"""Untracked module (no ledger artifact) with two partial importers."""


def fast_path(x: int) -> int:
    return x + 1


def slow_path(x: int) -> int:
    return x - 1
'''

CONFTEST = '''import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
'''

TEST_CORE_A = '''from pkgs.solver.kernel import SCHEME_VERSION


def test_scheme_version():
    assert SCHEME_VERSION == 2
'''

TEST_CORE_B = '''from pkgs.solver.kernel import relax


def test_relax_doubles():
    assert relax(3) == 6
'''

TEST_CORE_C = '''from pkgs.solver.kernel import damping


def test_damping_halves():
    assert damping(2.0) == 1.0
'''

TEST_API_A = '''from pkgs.platform.api import endpoint_probe


def test_endpoint_probe_ok():
    assert endpoint_probe()["status"] == "ok"
'''

TEST_API_B = '''from pkgs.platform.api import endpoint_reject


def test_endpoint_reject():
    assert endpoint_reject()["status"] == "rejected"
'''

TEST_API_C = '''from pkgs.platform.api import API_VERSION


def test_api_version():
    assert API_VERSION == 1
'''

TEST_EXTRA_FAST = '''from pkgs.extra.helper import fast_path


def test_fast():
    assert fast_path(1) == 2
'''

TEST_EXTRA_SLOW = '''from pkgs.extra.helper import slow_path


def test_slow():
    assert slow_path(1) == 0
'''

LEDGER = {
    "schema_version": "1.0",
    "artifacts": [
        {"artifact_id": "core.kernel", "kind": "solver_core", "paths": ["pkgs/solver/kernel.py"],
         "symbols": ["SCHEME_VERSION", "relax", "damping"]},
        {"artifact_id": "surface.api", "kind": "platform_api", "paths": ["pkgs/platform/api.py"],
         "symbols": ["API_VERSION", "endpoint_probe", "endpoint_reject"]},
    ],
    "features": [
        {"feature_id": "feat.solver_core", "artifacts": ["core.kernel"]},
        {"feature_id": "feat.surface", "artifacts": ["surface.api"]},
        {"feature_id": "feat.composite", "artifacts": [], "depends_on": ["feat.solver_core"]},
    ],
    "tasks": [
        {"task_id": "T-core-a", "feature_ids": ["feat.solver_core"], "tests": ["tests/test_core_a.py"],
         "campaign_node": "n-core"},
        {"task_id": "T-core-b", "feature_ids": ["feat.composite"], "tests": ["tests/test_core_b.py"],
         "campaign_node": "n-core"},
        {"task_id": "T-core-c", "feature_ids": ["feat.solver_core"], "tests": ["tests/test_core_c.py"],
         "campaign_node": "n-core"},
        {"task_id": "T-api-a", "feature_ids": ["feat.surface"], "tests": ["tests/test_api_a.py"],
         "campaign_node": "n-surface"},
        {"task_id": "T-api-b", "feature_ids": ["feat.surface"], "tests": ["tests/test_api_b.py"],
         "campaign_node": "n-surface"},
        {"task_id": "T-api-c", "feature_ids": ["feat.surface"], "tests": ["tests/test_api_c.py"],
         "campaign_node": "n-surface"},
    ],
    "nodes": [
        {"node_id": "n-core", "depends_on": []},
        {"node_id": "n-surface", "depends_on": []},
        {"node_id": "n-integration", "depends_on": ["n-core", "n-surface"]},
        {"node_id": "n-report", "depends_on": ["n-integration"]},
    ],
}

CORE_TESTS = ["tests/test_core_a.py", "tests/test_core_b.py", "tests/test_core_c.py"]
API_TESTS = ["tests/test_api_a.py", "tests/test_api_b.py", "tests/test_api_c.py"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(scope="session")
def workspace(tmp_path_factory) -> Path:
    """A complete synthetic mini-repo with solver kernel, API module and tests."""
    ws = tmp_path_factory.mktemp("rguard_ws")
    _write(ws / "pkgs/solver/kernel.py", SOLVER_KERNEL)
    _write(ws / "pkgs/platform/api.py", PLATFORM_API)
    _write(ws / "pkgs/isolated/orphan.py", ORPHAN)
    _write(ws / "pkgs/extra/helper.py", HELPER)
    _write(ws / "tests/test_extra_fast.py", TEST_EXTRA_FAST)
    _write(ws / "tests/test_extra_slow.py", TEST_EXTRA_SLOW)
    _write(ws / "docs/notes.md", "# notes\n")
    _write(ws / "tests/conftest.py", CONFTEST)
    _write(ws / "tests/test_core_a.py", TEST_CORE_A)
    _write(ws / "tests/test_core_b.py", TEST_CORE_B)
    _write(ws / "tests/test_core_c.py", TEST_CORE_C)
    _write(ws / "tests/test_api_a.py", TEST_API_A)
    _write(ws / "tests/test_api_b.py", TEST_API_B)
    _write(ws / "tests/test_api_c.py", TEST_API_C)
    (ws / "ledger.json").write_text(json.dumps(LEDGER, indent=2), encoding="utf-8")
    return ws


@pytest.fixture(scope="session")
def graph(workspace: Path) -> RepoGraph:
    return RepoGraph.build(workspace, ("pkgs", "tests"))


@pytest.fixture()
def ledger() -> TaskLedger:
    return TaskLedger.from_dict(LEDGER)


@pytest.fixture()
def guard_factory(workspace: Path, graph: RepoGraph, ledger: TaskLedger):
    """Build a guard bound to the synthetic workspace (fresh cache dir per call)."""

    def make(runner=None, cache_dir=None, baseline=None) -> RegressionGuard:
        config = GuardConfig(
            root=workspace,
            ledger=ledger,
            graph=graph,
            test_roots=("tests",),
            cache_dir=cache_dir,
            baseline_path=baseline,
        )
        return RegressionGuard(config, runner=runner)

    return make