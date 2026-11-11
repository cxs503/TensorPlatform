#!/usr/bin/env python3
"""KPI-QC-RECHECK 计量引擎 —— 已合并 agent PR 的独立参考复核通过率。

只计量，不改任何代码/数据：连接置 ``read_only``，全部 SQL 是 SELECT。

用法::

    # 真实读数 + 工件落盘
    python3 -m tools.kpi_qc_recheck.measure --days 30 --outdir artifacts/kpi_qc_recheck

    # 只打主读数（给 CI/看板）
    python3 -m tools.kpi_qc_recheck.measure --days 30 --quiet

    # 平台 API 内联调用（backend/services/platform_kpi.py）
    from tools.kpi_qc_recheck.measure import measure_with_session
    measure_with_session(db, days=30)

聚合逻辑是**纯函数**（``aggregate``），不碰数据库 —— 因此可脱库单测
（tests/test_kpi_qc_recheck.py 用合成 FakeReader 断言不变式）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from tools.kpi_qc_recheck import spec
from tools.kpi_qc_recheck.queries import PgReader, SessionReader

VOL_KEYS = {"measured_at", "generated_at", "provenance", "dsn"}
PENDING_GRACE = ""


# ── 小工具 ────────────────────────────────────────────────────────────

def _norm_ts(v: Any) -> str:
    """时间统一成 ISO-8601 UTC 字符串；None/空 → ''。"""
    if v is None:
        return ""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(v)


def _parse_ts(v: Any) -> datetime | None:
    s = _norm_ts(v)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canonical(obj: Any) -> str:
    """稳定序列化（剔除挥发键），用于 provenance 摘要的可复现性。"""

    def prune(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: prune(v) for k, v in sorted(o.items()) if k not in VOL_KEYS}
        if isinstance(o, list):
            return [prune(v) for v in o]
        return o

    return json.dumps(prune(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ── 聚合纯函数 ────────────────────────────────────────────────────────

def dedupe_denominator(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """分母去重：同一 (mission_id, repo, pr) 只算一条 PR，取最早 merged_at。"""
    best: dict[tuple[int, str, str], dict[str, Any]] = {}
    for r in rows:
        mid = int(r.get("mission_id") or 0)
        key = (mid, str(r.get("repo") or ""), str(r.get("pr") or ""))
        ts = _norm_ts(r.get("merged_at"))
        cur = best.get(key)
        if cur is None or (ts and (not cur["merged_at"] or ts < cur["merged_at"])):
            best[key] = {"mission_id": mid, "repo": key[1], "pr": key[2], "merged_at": ts}
    return [best[k] for k in sorted(best)]


def index_evaluations(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """把复核记录折成 per-mission 标志位（口径阶梯的原料）。"""
    idx: dict[int, dict[str, Any]] = {}
    for r in rows:
        mid = int(r.get("mission_id") or 0)
        e = idx.setdefault(mid, {
            "missions": mid, "rows": 0, "any_row": False,
            "any_reviewed": False, "ind_reviewed": False, "ind_cross": False,
            "first_ind_at": "", "last_decided_at": "", "kinds": set(),
        })
        e["rows"] += 1
        e["any_row"] = True
        kind = str(r.get("reviewer_kind") or "")
        rs = str(r.get("review_status") or "")
        st = str(r.get("status") or "")
        decided = _norm_ts(r.get("decided_at"))
        e["kinds"].add(kind or "(none)")
        if decided and decided > e["last_decided_at"]:
            e["last_decided_at"] = decided
        if rs == "reviewed" and st == "confirmed":
            e["any_reviewed"] = True
            if kind == "independent":
                e["ind_reviewed"] = True
                if bool(r.get("cross_model_verified")):
                    e["ind_cross"] = True
                if decided and (not e["first_ind_at"] or decided < e["first_ind_at"]):
                    e["first_ind_at"] = decided
    for e in idx.values():
        e["kinds"] = sorted(e["kinds"])
    return idx


def _pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def aggregate(
    den_rows: Iterable[dict[str, Any]],
    eval_rows: Iterable[dict[str, Any]],
    *,
    days: int = spec.DEFAULT_WINDOW_DAYS,
    measured_at: str | None = None,
    supply_rows: Iterable[dict[str, Any]] | None = None,
    backlog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把原始行折成 KPI 结果（纯函数，无 IO）。"""
    prs = dedupe_denominator(den_rows)
    idx = index_evaluations(eval_rows)
    n_pr = len(prs)
    n_mission = len({p["mission_id"] for p in prs})

    def flag(p: dict[str, Any], key: str) -> bool:
        return bool(idx.get(p["mission_id"], {}).get(key))

    d0 = sum(1 for p in prs if flag(p, "any_row"))
    d1 = sum(1 for p in prs if flag(p, "any_reviewed"))
    d2 = sum(1 for p in prs if flag(p, "ind_reviewed"))
    d3 = sum(1 for p in prs if flag(p, "ind_cross"))

    unreviewed = [p for p in prs if not flag(p, "ind_reviewed")]
    reviewed = [p for p in prs if flag(p, "ind_reviewed")]

    rate = _pct(d2, n_pr)
    gap = round(spec.TARGET - rate, 2)

    # 分仓覆盖
    per_repo: dict[str, dict[str, Any]] = {}
    for p in prs:
        s = per_repo.setdefault(p["repo"], {"repo": p["repo"], "merged_prs": 0,
                                           "any_review": 0, "ind_reviewed": 0, "missions": set()})
        s["merged_prs"] += 1
        s["missions"].add(p["mission_id"])
        if flag(p, "any_row"):
            s["any_review"] += 1
        if flag(p, "ind_reviewed"):
            s["ind_reviewed"] += 1
    per_repo_rows = []
    for s in sorted(per_repo.values(), key=lambda x: (-x["merged_prs"], x["repo"])):
        per_repo_rows.append({
            "repo": s["repo"], "merged_prs": s["merged_prs"],
            "missions": len(s["missions"]), "any_review": s["any_review"],
            "ind_reviewed": s["ind_reviewed"], "rate_pct": _pct(s["ind_reviewed"], s["merged_prs"]),
        })

    # 逐日
    daily: dict[str, dict[str, int]] = {}
    for p in prs:
        day = (p["merged_at"] or "")[:10] or "unknown"
        s = daily.setdefault(day, {"day": day, "merged_prs": 0, "ind_reviewed": 0})
        s["merged_prs"] += 1
        if flag(p, "ind_reviewed"):
            s["ind_reviewed"] += 1
    daily_rows = [daily[k] for k in sorted(daily)]

    # 阶段切分：以"最后一次独立复核给出结论"为切点（自动判定，不手填）
    ind_times = [e["last_decided_at"] for e in idx.values() if e["ind_reviewed"] and e["last_decided_at"]]
    pivot_date = max(ind_times)[:10] if ind_times else ""
    phase_split = {}
    if pivot_date:
        before = [p for p in prs if (p["merged_at"] or "")[:10] and (p["merged_at"] or "")[:10] <= pivot_date]
        after = [p for p in prs if (p["merged_at"] or "")[:10] > pivot_date]
        for label, part in (("before_or_on_pivot", before), ("after_pivot", after)):
            phase_split[label] = {
                "cutoff": pivot_date, "merged_prs": len(part),
                "ind_reviewed": sum(1 for p in part if flag(p, "ind_reviewed")),
            }
            phase_split[label]["rate_pct"] = _pct(phase_split[label]["ind_reviewed"], len(part))

    # 复核时延（合并 → 首个独立复核结论）
    lat_hours: list[float] = []
    for p in reviewed:
        merged = _parse_ts(p["merged_at"])
        first = _parse_ts(idx[p["mission_id"]]["first_ind_at"])
        if merged and first:
            dh = (first - merged).total_seconds() / 3600.0
            if dh >= 0:
                lat_hours.append(dh)
    latency = {
        "n": len(lat_hours),
        "median_h": round(statistics.median(lat_hours), 2) if lat_hours else None,
        "p90_h": round(sorted(lat_hours)[max(0, int(0.9 * (len(lat_hours) - 1)))], 2) if lat_hours else None,
    }

    supply = [dict(r) for r in (supply_rows or [])]

    return {
        "anchor": spec.ANCHOR,
        "kpi_key": spec.KPI_KEY,
        "title": spec.KPI_TITLE,
        "unit": spec.UNIT,
        "target": spec.TARGET,
        "current": rate,
        "gap": gap,
        "numerator": {"ind_reviewed_prs": d2, "ind_reviewed_missions": len({p["mission_id"] for p in reviewed})},
        "denominator": {"merged_prs": n_pr, "merged_missions": n_mission},
        "ladder": {
            "D0_any_record": {"prs": d0, "pct": _pct(d0, n_pr)},
            "D1_any_confirmed": {"prs": d1, "pct": _pct(d1, n_pr)},
            "D2_independent_confirmed": {"prs": d2, "pct": _pct(d2, n_pr)},
            "D3_independent_cross_model": {"prs": d3, "pct": _pct(d3, n_pr)},
            "definitions": spec.LADDER,
        },
        "phase_split": phase_split,
        "per_repo": per_repo_rows,
        "daily": daily_rows,
        "latency": latency,
        "review_supply": supply,
        "pending_backlog": dict(backlog or {}),
        "unreviewed_prs": [dict(p, has_any_review=flag(p, "any_row")) for p in unreviewed],
        "data_sources": spec.DATA_SOURCES,
        "definitions": {"denominator": spec.DENOMINATOR_PREDICATES,
                        "numerator": spec.NUMERATOR_PREDICATES},
        "window": {"days": int(days)},
        "measured_at": measured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


# ── 组织产物 ──────────────────────────────────────────────────────────

RECOMPUTE_SQL = """-- KPI-QC-RECHECK 独立复算（手写单条 SQL，不复用 measure.py 的查询模板）
-- 用法: psql -f recompute.sql   （只读，输出 N | M | rate_pct）
WITH den AS (
  SELECT DISTINCT mission_id,
         COALESCE(detail->>'repo','') AS repo,
         COALESCE(detail->>'pr','')   AS pr
    FROM mission_lifecycle_log
   WHERE action = 'pr_gate'
     AND detail->>'state' = 'merged'
     AND created_at > now() - make_interval(days => :days)
), num AS (
  SELECT DISTINCT mission_id
    FROM outcome_evaluations
   WHERE reviewer_kind = 'independent'
     AND review_status = 'reviewed'
     AND status = 'confirmed'
)
SELECT (SELECT count(*) FROM den) AS n_merged_prs,
       (SELECT count(*) FROM den d WHERE d.mission_id IN (SELECT mission_id FROM num)) AS m_ind_reviewed,
       round(100.0 * (SELECT count(*) FROM den d WHERE d.mission_id IN (SELECT mission_id FROM num))
             / NULLIF((SELECT count(*) FROM den), 0), 2) AS rate_pct;
"""

TOP_GAPS = [
    "独立复核覆盖率距 100% 目标的缺口（见 current/gap）",
    "复核供给侧是否停产（见 review_supply 逐日状态堆叠；有结论行占比）",
    "待裁决积压（见 pending_backlog.pending_rows，只建行不裁决=队列空转）",
    "零复核记录仓（见 per_repo 中 ind_reviewed=0 且 merged_prs>0）",
    "auto_scan 冒充满复核的虚高量（D1 - D2 个百分点差）",
]

PROPOSED_ANCHORS = [
    {"anchor": "KPI-QC-LATENCY-24H",
     "acceptance": "合并→独立复核结论时延 P90 ≤ 24h（由 latency.p90_h 逐日复算）"},
    {"anchor": "KPI-QC-COVERAGE-80",
     "acceptance": "独立复核覆盖率 ≥ 80%，验收=recompute.sql 复算一致且未复核清单条数 ≤ 20% 分母"},
    {"anchor": "KPI-QC-INDEP-PURITY",
     "acceptance": "分子中 independent 占比 ≥ 90%，auto_scan 不得计入分子（D2 与 D1 差值 ≤ 1pp）"},
]


def _write_csv(path: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def git_sensitivity(repo_dir: str, days: int, subdir: str = "") -> dict[str, Any]:
    """第三源：git 合并提交数（``Merge #N (mX) via merge-master``），只作分母敏感性对照。"""
    try:
        out = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--pretty=%s"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"available": False}
    n = sum(1 for line in out.splitlines() if line.startswith("Merge #") and "via merge-master" in line)
    return {"available": True, "source": "git log --pretty=%s | Merge #N via merge-master",
            "merge_commits": n, "days": days, "subdir": subdir}


def write_artifacts(result: dict[str, Any], raw: dict[str, list[dict[str, Any]]],
                    outdir: str) -> dict[str, str]:
    """落盘：主结果 JSON + 原始行 CSV + 复算 SQL + provenance（每件 sha256）。"""
    os.makedirs(outdir, exist_ok=True)
    paths: dict[str, str] = {}

    # 主结果（未复核清单单独落 CSV，JSON 里保留计数以免体积膨胀）
    slim = dict(result)
    slim["unreviewed_count"] = len(result.get("unreviewed_prs", []))
    slim["unreviewed_prs"] = result.get("unreviewed_prs", [])[:200]  # 预览前 200 行，全量见 CSV
    slim["top_gaps"] = TOP_GAPS
    slim["proposed_anchors"] = PROPOSED_ANCHORS
    slim["repro_cmd"] = ("python3 -m tools.kpi_qc_recheck.measure --days %s --outdir %s"
                         % (result["window"]["days"], outdir))
    p = os.path.join(outdir, "kpi_result.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, ensure_ascii=False, indent=2, sort_keys=True)
    paths["kpi_result"] = p

    p = os.path.join(outdir, "recompute.sql")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(RECOMPUTE_SQL.replace(":days", str(result["window"]["days"])))
    paths["recompute_sql"] = p

    _write_csv(os.path.join(outdir, "unreviewed_prs.csv"),
               result.get("unreviewed_prs", []),
               ["repo", "mission_id", "pr", "merged_at", "has_any_review"])
    _write_csv(os.path.join(outdir, "per_repo.csv"), result.get("per_repo", []),
               ["repo", "merged_prs", "missions", "any_review", "ind_reviewed", "rate_pct"])
    _write_csv(os.path.join(outdir, "daily.csv"), result.get("daily", []),
               ["day", "merged_prs", "ind_reviewed"])
    _write_csv(os.path.join(outdir, "raw_denominator.csv"), raw.get("denominator", []),
               ["mission_id", "repo", "pr", "merged_at"])
    _write_csv(os.path.join(outdir, "raw_evaluations.csv"), raw.get("evaluations", []),
               ["mission_id", "reviewer_kind", "review_status", "status",
                "cross_model_verified", "decided_at"])

    # provenance：逐件 sha256 + SQL 原文摘要 + 口径常数摘要（可复算、可追溯）
    prov: dict[str, Any] = {
        "measured_at": result["measured_at"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anchors": {"anchor": spec.ANCHOR, "target": spec.TARGET, "unit": spec.UNIT},
        "spec_sha256": _sha256_bytes(_canonical({"ladder": spec.LADDER, "pred": [
            spec.DENOMINATOR_PREDICATES, spec.NUMERATOR_PREDICATES],
            "target": spec.TARGET}).encode()),
        "sql_sha256": _sha256_bytes(_canonical(
            {"recompute": RECOMPUTE_SQL}).encode()),
        "result_sha256": _sha256_bytes(_canonical(result).encode()),
        "files": {},
        "counts": {"denominator_rows": len(raw.get("denominator", [])),
                   "evaluation_rows": len(raw.get("evaluations", []))},
    }
    for key, path in list(paths.items()):
        with open(path, "rb") as fh:
            prov["files"][os.path.basename(path)] = {
                "sha256": _sha256_bytes(fh.read()), "bytes": os.path.getsize(path)}
    for name in ("unreviewed_prs.csv", "per_repo.csv", "daily.csv",
                 "raw_denominator.csv", "raw_evaluations.csv"):
        fp = os.path.join(outdir, name)
        with open(fp, "rb") as fh:
            prov["files"][name] = {"sha256": _sha256_bytes(fh.read()), "bytes": os.path.getsize(fp)}
        paths[name.split(".")[0]] = fp

    p = os.path.join(outdir, "provenance.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(prov, fh, ensure_ascii=False, indent=2, sort_keys=True)
    paths["provenance"] = p
    return paths


# ── 顶层入口 ──────────────────────────────────────────────────────────

def measure(reader: Any, days: int = spec.DEFAULT_WINDOW_DAYS,
            *, git_repo: str | None = None) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """跑一次计量：Reader → (result, raw_rows)。"""
    den = reader.denominator(days)
    mission_ids = sorted({int(r["mission_id"]) for r in den if r.get("mission_id") is not None})
    evals = reader.evaluations(mission_ids)
    supply: list[dict[str, Any]] = []
    if hasattr(reader, "review_supply"):
        since = ""
        if den:
            since = min(_norm_ts(r.get("merged_at")) for r in den if r.get("merged_at"))[:10]
        if since:
            supply = reader.review_supply(since)
    backlog = reader.pending_backlog() if hasattr(reader, "pending_backlog") else {}
    result = aggregate(den, evals, days=days, supply_rows=supply, backlog=backlog)
    if git_repo:
        result["sensitivity_git"] = git_sensitivity(git_repo, days)
    raw = {"denominator": dedupe_denominator(den),
           "evaluations": [dict(r) for r in evals]}
    return result, raw


def measure_with_session(db: Any, days: int = spec.DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """平台侧内联入口（SQLAlchemy Session）—— 失败时 fail-closed 返回 unavailable，不抛断 API。"""
    try:
        result, _raw = measure(SessionReader(db), days)
        return {
            "kpi_key": result["kpi_key"], "anchor": result["anchor"],
            "current": result["current"], "numerator": result["numerator"]["ind_reviewed_prs"],
            "denominator": result["denominator"]["merged_prs"],
            "target": result["target"], "gap": result["gap"],
            "status": "measured", "window_days": days,
        }
    except Exception as exc:  # noqa: BLE001 —— 计量失败不得连坐 KPI 面板
        return {"kpi_key": spec.KPI_KEY, "anchor": spec.ANCHOR, "current": None,
                "target": spec.TARGET, "gap": None, "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}", "window_days": days}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=spec.KPI_TITLE)
    ap.add_argument("--days", type=int, default=spec.DEFAULT_WINDOW_DAYS)
    ap.add_argument("--outdir", default="artifacts/kpi_qc_recheck")
    ap.add_argument("--dsn", default=None, help="默认取平台库（只读）")
    ap.add_argument("--git-repo", default=".", help="分母敏感性第三源（可关闭：--git-repo ''）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    reader = PgReader(args.dsn)
    try:
        result, raw = measure(reader, args.days, git_repo=args.git_repo or None)
    finally:
        reader.close()
    paths = write_artifacts(result, raw, args.outdir)

    if not args.quiet:
        print(json.dumps({
            "kpi": f"{result['current']}%", "numerator": result["numerator"]["ind_reviewed_prs"],
            "denominator": result["denominator"]["merged_prs"], "gap_pp": result["gap"],
            "artifacts": sorted(os.path.basename(p) for p in paths.values()),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())