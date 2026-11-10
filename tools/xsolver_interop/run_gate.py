#!/usr/bin/env python3
"""Run the cross-solver interoperation gate for a campaiga data package.

    python3 -m tools.xsolver_interop.run_gate \
        --manifest data/campaigns/p106w2/interop_manifest.json \
        --data-dir data/campaigns/p106w2 \
        --out artifacts/p106w2_interop --figures

Writes ``verdict.json`` (machine judged, deterministic digest), ``summary.md``
(programmatically injected numbers, no hand typing) and the figures.
Exit code: 0 when the gate PASSes, 1 when it FAILs or is WITHHELD, 2 on a
manifest/schema error -- a failing gate must never exit 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.xsolver.interop import (  # noqa: E402
    FAIL, PASS, WITHHELD, evaluate, load_manifest, summarize, write_verdict,
)
from services.xsolver.interop.gate import GateError, digest_of  # noqa: E402


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# markdown summary (numbers injected from verdict.json)
# ---------------------------------------------------------------------------
STATUS_ZH = {PASS: "PASS", FAIL: "FAIL", WITHHELD: "WITHHELD"}


def summary_markdown(verdict: Mapping[str, Any], figures: List[Mapping[str, str]],
                     pytest_summary: Optional[str] = None) -> str:
    lines = [
        "# p106w2 跨求解器互裁集成门（n11）· 机器裁决",
        "",
        f"- 战役 `{verdict['campaign']}` · schema `{verdict['schema']}` · 生成 {verdict['generated_at']}",
        f"- 门总体判定：**{verdict['gate_status']}**（honest_fail={verdict['honest_fail']}，"
        f"upgrade_forbidden={verdict['upgrade_forbidden']}）",
        f"- manifest sha256 `{verdict['manifest_digest']}` · verdict sha256 `{verdict['verdict_digest']}`",
        f"- 断裂点按类型统计：`{json.dumps(verdict['fracture_summary']['by_type'], ensure_ascii=False)}`",
        "",
        "## 1 工件（输入快照，sha256 钉住）",
        "",
        "| 工件 | 路径 | 是否可读 | sha256(前12) | 说明 |",
        "|---|---|---|---|---|",
    ]
    for name, art in verdict["artifacts"].items():
        lines.append(f"| `{name}` | `{art['path']}` | {'是' if art['present'] else '否'} | "
                     f"`{(art['sha256'] or '')[:12]}` | {art['reason'] or ''} |")
    lines += ["", "## 2 逐项裁决", ""]
    for item in verdict["items"]:
        counts = item["counts"]
        lines += [f"### {item['id']} — {item['title']}", "",
                  f"判定 **{item['status']}**（PASS {counts[PASS]} / FAIL {counts[FAIL]} / WITHHELD {counts[WITHHELD]}）", "",
                  "| 检查 | 类型 | 状态 | 关键数字 |", "|---|---|---|---|"]
        for check in item["checks"]:
            lines.append(f"| `{check['id']}` | {check['kind']} | {STATUS_ZH[check['status']]} | "
                         f"{_key_numbers(check)} |")
        lines.append("")
    lines += ["## 3 断裂点清单（机器生成）", ""]
    if verdict["fractures"]:
        lines += ["| 项 | 检查 | 类型 | 说明 |", "|---|---|---|---|"]
        for fracture in verdict["fractures"]:
            lines.append(f"| `{fracture['item']}` | `{fracture['check']}` | `{fracture['type']}` | "
                         f"{fracture['message']} |")
    else:
        lines.append("（无断裂点）")
    lines += ["", "## 4 图", ""]
    for figure in figures:
        lines += [f"![{figure['name']}](figures/{figure['name']})", "",
                  f"*{figure['caption']}*", ""]
    if pytest_summary:
        lines += ["## 5 测试", "", f"```\n{pytest_summary}\n```", ""]
    return "\n".join(lines) + "\n"


def _key_numbers(check: Mapping[str, Any]) -> str:
    detail = check.get("detail") or {}
    keys = ("max_rel_diff", "mean_rel_diff", "n_violations", "missing_orders", "overlap_fraction",
            "residual", "spearman", "tolerance", "component_sum")
    parts = []
    for key in keys:
        if key in detail and isinstance(detail[key], (int, float)):
            value = detail[key]
            parts.append(f"{key}={value:.4g}" if isinstance(value, float) else f"{key}={value}")
    if "points" in detail and isinstance(detail["points"], list) and detail["points"]:
        first = detail["points"][0]
        if isinstance(first, Mapping) and "factor_below_lo" in first:
            parts.append("; ".join(f"{p['label']}={p['value']:.4g}" for p in detail["points"]))
    if "entries" in detail and isinstance(detail["entries"], list) and detail["entries"]:
        parts.append(", ".join(f"{e['label']}={e['value']:.6g}" for e in detail["entries"]
                               if e.get("value") is not None))
    if "band_a" in detail:
        parts.append(f"band_a={detail['band_a']}, band_b={detail['band_b']}")
    if "record" in detail and isinstance(detail["record"], Mapping):
        parts.append(f"pass={detail['record'].get('pass')}, cases_run={detail['record'].get('cases_run')}")
    return "; ".join(parts) if parts else "—"


def _load_figure_runner(path: str):
    """Load a campaign-owned figure runner (domain content stays in the data package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("interop_figure_runner", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load figure runner {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "draw"):
        raise SystemExit(f"figure runner {path} exposes no draw(verdict, data_dir, out_dir)")
    return module


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="run the cross-solver interop gate")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--data-dir", required=True, help="root the manifest artifact paths are relative to")
    parser.add_argument("--out", required=True)
    parser.add_argument("--figures", default=None,
                        help="path to a campaign-owned figure runner exposing draw(verdict, data_dir, out_dir)")
    parser.add_argument("--pytest-summary", default=None)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        verdict = evaluate(load_manifest(args.manifest), root=data_dir)
    except GateError as exc:
        (out_dir / "gate_error.json").write_text(json.dumps({"error": str(exc)}, ensure_ascii=False),
                                                 encoding="utf-8")
        print(f"gate error (fail-closed): {exc}", file=sys.stderr)
        return 2

    write_verdict(verdict, out_dir / "verdict.json")
    figures: List[Dict[str, str]] = []
    if args.figures:
        runner = _load_figure_runner(args.figures)
        figures = runner.draw(verdict, data_dir, out_dir / "figures")
        (out_dir / "figures" / "figures_metadata.json").write_text(
            json.dumps(figures, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pytest_summary = Path(args.pytest_summary).read_text(encoding="utf-8") if args.pytest_summary else None
    markdown = summary_markdown(verdict, figures, pytest_summary)
    (out_dir / "summary.md").write_text(markdown, encoding="utf-8")
    (out_dir / "summary_digest.json").write_text(
        json.dumps({"summary_md_sha256": digest_of(markdown)}, indent=2) + "\n", encoding="utf-8")
    print(summarize(verdict))
    print(f"\nartifacts -> {out_dir} (verdict.json, summary.md{', figures/' if figures else ''})")
    return 0 if verdict["gate_status"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())