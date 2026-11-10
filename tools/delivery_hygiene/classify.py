"""四类内容等价分类器：A（main 已覆盖）/ B（被另一分支超集覆盖）/ C（真未交付）/ D（孤儿）。

判据（每条都可被反例推翻，故每条都在账本里带 ``evidence``）
----------------------------------------------------------
前置量（对分支 ``b``、主干 ``base``）：

* ``ahead  = len(git rev-list base..b)``（0 ⇔ ``b`` 的 tip 已在 base 历史内）
* ``unique = {c ∈ base..b（非合并） : patch-id(c) ∉ patch-ids(base)}``
* ``net_diff_empty = git diff --quiet base b``

判定顺序（**优先级 D > A > B > C**，四类互斥）：

1. **D 孤儿**：``ahead == 0``。分支 ref 只是过期指针，删掉不丢任何内容。
   ``rule:D-tip-in-base-history/v1``。
2. **A 已覆盖**：``ahead > 0`` 且（``unique`` 为空 或 ``net_diff_empty``）。
   - ``unique`` 为空：所有提交的 patch-id 都命中 base（rebase/cherry-pick/squash 残壳）⇒
     ``rule:A-patch-id-all-in-base/v1``；
   - ``net_diff_empty``：patch-id 不命中但净内容为空（改了又改回）⇒ ``rule:A-net-diff-empty/v1``。
3. **B 超集**：``unique`` 非空且其 patch-id 集合 ⊆ 另一条分支的独有 patch-id 集合。
   候选里取「额外提交数最少 → 名字字典序最小」。两分支集合**相等**（重复 cherry-pick 的孪生）时，
   按「tip 时间最新 → 名字字典序最小」选唯一 canonical，其余判 B，``superset_source`` 指向 canonical。
   ``rule:B-patch-id-subset-of-superset/v1``。
4. **C 未交付**：以上都不成立 ⇒ 主干没有、别分支也没有的独有补丁，必须保留 + 开补单。
   ``rule:C-unique-patch-ids/v1``。

两个引擎（性能 vs 交叉校验）
--------------------------
* ``pidset``（默认）：一次算好 base 的 patch-id 集合 + 一次性批量算好所有候选提交的 patch-id，
  再本地做集合运算。为什么：``git cherry`` 每条分支都要重算 base 侧 patch-id，实测本仓
  2173 条分支需 ~11 min；pidset 引擎把同一信息摊到一次全局计算，实测降到 ~1 min。
* ``cherry``：``git cherry`` 逐分支（与 ``tools/branch_ledger.py`` 完全同口径）。

两引擎判据等价（都是「patch-id 是否在 base」），但实现路径不同，所以
``classify(..., crosscheck=N)`` 会抽样 N 条分支用 cherry 复算并把一致率写进账本
（``stats.engine_crosscheck``）——这是可审计的自证，不是自说自话。

为什么 B 类必须单独成类
----------------------
``tools/branch_ledger.py`` 只分到 ``UNIQUE`` 一层；实测集里大量 ``UNIQUE`` 是「同一处改动被
重复 cherry-pick/rebase 成多条分支」。把它们记成「真未交付」会同时导致两件坏事：补单池被灌水，
门永远达不到只减不增。B 类让「内容守恒」与「只减不增」同时成立：分支可以关，因为内容在 S 里还有一份。

空提交（无 diff 的提交）不参与独有判定（记账在 ``evidence.empty_commits``）：它不携带内容，
把它算成「未交付」会让分支永远闭不掉。这一点比 ``git cherry`` 更严于内容守恒、更宽于计数。

安全可关（``safe_to_close``）
----------------------------
* D/A：内容在 base 或在历史里 ⇒ 可关。
* B：沿 ``superset_source`` 链走到被保留的终点（C 类，或 A/D）⇒ 可关；链上有环 ⇒ 不可关。
* C：不可关。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import CLASS_A, CLASS_B, CLASS_C, CLASS_D, CLASS_LABELS
from . import gitio

DECIDED_D = "rule:D-tip-in-base-history/v1"
DECIDED_A_PATCH = "rule:A-patch-id-all-in-base/v1"
DECIDED_A_DIFF = "rule:A-net-diff-empty/v1"
DECIDED_A_SQUASH = "rule:A-squash-combined-patch/v1"
DECIDED_B = "rule:B-patch-id-subset-of-superset/v1"
DECIDED_C = "rule:C-unique-patch-ids/v1"

ENGINE_PIDSET = "pidset"
ENGINE_CHERRY = "cherry"


def parse_mission_id(branch_name: str) -> Optional[int]:
    """从分支名里取 mission id（``agent/m1875`` → 1875）。拿不到返回 None。"""
    m = re.search(r"(?:^|[/_-])m(\d+)(?:$|[/_-])", branch_name)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:^|[/_-])m(\d+)", branch_name)
    return int(m.group(1)) if m else None


def _rev_list_parents(repo: str, rng: str) -> List[Tuple[str, int]]:
    """``(sha, 父提交数)`` 列表：一次调用同时给出 ahead（长度）与非合并提交（父数 ≤ 1）。"""
    rc, out = gitio.run_git(repo, "rev-list", "--parents", rng)
    if rc != 0:
        return []
    rows: List[Tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            rows.append((parts[0], max(len(parts) - 1, 0)))
    return rows


def base_patch_ids(repo: str, base: str) -> Optional[set]:
    """base 全部非合并提交的 patch-id 集合；算不出来返回 None（调用方降级到 cherry 引擎）。"""
    import subprocess
    try:
        p1 = subprocess.run(["git", "-C", repo, "log", "--no-merges", "-p", base],
                            capture_output=True, timeout=gitio.GIT_TIMEOUT)
        if p1.returncode != 0:
            return None
        p2 = subprocess.run(["git", "-C", repo, "patch-id", "--stable"], input=p1.stdout,
                            capture_output=True, timeout=gitio.GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p2.returncode != 0:
        return None
    return {ln.split()[0] for ln in p2.stdout.decode("utf-8", "replace").splitlines() if ln.split()}


def combined_patch_id(repo: str, from_ref: str, to_ref: str) -> Optional[str]:
    """``merge-base..branch`` 的合成 diff 的 patch-id（squash 合并后在 base 侧留下的指纹）。"""
    import subprocess
    try:
        p1 = subprocess.run(["git", "-C", repo, "diff", from_ref, to_ref],
                            capture_output=True, timeout=gitio.GIT_TIMEOUT)
        if p1.returncode != 0 or not p1.stdout.strip():
            return None
        p2 = subprocess.run(["git", "-C", repo, "patch-id", "--stable"], input=p1.stdout,
                            capture_output=True, timeout=gitio.GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in p2.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if parts:
            return parts[0]
    return None


def collect_facts(repo: str, base: str, refs: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    """阶段 1：逐分支取提交清单（纯读，一次 ``rev-list --parents``/分支）。"""
    facts: List[Dict[str, Any]] = []
    for ref in refs:
        name = ref["name"]
        # 钉住采证时的 tip（ref["sha"]）：仓库是活的，别的 worker 会推进同名分支；
        # 若按分支名逐条查，ahead / patch-id / diff 可能来自不同时刻（实测踩到过：
        # 扫完到复核之间 agent/m9467 被推进了一个提交，audit 立刻报不一致）
        rows = _rev_list_parents(repo, f"{base}..{ref['sha']}")
        facts.append({
            "branch": name,
            "kind": ref.get("kind", "local"),
            "sha": ref["sha"],
            "tip_date": ref.get("date", ""),
            "mission_id": parse_mission_id(name),
            "ahead": len(rows),
            "candidate_shas": [s for s, np in rows if np <= 1],
            "unique_shas": [],
            "unique_patch_ids": [],
            "equivalent_count": 0,
            "empty_commits": 0,
            "net_diff_empty": None,
            "tip_in_base_history": len(rows) == 0,
            "merge_base": None,
            "changed_files": [],
            "changed_files_total": 0,
        })
    return facts


def attach_patch_ids(repo: str, facts: Sequence[Dict[str, Any]], base: str,
                     engine: str = ENGINE_PIDSET) -> Tuple[str, Optional[set]]:
    """阶段 2：批量算 patch-id → 定 ``unique_shas`` / ``equivalent_count``；返回实际用的引擎名。"""
    if engine == ENGINE_CHERRY:
        for f in facts:
            if f["ahead"] == 0:
                continue
            uniq, equiv = gitio.cherry_split(repo, base, f["sha"])
            full = gitio.resolve_short(repo, uniq)
            f["unique_shas"] = [full.get(s, s) for s in uniq]
            f["equivalent_count"] = len(equiv)
        _fill_pids(repo, facts)
        return ENGINE_CHERRY, base_patch_ids(repo, base)

    base_pids = base_patch_ids(repo, base)
    if base_pids is None:      # 降级：base 侧 patch-id 算不出来就走 git cherry
        return attach_patch_ids(repo, facts, base, engine=ENGINE_CHERRY)
    all_shas: List[str] = []
    for f in facts:
        all_shas.extend(f["candidate_shas"])
    pid_map = gitio.patch_id_map(repo, sorted(set(all_shas)))
    for f in facts:
        uniq: List[str] = []
        equiv = 0
        empty = 0
        for sha in f["candidate_shas"]:
            pid = pid_map.get(sha)
            if pid is None:
                empty += 1                 # 无 diff 的提交：不携带内容，不计独有
            elif pid in base_pids:
                equiv += 1
            else:
                uniq.append(sha)
        f["unique_shas"] = uniq
        f["equivalent_count"] = equiv
        f["empty_commits"] = empty
    _fill_pids(repo, facts, pid_map)
    return ENGINE_PIDSET, base_pids


def _fill_pids(repo: str, facts: Sequence[Dict[str, Any]],
               pid_map: Optional[Dict[str, str]] = None) -> None:
    """给有独有提交的分支补 ``unique_patch_ids``（去重保序）。"""
    if pid_map is None:
        shas = [s for f in facts for s in f["unique_shas"]]
        pid_map = gitio.patch_id_map(repo, sorted(set(shas)))
    for f in facts:
        pids: List[str] = []
        for sha in f["unique_shas"]:
            pid = pid_map.get(sha)
            if pid and pid not in pids:
                pids.append(pid)
        f["unique_patch_ids"] = pids
        # 提交里没有 patch-id（空提交）⇒ 独有清单与 pid 清单长度可能不等，此处不臆造 pid


def enrich_content(repo: str, base: str, facts: Sequence[Dict[str, Any]],
                   with_files: bool = True, base_pids: Optional[set] = None,
                   squash_detect: bool = True) -> None:
    """阶段 3：只对「有独有提交」的分支算净 diff / 改动文件 / squash 合成 patch-id。

    所有查询都钉在这一行采证时记录的 ``sha`` 上（不是分支名）：仓库是活的，同名分支会在
    扫描途中被别的 worker 推进，按名字复查会得到自相矛盾的证据（实测踩过，见模块文档）。
    """
    for f in facts:
        if not f["unique_shas"]:
            continue
        f["merge_base"] = gitio.merge_base(repo, base, f["sha"])
        f["net_diff_empty"] = gitio.net_diff_empty(repo, base, f["sha"])
        if with_files and f["merge_base"]:
            files = gitio.changed_files(repo, f["merge_base"], f["sha"])
            f["changed_files"] = files
            f["changed_files_total"] = len(files)
        f["squash_patch_in_base"] = None
        if squash_detect and base_pids and f["net_diff_empty"] is False and f["merge_base"]:
            cpid = combined_patch_id(repo, f["merge_base"], f["sha"])
            f["squash_patch_in_base"] = bool(cpid and cpid in base_pids)
            f["combined_patch_id"] = cpid


def _choose_canonical(group: Sequence[str], by_branch: Dict[str, Dict[str, Any]]) -> str:
    """等价集里选 canonical：tip 时间最新，其次名字字典序最小（确定性，保证可复算）。"""
    newest = max(by_branch[b]["tip_date"] or "" for b in group)
    tied = [b for b in group if (by_branch[b]["tip_date"] or "") == newest]
    return sorted(tied or list(group))[0]


def classify_facts(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 facts 定成 A/B/C/D，并解析 ``safe_to_close`` 与 ``superset_source``（纯函数，可测）。"""
    by_branch = {f["branch"]: f for f in facts}

    # 第一轮：D / A（与全局信息无关）
    pending: List[str] = []
    for f in facts:
        if f["tip_in_base_history"]:
            f["class"], f["decided_by"] = CLASS_D, DECIDED_D
        elif not f["unique_patch_ids"] and not f["unique_shas"]:
            f["class"], f["decided_by"] = CLASS_A, DECIDED_A_PATCH
        elif f["net_diff_empty"]:
            f["class"], f["decided_by"] = CLASS_A, DECIDED_A_DIFF
        elif f.get("squash_patch_in_base"):
            f["class"], f["decided_by"] = CLASS_A, DECIDED_A_SQUASH
        else:
            pending.append(f["branch"])

    # 第二轮：B（patch-id 集合包含关系）
    #
    # 关系是**偏序**：等集（重复 cherry-pick 的孪生）与严格超集要分开处理，否则会漏判——
    # 一度出现过「等集簇的 canonical 本身还有严格超集，却因自指被判 C」的情形
    # （实测 agent/m1938-ship：与 10 条分支等集且 {p} ⊂ {p,q}，归档掉等集成员后才变 B）。
    # 现在按偏序取极大元：
    #   1. strict_sup[b] = 最优严格超集（额外提交最少 → 名字字典序）；
    #   2. 极大元（无严格超集）里按等集分组，每组留一个 canonical（C），其余 B 指向它；
    #   3. 非极大元沿 strict_sup 链走到极大元作为 superset_source（链长必有限：集合真包含）。
    pid_index: Dict[str, List[str]] = {}
    for b in pending:
        for pid in by_branch[b]["unique_patch_ids"]:
            pid_index.setdefault(pid, []).append(b)

    strict_sup: Dict[str, str] = {}
    for b in pending:
        pids = set(by_branch[b]["unique_patch_ids"])
        if not pids:
            continue
        rarest = min(pids, key=lambda p: (len(pid_index.get(p, [])), p))
        best: Optional[Tuple[int, str]] = None
        for c in pid_index.get(rarest, []):
            if c == b:
                continue
            cpids = set(by_branch[c]["unique_patch_ids"])
            if not pids < cpids:            # 严格包含：等集不参与这一层
                continue
            key = (len(cpids - pids), c)
            if best is None or key < best:
                best = key
        if best is not None:
            strict_sup[b] = best[1]

    maximal = [b for b in pending if b not in strict_sup]
    by_set: Dict[frozenset, List[str]] = {}
    for b in maximal:
        by_set.setdefault(frozenset(by_branch[b]["unique_patch_ids"]), []).append(b)
    canonical_of: Dict[str, str] = {}
    for members in by_set.values():
        canon = _choose_canonical(members, by_branch)
        for m in members:
            canonical_of[m] = canon

    superset_of: Dict[str, str] = {}
    for b in pending:
        target = _terminal_strict_superset(b, strict_sup)
        if target is not None:
            superset_of[b] = target
        elif canonical_of.get(b, b) != b:
            superset_of[b] = canonical_of[b]

    for f in facts:
        if f.get("class"):
            continue
        src = superset_of.get(f["branch"])
        if src and src != f["branch"]:
            f["class"], f["decided_by"] = CLASS_B, DECIDED_B
            f["superset_source"] = src
            f["superset_extra_commits"] = (len(by_branch[src]["unique_patch_ids"])
                                           - len(f["unique_patch_ids"]))
        else:
            f["class"], f["decided_by"] = CLASS_C, DECIDED_C
            f.pop("superset_source", None)

    # 第三轮：safe_to_close（沿 superset 链解析留存终点）
    for f in facts:
        f["class_label"] = CLASS_LABELS[f["class"]]
        f["safe_to_close"], f["retention_owner"] = _resolve_retention(f, by_branch)
    return list(facts)


