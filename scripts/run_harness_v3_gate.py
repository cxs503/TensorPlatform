#!/usr/bin/env python3
"""Harness v3 机器验收门（可红）—— mission m1231 判据的可执行形式。

门禁三条（每条都能真红，见 --scenarios 输出里的 gate_red_capable）:
  ①kill -9 进度 ≥1/3 的席位 → supervisor 拉起 → 重领同单 → 从 journal 恢复 ≥1 已完成步骤；
    对照组（无 journal）必须从零（resumed=0），两组差 = journal 的净贡献。
  ②journal 损坏/缺失 → 降级并如实记录（degraded=True），绝不伪造续跑（resumed 只含磁盘证据
    复核通过的步骤）。
  ③预算帽仍强制：calls_used 跨续跑累计，永不超过下发额度，用尽即 budget_exhausted（不追加）。

用法:
  python3 scripts/run_harness_v3_gate.py                 # 跑全部门禁，PASS 退出 0 / FAIL 退出 1
  python3 scripts/run_harness_v3_gate.py --outdir docs/harness_v3
失败即真实失败（如实写进 gate_report.json），不做任何重试美化。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness_journal as HJ  # noqa: E402

SUP = HERE / "harness_supervisor.py"


def _run_sup(workdir: pathlib.Path, journal_root: pathlib.Path, **kw) -> dict:
    cmd = [sys.executable, str(SUP), "--workdir", str(workdir),
           "--journal-root", str(journal_root)]
    for k, v in kw.items():
        if v is False or v is None:
            continue
        flag = "--" + k.replace("_", "-")
        if v is True:
            cmd.append(flag)
        else:
            cmd += [flag, str(v)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    line = (p.stdout or "").strip().splitlines()
    summary = json.loads(line[-1]) if line else {"error": (p.stderr or "")[-400:]}
    summary["_gate_cmd_rc"] = p.returncode
    return summary


def _fresh(base: pathlib.Path, name: str):
    work = base / name / "work"
    jr = base / name / "journal"
    for d in (work, jr):
        if d.exists():
            shutil.rmtree(d)
    work.mkdir(parents=True, exist_ok=True)
    jr.mkdir(parents=True, exist_ok=True)
    return work, jr


# ───────────────────────── 场景 ─────────────────────────
def sc_crash_resume(base: pathlib.Path) -> dict:
    """①主场景：journal 开，进度 ≥1/3 时 SIGKILL，supervisor 拉起续跑。"""
    work, jr = _fresh(base, "A_crash_resume")
    s = _run_sup(work, jr, mission_id=101, steps=6, step_seconds=0.08,
                 budget_calls=100, max_attempts=3, kill_at_progress=0.34)
    events = s.get("events") or []
    killed_at = [e.get("killed_at_progress") for e in events if e.get("killed_at_progress")]
    first_attempt_journal = (((events[0].get("journal") or {}).get("progress"))
                             if events else 0) or 0
    checks = {
        "kill9_fired": s.get("kills", 0) >= 1,
        "killed_at_ge_one_third": bool(killed_at) and killed_at[0] >= 1 / 3,
        "supervisor_relaunched": s.get("attempts", 0) >= 2,
        "resumed_at_least_one_step": s.get("resumed_step_count", 0) >= 1,
        "all_steps_done": s.get("done_step_count", 0) == 6,
        "journal_clean": (s.get("journal_corrupt_records", 1) == 0
                          and not s.get("journal_degraded", True)),
        "resume_brief_injected": _log_has(work / "seat_log.jsonl", "resume_brief_injected"),
    }
    return {"scenario": "A_crash_resume", "checks": checks, "pass": all(checks.values()),
            "observed": {"kills": s.get("kills"), "attempts": s.get("attempts"),
                         "killed_at_progress": killed_at, "resumed": s.get("resumed_steps"),
                         "resumed_step_count": s.get("resumed_step_count"),
                         "done": s.get("done_step_count"), "status": s.get("status"),
                         "journal_valid_records": s.get("journal_valid_records"),
                         "calls_used": s.get("calls_used")},
            "summary": s}


def sc_control_no_journal(base: pathlib.Path) -> dict:
    """对照组：同样 kill -9，但不用 journal → 只能从零（resumed=0）。"""
    work, jr = _fresh(base, "B_control_no_journal")
    s = _run_sup(work, jr, mission_id=102, steps=6, step_seconds=0.08,
                 budget_calls=100, max_attempts=3, journal_off=True,
                 kill_after_seconds=0.25)
    checks = {
        "kill9_fired": s.get("kills", 0) >= 1,
        "no_resume_possible": s.get("resumed_step_count", 1) == 0,
        "restarted_from_zero": s.get("calls_used", 0) >= 6,
        "rebuilt_all_evidence": s.get("done_step_count", 0) == 6,
    }
    return {"scenario": "B_control_no_journal", "checks": checks, "pass": all(checks.values()),
            "observed": {"kills": s.get("kills"), "attempts": s.get("attempts"),
                         "resumed_step_count": s.get("resumed_step_count"),
                         "calls_used": s.get("calls_used"),
                         "done": s.get("done_step_count"), "status": s.get("status")},
            "summary": s}


def sc_corrupt_total(base: pathlib.Path) -> dict:
    """②a 全损：journal 变垃圾 → 必须降级从零且如实记录，不得伪造续跑。"""
    work, jr = _fresh(base, "C1_corrupt_total")
    _run_sup(work, jr, mission_id=103, steps=6, step_seconds=0.05,
             budget_calls=100, max_attempts=1, kill_at_progress=0.5)
    jp = HJ.journal_path(jr, 103)
    jp.write_bytes(os.urandom(512))                      # 真实损坏：随机字节
    s = _run_sup(work, jr, mission_id=103, steps=6, step_seconds=0.05,
                 budget_calls=100, max_attempts=1, force_resume=True)
    brief = _last_brief(work / "seat_log.jsonl")
    checks = {
        "degraded_flagged": bool(s.get("journal_degraded")),
        "no_fabricated_resume": s.get("resumed_step_count", 1) == 0,
        "degradation_recorded_in_brief": bool(brief) and brief.get("degraded") is True,
        "work_still_completed": s.get("done_step_count", 0) == 6,
    }
    return {"scenario": "C1_corrupt_total", "checks": checks, "pass": all(checks.values()),
            "observed": {"degraded": s.get("journal_degraded"),
                         "reason": s.get("journal_reason"),
                         "resumed_step_count": s.get("resumed_step_count"),
                         "corrupt_records": s.get("journal_corrupt_records"),
                         "done": s.get("done_step_count")},
            "summary": s}


def sc_corrupt_partial(base: pathlib.Path) -> dict:
    """②b 部分损坏：尾部半行截断 → 只用通过校验的前缀，绝不把坏行当证据。"""
    work, jr = _fresh(base, "C2_corrupt_partial")
    _run_sup(work, jr, mission_id=104, steps=6, step_seconds=0.05,
             budget_calls=100, max_attempts=1, kill_at_progress=0.5)
    jp = HJ.journal_path(jr, 104)
    raw = jp.read_text().splitlines(keepends=True)
    # 追加一条"半行"（模拟 kill -9 落在 write 中间）+ 一条语法坏行
    with open(jp, "a", encoding="utf-8") as f:
        f.write('{"schema":"harness.journal.v3","seq":999,"kind":"checkpoi')
        f.write("\n")
        f.write("@@garbage@@\n")
    st_before = HJ.read(jr, 104)
    s = _run_sup(work, jr, mission_id=104, steps=6, step_seconds=0.05,
                 budget_calls=100, max_attempts=1, force_resume=True)
    brief = _last_brief(work / "seat_log.jsonl")
    checks = {
        "corruption_detected": len(st_before["corrupt"]) >= 2,
        "valid_prefix_survives": len(st_before["entries"]) >= 1,
        "degraded_flagged": bool(s.get("journal_degraded")),
        "resume_uses_only_verified": (s.get("resumed_step_count", 0) >= 1
                                      and set(s.get("resumed_steps") or [])
                                      <= {f"s{i}" for i in range(1, 7)}),
        "work_still_completed": s.get("done_step_count", 0) == 6,
        "degradation_in_brief": bool(brief) and brief.get("degraded") is True,
    }
    return {"scenario": "C2_corrupt_partial", "checks": checks, "pass": all(checks.values()),
            "observed": {"corrupt_before": len(st_before["corrupt"]),
                         "valid_before": len(st_before["entries"]),
                         "resumed": s.get("resumed_steps"),
                         "degraded": s.get("journal_degraded"),
                         "corrupt_after": s.get("journal_corrupt_records"),
                         "done": s.get("done_step_count")},
            "summary": s}


def sc_budget_cap(base: pathlib.Path) -> dict:
    """③预算帽：续跑不追加预算，calls_used 永不超过下发额度。"""
    work, jr = _fresh(base, "D_budget_cap")
    s = _run_sup(work, jr, mission_id=105, steps=6, step_seconds=0.05,
                 budget_calls=4, max_attempts=3, kill_at_progress=0.34)
    checks = {
        "killed_and_resumed": (s.get("kills", 0) >= 1 and s.get("resumed_step_count", 0) >= 1),
        "never_exceeds_cap": s.get("calls_used", 99) <= 4,
        "stopped_honestly": s.get("status") == "budget_exhausted",
        "no_budget_extension": (s.get("budget_calls") == 4
                                and not any((g.get("journal") or {}).get("calls_used", 0) > 4
                                            for g in (s.get("events") or []))),
        "no_third_attempt_after_cap": s.get("attempts", 9) <= 2,
    }
    return {"scenario": "D_budget_cap", "checks": checks, "pass": all(checks.values()),
            "observed": {"kills": s.get("kills"), "attempts": s.get("attempts"),
                         "resumed_step_count": s.get("resumed_step_count"),
                         "calls_used": s.get("calls_used"), "budget": s.get("budget_calls"),
                         "status": s.get("status"), "done": s.get("done_step_count")},
            "summary": s}


def sc_red_capability(base: pathlib.Path) -> dict:
    """门禁可红性证明：把 journal 关掉再去要"续跑 ≥1 步"，判据必须判红。"""
    work, jr = _fresh(base, "E_red_capability")
    s = _run_sup(work, jr, mission_id=106, steps=6, step_seconds=0.05,
                 budget_calls=100, max_attempts=2, journal_off=True,
                 kill_after_seconds=0.2, resume_probe=True)
    would_pass = s.get("resumed_step_count", 0) >= 1     # 门禁 ① 的判据式
    checks = {"gate_turns_red_without_journal": would_pass is False}
    return {"scenario": "E_red_capability", "checks": checks, "pass": all(checks.values()),
            "observed": {"resumed_step_count": s.get("resumed_step_count"),
                         "gate_verdict_if_evaluated": "PASS" if would_pass else "RED",
                         "journal_on": False},
            "summary": s}


def _log_has(path: pathlib.Path, event: str) -> bool:
    if not path.exists():
        return False
    for ln in path.read_text().splitlines():
        try:
            if json.loads(ln).get("event") == event:
                return True
        except Exception:
            continue
    return False


def _last_brief(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    last = {}
    for ln in path.read_text().splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("event") == "resume_brief_injected":
            last = d
    return last


# ───────────────────────── 报告 ─────────────────────────
def emit_markdown(rep: dict, out: pathlib.Path):
    L = ["# Harness v3 门禁证据（程序化生成，数字零手抄）", "",
         f"- 生成时间: {rep['generated_at']}",
         f"- 总判定: **{rep['verdict']}**（场景 {rep['scenario_pass']}/{rep['scenario_total']} 通过）",
         f"- 门禁可红性: {'已验证（去掉 journal 后判据变红）' if rep['gate_red_capable'] else '未验证'}",
         f"- 运行时环境: python {rep['python']}", "",
         "| 场景 | 判定 | 失败判据 | 关键观测 |", "|---|---|---|---|"]
    for sc in rep["scenarios"]:
        bad = [k for k, v in sc["checks"].items() if not v]
        L.append(f"| `{sc['scenario']}` | {'PASS' if sc['pass'] else 'FAIL'} | "
                 f"{', '.join(bad) or '—'} | {json.dumps(sc['observed'], ensure_ascii=False)} |")
    L += ["", "## 判据明细", ""]
    for sc in rep["scenarios"]:
        L.append(f"### {sc['scenario']} — {'PASS' if sc['pass'] else 'FAIL'}")
        for k, v in sc["checks"].items():
            L.append(f"- [{'x' if v else ' '}] `{k}`")
        L.append("")
    L += ["## 与 m1121 事故根因对照", "",
          "| 事故链环节 | m1121 事故表现 | Harness v3 对应控制 | 证据 |", "|---|---|---|---|",
          "| 进度只在内存 | 15 分钟工作随 stop 清空 | 每检查点 append-only journal | 场景 A："
          f"续跑 {rep['scenarios'][0]['observed'].get('resumed_step_count')} 步 |",
          "| 重领后从零 | 重读代码、重复探索 | claim 注入续跑简报 | 场景 A `resume_brief_injected` |",
          "| 无日志时无法判断进度 | 只能靠模型自述 | 无日志=显式降级记录，不猜测 | 场景 B/C1 |",
          "| 日志不完整时编造进度 | 无机制（易吹牛） | 逐步证据复核，缺证据即重做 | 场景 C1/C2 |",
          "| 续跑变成无限预算 | 无（此前不续跑） | calls_used 跨续跑累计、用尽即停 | 场景 D |",
          ""]
    out.write_text("\n".join(L) + "\n")


def emit_figure(rep: dict, png: pathlib.Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    names = [s["scenario"].split("_")[0] for s in rep["scenarios"]]
    resumed = [s["observed"].get("resumed_step_count", 0) for s in rep["scenarios"]]
    calls = [s["observed"].get("calls_used", 0) for s in rep["scenarios"]]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    ax = axes[0]
    bars = ax.bar(names, resumed, color=["#2b7bba", "#b0b0b0", "#c0504d", "#e0a03c", "#7f7f7f"])
    ax.set_title("resumed steps after kill -9 (>=1 required for A)")
    ax.axhline(1, ls="--", c="k", lw=0.8)
    for b, v in zip(bars, resumed):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, str(v), ha="center", fontsize=9)
    ax = axes[1]
    ax.bar(names, calls, color="#4f81bd")
    ax.axhline(4, ls="--", c="r", lw=0.9, label="budget cap (scenario D)")
    ax.set_title("tool calls consumed (cross-resume cumulative)")
    ax.legend(fontsize=8)
    fig.suptitle(f"Harness v3 acceptance gate: {rep['verdict']} "
                 f"({rep['scenario_pass']}/{rep['scenario_total']})", fontsize=10)
    fig.tight_layout()
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=140)
    plt.close(fig)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="docs/harness_v3")
    ap.add_argument("--base", default="")
    a = ap.parse_args(argv)
    outdir = pathlib.Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    base = pathlib.Path(a.base) if a.base else outdir / "_work"
    base.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    scenarios = [sc_crash_resume(base), sc_control_no_journal(base),
                 sc_corrupt_total(base), sc_corrupt_partial(base),
                 sc_budget_cap(base), sc_red_capability(base)]
    passed = sum(1 for s in scenarios if s["pass"])
    rep = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "elapsed_seconds": round(time.time() - t0, 2),
        "verdict": "PASS" if passed == len(scenarios) else "FAIL",
        "scenario_pass": passed, "scenario_total": len(scenarios),
        "gate_red_capable": bool(scenarios[-1]["pass"]),
        "scenarios": scenarios,
        "criteria": {
            "C1": "kill -9 进度 >=1/3 后续跑 >=1 步（对照无日志=0）",
            "C2": "journal 损坏/缺失 -> degraded 记录，不伪造续跑",
            "C3": "预算帽跨续跑累计，永不超过下发额度",
            "C4": "门禁本身可红（去掉 journal 判据必须变红）",
        },
    }
    (outdir / "gate_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    emit_markdown(rep, outdir / "gate_evidence.md")
    fig_ok = emit_figure(rep, outdir / "figs" / "harness_v3_gate.png")
    print(json.dumps({"verdict": rep["verdict"], "scenario_pass": rep["scenario_pass"],
                      "scenario_total": rep["scenario_total"],
                      "gate_red_capable": rep["gate_red_capable"],
                      "figure": fig_ok,
                      "report": str(outdir / "gate_report.json")},
                     ensure_ascii=False))
    for s in scenarios:
        bad = [k for k, v in s["checks"].items() if not v]
        print(f"  {'PASS' if s['pass'] else 'FAIL'} {s['scenario']}"
              f"{' 失败判据: ' + ','.join(bad) if bad else ''}")
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())