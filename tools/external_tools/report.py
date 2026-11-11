"""Report + artifacts for an external-tool ledger.

The report is generated *from* the ledger, never typed: every number in the
markdown is read out of ``ledger.json`` so a claim cannot drift from the run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

STATUS_ORDER = ["REAL", "MOCK", "WITHHELD", "FAIL"]


def _fmt(x: Any) -> str:
    if isinstance(x, float):
        if x == 0:
            return "0"
        if abs(x) < 1e-3 or abs(x) >= 1e5:
            return f"{x:.3e}"
        return f"{x:.6g}"
    return str(x)


def write_artifacts(ledger: Mapping[str, Any], out_dir: str | Path,
                    stem: str = "external_tools") -> Dict[str, str]:
    """Persist ``<stem>.json``, ``<stem>_latest.json`` and the markdown report."""

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = str(ledger.get("generated_at", "")).replace(":", "").replace("-", "")[:15]
    run_path = d / f"{stem}_{stamp}.json"
    latest_path = d / "latest.json"
    md_path = Path(str(run_path).replace(".json", ".md"))
    blob = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    run_path.write_text(blob, encoding="utf-8")
    latest_path.write_text(blob, encoding="utf-8")
    md_path.write_text(render_markdown(ledger), encoding="utf-8")
    return {"run": str(run_path), "latest": str(latest_path), "markdown": str(md_path)}


def render_markdown(ledger: Mapping[str, Any]) -> str:
    m = ledger.get("measured", {})
    cov = ledger.get("coverage", {})
    counts = m.get("counts_by_status", {})
    lines: List[str] = []
    add = lines.append
    add(f"# 外部工具类接入台账 — {ledger.get('suite_id')}")
    add("")
    add(f"- 生成时间（UTC）：{ledger.get('generated_at')}（不参与 digest）")
    add(f"- 规格：`{ledger.get('spec_path')}` spec_digest=`{str(ledger.get('spec_digest'))[:16]}`")
    add(f"- 覆盖判据：REAL 类数 ≥ {cov.get('target_classes')} → "
        f"**{m.get('real_classes')} / {cov.get('target_classes')}**，"
        f"缺口 {cov.get('shortfall')}，裁定 **{ledger.get('verdict', '')}**")
    add(f"- 证据等级分布：" + "，".join(f"{k}={counts.get(k, 0)}" for k in STATUS_ORDER))
    add(f"- 结构指纹 digest=`{str(ledger.get('digest'))[:16]}`，"
        f"数值指纹 value_digest=`{str(ledger.get('value_digest'))[:16]}`")
    add("")
    add("## 1. 逐类结果")
    add("")
    add("| 工具类 | 类别 | 状态 | 探针版本 | 冒烟调用 | 不变式 | 耗时(s) |")
    add("|---|---|---|---|---|---|---|")
    for rec in ledger.get("classes", []):
        probe = rec.get("probe") or {}
        invs = rec.get("invariants") or []
        inv_s = "；".join(
            f"{i['metric']}={_fmt(i['observed'])} {'✓' if i.get('ok') else '✗'}"
            for i in invs) or "—"
        add(f"| `{rec['tool_id']}` | {rec.get('category')} | **{rec.get('status')}** | "
            f"{probe.get('kind')}:{probe.get('target')} {probe.get('version') or ''} | "
            f"{(rec.get('call') or {}).get('entry', '—')} | {inv_s} | "
            f"{rec.get('duration_s')} |")
    add("")
    add("## 2. 观察量与判据")
    add("")
    add("| 工具类 | 指标 | 观测值 | 判据 op/limit | 通过 |")
    add("|---|---|---|---|---|")
    for rec in ledger.get("classes", []):
        for inv in rec.get("invariants") or []:
            tgt = "" if inv.get("target") is None else f" target={_fmt(inv['target'])}"
            add(f"| `{rec['tool_id']}` | {inv['metric']} | {_fmt(inv.get('observed'))} | "
                f"{inv['op']} {_fmt(inv['limit'])}{tgt} | {'✓' if inv.get('ok') else '✗'} |")
    add("")
    add("## 3. 失败与降级")
    add("")
    bad = [r for r in ledger.get("classes", []) if r.get("status") != "REAL"]
    if not bad:
        add("无：全部类均达 REAL（探针命中 + 冒烟调用成功 + 不变式全过）。")
    else:
        for rec in bad:
            add(f"- `{rec['tool_id']}` → **{rec.get('status')}**：{rec.get('detail')}")
    add("")
    add("## 4. 环境")
    add("")
    env = ledger.get("environment", {})
    add(f"python={env.get('python')} platform={env.get('platform')} "
        f"executable={env.get('executable')}")
    add("")
    return "\n".join(lines) + "\n"