def _terminal_strict_superset(branch: str, strict_sup: Dict[str, str]) -> Optional[str]:
    """沿严格超集链走到极大元（终点 = 没有严格超集的分支，即内容留存源）。

    链长必有限（每步集合真包含），所以不需要防环；但仍加深度保险，防止数据异常时死循环。
    """
    cur = strict_sup.get(branch)
    depth = 0
    while cur and cur in strict_sup and depth < 10000:
        cur = strict_sup[cur]
        depth += 1
    return cur


def _resolve_retention(fact: Dict[str, Any],
                       by_branch: Dict[str, Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """返回 (safe_to_close, 内容留在哪)。B 类需链式解析并在环上收敛。"""
    cls = fact["class"]
    if cls in (CLASS_A, CLASS_D):
        return True, "base"
    if cls == CLASS_C:
        return False, fact["branch"]
    seen = {fact["branch"]}
    cur = fact.get("superset_source")
    while cur:
        if cur in seen:
            return False, None          # 环：无法证明内容在别处留存
        seen.add(cur)
        node = by_branch.get(cur)
        if node is None:
            return False, None
        if node["class"] in (CLASS_A, CLASS_D):
            return True, "base"
        if node["class"] == CLASS_C:
            return True, cur
        cur = node.get("superset_source")
    return False, None


# ── 引擎交叉校验（可审计自证） ────────────────────────────────────────────────
def crosscheck(repo: str, base: str, refs: Sequence[Dict[str, str]], sample: int,
               with_files: bool = False) -> Dict[str, Any]:
    """抽样用 ``git cherry`` 复算分类，与 pidset 引擎比对（一致率写进账本）。"""
    if sample <= 0 or not refs:
        return {"sample": 0, "agree": 0, "disagree": 0, "agreement": None, "disagreements": []}
    step = max(len(refs) // sample, 1)
    picked = list(refs)[::step][:sample]
    a = {f["branch"]: f["class"] for f in classify(repo, base, picked, with_files=with_files,
                                                   engine=ENGINE_PIDSET, crosscheck=0)}
    b = {f["branch"]: f["class"] for f in classify(repo, base, picked, with_files=with_files,
                                                   engine=ENGINE_CHERRY, crosscheck=0)}
    dis = [{"branch": k, "pidset": a.get(k), "cherry": b.get(k)}
           for k in sorted(a) if a.get(k) != b.get(k)]
    agree = len(a) - len(dis)
    return {
        "sample": len(a),
        "agree": agree,
        "disagree": len(dis),
        "agreement": (agree / len(a)) if a else None,
        "disagreements": dis[:20],
        "note": "B 类依赖分支集合，抽样比对时以同一抽样子集为上下文，故差异只会来自 patch-id 判定本身",
    }


def classify(repo: str, base: str, refs: Sequence[Dict[str, str]],
             with_files: bool = True, engine: str = ENGINE_PIDSET,
             crosscheck: int = 0, squash_detect: bool = True) -> List[Dict[str, Any]]:
    """完整分类：采证 → 批量 patch-id → 内容丰富化（净 diff / 改动文件 / squash 指纹）→ 定类。"""
    if not gitio.rev_parse(repo, base):
        raise SystemExit(f"[delivery_hygiene] base ref not found: {base} (repo={repo})")
    facts = collect_facts(repo, base, refs)
    used, base_pids = attach_patch_ids(repo, facts, base, engine=engine)
    enrich_content(repo, base, facts, with_files=with_files, base_pids=base_pids,
                   squash_detect=squash_detect)
    out = classify_facts(facts)
    for f in out:
        f["engine"] = used
    return out