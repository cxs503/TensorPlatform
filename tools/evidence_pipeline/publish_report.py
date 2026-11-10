#!/usr/bin/env python3
"""Publish the P167W2-N11 report (markdown + figures) to the platform report API.

The markdown comes from ``docs/P167W2-N11-evidence-pipeline.md`` (itself generated
from the acceptance artifact by ``gen_report.py``), so the published report and
the committed evidence cannot disagree.

Usage::

    TENSOR_AGENT_USER=... TENSOR_AGENT_PASS=... \
      python3 tools/evidence_pipeline/publish_report.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "P167W2-N11-evidence-pipeline.md"
FIGS = ROOT / "artifacts" / "P167W2-N11" / "figs"

FIGURES = (
    ("fig1_metric_margin", "Measured value vs predeclared threshold, per judged metric"),
    ("fig2_conservation_gate", "Conservation gate: exchange with / without the mass correction"),
    ("fig3_acceptance_and_exit_codes", "Acceptance criteria C1-C10 and the exit-code contract"),
    ("fig4_provenance_chain", "Hash-chained provenance: one entry per executed stage"),
)

TITLE = (
    "P167W2-N11 Evidence-Governance Pipeline: benchmark engine + numeric-channel backfill "
    "+ cross-library conservation in one fail-closed chain"
)

SUMMARY = (
    "Integrated the autonomous benchmark engine, the artifact ingest gate and the cross-library "
    "conservation check into one end-to-end chain (config -> execute -> gates -> artifact -> "
    "ingest gate -> regression -> hash-chained provenance) in tools/evidence_pipeline/. "
    "Three synthetic benchmarks with a conservation gate type plus two negative controls; "
    "10/10 machine criteria pass, 39 pytest cases, zero domain vocabulary."
)


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    password = os.environ.get("TENSOR_AGENT_PASS", "")

    login = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

    markdown = DOC.read_text(encoding="utf-8")
    figures = []
    for name, caption in FIGURES:
        path = FIGS / f"{name}.png"
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(path.read_bytes()).decode(),
        })

    payload = {
        "title": TITLE,
        "summary": SUMMARY,
        "scenario_key": "p167w2-n11",
        "mission_id": 9394,
        "tags": [
            "evidence-governance", "benchmark-engine", "ingest-gate", "replay-consistency",
            "conservation", "regression-detection", "provenance", "fail-closed",
            "TensorPlatform", "pytest",
        ],
        "markdown": markdown,
        "figures": figures,
    }
    request = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    result = json.loads(urllib.request.urlopen(request, timeout=60).read())
    print(json.dumps({"posted": result, "markdown_chars": len(markdown),
                      "n_figures": len(figures)}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - network path
        print(f"report publish failed: {exc}", file=sys.stderr)
        sys.exit(1)