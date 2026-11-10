"""能力特征提取（extraction layer）。

把 :class:`DiscoveryRecord` 列表 + 运行结局索引，聚合成
:class:`~tools.capability_discovery.model.CapabilityFeature`。

聚合规则（全部与领域无关）
--------------------------
* 同一 ``capability_id`` 的多条记录（函数级 / 类级 / 模块级 / parametrize）
  合并为**一个**能力特征；
* ``declared_level`` 取所有声明中的**最高**等级，同时保留
  ``declared_levels_seen`` 供冲突检测（声明不一致 = 需人审）；
* ``anchors`` / ``tags`` / ``marker_formats`` 取并集并排序（确定性）；
* ``params`` 的数值区间合并（``{"min":a,"max":b}`` → 取外包络），非区间值
  冲突时保留先出现者并记 ``extra["param_conflicts"]``；
* 每个测试节点的运行结局来自运行期插件（``plugin.py``），缺失即 ``not_run``。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pathlib import Path
from typing import Any as _Any

from .markers import derive_bare_id
from .model import (
    CapabilityFeature,
    DiscoveryRecord,
    MARKER_FORMATS,
    OUTCOME_STATES,
    SCOPE_FUNCTION,
    level_rank,
    sha256_file,
)


def normalize_nodeid(nodeid: str) -> str:
    """把 pytest 与静态扫描的 nodeid 归一（去 ``./``、反斜杠、重复冒号）。"""
    nid = str(nodeid or "").replace("\\", "/").strip()
    while nid.startswith("./"):
        nid = nid[2:]
    return nid


class OutcomeIndex:
    """nodeid → 运行结局索引，带后缀／参数化回退匹配。

    静态扫描的 nodeid 相对仓根且**不含参数化后缀**（``test_x``），pytest 运行
    期的 nodeid 含参数（``test_x[8]``）且相对 rootdir，两者可能差前缀。回退
    顺序：完全匹配 → ``Class::test`` 后缀 → ``test`` 末段 → 去掉 ``[...]`` 的
    末段。多条运行记录落同一回退键时取**矛盾度最高**的结局（宁可低估）。
    仍不中即视为未运行（``not_run``），绝不假设通过。
    """

    _BRACKET = re.compile(r"\[.*\]$")

    def __init__(self, outcomes: Optional[Dict[str, str]] = None) -> None:
        self._exact: Dict[str, str] = {}
        self._suffix: Dict[str, str] = {}
        for nid, state in (outcomes or {}).items():
            state = str(state).lower()
            if state not in OUTCOME_STATES:
                raise ValueError(f"未知运行结局: {state!r}；允许 {OUTCOME_STATES}")
            norm = normalize_nodeid(nid)
            self._exact[norm] = state
            parts = [p for p in norm.split("::") if p]
            bare = [self._BRACKET.sub("", p) for p in parts]
            if len(bare) >= 2:
                self._merge("::".join(bare[-2:]), state)
            if bare:
                self._merge(bare[-1], state)

    def _merge(self, key: str, state: str) -> None:
        cur = self._suffix.get(key)
        if cur is None or OUTCOME_STATES.index(state) < OUTCOME_STATES.index(cur):
            self._suffix[key] = state

    def get(self, nodeid: str) -> str:
        norm = normalize_nodeid(nodeid)
        if norm in self._exact:
            return self._exact[norm]
        parts = [p for p in norm.split("::") if p]
        bare = [self._BRACKET.sub("", p) for p in parts]
        if len(bare) >= 2:
            hit = self._suffix.get("::".join(bare[-2:]))
            if hit:
                return hit
        if bare:
            hit = self._suffix.get(bare[-1])
            if hit:
                return hit
        return "not_run"

    def __len__(self) -> int:
        return len(self._exact)


def _merge_params(target: Dict[str, Any], incoming: Dict[str, Any], conflicts: Dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key not in target:
            target[key] = value
            continue
        cur = target[key]
        if isinstance(cur, dict) and isinstance(value, dict) and {
            "min",
            "max",
        } <= set(cur) | set(value):
            lo = min(v for v in (cur.get("min"), value.get("min")) if isinstance(v, (int, float)))
            hi = max(v for v in (cur.get("max"), value.get("max")) if isinstance(v, (int, float)))
            target[key] = {"min": lo, "max": hi}
            continue
        if cur != value:
            conflicts.setdefault(key, [cur]).append(value)


def extract_features(
    records: Iterable[DiscoveryRecord], outcomes: Optional[Dict[str, str] | OutcomeIndex] = None
) -> List[CapabilityFeature]:
    """记录列表 → 按能力聚合的特征列表（按 capability_id 排序）。"""
    index = outcomes if isinstance(outcomes, OutcomeIndex) else OutcomeIndex(outcomes)
    grouped: Dict[str, List[DiscoveryRecord]] = {}
    for rec in records:
        grouped.setdefault(rec.capability_id, []).append(rec)

    features: List[CapabilityFeature] = []
    for cap_id in sorted(grouped):
        recs = sorted(grouped[cap_id], key=lambda r: (r.nodeid, r.lineno, r.marker_format))
        label = next((r.label for r in recs if r.label), "")
        declared_seen = sorted({r.declared_level for r in recs if r.declared_level})
        declared = max(declared_seen, key=level_rank) if declared_seen else ""
        anchors: List[str] = sorted({a for r in recs for a in r.anchors})
        tags: List[str] = sorted({t for r in recs for t in r.tags})
        params: Dict[str, Any] = {}
        conflicts: Dict[str, Any] = {}
        for r in recs:
            _merge_params(params, r.params, conflicts)

        tests: Dict[str, Dict[str, Any]] = {}
        for r in recs:
            entry = tests.setdefault(
                r.nodeid,
                {
                    "nodeid": r.nodeid,
                    "source_file": r.source_file,
                    "lineno": r.lineno,
                    "marker_formats": [],
                    "scopes": [],
                    "modifiers": [],
                    "outcome": index.get(r.nodeid),
                },
            )
            if r.marker_format not in entry["marker_formats"]:
                entry["marker_formats"].append(r.marker_format)
            if r.scope not in entry["scopes"]:
                entry["scopes"].append(r.scope)
            for mod in r.modifiers:
                if mod not in entry["modifiers"]:
                    entry["modifiers"].append(mod)

        sources: Dict[str, str] = {}
        for r in recs:
            sources[r.source_file] = r.source_sha256

        outcomes_count: Dict[str, int] = {}
        for entry in tests.values():
            state = entry["outcome"]
            outcomes_count[state] = outcomes_count.get(state, 0) + 1

        features.append(
            CapabilityFeature(
                capability_id=cap_id,
                label=label,
                declared_level=declared,
                anchors=anchors,
                params=params,
                tags=tags,
                tests=[tests[n] for n in sorted(tests)],
                sources=[{"path": p, "sha256": h} for p, h in sorted(sources.items())],
                marker_formats=sorted({r.marker_format for r in recs}),
                outcomes=dict(sorted(outcomes_count.items())),
                declared_levels_seen=declared_seen,
                extra={"param_conflicts": conflicts} if conflicts else {},
            )
        )
    return features


def aggregate_outcome(outcomes: Dict[str, int]) -> str:
    """把结局计数字典压成单一状态（矛盾度优先；全空=not_run）。"""
    present = {state for state, n in (outcomes or {}).items() if n > 0}
    if not present:
        return "not_run"
    for state in OUTCOME_STATES:  # 顺序即优先级
        if state in present:
            return state
    return "not_run"

# ── 运行期发现（插件采集的 marker 元数据 → 记录） ────────────────────────────
_RUNTIME_ID_KEYS = ("id", "capability", "capability_id", "name")
_RUNTIME_LEVEL_KEYS = ("level", "claim", "claimed_level")
_RUNTIME_ANCHOR_KEYS = ("anchor", "anchors", "refs", "references")
_RUNTIME_PARAM_KEYS = ("params", "param")
_RUNTIME_TAG_KEYS = ("tags", "tag")
_RUNTIME_LABEL_KEYS = ("label", "description", "title")


def _first_known(mapping: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in mapping:
            return mapping[k]
    return None


def runtime_records(
    items: List[Dict[str, Any]], source_root: Optional[Path | str] = None
) -> Tuple[List[DiscoveryRecord], List[Dict[str, Any]]]:
    """把插件采到的测试记录转成 DiscoveryRecord。

    运行期 marker 的元数据已被 pytest 求值，因此**非字面量 id**（静态扫描只能
    报 diagnostic 的情形）在这里可以落地。来源 sha256 通过
    ``source_root / <nodeid 的文件部分>`` 求；找不到文件则留空，定级时会被
    来源可追溯性闸门压到 WITHHELD（诚实：认不出出处的证据不算证据）。
    """
    root = Path(source_root).resolve() if source_root else None
    sha_cache: Dict[Path, str] = {}
    records: List[DiscoveryRecord] = []
    diagnostics: List[Dict[str, Any]] = []
    for item in items:
        nodeid = normalize_nodeid(item.get("nodeid", ""))
        if not nodeid:
            continue
        file_part = nodeid.split("::")[0]
        source_sha = ""
        if root is not None:
            candidate = (root / file_part).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                candidate = None  # 越界路径不接受
            if candidate is not None and candidate.is_file():
                if candidate not in sha_cache:
                    sha_cache[candidate] = sha256_file(candidate)
                source_sha = sha_cache[candidate]
        for meta in item.get("markers", []) or []:
            kwargs = dict(meta.get("kwargs") or {})
            args = list(meta.get("args") or [])
            cap_id = _first_known(kwargs, _RUNTIME_ID_KEYS)
            if not cap_id and args:
                cap_id = args[0]
            if not isinstance(cap_id, str) or not cap_id.strip():
                # 裸 marker（无 id）：与静态扫描同口径，由测试节点派生
                cap_id = derive_bare_id(nodeid)
            level = _first_known(kwargs, _RUNTIME_LEVEL_KEYS) or ""
            anchors = _first_known(kwargs, _RUNTIME_ANCHOR_KEYS) or []
            params = _first_known(kwargs, _RUNTIME_PARAM_KEYS) or {}
            tags = _first_known(kwargs, _RUNTIME_TAG_KEYS) or []
            label = _first_known(kwargs, _RUNTIME_LABEL_KEYS) or ""
            records.append(
                DiscoveryRecord(
                    capability_id=str(cap_id).strip(),
                    nodeid=nodeid,
                    source_file=file_part,
                    source_sha256=source_sha,
                    lineno=0,
                    marker_format="runtime_marker",
                    scope=SCOPE_FUNCTION,
                    label=label if isinstance(label, str) else "",
                    declared_level=str(level).upper() if isinstance(level, str) else "",
                    anchors=[a for a in anchors if isinstance(a, str)] if isinstance(anchors, list) else [],
                    params=params if isinstance(params, dict) else {},
                    tags=[t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
                    modifiers=[],
                )
            )
    records.sort(key=lambda r: (r.capability_id, r.nodeid, r.marker_format))
    return records, diagnostics


def merge_records(
    static_records: List[DiscoveryRecord], runtime_records_: List[DiscoveryRecord]
) -> List[DiscoveryRecord]:
    """静态记录为主，运行期记录只补静态扫不到的测试节点。

    去重键：``(capability_id, 去参数化后缀的 nodeid)``——参数化用例在静态视图里
    是一个节点，运行期是多个带 ``[param]`` 的节点，不应重复计数。
    """
    covered = {(r.capability_id, _strip_params(r.nodeid)) for r in static_records}
    merged = list(static_records)
    for rec in runtime_records_:
        key = (rec.capability_id, _strip_params(rec.nodeid))
        if key in covered:
            continue
        covered.add(key)
        merged.append(rec)
    merged.sort(key=lambda r: (r.capability_id, r.nodeid, r.lineno, r.marker_format))
    return merged


def _strip_params(nodeid: str) -> str:
    return re.sub(r"\[.*\]$", "", normalize_nodeid(nodeid))
