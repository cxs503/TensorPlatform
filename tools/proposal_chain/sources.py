"""数据源：历史提案池（磁盘）+ 战役节点（HTTP API 或已采集快照）。

两条路径都只提供**文本与结构化键**，不含域词表：

* 提案池：递归扫 ``proposals_root/**/*.json``，把任何形如 ``{"proposals": [...]}``
  的对象展开成提案条目；坏 JSON 文件**跳过并计数**（真实池里确有 1 个坏文件——这是鲁棒性
  要求，不是异常）。
* 战役节点：优先读 ``paths.nodes_snapshot``（已采集快照，离线可复算）；
  快照缺失时才走 HTTP（``sources.campaign_api`` + Bearer token）。两条路径产出同一 schema。

归一后的 :class:`~tools.proposal_chain.dedup.Doc`：
``campaign`` = 提案的波次目录（如 ``p1/w1``）/ 节点的 ``plan_id``，
``physics_domain`` = 数据里现成的域标签字段（有则记、无则空——代码不猜）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import REPO_ROOT, ChainConfig
from .dedup import Doc

PROPOSAL_LIST_KEYS = ("proposals", "items", "proposal_pool", "pool")


@dataclass
class LoadReport:
    proposals: int = 0
    proposal_files: int = 0
    bad_json_files: list[str] = field(default_factory=list)
    nodes: int = 0
    nodes_source: str = ""
    proposals_source: str = ""
    sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposals": self.proposals,
            "proposal_files": self.proposal_files,
            "bad_json_files": list(self.bad_json_files),
            "nodes": self.nodes,
            "nodes_source": self.nodes_source,
            "proposals_source": self.proposals_source,
            "proposals_sha256": self.sha256,
        }


def _text_of(entry: Mapping[str, Any], fields: Sequence[str]) -> str:
    parts: list[str] = []
    for f in fields:
        value = entry.get(f)
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.append(" ".join(str(v) for v in value))
        elif isinstance(value, Mapping):
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            parts.append(str(value))
    return "\n".join(p for p in parts if p)


def _walk_proposals(obj: Any) -> Iterable[Mapping[str, Any]]:
    """深度优先找出所有提案条目（dict 且含 id 或 title）。"""
    if isinstance(obj, Mapping):
        for key in PROPOSAL_LIST_KEYS:
            value = obj.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        yield item
        for value in obj.values():
            if isinstance(value, (Mapping, list)):
                yield from _walk_proposals(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_proposals(item)


def load_proposals(
    cfg: ChainConfig, *, root: str | Path | None = None, prefer_snapshot: bool = True
) -> tuple[list[Doc], LoadReport]:
    """读历史提案池：优先已钉住的紧凑语料快照，否则扫盘（坏文件跳过计数）。"""
    if root is None and prefer_snapshot and bool(cfg.get("sources.prefer_corpus_snapshot", True)):
        corpus = cfg.path_of("corpus_gz")
        if corpus.exists():
            proposals, nodes = read_compact_corpus(corpus)
            report = LoadReport(
                proposals=len(proposals),
                proposal_files=0,
                nodes=len(nodes),
                proposals_source=f"snapshot:{corpus}",
                nodes_source=f"snapshot:{corpus}",
            )
            return proposals, report
    fields = list(cfg.get("tfidf.text_fields", []) or ["title", "goal"])
    base = Path(root) if root else Path(str(cfg.get("paths.proposals_root", "")))
    report = LoadReport(proposals_source=str(base))
    docs: list[Doc] = []
    if not base.exists():
        report.proposals_source = f"{base} (missing)"
        return docs, report
    digests: list[str] = []
    seen: set[str] = set()
    for path in sorted(base.rglob("*.json")):
        report.proposal_files += 1
        try:
            raw = path.read_bytes()
        except OSError:
            report.bad_json_files.append(str(path))
            continue
        digests.append(hashlib.sha256(raw).hexdigest())
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            report.bad_json_files.append(str(path))
            continue
        campaign = path.parent.relative_to(base).as_posix() if path.parent != base else "."
        for k, entry in enumerate(_walk_proposals(payload)):
            title = str(entry.get("title") or entry.get("name") or "").strip()
            pid = str(entry.get("id") or entry.get("proposal_id") or f"{path.stem}#{k}")
            if pid in seen:
                pid = f"{pid}@{campaign}"
            seen.add(pid)
            text = _text_of(entry, fields)
            docs.append(
                Doc(
                    id=pid,
                    text=text,
                    campaign=campaign,
                    repo=str(entry.get("repo") or entry.get("repository") or ""),
                    kind="proposal",
                    physics_domain=str(
                        entry.get("physics_domain") or entry.get("domain") or ""
                    ),
                )
            )
    report.proposals = len(docs)
    report.sha256 = hashlib.sha256("".join(sorted(digests)).encode()).hexdigest()
    return docs, report


def load_nodes_snapshot(path: str | Path, cfg: ChainConfig) -> list[Doc]:
    """读已采集节点快照（list 或 {items: [...]}）→ Doc 列表。"""
    p = Path(path)
    if not p.exists():
        return []
    payload = json.loads(p.read_text(encoding="utf-8"))
    rows = payload.get("items") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return []
    return nodes_to_docs(rows, cfg)


def nodes_to_docs(rows: Sequence[Mapping[str, Any]], cfg: ChainConfig) -> list[Doc]:
    fields = list(cfg.get("tfidf.text_fields", []) or ["title", "goal"])
    max_chars = int(cfg.get("sources.node_text_max_chars", 4000))
    out: list[Doc] = []
    for row in rows:
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if not isinstance(meta, Mapping):
            meta = {}
        merged = dict(meta)
        merged.setdefault("title", row.get("name") or "")
        text = "\n".join(
            [
                str(row.get("name") or ""),
                _text_of(merged, fields),
            ]
        ).strip()[:max_chars]
        out.append(
            Doc(
                id=f"node:{row.get('id')}",
                text=text,
                campaign=str(row.get("plan_id") or merged.get("plan_id") or ""),
                repo=str(merged.get("repo") or ""),
                kind="campaign_node",
                physics_domain=str(merged.get("physics_domain") or ""),
            )
        )
    return out


def fetch_live_nodes(cfg: ChainConfig, *, token: str | None = None) -> list[dict[str, Any]]:
    """HTTP 采集战役节点（只读 GET；无 token / 不可达 → 返回空，由调用方决定降级）。"""
    api = str(cfg.get("sources.campaign_api", "")).rstrip("/")
    if not api:
        return []
    token = token or os.environ.get("TENSOR_TOKEN")
    if not token:
        try:  # 复用仓库内的登录流程（用户名/口令来自环境，不落盘）
            user, passwd = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")
            if not user or not passwd:
                return []
            req = urllib.request.Request(
                f"{api}/auth/login",
                data=json.dumps({"username": user, "password": passwd}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                token = json.loads(resp.read())["access_token"]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, OSError):
            return []

    def get(path: str) -> Any:
        req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    try:
        plans = get(f"/tensor/campaigns/plans?limit={int(cfg.get('sources.plans_limit', 200))}")
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return []
    items = plans.get("items") if isinstance(plans, Mapping) else plans
    rows: list[dict[str, Any]] = []
    for plan in items or []:
        pid = plan.get("id")
        try:
            payload = get(f"/tensor/campaigns/plans/{pid}/nodes")
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            continue
        nodes = payload.get("items") if isinstance(payload, Mapping) else payload
        for node in nodes or []:
            node = dict(node)
            node.setdefault("plan_id", pid)
            rows.append(node)
    return rows


def load_nodes(
    cfg: ChainConfig, *, allow_live: bool = False, token: str | None = None
) -> tuple[list[Doc], list[dict[str, Any]], LoadReport]:
    """节点加载：快照优先；``allow_live`` 时快照缺失才联网。"""
    report = LoadReport()
    snapshot = cfg.path_of("nodes_snapshot")
    gz_snapshot = snapshot.with_suffix(".json.gz")
    rows: list[dict[str, Any]] = []

    def _read_snapshot(path: Path) -> list[dict[str, Any]]:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as fh:
                payload = json.loads(fh.read().decode("utf-8"))
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        out = payload.get("items") if isinstance(payload, Mapping) else payload
        return list(out or [])

    if not allow_live and (gz_snapshot.exists() or snapshot.exists()):
        chosen = gz_snapshot if gz_snapshot.exists() else snapshot
        rows = _read_snapshot(chosen)
        report.nodes_source = str(chosen)
    else:
        rows = fetch_live_nodes(cfg, token=token)
        report.nodes_source = "live:" + str(cfg.get("sources.campaign_api", ""))
        if not rows and (gz_snapshot.exists() or snapshot.exists()):
            chosen = gz_snapshot if gz_snapshot.exists() else snapshot
            rows = _read_snapshot(chosen)
            report.nodes_source = str(chosen) + " (live unavailable)"
    rows = list(rows or [])
    report.nodes = len(rows)
    return nodes_to_docs(rows, cfg), rows, report


def write_compact_corpus(
    path: str | Path,
    proposals: Sequence[Doc],
    nodes: Sequence[Doc],
) -> str:
    """把两池写成 gzip 紧凑工件（离线复算用），返回 sha256。"""
    payload = {
        "schema_version": "proposal-chain-corpus/1.0",
        "proposals": [
            {
                "id": d.id,
                "campaign": d.campaign,
                "repo": d.repo,
                "physics_domain": d.physics_domain,
                "text": d.text,
            }
            for d in proposals
        ],
        "nodes": [
            {
                "id": d.id,
                "campaign": d.campaign,
                "repo": d.repo,
                "physics_domain": d.physics_domain,
                "text": d.text,
            }
            for d in nodes
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wb", compresslevel=9) as fh:
        fh.write(blob)
    return hashlib.sha256(blob).hexdigest()


def read_compact_corpus(path: str | Path) -> tuple[list[Doc], list[Doc]]:
    p = Path(path)
    if not p.exists():
        return [], []
    with gzip.open(p, "rb") as fh:
        payload = json.loads(fh.read().decode("utf-8"))

    def to_docs(rows: Iterable[Mapping[str, Any]], kind: str) -> list[Doc]:
        return [
            Doc(
                id=str(r["id"]),
                text=str(r.get("text") or ""),
                campaign=str(r.get("campaign") or ""),
                repo=str(r.get("repo") or ""),
                kind=kind,
                physics_domain=str(r.get("physics_domain") or ""),
            )
            for r in rows
        ]

    return (
        to_docs(payload.get("proposals", []), "proposal"),
        to_docs(payload.get("nodes", []), "campaign_node"),
    )