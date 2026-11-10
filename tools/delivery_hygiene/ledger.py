"""机器可读账本：逐分支 ``{repo, branch, class, evidence, decided_by, safe_to_close}`` + 统计 + 人读摘要。

账本契约（稳定，供 CI / 补单池 / 看板消费）
------------------------------------------
::

    {
      "schema_version": "1.0",
      "generated_at": "...",            # 唯一可变字段
      "repo": "<label>", "repo_path": "...",
      "base": "main", "base_sha": "...", "pattern": "agent/*",
      "include_remote": false,
      "stats": {...},
      "rows": [ {"repo","branch","class","class_label","decided_by",
                 "safe_to_close","mission_id","sha","tip_date",
                 "evidence": {...}, "superset_source": <str|null>} ],
      "retention": [ {"branch","held_by","class"} ],       # 内容留存去向
      "tickets": [ {...} ],                               # C 类交付补单
      "dispositions": [ {...} ]                           # 处置动作计划（未执行）
    }

可复算性：除 ``generated_at`` 外所有字段由 git 状态唯一决定；``rows`` 按分支名排序。
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import CLASS_A, CLASS_B, CLASS_C, CLASS_D, CLASSES, CLASS_LABELS, SCHEMA_VERSION
from . import gitio

EVIDENCE_KEYS = (
    "ahead", "unique_shas", "equivalent_count", "empty_commits", "unique_patch_ids",
    "net_diff_empty", "squash_patch_in_base", "combined_patch_id", "tip_in_base_history",
    "merge_base", "changed_files", "changed_files_total",
)


def build_row(repo_label: str, fact: Dict[str, Any]) -> Dict[str, Any]:
    """把一条 fact 折成账本行：``evidence`` 保留全部可推翻判据的原始数字。"""
    row: Dict[str, Any] = {
        "repo": repo_label,
        "branch": fact["branch"],
        "class": fact["class"],
        "class_label": CLASS_LABELS[fact["class"]],
        "decided_by": fact["decided_by"],
        "safe_to_close": bool(fact["safe_to_close"]),
        "retention_owner": fact.get("retention_owner"),
        "mission_id": fact.get("mission_id"),
        "sha": fact["sha"],
        "tip_date": fact.get("tip_date", ""),
        "superset_source": fact.get("superset_source"),
        "evidence": {k: fact.get(k) for k in EVIDENCE_KEYS},
        "engine": fact.get("engine"),
    }
    if fact["class"] == CLASS_B:
        row["evidence"]["superset_extra_commits"] = fact.get("superset_extra_commits")
    return row


def summarize(rows: Sequence[Dict[str, Any]], base_sha: str,
              base_date: str, engine_crosscheck: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统计：按类计数、未合并数、最老未交付 tip、按月份趋势。"""
    by_class = Counter(r["class"] for r in rows)
    unmerged = [r for r in rows if r["class"] in (CLASS_B, CLASS_C)]
    undelivered = [r for r in rows if r["class"] == CLASS_C]
    by_month: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_month[(r.get("tip_date") or "")[:7] or "unknown"][r["class"]] += 1
    oldest = min((r["tip_date"] for r in undelivered if r.get("tip_date")), default=None)
    safe = [r for r in rows if r["safe_to_close"]]
    return {
        "total_branches": len(rows),
        "by_class": {c: by_class.get(c, 0) for c in CLASSES},
        "unmerged": len(unmerged),
        "undelivered": len(undelivered),
        "safe_to_close": len(safe),
        "unmerged_ratio": (len(unmerged) / len(rows)) if rows else 0.0,
        "unique_commits_total": sum(len(r["evidence"]["unique_shas"] or []) for r in rows),
        "oldest_undelivered_tip": oldest,
        "base_sha": base_sha,
        "base_tip_date": base_date,
        "by_month": {m: {c: v.get(c, 0) for c in CLASSES} for m, v in sorted(by_month.items())},
        "engine": next((r.get("engine") for r in rows if r.get("engine")), None),
        "engine_crosscheck": engine_crosscheck or {"sample": 0, "agreement": None},
    }


