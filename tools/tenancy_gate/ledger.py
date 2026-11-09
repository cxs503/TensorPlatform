#!/usr/bin/env python3
"""冲突事件台账：append-only JSONL + 查询 + 复算（审计链）。

三条纪律
--------
1. **append-only**：只追加，不改不删已有行；同一事件重放不产生第二行（``event_id``
   由内容派生，重复即跳过）。因此"同一 finish 重放 3 次"后**文件逐字节不变**。
2. **可复算**：入库的不只是结论，还有输入（``payload.inputs``）。任何人事后可用
   :func:`recompute_matrix` 拿当时的配置重跑一遍，比对 ``payload.matrix_digest``——
   结论对不对可以重判，而不是只能信记录。
3. **可篡改检测**：每条事件带 ``prev``（前一条的 ``event_id``），构成链表；
   :func:`verify_chain` 重算 ``event_id`` 与链表，任何改行/删行/换序都会被指出来。

事件种类（``kind``）
--------------------
``lease_decision``    派单前路径预留裁决（:mod:`tools.tenancy_gate.lease`）
``intake_matrix``     收单冲突矩阵（:mod:`tools.tenancy_gate.intake`）
``rehearsal``         推送前三方合并预演（:mod:`tools.tenancy_gate.rehearsal`）
"""
from __future__ import annotations

import json
from pathlib import Path

from .intake import conflict_matrix
from .model import FinishRecord, TenancyError, canonical_json, digest, parse_ts

__all__ = [
    "EVENT_SCHEMA",
    "make_event",
    "append_events",
    "read_events",
    "query_events",
    "verify_chain",
    "recompute_matrix",
    "record_lease_decision",
    "record_intake",
    "record_rehearsal",
    "strip_volatile",
]

EVENT_SCHEMA = "tenancy_gate/event/v1"


def _event_id(kind: str, ts: str, payload: dict) -> str:
    return digest({"kind": kind, "ts": ts, "payload": payload})


def make_event(kind: str, payload: dict, ts: str, prev: str = "") -> dict:
    if not kind:
        raise TenancyError("missing_kind")
    parse_ts(ts)
    return {
        "schema": EVENT_SCHEMA,
        "kind": str(kind),
        "ts": str(ts),
        "prev": str(prev),
        "event_id": _event_id(kind, str(ts), payload),
        "payload": payload,
    }


def read_events(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def append_events(path: str | Path, events) -> dict:
    """幂等追加：``event_id`` 已存在则跳过；返回 ``{appended, skipped}``。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_events(p)
    seen = {e.get("event_id") for e in existing}
    prev = existing[-1]["event_id"] if existing else ""
    appended = 0
    skipped = 0
    lines = []
    for event in events:
        eid = event.get("event_id") or _event_id(event["kind"], event["ts"], event["payload"])
        if eid in seen:
            skipped += 1
            continue
        record = dict(event)
        record.setdefault("schema", EVENT_SCHEMA)
        record["event_id"] = eid
        record["prev"] = prev
        lines.append(canonical_json(record))
        seen.add(eid)
        prev = eid
        appended += 1
    if lines:
        with p.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return {"appended": appended, "skipped": skipped, "path": str(p)}


def query_events(path: str | Path, kind: str | None = None, repo: str | None = None,
                 mission_id: str | None = None, since: str | None = None,
                 until: str | None = None) -> list[dict]:
    """按种类 / 仓 / 任务 / 时间窗过滤（条件全部 AND）。"""
    lo = parse_ts(since) if since else None
    hi = parse_ts(until) if until else None
    out = []
    for event in read_events(path):
        if kind and event.get("kind") != kind:
            continue
        if lo or hi:
            ts = parse_ts(event["ts"])
            if lo and ts < lo:
                continue
            if hi and ts > hi:
                continue
        payload = event.get("payload") or {}
        if repo and repo not in _repos_of(payload):
            continue
        if mission_id and mission_id not in _missions_of(payload):
            continue
        out.append(event)
    return out


def _walk(payload, key: str) -> list[str]:
    found = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key and isinstance(v, str):
                    found.append(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return found


def _repos_of(payload) -> set[str]:
    return set(_walk(payload, "repo"))


def _missions_of(payload) -> set[str]:
    return set(_walk(payload, "mission_id"))


def verify_chain(path: str | Path) -> dict:
    """重算每条事件的 ``event_id`` 与 ``prev`` 链，报告不一致处（审计链自检）。"""
    events = read_events(path)
    problems = []
    prev = ""
    for idx, event in enumerate(events):
        expect_id = _event_id(event.get("kind", ""), event.get("ts", ""), event.get("payload") or {})
        if event.get("event_id") != expect_id:
            problems.append({"line": idx + 1, "problem": "event_id_mismatch", "event_id": event.get("event_id")})
        if (event.get("prev") or "") != prev:
            problems.append({"line": idx + 1, "problem": "prev_mismatch", "prev": event.get("prev"), "expected": prev})
        prev = event.get("event_id") or ""
    return {"events": len(events), "ok": not problems, "problems": problems}


def recompute_matrix(event: dict, config: dict) -> dict:
    """拿事件里存的输入重跑冲突矩阵，与入库结论比对（可复算判据）。"""
    if event.get("kind") != "intake_matrix":
        raise TenancyError("event_not_intake_matrix")
    inputs = (event.get("payload") or {}).get("inputs") or {}
    finish = FinishRecord.from_dict(inputs["finish"])
    matrix = conflict_matrix(finish, inputs.get("peers") or [], config, now_ts=inputs.get("now_ts"))
    stored = (event.get("payload") or {}).get("matrix_digest")
    return {"recomputed_digest": matrix["digest"], "stored_digest": stored,
            "match": matrix["digest"] == stored, "matrix": matrix}


def record_lease_decision(path, decision: dict, ts: str) -> dict:
    return append_events(path, [make_event("lease_decision", decision, ts)])


def record_intake(path, matrix: dict, finish: FinishRecord, peers, ts: str,
                  now_ts: str | None = None) -> dict:
    payload = {
        "inputs": {
            "finish": {
                "mission_id": finish.mission_id,
                "repo": finish.repo,
                "changed_files": list(finish.changed_files),
                "ts": finish.ts,
                "path_globs": list(finish.path_globs),
            },
            "peers": list(peers),
            "now_ts": now_ts or finish.ts,
        },
        "matrix_digest": matrix["digest"],
        "decision": matrix["decision"],
        "reason": matrix["reason"],
        "conflict_pairs": matrix["conflict_pairs"],
        "scope_violations": matrix["scope_violations"],
    }
    return append_events(path, [make_event("intake_matrix", payload, ts)])


VOLATILE_KEYS = ("elapsed_ms",)


def strip_volatile(obj: dict) -> dict:
    """去掉纯观测字段（耗时），让事件 ``event_id`` 只由判定内容决定 ⇒ 重放幂等。"""
    return {k: v for k, v in obj.items() if k not in VOLATILE_KEYS}


def record_rehearsal(path, report: dict, ts: str, kind: str = "rehearsal") -> dict:
    return append_events(path, [make_event(kind, strip_volatile(report), ts)])