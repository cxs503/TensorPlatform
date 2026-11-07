#!/usr/bin/env python3
"""coordinator_shadow v1 —— 命令行入口（IN27-H1）。

    python3 -m tools.coordinator_shadow.cli once        --json-out /tmp/beat.json [--dry-run]
    python3 -m tools.coordinator_shadow.cli migrate
    python3 -m tools.coordinator_shadow.cli soak        --hours 72 --json-out runs/in27/soak.json
    python3 -m tools.coordinator_shadow.cli drill       --json-out runs/in27/drill.json
    python3 -m tools.coordinator_shadow.cli acceptance  --dsn ... --hours 72 --json-out acc.json

``once`` / ``migrate`` 走生产库（只读 + 账本写）；``soak`` / ``drill`` 全离线、确定性；
``acceptance`` 读账本与诊断单现状，输出验收 JSON（72h 墙钟未满时只能是
``PENDING_WALL_CLOCK``，不会自己升格成 PASS）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from . import acceptance as acc
from . import daemon as dmn
from . import diagnose as dg
from . import judge as jd
from . import ledger as lg
from . import pulse as pu
from . import soak as sk
from .filing import fetch_filings
from .runtime import LivePulseSource, run_beat_safe


def _dump(payload: Any, path: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        print(f"[written] {p} ({len(text)} bytes)")
    else:
        print(text)


def _judge_with_fallback(judge, pulse, summary):
    """LLM 失败 → 规则层顶上（dry-run 与常驻同一套回退语义）。"""
    try:
        jr = judge.judge(pulse, summary)
    except Exception as exc:
        jr = jd.RuleJudge().judge(pulse, summary)
        jr.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        jr.judge = "rule-fallback"
        return jr, True
    if jr.ok:
        return jr, False
    fb = jd.RuleJudge().judge(pulse, summary)
    fb.error = f"fallback-from:{jr.judge}:{jr.error}"
    fb.judge = "rule-fallback"
    return fb, True


def cmd_once(args) -> int:
    cfg = dmn.DaemonConfig(
        dsn=args.dsn, interval_seconds=args.interval, judge_mode=args.judge,
        dry_run=args.dry_run, once=True, state_dir=args.state_dir, log_path=args.log,
        watch_logs=tuple(args.watch_log) if args.watch_log else dmn.DEFAULT_WATCH_LOGS,
    )
    if args.dry_run:
        # 完全离线安全模式：不写账本、不立单，只把诊断写到 stdout/文件
        runner = pu.ReadOnlyRunner(pu.PgRunner(args.dsn))
        policy = dg.Policy(interval_seconds=args.interval)
        src = LivePulseSource(runner, cfg.watch_logs, policy)
        pulse = src.collect(pu.utc_now())
        findings = dg.rule_findings(pulse, policy)
        judge = dmn.build_judge(args.judge)
        summary = [{"organ": f.organ, "metric": f.metric, "value": f.value, "threshold": f.threshold,
                    "severity": f.severity, "symptom": f.symptom, "zero_flow": f.zero_flow} for f in findings]
        jr, fell_back = _judge_with_fallback(judge, pulse, summary)
        state = lg.RuntimeState.load(Path(args.state_dir) / "state.json")
        diag = dg.build_diagnosis(pulse, policy, llm=jr.verdict, llm_provenance=jr.provenance(),
                                  streaks=state.streaks)
        proposals, suppressed = dg.decide_filings(diag, pulse, policy,
                                                 last_filed_organ=state.last_filed_organ)
        # dry-run 只推进本地的签名连击表（不写库、不立单），让「持久性门槛」在
        # 连跑两拍时可被观察到：第一拍 consecutive=1 不立单，第二拍 consecutive=2 立单。
        state.streaks = dg.streaks_after(state.streaks, diag.findings)
        state.last_beat_ts = pulse.as_of
        state.save(Path(args.state_dir) / "state.json")
        _dump({
            "mode": "dry_run",
            "pulse": pulse.to_dict(),
            "diagnosis": diag.verdict(),
            "would_file": [p.to_dict() for p in proposals],
            "suppressed": suppressed,
            "judge_fallback": fell_back,
            "policy": policy.to_dict(),
        }, args.json_out)
        return 0
    result = dmn.run_once(cfg)
    _dump(result, args.json_out)
    return 0


def cmd_migrate(args) -> int:
    ledger = lg.PgLedger(args.dsn)
    ran = ledger.migrate()
    _dump({"migrated": len(ran), "statements": ran}, args.json_out)
    return 0


def cmd_soak(args) -> int:
    t0 = (datetime.fromisoformat(args.t0.replace("Z", "+00:00")) if args.t0
          else datetime(2026, 11, 6, tzinfo=timezone.utc))
    result = sk.run_soak(hours=args.hours, interval_seconds=args.interval, t0=t0,
                         judge_mode=args.judge_mode)
    iron = sk.iron_law_check(result)
    end = t0 + timedelta(hours=args.hours)
    evaluation = acc.evaluate(
        beats=result.beats, filings=result.filings,
        criteria=acc.Criteria(window_hours=args.hours, interval_seconds=args.interval),
        start=t0, end=end, now=end,
        extra={"source": "synthetic-72h", "judge_mode": args.judge_mode,
               "note": "时钟加速演练：验证时序逻辑自洽，不等于生产 72h 墙钟验收"},
    )
    payload = {"schema": "coordinator-shadow-soak-report/1", "iron_law": iron,
               "acceptance_simulated": evaluation, "soak": result.to_dict()}
    _dump(payload, args.json_out)
    return 0


def cmd_acceptance(args) -> int:
    ledger = lg.PgLedger(args.dsn)
    now = pu.utc_now()
    # ``--since`` 给「从第一拍起算 72h 墙钟」用；默认看「最近 N 小时」。
    start = (datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since
             else now - timedelta(hours=args.hours))
    beats = ledger.read(start=pu.iso(start), end=pu.iso(now))
    runner = pu.ReadOnlyRunner(pu.PgRunner(args.dsn))
    filings = fetch_filings(runner.fetchall)
    log_text = ""
    try:
        log_text = Path(args.log).read_text(errors="replace")
    except OSError:
        log_text = ""
    criteria = acc.Criteria(window_hours=args.hours, interval_seconds=args.interval)
    payload = acc.evaluate(beats=beats, filings=filings, criteria=criteria,
                           start=start, end=now, now=now, log_text=log_text,
                           extra={"source": "live", "dsn": args.dsn or "TP_DSN",
                                  "beats_read": len(beats), "filings_read": len(filings)})
    _dump(payload, args.json_out)
    return 0


def cmd_drill(args) -> int:
    """负控演练：每条「该挡住」的路径都要真挡住，否则 drill 不通过。"""
    from .filing import DryRunIntake
    from .pulse import ReadOnlyViolation, ReadOnlyRunner, SqliteRunner

    checks: dict[str, Any] = {}

    # N1 只读门：写语句必须被拒
    runner = ReadOnlyRunner(SqliteRunner(":memory:"))
    blocked = []
    for sql in ("UPDATE tensor_dev_missions SET status='x'",
                "INSERT INTO coordinator_devissions VALUES (1)",
                "SELECT 1; DELETE FROM tensor_dev_missions",
                "WITH x AS (DELETE FROM tensor_dev_missions RETURNING 1) SELECT * FROM x",
                "EXPLAIN ANALYZE SELECT 1"):
        try:
            runner.fetchall(sql, {})
            blocked.append({"sql": sql, "outcome": "ALLOWED"})
        except ReadOnlyViolation:
            blocked.append({"sql": sql, "outcome": "REJECTED"})
    checks["N1_read_only_guard"] = all(b["outcome"] == "REJECTED" for b in blocked)
    checks["N1_detail"] = blocked

    # N2 健康脉搏 → 0 立单
    t0 = datetime(2026, 11, 6, tzinfo=timezone.utc)
    healthy = sk.synthetic_pulse(t0 + timedelta(minutes=12), t0)  # baseline 相位
    policy = dg.Policy()
    findings = dg.rule_findings(healthy, policy)
    checks["N2_healthy_no_findings"] = len(findings) == 0
    checks["N2_findings"] = [f.metric for f in findings]

    # N3 单拍尖峰 → 持久性门槛挡掉
    spike = sk.synthetic_pulse(t0 + timedelta(hours=60, minutes=1), t0)
    sp_findings = dg.rule_findings(spike, policy)
    diag = dg.build_diagnosis(spike, policy, streaks={})
    proposals, suppressed = dg.decide_filings(diag, spike, policy)
    checks["N3_spike_findings"] = [f.metric for f in sp_findings]
    checks["N3_spike_consecutive"] = [f.consecutive for f in sp_findings]
    checks["N3_filable"] = len(diag.filable(policy))
    checks["N3_single_beat_spike_no_filing"] = (len(proposals) == 0 and len(diag.filable(policy)) == 0
                                                and all(f.consecutive < policy.require_persistence
                                                        for f in sp_findings))

    # N4 坏 JSON → 解析失败 → 回退规则层，仍不编造越界
    bad = jd.ScriptedJudge(["这不是 JSON"])
    jr = bad.judge(healthy, [])
    checks["N4_bad_json_rejected"] = (not jr.ok) and jr.verdict == {}

    # N5 LLM 引用字典外器官 → 丢弃并记 ungrounded
    obj = {"findings": [{"organ": "queue_latency", "metric": "queue_latency_p99",
                         "opinion": "confirm", "severity": "high", "symptom": "幻觉指标"}],
           "rationale": "x", "urgent": True}
    verdict, problems = jd.validate_verdict(obj)
    checks["N5_hallucinated_organ_dropped"] = verdict["findings"] == [] and bool(problems)

    # N6 立单幂等：同一 proposal 二次提交 → duplicate
    intake = DryRunIntake()
    spike2 = sk.synthetic_pulse(t0 + timedelta(hours=12), t0)
    d2 = dg.build_diagnosis(spike2, policy, streaks={"credit_backlog:higher_worse": 1})
    props, _ = dg.decide_filings(d2, spike2, policy)
    ok_dupe = True
    if props:
        first = intake.file(props[0], 1)
        second = intake.file(props[0], 1)
        ok_dupe = first.status == "filed" and second.status == "duplicate"
    checks["N6_filing_idempotent"] = ok_dupe
    checks["N6_proposals"] = [p.task_key for p in props]

    # N7 账本幂等：同 beat_id 写两次只留一行
    led = lg.SqliteLedger(":memory:")
    led.migrate()
    beat = lg.Beat(beat_id="123", ts=pu.iso(t0), model="rule/1", urgent=False,
                   verdict={"findings": []}, pulse_digest={})
    a = led.append(beat)
    b = led.append(beat)
    checks["N7_ledger_idempotent"] = a is True and b is False and len(led.read()) == 1

    verdicts = {k: v for k, v in checks.items() if isinstance(v, bool)}
    checks["all_pass"] = all(verdicts.values())
    _dump({"schema": "coordinator-shadow-drill/1", "checks": checks}, args.json_out)
    return 0 if checks["all_pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="coordinator_shadow", description="影子协调官 v1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, with_accept=False):
        p.add_argument("--dsn", default=None)
        p.add_argument("--interval", type=int, default=1800)
        p.add_argument("--json-out", default=None)

    p = sub.add_parser("once")
    common(p)
    p.add_argument("--dry-run", action="store_true", help="离线安全模式：不写库不立单")
    p.add_argument("--judge", choices=("auto", "llm", "rule"), default="auto")
    p.add_argument("--state-dir", default=dmn.DEFAULT_STATE_DIR)
    p.add_argument("--log", default=dmn.DEFAULT_LOG)
    p.add_argument("--watch-log", action="append", default=None)
    p.set_defaults(func=cmd_once)

    p = sub.add_parser("migrate")
    common(p)
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("soak")
    common(p)
    p.add_argument("--hours", type=float, default=72.0)
    p.add_argument("--t0", default=None)
    p.add_argument("--judge-mode", choices=("scripted", "rule"), default="scripted")
    p.set_defaults(func=cmd_soak)

    p = sub.add_parser("acceptance")
    common(p)
    p.add_argument("--hours", type=float, default=72.0)
    p.add_argument("--since", default=None, help="窗口起点（ISO8601）；缺省=最近 N 小时")
    p.add_argument("--log", default=dmn.DEFAULT_LOG)
    p.set_defaults(func=cmd_acceptance)

    p = sub.add_parser("drill")
    common(p)
    p.set_defaults(func=cmd_drill)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())