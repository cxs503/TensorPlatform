"""CI 漂移门 v2：**来源绑定感知**的阈值漂移裁决（零领域语义）。

与 v1（``drift_gate.py``，整件哈希 + 数值叶子）的区别：

* 漂移的合法理由只有一个——``source_ref``（出处）变了。阈值被改而出处没变 = 红灯；
  出处与阈值一起改 = 可追溯修订（绿灯 + 审计记录）；只改注释/排版 = 必须安静。
* 因此把条目投影成三组字段：语义（阈值身份 + 出处绑定 + 依赖链）、装饰（note/comment）、
  描述（title/statement）。裁决只看语义组，装饰组永远不得造成红灯。

门本身不产生"自报成绩"：检出率与假阳性由 :func:`run_tamper_campaign` /
:func:`run_cosmetic_campaign` 程序化注入计量。
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from . import schema_v2

PIN_SCHEMA = "criteria-governance/v2-pins/1"


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 投影 / 钉桩
# --------------------------------------------------------------------------- #
def entry_view(entry: Dict) -> Dict[str, object]:
    """条目 → {semantic, cosmetic, descriptive} 三组投影。"""
    return {
        "semantic": schema_v2.semantic_projection(entry),
        "cosmetic": schema_v2.cosmetic_projection(entry),
        "descriptive": {k: entry.get(k) for k in schema_v2.DESCRIPTIVE_KEYS if k in entry},
    }


def artifact_view(entries: Iterable[Dict]) -> Dict[str, object]:
    view: Dict[str, object] = {}
    for entry in entries:
        cid = entry.get("criterion_id")
        if cid is None:
            continue
        view[str(cid)] = entry_view(entry)
    return {"schema": "criteria-governance/v2-view/1", "criteria": view}


def pin_view(view: Dict[str, object]) -> Dict[str, object]:
    """生成钉桩：每条判据的语义哈希 + 出处绑定哈希（CI 只比这两个数）。"""
    criteria: Dict[str, object] = {}
    for cid, groups in sorted((view.get("criteria") or {}).items()):
        semantic = groups.get("semantic") or {}
        criteria[cid] = {
            "semantic_sha256": sha256_of(semantic),
            "source_sha256": sha256_of(semantic.get("source_ref") or {}),
        }
    return {"schema": PIN_SCHEMA, "criteria": criteria}


# --------------------------------------------------------------------------- #
# 裁决
# --------------------------------------------------------------------------- #
@dataclass
class DriftFinding:
    criterion_id: str
    status: str  # RED / GREEN / INFO
    reasons: List[str] = field(default_factory=list)
    changed_keys: List[str] = field(default_factory=list)
    threshold_changed: bool = False
    source_changed: bool = False
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def red(self) -> bool:
        return self.status == "RED"

    def to_dict(self) -> Dict[str, object]:
        return {"criterion_id": self.criterion_id, "status": self.status,
                "reasons": self.reasons, "changed_keys": self.changed_keys,
                "threshold_changed": self.threshold_changed,
                "source_changed": self.source_changed, "detail": self.detail}


#: 阈值身份字段（改这些 = 改判据强度）
_THRESHOLD_KEYS = ("threshold", "comparator", "unit")
#: 出处绑定字段（改这些 = 给出新的合法理由）
_SOURCE_KEYS = ("source_ref", "source_type")
#: 派生链字段（改这些 = 改"阈值如何随依赖量变化"）
_DERIVATION_KEYS = ("depends_on", "derivation", "quantity")


def classify_change(cid: str, reference: Dict[str, object], candidate: Dict[str, object]) -> DriftFinding:
    """单条判据的漂移裁决（reference = 钉桩时的语义投影，candidate = 当前语义投影）。"""
    ref_sem = reference.get("semantic") or {}
    cand_sem = candidate.get("semantic") or {}
    changed = [key for key in sorted(set(ref_sem) | set(cand_sem))
               if ref_sem.get(key) != cand_sem.get(key)]
    threshold_changed = any(key in changed for key in _THRESHOLD_KEYS)
    source_changed = any(key in changed for key in _SOURCE_KEYS)
    derivation_changed = any(key in changed for key in _DERIVATION_KEYS)
    cosmetic_changed = (reference.get("cosmetic") or {}) != (candidate.get("cosmetic") or {})
    descriptive_changed = (reference.get("descriptive") or {}) != (candidate.get("descriptive") or {})

    reasons: List[str] = []
    status = "GREEN"
    if threshold_changed and not source_changed:
        status = "RED"
        reasons.append("threshold_changed_without_source_change")
    elif derivation_changed and not source_changed:
        status = "RED"
        reasons.append("derivation_changed_without_source_change")
    elif "source_type" in changed and not source_changed:
        status = "RED"
        reasons.append("source_type_changed_without_source_change")
    elif changed and source_changed and (threshold_changed or derivation_changed):
        reasons.append("source_tracked_revision")
    elif changed and source_changed:
        reasons.append("source_updated")
    if cosmetic_changed:
        reasons.append("cosmetic_only")
    if descriptive_changed:
        reasons.append("descriptive_only")
    if not changed and not cosmetic_changed and not descriptive_changed:
        status = "GREEN"
        reasons.append("unchanged")
    if status != "RED" and (cosmetic_changed or descriptive_changed) and changed:
        pass
    return DriftFinding(criterion_id=cid, status=status, reasons=reasons,
                        changed_keys=changed, threshold_changed=threshold_changed,
                        source_changed=source_changed,
                        detail={"cosmetic_changed": cosmetic_changed,
                                "descriptive_changed": descriptive_changed})


def check(reference_view: Dict[str, object], candidate_view: Dict[str, object]) -> List[DriftFinding]:
    """整件裁决：删除 = 红（静默缩表不可接受），新增 = 信息。"""
    ref = reference_view.get("criteria") or {}
    cand = candidate_view.get("criteria") or {}
    findings: List[DriftFinding] = []
    for cid in sorted(ref):
        if cid not in cand:
            findings.append(DriftFinding(criterion_id=cid, status="RED",
                                         reasons=["criterion_removed"]))
            continue
        findings.append(classify_change(cid, ref[cid], cand[cid]))
    for cid in sorted(set(cand) - set(ref)):
        findings.append(DriftFinding(criterion_id=cid, status="INFO",
                                     reasons=["criterion_added"]))
    return findings


def gate_verdict(findings: Sequence[DriftFinding]) -> Dict[str, object]:
    red = [f for f in findings if f.red]
    return {
        "red_total": len(red),
        "red_ids": sorted(f.criterion_id for f in red),
        "green_total": sum(1 for f in findings if f.status == "GREEN"),
        "info_total": sum(1 for f in findings if f.status == "INFO"),
        "passed": not red,
    }


# --------------------------------------------------------------------------- #
# 程序化注入：篡改检出率 / 假阳性率
# --------------------------------------------------------------------------- #
def _serialised_copy(entry: Dict) -> Dict:
    return json.loads(json.dumps(entry, ensure_ascii=False))


def run_tamper_campaign(entries: Sequence[Dict], *, n: int = 20, factor: float = 1.1,
                        seed: int = 20261109) -> Dict[str, object]:
    """注入 n 次"阈值 ±factor 而出处不变"，统计检出率（C3）。"""
    pool = [e for e in entries
            if isinstance(e.get("threshold"), (int, float)) and not isinstance(e.get("threshold"), bool)]
    reference = artifact_view(entries)
    rng = random.Random(seed)
    order = list(pool)
    rng.shuffle(order)
    injections: List[Dict[str, object]] = []
    for idx in range(min(n, len(order))):
        target = order[idx]
        cid = str(target.get("criterion_id"))
        mutated = [_serialised_copy(e) for e in entries]
        for entry in mutated:
            if str(entry.get("criterion_id")) != cid:
                continue
            direction = 1.0 if idx % 2 == 0 else -1.0
            entry["threshold"] = float(entry["threshold"]) * (1.0 + direction * (factor - 1.0))
        findings = check(reference, artifact_view(mutated))
        hit = [f for f in findings if f.criterion_id == cid]
        detected = bool(hit) and hit[0].red
        injections.append({"criterion_id": cid, "detected": detected,
                           "reasons": hit[0].reasons if hit else [],
                           "changed_keys": hit[0].changed_keys if hit else []})
    detected_total = sum(1 for item in injections if item["detected"])
    return {
        "campaign": "threshold_tamper",
        "n": len(injections),
        "factor": factor,
        "detected_total": detected_total,
        "detection_rate": (detected_total / len(injections)) if injections else 0.0,
        "injections": injections,
    }


def run_cosmetic_campaign(entries: Sequence[Dict], *, n: int = 20,
                          seed: int = 20261110) -> Dict[str, object]:
    """注入 n 次"只改注释/排版"，统计误报（C4，期望 0）。"""
    reference = artifact_view(entries)
    rng = random.Random(seed)
    order = list(entries)
    rng.shuffle(order)
    injections: List[Dict[str, object]] = []
    for idx in range(min(n, len(order))):
        target = order[idx]
        cid = str(target.get("criterion_id"))
        mutated = [_serialised_copy(e) for e in entries]
        style = "comment" if idx % 2 == 0 else "reformat"
        for entry in mutated:
            if str(entry.get("criterion_id")) != cid:
                continue
            if style == "comment":
                entry["note"] = (str(entry.get("note") or "") + f" [comment edit {idx}]").strip()
                entry["comment"] = f"reviewer remark {idx}"
            else:
                entry["note"] = entry.get("note") or "formatting variant"
        if style == "reformat":
            # 重排版：不同缩进/键序/分隔符，语义必须不变
            text = json.dumps(mutated, ensure_ascii=False, indent=2 if idx % 4 == 0 else 1,
                              sort_keys=idx % 3 == 0)
            mutated = json.loads(text)
        findings = check(reference, artifact_view(mutated))
        hit = [f for f in findings if f.criterion_id == cid]
        false_positive = bool(hit) and hit[0].red
        injections.append({"criterion_id": cid, "style": style,
                           "false_positive": false_positive,
                           "status": hit[0].status if hit else "GREEN",
                           "reasons": hit[0].reasons if hit else []})
    fp_total = sum(1 for item in injections if item["false_positive"])
    return {
        "campaign": "cosmetic_only",
        "n": len(injections),
        "false_positive_total": fp_total,
        "false_positive_rate": (fp_total / len(injections)) if injections else 0.0,
        "injections": injections,
    }


def source_tracked_revision_probe(entries: Sequence[Dict]) -> Dict[str, object]:
    """正控：阈值与出处一起改 → 必须放行（否则门就是"一刀切"，会逼人绕门）。"""
    reference = artifact_view(entries)
    pool = [e for e in entries if isinstance(e.get("threshold"), (int, float))]
    if not pool:
        return {"skipped": "no_numeric_threshold"}
    target = pool[0]
    cid = str(target.get("criterion_id"))
    mutated = [_serialised_copy(e) for e in entries]
    for entry in mutated:
        if str(entry.get("criterion_id")) != cid:
            continue
        entry["threshold"] = float(entry["threshold"]) * 1.1
        ref = dict(entry.get("source_ref") or {})
        ref["identifier"] = str(ref.get("identifier") or "") + "#revision-2026-11-09"
        entry["source_ref"] = ref
    findings = check(reference, artifact_view(mutated))
    hit = [f for f in findings if f.criterion_id == cid]
    return {"criterion_id": cid, "status": hit[0].status if hit else None,
            "reasons": hit[0].reasons if hit else [], "passed": bool(hit) and not hit[0].red}


__all__ = [
    "PIN_SCHEMA", "canonical_json", "sha256_of", "entry_view", "artifact_view", "pin_view",
    "DriftFinding", "classify_change", "check", "gate_verdict",
    "run_tamper_campaign", "run_cosmetic_campaign", "source_tracked_revision_probe",
]