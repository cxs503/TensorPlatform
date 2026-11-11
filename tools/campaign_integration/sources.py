"""战役证据收集层：异构来源 → 可复算 ``presence`` 文档 + 严格证据规格。

平台共性机制层，**零领域语义**：节点名/仓库/判据文字/证据目录全部由 JSON 工件注入
（``data/campaign_integration/*.json``），本模块只提供「声明 → 定位 → 计量」的通用机器。

三类来源（全部只读，绝不写别人的工作树）

1. **战役注册表快照** —— ``/api/v1/tensor/campaigns/plans/{plan_id}/nodes`` 的落盘快照
   （:mod:`tools.campaign_integration.fetch` 抓取；快照 sha256 入审计链）；
2. **节点工作树的规范交付位** —— ``<work_root>/m<mission_id>/<evidence_dir>/<node_key>.json``
   （协议见 ``docs/CAMPAIGN_EVIDENCE_PROTOCOL.md``），可另配额外 glob；
3. **报告索引** —— 快照里每个 mission 的 ``tensor_reports`` 条目数（**辅助信号，不作判据**：
   报告存在不等于证据可复算）。

三条硬规矩（anti-fake-pass 的供给侧）

* **缺就写缺**：节点零 artifact 也照落 presence 文档（``delivery.delivered=false``），
  不跳过、不补值 —— 「没交付」是可复算事实，不是「不知道」；
* **值只来自磁盘**：sha256 由文件字节算出，路径相对节点工作树；收集器不推算、不猜单位；
* **自述与实测分离**：``claim.*`` 只抄节点自报（mission status），``delivery.*`` 由本层实测；
  两者的差是上游互裁（``claim_mismatch``）的入口。

由此得到的证据规格（:func:`build_spec`）交给 :mod:`tools.maturity_gate` 判定：
证据文档不存在 → 判据 ``WITHHELD``；存在但值不达标 → ``FAIL``；自述与复算相反 →
``claim_mismatch``（不改判，但一票入账）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tools.maturity_gate.evidence import payload_digest, sha256_file

SOURCE_SCHEMA = "campaign-sources/1.0"
PRESENCE_SCHEMA = "campaign-presence/1.0"
SPEC_SCHEMA = "1.0"

_TOP_KEYS = {"schema", "work_root", "worktree_template", "evidence_dir",
             "evidence_doc_template", "collect_patterns", "integration_out", "report_path",
             "report_figure_dir", "spec_path", "defaults", "nodes", "notes",
             "integration_node_key", "self_capability"}

#: mission 的「已交付」状态词（任务机历史上有 completed / done 两种写法，统一在此声明，
#: 由配置 ``defaults.completed_states`` 覆盖——不把某一种写法写死成唯一真相）。
DEFAULT_COMPLETED_STATES = ("completed", "done")

#: 证据协议（docs/CAMPAIGN_EVIDENCE_PROTOCOL.md）判据依赖的顶层块
PROTOCOL_BLOCKS = ("claim", "delivery", "pytest", "acceptance")


class SourcesError(ValueError):
    """来源配置本身不合法（配置错 ≠ 判据不过）。"""


# ---------------------------------------------------------------- 配置装载
def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_sources(path: str | Path) -> Dict[str, Any]:
    cfg = load_json(path)
    if not isinstance(cfg, dict):
        raise SourcesError("sources 顶层必须是对象")
    unknown = sorted(set(cfg) - _TOP_KEYS)
    if unknown:
        raise SourcesError(f"sources: 未知键 {unknown}（防笔误静默降级）")
    if cfg.get("schema") != SOURCE_SCHEMA:
        raise SourcesError(f"sources: 不支持的 schema {cfg.get('schema')!r}（需 {SOURCE_SCHEMA}）")
    for key in ("work_root", "worktree_template", "evidence_dir"):
        if not cfg.get(key):
            raise SourcesError(f"sources: 缺少必填键 {key}")
    return cfg


# ---------------------------------------------------------------- 定位与计量
def worktree_root(cfg: Mapping[str, Any], mission_id: int) -> Path:
    return Path(str(cfg["work_root"])) / str(cfg["worktree_template"]).format(
        mission_id=mission_id)


def expand_patterns(cfg: Mapping[str, Any], node_key: str,
                    mission_id: Optional[int] = None) -> List[str]:
    """收集模式 = 全局 ``collect_patterns`` + 节点专属 ``extra_patterns``（数据驱动）。

    模板可用占位符：``{evidence_dir}`` / ``{node_key}`` / ``{mission_id}``。
    """
    extra = ((cfg.get("nodes") or {}).get(node_key) or {}).get("extra_patterns") or []
    raw = list(cfg.get("collect_patterns") or []) + list(extra)
    return [str(p).format(evidence_dir=cfg["evidence_dir"], node_key=node_key,
                          mission_id="" if mission_id is None else mission_id) for p in raw]


def scan_artifacts(worktree: Path, patterns: Sequence[str]) -> List[Dict[str, Any]]:
    """按模式扫工作树，返回 ``path/sha256/bytes``（路径相对工作树，字节指纹算自文件）。"""
    seen: Dict[str, Path] = {}
    for pat in patterns:
        for p in sorted(worktree.glob(pat)):
            if p.is_file():
                seen.setdefault(str(p.relative_to(worktree)), p)
    out: List[Dict[str, Any]] = []
    for rel in sorted(seen):
        p = seen[rel]
        rec: Dict[str, Any] = {"path": rel, "bytes": p.stat().st_size,
                              "sha256": sha256_file(p), "json": False,
                              "protocol_blocks": []}
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict):
            rec["json"] = True
            rec["protocol_blocks"] = [k for k in PROTOCOL_BLOCKS if k in payload]
        out.append(rec)
    return out


def git_dirty_count(root: Path) -> Optional[int]:
    """工作树脏文件数（只读）。取不到就返回 ``None`` —— 不猜。"""
    try:
        proc = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return len([line for line in proc.stdout.splitlines() if line.strip()])


# ---------------------------------------------------------------- presence 文档
def _completed_states(cfg: Mapping[str, Any]) -> Sequence[str]:
    states = (cfg.get("defaults") or {}).get("completed_states") or DEFAULT_COMPLETED_STATES
    return tuple(str(x) for x in states)


def build_presence(node: Mapping[str, Any], cfg: Mapping[str, Any],
                   reports: Sequence[Any]) -> Dict[str, Any]:
    """单个节点的 presence 文档：实测交付态 + 上游自述 + 声明验收清单（不改一个字）。"""
    mission_id = int(node["mission_id"])
    wt = worktree_root(cfg, mission_id)
    artifacts = (scan_artifacts(wt, expand_patterns(cfg, node["node_key"], mission_id))
                 if wt.is_dir() else [])
    declared = list(((node.get("metadata") or {}).get("acceptance_criteria")) or [])
    status = str(node.get("status") or "")
    conformant = [a for a in artifacts
                  if set(PROTOCOL_BLOCKS).issubset(set(a.get("protocol_blocks") or []))]
    return {
        "schema": PRESENCE_SCHEMA,
        "node_key": node["node_key"],
        "mission_id": mission_id,
        "campaign_node_id": node.get("campaign_node_id"),
        "repo": node.get("repo"),
        "mission": {
            "status": node.get("status"),
            "block_reason": node.get("block_reason"),
            "consecutive_fails": node.get("consecutive_fails"),
            "actual_end": node.get("actual_end"),
        },
        "worktree": {
            "root": str(wt),
            "exists": wt.is_dir(),
            "dirty_paths": git_dirty_count(wt) if wt.is_dir() else None,
        },
        "delivery": {
            "delivered": bool(artifacts),
            "artifact_count": len(artifacts),
            "protocol_conformant": bool(conformant),
            "protocol_conformant_count": len(conformant),
            "artifacts": artifacts,
        },
        "reports": {"count": len(reports),
                    "ids": [str((r or {}).get("id")) if isinstance(r, dict) else str(r)
                            for r in reports],
                    "items": [dict(r) if isinstance(r, dict) else {"id": r} for r in reports]},
        "claim": {"delivered": status in _completed_states(cfg)},
        "declared_acceptance": {"count": len(declared), "items": declared},
    }


def upstream_keys(cfg: Mapping[str, Any], snapshot: Mapping[str, Any]) -> List[str]:
    """上游节点键（剔除集成节点自身：自己评自己不算交付覆盖率）。"""
    own = cfg.get("integration_node_key")
    return [str(n["node_key"]) for n in (snapshot.get("nodes") or [])
            if str(n["node_key"]) != str(own)]


def collect(snapshot: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """全战役 presence 收集：返回 ``{presence, summary, provenance}``。"""
    nodes = list(snapshot.get("nodes") or [])
    reports = snapshot.get("reports") or {}
    presence: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        rid = reports.get(str(node.get("mission_id"))) or []
        presence[node["node_key"]] = build_presence(node, cfg, rid)

    status_counts: Dict[str, int] = {}
    for key in upstream_keys(cfg, snapshot):
        st = str(presence[key]["mission"]["status"])
        status_counts[st] = status_counts.get(st, 0) + 1
    upstream = upstream_keys(cfg, snapshot)
    delivered = [k for k in upstream if presence[k]["delivery"]["delivered"]]
    infra_blocked = [k for k in upstream
                     if "infra" in str(presence[k]["mission"]["block_reason"] or "")]
    summary = {
        "nodes_total": len(upstream),
        "nodes_with_presence": len(presence),
        "nodes_delivered": len(delivered),
        "delivered_keys": sorted(delivered),
        "artifact_total": sum(presence[k]["delivery"]["artifact_count"] for k in upstream),
        "artifact_coverage": (len(delivered) / len(upstream)) if upstream else 0.0,
        "mission_status_counts": dict(sorted(status_counts.items())),
        "infra_blocked_keys": sorted(infra_blocked),
        "report_total": sum(presence[k]["reports"]["count"] for k in upstream),
        "protocol_conformant_keys": sorted(k for k in upstream
                                           if presence[k]["delivery"]["protocol_conformant"]),
        "artifact_with_json": sum(1 for k in upstream
                                  for a in presence[k]["delivery"]["artifacts"] if a.get("json")),
    }
    provenance = {
        "sources_config_digest": payload_digest(cfg),
        "snapshot_digest": payload_digest(snapshot),
        "presence_digests": {k: payload_digest(v) for k, v in sorted(presence.items())},
    }
    return {"presence": presence, "summary": summary, "provenance": provenance}


# ---------------------------------------------------------------- 证据规格
def _doc_path(cfg: Mapping[str, Any], node_key: str) -> str:
    tpl = cfg.get("evidence_doc_template") or (str(cfg["evidence_dir"]) + "/{node_key}.json")
    return str(tpl).format(node_key=node_key, evidence_dir=cfg["evidence_dir"])


def build_spec(snapshot: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """由战役注册表快照**程序化生成**证据规格（零手抄；验收条目数直接取自 metadata）。"""
    nodes_out: List[Dict[str, Any]] = []
    own = cfg.get("integration_node_key")
    for node in (snapshot.get("nodes") or []):
        key = node["node_key"]
        if str(key) == str(own):
            continue
        doc = _doc_path(cfg, key)
        declared = len(((node.get("metadata") or {}).get("acceptance_criteria")) or [])
        need = max(1, declared)
        spec_node: Dict[str, Any] = {
            "node_key": key,
            "title": str(node.get("title") or key),
            "repo": str(node.get("repo") or "unknown"),
            "mission_id": int(node["mission_id"]),
            "capability": str(node.get("title") or key),
            "criteria": [
                {
                    "criterion_id": "delivery",
                    "statement": "节点在规范交付位产出 ≥1 份可复算证据 artifact",
                    "unit": "count",
                    "checks": [
                        {"doc": doc, "pointer": "delivery.artifact_count",
                         "comparator": "ge", "threshold": 1, "unit": "count"},
                        {"doc": doc, "pointer": "delivery.delivered",
                         "comparator": "eq", "threshold": True, "unit": "bool",
                         "claim": "claim.delivered"},
                    ],
                },
                {
                    "criterion_id": "tests",
                    "statement": "节点证据自报 pytest returncode=0 且 failed=0",
                    "unit": "count",
                    "checks": [
                        {"doc": doc, "pointer": "pytest.returncode",
                         "comparator": "eq", "threshold": 0, "unit": "code"},
                        {"doc": doc, "pointer": "pytest.failed",
                         "comparator": "le", "threshold": 0, "unit": "count"},
                    ],
                },
                {
                    "criterion_id": "acceptance",
                    "statement": (f"声明验收判据（{declared} 条）全数被可复算数值覆盖，"
                                  "且自述 all_pass 不得超过覆盖数"),
                    "unit": "count",
                    "checks": [
                        {"doc": doc, "pointer": "acceptance.declared_total",
                         "comparator": "eq", "threshold": declared, "unit": "count"},
                        {"doc": doc, "pointer": "acceptance.covered_values",
                         "comparator": "ge", "threshold": need, "unit": "count",
                         "claim": "acceptance.claim_all_pass"},
                    ],
                },
            ],
        }
        nodes_out.append(spec_node)
    return {
        "spec_id": str(snapshot.get("spec_id") or "campaign-integration"),
        "schema_version": SPEC_SCHEMA,
        "title": str((snapshot.get("plan") or {}).get("name") or "campaign"),
        "criteria_pass_threshold": float(cfg.get("defaults", {}).get(
            "criteria_pass_threshold", 0.8)),
        "notes": "由 tools.campaign_integration.sources.build_spec 从注册表快照程序化生成，零手抄。",
        "nodes": nodes_out,
    }


# ---------------------------------------------------------------- 落盘
def write_json(payload: Any, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def write_presence(presence: Mapping[str, Mapping[str, Any]], out_dir: str | Path) -> List[Path]:
    """把每个节点的 presence 文档落到 ``<out_dir>/presence/<node_key>.json``（一节点一文件）。"""
    out = Path(out_dir) / "presence"
    paths = []
    for key in sorted(presence):
        paths.append(write_json(presence[key], out / f"{key}.json"))
    return paths


__all__ = ["SOURCE_SCHEMA", "PRESENCE_SCHEMA", "upstream_keys", "SourcesError", "load_json", "load_sources",
           "worktree_root", "expand_patterns", "scan_artifacts", "git_dirty_count",
           "build_presence", "collect", "build_spec", "write_json", "write_presence"]