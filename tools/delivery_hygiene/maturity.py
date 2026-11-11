"""交付面卫生的**成熟度自证**：把节点的五条机器判据变成一次可重算的审计。

为什么要这一层
--------------
``scan``/``freeze``/``gate`` 已经能给出账本与门裁决，但"机制是否成熟"这句话本身
一直是散文——谁也没法机器回答下面这五个问题：

* (a) 分类覆盖率是否 100%（每条分支都有 class + 证据，且账本内容可重算 sha256 一致）
* (b) 抽 30 条独立复核一致率是否 ≥95%
* (c) 目标仓未合并积压降了多少，C 类（真未交付）是否零丢失
* (d) 门禁能否被"负控"打红：注入 3 条新分支后门必须变红且检出率 100%
* (e) 账本分支集合与 ``git for-each-ref`` 的差集是否为空

本模块只做**测量与取证**（只读 git + 在临时合成仓库里做负控），不动任何真实 ref；
所有数字写进 ``report.json``，每份账本给 ``content_sha256``，判据结论绑定到该 sha256。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import CLASS_A, CLASS_B, CLASS_C, CLASS_D, CLASSES, CLASS_LABELS
from . import classify as classify_mod
from . import gate as gate_mod, gitio, ledger as ledger_mod

MATURITY_SCHEMA_VERSION = "1.0"

# 每条账本行必须携带的证据键（缺一即"证据不足"，覆盖率不得记 100%）
REQUIRED_EVIDENCE_KEYS = (
    "ahead",
    "unique_shas",
    "unique_patch_ids",
    "net_diff_empty",
    "tip_in_base_history",
    "merge_base",
)
CANONICAL_ROW_KEYS = (
    "branch", "class", "decided_by", "safe_to_close", "retention_owner",
    "mission_id", "sha", "superset_source",
)


# ── 可重算指纹 ─────────────────────────────────────────────────────────────────
def canonical_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把账本行规范化为"只由 git 状态决定"的投影（去掉易变时间戳），用于指纹。"""
    out = []
    for r in rows:
        ev = r.get("evidence") or {}
        out.append({
            "branch": r.get("branch"),
            "class": r.get("class"),
            "decided_by": r.get("decided_by"),
            "safe_to_close": r.get("safe_to_close"),
            "retention_owner": r.get("retention_owner"),
            "mission_id": r.get("mission_id"),
            "sha": r.get("sha"),
            "superset_source": r.get("superset_source"),
            "evidence": {k: ev.get(k) for k in REQUIRED_EVIDENCE_KEYS},
        })
    return sorted(out, key=lambda r: str(r["branch"]))


