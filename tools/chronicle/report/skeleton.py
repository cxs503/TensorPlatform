#!/usr/bin/env python3
"""Top-journal paper skeleton for chronicle reports (IN18-T3, section ①).

Ten-section structure fixed by the task book:

    Abstract / Introduction / System Architecture / Methods / Experimental Setup
    / Results / Discussion / Limitations / Threats to Validity / Reproducibility

The generator is deliberately dumb about physics: it emits *structure plus
enforcement scaffolding* (provenance blocks in Abstract and Results, a table
stub, figure slots, reproducibility manifest hooks) and refuses to emit a
manuscript whose sections are out of order or empty. The numbers are never
produced here - they come from ``provenance.ProvenanceStore`` records bound to a
``snapshot.Snapshot`` query, which is what makes the W2 veto machine-checkable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .provenance import BLOCK_BEGIN, BLOCK_END

SCHEMA_VERSION = "chronicle-skeleton/1"

SECTIONS: list[tuple[str, list[str], str]] = [
    (
        "Abstract",
        ["背景与问题", "方法与数据", "主要结果", "结论与适用边界"],
        "150-250 词；每个量化结论必须带 provenance token（本块受门禁强制）。",
    ),
    (
        "Introduction",
        ["问题陈述", "已有工作与差距", "本文贡献（逐条可验证）", "论文组织"],
        "贡献条目须写成可被测试或查询复核的陈述，不写『显著提升』类无基准断言。",
    ),
    (
        "System Architecture",
        ["组件与边界", "数据流（artifact → snapshot → query → 图/表）", "契约与版本", "失败模式"],
        "标注每个组件的证据等级；未实现的路径写 NOT_SUPPORTED，不写占位符冒充。",
    ),
    (
        "Methods",
        ["证据采集与快照", "溯源标记协议", "门禁判定规则（PASS/FAIL 码）", "统计与容差"],
        "每条规则给出触发条件与判定码；容差必须给出取值理由。",
    ),
    (
        "Experimental Setup",
        ["数据源与快照哈希", "软件环境与版本", "运行命令", "负例（对抗）测试设计"],
        "快照哈希、解释器与库版本必须由程序写入，不得手抄。",
    ),
    (
        "Results",
        ["主结果表", "主结果图", "负例测试结果", "饱和/覆盖分析"],
        "表格与图内数字全部来自 snapshot 查询；本块受门禁强制。",
    ),
    (
        "Discussion",
        ["结果含义", "与已有工作的关系", "工程可用性", "未解释现象"],
        "区分『机器可验证的结论』与『作者判断』，后者不得进入 Abstract 数字。",
    ),
    (
        "Limitations",
        ["覆盖范围限制", "方法学限制", "数据限制", "不可判定项"],
        "不得以沉默代替声明：写不出的限制等于隐藏的限制。",
    ),
    (
        "Threats to Validity",
        ["内部效度", "外部效度", "构念效度", "结论效度"],
        "逐项给出威胁机制与已采取的缓解措施。",
    ),
    (
        "Reproducibility",
        ["快照重建", "一键复跑命令", "判据与预期输出", "产物清单"],
        "复跑命令须为本仓可直接执行的命令；预期输出含 exit code 与关键判据数字。",
    ),
]

SECTION_ORDER = [s[0] for s in SECTIONS]


@dataclass
class SkeletonMeta:
    title: str
    subtitle: str
    authors: str
    snapshot_hash: str
    snapshot_ts: str
    issued: str
    mission: str = "IN18-T3"

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "authors": self.authors,
            "snapshot_hash": self.snapshot_hash,
            "snapshot_ts": self.snapshot_ts,
            "issued": self.issued,
            "mission": self.mission,
        }


def _header(meta: SkeletonMeta) -> list[str]:
    return [
        f"# {meta.title}",
        "",
        f"**{meta.subtitle}**",
        "",
        f"作者/责任主体：{meta.authors}  ",
        f"战役节点：{meta.mission}  ",
        f"证据快照：`{meta.snapshot_hash}`（采集于 {meta.snapshot_ts}）  ",
        f"签发：{meta.issued}",
        "",
        "> 所有位于 `prov:begin/end` 块内的数字均带 `{{prov:<id>}}` 溯源标记，"
        "对应 `provenance.json` 中的 SQL 与快照哈希；校验器重跑 SQL 比对，不符即 FAIL。",
        "",
    ]


def render_skeleton(meta: SkeletonMeta, bodies: dict[str, list[str]] | None = None) -> str:
    """Render the ten-section skeleton. ``bodies`` overrides a section body with
    generated content (used by ``demo.py``); absent sections keep the stub."""
    bodies = bodies or {}
    lines = _header(meta)
    for idx, (name, subs, guidance) in enumerate(SECTIONS, start=1):
        lines.append(f"## {idx}. {name}")
        lines.append("")
        if name in bodies:
            lines.extend(bodies[name])
            lines.append("")
            continue
        lines.append(f"> 写作要求：{guidance}")
        lines.append("")
        for sub in subs:
            lines.append(f"### {sub}")
            lines.append("")
            lines.append(f"<!-- TODO({sub}) -->")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def results_scaffold(table_id: str = "table1") -> list[str]:
    """A table stub that already declares its provenance columns."""
    return [
        BLOCK_BEGIN,
        "",
        f"表 {table_id}：占位表头，数值须由 `ProvenanceStore.add_number()` 生成并内联 token。",
        "",
        "| 指标 | 数值 | 溯源 |",
        "|---|---|---|",
        "| _待程序化注入_ | - | - |",
        "",
        BLOCK_END,
    ]


def validate_skeleton(markdown: str, min_words: int = 0) -> dict:
    """Structural gate on the skeleton itself: all ten sections, in order, non-empty."""
    findings: list[dict] = []
    heads = [ln[3:].strip() for ln in markdown.splitlines() if ln.startswith("## ")]
    names = [h.split(". ", 1)[1] if ". " in h else h for h in heads]
    for name in SECTION_ORDER:
        if name not in names:
            findings.append({"code": "SKELETON_SECTION_MISSING", "severity": "FAIL", "detail": name})
    present = [n for n in names if n in SECTION_ORDER]
    # A section list can contain all ten names and still be misordered, so the
    # check is monotonicity of the canonical index, not set equality.
    canonical_idx = [SECTION_ORDER.index(n) for n in present]
    if canonical_idx != sorted(canonical_idx):
        findings.append(
            {"code": "SKELETON_SECTION_ORDER", "severity": "FAIL", "detail": " -> ".join(present)}
        )
    # bodies
    chunks = markdown.split("\n## ")
    body_map: dict[str, str] = {}
    for chunk in chunks[1:]:
        head, _, body = chunk.partition("\n")
        key = head.split(". ", 1)[1] if ". " in head else head
        body_map[key.strip()] = body
    for name in SECTION_ORDER:
        body = body_map.get(name, "")
        words = [w for w in body.replace("\n", " ").split() if w.strip()]
        if not words:
            findings.append({"code": "SKELETON_SECTION_EMPTY", "severity": "FAIL", "detail": name})
        elif min_words and len(words) < min_words:
            findings.append(
                {
                    "code": "SKELETON_SECTION_THIN",
                    "severity": "FAIL",
                    "detail": f"{name}: {len(words)} words < {min_words}",
                }
            )
    return {
        "sections_found": present,
        "sections_expected": SECTION_ORDER,
        "ok": not any(f["severity"] == "FAIL" for f in findings),
        "findings": findings,
    }


def write_skeleton(markdown: str, out_path: str | Path, meta: SkeletonMeta) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "meta": meta.as_dict(),
        "sections": SECTION_ORDER,
        "manuscript": {"path": str(out_path), "sha256": hashlib.sha256(markdown.encode()).hexdigest()},
    }
    (out_path.parent / "skeleton.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out_path


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="emit a ten-section journal skeleton")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Chronicle Report Skeleton")
    ap.add_argument("--subtitle", default="Ten-section journal scaffold with provenance enforcement")
    ap.add_argument("--authors", default="TensorPlatform chronicle line")
    ap.add_argument("--snapshot-hash", default="0" * 64)
    ap.add_argument("--snapshot-ts", default="1970-01-01T00:00:00+00:00")
    ap.add_argument("--issued", default="1970-01-01")
    args = ap.parse_args(list(argv) if argv is not None else None)
    meta = SkeletonMeta(
        title=args.title,
        subtitle=args.subtitle,
        authors=args.authors,
        snapshot_hash=args.snapshot_hash,
        snapshot_ts=args.snapshot_ts,
        issued=args.issued,
    )
    md = render_skeleton(meta)
    path = write_skeleton(md, args.out, meta)
    print(json.dumps({"written": str(path), **validate_skeleton(md)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())