#!/usr/bin/env python3
"""Terminal-mission attribution: machine-classified evidence grades for every finished mission.

Why this exists
---------------
The fleet KPIs (`guide.success_rate`, the case-escalation loop in guide_tracker, and the
"无人值守案例" counter) are all computed from `tensor_dev_missions.status` alone.  A mission
that never touched its work tree (agent did not call finish / lost / crashed) and a mission
whose *checks actually failed* both land in the same bucket, so the platform cannot tell
"our physics is wrong" from "our worker died".  This module derives a *cause class* for every
terminal mission from machine fields only (``result.evidence.checks.gate``,
``result.stop_condition``, ``result.fail_stage``, ``result.revoked_reason``,
``result.accepted``, ``result.on_disk_changes``) -- never from the free-text self-report in
``result.checks`` -- and reports four separate rates instead of one blended one.

Classes (precedence as listed; first match wins)
-----------------------------------------------
1. ``admin_dedupe``        revoked by owner bulk de-duplication -> administrative, not a quality signal
2. ``infra_crash``         evidence gate ``worker-crash`` (repo missing / traceback / worker died)
3. ``infra_runtime``       ``agent_lost`` or runtime stage in {llm_error, wall_exhausted, budget_exhausted, no_progress}
4. ``evidence_absent``     evidence gate ``no-evidence`` (work tree untouched / finish never called)
5. ``claim_falsified``     revoked for a claim that was independently disproved -> real quality failure
6. ``acceptance_rejected`` failed with an explicit acceptance decision against it -> real technical failure
7. ``failed_other``        failed without any machine marker
8. ``succeeded_unverified``succeeded but no evidence gate pass and/or no on-disk change
9. ``succeeded_verified``  succeeded with evidence and at least one changed artefact

Usage
-----
    # from the platform DB (needs psql + PGPASSWORD)
    python3 tools/validation/mission_attribution.py --from-db \
        --snapshot artifacts/m1814/missions_snapshot.jsonl \
        --out-json artifacts/m1814/mission_attribution.json \
        --out-md artifacts/m1814/mission_attribution.md \
        --out-fig artifacts/m1814/mission_attribution.png

    # reproduce from the committed snapshot (no DB needed, byte-identical numbers)
    python3 tools/validation/mission_attribution.py --jsonl artifacts/m1814/missions_snapshot.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict
from typing import Any, Dict, Iterable, List, Optional

TERMINAL = ("succeeded", "failed", "revoked")

# ── machine markers ────────────────────────────────────────────────────────────
DEDUPE_RE = re.compile(r"dup-dispatch|批量停重复|重复派发|去重停单", re.I)
RUNTIME_STAGE_RE = re.compile(r"llm_error|wall_exhausted|budget_exhausted|no_progress|timeout", re.I)
CASE_RE = re.compile(r"案例\s+([a-zA-Z][a-zA-Z0-9_]+)\s*（")

CLASSES = (
    "admin_dedupe",
    "infra_crash",
    "infra_runtime",
    "evidence_absent",
    "claim_falsified",
    "acceptance_rejected",
    "failed_other",
    "succeeded_unverified",
    "succeeded_verified",
)
# classes that are legitimate *technical* outcomes (quality signal)
TECHNICAL_FAIL = ("claim_falsified", "acceptance_rejected", "failed_other")
# classes that are loss of work for non-technical reasons
INFRA_LOSS = ("infra_crash", "infra_runtime", "evidence_absent")

CLASS_LABEL = {
    "admin_dedupe": "行政去重停单（不计质量）",
    "infra_crash": "基础设施崩溃（仓缺失/异常退出）",
    "infra_runtime": "运行期丢失（llm_error/agent_lost/预算耗尽）",
    "evidence_absent": "无证据（工作树零改动/未 finish）",
    "claim_falsified": "声明被独立复核证伪",
    "acceptance_rejected": "验收判据拒绝",
    "failed_other": "失败（无机器标记）",
    "succeeded_unverified": "报成功但无证据闸",
    "succeeded_verified": "成功且有证据+落盘改动",
}

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Kept in the snapshot so the committed artefact alone reproduces every number.
SNAPSHOT_RESULT_KEYS = (
    "model",
    "accepted",
    "decision_note",
    "revoked",
    "revoked_reason",
    "stop_condition",
    "fail_stage",
    "on_disk_changes",
    "changed_files",
)


# ── input ─────────────────────────────────────────────────────────────────────
def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def result_of(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Return the mission result payload as a dict (DB rows may hold it as text)."""
    return _as_dict(mission.get("result"))