def build_retention(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """内容留存去向：B 类的内容最终留在哪条分支上（C 类留下 / base 已有）。"""
    return [{"branch": r["branch"], "held_by": r["retention_owner"], "class": r["class"]}
            for r in rows if r["class"] == CLASS_B]


def build_tickets(rows: Sequence[Dict[str, Any]], repo_label: str, base: str,
                  max_files: int = 10) -> List[Dict[str, Any]]:
    """C 类 → 交付补单（机器可读，可被任务机收养）。一分支一单，``kind=delivery``。"""
    tickets: List[Dict[str, Any]] = []
    for r in sorted((x for x in rows if x["class"] == CLASS_C),
                    key=lambda x: (-len(x["evidence"]["unique_shas"] or []), x["branch"])):
        ev = r["evidence"]
        tickets.append({
            "kind": "delivery",
            "repo": repo_label,
            "branch": r["branch"],
            "mission_id": r["mission_id"],
            "base": base,
            "branch_tip": r["sha"],
            "unique_commits": len(ev["unique_shas"] or []),
            "unique_commit_shas": (ev["unique_shas"] or [])[:20],
            "changed_files": (ev["changed_files"] or [])[:max_files],
            "changed_files_total": ev["changed_files_total"],
            "priority": "P1" if len(ev["unique_shas"] or []) >= 3 else "P2",
            "acceptance": "merge-or-cherry-pick into base, then re-run delivery_hygiene scan "
                          "until class becomes A/D",
            "decided_by": r["decided_by"],
        })
    return tickets


def build_ledger(repo: str, repo_label: str, base: str, pattern: str, rows: Sequence[Dict[str, Any]],
                 include_remote: bool = False, base_sha: Optional[str] = None,
                 base_date: Optional[str] = None,
                 engine_crosscheck: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """组账本（不写盘）。"""
    base_sha = base_sha or (gitio.rev_parse(repo, base) or "")
    base_date = base_date if base_date is not None else gitio.tip_date(repo, base)
    rows = sorted(rows, key=lambda r: r["branch"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo_label,
        "repo_path": os.path.abspath(repo),
        "base": base,
        "base_sha": base_sha,
        "base_tip_date": base_date,
        "pattern": pattern,
        "include_remote": include_remote,
        "stats": summarize(rows, base_sha, base_date, engine_crosscheck),
        "retention": build_retention(rows),
        "tickets": build_tickets(rows, repo_label, base),
        "rows": list(rows),
    }


def render_markdown(ledger: Dict[str, Any], top_n: int = 20) -> str:
    s = ledger["stats"]
    rows = ledger["rows"]
    by = s["by_class"]
    lines = [
        f"# 交付面卫生账本 — `{ledger['repo']}`",
        "",
        f"- base：`{ledger['base']}` @ `{ledger['base_sha'][:12]}`　"
        f"pattern：`{ledger['pattern']}`　扫描分支：**{s['total_branches']}**",
        f"- 分类：A 已覆盖 **{by[CLASS_A]}** / B 超集覆盖 **{by[CLASS_B]}** / "
        f"C 真未交付 **{by[CLASS_C]}** / D 孤儿 **{by[CLASS_D]}**",
        f"- 未合并（B+C）：**{s['unmerged']}**（占 {s['unmerged_ratio'] * 100:.1f}%）；"
        f"可安全关闭（A+B+D）：**{s['safe_to_close']}**；独有提交合计 **{s['unique_commits_total']}** 条",
    ]
    if s["oldest_undelivered_tip"]:
        lines.append(f"- 最老未交付 tip：`{s['oldest_undelivered_tip']}`（年龄即「报告绿、代码没落」的累积时长）")
    lines += ["", "## 分类判据（可推翻）", "",
              "| 类 | 判据 | decided_by | 可安全关闭 |", "|---|---|---|---|",
              "| D | `base..b` 无提交（tip 已在 base 历史） | `rule:D-tip-in-base-history/v1` | 是 |",
              "| A | `unique==0` / 净 diff 为空 / squash 合成 patch-id 命中 base | "
              "`rule:A-patch-id-all-in-base/v1` / `rule:A-net-diff-empty/v1` / "
              "`rule:A-squash-combined-patch/v1` | 是 |",
              "| B | 独有 patch-id ⊆ 另一分支独有集 | `rule:B-patch-id-subset-of-superset/v1` | 是（内容在 S） |",
              "| C | 独有 patch-id 既不在 base 也不在别分支 | `rule:C-unique-patch-ids/v1` | 否 |",
              "", f"## C 类交付补单（{len(ledger['tickets'])} 条，按独有提交降序，前 {top_n}）", "",
              "| 分支 | mission | 独有提交 | 改文件 | 优先 | 顶端改动路径 |", "|---|---|---|---|---|---|"]
    for r in sorted((x for x in rows if x["class"] == CLASS_C),
                    key=lambda x: (-len(x["evidence"]["unique_shas"] or []), x["branch"]))[:top_n]:
        ev = r["evidence"]
        head = ", ".join(f"`{p}`" for p in (ev["changed_files"] or [])[:2]) or "—"
        lines.append(f"| `{r['branch']}` | {r['mission_id']} | {len(ev['unique_shas'] or [])} | "
                     f"{ev['changed_files_total']} | {'P1' if len(ev['unique_shas'] or []) >= 3 else 'P2'} | {head} |")
    grp: Dict[str, List[str]] = defaultdict(list)
    for r in rows:
        if r["class"] == CLASS_B and r.get("superset_source"):
            grp[r["superset_source"]].append(r["branch"])
    lines += ["", f"## B 类超集组（{len(grp)} 个留存源，共 {by[CLASS_B]} 条被覆盖分支）", ""]
    if grp:
        lines += ["| 留存源（canonical） | 被它覆盖的分支数 | 示例 |", "|---|---|---|"]
        for src, members in sorted(grp.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top_n]:
            lines.append(f"| `{src}` | {len(members)} | {', '.join('`' + m + '`' for m in members[:3])} |")
    else:
        lines.append("（本仓没有重复 cherry-pick 造成的等价分支簇）")
    lines += ["", "## 局限", "",
              "- 判等用 patch-id：**squash 合并会改变 patch-id**，被 squash 的分支若净内容也一致仍判 A"
              "（净 diff 判据兜底），但若 squash 同时改了内容，则可能仍判 C——本表只宣称"
              "「base 里没有等价补丁」，不宣称「一定还有新内容」。",
              "- B 类只宣称「本分支独有补丁集合 ⊆ 另一分支独有补丁集合」，不宣称后者质量更好。",
              "- 本账本默认 dry-run：不建/删/改任何 ref，处置动作只出现在 `dispositions` 计划里。"]
    xc = s.get("engine_crosscheck") or {}
    if xc.get("sample"):
        lines.append(f"- 引擎交叉校验：pidset 引擎 vs `git cherry` 抽样 {xc['sample']} 条，"
                     f"一致 {xc['agree']} / 不一致 {xc['disagree']}"
                     f"（一致率 {xc['agreement']:.1%}）。" if xc.get("agreement") is not None
                     else f"- 引擎交叉校验：抽样 {xc['sample']} 条未得出一致率。")
    return "\n".join(lines) + "\n"


def write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)