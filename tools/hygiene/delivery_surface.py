#!/usr/bin/env python3
"""交付面卫生机制：未合并分支的内容等价四分 + 「只减不增」门（零领域语义）。

动机
----
平台每单在工作树上建 ``agent/m<id>`` 分支，交付后由平台推送/合并。分支只增不减时，
两个问题同时出现：

1. **数字失真**：仓库里「看起来没合并」的分支数远大于「真的还有东西没进主干」的分支数。
   后者才是风险量，前者只能用来吓人。没有内容等价判据，任何「降分支数」的动作都可能
   把真成果一起删掉。
2. **门禁缺位**：没有基线，新波次开出来多少分支都没人管；有基线但只比总数，则「删十条
   真未交付 + 新增十条空壳」也能骗过门。

本模块把这两件事各做成一个可机器裁的机制：

* **四分**（内容等价，判据全部可被推翻，逐条留证据）：

  ========  ============================================================  ==========================
  分类      判据                                                          处置
  ========  ============================================================  ==========================
  ``A``     ``base..ref`` 为空（ANCHOR）/ ``git cherry`` 全为 ``-``        主干已覆盖，可安全归档
            （BANKED）/ 触及路径内容与 base 逐字节一致（CONTENT_SAME）
  ``B``     唯一 patch-id 集合是**另一个分支唯一集**的真子集（超集覆盖）    内容已被后续分支吸收
  ``C``     存在主干没有的等价补丁（``git cherry`` 有 ``+``）且无超集       真未交付 —— 唯一必须
                                                                            优先处理的集合
  ``D``     与 base 无共同祖先 / 引用不可解析（孤儿或抓取残片）              归属不可判，需人工裁决
  ========  ============================================================  ==========================

* **只减不增门**：每仓把「未合并集合 = B∪C∪D」基线化，之后每次检查要求
  (i) 未合并数 ≤ 基线；(ii) 不得出现基线里没有的未合并分支；
  (iii) 基线里的 C 分支若消失，必须能**证明**它已进主干（记录的交付 commit 是 base 祖先），
  否则判 ``C_LOSS_WITHOUT_PROOF``（C 类零丢失）；(iv) 覆盖率 100%（每行都有 class + 证据）。

只读纪律
--------
本模块只跑 ``git`` 只读查询（``for-each-ref`` / ``rev-list`` / ``cherry`` / ``log -p`` /
``patch-id`` / ``diff`` / ``merge-base``），不建/删/改任何 ref，不写对象库，不改 ``.git`` 配置。
分支的实际删除/合并由运维侧执行；本模块给的是「删哪条安全、哪条不能碰」的证据与门禁。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_ROOT not in sys.path:
    sys.path.insert(0, _TOOLS_ROOT)

import branch_ledger as BL  # noqa: E402  (复用只读 git 底层，避免第二套 plumbing)

SCHEMA_LEDGER = "hygiene.delivery_ledger/1"
SCHEMA_BASELINE = "hygiene.delivery_baseline/1"
SCHEMA_GATE = "hygiene.delivery_gate/1"

CLASS_A = "A"   # 已被主干覆盖
CLASS_B = "B"   # 被超集覆盖
CLASS_C = "C"   # 真未交付
CLASS_D = "D"   # 孤儿/不可判
CLASSES: Tuple[str, ...] = (CLASS_A, CLASS_B, CLASS_C, CLASS_D)
UNMERGED_CLASSES: Tuple[str, ...] = (CLASS_B, CLASS_C, CLASS_D)

DEFAULT_PATTERN = "agent/*"
DEFAULT_BASE = "main"

REASON_ANCHOR = "ANCHOR"
REASON_BANKED = "BANKED"
REASON_CONTENT_SAME = "CONTENT_SAME"
REASON_SUPERSET = "SUPERSET"
REASON_UNIQUE_PATCH = "UNIQUE_PATCH"
REASON_NO_COMMON_ANCESTOR = "NO_COMMON_ANCESTOR"
REASON_UNRESOLVABLE_REF = "UNRESOLVABLE_REF"

VIOL_NEW_UNMERGED = "NEW_UNMERGED_BRANCH"
VIOL_COUNT_INCREASE = "UNMERGED_COUNT_INCREASE"
VIOL_C_LOSS = "C_LOSS_WITHOUT_PROOF"
VIOL_C_INCREASE = "C_COUNT_INCREASE"
VIOL_COVERAGE = "COVERAGE_GAP"
VIOL_BASELINE_MISSING = "BASELINE_MISSING"
VIOL_DIGEST = "LEDGER_DIGEST_MISMATCH"
VIOL_CLASS_FLIP = "CLASS_FLIP"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── git 判据 ────────────────────────────────────────────────────────────────
def _git(repo: str, *args: str) -> Tuple[int, str]:
    return BL.run_git(repo, *args)


def unique_commits(repo: str, base: str, ref: str) -> List[str]:
    """``git cherry`` 判为 ``+`` 的提交（主干没有等价补丁）的 sha 列表。"""
    rc, out = _git(repo, "cherry", base, ref)
    if rc != 0:
        return []
    return [line[2:].strip() for line in out.splitlines() if line.startswith("+")]


def patch_ids(repo: str, rev_range: str) -> Dict[str, str]:
    """``rev_range`` 内每个提交的 stable patch-id（commit sha → patch-id）。

    用管道而不是 ``shell=True``：仓路径/ref 名不进 shell，避免命令拼接面。
    """
    log = BL.subprocess.Popen(
        ["git", "-C", repo, "log", "-p", "--no-merges", "--format=%H", rev_range],
        stdout=BL.subprocess.PIPE, stderr=BL.subprocess.DEVNULL,
    )
    try:
        out = BL.subprocess.run(
            ["git", "patch-id", "--stable"],
            stdin=log.stdout, capture_output=True, text=True, timeout=300,
        )
    finally:
        if log.stdout is not None:
            log.stdout.close()
        log.wait(timeout=60)
    if out.returncode != 0:
        return {}
    mapping: Dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            mapping[parts[1]] = parts[0]
    return mapping


def _content_identical(repo: str, base: str, ref: str, paths: Sequence[str]) -> bool:
    if not paths:
        return False
    rc, out = _git(repo, "diff", "--name-only", base, ref, "--", *paths)
    return rc == 0 and out.strip() == ""


def _changed_paths_long(repo: str, base: str, ref: str) -> List[str]:
    rc, out = _git(repo, "diff", "--name-only", base, ref)
    if rc != 0:
        return []
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def classify_one(repo: str, base: str, ref: str) -> Dict[str, Any]:
    """单分支的内容等价判定（A / D 可在本函数内定案；B/C 需全局超集检测）。"""
    mb = BL.merge_base(repo, base, ref)
    if mb is None:
        return {
            "class": CLASS_D,
            "evidence": {"reason": REASON_NO_COMMON_ANCESTOR, "base": base, "merge_base": None},
        }
    ahead = BL.rev_count(repo, f"{base}..{ref}")
    if ahead == 0:
        return {
            "class": CLASS_A,
            "evidence": {"reason": REASON_ANCHOR, "base": base, "ahead": 0, "unique": 0,
                         "merge_base": mb},
        }
    uniq = unique_commits(repo, base, ref)
    if not uniq:
        return {
            "class": CLASS_A,
            "evidence": {"reason": REASON_BANKED, "base": base, "ahead": ahead, "unique": 0,
                         "merge_base": mb},
        }
    files = _changed_paths_long(repo, base, ref)
    # 空 diff ⇒ 整棵树与 base 相同（改过又改回的典型形态）；否则按触及路径逐字节比对
    if not files or _content_identical(repo, base, ref, files):
        return {
            "class": CLASS_A,
            "evidence": {"reason": REASON_CONTENT_SAME, "base": base, "ahead": ahead,
                         "unique": len(uniq), "merge_base": mb, "files": files},
        }
    pids = patch_ids(repo, f"{base}..{ref}")
    uniq_pids = sorted({pids[s] for s in uniq if s in pids})
    return {
        "class": None,  # 待全局超集检测
        "evidence": {
            "reason": REASON_UNIQUE_PATCH,
            "base": base,
            "ahead": ahead,
            "unique": len(uniq),
            "unique_patch_ids": uniq_pids,
            "merge_base": mb,
            "files": files,
        },
    }


def resolve_supersets(candidates: List[Dict[str, Any]]) -> None:
    """把「唯一 patch-id 集是被另一分支真子集」的行改判为 B，其余改判 C（原地修改）。"""
    pid_sets = {row["name"]: set(row["evidence"].get("unique_patch_ids") or []) for row in candidates}
    for row in candidates:
        mine = pid_sets[row["name"]]
        best = None
        for other, theirs in pid_sets.items():
            if other == row["name"] or not mine:
                continue
            if mine < theirs:  # 真子集
                if best is None or len(theirs) > len(pid_sets[best]) or (
                    len(theirs) == len(pid_sets[best]) and other < best
                ):
                    best = other
        if best is not None:
            row["class"] = CLASS_B
            row["evidence"]["reason"] = REASON_SUPERSET
            row["evidence"]["superset_by"] = best
            row["evidence"]["superset_unique"] = len(pid_sets[best])
        else:
            row["class"] = CLASS_C
            row["evidence"]["reason"] = REASON_UNIQUE_PATCH


def list_branches(repo: str, pattern: str = DEFAULT_PATTERN, *,
                  include_remote: bool = False) -> List[Dict[str, str]]:
    refs = BL.list_refs(repo, pattern, include_remote)
    return refs


def _classify_ref(repo: str, base: str, ref: Dict[str, str]) -> Dict[str, Any]:
    judged = classify_one(repo, base, ref["name"])
    return {
        "name": ref["name"],
        "sha": ref["sha"],
        "date": ref["date"],
        "kind": ref["kind"],
        "class": judged["class"],
        "evidence": judged["evidence"],
    }


def collect_rows(repo: str, base: str = DEFAULT_BASE, pattern: str = DEFAULT_PATTERN, *,
                 limit: Optional[int] = None, include_remote: bool = False,
                 jobs: int = 1) -> List[Dict[str, Any]]:
    """逐分支判定并组装台账行（每行必有 class + 非空 evidence）。

    ``jobs > 1`` 用线程池并行跑只读 git 查询（子进程 I/O 为主）；结果按分支名排序，
    因此并行与串行的产物逐字节一致（可复算性不受并行度影响）。
    """
    refs = list_branches(repo, pattern, include_remote=include_remote)
    if limit is not None and limit >= 0:
        refs = refs[:limit]  # 按 ref 名排序后的前 N 条（可复算，不用时间做次序）
    rows: List[Dict[str, Any]] = []
    if jobs and jobs > 1 and len(refs) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=int(jobs)) as pool:
            rows = list(pool.map(lambda r: _classify_ref(repo, base, r), refs))
    else:
        rows = [_classify_ref(repo, base, r) for r in refs]
    candidates: List[Dict[str, Any]] = [r for r in rows if r["class"] is None]
    resolve_supersets(candidates)
    rows.sort(key=lambda r: r["name"])
    return rows


# ── 台账 / 摘要 / 覆盖率 ────────────────────────────────────────────────────
SLIM_MAX_FILES = 8


def row_evidence_sha256(row: Dict[str, Any]) -> str:
    """单行完整证据（name + class + evidence 全量）的摘要：瘦身表的防篡改锚。"""
    return sha256_text(canonical_json({"name": row["name"], "class": row["class"],
                                       "evidence": row.get("evidence") or {}}))


def slim_rows(rows: Sequence[Dict[str, Any]], max_files: int = SLIM_MAX_FILES) -> List[Dict[str, Any]]:
    """把逐行证据里体积最大的 ``files`` 列表截断，保留可核验锚。

    动机：一次全仓扫描的行证据可能上千 MB（大仓单分支可改上千文件），直接进 git 不现实。
    瘦身只动 ``files``：保留前 ``max_files`` 条、总数 ``files_count``、
    以及完整文件列表的 ``files_digest``；同时整行写 ``evidence_sha256``（对完整证据）。
    因此「瘦身表 → 完整证据」的链是可核验的，而 ``class`` 判据本身从未被截断。
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        ev = dict(row.get("evidence") or {})
        digest = row_evidence_sha256(row)
        files = ev.get("files")
        if isinstance(files, list):
            ev["files_count"] = len(files)
            ev["files_digest"] = sha256_text(canonical_json(list(files)))
            ev["files"] = list(files)[:max_files]
            ev["files_truncated"] = len(files) > max_files
        out.append({**row, "evidence": ev, "evidence_sha256": digest})
    return out


