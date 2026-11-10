"""Fixtures for the cross-solver verification frame (services.xsolver.*).

Everything here is synthetic: the frame must be testable without the ecosystem
checkouts and without a live solver.  The one test that does touch a real kernel
(:mod:`tests.xsolver.test_xsolver_probe_provenance`) skips when the checkout or
torch is unavailable, and would rather skip than fake a residual.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CORPUS_SNAPSHOT = REPO / "artifacts" / "xsolver" / "corpus" / "corpus.json"
INDEX_SNAPSHOT = REPO / "artifacts" / "xsolver" / "corpus" / "index.json"


@pytest.fixture(scope="session")
def xsolver():
    """The frame's modules, imported once."""
    # ``services.xsolver`` re-exports the *functions* under the same names as the
    # modules (``from .adjudicate import adjudicate``), so ``import x.y as z`` would
    # bind the function: import through importlib to get the modules themselves.
    from importlib import import_module

    acceptance = import_module("services.xsolver.acceptance")
    adjudicate = import_module("services.xsolver.adjudicate")
    conservation = import_module("services.xsolver.conservation")
    corpus = import_module("services.xsolver.corpus")
    dag_consumer = import_module("services.xsolver.dag_consumer")
    dag_push = import_module("services.xsolver.dag_push")
    normalize = import_module("services.xsolver.normalize")
    probe = import_module("services.xsolver.probe")

    class Frame:
        pass

    frame = Frame()
    frame.acceptance = acceptance
    frame.adjudicate = adjudicate
    frame.conservation = conservation
    frame.corpus = corpus
    frame.dag_consumer = dag_consumer
    frame.dag_push = dag_push
    frame.normalize = normalize
    frame.probe = probe
    frame.repo = REPO
    return frame


@pytest.fixture(scope="session")
def snapshot_corpus():
    """The committed corpus snapshot: deterministic, no ecosystem checkouts needed."""
    if not CORPUS_SNAPSHOT.is_file():
        pytest.skip(f"corpus snapshot missing: {CORPUS_SNAPSHOT}")
    return json.loads(CORPUS_SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def snapshot_index():
    if not INDEX_SNAPSHOT.is_file():
        pytest.skip(f"corpus index missing: {INDEX_SNAPSHOT}")
    return json.loads(INDEX_SNAPSHOT.read_text(encoding="utf-8"))


def make_case(normalize, problem_id: str, solver: str, quantities: dict, *,
              family: str = "LB", condition=None, residuals=None,
              grid_levels=None, commit: str = "cafebabe0123", method: str = "synthetic",
              version: str = "1.0.0") -> dict:
    """Build one canonical case record from synthetic numbers only."""
    raw = {"case": problem_id, "output": dict(quantities)}
    record = normalize.build_case(
        raw,
        source_path=f"/tmp/xsolver-tests/{problem_id}/{solver}.json",
        repo="synthetic",
        root=Path("/tmp/xsolver-tests"),
        problem_id=problem_id,
        condition=condition or {"label": "Fn", "value": 0.3, "case_name": problem_id},
        solver_override={"name": solver, "family": family, "version": f"{solver}@{version}",
                         "method": method},
        quantity_override=dict(quantities),
        residual_override=residuals or {"mass": None, "momentum": None, "energy": None},
        geometry_override={"kind": "synthetic", "units": "SI", "dimensions": None,
                           "grid": {"nx": 1.0}, "derived": None,
                           "source_path": "synthetic", "stage": "test"},
    )
    if grid_levels:
        record["output"]["convergence"]["grid_levels"] = grid_levels
    record["provenance"]["git_commit"] = commit
    record["provenance"]["solver_version"] = f"{solver}@{version}"
    return record


def make_corpus(cases) -> dict:
    return {"schema": "xsolver/corpus/v1", "cases": list(cases),
            "stats": {"coverage": {}, "case_count": len(cases)}, "sources": []}


@pytest.fixture
def make_corpus_fixture():
    """Expose the synthetic corpus builder as a fixture."""
    return make_corpus


@pytest.fixture
def case_factory(xsolver):
    def factory(*args, **kwargs):
        return make_case(xsolver.normalize, *args, **kwargs)
    return factory


@pytest.fixture
def fake_package():
    """Create an importable fake solver package with the declared kernel API."""
    def build(root: Path, name: str = "xsv_fakebackend", *,
              api=("equilibrium", "collide_bgk", "stream", "macroscopic"),
              version: str = "9.9.9", marker: str = "original") -> Path:
        pkg = Path(root) / name
        pkg.mkdir(parents=True, exist_ok=True)
        body = [f"__version__ = {version!r}", f"MARKER = {marker!r}"]
        for fn in api:
            body.append(f"def {fn}(*args, **kwargs):\n    return None")
        (pkg / "__init__.py").write_text("\n".join(body) + "\n", encoding="utf-8")
        return pkg
    return build


@pytest.fixture
def clean_sys_modules():
    """Drop fake backend modules from sys.modules after the test."""
    names = []
    yield names
    for name in names:
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _no_ecosystem_env(monkeypatch):
    """Keep the ambient ecosystem path out of the synthetic tests' way."""
    if not os.environ.get("XSOLVER_ECOSYSTEM_ROOT"):
        monkeypatch.setenv("XSOLVER_ECOSYSTEM_ROOT", "/nonexistent/xsolver-tests")