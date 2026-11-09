"""interface_probe 验收测试（合成 openapi 文档，零领域语义）。

断言三态齐全：ok / missing_path / missing_method / missing_field，
以及「探针多于文档」的 missing_documents 阻塞（不能让漏掉的服务静默通过）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops" / "taskplane"
sys.path.insert(0, str(OPS))

import interface_probe as ip  # noqa: E402


def doc(paths: dict, title: str = "synthetic-api", version: str = "0.0.1") -> dict:
    return {"openapi": "3.1.0", "info": {"title": title, "version": version},
            "paths": paths}


GOOD_DOC = doc({
    "/api/v1/alpha": {"post": {"responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object", "properties": {"result": {"properties": {
            "alpha_value": {"type": "number"}, "gamma": {"type": "array"}}}}}}}}}}},
    "/api/v1/beta": {"get": {"responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object", "properties": {"items": {"type": "array"}}}}}}}}},
})

PROBES = {"syn": [
    {"id": "alpha_out", "path": "/api/v1/alpha", "method": "post", "fields": ["alpha_value"]},
    {"id": "beta_list", "path": "/api/v1/beta", "method": "get", "fields": ["items"]},
]}


def test_all_ok():
    report = ip.verify_interfaces({"syn": GOOD_DOC}, PROBES)
    assert report["summary"]["all_ok"] is True
    assert report["summary"]["ok"] == 2
    assert report["documents"]["syn"]["openapi"]["version"] == "0.0.1"


def test_missing_path_blocks():
    probes = {"syn": PROBES["syn"] + [
        {"id": "ghost", "path": "/api/v1/ghost", "method": "get", "fields": []}]}
    report = ip.verify_interfaces({"syn": GOOD_DOC}, probes)
    assert report["summary"]["all_ok"] is False
    assert report["documents"]["syn"]["by_status"]["missing_path"] == 1


def test_missing_method_blocks():
    probes = {"syn": [{"id": "wrong_verb", "path": "/api/v1/beta", "method": "post",
                       "fields": []}]}
    report = ip.verify_interfaces({"syn": GOOD_DOC}, probes)
    rec = report["documents"]["syn"]["results"][0]
    assert rec["status"] == "missing_method" and rec["detail"]["available"] == ["get"]


def test_missing_field_blocks():
    probes = {"syn": [{"id": "drift", "path": "/api/v1/alpha", "method": "post",
                       "fields": ["renamed_value"]}]}
    report = ip.verify_interfaces({"syn": GOOD_DOC}, probes)
    rec = report["documents"]["syn"]["results"][0]
    assert rec["status"] == "missing_field"
    assert rec["detail"]["missing_fields"] == ["renamed_value"]


def test_probed_document_missing_is_not_ok():
    """探针覆盖的服务没拉到文档 → 整体不得判 ok（漏服务不能静默放过）。"""
    report = ip.verify_interfaces({"syn": GOOD_DOC},
                                  {"syn": PROBES["syn"], "other": PROBES["syn"]})
    assert report["missing_documents"] == ["other"]
    assert report["summary"]["all_ok"] is False


def test_digest_deterministic_and_version_sensitive():
    """同一文档两次 → 同 digest；服务版本变了 → digest 变（接口漂移可钉到版本）。"""
    r1 = ip.verify_interfaces({"syn": GOOD_DOC}, PROBES)
    r2 = ip.verify_interfaces({"syn": GOOD_DOC}, PROBES)
    assert r1["input_digest"] == r2["input_digest"]
    drifted = doc(dict(GOOD_DOC["paths"]), version="0.0.2")
    assert ip.verify_interfaces({"syn": drifted}, PROBES)["input_digest"] != r1["input_digest"]


def test_ref_resolution_enables_field_assertion():
    """响应走 ``$ref`` 时字段名不在 operation 里 —— 不展开 ref 会全部误报 missing_field。"""
    ref_doc = {
        "openapi": "3.1.0", "info": {"title": "ref-api", "version": "1.0"},
        "paths": {"/api/v1/gamma": {"post": {"responses": {"200": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/GammaOut"}}}}}}}},
        "components": {"schemas": {"GammaOut": {"type": "object", "properties": {
            "gamma_value": {"type": "number"}}}}},
    }
    probes = {"syn": [{"id": "gamma", "path": "/api/v1/gamma", "method": "post",
                       "fields": ["gamma_value"]}]}
    report = ip.verify_interfaces({"syn": ref_doc}, probes)
    assert report["documents"]["syn"]["results"][0]["status"] == "ok"
    assert report["summary"]["all_ok"] is True


def test_walk_helper():
    schema = {"properties": {"a": {"properties": {"b": {"type": "number"}}}}}
    assert ip._walk(schema, "a.b") is True
    assert ip._walk(schema, "a.c") is False


def test_cli_offline_roundtrip(tmp_path):
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps({"documents": PROBES}), encoding="utf-8")
    out = tmp_path / "out.json"
    # 无 --document → 探针全落 missing_documents，退出码 2（不误报通过）
    rc = ip.main(["--probes", str(probes), "--json-out", str(out)])
    assert rc == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["missing_documents"] == ["syn"]


def test_cli_bad_input(tmp_path):
    assert ip.main(["--probes", str(tmp_path / "nope.json")]) == 3
    probes = tmp_path / "p.json"
    probes.write_text("{}", encoding="utf-8")
    assert ip.main(["--probes", str(probes), "--document", "bad-spec"]) == 3