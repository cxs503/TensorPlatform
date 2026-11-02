"""Branch-specific tests for the G1-G3 domain boundary gates.

The tests build throw-away *mini repositories* (a few files, no backend
dependencies) so the gate logic is verified end to end: it must go **red** on a
planted violation and **green** on a clean tree.  Nothing here touches the real
repository tree or the network.

Run: ``python -m pytest -q tests/test_boundary_gates.py``
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "audit" / "boundary_gates.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("boundary_gates_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bg = _load_module()


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------

def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


CLEAN_FILES = {
    "backend/main.py": (
        "from fastapi import FastAPI\n"
        "from api.domain import tensor\n"
        "\n"
        "app = FastAPI()\n"
    ),
    "backend/api/domain/tensor.py": "router = object()\n",
    "backend/api/domain/misc.py": (
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
    ),
    "backend/services/benchmark_canon.py": "import json\n\nSCHEMA = 'v1'\n",
    "tools/audit/clean_tool.py": "from pathlib import Path\n\nprint(Path('.'))\n",
}

DIRTY_FILES = {
    # from-import of a ship module
    "backend/main.py": (
        "from fastapi import FastAPI\n"
        "from api.domain import ship_seakeeping\n"
        "\n"
        "app = FastAPI()\n"
    ),
    # direct module import of a ship module
    "backend/api/domain/misc.py": (
        "from fastapi import APIRouter\n"
        "import api.domain.ice_uq\n"
        "\n"
        "router = APIRouter()\n"
    ),
    "backend/api/domain/tensor.py": "router = object()\n",
    "backend/services/benchmark_canon.py": (
        "from services.ship_proxy import BENCHMARK_REFERENCE\n"
    ),
    # ship vocabulary inside the audit tooling surface
    "tools/audit/dirty_tool.py": (
        "from baseline_integrity import holtrop_wetted_surface\n"
        "KVLCC2_LPP = 320.0\n"
        "RHO = WATER_DENSITY  # noqa: F821\n"
    ),
}


def build_repo(root: Path, files: dict[str, str]) -> Path:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "audit").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        _write(root, rel, text)
    return root


CLEAN_OPENAPI = {
    "openapi": "3.0.2",
    "paths": {
        "/api/v1/tensor/jobs": {"get": {}, "post": {}},
        "/api/v1/iam/tokens": {"get": {}},
        "/health": {"get": {}},
    },
}

DIRTY_OPENAPI = {
    "openapi": "3.0.2",
    "paths": {
        "/api/v1/tensor/jobs": {"get": {}},
        "/api/v1/calculations/seakeeping_rao": {"post": {}},
        "/api/v1/ice-uq/uq": {"post": {}},
        "/api/v1/calculations/maneuvering/mmg": {"post": {}, "get": {}},
        "/api/v1/iam/tokens": {"get": {}},
    },
}


def write_openapi(root: Path, doc: dict, name: str = "openapi.json") -> str:
    path = root / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# unit level: token matcher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "segment, token, expected",
    [
        ("ship_seakeeping", "ship", True),
        ("ship", "ship", True),
        ("ice_uq", "ice_uq", True),
        ("manoeuvring", "manoeuvr", True),
        ("kvlcc2", "kvlcc", True),
        ("polar", "polar", True),
        ("relationship", "ship", False),
        ("membership", "ship", False),
        ("chairmanship", "ship", False),
        ("hull_geometry", "ship", False),
        ("tensor", "polar", False),
    ],
)
def test_token_matcher_boundary_aware(segment, token, expected):
    assert bg._token_matches(segment, token) is expected


# ---------------------------------------------------------------------------
# G1 import boundary gate
# ---------------------------------------------------------------------------

def test_g1_green_on_clean_tree(tmp_path):
    repo = build_repo(tmp_path, CLEAN_FILES)
    gate = bg.gate_g1_imports(repo)
    assert gate["count"] == 0
    assert gate["status"] == "green"
    assert gate["violations"] == []
    assert gate["files_scanned"] >= 4
    # no false positive on ``from api.domain import tensor``
    assert all("tensor" not in v["module"] for v in gate["violations"])


def test_g1_red_on_planted_violations(tmp_path):
    repo = build_repo(tmp_path, DIRTY_FILES)
    gate = bg.gate_g1_imports(repo)
    assert gate["status"] == "red"
    assert gate["count"] == 4, gate["violations"]
    found = {(v["file"], v["matched_token"]) for v in gate["violations"]}
    assert ("backend/main.py", "ship") in found
    assert ("backend/api/domain/misc.py", "ice_uq") in found
    assert ("backend/services/benchmark_canon.py", "ship") in found
    assert ("tools/audit/dirty_tool.py", "holtrop") in found
    # lines and evidence are recorded, not just a count
    main_hit = [v for v in gate["violations"] if v["file"] == "backend/main.py"][0]
    assert main_hit["line"] == 2
    assert "ship_seakeeping" in main_hit["source"]


def test_g1_ignores_orm_lookalikes(tmp_path):
    files = {
        "backend/api/domain/tensor.py": (
            "from sqlalchemy.orm import relationship\n"
            "from pkg import membership_record\n"
        ),
        "backend/main.py": "import membership\n",
        "backend/api/domain/misc.py": "router = object()\n",
        "backend/services/benchmark_canon.py": "import json\n",
        "tools/audit/clean_tool.py": "import ast\n",
    }
    gate = bg.gate_g1_imports(build_repo(tmp_path, files))
    assert gate["count"] == 0, gate["violations"]


def test_g1_relative_import_alias_detected(tmp_path):
    files = {
        "backend/api/domain/misc.py": "from . import ship_batch\n",
        "backend/main.py": "import json\n",
        "backend/api/domain/tensor.py": "router = object()\n",
        "backend/services/benchmark_canon.py": "import json\n",
        "tools/audit/clean_tool.py": "import ast\n",
    }
    gate = bg.gate_g1_imports(build_repo(tmp_path, files))
    assert gate["count"] == 1
    assert gate["violations"][0]["matched_token"] == "ship"


# ---------------------------------------------------------------------------
# G2 endpoint vocabulary gate
# ---------------------------------------------------------------------------

def test_g2_green_on_neutral_openapi(tmp_path):
    repo = build_repo(tmp_path, CLEAN_FILES)
    gate = bg.gate_g2_endpoints("http://127.0.0.1:1", write_openapi(repo, CLEAN_OPENAPI))
    assert gate["status"] == "green"
    assert gate["count"] == 0
    assert gate["endpoints"] == []


def test_g2_red_counts_operations_not_paths(tmp_path):
    repo = build_repo(tmp_path, CLEAN_FILES)
    gate = bg.gate_g2_endpoints("http://127.0.0.1:1", write_openapi(repo, DIRTY_OPENAPI))
    assert gate["status"] == "red"
    # 3 ship paths, one of them carrying two operations -> 4 operations
    assert gate["path_count"] == 3
    assert gate["count"] == 4
    tokens = {e["path"]: e["matched_token"] for e in gate["endpoints"]}
    assert tokens["/api/v1/calculations/seakeeping_rao"] == "seakeep"
    assert tokens["/api/v1/ice-uq/uq"] == "ice-uq"
    assert gate["total_openapi_paths"] == 5


def test_g2_withheld_when_service_unreachable(tmp_path):
    gate = bg.gate_g2_endpoints("http://127.0.0.1:1", None, timeout=0.5)
    assert gate["status"] == "withheld"
    assert gate["count"] is None
    assert "openapi unavailable" in gate["error"]


# ---------------------------------------------------------------------------
# G3 constant hoisting gate
# ---------------------------------------------------------------------------

def test_g3_green_on_clean_tree(tmp_path):
    gate = bg.gate_g3_constants(build_repo(tmp_path, CLEAN_FILES))
    assert gate["count"] == 0
    assert gate["status"] == "green"


def test_g3_red_counts_each_constant(tmp_path):
    gate = bg.gate_g3_constants(build_repo(tmp_path, DIRTY_FILES))
    assert gate["status"] == "red"
    # WATER_DENSITY(1) + KVLCC2_LPP(1, family hit for KVLCC) from dirty_tool.py
    assert gate["count"] == 2, gate["violations"]
    assert gate["by_constant"] == {"WATER_DENSITY": 1, "KVLCC": 1}
    assert {v["constant"] for v in gate["violations"]} == {"WATER_DENSITY", "KVLCC"}


def test_g3_triage_splits_code_string_comment(tmp_path):
    """Context triage must separate real code leaks from benchmark-id prose."""
    files = dict(CLEAN_FILES)
    files["tools/audit/ctx.py"] = (
        "RHO = 1025.0  # WATER_DENSITY in old code\n"          # comment hit
        "CASE = 'KVLCC2'\n"                                     # string hit
        "MASS = DTMB5415_DISP\n"                                # code hit
    )
    gate = bg.gate_g3_constants(build_repo(tmp_path, files))
    assert gate["count"] == 3
    assert gate["by_context"] == {"comment": 1, "string": 1, "code": 1}
    contexts = {v["constant"]: v["context"] for v in gate["violations"]}
    assert contexts == {"WATER_DENSITY": "comment", "KVLCC": "string", "DTMB": "code"}


# ---------------------------------------------------------------------------
# orchestration: modes, exit codes, artefacts
# ---------------------------------------------------------------------------

def _run_cli(repo: Path, *extra: str) -> int:
    return bg.main(["--repo-root", str(repo), "--quiet", *extra])


def test_baseline_mode_never_blocks_and_writes_artifacts(tmp_path):
    repo = build_repo(tmp_path, DIRTY_FILES)
    openapi = write_openapi(repo, DIRTY_OPENAPI)
    code = _run_cli(
        repo,
        "--openapi-file",
        openapi,
        "--json",
        "artifacts/boundary_gates/baseline.json",
        "--report",
        "docs/audit/boundary_gates_baseline.md",
    )
    assert code == 0, "baseline mode must not block even with violations"

    payload = json.loads((repo / "artifacts/boundary_gates/baseline.json").read_text())
    assert payload["schema"] == bg.SCHEMA
    assert payload["mode"] == "baseline"
    assert payload["enforce"] is False
    assert set(payload["gates"]) == {
        "G1_import_boundary",
        "G2_endpoint_vocabulary",
        "G3_constant_hoisting",
    }
    assert payload["totals"]["gates_red"] == 3
    assert payload["totals"]["violations"] == 4 + 4 + 2

    report = (repo / "docs/audit/boundary_gates_baseline.md").read_text()
    assert "边界门 G1-G3" in report
    assert "G1_import_boundary" in report
    assert "seakeeping_rao" in report


def test_enforce_mode_blocks_on_violations(tmp_path):
    repo = build_repo(tmp_path, DIRTY_FILES)
    openapi = write_openapi(repo, DIRTY_OPENAPI)
    code = _run_cli(repo, "--openapi-file", openapi, "--enforce")
    assert code == 2


def test_enforce_mode_green_on_clean_tree(tmp_path):
    repo = build_repo(tmp_path, CLEAN_FILES)
    openapi = write_openapi(repo, CLEAN_OPENAPI)
    code = _run_cli(repo, "--openapi-file", openapi, "--enforce")
    assert code == 0


def test_enforce_mode_blocks_when_g2_withheld(tmp_path):
    repo = build_repo(tmp_path, CLEAN_FILES)
    code = _run_cli(repo, "--api-base", "http://127.0.0.1:1", "--timeout", "0.5", "--enforce")
    assert code == 2, "an unevaluable gate must not pass silently under --enforce"


def test_max_violations_ratchet(tmp_path):
    repo = build_repo(tmp_path, DIRTY_FILES)
    openapi = write_openapi(repo, CLEAN_OPENAPI)
    # G1 has 4 and G3 has 2 violations: allowance 4 passes, allowance 1 blocks.
    code = _run_cli(repo, "--openapi-file", openapi, "--enforce", "--max-violations", "4")
    assert code == 0
    code = _run_cli(repo, "--openapi-file", openapi, "--enforce", "--max-violations", "1")
    assert code == 2


def test_only_selects_gates(tmp_path):
    repo = build_repo(tmp_path, DIRTY_FILES)
    openapi = write_openapi(repo, CLEAN_OPENAPI)
    result = bg.run_gates(repo, openapi_file=openapi, only=["G3"])
    assert list(result["gates"]) == ["G3_constant_hoisting"]
    assert result["gates"]["G3_constant_hoisting"]["count"] == 2


def test_gate_surface_is_committed_and_self_excluded(tmp_path):
    """The real repo surface must exist; the gate must not lint its own table."""
    result = bg.run_gates(
        REPO_ROOT,
        openapi_file=str(tmp_path / "missing.json"),
        only=["G1"],
    )
    g1 = result["gates"]["G1_import_boundary"]
    assert g1["missing_surface"] == []
    assert all(v["file"] != "tools/audit/boundary_gates.py" for v in g1["violations"])