#!/usr/bin/env python3
"""Acceptance-queue triage for the TensorPlatform mission fleet (READ-ONLY).

Purpose
-------
The fleet accumulated 150+ missions in ``awaiting_acceptance`` where real
deliverables and empty shells are interleaved (missions that never occupied an
agent session, missions without any report, missions whose report carries no
parsable commit sha).  Owners/verifiers cannot rank the queue by value and the
lineage KPI is distorted.  This tool produces an *evidence-based triage queue*:

    artifacts/acceptance_queue.json          machine-readable, one entry/mission
    artifacts/acceptance_queue.md            human table, accept -> verify -> rerelease -> revoke
    artifacts/acceptance_queue_actions.png   distribution bars (action x repo / x model)
    artifacts/acceptance_input_snapshot.json frozen input, replayable offline

READ-ONLY CONTRACT
------------------
Only HTTP GET is issued (``/tensor/missions``, ``/tensor/reports``,
``/tensor/reports/{id}``).  No POST/PUT/PATCH/DELETE is ever sent, so no mission
status can be changed by this tool.  ``HttpTransport`` exposes ``get`` only.
The single POST in the module, ``login()``, only exchanges credentials for a
bearer token at ``/auth/login``; it is skipped when ``--token``/``$TOKEN`` given.

Decision rules (first match wins; precedence is echoed into the JSON artifact)
-----------------------------------------------------------------------------
E0  has_report       = a report whose ``mission_id`` equals the mission id exists
                       (``report_link = api``) OR a report id mentioned in the
                       result text (``报告 id=1653`` / ``/tensor/reports/1653``)
                       resolves through ``GET /tensor/reports/{id}``
                       (``report_link = mentioned``)
    has_session      = ``session_id`` / ``agent_session_id`` column non-null, OR
                       an agent_session_id UUID embedded in ``result`` /
                       ``result.evidence.summary`` (finding F1)
    commit_evidence  = >=1 commit sha parsed from mission result / report
                       summary / report markdown; otherwise ``unknown``
                       (never guessed)
    criteria_computable = goal text matches >=1 rule C1..C6 below

    revoke     : no report evidence and no session evidence and no commit sha
                 -> empty shell, nothing was ever delivered
    rerelease  : no report evidence but session/commit evidence exists
                 -> work happened, the report is missing: send back
    rerelease  : has_report and commit_evidence == unknown
                 -> report without provenance: resubmit with commit sha
    accept     : has_report and commit found and criteria_computable and green
                 -> evidence chain complete + machine-checkable criteria + a
                    green self-check receipt in ``result``
    verify     : has_report and commit found and (not criteria_computable or
                 not green) -> a verifier must replay the claim

criteria_computable rules (regex on the mission ``goal``)
----------------------------------------------------------
    C1 error threshold   (误差|偏差|relative error) <cmp> <number> [%]
    C2 test count        <cmp> <n> [例条个] | (pytest|测试) ... (>=|至少) n
                         | <n> 例 ... 全绿/通过
    C3 zero/equality     (差值|差异|误差) = 0 | (=|等于) 0
    C4 byte-identity     byte-identical | 逐字节相同 | 二进制一致
    C5 coverage/rate     (覆盖率|通过率|成功率|准确率|precision|recall) <cmp> <n>
    C6 metric target     (R2|F1|MAPE|RMSE|MAE|Cb|L/B|B/T|精度|耗时|内存|延迟)
                         within 20 chars of <cmp> <number>
    where cmp = <, <=, ≤, >, >=, ≥

Fleet findings that motivated these evidence definitions (measured, see report)
---------------------------------------------------------------------------
F1  the top-level ``session_id`` column is NULL for ~96% of the fleet, yet the
    session UUID is present in the ``result`` payload of the very same missions
    -> "session_id IS NULL" is NOT evidence that no session ever ran.
F2  some missions shipped a published report whose ``tensor_reports.mission_id``
    is NULL, so a pure mission_id join under-counts deliverables; a report id
    mentioned in the result text is resolved via GET /tensor/reports/{id} and
    linked as ``report_link = mentioned``.
F3  a parsed sha token is not a verified git object (see limitations).

Known limitations (declared, not hidden)
----------------------------------------
* Reports are listed per mission because ``GET /tensor/reports`` supports no
  ``offset`` parameter (only ``limit<=100``); a mission with >100 reports would
  be truncated at 100.
* ``commit_evidence`` only asserts that a sha *token* is parseable in the
  evidence text; it is NOT verified against git objects (other agents' branches
  are invisible from this worktree), so a fabricated sha still reads ``found``.
  Unparsable-evidence rate and regex false-positive rate are not quantified
  here -- see the report's limitation section.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

ACTIONS = ("accept", "verify", "rerelease", "revoke")
# ordering: ready-to-accept first, then verify, rerelease, revoke last
ACTION_ORDER = {"accept": 0, "verify": 1, "rerelease": 2, "revoke": 3}

# ---------------------------------------------------------------- commit shas
SHA_RE = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")
SHA_CUES = (
    "commit", "commits", "sha", "git", "merge", "head", "revision", "rev",
    "提交", "合并", "哈希",
)
SHA_CONTEXT_CHARS = 24
SHA_DENY = {
    "abcdef0", "abcdefa", "deadbee", "deadbeef", "feedface", "cafebabe",
    "decade", "defaced", "facade", "accede", "baffled", "effaced",
}


def parse_commit_shas(text: str) -> list[str]:
    """Parse commit-sha tokens from free text.

    A candidate must (a) be 7..40 hex chars delimited by non-identifier chars,
    (b) be preceded within SHA_CONTEXT_CHARS chars by a cue word
    (commit/sha/git/merge/HEAD/提交/合并), (c) not be all digits, (d) not be a
    known hex-word false positive.  Anything else is rejected -- we never guess.
    """
    if not text:
        return []
    out: set[str] = set()
    low = text.lower()
    for m in SHA_RE.finditer(text):
        tok = m.group(0)
        if tok.isdigit() or tok in SHA_DENY:
            continue
        window = low[max(0, m.start() - SHA_CONTEXT_CHARS): m.start()]
        if not any(cue in window for cue in SHA_CUES):
            continue
        out.add(tok)
    return sorted(out)


# ------------------------------------------------------------- report ids
REPORT_ID_PATTERNS = (
    re.compile(r"(?:报告|report)\s*(?:id)?\s*[=:：#]\s*(\d{2,6})", re.I),
    re.compile(r"/tensor/reports/(\d{2,6})"),
)


def parse_report_ids(text: str) -> list[int]:
    """Report ids mentioned in free text (``报告 id=1653`` / ``/tensor/reports/1653``)."""
    ids: set[int] = set()
    for rx in REPORT_ID_PATTERNS:
        for m in rx.finditer(text or ""):
            ids.add(int(m.group(1)))
    return sorted(ids)


# ------------------------------------------------------- criteria (machine)
_CMP = r"(?:<|<=|≤|＜|≦|>|>=|≥|＞|≧)"
_NUM = r"\d+(?:\.\d+)?"

CRITERIA_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "C1_error_threshold",
        "(误差|偏差|相对误差|relative error) <cmp> <n> [%]",
        re.compile(
            r"(?:误差|偏差|相对误差|relative\s*error|error)\s*"
            r"(?:应|须|必须|务必)?\s*(?:应当)?\s*" + _CMP + r"\s*" + _NUM + r"\s*%?",
            re.I,
        ),
    ),
    (
        "C2_test_count",
        "<cmp> <n> [例条个] | (pytest|测试) ... (>=|至少) <n> | <n> 例 全绿/通过",
        re.compile(
            _CMP + r"\s*" + _NUM + r"\s*(?:例|条|个用例|个测试)"
            r"|(?:pytest|测试|用例)[^\n。；;]{0,40}?(?:>=|≥|至少|不少于)\s*\d+"
            r"|\d+\s*(?:例|条|个用例)[^\n。；;]{0,12}?(?:全绿|全通过|通过)",
            re.I,
        ),
    ),
    (
        "C3_zero_or_equality",
        "(差值|差异|误差) = 0 | (=|等于) 0",
        re.compile(
            r"(?:差值|差异|误差)\s*(?:=|为|恒为)?\s*0(?![\d.])"
            r"|(?:=|等于)\s*0(?:\.0+)?(?![\d.])",
            re.I,
        ),
    ),
    (
        "C4_byte_identity",
        "byte-identical | 逐字节相同 | 二进制一致",
        re.compile(r"byte[- ]identical|逐字节相同|逐字节一致|二进制一致|bit-identical", re.I),
    ),
    (
        "C5_coverage_rate",
        "(覆盖率|通过率|成功率|准确率|precision|recall) <cmp> <n>",
        re.compile(
            r"(?:覆盖率|通过率|成功率|准确率|召回率|precision|recall|accuracy)\s*"
            r"(?:达到|为|应|须)?\s*" + _CMP + r"\s*" + _NUM + r"\s*%?",
            re.I,
        ),
    ),
    (
        "C6_metric_target",
        "(R2|F1|MAPE|RMSE|MAE|Cb|L/B|B/T|精度|耗时|内存|延迟) near <cmp> <n>",
        re.compile(
            r"(?:R\^?2|R²|F1|MAPE|RMSE|MAE|Cb|L/B|B/T|精度|耗时|内存|延迟)"
            r"[^\n。；;]{0,20}?" + _CMP + r"\s*" + _NUM,
            re.I,
        ),
    ),
)

CRITERIA_DOC = {rid: doc for rid, doc, _ in CRITERIA_RULES}

GREEN_RE = re.compile(
    r"\b\d+\s+passed\b|\bPASSED\b|全绿|全通过|0\s+failed|\b\d+\s+passed\s*[,，]?\s*0\s+failed",
    re.I,
)


def match_criteria(goal: str) -> list[dict[str, str]]:
    """Return matched machine-decidable criteria rules (id + snippet)."""
    hits: list[dict[str, str]] = []
    if not goal:
        return hits
    for rid, _doc, rx in CRITERIA_RULES:
        m = rx.search(goal)
        if m:
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()[:60]
            hits.append({"rule": rid, "match": snippet})
    return hits


# ------------------------------------------------------------------ helpers
def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            got = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def _walk_strings(obj: Any, depth: int = 0) -> Iterable[str]:
    if depth > 4:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key in sorted(obj, key=str):
            yield from _walk_strings(obj[key], depth + 1)
    elif isinstance(obj, (list, tuple)):
        for item in obj[:40]:
            yield from _walk_strings(item, depth + 1)


def mission_text(mission: dict[str, Any]) -> str:
    """All text carried by a mission result (checks / evidence / summary / headline)."""
    result = _as_dict(mission.get("result"))
    return "\n".join(_walk_strings(result))


def session_evidence(mission: dict[str, Any]) -> tuple[bool, str]:
    """Where the session evidence lives (columns are NULL for ~96% of the fleet)."""
    if mission.get("session_id"):
        return True, "column:session_id"
    if mission.get("agent_session_id"):
        return True, "column:agent_session_id"
    result = _as_dict(mission.get("result"))
    if result.get("agent_session_id"):
        return True, "result:agent_session_id"
    summary = _as_dict(_as_dict(result.get("evidence")).get("summary"))
    if summary.get("agent_session_id"):
        return True, "result:evidence.summary.agent_session_id"
    return False, "none"


# -------------------------------------------------------------- transport
class HttpTransport:
    """GET-only HTTP transport. There is deliberately no write method."""

    def __init__(self, base: str, token: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.calls: list[dict[str, str]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        self.calls.append({"method": "GET", "path": path, "url": url})
        req = urllib.request.Request(  # noqa: S310 - fixed internal base URL
            url, headers={"Authorization": f"Bearer {self.token}"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())


class CachingClient:
    """GET-only wrapper that records report details (kept for snapshot replay)."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.details: dict[int, dict] = {}
        self.calls = getattr(inner, "calls", [])

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        payload = self.inner.get(path, **params)
        m = re.fullmatch(r"tensor/reports/(\d+)", path)
        if m and isinstance(payload, dict) and payload.get("id"):
            self.details[int(m.group(1))] = payload
        return payload


