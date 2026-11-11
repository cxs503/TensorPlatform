"""Tests for the generic OpenAPI contract-testing harness.

Zero domain semantics on purpose: the fixture service ("measure"/"gizmo") is
synthetic and exists only to exercise the harness.  No literal endpoint, host or
port of any real application appears here -- those are injected as data by the
runner configs under ``runs/``.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.api_contract import breaking, checks, generate, report, runner, spec as spec_mod  # noqa: E402
from tools.api_contract import targets  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic fixture service
# ---------------------------------------------------------------------------


from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402


class ScaleIn(BaseModel):
    value: float = Field(..., description="input magnitude")
    factor: float = Field(..., ge=0.5, le=8.0)


class ScaleOut(BaseModel):
    scaled: float
    echo_value: float
    ratio: float


class ConstantIn(BaseModel):
    seed: float


class ConstantOut(BaseModel):
    fixed: float
    seed_echo: float


class MetaOut(BaseModel):
    units: str
    precision: int


def _build_app() -> FastAPI:
    app = FastAPI(title="gizmo-contract-fixture")

    @app.post("/v1/measure/scale", response_model=ScaleOut)
    def scale(payload: ScaleIn) -> ScaleOut:
        return ScaleOut(scaled=payload.value * payload.factor, echo_value=payload.value,
                        ratio=payload.value * payload.factor / payload.value)

    @app.post("/v1/measure/constant", response_model=ConstantOut)
    def constant(payload: ConstantIn) -> ConstantOut:
        # deliberately input-independent: the sensitivity probe must flag it
        return ConstantOut(fixed=2.0, seed_echo=payload.seed)

    @app.get("/v1/measure/meta", response_model=MetaOut)
    def meta() -> MetaOut:
        return MetaOut(units="synthetic", precision=4)

    @app.get("/v1/measure/item/{item_id}", response_model=MetaOut)
    def item(item_id: int) -> MetaOut:
        if item_id < 0:
            raise HTTPException(status_code=404, detail="absent")
        return MetaOut(units="item-%d" % item_id, precision=item_id)

    return app


@pytest.fixture(scope="module")
def app_and_spec():
    app = _build_app()
    return app, app.openapi()


@pytest.fixture()
def target(app_and_spec):
    app, _ = app_and_spec
    t = targets.ASGITarget(app)
    yield t
    t.close()


# ---------------------------------------------------------------------------
# spec loading / degradation
# ---------------------------------------------------------------------------


def test_spec_missing_degrades_to_withheld(tmp_path):
    load = spec_mod.load_spec(str(tmp_path / "absent.json"))
    assert load.status == spec_mod.STATUS_WITHHELD
    assert load.spec is None
    assert "not found" in load.reason


def test_spec_malformed_is_error_not_crash(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    load = spec_mod.load_spec(str(path))
    assert load.status == spec_mod.STATUS_ERROR
    assert "malformed" in load.reason


def test_spec_valid_and_operations_enumerated(app_and_spec, tmp_path):
    _, document = app_and_spec
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    load = spec_mod.load_spec(str(path))
    assert load.ok and load.sha256
    ops = spec_mod.enumerate_operations(load.spec)
    assert len(ops) >= 4
    assert {"GET /v1/measure/meta", "POST /v1/measure/scale"} <= {op.key for op in ops}


def test_deref_cuts_cycles():
    doc = {
        "components": {"schemas": {
            "Node": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}
        }}
    }
    resolved = spec_mod.deref(doc, {"$ref": "#/components/schemas/Node"})
    assert resolved["type"] == "object"
    assert resolved["properties"]["next"] == {}


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


def test_generation_covers_every_operation(app_and_spec):
    _, document = app_and_spec
    cases = generate.generate_all(document)
    ops = {op.key for op in spec_mod.enumerate_operations(document)}
    assert {case.operation for case in cases} == ops
    assert sum(1 for case in cases if case.kind == "valid") == len(ops)
    kinds = {case.kind for case in cases}
    assert {"invalid_missing_required", "invalid_wrong_type"} <= kinds


def test_generated_valid_payload_respects_required_fields(app_and_spec):
    _, document = app_and_spec
    op = spec_mod.Operation("/v1/measure/scale", "post")
    case = generate.valid_case(document, op)
    schema = spec_mod.request_body_schema(document, op)
    assert set(schema["required"]) <= set(case.body)


def test_matrix_scenarios_cover_five_status_families(app_and_spec):
    _, document = app_and_spec
    op = spec_mod.Operation("/v1/measure/scale", "post")
    scenarios = [generate.matrix_case(document, op, name,
                                      {"body": {"value": 2.0, "factor": 3.0}})
                 for name in generate.MATRIX_SCENARIOS]
    assert all(scenario is not None for scenario in scenarios)
    assert scenarios[0].expected["status"] == ["200"]
    assert scenarios[3].expected["status"] == ["404"]
    assert scenarios[4].expected["status"] == ["404", "405"]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def test_schema_validation_accepts_and_rejects():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "number"}}}
    assert checks.validate_schema({"a": 1.0}, schema).valid
    bad = checks.validate_schema({"a": "x"}, schema)
    assert bad.checked and not bad.valid and bad.errors


def test_status_conformance_handles_wildcards():
    assert checks.status_conforms(["200"], 200)
    assert checks.status_conforms(["2XX"], 204)
    assert checks.status_conforms(["4XX"], 422)
    assert not checks.status_conforms(["2XX"], 404)
    assert not checks.status_conforms(["200"], None)


def test_invariant_tolerance_and_failures():
    body = {"lhs_field": 10.0, "rhs_field": 10.2}
    ok = checks.check_invariant("tol", "/x", body,
                                {"lhs": "lhs_field", "rhs": "rhs_field", "rel_tol": 0.03})
    assert ok.passed and ok.rel_error == pytest.approx(0.02, abs=1e-3)
    bad = checks.check_invariant("tight", "/x", body,
                                 {"lhs": "lhs_field", "rhs": "rhs_field", "rel_tol": 0.001})
    assert not bad.passed


def test_invariant_expression_whitelist():
    with pytest.raises(checks.InvariantError):
        checks.safe_eval("__import__('os').getcwd()", {})
    with pytest.raises(checks.InvariantError):
        checks.safe_eval("a.b", {"a": 1})
    result = checks.check_invariant("bad", "/x", {"a": 1.0}, {"lhs": "a.b", "rhs": "1.0"})
    assert result.status.startswith("UNEVALUABLE")


def test_numeric_leaves_and_finiteness():
    body = {"a": 1, "nested": {"b": 2.5}, "list": [{"c": 3}], "text": "x"}
    leaves = checks.numeric_leaves(body)
    assert set(leaves) == {"a", "nested.b", "list[0].c"}
    assert checks.finiteness({"a": float("nan")})["ok"] is False


# ---------------------------------------------------------------------------
# breaking-change detection
# ---------------------------------------------------------------------------


def test_mutation_detection_all_five_classes(app_and_spec):
    _, document = app_and_spec
    result = breaking.verify_mutations(document)
    assert result["injected"] == 5
    assert result["detected"] == 5, result["rows"]
    assert result["detection_rate"] == 1.0


def test_additive_change_is_not_breaking(app_and_spec):
    import copy

    _, document = app_and_spec
    mutated = copy.deepcopy(document)
    schema = mutated["paths"]["/v1/measure/scale"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    resolved = spec_mod.deref(mutated, schema)
    resolved["properties"]["extra_optional"] = {"type": "number"}
    changes = breaking.diff_specs(document, mutated)
    summary = breaking.summarise(changes)
    assert summary["breaking"] == 0


def test_operation_removal_is_breaking(app_and_spec):
    import copy

    _, document = app_and_spec
    mutated = copy.deepcopy(document)
    mutated["paths"].pop("/v1/measure/meta")
    summary = breaking.summarise(breaking.diff_specs(document, mutated))
    assert summary["breaking"] >= 1
    assert "OPERATION_REMOVED" in summary["breaking_codes"]


# ---------------------------------------------------------------------------
# targets / runner / criteria
# ---------------------------------------------------------------------------


def test_asgi_target_reports_statuses(target):
    ok = target.request("post", "/v1/measure/scale", body={"value": 2.0, "factor": 3.0}, has_body=True)
    assert ok.status == 200 and abs(ok.body["scaled"] - 6.0) < 1e-9
    invalid = target.request("post", "/v1/measure/scale", body={"value": 2.0}, has_body=True)
    assert invalid.status == 422
    unknown = target.request("get", "/v1/measure/nope")
    assert unknown.status == 404
    wrong_method = target.request("delete", "/v1/measure/meta")
    assert wrong_method.status in (404, 405)


def test_unreachable_target_returns_error_not_raise():
    t = targets.HttpTarget("http://127.0.0.1:9", timeout=1.0, retries=0)
    response = t.request("get", "/v1/measure/meta")
    assert not response.ok and response.error in (targets.ERROR_UNREACHABLE, targets.ERROR_TIMEOUT)


def test_full_run_meets_all_criteria(app_and_spec, target):
    _, document = app_and_spec
    cases = generate.generate_all(document)
    rows = runner.run_cases(target, document, cases)

    matrix = runner.run_matrix(target, document, [
        {"path": "/v1/measure/scale", "method": "post", "body": {"value": 2.0, "factor": 3.0}},
        {"path": "/v1/measure/meta", "method": "get"},
    ])
    references = runner.run_references(target, document, [{
        "name": "scale_identity",
        "path": "/v1/measure/scale",
        "request": {"value": 2.0, "factor": 3.0},
        "checks": [{"id": "scaled_equals_input_times_factor", "lhs": "scaled",
                    "rhs": "echo_value*factor", "rel_tol": 0.03}],
    }])
    determinism = runner.run_determinism(target, [c for c in cases if c.kind == "valid"], repeats=2)
    sensitivity = runner.run_sensitivity(target, document, [{
        "path": "/v1/measure/constant", "request": {"seed": 1.0}, "vary": "seed",
        "values": [1.0, 2.0, 4.0], "watch": ["fixed", "seed_echo"],
    }])
    mutations = breaking.verify_mutations(document)
    negatives = runner.negative_controls(
        spec_source_missing="/nonexistent/spec.json",
        spec_source_malformed=_write_malformed(),
        unreachable_base_url="",
    )

    acceptance = report.evaluate({
        "coverage": report.endpoint_coverage(document, rows),
        "schema": report.schema_criteria(rows),
        "matrix": report.matrix_criterion(matrix, 10),
        "breaking": mutations,
        "precision": report.precision_criterion(references, determinism, rows),
        "negatives": negatives,
    })
    criteria = acceptance["criteria"]
    assert criteria["a_endpoint_coverage"]["verdict"] == "PASS"
    assert criteria["b_schema_validation"]["verdict"] == "PASS", criteria["b_schema_validation"]["detail"]
    assert criteria["c_status_codes"]["verdict"] == "PASS", criteria["c_status_codes"]["detail"]
    assert criteria["d_breaking_change"]["verdict"] == "PASS"
    assert criteria["e_numeric_precision"]["verdict"] == "PASS", criteria["e_numeric_precision"]["detail"]
    assert criteria["f_negative_controls"]["verdict"] == "PASS"
    assert acceptance["summary"]["all_pass"] is True

    # the sensitivity probe must catch the deliberately constant field
    assert sensitivity["mock_suspect"] == 1
    verdict = sensitivity["results"][0]
    assert verdict["fields"]["fixed"]["state"] == "INVARIANT"
    assert verdict["fields"]["seed_echo"]["state"] == "SENSITIVE"


def test_quantization_aware_tolerance():
    """A 2-dp published value must not be judged against a 6-dp identity."""
    body = {"rt": 78.56, "ct": 0.002364}
    tight = {"lhs": "ct*0.5*1025*4355.81*3.858333**2/1000", "rhs": "rt", "rel_tol": 1e-06}
    assert not checks.check_invariant("no_quant", "/x", body, tight).passed
    aware = dict(tight, quantization={"lhs": 5e-07, "rhs": 0.005})
    assert checks.check_invariant("quant", "/x", body, aware).passed


def test_cli_run_degrades_to_withheld(tmp_path):
    """End-to-end: a missing spec must produce a WITHHELD report and exit 0."""
    from tools.api_contract import cli

    cfg = {
        "spec": {"source": str(tmp_path / "absent_openapi.json")},
        "target": {"kind": "http", "base_url": "http://stage.invalid"},
        "out": str(tmp_path / "out"),
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert cli.main(["run", "--config", str(config_path)]) == 0
    acceptance = json.loads((tmp_path / "out" / "acceptance.json").read_text())
    assert acceptance["degraded"] is True
    assert acceptance["criteria"]["a_endpoint_coverage"]["verdict"] == "WITHHELD"


def test_cli_diff_fails_on_breaking_change(app_and_spec, tmp_path):
    from tools.api_contract import cli

    _, document = app_and_spec
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text(json.dumps(document), encoding="utf-8")
    mutated, _ = breaking.inject_mutation(document, "field_removal")
    head.write_text(json.dumps(mutated), encoding="utf-8")
    assert cli.main(["diff", "--base", str(base), "--head", str(head)]) == 0
    assert cli.main(["diff", "--base", str(base), "--head", str(head),
                     "--fail-on-breaking"]) == 1


def test_zero_execution_is_withheld_not_pass():
    """No executed evidence must never evaluate to PASS (anti-vacuous-green)."""
    acceptance = report.evaluate({
        "coverage": {"state": "WITHHELD", "operations_in_spec": 0},
        "schema": {"state": "WITHHELD"},
        "matrix": {"state": "WITHHELD"},
        "breaking": {"injected": 0},
        "precision": {"state": "WITHHELD"},
        "negatives": {"all_degraded": True, "controls": {}},
    })
    assert acceptance["summary"]["all_pass"] is False
    assert acceptance["criteria"]["a_endpoint_coverage"]["verdict"] == "WITHHELD"


def test_negative_controls_all_degraded(tmp_path):
    negatives = runner.negative_controls(
        spec_source_missing=str(tmp_path / "absent.json"),
        spec_source_malformed=_write_malformed(tmp_path),
        unreachable_base_url="",
    )
    assert negatives["all_degraded"] is True
    assert negatives["controls"]["spec_missing"]["state"] == "WITHHELD"
    assert negatives["controls"]["spec_malformed"]["state"] == "ERROR"
    assert negatives["controls"]["endpoint_unreachable"]["state"] == "ERROR"


# ---------------------------------------------------------------------------
# domain-leak guard (iron rule: no application specifics inside the package)
# ---------------------------------------------------------------------------


def test_harness_sources_contain_no_endpoint_or_host_literals():
    package_dir = os.path.dirname(os.path.abspath(generate.__file__))
    forbidden = [
        (re.compile(r"https?://(?!\s)"), "absolute URL literal"),
        (re.compile(r"127\.0\.0\.1"), "loopback address literal"),
        (re.compile(r"[\"']/api/"), "application path literal"),
    ]
    offenders = []
    for name in sorted(os.listdir(package_dir)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(package_dir, name), encoding="utf-8").read()
        for pattern, label in forbidden:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append("%s:%d %s" % (name, line, label))
    assert not offenders, offenders


def _write_malformed(tmp_path=None) -> str:
    import tempfile

    base = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    path = os.path.join(base, "_malformed_spec.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<<not json>>")
    return path