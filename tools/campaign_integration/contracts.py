"""跨节点接口契约检查：把「两边接口一致」变成机器判据。

两类契约（都由 JSON 工件注入，代码里没有任何接口名/量名/单位/领域词）

* ``interface`` —— 生产者与消费者对**同一个接口**的声明必须一致：
  - 声明字段齐备（``required_fields``）；
  - 轴（``axis``）：单位相等、样本按声明方向单调；
  - 量（``quantities``）：同名量单位相等；两侧都有样本时同长且相对差 ≤ ``tolerance_rel``。
* ``registry`` —— 战役注册表自洽：节点键/mission 唯一、仓库已声明、验收判据非空、
  presence 与快照的 mission 状态一致（抓「presence 是上一轮残留」的陈旧证据）。

**反空真（anti-vacuity）**：任一侧没有该接口的证据 → 该契约 ``WITHHELD``，绝不算 PASS。
「两边都没交付，所以没矛盾」是空真；把它记成 0 违反 = 把无证据洗成绿。总体 verdict 只有
在所有契约都真被检查过且 0 违反时才是 ``PASS``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"

CONTRACTS_SCHEMA = "campaign-contracts/1.0"
KIND_INTERFACE = "interface"
KIND_REGISTRY = "registry"

_TOP_KEYS = {"schema", "contracts", "notes"}
_CONTRACT_KEYS = {"id", "kind", "producer", "consumer", "interface", "quantities", "axis",
                  "required_fields", "note"}


class ContractsError(ValueError):
    """契约工件不合法（配置错 ≠ 判据不过）。"""


def load_contracts(path: str | Path) -> Dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ContractsError("contracts 顶层必须是对象")
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        raise ContractsError(f"contracts: 未知键 {unknown}")
    if doc.get("schema") != CONTRACTS_SCHEMA:
        raise ContractsError(f"contracts: 不支持的 schema {doc.get('schema')!r}")
    ids: List[str] = []
    for i, c in enumerate(doc.get("contracts") or []):
        if not isinstance(c, dict):
            raise ContractsError(f"contracts[{i}] 必须是对象")
        bad = sorted(set(c) - _CONTRACT_KEYS)
        if bad:
            raise ContractsError(f"contracts[{i}]: 未知键 {bad}")
        for key in ("id", "producer", "consumer", "interface"):
            if not c.get(key):
                raise ContractsError(f"contracts[{i}]: 缺少必填键 {key}")
        ids.append(str(c["id"]))
    dup = sorted({k for k in ids if ids.count(k) > 1})
    if dup:
        raise ContractsError(f"contracts: id 重复 {dup}")
    return doc


# ---------------------------------------------------------------- 接口取证
def _json_payloads(presence: Mapping[str, Any]):
    root = Path(str((presence.get("worktree") or {}).get("root") or ""))
    for art in (presence.get("delivery") or {}).get("artifacts") or []:
        path = root / str(art.get("path"))
        try:
            yield str(art.get("path")), json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue


def interfaces_of(presence: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从节点已交付 artifact 中抽取 ``interfaces.<name>`` 声明（先到先得，附来源路径）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for rel, doc in _json_payloads(presence):
        block = doc.get("interfaces") if isinstance(doc, dict) else None
        if not isinstance(block, dict):
            continue
        for name, payload in block.items():
            if name in out or not isinstance(payload, dict):
                continue
            merged = dict(payload)
            merged["_source"] = rel
            out[str(name)] = merged
    return out


def _monotonic(samples: Sequence[Any], direction: str) -> bool:
    vals = [float(s) for s in samples]
    if len(vals) < 2:
        return True
    pairs = list(zip(vals, vals[1:]))
    if direction == "increasing":
        return all(b >= a for a, b in pairs)
    if direction == "decreasing":
        return all(b <= a for a, b in pairs)
    return False


def _samples_equal(a: Sequence[Any], b: Sequence[Any], tol: float) -> Optional[float]:
    """返回最大相对差；长度不等或非数值返回 ``None``（不等即违反）。"""
    if len(a) != len(b):
        return None
    worst = 0.0
    for x, y in zip(a, b):
        try:
            xf, yf = float(x), float(y)
        except (TypeError, ValueError):
            return None
        scale = max(abs(xf), abs(yf), 1e-12)
        worst = max(worst, abs(xf - yf) / scale)
    return worst


def check_interface_contract(contract: Mapping[str, Any],
                             presence: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    cid = str(contract["id"])
    name = str(contract["interface"])
    prod_key, cons_key = str(contract["producer"]), str(contract["consumer"])
    out: Dict[str, Any] = {
        "id": cid, "kind": KIND_INTERFACE, "interface": name,
        "producer": prod_key, "consumer": cons_key,
        "checks": 0, "violations": [], "status": WITHHELD, "reason": None,
        "sources": {},
    }
    checks = 0
    violations: List[Dict[str, Any]] = []
    prod, cons = presence.get(prod_key), presence.get(cons_key)
    missing = [k for k, p in ((prod_key, prod), (cons_key, cons)) if p is None]
    if missing:
        out["reason"] = "presence_absent:" + ",".join(missing)
        return out

    sides = {"producer": (prod, interfaces_of(prod)), "consumer": (cons, interfaces_of(cons))}
    absent = [side for side, (_, ifaces) in sides.items() if name not in ifaces]
    if absent:
        out["reason"] = "interface_absent:" + ",".join(f"{s}:{name}" for s in absent)
        return out

    payloads = {}
    for side, (_, ifaces) in sides.items():
        payloads[side] = ifaces[name]
        out["sources"][side] = ifaces[name].get("_source")

    # 1) 声明字段齐备
    for side, payload in payloads.items():
        for field in contract.get("required_fields") or []:
            checks += 1
            if field not in payload:
                violations.append({"rule": "missing_field", "side": side, "field": field})

    # 2) 轴：单位相等 + 单调
    axis = contract.get("axis") or {}
    if axis:
        pa = payloads["producer"].get("axis") or {}
        ca = payloads["consumer"].get("axis") or {}
        checks += 2
        if not pa or not ca:
            violations.append({"rule": "axis_absent",
                               "side": [s for s, p in (("producer", pa), ("consumer", ca)) if not p]})
        else:
            if axis.get("unit") and (pa.get("unit") != axis.get("unit")
                                     or ca.get("unit") != axis.get("unit")):
                violations.append({"rule": "axis_unit_mismatch",
                                   "declared": axis.get("unit"),
                                   "producer": pa.get("unit"), "consumer": ca.get("unit")})
            direction = str(axis.get("monotonic") or "")
            if direction in ("increasing", "decreasing"):
                for side, payload in (("producer", pa), ("consumer", ca)):
                    samples = payload.get("samples")
                    if isinstance(samples, list) and not _monotonic(samples, direction):
                        violations.append({"rule": "axis_not_monotonic", "side": side,
                                           "direction": direction})

    # 3) 量：单位相等 + 样本同长同值（容差内）
    for q in contract.get("quantities") or []:
        qname = str(q.get("name"))
        quota = payloads["producer"].get("quantities") or {}
        quoc = payloads["consumer"].get("quantities") or {}
        checks += 1
        if qname not in quota or qname not in quoc:
            violations.append({"rule": "quantity_absent", "quantity": qname,
                               "producer": qname in quota, "consumer": qname in quoc})
            continue
        checks += 1
        up, uc = quota[qname].get("unit"), quoc[qname].get("unit")
        if q.get("unit") and (up != q.get("unit") or uc != q.get("unit")):
            violations.append({"rule": "quantity_unit_mismatch", "quantity": qname,
                               "declared": q.get("unit"), "producer": up, "consumer": uc})
        sp, sc = quota[qname].get("samples"), quoc[qname].get("samples")
        if isinstance(sp, list) and isinstance(sc, list):
            checks += 1
            tol = float(q.get("tolerance_rel") or 0.0)
            worst = _samples_equal(sp, sc, tol)
            if worst is None or worst > tol:
                violations.append({"rule": "value_divergence", "quantity": qname,
                                   "tolerance_rel": tol, "max_rel_diff": worst})

    out["checks"] = checks
    out["violations"] = violations
    out["status"] = FAIL if violations else PASS
    return out


# ---------------------------------------------------------------- 注册表自洽
def check_registry(snapshot: Mapping[str, Any],
                   presence: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    nodes = list(snapshot.get("nodes") or [])
    violations: List[Dict[str, Any]] = []
    checks = 0

    keys = [str(n.get("node_key")) for n in nodes]
    missions = [n.get("mission_id") for n in nodes]
    checks += 2
    dup_keys = sorted({k for k in keys if keys.count(k) > 1})
    if dup_keys:
        violations.append({"rule": "duplicate_node_key", "keys": dup_keys})
    dup_missions = sorted({m for m in missions if missions.count(m) > 1})
    if dup_missions:
        violations.append({"rule": "duplicate_mission_id", "mission_ids": dup_missions})

    # 报告索引与 mission 的链路一致性（报告自述 mission ≠ 索引 mission = 审计链断裂）
    by_title: Dict[str, List[Any]] = {}
    for mid, entries in (snapshot.get("reports") or {}).items():
        for entry in entries or []:
            if isinstance(entry, dict):
                by_title.setdefault(str(entry.get("title")), []).append(int(mid))
    duplicated_titles = {t: sorted(set(ms)) for t, ms in by_title.items() if len(set(ms)) > 1}
    checks += 1
    if duplicated_titles:
        violations.append({"rule": "report_duplicate_across_missions",
                           "titles": duplicated_titles})

    for node in nodes:
        key = str(node.get("node_key"))
        checks += 5
        if not node.get("repo"):
            violations.append({"rule": "repo_undeclared", "node": key})
        if not ((node.get("metadata") or {}).get("acceptance_criteria")):
            violations.append({"rule": "acceptance_undeclared", "node": key})
        doc = presence.get(key)
        if doc is None:
            violations.append({"rule": "presence_missing", "node": key})
            continue
        if int(doc.get("mission_id", -1)) != int(node.get("mission_id", -2)):
            violations.append({"rule": "presence_mission_mismatch", "node": key})
        if str((doc.get("mission") or {}).get("status")) != str(node.get("status")):
            violations.append({"rule": "presence_stale",
                               "node": key,
                               "presence": (doc.get("mission") or {}).get("status"),
                               "snapshot": node.get("status")})
        for item in ((doc.get("reports") or {}).get("items") or []):
            if isinstance(item, dict) and item.get("mission_link_mismatch"):
                violations.append({"rule": "report_mission_mismatch", "node": key,
                                   "report_id": item.get("id"),
                                   "claimed_missions": item.get("claimed_missions")})

    return {"id": "registry-consistency", "kind": KIND_REGISTRY, "interface": None,
            "producer": None, "consumer": None, "checks": checks,
            "violations": violations, "status": FAIL if violations else PASS,
            "reason": None, "sources": {}}


# ---------------------------------------------------------------- 总体
def evaluate(contracts_doc: Mapping[str, Any], snapshot: Mapping[str, Any],
             presence: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """跑完整张契约清单 → 逐条明细 + 反空真总体裁定。"""
    checks = [check_registry(snapshot, presence)]
    for contract in contracts_doc.get("contracts") or []:
        checks.append(check_interface_contract(contract, presence))

    violations_total = sum(len(c["violations"]) for c in checks)
    checked = sum(1 for c in checks if c["status"] in (PASS, FAIL))
    withheld = sum(1 for c in checks if c["status"] == WITHHELD)
    interface_total = len(contracts_doc.get("contracts") or [])
    interface_checked = sum(1 for c in checks
                            if c["kind"] == KIND_INTERFACE and c["status"] in (PASS, FAIL))
    if violations_total:
        verdict = FAIL
    elif withheld == 0 and checked == len(checks):
        verdict = PASS
    else:
        verdict = WITHHELD
    return {
        "schema": CONTRACTS_SCHEMA,
        "checks": checks,
        "contracts_total": len(checks),
        "interface_contracts_total": interface_total,
        "interface_contracts_checked": interface_checked,
        "checked_total": checked,
        "withheld_total": withheld,
        "violations_total": violations_total,
        "verdict": verdict,
        "anti_vacuity_note": "任一侧无接口证据的契约记 WITHHELD；『0 违反』只在全部契约"
                             "真被检查过时才算 PASS。",
    }


__all__ = ["PASS", "FAIL", "WITHHELD", "ContractsError", "load_contracts", "interfaces_of",
           "check_interface_contract", "check_registry", "evaluate"]