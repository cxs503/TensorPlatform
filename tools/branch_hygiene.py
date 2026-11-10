#!/usr/bin/env python3
"""分支交付面卫生机制（p223w3-n10）：在 branch_ledger 的 ANCHOR/BANKED/CONTENT_SAME/UNIQUE
分类之上，提供 ABCD 等价分类、sha256 账本、处置动作、只减不增门。

分类映射
--------
- A（内容已被 main 覆盖）：ANCHOR / BANKED / CONTENT_SAME → safe_to_close
- B（被另一分支超集覆盖）：UNIQUE 但改动文件集 ⊆ 另一 UNIQUE 分支 → safe_to_close
- C（真未交付）：UNIQUE 且不被任何其他分支超集覆盖 → 需保留/开交付补单
- D（孤儿）：无提交、无内容差异的空壳分支 → safe_to_close

产物
----
- ledger_hygiene.json：每分支 {repo, branch, class, evidence, decided_by, safe_to_close}
- sha256 入 provenance 字段
- baseline.json：只减不增门基线
- disposition.json：处置动作清单

纪律
----
- 零领域语义：只处理 git/交付元数据
- 只读 git 操作（继承 branch_ledger 的只读纪律）
- 可复算：除 generated_at/provenance 外确定性
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# 复用 branch_ledger 的核心函数
try:
    from tools.branch_ledger import (
        collect as ledger_collect,
        build_ledger as ledger_build,
        CLASS_ANCHOR, CLASS_BANKED, CLASS_CONTENT_SAME, CLASS_UNIQUE,
        run_git, list_refs, rev_count, cherry_split, merge_base, changed_files,
    )
    HAS_LEDGER = True
except ImportError:
    HAS_LEDGER = False
    # Fallback: define minimal versions
    CLASS_ANCHOR = "ANCHOR"
    CLASS_BANKED = "BANKED"
    CLASS_CONTENT_SAME = "CONTENT_SAME"
    CLASS_UNIQUE = "UNIQUE"

    def run_git(repo: str, *args: str, timeout: int = 120) -> Tuple[int, str]:
        try:
            p = subprocess.run(
                ["git", "-C", repo, *args],
                capture_output=True, text=True, timeout=timeout,
            )
            return p.returncode, p.stdout
        except (OSError, subprocess.TimeoutExpired):
            return 127, ""

SCHEMA_VERSION = "2.0"

# ABCD classes
CLASS_A = "A"  # Content already in main
CLASS_B = "B"  # Covered by another branch (superset)
CLASS_C = "C"  # Truly undelivered
CLASS_D = "D"  # Orphan

ABCD_CLASSES = (CLASS_A, CLASS_B, CLASS_C, CLASS_D)


def compute_sha256(data: Any) -> str:
    """计算 JSON 可序列化数据的 sha256（排序键、无空格）。"""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_abcd(ledger_row: Dict[str, Any]) -> Tuple[str, str]:
    """将 branch_ledger 的行映射到 ABCD 分类。

    Returns:
        (abcd_class, evidence_description)
    """
    cls = ledger_row.get("class", "")
    ahead = ledger_row.get("ahead", 0)
    unique = ledger_row.get("unique_commits", 0)
    files = ledger_row.get("files", [])

    # A: content already in main (check class first)
    if cls == CLASS_ANCHOR:
        return CLASS_A, "in_main_history"
    if cls == CLASS_BANKED:
        return CLASS_A, "patch_id_all_banked"
    if cls == CLASS_CONTENT_SAME:
        return CLASS_A, "content_identical_on_touched"

    # D: orphan - UNIQUE class but no actual content (truly abandoned)
    if cls == CLASS_UNIQUE and ahead == 0 and unique == 0 and not files:
        return CLASS_D, "orphan_no_content"

    # C: truly undelivered (will be refined to B if superset found)
    if cls == CLASS_UNIQUE:
        return CLASS_C, "unique_commits_not_in_main"

    # Fallback
    return CLASS_C, "unknown_state"


def detect_supersets(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """检测 B 类分支：改动文件集是另一 UNIQUE 分支的子集。

    Returns:
        {branch_name: superset_branch_name}
    """
    unique_rows = [r for r in rows if r.get("class") == CLASS_UNIQUE and r.get("files")]
    supersets: Dict[str, str] = {}

    for i, row_i in enumerate(unique_rows):
        files_i = set(row_i["files"])
        if not files_i:
            continue
        for j, row_j in enumerate(unique_rows):
            if i == j:
                continue
            files_j = set(row_j["files"])
            # row_i is subset of row_j (and row_j is strictly larger or same mission priority)
            if files_i <= files_j and len(files_i) < len(files_j):
                supersets[row_i["branch"]] = row_j["branch"]
                break  # Found a superset, no need to check more
            # Same file set but different branch name - pick the one with more commits
            elif files_i == files_j and files_i:
                if row_j.get("unique_commits", 0) > row_i.get("unique_commits", 0):
                    supersets[row_i["branch"]] = row_j["branch"]
                    break

    return supersets


def build_hygiene_ledger(
    repo: str,
    base: str = "main",
    pattern: str = "agent/*",
    include_remote: bool = False,
    conflict_check: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """构建 ABCD 分类账本。"""
    # Collect using branch_ledger
    if HAS_LEDGER:
        collected = ledger_collect(
            repo, base=base, pattern=pattern,
            include_remote=include_remote,
            conflict_check=conflict_check, limit=limit,
        )
    else:
        collected = _minimal_collect(repo, base, pattern, include_remote, limit)

    rows = collected["rows"]

    # Step 1: Initial ABCD classification
    for row in rows:
        abcd_class, evidence = classify_abcd(row)
        row["abcd_class"] = abcd_class
        row["abcd_evidence"] = evidence

    # Step 2: Detect supersets (B class)
    supersets = detect_supersets(rows)
    for row in rows:
        if row["branch"] in supersets:
            row["abcd_class"] = CLASS_B
            row["abcd_evidence"] = f"subset_of:{supersets[row['branch']]}"
            row["superset_source"] = supersets[row["branch"]]

    # Step 3: Build final entries
    entries = []
    for row in rows:
        abcd = row["abcd_class"]
        safe_to_close = abcd in (CLASS_A, CLASS_B, CLASS_D)
        entry = {
            "repo": os.path.basename(os.path.abspath(repo)),
            "branch": row["branch"],
            "class": abcd,
            "evidence": row["abcd_evidence"],
            "decided_by": "branch_hygiene_v2",
            "safe_to_close": safe_to_close,
            # Additional metadata
            "ahead": row.get("ahead", 0),
            "unique_commits": row.get("unique_commits", 0),
            "files_count": len(row.get("files", [])),
            "month": row.get("month", ""),
        }
        if "superset_source" in row:
            entry["superset_source"] = row["superset_source"]
        entries.append(entry)

    # Sort by branch name for determinism
    entries.sort(key=lambda e: e["branch"])

    # Compute stats
    by_class = defaultdict(int)
    for e in entries:
        by_class[e["class"]] += 1

    # Compute sha256 of the entries (excluding generated_at)
    entries_for_hash = [{"branch": e["branch"], "class": e["class"], "evidence": e["evidence"]}
                        for e in entries]
    sha256 = compute_sha256(entries_for_hash)

    ledger = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": os.path.basename(os.path.abspath(repo)),
        "base": base,
        "pattern": pattern,
        "provenance": {
            "sha256": sha256,
            "algorithm": "sha256(json.dumps(entries, sort_keys=True))",
            "reproducible_fields": ["branch", "class", "evidence"],
        },
        "stats": {
            "total_branches": len(entries),
            "by_class": dict(by_class),
            "safe_to_close_count": sum(1 for e in entries if e["safe_to_close"]),
            "needs_delivery_count": by_class.get(CLASS_C, 0),
        },
        "entries": entries,
    }

    return ledger


def _minimal_collect(repo: str, base: str, pattern: str,
                      include_remote: bool, limit: Optional[int]) -> Dict[str, Any]:
    """Minimal collection when branch_ledger is not available."""
    rc, out = run_git(repo, "for-each-ref", f"--format=%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)",
                      f"refs/heads/{pattern}")
    if rc != 0:
        return {"base": base, "pattern": pattern, "include_remote": include_remote, "rows": []}

    rows = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        branch, sha, date = parts[0], parts[1], parts[2]

        # Count ahead commits
        rc2, count_out = run_git(repo, "rev-list", "--count", f"{base}..{branch}")
        ahead = int(count_out.strip()) if rc2 == 0 else 0

        # Simple classification
        if ahead == 0:
            cls = CLASS_ANCHOR
            unique = 0
        else:
            # Check cherry
            rc3, cherry_out = run_git(repo, "cherry", base, branch)
            if rc3 == 0:
                lines = cherry_out.strip().split("\n") if cherry_out.strip() else []
                plus = sum(1 for l in lines if l.startswith("+"))
                minus = sum(1 for l in lines if l.startswith("-"))
                unique = plus
                if plus == 0 and minus > 0:
                    cls = CLASS_BANKED
                else:
                    cls = CLASS_UNIQUE
            else:
                cls = CLASS_UNIQUE
                unique = ahead

        month = date[:7] if len(date) >= 7 else "unknown"
        rows.append({
            "branch": branch,
            "sha": sha,
            "date": date,
            "month": month,
            "ahead": ahead,
            "unique_commits": unique,
            "banked_commits": 0,
            "class": cls,
            "files": [],
            "content_same_on_touched": False,
            "merge_conflict": False,
            "landing": "ALREADY_IN_BASE" if cls != CLASS_UNIQUE else "UNREPORTED",
            "mission_id": None,
            "report_id": None,
            "report_title": None,
        })

    if limit:
        rows = rows[:limit]

    return {"base": base, "pattern": pattern, "include_remote": include_remote, "rows": rows}


def generate_disposition(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """生成处置动作清单。

    Actions:
    - A/D: close_and_archive (move to refs/attic/)
    - B: annotate_superset
    - C: create_delivery_ticket
    """
    actions = []
    for entry in ledger["entries"]:
        cls = entry["class"]
        branch = entry["branch"]

        if cls in (CLASS_A, CLASS_D):
            actions.append({
                "action": "close_and_archive",
                "branch": branch,
                "target_ref": f"refs/attic/{branch.replace('/', '__')}",
                "reason": entry["evidence"],
                "safe": True,
            })
        elif cls == CLASS_B:
            actions.append({
                "action": "annotate_superset",
                "branch": branch,
                "superset_source": entry.get("superset_source", "unknown"),
                "reason": entry["evidence"],
                "safe": True,
            })
        elif cls == CLASS_C:
            # Extract mission id if possible
            mission_id = None
            parts = branch.split("/")
            if len(parts) >= 2:
                name = parts[-1]
                if name.startswith("m") and name[1:].isdigit():
                    mission_id = int(name[1:])
            actions.append({
                "action": "create_delivery_ticket",
                "branch": branch,
                "mission_id": mission_id,
                "reason": entry["evidence"],
                "safe": False,
                "priority": "high" if entry["unique_commits"] > 3 else "medium",
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ledger_sha256": ledger["provenance"]["sha256"],
        "total_actions": len(actions),
        "by_action_type": {
            "close_and_archive": sum(1 for a in actions if a["action"] == "close_and_archive"),
            "annotate_superset": sum(1 for a in actions if a["action"] == "annotate_superset"),
            "create_delivery_ticket": sum(1 for a in actions if a["action"] == "create_delivery_ticket"),
        },
        "actions": actions,
    }


def load_baseline(path: str) -> Optional[Dict[str, Any]]:
    """加载只减不增门基线。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_baseline(path: str, ledger: Dict[str, Any]) -> Dict[str, Any]:
    """保存只减不增门基线。"""
    baseline = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ledger_sha256": ledger["provenance"]["sha256"],
        "total_branches": ledger["stats"]["total_branches"],
        "by_class": ledger["stats"]["by_class"],
        "needs_delivery_count": ledger["stats"]["needs_delivery_count"],
    }
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
    return baseline


