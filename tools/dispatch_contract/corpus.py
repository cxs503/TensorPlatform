"""corpus —— 从历史任务快照推导「派单前契约就绪」回测语料。

为什么要推导而不是手标
----------------------
历史终态单里没有 ``input_contract`` 字段（平台尚未强制声明），但任务书文本中带有
**机器可解析**的依赖引用：上游任务号（``mission 1234`` / ``m1234``）与工件路径
（``artifacts/...``、``runs/...``、``docs/report_m1234.md`` 等）。本模块按配置
``config/dispatch_contract.v1.json → corpus`` 的正则把这些引用推断成 v1 契约，
再用**派单时刻**（``created_at``）为界回放上游注册表快照——即只允许使用当时可得
的信息，杜绝未来信息渗漏（no look-ahead）。

标签
----
* 主标签 ``label_blocked``：终态 ∈ 配置 ``blocked_status``（failed/stopped）。
* 次标签 ``label_non_delivery``：主标签 或 ``succeeded`` 但交付工件数为 0。

零领域语义
----------
只依赖平台级模式：任务号、路径前缀、判据标记（``pytest``/``assert``/比较符）。
不出现任何领域词条、领域判据或领域算例。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contract_schema import CONTRACT_VERSION, load_config

CORPUS_SCHEMA = "dispatch-contract/corpus/v1"
ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# 快照读取
# --------------------------------------------------------------------------- #
def _as_list(doc: Any) -> list[dict]:
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, Mapping):
        for key in ("items", "missions", "data"):
            v = doc.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        vals = [v for v in doc.values() if isinstance(v, dict)]
        if vals:
            return vals
    return []


def load_missions(path: str | Path) -> list[dict]:
    """读历史任务快照（JSON 列表 / ``{items: [...]}`` / ``{id: {...}}`` 均可）。"""
    p = Path(path)
    with open(p, encoding="utf-8") as fh:
        return _as_list(json.load(fh))


def _maybe_obj(v: Any) -> Any:
    """平台把结构化字段存成 Python repr 字符串，尽量还原为对象。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            for parser in (json.loads, ast.literal_eval):
                try:
                    return parser(s)
                except Exception:
                    continue
    return v


def extract_result_facts(record: Mapping[str, Any]) -> dict:
    """从 result 字段提取机器事实：交付工件、证据门、失败阶段。"""
    res = _maybe_obj(record.get("result"))
    facts = {"changed_files": [], "gate": None, "fail_stage": None}
    if not isinstance(res, Mapping):
        return facts
    ev = res.get("evidence") if isinstance(res.get("evidence"), Mapping) else {}
    checks = ev.get("checks") if isinstance(ev.get("checks"), Mapping) else {}
    summary = ev.get("summary") if isinstance(ev.get("summary"), Mapping) else {}
    if not summary and isinstance(res.get("summary"), Mapping):
        summary = res["summary"]
    cf = summary.get("changed_files")
    if isinstance(cf, list):
        facts["changed_files"] = [str(x) for x in cf if isinstance(x, str)]
    facts["gate"] = checks.get("gate")
    facts["fail_stage"] = checks.get("fail_stage")
    return facts


def normalise_path(p: str) -> str:
    """把工作树绝对路径归一为仓内相对路径（派生语料里只保留仓内相对路径）。"""
    p = str(p).replace("\\", "/")
    m = re.search(r"/(?:work|repos)/[^/]+/(.+)$", p)
    if m:
        return m.group(1)
    return p.lstrip("./")


# --------------------------------------------------------------------------- #
# 引用推断
# --------------------------------------------------------------------------- #
def infer_declared(goal: str, mission_id: str, cfg: Mapping[str, Any] | None = None) -> dict:
    """从任务书文本推断机器可解析的依赖声明（refs / 输入工件 / 输出工件 / 判据数）。"""
    cfg = cfg or load_config()
    c = cfg.get("corpus") or {}
    goal = goal or ""
    refs: set[str] = set()
    for pat in c.get("reference_patterns", []):
        for m in re.finditer(pat, goal):
            refs.add(m.group(1))
    if mission_id:
        refs.discard(str(mission_id))
    paths: set[str] = set()
    for pat in c.get("artifact_path_patterns", []):
        for m in re.finditer(pat, goal):
            paths.add(m.group(1).rstrip(".,;:"))
    in_prefix = tuple(c.get("input_path_prefixes", []))
    out_prefix = tuple(c.get("output_path_prefixes", []))
    inputs = sorted(p for p in paths if p.startswith(in_prefix))
    outputs = sorted(p for p in paths if p.startswith(out_prefix))
    n_crit = 0
    for pat in c.get("criteria_patterns", []):
        n_crit += len(re.findall(pat, goal))
    return {
        "refs": sorted(refs),
        "input_paths": inputs,
        "output_paths": outputs,
        "other_paths": sorted(p for p in paths if p not in set(inputs) | set(outputs)),
        "n_criteria": n_crit,
        "goal_sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
        "goal_len": len(goal),
    }


