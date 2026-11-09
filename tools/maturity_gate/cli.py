#!/usr/bin/env python3
"""战役成熟化整合运行器：规格 → 证据 → 裁定 → 台账 → 契约 → 报告（一次跑完，零手抄）。

产出（全部可在同一命令下逐字节复算）：

* ``artifacts/p54w3_maturity/gate.json`` —— 判定体（含节点/判据/检查三级明细 + 五条交付判据）
* ``artifacts/p54w3_maturity/p54w3_maturity_ledger.json`` —— 能力台账（节点↔条目双射）
* ``artifacts/p54w3_maturity/p54w3_maturity_contracts.json`` —— 契约完整性
* ``artifacts/p54w3_maturity/p54w3_maturity_report_check.json`` —— 报告六要素检查
* ``artifacts/p54w3_maturity/fig1_node_status.png`` / ``fig2_delivery_criteria.png``
* ``docs/P54W3-N08_maturity_integration.md`` —— 论文级报告（六要素）

用法::

    python3 -m tools.maturity_gate.cli --work-root /root/tp-agent/work
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_gate import contracts as contracts_engine  # noqa: E402
from tools.maturity_gate import gate as gate_engine  # noqa: E402
from tools.maturity_gate import ledger as ledger_engine  # noqa: E402
from tools.maturity_gate import report as report_engine  # noqa: E402
from tools.maturity_gate.evidence import EvidenceResolver  # noqa: E402
from tools.maturity_gate.spec import load_spec  # noqa: E402

DEFAULT_SPEC = REPO_ROOT / "data" / "maturity_gate" / "p54w3_campaign.spec.json"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "maturity_gate" / "contracts.manifest.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "p54w3_maturity"
DEFAULT_REPORT = REPO_ROOT / "docs" / "P54W3-N08_maturity_integration.md"
EVIDENCE_PROTOCOL = ("节点把可复算数字写进 `artifacts/p54w3_evidence/<node_key>.json`"
                     "（结构见 `contracts/maturity-evidence.v1.schema.json`），"
                     "`criteria.<id>.values.<name>` 供判据取值、`criteria.<id>.pass` 供互裁，"
                     "`pytest.{returncode,passed,failed}` 供『全链测试』判据取值。")

_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def run_pytest(cmd: Sequence[str], cwd: Optional[Path] = None) -> Dict[str, Any]:
    """跑测试并把机器可读结论落成 dict（不靠肉眼看输出）。"""
    proc = subprocess.run(list(cmd), cwd=str(cwd or REPO_ROOT), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for num, word in _PYTEST_SUMMARY.findall(out):
        key = "error" if word.startswith("error") else word
        counts[key] = int(num)
    # 去掉耗时/路径等易变量，保证判定体可逐字节复算
    tail = [re.sub(r"in [\d.]+s", "in <t>s", line)
            for line in out.strip().splitlines()[-6:]]
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "errors": counts["error"],
        "skipped": counts["skipped"],
        "tail": tail,
        "green": proc.returncode == 0 and counts["failed"] == 0 and counts["error"] == 0,
    }


def domain_scan(paths: Sequence[str]) -> Dict[str, Any]:
    """零领域语义扫描（复用平台既有 CI 扫描器 + 词表工件）。"""
    try:
        from tools.evidence_domain_scan import scan_paths
    except Exception as exc:  # pragma: no cover - 扫描器缺失必须显形，不能静默通过
        return {"available": False, "error": str(exc), "hits": [], "clean": False}
    result = scan_paths([str(p) for p in paths])
    return {"available": True, "scanned_files": result["scanned_files"],
            "hits": result["hits"], "clean": not result["hits"],
            "targets": result["targets"]}


def _figures(out_dir: Path, gate: Mapping[str, Any]) -> List[Dict[str, str]]:
    """两张图：节点状态堆叠条 + 五条交付判据看板。标签一律 ASCII。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    figures: List[Dict[str, str]] = []
    summaries = gate["node_summaries"]
    labels = [s["node_key"].split("-")[-1] if "-" in s["node_key"] else s["node_key"]
              for s in summaries]
    passed = [s["passed"] for s in summaries]
    failed = [s["failed"] for s in summaries]
    withheld = [s["withheld"] for s in summaries]
    thr = gate["campaign"]["criteria_pass_threshold"]

    fig, ax = plt.subplots(figsize=(9, 4.2))
    idx = range(len(labels))
    ax.bar(idx, passed, color="#3b7dd8", label="PASS")
    ax.bar(idx, failed, bottom=passed, color="#d64545", label="FAIL")
    ax.bar(idx, withheld, bottom=[p + f for p, f in zip(passed, failed)],
           color="#c9c9c9", label="WITHHELD")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("criteria count")
    ax.set_title(f"Per-node criteria status (threshold={thr})")
    ax.legend(fontsize=8)
    ax2 = ax.twinx()
    ax2.plot(list(idx), [s["pass_ratio"] for s in summaries], "k--o", ms=4, lw=1,
             label="pass ratio")
    ax2.axhline(thr, color="#d6a000", ls=":", lw=1.4, label=f"green line {thr}")
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("pass ratio")
    ax2.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    p1 = out_dir / "fig1_node_status.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig1_node_status.png", "path": str(p1.relative_to(REPO_ROOT)),
                    "caption": "每节点判据 P/N 堆叠（蓝=PASS，红=FAIL，灰=WITHHELD）+ 通过率折线"
                               "与 0.8 绿线。灰柱意味着『不知道』，不是『不过』。"})

    crit = gate["delivery_criteria"]
    names = [k for k in ("a", "b", "c", "d", "e", "f") if k in crit]
    fig, ax = plt.subplots(figsize=(8.4, 2.6))
    colors = ["#2e9e5b" if crit[k]["pass"] else "#d64545" for k in names]
    ax.barh([crit[k]["name"] for k in names], [1 for _ in names], color=colors)
    for i, k in enumerate(names):
        ax.text(0.02, i, crit[k]["detail"][:96], va="center", fontsize=7, color="white")
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.set_title("Delivery criteria (green=pass, red=fail)")
    fig.tight_layout()
    p2 = out_dir / "fig2_delivery_criteria.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig2_delivery_criteria.png", "path": str(p2.relative_to(REPO_ROOT)),
                    "caption": "整合节点六条交付判据看板（a 上游判据≥80%、b 跨仓集成 pytest、"
                               "c 台账双射、d 契约完整、e 报告六要素、f 零领域语义）。"})
    return figures


