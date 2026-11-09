"""repro_audit.scan_select —— 分层扫描 + 多策略选择（零领域语义）。

在 :mod:`core.sample_stratified` 基础上扩展：
  * **扫描**：遍历所有 done 节点，按结构化条件筛候选（有工件、有测试、有判据）。
  * **选择**：支持多种策略（stratified / priority / coverage / random），
    每种策略都是确定性纯函数（同输入同输出）。
  * **覆盖度**：计算选中集合对仓/年/判据类别的覆盖度，供报告模块消费。

设计约束：
  * **零领域语义**：仓名、学科词全部来自节点元数据，本模块不硬编码。
  * **幂等**：所有策略接受 seed 参数，同 (nodes, n, seed) 同输出。
  * **可组合**：策略可链式调用（先 coverage 再 stratified 补齐）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from . import canonical

# 测试文件识别（结构性正则，与 cli._TEST_HINT_RE 对齐）
_TEST_HINT_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$")


def is_test_file(path: str) -> bool:
    return bool(_TEST_HINT_RE.search(path)) and path.endswith(".py")


def has_executable_artifacts(node: dict[str, Any], committed_files: list[str]) -> bool:
    """节点是否有可执行工件：至少 1 个测试文件 + 至少 1 个判据。"""
    tests = [f for f in committed_files if is_test_file(f)]
    if not tests:
        return False
    meta = node.get("metadata") or {}
    criteria = meta.get("acceptance_criteria") or []
    return len(criteria) > 0


def scan_candidates(nodes: list[dict[str, Any]],
                    files_by_mission: dict[int, list[str]] | None = None
                    ) -> list[dict[str, Any]]:
    """扫描所有 done 节点，返回带可执行工件的候选列表。

    ``files_by_mission`` 可选：若提供，用它判断测试文件存在性；否则只按元数据筛。
    """
    candidates = []
    for node in nodes:
        if str(node.get("status")) != "done":
            continue
        mid = canonical.normalize_mission_id(node.get("mission_id"))
        if mid is None:
            continue
        meta = node.get("metadata") or {}
        criteria = meta.get("acceptance_criteria") or []
        if not criteria:
            continue

        # 如果有文件列表，检查测试文件存在性
        if files_by_mission is not None:
            files = files_by_mission.get(mid, [])
            if not any(is_test_file(f) for f in files):
                continue
            n_tests = sum(1 for f in files if is_test_file(f))
        else:
            n_tests = 0

        canon = canonical.canonicalize_node(node)
        candidates.append({
            "node": node,
            "canonical": canon,
            "mission_id": mid,
            "repo": meta.get("repo", "unknown"),
            "n_criteria": len(criteria),
            "n_test_files": n_tests,
        })

    return candidates


# ─────────────────────────── 选择策略 ───────────────────────────

def _deterministic_sort_key(item: dict[str, Any], seed: str) -> str:
    raw = f"{seed}|{item.get('mission_id')}|{item.get('repo')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def select_stratified(candidates: list[dict[str, Any]], n: int, seed: str = "repro-audit",
                      per_stratum_cap: int = 3) -> list[dict[str, Any]]:
    """分层选择：repo × 年 分层，层内按 sha256 定序，轮转取满 n。

    与 core.sample_stratified 逻辑一致，但操作候选对象（含 canonical 字段）。
    """
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for cand in candidates:
        repo = cand.get("repo", "unknown")
        year = str(cand["node"].get("actual_end") or cand["node"].get("created_at") or "")[:4] or "unknown"
        strata.setdefault((repo, year), []).append(cand)

    for key in strata:
        strata[key].sort(key=lambda c: (_deterministic_sort_key(c, seed), c.get("mission_id")))

    order = sorted(strata)
    picked: list[dict[str, Any]] = []
    round_idx = 0
    while len(picked) < n and round_idx < per_stratum_cap:
        before = len(picked)
        for key in order:
            if len(picked) >= n:
                break
            bucket = strata[key]
            if round_idx < len(bucket):
                picked.append(bucket[round_idx])
        if len(picked) == before:
            break
        round_idx += 1
    return picked[:n]


def select_priority(candidates: list[dict[str, Any]], n: int, seed: str = "repro-audit",
                    weight_fn: Callable[[dict[str, Any]], float] | None = None
                    ) -> list[dict[str, Any]]:
    """优先级选择：按权重降序取前 n（权重相同时按 sha256 定序保证幂等）。

    ``weight_fn`` 可选：默认权重 = n_criteria * (1 + n_test_files)。
    """
    if weight_fn is None:
        weight_fn = lambda c: c.get("n_criteria", 0) * (1 + c.get("n_test_files", 0))

    decorated = [(weight_fn(c), _deterministic_sort_key(c, seed), c) for c in candidates]
    decorated.sort(key=lambda t: (-t[0], t[1]))
    return [t[2] for t in decorated[:n]]


def select_coverage(candidates: list[dict[str, Any]], n: int, seed: str = "repro-audit",
                    stratum_fn: Callable[[dict[str, Any]], str] | None = None
                    ) -> list[dict[str, Any]]:
    """覆盖度选择：贪心最大化 stratum 覆盖（每轮取未覆盖 stratum 里 sha256 最小的）。

    ``stratum_fn`` 可选：默认 stratum = repo。
    """
    if stratum_fn is None:
        stratum_fn = lambda c: c.get("repo", "unknown")

    remaining = list(candidates)
    covered_strata: set[str] = set()
    picked: list[dict[str, Any]] = []

    while len(picked) < n and remaining:
        by_stratum: dict[str, list[dict[str, Any]]] = {}
        for c in remaining:
            s = stratum_fn(c)
            by_stratum.setdefault(s, []).append(c)

        uncovered = [s for s in by_stratum if s not in covered_strata]
        pool_strata = uncovered if uncovered else list(by_stratum.keys())

        best = None
        best_key = None
        for s in pool_strata:
            for c in by_stratum[s]:
                key = _deterministic_sort_key(c, seed)
                if best_key is None or key < best_key:
                    best = c
                    best_key = key

        if best is None:
            break

        picked.append(best)
        covered_strata.add(stratum_fn(best))
        remaining.remove(best)

    return picked


def select_random(candidates: list[dict[str, Any]], n: int, seed: str = "repro-audit"
                  ) -> list[dict[str, Any]]:
    """随机选择：按 sha256(seed|mission_id) 定序取前 n（确定性伪随机）。"""
    decorated = [(_deterministic_sort_key(c, seed), c) for c in candidates]
    decorated.sort(key=lambda t: t[0])
    return [t[1] for t in decorated[:n]]


# ─────────────────────────── 覆盖度统计 ───────────────────────────

def compute_coverage(selected: list[dict[str, Any]], all_candidates: list[dict[str, Any]],
                     stratum_fn: Callable[[dict[str, Any]], str] | None = None
                     ) -> dict[str, Any]:
    """计算选中集合对全量候选的覆盖度。

    返回 ``{total, selected, coverage_rate, strata_total, strata_covered, strata_coverage}``。
    """
    if stratum_fn is None:
        stratum_fn = lambda c: c.get("repo", "unknown")

    all_strata = {stratum_fn(c) for c in all_candidates}
    sel_strata = {stratum_fn(c) for c in selected}

    total = len(all_candidates)
    sel_count = len(selected)

    return {
        "total": total,
        "selected": sel_count,
        "coverage_rate": (sel_count / total) if total > 0 else None,
        "strata_total": len(all_strata),
        "strata_covered": len(sel_strata),
        "strata_coverage": (len(sel_strata) / len(all_strata)) if all_strata else None,
    }


def select_with_strategy(candidates: list[dict[str, Any]], n: int, strategy: str = "stratified",
                         seed: str = "repro-audit", **kwargs) -> list[dict[str, Any]]:
    """策略分发入口：根据 strategy 名调用对应选择函数。"""
    strategies = {
        "stratified": select_stratified,
        "priority": select_priority,
        "coverage": select_coverage,
        "random": select_random,
    }
    fn = strategies.get(strategy)
    if fn is None:
        raise ValueError(f"unknown strategy: {strategy}, valid: {list(strategies)}")
    # Filter kwargs to only those accepted by the function
    import inspect
    sig = inspect.signature(fn)
    valid_params = set(sig.parameters.keys()) - {"candidates", "n", "seed"}
    filtered = {k: v for k, v in kwargs.items() if k in valid_params}
    return fn(candidates, n, seed=seed, **filtered)
