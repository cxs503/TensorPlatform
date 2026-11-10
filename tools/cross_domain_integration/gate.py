"""门主体: 工件汇总 → 契约校验 → 基线比对 → 判据 → 报告 body (零领域语义)。

输入全部来自 manifest(领域内容) + work_root(各节点工作树)；输出是确定性
可复算的 report body。``generated_utc`` 由调用方传入，测试可用固定值复现
同一 body (同输入 → 同 sha256)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .baseline import bound_delta, compare, read_baseline
from .contract import (check_declarations, check_envelope, check_quantities,
                       cross_producer, legacy_contract_checks, legacy_metadata,
                       summarize)
from .criteria import evaluate
from .ingest import (IngestError, find_envelope, load_json, resolve_pointer,
                     scan_node)
from .provenance import sha256_file, sha256_obj
from .units import UnitError, dimension_signature

MAX_JSON_PROBE_BYTES = 8 << 20


def _envelope_quantities(envelope: dict[str, Any], node: dict[str, Any],
                         allowed_frames: list[str]
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从信封读量值 + 组装契约检查入口 (单位以信封声明为准)。"""
    out: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    declared = envelope.get("quantities")
    if not isinstance(declared, dict):
        return out, checks
    ref_by_name = {q["name"]: q for q in node["quantities"]}
    for name, payload in sorted(declared.items()):
        if not isinstance(payload, dict):
            continue
        value = payload.get("value")
        unit = payload.get("unit")
        frame = payload.get("frame", "none")
        ref = ref_by_name.get(name)
        entry = {
            "name": name,
            "value": float(value) if isinstance(value, (int, float)) else None,
            "unit": unit,
            "unit_ref": ref["unit"] if ref else None,
            "frame": frame,
            "frame_ref": ref["frame"] if ref else None,
            "declared_in_manifest": ref is not None,
            "node_ext_id": node["ext_id"],
            "repo": node["repo"],
            "source": "envelope",
        }
        out.append(entry)
        frames = sorted(set(allowed_frames) | {str(frame)})
        checks.extend(check_quantities([entry], frames))
    return out, checks


