"""扫根目录 → 逐 mission 沉淀 → 汇总（判据可机裁）。

两遍扫描
--------
1. **索引遍**：列出候选 mission 目录，给每个报告工件算 sha256，统计「同一份内容出现在
   多少个 mission 目录里」——``shared_shas`` = 出现次数 > 1 的集合。
   语料实测：整仓检出会把仓根 ``report.json`` / ``REPORT_GATE_SUMMARY.md`` 带进每个
   工作树，不识别共享件就会把别人的结论记到本 mission 头上。
2. **沉淀遍**：逐 mission 抽取（归属门 + 章节模板 + 质量门）→ 入库/拒绝 → 汇总。

判据映射（本模块产出、测试/CLI 断言）
------------------------------------
(a) 召回：``recall_against_reference`` 对独立标注集算 recall 与虚构槽位；
(b) schema 完整率：``store.stats()['schema_complete_ratio']``；
(c) 幂等：同一批 mission 跑两遍 → 第二遍 ``inserted == 0``；
(e) 质量门：无证据构造件 → ``status == rejected`` 且 rejected 日志增长；
(f) 检索时延：``store.search`` 自报 ``took_ms``；
(h) 零领域语义：``tools/boundary/pr_gate_scan.py paths`` 判红数为 0（不在本模块判）。

零领域语义：目录名只按 ``m<数字>`` 识别（平台任务号形状），不认任何学科名。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import CONTRACT_FIELDS
from .extractor import (
    ACCEPTANCE_GLOBS,
    REASON_INCOMPLETE,
    REASON_NO_ATTRIBUTION,
    REASON_NO_EVIDENCE,
    REASON_NO_SOURCE,
    REPORT_FILES,
    extract_from_dir,
    load_domain_map,
)
from .store import KnowledgeStore

DEFAULT_WORK_ROOT = "/root/tp-agent/work"
_MISSION_DIR_RX = re.compile(r"^m\d+$")

#: 拒绝原因码全集（``reason_ok`` 用它做机器校验）。
KNOWN_REASONS: tuple[str, ...] = (REASON_INCOMPLETE, REASON_NO_EVIDENCE, REASON_NO_SOURCE, REASON_NO_ATTRIBUTION)


def discover_missions(root: str | Path, limit: int | None = None, pattern: str | None = None) -> list[Path]:
    """列出根目录下带报告工件的 mission 目录（按目录名排序，稳定可复现）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    rx = re.compile(pattern) if pattern else _MISSION_DIR_RX
    found: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not rx.match(child.name):
            continue
        has_report = any((child / name).is_file() for name in REPORT_FILES)
        has_acc = any(p.is_file() for g in ACCEPTANCE_GLOBS for p in child.glob(g))
        if has_report or has_acc:
            found.append(child)
            if limit and len(found) >= limit:
                break
    return found


def index_artifacts(missions: Sequence[Path]) -> dict[str, Any]:
    """索引遍：每份工件的 sha256 及其出现的 mission 目录数。"""
    owners: dict[str, list[str]] = {}
    names = set(REPORT_FILES) | {"acceptance.json"}
    for mission in missions:
        for path in sorted(mission.iterdir()):
            if not path.is_file():
                continue
            if path.name not in names and not (path.name.startswith("acceptance") and path.suffix == ".json"):
                continue
            try:
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            owners.setdefault(sha, []).append(path.parent.name)
    shared = {sha for sha, ds in owners.items() if len(set(ds)) > 1}
    return {
        "files_indexed": sum(len(v) for v in owners.values()),
        "distinct_contents": len(owners),
        "shared_contents": len(shared),
        "shared_shas": shared,
        "shared_detail": {
            sha: {"missions": len(set(ds)), "example": sorted(set(ds))[:3]} for sha, ds in owners.items() if sha in shared
        },
    }


