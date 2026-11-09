"""repro_audit.canonical —— 任务交付物的规范化表示（零领域语义）。

把异构的任务元数据（节点 JSON、工件路径、判据文本）归一化成统一结构，
供后续扫描/抽样/复证/报告模块消费。所有字段名都是结构性的（mission_id、
artifact_path、criteria_text），不含任何学科词或仓名常量。

设计约束：
  * **幂等**：同输入同输出（sort_keys=True，无 wall-clock 派生字段）。
  * **容错**：缺失字段用默认值填充，不抛异常（下游模块自行判空）。
  * **可验证**：每个规范化对象带 sha256 摘要，便于溯源与去重。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

# 工件类型识别（结构性正则，无领域词）
_ARTIFACT_TYPE_RE = {
    "json": re.compile(r"\.json$", re.I),
    "csv": re.compile(r"\.csv$", re.I),
    "md": re.compile(r"\.md$", re.I),
    "png": re.compile(r"\.png$", re.I),
    "py": re.compile(r"\.py$", re.I),
}

# 判据文本里的数值阈值抽取（与 core.parse_tolerance 对齐）
_TOL_RE = re.compile(r"(?:<|≤|<=|within)\s*([0-9]+(?:\.\d+)?)\s*%")


def normalize_mission_id(value: Any) -> int | None:
    """任务编号归一化：支持 int/str/None，非法值返回 None（不抛异常）。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        return int(m.group()) if m else None
    return None


def normalize_artifact_path(path: str, mission_id: int | None = None) -> dict[str, Any]:
    """工件路径归一化：提取类型、任务关联、相对路径。

    返回 ``{path, type, mission_scoped, rel_path}``。
    """
    path = str(path).strip()
    artifact_type = "unknown"
    for typ, pattern in _ARTIFACT_TYPE_RE.items():
        if pattern.search(path):
            artifact_type = typ
            break
    
    mission_scoped = False
    if mission_id is not None:
        patterns = [f"m{mission_id}", f"artifacts/m{mission_id}", f"report_m{mission_id}"]
        mission_scoped = any(p in path for p in patterns)
    
    # 相对路径（去掉前导 / 或 ./）
    rel_path = re.sub(r"^(?:\./|/)+", "", path)
    
    return {
        "path": path,
        "type": artifact_type,
        "mission_scoped": mission_scoped,
        "rel_path": rel_path,
    }


def normalize_criteria(criteria: Iterable[str] | str | None) -> list[dict[str, Any]]:
    """判据文本归一化：抽取数值阈值、分类标签。

    返回 ``[{text, tolerance, category}]``。
    """
    if not criteria:
        return []
    texts = [criteria] if isinstance(criteria, str) else list(criteria)
    result = []
    for text in texts:
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue
        
        # 抽取数值阈值
        tolerances = []
        for m in _TOL_RE.finditer(text):
            tolerances.append(float(m.group(1)) / 100.0)
        
        # 分类（结构性标签，无领域词）
        category = "unknown"
        low = text.lower()
        if any(w in low for w in ("误差", "error", "deviation", "diff")):
            category = "numerical_accuracy"
        elif any(w in low for w in ("收敛", "converge", "tolerance")):
            category = "convergence"
        elif any(w in low for w in ("守恒", "conservation", "balance")):
            category = "conservation"
        
        result.append({
            "text": text,
            "tolerances": sorted(tolerances),
            "category": category,
        })
    
    return result


def canonicalize_node(node: dict[str, Any]) -> dict[str, Any]:
    """节点元数据归一化：提取关键字段、生成摘要。

    返回 ``{mission_id, name, repo, status, criteria, artifacts_sha256}``。
    """
    mission_id = normalize_mission_id(node.get("mission_id"))
    meta = node.get("metadata") or {}
    
    criteria = normalize_criteria(meta.get("acceptance_criteria"))
    
    # 工件列表（从 metadata.artifacts 或 node.artifacts 抽取）
    artifacts_raw = meta.get("artifacts") or node.get("artifacts") or []
    artifacts = [normalize_artifact_path(a, mission_id) for a in artifacts_raw if isinstance(a, str)]
    
    # 摘要（对关键字段排序后哈希）
    digest_fields = {
        "mission_id": mission_id,
        "name": node.get("name"),
        "repo": meta.get("repo"),
        "status": node.get("status"),
        "criteria_count": len(criteria),
        "artifacts_count": len(artifacts),
    }
    digest = hashlib.sha256(
        json.dumps(digest_fields, sort_keys=True).encode()
    ).hexdigest()[:16]
    
    return {
        "mission_id": mission_id,
        "name": node.get("name"),
        "repo": meta.get("repo"),
        "status": node.get("status"),
        "criteria": criteria,
        "artifacts": artifacts,
        "sha256": digest,
    }


def canonicalize_deliverable(node: dict[str, Any], artifact_path: str) -> dict[str, Any]:
    """单个交付物归一化：节点 + 工件的联合表示。

    返回 ``{node, artifact, composite_sha256}``。
    """
    canon_node = canonicalize_node(node)
    mission_id = canon_node["mission_id"]
    canon_artifact = normalize_artifact_path(artifact_path, mission_id)
    
    composite = {
        "node_sha256": canon_node["sha256"],
        "artifact_path": canon_artifact["path"],
        "artifact_type": canon_artifact["type"],
    }
    composite_sha = hashlib.sha256(
        json.dumps(composite, sort_keys=True).encode()
    ).hexdigest()[:16]
    
    return {
        "node": canon_node,
        "artifact": canon_artifact,
        "composite_sha256": composite_sha,
    }


def batch_canonicalize(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量归一化：过滤无效节点、按 mission_id 排序。"""
    results = []
    for node in nodes:
        canon = canonicalize_node(node)
        if canon["mission_id"] is not None:
            results.append(canon)
    return sorted(results, key=lambda x: x["mission_id"])


def deduplicate_by_sha256(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 sha256 去重（保留首次出现的）。"""
    seen = set()
    result = []
    for item in items:
        sha = item.get("sha256") or item.get("composite_sha256")
        if sha and sha in seen:
            continue
        if sha:
            seen.add(sha)
        result.append(item)
    return result
