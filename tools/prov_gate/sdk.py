#!/usr/bin/env python3
"""Publisher entry point: render from the sidecar, then gate what was written.

This is the one call a report publisher makes::

    from tools.prov_gate.sdk import render_and_gate, verify_published
    out = render_and_gate("report.template.md", "sidecar.json",
                          out="report.md", manifest_out="manifest.json",
                          db_path="evidence.sqlite", artifact_root=".")
    if out.status != "PASS":
        raise SystemExit(out.gate["findings"])

``render_and_gate`` never returns a PASS on a document it did not gate: after
writing, it **re-reads the file from disk** and gates those bytes, so the
verdict describes what a reader will actually open, not an in-memory string.

``verify_published`` is the reviewer-side call: gate an already published file
against its template, sidecar and manifest.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gate import GateConfig, gate_report
from .render import RenderResult, load_manifest, render
from .sidecar import Sidecar


@dataclass
class PublishResult:
    status: str
    gate: dict[str, Any]
    rendered: RenderResult
    published_path: Path | None = None
    manifest_path: Path | None = None

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def render_and_gate(
    template: str | Path,
    sidecar: str | Path | Sidecar,
    out: str | Path | None = None,
    manifest_out: str | Path | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    config: GateConfig | None = None,
    write: bool = True,
) -> PublishResult:
    """Render ``template`` from ``sidecar``, write the result, gate the written
    bytes.  A strict render raises ``MissingKeyError`` before anything is
    written: a partially rendered report is never published."""
    template_path = Path(template)
    template_text = template_path.read_text(encoding="utf-8")
    sc = sidecar if isinstance(sidecar, Sidecar) else Sidecar.load(sidecar)

    rendered = render(template_text, sc, strict=True)
    published_path = Path(out) if out is not None else None
    manifest_path = Path(manifest_out) if manifest_out is not None else None
    if write and published_path is not None:
        published_path.parent.mkdir(parents=True, exist_ok=True)
        published_path.write_text(rendered.markdown, encoding="utf-8")
    if write and manifest_path is not None:
        import json

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(rendered.to_manifest(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    # gate what is on disk, not the string we happen to hold in memory
    subject = published_path.read_text(encoding="utf-8") if (
        write and published_path is not None) else rendered.markdown
    gate = gate_report(subject, sc, template=template_text,
                       manifest=rendered.to_manifest(), db_path=db_path,
                       artifact_root=artifact_root, config=config)
    return PublishResult(status=gate["status"], gate=gate, rendered=rendered,
                         published_path=published_path, manifest_path=manifest_path)


def verify_published(
    published: str | Path,
    sidecar: str | Path | Sidecar,
    template: str | Path | None = None,
    manifest: str | Path | dict | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    config: GateConfig | None = None,
) -> dict[str, Any]:
    """Reviewer-side gate on an already published document."""
    sc = sidecar if isinstance(sidecar, Sidecar) else Sidecar.load(sidecar)
    template_text = Path(template).read_text(encoding="utf-8") if template is not None else None
    man = load_manifest(manifest) if isinstance(manifest, (str, Path)) else manifest
    return gate_report(published, sc, template=template_text, manifest=man,
                       db_path=db_path, artifact_root=artifact_root, config=config)


__all__ = ["PublishResult", "render_and_gate", "verify_published"]