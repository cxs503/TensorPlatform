"""能力台账合并：证据定级 + honest FAIL 保护 + 确定性审计链。

定级规则 (写进产物，可被独立复算)：

R1 ``level = min(claim_level, 判据允许上限, 互裁允许上限)``：自述强于证据 → 按证据定级，
   并在 ``notes`` 记 ``claimed_above_evidence``。
R2 honest FAIL 不可覆盖：任一判据 FAIL 的条目 ``honest_fail=true``，等级 ≤ MOCK；
   基础台账里同 id 已是 ``honest_fail`` 的条目，新证据不得抬高 (只能持平或下降)。
R3 未合并分支/外部仓证据 → ``status=PROVISIONAL``（等级不虚高，但注明不可从 main 复现）。
R4 无序输出：条目按 (node_ext_id, capability_id) 排序，整份台账有内容 sha256。
R5 无证据 → WITHHELD（"未验证"的默认态），绝不默认 REAL。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .model import LEVEL_RANK, Attestation, PairResult, cap, rank, weaker
from .provenance import sha256_obj

LEDGER_SCHEMA = "tp.integration.ledger/v1"

PAIR_CAP = {"AGREE": "REAL", "AGREE_CAVEAT": "REAL", "DISAGREE": "MOCK",
            "DEGENERATE": "MOCK", "WITHHELD": "WITHHELD"}


def _merge_state(facts: dict[str, Any] | None, repo: str, self_repo: str) -> str:
    """合并态以基线对齐结论为准（跨仓节点也可能在本地 worktree 里查得到 git 事实）。"""
    if facts and facts.get("state"):
        return str(facts["state"])
    if repo and self_repo and repo != self_repo:
        return "EXTERNAL_REPO"
    if not facts or not facts.get("known"):
        return "UNKNOWN"
    return "MERGED" if facts.get("merged_into_main") else "BRANCH_ONLY"


def _pairs_for(cap_id: str, pairs: list[PairResult], pairs_spec: dict[str, Any]) -> list[str]:
    out = []
    for spec in pairs_spec.get("pairs", []):
        if cap_id in (spec.get("capabilities") or []):
            out.append(spec["id"])
    return out


def build_entries(attestations: list[Attestation],
                  pairs: list[PairResult],
                  pairs_spec: dict[str, Any],
                  facts_by_node: dict[str, dict[str, Any]],
                  self_repo: str,
                  base_ledger: dict[str, Any] | None = None,
                  nodes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    pair_by_id = {p.id: p for p in pairs}
    base_index: dict[str, dict[str, Any]] = {}
    if base_ledger:
        for e in base_ledger.get("capabilities") or []:
            rid = e.get("id")
            if rid:
                base_index[rid] = e

    entries: list[dict[str, Any]] = []
    for att in attestations:
        merge_state = _merge_state(facts_by_node.get(att.node_ext_id), att.repo, self_repo)
        ceiling = att.ceiling()
        caps = att.capabilities or [{
            "id": f"{att.node_ext_id}:unspecified",
            "claim_level": "WITHHELD",
            "summary": "(节点未声明能力条目)",
        }]
        for c in caps:
            cid = c["id"]
            claimed = c.get("claim_level", "WITHHELD")
            if claimed not in LEVEL_RANK:
                claimed = "WITHHELD"
            level = cap(claimed, ceiling)
            linked = _pairs_for(cid, pairs, pairs_spec)
            pair_cap = "REAL"
            for pid in linked:
                p = pair_by_id.get(pid)
                if p is None:
                    pair_cap = weaker(pair_cap, "WITHHELD")
                    continue
                pair_cap = weaker(pair_cap, p.cap_level or PAIR_CAP.get(p.verdict(), "MOCK"))
            level = cap(level, pair_cap)
            honest_fail = att.honest_fail()
            blocking = c.get("blocking_criteria") or []
            notes: list[str] = []
            if rank(claimed) > rank(level):
                notes.append(f"claimed_above_evidence: claimed {claimed} -> graded {level} "
                             f"(ceiling={ceiling}, pair_cap={pair_cap})")
            if honest_fail:
                failed = [cr.id for cr in att.gate_criteria() if cr.verdict == "FAIL"]
                notes.append(f"honest_fail criteria: {', '.join(failed)}")
            base = base_index.get(cid)
            if base is not None:
                base_level = base.get("reconciled_level") or base.get("level")
                base_honest = bool(base.get("honest_fail"))
                if base_level in LEVEL_RANK:
                    if base_honest and rank(level) > rank(base_level):
                        notes.append("override_blocked: base entry is honest FAIL, "
                                     f"cannot raise {base_level} -> {level}")
                        level = weaker(level, base_level)
                    else:
                        level = weaker(level, base_level)
                    honest_fail = honest_fail or base_honest
            status = "VERIFIED" if (merge_state == "MERGED" and rank(level) >= rank("MOCK")) \
                else "PROVISIONAL"
            if merge_state == "MERGE_MISSING":  # pragma: no cover - defensive
                status = "PROVISIONAL"
            entries.append({
                "capability_id": cid,
                "node_ext_id": att.node_ext_id,
                "repo": att.repo,
                "mission_id": att.mission_id,
                "summary": c.get("summary", ""),
                "claimed_level": claimed,
                "level": level,
                "status": status,
                "merge_state": merge_state,
                "honest_fail": honest_fail,
                "blocking_criteria": blocking,
                "pair_links": linked,
                "pair_cap": pair_cap,
                "criteria_ceiling": ceiling,
                "evidence": {
                    "source_kind": att.source_kind,
                    "source_ref": att.source_ref,
                    "source_sha256": att.source_sha256,
                    "criteria_counts": att.counts(),
                    "pass_rate": att.pass_rate(),
                    "node_verdict": att.verdict(),
                },
                "notes": " | ".join(notes),
            })
    # 无自证的节点也必须登记（等级 WITHHELD）：台账不许"漏登记"当"没能力"
    seen_nodes = {a.node_ext_id for a in attestations}
    for node in (nodes or []):
        ext = node["ext_id"]
        if ext in seen_nodes:
            continue
        merge_state = _merge_state(facts_by_node.get(ext), node.get("repo", ""), self_repo)
        entries.append({
            "capability_id": f"{ext}:unattested",
            "node_ext_id": ext,
            "repo": node.get("repo", ""),
            "mission_id": node.get("mission_id"),
            "summary": (node.get("label", "") + "（本门运行时无可机器复算的验收自证）").strip(),
            "claimed_level": "WITHHELD",
            "level": "WITHHELD",
            "status": "PROVISIONAL",
            "merge_state": merge_state,
            "honest_fail": False,
            "blocking_criteria": [],
            "pair_links": [],
            "pair_cap": "REAL",
            "criteria_ceiling": "WITHHELD",
            "evidence": {"source_kind": "none", "source_ref": "", "source_sha256": "",
                         "criteria_counts": {"total": 0}, "pass_rate": None,
                         "node_verdict": "WITHHELD"},
            "notes": "no_attestation: 无证据默认 WITHHELD（R5），不自称能力也不否定能力",
        })
    entries.sort(key=lambda e: (e["node_ext_id"], e["capability_id"]))
    return entries


def build_ledger(campaign: str, entries: list[dict[str, Any]], pairs: list[PairResult],
                 alignments: list[dict[str, Any]], base_commit: str | None,
                 generated_at: str | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {lv: 0 for lv in LEVEL_RANK}
    for e in entries:
        counts[e["level"]] = counts.get(e["level"], 0) + 1
    doc = {
        "schema": LEDGER_SCHEMA,
        "campaign": campaign,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "base_commit": base_commit,
        "level_rank": LEVEL_RANK,
        "rules": [
            "R1 level = min(claim_level, criteria_ceiling, pair_cap)；自述强于证据按证据定级",
            "R2 honest FAIL 不可覆盖：含 FAIL 判据的能力 ≤ MOCK，基础台账 honest_fail 条目不得抬高",
            "R3 未合并分支/外部仓证据 → status=PROVISIONAL（等级不虚高，注明不可从 main 复现）",
            "R4 条目按 (node_ext_id, capability_id) 排序，台账内容 sha256 可由 canonical JSON 复算",
            "R5 无证据 → WITHHELD，绝不默认 REAL",
        ],
        "entries": entries,
        "counts": counts,
        "pairs": [p.as_dict() for p in pairs],
        "baseline": alignments,
    }
    doc["content_sha256"] = sha256_obj({k: v for k, v in doc.items() if k != "generated_at"})
    return doc