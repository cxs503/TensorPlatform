"""编排层：扫描 → 提取 → 定级 → 登记 → 差异报告（一条命令跑通闭环）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .extract import OutcomeIndex, extract_features, merge_records, runtime_records
from .grading import DEFAULT_POLICY, grade_features, policy_digest
from .markers import MarkerScanner
from .model import CapabilityFeature, Grade, digest, now_iso
from .store import Ledger, PIPELINE, PIPELINE_VERSION, set_policy_digest

REPORT_SCHEMA = "capability_discovery.report.v1"


@dataclass
class RunResult:
    """一次发现-登记闭环的完整产物。"""

    run_id: str
    features: List[CapabilityFeature] = field(default_factory=list)
    grades: Dict[str, Grade] = field(default_factory=dict)
    report: Dict[str, Any] = field(default_factory=dict)
    ledger: Optional[Ledger] = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    records_count: int = 0
    files_scanned: int = 0

    def grade_of(self, capability_id: str) -> Optional[Grade]:
        return self.grades.get(capability_id)


def discover(
    roots: Sequence[Path | str],
    root: Path | str = ".",
) -> tuple[List[Any], List[Dict[str, Any]], int]:
    """静态扫描：返回 (记录列表, 诊断列表, 扫描文件数)。"""
    scanner = MarkerScanner(root)
    records = scanner.scan_paths(roots)
    files = {r.source_file for r in records}
    return records, scanner.diagnostics.to_list(), len(files)


def run(
    roots: Sequence[Path | str],
    root: Path | str = ".",
    outcomes: Optional[Dict[str, str]] = None,
    outcome_items: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[Dict[str, Any]] = None,
    ledger: Optional[Ledger] = None,
    write_ledger: bool = False,
    run_id: Optional[str] = None,
    actor: str = "auto",
) -> RunResult:
    """执行一次闭环；``write_ledger=False`` 时只在内存改台账（dry-run）。"""
    pol = policy or DEFAULT_POLICY
    run_id = run_id or f"cd-{uuid.uuid4().hex[:12]}"
    at = now_iso()

    records, diagnostics, files_scanned = discover(roots, root)
    runtime_added = 0
    if outcome_items:
        rt_records, rt_diag = runtime_records(outcome_items, source_root=root)
        # 运行期发现同样受扫描范围约束：只接受落在 roots 内的测试文件，
        # 否则「扫 A 目录」会因为运行结局文件覆盖全仓而偷偷发现别处的能力。
        rt_records = [r for r in rt_records if _in_scope(r.source_file, root, roots)]
        before = len(records)
        records = merge_records(records, rt_records)
        runtime_added = len(records) - before
        diagnostics = diagnostics + rt_diag
    index = OutcomeIndex(outcomes or {})
    features = extract_features(records, index)
    grades = {f.capability_id: g for f, g in zip(features, grade_features(features, pol))}

    led = ledger or Ledger()
    set_policy_digest(led, pol)

    added: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    held: List[Dict[str, Any]] = []

    for feat in features:
        grade = grades[feat.capability_id]
        action, info = led.apply_grade(feat, grade, run_id=run_id, at=at, reason="; ".join(grade.reasons))
        summary = {
            "capability_id": feat.capability_id,
            "label": feat.label,
            "level": info["level"],
            "declared_level": feat.declared_level,
            "outcome_state": grade.outcome_state,
            "confidence": grade.confidence,
            "outcomes": feat.outcomes,
            "anchors": feat.anchors,
            "params": feat.params,
            "marker_formats": feat.marker_formats,
            "tests": [t["nodeid"] for t in feat.tests],
            "sources": [s["path"] for s in feat.sources],
        }
        if action == "added":
            added.append(summary)
        elif action == "updated":
            summary.update(
                {
                    "from_level": info.get("from_level"),
                    "level_changed": info.get("level_changed", False),
                    "revision": info.get("revision"),
                    "reasons": grade.reasons,
                }
            )
            updated.append(summary)
        elif action == "held_retracted":
            summary["retraction_reason"] = info.get("retraction_reason", "")
            held.append(summary)
        else:
            unchanged.append(feat.capability_id)

    missing = led.missing([f.capability_id for f in features])
    levels: Dict[str, int] = {}
    for g in grades.values():
        levels[g.level] = levels.get(g.level, 0) + 1
    formats: Dict[str, int] = {}
    for f in features:
        for fmt in f.marker_formats:
            formats[fmt] = formats.get(fmt, 0) + 1

    chain_ok, chain_msg = led.verify_chain()
    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "run_id": run_id,
        "generated_at": at,
        "pipeline": {"name": PIPELINE, "version": PIPELINE_VERSION},
        "inputs": {
            "roots": [str(r) for r in roots],
            "files_scanned": files_scanned,
            "records": len(records),
            "runtime_records_added": runtime_added,
            "outcomes_indexed": len(index),
            "policy_digest": policy_digest(pol),
            "policy_schema": pol.get("schema"),
        },
        "summary": {
            "discovered": len(features),
            "added": len(added),
            "updated": len(updated),
            "unchanged": len(unchanged),
            "missing": len(missing),
            "held_retracted": len(held),
            "diagnostics": len(diagnostics),
            "runtime_records_added": runtime_added,
            "levels": dict(sorted(levels.items())),
            "marker_formats": dict(sorted(formats.items())),
            "ledger_entries": len(led.entries),
            "ledger_head_hash": led.head_hash(),
            "chain_ok": chain_ok,
            "chain_message": chain_msg,
        },
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "missing": missing,
        "held_retracted": held,
        "diagnostics": diagnostics,
    }

    result = RunResult(
        run_id=run_id,
        features=features,
        grades=grades,
        report=report,
        ledger=led,
        diagnostics=diagnostics,
        records_count=len(records),
        files_scanned=files_scanned,
    )
    if write_ledger and led.path is not None:
        led.save()
    return result


def _in_scope(source_file: str, root: Path | str, roots: Sequence[Path | str]) -> bool:
    """判断运行期发现的源文件是否落在本次扫描范围内。"""
    base = Path(root).resolve()
    target = (base / source_file).resolve()
    for r in roots:
        cand = Path(r)
        cand = (cand if cand.is_absolute() else base / cand).resolve()
        if cand.is_file():
            if target == cand:
                return True
        elif target == cand or cand in target.parents:
            return True
    return False


def report_markdown(report: Dict[str, Any]) -> str:
    """把差异报告渲染成可 review 的 Markdown（人审入口）。"""
    s = report["summary"]
    lines = [
        f"# 能力自动发现报告 · `{report['run_id']}`",
        "",
        f"生成时间：`{report['generated_at']}`　流水线：`{report['pipeline']['name']} v{report['pipeline']['version']}`",
        "",
        "## 1. 输入",
        "",
        f"- 扫描根：`{', '.join(report['inputs']['roots'])}`",
        f"- 测试文件 {report['inputs']['files_scanned']} 个，marker 记录 {report['inputs']['records']} 条",
        f"- 运行结局索引 {report['inputs']['outcomes_indexed']} 条（0 = 仅静态发现，全部判为 not_run）",
        f"- 定级策略指纹：`{report['inputs']['policy_digest'][:16]}`",
        "",
        "## 2. 差异摘要",
        "",
        "| 指标 | 数量 |",
        "|---|---|",
        f"| 发现能力 | {s['discovered']} |",
        f"| 新增登记 | {s['added']} |",
        f"| 更新登记 | {s['updated']} |",
        f"| 未变 | {s['unchanged']} |",
        f"| 台账有而本次缺失 | {s['missing']} |",
        f"| 撤回态保持（不自动复活） | {s['held_retracted']} |",
        f"| 扫描诊断 | {s['diagnostics']} |",
        f"| 哈希链 | {'OK' if s['chain_ok'] else 'BROKEN'} — {s['chain_message']} |",
        "",
        f"等级分布：`{s['levels']}`　marker 格式分布：`{s['marker_formats']}`",
        "",
        "## 3. 新增",
        "",
    ]
    if report["added"]:
        lines += ["| 能力 | 等级 | 结局 | 置信 | 测试 |", "|---|---|---|---|---|"]
        for a in report["added"]:
            lines.append(
                f"| `{a['capability_id']}` | {a['level']} | {a['outcome_state']} | {a['confidence']} | "
                f"{len(a['tests'])} |"
            )
    else:
        lines.append("（无）")
    lines += ["", "## 4. 更新", ""]
    if report["updated"]:
        lines += ["| 能力 | 变更 | rev | 理由 |", "|---|---|---|---|"]
        for u in report["updated"]:
            lines.append(
                f"| `{u['capability_id']}` | {u.get('from_level')} → {u['level']} | {u.get('revision')} | "
                f"{(u.get('reasons') or [''])[0][:60]} |"
            )
    else:
        lines.append("（无）")
    lines += ["", "## 5. 缺失（台账有、本次未发现；只报告不删除）", ""]
    if report["missing"]:
        lines += ["| 能力 | 台账等级 | 上次更新 |", "|---|---|---|"]
        for m in report["missing"]:
            lines.append(f"| `{m['capability_id']}` | {m['level']} | {m['last_updated']} |")
    else:
        lines.append("（无）")
    if report["held_retracted"]:
        lines += ["", "## 6. 撤回态保持（自动登记不覆盖人工撤回）", ""]
        for h in report["held_retracted"]:
            lines.append(f"- `{h['capability_id']}` — 撤回理由：{h.get('retraction_reason', '')}")
    if report["diagnostics"]:
        lines += ["", "## 7. 扫描诊断（需人审）", ""]
        for d in report["diagnostics"]:
            lines.append(f"- `{d['source_file']}:{d['lineno']}` [{d['severity']}] {d['message']}")
    lines.append("")
    return "\n".join(lines)