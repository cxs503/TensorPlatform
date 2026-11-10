#!/usr/bin/env python3
"""``tools/hygiene`` 的命令行入口：一处产出「可复算」的卫生证据。

    # 1) 跨求解器 checkpoint/restart 一致性（AC1–AC4）
    python3 tools/hygiene/cli.py restart --out artifacts/p223w2_n09_hygiene --n-micro 1000

    # 2) 交付面四分台账（AC5/AC6）
    python3 tools/hygiene/cli.py delivery --repo . --out artifacts/p223w2_n09_hygiene \
        --pattern 'agent/*' --sample 30 --baseline artifacts/p223w2_n09_hygiene/baseline.json

    # 3) 只减不增门（拿基线卡当前状态；基线缺失 ⇒ fail-closed 判红）
    python3 tools/hygiene/cli.py gate --repo . --baseline <baseline.json> --out <dir>

设计纪律：所有证据落成 JSON + Markdown，数字程序化注入（报告里手抄数字等于不可审计）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_ROOT = os.path.dirname(_HERE)
if _TOOLS_ROOT not in sys.path:
    sys.path.insert(0, _TOOLS_ROOT)

from hygiene import coupling_restart as CR  # noqa: E402
from hygiene import delivery_surface as DS  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: str, obj: Any) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── 图 ──────────────────────────────────────────────────────────────────────
def figure_energy(evidence: Dict[str, Any], out_dir: str) -> List[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for key, block in sorted(evidence["scenarios"].items()):
        curve = block["compression"]["energy_curve"]
        xs = [p["micro_step"] for p in curve]
        ys = [p["e_rel"] for p in curve]
        ax.plot(xs, ys, lw=1.2, label=f"{key} (band={max(abs(v) for v in ys):.2e})")
    ax.axhline(1e-3, ls="--", lw=1.0, color="crimson", label="0.1% drift threshold")
    ax.axhline(-1e-3, ls="--", lw=1.0, color="crimson")
    ax.set_xlabel("micro-step")
    ax.set_ylabel(r"$E_{\rm total}(t)/E_{\rm total}(0)-1$")
    ax.set_title("Coupled-state energy ledger: bounded oscillation, no drift")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(out_dir, "fig_energy_ledger.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return [p]


def figure_branches(ledger: Dict[str, Any], out_dir: str,
                    baseline: Optional[Dict[str, Any]] = None) -> List[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    counts = ledger["counts"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.2))
    labels = ["A\ncovered", "B\nsuperset", "C\nundelivered", "D\norphan"]
    vals = [counts.get("A", 0), counts.get("B", 0), counts.get("C", 0), counts.get("D", 0)]
    colors = ["#3b7dd8", "#8e9aa8", "#d1495b", "#e8a33d"]
    ax1.bar(labels, vals, color=colors)
    for i, v in enumerate(vals):
        ax1.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax1.set_ylabel("branches")
    ax1.set_title(f"{os.path.basename(ledger['repo'])}: {ledger['total']} branches (pattern {ledger['pattern']})")
    ax1.grid(axis="y", alpha=0.3)

    if baseline:
        cur = ledger["unmerged_count"]
        base = int(baseline.get("unmerged_count") or 0)
        ax2.bar(["baseline", "current"], [base, cur], color=["#8e9aa8", "#3b7dd8"])
        for i, v in enumerate([base, cur]):
            ax2.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
        ax2.set_title("unmerged (B∪C∪D) vs baseline: only-decrease gate")
        ax2.grid(axis="y", alpha=0.3)
    else:
        ax2.axis("off")
    p = os.path.join(out_dir, "fig_branch_classes.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return [p]


# ── 子命令 ──────────────────────────────────────────────────────────────────
def cmd_restart(args: argparse.Namespace) -> int:
    ev = CR.build_evidence(n_micro=args.n_micro, codec=args.codec)
    ev["generated_at"] = _now()
    _write_json(os.path.join(args.out, "restart_evidence.json"), ev)
    figs = figure_energy(ev, args.out) if args.figures else []
    summary = {
        "generated_at": ev["generated_at"],
        "n_micro": ev["n_micro"],
        "codec": ev["codec"],
        "ac": ev["ac"],
        "totals": ev["totals"],
        "figures": [os.path.basename(f) for f in figs],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(ev["ac"].values()) else 1


def _ledger(args: argparse.Namespace) -> Dict[str, Any]:
    rows = DS.collect_rows(args.repo, args.base, args.pattern, limit=args.limit,
                           include_remote=args.include_remote, jobs=args.jobs)
    return DS.build_ledger(args.repo, rows, base=args.base, pattern=args.pattern,
                           meta={"generated_at": _now(), "limit": args.limit})


def cmd_delivery(args: argparse.Namespace) -> int:
    led = _ledger(args)
    ver = DS.verify_ledger(led)
    led["verification"] = ver
    _write_json(os.path.join(args.out, "ledger.json"), led)
    baseline = None
    if args.write_baseline:
        baseline = DS.make_baseline(led, created_at=_now())
        _write_json(os.path.join(args.out, "baseline.json"), baseline)
    if args.baseline and os.path.exists(args.baseline):
        baseline = _read_json(args.baseline)
    gate = DS.evaluate_gate(led, baseline, repo=args.repo)
    _write_json(os.path.join(args.out, "gate.json"), gate)
    recheck = DS.sample_recheck(led, repo=args.repo, n=args.sample)
    _write_json(os.path.join(args.out, "sample_recheck.json"), recheck)
    figs = figure_branches(led, args.out, baseline) if args.figures else []
    summary = {
        "repo": led["repo"],
        "pattern": led["pattern"],
        "total": led["total"],
        "counts": led["counts"],
        "unmerged_count": led["unmerged_count"],
        "coverage": led["coverage"],
        "ledger_sha256": led["ledger_sha256"],
        "verify_ok": ver["ok"],
        "gate_status": gate["status"],
        "gate_violations": [v["code"] for v in gate["violations"]],
        "sample_recheck_rate": recheck["rate"],
        "figures": [os.path.basename(f) for f in figs],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    led = _ledger(args)
    baseline = _read_json(args.baseline) if args.baseline and os.path.exists(args.baseline) else None
    proofs = _read_json(args.proofs) if args.proofs and os.path.exists(args.proofs) else None
    gate = DS.evaluate_gate(led, baseline, repo=args.repo, proofs=proofs)
    _write_json(os.path.join(args.out, "gate.json"), gate)
    _write_json(os.path.join(args.out, "ledger.json"), led)
    print(json.dumps({"status": gate["status"],
                      "violations": [v["code"] for v in gate["violations"]],
                      "checks": gate["checks"]}, indent=2, ensure_ascii=False))
    return 0 if gate["status"] == "green" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="交付面与重启动卫生机制")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("restart", help="AC1–AC4：位级可复现 / 能量账本 / 同步点 / 压缩无损")
    r.add_argument("--out", default="artifacts/hygiene")
    r.add_argument("--n-micro", type=int, default=1000)
    r.add_argument("--codec", default=None, help="压缩编解码器（默认取首个可用）")
    r.add_argument("--no-figures", dest="figures", action="store_false")
    r.set_defaults(func=cmd_restart, figures=True)

    for name, func, helptext in (
        ("delivery", cmd_delivery, "AC5/AC6：四分台账 + 摘要复算 + 抽样独立复核"),
        ("gate", cmd_gate, "只减不增门（基线缺失 ⇒ fail-closed 判红）"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--repo", required=True)
        p.add_argument("--base", default=DS.DEFAULT_BASE)
        p.add_argument("--pattern", default=DS.DEFAULT_PATTERN)
        p.add_argument("--limit", type=int, default=None, help="只审计按名排序的前 N 条（可复算）")
        p.add_argument("--out", default="artifacts/hygiene")
        p.add_argument("--figures", action="store_true")
        p.add_argument("--include-remote", action="store_true")
        p.add_argument("--jobs", type=int, default=1, help="并行只读 git 查询数（结果与串行一致）")
        if func is cmd_delivery:
            p.add_argument("--baseline", default=None)
            p.add_argument("--write-baseline", action="store_true")
            p.add_argument("--sample", type=int, default=30)
        else:
            p.add_argument("--baseline", default=None)
            p.add_argument("--proofs", default=None, help="JSON: {branch: commit_sha} 交付证明")
        p.set_defaults(func=func)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())