def _legacy_quantities(node: dict[str, Any], worktree: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """legacy 工件经 manifest adapter 指针取值 (单位/参考系取自 manifest)。"""
    out: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for q in node["quantities"]:
        adapter = q.get("adapter")
        if not adapter:
            out.append({"name": q["name"], "value": None, "unit": q["unit"],
                        "unit_ref": q["unit"], "frame": q["frame"],
                        "node_ext_id": node["ext_id"], "repo": node["repo"],
                        "source": "absent",
                        "note": "no adapter declared in manifest"})
            continue
        path = worktree / adapter["artifact"]
        entry = {"name": q["name"], "value": None, "unit": q["unit"],
                 "unit_ref": q["unit"], "frame": q["frame"],
                 "node_ext_id": node["ext_id"], "repo": node["repo"],
                 "source": "adapter", "unit_source": "manifest",
                 "frame_source": "manifest", "artifact": adapter["artifact"],
                 "pointer": adapter["pointer"], "artifact_sha256": None,
                 "note": q.get("note", "")}
        scale = float(adapter.get("scale", 1.0))
        entry["scale_applied"] = scale
        entry["unit_in_artifact"] = adapter.get("unit_in_artifact")
        if path.is_file():
            entry["artifact_sha256"] = sha256_file(path)
            try:
                raw = float(resolve_pointer(load_json(path), adapter["pointer"]))
                entry["raw_value"] = raw
                entry["value"] = raw * scale
            except (IngestError, TypeError, ValueError) as exc:
                entry["error"] = str(exc)
                notes.append({"code": "adapter_read", "subject": f"{node['ext_id']}:{q['name']}",
                              "detail": str(exc)})
        else:
            notes.append({"code": "adapter_artifact_missing",
                          "subject": f"{node['ext_id']}:{q['name']}",
                          "detail": f"{adapter['artifact']} not found"})
        out.append(entry)
        checks.extend(check_quantities([entry], [q["frame"], "none"]))
    return out, checks, notes


def _probe_legacy_metadata(node: dict[str, Any], worktree: Path,
                           scanned: dict[str, Any]) -> dict[str, Any]:
    docs = {}
    for art in scanned["artifacts"]:
        rel = art["rel"]
        if not rel.endswith(".json") or art["bytes"] > MAX_JSON_PROBE_BYTES:
            continue
        try:
            docs[rel] = load_json(worktree / rel)
        except IngestError:
            docs[rel] = None
    return legacy_metadata(docs)


def run_gate(manifest: dict[str, Any], work_root: str | Path, generated_utc: str,
             report_path: str | Path | None = None,
             doc_path: str | Path | None = None) -> dict[str, Any]:
    work_root = Path(work_root)
    nodes_block: list[dict[str, Any]] = []
    all_quantities: list[dict[str, Any]] = []
    all_checks: list[dict[str, Any]] = []
    baseline_block: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    legacy_by_node: dict[str, Any] = {}
    artifact_index: list[dict[str, Any]] = []

    declared: list[dict[str, Any]] = []
    for node in manifest["nodes"]:
        worktree = work_root / node["worktree"]
        for q in node["quantities"]:
            declared.append({"name": q["name"], "unit": q["unit"], "frame": q["frame"],
                             "node_ext_id": node["ext_id"], "repo": node["repo"]})
        scanned = scan_node(node, work_root,
                            (manifest["campaign"] or {}).get("started_utc"))
        env_rel = find_envelope(scanned)
        envelope = None
        env_checks: list[dict[str, Any]] = []
        if env_rel:
            try:
                envelope = load_json(worktree / env_rel)
                env_checks = check_envelope(envelope, node,
                                           manifest["contract"]["require_envelope_schema"])
            except IngestError as exc:
                notes.append({"code": "envelope_unreadable",
                              "subject": node["ext_id"], "detail": str(exc)})
        quantities: list[dict[str, Any]] = []
        q_checks: list[dict[str, Any]] = []
        if envelope is not None and isinstance(envelope.get("quantities"), dict):
            quantities, q_checks = _envelope_quantities(
                envelope, node, manifest["contract"]["allowed_frames"])
        else:
            quantities, q_checks, legacy_notes = _legacy_quantities(node, worktree)
            notes.extend(legacy_notes)
        all_checks.extend(env_checks)
        all_checks.extend(q_checks)
        all_quantities.extend(quantities)

        legacy = _probe_legacy_metadata(node, worktree, scanned)
        legacy_by_node[node["ext_id"]] = legacy
        if envelope is None:
            for rel, doc in sorted((legacy.get("docs") or {}).items()):
                all_checks.extend(legacy_contract_checks(f"{node['ext_id']}/{rel}", doc))

        node_baselines = []
        for q in node["quantities"]:
            base = read_baseline(q, work_root)
            current = next((c for c in quantities if c["name"] == q["name"]), None)
            comparison = compare(base, current, q.get("tolerance_rel"))
            node_baselines.append({
                "name": q["name"], "unit": q["unit"], "frame": q["frame"],
                "tolerance_rel": q.get("tolerance_rel"),
                "baseline": base, "current": current, "comparison": comparison})
            if base.get("drift") and not base["drift"]["consistent"]:
                notes.append({"code": "baseline_value_drift",
                              "subject": f"{node['ext_id']}:{q['name']}",
                              "detail": (f"manifest declared {base['drift']['declared_value']} "
                                         f"vs artifact {base['drift']['artifact_value']}")})
            if base.get("declared"):
                baseline_block.append({"node_ext_id": node["ext_id"], **node_baselines[-1]})

        bd = bound_delta(node.get("baseline_criteria_text", []), node.get("criteria_text", []))

        nodes_block.append({
            "node_ext_id": node["ext_id"],
            "ledger_node_id": node.get("ledger_node_id"),
            "mission_id": node["mission_id"],
            "repo": node["repo"],
            "label": node.get("label", ""),
            "worktree": node["worktree"],
            "git": scanned["git"],
            "worktree_exists": scanned["exists"],
            "required_artifacts": scanned["required"],
            "required_present": scanned["required_present"],
            "required_missing": scanned["required_missing"],
            "expected_artifacts": node["expected_artifacts"],
            "expected_present": scanned["expected_present"],
            "expected_missing": scanned["expected_missing"],
            "complete": scanned["complete"],
            "completeness": scanned["completeness"],
            "status": ("PRESENT" if scanned["complete"] else
                       ("PARTIAL" if scanned["artifacts"] else "MISSING")),
            "artifact_count": len(scanned["artifacts"]),
            "artifacts": scanned["artifacts"],
            "stale_excluded": scanned.get("stale_excluded", []),
            "envelope": {"present": envelope is not None, "rel": env_rel,
                         "checks": env_checks},
            "quantities": quantities,
            "baseline": node_baselines,
            "bound_delta": bd,
            "legacy_metadata": legacy,
            "notes": [n for n in notes if n["subject"].startswith(node["ext_id"])],
        })
        for art in scanned["artifacts"]:
            artifact_index.append({
                "node_ext_id": node["ext_id"], "repo": node["repo"],
                "mission_id": node["mission_id"],
                "artifact": art["rel"], "bytes": art["bytes"],
                "sha256": art["sha256"],
                "canonical_name": f"{node['ext_id']}/{art['rel']}"})

    all_checks.extend(check_declarations(declared,
                                        manifest["contract"]["allowed_frames"]))
    contract_summary = summarize(all_checks)
    adoption = {
        "nodes_with_envelope": sum(1 for n in nodes_block if n["envelope"]["present"]),
        "nodes_total": len(nodes_block),
    }
    adoption["rate"] = (round(adoption["nodes_with_envelope"] / adoption["nodes_total"], 6)
                        if adoption["nodes_total"] else None)
    cross = cross_producer(declared)
    contract_block = {
        "summary": contract_summary,
        "checks": all_checks,
        "cross_producer": cross,
        "envelope_adoption": adoption,
        "cross_producer_basis": "manifest declarations (unit/frame per quantity per repo)",
        "cross_producer_summary": {
            "applicable": sum(1 for c in cross if c["result"] != "NA"),
            "pass": sum(1 for c in cross if c["result"] == "PASS"),
            "fail": sum(1 for c in cross if c["result"] == "FAIL"),
        },
        "legacy_metadata": {
            "by_node": {k: {"count": v["count"],
                            "presence_rate": v["presence_rate"]} for k, v in
                        legacy_by_node.items()},
            "aggregate": _aggregate_legacy(legacy_by_node),
        },
        "notes": [n for n in notes if n["code"] in
                  ("envelope_unreadable", "adapter_read", "baseline_value_drift")],
    }

    flat_baselines = [{"name": b["name"], "baseline": b["baseline"],
                       "comparison": b["comparison"]}
                      for nb in nodes_block for b in nb["baseline"]]
    criteria_block = evaluate(
        nodes=nodes_block, contract_summary=contract_summary,
        quantities=flat_baselines,
        doc_path=doc_path, doc_sections=manifest["doc"]["sections"],
        report_path=report_path, report_sha256=None, figures=[],
        applicable=manifest.get("criteria_applicable"))

    gaps = _gaps(nodes_block, contract_summary, baseline_block, criteria_block)
    body = {
        "schema": "tp.cdi.report/v1",
        "gate": {"module": "tools/cross_domain_integration",
                 "version": SCHEMA_VERSION},
        "campaign": manifest["campaign"],
        "generated_utc": generated_utc,
        "scan": {"work_root": str(work_root),
                 "nodes_scanned": len(nodes_block),
                 "artifacts_indexed": len(artifact_index),
                 "stale_excluded": sum(len(n["stale_excluded"]) for n in nodes_block),
                 "freshness_rule": ("artifact counted only if untracked or last commit "
                                    "epoch >= campaign.started_utc")},
        "provenance": {
            "manifest_path": manifest.get("path"),
            "manifest_sha256": (sha256_file(manifest["path"])
                                if manifest.get("path") and
                                Path(manifest["path"]).is_file() else None),
        },
        "artifact_index": artifact_index,
        "nodes": nodes_block,
        "contract": contract_block,
        "baseline": {
            "wave": (manifest["campaign"]["baseline"] or {}),
            "quantities": baseline_block,
            "comparable": sum(1 for b in baseline_block
                              if b["comparison"]["verdict"] in ("PASS", "FAIL", "NA")),
            "withheld": sum(1 for b in baseline_block
                            if b["comparison"]["verdict"] == "WITHHELD"),
            "bound_delta": {nb["node_ext_id"]: nb["bound_delta"] for nb in nodes_block},
        },
        "criteria": criteria_block,
        "gaps": gaps,
    }
    return body


def _aggregate_legacy(legacy_by_node: dict[str, Any]) -> dict[str, Any]:
    totals = {"count": 0}
    kinds = ("schema", "mission", "timestamp", "unit")
    hits = {k: 0 for k in kinds}
    for block in legacy_by_node.values():
        totals["count"] += block["count"]
        for row in block["artifacts"]:
            for k in kinds:
                if row.get("keys", {}).get(k):
                    hits[k] += 1
    n = totals["count"]
    totals["presence_rate"] = {k: (round(v / n, 6) if n else None)
                               for k, v in hits.items()}
    return totals


def _gaps(nodes_block: list[dict[str, Any]], contract_summary: dict[str, Any],
          baseline_block: list[dict[str, Any]], criteria_block: dict[str, Any]
          ) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for nb in nodes_block:
        if not nb["complete"]:
            gaps.append({
                "id": f"gap:{nb['node_ext_id']}:artifacts",
                "kind": "missing_artifacts",
                "node_ext_id": nb["node_ext_id"],
                "detail": {"required_missing": nb["required_missing"],
                           "expected_missing": nb["expected_missing"],
                           "status": nb["status"]},
                "action": ("node produced no artifact at scan time; re-run gate with "
                           "--rescan after the producing node lands artifacts"),
            })
        if not nb["envelope"]["present"] and nb["artifact_count"]:
            gaps.append({
                "id": f"gap:{nb['node_ext_id']}:no_envelope",
                "kind": "missing_envelope",
                "node_ext_id": nb["node_ext_id"],
                "detail": "artifacts present but no tp.cdi.envelope/v1 bundle",
                "action": "emit artifacts/<node_ext_id>/envelope.json per contract",
            })
    if contract_summary.get("violations"):
        gaps.append({
            "id": "gap:contract:violations",
            "kind": "contract_violation",
            "node_ext_id": None,
            "detail": {"count": len(contract_summary["violations"]),
                       "codes": sorted({v["code"] for v in contract_summary["violations"]})},
            "action": "fix unit/frame/format declarations at the producing side",
        })
    withheld = [b for b in baseline_block if b["comparison"]["verdict"] == "WITHHELD"]
    if withheld:
        gaps.append({
            "id": "gap:baseline:withheld",
            "kind": "baseline_withheld",
            "node_ext_id": None,
            "detail": {"withheld": len(withheld),
                       "names": sorted({w["name"] for w in withheld})},
            "action": "measured delta unavailable until producing nodes emit values",
        })
    for key, crit in criteria_block["criteria"].items():
        if not crit["pass"]:
            gaps.append({
                "id": f"gap:criteria:{key}", "kind": "criterion_fail",
                "node_ext_id": None, "detail": crit,
                "action": "see criterion detail; do not overwrite an honest FAIL",
            })
    return gaps


def seal(body: dict[str, Any], figures: list[dict[str, Any]]) -> dict[str, Any]:
    """报告自封: body 内容 sha256 + 图 sha256 (不含 seal 字段本身)。"""
    body_sha = sha256_obj(body)
    out = dict(body)
    out["seal"] = {"body_sha256": body_sha, "algorithm": "sha256(canonical-json(body))",
                   "figures": [{"name": f["name"], "sha256": f.get("sha256")}
                               for f in figures]}
    return out