def sediment_mission(
    mission_dir: str | Path,
    store: KnowledgeStore,
    domain_map: Mapping[str, str] | None = None,
    dry_run: bool = False,
    shared_shas: Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    """单个 mission：抽取 → 质量门 → 入库，返回一条可汇总的结果行。"""
    extraction = extract_from_dir(mission_dir, domain_map=domain_map, shared_shas=shared_shas)
    row: dict[str, Any] = {
        "mission_id": extraction.mission_id,
        "mission_dir": extraction.mission_dir,
        "title": extraction.title,
        "domain": extraction.domain,
        "attributed": extraction.attributed,
        "filled": list(extraction.filled),
        "missing": list(extraction.missing),
        "fill_ratio": round(extraction.fill_ratio, 4),
        "sha256": extraction.sha256,
        "sources": [s.get("path") for s in extraction.sources],
        "unattributed_sources": [s.get("path") for s in extraction.sources if str(s.get("role", "")).endswith(("unattributed", "shared"))],
        "sections": {k: v for k, v in extraction.sections.items() if v},
        "status": "dry_run",
        "reason": extraction.reason,
        "entry_id": None,
    }
    if dry_run:
        row["status"] = "would_insert" if extraction.accepted else "would_reject"
        return row
    if not extraction.accepted:
        # 抽取阶段已判死：按抽取器给的原因码落拒绝账（不伪造空条目去撞 schema 门）
        store.reject(extraction.to_reject())
        row["status"] = "rejected"
        row["reason"] = extraction.reason
        return row
    result = store.add(extraction.to_entry())
    row["status"] = result.status
    row["entry_id"] = result.entry_id
    row["reason"] = result.reason or extraction.reason
    return row


def scan_root(
    root: str | Path | None = None,
    store: KnowledgeStore | None = None,
    limit: int | None = None,
    domain_map: Mapping[str, str] | None = None,
    dry_run: bool = False,
    pattern: str | None = None,
) -> dict[str, Any]:
    """扫根目录并沉淀。返回汇总（含逐 mission 明细，供报告与回归使用）。"""
    base = root or DEFAULT_WORK_ROOT
    domain_map = domain_map if domain_map is not None else load_domain_map()
    store = store or KnowledgeStore()
    missions = discover_missions(base, limit=limit, pattern=pattern)
    index = index_artifacts(missions)

    rows: list[dict[str, Any]] = []
    tally = {"inserted": 0, "duplicate": 0, "linked": 0, "rejected": 0, "would_insert": 0, "would_reject": 0}
    for mission_dir in missions:
        row = sediment_mission(mission_dir, store, domain_map=domain_map, dry_run=dry_run, shared_shas=index["shared_shas"])
        tally[row["status"]] = tally.get(row["status"], 0) + 1
        rows.append(row)

    attributable = [r for r in rows if r["attributed"]]
    filled_total = sum(len(r["filled"]) for r in attributable)
    slots = len(attributable) * len(CONTRACT_FIELDS)
    return {
        "root": str(base),
        "dry_run": dry_run,
        "missions_scanned": len(rows),
        "missions_attributable": len(attributable),
        "contamination": {
            "files_indexed": index["files_indexed"],
            "distinct_contents": index["distinct_contents"],
            "shared_contents": index["shared_contents"],
            "shared_mission_dirs": sum(d["missions"] for d in index["shared_detail"].values()),
        },
        "tally": tally,
        "mean_fill_ratio": round(filled_total / slots, 4) if slots else 0.0,
        "missions_all_four": sum(1 for r in attributable if len(r["filled"]) == len(CONTRACT_FIELDS)),
        "store": store.stats() if not dry_run else None,
        "rows": rows,
    }


def recall_against_reference(rows: Sequence[Mapping[str, Any]], labels: Mapping[str, Any]) -> dict[str, Any]:
    """对独立标注集算召回率与虚构率。

    ``labels`` = ``{mission_id: {"present": [field, ...], "source": str, "sha256": str, "note": str}}``
    —— 标注由人读源报告章节/正文得出（不来自抽取器输出，避免自证）。

    recall     = 源里存在的契约中，被抽出来的比例；
    fabricated = 源里不存在、抽取器却填了的槽位数（溯源正确性，应为 0）。
    """
    hit = 0
    expected = 0
    fabricated = 0
    per_mission: list[dict[str, Any]] = []
    by_mission = {str(r["mission_id"]): r for r in rows}
    for mid, label in labels.items():
        present = [f for f in label.get("present", []) if f in CONTRACT_FIELDS]
        row = by_mission.get(str(mid))
        got = set(row["filled"]) if row else set()
        miss = [f for f in present if f not in got]
        extra = [f for f in CONTRACT_FIELDS if f not in present and f in got]
        expected += len(present)
        hit += len(present) - len(miss)
        fabricated += len(extra)
        per_mission.append(
            {
                "mission_id": str(mid),
                "expected": present,
                "extracted": sorted(got),
                "missed": miss,
                "fabricated": extra,
                "recall": round((len(present) - len(miss)) / len(present), 4) if present else None,
                "status": row.get("status") if row else "not_scanned",
            }
        )
    return {
        "missions": len(labels),
        "expected_slots": expected,
        "hit_slots": hit,
        "recall": round(hit / expected, 4) if expected else 0.0,
        "fabricated_slots": fabricated,
        "per_mission": per_mission,
    }


def load_labels(path: str | Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("missions"), list):
        return {str(m["mission_id"]): m for m in doc["missions"]}
    if isinstance(doc, dict):
        return {str(k): v for k, v in doc.items() if isinstance(v, dict)}
    raise ValueError(f"标注文件形状不认识: {path}")


def write_report(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """把汇总落盘（证据工件，供报告引用与回归比对）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out


def reason_ok(reason: str | None) -> bool:
    return reason is None or reason in KNOWN_REASONS


def summarize_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status"))
        tally[key] = tally.get(key, 0) + 1
    return tally