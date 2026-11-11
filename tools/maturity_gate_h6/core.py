"""H6 成熟度门 · 核心（能力增量账本 + 五级成熟度 + 交叉一致性）。

平台共性机制层：**零领域语义**。能力名、域名、判据阈值全部来自 JSON/Markdown
工件注入；本模块只提供「五级阶梯 → 多源交叉复核 → 增量账本 → 门禁裁定」的通用机器。

五级成熟度阶梯（唯一权威定义，其余模块只许引用）
----------------------------------------------------
==========  ==  ==========================================================
等级       秩  判据（可机器判的部分）
==========  ==  ==========================================================
NOT_SUPPORTED 0  端点/能力不存在（无实现、无可调用面）
STUB          1  仅接口存在，无算法内核（回显/空实现）
MOCK          2  有实现但与独立基准背离（超出容差）或非物理（合成响应）
WITHHELD      3  有实现但证据不足：无独立基准 / 基准不可信 / 引文失效
REAL          4  有独立基准，误差在容差内，且结论可复现（逐字可查引文）
==========  ==  ==========================================================

设计约束（与平台三条铁律对齐）
--------------------------------
1. **零领域语义**：本文件不含任何域词条；域信息只作为不透明字符串透传。
2. **宁拒不猜**：等级不在阶梯内、工件缺键、引文对不上 → 显式错误或显式 finding，
   绝不静默降级成「通过」。缺失的源记入 ``sources`` 并标注 ``available=False``。
3. **数字零手抄**：报告里的每个数字都由 ``run_gate`` 的返回值程序化注入；
   ``repro_cmd`` 一次命令可逐字节复算（``--verify`` 会重算并比对 sha256）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

#: 五级成熟度阶梯（有序；秩越大越成熟）。**唯一权威定义**。
LEVELS: Tuple[str, ...] = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK: Dict[str, int] = {name: i for i, name in enumerate(LEVELS)}
LEVEL_DEFINITIONS: Dict[str, str] = {
    "NOT_SUPPORTED": "能力/端点不存在",
    "STUB": "仅接口存在，无算法内核",
    "MOCK": "有实现但与独立基准背离（超容差）或非物理",
    "WITHHELD": "有实现但证据不足（无基准/基准不可信/引文失效）",
    "REAL": "独立基准 + 误差达容差 + 可复现（逐字可查引文）",
}

#: 分歧分类词表（与既有台账对齐；纯字符串常量，无语义硬编码）
DIVERGENCE_KINDS: Tuple[str, ...] = ("OK", "OVERCLAIM", "REVERIFY", "UNVERIFIED")


class GateError(Exception):
    """工件结构错误（配置写错 ≠ 判据不过）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def file_sha256(path: "Path | str") -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line_sha256(line: str, ndigits: int = 16) -> str:
    """行级指纹（与既有台账 ``line_sha256`` 口径一致：sha256 前 16 位）。"""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:ndigits]


