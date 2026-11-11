"""能力台账成熟化单：只给**证据成立**的节点写 ``feature_ledger``，并生成可回滚脚本。

准入规则（fail-closed，四道全过才 REAL）

1. ``presence.delivery.delivered`` 为真（真有一份可复算 artifact，不是报告里的形容词）；
2. gate 节点 ``pass_ratio ≥ criteria_pass_threshold``（判据过绿线）；
3. 无 ``FAIL`` 判据（缺证据压低的通过率不算过，但真有 FAIL 也不许洗）；
4. 无 ``claim_mismatch``（上游自述与复算相反 = 造假嫌疑，一票否决）。

不满足的节点**不写 INSERT**，进 ``deferred`` 并附具名原因（``no_artifact_delivered`` /
``criteria_failed`` / ``pass_ratio_below_threshold`` / ``claim_mismatch``）——
``WITHHELD`` 绝不写成 ``REAL``。

SQL 形态
--------
``INSERT ... SELECT ... WHERE NOT EXISTS``：按 ``slug`` 幂等，重复执行不产生重复行；
回滚脚本按 ``slug + ledger_group`` 精确 ``DELETE``，并给出行数校验 ``SELECT``。
两条脚本都只含**确定性**文本（键排序、无时间戳），同一输入逐字节相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

LEDGER_SCHEMA = "campaign-maturity-plan/1.0"
LEVEL_REAL = "REAL"
LEVEL_PARTIAL = "PARTIAL"
LEVEL_WITHHELD = "WITHHELD"


def _q(value: Any) -> str:
    """SQL 单引号转义（字符串常量）。"""
    return "'" + str(value).replace("'", "''") + "'"


def _self_entry(self_cfg: Mapping[str, Any], repo_root: Optional[Path],
                self_tests: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """集成节点**自身**机制的能力条目（正控）：证据必须是本仓可复算文件 + 测试真绿。

    这条与上游节点同一个准入函数，但证据来源不同（本仓路径 + pytest 结论），
    故它同时充当「台账写入路径真的有东西可写」的正控——否则零 REAL 时无法区分
    「机制坏了」与「上游确实没交付」。
    """
    paths = [str(p) for p in (self_cfg.get("evidence_paths") or [])]
    digests: Dict[str, Optional[str]] = {}
    for rel in paths:
        path = (repo_root / rel) if repo_root is not None else Path(rel)
        digests[rel] = None
        try:
            from tools.maturity_gate.evidence import sha256_file
            if path.is_file():
                digests[rel] = sha256_file(path)
        except Exception:  # pragma: no cover - 指纹取不到就记 None（不猜）
            digests[rel] = None

    reasons: List[str] = []
    if not paths or any(d is None for d in digests.values()):
        reasons.append("self_evidence_missing")
    if self_cfg.get("require_green_tests") and not (self_tests or {}).get("green"):
        reasons.append("self_tests_not_green")
    return {
        "entry_id": str(self_cfg.get("slug")),
        "slug": str(self_cfg.get("slug")),
        "title": str(self_cfg.get("title") or self_cfg.get("slug")),
        "capability": str(self_cfg.get("capability") or self_cfg.get("title")),
        "domain": str(self_cfg.get("domain") or "unspecified"),
        "repo": str(self_cfg.get("repo") or "TensorPlatform"),
        "mission_id": self_cfg.get("mission_id"),
        "criteria_total": None,
        "criteria_passed": None,
        "pass_ratio": None,
        "self": True,
        "evidence_level": LEVEL_REAL if not reasons else LEVEL_WITHHELD,
        "register": not reasons,
        "defer_reasons": reasons,
        "evidence": {"artifacts": [{"path": p, "sha256": digests.get(p)} for p in paths],
                     "tests": None if self_tests is None else
                     {k: self_tests.get(k) for k in ("returncode", "green")}},
    }


def build_entries(gate: Mapping[str, Any], presence: Mapping[str, Mapping[str, Any]],
                  cfg: Mapping[str, Any], repo_root: Optional[Path] = None,
                  self_tests: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """逐节点算证据等级与准入原因（全部数字来自 gate/presence 两份机器产物）。"""
    cfg_nodes = cfg.get("nodes") or {}
    defaults = cfg.get("defaults") or {}
    threshold = float(gate["campaign"]["criteria_pass_threshold"])
    entries: List[Dict[str, Any]] = []
    for summary in gate["node_summaries"]:
        key = str(summary["node_key"])
        node_cfg = cfg_nodes.get(key) or {}
        doc = presence.get(key) or {}
        delivered = bool((doc.get("delivery") or {}).get("delivered"))
        artifacts = (doc.get("delivery") or {}).get("artifacts") or []

        reasons: List[str] = []
        if not delivered:
            reasons.append("no_artifact_delivered")
        if summary.get("failed"):
            reasons.append("criteria_failed")
        if float(summary.get("pass_ratio") or 0.0) < threshold:
            reasons.append("pass_ratio_below_threshold")
        if summary.get("claim_mismatches"):
            reasons.append("claim_mismatch")

        level = LEVEL_REAL if not reasons else (LEVEL_PARTIAL if delivered else LEVEL_WITHHELD)
        entries.append({
            "entry_id": key,
            "slug": node_cfg.get("slug") or key,
            "title": summary.get("title") or key,
            "capability": summary.get("capability") or summary.get("title") or key,
            "domain": node_cfg.get("domain") or defaults.get("domain") or "unspecified",
            "repo": summary.get("repo"),
            "mission_id": summary.get("mission_id"),
            "criteria_total": summary.get("criteria_total"),
            "criteria_passed": summary.get("passed"),
            "pass_ratio": summary.get("pass_ratio"),
            "evidence_level": level,
            "register": not reasons,
            "defer_reasons": reasons,
            "evidence": {
                "gate_input_digest": gate.get("input_digest"),
                "artifacts": [{"path": a.get("path"), "sha256": a.get("sha256")}
                              for a in artifacts],
                "presence": f"{cfg.get('integration_out')}/presence/{key}.json",
            },
        })
    self_cfg = cfg.get("self_capability")
    if isinstance(self_cfg, dict) and self_cfg.get("slug"):
        entries.append(_self_entry(self_cfg, repo_root, self_tests))
    return entries


def plan_document(entries: Sequence[Mapping[str, Any]], campaign: Mapping[str, Any],
                  provenance: Mapping[str, Any]) -> Dict[str, Any]:
    real = [e for e in entries if e["register"]]
    deferred = [e for e in entries if not e["register"]]
    real_upstream = [e for e in real if not e.get("self")]
    return {
        "schema": LEDGER_SCHEMA,
        "campaign": dict(campaign),
        "provenance": dict(provenance),
        "thresholds": {"real_requires": ["artifact_delivered", "pass_ratio>=threshold",
                                         "no_failed_criteria", "no_claim_mismatch"]},
        "entries_total": len(entries),
        "real_total": len(real),
        "real_upstream_total": len(real_upstream),
        "self_registered": [e["slug"] for e in real if e.get("self")],
        "deferred_total": len(deferred),
        "levels": {lvl: sum(1 for e in entries if e["evidence_level"] == lvl)
                   for lvl in (LEVEL_REAL, LEVEL_PARTIAL, LEVEL_WITHHELD)},
        "entries": list(entries),
        "real": [e["slug"] for e in real],
        "deferred": [{"slug": e["slug"], "reasons": e["defer_reasons"]} for e in deferred],
        "note": "REAL 需四道准入全过；WITHHELD 不等于无能力，只表示本期未交付可复算证据，"
                "故不写 INSERT。",
    }


_HEADER = """-- campaign maturity order · generated by tools.campaign_integration.maturity
-- campaign: {campaign}  plan: {plan}  gate_input_digest: {digest}
-- 只登记 evidence_level=REAL 的条目（四道准入全过）；deferred 条目见 maturity_plan.json
-- 幂等：按 slug 检查 NOT EXISTS；执行前后行数可用文件末尾 SELECT 核对
"""


def render_insert_sql(entries: Sequence[Mapping[str, Any]], campaign: Mapping[str, Any],
                      gate_digest: Optional[str]) -> str:
    group = f"campaign:{campaign.get('name') or campaign.get('plan_id')}"
    lines = [_HEADER.format(campaign=campaign.get("name"), plan=campaign.get("plan_id"),
                            digest=gate_digest).rstrip("\n"), "BEGIN;", ""]
    real = [e for e in entries if e["register"]]
    if not real:
        lines.append("-- 无 REAL 条目：本期所有节点均未满足准入（见 maturity_plan.json）。")
    for e in real:
        evidence = json.dumps(e["evidence"], sort_keys=True, ensure_ascii=False)
        if e.get("self"):
            acceptance = ("self capability: integration pytest green={} + {} evidence files"
                          " pinned by sha256".format(
                              (e["evidence"].get("tests") or {}).get("green"),
                              len(e["evidence"].get("artifacts") or [])))
        else:
            acceptance = "campaign gate: pass_ratio {:.4g} >= threshold; criteria {}/{}".format(
                float(e["pass_ratio"] or 0.0), e["criteria_passed"], e["criteria_total"])
        lines += [
            "INSERT INTO feature_ledger",
            "    (slug, title, domain, source, acceptance, ledger_group, status,"
            " evidence, mission_id)",
            "SELECT {slug}, {title}, {domain}, {source}, {acceptance}, {group}, {status},"
            " {evidence}, {mission}".format(
                slug=_q(e["slug"]), title=_q(e["title"]), domain=_q(e["domain"]),
                source=_q("campaign_integration"), acceptance=_q(acceptance),
                group=_q(group), status=_q(LEVEL_REAL), evidence=_q(evidence),
                mission=_q(e["mission_id"])),
            "WHERE NOT EXISTS (SELECT 1 FROM feature_ledger WHERE slug = {slug});".format(
                slug=_q(e["slug"])),
            "",
        ]
    lines += ["COMMIT;", "",
              "SELECT slug, status, ledger_group FROM feature_ledger",
              " WHERE ledger_group = {group}".format(group=_q(group)),
              " ORDER BY slug;", ""]
    return "\n".join(lines)


def render_rollback_sql(entries: Sequence[Mapping[str, Any]],
                        campaign: Mapping[str, Any]) -> str:
    group = f"campaign:{campaign.get('name') or campaign.get('plan_id')}"
    lines = ["-- rollback for campaign maturity order · generated by"
             " tools.campaign_integration.maturity",
             f"-- campaign: {campaign.get('name')}  plan: {campaign.get('plan_id')}",
             "-- 精确按 slug + ledger_group 回滚（不误删同名历史条目）", "BEGIN;", ""]
    real = [e for e in entries if e["register"]]
    if not real:
        lines.append("-- 本期无 REAL 条目：回滚为空操作（保留可审计的 no-op）。")
    for e in real:
        lines.append("DELETE FROM feature_ledger WHERE slug = {slug} AND ledger_group = {group};"
                     .format(slug=_q(e["slug"]), group=_q(group)))
    lines += ["", "COMMIT;", "",
              "-- 回滚后应为 0 行：",
              "SELECT count(*) AS residual FROM feature_ledger WHERE ledger_group = {group};"
              .format(group=_q(group)), ""]
    return "\n".join(lines)


def rollback_covers(insert_sql: str, rollback_sql: str) -> bool:
    """回归守卫：INSERT 里出现的每个 slug 都必须在回滚脚本里被 DELETE。"""
    import re
    inserted = set(re.findall(r"SELECT '([^']*)',", insert_sql))
    deleted = set(re.findall(r"WHERE slug = '([^']*)'", rollback_sql))
    return inserted <= deleted


__all__ = ["LEDGER_SCHEMA", "LEVEL_REAL", "LEVEL_PARTIAL", "LEVEL_WITHHELD",
           "build_entries", "plan_document", "render_insert_sql", "render_rollback_sql",
           "rollback_covers"]