def content_sha256(ledger: Dict[str, Any]) -> str:
    """账本内容指纹：同一 git 状态必须得到同一 sha256（跨进程、跨机器可重算）。"""
    payload = json.dumps(
        {"base": ledger.get("base"), "base_sha": ledger.get("base_sha"),
         "pattern": ledger.get("pattern"), "rows": canonical_rows(ledger.get("rows") or [])},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── (a) 分类覆盖率 ─────────────────────────────────────────────────────────────
def measure_coverage(ledger: Dict[str, Any]) -> Dict[str, Any]:
    rows = ledger.get("rows") or []
    missing: List[Dict[str, Any]] = []
    for r in rows:
        why = []
        if r.get("class") not in CLASSES:
            why.append(f"class={r.get('class')!r}")
        if not str(r.get("decided_by") or "").startswith("rule:"):
            why.append("no-rule-id")
        ev = r.get("evidence") or {}
        lack = [k for k in REQUIRED_EVIDENCE_KEYS if k not in ev]
        if lack:
            why.append("missing-evidence:" + ",".join(lack))
        if not isinstance(r.get("safe_to_close"), bool):
            why.append("safe_to_close-not-bool")
        if r.get("class") in (CLASS_B,) and not r.get("superset_source"):
            why.append("B-without-superset_source")
        if r.get("class") == CLASS_C and not r.get("safe_to_close") is False:
            why.append("C-must-not-be-safe_to_close")
        if why:
            missing.append({"branch": r.get("branch"), "reasons": why})
    total = len(rows)
    covered = total - len(missing)
    stored = ledger.get("content_sha256")
    recomputed = content_sha256(ledger)
    return {
        "total_rows": total,
        "covered_rows": covered,
        "coverage": (covered / total) if total else 1.0,
        "missing": missing[:50],
        "missing_count": len(missing),
        "by_class": dict(ledger.get("stats", {}).get("by_class") or {}),
        "content_sha256_stored": stored,
        "content_sha256_recomputed": recomputed,
        "sha256_consistent": (stored == recomputed) if stored else None,
        "pass": (covered == total) and (stored is not None) and (stored == recomputed),
    }


# ── (e) 账本分支集合 == git ref 集合 ───────────────────────────────────────────
def measure_ref_diff(repo: str, ledger: Dict[str, Any],
                     include_remote: Optional[bool] = None) -> Dict[str, Any]:
    """(e) 账本 vs ``git for-each-ref``。

    账本是**某一瞬间的快照**，因此把三种情形分开，只有真缺陷判红：

    * ``lost``：账本里有、现在没了（且不在 ``refs/attic`` 备份里）——内容可能丢，**判红**；
    * ``advanced_in_flight``：两边都有、sha 变了但旧 sha 是新 sha 的祖先（在飞分支继续提交）——只记录；
    * ``rewritten``：两边都有、旧 sha 已不是新 sha 的祖先（amend/rebase/force-push）——证据失效，**判红**；
    * ``arrived_after_snapshot``：现在有、账本没有——快照之后新建的分支（平台持续建分支），
      属正常漂移，**只记录不判红**；git 不保存 ref 创建时间，故同时给出 tip 日期与 reflog 线索。
    """
    pattern = ledger.get("pattern") or "agent/*"
    if include_remote is None:
        include_remote = bool(ledger.get("include_remote"))
    refs = gitio.list_refs(repo, pattern, include_remote)
    live = {r["name"]: r["sha"] for r in refs}
    rows = {r.get("branch"): r.get("sha") for r in (ledger.get("rows") or [])}
    arrived = sorted(set(live) - set(rows))
    missing = sorted(set(rows) - set(live))
    lost, archived = [], []
    for name in missing:
        if gitio.ref_exists(repo, f"refs/attic/{name}") or gitio.ref_exists(repo, name):
            archived.append(name)
        else:
            reflog = gitio.run_git(repo, "reflog", "show", "--format=%H", f"refs/heads/{name}")[1]
            (archived if reflog.strip() else lost).append(name)
    advanced, rewritten = [], []
    for n in sorted(set(live) & set(rows)):
        if live[n] == rows[n]:
            continue
        # 在飞分支继续追加提交（旧 sha 是新 sha 的祖先）= 正常推进；
        # 旧 sha 不再被包含（amend/rebase/force-push）= 账本证据失效，判红。
        if rows[n] and gitio.is_ancestor(repo, rows[n], live[n]):
            advanced.append(n)
        else:
            rewritten.append(n)
    return {
        "repo_path": repo,
        "pattern": pattern,
        "git_refs": len(live),
        "ledger_rows": len(rows),
        "arrived_after_snapshot": arrived[:50],
        "arrived_count": len(arrived),
        "lost_ledger_rows": lost[:50],
        "archived_or_dropped_after_snapshot": archived[:50],
        "advanced_in_flight": advanced[:50],
        "rewritten": rewritten[:50],
        "symmetric_difference": len(arrived) + len(missing),
        "strict_equality": not arrived and not missing and not rewritten,
        "pass": not lost and not rewritten,
        "note": "snapshot semantics: arrivals after the snapshot are drift, not ledger defects; "
                "red only on lost/rewritten refs",
    }


# ── (b) 抽样独立复核 ──────────────────────────────────────────────────────────
def _fresh_class(repo: str, base: str, row: Dict[str, Any],
                 base_pids: Optional[set] = None) -> Dict[str, Any]:
    """用**另一条实现路径**（git cherry / ancestor / 合成 patch-id）重算单条分支的类别。

    与 ``classify`` 的 pidset 引擎独立：这里逐分支调 ``git cherry``，squash 判据则重算
    ``git diff merge-base..branch | git patch-id`` 并在 base 的 patch-id 集合里查。
    """
    br, sha = row["branch"], row["sha"]
    ev = row.get("evidence") or {}
    ancestor = gitio.is_ancestor(repo, sha, base)
    plus, minus = gitio.cherry_split(repo, base, br)
    net_empty = gitio.net_diff_empty(repo, base, br)
    combined = classify_mod.combined_patch_id(repo, ev.get("merge_base") or base, br)
    if base_pids is None:
        base_pids = classify_mod.base_patch_ids(repo, base) or set()
    squash_hit = bool(combined and combined in base_pids)

    if ancestor:
        derived, why = CLASS_D, "tip-ancestor-of-base"
    elif not plus:
        derived, why = CLASS_A, "cherry-no-unique-commits"
    elif net_empty:
        derived, why = CLASS_A, "net-diff-empty"
    elif squash_hit:
        derived, why = CLASS_A, "squash-combined-patch-in-base"
    else:
        derived, why = CLASS_C, "unique-patch-ids-not-in-base"
    sup = row.get("superset_source")
    consistent = derived == row["class"]
    note = ""
    if row["class"] == CLASS_B:
        # B 需看全局超集：checks 局部只能判"未在 base"，故放宽为 C 族一致 + 超集来源可解析
        consistent = derived in (CLASS_B, CLASS_C) and bool(sup) and \
            gitio.ref_exists(repo, sup) is not None
        note = "B-relaxed-to-C-family+superset-resolvable"
    elif row["class"] == CLASS_A and derived == CLASS_C:
        note = "A-vs-C-mismatch"
    return {
        "branch": br,
        "ledger_class": row["class"],
        "fresh_class": derived,
        "rule": why,
        "cherry_unique": len(plus),
        "net_diff_empty": net_empty,
        "squash_hit": squash_hit,
        "superset_source": sup,
        "consistent": bool(consistent),
        "note": note,
    }


def sample_audit(repo: str, ledger: Dict[str, Any], base: str, sample: int = 30,
                 seed: str = "delivery-hygiene-audit-v1") -> Dict[str, Any]:
    """分层抽样（每类至少 1 条，其余按类占比确定性伪随机），逐条独立重算。"""
    rows = ledger.get("rows") or []
    if not rows:
        return {"sample": 0, "agree": 0, "agreement": None, "pass": False,
                "items": [], "note": "empty-ledger"}
    by_class: Dict[str, List[Dict[str, Any]]] = {c: [] for c in CLASSES}
    for r in rows:
        if r.get("class") in by_class:
            by_class[r["class"]].append(r)

    def key(r: Dict[str, Any]) -> str:
        return hashlib.sha256((seed + "|" + str(r["branch"])).encode()).hexdigest()

    picked: List[Dict[str, Any]] = []
    for c in CLASSES:
        group = sorted(by_class[c], key=key)
        if group:
            picked.append(group[0])
    rest = [r for r in rows if r not in picked]
    rest.sort(key=key)
    for r in rest:
        if len(picked) >= sample:
            break
        picked.append(r)
    picked = picked[:max(sample, len([c for c in CLASSES if by_class[c]]))]

    base_pids = classify_mod.base_patch_ids(repo, base) or set()
    items = [_fresh_class(repo, base, r, base_pids=base_pids) for r in picked]
    agree = sum(1 for i in items if i["consistent"])
    return {
        "sample": len(items),
        "agree": agree,
        "disagree": len(items) - agree,
        "agreement": agree / len(items) if items else None,
        "stratified_by_class": {c: len(by_class[c]) for c in CLASSES},
        "items": items,
        "pass": bool(items) and (agree / len(items)) >= 0.95,
    }


# ── (c) 积压收敛与 C 类零丢失 ────────────────────────────────────────────────
def measure_backlog(ledger: Dict[str, Any], target_unmerged: Optional[int] = None) -> Dict[str, Any]:
    stats = ledger.get("stats") or {}
    by = dict(stats.get("by_class") or {})
    plan = ledger.get("disposition_plan") or ledger.get("dispositions") or []
    counts = ledger.get("disposition_counts") or {}
    archive_classes: Dict[str, int] = {}
    for item in plan:
        if item.get("archive_ref") or "archive_ref" in (item.get("actions") or []):
            cls = item.get("class")
            archive_classes[cls] = archive_classes.get(cls, 0) + 1
    unmerged_before = int(stats.get("unmerged", by.get(CLASS_B, 0) + by.get(CLASS_C, 0)))
    # 归档只对 B 类产生"未合并下降"（A/D 本就不在 unmerged 里；C 永不归档）
    projected = unmerged_before - int(archive_classes.get(CLASS_B, 0))

    tickets = ledger.get("tickets") or []
    c_rows = [r for r in (ledger.get("rows") or []) if r.get("class") == CLASS_C]
    c_branches = {r["branch"] for r in c_rows}
    ticket_branches = {t.get("branch") for t in tickets if t.get("branch")}
    missing_tickets = sorted(c_branches - ticket_branches)
    extra_tickets = sorted(ticket_branches - c_branches)
    def _sha(t: Dict[str, Any]) -> Any:
        return t.get("branch_tip") or t.get("sha")

    def _uniq(t: Dict[str, Any]) -> Any:
        return t.get("unique_commit_shas") or t.get("unique_shas")

    ticket_sha_missing = [t.get("branch") for t in tickets
                          if not _sha(t) or not _uniq(t)]
    zero_loss = not missing_tickets and not extra_tickets and not ticket_sha_missing
    out = {
        "unmerged_before": unmerged_before,
        "by_class": by,
        "archive_by_class": archive_classes,
        "unmerged_projected_after_closure": projected,
        "c_branches": len(c_branches),
        "tickets": len(ticket_branches),
        "c_without_ticket": missing_tickets[:20],
        "ticket_without_c": extra_tickets[:20],
        "ticket_missing_sha": ticket_sha_missing[:20],
        "c_zero_loss": zero_loss,
        "target_unmerged": target_unmerged,
    }
    if target_unmerged is None:
        out["target_met"] = None
    else:
        out["target_met"] = projected < int(target_unmerged)
        out["gap_to_target"] = projected - int(target_unmerged)
    out["pass"] = zero_loss and (out["target_met"] in (True, None))
    return out


def probe_undelivered_content(repo: str, ledger: Dict[str, Any], n: int = 100) -> Dict[str, Any]:
    """抽 n 条 C 类分支，检查它们引入的 blob 是否其实是"旧酒"（已被 base 或其他分支覆盖）。

    若命中率≈0，说明 C 类是真未交付内容，只减不增门只能靠**交付**收敛，
    删分支/归档都动不了数字——这条探针就是"目标不可达"的证据。
    """
    base = ledger.get("base") or "main"
    rows = [r for r in (ledger.get("rows") or []) if r.get("class") == CLASS_C][:max(1, n)]
    if not rows:
        return {"sampled": 0, "tip_covered": 0, "superset_contained": 0, "pass": None}
    main_blobs: Dict[str, str] = {}
    rc, out = gitio.run_git(repo, "ls-tree", "-r", "-z", base)
    for chunk in (out.split("\0") if rc == 0 else []):
        if not chunk:
            continue
        meta, path = chunk.split("\t", 1)
        main_blobs[path] = meta.split()[2]
    intro: Dict[str, Tuple[Tuple[str, str], ...]] = {}
    for r in rows:
        mb = (r.get("evidence") or {}).get("merge_base") or base
        rc, raw = gitio.run_git(repo, "diff", "--raw", "--no-renames", mb, r["sha"])
        pairs = []
        for ln in raw.splitlines():
            parts = ln.split()
            if len(parts) < 6 or parts[4] == "D":
                continue
            pairs.append((parts[5], parts[3]))
        intro[r["branch"]] = tuple(pairs)
    tip_covered = sum(1 for ps in intro.values() if ps and all(main_blobs.get(p) == s for p, s in ps))
    blobs = {br: frozenset(s for _, s in ps) for br, ps in intro.items()}
    inv: Dict[str, set] = {}
    for br, bs in blobs.items():
        for b in bs:
            inv.setdefault(b, set()).add(br)
    superset = 0
    for br, bs in blobs.items():
        if not bs:
            continue
        cand: Dict[str, int] = {}
        for b in bs:
            for other in inv[b]:
                if other != br:
                    cand[other] = cand.get(other, 0) + 1
        if any(c == len(bs) and len(blobs[o]) > len(bs) for o, c in cand.items()):
            superset += 1
    sizes = sorted(len(bs) for bs in blobs.values())
    return {
        "sampled": len(intro),
        "introduced_blobs_median": sizes[len(sizes) // 2] if sizes else 0,
        "introduced_blobs_max": sizes[-1] if sizes else 0,
        "tip_covered": tip_covered,
        "superset_contained": superset,
        "tip_covered_ratio": tip_covered / len(intro) if intro else 0.0,
        "superset_ratio": superset / len(intro) if intro else 0.0,
        "verdict": ("undelivered-content-is-real" if (tip_covered + superset) == 0
                    else "closure-may-recover-content"),
    }


# ── (d) 门禁负控 ─────────────────────────────────────────────────────────────
def _synth_repo(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    gitio.run_git(path, "init", "-q", "-b", "main")
    gitio.run_git(path, "config", "user.email", "hygiene@example.invalid")
    gitio.run_git(path, "config", "user.name", "hygiene-probe")
    return path


def _commit(repo: str, fname: str, text: str, when: Optional[datetime] = None,
            msg: Optional[str] = None) -> None:
    p = os.path.join(repo, fname)
    os.makedirs(os.path.dirname(p) or repo, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    gitio.run_git(repo, "add", "-A")
    env = {}
    if when is not None:
        iso = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env = {"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", msg or f"add {fname}"],
                   env={**os.environ, **env}, check=True, capture_output=True)


def gate_negative_control(injected: int = 3, pattern: str = "agent/*") -> Dict[str, Any]:
    """在临时合成仓库里做负控：冻结基线后注入 N 条全新未交付分支，门必须变红并全部点名。

    这是"只减不增门"的**证伪测试**：门若在注入后仍绿，说明门是纸门。
    """
    tmp = tempfile.mkdtemp(prefix="dh-negctl-")
    try:
        repo = _synth_repo(os.path.join(tmp, "repo"))
        _commit(repo, "README.md", "base\n")
        # 一条 A 类（同一处改动在 main 里已落地：补丁等价）
        gitio.run_git(repo, "checkout", "-q", "-b", "agent/already-landed")
        _commit(repo, "already/merged.txt", "landed\n")
        gitio.run_git(repo, "checkout", "-q", "main")
        _commit(repo, "already/merged.txt", "landed\n")
        ledger = _scan(repo, "main", pattern)
        baseline = gate_mod.freeze_baseline(ledger, None)
        before = gate_mod.evaluate_gate(ledger, baseline, strict_new=True)

        names = [f"agent/injected-{i:02d}" for i in range(1, injected + 1)]
        when = datetime.now(timezone.utc) + timedelta(seconds=120)
        for i, name in enumerate(names, start=1):
            gitio.run_git(repo, "checkout", "-q", "-b", name, "main")
            _commit(repo, f"new/payload_{i}.txt", f"payload {i}\n", when=when + timedelta(seconds=i))
        gitio.run_git(repo, "checkout", "-q", "main")

        after_ledger = _scan(repo, "main", pattern)
        after = gate_mod.evaluate_gate(after_ledger, baseline, strict_new=True)
        detected = set(after.get("new_undelivered") or []) & set(names)
        red_violations = [v["code"] for v in (after.get("violations") or [])
                          if v.get("severity", "error") == "error"]
        return {
            "injected": len(names),
            "injected_branches": names,
            "gate_before": {"pass": before["pass"], "exit_code": before["exit_code"],
                            "unmerged": before["current_unmerged"]},
            "gate_after": {"pass": after["pass"], "exit_code": after["exit_code"],
                          "unmerged": after["current_unmerged"],
                          "violations": red_violations,
                          "new_undelivered": after.get("new_undelivered")},
            "detected": sorted(detected),
            "detection_rate": len(detected) / len(names) if names else 0.0,
            "went_red": (not after["pass"]) and after["exit_code"] != 0,
            "pass": (not after["pass"]) and after["exit_code"] != 0
                    and len(detected) == len(names) and before["pass"],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 扫描复用（负控里用） ──────────────────────────────────────────────────────
def _scan(repo: str, base: str, pattern: str, with_files: bool = False) -> Dict[str, Any]:
    refs = gitio.list_refs(repo, pattern, False)
    facts = classify_mod.classify(repo, base, refs, with_files=with_files)
    label = gitio.repo_label(repo)
    rows = [ledger_mod.build_row(label, f) for f in facts]
    led = ledger_mod.build_ledger(repo, label, base, pattern, rows, include_remote=False)
    led["content_sha256"] = content_sha256(led)
    return led


# ── 主流程 ───────────────────────────────────────────────────────────────────
def measure(repo: str, ledger: Dict[str, Any], sample: int = 30,
            target_unmerged: Optional[int] = None,
            negative_control: bool = True, probe_c: int = 0) -> Dict[str, Any]:
    base = ledger.get("base") or "main"
    a = measure_coverage(ledger)
    b = sample_audit(repo, ledger, base, sample=sample)
    c = measure_backlog(ledger, target_unmerged=target_unmerged)
    if probe_c:
        c["probe"] = probe_undelivered_content(repo, ledger, n=probe_c)
    d = gate_negative_control() if negative_control else {"pass": None, "skipped": True}
    e = measure_ref_diff(repo, ledger)
    crit = {"a_coverage": a, "b_sample_audit": b, "c_backlog": c,
            "d_gate_negative_control": d, "e_ref_set": e}
    verdicts = {k: v.get("pass") for k, v in crit.items()}
    return {
        "schema_version": MATURITY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": ledger.get("repo"),
        "repo_path": os.path.abspath(repo),
        "base": base,
        "base_sha": ledger.get("base_sha"),
        "ledger_content_sha256": content_sha256(ledger),
        "criteria": crit,
        "verdicts": verdicts,
        "hard_pass": all(v is True for v in verdicts.values()),
        "honest_notes": [],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="delivery_hygiene.maturity",
                                 description="五条机器判据的成熟度自证（只读 git + 临时合成仓库负控）")
    ap.add_argument("--repo", default=".", help="仓库路径（账本对应的仓）")
    ap.add_argument("--ledger", required=True, help="scan 产出的 ledger.json（支持 .gz）")
    ap.add_argument("--out", default="artifacts/delivery_hygiene/maturity", help="输出目录")
    ap.add_argument("--sample", type=int, default=30, help="独立复核抽样条数")
    ap.add_argument("--target-unmerged", type=int, default=None,
                    help="节点目标（未合并积压上限）；给了就判 target_met")
    ap.add_argument("--no-negative-control", action="store_true")
    ap.add_argument("--probe-c", type=int, default=0,
                    help="抽 N 条 C 类做内容探针（证明积压是真内容、闭包不可收敛）")
    ap.add_argument("--label", default=None, help="输出文件名标签（多仓时区分）")
    args = ap.parse_args(argv)

    from .cli import read_json_maybe_gz
    led = read_json_maybe_gz(args.ledger)
    rep = measure(args.repo, led, sample=args.sample,
                  target_unmerged=args.target_unmerged,
                  negative_control=not args.no_negative_control,
                  probe_c=args.probe_c)
    tag = args.label or rep["repo"] or "repo"
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"maturity_{tag}.json")
    ledger_mod.write_json(path, rep)
    c = rep["criteria"]
    print(f"[maturity] {rep['repo']}: " + " ".join(
        f"{k}={'PASS' if v else ('SKIP' if v is None else 'FAIL')}"
        for k, v in rep["verdicts"].items()))
    print(f"[maturity] coverage={c['a_coverage']['coverage']:.3f} "
          f"audit={c['b_sample_audit']['agree']}/{c['b_sample_audit']['sample']} "
          f"unmerged={c['c_backlog']['unmerged_before']}->"
          f"{c['c_backlog']['unmerged_projected_after_closure']} "
          f"C={c['c_backlog']['c_branches']} zero_loss={c['c_backlog']['c_zero_loss']} "
          f"negctl={c['d_gate_negative_control'].get('detection_rate')} "
          f"refdiff={c['e_ref_set']['symmetric_difference']} -> {path}")
    return 0 if rep["hard_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())