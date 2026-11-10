"""Delivery of a verdict to the campaign-DAG endpoint (the last hop of C6).

The consumer module proves the payload *can* be projected onto ``NodeVerdictUpdate``;
these tests prove the projection is what actually goes over the wire, to the route the
API serves, with the pass/fail mapping intact.
"""
from __future__ import annotations

import copy
import json

import pytest


@pytest.fixture
def document(xsolver, snapshot_corpus):
    report = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    return xsolver.dag_consumer.build_verdict_document(report, "wigley3_total_drag")


@pytest.fixture
def recorder():
    calls = []

    def transport(request, timeout):
        calls.append({"request": request, "timeout": timeout})
        return {"ok": True, "node_id": 7}

    transport.calls = calls
    return transport


def test_endpoint_matches_the_api_route(xsolver):
    url = xsolver.dag_push.endpoint("http://127.0.0.1:8001/", 7)
    assert url == "http://127.0.0.1:8001/api/v1/tensor/campaigns/nodes/7/verdict"


def test_push_sends_the_contract_payload(xsolver, document, recorder):
    result = xsolver.dag_push.push(document, 7, "http://api.test", "tok",
                                    transport=recorder)
    assert len(recorder.calls) == 1
    sent = recorder.calls[0]["request"]
    assert sent["url"].endswith("/nodes/7/verdict")
    assert sent["method"] == "POST"
    assert sent["headers"]["Authorization"] == "Bearer tok"
    body = json.loads(sent["body"].decode("utf-8"))
    assert set(body) == {"reproduction_verdict", "block_reason"}
    assert body["reproduction_verdict"] in ("pass", "fail")
    assert result["response"] == {"ok": True, "node_id": 7}
    assert result["body"] == body


def test_withheld_verdict_is_sent_as_fail(xsolver, case_factory, make_corpus_fixture,
                                          recorder):
    """A node must never be closed on unestablished evidence."""
    a = case_factory("synthetic_push_withheld", "SolverA", {"C_T": 0.006, "C_F": 0.004})
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([a]))
    document = xsolver.dag_consumer.build_verdict_document(report, "synthetic_push_withheld")
    xsolver.dag_push.push(document, 7, "http://api.test", transport=recorder)
    body = json.loads(recorder.calls[0]["request"]["body"].decode("utf-8"))
    assert body["reproduction_verdict"] == "fail"
    assert body["block_reason"]


def test_malformed_document_never_reaches_the_wire(xsolver, document, recorder):
    broken = copy.deepcopy(document)
    broken["node_verdict_payload"]["reproduction_verdict"] = "withheld"
    with pytest.raises(ValueError):
        xsolver.dag_push.push(broken, 7, "http://api.test", transport=recorder)
    assert recorder.calls == [], "an invalid verdict must not be transmitted"


def test_transport_errors_propagate(xsolver, document):
    def failing(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(OSError):
        xsolver.dag_push.push(document, 7, "http://api.test", transport=failing)