# --------------------------------------------------------------------------- #
# 派单时刻快照回放
# --------------------------------------------------------------------------- #
def _ts(v: Any) -> str | None:
    return str(v) if v not in (None, "", "None") else None


def build_producer_index(missions: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    """工件路径 → 产出记录列表（按完成时间排序），用于派单时刻回放。"""
    idx: dict[str, list[dict]] = {}
    for rec in missions:
        facts = extract_result_facts(rec)
        for p in facts["changed_files"]:
            idx.setdefault(normalise_path(p), []).append({
                "mission_id": str(rec.get("id")),
                "status": rec.get("status"),
                "finished_at": _ts(rec.get("finished_at")) or _ts(rec.get("created_at")),
            })
    for v in idx.values():
        v.sort(key=lambda d: d["finished_at"] or "9999")
    return idx


def resolve_upstream(declared: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]],
                     producer_index: Mapping[str, list[dict]], dispatched_at: str | None) -> dict:
    """回放派单时刻的上游注册表快照（只看 ``dispatched_at`` 之前可见的事实）。"""
    up: dict[str, dict] = {}
    for ref in declared.get("refs", []):
        rec = by_id.get(str(ref))
        if rec is None:
            up[str(ref)] = {"found": False}
            continue
        fin = _ts(rec.get("finished_at")) or _ts(rec.get("created_at"))
        up[str(ref)] = {
            "found": True,
            "status": rec.get("status"),
            "finished_at": fin,
            "delivered_before_dispatch": bool(fin and dispatched_at and fin <= dispatched_at),
        }
    for path in declared.get("input_paths", []):
        cands = producer_index.get(str(path), [])
        visible = [c for c in cands if dispatched_at and (c["finished_at"] or "9999") <= dispatched_at]
        if not visible:
            up[str(path)] = {"found": False, "status": None, "delivered_before_dispatch": False}
        else:
            last = visible[-1]
            up[str(path)] = {
                "found": True,
                "status": last["status"],
                "finished_at": last["finished_at"],
                "delivered_before_dispatch": True,
                "producer_mission_id": last["mission_id"],
                "payload": None,
            }
    return up


def infer_contract(declared: Mapping[str, Any], mission_id: str,
                   cfg: Mapping[str, Any] | None = None) -> dict | None:
    """把推断出的依赖声明整理成 v1 契约；无任何输入需求 → ``None``（降级放行）。"""
    cfg = cfg or load_config()
    input_reqs: list[dict] = []
    for ref in declared.get("refs", []):
        input_reqs.append({"kind": "upstream_delivery", "ref": str(ref), "required": True,
                           "note": "任务书引用上游任务号"})
    for path in declared.get("input_paths", []):
        input_reqs.append({"kind": "artifact", "ref": str(path), "required": True,
                           "note": "任务书引用输入工件路径"})
    if not input_reqs:
        return None
    output_reqs: list[dict] = []
    for path in declared.get("output_paths", []):
        output_reqs.append({"kind": "artifact", "ref": str(path), "required": True,
                            "note": "任务书声明的输出工件路径"})
    return {
        "node_id": str(mission_id),
        "contract_version": CONTRACT_VERSION,
        "input_contract": input_reqs,
        "output_contract": output_reqs,
        "provenance": "inferred_from_goal_text/v1",
    }


