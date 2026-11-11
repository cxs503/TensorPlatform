"""台账工件：规范化 JSON、sha256 证据索引、两次运行可复现校验。

复现口径
========
台账主体（``ledger.json``）**不含时间戳/耗时/绝对路径**：所有易变元数据写进
``meta.json``，且不参与摘要。因此同一代码库 + 同一 spec 的两次运行，
``ledger.json`` 的 sha256 必然相同；``verify`` 子命令把它做成硬判据。

证据索引（``evidence_index.json``）把"这次审计读到的每个输入文件"钉上 sha256：
spec / exec spec / 实现 / 测试 / 基准 / 探针目标模块。任何文件改动都会让索引与
台账摘要对不上，审计链因此可回溯到具体版本，而不是"某天跑过一次"。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "capability_audit_v2.ledger.v1"


def canonical_json(obj: Any) -> str:
    """规范化 JSON：键排序 + 紧凑分隔符 + 保留非 ASCII。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_index(paths: Iterable[str | Path], root: str | Path = ".") -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for p in paths:
        fp = Path(p)
        fp = fp if fp.is_absolute() else root / fp
        if not fp.is_file():
            continue
        rel = fp.resolve().relative_to(root.resolve()).as_posix()
        out[rel] = file_sha256(fp)
    return dict(sorted(out.items()))


def collect_evidence_paths(spec_v2: dict, base_report: dict, root: str | Path = ".",
                           extra: Iterable[str] = ()) -> list[str]:
    """把本次审计实际读到的仓内文件列全（spec / 实现 / 测试 / 基准 / 探针目标）。"""
    root = Path(root)
    paths: set[str] = set(extra)
    base_rel = str(spec_v2.get("base_spec_path") or "")
    if base_rel:
        paths.add(base_rel)
    for cap in base_report:
        ev = cap.get("evidence") or {}
        for f in ev.get("impl_files") or []:
            paths.add(f["path"])
        for f in ev.get("test_files") or []:
            paths.add(f["path"])
        bench = ev.get("benchmark") or {}
        if bench.get("path"):
            paths.add(str(bench["path"]))
    for decl in (spec_v2.get("exec") or {}).values():
        for sel in (decl.get("pytest") or {}).get("selectors") or []:
            paths.add(sel.split("::")[0])
        for probe in (decl.get("probe") or {}, decl.get("control_probe") or {}):
            target = str(probe.get("target") or "")
            mod = target.split(":")[0]
            if not mod:
                continue
            rel = Path(*mod.split(".")).with_suffix(".py").as_posix()
            for base in ["", *(probe.get("path_prepend") or [])]:
                cand = Path(base) / rel if base else Path(rel)
                if (root / cand).is_file():
                    paths.add(cand.as_posix())
                    break
    return sorted(p for p in paths if (root / p).exists())


def build_ledger(spec_v2: dict, verdicts: list[dict], base_report: dict,
                 exec_index: dict, labels: dict, agreement: dict,
                 evidence_sha: dict[str, str], root: str | Path = ".",
                 static_agreement: dict | None = None) -> dict:
    """拼台账主体（纯数据，无易变字段）。"""
    static_by_id = {c["capability"]: c for c in base_report}
    caps = []
    for v in sorted(verdicts, key=lambda x: x["capability"]):
        cid = v["capability"]
        static = static_by_id.get(cid) or {}
        exec_ev = exec_index.get(cid) or {}
        caps.append({
            "id": cid,
            "level": v["level"],
            "reason_code": v["reason_code"],
            "rule": v["rule"],
            "static_level": v["static_level"],
            "static_reasons": v["static_reasons"],
            "contradictions": v["contradictions"],
            "warnings": v["warnings"],
            "exec_status": v["exec"],
            "non_discriminating_tests": bool(v.get("non_discriminating_tests")),
            "exec_evidence": {
                "pytest": {k: exec_ev.get("pytest", {}).get(k)
                           for k in ("bound", "status", "tests", "passed", "failed",
                                     "errors", "skipped", "numeric_assertions",
                                     "failing_tests", "digest")},
                "probe": {k: exec_ev.get("probe", {}).get(k)
                          for k in ("bound", "status", "contradictions", "digest")},
                "probe_checks": exec_ev.get("probe", {}).get("checks") or [],
                "control_probe": {k: exec_ev.get("control_probe", {}).get(k)
                                  for k in ("bound", "status", "contradictions", "digest")},
                "control_checks": exec_ev.get("control_probe", {}).get("checks") or [],
            },
            "evidence": (static.get("evidence") or {}),
            "label": labels.get(cid),
            "label_match": (None if not labels.get(cid) else labels[cid] == v["level"]),
        })
    tally: dict[str, int] = {}
    for c in caps:
        tally[c["level"]] = tally.get(c["level"], 0) + 1
    return {
        "schema_version": SCHEMA,
        "ruleset_version": verdicts[0]["ruleset_version"] if verdicts else "",
        "spec": {
            "schema_version": spec_v2.get("schema_version"),
            "title": spec_v2.get("title"),
            "base_spec": spec_v2.get("base_spec_path"),
            "base_spec_sha256": spec_v2.get("base_spec_sha256"),
            "exec_spec_sha256": spec_v2.get("exec_spec_sha256"),
            "thresholds": spec_v2.get("thresholds"),
            "execution": spec_v2.get("execution"),
        },
        "capabilities": caps,
        "summary": {
            "audited": len(caps),
            "coverage": (len(caps) / len(spec_v2["base_spec"]["capabilities"])
                         if spec_v2["base_spec"]["capabilities"] else 0.0),
            "level_tally": tally,
            "exec_bindings": {
                "pytest": sum(1 for c in caps if c["exec_evidence"]["pytest"]["bound"]),
                "probe": sum(1 for c in caps if c["exec_evidence"]["probe"]["bound"]),
            },
            "agreement": agreement,
            "static_agreement": static_agreement or {},
            "non_discriminating_tests": sum(1 for c in caps if c["non_discriminating_tests"]),
            "evidence_files": len(evidence_sha),
        },
    }


def write_json(path: str | Path, obj: Any) -> str:
    """写规范化 JSON 并返回其 sha256。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    p.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()