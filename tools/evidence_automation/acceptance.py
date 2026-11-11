"""验收证据工件（acceptance.json）与覆盖率汇总的落盘。

产物纪律
--------
* 每条有证据的能力一份 ``acceptance.json``：判据（criteria）、实测（measured）、
  等级与理由（verdict/reasoning）、来源摘要（mapping/tests/probe 的 sha256）。
* 文件内自带 ``content_digest`` = 对剔除易变字段后的规范化内容求 sha256；
  旁车 ``acceptance.json.sha256`` 给整文件摘要 —— 证据可被外部独立复算。
* ``coverage.json`` 汇总覆盖率与判据 C1~C4 的机械结论（数字全部来自本次运行）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .mapping import LEVEL_RANK

SCHEMA = "capability_acceptance/1.0"
COVERAGE_SCHEMA = "capability_evidence_coverage/1.0"
VOLATILE_KEYS = ("generated_at", "content_digest", "run_metrics")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_digest(payload: Dict[str, Any]) -> str:
    trimmed = {k: v for k, v in payload.items() if k not in VOLATILE_KEYS}
    return sha256_text(canonical_json(trimmed))


def build_acceptance(capability_id: str, *, criteria: Dict[str, Any],
                     tests: Dict[str, Any], probes: List[Dict[str, Any]],
                     grade: Dict[str, Any], mapping_digest: str,
                     evidence_scope: str) -> Dict[str, Any]:
    """构造单条能力的验收工件（纯函数）。"""
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "capability_id": capability_id,
        "evidence_scope": evidence_scope,
        "criteria": criteria,
        "measured": {"tests": tests, "probes": probes},
        "verdict": {
            "level": grade["level"],
            "basis": grade.get("basis"),
            "ceiling_applied": grade.get("ceiling_applied"),
            "reasoning": grade.get("reasoning", []),
        },
        "markers": grade.get("markers", {}),
        "sources": {"mapping_sha256": mapping_digest},
        "covered": bool(_executed(tests, probes)),
    }
    payload["content_digest"] = content_digest(payload)
    return payload


def _executed(tests: Dict[str, Any], probes: List[Dict[str, Any]]) -> bool:
    """覆盖口径：至少一条**真实执行过**的证据（测试或探针可用）。"""
    if int(tests.get("decisive", 0) or 0) > 0:
        return True
    return any(p.get("available") for p in probes)


def write_acceptance(out_dir: Path, capability_id: str,
                     payload: Dict[str, Any]) -> Dict[str, str]:
    """落盘 acceptance.json + sha256 旁车，返回路径。"""
    target = out_dir / capability_id / "acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    digest = sha256_file(target)
    sidecar = target.with_suffix(".json.sha256")
    sidecar.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return {"path": str(target), "sha256": digest}


def build_coverage(*, capability_ids: Iterable[str], acceptances: Dict[str, Dict[str, Any]],
                   target_covered: int = 15) -> Dict[str, Any]:
    """覆盖率汇总 + C1/C3 机械结论。"""
    ids = list(capability_ids)
    covered = [cid for cid in ids if acceptances.get(cid, {}).get("covered")]
    uncovered = [cid for cid in ids if cid not in covered]
    reasons: Dict[str, str] = {}
    for cid in uncovered:
        acc = acceptances.get(cid)
        if not acc:
            reasons[cid] = "no_acceptance_artifact"
        else:
            level = acc.get("verdict", {}).get("level")
            reasons[cid] = f"no_executed_evidence:{level}"
    tally: Dict[str, int] = {}
    for cid in ids:
        level = (acceptances.get(cid) or {}).get("verdict", {}).get("level", "UNMAPPED")
        tally[level] = tally.get(level, 0) + 1
    return {
        "schema": COVERAGE_SCHEMA,
        "total_capabilities": len(ids),
        "covered": len(covered),
        "covered_ids": covered,
        "uncovered_ids": uncovered,
        "uncovered_reasons": reasons,
        "target_covered": target_covered,
        "target_met": len(covered) >= target_covered,
        "grade_tally": dict(sorted(tally.items())),
        "C1_coverage_target_met": len(covered) >= target_covered,
        "C3_every_covered_has_acceptance": all(
            (acceptances.get(cid) or {}).get("content_digest") for cid in covered),
    }


def check_no_downgrade(*, capability_ids: Iterable[str], acceptances: Dict[str, Dict[str, Any]],
                       manual_levels: Dict[str, str]) -> Dict[str, Any]:
    """C4：人工复核为 REAL 的能力不得被降级；且任何能力不得超过人工上限。"""
    downgraded: List[str] = []
    overclaimed: List[str] = []
    checked = 0
    for cid in capability_ids:
        manual = manual_levels.get(cid)
        auto = (acceptances.get(cid) or {}).get("verdict", {}).get("level")
        if not manual or not auto:
            continue
        checked += 1
        if manual == "REAL" and LEVEL_RANK.get(auto, -1) < LEVEL_RANK["REAL"]:
            downgraded.append(f"{cid}:{auto}<{manual}")
        if LEVEL_RANK.get(auto, -1) > LEVEL_RANK.get(manual, -1):
            overclaimed.append(f"{cid}:{auto}>{manual}")
    return {
        "checked": checked,
        "downgraded": downgraded,
        "overclaimed": overclaimed,
        "C4_manual_real_not_downgraded": not downgraded,
        "C4_no_auto_overclaim": not overclaimed,
    }