def deliver(spec_path: Path, work_root: Optional[str], out_dir: Path, report_path: Path,
            manifest_path: Path, repo_roots: Mapping[str, str], run_tests: bool,
            write_figures: bool) -> Dict[str, Any]:
    """一次跑完：证据 → 裁定 → 台账 → 契约 → 运行证据（pytest/域扫描）→ 图 → 报告。"""
    spec = load_spec(spec_path)
    resolver = EvidenceResolver(repo_roots=repo_roots, work_root=work_root)
    gate = gate_engine.evaluate_campaign(spec, resolver)

    entries = ledger_engine.build_entries(gate)
    bijection = ledger_engine.verify_bijection(entries, spec.node_keys())
    ledger_doc = ledger_engine.ledger_document(entries, bijection, spec.spec_id,
                                               gate["input_digest"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract_check = contracts_engine.check_manifest(REPO_ROOT, manifest)

    # pytest 判定放后面跑：跨仓集成测试要读本次生成的报告（否则干净树上首轮必红）
    pytest_res: Dict[str, Any] = {"green": None, "returncode": None, "passed": None,
                                  "failed": None, "tail": ["pending"]}
    scan = domain_scan(["tools/maturity_gate", "backend/api/maturity_gate.py",
                        "tests/test_campaign_maturity_gate.py",
                        "tests/integration/test_cross_repo_maturity_delivery.py"])

    c = gate["campaign"]
    gate["delivery_criteria"] = {
        "a": {"name": "a upstream criteria >= threshold",
              "pass": bool(c["criterion_a_all_nodes_ge_threshold"]),
              "detail": f"delivered {c['nodes_delivered']}/{c['nodes_total']}, "
                        f"green {len(c['nodes_green'])}/{c['nodes_total']}, "
                        f"min pass_ratio {c['min_pass_ratio']}, absent {c['nodes_absent']}"},
        "b": {"name": "b cross-repo integration pytest green",
              "pass": bool(pytest_res.get("green")),
              "detail": "pending (runs after report render)"},
        "c": {"name": "c ledger one-to-one with nodes",
              "pass": bool(bijection["one_to_one"]),
              "detail": f"entries {bijection['entries_total']} vs nodes {bijection['nodes_total']}, "
                        f"levels {bijection['levels']}"},
        "d": {"name": "d API contract doc complete",
              "pass": bool(contract_check["complete"]),
              "detail": f"completeness {contract_check['completeness']:.4f}, "
                        f"missing {contract_check['missing']}"},
        "e": {"name": "e report six elements", "pass": False, "detail": "pending render"},
        "f": {"name": "f zero domain semantics",
              "pass": bool(scan.get("clean")),
              "detail": f"scanned {scan.get('scanned_files')} files, "
                        f"hits {len(scan.get('hits', []))}"},
    }

    limitations = [
        f"上游多数节点仍在飞：{len(c['nodes_absent'])} 个节点零可复算证据 → 战役裁定停在 "
        f"{c['verdict']}，本报告不把『在飞』写成『通过』。",
        "证据协议是**新契约**：既有上游交付（如 study.json）字段命名各异，只有显式映射进规格的"
        "节点能被取值复算；未映射的字段一律 WITHHELD，不做模糊猜测。",
        "判据通过率分母是**声明判据数**：上游若少声明一条判据，分母同步变小，这是刻意的"
        "（少声明即少交付），故报告中逐条列出判据总数供审阅。",
        "跨仓集成 pytest 只覆盖平台侧判定链（合成域夹具 + 真实战役工件）；不代跑上游仓库测试——"
        "上游测试执行属于其 own mission 的验收，平台不越权。",
        "本战役多数节点无实验基准（残差/正交性/闭合量），故『误差』按阈值余量口径给出，"
        "不作跨学科的 3% 统一宣称。",
        "既有机制分工：`scripts/maturity_gate.py` 管平台模块 maturity 标签证据、"
        "`tools/criteria_governance/maturity.py` 管判据集四维治理，本包只管战役节点交付裁定；"
        "三者不重叠，后续若合并须先统一证据指纹口径（本包用 canonical_json 指纹以便磁盘/内存两路同值）。",
    ]
    error_notes = [
        "误差口径：判据多为「≤阈值」型谓词（相对误差/残差/闭合量），『相对阈值余量』列 = |实测|/|阈值|，"
        "小于 1 即满足；这不是与实验值的对标误差。",
        "有实验/理论锚的对标项按各自阈值（2%–25%）表述，不统一吹成 3%；无基准项显式声明原因。",
    ]

    def render(figures_, report_check_):
        md = report_engine.render_report(
            gate, ledger_doc, contract_check, report_check=report_check_, figures=figures_,
            evidence_protocol=EVIDENCE_PROTOCOL, limitations=limitations,
            error_notes=error_notes,
            run_command="python3 -m tools.maturity_gate.cli --work-root <work_root>")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8")
        return md

    # 第一轮：图（e 待定）→ 报告 → 六要素自检 → 回写 e
    figures = _figures(out_dir, gate) if write_figures else []
    md = render(figures, None)
    report_check = report_engine.check_six_elements(md, figures=figures)
    gate["delivery_criteria"]["e"] = {
        "name": "e report six elements",
        "pass": bool(report_check["complete"]),
        "detail": ("missing " + str(report_check["missing"])) if report_check["missing"]
                  else "all present",
    }
    # 跨仓集成 pytest（此时报告已落盘，测试可校验真实交付物）
    if run_tests:
        pytest_res = run_pytest([sys.executable, "-m", "pytest", "-q",
                                 "tests/test_campaign_maturity_gate.py",
                                 "tests/integration/test_cross_repo_maturity_delivery.py"])
    gate["delivery_criteria"]["b"] = {
        "name": "b cross-repo integration pytest green",
        "pass": bool(pytest_res.get("green")),
        "detail": f"rc={pytest_res.get('returncode')} passed={pytest_res.get('passed')} "
                  f"failed={pytest_res.get('failed')} errors={pytest_res.get('errors')}",
    }

    # 第二轮：图与报告反映最终判据态
    if write_figures:
        figures = _figures(out_dir, gate)
    md = render(figures, report_check)
    report_check = report_engine.check_six_elements(md, figures=figures)
    gate["delivery_criteria"]["e"]["pass"] = bool(report_check["complete"])

    gate["integration_pytest"] = {k: v for k, v in pytest_res.items() if k != "tail"}
    gate["integration_pytest_tag"] = pytest_res.get("tail")
    gate["domain_scan"] = scan
    gate["contracts"] = contract_check
    gate["ledger"] = {"one_to_one": bijection["one_to_one"],
                      "entries_total": bijection["entries_total"],
                      "levels": bijection["levels"]}
    gate["report_check"] = report_check
    gate["limitations"] = limitations
    hard_fail = [k for k in ("b", "c", "d", "f")
                 if gate["delivery_criteria"][k]["pass"] is False]
    if hard_fail:
        gate["delivery_verdict"] = "FAIL"
    elif all(v["pass"] for v in gate["delivery_criteria"].values()):
        gate["delivery_verdict"] = "PASS"
    else:
        gate["delivery_verdict"] = "WITHHELD"
    gate["delivery_hard_fail"] = hard_fail

    out_dir.mkdir(parents=True, exist_ok=True)
    gate_engine.dump_json(gate, out_dir / "gate.json")
    gate_engine.dump_json(ledger_doc, out_dir / "p54w3_maturity_ledger.json")
    gate_engine.dump_json(contract_check, out_dir / "p54w3_maturity_contracts.json")
    gate_engine.dump_json(report_check, out_dir / "p54w3_maturity_report_check.json")
    return {"gate": gate, "ledger": ledger_doc, "contracts": contract_check,
            "report_check": report_check, "figures": figures}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="战役成熟化整合运行器")
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--work-root", default="/root/tp-agent/work")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--repo-root", action="append", default=[],
                    help="name=path，显式指定仓库工作树（可多次）")
    ap.add_argument("--no-tests", action="store_true", help="跳过 pytest 判定（仅调试用）")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    repo_roots: Dict[str, str] = {}
    for item in args.repo_root:
        name, _, path = item.partition("=")
        repo_roots[name] = path

    result = deliver(Path(args.spec), args.work_root, Path(args.out_dir), Path(args.report),
                     Path(args.manifest), repo_roots, run_tests=not args.no_tests,
                     write_figures=not args.no_figures)
    gate = result["gate"]
    print(json.dumps({"campaign": gate["campaign"],
                      "delivery_criteria": {k: v["pass"] for k, v in
                                            gate["delivery_criteria"].items()},
                      "delivery_verdict": gate["delivery_verdict"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())