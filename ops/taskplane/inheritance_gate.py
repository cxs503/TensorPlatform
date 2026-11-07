#!/usr/bin/env python3
"""inheritance_gate —— 综合继承门（跨里程碑一致性裁决 + 继承就绪度评分）。

定位
----
战役 DAG 的门节点（node_type=gate，下文称"继承门"）需要可复算的裁决器：上游若干
里程碑的节点是否真的验收通过、跨节点/跨求解器的同一关键量是否互裁一致、治理类节点
是否成熟、最终继承就绪度是否达标。人工读报告下判不可复算、不可审计，因此判据全部
做成**纯函数**：

    evaluate_inheritance_gate(snapshot, config) -> dict

同一 snapshot + config 必得逐字节相同报告（不取 wall clock，`input_digest` 由内容派生）。

零领域语义（TensorPlatform 铁律）
-----------------------------
本模块**不含任何领域词条 / 域判据 / 域算例**：
  * 哪些量是关键量、容差多少、哪些节点属治理类 —— 全部由 ``config`` 注入；
  * 判据文本来自 ``campaign_nodes.metadata.acceptance_criteria``（进 snapshot 前由
    平台入库），本模块只认通用语法 ``<key> <op> <number>[%]``；
  * 测量值来自节点自带证据块 ``inheritance_evidence``（契约见
    ``docs/specs/inheritance_evidence_contract.md``），不由本模块猜测。

节点验收（node_accepted）条件
----------------------------
  A1 节点状态 ∈ config.done_statuses（默认 {"done"}）；
  A2 已声明 reproduction_verdict 必须 pass，fail 一律不验收；config 默认
     require_reproduction_verdict=True（平台红队规则：无复现判决的"完成"不算完成）；
  A3 关联 mission 不得是失败终态；config.require_mission 时缺 mission 链接不算验收；
  A4 config.require_evidence 时须有报告或证据工件（可溯源）；
  A5 声明的每条验收判据都必须**已测且通过**；``unproven`` 一律不算通过
     （有判据文本却拿不出测量值 = 不可裁，不是 pass）。

判据求值
--------
  ``"C_x_vs_y < 8%"``  → key=c_x_vs_y op=lt threshold=0.08（百分点需 unit 注明）
  ``"err <= 0.05"``    → le 0.05
  ``"rho = 1"``        → eq 1（config.eq_abs_tol 内视为相等）
  ``"pass_rate >= 2/3"`` → ge 0.6667（分数阈值按比值）
  ``"pytest_all_green"`` → 布尔/质性判据：证据项需 ``{"pass": true, "evidence": "artifacts/..."}``，
      只要声明不要工件 = unproven（不可复算不算通过）
  证据项 ``{"value": 0.061, "unit": "fraction"}``；缺 value 或百分点判据缺 unit 一律
  unproven —— **不做量纲猜测**（6.1 vs 0.061 的静默换算是造假通道）。

跨节点一致性
------------
  证据块 ``quantities: {key: value | {value,tol,reference,rtol}}``。同一 key 出现
  ≥2 个已验收节点 → ``spread=(max-min)/mean`` 与容差比；带 reference 的条目另做
  ``|value-reference|/|reference| <= rtol``。只有一个来源的量不算交叉裁决（记 fail）。
  config.consistency.min_shared_keys 保证"没有任何互裁量"不能白拿分。
  config.exclude_node_ids 可排除门自身等节点（排除项在报告 excluded_nodes 留痕）。

评分（config.weights；不适用分量自动重归一化）
-------------------------------------------
  milestone   里程碑完成率（里程碑内节点全部 accepted 才算完成）
  criteria    已声明判据通过率（passed / declared）
  consistency 互裁检查通过率
  governance  治理节点验收率（config.governance.node_ids 或节点/里程碑 metadata
              inheritance_class='governance' 注入；无治理节点则该分量不适用）
verdict="ready" 需 score >= config.min_score **且** 四条硬门全过：
  H1 所有被覆盖里程碑完成；H2 治理节点全验收；H3 互裁全通过；H4 score 达标。

CLI
---
    # 离线复算（snapshot/config 都是 JSON —— 可审计、可进 CI）
    python3 ops/taskplane/inheritance_gate.py \
        --snapshot artifacts/in28_g25/snapshot.json \
        --config   artifacts/in28_g25/gate_config.json \
        --json-out artifacts/in28_g25/inheritance_gate.json \
        --md-out   artifacts/in28_g25/inheritance_gate.md

    # 从平台只读拉快照
    python3 ops/taskplane/inheritance_gate.py --api http://127.0.0.1:8010/api/v1 \
        --token "$TOKEN" --plan 83 --snapshot-out artifacts/plan83_snapshot.json

退出码：0=ready，2=not_ready，3=输入错误。**未就绪绝不返回 0。**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "1.0.0"
EVIDENCE_KEY = "inheritance_evidence"
EVIDENCE_FENCE = "inheritance-evidence"

DONE_STATUSES = frozenset({"done"})
SUCCESS_MISSION = frozenset({"done", "succeeded", "success", "completed", "finished"})
FAILED_MISSION = frozenset({"failed", "cancelled", "canceled", "error", "aborted", "timeout"})
FAIL_VERDICTS = frozenset({"fail", "failed", "reject", "rejected"})
PASS_VERDICTS = frozenset({"pass", "passed", "ok"})

DEFAULT_WEIGHTS = {"milestone": 0.40, "criteria": 0.25, "consistency": 0.20,
                   "governance": 0.15}
DEFAULT_MIN_SCORE = 0.70
DEFAULT_CONSISTENCY_TOL = 0.10

_OP_ALIASES = {"<": "lt", "<=": "le", "≤": "le", ">": "gt", ">=": "ge", "≥": "ge",
               "=": "eq", "==": "eq", "!=": "ne"}
_PCT_UNITS = {"%", "pct", "percent"}
_FRACTION_UNITS = {"fraction", "rel", "relative", "ratio", "1", "-"}

_NUM_RE = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*/\s*[-+]?\d+(?:\.\d+)?)?"
_CRIT_RE = re.compile(
    r"^(?P<key>.{1,80}?)\s*(?P<op><=|>=|≤|≥|!=|==|<|>|=)\s*"
    r"(?P<num>" + _NUM_RE + r")\s*(?P<pct>%)?\s*$"
)


def _num(value: str) -> float:
    """数值词法：``2/3`` 形式的分数阈值按比值求值，其余按 float。"""
    text = str(value).replace(" ", "")
    if "/" in text:
        a, b = text.split("/", 1)
        return float(a) / float(b)
    return float(text)
_FENCE_RE = re.compile(r"```(?P<info>[A-Za-z0-9_\-]*)[ \t]*\n(?P<body>.*?)\n```", re.DOTALL)


# ─────────────────────────── 基础工具 ───────────────────────────

def normalize_key(text: Any) -> str:
    """判据/量名归一化：小写、空白折叠、去掉非 [0-9a-z_] 字符。

    只做**字面**归一化，不做同义词映射（同义映射=领域知识，必须由 config 注入）。
    """
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9a-z_]", "", s)
    return re.sub(r"_+", "_", s).strip("_")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _metadata(obj: Mapping[str, Any]) -> dict:
    meta = obj.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    return meta if isinstance(meta, Mapping) else {}


# ─────────────────────────── 判据解析 ───────────────────────────

def parse_criterion(raw: Any) -> dict:
    """把一条判据文本解析为 {raw,key,op,threshold,percent,parsed}。"""
    text = str(raw).strip()
    m = _CRIT_RE.match(text)
    if not m:
        return {"raw": text, "key": normalize_key(text), "op": None,
                "threshold": None, "percent": None, "parsed": False}
    key = normalize_key(m.group("key"))
    return {"raw": text, "key": key, "op": _OP_ALIASES[m.group("op")],
            "threshold": _num(m.group("num")), "percent": bool(m.group("pct")),
            "parsed": bool(key)}


def parse_criteria(raw_items: Iterable[Any]) -> list[dict]:
    """批量解析；支持纯文本条目与结构化 dict 条目。"""
    out = []
    for item in raw_items or []:
        if isinstance(item, Mapping):
            key = normalize_key(item.get("key") or item.get("metric") or item.get("name"))
            op = _OP_ALIASES.get(str(item.get("op", "")).strip())
            threshold = _as_float(item.get("threshold", item.get("limit")))
            if key and op and threshold is not None:
                out.append({"raw": item.get("raw") or key, "key": key, "op": op,
                            "threshold": threshold,
                            "percent": bool(item.get("percent")), "parsed": True})
            else:
                out.append(parse_criterion(item.get("raw") or json.dumps(item, sort_keys=True)))
        else:
            out.append(parse_criterion(item))
    return out


# ─────────────────────────── 证据抽取 ───────────────────────────

def _evidence_from_markdown(markdown: str) -> dict | None:
    """从 ```inheritance-evidence / ```json 围栏中抽证据块。

    契约：``inheritance-evidence`` 围栏体内可直接是证据对象，也可包一层
    ``{"inheritance_evidence": {...}}``；``json`` 围栏必须包一层（否则任何 JSON 代码块
    都会被误当证据）。
    """
    if not markdown:
        return None
    for m in _FENCE_RE.finditer(markdown):
        info = (m.group("info") or "").strip().lower()
        if info not in (EVIDENCE_FENCE, "json", ""):
            continue
        body = m.group("body").strip()
        if info != EVIDENCE_FENCE and EVIDENCE_KEY not in body:
            continue
        try:
            obj = json.loads(body)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, Mapping):
            if isinstance(obj.get(EVIDENCE_KEY), Mapping):
                return dict(obj[EVIDENCE_KEY])
            if info == EVIDENCE_FENCE and obj:
                return dict(obj)
    return None


def extract_evidence(reports: Sequence[Mapping[str, Any]]) -> tuple[dict, str | None, list[str]]:
    """从节点报告集中抽证据。优先级：报告顶层 > metadata > markdown 围栏。

    多份命中时取**最后一份**（调用方按 created_at 升序传入 = 最新证据），其余进 warnings。
    """
    warnings: list[str] = []
    hits: list[tuple[str, dict]] = []
    for rep in reports or []:
        rid = str(rep.get("id") or rep.get("report_id") or "?")
        direct = rep.get(EVIDENCE_KEY)
        if isinstance(direct, Mapping):
            hits.append((f"report:{rid}", dict(direct)))
            continue
        meta = rep.get("metadata")
        if isinstance(meta, Mapping) and isinstance(meta.get(EVIDENCE_KEY), Mapping):
            hits.append((f"report:{rid}#metadata", dict(meta[EVIDENCE_KEY])))
            continue
        md = _evidence_from_markdown(rep.get("markdown") or "")
        if md is not None:
            hits.append((f"report:{rid}#fence", md))
    if not hits:
        return {}, None, warnings
    if len(hits) > 1:
        warnings.append("multiple_evidence_sources:" + ",".join(h[0] for h in hits[:-1]))
    source, evidence = hits[-1]
    return evidence, source, warnings


def load_evidence_file(path: str | Path, roots: Sequence[str | Path] = ()) -> dict | None:
    """读本地证据 JSON；路径须落在 roots 内，防跨仓同名工件误判。"""
    rel = str(path).replace("\\", "/").lstrip("./")
    candidates: list[Path] = []
    for root in roots or (".",):
        root_p = Path(root)
        name = root_p.name.rstrip("/")
        if name and rel.startswith(name + "/"):
            candidates.append(Path(rel))
        candidates.append(root_p / rel)
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            obj = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(obj, Mapping):
            if isinstance(obj.get(EVIDENCE_KEY), Mapping):
                return dict(obj[EVIDENCE_KEY])
            return dict(obj)
    return None


def _acceptance_map(evidence: Mapping[str, Any]) -> dict:
    """证据块里的被测量判据值：{normalized_key: entry}。"""
    out: dict[str, dict] = {}
    for field in ("acceptance", "criteria", "measurements", "metrics"):
        block = evidence.get(field)
        if isinstance(block, Mapping):
            for k, v in block.items():
                out[normalize_key(k)] = dict(v) if isinstance(v, Mapping) else {"value": v}
    return out


def _quantity_map(evidence: Mapping[str, Any]) -> dict:
    out: dict[str, dict] = {}
    block = evidence.get("quantities")
    if isinstance(block, Mapping):
        for k, v in block.items():
            out[normalize_key(k)] = dict(v) if isinstance(v, Mapping) else {"value": v}
    return out


# ─────────────────────────── 判据求值 ───────────────────────────

def evaluate_criterion(crit: Mapping[str, Any], entry: Mapping[str, Any] | None,
                       eq_abs_tol: float = 1e-3) -> dict:
    """单条判据裁决：passed / failed / unproven（unproven 不算通过）。"""
    base = {"raw": crit.get("raw"), "key": crit.get("key"), "op": crit.get("op"),
            "threshold": crit.get("threshold"), "percent": crit.get("percent"),
            "measured": None, "unit": None, "status": "unproven", "reason": None}
    if not crit.get("parsed"):
        # 布尔/质性判据：无算符无数值，只能凭「显式通过声明 + 工件指针」验收。
        # 只要声明不要工件 = 不可复算 → unproven（与数值判据同一纪律）。
        if entry is None:
            base["reason"] = "no_measurement_for_criterion"
            return base
        flag = entry.get("pass", entry.get("passed", entry.get("ok")))
        ref = entry.get("evidence") or entry.get("artifact") or entry.get("ref") \
            or entry.get("artifacts")
        if isinstance(ref, (list, tuple)):
            ref = next((str(x) for x in ref if str(x).strip()), "")
        base["attestation"] = bool(flag)
        base["evidence_ref"] = str(ref) if ref else None
        if flag is False:
            base["status"], base["reason"] = "failed", "attestation_failed"
            return base
        if flag is not True:
            base["reason"] = "attestation_missing"
            return base
        if not ref:
            base["reason"] = "attestation_without_artifact"
            return base
        base["status"] = "passed"
        return base
    if entry is None:
        base["reason"] = "no_measurement_for_criterion"
        return base
    value = _as_float(entry.get("value", entry.get("measured")))
    if value is None:
        base["reason"] = "measurement_missing_value"
        return base
    unit = str(entry.get("unit", "")).strip().lower() or None
    base["unit"] = unit
    base["measured"] = value

    threshold = float(crit["threshold"])
    if crit.get("percent") or entry.get("percent") is True:
        if unit is None:
            if entry.get("percent") is True:
                unit, base["unit"] = "pct", "pct"
            else:
                base["reason"] = "pct_criterion_requires_unit"
                return base
        if unit in _PCT_UNITS:
            measured = value / 100.0
        elif unit in _FRACTION_UNITS:
            measured = value
        else:
            base["reason"] = f"unknown_unit:{unit}"
            return base
        base["measured_normalized"] = measured
        threshold = threshold / 100.0
    else:
        measured = value

    tol = _as_float(entry.get("tol"))
    op = crit["op"]
    if op == "lt":
        ok = measured < threshold
    elif op == "le":
        ok = measured <= threshold
    elif op == "gt":
        ok = measured > threshold
    elif op == "ge":
        ok = measured >= threshold
    elif op == "eq":
        ok = abs(measured - threshold) <= (tol if tol is not None else eq_abs_tol)
    elif op == "ne":
        ok = abs(measured - threshold) > (tol if tol is not None else eq_abs_tol)
    else:
        base["reason"] = "unsupported_op"
        return base
    base["status"] = "passed" if ok else "failed"
    base["reason"] = None if ok else "measurement_out_of_limit"
    return base


# ─────────────────────────── 节点裁决 ───────────────────────────

def _reports_for_mission(reports: Mapping[Any, Sequence[Mapping[str, Any]]],
                         mission_id: Any) -> list[dict]:
    if mission_id is None:
        return []
    for key in (str(mission_id), mission_id):
        if key in reports:
            items = list(reports[key])
            return sorted(items, key=lambda r: str(r.get("created_at") or ""))
    return []


def evaluate_node(node: Mapping[str, Any], mission: Mapping[str, Any] | None,
                  reports: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
                  roots: Sequence[str | Path] = ()) -> dict:
    """单节点验收裁决 + 判据逐条状态 + 证据溯源路径。"""
    done_statuses = {str(s).lower() for s in config.get("done_statuses", DONE_STATUSES)}
    require_verdict = bool(config.get("require_reproduction_verdict", True))
    require_mission = bool(config.get("require_mission", True))
    require_evidence = bool(config.get("require_evidence", True))
    eq_abs_tol = float(config.get("eq_abs_tol", 1e-3))

    meta = _metadata(node)
    status = str(node.get("status") or "").lower()
    raw_verdict = node.get("reproduction_verdict")
    verdict = str(raw_verdict).lower() if raw_verdict is not None else None
    mission_status = str((mission or {}).get("status") or "").lower() or None
    mission_id = node.get("mission_id")

    warnings: list[str] = []
    blockers: list[str] = []

    if status not in done_statuses:
        blockers.append(f"node_not_done(status={status or 'unknown'})")
    if verdict in FAIL_VERDICTS:
        blockers.append("reproduction_verdict=fail")
    elif require_verdict and verdict not in PASS_VERDICTS:
        blockers.append("reproduction_verdict_missing")
    if mission_status in FAILED_MISSION:
        blockers.append(f"mission_failed(status={mission_status})")
    if require_mission and mission_status is None:
        blockers.append("mission_missing")

    evidence, source, ev_warn = extract_evidence(reports)
    warnings.extend(ev_warn)
    if not evidence:
        ref = meta.get(EVIDENCE_KEY) or meta.get("inheritance_evidence_path")
        if isinstance(ref, str):
            loaded = load_evidence_file(ref, roots)
            if loaded is not None:
                evidence, source = loaded, f"file:{ref}"
            else:
                warnings.append(f"evidence_file_unresolved:{ref}")

    artifacts: list[str] = []
    if isinstance(evidence.get("artifacts"), Sequence) and not isinstance(evidence.get("artifacts"), str):
        artifacts = [str(a) for a in evidence["artifacts"]]
    if require_evidence and not evidence and not reports:
        blockers.append("no_evidence")

    declared = parse_criteria(meta.get("acceptance_criteria") or [])
    acc_map = _acceptance_map(evidence)
    criteria_eval = [evaluate_criterion(c, acc_map.get(c["key"]), eq_abs_tol) for c in declared]
    declared_n = len(criteria_eval)
    proven_n = sum(1 for c in criteria_eval if c["status"] in ("passed", "failed"))
    passed_n = sum(1 for c in criteria_eval if c["status"] == "passed")
    for c in criteria_eval:
        if c["status"] != "passed":
            reason = f"({c['reason']})" if c.get("reason") else ""
            blockers.append(f"criterion_{c['status']}{reason}:{c['raw']}")

    status_ok = (status in done_statuses and verdict not in FAIL_VERDICTS
                 and (not require_verdict or verdict in PASS_VERDICTS)
                 and mission_status not in FAILED_MISSION
                 and (not require_mission or mission_status is not None))
    evidence_ok = (not require_evidence) or bool(evidence) or bool(reports)
    criteria_ok = declared_n == 0 or passed_n == declared_n
    accepted = bool(status_ok and evidence_ok and criteria_ok and not blockers)

    return {
        "node_id": node.get("id"),
        "ext_id": meta.get("ext_id") or node.get("ext_id"),
        "name": node.get("name"),
        "milestone_id": node.get("milestone_id"),
        "node_type": node.get("node_type"),
        "mission_id": mission_id,
        "node_status": status,
        "reproduction_verdict": verdict,
        "mission_status": mission_status,
        "status_ok": status_ok,
        "evidence_ok": evidence_ok,
        "evidence_source": source,
        "evidence_artifacts": artifacts,
        "criteria_declared": declared_n,
        "criteria_proven": proven_n,
        "criteria_passed": passed_n,
        "criteria": criteria_eval,
        "quantities": _quantity_map(evidence),
        "accepted": accepted,
        "blockers": blockers,
        "warnings": warnings,
    }


# ─────────────────────────── 跨节点一致性 ───────────────────────────

def cross_consistency(node_evals: Sequence[Mapping[str, Any]],
                      config: Mapping[str, Any]) -> dict:
    """同一关键量在多节点间互裁：spread + 基准偏差（带 reference 时）。"""
    tol_conf = config.get("consistency") or {}
    default_tol = float(tol_conf.get("tolerance", DEFAULT_CONSISTENCY_TOL))
    by_key = {normalize_key(k): float(v)
              for k, v in (tol_conf.get("tolerance_by_key") or {}).items()}
    min_shared = int(tol_conf.get("min_shared_keys", 1))
    only_accepted = bool(tol_conf.get("only_accepted_nodes", True))

    collected: dict[str, list[dict]] = {}
    for ev in node_evals:
        if only_accepted and not ev.get("accepted"):
            continue
        for key, entry in (ev.get("quantities") or {}).items():
            value = _as_float(entry.get("value"))
            if value is None:
                continue
            collected.setdefault(key, []).append({
                "node_id": ev.get("node_id"), "ext_id": ev.get("ext_id"), "value": value,
                "tol": _as_float(entry.get("tol")),
                "reference": _as_float(entry.get("reference")),
                "rtol": _as_float(entry.get("rtol"))})

    rows: list[dict] = []
    for key in sorted(collected):
        entries = collected[key]
        tol = by_key.get(key)
        if tol is None:
            declared = [e["tol"] for e in entries if e["tol"] is not None]
            tol = min(declared) if declared else default_tol
        vals = [e["value"] for e in entries]
        mean = sum(vals) / len(vals)
        spread = (max(vals) - min(vals)) / abs(mean) if mean else float("inf")
        checks: list[dict] = []
        if len(vals) >= 2:
            checks.append({"kind": "spread", "metric": spread, "tol": tol,
                           "passed": spread <= tol})
        else:
            checks.append({"kind": "spread", "metric": spread, "tol": tol, "passed": False,
                           "reason": "single_source_no_cross_judgement"})
        for e in entries:
            if e["reference"] is None or not e["reference"]:
                continue
            rtol = e["rtol"] if e["rtol"] is not None else tol
            dev = abs(e["value"] - e["reference"]) / abs(e["reference"])
            checks.append({"kind": "reference", "node_id": e["node_id"], "metric": dev,
                           "tol": rtol, "passed": dev <= rtol})
        rows.append({"key": key, "n_sources": len(vals), "values": vals, "mean": mean,
                     "spread": spread, "tolerance": tol, "checks": checks,
                     "passed": all(c["passed"] for c in checks)})

    judged = [r for r in rows if r["n_sources"] >= 2]
    score = (sum(1 for r in judged if r["passed"]) / len(judged)) if judged else 0.0
    blockers: list[str] = []
    if not rows:
        blockers.append("no_shared_quantities")
    if len(judged) < min_shared:
        blockers.append(f"insufficient_cross_judged_keys({len(judged)}<{min_shared})")
    for r in judged:
        if not r["passed"]:
            blockers.append(f"consistency_violation:{r['key']}"
                            f"(spread={r['spread']:.4g}>tol={r['tolerance']:.4g})")
    return {"keys": rows, "n_keys_total": len(rows), "n_keys_cross_judged": len(judged),
            "score": score, "blockers": blockers, "tolerance_default": default_tol,
            "only_accepted_nodes": only_accepted}


# ─────────────────────────── 治理分量 ───────────────────────────

def _inheritance_class(obj: Mapping[str, Any]) -> str | None:
    return _metadata(obj).get("inheritance_class")


def governance_component(milestones: Sequence[Mapping[str, Any]],
                         node_evals: Sequence[Mapping[str, Any]],
                         config: Mapping[str, Any]) -> dict:
    """治理类节点验收率。治理集合由 config / metadata 注入，模块内不含治理词表。"""
    gov_conf = config.get("governance") or {}
    explicit = [int(x) for x in (gov_conf.get("node_ids") or [])]
    if explicit:
        selected = [ev for ev in node_evals if int(ev["node_id"]) in explicit]
        source = "config.governance.node_ids"
    else:
        classes = {str(c).lower() for c in (gov_conf.get("classes") or ["governance"])}
        gov_ms = {int(m["id"]) for m in milestones
                  if str(_inheritance_class(m) or "").lower() in classes}
        selected = [ev for ev in node_evals
                    if str(_inheritance_class(ev) or "").lower() in classes
                    or int(ev.get("milestone_id") or 0) in gov_ms]
        source = "metadata.inheritance_class"
    if not selected:
        return {"node_ids": [], "source": source, "applicable": False, "n_nodes": 0,
                "n_accepted": 0, "score": 0.0,
                "blockers": ["no_governance_nodes_declared"]}
    passed = sum(1 for ev in selected if ev.get("accepted"))
    return {"node_ids": [ev.get("node_id") for ev in selected], "source": source,
            "applicable": True, "n_nodes": len(selected), "n_accepted": passed,
            "score": passed / len(selected),
            "blockers": [f"governance_node_not_accepted:{ev.get('node_id')}"
                         for ev in selected if not ev.get("accepted")]}


# ─────────────────────────── 综合评分 ───────────────────────────

def _milestone_summary(milestones: Sequence[Mapping[str, Any]],
                       node_evals: Sequence[Mapping[str, Any]],
                       config: Mapping[str, Any]) -> dict:
    covered = config.get("milestones")
    covered_ids = [int(m) for m in covered] if covered else [int(m["id"]) for m in milestones]
    rows = []
    for m in milestones:
        mid = int(m["id"])
        if mid not in covered_ids:
            continue
        nodes = [ev for ev in node_evals if int(ev.get("milestone_id") or 0) == mid]
        accepted = [ev for ev in nodes if ev.get("accepted")]
        rows.append({
            "milestone_id": mid, "name": m.get("name"), "n_nodes": len(nodes),
            "n_accepted": len(accepted),
            "criteria_declared": sum(int(ev.get("criteria_declared") or 0) for ev in nodes),
            "criteria_passed": sum(int(ev.get("criteria_passed") or 0) for ev in nodes),
            "complete": bool(nodes) and len(accepted) == len(nodes),
            "blocking_nodes": [ev.get("node_id") for ev in nodes if not ev.get("accepted")]})
    n_complete = sum(1 for r in rows if r["complete"])
    if not rows:
        return {"milestones": [], "n_complete": 0, "score": 0.0,
                "blockers": ["no_milestones_covered"]}
    return {"milestones": rows, "n_complete": n_complete,
            "score": n_complete / len(rows),
            "blockers": [f"milestone_incomplete:{r['milestone_id']}"
                         for r in rows if not r["complete"]]}


def _renormalize(weights: Mapping[str, float], applicable: Mapping[str, bool]) -> dict:
    active = {k: float(v) for k, v in weights.items() if applicable.get(k, True)}
    total = sum(active.values())
    if total <= 0:
        return {k: 0.0 for k in weights}
    out = {k: 0.0 for k in weights}
    for k, v in active.items():
        out[k] = v / total
    return out


def evaluate_inheritance_gate(snapshot: Mapping[str, Any],
                             config: Mapping[str, Any] | None = None) -> dict:
    """纯函数入口：snapshot + config → 继承就绪度报告（可复算、可审计）。"""
    cfg = dict(config or {})
    min_score = float(cfg.get("min_score", DEFAULT_MIN_SCORE))
    weights_in = dict(DEFAULT_WEIGHTS)
    weights_in.update({k: float(v) for k, v in (cfg.get("weights") or {}).items()})
    roots = cfg.get("roots") or ["."]

    plan = snapshot.get("plan") or {}
    milestones = list(snapshot.get("milestones") or [])
    nodes = list(snapshot.get("nodes") or [])
    missions = snapshot.get("missions") or {}
    reports = snapshot.get("reports") or {}

    excluded_ids = {int(x) for x in (cfg.get("exclude_node_ids") or [])}
    node_evals = []
    excluded: list[dict] = []
    for node in nodes:
        if int(node.get("id") or 0) in excluded_ids:
            excluded.append({"node_id": node.get("id"), "name": node.get("name"),
                             "reason": "excluded_by_config"})
            continue
        mid = node.get("mission_id")
        mission = missions.get(str(mid)) or missions.get(mid) if mid is not None else None
        ev = evaluate_node(node, mission, _reports_for_mission(reports, mid), cfg, roots)
        ev["metadata"] = node.get("metadata")
        node_evals.append(ev)

    ms = _milestone_summary(milestones, node_evals, cfg)
    cons = cross_consistency(node_evals, cfg)
    gov = governance_component(milestones, node_evals, cfg)

    declared = sum(int(ev["criteria_declared"]) for ev in node_evals)
    passed = sum(int(ev["criteria_passed"]) for ev in node_evals)
    criteria_score = (passed / declared) if declared else 0.0
    if declared:
        criteria_blockers = [
            (f"criterion_{c['status']}({c['reason']}):{ev['node_id']}:{c['raw']}"
             if c.get("reason") else
             f"criterion_{c['status']}:{ev['node_id']}:{c['raw']}")
            for ev in node_evals for c in ev["criteria"] if c["status"] != "passed"]
    else:
        criteria_blockers = ["no_criteria_declared_on_any_node"]

    components = {"milestone": ms["score"], "criteria": criteria_score,
                  "consistency": cons["score"], "governance": gov["score"]}
    applicable = {"milestone": True, "criteria": True, "consistency": True,
                  "governance": bool(gov["applicable"])}
    weights = _renormalize(weights_in, applicable)
    score = sum(components[k] * weights[k] for k in components)

    hard_gates = {
        "all_covered_milestones_complete":
            bool(ms["milestones"]) and ms["n_complete"] == len(ms["milestones"]),
        "governance_nodes_accepted": bool(gov["applicable"]) and gov["n_accepted"] == gov["n_nodes"],
        "cross_consistency_within_tolerance": not cons["blockers"],
        "score_above_min": score >= min_score,
    }
    ready = all(hard_gates.values())

    blockers: list[str] = [f"hard_gate_failed:{k}" for k, v in hard_gates.items() if not v]
    blockers.extend(ms["blockers"])
    blockers.extend(cons["blockers"])
    blockers.extend(gov["blockers"])
    blockers.extend(criteria_blockers)
    for ev in node_evals:
        blockers.extend(f"node{ev['node_id']}:{b}" for b in ev["blockers"])
    seen, uniq = set(), []
    for b in blockers:
        if b not in seen:
            seen.add(b)
            uniq.append(b)

    accepted_nodes = [ev for ev in node_evals if ev["accepted"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": {"plan_id": plan.get("id", snapshot.get("plan_id")),
                 "plan_name": plan.get("name") or plan.get("title"),
                 "mode": "inheritance_gate"},
        "input_digest": _digest({"plan": plan, "milestones": milestones, "nodes": nodes,
                                 "missions": missions,
                                 "reports": {str(k): list(v) for k, v in reports.items()}}),
        "config_echo": {
            "min_score": min_score, "weights_input": weights_in,
            "weights_effective": weights,
            "done_statuses": sorted({str(s).lower() for s in cfg.get("done_statuses", DONE_STATUSES)}),
            "require_reproduction_verdict": bool(cfg.get("require_reproduction_verdict", True)),
            "require_mission": bool(cfg.get("require_mission", True)),
            "require_evidence": bool(cfg.get("require_evidence", True)),
            "eq_abs_tol": float(cfg.get("eq_abs_tol", 1e-3)),
            "consistency": cfg.get("consistency") or {},
            "roots": [str(r) for r in roots]},
        "score": round(score, 6),
        "verdict": "ready" if ready else "not_ready",
        "hard_gates": hard_gates,
        "components": {k: round(components[k], 6) for k in components},
        "components_applicable": applicable,
        "node_summary": {
            "n_nodes": len(node_evals), "n_accepted": len(accepted_nodes),
            "acceptance_rate": round(len(accepted_nodes) / len(node_evals), 6)
            if node_evals else 0.0},
        "milestones": ms, "consistency": cons, "governance": gov,
        "nodes": node_evals, "excluded_nodes": excluded, "blockers": uniq,
    }


# ─────────────────────────── 线上快照构建 ───────────────────────────

def _http_get(url: str, token: str | None, timeout: float = 30.0) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_snapshot_from_api(base_url: str, plan_id: int, token: str | None = None,
                            include_report_markdown: bool = True,
                            timeout: float = 30.0) -> dict:
    """只读拉取战役快照：plan / milestones / nodes / missions / reports。"""
    base = base_url.rstrip("/")
    plan = _http_get(f"{base}/tensor/campaigns/plans/{plan_id}", token, timeout)
    if isinstance(plan, Mapping) and isinstance(plan.get("items"), list) and len(plan["items"]) == 1:
        plan = plan["items"][0]
    milestones = list(_http_get(f"{base}/tensor/campaigns/plans/{plan_id}/milestones",
                                token, timeout).get("items", []))
    nodes = list(_http_get(f"{base}/tensor/campaigns/plans/{plan_id}/nodes",
                           token, timeout).get("items", []))

    missions: dict[str, dict] = {}
    reports: dict[str, list[dict]] = {}
    for node in nodes:
        mid = node.get("mission_id")
        if mid is None or str(mid) in missions:
            continue
        try:
            m = _http_get(f"{base}/tensor/missions/{mid}", token, timeout)
            missions[str(mid)] = m.get("mission", m) if isinstance(m, Mapping) else {"id": mid}
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            missions[str(mid)] = {"id": mid, "status": "unknown"}
        try:
            items = list(_http_get(f"{base}/tensor/reports?mission_id={mid}",
                                   token, timeout).get("items", []))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            items = []
        detailed: list[dict] = []
        for item in items:
            rid = item.get("id")
            if include_report_markdown and rid is not None:
                try:
                    detail = _http_get(f"{base}/tensor/reports/{rid}", token, timeout)
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
                    detail = item
                detailed.append(dict(detail) if isinstance(detail, Mapping) else item)
            else:
                detailed.append(item)
        reports[str(mid)] = detailed

    return {"snapshot_meta": {"source": "api", "base_url": base, "plan_id": int(plan_id),
                              "include_report_markdown": bool(include_report_markdown)},
            "plan": plan, "milestones": milestones, "nodes": nodes,
            "missions": missions, "reports": reports}


# ─────────────────────────── Markdown 摘要 ───────────────────────────

def render_markdown(report: Mapping[str, Any]) -> str:
    gate = report.get("gate", {})
    comp = report.get("components", {})
    w = report.get("config_echo", {}).get("weights_effective", {})
    ns = report.get("node_summary", {})
    lines = [
        f"# 继承门报告 · plan {gate.get('plan_id')} — {gate.get('plan_name')}",
        "",
        f"- 裁决：**{report.get('verdict')}**；综合评分 **{report.get('score')}**"
        f"（阈值 {report.get('config_echo', {}).get('min_score')}）",
        f"- 输入摘要 sha256：`{report.get('input_digest')}`",
        f"- 节点验收：{ns.get('n_accepted')}/{ns.get('n_nodes')}",
        "", "## 分量", "", "| 分量 | 得分 | 权重 | 适用 |", "|---|---|---|---|",
    ]
    for k in ("milestone", "criteria", "consistency", "governance"):
        lines.append(f"| {k} | {comp.get(k)} | {w.get(k, 0):.3f} | "
                     f"{report.get('components_applicable', {}).get(k)} |")
    lines += ["", "## 硬门", "", "| 硬门 | 通过 |", "|---|---|"]
    for k, v in (report.get("hard_gates") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 节点验收摘要", "",
              "| 节点 | 名称 | 里程碑 | 状态 | 复现判决 | mission | 判据 pass/declared | 验收 |",
              "|---|---|---|---|---|---|---|---|"]
    for ev in report.get("nodes", []):
        lines.append(
            f"| {ev.get('node_id')} | {ev.get('name')} | {ev.get('milestone_id')} | "
            f"{ev.get('node_status')} | {ev.get('reproduction_verdict')} | "
            f"{ev.get('mission_id')}/{ev.get('mission_status')} | "
            f"{ev.get('criteria_passed')}/{ev.get('criteria_declared')} | "
            f"{'PASS' if ev.get('accepted') else 'FAIL'} |")
    lines += ["", "## 里程碑", "", "| 里程碑 | 名称 | 完成/总 | 完成 |", "|---|---|---|---|"]
    for m in (report.get("milestones") or {}).get("milestones", []):
        lines.append(f"| {m['milestone_id']} | {m['name']} | {m['n_accepted']}/{m['n_nodes']} | "
                     f"{m['complete']} |")
    cons = report.get("consistency") or {}
    lines += ["", "## 跨节点一致性", "",
              f"- 交叉裁决量 {cons.get('n_keys_cross_judged')} / 收集量 {cons.get('n_keys_total')}；"
              f"得分 {cons.get('score')}；默认容差 {cons.get('tolerance_default')}"]
    if cons.get("keys"):
        lines += ["", "| 量 | 源数 | mean | spread | 容差 | 通过 |", "|---|---|---|---|---|---|"]
        for k in cons["keys"]:
            lines.append(f"| {k['key']} | {k['n_sources']} | {k['mean']:.6g} | "
                         f"{k['spread']:.4g} | {k['tolerance']:.4g} | {k['passed']} |")
    lines += ["", "## 阻塞项", ""]
    lines += [f"- `{b}`" for b in report.get("blockers", [])] or ["- 无"]
    return "\n".join(lines) + "\n"


# ─────────────────────────── CLI ───────────────────────────

def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                 encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="综合继承门：跨里程碑一致性裁决 + 就绪度评分")
    ap.add_argument("--snapshot", help="离线快照 JSON")
    ap.add_argument("--snapshot-out", help="把拉取到的快照落盘")
    ap.add_argument("--api", help="平台 API base，如 http://127.0.0.1:8010/api/v1")
    ap.add_argument("--plan", type=int, help="战役 plan_id（配合 --api）")
    ap.add_argument("--token", default=None, help="Bearer token（缺省读 $TENSOR_TOKEN）")
    ap.add_argument("--no-report-markdown", action="store_true",
                    help="不拉报告正文（快照更小，但无法解析围栏证据）")
    ap.add_argument("--config", help="gate 配置文件 JSON")
    ap.add_argument("--json-out", help="裁决报告 JSON 输出路径")
    ap.add_argument("--md-out", help="裁决报告 Markdown 摘要输出路径")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    token = args.token
    if token is None:
        import os
        token = os.environ.get("TENSOR_TOKEN") or os.environ.get("TOKEN")

    snapshot: dict | None = None
    if args.snapshot:
        try:
            snapshot = _load_json(args.snapshot)
        except (OSError, ValueError) as exc:
            print(f"snapshot 读取失败: {exc}", file=sys.stderr)
            return 3
    elif args.api and args.plan:
        try:
            snapshot = build_snapshot_from_api(args.api, args.plan, token,
                                               include_report_markdown=not args.no_report_markdown)
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
            print(f"API 拉取失败: {exc}", file=sys.stderr)
            return 3
        if args.snapshot_out:
            _write_json(args.snapshot_out, snapshot)
    else:
        print("需要 --snapshot 或 (--api 且 --plan)", file=sys.stderr)
        return 3

    config = {}
    if args.config:
        try:
            config = _load_json(args.config) or {}
        except (OSError, ValueError) as exc:
            print(f"config 读取失败: {exc}", file=sys.stderr)
            return 3

    report = evaluate_inheritance_gate(snapshot, config)

    if args.json_out:
        _write_json(args.json_out, report)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(report), encoding="utf-8")
    if not args.quiet:
        print(render_markdown(report))
    return 0 if report["verdict"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())