def _load_json(path: "Path | str") -> Any:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - 由调用方降级
        raise GateError(f"工件缺失: {p}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"工件不是合法 JSON: {p} ({exc})") from exc


def _require_level(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in LEVEL_RANK:
        raise GateError(f"{where}: 非法等级 {value!r}（合法={list(LEVELS)}）")
    return value


def rank(level: str) -> int:
    return LEVEL_RANK[_require_level(level, "rank()")]


# ---------------------------------------------------------------------------
# 源装载（每个源都带 sha256 钉桩与 as_of；不可用时显式记 available=False）
# ---------------------------------------------------------------------------
@dataclass
class Source:
    source_id: str
    path: str
    available: bool
    levels: Dict[str, str] = field(default_factory=dict)
    evidence: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    divergence: Dict[str, str] = field(default_factory=dict)
    as_of: Optional[str] = None
    sha256: Optional[str] = None
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "available": self.available,
            "as_of": self.as_of,
            "sha256": self.sha256,
            "capability_count": len(self.levels),
            "note": self.note,
        }


def load_flat_snapshot(source_id: str, path: "Path | str") -> Source:
    """自述等级快照：``{"capabilities": {id: LEVEL}}``。"""
    try:
        data = _load_json(path)
    except GateError as exc:
        return Source(source_id, str(path), False, note=str(exc))
    caps = data.get("capabilities")
    if not isinstance(caps, dict) or not caps:
        probe_err = data.get("probe_error")
        if probe_err:
            return Source(source_id, str(path), False, note=str(probe_err))
        raise GateError(f"{path}: capabilities 必须是非空对象")
    levels = {str(k): _require_level(v, f"{path}:{k}") for k, v in caps.items()}
    meta = data.get("source") if isinstance(data.get("source"), dict) else {}
    return Source(
        source_id=source_id,
        path=str(path),
        available=True,
        levels=levels,
        as_of=meta.get("observed_at") or data.get("observed_at"),
        sha256=file_sha256(path),
    )


def load_ledger(source_id: str, path: "Path | str") -> Source:
    """证据台账：``{"capabilities": [{id, claimed_level, reconciled_level, ...}]}``。"""
    try:
        data = _load_json(path)
    except GateError as exc:
        return Source(source_id, str(path), False, note=str(exc))
    items = data.get("capabilities")
    if not isinstance(items, list) or not items:
        raise GateError(f"{path}: capabilities 必须是非空数组")
    levels: Dict[str, str] = {}
    evidence: Dict[str, List[Dict[str, Any]]] = {}
    divergence: Dict[str, str] = {}
    stamps: List[str] = []
    for item in items:
        cid = str(item.get("id"))
        if not cid or cid == "None":
            raise GateError(f"{path}: 能力条目缺 id")
        if cid in levels:
            raise GateError(f"{path}: 能力 id 重复 {cid}")
        levels[cid] = _require_level(item.get("reconciled_level"), f"{path}:{cid}")
        claimed = item.get("claimed_level")
        evidence[cid] = list(item.get("evidence_refs") or [])
        divergence[cid] = str(item.get("divergence", ""))
        # 自述等级也钉住：增量账本的基线必须可复算
        if claimed is not None:
            _require_level(claimed, f"{path}:{cid}.claimed_level")
        if item.get("latest_evidence_as_of"):
            stamps.append(str(item["latest_evidence_as_of"]))
    return Source(
        source_id=source_id,
        path=str(path),
        available=True,
        levels=levels,
        evidence=evidence,
        divergence=divergence,
        as_of=max(stamps) if stamps else None,
        sha256=file_sha256(path),
    )


_MD_COUNT_HEAD = re.compile(r"能力数：(\d+)（有证据 (\d+) / 无证据 (\d+)）")
_MD_COUNT_DIV = re.compile(r"分歧：(.+)")
_MD_COUNT_MISC = re.compile(r"未注册域缺口：(\d+)；证据行：(\d+)；违规：(\d+)")
_MD_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|([^|]*)\|\s*([^|]+?)\s*\|\s*\**([A-Z_]+)\**\s*\|\s*([A-Z]+)\s*\|")


def parse_markdown_claims(text: str) -> Dict[str, Any]:
    """从人类可读对账表里抽出**已发布数字**（作为 ref 侧的独立参照）。"""
    claims: Dict[str, Any] = {"counts": {}, "divergence": {}, "rows": {}, "parsed": []}
    for line in text.splitlines():
        m = _MD_COUNT_HEAD.search(line)
        if m:
            claims["counts"].update(
                capabilities=int(m.group(1)), with_evidence=int(m.group(2)),
                without_evidence=int(m.group(3)))
            claims["parsed"].append("counts_head")
            continue
        m = _MD_COUNT_DIV.search(line)
        if m:
            for part in m.group(1).split("·"):
                bits = part.strip().split()
                if len(bits) == 2 and bits[0] in DIVERGENCE_KINDS and bits[1].isdigit():
                    claims["divergence"][bits[0]] = int(bits[1])
            claims["parsed"].append("counts_divergence")
            continue
        m = _MD_COUNT_MISC.search(line)
        if m:
            claims["counts"].update(
                unregistered_domains=int(m.group(1)), evidence_rows=int(m.group(2)),
                violations=int(m.group(3)))
            claims["parsed"].append("counts_misc")
            continue
        m = _MD_ROW.match(line.strip())
        if m:
            claims["rows"][m.group(1)] = {
                "claimed": _require_level(m.group(3), "markdown.claimed"),
                "reconciled": _require_level(m.group(4), "markdown.reconciled"),
                "divergence": m.group(5),
            }
    return claims


# ---------------------------------------------------------------------------
# 交叉一致性三轴
# ---------------------------------------------------------------------------
def label_divergence(claimed: str, reconciled: str, evidence_count: int) -> str:
    """预声明的分歧分类规则（纯秩比较 + 有无证据），产出既有词表内的标签。

    规则来源：台账自带 ``reconciliation_rules``——「无证据：reconciled=WITHHELD
    （未验证默认），claim 强于 WITHHELD 记 UNVERIFIED」。故无证据时，主张不高于
    WITHHELD 视为 OK（本来就该按未验证默认记账），高于 WITHHELD 才是 UNVERIFIED。
    """
    if rank(claimed) == rank(reconciled):
        return "OK"
    if evidence_count > 0:
        return "OVERCLAIM" if rank(claimed) > rank(reconciled) else "REVERIFY"
    return "UNVERIFIED" if rank(claimed) > rank("WITHHELD") else "OK"


def cross_source(claimed: Source, reconciled: Source) -> Dict[str, Any]:
    """轴 A：自述 ↔ 对账（逐能力），外加「我方规则 vs 台账存储标签」的复算一致性。"""
    if not claimed.available or not reconciled.available:
        return {"available": False, "why": "源缺失", "entries": [], "recomputed_mismatch": []}
    only_claimed = sorted(set(claimed.levels) - set(reconciled.levels))
    only_reconciled = sorted(set(reconciled.levels) - set(claimed.levels))
    entries: List[Dict[str, Any]] = []
    mismatch: List[Dict[str, Any]] = []
    for cid in sorted(set(claimed.levels) & set(reconciled.levels)):
        c_lvl = claimed.levels[cid]
        r_lvl = reconciled.levels[cid]
        ev_count = len(reconciled.evidence.get(cid, []))
        mine = label_divergence(c_lvl, r_lvl, ev_count)
        stored = reconciled.divergence.get(cid, "")
        entries.append({
            "capability_id": cid, "claimed_level": c_lvl, "reconciled_level": r_lvl,
            "rank_delta": rank(r_lvl) - rank(c_lvl), "evidence_count": ev_count,
            "divergence_recomputed": mine, "divergence_stored": stored,
        })
        if stored and stored != mine:
            mismatch.append({"capability_id": cid, "stored": stored, "recomputed": mine,
                             "claimed": c_lvl, "reconciled": r_lvl, "evidence_count": ev_count})
    counts: Dict[str, int] = {k: 0 for k in DIVERGENCE_KINDS}
    for e in entries:
        counts[e["divergence_recomputed"]] += 1
    unexplained = [e["capability_id"] for e in entries
                   if e["divergence_recomputed"] not in DIVERGENCE_KINDS]
    return {
        "available": True,
        "entries": entries,
        "counts": counts,
        "coverage": (len(entries) / max(1, len(set(claimed.levels) | set(reconciled.levels)))),
        "only_claimed": only_claimed,
        "only_reconciled": only_reconciled,
        "recomputed_mismatch": mismatch,
        "unexplained": unexplained,
        "agreement": sum(1 for e in entries if e["divergence_recomputed"] == "OK"),
        "total": len(entries),
    }


def _resolve_text(report_id: Any, corpus: Mapping[str, Any], work_root: Path) -> Optional[str]:
    """把证据引用的来源解析成正文文本（在线报告 / 语料库文档 / 工作树文件）。"""
    rid = str(report_id)
    reports = corpus.get("reports") if isinstance(corpus.get("reports"), dict) else {}
    if rid in reports:
        item = reports[rid]
        if isinstance(item, dict) and isinstance(item.get("markdown"), str):
            return item["markdown"]
    if rid.startswith("doc:"):
        rel = rid[4:]
        for base in (work_root, Path.cwd()):
            p = base / rel
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
    docs = corpus.get("docs")
    if isinstance(docs, dict):
        entry = docs.get(rid) or docs.get(rid.replace("doc:", ""))
        if isinstance(entry, dict) and isinstance(entry.get("markdown"), str):
            return entry["markdown"]
        if isinstance(entry, str):
            return entry
    return None


def _line_containing(text: str, quote: str) -> Optional[str]:
    if not quote:
        return None
    for raw in text.splitlines():
        if quote in raw:
            return raw.strip()
    return None


def quote_binding(ledger: Source, corpus: Mapping[str, Any], work_root: Path) -> Dict[str, Any]:
    """轴 B：证据引文的逐字绑定（记录值 vs 重算值）。

    台账里每条证据都钉了 ``line`` 与 ``line_sha256``；此处从**原始来源**重算，
    对不上就是引文漂移（文档被改 / 手抄 / 转述），必须显式成行。
    """
    total = matched = missing_source = drift = 0
    findings: List[Dict[str, Any]] = []
    if not ledger.available:
        return {"available": False, "why": "台账源缺失"}
    for cid, refs in ledger.evidence.items():
        for ref in refs:
            total += 1
            stored = str(ref.get("line_sha256") or "")
            quote = str(ref.get("line") or "")
            text = _resolve_text(ref.get("report_id"), corpus, work_root)
            if text is None:
                missing_source += 1
                findings.append({"capability_id": cid, "kind": "source_unresolved",
                                 "report_id": str(ref.get("report_id"))})
                continue
            line = _line_containing(text, quote)
            if line is None:
                drift += 1
                findings.append({"capability_id": cid, "kind": "quote_absent",
                                 "report_id": str(ref.get("report_id"))})
                continue
            got = line_sha256(line)
            if stored and got != stored:
                drift += 1
                findings.append({"capability_id": cid, "kind": "line_sha256_drift",
                                 "report_id": str(ref.get("report_id")),
                                 "stored": stored, "got": got})
            else:
                matched += 1
    return {
        "available": True, "total": total, "matched": matched,
        "drift": drift, "source_unresolved": missing_source,
        "match_rate": (matched / total) if total else None,
        "findings": findings,
    }


def recount_markdown(claims: Mapping[str, Any], ledger: Source) -> Dict[str, Any]:
    """轴 C：已发布数字 ↔ 从台账重算（人读表 vs JSON，防止手改/生成漂移）。"""
    if not ledger.available:
        return {"available": False, "why": "台账源缺失", "metrics": []}
    levels = ledger.levels
    ev = ledger.evidence
    with_ev = sum(1 for cid in levels if ev.get(cid))
    recomputed_counts = {
        "capabilities": len(levels),
        "with_evidence": with_ev,
        "without_evidence": len(levels) - with_ev,
    }
    div: Dict[str, int] = {}
    for cid, stored in ledger.divergence.items():
        if stored:
            div[stored] = div.get(stored, 0) + 1
    metrics: List[Dict[str, Any]] = []
    ref_counts = claims.get("counts", {})
    for key, got in recomputed_counts.items():
        if key in ref_counts:
            ref = int(ref_counts[key])
            metrics.append({"axis": "counts", "metric": key, "ref": ref, "got": got,
                            "delta": got - ref, "ok": got == ref})
    ref_div = claims.get("divergence", {})
    for kind in DIVERGENCE_KINDS:
        if kind in ref_div:
            got = div.get(kind, 0)
            ref = int(ref_div[kind])
            metrics.append({"axis": "divergence", "metric": kind, "ref": ref, "got": got,
                            "delta": got - ref, "ok": got == ref})
    row_bad: List[Dict[str, Any]] = []
    for cid, row in (claims.get("rows") or {}).items():
        if cid not in ledger.levels:
            row_bad.append({"capability_id": cid, "kind": "row_not_in_ledger"})
            continue
        if row["reconciled"] != ledger.levels[cid]:
            row_bad.append({"capability_id": cid, "kind": "level_drift",
                            "markdown": row["reconciled"], "ledger": ledger.levels[cid]})
        stored = ledger.divergence.get(cid, "")
        if stored and row["divergence"] != stored:
            row_bad.append({"capability_id": cid, "kind": "divergence_drift",
                            "markdown": row["divergence"], "ledger": stored})
    return {"available": True, "metrics": metrics, "row_findings": row_bad,
            "row_count": len(claims.get("rows") or {}),
            "parsed": list(claims.get("parsed") or [])}


def evidence_support(ledger: Source) -> Dict[str, Any]:
    """轴 D：等级 ↔ 证据自洽（非 WITHHELD 必须有同向证据；无证据必须是 WITHHELD）。"""
    if not ledger.available:
        return {"available": False, "why": "台账源缺失"}
    supported = orphan = no_evidence_not_withheld = 0
    findings: List[Dict[str, Any]] = []
    for cid, lvl in sorted(ledger.levels.items()):
        refs = ledger.evidence.get(cid, [])
        if not refs:
            if lvl != "WITHHELD":
                no_evidence_not_withheld += 1
                findings.append({"capability_id": cid, "level": lvl, "kind": "no_evidence"})
            continue
        asserted = [str(r.get("asserted_level")) for r in refs]
        if lvl in asserted:
            supported += 1
        else:
            orphan += 1
            findings.append({"capability_id": cid, "level": lvl, "kind": "level_unsupported",
                             "asserted_levels": sorted(set(asserted))})
    return {"available": True, "supported": supported, "orphan": orphan,
            "no_evidence_not_withheld": no_evidence_not_withheld, "findings": findings}


# ---------------------------------------------------------------------------
# 增量账本（基线 → 当前，事件哈希链）
# ---------------------------------------------------------------------------
def build_increment_ledger(baseline: Source, current: Source) -> Dict[str, Any]:
    """能力增量账本：逐能力 direction/delta + 前驱哈希链（可证明历史未被改写）。"""
    if not baseline.available or not current.available:
        return {"available": False, "why": "基线或当前源缺失", "entries": [], "chain_head": None}
    ids = sorted(set(baseline.levels) | set(current.levels))
    entries: List[Dict[str, Any]] = []
    prev = "GENESIS"
    for seq, cid in enumerate(ids):
        b = baseline.levels.get(cid)
        c = current.levels.get(cid)
        if b is None:
            direction, delta = "NEW", None
        elif c is None:
            direction, delta = "DROPPED", None
        else:
            delta = rank(c) - rank(b)
            direction = "HOLD" if delta == 0 else ("PROMOTE" if delta > 0 else "DEMOTE")
        refs = [{"report_id": str(r.get("report_id")), "asserted_level": r.get("asserted_level"),
                 "line_sha256": r.get("line_sha256")}
                for r in current.evidence.get(cid, [])]
        payload = {
            "seq": seq, "capability_id": cid, "baseline_level": b, "current_level": c,
            "direction": direction, "rank_delta": delta,
            "evidence_refs": refs, "evidence_count": len(refs), "prev_sha256": prev,
        }
        payload["sha256"] = text_sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        prev = payload["sha256"]
        entries.append(payload)
    counts: Dict[str, int] = {"PROMOTE": 0, "DEMOTE": 0, "HOLD": 0, "NEW": 0, "DROPPED": 0}
    for e in entries:
        counts[e["direction"]] += 1
    return {
        "available": True,
        "baseline": baseline.as_dict(), "current": current.as_dict(),
        "entries": entries, "counts": counts, "chain_head": prev,
        "chain_len": len(entries),
    }


def maturity_profile(levels: Mapping[str, str]) -> Dict[str, Any]:
    """五级成熟度画像：直方图 + 平均秩（归一化成熟度得分）。"""
    hist = {k: 0 for k in LEVELS}
    for lvl in levels.values():
        hist[_require_level(lvl, "maturity_profile")] += 1
    n = sum(hist.values())
    mean_rank = (sum(LEVEL_RANK[k] * v for k, v in hist.items()) / n) if n else 0.0
    top = len(LEVELS) - 1
    return {"histogram": hist, "n": n, "mean_rank": round(mean_rank, 6),
            "maturity_score": round(mean_rank / top, 6) if n else 0.0,
            "max_rank": top}


# ---------------------------------------------------------------------------
# 门禁裁定（阈值在运行前声明；判据即代码，可复算）
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "coverage_min": 1.0,            # 基线↔当前能力集合覆盖率下限
    "unexplained_divergence_max": 0,  # 无法归类的分歧上限
    "recompute_mismatch_max": 0,    # 我方规则 vs 台账标签 复算不一致上限
    "quote_drift_max": 0,           # 引文漂移（逐字可查）上限
    "quote_source_unresolved_max": 0,
    "markdown_delta_max": 0,        # 人读表数字 vs 重算 偏差上限
    "orphan_evidence_max": 0,       # 等级无同向证据上限
    "ladder_sound": True,           # 阶梯严格单调 + 观测等级全在阶梯内
}


