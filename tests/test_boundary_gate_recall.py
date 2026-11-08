"""Tests for the boundary-gate adversarial recall measurement (P22-N10).

Two layers:

1. **Harness layer** -- the corpus/recall pipeline must be internally consistent:
   every adversarial variant has a documented root cause, hierarchy of the
   criteria blocks is right, and the hardened configuration satisfies the node's
   acceptance criteria (recall >= 95 %, root-cause coverage 100 %, control
   detection 100 %).
2. **Rule layer** -- each hardened detector (runtime-import folding, surface
   globs, separator-normalised + metadata endpoint vocabulary, constant case /
   embedded / folded variants) is pinned individually, including the
   false-positive side and the legacy switch that reproduces the old behaviour.

Run: ``python -m pytest -q tests/test_boundary_gate_recall.py``
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO_ROOT / "tools" / "audit" / "boundary_gates.py"
RECALL_SCRIPT = REPO_ROOT / "tools" / "audit" / "boundary_gate_recall.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bg = _load("boundary_gates_under_test", GATE_SCRIPT)
recall = _load("boundary_gate_recall_under_test", RECALL_SCRIPT)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


@pytest.fixture(scope="module")
def measurement():
    """One full corpus measurement, reused by the criteria tests."""
    return recall.run_measurement(with_live=False, figures_dir=None)


# ---------------------------------------------------------------------------
# harness layer
# ---------------------------------------------------------------------------

def test_corpus_has_documented_root_cause_for_every_variant():
    corpus = recall.build_corpus()
    ids = [case.id for case in corpus]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    undocumented = sorted({c.variant for c in corpus if c.variant not in recall.ROOT_CAUSES})
    assert undocumented == [], f"variants without a root-cause entry: {undocumented}"
    assert {c.kind for c in corpus} == {"leak", "clean", "control"}
    for gate in ("G1", "G2", "G3"):
        assert any(c.gate == gate for c in corpus), gate
    # payload must actually exist for every case
    for case in corpus:
        assert case.files or case.openapi, case.id
        if case.openapi is not None:
            assert case.openapi["paths"], case.id


def test_recall_meets_acceptance_criteria(measurement):
    crit = measurement["criteria"]
    assert crit["E1_recall_after_fix"] >= 0.95, crit
    assert crit["E3_recall_after_fix"] >= 0.95, crit
    assert crit["E2_root_cause_coverage"] == 1.0, crit
    assert crit["E4_control_detect_rate"] == 1.0, crit
    assert crit["specificity_after"] == 1.0, crit
    assert crit["all_pass"] is True


def test_recall_is_statistically_certified(measurement):
    """A point recall is not a bound: the corpus must be wide enough to certify it."""
    crit = measurement["criteria"]
    assert crit["leak_cases"] >= 59, (
        "with fewer than 59 leak cases a perfect score cannot certify recall >= 95% "
        "at 95% confidence"
    )
    assert crit["E1_recall_one_sided95_lower"] >= 0.95, crit
    assert crit["E1_statistically_certified"] is True
    # sanity check the interval routine itself against the closed form 0.05**(1/n)
    assert recall.recall_lower_bound(1.0, 62) == pytest.approx(0.05 ** (1 / 62), abs=2e-3)
    assert recall.recall_lower_bound(0.0, 10) == 0.0
    assert recall.recall_lower_bound(None, 10) is None


def test_hardening_improves_every_gate(measurement):
    before, after = measurement["before"], measurement["after"]
    for gate in ("G1", "G2", "G3"):
        b = before["per_gate"][gate]["recall"]
        a = after["per_gate"][gate]["recall"]
        assert a >= b, (gate, b, a)
        assert after["per_gate"][gate]["false_positives"] == 0, gate
    # the pre-hardening configuration must fail the acceptance line, otherwise
    # the corpus is not adversarial enough to be worth measuring
    assert before["leak"]["rate"] < 0.95


def test_miss_ledger_answers_every_miss(measurement):
    ledger = measurement["miss_ledger"]
    missed_before = {
        r["case"] for r in measurement["before"]["results"]
        if r["kind"] == "leak" and not r["detected"]
    }
    assert {entry["case"] for entry in ledger} == missed_before
    for entry in ledger:
        assert entry["root_cause"].strip(), entry
        assert entry["root_cause_category"] in {
            "rule_missing", "logic_gap", "coverage", "scope", "none",
        }, entry
        assert entry["fix_rule"].startswith(("G1-R", "G2-R", "G3-R")), entry
        assert entry["detected_after_fix"] is True, entry


def test_report_and_figure_rendering(tmp_path, measurement):
    markdown = recall.render_markdown(measurement)
    assert "E1 边界门对抗语料召回率" in markdown
    assert "## 漏检清单与根因" in markdown
    figures = recall.render_figures(measurement, tmp_path / "figures")
    names = [f["name"] for f in figures]
    assert names == ["recall_before_after.png", "miss_taxonomy.png"]
    for figure in figures:
        assert Path(figure["path"]).stat().st_size > 1000
        assert figure["png_base64"]


def test_cli_writes_artifacts(tmp_path, capfd):
    json_out = tmp_path / "recall" / "report.json"
    md_out = tmp_path / "recall" / "report.md"
    code = recall.main([
        "--no-live", "--no-figures",
        "--json-out", str(json_out), "--markdown-out", str(md_out),
    ])
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["criteria"]["all_pass"] is True
    assert md_out.read_text(encoding="utf-8").startswith("# 边界门对抗语料召回率度量")
    captured = capfd.readouterr().out
    assert "all criteria pass: True" in captured


# ---------------------------------------------------------------------------
# rule layer: G1
# ---------------------------------------------------------------------------

VOCAB = recall.SYNTH_VOCAB


@pytest.mark.parametrize(
    "body",
    [
        "importlib.import_module('widget.solver')",
        "__import__('flux.solver')",
    ],
)
def test_g1_runtime_import_literal_detected(tmp_path, body):
    rel = _write(tmp_path, "backend/api/domain/s.py", f"import importlib\n\nx = {body}\n")
    hit = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])
    assert hit["count"] == 1
    assert hit["violations"][0]["kind"] == "dynamic-import"


def test_g1_runtime_import_folding_forms(tmp_path):
    rel = _write(
        tmp_path,
        "backend/api/domain/s.py",
        "import importlib\n"
        "MODULE = 'widget.solver'\n"
        "A = importlib.import_module('wid' + 'get.core')\n"
        "B = importlib.import_module(f'flux.{name}')\n"
        "C = importlib.import_module(MODULE)\n",
    )
    hit = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])
    assert hit["count"] == 3, hit["violations"]


def test_g1_runtime_import_legacy_switch_is_blind(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/s.py",
                 "import importlib\nx = importlib.import_module('widget.solver')\n")
    assert bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"],
                              dynamic_imports=False)["count"] == 0


def test_g1_opaque_runtime_import_becomes_advisory(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/s.py",
                 "import importlib\ndef f(pkg):\n    return importlib.import_module(f'{pkg}.x')\n")
    hit = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])
    assert hit["count"] == 0
    assert [a["kind"] for a in hit["advisories"]] == ["dynamic-import-opaque"]


def test_g1_resolved_neutral_runtime_import_stays_quiet(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/s.py",
                 "import importlib\nx = importlib.import_module('services.report_gate')\n")
    hit = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])
    assert hit["count"] == 0 and hit["advisories"] == []


def test_g1_surface_glob_reaches_unlisted_file(tmp_path):
    rel = _write(tmp_path, "backend/services/helper.py", "from widget import solver\n")
    assert bg.gate_g1_imports(tmp_path, [], tokens=VOCAB["import_tokens"])["count"] == 0
    hit = bg.gate_g1_imports(
        tmp_path, [], tokens=VOCAB["import_tokens"], surface_globs=("backend/services/*.py",)
    )
    assert hit["count"] == 1
    assert hit["surface_globs"] == ["backend/services/*.py"]


# ---------------------------------------------------------------------------
# rule layer: G2
# ---------------------------------------------------------------------------

def _openapi(tmp_path: Path, paths: dict) -> str:
    """Write an OpenAPI document and return its absolute path."""
    doc = {"openapi": "3.0.0", "paths": paths}
    rel = _write(tmp_path, "openapi.json", json.dumps(doc))
    return str(tmp_path / rel)


def _g2(tmp_path, paths, **kwargs):
    path = _openapi(tmp_path, paths)
    return bg.gate_g2_endpoints(
        "http://127.0.0.1:0/unused", openapi_file=path, timeout=0.1,
        vocab_pattern=VOCAB["endpoint_pattern"], **kwargs,
    )


@pytest.mark.parametrize(
    "path,legacy_detects",
    [
        ("/v1/gadget_cfd/run", False),   # underscore instead of hyphen -> legacy blind
        ("/v1/hullform/run", False),     # separator removed -> legacy blind
        ("/v1/GADGET-CFD/run", True),    # case variant only -> legacy already matches
    ],
)
def test_g2_separator_normalisation(tmp_path, path, legacy_detects):
    assert _g2(tmp_path, {path: {"post": {}}})["count"] == 1
    legacy = _g2(tmp_path, {path: {"post": {}}}, normalize_separators=False)
    assert (legacy["count"] == 1) is legacy_detects, legacy


@pytest.mark.parametrize(
    "operation",
    [{"tags": ["widget"]}, {"summary": "widget solver"}, {"operationId": "computeWidget"}],
)
def test_g2_metadata_vocabulary_detected(tmp_path, operation):
    result = _g2(tmp_path, {"/v1/compute/run": {"post": operation}})
    assert result["metadata_hits"], result
    assert result["violations"] and result["violations"][0]["kind"] == "metadata"
    assert _g2(tmp_path, {"/v1/compute/run": {"post": operation}}, metadata_scan=False)[
        "metadata_hits"
    ] == []


def test_g2_neutral_document_stays_green(tmp_path):
    result = _g2(tmp_path, {
        "/v1/calculations/run": {"post": {"tags": ["compute"], "summary": "run a job"}},
        "/v1/relationships/list": {"post": {}},
    })
    assert result["count"] == 0 and result["metadata_hits"] == []
    assert result["status"] == "green"


# ---------------------------------------------------------------------------
# rule layer: G3
# ---------------------------------------------------------------------------

def test_g3_constant_forms(tmp_path):
    rel = _write(
        tmp_path,
        "backend/api/domain/c.py",
        "GADGET_RHO = 1025.0\n"
        "DEEP_GADGET_RHO = 1025.0\n"
        "gadget_rho = 1025.0\n"
        "NAME = 'GADGET_' + 'RHO'\n",
    )
    result = bg.gate_g3_constants(tmp_path, [rel], constants=VOCAB["hoist_constants"])
    forms = sorted({v["form"] for v in result["violations"]})
    assert forms == ["case_variant", "embedded", "exact", "folded"], forms
    assert result["count"] == 4


def test_g3_legacy_configuration_is_blind_to_variants(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/c.py",
                 "DEEP_GADGET_RHO = 1.0\ngadget_rho = 1.0\nNAME = 'GADGET_' + 'RHO'\n")
    legacy = bg.gate_g3_constants(
        tmp_path, [rel], constants=VOCAB["hoist_constants"],
        variants=False, embedded=False, constant_folding=False,
    )
    assert legacy["count"] == 0


def test_g3_neutral_names_stay_quiet(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/c.py", "MAX_RETRIES = 3\ngadget = object()\n")
    result = bg.gate_g3_constants(tmp_path, [rel], constants=VOCAB["hoist_constants"])
    assert result["count"] == 0


@pytest.mark.parametrize(
    "body",
    [
        "x = importlib.import_module(name='widget.core')",           # keyword argument
        "from importlib import import_module as im\nx = im('widget.core')",  # local alias
        "x = getattr(importlib, 'import_module')('widget.core')",    # fetched by name
        "exec('import widget.core')",                                # source string
    ],
)
def test_g1_hardened_runtime_forms_detected(tmp_path, body):
    rel = _write(tmp_path, "backend/api/domain/s.py", f"import importlib\n{body}\n")
    hardened = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])
    assert hardened["count"] >= 1, hardened
    legacy = bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"],
                                dynamic_imports=False)
    assert legacy["count"] == 0, legacy


def test_g1_exec_string_without_vocabulary_stays_quiet(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/s.py", "exec('import json')\n")
    assert bg.gate_g1_imports(tmp_path, [rel], tokens=VOCAB["import_tokens"])["count"] == 0


def test_g2_parameter_names_and_document_metadata(tmp_path):
    param_path = _openapi(tmp_path, {"/v1/compute/run": {"post": {
        "parameters": [{"name": "widget_id", "in": "query"}]}}})
    result = bg.gate_g2_endpoints("http://127.0.0.1:0/unused", openapi_file=param_path,
                                 timeout=0.1, vocab_pattern=VOCAB["endpoint_pattern"])
    assert [h["field"] for h in result["metadata_hits"]] == ["parameters"]

    doc_path = _write(tmp_path, "openapi2.json", json.dumps({
        "openapi": "3.0.0", "info": {"title": "Widget API", "version": "1"},
        "paths": {"/v1/compute": {"post": {}}}}))
    result = bg.gate_g2_endpoints("http://127.0.0.1:0/unused",
                                 openapi_file=str(tmp_path / doc_path),
                                 timeout=0.1, vocab_pattern=VOCAB["endpoint_pattern"])
    assert [h["field"] for h in result["metadata_hits"]] == ["info.title"]
    assert result["metadata_hits"][0]["path"] == "<document>"


def test_g3_folded_hit_is_triaged_as_string(tmp_path):
    rel = _write(tmp_path, "backend/api/domain/c.py",
                 "def f():\n    '''Notes: GADGET_''' + '''RHO.'''\n")
    result = bg.gate_g3_constants(tmp_path, [rel], constants=VOCAB["hoist_constants"])
    folded = [v for v in result["violations"] if v["form"] == "folded"]
    assert folded and folded[0]["context"] == "string"