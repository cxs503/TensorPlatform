"""战役成熟化整合门（maturity gate）· 规格装载与严格校验。

平台共性机制层：**零领域语义**。战役的全部领域内容（节点名、判据陈述、阈值、
证据文档路径）由 JSON 工件注入（``data/maturity_gate/*.spec.json``），本包只提供
「声明 → 计量 → 裁定」的通用机器。

规格（spec）
------------
::

    {
      "spec_id": "…", "schema_version": "1.0",
      "criteria_pass_threshold": 0.8,          # 节点绿线（判据通过率下限）
      "nodes": [
        {"node_key": "…", "title": "…", "repo": "…", "mission_id": 8411,
         "capability": "…",
         "criteria": [
           {"criterion_id": "a",
            "statement": "…",                  # 人读判据陈述（原样保留，供报告引用）
            "unit": "-",
            "checks": [                        # 合取：全部成立才算该判据通过
              {"doc": "artifacts/…/study.json",     # 相对节点工作树；绝对路径直用
               "pointer": "criteria.a.rel_error",   # 点号路径 + 可选 [i] 下标
               "comparator": "le", "threshold": 0.10,
               "sha256": "…",                       # 可选：钉桩，不符即 FAIL(integrity)
               "claim": "criteria.a.pass"}          # 可选：上游自述布尔，用于互裁
            ]}
         ]}
      ]
    }

判据与检查的层级
----------------
一条**判据**（criterion）对应验收清单里的一行；一行常是复合命题（"单调递增且
α∈[0.05,0.2]" 之类），故判据由 1..n 个**检查**（check）合取构成。通过率按判据计，
不按检查计——这样"把一句话拆成两半刷分"不会发生。

严格性（宁拒不猜）
------------------
未知比较器 / 未知键 / 节点键重复 / 判据 id 重复 / 空 checks 一律抛 :class:`SpecError`：
「配置写错」与「判据不过」必须可区分，笔误不能静默降级成 WITHHELD。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "1.0"

#: 通用比较器（纯数学谓词，无领域含义）。``eq`` 支持布尔阈值（身份判定）。
COMPARATORS: Tuple[str, ...] = ("le", "lt", "ge", "gt", "eq", "between", "nonempty")

_TOP_KEYS = {"spec_id", "schema_version", "criteria_pass_threshold", "nodes",
             "title", "notes"}
_NODE_KEYS = {"node_key", "title", "repo", "mission_id", "capability", "criteria",
              "layer", "risk"}
_CRIT_KEYS = {"criterion_id", "statement", "unit", "checks",
              "comparator", "threshold", "evidence"}  # 后三者 = 单检查简写
_CHECK_KEYS = {"doc", "pointer", "comparator", "threshold", "unit", "sha256", "claim",
               "tolerance"}
_EVID_KEYS = {"doc", "pointer", "sha256", "claim"}


class SpecError(ValueError):
    """规格本身不合法（配置错误，不是判据失败）。"""


@dataclass(frozen=True)
class Check:
    """一次取值 + 一次谓词判定。"""

    doc: str
    pointer: str
    comparator: str
    threshold: Any
    unit: str = "-"
    sha256: Optional[str] = None
    claim: Optional[str] = None
    tolerance: float = 0.0


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    statement: str
    checks: Tuple[Check, ...]
    unit: str = "-"


@dataclass(frozen=True)
class NodeSpec:
    node_key: str
    title: str
    repo: str
    criteria: Tuple[Criterion, ...]
    mission_id: Optional[int] = None
    capability: Optional[str] = None
    layer: Optional[str] = None
    risk: Optional[str] = None


@dataclass(frozen=True)
class CampaignSpec:
    spec_id: str
    nodes: Tuple[NodeSpec, ...]
    criteria_pass_threshold: float = 0.8
    title: str = ""
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    def node_keys(self) -> List[str]:
        return [n.node_key for n in self.nodes]

    def node(self, key: str) -> NodeSpec:
        for n in self.nodes:
            if n.node_key == key:
                return n
        raise KeyError(key)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecError(msg)


def _check_keys(obj: Dict[str, Any], allowed: set, where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    _require(not unknown, f"{where}: 未知键 {unknown}（防笔误静默降级）")


def _validate_threshold(comparator: str, threshold: Any, where: str) -> None:
    if comparator == "nonempty":
        return
    _require("threshold" in {} or threshold is not None, f"{where}: {comparator} 必须给 threshold")
    if comparator == "between":
        _require(isinstance(threshold, (list, tuple)) and len(threshold) == 2,
                 f"{where}: between 的 threshold 必须是 [lo, hi]")
        _require(float(threshold[0]) <= float(threshold[1]), f"{where}: between 要求 lo <= hi")
        return
    if isinstance(threshold, bool):
        _require(comparator == "eq", f"{where}: 布尔阈值只允许配合 eq")
        return
    _require(isinstance(threshold, (int, float)) and not isinstance(threshold, bool),
             f"{where}: threshold 必须是数值（或 eq 的布尔）")


def _parse_check(raw: Any, where: str, *, default_cmp: Optional[str] = None,
                 default_thr: Any = None, default_unit: str = "-") -> Check:
    _require(isinstance(raw, dict), f"{where}: check 必须是对象")
    _check_keys(raw, _CHECK_KEYS, where)
    doc = raw.get("doc")
    _require(isinstance(doc, str) and doc, f"{where}: doc 缺失")
    pointer = raw.get("pointer")
    _require(isinstance(pointer, str) and pointer, f"{where}: pointer 缺失")
    cmp_ = raw.get("comparator", default_cmp)
    _require(cmp_ in COMPARATORS, f"{where}: 未知比较器 {cmp_!r}，允许 {list(COMPARATORS)}")
    thr = raw.get("threshold", default_thr)
    _validate_threshold(cmp_, thr, where)
    sha = raw.get("sha256")
    if sha is not None:
        _require(isinstance(sha, str) and len(sha) == 64,
                 f"{where}: sha256 必须是 64 位十六进制")
    claim = raw.get("claim")
    _require(claim is None or isinstance(claim, str), f"{where}: claim 必须是字符串路径")
    return Check(doc=doc, pointer=pointer, comparator=cmp_, threshold=thr,
                 unit=str(raw.get("unit", default_unit)), sha256=sha, claim=claim,
                 tolerance=float(raw.get("tolerance", 0.0) or 0.0))


def _parse_criterion(raw: Any, where: str) -> Criterion:
    _require(isinstance(raw, dict), f"{where}: 判据必须是对象")
    _check_keys(raw, _CRIT_KEYS, where)
    cid = raw.get("criterion_id")
    _require(isinstance(cid, str) and cid.strip() != "", f"{where}: criterion_id 缺失/为空")
    unit = str(raw.get("unit", "-"))
    raws = raw.get("checks")
    checks: List[Check] = []
    if raws is not None:
        _require(isinstance(raws, list) and raws, f"{where}: checks 必须是非空列表")
        checks = [_parse_check(c, f"{where}.checks[{i}]", default_unit=unit)
                  for i, c in enumerate(raws)]
        _require("evidence" not in raw, f"{where}: checks 与 evidence 简写不可同时出现")
    else:
        ev = raw.get("evidence")
        _require(isinstance(ev, dict), f"{where}: 需要 checks，或 evidence 单检查简写")
        _check_keys(ev, _EVID_KEYS, f"{where}.evidence")
        merged = dict(ev)
        if "comparator" in raw:
            merged.setdefault("comparator", raw["comparator"])
        if "threshold" in raw:
            merged.setdefault("threshold", raw["threshold"])
        merged.setdefault("unit", unit)
        checks = [_parse_check(merged, f"{where}.evidence", default_unit=unit)]
    return Criterion(criterion_id=cid, statement=str(raw.get("statement", "")),
                     checks=tuple(checks), unit=unit)


def _parse_node(raw: Any, idx: int) -> NodeSpec:
    where = f"nodes[{idx}]"
    _require(isinstance(raw, dict), f"{where}: 必须是对象")
    _check_keys(raw, _NODE_KEYS, where)
    key = raw.get("node_key")
    _require(isinstance(key, str) and key.strip() != "", f"{where}: node_key 缺失/为空")
    repo = raw.get("repo")
    _require(isinstance(repo, str) and repo.strip() != "", f"{where}: repo 缺失/为空")
    raws = raw.get("criteria")
    _require(isinstance(raws, list) and raws, f"{where}: criteria 必须是非空列表")
    crits = [_parse_criterion(c, f"{where}.criteria[{i}]") for i, c in enumerate(raws)]
    ids = [c.criterion_id for c in crits]
    _require(len(set(ids)) == len(ids), f"{where}: criterion_id 重复 {ids}")
    mid = raw.get("mission_id")
    _require(mid is None or isinstance(mid, int), f"{where}: mission_id 必须是整数")
    return NodeSpec(node_key=key, title=str(raw.get("title", key)), repo=repo,
                    criteria=tuple(crits), mission_id=mid, capability=raw.get("capability"),
                    layer=raw.get("layer"), risk=raw.get("risk"))


def parse_spec(raw: Any) -> CampaignSpec:
    """把 JSON 工件解析成 :class:`CampaignSpec`；不合法即抛 :class:`SpecError`。"""
    _require(isinstance(raw, dict), "spec 顶层必须是对象")
    _check_keys(raw, _TOP_KEYS, "spec")
    sid = raw.get("spec_id")
    _require(isinstance(sid, str) and sid.strip() != "", "spec_id 缺失/为空")
    ver = str(raw.get("schema_version", SCHEMA_VERSION))
    _require(ver == SCHEMA_VERSION, f"schema_version 不支持：{ver}（本引擎只认 {SCHEMA_VERSION}）")
    thr = float(raw.get("criteria_pass_threshold", 0.8))
    _require(0.0 < thr <= 1.0, "criteria_pass_threshold 必须落在 (0, 1]")
    raws = raw.get("nodes")
    _require(isinstance(raws, list) and raws, "nodes 必须是非空列表")
    nodes = [_parse_node(n, i) for i, n in enumerate(raws)]
    keys = [n.node_key for n in nodes]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    _require(not dup, f"node_key 重复：{dup}")
    return CampaignSpec(spec_id=sid, nodes=tuple(nodes), criteria_pass_threshold=thr,
                        title=str(raw.get("title", "")), schema_version=ver,
                        notes=str(raw.get("notes", "")))


def load_spec(path: str | Path) -> CampaignSpec:
    return parse_spec(json.loads(Path(path).read_text(encoding="utf-8")))