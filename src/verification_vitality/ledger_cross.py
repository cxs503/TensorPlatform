"""能力台账 × 活性巡检交叉器：dead 验证面 + 台账 REAL ⇒ fail-closed。

台账（feature ledger）说的是"这条能力被证据支持到什么等级"；活性巡检说的是"支撑它的
验证面还活着吗"。两者必须交叉，否则会出现最坏的组合——**台账 REAL，而验证面早已
因符号迁移静默失效**：越是被升级过的能力，越可能因为一次重构失去支撑而没人发现。

本模块只输出**建议**，不改台账、不改注册表：
``status=REAL`` 且能力验证面 dead ⇒ ``verdict=FAIL_CLOSED`` + 一条
``action=DOWNGRADE``（REAL→WITHHELD）的建议 JSON，交给升账引擎的
rollback guard 或人工复核执行。这是有意的：fail-closed 的语义是"不许继续声称 REAL"，
执行层面必须留痕可回滚。

台账输入两种形态都吃：

* ``artifacts/capability_ledger/capability_ledger.json``（``capabilities[].id /
  claimed_level / evidence[].asserted_level``）
* 扁平 ``{"capabilities": [{"capability_id"/"id", "status"/"level"/"claimed_level"}]}``
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

LEVEL_RANK = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}
SCHEMA = "capability-vitality-cross/1.0"


def _cap_id(entry: Mapping[str, Any]) -> Optional[str]:
    return entry.get("id") or entry.get("capability_id") or entry.get("subject_id")


def _declared_level(entry: Mapping[str, Any]) -> str:
    for key in ("reconciled_level", "effective_level", "status", "level", "decided_level",
                "claimed_level"):
        val = entry.get(key)
        if isinstance(val, str) and val.upper() in LEVEL_RANK:
            return val.upper()
    return "WITHHELD"


def effective_level(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """台账条目的"当前可声称等级" = 台账裁决与反证裁决里更保守的那个。

    反证（``evidence[].stance == 'contradicts'``）比台账自称更有信息量：一条独立审计说
    MOCK，就不该继续按 REAL 记账。
    """
    declared = _declared_level(entry)
    contradictions: List[Dict[str, Any]] = []
    for ev in entry.get("evidence") or []:
        if not isinstance(ev, Mapping):
            continue
        stance = (ev.get("stance") or "").lower()
        asserted = (ev.get("asserted_level") or "").upper()
        if stance == "contradicts" and asserted in LEVEL_RANK:
            contradictions.append({"asserted_level": asserted,
                                   "ref": ev.get("report_id") or ev.get("id") or ev.get("ref"),
                                   "note": ev.get("note"),
                                   "quote_match": ev.get("quote_match")})
    level = declared
    if contradictions:
        lowest = min(contradictions, key=lambda c: LEVEL_RANK[c["asserted_level"]])
        if LEVEL_RANK[lowest["asserted_level"]] < LEVEL_RANK[level]:
            level = lowest["asserted_level"]
    return {"declared_level": declared, "effective_level": level,
            "contradictions": contradictions}


def load_feature_ledger(path: Path | str) -> Dict[str, Dict[str, Any]]:
    """读台账，返回 ``{capability_id: {declared_level, effective_level, contradictions, raw}}``。"""
    p = Path(path)
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    caps = raw.get("capabilities", raw) if isinstance(raw, Mapping) else raw
    out: Dict[str, Dict[str, Any]] = {}
    for entry in caps or []:
        if not isinstance(entry, Mapping):
            continue
        cid = _cap_id(entry)
        if not cid:
            continue
        eff = effective_level(entry)
        out[str(cid)] = {**eff, "raw": dict(entry)}
    return out


def load_deltas(path: Path | str | None) -> List[Dict[str, Any]]:
    """读 ``tensor_capability_deltas`` 导出（``{"deltas": [...]}`` 或列表）。

    导出里若带 ``capability_id`` 就直接用；没有则退化为按 summary/artifacts 文本里
    出现的能力 id 做匹配（并显式标注 ``match='text'``，不假装是硬关联）。
    """
    if path is None:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw.get("deltas", raw) if isinstance(raw, Mapping) else raw
    return [r for r in rows or [] if isinstance(r, Mapping)]


def _delta_text(delta: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for key in ("summary", "reason", "headline", "artifacts", "evidence", "card_id"):
        val = delta.get(key)
        if val is None:
            continue
        parts.append(json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val)
    return " ".join(parts)


def cross_ledger(report: Mapping[str, Any], ledger: Mapping[str, Mapping[str, Any]], *,
                 deltas: Sequence[Mapping[str, Any]] = (), real_only: bool = True) -> Dict[str, Any]:
    """交叉巡检报告与台账，输出 fail-closed 判定与降级建议。"""
    cap_status = {c["capability_id"]: c for c in report.get("capabilities", [])}
    dead_units_by_cap: Dict[str, List[Dict[str, Any]]] = {}
    for surf in report.get("surfaces", []):
        if surf.get("status") != "dead":
            continue
        for cid in surf.get("capability_ids") or []:
            dead_units_by_cap.setdefault(cid, []).append(surf)

    findings: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []
    for cid, entry in sorted(ledger.items()):
        status = cap_status.get(cid)
        patrol_status = status["status"] if status else "unmapped"
        level = entry["effective_level"]
        dead = dead_units_by_cap.get(cid, [])
        if dead and level == "REAL":
            verdict = "FAIL_CLOSED"
        elif dead:
            verdict = "DOWNGRADE_RECOMMENDED"
        elif patrol_status == "unmapped" and level == "REAL":
            verdict = "REVIEW_NO_SURFACE"
        else:
            verdict = "OK"
        if real_only and level != "REAL" and verdict == "OK":
            pass
        finding = {
            "capability_id": cid,
            "declared_level": entry["declared_level"],
            "effective_level": level,
            "patrol_status": patrol_status,
            "verdict": verdict,
            "dead_units": [{"unit": d["unit"], "reasons": d.get("dead_reasons", []),
                            "evidence": d.get("evidence", {})} for d in dead],
            "n_dead_units": len(dead),
        }
        findings.append(finding)
        if verdict in ("FAIL_CLOSED", "DOWNGRADE_RECOMMENDED"):
            suggestions.append({
                "capability_id": cid,
                "action": "DOWNGRADE",
                "from": level,
                "to": "WITHHELD",
                "policy": "fail-closed: 验证面 dead 时不得继续声称 REAL",
                "reasons": sorted({r for d in dead for r in d.get("dead_reasons", [])}),
                "evidence": {"dead_units": [d["unit"] for d in dead],
                             "collect_errors": [d.get("evidence", {}).get("collect") for d in dead
                                                if d.get("evidence", {}).get("collect")],
                             "patrol_commit": report.get("repo", {}).get("git_commit")},
            })
        elif verdict == "REVIEW_NO_SURFACE":
            suggestions.append({
                "capability_id": cid, "action": "REVIEW_MAPPING", "from": level, "to": level,
                "policy": "台账 REAL 但注册表没有任何验证面认领该能力（边缺失，非死面）",
                "reasons": ["no_registered_surface"],
                "evidence": {"patrol_commit": report.get("repo", {}).get("git_commit")},
            })

    # 增量流水交叉：声称 capability 增量的 delta 若落在 dead 验证面上，标 EXPOSED
    exposed: List[Dict[str, Any]] = []
    known_dead = set(dead_units_by_cap)
    for delta in deltas:
        cids: List[str] = []
        match = "explicit"
        if delta.get("capability_id"):
            cids = [str(delta["capability_id"])]
        else:
            text = _delta_text(delta)
            cids = [cid for cid in ledger if cid in text] or [cid for cid in known_dead if cid in text]
            match = "text"
        hit = [cid for cid in cids if cid in known_dead]
        if hit:
            exposed.append({"delta_id": delta.get("id"), "mission_id": delta.get("mission_id"),
                            "category": delta.get("category"), "match": match,
                            "capability_ids": hit,
                            "dead_units": [d["unit"] for cid in hit for d in dead_units_by_cap[cid]],
                            "verdict": "EXPOSED"})

    # 第二路：增量流水自带的**验证面工件路径**是否落在 dead 面上。
    # 这比能力 id 文本匹配硬得多：delta 说"我加了 tests/foo/test_bar.py"，而那个文件
    # 现在收集不起来 —— 这就是"能力增量宣称的验证面已经死了"，可以直接指到文件。
    dead_by_unit = {s["unit"]: s for s in report.get("surfaces", []) if s.get("status") == "dead"}
    for delta in deltas:
        paths: List[str] = []
        arts = delta.get("artifacts")
        if isinstance(arts, str):
            arts = [arts]
        for art in arts or []:
            if isinstance(art, str):
                paths.append(art.replace("\\", "/").lstrip("./"))
            elif isinstance(art, Mapping):
                for key in ("path", "file", "name"):
                    if isinstance(art.get(key), str):
                        paths.append(str(art[key]).replace("\\", "/").lstrip("./"))
        matched = [pth for pth in paths if pth in dead_by_unit]
        if matched:
            exposed.append({"delta_id": delta.get("id"), "mission_id": delta.get("mission_id"),
                            "category": delta.get("category"), "match": "artifacts",
                            "capability_ids": sorted({cid for pth in matched
                                                      for cid in dead_by_unit[pth]["capability_ids"]}),
                            "dead_units": matched,
                            "reasons": sorted({r for pth in matched
                                               for r in dead_by_unit[pth].get("dead_reasons", [])}),
                            "verdict": "EXPOSED"})

    blocked = any(f["verdict"] == "FAIL_CLOSED" for f in findings) or bool(exposed)
    return {
        "schema": SCHEMA,
        "policy": "fail-closed",
        "blocked": blocked,
        "findings": findings,
        "suggestions": suggestions,
        "deltas_crossed": len(deltas),
        "deltas_exposed": exposed,
        "summary": {
            "n_capabilities": len(findings),
            "n_fail_closed": sum(1 for f in findings if f["verdict"] == "FAIL_CLOSED"),
            "n_downgrade_recommended": sum(1 for f in findings
                                           if f["verdict"] == "DOWNGRADE_RECOMMENDED"),
            "n_review_no_surface": sum(1 for f in findings if f["verdict"] == "REVIEW_NO_SURFACE"),
            "n_ok": sum(1 for f in findings if f["verdict"] == "OK"),
            "n_suggestions": len(suggestions),
            "n_deltas_exposed": len(exposed),
        },
    }