def slim_ledger(ledger: Dict[str, Any], max_files: int = SLIM_MAX_FILES) -> Dict[str, Any]:
    """产出瘦身台账；保留 ``full_ledger_sha256``（瘦身前的摘要）作为溯源锚。"""
    rows = slim_rows(ledger.get("rows") or [], max_files)
    slim = dict(ledger)
    slim["rows"] = rows
    slim["full_ledger_sha256"] = ledger.get("ledger_sha256")
    slim["ledger_sha256"] = ledger_sha256(rows)
    slim["slim"] = {"max_files": int(max_files), "rows": len(rows),
                    "note": "files 截断；完整证据锚 = 每行 evidence_sha256 + 整表 full_ledger_sha256"}
    return slim


def ledger_sha256(rows: Sequence[Dict[str, Any]]) -> str:
    """台账摘要：只对「判定结果 + 证据」的规范化 JSON 取 sha256，可被第三方重算。"""
    payload = [
        {"name": r["name"], "class": r["class"], "evidence": r["evidence"]} for r in rows
    ]
    return sha256_text(canonical_json(payload))


def coverage(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    missing: List[str] = []
    bad_evidence: List[str] = []
    for r in rows:
        if r.get("class") not in CLASSES:
            missing.append(r.get("name", "<noname>"))
        elif not isinstance(r.get("evidence"), dict) or not r["evidence"].get("reason"):
            bad_evidence.append(r.get("name", "<noname>"))
    total = len(rows)
    covered = total - len(missing) - len(bad_evidence)
    return {
        "total": total,
        "covered": covered,
        "rate": (covered / total) if total else 1.0,
        "missing_class": missing,
        "missing_evidence": bad_evidence,
        "complete": not missing and not bad_evidence,
    }


def build_ledger(repo: str, rows: Sequence[Dict[str, Any]], *, base: str = DEFAULT_BASE,
                 pattern: str = DEFAULT_PATTERN, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    counts = Counter(r["class"] for r in rows)
    unmerged = [r["name"] for r in rows if r["class"] in UNMERGED_CLASSES]
    cov = coverage(rows)
    return {
        "schema": SCHEMA_LEDGER,
        "repo": os.path.abspath(repo),
        "base": base,
        "pattern": pattern,
        "counts": {c: int(counts.get(c, 0)) for c in CLASSES},
        "total": len(rows),
        "unmerged_count": len(unmerged),
        "unmerged": sorted(unmerged),
        "class_c": sorted(r["name"] for r in rows if r["class"] == CLASS_C),
        "coverage": cov,
        "ledger_sha256": ledger_sha256(rows),
        "rows": list(rows),
        "meta": meta or {},
    }


def verify_ledger(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """重算摘要 + 覆盖率，回答「这张台账有没有被改过」。"""
    rows = ledger.get("rows") or []
    recomputed = ledger_sha256(rows)
    cov = coverage(rows)
    counts = Counter(r["class"] for r in rows)
    consistent_counts = all(int(ledger.get("counts", {}).get(c, -1)) == counts.get(c, 0) for c in CLASSES)
    return {
        "stored_sha256": ledger.get("ledger_sha256"),
        "recomputed_sha256": recomputed,
        "digest_match": ledger.get("ledger_sha256") == recomputed,
        "coverage": cov,
        "counts_consistent": consistent_counts,
        "ok": ledger.get("ledger_sha256") == recomputed and cov["complete"] and consistent_counts,
    }


# ── 基线 / 只减不增门 ───────────────────────────────────────────────────────
def make_baseline(ledger: Dict[str, Any], *, created_at: str = "") -> Dict[str, Any]:
    """把台账固化成门禁基线（每分支记 class + 行摘要，便于检出改判）。"""
    rows = ledger.get("rows") or []
    return {
        "schema": SCHEMA_BASELINE,
        "repo": ledger.get("repo"),
        "base": ledger.get("base"),
        "pattern": ledger.get("pattern"),
        "created_at": created_at,
        "counts": dict(ledger.get("counts") or {}),
        "total": int(ledger.get("total") or 0),
        "unmerged_count": int(ledger.get("unmerged_count") or 0),
        "ledger_sha256": ledger.get("ledger_sha256"),
        "branches": [
            {
                "name": r["name"],
                "class": r["class"],
                "row_sha256": sha256_text(canonical_json({"name": r["name"], "class": r["class"],
                                                          "evidence": r["evidence"]})),
            }
            for r in rows
        ],
    }


def delivered_proof(repo: str, base: str, sha: Optional[str]) -> bool:
    """某条曾被判为 C 的分支消失后，能否证明其成果已进主干（记录的 commit 是 base 祖先）。"""
    if not sha:
        return False
    rc, _ = _git(repo, "merge-base", "--is-ancestor", sha, base)
    return rc == 0


def evaluate_gate(ledger: Dict[str, Any], baseline: Optional[Dict[str, Any]], *,
                  repo: Optional[str] = None, proofs: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """只减不增门：返回 green/red + 逐条违规（每条都带证据）。

    ``proofs``：``{branch_name: commit_sha}``，用于证明「基线里的 C 分支已兑现」。
    """
    violations: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}
    rows = ledger.get("rows") or []
    repo = repo or ledger.get("repo")

    cov = coverage(rows)
    checks["coverage_complete"] = cov["complete"]
    if not cov["complete"]:
        violations.append({"code": VIOL_COVERAGE, "detail": cov})

    ver = verify_ledger(ledger)
    checks["digest_match"] = ver["digest_match"]
    if not ver["digest_match"]:
        violations.append({"code": VIOL_DIGEST, "detail": {"stored": ver["stored_sha256"],
                                                          "recomputed": ver["recomputed_sha256"]}})

    if not baseline:
        checks["baseline_present"] = False
        violations.append({"code": VIOL_BASELINE_MISSING,
                           "detail": "无基线 ⇒ 无法证明只减不增，门必须红（fail-closed）"})
        return {
            "schema": SCHEMA_GATE,
            "status": "red",
            "checks": checks,
            "violations": violations,
            "detection": {"new_unmerged": 0, "detected": 0, "rate": None},
        }

    checks["baseline_present"] = True
    base_unmerged = {b["name"] for b in baseline.get("branches", []) if b["class"] in UNMERGED_CLASSES}
    base_classes = {b["name"]: b["class"] for b in baseline.get("branches", [])}
    cur_unmerged = {r["name"] for r in rows if r["class"] in UNMERGED_CLASSES}
    cur_classes = {r["name"]: r["class"] for r in rows}

    # (i) 未合并数只减不增
    base_count = int(baseline.get("unmerged_count") or 0)
    checks["unmerged_count"] = {"current": len(cur_unmerged), "baseline": base_count}
    checks["count_le_baseline"] = len(cur_unmerged) <= base_count
    if len(cur_unmerged) > base_count:
        violations.append({"code": VIOL_COUNT_INCREASE,
                           "detail": {"current": len(cur_unmerged), "baseline": base_count}})

    # (ii) 不得出现基线里没有的未合并分支
    new_unmerged = sorted(cur_unmerged - base_unmerged)
    checks["new_unmerged"] = new_unmerged
    checks["no_new_unmerged"] = not new_unmerged
    for name in new_unmerged:
        violations.append({"code": VIOL_NEW_UNMERGED,
                           "detail": {"branch": name, "class": cur_classes.get(name)}})

    # (iii) C 类零丢失：基线里的 C 分支消失必须能证明已进主干
    base_c = {n for n, c in base_classes.items() if c == CLASS_C}
    cur_c = {n for n, c in cur_classes.items() if c == CLASS_C}
    lost = sorted(base_c - set(cur_classes))
    unproven = sorted(n for n in lost if not delivered_proof(repo or "", baseline.get("base", DEFAULT_BASE),
                                                             (proofs or {}).get(n)))
    checks["c_baseline"] = sorted(base_c)
    checks["c_lost"] = lost
    checks["c_lost_proven"] = sorted(set(lost) - set(unproven))
    checks["c_lost_unproven"] = unproven
    for name in unproven:
        violations.append({"code": VIOL_C_LOSS, "detail": {"branch": name,
                                                          "reason": "消失且无已进主干的 commit 证明"}})
    # (iv) C 类不得增长
    checks["c_count"] = {"current": len(cur_c), "baseline": len(base_c)}
    if len(cur_c) > len(base_c):
        violations.append({"code": VIOL_C_INCREASE,
                           "detail": {"current": len(cur_c), "baseline": len(base_c)}})
    # (v) 改判：A→未合并 = 回归（分支被重写/强推）；未合并→A = 兑现，允许
    flips = []
    for name, cur in cur_classes.items():
        prev = base_classes.get(name)
        if prev is None or prev == cur:
            continue
        if prev == CLASS_A and cur in UNMERGED_CLASSES:
            flips.append({"branch": name, "from": prev, "to": cur})
    checks["regressions"] = flips
    for f in flips:
        violations.append({"code": VIOL_CLASS_FLIP, "detail": f})

    detection_total = len(new_unmerged)
    detected = sum(1 for v in violations if v["code"] == VIOL_NEW_UNMERGED)
    status = "red" if violations else "green"
    return {
        "schema": SCHEMA_GATE,
        "status": status,
        "checks": checks,
        "violations": violations,
        "counts": {"current": ledger.get("counts"), "baseline": baseline.get("counts")},
        "detection": {
            "new_unmerged": detection_total,
            "detected": detected,
            "rate": (detected / detection_total) if detection_total else None,
        },
        "ledger_sha256": ledger.get("ledger_sha256"),
        "baseline_ledger_sha256": baseline.get("ledger_sha256"),
    }


# ── 抽样独立复核（AC6） ─────────────────────────────────────────────────────
def independent_class(repo: str, base: str, ref: str) -> Dict[str, Any]:
    """用**另一条命令路径**独立判类，供抽样复核（不复用 classify_one）。

    口径：
      * ``git log --cherry-pick --right-only --no-merges base...ref`` 为空 ⇒ 无主干缺失补丁（A）
      * 有提交、``git diff --quiet base ref`` 干净 ⇒ 内容一致（A）
      * 否则：拿到自己的 patch-id 集；若台账声称 B，需由调用方用超集分支再核；此处只回 C 候选
    """
    rc, out = _git(repo, "log", "--cherry-pick", "--right-only", "--no-merges",
                   "--format=%H", f"{base}...{ref}")
    if rc != 0:
        return {"class": CLASS_D, "reason": "LOG_FAILED"}
    shas = [line.strip() for line in out.splitlines() if line.strip()]
    if not shas:
        return {"class": CLASS_A, "reason": "NO_LEFT_PATCHES"}
    rc2, _ = _git(repo, "diff", "--quiet", base, ref)
    if rc2 == 0:
        return {"class": CLASS_A, "reason": "TREE_IDENTICAL"}
    pids = patch_ids(repo, f"{base}..{ref}")
    mine = sorted({pids[s] for s in shas if s in pids})
    return {"class": CLASS_C, "reason": "PATCHES_NOT_IN_BASE", "unique_patch_ids": mine}


def _branch_exists(repo: str, name: str) -> bool:
    rc, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
    return rc == 0


def sample_recheck(ledger: Dict[str, Any], *, repo: Optional[str] = None, n: int = 30,
                   seed: int = 20261110) -> Dict[str, Any]:
    """随机抽 n 条，用独立判据复核台账分类，返回一致率（AC6 ≥95%）。"""
    rows = ledger.get("rows") or []
    repo = repo or ledger.get("repo")
    base = ledger.get("base", DEFAULT_BASE)
    if not rows:
        return {"sampled": 0, "agreed": 0, "rate": None, "details": []}
    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))
    details: List[Dict[str, Any]] = []
    agreed = 0
    for row in sorted(sample, key=lambda r: r["name"]):
        ind = independent_class(repo, base, row["name"])
        ok = False
        note = ""
        if row["class"] == CLASS_A:
            ok = ind["class"] == CLASS_A
            # 补强：BANKED 分支在 --cherry-pick 视角下也算 A；CONTENT_SAME 同理
            note = ind["reason"]
        elif row["class"] == CLASS_C:
            ok = ind["class"] == CLASS_C
            note = ind["reason"]
        elif row["class"] == CLASS_B:
            sup = (row.get("evidence") or {}).get("superset_by")
            if ind["class"] == CLASS_A:
                ok = False
                note = "ledger=B 但独立判据认为主干已覆盖（可能超集分支已入库 ⇒ B 应升为 A）"
            elif sup and _branch_exists(repo, sup):
                sup_ind = independent_class(repo, base, sup)
                mine = set(ind.get("unique_patch_ids") or [])
                theirs = set(sup_ind.get("unique_patch_ids") or [])
                ok = bool(mine) and mine < theirs
                note = "SUBSET_CHECK:" + ("pass" if ok else "fail")
            else:
                ok = False
                note = "超集分支不存在，B 判据不成立"
        elif row["class"] == CLASS_D:
            ok = BL.merge_base(repo, base, row["name"]) is None
            note = "NO_COMMON_ANCESTOR"
        agreed += 1 if ok else 0
        details.append({"branch": row["name"], "ledger_class": row["class"],
                        "independent": ind.get("class"), "independent_reason": ind.get("reason"),
                        "note": note, "agree": bool(ok)})
    return {
        "sampled": len(details),
        "agreed": agreed,
        "rate": (agreed / len(details)) if details else None,
        "seed": seed,
        "details": details,
    }


# ── 门禁负控（AC8）：注入 N 条新未合并分支，门必须变红 ──────────────────────
def gate_negative_control(repo: str, base: str, *, pattern: str = DEFAULT_PATTERN,
                          injections: int = 3) -> Dict[str, Any]:
    """在**临时夹具仓**里做负控：先基线，再注入新分支，门必须红且检出率 100%。

    真实仓不会被本函数改动：调用方必须传一次性夹具仓（见 tests）。
    """
    baseline_rows = collect_rows(repo, base, pattern)
    ledger0 = build_ledger(repo, baseline_rows, base=base, pattern=pattern)
    baseline = make_baseline(ledger0)
    gate0 = evaluate_gate(ledger0, baseline, repo=repo)

    injected = inject_branches(repo, base, count=injections, pattern=pattern)
    ledger1 = build_ledger(repo, collect_rows(repo, base, pattern), base=base, pattern=pattern)
    gate1 = evaluate_gate(ledger1, baseline, repo=repo)
    detected = [v["detail"]["branch"] for v in gate1["violations"] if v["code"] == VIOL_NEW_UNMERGED]
    hit = [b for b in injected if b in detected]
    return {
        "baseline_status": gate0["status"],
        "injected": injected,
        "detected": detected,
        "hit": hit,
        "detection_rate": (len(hit) / len(injected)) if injected else None,
        "post_status": gate1["status"],
        "ok": gate0["status"] == "green" and gate1["status"] == "red"
              and len(hit) == len(injected),
        "gate_before": gate0,
        "gate_after": gate1,
    }


# ── 夹具辅助：注入新分支（只用于一次性测试仓，绝不作用于真实仓） ──────────────
def inject_branches(repo: str, base: str, *, count: int = 3, pattern: str = DEFAULT_PATTERN,
                    prefix: Optional[str] = None) -> List[str]:
    """在**夹具仓**里从 ``base`` 拉出 ``count`` 条各带一个独占提交的分支。

    用途：门禁负控（AC8）——注入后「只减不增」门必须变红，检出率必须 100%。
    真实仓不应调用：它会把新 ref 写进对象库（违反只读纪律）。
    """
    base_prefix = (prefix or pattern).replace("*", "")
    created: List[str] = []
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "hygiene-fixture")
    env.setdefault("GIT_AUTHOR_EMAIL", "hygiene@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    for i in range(count):
        name = f"{base_prefix}inject_{i:02d}"
        rc, _ = _git(repo, "checkout", "--quiet", "-B", name, base)
        if rc != 0:
            raise RuntimeError(f"无法创建夹具分支 {name}")
        path = os.path.join(repo, "injected", f"note_{i:02d}.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"injected branch {name} payload\n")
        _git(repo, "add", os.path.relpath(path, repo))
        BL.subprocess.run(
            ["git", "-C", repo, "-c", f"user.name={env['GIT_AUTHOR_NAME']}",
             "-c", f"user.email={env['GIT_AUTHOR_EMAIL']}", "commit", "--quiet", "-m",
             f"fixture: injected commit for {name}"],
            capture_output=True, text=True, env=env,
        )
        created.append(name)
    _git(repo, "checkout", "--quiet", base)
    return created


__all__ = [
    "CLASS_A", "CLASS_B", "CLASS_C", "CLASS_D", "CLASSES", "UNMERGED_CLASSES",
    "collect_rows", "classify_one", "resolve_supersets", "ledger_sha256", "coverage",
    "build_ledger", "verify_ledger", "slim_rows", "slim_ledger", "row_evidence_sha256", "make_baseline", "evaluate_gate", "delivered_proof",
    "sample_recheck", "independent_class", "gate_negative_control", "inject_branches",
    "VIOL_NEW_UNMERGED", "VIOL_COUNT_INCREASE", "VIOL_C_LOSS", "VIOL_C_INCREASE",
    "VIOL_COVERAGE", "VIOL_BASELINE_MISSING", "VIOL_DIGEST", "VIOL_CLASS_FLIP",
]