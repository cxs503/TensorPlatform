#!/usr/bin/env python3
"""采集器：真实战役记录 → 脱敏合成域字段的 golden fixture（输入快照 → 判词对）。

管线
----
1. **快照**：``/tensor/reports``、``/tensor/missions``、``/tensor/campaigns/plans`` +
   ``/plans/{id}/nodes`` 拉真实记录；此后**只保留结构化投影**（布尔/计数/数值），
   自由文本一律不落地——脱敏在此完成：agent/repo 名不外泄，记录主键替换为加盐
   sha256 前缀 ``sha256:xxxxxxxx``。
2. **派生**：``derive_fixtures()`` 把快照映射为机制记录（真实字段优先），每个字段都
   标注来源 ``real`` / ``real-mapped`` / ``proxy`` / ``synthetic``，可机器对账。
3. **探针**：``probe_fixtures()`` 生成确定性探针（全绿 / 阈值边界 / 缺字段 / 顺序置换
   A、B / 机制特有负控）——它们不伪造真实数据，只用合成记录把「机制必须抓住的边角」
   摆上台面，``origin`` 标为 ``probe``，与 real 分开统计。
4. **判词冻结**：每条 fixture 的 ``expected_verdict`` 必须由解释器与**独立第二实现**
   双算一致才落盘（否则抛错），并给出 ``verdict_sha256``。
5. **sha256 清单**：``manifest.json`` 记录每个 fixture 文件的 sha256 + 快照 sha256，
   供 C4 完整性校验。

确定性：输出按 fixture_id 排序、``sort_keys=True``、无 wall clock、无随机数；
同一份快照两次采集逐字节相同。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from tools.mech_replay import mechanisms, reference, spec

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DEFAULT_OUT = REPO_ROOT / "artifacts" / "mech_replay"
SNAPSHOT_SCHEMA = "mech-replay-sources-v1"
SALT = "mech-replay-v1"

STATUS_MAP = {
    "pending": "pending", "queued": "pending", "not_started": "pending",
    "in_flight": "in_flight", "in-flight": "in_flight", "inflight": "in_flight",
    "running": "in_flight", "in_progress": "in_flight", "done": "done",
    "completed": "done",
    "verified": "verified", "blocked": "blocked", "failed": "failed",
    "cancelled": "failed", "rejected": "failed",
}

PYTEST_RE = re.compile(r"(\d+)\s*(?:项|个|条|例)?\s*pytest")
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


# --------------------------------------------------------------------------- #
# 脱敏原语
# --------------------------------------------------------------------------- #
def id_token(value: Any) -> str:
    """主键 → 加盐 sha256 前缀（不可逆、可对账）。"""
    if value is None:
        return "sha256:none"
    digest = hashlib.sha256((SALT + str(value)).encode("utf-8")).hexdigest()
    return "sha256:" + digest[:12]


def number(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def map_status(raw: Any) -> str:
    key = str(raw)
    return STATUS_MAP.get(key, key)  # 未登记状态原样透出 → 由 CS_STATUS_ENUM 暴露词表漂移


# --------------------------------------------------------------------------- #
# 采集快照（API → 脱敏结构化投影）
# --------------------------------------------------------------------------- #
def _http_json(url: str, token: str | None, timeout: float = 30.0) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - 受控内网
        return json.loads(resp.read().decode("utf-8"))


def api_login(base: str) -> str | None:
    user = os.environ.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS")
    if not user or not password:
        return None
    payload = json.dumps({"username": user, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        base + "/auth/login", data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("access_token") or data.get("token")


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("items", "data", "results"):
            if isinstance(payload.get(key), list):
                return list(payload[key])
        return []
    return list(payload or [])


def snapshot_from_api(base: str, token: str | None, *, n_reports: int = 12,
                      n_plans: int = 10, n_missions: int = 12) -> dict[str, Any]:
    reports = _items(_http_json(f"{base}/tensor/reports?limit={n_reports * 2}", token))
    missions = _items(_http_json(f"{base}/tensor/missions?limit={n_missions * 2}", token))
    plans = _items(_http_json(f"{base}/tensor/campaigns/plans?limit={n_plans * 2}", token))

    snap_reports = []
    for item in sorted(reports, key=lambda r: number(r.get("id")), reverse=True)[:n_reports]:
        summary = str(item.get("summary") or "")
        tags = item.get("tags") or []
        figures = item.get("figures") or []
        match = PYTEST_RE.search(summary)
        snap_reports.append({
            "record": id_token(item.get("id")),
            "summary_length": len(summary),
            "tag_count": len(tags) if isinstance(tags, list) else 0,
            "figure_count": len(figures) if isinstance(figures, list) else 0,
            "has_percent": bool(PERCENT_RE.search(summary)),
            "has_mission_link": item.get("mission_id") is not None,
            "scenario_key_present": bool(item.get("scenario_key")),
            "status_published": str(item.get("status")) == "published",
            "pytest_count": int(match.group(1)) if match else 0,
            "honest_fail_mentioned": "honest" in summary.lower(),
        })

    snap_missions = []
    for item in sorted(missions, key=lambda m: number(m.get("id")), reverse=True)[:n_missions]:
        budget = item.get("budget") or {}
        result = item.get("result")
        snap_missions.append({
            "record": id_token(item.get("id")),
            "budget": {
                "max_tokens": number(budget.get("max_tokens")),
                "max_tool_calls": number(budget.get("max_tool_calls")),
                "max_wall_seconds": number(budget.get("max_wall_seconds")),
            },
            "status": str(item.get("status")),
            "result_keys": len(result) if isinstance(result, Mapping) else 0,
            "has_campaign": item.get("campaign_id") is not None,
        })

    snap_plans = []
    for plan in sorted(plans, key=lambda p: number(p.get("id")), reverse=True)[:n_plans]:
        plan_id = plan.get("id")
        try:
            nodes = _items(_http_json(f"{base}/tensor/campaigns/plans/{plan_id}/nodes", token))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            nodes = []
        planned = plan.get("budget_planned") or {}
        actual = plan.get("budget_actual") or {}
        snap_nodes = []
        for node in nodes:
            meta = node.get("metadata") or {}
            criteria = meta.get("acceptance_criteria") or []
            snap_nodes.append({
                "status": map_status(node.get("status")),
                "evidence_count": len(criteria) if isinstance(criteria, list) else 0,
                "consecutive_fails": int(number(node.get("consecutive_fails"))),
            })
        snap_plans.append({
            "record": id_token(plan_id),
            "status": map_status(plan.get("status")),
            "node_count": len(nodes),
            "nodes": snap_nodes,
            "token_planned": number(planned.get("max_tokens")),
            "token_actual": number(actual.get("max_tokens")),
            "wall_planned": number(planned.get("max_wall_seconds")),
            "wall_actual": number(actual.get("max_wall_seconds")),
        })

    return {
        "schema": SNAPSHOT_SCHEMA,
        "backend": "api",
        "reports": snap_reports,
        "missions": snap_missions,
        "plans": snap_plans,
    }


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    return spec.digest(snapshot)


# --------------------------------------------------------------------------- #
# 派生 fixture
# --------------------------------------------------------------------------- #
def _fixture(mechanism: str, slug: str, record: Mapping[str, Any], *, origin: str,
             probe_class: str | None, source: Mapping[str, Any],
             field_sources: Mapping[str, str], notes: str) -> dict[str, Any]:
    verdict = mechanisms.judge(mechanism, record)
    ref = reference.reference_verdict(mechanism, record)
    agree = (verdict["decision"] == ref["decision"]
             and reference.spec_codes(verdict) == ref["codes"])
    if not agree:  # 双算不一致 → 不允许落盘（golden 必须是双实现共识）
        raise RuntimeError(
            f"reference disagreement for {mechanism}/{slug}: "
            f"spec={verdict['decision']}{reference.spec_codes(verdict)} "
            f"ref={ref['decision']}{ref['codes']}"
        )
    return {
        "fixture_id": f"{mechanism}/{slug}",
        "mechanism": mechanism,
        "origin": origin,
        "probe_class": probe_class,
        "source": dict(source),
        "field_sources": dict(field_sources),
        "notes": notes,
        "record": spec._normalize(record),  # noqa: SLF001 - 复用同一规范化口径
        "expected_verdict": verdict,
        "verdict_sha256": spec.digest(verdict),
        "reference_agreement": True,
    }


def derive_fixtures(snapshot: Mapping[str, Any], *, per_mechanism: int = 6) -> list[dict[str, Any]]:
    """真实快照 → 5 个机制的 real 派生 fixture。

    其余 3 个机制（machine_gate / merge_gate / autonomy_proposal）所需的
    measured/评审/检索字段在库里不存在，只用合成探针覆盖——这一局限在报告里明写。
    """
    fixtures: list[dict[str, Any]] = []
    reports = list(snapshot.get("reports") or [])
    plans = list(snapshot.get("plans") or [])

    usable_plans = [p for p in plans if p.get("node_count", 0) > 0]
    for plan in usable_plans[:per_mechanism]:
        record = {"nodes": [dict(n) for n in plan["nodes"]]}
        fixtures.append(_fixture(
            "campaign_status", f"real-plan-{plan['record'].split(':')[1]}", record,
            origin="real", probe_class=None,
            source={"table": "campaign_nodes", "record": plan["record"],
                    "backend": "api:/tensor/campaigns/plans/{id}/nodes"},
            field_sources={"nodes[].status": "real-mapped",
                           "nodes[].consecutive_fails": "real",
                           "nodes[].evidence_count": "proxy:metadata.acceptance_criteria"},
            notes="真实战役节点状态快照；证据条数以 metadata.acceptance_criteria 长度代理",
        ))

    for plan in plans[:per_mechanism]:
        record = {
            "spent_tokens": plan["token_actual"],
            "budget_tokens": plan["token_planned"],
            "concurrent_jobs": sum(1 for n in plan.get("nodes", [])
                                   if n.get("status") == "in_flight"),
            "max_jobs": 40,
            "wall_used": plan["wall_actual"],
            "wall_limit": plan["wall_planned"],
        }
        fixtures.append(_fixture(
            "budget_gate", f"real-plan-{plan['record'].split(':')[1]}", record,
            origin="real", probe_class=None,
            source={"table": "campaign_plans+campaign_nodes", "record": plan["record"],
                    "backend": "api:/tensor/campaigns/plans"},
            field_sources={"spent_tokens": "real:budget_actual.max_tokens",
                           "budget_tokens": "real:budget_planned.max_tokens",
                           "concurrent_jobs": "real-mapped:node.status==in_flight",
                           "max_jobs": "synthetic:平台并发口径 40",
                           "wall_used": "real:budget_actual.max_wall_seconds",
                           "wall_limit": "real:budget_planned.max_wall_seconds"},
            notes="真实计划预算快照；planned 为 0 的计划将以 TYPE_ERROR 判 fail（零预算不可门禁）",
        ))

    for rep in reports[:per_mechanism]:
        sections = {
            "background": rep["scenario_key_present"],
            "method": rep["tag_count"] >= 2,
            "results": rep["summary_length"] >= 40,
            "figures": rep["figure_count"] >= 1,
            "caveats": rep["summary_length"] >= 120,
            "error_judgement": rep["has_percent"],
        }
        status = "FAIL" if (not rep["status_published"] and rep["honest_fail_mentioned"]) else "PASS"
        record = {
            "title": "T" if rep["summary_length"] > 0 else "",
            "summary_length": rep["summary_length"],
            "sections": sections,
            "figures": [f"fig{i}" for i in range(rep["figure_count"])],
            "figure_waiver_reason": "",
            "error_answer": rep["has_percent"],
            "status": status,
            "honest_fail": rep["honest_fail_mentioned"],
        }
        fixtures.append(_fixture(
            "report_gate", f"real-report-{rep['record'].split(':')[1]}", record,
            origin="real", probe_class=None,
            source={"table": "tensor_reports", "record": rep["record"],
                    "backend": "api:/tensor/reports"},
            field_sources={"summary_length": "real:len(summary)", "figures": "real:figures[]",
                           "sections.*": "proxy:结构化字段存在性",
                           "error_answer": "proxy:摘要含百分数",
                           "status": "proxy:status+honest 关键词"},
            notes="真实报告六要素结构投影（文本内容不落地，只留布尔/计数）",
        ))

    for rep in reports[:per_mechanism]:
        evidence_verified = {
            "mission_link": rep["has_mission_link"],
            "figures": rep["figure_count"] >= 1,
            "tags": rep["tag_count"] >= 3,
            "scenario_key": rep["scenario_key_present"],
            "status_published": rep["status_published"],
            "percent_conclusion": rep["has_percent"],
            "pytest_count": rep["pytest_count"] > 0,
        }
        kinds = sorted(k for k, v in evidence_verified.items() if v)
        if len(kinds) >= 3 and rep["figure_count"] >= 2 and rep["pytest_count"] > 0:
            grade = "REAL"
        elif kinds:
            grade = "MOCK"
        else:
            grade = "WITHHELD"
        record = {
            "declared_grade": grade,
            "evidence_verified": evidence_verified,
            "evidence_kinds": kinds,
            "reference_anchor": rep["has_mission_link"],
            "independent_reimpl": rep["figure_count"] >= 2,
            "artifact_count": rep["figure_count"],
        }
        fixtures.append(_fixture(
            "evidence_grade", f"real-report-{rep['record'].split(':')[1]}", record,
            origin="real", probe_class=None,
            source={"table": "tensor_reports", "record": rep["record"],
                    "backend": "api:/tensor/reports"},
            field_sources={"declared_grade": "proxy:结构完整性启发式",
                           "evidence_verified.*": "proxy:报告结构字段",
                           "reference_anchor": "proxy:mission 链接",
                           "independent_reimpl": "proxy:figures>=2"},
            notes="报告证据面投影；declared_grade 为启发式代理，不作真假证据等级的公证",
        ))

    for rep in reports[:per_mechanism]:
        tests = rep["pytest_count"]
        figures = rep["figure_count"]
        grade = "REAL" if (tests and figures) else ("MOCK" if (tests or figures) else "WITHHELD")
        record = {
            "evidence_grade": grade,
            "artifacts": [f"artifact{i}" for i in range(figures)],
            "tests": tests,
            "docs": rep["summary_length"] >= 40,
            "artifact_count": figures,
        }
        fixtures.append(_fixture(
            "capability_registration", f"real-report-{rep['record'].split(':')[1]}", record,
            origin="real", probe_class=None,
            source={"table": "tensor_reports", "record": rep["record"],
                    "backend": "api:/tensor/reports"},
            field_sources={"tests": "real-regex:summary 中 'N 项 pytest'",
                           "artifacts": "proxy:figures[]", "docs": "proxy:summary 非空",
                           "evidence_grade": "proxy:启发式"},
            notes="真实报告抽取的 pytest 用例数与工件计数（文本不落地）",
        ))

    return fixtures


# --------------------------------------------------------------------------- #
# 确定性探针
# --------------------------------------------------------------------------- #
GOOD: dict[str, dict[str, Any]] = {
    "capability_registration": {
        "evidence_grade": "REAL", "artifacts": ["a1", "a2", "a3"], "tests": 14,
        "docs": True, "artifact_count": 3,
    },
    "evidence_grade": {
        "declared_grade": "REAL",
        "evidence_verified": {f"unit_{i}": True for i in range(1, 7)},
        "evidence_kinds": ["unit_test", "artifact_sha", "reference_anchor"],
        "reference_anchor": True, "independent_reimpl": True, "artifact_count": 2,
    },
    "machine_gate": {
        "criteria": [{"id": f"C{i:02d}", "measured": 0.010, "threshold": 0.020,
                      "direction": "max"} for i in range(1, 7)],
        "tol_rel": 0.02, "runs": 10,
    },
    "report_gate": {
        "title": "T", "summary_length": 160,
        "sections": {"background": True, "method": True, "results": True, "figures": True,
                     "caveats": True, "error_judgement": True},
        "figures": ["f1", "f2"], "figure_waiver_reason": "", "error_answer": True,
        "status": "PASS", "honest_fail": False,
    },
    "merge_gate": {
        "ci_green": True, "approvals": 8, "conflicts": 0, "guard_risk": 0.01,
        "changed_files": 12, "base_branch_ok": True,
    },
    "autonomy_proposal": {
        "proposal_title": "T", "prior_art_checks": 8, "novelty_anchors": ["anchor:1"],
        "criteria": ["c1", "c2"],
        "redline_flags": {"strategic_rule": False, "safety": False, "irreversible": False},
    },
    "campaign_status": {
        "nodes": [{"status": "verified", "evidence_count": 8, "consecutive_fails": 0},
                  {"status": "in_flight", "evidence_count": 2, "consecutive_fails": 1}],
    },
    "budget_gate": {
        "spent_tokens": 500, "budget_tokens": 1000, "concurrent_jobs": 10,
        "max_jobs": 40, "wall_used": 100, "wall_limit": 1000,
    },
}


def _clone(record: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(record))


def _drop(record: dict[str, Any], path: str) -> dict[str, Any]:
    *parents, leaf = path.split(".")
    cur: Any = record
    for part in parents:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    if isinstance(cur, list):
        cur.pop(int(leaf))
    else:
        cur.pop(leaf, None)
    return record


def _crit(ids_and_measured: list[tuple[str, float]], threshold: float = 0.020) -> list[dict[str, Any]]:
    return [{"id": cid, "measured": measured, "threshold": threshold, "direction": "max"}
            for cid, measured in ids_and_measured]


PROBES: list[tuple[str, str, dict[str, Any], str]] = [
    # ---------------- capability_registration ----------------
    ("capability_registration", "all-green", _clone(GOOD["capability_registration"]),
     "全绿基线：REAL 档带齐 3 工件 / 14 用例 / 文档"),
    ("capability_registration", "boundary-relax",
     {**_clone(GOOD["capability_registration"]), "tests": 10},
     "阈值边界：tests=10 < 12 判 fail；阈值放宽 20%（12→10）翻绿 → M1 靶"),
    ("capability_registration", "missing-field",
     _drop(_clone(GOOD["capability_registration"]), "artifacts"),
     "缺字段：REAL 档无工件清单 → 必须 fail(MISSING_FIELD) → M2 靶"),
    ("capability_registration", "order-pair-A",
     {**_clone(GOOD["capability_registration"]), "artifacts": ["a1", "a2", "a3"]},
     "顺序置换对 A：工件顺序 a1,a2,a3"),
    ("capability_registration", "order-pair-B",
     {**_clone(GOOD["capability_registration"]), "artifacts": ["a3", "a1", "a2"]},
     "顺序置换对 B：同集合换序，判词必须与 A 相同 → M3 靶"),
    ("capability_registration", "mixed-grade",
     {"evidence_grade": "MOCK", "artifacts": [], "tests": 0, "docs": False,
      "artifact_count": 0},
     "MOCK 档不触发 REAL 专属判据（条件规则跳过）"),
    # ---------------- evidence_grade ----------------
    ("evidence_grade", "all-green", _clone(GOOD["evidence_grade"]),
     "全绿基线：6 条已验证证据 + 3 类证据 + 参考锚 + 独立实现"),
    ("evidence_grade", "boundary-relax",
     {**_clone(GOOD["evidence_grade"]),
      "evidence_verified": {**{f"unit_{i}": True for i in range(1, 6)}, "unit_6": False}},
     "阈值边界：已验证证据 5 < 6 判 fail；放宽 20%（6→5）翻绿 → M1 靶"),
    ("evidence_grade", "missing-field",
     _drop(_clone(GOOD["evidence_grade"]), "reference_anchor"),
     "缺字段：REAL 声明无参考锚 → fail(MISSING_FIELD) → M2 靶"),
    ("evidence_grade", "order-pair-A",
     {**_clone(GOOD["evidence_grade"]),
      "evidence_verified": {"unit_1": True, "unit_2": True, "unit_3": True, "unit_4": True,
                            "unit_5": True, "unit_6": True, "unit_7": False}},
     "顺序置换对 A：证据映射首键为真"),
    ("evidence_grade", "order-pair-B",
     {**_clone(GOOD["evidence_grade"]),
      "evidence_verified": {"unit_7": False, "unit_1": True, "unit_2": True, "unit_3": True,
                            "unit_4": True, "unit_5": True, "unit_6": True}},
     "顺序置换对 B：同映射换序（首键为假），判词必须与 A 相同 → M3 靶"),
    ("evidence_grade", "not-supported",
     {"declared_grade": "NOT_SUPPORTED", "evidence_verified": {}, "evidence_kinds": [],
      "reference_anchor": False, "independent_reimpl": False, "artifact_count": 0},
     "NOT_SUPPORTED 且无工件 → 合法（分级自洽）"),
    # ---------------- machine_gate ----------------
    ("machine_gate", "all-green", _clone(GOOD["machine_gate"]),
     "全绿基线：6 条判据全达标 + 容差 2% + 10 次运行"),
    ("machine_gate", "boundary-relax",
     {**_clone(GOOD["machine_gate"]), "runs": 5},
     "阈值边界：runs=5 < 6 判 fail；放宽 20%（6→5）翻绿 → M1 靶"),
    ("machine_gate", "missing-field",
     _drop(_clone(GOOD["machine_gate"]), "runs"),
     "缺字段：无复现次数 → fail(MISSING_FIELD) → M2 靶"),
    ("machine_gate", "order-pair-A",
     {**_clone(GOOD["machine_gate"]),
      "criteria": _crit([("C04", 0.030), ("C01", 0.010), ("C02", 0.010), ("C03", 0.010),
                         ("C05", 0.010), ("C06", 0.010)])},
     "顺序置换对 A：越界判据 C04 排首位"),
    ("machine_gate", "order-pair-B",
     {**_clone(GOOD["machine_gate"]),
      "criteria": _crit([("C01", 0.010), ("C02", 0.010), ("C03", 0.010), ("C04", 0.030),
                         ("C05", 0.010), ("C06", 0.010)])},
     "顺序置换对 B：同一判据集合（C04 越界）换序，判词必须与 A 相同（均 fail）→ M3 靶"),
    ("machine_gate", "tol-boundary",
     {**_clone(GOOD["machine_gate"]), "tol_rel": 0.05,
      "criteria": _crit([("C01", 0.0209)] + [(f"C{i:02d}", 0.010) for i in range(2, 7)])},
     "容差边界：measured 0.0209 恰在 5% 容差内（上限 0.021）→ pass"),
    # ---------------- report_gate ----------------
    ("report_gate", "all-green", _clone(GOOD["report_gate"]),
     "全绿基线：六要素齐全 + 2 图 + 误差结论"),
    ("report_gate", "boundary-relax",
     {**_clone(GOOD["report_gate"]), "summary_length": 34},
     "阈值边界：摘要 34 < 40 判 fail；放宽 20%（40→33.33）翻绿 → M1 靶"),
    ("report_gate", "missing-field",
     _drop(_clone(GOOD["report_gate"]), "summary_length"),
     "缺字段：无摘要长度 → fail(MISSING_FIELD) → M2 靶"),
    ("report_gate", "order-pair-A",
     {**_clone(GOOD["report_gate"]),
      "sections": {"error_judgement": False, "background": True, "method": True,
                   "results": True, "figures": True, "caveats": True}},
     "顺序置换对 A：缺项排首位"),
    ("report_gate", "order-pair-B",
     {**_clone(GOOD["report_gate"]),
      "sections": {"background": True, "method": True, "results": True, "figures": True,
                   "caveats": True, "error_judgement": False}},
     "顺序置换对 B：缺项排末位（5/6 真），判词必须与 A 相同 → M3 靶"),
    ("report_gate", "honest-fail-missing",
     {**_clone(GOOD["report_gate"]), "status": "FAIL", "honest_fail": False},
     "FAIL 结论未带 honest_fail 声明 → fail(RG_HONEST_FAIL)"),
    # ---------------- merge_gate ----------------
    ("merge_gate", "all-green", _clone(GOOD["merge_gate"]),
     "全绿基线：CI 绿 + 8 评审 + 0 冲突 + 守卫 1%"),
    ("merge_gate", "boundary-relax",
     {**_clone(GOOD["merge_gate"]), "approvals": 5},
     "阈值边界：approvals=5 < 6 判 fail；放宽 20%（6→5）翻绿 → M1 靶"),
    ("merge_gate", "missing-field",
     _drop(_clone(GOOD["merge_gate"]), "approvals"),
     "缺字段：无评审数 → fail(MISSING_FIELD) → M2 靶"),
    ("merge_gate", "conflict-present",
     {**_clone(GOOD["merge_gate"]), "conflicts": 2},
     "带冲突合入必须被拦（MR_CONFLICTS）"),
    ("merge_gate", "ci-red",
     {**_clone(GOOD["merge_gate"]), "ci_green": False},
     "CI 未绿 → fail(MR_CI)"),
    # ---------------- autonomy_proposal ----------------
    ("autonomy_proposal", "all-green", _clone(GOOD["autonomy_proposal"]),
     "全绿基线：8 条前置检索 + 新颖锚 + 2 条判据 + 未越红线"),
    ("autonomy_proposal", "boundary-relax",
     {**_clone(GOOD["autonomy_proposal"]), "prior_art_checks": 5},
     "阈值边界：prior_art_checks=5 < 6 判 fail；放宽 20%（6→5）翻绿 → M1 靶"),
    ("autonomy_proposal", "missing-field",
     _drop(_clone(GOOD["autonomy_proposal"]), "novelty_anchors"),
     "缺字段：无新颖锚 → fail(MISSING_FIELD) → M2 靶"),
    ("autonomy_proposal", "order-pair-A",
     {**_clone(GOOD["autonomy_proposal"]),
      "redline_flags": {"strategic_rule": False, "safety": False, "irreversible": True}},
     "顺序置换对 A：越红线标志排末位"),
    ("autonomy_proposal", "order-pair-B",
     {**_clone(GOOD["autonomy_proposal"]),
      "redline_flags": {"irreversible": True, "safety": False, "strategic_rule": False}},
     "顺序置换对 B：越红线标志排首位，判词必须与 A 相同 → M3 靶"),
    # ---------------- campaign_status ----------------
    ("campaign_status", "all-green", _clone(GOOD["campaign_status"]),
     "全绿基线：verified 节点证据 8 条 + 连续失败 1"),
    ("campaign_status", "boundary-relax",
     {"nodes": [{"status": "verified", "evidence_count": 5, "consecutive_fails": 0},
                {"status": "in_flight", "evidence_count": 2, "consecutive_fails": 1}]},
     "阈值边界：verified 证据 5 < 6 判 fail；放宽 20%（6→5）翻绿 → M1 靶"),
    ("campaign_status", "missing-field",
     {"nodes": [{"status": "in_flight", "evidence_count": 2},
                {"status": "pending", "evidence_count": 0, "consecutive_fails": 0}]},
     "缺字段：节点无 consecutive_fails → fail(MISSING_FIELD) → M2 靶"),
    ("campaign_status", "order-pair-A",
     {"nodes": [{"status": "verified", "evidence_count": 5, "consecutive_fails": 0},
                {"status": "in_flight", "evidence_count": 2, "consecutive_fails": 1}]},
     "顺序置换对 A：证据不足的 verified 节点排首位"),
    ("campaign_status", "order-pair-B",
     {"nodes": [{"status": "in_flight", "evidence_count": 2, "consecutive_fails": 1},
                {"status": "verified", "evidence_count": 5, "consecutive_fails": 0}]},
     "顺序置换对 B：同节点集换序，判词必须与 A 相同 → M3 靶"),
    ("campaign_status", "status-vocab-drift",
     {"nodes": [{"status": "verified", "evidence_count": 8, "consecutive_fails": 0},
                {"status": "terminated", "evidence_count": 1, "consecutive_fails": 0}]},
     "未登记状态词 'terminated' → fail(CS_STATUS_ENUM)，暴露状态词表漂移"),
    # ---------------- budget_gate ----------------
    ("budget_gate", "all-green", _clone(GOOD["budget_gate"]),
     "全绿基线：token 50% / 并发 25% / wall 10%"),
    ("budget_gate", "boundary-relax",
     {**_clone(GOOD["budget_gate"]), "spent_tokens": 950},
     "阈值边界：消耗率 0.95 > 0.9 判 fail；放宽 20%（0.9→1.08）翻绿 → M1 靶"),
    ("budget_gate", "missing-field",
     _drop(_clone(GOOD["budget_gate"]), "wall_limit"),
     "缺字段：无 wall 上限 → fail(MISSING_FIELD) → M2 靶"),
    ("budget_gate", "over-concurrency",
     {**_clone(GOOD["budget_gate"]), "concurrent_jobs": 60},
     "并发超配（60 > 40）→ fail(BG_CONCURRENCY)"),
    ("budget_gate", "zero-budget",
     {**_clone(GOOD["budget_gate"]), "budget_tokens": 0},
     "零预算计划不可门禁 → fail(TYPE_ERROR 分母为 0)"),
]


def probe_fixtures() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for mechanism, probe_class, record, notes in PROBES:
        fixtures.append(_fixture(
            mechanism, f"probe-{probe_class}", record, origin="probe",
            probe_class=probe_class,
            source={"table": "synthetic-probe", "record": f"synthetic:{mechanism}:{probe_class}",
                    "backend": "tools.mech_replay.collector.PROBES"},
            field_sources={"*": "synthetic:探针记录（不伪造真实数据）"},
            notes=notes,
        ))
    return fixtures


# --------------------------------------------------------------------------- #
# 落盘 + sha256 清单
# --------------------------------------------------------------------------- #
def collect_fixtures(*, snapshot: Mapping[str, Any] | None = None, out_dir: Path = DEFAULT_OUT,
                     per_mechanism: int = 6) -> dict[str, Any]:
    if snapshot is None:
        base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
        token = api_login(base)
        snapshot = snapshot_from_api(base, token)
    fixtures = derive_fixtures(snapshot, per_mechanism=per_mechanism) + probe_fixtures()
    fixtures.sort(key=lambda f: f["fixture_id"])
    return write_fixtures(fixtures, snapshot, out_dir)


def write_fixtures(fixtures: list[dict[str, Any]], snapshot: Mapping[str, Any],
                   out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    fixture_dir = out_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    # 清掉旧文件，保证目录内容与清单严格对应
    for stale in fixture_dir.rglob("*.json"):
        stale.unlink()

    files: dict[str, str] = {}
    per_mechanism: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    for fixture in fixtures:
        mechanism = fixture["mechanism"]
        rel = f"fixtures/{mechanism}/{fixture['fixture_id'].split('/', 1)[1]}.json"
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(fixture, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
        files[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        per_mechanism[mechanism] = per_mechanism.get(mechanism, 0) + 1
        origin_counts[fixture["origin"]] = origin_counts.get(fixture["origin"], 0) + 1

    snap_path = out_dir / "sources_snapshot.json"
    snap_text = json.dumps(snapshot, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    snap_path.write_text(snap_text, encoding="utf-8")

    manifest = {
        "schema": "mech-replay-manifest-v1",
        "rules_version": spec.RULES_VERSION,
        "fixture_count": len(fixtures),
        "per_mechanism": dict(sorted(per_mechanism.items())),
        "origin_counts": dict(sorted(origin_counts.items())),
        "probe_class_counts": _count(fixtures, lambda f: f["probe_class"] or "real"),
        "mechanisms": sorted({f["mechanism"] for f in fixtures}),
        "files": dict(sorted(files.items())),
        "snapshot_path": "sources_snapshot.json",
        "snapshot_sha256": hashlib.sha256(snap_text.encode("utf-8")).hexdigest(),
        "snapshot_digest": snapshot_digest(snapshot),
        "snapshot_coverage": {
            "reports": len(snapshot.get("reports") or []),
            "plans": len(snapshot.get("plans") or []),
            "campaign_nodes": sum(p.get("node_count", 0) for p in snapshot.get("plans") or []),
            "mission_records": len(snapshot.get("missions") or []),
            # missions 已采集但未派生 fixture：平台缺 per-mission 用量字段，
            # 预算门的「已消耗」无法真实取数（见报告「局限」节）
            "mission_records_derived": 0,
        },
        "generator": "tools.mech_replay.collector v1",
    }
    manifest_text = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (out_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
    return manifest


def _count(fixtures: list[dict[str, Any]], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for fixture in fixtures:
        k = str(key(fixture))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def verify_manifest(out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    """C4：逐文件重算 sha256 与清单比对。"""
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatched, missing, ok = [], [], 0
    for rel, expected in manifest["files"].items():
        path = out_dir / rel
        if not path.exists():
            missing.append(rel)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == expected:
            ok += 1
        else:
            mismatched.append(rel)
    snap = out_dir / manifest["snapshot_path"]
    snap_ok = snap.exists() and hashlib.sha256(snap.read_bytes()).hexdigest() == manifest["snapshot_sha256"]
    return {
        "files_checked": len(manifest["files"]),
        "files_ok": ok,
        "missing": sorted(missing),
        "sha_mismatch": sorted(mismatched),
        "snapshot_sha_ok": snap_ok,
        "all_ok": ok == len(manifest["files"]) and not missing and not mismatched and snap_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mech_replay 采集器：真实记录 → golden fixture")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--per-mechanism", type=int, default=6)
    parser.add_argument("--snapshot", default=None, help="复用已落盘快照（离线可复算）")
    parser.add_argument("--verify", action="store_true", help="只做 sha256 完整性校验")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.verify:
        print(json.dumps(verify_manifest(out), ensure_ascii=False, indent=2))
        return 0
    snapshot = None
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    manifest = collect_fixtures(snapshot=snapshot, out_dir=out,
                                per_mechanism=args.per_mechanism)
    print(json.dumps({k: manifest[k] for k in
                      ("fixture_count", "per_mechanism", "origin_counts",
                       "probe_class_counts", "snapshot_sha256")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())