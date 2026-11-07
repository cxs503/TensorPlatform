"""Tests for tools/audit/gate_wiring_audit.py (m2049).

Two layers:
1. synthetic fixture repos — each verdict class is pinned by construction, so a
   classifier regression turns red without touching the real repository;
2. real-repo oracles — three hand-verified rows of the current tree
   (`backend/middleware/truth_gates_meter.py` never mounted,
   `backend/api/truth_gates.py` mounted through the tensor router,
   `backend/services/report_gate.py` imported by tests/tools only).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools" / "audit"))

import gate_wiring_audit as gwa  # noqa: E402


def _w(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    """Miniature repo with exactly one module per verdict class."""
    _w(tmp_path / "backend" / "main.py", """
        from services.wired_gate import f
        from api import user
    """)
    _w(tmp_path / "backend" / "services" / "__init__.py")
    _w(tmp_path / "backend" / "services" / "wired_gate.py", "def f():\n    return 1\n")
    _w(tmp_path / "backend" / "services" / "script_gate.py", "X = 1\n")
    _w(tmp_path / "backend" / "services" / "tool_gate.py", "Y = 1\n")
    _w(tmp_path / "backend" / "services" / "test_gate.py", "Z = 1\n")
    _w(tmp_path / "backend" / "services" / "orphan_gate.py", "W = 1\n")
    _w(tmp_path / "backend" / "models" / "__init__.py")
    _w(tmp_path / "backend" / "models" / "m_gate.py", "T = 2\n")
    _w(tmp_path / "backend" / "api" / "__init__.py")
    _w(tmp_path / "backend" / "api" / "user.py", "from models.m_gate import T\n")
    _w(tmp_path / "scripts" / "ci_gate.py", "from services.script_gate import X\n")
    _w(tmp_path / "tools" / "run_tool.py", "from services.tool_gate import Y\n")
    _w(tmp_path / "tests" / "test_thing.py", "from services.test_gate import Z\n")
    # Makefile recipe (tab-indented) is a command; a comment is not
    (tmp_path / "Makefile").write_text(
        "check:\n\tpython3 scripts/ci_gate.py\n# python3 scripts/orphan_gate.py\n",
        encoding="utf-8",
    )
    # workflow: a run block (real command) and a paths filter (NOT a command)
    _w(tmp_path / ".github" / "workflows" / "ci.yml", """
        on:
          pull_request:
            paths:
              - 'backend/services/orphan_gate.py'
        jobs:
          t:
            steps:
              - run: python -m pytest -q tests/
    """)
    _w(tmp_path / "docs" / "note.md", "```bash\npython3 tools/run_tool.py\n```\n")
    return tmp_path


def _rows(result: dict) -> dict[str, dict]:
    return {r["module"]: r for r in result["gates"]}


def test_fixture_verdict_ladder(fixture_repo: Path):
    res = gwa.audit(fixture_repo)
    rows = _rows(res)
    assert rows["backend/services/wired_gate.py"]["verdict"] == "WIRED_RUNTIME"
    assert rows["backend/services/script_gate.py"]["verdict"] == "WIRED_CI"
    assert rows["backend/services/tool_gate.py"]["verdict"] == "TOOL_ONLY"
    assert rows["backend/services/test_gate.py"]["verdict"] == "TEST_ONLY"
    assert rows["backend/services/orphan_gate.py"]["verdict"] == "ORPHAN"
    assert res["verdict_counts"] == {
        "WIRED_RUNTIME": 2, "WIRED_CI": 2, "TOOL_ONLY": 1, "TEST_ONLY": 1, "ORPHAN": 1,
    }


def test_path_filter_is_not_an_invocation(fixture_repo: Path):
    """A `paths:` trigger entry must not make a gate look CI-wired."""
    res = gwa.audit(fixture_repo)
    assert "scripts/ci_gate.py" in res["entrypoints"]["ci_scripts"]
    assert "backend/services/orphan_gate.py" not in res["entrypoints"]["ci_scripts"]
    assert any(t.startswith("tests") for t in res["entrypoints"]["ci_test_paths"])


def test_package_suffix_resolution(fixture_repo: Path):
    """`from models.m_gate import ...` must resolve under backend/ on sys.path,
    even though `backend/` itself is not a package (real repo layout)."""
    res = gwa.audit(fixture_repo)
    row = _rows(res)["backend/models/m_gate.py"]
    assert row["verdict"] == "WIRED_RUNTIME"
    assert row["importers_non_test"] == ["backend/api/user.py"]


def test_doc_invoked_dimension(fixture_repo: Path):
    res = gwa.audit(fixture_repo)
    row = _rows(res)["backend/services/tool_gate.py"]
    assert row["in_doc_invoked_closure"] is True
    assert any("docs/note.md" in s for s in row["doc_invoked_sites"])


def test_markdown_render_has_all_verdict_classes(fixture_repo: Path):
    md = gwa.to_markdown(gwa.audit(fixture_repo))
    for v in gwa.VERDICT_ORDER:
        assert v in md


def test_self_audit_is_serialisable(fixture_repo: Path):
    import json
    json.dumps(gwa.audit(fixture_repo))     # no Path/None leaks


@pytest.mark.skipif(not (REPO_ROOT / "backend" / "main.py").is_file(),
                    reason="real TensorPlatform tree not available")
def test_real_repo_oracles():
    """Hand-verified oracles in the live tree (each re-checked with grep)."""
    res = gwa.audit(REPO_ROOT)
    rows = _rows(res)
    # 1. metering middleware: no add_middleware / no import anywhere => ORPHAN
    meter = rows["backend/middleware/truth_gates_meter.py"]
    assert meter["verdict"] == "ORPHAN"
    assert meter["importers_non_test"] == [] and meter["importers_test"] == []
    # 2. truth-gates router is mounted (tensor router imports domain/tensor.py)
    assert rows["backend/api/truth_gates.py"]["verdict"] == "WIRED_RUNTIME"
    # 3. report gate: 生产路径已接线（POST /tensor/reports/qa/check 调它），
    #    这条 oracle 原先断言「只有 tests+tools 引用」——那是接线前的状态，
    #    现已反过来钉住**接线点必须是报告 QA 端点**（更严：不认别的入口）。
    rg = rows["backend/services/report_gate.py"]
    assert rg["verdict"] == "WIRED_RUNTIME"
    assert "backend/api/tensor_reports.py" in rg["importers_non_test"], rg
    # 4. sqlalchemy models ARE wired (regression guard for the resolver bug)
    assert rows["backend/models/truth_gates.py"]["verdict"] == "WIRED_RUNTIME"
    # 5. inventory must be non-trivial and parse errors bounded
    assert res["n_gate_modules"] >= 25
    assert len(res["parse_errors"]) <= 3