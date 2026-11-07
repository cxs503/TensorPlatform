"""``python3 -m tools.evaluation_ops.cli`` —— cohort 成熟化链的命令行入口。

子命令
------
- ``capture``   读库 → 逐 cohort 指标 + 视图无关性 + SLO + 稳健性 + SQL 交叉验证 → 产物 JSON
                （``--write`` 时写 ``eval_cohorts``；写库只加行，不动调度面）
- ``stability`` 同一库连跑 N 个 capture cycle，冻结 cohort 必须零漂移
- ``replay``    in-flight 增量评价连跑 N 轮（pending 下降幅度可机器裁），前后 cohort/SLO 对照
- ``export``    线上事实 → SQLite 快照（外部效度与可重放用的第二条独立链路）
- ``verify``    线上直读 vs 快照复算，逐 cohort 比对

所有产物都是 JSON（数字程序化注入，不手抄），图由 ``tools/evaluation_ops/plot.py`` 读同一
份 JSON 生成。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, text  # noqa: E402

from tools.evaluation_ops import cohorts as ch  # noqa: E402
from tools.evaluation_ops import inflight as fl  # noqa: E402
from tools.evaluation_ops import robustness as rb  # noqa: E402
from tools.evaluation_ops import slo as sl  # noqa: E402
from tools.evaluation_ops import views as vw  # noqa: E402


# ─────────────────────────── 小工具 ───────────────────────────

def _dump(payload: Mapping[str, Any], out: str | pathlib.Path | None) -> pathlib.Path | None:
    if not out:
        return None
    path = pathlib.Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _as_of(value: str | None) -> str:
    return ch.isots(value) or ch.now_utc()


def _load(conn, repo: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missions = ch.fetch_missions(conn, repo=repo)
    records = ch.fetch_records(conn, repo=repo)
    return missions, records


def summarize(cohorts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """全局汇总（只做加总，不做加权/排名）。"""
    total = sum(int(r.get("missions_total") or 0) for r in cohorts.values())
    decided = sum(int(r.get("missions_decided") or 0) for r in cohorts.values())
    pending_rows = sum(int(r.get("pending_rows") or 0) for r in cohorts.values())
    p90s = [r["p90_latency_hours"] for r in cohorts.values() if r.get("p90_latency_hours") is not None]
    return {
        "cohorts": len(cohorts),
        "missions_total": total,
        "missions_decided": decided,
        "missions_unrecorded": sum(int(r.get("missions_unrecorded") or 0) for r in cohorts.values()),
        "coverage": (decided / total) if total else None,
        "pending_rows": pending_rows,
        "cohort_p90_latency_hours_max": max(p90s) if p90s else None,
        "undefined_cohorts": sorted(k for k, r in cohorts.items() if r.get("undefined_metrics")),
    }


# ─────────────────────────── capture ───────────────────────────

def capture(*, dsn: str, repo: str | None, as_of: str,
            write: bool = False, freeze: bool = True, trials: int = 5) -> dict[str, Any]:
    engine = create_engine(dsn)
    with engine.connect() as conn:
        missions, records = _load(conn, repo)
        cohorts = ch.compute_cohorts(missions, records, as_of=as_of, repo=repo)
        cross = ch.sql_crosscheck(conn, cohorts)
        write_result: dict[str, Any] = {"performed": False}
        stored: dict[str, Any] = {}
        if write:
            ch.ensure_table(conn)
            write_result = ch.upsert_cohorts(conn, cohorts.values(), as_of=as_of, freeze=freeze)
            write_result["performed"] = True
            conn.commit()
            stored = ch.load_stored(conn)

    invariants = rb.score_invariants(missions, records, as_of=as_of, trials=trials)
    window = vw.cohort_vs_legacy(cohorts, missions, records, as_of=as_of)
    # 压力视图：把回看压到 2 天，让较早的 cohort 真的被筛出视图（证明筛选不影响余下 cohort 的数）
    window_stress = vw.window_independence(cohorts, as_of=as_of, days=(2, 7, 30, 90))
    slo = sl.slo_report(missions, records, as_of=as_of)

    stored_drift: list[dict[str, Any]] = []
    for key, row in cohorts.items():
        prev = stored.get(key)
        if not prev:
            continue
        d = ch.diff_metrics(prev, row)
        if d:
            stored_drift.append({"cohort_key": key, "fields": d})

    payload = {
        "schema": "eval-ops-capture/1",
        "generated_at": ch.now_utc(),
        "as_of": as_of,
        "repo": repo,
        "totals": summarize(cohorts),
        "cohorts": cohorts,
        "window_independence": window,
        "window_independence_stress": window_stress,
        "slo": slo,
        "sql_crosscheck": cross,
        "invariants": invariants,
        "write": {**write_result, "stored_drift": stored_drift,
                  "stored_rows": len(stored)},
        "thresholds": dict(sl.DEFAULT_SLO),
        "verdict": {
            "window_invariant": window["cohort_invariant"]["identical"],
            "legacy_window_dependent": window["legacy_window_dependent"]["window_dependent"],
            "sql_crosscheck_ok": cross.get("identical") if cross.get("supported") else "not_supported",
            "invariants_ok": invariants["ok"],
            "slo_met_defined_only": slo["primary_slo"]["slo_met_defined_only"],
            "stored_drift_zero": not stored_drift,
        },
    }
    return payload


# ─────────────────────────── stability ───────────────────────────

def stability(*, dsn: str, repo: str | None, as_of: str, cycles: int = 5) -> dict[str, Any]:
    """连跑 N 个 capture cycle；冻结 cohort 的指标序列必须完全不变。"""
    engine = create_engine(dsn)
    series: dict[str, list[dict[str, Any]]] = {}
    cycle_meta: list[dict[str, Any]] = []
    with engine.connect() as conn:
        ch.ensure_table(conn)
        for i in range(1, int(cycles) + 1):
            missions, records = _load(conn, repo)
            cohorts = ch.compute_cohorts(missions, records, as_of=as_of, repo=repo)
            result = ch.upsert_cohorts(conn, cohorts.values(), as_of=as_of, freeze=True)
            conn.commit()
            stored = ch.load_stored(conn)
            cycle_meta.append({
                "cycle": i,
                "written": len(result["written"]),
                "late_arrivals": len(result["late_arrivals"]),
                "drift": len(result["drift"]),
            })
            for key, row in stored.items():
                if not row.get("frozen"):
                    continue
                series.setdefault(key, []).append({f: row.get(f) for f in ch.METRIC_FIELDS})

    drifted: list[dict[str, Any]] = []
    for key, rows in series.items():
        if any(r != rows[0] for r in rows[1:]):
            drifted.append({"cohort_key": key, "series": rows})
    return {
        "schema": "eval-ops-stability/1",
        "as_of": as_of,
        "repo": repo,
        "cycles": int(cycles),
        "frozen_cohorts": len(series),
        "cycle_meta": cycle_meta,
        "drifted_cohorts": drifted,
        "zero_drift": not drifted,
        "note": ("冻结 cohort（ISO 周已结束）在连续 cycle 中指标序列必须逐字段恒定；"
                 "若出现差异即评分器漂移，或输入指纹已变（迟到评价，另行记 revision）。"),
    }


# ─────────────────────────── replay（in-flight 轮次） ───────────────────────────

def replay(*, dsn: str, repo: str | None, as_of: str, cycles: int = 3,
           limit: int = 50, dry_run: bool = False) -> dict[str, Any]:
    engine = create_engine(dsn)
    with engine.connect() as conn:
        ch.ensure_table(conn)
        missions0, records0 = _load(conn, repo)
        cohorts_before = ch.compute_cohorts(missions0, records0, as_of=as_of, repo=repo)
        cycles_result = fl.run_cycles(conn, cycles=cycles, repo=repo, limit=limit,
                                      dry_run=dry_run, commit=True)
        missions1, records1 = _load(conn, repo)
        cohorts_after = ch.compute_cohorts(missions1, records1, as_of=as_of, repo=repo)
        window = vw.window_independence(cohorts_after, as_of=as_of)
        slo_after = sl.slo_report(missions1, records1, as_of=as_of)

    tot_before = summarize(cohorts_before)
    tot_after = summarize(cohorts_after)
    return {
        "schema": "eval-ops-replay/1",
        "generated_at": ch.now_utc(),
        "as_of": as_of,
        "repo": repo,
        "dry_run": bool(dry_run),
        "cycles": cycles_result,
        "totals_before": tot_before,
        "totals_after": tot_after,
        "coverage_gain": (
            None if tot_before["coverage"] is None or tot_after["coverage"] is None
            else round(tot_after["coverage"] - tot_before["coverage"], 6)
        ),
        "window_independence_after": window,
        "slo_after": slo_after,
        "verdict": {
            "pending_drop_ge_50pct": (
                cycles_result["pending_drop_share"] is not None
                and cycles_result["pending_drop_share"] >= 0.5
            ),
            "dispatcher_untouched": cycles_result["dispatcher_untouched"],
            "window_invariant_after": window["identical"],
            "p90_computable": slo_after["primary_slo"]["states"].get("latency_p90") != "undefined",
        },
    }


# ─────────────────────────── export（第二条独立链路） ───────────────────────────

SQLITE_DDL = f"""
CREATE TABLE IF NOT EXISTS {fl.TAB_MISSIONS} (
    id INTEGER PRIMARY KEY,
    repo TEXT,
    organization_id INTEGER,
    claimed_by TEXT,
    status TEXT,
    finished_at TEXT,
    report_id INTEGER,
    result TEXT
);
CREATE TABLE IF NOT EXISTS {fl.TAB_REPORTS} (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER,
    title TEXT,
    markdown TEXT,
    figures TEXT
);
CREATE TABLE IF NOT EXISTS {fl.TAB_DELTAS} (
    mission_id INTEGER,
    category TEXT,
    summary TEXT,
    reason TEXT,
    artifacts TEXT,
    evidence TEXT,
    reviewer TEXT
);
"""
#: 快照里报告正文截断长度（只影响理由文本的字数展示，不影响 S/E 等级判定）
REPORT_TEXT_CAP = 4000


def export(*, dsn: str, out_sqlite: str, repo: str | None = None) -> dict[str, Any]:
    src = create_engine(dsn)
    dst_path = pathlib.Path(out_sqlite)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    dst = create_engine(f"sqlite+pysqlite:///{dst_path}")

    with src.connect() as sconn:
        has_reports = fl.has_table(sconn, fl.TAB_REPORTS)
        has_deltas = fl.has_table(sconn, fl.TAB_DELTAS)
        where = "WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL"
        params: dict[str, Any] = {}
        if repo:
            where += " AND m.repo = :repo"
            params["repo"] = repo
        missions = [dict(r) for r in sconn.execute(text(
            "SELECT m.id, m.repo, m.organization_id, m.claimed_by, m.status, "
            "       CAST(m.finished_at AS TEXT) AS finished_at, m.report_id, "
            "       CAST(m.result AS TEXT) AS result "
            f"FROM {fl.TAB_MISSIONS} m {where} ORDER BY m.id"
            if not str(getattr(sconn.dialect, "name", "")).startswith("sqlite") else
            "SELECT m.id, m.repo, m.organization_id, m.claimed_by, m.status, "
            "       m.finished_at, m.report_id, m.result "
            f"FROM {fl.TAB_MISSIONS} m {where} ORDER BY m.id"
        ), params).mappings().all()]
        evals = [dict(r) for r in sconn.execute(text(
            f"SELECT {', '.join(oe_columns())} FROM {fl.TAB_EVALS}"
        ), {}).mappings().all()] if fl.has_table(sconn, fl.TAB_EVALS) else []
        reports = []
        if has_reports:
            reports = [dict(r) for r in sconn.execute(text(
                "SELECT id, mission_id, title, substr(markdown, 1, :cap) AS markdown, "
                "       CAST(figures AS TEXT) AS figures "
                f"FROM {fl.TAB_REPORTS} WHERE mission_id IS NOT NULL"
            ), {"cap": REPORT_TEXT_CAP}).mappings().all()]
        deltas = []
        if has_deltas:
            deltas = [dict(r) for r in sconn.execute(text(
                "SELECT mission_id, category, summary, reason, "
                "       CAST(artifacts AS TEXT) AS artifacts, CAST(evidence AS TEXT) AS evidence, reviewer "
                f"FROM {fl.TAB_DELTAS}"
            ), {}).mappings().all()]

    with dst.begin() as dconn:
        for stmt in [s.strip() for s in (SQLITE_DDL + ch.DDL_SQLITE + oe_ddl()).split(";") if s.strip()]:
            dconn.execute(text(stmt))
        if missions:
            dconn.execute(text(
                f"INSERT INTO {fl.TAB_MISSIONS} (id, repo, organization_id, claimed_by, status, "
                "finished_at, report_id, result) VALUES (:id, :repo, :organization_id, :claimed_by, "
                ":status, :finished_at, :report_id, :result)"
            ), missions)
        if evals:
            cols = ", ".join(oe_columns())
            vals = ", ".join(f":{c}" for c in oe_columns())
            dconn.execute(text(f"INSERT INTO {fl.TAB_EVALS} ({cols}) VALUES ({vals})"), evals)
        if reports:
            dconn.execute(text(
                f"INSERT INTO {fl.TAB_REPORTS} (id, mission_id, title, markdown, figures) "
                "VALUES (:id, :mission_id, :title, :markdown, :figures)"
            ), reports)
        if deltas:
            dconn.execute(text(
                f"INSERT INTO {fl.TAB_DELTAS} (mission_id, category, summary, reason, artifacts, "
                "evidence, reviewer) VALUES (:mission_id, :category, :summary, :reason, :artifacts, "
                ":evidence, :reviewer)"
            ), deltas)

    return {
        "schema": "eval-ops-export/1",
        "generated_at": ch.now_utc(),
        "source": dsn.split("@")[-1],
        "sqlite": str(dst_path),
        "repo": repo,
        "rows": {"missions": len(missions), "evaluations": len(evals),
                 "reports": len(reports), "deltas": len(deltas)},
        "caps": {"report_text_chars": REPORT_TEXT_CAP},
    }


def oe_columns() -> list[str]:
    from tools import outcome_evaluations as oe

    return list(oe.COLUMNS)


def oe_ddl() -> str:
    from tools import outcome_evaluations as oe

    return oe.DDL


# ─────────────────────────── verify（外部效度） ───────────────────────────

def verify(*, live_dsn: str, snapshot_dsn: str, repo: str | None, as_of: str) -> dict[str, Any]:
    live_engine = create_engine(live_dsn)
    snap_engine = create_engine(snapshot_dsn)
    with live_engine.connect() as lconn:
        lm, lr = _load(lconn, repo)
        live = ch.compute_cohorts(lm, lr, as_of=as_of, repo=repo)
        cross = ch.sql_crosscheck(lconn, live)
    with snap_engine.connect() as sconn:
        sm, sr = _load(sconn, repo)
        snap = ch.compute_cohorts(sm, sr, as_of=as_of, repo=repo)
    ev = rb.external_validity(live, snap, name=snapshot_dsn.split("/")[-1])
    window = vw.window_independence(live, as_of=as_of)
    return {
        "schema": "eval-ops-verify/1",
        "generated_at": ch.now_utc(),
        "as_of": as_of,
        "repo": repo,
        "totals_live": summarize(live),
        "totals_snapshot": summarize(snap),
        "external_validity": ev,
        "window_independence": window,
        "window_independence_stress": window_stress,
        "sql_crosscheck": cross,
        "verdict": {
            "external_validity_ok": ev["identical"],
            "window_invariant": window["identical"],
            "sql_crosscheck_ok": cross.get("identical") if cross.get("supported") else "not_supported",
        },
    }


# ─────────────────────────── CLI ───────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tools.evaluation_ops.cli", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="逐 cohort 指标 + 视图无关性 + SLO + 稳健性")
    c.add_argument("--dsn", required=True)
    c.add_argument("--repo", default=None)
    c.add_argument("--as-of", default=None)
    c.add_argument("--out", default=None)
    c.add_argument("--write", action="store_true", help="写 eval_cohorts（只加行）")
    c.add_argument("--no-freeze", action="store_true")
    c.add_argument("--trials", type=int, default=5)

    s = sub.add_parser("stability", help="连跑 N 个 cycle，冻结 cohort 零漂移")
    s.add_argument("--dsn", required=True)
    s.add_argument("--repo", default=None)
    s.add_argument("--as-of", default=None)
    s.add_argument("--cycles", type=int, default=5)
    s.add_argument("--out", default=None)

    r = sub.add_parser("replay", help="in-flight 增量评价连跑 N 轮")
    r.add_argument("--dsn", required=True)
    r.add_argument("--repo", default=None)
    r.add_argument("--as-of", default=None)
    r.add_argument("--cycles", type=int, default=3)
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--out", default=None)

    e = sub.add_parser("export", help="线上事实 → SQLite 快照")
    e.add_argument("--dsn", required=True)
    e.add_argument("--out-sqlite", required=True)
    e.add_argument("--repo", default=None)
    e.add_argument("--out", default=None)

    v = sub.add_parser("verify", help="线上直读 vs 快照复算（外部效度）")
    v.add_argument("--live-dsn", required=True)
    v.add_argument("--snapshot-dsn", required=True)
    v.add_argument("--repo", default=None)
    v.add_argument("--as-of", default=None)
    v.add_argument("--out", default=None)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = _as_of(getattr(args, "as_of", None))

    if args.cmd == "capture":
        payload = capture(dsn=args.dsn, repo=args.repo, as_of=as_of,
                          write=args.write, freeze=not args.no_freeze, trials=args.trials)
    elif args.cmd == "stability":
        payload = stability(dsn=args.dsn, repo=args.repo, as_of=as_of, cycles=args.cycles)
    elif args.cmd == "replay":
        payload = replay(dsn=args.dsn, repo=args.repo, as_of=as_of, cycles=args.cycles,
                         limit=args.limit, dry_run=args.dry_run)
    elif args.cmd == "export":
        payload = export(dsn=args.dsn, out_sqlite=args.out_sqlite, repo=args.repo)
    elif args.cmd == "verify":
        payload = verify(live_dsn=args.live_dsn, snapshot_dsn=args.snapshot_dsn,
                         repo=args.repo, as_of=as_of)
    else:  # pragma: no cover
        raise SystemExit(f"unknown cmd {args.cmd}")

    path = _dump(payload, getattr(args, "out", None))
    print(json.dumps({"cmd": args.cmd, "out": str(path) if path else None,
                      "verdict": payload.get("verdict"),
                      "totals": payload.get("totals") or payload.get("totals_after")},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())