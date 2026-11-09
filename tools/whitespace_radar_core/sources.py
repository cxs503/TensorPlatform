"""Pool adapters: turn heterogeneous ecosystem artifacts into ``Doc`` records.

Everything is *configuration driven* (``--pool <manifest.json>``).  The adapter
knows four shapes of evidence, all of them generic:

``proposal_json``    one JSON document per file; the manifest names the id field
                     (optional) and the text fields to concatenate.
``markdown``         ``**/*.md`` style globs; the first ATX heading becomes the
                     title, the body is the indexed text.
``module_manifest``  a JSON inventory of code modules per repository (built by
                     ``tools/whitespace_radar.py --build-manifest``); lets the
                     radar cover *other* repos without cloning them.
``campaign_export``  a JSON export of campaign plans/nodes; the adapter is
                     agnostic to which subject the campaigns are about.

There is deliberately **no domain field mapping** in this module: field names
come from the pool manifest, so onboarding a new repo or a new artifact type is a
data change, not a code change.  Docs are returned sorted by ``doc_id`` so the
inverted index (and therefore the evidence digest) never depends on filesystem
ordering.
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

POOL_SCHEMA = "radar-pool/v1"


@dataclass(frozen=True)
class Doc:
    doc_id: str
    kind: str
    title: str
    text: str
    ref: str
    extra: Dict[str, Any] = field(default_factory=dict, compare=False)

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "doc_id": self.doc_id,
            "kind": self.kind,
            "title": self.title,
            "ref": self.ref,
        }
        if self.extra:
            d["extra"] = {k: self.extra[k] for k in sorted(self.extra)}
        return d


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _json(path: str) -> Any:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _glob(root: str, pattern: str) -> List[str]:
    hits = glob.glob(os.path.join(root, pattern), recursive=True)
    return sorted(p for p in hits if os.path.isfile(p))


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if title:
                return title
    return fallback


def _doc_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


# --------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------
def _load_proposal_json(root: str, spec: Dict[str, Any]) -> List[Doc]:
    out: List[Doc] = []
    id_field = spec.get("id_field")
    title_field = spec.get("title_field", "title")
    text_fields = spec.get("text_fields") or []
    for path in _glob(root, spec["glob"]):
        payload = _json(path)
        if not isinstance(payload, dict):
            continue
        ref = _rel(root, path)
        did = str(payload.get(id_field, ref)) if id_field else ref
        parts = []
        for f in text_fields:
            val = payload.get(f)
            if isinstance(val, str):
                parts.append(val)
            elif isinstance(val, (list, tuple)):
                parts.extend(str(v) for v in val if isinstance(v, (str, int, float)))
        title = str(payload.get(title_field) or did)
        out.append(
            Doc(
                doc_id=_doc_id(spec.get("kind_tag", "proposal"), did),
                kind=spec.get("kind_tag", "proposal"),
                title=title,
                text="\n".join(p for p in parts if p),
                ref=ref,
                extra={"sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()},
            )
        )
    return out


def _load_markdown(root: str, spec: Dict[str, Any]) -> List[Doc]:
    out: List[Doc] = []
    kind = spec.get("kind_tag", "doc")
    limit = int(spec.get("max_chars") or 0)
    for path in _glob(root, spec["glob"]):
        ref = _rel(root, path)
        body = _read(path)
        title = _first_heading(body, os.path.basename(path))
        text = body[:limit] if limit else body
        out.append(
            Doc(
                doc_id=_doc_id(kind, ref),
                kind=kind,
                title=title,
                text=text,
                ref=ref,
            )
        )
    return out


def load_module_manifest(path: str, kind_tag: str = "module") -> List[Doc]:
    """Expand a repo module inventory into one ``Doc`` per module."""
    payload = _json(path)
    repos = payload.get("repos") or []
    out: List[Doc] = []
    for repo in repos:
        name = repo.get("repo") or "unknown"
        for mod in repo.get("modules") or []:
            mid = str(mod.get("id") or "")
            text = "\n".join(
                str(mod.get(k) or "") for k in ("title", "text", "symbols", "doc")
            )
            out.append(
                Doc(
                    doc_id=_doc_id(kind_tag, f"{name}/{mid}"),
                    kind=kind_tag,
                    title=str(mod.get("title") or mid),
                    text=text,
                    ref=str(mod.get("ref") or f"{name}:{mid}"),
                    extra={"repo": name},
                )
            )
    return out


def load_campaign_export(path: str, kind_tag: str = "campaign") -> List[Doc]:
    """Expand a campaign plan/node export into one ``Doc`` per plan and node."""
    payload = _json(path)
    out: List[Doc] = []
    for plan in sorted(payload.get("plans") or [], key=lambda p: str(p.get("id") or p.get("plan_id") or "")):
        pid = str(plan.get("id") or plan.get("plan_id") or "")
        text = "\n".join(
            str(plan.get(k) or "")
            for k in ("title", "goal", "objective", "summary", "milestones")
        )
        out.append(
            Doc(
                doc_id=_doc_id(kind_tag + ".plan", pid),
                kind=kind_tag + ".plan",
                title=str(plan.get("title") or pid),
                text=text,
                ref=f"{os.path.basename(path)}#{pid}",
            )
        )
    for node in sorted(payload.get("nodes") or [], key=lambda n: str(n.get("node_id") or n.get("id") or "")):
        pid = str(node.get("plan_id") or "")
        nid = str(node.get("node_id") or node.get("id") or "")
        text = "\n".join(
            str(node.get(k) or "")
            for k in ("title", "goal", "objective", "summary", "acceptance")
        )
        out.append(
            Doc(
                doc_id=_doc_id(kind_tag + ".node", f"{pid}/{nid}"),
                kind=kind_tag + ".node",
                title=str(node.get("title") or nid),
                text=text,
                ref=f"{os.path.basename(path)}#{pid}/{nid}",
            )
        )
    out.sort(key=lambda d: d.doc_id)
    return out


_ADAPTERS = {
    "proposal_json": _load_proposal_json,
    "markdown": _load_markdown,
}


def load_pool(root: str, manifest_path: str) -> List[Doc]:
    """Load and return all docs of the pool described by ``manifest_path``.

    ``exclude_globs`` in the manifest are matched against repo-relative doc refs
    and dropped *after* loading.  This exists because benchmark or tool
    documentation often quotes the very topics it is about: indexing that text
    would let the radar "find" evidence it manufactured itself (a self-pollution
    loop).  The exclusion is data, so the pool stays auditable.
    """
    manifest = _json(manifest_path)
    if manifest.get("schema") != POOL_SCHEMA:
        raise ValueError(
            f"pool manifest schema {manifest.get('schema')!r} != {POOL_SCHEMA!r}"
        )
    exclude = tuple(manifest.get("exclude_globs") or ())
    docs: List[Doc] = []
    for spec in manifest.get("sources") or []:
        kind = spec.get("type")
        if kind in _ADAPTERS:
            docs.extend(_ADAPTERS[kind](root, spec))
        elif kind == "module_manifest":
            docs.extend(
                load_module_manifest(
                    os.path.join(root, spec["path"]), spec.get("kind_tag", "module")
                )
            )
        elif kind == "campaign_export":
            docs.extend(
                load_campaign_export(
                    os.path.join(root, spec["path"]), spec.get("kind_tag", "campaign")
                )
            )
        else:
            raise ValueError(f"unknown pool source type {kind!r}")
    if exclude:
        docs = [d for d in docs if not any(fnmatch.fnmatch(d.ref, pat) for pat in exclude)]
    # de-duplicate by doc_id, keep first, then sort -> filesystem-order free
    seen = {}
    for d in docs:
        seen.setdefault(d.doc_id, d)
    return [seen[k] for k in sorted(seen)]


def pool_digest(docs: Sequence[Doc]) -> str:
    """Content digest of the pool: ids + refs + indexed text."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda x: x.doc_id):
        h.update(d.doc_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(d.title.encode("utf-8"))
        h.update(b"\x00")
        h.update(d.text.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()