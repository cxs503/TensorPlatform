"""能力台账自动审计 v2 命令行：``audit`` / ``verify``。

退出码
======
0 = 验收判据全 PASS
1 = 判据未全过（数字如实落在 ``acceptance.json``）
2 = 升级路径数据不合格（缺 MOCK/STUB 路径等）
4 = spec 配置错误（配置错误 ≠ 审计结论）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from tools.capability_audit_gate.engine import load_contrast, run_audit

from . import cascade_v2, exec_evidence, figures, ledger_v2, roadmap as roadmap_mod
from .spec_v2 import SpecV2Error, load_spec_v2

#: 零领域语义扫描面（新增代码）
DOMAIN_SCAN_PATHS = ("tools/capability_audit_v2", "tests/capability_audit_v2")


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _domain_scan(root: Path, out_path: Path) -> dict:
    """调用仓内域词扫描器；扫不动就记 NOT_RUN，绝不写成 PASS。"""
    script = root / "tools" / "audit" / "domain_leak_scan.py"
    if not script.is_file():
        return {"status": "NOT_RUN", "reason": "scanner_missing", "hit_count": None}
    cmd = [sys.executable, str(script), "--paths", *DOMAIN_SCAN_PATHS,
           "--json", str(out_path), "--quiet"]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"status": "NOT_RUN", "reason": "scanner_timeout", "hit_count": None}
    data = {}
    if out_path.is_file():
        data = json.loads(out_path.read_text(encoding="utf-8"))
    return {
        "status": "REAL" if proc.returncode == 0 else "HITS",
        "returncode": proc.returncode,
        "hit_count": data.get("hit_count"),
        "files_scanned": len(data.get("files_scanned") or []),
        "targets": list(DOMAIN_SCAN_PATHS),
        "hits": (data.get("hits") or [])[:5],
        "stderr_tail": (proc.stderr or "")[-300:],
    }


def _run_once(spec_path: str, root: Path, labels_path: str, paths_path: str,
              out_dir: Path | None) -> dict:
    spec = load_spec_v2(spec_path, root)
    base_rel = str(spec.get("base_spec_path") or "")
    spec["base_spec_sha256"] = ledger_v2.file_sha256(Path(root) / base_rel) if base_rel else ""
    spec["exec_spec_sha256"] = ledger_v2.file_sha256(Path(root) / spec_path) \
        if (Path(root) / spec_path).is_file() else ""
    t0 = time.perf_counter()
    static_report = run_audit(spec["base_spec"], root=root, cache_path=None)
    static_caps = static_report["capabilities"]

    exec_index = exec_evidence.collect(spec, root)
    verdicts = cascade_v2.adjudicate_all(static_caps, exec_index, spec["thresholds"])

    labels: dict[str, str] = {}
    if labels_path:
        labels = load_contrast(Path(root) / labels_path)
    agree = cascade_v2.agreement(verdicts, labels)
    static_agree = cascade_v2.agreement(
        [{"capability": c["capability"], "level": c["level"]} for c in static_caps], labels)
    agree["by_level"] = {
        lv: {"n": sum(1 for v in verdicts if v["level"] == lv),
             "labelled": sum(1 for v in verdicts if v["level"] == lv and labels.get(v["capability"]))}
        for lv in cascade_v2.tally(verdicts)
    }

    paths = roadmap_mod.load_paths(Path(root) / paths_path, {c["id"] for c in spec["base_spec"]["capabilities"]})
    roadmap = roadmap_mod.build_roadmap(verdicts, paths, root,
                                        ruleset_version=cascade_v2.RULESET_VERSION)

    ev_paths = ledger_v2.collect_evidence_paths(spec, static_caps, root,
                                                extra=[labels_path] if labels_path else [])
    evidence_sha = ledger_v2.evidence_index(ev_paths, root)
    ledger = ledger_v2.build_ledger(spec, verdicts, static_caps, exec_index, labels,
                                    agree, evidence_sha, root,
                                    static_agreement=static_agree)
    canonical = ledger_v2.canonical_json(ledger)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    runtime = time.perf_counter() - t0
    return {
        "spec": spec,
        "static_report": static_report,
        "exec_index": exec_index,
        "verdicts": verdicts,
        "labels": labels,
        "agreement": agree,
        "static_agreement": static_agree,
        "roadmap": roadmap,
        "evidence_sha": evidence_sha,
        "ledger": ledger,
        "digest": digest,
        "runtime_sec": round(runtime, 3),
    }


def _acceptance(result: dict, root: Path, domain: dict, repro: dict) -> dict:
    ledger = result["ledger"]
    tally = ledger["summary"]["level_tally"]
    upgrade_items = result["roadmap"]["items"]
    checks = {
        "C1_coverage_100pct": {
            "value": ledger["summary"]["coverage"],
            "threshold": 1.0,
            "audited": ledger["summary"]["audited"],
            "expected": len(result["spec"]["base_spec"]["capabilities"]),
            "pass": ledger["summary"]["coverage"] == 1.0,
        },
        "C2_agreement_ge_95pct": {
            "value": result["agreement"].get("agreement"),
            "threshold": 0.95,
            "n": result["agreement"].get("n"),
            "majority_baseline": result["agreement"].get("majority_baseline"),
            "mismatches": result["agreement"].get("mismatches"),
            "pass": bool(result["agreement"].get("agreement") is not None
                         and result["agreement"]["agreement"] >= 0.95),
        },
        "C3_roadmap_complete": {
            "mock_stub": tally.get("MOCK", 0) + tally.get("STUB", 0),
            "items": len(upgrade_items),
            "algorithm_refs": sum(len(i["algorithm_refs"]) for i in upgrade_items),
            "steps": sum(len(i["steps"]) for i in upgrade_items),
            "min_steps_required": 3,
            "completion_actions": result["roadmap"]["summary"]["completion_actions"],
            "pass": (len(upgrade_items) == tally.get("MOCK", 0) + tally.get("STUB", 0)
                     and all(i["algorithm_refs"] and len(i["steps"]) >= 3 for i in upgrade_items)),
        },
        "C4_zero_domain_semantics": {
            "value": domain.get("hit_count"),
            "threshold": 0,
            "scanner_status": domain.get("status"),
            "files_scanned": domain.get("files_scanned"),
            "targets": domain.get("targets"),
            "pass": domain.get("status") == "REAL" and domain.get("hit_count") == 0,
        },
        "C5_reproducible_sha256": {
            "digest_run1": repro.get("digest_run1"),
            "digest_run2": repro.get("digest_run2"),
            "equal": repro.get("equal"),
            "pass": bool(repro.get("equal")),
        },
    }
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks.values())}


def _render_report(result: dict, acceptance: dict, out_dir: Path) -> str:
    ledger = result["ledger"]
    a = result["agreement"]
    lines = [
        "# 能力台账自动审计 v2 —— 报告",
        "",
        f"规则集 `{ledger['ruleset_version']}`；能力数 {ledger['summary']['audited']}；"
        f"覆盖率 {ledger['summary']['coverage']:.0%}。",
        "",
        "## 验收判据",
        "",
        "| 判据 | 阈值 | 实测 | 结论 |",
        "|---|---|---|---|",
        f"| C1 覆盖率 | 100% | {ledger['summary']['audited']}/{ledger['summary']['audited']} | "
        f"{'PASS' if acceptance['checks']['C1_coverage_100pct']['pass'] else 'FAIL'} |",
        f"| C2 与人工复核一致率 | ≥95% | {a.get('agreement')}"
        f"（静态管线 {result['static_agreement'].get('agreement')}，多数类基线 "
        f"{a.get('majority_baseline')}） | "
        f"{'PASS' if acceptance['checks']['C2_agreement_ge_95pct']['pass'] else 'FAIL'} |",
        f"| C3 升级路线完整（MOCK/STUB 全有算法引用+步骤） | 100% | "
        f"{acceptance['checks']['C3_roadmap_complete']['items']} 项 / "
        f"{acceptance['checks']['C3_roadmap_complete']['algorithm_refs']} 引用 | "
        f"{'PASS' if acceptance['checks']['C3_roadmap_complete']['pass'] else 'FAIL'} |",
        f"| C4 零领域语义命中 | 0 | {acceptance['checks']['C4_zero_domain_semantics']['value']} | "
        f"{'PASS' if acceptance['checks']['C4_zero_domain_semantics']['pass'] else 'FAIL'} |",
        f"| C5 两次运行 sha256 一致 | 相等 | "
        f"{(acceptance['checks']['C5_reproducible_sha256']['digest_run1'] or '')[:16]}… | "
        f"{'PASS' if acceptance['checks']['C5_reproducible_sha256']['pass'] else 'FAIL'} |",
        "",
        "## 逐能力判定",
        "",
        "| 能力 | v2 等级 | 理由码 | 静态等级 | pytest | probe | 测试不判别 | 人工复核 | 一致 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in ledger["capabilities"]:
        lines.append(
            f"| `{c['id']}` | {c['level']} | {c['reason_code']} | {c['static_level']} | "
            f"{c['exec_status']['pytest_status']} | {c['exec_status']['probe_status']} | "
            f"{'**是**' if c['non_discriminating_tests'] else '否'} | "
            f"{c['label'] or '—'} | {'✓' if c['label_match'] else ('—' if c['label_match'] is None else '✗')} |")
    lines += [
        "",
        "## 升级路线",
        "",
        "| 能力 | 从 | 升级类别 | 算法引用 | 步骤数 |",
        "|---|---|---|---|---|",
    ]
    for item in result["roadmap"]["items"]:
        lines.append(f"| `{item['capability']}` | {item['from_level']} | {item['upgrade_class']} | "
                     f"{len(item['algorithm_refs'])} | {len(item['steps'])} |")
    comp = result["roadmap"]["evidence_completion"]
    lines += ["", f"证据补齐动作（WITHHELD）：{len(comp)} 项，逐条见 `roadmap.json`。", ""]
    text = "\n".join(lines)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    return text


def cmd_audit(args) -> int:
    root = Path(args.root).resolve()
    out_dir = Path(args.out) if Path(args.out).is_absolute() else root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = str(Path(args.spec))

    try:
        result = _run_once(spec_path, root, args.labels, args.paths, out_dir)
    except SpecV2Error as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 4
    except roadmap_mod.RoadmapError as exc:
        print(f"roadmap error: {exc}", file=sys.stderr)
        return 2

    # 复现：同进程再跑一遍完整管线（含可执行证据），比较台账摘要
    repro = {"digest_run1": result["digest"], "digest_run2": None, "equal": False,
             "runs": args.repro_runs}
    if args.repro_runs and args.repro_runs >= 2:
        second = _run_once(spec_path, root, args.labels, args.paths, out_dir)
        repro["digest_run2"] = second["digest"]
        repro["equal"] = second["digest"] == result["digest"]

    domain = _domain_scan(root, out_dir / "domain_scan.json")
    acceptance = _acceptance(result, root, domain, repro)

    ledger_sha = ledger_v2.write_json(out_dir / "ledger.json", result["ledger"])
    ledger_v2.write_json(out_dir / "roadmap.json", result["roadmap"])
    ledger_v2.write_json(out_dir / "evidence_index.json", result["evidence_sha"])
    ledger_v2.write_json(out_dir / "acceptance.json", acceptance)
    ledger_v2.write_json(out_dir / "reproducibility.json", repro)
    ledger_v2.write_json(out_dir / "meta.json", {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_sec": result["runtime_sec"],
        "spec_path": spec_path,
        "labels": args.labels,
        "paths": args.paths,
        "note": "本文件是易变元数据，不参与台账摘要",
    })
    ledger_v2.write_json(out_dir / "sha256_manifest.json", {
        "ledger.json": ledger_sha,
        "ledger_canonical_sha256": result["digest"],
        "evidence_index.json": ledger_v2.file_sha256(out_dir / "evidence_index.json"),
        "roadmap.json": ledger_v2.file_sha256(out_dir / "roadmap.json"),
        "ruleset_version": result["ledger"]["ruleset_version"],
    })

    figs = {}
    try:
        static_tally = result["static_report"]["summary"]["level_tally"]
        figs["level_tally"] = str(figures.level_tally(
            result["ledger"], static_tally, out_dir / "figures" / "level_tally.png"))
        figs["agreement"] = str(figures.agreement(
            result["agreement"], out_dir / "figures" / "agreement.png",
            static_block=result["static_agreement"]))
        figs["evidence_planes"] = str(figures.evidence_planes(
            result["ledger"], out_dir / "figures" / "evidence_planes.png"))
    except Exception as exc:  # 图失败不影响判定，但必须留痕
        figs["error"] = f"{type(exc).__name__}: {exc}"
    ledger_v2.write_json(out_dir / "figures.json", figs)
    _render_report(result, acceptance, out_dir)

    s = result["ledger"]["summary"]
    print(f"audited={s['audited']} coverage={s['coverage']:.3f} "
          f"levels={s['level_tally']} agreement={result['agreement'].get('agreement')} "
          f"exec_bindings={s['exec_bindings']} runtime={result['runtime_sec']}s "
          f"acceptance={'PASS' if acceptance['all_pass'] else 'FAIL'}")
    print(f"outputs: {out_dir}")
    return 0 if acceptance["all_pass"] else 1


def cmd_verify(args) -> int:
    root = Path(args.root).resolve()
    digests = []
    for i in range(args.runs):
        result = _run_once(str(Path(args.spec)), root, args.labels, args.paths, None)
        digests.append(result["digest"])
    equal = len(set(digests)) == 1
    print(json.dumps({"runs": args.runs, "digests": digests, "equal": equal}, indent=1))
    return 0 if equal else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="capability ledger auto-audit v2")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", default=".")
        p.add_argument("--spec", required=True)
        p.add_argument("--labels", default="")
        p.add_argument("--paths", required=True)

    a = sub.add_parser("audit", help="跑完整审计并落盘工件")
    common(a)
    a.add_argument("--out", required=True)
    a.add_argument("--repro-runs", type=int, default=2)
    a.set_defaults(func=cmd_audit)

    v = sub.add_parser("verify", help="可复现校验：跑 N 次比较台账摘要")
    common(v)
    v.add_argument("--runs", type=int, default=2)
    v.set_defaults(func=cmd_verify)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())