def _nested(result: Dict[str, Any], *path: str) -> Any:
    cur: Any = result
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def gates_and_markers(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten every machine marker this module classifies on."""
    result = result_of(mission)
    checks = _nested(result, "evidence", "checks")
    checks = checks if isinstance(checks, dict) else {}
    summary = _nested(result, "evidence", "summary")
    summary = summary if isinstance(summary, dict) else {}
    changes = result.get("on_disk_changes") or result.get("changed_files") or []
    if isinstance(changes, str):
        changes = [changes] if changes.strip() else []
    if not changes and "has_changes" in result:  # compacted snapshot marker
        changes = ["<snapshot>"] if result.get("has_changes") else []
    return {
        "status": mission.get("status"),
        "gate": checks.get("gate"),
        "gate_reason": checks.get("reason"),
        "fail_stage": result.get("fail_stage") or checks.get("fail_stage") or "",
        "stop_condition": result.get("stop_condition"),
        "revoked": bool(result.get("revoked")),
        "revoked_reason": str(result.get("revoked_reason") or ""),
        "accepted": result.get("accepted"),
        "decision_note": result.get("decision_note"),
        "n_changes": len([c for c in changes if str(c).strip()]),
        "model": result.get("model") or summary.get("model") or mission.get("model"),
        "case_key": case_key(mission),
    }


def case_key(mission: Dict[str, Any]) -> Optional[str]:
    """Extract the sweep case name (e.g. ``dam_break_sc``) from the mission goal."""
    if mission.get("case_key"):
        return str(mission["case_key"])
    for text in (mission.get("goal"), mission.get("goal_head"), ""):
        if not text:
            continue
        hit = CASE_RE.search(str(text))
        if hit:
            return hit.group(1)
    return None


# ── the classifier ────────────────────────────────────────────────────────────
def classify(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Map one mission to (primary class, flag list, evidence basis).

    Only machine fields are consulted; free-text ``result.checks`` (the agent's own
    claims) is never read, by design.
    """
    m = gates_and_markers(mission)
    status = m["status"]
    if status not in TERMINAL:
        raise ValueError(f"mission status {status!r} is not terminal; refusing to classify")

    flags: List[str] = []
    if m["gate"] == "no-evidence":
        flags.append("no_evidence")
    if m["gate"] == "worker-crash":
        flags.append("worker_crash")
    if m["stop_condition"] == "agent_lost":
        flags.append("agent_lost")
    if RUNTIME_STAGE_RE.search(str(m["fail_stage"])):
        flags.append("runtime_stage")
    if m["revoked"] and DEDUPE_RE.search(m["revoked_reason"]):
        flags.append("admin_dedupe")
    if m["revoked"] and not DEDUPE_RE.search(m["revoked_reason"]):
        flags.append("claim_falsified")
    if status == "succeeded" and "runtime_stage" in flags:
        flags.append("forced_finish")
    if m["n_changes"] == 0:
        flags.append("no_changes")

    if status == "revoked" and "admin_dedupe" in flags:
        primary, basis = "admin_dedupe", "revoked_reason~dup-dispatch"
    elif m["gate"] == "worker-crash":
        primary, basis = "infra_crash", "evidence.checks.gate=worker-crash"
    elif status != "succeeded" and ("agent_lost" in flags or "runtime_stage" in flags):
        primary, basis = "infra_runtime", f"stop_condition={m['stop_condition']}/fail_stage={m['fail_stage']}"
    elif m["gate"] == "no-evidence" and status != "succeeded":
        primary, basis = "evidence_absent", "evidence.checks.gate=no-evidence"
    elif status == "revoked":
        primary, basis = "claim_falsified", "revoked_reason(非去重)"
    elif status == "failed" and (m["accepted"] is False or m["decision_note"]):
        primary, basis = "acceptance_rejected", "accepted=False/decision_note"
    elif status == "failed":
        primary, basis = "failed_other", "status=failed,无机器标记"
    elif m["gate"] == "no-evidence" or m["n_changes"] == 0:
        primary, basis = "succeeded_unverified", "status=succeeded 但 gate=no-evidence/零改动"
    else:
        primary, basis = "succeeded_verified", "status=succeeded+gate通过+有落盘改动"

    return {
        "id": mission.get("id"),
        "status": status,
        "repo": mission.get("repo"),
        "model": m["model"],
        "case_key": m["case_key"],
        "primary": primary,
        "flags": sorted(flags),
        "basis": basis,
    }


# ── aggregation ───────────────────────────────────────────────────────────────
def _rate(num: int, den: int) -> Optional[float]:
    return (num / den) if den else None


def summarize(mission_classes: Iterable[Dict[str, Any]],
              cases: Iterable[str] = (),
              ) -> Dict[str, Any]:
    rows = list(mission_classes)
    by_class = Counter(r["primary"] for r in rows)
    total = len(rows)
    succ_all = sum(v for k, v in by_class.items() if k.startswith("succeeded"))
    verified = by_class.get("succeeded_verified", 0)
    tech_fail = sum(by_class.get(k, 0) for k in TECHNICAL_FAIL)
    infra = sum(by_class.get(k, 0) for k in INFRA_LOSS)
    admin = by_class.get("admin_dedupe", 0)

    def per(key: str) -> "OrderedDict[str, Any]":
        groups = sorted({str(r.get(key) or "unknown") for r in rows})
        out: "OrderedDict[str, Any]" = OrderedDict()
        for g in groups:
            sub = [r for r in rows if str(r.get(key) or "unknown") == g]
            c = Counter(r["primary"] for r in sub)
            out[g] = {
                "n": len(sub),
                "succeeded_verified": c.get("succeeded_verified", 0),
                "succeeded_unverified": c.get("succeeded_unverified", 0),
                "technical_fail": sum(c.get(k, 0) for k in TECHNICAL_FAIL),
                "infra_loss": sum(c.get(k, 0) for k in INFRA_LOSS),
                "admin_dedupe": c.get("admin_dedupe", 0),
                "infra_loss_share": _rate(sum(c.get(k, 0) for k in INFRA_LOSS), len(sub)),
                "verified_share": _rate(c.get("succeeded_verified", 0), len(sub)),
            }
        return out

    case_rows: Dict[str, Any] = {}
    wanted = [c for c in cases if c]
    if wanted:
        for case in wanted:
            sub = [r for r in rows if r.get("case_key") == case]
            c = Counter(r["primary"] for r in sub)
            tech = sum(c.get(k, 0) for k in TECHNICAL_FAIL)
            case_rows[case] = {
                "n": len(sub),
                "by_class": {k: c.get(k, 0) for k in CLASSES if c.get(k)},
                "technical_fail": tech,
                "non_technical": len(sub) - tech,
                "escalation_would_fire_technical_only": tech >= 3,
                "escalation_fires_with_status_only": len(sub) >= 3,
            }

    flag_counts = Counter(f for r in rows for f in r["flags"])
    return {
        "total_terminal": total,
        "by_class": {k: by_class.get(k, 0) for k in CLASSES},
        "unclassified": total - sum(by_class.get(k, 0) for k in CLASSES),
        "flag_counts": dict(flag_counts.most_common()),
        "rates": {
            "raw_status_success_rate": _rate(succ_all, total),
            "verified_delivery_rate": _rate(verified, total),
            "technical_pass_rate": _rate(verified, verified + tech_fail),
            "infra_loss_share": _rate(infra, total),
            "admin_share": _rate(admin, total),
            "raw_success_rate_excl_admin": _rate(succ_all, total - admin),
        },
        "by_model": per("model"),
        "by_repo": per("repo"),
        "cases": case_rows,
    }


# ── rendering ─────────────────────────────────────────────────────────────────
def _pct(value: Optional[float], digits: int = 2) -> str:
    return "n/a" if value is None else f"{100.0 * value:.{digits}f}%"


def render_markdown(stats: Dict[str, Any], source: str = "platform DB") -> str:
    r = stats["rates"]
    lines = [
        "## 终态单归因（机器字段判定，程序化产出）",
        "",
        f"数据源：{source} · 终态单 N = **{stats['total_terminal']}** · 未归因 = **{stats['unclassified']}**",
        "",
        "| 类别 | 含义 | 计数 | 占终态单 |",
        "|---|---|---:|---:|",
    ]
    n = stats["total_terminal"]
    for cls in CLASSES:
        cnt = stats["by_class"].get(cls, 0)
        lines.append(f"| `{cls}` | {CLASS_LABEL[cls]} | {cnt} | {_pct(cnt / n if n else None)} |")
    lines += [
        "",
        "| 口径 | 定义 | 数值 |",
        "|---|---|---:|",
        f"| raw_status_success_rate | status=succeeded / 终态单（现行 KPI 口径） | {_pct(r['raw_status_success_rate'])} |",
        f"| raw_success_rate_excl_admin | 剔除行政去重停单后的 status 成功率 | {_pct(r['raw_success_rate_excl_admin'])} |",
        f"| verified_delivery_rate | 成功且有证据+落盘改动 / 终态单 | {_pct(r['verified_delivery_rate'])} |",
        f"| technical_pass_rate | 已验证交付 / (已验证交付 + 技术性失败) | {_pct(r['technical_pass_rate'])} |",
        f"| infra_loss_share | 非技术性丢失 / 终态单 | {_pct(r['infra_loss_share'])} |",
        "",
        "### 按模型",
        "",
        "| model | n | verified | technical_fail | infra_loss | infra_loss 占比 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, row in stats["by_model"].items():
        lines.append(f"| {key} | {row['n']} | {row['succeeded_verified']} | {row['technical_fail']} | "
                     f"{row['infra_loss']} | {_pct(row['infra_loss_share'])} |")
    lines += [
        "",
        "### 按仓",
        "",
        "| repo | n | verified | technical_fail | infra_loss | infra_loss 占比 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, row in stats["by_repo"].items():
        lines.append(f"| {key} | {row['n']} | {row['succeeded_verified']} | {row['technical_fail']} | "
                     f"{row['infra_loss']} | {_pct(row['infra_loss_share'])} |")
    if stats["cases"]:
        lines += [
            "",
            "### 升级信号回放（guide_tracker 标记为 escalated 的案例）",
            "",
            "| case | n | 技术性失败 | 非技术性 | 现行规则触发升级 | 仅技术失败触发升级 |",
            "|---|---:|---:|---:|---|---|",
        ]
        for case, row in stats["cases"].items():
            lines.append(
                f"| {case} | {row['n']} | {row['technical_fail']} | {row['non_technical']} | "
                f"{'是' if row['escalation_fires_with_status_only'] else '否'} | "
                f"{'是' if row['escalation_would_fire_technical_only'] else '否'} |")
    return "\n".join(lines) + "\n"


def plot(stats: Dict[str, Any], path: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0), gridspec_kw={"width_ratios": [1.35, 1.0, 1.15]})
    n = max(stats["total_terminal"], 1)

    ax = axes[0]
    labels = [CLASS_LABEL[c] for c in CLASSES]
    counts = [stats["by_class"].get(c, 0) for c in CLASSES]
    colors = ["#9aa5b1" if c == "admin_dedupe" else
              "#d1495b" if c in TECHNICAL_FAIL else
              "#f0a202" if c in INFRA_LOSS else "#2e86ab" for c in CLASSES]
    ax.barh(range(len(CLASSES)), counts, color=colors)
    ax.set_yticks(range(len(CLASSES)))
    ax.set_yticklabels([f"{l}" for l in labels], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("missions")
    ax.set_title(f"Terminal missions by cause class (N={stats['total_terminal']})")
    for i, v in enumerate(counts):
        ax.text(v + n * 0.005, i, f"{v}  ({100*v/n:.1f}%)", va="center", fontsize=8)

    ax = axes[1]
    r = stats["rates"]
    keys = ["raw_status_success_rate", "raw_success_rate_excl_admin",
            "verified_delivery_rate", "technical_pass_rate"]
    vals = [100 * (r[k] or 0) for k in keys]
    bars = ax.bar(range(len(keys)), vals, color=["#8d99ae", "#457b9d", "#2a9d8f", "#1d3557"])
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels(["raw\n(status)", "raw\nexcl. admin", "verified\ndelivery", "technical\npass"],
                       fontsize=8.5)
    ax.set_ylim(0, 105)
    ax.set_ylabel("%")
    ax.set_title("Four rates instead of one blended KPI")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.2f}%", ha="center", fontsize=8.5)

    ax = axes[2]
    models = list(stats["by_model"].keys())[:12]
    verified = [stats["by_model"][m]["succeeded_verified"] for m in models]
    infra = [stats["by_model"][m]["infra_loss"] for m in models]
    tech = [stats["by_model"][m]["technical_fail"] for m in models]
    admin = [stats["by_model"][m]["admin_dedupe"] for m in models]
    idx = range(len(models))
    ax.barh(idx, verified, color="#2a9d8f", label="verified delivery")
    ax.barh(idx, tech, left=verified, color="#d1495b", label="technical fail")
    ax.barh(idx, infra, left=[a + b for a, b in zip(verified, tech)], color="#f0a202",
            label="infra loss")
    ax.barh(idx, admin, left=[a + b + c for a, b, c in zip(verified, tech, infra)],
            color="#9aa5b1", label="admin dedupe")
    ax.set_yticks(list(idx))
    ax.set_yticklabels([str(m)[:22] for m in models], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("missions")
    ax.set_title("Per model: delivery vs technical vs infra loss")
    ax.legend(fontsize=7.5, loc="lower right")

    fig.suptitle("Terminal-mission attribution — separating physics failures from worker deaths",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ── DB input / snapshot ───────────────────────────────────────────────────────
DUMP_SQL = """SELECT json_build_object(
    'id', id, 'status', status, 'repo', repo, 'model', model, 'task_key', task_key,
    'created_at', created_at, 'finished_at', finished_at, 'goal', left(goal, 600),
    'result', result)::text
  FROM tensor_dev_missions
 WHERE status IN ('succeeded','failed','revoked')
 ORDER BY id"""


AGENT_ENV = "/srv/tensor/agent/agent.env"


def _password_from_env_file(path: str = AGENT_ENV) -> str:
    """Fall back to the fleet's agent env file (never committed, never echoed)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TP_PG_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def fetch_from_db(sql: str = DUMP_SQL, env: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Read terminal missions out of the platform DB (read-only SELECT)."""
    pg = {
        "PGPASSWORD": os.environ.get("TP_PG_PASSWORD") or _password_from_env_file(),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    pg.update({k: v for k, v in (env or {}).items() if v})
    out = subprocess.run(
        ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
         "-U", os.environ.get("TP_PG_USER", "foundation"),
         "-d", os.environ.get("TP_PG_DB", "tensorplatform"), "-tAc", sql],
        capture_output=True, text=True, env=pg, timeout=180,
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr[:400]}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def compact_result(result: Any) -> Dict[str, Any]:
    """Keep only classification-relevant fields; drops free-text self-reports."""
    res = _as_dict(result)
    checks = _nested(res, "evidence", "checks")
    checks = checks if isinstance(checks, dict) else {}
    summary = _nested(res, "evidence", "summary")
    summary = summary if isinstance(summary, dict) else {}
    out: Dict[str, Any] = {}
    for key in SNAPSHOT_RESULT_KEYS:
        if key not in res or res[key] in (None, "", [], {}):
            continue
        if key in ("on_disk_changes", "changed_files"):
            out["has_changes"] = True
            continue
        out[key] = res[key]
    if "has_changes" not in out:
        out["has_changes"] = False
    if not out.get("model") and summary.get("model"):
        out["model"] = summary["model"]
    keep = {k: checks[k] for k in ("gate", "reason", "fail_stage") if k in checks}
    if keep:
        out["evidence"] = {"checks": keep}
    return out


def snapshot(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snap = []
    for row in rows:
        if row.get("status") not in TERMINAL:
            continue
        snap.append({
            "id": row.get("id"),
            "status": row.get("status"),
            "repo": row.get("repo"),
            "model": row.get("model"),
            "case_key": case_key(row),
            "result": compact_result(row.get("result")),
        })
    return snap


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


# ── CLI ───────────────────────────────────────────────────────────────────────
DEFAULT_CASES = ("lid_driven_cavity_3d", "dam_break_sc", "forchheimer", "kelvin_helmholtz")


def run(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-db", action="store_true", help="read terminal missions via psql")
    src.add_argument("--jsonl", help="read a mission JSONL snapshot instead")
    ap.add_argument("--snapshot", help="write a compact mission snapshot (JSONL) from --from-db")
    ap.add_argument("--out-json", help="write aggregated stats JSON")
    ap.add_argument("--out-md", help="write the rendered markdown tables")
    ap.add_argument("--out-fig", help="write the summary figure (requires matplotlib)")
    ap.add_argument("--cases", default=",".join(DEFAULT_CASES), help="comma separated case keys to replay")
    args = ap.parse_args(argv)

    if args.jsonl:
        rows, source = load_jsonl(args.jsonl), os.path.basename(args.jsonl)
    else:
        rows, source = fetch_from_db(), "tensor_dev_missions (live)"
    if args.snapshot:
        os.makedirs(os.path.dirname(os.path.abspath(args.snapshot)), exist_ok=True)
        with open(args.snapshot, "w", encoding="utf-8") as fh:
            for rec in snapshot(rows):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    classes = [classify(r) for r in rows]
    stats = summarize(classes, cases=[c for c in args.cases.split(",") if c])
    stats["source"] = source

    md = render_markdown(stats, source)
    if args.out_md:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_md)), exist_ok=True)
        open(args.out_md, "w", encoding="utf-8").write(md)
    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        json.dump(stats, open(args.out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if args.out_fig:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_fig)), exist_ok=True)
        plot(stats, args.out_fig)

    print(md)
    if stats["unclassified"]:
        print(f"GATE FAIL: {stats['unclassified']} terminal missions unclassified", file=sys.stderr)
        return stats
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    stats = run(argv)
    return 1 if stats.get("unclassified") else 0


if __name__ == "__main__":
    sys.exit(main())