def _criterion(cid: str, statement: str, value: Any, threshold: Any,
               comparator: str, detail: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    if comparator == "le":
        ok = value is not None and value <= threshold
    elif comparator == "ge":
        ok = value is not None and value >= threshold
    elif comparator == "eq":
        ok = value == threshold
    elif comparator == "true":
        ok = bool(value) is True
    else:  # pragma: no cover
        raise GateError(f"未知比较器 {comparator}")
    return {"criterion_id": cid, "statement": statement, "value": value,
            "threshold": threshold, "comparator": comparator, "pass": bool(ok),
            "detail": dict(detail or {})}


def run_gate(*, claimed: Source, reconciled: Source, markdown_claims: Mapping[str, Any],
             corpus: Mapping[str, Any], work_root: Path,
             thresholds: Mapping[str, Any] | None = None,
             extra_sources: Sequence[Source] = ()) -> Dict[str, Any]:
    """跑完整门：四轴交叉一致性 → 增量账本 → 五级画像 → 判据裁定。"""
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        unknown = set(thresholds) - set(th)
        if unknown:
            raise GateError(f"未知阈值键: {sorted(unknown)}")
        th.update(thresholds)
    th_sha = text_sha256(json.dumps(th, sort_keys=True))

    xs = cross_source(claimed, reconciled)
    qb = quote_binding(reconciled, corpus, work_root)
    rc = recount_markdown(markdown_claims, reconciled)
    es = evidence_support(reconciled)
    inc = build_increment_ledger(claimed, reconciled)
    prof_before = maturity_profile(claimed.levels) if claimed.available else None
    prof_after = maturity_profile(reconciled.levels) if reconciled.available else None

    observed = set(reconciled.levels.values()) | set(claimed.levels.values())
    ladder_sound = bool(observed) and (
        all(lvl in LEVEL_RANK for lvl in observed)
        and all(LEVEL_RANK[a] < LEVEL_RANK[b] for a, b in zip(LEVELS, LEVELS[1:]))
    )
    md_violations = [m for m in rc.get("metrics", []) if abs(m["delta"]) > th["markdown_delta_max"]]

    criteria = [
        _criterion("C1", "基线↔当前能力集合全覆盖（无孤儿能力）",
                   round(xs.get("coverage", 0.0), 6) if xs.get("available") else None,
                   th["coverage_min"], "ge",
                   {"only_claimed": xs.get("only_claimed"), "only_reconciled": xs.get("only_reconciled")}),
        _criterion("C2", "五级阶梯严格单调且全部观测等级落在阶梯内",
                   ladder_sound, th["ladder_sound"], "true",
                   {"observed_levels": sorted(observed)}),
        _criterion("C3", "分歧全部可归类（无未解释分歧）",
                   len(xs.get("unexplained", [])) if xs.get("available") else None,
                   th["unexplained_divergence_max"], "le",
                   {"counts": xs.get("counts")}),
        _criterion("C4", "分歧标签可复算（规则 vs 台账存储标签一致）",
                   len(xs.get("recomputed_mismatch", [])) if xs.get("available") else None,
                   th["recompute_mismatch_max"], "le",
                   {"examples": xs.get("recomputed_mismatch", [])[:5]}),
        _criterion("C5", "证据引文逐字可查（记录 sha256 == 从原始来源重算）",
                   qb.get("drift"), th["quote_drift_max"], "le",
                   {"match_rate": qb.get("match_rate"), "total": qb.get("total"),
                    "matched": qb.get("matched")}),
        _criterion("C6", "证据来源可解析（无悬空引文）",
                   qb.get("source_unresolved"), th["quote_source_unresolved_max"], "le"),
        _criterion("C7", "人读对账表数字 == 台账重算数字",
                   (max(abs(m["delta"]) for m in rc["metrics"]) if rc.get("metrics") else None),
                   th["markdown_delta_max"], "le",
                   {"metrics": rc.get("metrics", []), "row_findings": rc.get("row_findings", [])}),
        _criterion("C8", "等级与证据同向（非 WITHHELD 必有同向证据）",
                   es.get("orphan"), th["orphan_evidence_max"], "le",
                   {"findings": es.get("findings", []),
                    "no_evidence_not_withheld": es.get("no_evidence_not_withheld")}),
    ]
    passed = [c for c in criteria if c["pass"]]
    verdict = "PASS" if len(passed) == len(criteria) else "FAIL"

    return {
        "schema_version": SCHEMA_VERSION,
        "gate_id": "H6-MATURITY-GATE",
        "verdict": verdict,
        "criteria_pass_rate": round(len(passed) / len(criteria), 6),
        "thresholds": th,
        "thresholds_sha256": th_sha,
        "sources": [s.as_dict() for s in (*extra_sources, claimed, reconciled)],
        "axes": {"cross_source": {k: v for k, v in xs.items() if k != "entries"},
                 "quote_binding": {k: v for k, v in qb.items() if k != "findings"},
                 "markdown_recount": rc, "evidence_support": {k: v for k, v in es.items() if k != "findings"}},
        "findings": {"quote_binding": qb.get("findings", []),
                     "evidence_support": es.get("findings", []),
                     "markdown_rows": rc.get("row_findings", []),
                     "divergence_mismatch": xs.get("recomputed_mismatch", [])},
        "maturity": {"before": prof_before, "after": prof_after,
                     "score_delta": (round((prof_after["maturity_score"] - prof_before["maturity_score"]), 6)
                                     if prof_before and prof_after else None)},
        "increments": inc,
        "criteria": criteria,
    }


def verify_artifact(gate: Mapping[str, Any], *, claimed: Source, reconciled: Source,
                    markdown_claims: Mapping[str, Any], corpus: Mapping[str, Any],
                    work_root: Path) -> List[str]:
    """重算并与已落盘工件比对（防手改；返回问题列表，空=一致）。"""
    problems: List[str] = []
    fresh = run_gate(claimed=claimed, reconciled=reconciled,
                     markdown_claims=markdown_claims, corpus=corpus,
                     work_root=work_root, thresholds=gate.get("thresholds"))
    for key in ("verdict", "criteria_pass_rate", "thresholds_sha256", "criteria"):
        if json.dumps(fresh[key], sort_keys=True) != json.dumps(gate.get(key), sort_keys=True):
            problems.append(f"重算不符: {key}")
    if fresh["increments"].get("chain_head") != gate.get("increments", {}).get("chain_head"):
        problems.append("重算不符: 增量账本链头")
    return problems