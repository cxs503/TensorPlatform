"""集成门 CLI：一次跑完 轨迹量测 → 互裁 → 台账 → 播种报告。

用法::

    python -m tools.integration_gate.cli \
        --campaign p158w2 \
        --data data/campaigns/p158w2 \
        --out artifacts/campaigns/p158w2 \
        --repo-root . --main main --self-repo m8510 \
        --base-ledger artifacts/capability_ledger/capability_ledger.json \
        [--pytest-summary artifacts/campaigns/p158w2/pytest_summary.json] \
        [--figures] [--strict]

退出码：0 = 门跑通（无论节点 PASS/FAIL）；2 = 工件 schema 违约；3 = --strict 且 I1–I5 未全满足。
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .baseline import align_all
from .compare import collect_metrics, run_check
from .ledger import PAIR_CAP, build_entries, build_ledger
from .model import Attestation, Criterion, PairResult, Trace
from .provenance import git_facts, read_json, sha256_obj, write_json
from .report import REPORT_SCHEMA, build_report, render_markdown
from .sampler import produce_all
from .schema import (SchemaError, reconcile_criterion, validate_attestation,
                     validate_nodes, validate_pairs, validate_traces)


def _load_attestation(path: Path) -> Attestation:
    raw = read_json(path)
    validate_attestation(raw, str(path))
    node = raw["node"]
    crit: list[Criterion] = []
    for c in raw["criteria"]:
        verdict, conflict = reconcile_criterion(c)
        measured = c.get("measured")
        thr = c.get("threshold")
        rel = None
        if isinstance(measured, (int, float)) and isinstance(thr, (int, float)) and thr:
            rel = abs(float(measured) - float(thr)) / abs(float(thr))
        crit.append(Criterion(
            id=c["id"], verdict=verdict,
            measured=None if measured is None else float(measured),
            reference=c.get("reference"),
            op=c.get("op"), threshold=None if thr is None else float(thr),
            rel_error=c.get("rel_error", rel),
            note=(c.get("note", "") + (f" [{conflict}]" if conflict else "")).strip(),
            source=raw["source"]["ref"],
            role=c.get("role", "capability_gate"),
        ))
    return Attestation(
        node_ext_id=node["ext_id"], repo=node["repo"], mission_id=node.get("mission_id"),
        branch=node.get("branch"), base_commit=node.get("base_commit"),
        head_commit=node.get("head_commit"),
        source_kind=raw["source"]["kind"], source_ref=raw["source"]["ref"],
        source_sha256=raw["source"]["sha256"], criteria=crit,
        capabilities=raw.get("capabilities") or [],
        interface_contract=raw.get("interface_contract"),
        notes=raw.get("notes", ""),
    )


def _run_pairs(pairs_spec: dict[str, Any], traces: dict[str, Trace],
               attestations: list[Attestation] | None = None) -> list[PairResult]:
    metrics = collect_metrics(traces)
    # 自证判据的实测值也进指标命名空间：att:<node_ext_id>:<criterion_id>
    for att in attestations or []:
        for c in att.criteria:
            if c.measured is not None:
                metrics[f"att:{att.node_ext_id}:{c.id}"] = float(c.measured)
    out: list[PairResult] = []
    for spec in pairs_spec["pairs"]:
        res = PairResult(id=spec["id"], title=spec["title"],
                         kind=spec.get("kind", "consistency"),
                         counterpart_status=spec.get("counterpart_status", ""),
                         note=spec.get("note", ""))
        for cspec in spec["checks"]:
            try:
                res.checks.append(run_check(cspec, traces, metrics))
            except Exception as exc:  # fail-closed
                from .model import CheckResult
                res.checks.append(CheckResult(cspec.get("check", "unknown"), "WITHHELD",
                                              cspec.get("severity", "blocking"),
                                              {"error": f"{type(exc).__name__}: {exc}"},
                                              "检查执行失败 (fail-closed)"))
        cap = spec.get("cap_level")
        if cap is None:
            cap = PAIR_CAP.get(res.verdict(), "MOCK")
        res.cap_level = cap
        out.append(res)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="integration_gate")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--main", default="main")
    ap.add_argument("--self-repo", default=None)
    ap.add_argument("--workspace-root", default=None,
                    help="兄弟 worktree 父目录（缺省自动向上探测含 work/ 的层级）")
    ap.add_argument("--base-ledger", default=None)
    ap.add_argument("--pytest-summary", default=None)
    ap.add_argument("--interfaces", default=None)
    ap.add_argument("--assumptions", default=None)
    ap.add_argument("--archive-spec", default=None)
    ap.add_argument("--platform-observed", default=None,
                    help="平台能力等级独立观测文件 (capabilities: [{id, level}])")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo_root).resolve()
    data = Path(args.data)
    if not data.is_absolute():
        data = repo / data
    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    out.mkdir(parents=True, exist_ok=True)

    try:
        nodes_doc = validate_nodes(read_json(data / "nodes.json"), "nodes")
        traces_doc = validate_traces(read_json(data / "traces.json"), "traces")
        pairs_doc = validate_pairs(read_json(data / "pairs.json"), "pairs")
        attestations = [_load_attestation(Path(p))
                        for p in sorted(glob.glob(str(data / "attestations" / "*.json")))]
    except (SchemaError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[integration-gate] FAIL-CLOSED: {exc}", file=sys.stderr)
        write_json(out / "gate_error.json",
                   {"schema": SCHEMA_VERSION, "error": f"{type(exc).__name__}: {exc}",
                    "stage": "artifact_load"})
        return 2

    # 1) 轨迹量测
    traces = produce_all(traces_doc, repo)
    write_json(out / "traces.json",
               {"schema": SCHEMA_VERSION, "traces": [t.as_dict() for t in traces.values()]})

    # 2) 互裁
    pairs = _run_pairs(pairs_doc, traces, attestations)

    # 3) 基线对齐 (workspace_root = 兄弟 worktree 的父目录)
    facts = git_facts(repo, None, args.main)
    base_commit = facts.get("main_head")
    if args.workspace_root:
        workspace_root = Path(args.workspace_root).resolve()
    else:
        # 兄弟 worktree 的父目录：向上找包含 work/ 的那一层（/root/tp-agent/work/<m> → /root/tp-agent）
        workspace_root = repo.parent
        for cand in (repo.parent, repo.parent.parent, repo.parent.parent.parent):
            if (cand / "work").is_dir():
                workspace_root = cand
                break
    alignments = align_all(nodes_doc["nodes"],
                           [a.as_dict() for a in attestations], str(repo),
                           args.main, args.self_repo, workspace_root)

    # 4) 台账
    base_ledger = None
    if args.base_ledger:
        p = Path(args.base_ledger)
        if not p.is_absolute():
            p = repo / p
        if p.is_file():
            base_ledger = read_json(p)
    facts_by_node = {a["node_ext_id"]: {**(a.get("facts") or {}), "state": a["state"]}
                     for a in alignments}
    entries = build_entries(attestations, pairs, pairs_doc, facts_by_node,
                            args.self_repo or "", base_ledger, nodes_doc["nodes"])
    ledger = build_ledger(args.campaign, entries, pairs, alignments, base_commit)
    write_json(out / "ledger.json", ledger)

    # 4b) 跨节点耦合闸（接口契约 + 假设一致性）与工件快照
    coupling = None
    if args.interfaces or args.assumptions:
        from . import coupling as cpl
        coupling = {}
        if args.interfaces:
            ipath = Path(args.interfaces)
            ipath = ipath if ipath.is_absolute() else repo / ipath
            idoc = cpl.validate_interfaces(read_json(ipath), "interfaces")
            node_ids = [n["ext_id"] for n in nodes_doc["nodes"]]
            # 「有可核证据」= 有验收自证工件，或本门在自己的轨迹里成功跑过该节点实现
            trace_evidenced = {t.meta.get("node") for t in traces.values()
                               if t.status == "ok" and t.meta.get("node")}
            attested = {n["ext_id"]: (any(a.node_ext_id == n["ext_id"]
                                          and a.verdict() != "WITHHELD" for a in attestations)
                                      or n["ext_id"] in trace_evidenced)
                        for n in nodes_doc["nodes"]}
            documented = {}
            for e in idoc["interfaces"]:
                ref = Path(e["doc_ref"])
                documented[e["doc_ref"]] = (ref if ref.is_absolute() else repo / ref).is_file()
            iface_res = cpl.check_interfaces(idoc, attested=attested, documented=documented)
            unknown = sorted({r["producer"] for r in iface_res["interfaces"]} - set(node_ids))
            iface_res["unknown_producers"] = unknown
            coupling["interfaces"] = iface_res
        if args.assumptions:
            apath = Path(args.assumptions)
            apath = apath if apath.is_absolute() else repo / apath
            adoc = cpl.validate_assumptions(read_json(apath), "assumptions")
            attested_nodes = {a.node_ext_id for a in attestations if a.verdict() != "WITHHELD"}
            coupling["assumptions"] = cpl.check_assumptions(
                adoc, pair_verdicts={p.id: p.verdict() for p in pairs},
                attested_nodes=attested_nodes)
        write_json(out / "coupling.json", coupling)

    platform_delta = locals().get("platform_delta")
    archive = None
    if args.archive_spec:
        from . import archive as arc
        spath = Path(args.archive_spec)
        spath = spath if spath.is_absolute() else repo / spath
        spec = read_json(spath)
        manifest = arc.build_manifest(args.campaign, spec["paths"], root=repo,
                                      exclude_globs=tuple(spec.get("exclude_globs") or ()),
                                      note=spec.get("note", ""))
        mpath = arc.write_manifest(manifest, out / "archive_manifest.json")
        verification = arc.verify_manifest(arc.read_manifest(mpath), root=repo)
        archive = {**manifest, "manifest_path": str(mpath.relative_to(repo)),
                   "verification": verification}

    # 4c) 平台台账增量（独立观测 vs 基础台账，R2 honest-FAIL 抬高保护）
    if args.platform_observed:
        from .ledger import platform_ledger_delta
        opath = Path(args.platform_observed)
        opath = opath if opath.is_absolute() else repo / opath
        obs = read_json(opath)
        levels = {c["id"]: c["level"] for c in obs["capabilities"]}
        pdelta = platform_ledger_delta(levels, base_ledger)
        pdelta["observed_source"] = {"ref": str(opath.relative_to(repo))
                                     if str(opath).startswith(str(repo)) else str(opath),
                                     "sha256": __import__("hashlib").sha256(
                                         opath.read_bytes()).hexdigest()}
        write_json(out / "platform_ledger_delta.json", pdelta)
        platform_delta = pdelta

    # 4d) 台账增量（战役条目 vs 基础台账）
    from .ledger import compute_delta
    ledger_delta = compute_delta(entries, base_ledger)
    write_json(out / "ledger_delta.json", ledger_delta)

    # 5) 播种报告
    pytest_summary = None
    if args.pytest_summary:
        p = Path(args.pytest_summary)
        if not p.is_absolute():
            p = repo / p
        if p.is_file():
            pytest_summary = read_json(p)
    report = build_report(args.campaign, base_commit, nodes_doc["nodes"], attestations,
                          pairs, ledger, alignments, pytest_summary,
                          coupling=coupling, archive=archive,
                          extra={"gate_schema": SCHEMA_VERSION,
                                 "traces": {tid: {"status": t.status, "kind": t.kind,
                                                  "sha256": t.provenance.get("trace_sha256")}
                                            for tid, t in traces.items()},
                                 "ledger_delta": ledger_delta,
                                 "platform_ledger_delta": platform_delta,
                                 "data_dir": str(data.relative_to(repo))
                                 if str(data).startswith(str(repo)) else str(data)})
    write_json(out / "seed_report.json", report)
    (out / "seed_report.md").write_text(render_markdown(report), encoding="utf-8")

    # 6) 图
    if args.figures:
        from .figures import render_all
        written = render_all(report, traces, out / "figures")
        print(f"[integration-gate] figures: {', '.join(written)}")

    ok = all(v["passed"] for v in report["integration_criteria"].values())
    print(json.dumps({"campaign": args.campaign,
                      "report_sha256": report["content_sha256"],
                      "ledger_sha256": ledger["content_sha256"],
                      "criteria_passed": ok,
                      "pairs": {p.id: p.verdict() for p in pairs},
                      "levels": ledger["counts"],
                      "new_real": ledger_delta["n_new_real"],
                      "gaps": len(report["gaps"]),
                      "coupling": ({"interfaces": coupling["interfaces"]["summary"]}
                                   if coupling and "interfaces" in coupling else None),
                      "consistency": (coupling["assumptions"]["summary"]
                                      if coupling and "assumptions" in coupling else None),
                      "archive": ({"n_files": archive["n_files"],
                                   "ok": archive["verification"]["ok"]} if archive else None)},
                     ensure_ascii=False))
    if args.strict and not ok:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())