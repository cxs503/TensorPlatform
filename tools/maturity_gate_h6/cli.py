#!/usr/bin/env python3
"""H6 成熟度门 CLI：规格 → 多源装载 → 四轴一致性 → 增量账本 → 裁定 → 报告/图。

子命令
------
``run``    跑完整门，落盘 gate.json / consistency.json / increment_ledger.json /
           mutation_benchmark.json / 三张图 / 论文级报告；``--verify`` 同进程重算。
``verify`` 只读重算已落盘 gate.json 并比对（防手改、防阈值暗改）。
``mutate`` 对台账副本施加确定性变异，测量门的检出召回与假阳性（ref vs got 基准）。

用法::

    python3 -m tools.maturity_gate_h6 run
    python3 -m tools.maturity_gate_h6 verify
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_gate_h6 import core  # noqa: E402
from tools.maturity_gate_h6 import report as report_mod  # noqa: E402

DEFAULT_SPEC = REPO_ROOT / "data" / "maturity_gate_h6" / "h6.spec.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "maturity_gate_h6"
DEFAULT_REPORT = REPO_ROOT / "docs" / "H6_MATURITY_GATE_G1.md"


# ---------------------------------------------------------------------------
def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _load_source(root: Path, spec: Mapping[str, Any]) -> core.Source:
    path = root / spec["path"] if not str(spec["path"]).startswith("/") else Path(spec["path"])
    loader = {"flat_snapshot": core.load_flat_snapshot, "ledger": core.load_ledger}[spec["kind"]]
    try:
        return loader(spec["source_id"], path)
    except core.GateError as exc:  # 工件结构异常 → 显式降级，不静默成绿
        return core.Source(spec["source_id"], str(path), False, note=f"装载失败: {exc}")


def load_inputs(root: Path, spec_path: Path) -> Dict[str, Any]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    for key in ("sources", "markdown_table", "corpus", "gate_id"):
        if key not in spec:
            raise core.GateError(f"{spec_path}: 缺键 {key}")
    sources = [_load_source(root, s) for s in spec["sources"]]
    by_role: Dict[str, List[core.Source]] = {}
    for s, raw in zip(sources, spec["sources"]):
        by_role.setdefault(raw.get("role", "extra"), []).append(s)
    if not by_role.get("baseline") or not by_role.get("current"):
        raise core.GateError(f"{spec_path}: 必须声明 baseline 与 current 源")
    md_path = root / spec["markdown_table"]
    md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    corpus_path = root / spec["corpus"]
    corpus = json.loads(corpus_path.read_text(encoding="utf-8")) if corpus_path.is_file() else {}
    return {
        "spec": spec,
        "baseline": by_role["baseline"][0],
        "current": by_role["current"][0],
        "extras": [s for role, lst in by_role.items() if role not in ("baseline", "current") for s in lst],
        "markdown_claims": core.parse_markdown_claims(md_text),
        "markdown_sha256": core.text_sha256(md_text),
        "corpus": corpus,
        "corpus_sha256": core.file_sha256(corpus_path) if corpus_path.is_file() else None,
    }


def run_gate_from(inputs: Mapping[str, Any], root: Path,
                  thresholds: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    gate = core.run_gate(claimed=inputs["baseline"], reconciled=inputs["current"],
                         markdown_claims=inputs["markdown_claims"], corpus=inputs["corpus"],
                         work_root=root, thresholds=thresholds,
                         extra_sources=inputs.get("extras", ()))
    gate["gate_id"] = inputs["spec"].get("gate_id", gate["gate_id"])
    gate["inputs"] = {
        "spec": str(inputs["spec"].get("spec_id", "")),
        "markdown_sha256": inputs["markdown_sha256"],
        "corpus_sha256": inputs["corpus_sha256"],
        "markdown_metrics_parsed": inputs["markdown_claims"].get("parsed", []),
    }
    gate["increment_ledger_reset"] = None
    return gate


# ---------------------------------------------------------------------------
# 变异基准：确定性注入缺陷 → 测量检出召回 / 假阳性
# ---------------------------------------------------------------------------
def _mutate_ledger(ledger: Mapping[str, Any], cid: str, field: str, new_value: Any) -> Dict[str, Any]:
    import copy
    data = copy.deepcopy(ledger)
    for item in data["capabilities"]:
        if str(item.get("id")) == cid:
            if field == "reconciled_level":
                item["reconciled_level"] = new_value
            elif field == "divergence":
                item["divergence"] = new_value
            elif field == "drop_evidence":
                item["evidence_refs"] = []
            elif field == "line_sha256":
                if item.get("evidence_refs"):
                    item["evidence_refs"][0]["line_sha256"] = new_value
            elif field == "asserted_level":
                if item.get("evidence_refs"):
                    item["evidence_refs"][0]["asserted_level"] = new_value
            return data
    raise core.GateError(f"变异用例找不到能力 {cid}")


def _mutate_markdown(text: str, pattern: Tuple[str, str]) -> str:
    old, new = pattern
    if old not in text:
        raise core.GateError(f"变异用例 markdown 锚点不存在: {old!r}")
    return text.replace(old, new, 1)


def _axes_counts(gate: Mapping[str, Any]) -> Dict[str, int]:
    f = gate["findings"]
    return {
        "cross_source": len(f["divergence_mismatch"]),
        "quote_binding": len(f["quote_binding"]),
        "markdown": (len([m for m in gate["axes"]["markdown_recount"]["metrics"] if not m["ok"]])
                     + len(f["markdown_rows"])),
        "evidence_support": len(f["evidence_support"]),
    }


def build_mutation_benchmark(inputs: Mapping[str, Any], root: Path) -> Dict[str, Any]:
    current = inputs["current"]
    ledger_raw = json.loads(Path(current.path).read_text(encoding="utf-8"))
    md_text = (root / inputs["spec"]["markdown_table"]).read_text(encoding="utf-8")

    clean_gate = run_gate_from(inputs, root)
    clean_counts = _axes_counts(clean_gate)

    cases: List[Dict[str, Any]] = []
    cid_flip = next((c for c in sorted(current.levels) if current.evidence.get(c)),
                    sorted(current.levels)[0])
    target_level = "REAL" if current.levels[cid_flip] != "REAL" else "MOCK"
    cid_div = next((c for c in sorted(current.levels)
                    if current.divergence.get(c, "") not in ("", "OK")), cid_flip)
    cid_div_new = "OK" if current.divergence.get(cid_div, "") not in ("", "OK") else "OVERCLAIM"
    claims = core.parse_markdown_claims(md_text)
    n_ev = claims.get("counts", {}).get("with_evidence")
    md_mutation = (f"有证据 {n_ev}", f"有证据 {n_ev + 1}") if n_ev is not None else None
    plan = [
        ("M1", "篡改对账等级（level flip）", "cross:reconciled_level",
         lambda: _mutate_ledger(ledger_raw, cid_flip, "reconciled_level", target_level), None),
        ("M2", "篡改引文行指纹（line_sha256）", "quote_binding",
         lambda: _mutate_ledger(ledger_raw, cid_flip, "line_sha256", "0" * 16), None),
        ("M3", "抹除证据引用（evidence drop）", "evidence_support",
         lambda: _mutate_ledger(ledger_raw, cid_flip, "drop_evidence", None), None),
        ("M4", "篡改分歧标签（divergence）", "cross_source",
         lambda: _mutate_ledger(ledger_raw, cid_div, "divergence", cid_div_new), None),
        ("M5", "篡改已发布人读表数字", "markdown",
         None, md_mutation),
    ]
    for case_id, label, axis, ledger_fn, md_fn in plan:
        with tempfile.TemporaryDirectory(prefix="h6mut_") as td:
            tmp = Path(td)
            ledger_path = tmp / "ledger.json"
            data = ledger_fn() if ledger_fn else ledger_raw
            ledger_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            md_path = tmp / "table.md"
            if md_fn:
                md_path.write_text(_mutate_markdown(md_text, md_fn), encoding="utf-8")
            else:
                md_path.write_text(md_text, encoding="utf-8")
            mutated = dict(inputs)
            mutated["current"] = core.load_ledger("mutated", ledger_path)
            if md_fn:
                mutated["markdown_claims"] = core.parse_markdown_claims(
                    md_path.read_text(encoding="utf-8"))
            gate = run_gate_from(mutated, root)
            counts = _axes_counts(gate)
            fired = [k for k, v in counts.items() if v > clean_counts[k]]
            if gate["verdict"] != clean_gate["verdict"]:
                fired.append("verdict")
            cases.append({
                "case": case_id, "mutation": label, "injected": 1,
                "detected": 1 if fired else 0, "fired": fired,
                "target_axis": axis,
                "counts_before": clean_counts, "counts_after": counts,
            })
    injected = sum(c["injected"] for c in cases)
    detected = sum(c["detected"] for c in cases)
    # 假阳性：干净副本 vs 干净副本（确定性复算）——不改任何输入必须零新增 finding
    fp = sum(max(0, v - clean_counts[k]) for k, v in _axes_counts(run_gate_from(inputs, root)).items())
    return {
        "schema_version": core.SCHEMA_VERSION,
        "clean_counts": clean_counts,
        "cases": cases, "injected": injected, "detected": detected,
        "recall": round(detected / injected, 6) if injected else None,
        "false_positives": fp,
        "axis_perfect": all(c["detected"] for c in cases),
        "target_axis_hit": {c["case"]: (c["target_axis"].split(":")[-1] in c["fired"]) for c in cases},
    }


# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.work_root).resolve()
    spec_path = Path(args.spec).resolve()
    out = Path(args.out).resolve()
    inputs = load_inputs(root, spec_path)

    gate = run_gate_from(inputs, root)
    mutation = build_mutation_benchmark(inputs, root) if not args.no_mutate else None

    # 判定体（确定性：不含时间戳，可逐字节复算）
    _dump(out / "gate.json", gate)
    _dump(out / "consistency.json", {
        "cross_source": gate["axes"]["cross_source"],
        "quote_binding": gate["axes"]["quote_binding"],
        "markdown_recount": gate["axes"]["markdown_recount"],
        "evidence_support": gate["axes"]["evidence_support"],
        "findings": gate["findings"],
    })
    _dump(out / "increment_ledger.json", gate["increments"])
    if mutation:
        _dump(out / "mutation_benchmark.json", mutation)

    repro = (f"python3 -m tools.maturity_gate_h6 run --work-root {root} "
             f"--spec {spec_path.relative_to(root) if spec_path.is_relative_to(root) else spec_path} "
             f"--out {out.relative_to(root) if out.is_relative_to(root) else out} "
             f"&& python3 -m tools.maturity_gate_h6 verify")
    figs = [report_mod.fig_maturity_profile(gate, out / "fig1_maturity_profile.png"),
            report_mod.fig_axes_health(gate, out / "fig2_axes_health.png"),
            report_mod.fig_increment_flow(gate, out / "fig3_increment_flow.png")]
    md = report_mod.render_report(gate, mutation=mutation, repro_cmd=repro,
                                  anchors=inputs["spec"].get("anchors", []))
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")

    problems = core.verify_artifact(gate, claimed=inputs["baseline"], reconciled=inputs["current"],
                                    markdown_claims=inputs["markdown_claims"],
                                    corpus=inputs["corpus"], work_root=root)
    print(json.dumps({
        "gate_id": gate["gate_id"], "verdict": gate["verdict"],
        "criteria_pass_rate": gate["criteria_pass_rate"],
        "criteria": {c["criterion_id"]: c["pass"] for c in gate["criteria"]},
        "maturity_score": {"before": (gate["maturity"]["before"] or {}).get("maturity_score"),
                           "after": (gate["maturity"]["after"] or {}).get("maturity_score")},
        "increment_counts": gate["increments"].get("counts"),
        "mutation_recall": (mutation or {}).get("recall"),
        "false_positives": (mutation or {}).get("false_positives"),
        "findings": {k: len(v) for k, v in gate["findings"].items()},
        "verify_problems": problems,
        "artifacts": [str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in figs]
                     + [str(out / "gate.json"), str(report_path)],
    }, ensure_ascii=False, indent=2))
    return 0 if (gate["verdict"] == "PASS" and not problems) else 1


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.work_root).resolve()
    out = Path(args.out).resolve()
    gate = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    inputs = load_inputs(root, Path(args.spec).resolve())
    problems = core.verify_artifact(gate, claimed=inputs["baseline"], reconciled=inputs["current"],
                                    markdown_claims=inputs["markdown_claims"],
                                    corpus=inputs["corpus"], work_root=root)
    print(json.dumps({"verified": not problems, "problems": problems,
                      "verdict": gate["verdict"]}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def cmd_mutate(args: argparse.Namespace) -> int:
    root = Path(args.work_root).resolve()
    inputs = load_inputs(root, Path(args.spec).resolve())
    bench = build_mutation_benchmark(inputs, root)
    _dump(Path(args.out).resolve() / "mutation_benchmark.json", bench)
    print(json.dumps({"injected": bench["injected"], "detected": bench["detected"],
                      "recall": bench["recall"],
                      "false_positives": bench["false_positives"],
                      "axis_perfect": bench["axis_perfect"]}, ensure_ascii=False, indent=2))
    return 0 if bench["recall"] == 1.0 and bench["false_positives"] == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="H6 成熟度门（能力增量账本 + 五级成熟度 + 交叉一致性）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "verify", "mutate"):
        p = sub.add_parser(name)
        p.add_argument("--work-root", default=str(REPO_ROOT))
        p.add_argument("--spec", default=str(DEFAULT_SPEC))
        p.add_argument("--out", default=str(DEFAULT_OUT))
        p.add_argument("--report", default=str(DEFAULT_REPORT))
        p.add_argument("--no-mutate", action="store_true")
    args = ap.parse_args(argv)
    try:
        return {"run": cmd_run, "verify": cmd_verify, "mutate": cmd_mutate}[args.cmd](args)
    except core.GateError as exc:
        print(f"GATE_SPEC_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())