# --------------------------------------------------------------------------- #
# 独立事实查询（oracle）—— 与 readiness 判定逻辑零代码共享，用于交叉核对
# --------------------------------------------------------------------------- #
def _facts_table(missions: Iterable[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None) -> list[dict]:
    """一次性把历史任务的交付事实拍平成表（供 oracle 直扫，避免重复解析）。"""
    cfg = cfg or load_config()
    satisfied = set(cfg.get("satisfied_upstream_status", ["succeeded"]))
    table = []
    for rec in missions:
        facts = extract_result_facts(rec)
        table.append({
            "id": str(rec.get("id")),
            "status": rec.get("status"),
            "ok_status": rec.get("status") in satisfied,
            "fin": _ts(rec.get("finished_at")) or _ts(rec.get("created_at")),
            "paths": {normalise_path(p) for p in facts["changed_files"]},
        })
    return table


def oracle_input_unavailable(declared: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]],
                             facts_table: list[dict], dispatched_at: str | None,
                             cfg: Mapping[str, Any] | None = None) -> list[dict]:
    """**独立实现**的「派单时刻输入是否可得」事实查询。

    刻意不复用 ``resolve_upstream``/``build_producer_index``：这里直扫交付事实表，
    用最朴素的规则回答「这条声明输入在派单时刻能不能拿到」。它是回测里的
    真实标签来源（契约可满足性口径），与就绪门的判定实现互为交叉核对。
    """
    cfg = cfg or load_config()
    satisfied = set(cfg.get("satisfied_upstream_status", ["succeeded"]))
    out: list[dict] = []
    for ref in declared.get("refs", []):
        rec = by_id.get(str(ref))
        if rec is None:
            out.append({"ref": str(ref), "kind": "upstream_delivery", "why": "NOT_FOUND"})
            continue
        fin = _ts(rec.get("finished_at")) or _ts(rec.get("created_at"))
        if rec.get("status") not in satisfied:
            out.append({"ref": str(ref), "kind": "upstream_delivery", "why": "STATUS_NOT_SATISFIED"})
        elif not (fin and dispatched_at and fin <= dispatched_at):
            out.append({"ref": str(ref), "kind": "upstream_delivery", "why": "LATE"})
    for path in declared.get("input_paths", []):
        usable = False
        for row in facts_table:                      # 直扫，不用任何索引
            if path in row["paths"] and row["ok_status"] and row["fin"] and dispatched_at \
                    and row["fin"] <= dispatched_at:
                usable = True
                break
        if not usable:
            out.append({"ref": str(path), "kind": "artifact", "why": "NO_PRODUCER_BEFORE_DISPATCH"})
    return out


# --------------------------------------------------------------------------- #
# 语料构建
# --------------------------------------------------------------------------- #
def build_corpus(missions: list[dict], cfg: Mapping[str, Any] | None = None,
                 population_marker: str | None = None) -> tuple[list[dict], dict]:
    """构建回测语料；返回 ``(records, stats)``。"""
    cfg = cfg or load_config()
    c = cfg.get("corpus") or {}
    blocked_status = set(cfg.get("blocked_status", ["failed", "stopped"]))
    marker = population_marker if population_marker is not None else c.get("population_goal_marker")

    by_id = {str(m.get("id")): m for m in missions}
    producer_index = build_producer_index(missions)
    facts_table = _facts_table(missions, cfg)      # oracle 用的扁平交付事实表

    records: list[dict] = []
    stats = {"n_missions": len(missions), "n_population": 0, "n_terminal": 0,
             "n_with_contract": 0, "n_degraded": 0, "n_blocked": 0}
    for rec in missions:
        goal = rec.get("goal") or ""
        if marker and marker not in goal:
            continue
        stats["n_population"] += 1
        status = rec.get("status")
        if status in set(cfg.get("non_terminal_status", [])) or status in (None, "queued", "running"):
            continue
        stats["n_terminal"] += 1
        mid = str(rec.get("id"))
        facts = extract_result_facts(rec)
        declared = infer_declared(goal, mid, cfg)
        contract = infer_contract(declared, mid, cfg)
        dispatched_at = _ts(rec.get("created_at"))
        upstream = resolve_upstream(declared, by_id, producer_index, dispatched_at)
        blocked = 1 if status in blocked_status else 0
        non_delivery = 1 if (blocked or (status == "succeeded" and not facts["changed_files"])) else 0
        stats["n_blocked"] += blocked
        if contract is None:
            stats["n_degraded"] += 1
        else:
            stats["n_with_contract"] += 1
        oracle_unavail = oracle_input_unavailable(declared, by_id, facts_table, dispatched_at, cfg)
        records.append({
            "schema": CORPUS_SCHEMA,
            "node_id": mid,
            "mission_id": mid,
            "repo": rec.get("repo"),
            "status": status,
            "created_at": dispatched_at,
            "finished_at": _ts(rec.get("finished_at")),
            "label_blocked": blocked,
            "label_non_delivery": non_delivery,
            "label_input_unavailable": (1 if oracle_unavail else 0) if contract is not None else None,
            "oracle_unavailable": oracle_unavail,
            "fail_stage": facts["fail_stage"],
            "gate": facts["gate"],
            "n_delivered_artifacts": len(facts["changed_files"]),
            "declared": declared,
            "dispatch": {
                "node_id": mid,
                "contract": contract,
                "upstream": upstream,
                "dispatched_at": dispatched_at,
            },
        })
    return records, stats


def write_corpus(records: list[dict], path: str | Path) -> str:
    """写 JSONL 语料（定序、紧凑），返回 sha256。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(p, "w", encoding="utf-8") as fh:
        for r in sorted(records, key=lambda x: x["mission_id"]):
            line = json.dumps(r, ensure_ascii=False, sort_keys=True)
            fh.write(line + "\n")
            h.update(line.encode("utf-8"))
    return h.hexdigest()


def read_corpus(path: str | Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out