def check_only_decrease_gate(
    current_ledger: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    """只减不增门检查。

    Returns:
        {passed: bool, violations: [...], details: {...}}
    """
    current_total = current_ledger["stats"]["total_branches"]
    baseline_total = baseline["total_branches"]

    violations = []
    if current_total > baseline_total:
        violations.append({
            "metric": "total_branches",
            "baseline": baseline_total,
            "current": current_total,
            "delta": current_total - baseline_total,
        })

    # Check each class
    for cls in ABCD_CLASSES:
        baseline_count = baseline["by_class"].get(cls, 0)
        current_count = current_ledger["stats"]["by_class"].get(cls, 0)
        if current_count > baseline_count:
            violations.append({
                "metric": f"class_{cls}",
                "baseline": baseline_count,
                "current": current_count,
                "delta": current_count - baseline_count,
            })

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "baseline_sha256": baseline.get("ledger_sha256", ""),
        "current_sha256": current_ledger["provenance"]["sha256"],
        "details": {
            "baseline_total": baseline_total,
            "current_total": current_total,
            "delta": current_total - baseline_total,
        },
    }


def verify_ledger_against_git(ledger: Dict[str, Any], repo: str, pattern: str = "agent/*") -> Dict[str, Any]:
    """验证账本分支集合与 git for-each-ref 差集为空。"""
    # Get actual branches from git
    rc, out = run_git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{pattern}")
    if rc != 0:
        return {"verified": False, "error": "git for-each-ref failed"}

    git_branches = set(line.strip() for line in out.strip().split("\n") if line.strip())
    ledger_branches = set(e["branch"] for e in ledger["entries"])

    in_git_not_ledger = git_branches - ledger_branches
    in_ledger_not_git = ledger_branches - git_branches

    return {
        "verified": len(in_git_not_ledger) == 0 and len(in_ledger_not_git) == 0,
        "git_branch_count": len(git_branches),
        "ledger_branch_count": len(ledger_branches),
        "in_git_not_ledger": sorted(in_git_not_ledger),
        "in_ledger_not_git": sorted(in_ledger_not_git),
        "diff_empty": len(in_git_not_ledger | in_ledger_not_git) == 0,
    }


