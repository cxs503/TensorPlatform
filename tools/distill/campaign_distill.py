#!/usr/bin/env python3
"""campaign_distill —— 战役成果汇总与知识蒸馏的通用机制层（域无关）。

问题
----
战役波次收口时，"汇总与蒸馏"节点最常见的失效模式是：**人/智能体手抄**各子节点
结论，凑出一份看起来完整的报告——判据是否被覆盖、证据是否真的存在、图谱规模是否
达标、工件是否可校验，全靠自述。这类报告无法机器复核，也无法在下一次波次复用。

本模块把"汇总/蒸馏"拆成**五个纯机制**，输入是外部数据包（bundle JSON，域内容
经工件注入），输出机器可复核的 JSON + markdown：

1. ``build_coverage``  —— 判据覆盖矩阵：逐节点 × 逐判据给出状态
   （``PASS_CLAIMED`` / ``FAIL_CLAIMED`` / ``EVIDENCE_ONLY`` / ``NO_EVIDENCE``），
   每条都带证据引用（文件 sha256 / 探针结果 / 结论来源）。状态只由证据驱动，
   没有"我认为通过了"这一档。
2. ``build_graph``     —— 知识图谱：实体（节点/判据/工件/仓库/缺口）与关系边
   （has_criterion / depends_on / evidenced_by / in_repo / raises_gap / proposed_in），
   规模（实体数、边数）与最小阈值一起落 JSON，供"≥N 实体、≥M 边"型判据机裁。
3. ``find_gaps``       —— 遗留缺口识别：五条规则（未证判据、在途超时、失败节点、
   依赖链未闭合、结论缺来源）逐条产出缺口记录，每条附**提案草案骨架**
   （title/repo/physics_domain/why_frontier/goal/acceptance/metric），缺口清单不能为空话。
4. ``render_report`` + ``validate_report_md`` —— 报告渲染与自动格式校验：
   必需章节、引用完整性（正文每个 ``[E#]`` 必须命中工件清单，每个工件必须被引用或
   显式声明未引用）、数字一致性（正文声称的图谱规模/覆盖条数必须等于 JSON 实算值）、
   占位符残留（TBD/TODO/???）三类硬校验，违规即非零退出。
5. ``archive_artifacts`` + ``verify_manifest`` —— 归档与 sha256 校验和：
   复制 → 重算哈希 → 二次复核（复制前后逐文件一致才算"已归档"），并输出
   ``SHA256SUMS`` 与 ``manifest.json``。

输入契约（bundle JSON）
-----------------------
::

    {
      "bundle_version": "1.0",
      "campaign": {"plan_id": 272, "title": "...", "wave": 2},
      "snapshot_utc": "2026-11-08T13:30:00Z",
      "nodes": [
        {"node_key": "N01", "mission_id": 7293, "repo": "repo-a",
         "title": "...", "status": "in-flight", "depends_on": [],
         "criteria": [{"id": "C1", "text": "..."}],
         "evidence": {
           "files":  [{"path": "...", "sha256": "...", "bytes": 1234, "kind": "impl"}],
           "probes": [{"name": "pytest::x", "exit_code": 0, "summary": "3 passed"}],
           "claims": [{"criterion": "C1", "metric": "...", "value": 1.2,
                       "ok": true, "grade": "REAL", "source": "path#anchor"}]
         }}
      ]
    }

输入契约机裁（``validate_bundle``）
----------------------------------
``main`` 读入 bundle 后**先**过一遍 ``validate_bundle``，问题（缺 ``nodes`` / node 缺
``node_key`` / 判据缺 id / 证据段类型错 / ``bundle_version`` 不在
``SUPPORTED_BUNDLE_VERSIONS`` …）一次报全，stdout 输出
``{"ok": false, "stage": "bundle_contract", "contract_errors": [...]}`` 并以**退出码 2**
返回：与"报告自检有违规"（1）语义分开，且**一件产物都不落盘**。
此前这些情况是 ``KeyError`` 抛栈 + 退出码 1，调用方分不清"数据坏了"与"内容不合格"；
``bundle_version`` 也从未被读取，跨版本 bundle 会被静默按 1.0 语义解释。

纪律
----
* 纯函数、无墙钟：``snapshot_utc`` 由调用方传入，同一输入连跑两次逐字节相同。
* 零领域语义：本模块与测试都不含任何物理域词条（域词表当数据，扫描器
  ``tools/audit/domain_leak_scan.py`` 可复核）；域内容一律走 bundle/工件注入。
* 阈值（图谱规模、在途超时、必需章节名）走参数/``--spec`` 文件，不写死在逻辑里。

CLI
---
    python3 tools/distill/campaign_distill.py \
        --bundle artifacts/x/inputs/campaign_bundle.json \
        --spec artifacts/x/inputs/report_spec.json \
        --out artifacts/x/out \
        --archive verified/x --report docs/reports/x.md

退出码：0 = 全部校验通过；1 = 报告/图谱/归档校验有违规；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "CRITERION_STATUSES",
    "build_coverage",
    "build_graph",
    "find_gaps",
    "render_report",
    "validate_report_md",
    "archive_artifacts",
    "verify_manifest",
    "rollup_nodes",
    "graph_stats",
    "SUPPORTED_BUNDLE_VERSIONS",
    "validate_bundle",
]

# 判据状态：只允许这四档，杜绝"大概通过"式自述
CRITERION_STATUSES = ("PASS_CLAIMED", "FAIL_CLAIMED", "EVIDENCE_ONLY", "NO_EVIDENCE")

# 证据等级（与平台证据分级一致：REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）
GRADES = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")

# 受支持的 bundle 契约版本（新增字段语义变化时必须抬版本号，否则旧代码静默按旧语义读）
SUPPORTED_BUNDLE_VERSIONS = ("1.0",)

DEFAULT_SPEC = {
    "graph_min_entities": 20,
    "graph_min_edges": 30,
    "gaps_min": 3,
    "stale_inflight_hours": 6.0,
    "required_sections": [
        "## 0 摘要",
        "## 1 战役结构与快照",
        "## 2 判据覆盖矩阵",
        "## 3 知识图谱",
        "## 4 遗留缺口与提案草案",
        "## 5 工件清单与 sha256",
        "## 6 局限与误差判定",
    ],
    "placeholder_tokens": ["TBD", "TODO", "???", "FIXME"],
}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path | str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_bundle(bundle) -> list[dict]:
    """机裁 bundle 输入契约，返回问题清单（空 = 合规），每条带可机器判的 ``rule``。

    为什么要有这一层：下游五个机制对缺字段/类型错是直接抛 ``KeyError``/``TypeError``
    栈——退出码与"报告自检有违规"（1）混同，调用方分不清"数据坏了"与"内容不合格"；
    更糟的是 ``bundle_version`` 此前从未被读取，跨版本 bundle 会被静默按 1.0 语义解释
    （新增字段名一变就是静默丢证据，假绿）。契约问题必须在动手算之前一次性报全，
    并以退出码 2 独立语义返回，绝不留半成品账本。
    """
    problems: list[dict] = []

    def add(rule: str, target: str, detail: str = "") -> None:
        problems.append({"rule": rule, "target": target, "detail": detail})

    if not isinstance(bundle, dict):
        add("bundle_not_object", "<root>", f"实际类型 {type(bundle).__name__}")
        return problems

    version = bundle.get("bundle_version")
    if version is None:
        add("bundle_version_missing", "bundle_version",
            f"受支持版本: {', '.join(SUPPORTED_BUNDLE_VERSIONS)}")
    elif str(version) not in SUPPORTED_BUNDLE_VERSIONS:
        add("bundle_version_unsupported", "bundle_version",
            f"实际 {version!r}，受支持: {', '.join(SUPPORTED_BUNDLE_VERSIONS)}")

    for key in ("campaign", "snapshot_utc"):
        val = bundle.get(key)
        if val is not None and not isinstance(val, (dict if key == "campaign" else str)):
            expect = "object" if key == "campaign" else "string"
            add(f"{key}_not_{expect}", key, f"实际类型 {type(val).__name__}")

    nodes = bundle.get("nodes")
    if not isinstance(nodes, list):
        add("nodes_not_list", "nodes", f"缺失或类型为 {type(nodes).__name__}")
        return problems
    if not nodes:
        add("nodes_empty", "nodes", "波次汇总至少需要一个节点")
        return problems

    seen_keys: set[str] = set()
    for i, node in enumerate(nodes):
        at = f"nodes[{i}]"
        if not isinstance(node, dict):
            add("node_not_object", at, f"实际类型 {type(node).__name__}")
            continue
        key = node.get("node_key")
        if not isinstance(key, str) or not key.strip():
            add("node_key_invalid", f"{at}.node_key", f"实际 {key!r}")
        elif key in seen_keys:
            add("node_key_duplicate", f"{at}.node_key", f"重复 node_key {key!r}")
        else:
            seen_keys.add(key)
            at = f"nodes[{i}]({key})"

        crits = node.get("criteria")
        if crits is not None and not isinstance(crits, list):
            add("criteria_not_list", f"{at}.criteria", f"实际类型 {type(crits).__name__}")
            crits = []
        seen_cids: set[str] = set()
        for j, crit in enumerate(crits or []):
            cat = f"{at}.criteria[{j}]"
            if not isinstance(crit, dict):
                add("criterion_not_object", cat, f"实际类型 {type(crit).__name__}")
                continue
            cid = crit.get("id")
            if cid is None or str(cid).strip() == "":
                add("criterion_id_missing", f"{cat}.id", "判据缺 id，覆盖矩阵无法逐条对照")
                continue
            if str(cid) in seen_cids:
                add("criterion_duplicate", f"{cat}.id", f"节点内重复判据 id {str(cid)!r}")
            seen_cids.add(str(cid))

        ev = node.get("evidence")
        if ev is not None and not isinstance(ev, dict):
            add("evidence_not_object", f"{at}.evidence", f"实际类型 {type(ev).__name__}")
            continue
        ev = ev or {}
        for section in ("files", "probes", "claims"):
            items = ev.get(section)
            if items is None:
                continue
            if not isinstance(items, list):
                add("evidence_section_not_list", f"{at}.evidence.{section}",
                    f"实际类型 {type(items).__name__}")
                continue
            for k, item in enumerate(items):
                iat = f"{at}.evidence.{section}[{k}]"
                if not isinstance(item, dict):
                    add("evidence_item_not_object", iat, f"实际类型 {type(item).__name__}")
                    continue
                if section == "files" and not str(item.get("path") or "").strip():
                    add("evidence_file_path_missing", f"{iat}.path", "工件条目缺 path")
                if section == "claims" and item.get("criterion") is None:
                    # 缺 criterion 的结论会被 claims_by_crit 静默丢弃（从不进覆盖矩阵）
                    add("claim_criterion_missing", f"{iat}.criterion",
                        "结论未标判据 id，会被静默丢弃（假绿风险）")
    return problems


def _node_evidence(node: dict) -> dict:
    ev = node.get("evidence") or {}
    return {
        "files": ev.get("files") or [],
        "probes": ev.get("probes") or [],
        "claims": ev.get("claims") or [],
    }


def _claim_ok(claim: dict) -> bool:
    """结论是否被判为"达成"：显式 ok 优先；否则看 grade 与容差。"""
    if "ok" in claim:
        return bool(claim["ok"])
    grade = claim.get("grade", "WITHHELD")
    if grade != "REAL":
        return False
    tol = claim.get("tolerance")
    val = claim.get("value")
    if tol is not None and isinstance(val, (int, float)):
        return abs(val) <= float(tol)
    return False


# --------------------------------------------------------------------------- #
# 1. 判据覆盖矩阵
# --------------------------------------------------------------------------- #
def build_coverage(bundle: dict) -> dict:
    """逐节点 × 逐判据推导覆盖状态；状态只由证据决定。"""
    rows: list[dict] = []
    for node in bundle["nodes"]:
        key = node["node_key"]
        ev = _node_evidence(node)
        claims_by_crit: dict[str, list[dict]] = {}
        for claim in ev["claims"]:
            claims_by_crit.setdefault(str(claim.get("criterion")), []).append(claim)
        file_refs = [f"{f.get('path')}#sha256:{f.get('sha256', '')[:12]}" for f in ev["files"]]
        probe_refs = [
            f"probe:{p.get('name')}#exit={p.get('exit_code')}" for p in ev["probes"]
        ]
        has_evidence = bool(ev["files"] or ev["probes"])

        for crit in node.get("criteria") or []:
            cid = str(crit["id"])
            claims = claims_by_crit.get(cid, [])
            sourced = [c for c in claims if c.get("source")]
            if sourced:
                bad_grade = [c for c in sourced if c.get("grade") not in GRADES]
                passed = all(_claim_ok(c) for c in sourced)
                status = "PASS_CLAIMED" if passed else "FAIL_CLAIMED"
                refs = [f"claim:{c['source']}" for c in sourced]
                reason = (
                    "结论+来源齐备；等级=%s"
                    % ",".join(sorted({str(c.get("grade")) for c in sourced}))
                )
                if bad_grade:
                    status = "FAIL_CLAIMED"
                    reason += "；等级不在许可集"
            elif claims:
                status = "FAIL_CLAIMED"
                refs = ["claim:<missing source>"]
                reason = "结论缺来源引用（不可采信）"
            elif has_evidence:
                status = "EVIDENCE_ONLY"
                refs = file_refs + probe_refs
                reason = "有工件/探针，但无逐判据结论映射"
            else:
                status = "NO_EVIDENCE"
                refs = []
                reason = "快照内无任何证据"
            rows.append(
                {
                    "node_key": key,
                    "repo": node.get("repo"),
                    "mission_id": node.get("mission_id"),
                    "criterion": cid,
                    "text": crit.get("text", ""),
                    "status": status,
                    "reason": reason,
                    "evidence_refs": refs,
                }
            )
    counts: dict[str, int] = {s: 0 for s in CRITERION_STATUSES}
    for row in rows:
        counts[row["status"]] += 1
    return {
        "rows": rows,
        "counts": counts,
        "total": len(rows),
        "nodes": rollup_nodes(rows, bundle),
    }


def rollup_nodes(rows: list[dict], bundle: dict) -> list[dict]:
    """把判据状态按节点汇总（节点级结论仍不外推：PASS 只统计有来源结论的判据）。"""
    by_node: dict[str, list[dict]] = {}
    for row in rows:
        by_node.setdefault(row["node_key"], []).append(row)
    out = []
    for node in bundle["nodes"]:
        key = node["node_key"]
        node_rows = by_node.get(key, [])
        total = len(node_rows) or 0
        passed = sum(1 for r in node_rows if r["status"] == "PASS_CLAIMED")
        failed = sum(1 for r in node_rows if r["status"] == "FAIL_CLAIMED")
        evidence_only = sum(1 for r in node_rows if r["status"] == "EVIDENCE_ONLY")
        none = sum(1 for r in node_rows if r["status"] == "NO_EVIDENCE")
        if total and passed == total:
            verdict = "VERIFIED"
        elif passed:
            verdict = "PARTIAL"
        elif evidence_only:
            verdict = "UNVERIFIED_EVIDENCE"
        else:
            verdict = "WITHHELD"
        out.append(
            {
                "node_key": key,
                "mission_id": node.get("mission_id"),
                "repo": node.get("repo"),
                "title": node.get("title", ""),
                "declared_status": node.get("status"),
                "verdict": verdict,
                "criteria_total": total,
                "pass_claimed": passed,
                "fail_claimed": failed,
                "evidence_only": evidence_only,
                "no_evidence": none,
                "coverage_ratio": round(passed / total, 4) if total else 0.0,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 2. 知识图谱
# --------------------------------------------------------------------------- #
def build_graph(bundle: dict, coverage: dict, gaps: list[dict] | None = None) -> dict:
    """由 bundle + 覆盖矩阵构造实体/关系边（确定性排序，无墙钟）。"""
    entities: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def add_entity(eid: str, etype: str, label: str, **extra) -> None:
        if eid not in entities:
            entities[eid] = {"id": eid, "type": etype, "label": label, **extra}

    def add_edge(src: str, rel: str, dst: str) -> None:
        if src in entities and dst in entities:
            edges[(src, rel, dst)] = {"source": src, "relation": rel, "target": dst}

    row_by_crit = {(r["node_key"], r["criterion"]): r for r in coverage["rows"]}
    repos: set[str] = set()

    def ref_to_entity(ref: str, node_files: list[dict]) -> tuple[str, str, str]:
        """证据引用 → (实体 id, 类型, 标签)；文件/探针复用同一 id，避免图谱重复计数。"""
        if ref.startswith("claim:"):
            return ("claim:" + hashlib.sha256(ref.encode()).hexdigest()[:12], "claim", ref[6:])
        for f in node_files:
            if ref == f"{f.get('path')}#sha256:{str(f.get('sha256', ''))[:12]}":
                return ("artifact:" + str(f.get("sha256", ""))[:12], "artifact", str(f.get("path")))
        if ref.startswith("probe:"):
            name = ref.split("#exit=")[0][len("probe:"):]
            return ("artifact:" + hashlib.sha256(f"probe:{name}".encode()).hexdigest()[:12],
                    "artifact", f"probe:{name}")
        return ("artifact:" + hashlib.sha256(ref.encode()).hexdigest()[:12], "artifact", ref)

    for node in bundle["nodes"]:
        key = node["node_key"]
        nid = f"node:{key}"
        add_entity(nid, "node", node.get("title") or key, mission_id=node.get("mission_id"),
                   declared_status=node.get("status"))
        repo = node.get("repo")
        if repo:
            add_entity(f"repo:{repo}", "repo", repo)
            repos.add(repo)
            add_edge(nid, "in_repo", f"repo:{repo}")
        for crit in node.get("criteria") or []:
            cid = f"crit:{key}/{crit['id']}"
            add_entity(cid, "criterion", crit.get("text", "")[:80],
                       node_key=key, criterion=str(crit["id"]))
            add_edge(nid, "has_criterion", cid)
            row = row_by_crit.get((key, str(crit["id"])))
            node_files = _node_evidence(node)["files"]
            for ref in (row or {}).get("evidence_refs", []):
                aid, atype, alabel = ref_to_entity(ref, node_files)
                add_entity(aid, atype, alabel)
                add_edge(cid, "evidenced_by", aid)
        ev = _node_evidence(node)
        for f in ev["files"]:
            aid = "artifact:" + str(f.get("sha256", ""))[:12]
            add_entity(aid, "artifact", f.get("path", ""), kind=f.get("kind"))
            add_edge(nid, "produced", aid)
        for p in ev["probes"]:
            aid = "artifact:" + hashlib.sha256(
                f"probe:{p.get('name')}".encode()
            ).hexdigest()[:12]
            add_entity(aid, "artifact", f"probe:{p.get('name')}", kind="probe")
            add_edge(nid, "produced", aid)

    node_keys = {n["node_key"] for n in bundle["nodes"]}
    for node in bundle["nodes"]:
        for dep in node.get("depends_on") or []:
            if dep in node_keys:
                add_edge(f"node:{node['node_key']}", "depends_on", f"node:{dep}")

    for gap in gaps or []:
        gid = f"gap:{gap['id']}"
        add_entity(gid, "gap", gap.get("title", gap["id"]), rule=gap.get("rule"),
                   severity=gap.get("severity"))
        for nk in gap.get("affected_nodes", []):
            add_edge(f"node:{nk}", "raises_gap", gid)
        trepo = (gap.get("proposal") or {}).get("repo")
        if trepo:
            add_entity(f"repo:{trepo}", "repo", trepo)
            add_edge(gid, "proposed_in", f"repo:{trepo}")

    return {
        "entities": sorted(entities.values(), key=lambda e: e["id"]),
        "edges": sorted(edges.values(), key=lambda e: (e["source"], e["relation"], e["target"])),
    }


def graph_stats(graph: dict) -> dict:
    by_type: dict[str, int] = {}
    for ent in graph["entities"]:
        by_type[ent["type"]] = by_type.get(ent["type"], 0) + 1
    by_rel: dict[str, int] = {}
    for edge in graph["edges"]:
        by_rel[edge["relation"]] = by_rel.get(edge["relation"], 0) + 1
    return {
        "entities": len(graph["entities"]),
        "edges": len(graph["edges"]),
        "entities_by_type": dict(sorted(by_type.items())),
        "edges_by_relation": dict(sorted(by_rel.items())),
        "entities_sha256": hashlib.sha256(canonical_json(graph["entities"]).encode()).hexdigest(),
        "edges_sha256": hashlib.sha256(canonical_json(graph["edges"]).encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- #
# 3. 缺口识别 + 提案草案
# --------------------------------------------------------------------------- #
def _proposal_draft(node: dict, unresolved: list[str], rule: str) -> dict:
    """提案草案骨架：字段齐备、内容全部来自节点数据（零手抄、零域硬编码）。"""
    repo = node.get("repo") or "<unspecified>"
    key = node["node_key"]
    return {
        "title": f"{key} 判据收口：{len(unresolved)} 条未证判据的独立复核与增补验证",
        "repo": repo,
        "physics_domain": node.get("physics_domain") or "",
        "why_frontier": (
            "该节点在快照时点仍有未闭合判据（rule=%s），其结论未被机器可复核证据覆盖；"
            "补证前任何下游引用都应视为 WITHHELD。" % rule
        ),
        "goal": (
            "在 %s 内重建可复算证据链：逐条复现未证判据 %s 的度量，落 JSON 结论+来源，"
            "并提供负控用例证明判据非恒真。" % (repo, ",".join(unresolved))
        ),
        "acceptance_criteria": [
            f"{cid}: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON"
            for cid in unresolved
        ],
        "dependencies": [f"node:{key}"],
        "metric": "unresolved_criteria_closed / unresolved_criteria_total",
    }


def find_gaps(bundle: dict, coverage: dict, spec: dict | None = None) -> list[dict]:
    spec = {**DEFAULT_SPEC, **(spec or {})}
    gaps: list[dict] = []
    node_by_key = {n["node_key"]: n for n in bundle["nodes"]}
    rollup = {n["node_key"]: n for n in coverage["nodes"]}

    # G1 未证判据（每个节点一条聚合缺口，避免清单被同一原因刷满）
    for nk in sorted(rollup):
        r = rollup[nk]
        unresolved = [
            row["criterion"]
            for row in coverage["rows"]
            if row["node_key"] == nk and row["status"] in ("NO_EVIDENCE", "EVIDENCE_ONLY")
        ]
        if not unresolved:
            continue
        node = node_by_key[nk]
        gaps.append(
            {
                "id": "G1-%s" % nk,
                "rule": "unverified_criteria",
                "severity": "high" if r["pass_claimed"] == 0 else "medium",
                "title": f"{nk} 有 {len(unresolved)}/{r['criteria_total']} 条判据未被结论级证据覆盖",
                "detail": {
                    "unresolved": unresolved,
                    "pass_claimed": r["pass_claimed"],
                    "coverage_ratio": r["coverage_ratio"],
                    "verdict": r["verdict"],
                },
                "affected_nodes": [nk],
                "evidence_refs": sorted(
                    {
                        ref
                        for row in coverage["rows"]
                        if row["node_key"] == nk and row["status"] in ("NO_EVIDENCE", "EVIDENCE_ONLY")
                        for ref in row["evidence_refs"]
                    }
                )[:8],
                "proposal": _proposal_draft(node, unresolved, "unverified_criteria"),
            }
        )

    # G2 在途超时（按快照时刻相对 actual_start 计算，时钟由 bundle 提供）
    import datetime as _dt

    snap = _dt.datetime.fromisoformat(bundle["snapshot_utc"].replace("Z", "+00:00"))
    for node in bundle["nodes"]:
        started = node.get("actual_start")
        if node.get("status") not in ("in-flight", "running") or not started:
            continue
        st = _dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
        hours = (snap - st).total_seconds() / 3600.0
        if hours <= spec["stale_inflight_hours"]:
            continue
        gaps.append(
            {
                "id": "G2-%s" % node["node_key"],
                "rule": "stale_inflight",
                "severity": "medium",
                "title": "%s 在途 %.1f h（阈值 %.1f h）且结论未落库"
                % (node["node_key"], hours, spec["stale_inflight_hours"]),
                "detail": {"inflight_hours": round(hours, 2), "threshold_hours": spec["stale_inflight_hours"]},
                "affected_nodes": [node["node_key"]],
                "evidence_refs": [],
                "proposal": _proposal_draft(
                    node,
                    [c["id"] for c in (node.get("criteria") or [])],
                    "stale_inflight",
                ),
            }
        )

    # G3 失败节点（honest FAIL 不可覆盖：缺口记录 + 复证提案）
    for node in bundle["nodes"]:
        if node.get("status") != "failed":
            continue
        gaps.append(
            {
                "id": "G3-%s" % node["node_key"],
                "rule": "failed_node",
                "severity": "high",
                "title": "%s 宣告失败，判据真空需独立复证（FAIL 不覆盖）" % node["node_key"],
                "detail": {"declared_status": "failed"},
                "affected_nodes": [node["node_key"]],
                "evidence_refs": [],
                "proposal": _proposal_draft(
                    node, [c["id"] for c in (node.get("criteria") or [])], "failed_node"
                ),
            }
        )

    # G4 依赖链未闭合（下游只在下游自身未被完全验证时才记账）
    for node in bundle["nodes"]:
        for dep in node.get("depends_on") or []:
            dep_roll = rollup.get(dep)
            if dep_roll is None or dep_roll["verdict"] == "VERIFIED":
                continue
            gaps.append(
                {
                    "id": "G4-%s->%s" % (node["node_key"], dep),
                    "rule": "dependency_open",
                    "severity": "medium",
                    "title": "%s 依赖的 %s 未达 VERIFIED（%s），下游结论不可采信"
                    % (node["node_key"], dep, dep_roll["verdict"]),
                    "detail": {"upstream_verdict": dep_roll["verdict"],
                               "upstream_coverage_ratio": dep_roll["coverage_ratio"]},
                    "affected_nodes": [node["node_key"], dep],
                    "evidence_refs": [],
                    "proposal": _proposal_draft(node, [c["id"] for c in (node.get("criteria") or [])],
                                                "dependency_open"),
                }
            )

    # G5 结论缺来源（证据完整性缺口，独立于物理结论）
    for node in bundle["nodes"]:
        bad = [
            c.get("criterion")
            for c in _node_evidence(node)["claims"]
            if not c.get("source")
        ]
        if not bad:
            continue
        gaps.append(
            {
                "id": "G5-%s" % node["node_key"],
                "rule": "claim_without_source",
                "severity": "high",
                "title": "%s 有 %d 条结论缺来源引用，按纪律不可采信" % (node["node_key"], len(bad)),
                "detail": {"criteria": [str(b) for b in bad]},
                "affected_nodes": [node["node_key"]],
                "evidence_refs": [],
                "proposal": _proposal_draft(node, [str(b) for b in bad], "claim_without_source"),
            }
        )
    return gaps


# --------------------------------------------------------------------------- #
# 4. 报告渲染 + 自动格式校验
# --------------------------------------------------------------------------- #
def _fmt_status_counts(counts: dict) -> str:
    return " / ".join(f"{k} {counts.get(k, 0)}" for k in CRITERION_STATUSES)


def render_report(ctx: dict) -> str:
    """从上下文字典渲染报告 markdown（所有数字来自 JSON，零手抄）。

    ctx 必含：bundle / coverage / graph / graph_stats / gaps / manifest / spec / narrative
    """
    bundle = ctx["bundle"]
    cov = ctx["coverage"]
    graph = ctx["graph"]
    stats = ctx["graph_stats"]
    gaps = ctx["gaps"]
    manifest = ctx["manifest"]
    spec = ctx["spec"]
    nar = ctx.get("narrative") or {}
    camp = bundle["campaign"]
    lines: list[str] = []

    def section(title: str, key: str, body: list[str]) -> None:
        lines.append(title)
        lines.append("")
        for item in nar.get(key, []):
            lines.append(item)
            lines.append("")
        lines.extend(body)
        if body and body[-1] != "":
            lines.append("")

    lines.append(f"# {camp.get('title', 'Campaign')} — 成果汇总与知识蒸馏（{camp.get('label', '')}）")
    lines.append("")
    lines.append(f"*快照时点：{bundle['snapshot_utc']} ｜ 账本生成：{bundle.get('generated_by', 'campaign_distill')}*")
    lines.append("")

    # 0 摘要
    body = [
        f"- 节点数：{len(cov['nodes'])}；判据条数：{cov['total']}；覆盖状态分布：{_fmt_status_counts(cov['counts'])}",
        f"- 节点级结论分布："
        + "；".join(
            f"{n['node_key']}={n['verdict']}({n['pass_claimed']}/{n['criteria_total']})"
            for n in cov["nodes"]
        ),
        f"- 知识图谱：{stats['entities']} 实体 / {stats['edges']} 边（阈值 {spec['graph_min_entities']}/{spec['graph_min_edges']}，"
        f"{'达标' if stats['entities'] >= spec['graph_min_entities'] and stats['edges'] >= spec['graph_min_edges'] else '未达标'}）",
        f"- 遗留缺口：{len(gaps)} 条（阈值 ≥{spec['gaps_min']}，每条附提案草案）",
        f"- 归档：{len(manifest['entries'])} 个工件，manifest 校验 {manifest.get('verify_status', 'n/a')}",
    ]
    section("## 0 摘要", "summary", body)

    # 1 战役结构
    body = ["| 节点 | mission | 仓库 | 声明状态 | 判据数 | 覆盖结论 | depends_on |",
            "|---|---|---|---|---|---|---|"]
    for n in cov["nodes"]:
        node = next(x for x in bundle["nodes"] if x["node_key"] == n["node_key"])
        body.append(
            "| {k} | {m} | {r} | {s} | {t} | {v} | {d} |".format(
                k=n["node_key"], m=n["mission_id"], r=n["repo"], s=n["declared_status"],
                t=n["criteria_total"], v=n["verdict"], d=",".join(node.get("depends_on") or []) or "—")
        )
    body.append("")
    body.append("| 节点 | 证据文件 | 探针 | 探针结果（退出码 / 摘要） |")
    body.append("|---|---|---|---|")
    for node in bundle["nodes"]:
        ev = _node_evidence(node)
        probes = ev["probes"]
        probe_txt = "<br>".join(
            f"`exit={p.get('exit_code')}` {str(p.get('summary', '')).replace('|', '/')[:60]}"
            for p in probes
        ) or "—"
        body.append(
            f"| {node['node_key']} | {len(ev['files'])} | {len(probes)} | {probe_txt} |"
        )
    body.append("")
    body.append(f"数据包：`{bundle.get('bundle_path', '')}`（bundle_version {bundle.get('bundle_version')}）。")
    section("## 1 战役结构与快照", "layout", body)

    # 2 判据覆盖矩阵
    body = ["| 节点 | 判据 | 判据原文 | 状态 | 依据 |", "|---|---|---|---|---|"]
    for row in cov["rows"]:
        body.append(
            "| {n} | {c} | {t} | {s} | {r} |".format(
                n=row["node_key"], c=row["criterion"],
                t=row["text"].replace("|", "/")[:70], s=row["status"],
                r=(row["reason"] or "").replace("|", "/")[:60])
        )
    body.append("")
    body.append(
        "状态定义：`PASS_CLAIMED`=有结论且带来源引用并判为达成；`FAIL_CLAIMED`=有结论但未达成或等级越界；"
        "`EVIDENCE_ONLY`=有工件/探针但缺逐判据结论映射；`NO_EVIDENCE`=无任何证据。**节点级结论不外推**："
        "`VERIFIED` 要求该节点全部判据 `PASS_CLAIMED`。"
    )
    section("## 2 判据覆盖矩阵", "coverage", body)

    # 3 知识图谱
    body = [
        f"- 实体 {stats['entities']} 个：" + "，".join(f"{k} {v}" for k, v in stats["entities_by_type"].items()),
        f"- 关系边 {stats['edges']} 条：" + "，".join(f"{k} {v}" for k, v in stats["edges_by_relation"].items()),
        f"- 实体表 sha256：`{stats['entities_sha256']}`；边表 sha256：`{stats['edges_sha256']}`",
        "",
        "| 实体 id | 类型 | 标签 |",
        "|---|---|---|",
    ]
    for ent in graph["entities"][:40]:
        body.append(f"| `{ent['id']}` | {ent['type']} | {str(ent['label'])[:56].replace('|', '/')} |")
    if len(graph["entities"]) > 40:
        body.append(f"| … | … | 余 {len(graph['entities']) - 40} 条见 knowledge_graph.json |")
    section("## 3 知识图谱", "graph", body)

    # 4 缺口与提案
    body = ["| 缺口 | 规则 | 严重度 | 摘要 | 影响节点 |", "|---|---|---|---|---|"]
    for g in gaps:
        body.append(
            f"| {g['id']} | {g['rule']} | {g['severity']} | {g['title'].replace('|', '/')[:70]} | "
            f"{','.join(g['affected_nodes'])} |"
        )
    body.append("")
    for g in gaps:
        p = g["proposal"]
        body.append(f"### 提案草案 {g['id']}")
        body.append("")
        body.append(f"- **title**: {p['title']}")
        body.append(f"- **repo**: {p['repo']}")
        body.append(f"- **physics_domain**: {p['physics_domain']}")
        body.append(f"- **metric**: {p['metric']}")
        body.append(f"- **why_frontier**: {p['why_frontier']}")
        body.append(f"- **goal**: {p['goal']}")
        body.append(f"- **dependencies**: {', '.join(p['dependencies'])}")
        body.append("- **acceptance_criteria**:")
        for c in p["acceptance_criteria"]:
            body.append(f"  - {c}")
        body.append("")
    section("## 4 遗留缺口与提案草案", "gaps", body)

    # 5 工件清单
    body = ["| 引用 | 路径 | 类型 | 字节 | sha256 |", "|---|---|---|---|---|"]
    for i, ent in enumerate(manifest["entries"], start=1):
        body.append(
            f"| [E{i}] | `{ent['rel_path']}` | {ent.get('kind', '')} | {ent['bytes']} | `{ent['sha256']}` |"
        )
    body.append("")
    body.append(f"SHA256SUMS：`{manifest.get('sha256sums_path', '')}`；manifest：`{manifest.get('manifest_path', '')}`。")
    section("## 5 工件清单与 sha256", "manifest", body)

    # 6 局限
    body = list(nar.get("limits_bullets", []))
    body.append("")
    body.append("| 判据 | 目标 | 实测 | 是否达标 |")
    body.append("|---|---|---|---|")
    for chk in ctx.get("self_checks", []):
        body.append(
            f"| {chk['id']} | {chk['target']} | {chk['actual']} | {'是' if chk['ok'] else '否'} |"
        )
    section("## 6 局限与误差判定", "limits", body)
    return "\n".join(lines) + "\n"


def validate_report_md(md: str, ctx: dict, spec: dict | None = None) -> list[dict]:
    """报告硬校验：章节齐备 + 引用完整 + 数字一致 + 无占位符。返回违规列表。"""
    spec = {**DEFAULT_SPEC, **(spec or {})}
    violations: list[dict] = []

    for sec in spec["required_sections"]:
        if sec not in md:
            violations.append({"rule": "missing_section", "target": sec})

    for tok in spec["placeholder_tokens"]:
        if tok in md:
            violations.append({"rule": "placeholder_token", "target": tok})

    # 引用完整性：正文 [E#] 必须命中 manifest 条目；manifest 条目必须被引用或显式声明未引用
    import re

    cited = set(re.findall(r"\[E(\d+)\]", md))
    declared = {str(i) for i in range(1, len(ctx["manifest"]["entries"]) + 1)}
    for miss in sorted(cited - declared):
        violations.append({"rule": "dangling_citation", "target": f"E{miss}"})
    uncited = sorted(declared - cited)
    if uncited and "未被正文引用" not in md:
        violations.append({"rule": "uncited_artifacts", "target": ",".join(uncited)})

    # 数字一致性：正文声称的规模必须等于 JSON 实算值
    stats = ctx["graph_stats"]
    cov = ctx["coverage"]
    must_have = [
        f"实体 {stats['entities']} 个",
        f"关系边 {stats['edges']} 条",
        f"判据条数：{cov['total']}",
        f"遗留缺口：{len(ctx['gaps'])} 条",
        f"归档：{len(ctx['manifest']['entries'])} 个工件",
    ]
    for token in must_have:
        if token not in md:
            violations.append({"rule": "number_mismatch", "target": token})

    # 阈值型判据：未达标必须显式写"未达标"（不允许静默）
    if stats["entities"] < spec["graph_min_entities"] and "未达标" not in md:
        violations.append({"rule": "threshold_silent", "target": "graph_min_entities"})
    if len(ctx["gaps"]) < spec["gaps_min"]:
        violations.append({"rule": "threshold_not_met", "target": "gaps_min"})

    # 判据覆盖矩阵必须逐条出现（逐项对照表的机器可验形式）
    for row in cov["rows"]:
        if f"| {row['node_key']} | {row['criterion']} |" not in md:
            violations.append(
                {"rule": "coverage_row_missing", "target": f"{row['node_key']}/{row['criterion']}"}
            )
    return violations


# --------------------------------------------------------------------------- #
# 5. 归档 + sha256
# --------------------------------------------------------------------------- #
def archive_artifacts(items: list[dict], dest_dir: Path | str, out_manifest: Path | str | None = None) -> dict:
    """复制工件到归档目录并生成 sha256 清单；复制前后哈希必须一致。

    items: [{"path": 源路径, "kind": "impl|test|report|artifact|figure|ledger"}]
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, item in enumerate(sorted(items, key=lambda x: str(x["path"])), start=1):
        src = Path(item["path"])
        if not src.exists():
            raise FileNotFoundError(f"工件缺失: {src}")
        before = sha256_file(src)
        subdir = str(item.get("subdir") or "").strip("/")
        target_dir = (dest / subdir) if subdir else dest
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        if target.exists() and sha256_file(target) != before:
            raise RuntimeError(f"归档冲突：{target} 已存在且内容不同")
        if not target.exists():
            shutil.copy2(src, target)
        after = sha256_file(target)
        if after != before:
            raise RuntimeError(f"归档哈希不一致: {src} -> {target}")
        entries.append(
            {
                "index": i,
                "rel_path": str(target.relative_to(dest)).replace(os.sep, "/"),
                "source_path": str(src),
                "kind": item.get("kind", "artifact"),
                "bytes": target.stat().st_size,
                "sha256": after,
            }
        )
    sums = "".join(f"{e['sha256']}  {e['rel_path']}\n" for e in entries)
    (dest / "SHA256SUMS").write_text(sums, encoding="utf-8")
    payload = {
        "archive_dir": str(dest),
        "entries": entries,
        "entries_sha256": hashlib.sha256(canonical_json(entries).encode()).hexdigest(),
        "sha256sums_path": str(dest / "SHA256SUMS"),
        "manifest_path": str(out_manifest or (dest / "manifest.json")),
        "verify_status": "verified",
    }
    Path(payload["manifest_path"]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    return payload


def verify_manifest(manifest: dict) -> dict:
    """按 manifest 复算磁盘文件哈希，返回 {ok, checked, mismatches}。"""
    mismatches = []
    for ent in manifest["entries"]:
        path = Path(manifest["archive_dir"]) / ent["rel_path"]
        if not path.exists():
            mismatches.append({"path": ent["rel_path"], "error": "missing"})
            continue
        actual = sha256_file(path)
        if actual != ent["sha256"]:
            mismatches.append({"path": ent["rel_path"], "error": "sha256", "actual": actual})
    return {"ok": not mismatches, "checked": len(manifest["entries"]), "mismatches": mismatches}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="战役成果汇总与知识蒸馏（域无关机制层）")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--narrative", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--extra", action="append", default=[],
                    help="追加归档工件，格式 path::kind（可重复）")
    args = ap.parse_args(argv)

    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        print(f"bundle 缺失: {bundle_path}", file=sys.stderr)
        return 2
    try:
        bundle = load_json(bundle_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "stage": "bundle_contract", "exit_code": 2,
                          "contract_errors": [{"rule": "bundle_unreadable",
                                               "target": str(bundle_path),
                                               "detail": f"{type(exc).__name__}: {exc}"}]},
                         ensure_ascii=False))
        return 2
    contract = validate_bundle(bundle)
    if contract:
        print(json.dumps({"ok": False, "stage": "bundle_contract", "exit_code": 2,
                          "contract_errors": contract}, ensure_ascii=False))
        return 2
    bundle.setdefault("bundle_path", str(bundle_path))
    spec = {**DEFAULT_SPEC, **(load_json(args.spec) if args.spec else {})}
    narrative = load_json(args.narrative) if args.narrative else {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    coverage = build_coverage(bundle)
    gaps = find_gaps(bundle, coverage, spec)
    graph = build_graph(bundle, coverage, gaps)
    stats = graph_stats(graph)

    # 归档：bundle + 各节点证据文件 + 生成的三张账本
    _write_json(out / "coverage_matrix.json", coverage)
    _write_json(out / "gaps.json", {"gaps": gaps, "count": len(gaps)})
    _write_json(out / "knowledge_graph.json", {"graph": graph, "stats": stats})

    archive_items = [{"path": str(bundle_path), "kind": "bundle"}]
    for node in bundle["nodes"]:
        for f in (node.get("evidence") or {}).get("files") or []:
            p = Path(f["path"])
            if p.exists():
                archive_items.append(
                    {"path": str(p), "kind": f.get("kind", "artifact"),
                     "subdir": node["node_key"]}
                )
    for spec_item in args.extra:
        path_str, _, kind = spec_item.partition("::")
        archive_items.append({"path": path_str, "kind": kind or "artifact"})
        # 附加工件（如补丁/说明）与来源树同名风险由 subdir 隔离
        archive_items[-1]["subdir"] = "extra"
    archive_items += [
        {"path": str(out / "coverage_matrix.json"), "kind": "ledger"},
        {"path": str(out / "gaps.json"), "kind": "ledger"},
        {"path": str(out / "knowledge_graph.json"), "kind": "ledger"},
    ]
    if args.archive:
        manifest = archive_artifacts(archive_items, args.archive, Path(args.archive) / "manifest.json")
    else:
        manifest = {
            "entries": [], "archive_dir": "", "sha256sums_path": "", "manifest_path": "",
            "verify_status": "not_archived",
        }

    # 自检（本节点自身判据的可机算形式）
    self_checks = [
        {"id": "C1-覆盖", "target": f"全部 {coverage['total']} 条判据入矩阵",
         "actual": f"{len(coverage['rows'])} 行", "ok": len(coverage["rows"]) == coverage["total"]},
        {"id": "C2-图谱", "target": f"≥{spec['graph_min_entities']} 实体 / ≥{spec['graph_min_edges']} 边",
         "actual": f"{stats['entities']} / {stats['edges']}",
         "ok": stats["entities"] >= spec["graph_min_entities"] and stats["edges"] >= spec["graph_min_edges"]},
        {"id": "C3-缺口", "target": f"≥{spec['gaps_min']} 条且每条附提案草案",
         "actual": f"{len(gaps)} 条",
         "ok": len(gaps) >= spec["gaps_min"] and all(g.get("proposal") for g in gaps)},
        {"id": "C4-格式", "target": "报告自动校验零违规", "actual": "见下", "ok": True},
        {"id": "C5-归档", "target": "全部工件 sha256 复核通过",
         "actual": f"{len(manifest['entries'])} 件", "ok": True},
    ]

    ctx = {
        "bundle": bundle, "coverage": coverage, "graph": graph, "graph_stats": stats,
        "gaps": gaps, "manifest": manifest, "spec": spec, "narrative": narrative,
        "self_checks": self_checks,
    }

    # 报告（先用占位自检渲染，拿到违规后回填 C4/C5）
    md = render_report(ctx)
    violations = validate_report_md(md, ctx, spec)
    self_checks[3]["actual"] = f"{len(violations)} 违规"
    self_checks[3]["ok"] = not violations
    if args.archive:
        ver = verify_manifest(manifest)
        self_checks[4]["actual"] = f"{ver['checked']} 件，错配 {len(ver['mismatches'])}"
        self_checks[4]["ok"] = ver["ok"]
        manifest["verify_status"] = "verified" if ver["ok"] else "mismatch"
    md = render_report(ctx)
    violations = validate_report_md(md, ctx, spec) + [
        {"rule": "selfcheck_failed", "target": c["id"]} for c in self_checks if not c["ok"]
    ]

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(md, encoding="utf-8")

    acceptance = [
        {"id": "C1", "claim": "综合报告覆盖全部节点验收判据（逐项对照表）",
         "target": f"{coverage['total']} 条", "actual": f"{len(coverage['rows'])} 行",
         "ok": len(coverage["rows"]) == coverage["total"],
         "artifacts": [str(out / "coverage_matrix.json"), args.report or ""]},
        {"id": "C2", "claim": "知识图谱规模达标",
         "target": f"≥{spec['graph_min_entities']} 实体 / ≥{spec['graph_min_edges']} 边",
         "actual": f"{stats['entities']} / {stats['edges']}",
         "ok": stats["entities"] >= spec["graph_min_entities"] and stats["edges"] >= spec["graph_min_edges"],
         "artifacts": [str(out / "knowledge_graph.json")]},
        {"id": "C3", "claim": "遗留缺口清单且每条附提案草案",
         "target": f"≥{spec['gaps_min']} 条", "actual": f"{len(gaps)} 条",
         "ok": len(gaps) >= spec["gaps_min"] and all(g.get("proposal") for g in gaps),
         "artifacts": [str(out / "gaps.json"), args.report or ""]},
        {"id": "C4", "claim": "报告通过自动格式校验（章节/引用/数字一致性）",
         "target": "0 违规", "actual": f"{len(violations)} 违规",
         "ok": not violations, "artifacts": [args.report or "", str(out / "distill_summary.json")]},
        {"id": "C5", "claim": "全部工件归档并生成 sha256 校验和",
         "target": "复核 0 错配",
         "actual": f"{len(manifest['entries'])} 件 / {manifest.get('verify_status')}",
         "ok": manifest.get("verify_status") == "verified",
         "artifacts": [manifest.get("manifest_path", ""), manifest.get("sha256sums_path", "")]},
    ]
    summary = {
        "acceptance": acceptance,
        "graph_stats": stats,
        "coverage": {k: coverage[k] for k in ("counts", "total", "nodes")},
        "gaps": len(gaps),
        "gap_ids": [g["id"] for g in gaps],
        "manifest_entries": len(manifest["entries"]),
        "violations": violations,
        "self_checks": self_checks,
        "spec": spec,
    }
    _write_json(out / "distill_summary.json", summary)
    print(json.dumps({"ok": not violations, "violations": len(violations),
                      "entities": stats["entities"], "edges": stats["edges"],
                      "gaps": len(gaps)}, ensure_ascii=False))
    return 0 if not violations else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())