"""Deliver a cross-solver verdict to a campaign-DAG verification node.

:mod:`services.xsolver.dag_consumer` decides *what* the verdict is and shapes it into
``NodeVerdictUpdate``; this module is the wire, so the frame can actually close a DAG
node instead of stopping at a JSON file on disk.

Two deliberate choices:

* the payload is validated against the endpoint's own contract *before* the request
  is built -- a malformed body must fail here, not as a 422 whose message nobody reads;
* the transport is injectable, so the delivery path is testable without a live API and
  without a database, and the test can assert the exact URL and body.

Nothing is retried or swallowed: an HTTP error propagates to the caller, because a
verdict that silently failed to arrive is worse than a loud failure (the node would
stay open and nobody would know why).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from .dag_consumer import consume_verdict


def endpoint(base_url: str, node_id: int) -> str:
    """The campaign-DAG verdict route (``POST``), see ``api/campaign_plans.py``."""
    return f"{base_url.rstrip('/')}/api/v1/tensor/campaigns/nodes/{int(node_id)}/verdict"


def build_request(document: Dict[str, Any], node_id: int, base_url: str,
                  token: Optional[str] = None) -> Dict[str, Any]:
    """Validate the verdict document and build the HTTP request description."""
    consumed = consume_verdict(document)
    body = json.dumps(consumed["node_verdict_payload"], ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return {
        "url": endpoint(base_url, node_id),
        "method": "POST",
        "headers": headers,
        "body": body,
        "payload": consumed["node_verdict_payload"],
        "verdict": consumed["verdict"],
        "problem_id": consumed["problem_id"],
        "criteria_failed": consumed["criteria_failed"],
    }


def _urllib_transport(request: Dict[str, Any], timeout: float) -> Any:
    import urllib.request

    http = urllib.request.Request(request["url"], data=request["body"],
                                  headers=request["headers"], method=request["method"])
    with urllib.request.urlopen(http, timeout=timeout) as response:  # noqa: S310 - declared URL
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def push(document: Dict[str, Any], node_id: int, base_url: str,
         token: Optional[str] = None, *,
         transport: Optional[Callable[[Dict[str, Any], float], Any]] = None,
         timeout: float = 30.0) -> Dict[str, Any]:
    """POST the verdict to the DAG node and return ``{request, response}``.

    ``transport`` defaults to urllib; tests inject a recorder.  A ``withheld`` verdict is
    sent as ``fail`` on purpose (see :mod:`services.xsolver.dag_consumer`): a node must
    not be closed on evidence that could not be established.
    """
    request = build_request(document, node_id, base_url, token)
    send = transport or _urllib_transport
    response = send(request, timeout)
    return {"request": {k: v for k, v in request.items() if k != "body"},
            "body": json.loads(request["body"].decode("utf-8")),
            "response": response}