def render_markdown(ledger: Dict[str, Any], disposition: Dict[str, Any],
                    gate_result: Optional[Dict[str, Any]] = None) -> str:
    """渲染人读摘要。"""
    s = ledger["stats"]
    lines = [
        "# 分支交付面卫生报告（Branch Hygiene Ledger）",
        "",
        f"- repo: `{ledger['repo']}` base: `{ledger['base']}` pattern: `{ledger['pattern']}`",
        f"- generated_at: {ledger['generated_at']}",
        f"- provenance sha256: `{ledger['provenance']['sha256'][:16]}...`",
        "",
        "## 分类统计",
        "",
        f"| 类别 | 数量 | 说明 |",
        f"|------|------|------|",
        f"| A | {s['by_class'].get('A', 0)} | 内容已被 main 覆盖 |",
        f"| B | {s['by_class'].get('B', 0)} | 被另一分支超集覆盖 |",
        f"| C | {s['by_class'].get('C', 0)} | 真未交付（需保留/开单） |",
        f"| D | {s['by_class'].get('D', 0)} | 孤儿（空壳分支） |",
        "",
        f"- 总分支数: **{s['total_branches']}**",
        f"- 可安全关闭: **{s['safe_to_close_count']}**",
        f"- 需交付补单: **{s['needs_delivery_count']}**",
        "",
        "## 处置动作摘要",
        "",
        f"- close_and_archive: {disposition['by_action_type']['close_and_archive']}",
        f"- annotate_superset: {disposition['by_action_type']['annotate_superset']}",
        f"- create_delivery_ticket: {disposition['by_action_type']['create_delivery_ticket']}",
        "",
    ]

    if gate_result:
        status = "✅ PASS" if gate_result["passed"] else "❌ FAIL"
        lines.extend([
            "## 只减不增门",
            "",
            f"- 状态: {status}",
            f"- 基线总数: {gate_result['details']['baseline_total']}",
            f"- 当前总数: {gate_result['details']['current_total']}",
            f"- 差值: {gate_result['details']['delta']}",
        ])
        if gate_result["violations"]:
            lines.append("- 违规项:")
            for v in gate_result["violations"]:
                lines.append(f"  - {v['metric']}: {v['baseline']} → {v['current']} (+{v['delta']})")
        lines.append("")

    # C class details
    c_entries = [e for e in ledger["entries"] if e["class"] == CLASS_C]
    if c_entries:
        lines.extend([
            f"## C 类（真未交付）明细（前 20 条）",
            "",
            "| branch | unique_commits | files_count | evidence |",
            "|--------|----------------|-------------|----------|",
        ])
        for e in sorted(c_entries, key=lambda x: -x["unique_commits"])[:20]:
            lines.append(f"| {e['branch']} | {e['unique_commits']} | {e['files_count']} | {e['evidence']} |")
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="分支交付面卫生机制：ABCD 分类 + sha256 账本 + 只减不增门")
    ap.add_argument("--repo", default=".", help="git 仓库路径")
    ap.add_argument("--base", default="main", help="基准分支")
    ap.add_argument("--pattern", default="agent/*", help="refs 匹配模式")
    ap.add_argument("--include-remote", action="store_true")
    ap.add_argument("--conflict-check", action="store_true")
    ap.add_argument("--out", default="artifacts/branch_hygiene", help="输出目录")
    ap.add_argument("--baseline-path", default=None, help="只减不增门基线路径")
    ap.add_argument("--set-baseline", action="store_true", help="设置当前状态为基线")
    ap.add_argument("--check-gate", action="store_true", help="检查只减不增门")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 条")
    ap.add_argument("--verify-git", action="store_true", help="验证账本与 git 对账")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    # Build hygiene ledger
    ledger = build_hygiene_ledger(
        repo=args.repo, base=args.base, pattern=args.pattern,
        include_remote=args.include_remote,
        conflict_check=args.conflict_check, limit=args.limit,
    )

    # Generate disposition
    disposition = generate_disposition(ledger)

    # Verify against git
    git_verify = None
    if args.verify_git:
        git_verify = verify_ledger_against_git(ledger, args.repo, args.pattern)

    # Baseline / gate
    gate_result = None
    baseline_path = args.baseline_path or os.path.join(args.out, "baseline.json")

    if args.set_baseline:
        save_baseline(baseline_path, ledger)
        print(f"[branch_hygiene] baseline set: {baseline_path}")
    elif args.check_gate:
        baseline = load_baseline(baseline_path)
        if baseline:
            gate_result = check_only_decrease_gate(ledger, baseline)
        else:
            print(f"[branch_hygiene] WARNING: no baseline found at {baseline_path}, setting current as baseline")
            save_baseline(baseline_path, ledger)

    # Write outputs
    ledger_path = os.path.join(args.out, "ledger_hygiene.json")
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

    disposition_path = os.path.join(args.out, "disposition.json")
    with open(disposition_path, "w", encoding="utf-8") as f:
        json.dump(disposition, f, indent=2, ensure_ascii=False)

    md_path = os.path.join(args.out, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(ledger, disposition, gate_result))

    if git_verify:
        verify_path = os.path.join(args.out, "git_verify.json")
        with open(verify_path, "w", encoding="utf-8") as f:
            json.dump(git_verify, f, indent=2, ensure_ascii=False)

    # Summary
    s = ledger["stats"]
    print(f"[branch_hygiene] total={s['total_branches']} "
          f"A={s['by_class'].get('A', 0)} B={s['by_class'].get('B', 0)} "
          f"C={s['by_class'].get('C', 0)} D={s['by_class'].get('D', 0)} "
          f"safe_to_close={s['safe_to_close_count']}")
    print(f"  sha256: {ledger['provenance']['sha256'][:16]}...")
    print(f"  -> {ledger_path}")
    print(f"  -> {disposition_path}")
    print(f"  -> {md_path}")

    if gate_result and not gate_result["passed"]:
        print(f"[branch_hygiene] GATE FAILED: {len(gate_result['violations'])} violations")
        return 1

    if git_verify and not git_verify["verified"]:
        print(f"[branch_hygiene] GIT VERIFY FAILED")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