def login(base: str, user: str, password: str, timeout: float = 15.0) -> str:
    """Exchange credentials for a bearer token (the only POST in this module)."""
    body = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(  # noqa: S310 - fixed internal base URL
        f"{base.rstrip('/')}/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())["access_token"]


# ---------------------------------------------------------------- fetching
def fetch_missions(client: Any, page: int = 200) -> tuple[list[dict], int]:
    """Fetch every ``awaiting_acceptance`` mission (server caps limit at 200)."""
    collected: list[dict] = []
    total: int | None = None
    offset = 0
    seen: set[int] = set()
    while True:
        payload = client.get(
            "tensor/missions", status="awaiting_acceptance", limit=page, offset=offset
        )
        items = payload.get("items") or []
        if total is None:
            total = int(payload.get("total") or 0)
        new = 0
        for item in items:
            mid = int(item["id"])
            if mid not in seen:
                seen.add(mid)
                collected.append(item)
                new += 1
        if not items or new == 0 or len(collected) >= total:
            break
        offset += len(items)
    return collected, int(total if total is not None else len(collected))


def fetch_reports_for_mission(client: Any, mission_id: int, cap: int = 100) -> list[dict]:
    payload = client.get("tensor/reports", mission_id=mission_id, limit=cap)
    return list(payload.get("items") or [])


def fetch_report_detail(client: Any, report_id: int) -> dict | None:
    """GET one report; None when absent (404 etc.) -- absence is not an error."""
    try:
        payload = client.get(f"tensor/reports/{int(report_id)}")
    except Exception:  # noqa: BLE001 - evidence is best-effort, never fatal
        return None
    return payload if isinstance(payload, dict) and payload.get("id") else None


def fetch_report_markdown(client: Any, report_id: int) -> str:
    return str((fetch_report_detail(client, report_id) or {}).get("markdown") or "")


REPORT_KEEP_KEYS = ("id", "mission_id", "title", "summary", "status", "created_at")


def resolve_mentioned_reports(client: Any, mission: dict, cap: int = 3) -> list[dict]:
    """Report ids named in the result text that actually exist on the platform."""
    out: list[dict] = []
    for rid in parse_report_ids(mission_text(mission))[:cap]:
        detail = fetch_report_detail(client, rid)
        if detail:
            out.append({k: detail.get(k) for k in REPORT_KEEP_KEYS})
    return out
# ---------------------------------------------------------------- evidence
def build_evidence(mission: dict, reports: list[dict], markdowns: dict[int, str]) -> dict:
    sources: list[str] = []
    texts: list[tuple[str, str]] = [("result", mission_text(mission))]
    for rep in reports:
        rid = int(rep.get("id"))
        texts.append((f"report:{rid}:summary", str(rep.get("summary") or "")))
        texts.append((f"report:{rid}:title", str(rep.get("title") or "")))
        if markdowns.get(rid):
            texts.append((f"report:{rid}:markdown", markdowns[rid]))

    shas: set[str] = set()
    for label, text in texts:
        found = parse_commit_shas(text)
        if found:
            shas.update(found)
            sources.append(label)

    return {
        "status": "found" if shas else "unknown",
        "shas": sorted(shas),
        "sources": sorted(set(sources)),
        "green_receipt": bool(GREEN_RE.search(mission_text(mission))),
        "result_present": bool(_as_dict(mission.get("result"))),
    }


PRECEDENCE_DOC = [
    {
        "if": "no report evidence and no session evidence and no commit evidence",
        "action": "revoke",
        "why": "empty shell: no session, no report, no commit -> nothing delivered",
    },
    {
        "if": "no report evidence and (session evidence or commit evidence)",
        "action": "rerelease",
        "why": "work happened (session/commit) but no report -> ask for the report",
    },
    {
        "if": "report evidence and commit_evidence == unknown",
        "action": "rerelease",
        "why": "report without parsable commit sha -> provenance unverifiable",
    },
    {
        "if": "report + commit found + criteria_computable + green_receipt",
        "action": "accept",
        "why": "evidence chain complete + machine-checkable criteria + green receipt",
    },
    {
        "if": "report + commit found",
        "action": "verify",
        "why": "evidence exists but machine-decidability or green receipt missing",
    },
]


def decide(
    *,
    has_report: bool,
    has_session: bool,
    commit_status: str,
    criteria_computable: bool,
    green: bool,
    shas: list[str],
    report_ids: list[int],
) -> tuple[str, str]:
    """Apply PRECEDENCE_DOC in order; return (action, reason). Reason never empty."""
    sha_txt = (", ".join(shas[:3]) + ("..." if len(shas) > 3 else "")) if shas else ""
    rid_txt = ",".join(map(str, report_ids))
    if not has_report and not has_session and commit_status != "found":
        return (
            "revoke",
            "no report, no session evidence (columns + result payload) and no "
            "commit sha -> empty shell, nothing to accept",
        )
    if not has_report:
        why = []
        if has_session:
            why.append("session evidence present")
        if commit_status == "found":
            why.append(f"commit {sha_txt}")
        return (
            "rerelease",
            "work evidence exists (" + " + ".join(why) + ") but no report -> "
            "send back for the missing deliverable",
        )
    if commit_status != "found":
        return (
            "rerelease",
            f"report(s) {rid_txt} present but no parsable commit sha in "
            "result/report text -> provenance unknown, resubmit with commit evidence",
        )
    if criteria_computable and green:
        return (
            "accept",
            f"report(s) {rid_txt} + commit {sha_txt} + machine-checkable criteria "
            "in goal + green self-check receipt -> evidence chain complete, accept",
        )
    missing = []
    if not criteria_computable:
        missing.append("goal has no machine-decidable numeric criterion")
    if not green:
        missing.append("result carries no green test receipt")
    return (
        "verify",
        f"report(s) {rid_txt} + commit {sha_txt} but " + "; ".join(missing)
        + " -> a verifier must replay the claim",
    )
def triage_entry(
    mission: dict,
    reports: list[dict],
    markdowns: dict[int, str],
    mentioned_reports: list[dict] | None = None,
) -> dict[str, Any]:
    """One triage row. Pure function of its inputs (deterministic)."""
    result = _as_dict(mission.get("result"))
    mentioned = list(mentioned_reports or [])
    has_session, session_source = session_evidence(mission)
    linked_ids = sorted(int(r["id"]) for r in reports)
    mentioned_ids = sorted(int(r["id"]) for r in mentioned if int(r["id"]) not in linked_ids)
    evidence_reports = list(reports) + [r for r in mentioned if int(r["id"]) in mentioned_ids]
    report_ids = linked_ids + mentioned_ids
    has_report = bool(report_ids)
    report_link = "api" if linked_ids else ("mentioned" if mentioned_ids else "none")
    evidence = build_evidence(mission, evidence_reports, markdowns)
    criteria = match_criteria(str(mission.get("goal") or ""))
    action, reason = decide(
        has_report=has_report,
        has_session=has_session,
        commit_status=evidence["status"],
        criteria_computable=bool(criteria),
        green=evidence["green_receipt"],
        shas=evidence["shas"],
        report_ids=report_ids,
    )
    return {
        "mission_id": int(mission["id"]),
        "repo": mission.get("repo") or "unknown",
        "model": mission.get("model") or result.get("model") or "unknown",
        "created_at": mission.get("created_at"),
        "status": mission.get("status"),
        "origin": mission.get("origin"),
        "claimed_by": mission.get("claimed_by"),
        "has_session": has_session,
        "session_source": session_source,
        "session_id": mission.get("session_id"),
        "agent_session_id": mission.get("agent_session_id"),
        "report_ids": report_ids,
        "linked_report_ids": linked_ids,
        "mentioned_report_ids": mentioned_ids,
        "report_link": report_link,
        "has_report": has_report,
        "commit_evidence": evidence,
        "criteria_computable": bool(criteria),
        "criteria_rules": [c["rule"] for c in criteria],
        "criteria_matches": criteria,
        "suggested_action": action,
        "reason": reason,
    }
def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_action = {a: 0 for a in ACTIONS}
    by_action_repo: dict[str, dict[str, int]] = {}
    by_action_model: dict[str, dict[str, int]] = {}
    for e in entries:
        a = e["suggested_action"]
        by_action[a] = by_action.get(a, 0) + 1
        by_action_repo.setdefault(a, {})
        by_action_repo[a][e["repo"]] = by_action_repo[a].get(e["repo"], 0) + 1
        by_action_model.setdefault(a, {})
        by_action_model[a][e["model"]] = by_action_model[a].get(e["model"], 0) + 1
    return {
        "total": len(entries),
        "by_action": by_action,
        "by_action_repo": by_action_repo,
        "by_action_model": by_action_model,
        "with_session": sum(1 for e in entries if e["has_session"]),
        "with_session_column": sum(
            1 for e in entries if e["session_source"].startswith("column")
        ),
        "with_session_in_result": sum(
            1 for e in entries if e["session_source"].startswith("result")
        ),
        "with_report": sum(1 for e in entries if e["has_report"]),
        "report_link_api": sum(1 for e in entries if e["report_link"] == "api"),
        "report_link_mentioned": sum(1 for e in entries if e["report_link"] == "mentioned"),
        "commit_found": sum(1 for e in entries if e["commit_evidence"]["status"] == "found"),
        "criteria_computable": sum(1 for e in entries if e["criteria_computable"]),
        "green_receipt": sum(1 for e in entries if e["commit_evidence"]["green_receipt"]),
        "repos": sorted({e["repo"] for e in entries}),
        "models": sorted({e["model"] for e in entries}),
    }
STABLE_MISSION_KEYS = (
    "id", "goal", "repo", "base_ref", "origin", "model", "session_id",
    "agent_session_id", "status", "claimed_by", "result", "created_at",
    "parent_mission_id", "co_repos", "runner", "worker_role",
    "campaign_id", "campaign_phase", "finished_at",
)


def _stable_mission(mission: dict) -> dict:
    out = {k: mission.get(k) for k in STABLE_MISSION_KEYS}
    out["result"] = _as_dict(mission.get("result"))
    return out


def input_digest(missions: list[dict], reports_by_mission: dict[int, list[dict]]) -> str:
    """Digest of the stable projection of the fetched input (no wall-clock stamp)."""
    payload = {
        "missions": sorted((_stable_mission(m) for m in missions), key=lambda m: int(m["id"])),
        "reports": {
            str(mid): [
                {k: r.get(k) for k in ("id", "title", "summary", "scenario_key", "status", "created_at")}
                for r in sorted(reports, key=lambda r: int(r["id"]))
            ]
            for mid, reports in sorted(reports_by_mission.items())
        },
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------- rendering
def dumps_queue(queue: dict[str, Any]) -> str:
    return json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _md_table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return out


def render_markdown(queue: dict[str, Any]) -> str:
    s = queue["summary"]
    lines: list[str] = [
        "# 待验收队列证据化分诊（acceptance queue triage）",
        "",
        f"- 全量待验收：**{s['total']}** 张（API total = {queue['mission_total_from_api']}，"
        f"覆盖一致 = {queue['coverage_ok']}），input_digest = `{queue['input_digest'][:16]}`",
        f"- 会话证据 {s['with_session']}（顶层列 {s['with_session_column']} / 仅 result 内嵌 "
        f"{s['with_session_in_result']}）/ 报告证据 {s['with_report']}（mission_id 直连 "
        f"{s['report_link_api']} / 正文提及 {s['report_link_mentioned']}）/ 可解析 commit "
        f"{s['commit_found']} / 含可算判据 {s['criteria_computable']} / 含绿测回执 "
        f"{s['green_receipt']}",
        "- 本文件仅为队列，**未执行任何裁决写入**（全程只 GET）。",
        "",
        "## 1. 分诊分布（action × repo）",
        "",
    ]
    repos = s["repos"]
    rows = [
        [a] + [s["by_action_repo"].get(a, {}).get(r, 0) for r in repos]
        + [s["by_action"].get(a, 0)]
        for a in ACTIONS
    ]
    rows.append(
        ["合计"]
        + [sum(s["by_action_repo"].get(a, {}).get(r, 0) for a in ACTIONS) for r in repos]
        + [s["total"]]
    )
    lines += _md_table(["action"] + repos + ["合计"], rows)

    lines += ["", "## 2. 分诊分布（action × model）", ""]
    models = s["models"]
    rows = [
        [a] + [s["by_action_model"].get(a, {}).get(m, 0) for m in models]
        + [s["by_action"].get(a, 0)]
        for a in ACTIONS
    ]
    rows.append(
        ["合计"]
        + [sum(s["by_action_model"].get(a, {}).get(m, 0) for a in ACTIONS) for m in models]
        + [s["total"]]
    )
    lines += _md_table(["action"] + models + ["合计"], rows)

    lines += ["", "![分诊分布](acceptance_queue_actions.png)", ""]
    lines += ["## 3. 裁决规则（first match wins）", ""]
    lines += _md_table(
        ["#", "条件", "action", "理由"],
        [[i + 1, p["if"], p["action"], p["why"]] for i, p in enumerate(PRECEDENCE_DOC)],
    )
    lines += ["", "## 4. 判据可算性规则（regex）", ""]
    lines += _md_table(["rule", "含义"], [[rid, doc] for rid, doc in CRITERIA_DOC.items()])

    lines += ["", "## 5. 排序队列（accept → verify → rerelease → revoke）", ""]
    rows = []
    for i, e in enumerate(queue["items"], 1):
        sha = e["commit_evidence"]["status"]
        if e["commit_evidence"]["shas"]:
            sha += ":" + ",".join(e["commit_evidence"]["shas"][:2])
        rows.append([
            i, e["mission_id"], e["repo"], e["model"], (e["created_at"] or "")[:19],
            {"column": "col", "result": "res", "none": "-"}[e["session_source"].split(":")[0]],
            (",".join(map(str, e["linked_report_ids"])) or "-")
            + (f" ~{','.join(map(str, e['mentioned_report_ids']))}"
               if e["mentioned_report_ids"] else ""),
            sha,
            "Y" if e["criteria_computable"] else "-",
            f"**{e['suggested_action']}**",
            e["reason"],
        ])
    lines += _md_table(
        ["#", "mission", "repo", "model", "created_at", "session", "reports",
         "commit", "criteria", "action", "reason"],
        rows,
    )
    return "\n".join(lines) + "\n"


def render_figure(queue: dict[str, Any], path: Path) -> bool:
    """Grouped bars: action x repo and action x model. True when written."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - figure is optional
        return False

    s = queue["summary"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    for ax, key, title in (
        (axes[0], "by_action_repo", "suggested_action x repo"),
        (axes[1], "by_action_model", "suggested_action x model"),
    ):
        groups = s["repos"] if key == "by_action_repo" else s["models"]
        table = s[key]
        width = 0.8 / max(len(groups), 1)
        for i, g in enumerate(groups):
            vals = [table.get(a, {}).get(g, 0) for a in ACTIONS]
            xs = [j + i * width for j in range(len(ACTIONS))]
            ax.bar(xs, vals, width=width, label=g)
            for x, v in zip(xs, vals):
                if v:
                    ax.text(x, v, str(v), ha="center", va="bottom", fontsize=7)
        ax.set_xticks([j + 0.4 - width / 2 for j in range(len(ACTIONS))])
        ax.set_xticklabels(ACTIONS, fontsize=9)
        ax.set_ylabel("missions")
        ax.set_title(f"{title} (n={s['total']})", fontsize=10)
        ax.legend(fontsize=7)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("m2075 acceptance-queue triage: mission count by suggested action", fontsize=11)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


# ------------------------------------------------------------------ snapshot
def build_snapshot(queue: dict[str, Any], missions: list[dict],
                   reports_by_mission: dict[int, list[dict]],
                   details: dict[int, dict] | None = None) -> dict:
    return {
        "source": queue["source"],
        "mission_total_from_api": queue["mission_total_from_api"],
        "missions": sorted((_stable_mission(m) for m in missions), key=lambda m: int(m["id"])),
        "reports": {str(mid): reps for mid, reps in sorted(reports_by_mission.items())},
        "report_details": {str(k): v for k, v in sorted((details or {}).items())},
    }


class SnapshotTransport:
    """Offline replay of a frozen snapshot; also GET-only."""

    def __init__(self, snapshot: dict):
        self.mission_total = int(snapshot.get("mission_total_from_api") or 0)
        self.missions = list(snapshot.get("missions") or [])
        self.reports = {int(k): list(v) for k, v in (snapshot.get("reports") or {}).items()}
        self.details = {int(k): v for k, v in (snapshot.get("report_details") or {}).items()}
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"method": "GET", "path": path, "params": dict(params)})
        if path == "tensor/missions":
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or 200)
            return {
                "items": self.missions[offset: offset + limit],
                "total": self.mission_total or len(self.missions),
            }
        if path == "tensor/reports":
            mid = int(params.get("mission_id") or 0)
            return {"items": self.reports.get(mid, []), "total": len(self.reports.get(mid, []))}
        m = re.fullmatch(r"tensor/reports/(\d+)", path)
        if m and int(m.group(1)) in self.details:
            return self.details[int(m.group(1))]
        raise KeyError(f"snapshot has no data for {path}")
def write_outputs(queue: dict[str, Any], out_dir: Path, figure: bool = True,
                  snapshot: dict | None = None) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "acceptance_queue.json",
        "markdown": out_dir / "acceptance_queue.md",
    }
    paths["json"].write_text(dumps_queue(queue), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(queue), encoding="utf-8")
    written = {"json": str(paths["json"]), "markdown": str(paths["markdown"])}
    if figure:
        fig_path = out_dir / "acceptance_queue_actions.png"
        if render_figure(queue, fig_path):
            written["figure"] = str(fig_path)
    if snapshot is not None:
        snap_path = out_dir / "acceptance_input_snapshot.json"
        snap_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written["snapshot"] = str(snap_path)
    return written


def fetch_all(
    client: Any, *, page: int = 200, progress: Callable[[str], None] | None = None
) -> tuple[list[dict], int, dict[int, list[dict]]]:
    missions, total = fetch_missions(client, page=page)
    reports_by_mission = {
        int(m["id"]): fetch_reports_for_mission(client, int(m["id"])) for m in missions
    }
    if progress:
        n_rep = sum(1 for v in reports_by_mission.values() if v)
        progress(f"missions={len(missions)} total={total} with_linked_report={n_rep}")
    return missions, total, reports_by_mission


def _collect_markdowns(client: Any, mission: dict, evidence_reports: list[dict],
                       fetch_markdown: bool) -> dict[int, str]:
    if not fetch_markdown or not evidence_reports:
        return {}
    if build_evidence(mission, evidence_reports, {})["status"] == "found":
        return {}  # cheap sources already carry a sha: do not pay for report bodies
    markdowns: dict[int, str] = {}
    for rep in evidence_reports[:3]:
        rid = int(rep["id"])
        md = fetch_report_markdown(client, rid)
        if md:
            markdowns[rid] = md
    return markdowns


def run_triage_from(
    missions: list[dict],
    total: int,
    reports_by_mission: dict[int, list[dict]],
    client: Any,
    *,
    fetch_markdown: bool = True,
    resolve_mentions: bool = True,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for mission in missions:
        mid = int(mission["id"])
        reports = reports_by_mission.get(mid, [])
        mentioned: list[dict] = []
        if resolve_mentions and not reports:
            mentioned = resolve_mentioned_reports(client, mission)
        linked = {int(r["id"]) for r in reports}
        evidence_reports = reports + [r for r in mentioned if int(r["id"]) not in linked]
        markdowns = _collect_markdowns(client, mission, evidence_reports, fetch_markdown)
        entries.append(triage_entry(mission, reports, markdowns, mentioned_reports=mentioned))
    entries.sort(key=lambda e: (ACTION_ORDER[e["suggested_action"]], e["mission_id"]))
    return {
        "source": {
            "missions_endpoint": "GET /api/v1/tensor/missions?status=awaiting_acceptance",
            "reports_endpoint": (
                "GET /api/v1/tensor/reports?mission_id=<id> (+ /{id} for mentioned ids)"
            ),
            "read_only": True,
        },
        "mission_total_from_api": total,
        "entry_count": len(entries),
        "coverage_ok": len(entries) == total,
        "rules": {
            "actions": list(ACTIONS),
            "action_order": ACTION_ORDER,
            "precedence": PRECEDENCE_DOC,
            "criteria_rules": CRITERIA_DOC,
            "session_rule": (
                "session_id / agent_session_id column non-null, or agent_session_id "
                "embedded in result or result.evidence.summary"
            ),
            "report_rule": (
                "report with mission_id == mission id, or a report id mentioned in the "
                "result text that resolves via GET /tensor/reports/{id}"
            ),
            "commit_sha_rule": (
                "7..40 hex chars, delimited, preceded within 24 chars by a cue word "
                "(commit/sha/git/merge/HEAD/提交/合并), not all digits, not a known "
                "hex word; otherwise unknown (never guessed)"
            ),
            "green_receipt_rule": "'<n> passed' / 'PASSED' / '全绿' / '0 failed' in mission result",
        },
        "summary": summarize(entries),
        "items": entries,
        "input_digest": input_digest(missions, reports_by_mission),
    }


def run_triage(
    client: Any,
    *,
    fetch_markdown: bool = True,
    page: int = 200,
    resolve_mentions: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch + triage. Only ``client.get`` is used (read-only)."""
    missions, total, reports_by_mission = fetch_all(client, page=page, progress=progress)
    return run_triage_from(
        missions, total, reports_by_mission, client,
        fetch_markdown=fetch_markdown, resolve_mentions=resolve_mentions,
    )
# ---------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="read-only acceptance-queue triage")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"))
    ap.add_argument("--token", default=os.environ.get("TOKEN", ""))
    ap.add_argument("--user", default=os.environ.get("TENSOR_AGENT_USER", ""))
    ap.add_argument("--password", default=os.environ.get("TENSOR_AGENT_PASS", ""))
    ap.add_argument("--out", default="artifacts")
    ap.add_argument("--page", type=int, default=200)
    ap.add_argument("--no-markdown", action="store_true", help="skip report body fetch")
    ap.add_argument("--no-mentions", action="store_true", help="skip report-id mentions")
    ap.add_argument("--no-figure", action="store_true")
    ap.add_argument("--from-snapshot", default="", help="offline replay of a frozen input")
    ap.add_argument("--snapshot", default="artifacts/acceptance_input_snapshot.json")
    args = ap.parse_args(argv)

    def say(msg: str) -> None:
        print(f"[triage] {msg}", file=sys.stderr)

    if args.from_snapshot:
        snapshot = json.loads(Path(args.from_snapshot).read_text(encoding="utf-8"))
        client: Any = SnapshotTransport(snapshot)
        write_snapshot = False
    else:
        token = args.token or login(args.api_base, args.user, args.password)
        client = CachingClient(HttpTransport(args.api_base, token))
        write_snapshot = True

    missions, total, reports_by_mission = fetch_all(
        client, page=args.page, progress=say
    )
    queue = run_triage_from(
        missions, total, reports_by_mission, client,
        fetch_markdown=not args.no_markdown, resolve_mentions=not args.no_mentions,
    )
    if not queue["coverage_ok"]:
        say(f"COVERAGE MISMATCH: entries={queue['entry_count']} api_total={total}")
        return 1

    details = getattr(client, "details", {})
    snap = (
        build_snapshot(queue, missions, reports_by_mission, details)
        if write_snapshot else None
    )
    written = write_outputs(queue, Path(args.out), figure=not args.no_figure, snapshot=snap)
    say(
        f"entries={queue['entry_count']} actions={queue['summary']['by_action']} "
        f"digest={queue['input_digest'][:12]}"
    )
    for kind, path in sorted(written.items()):
        say(f"wrote {kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())